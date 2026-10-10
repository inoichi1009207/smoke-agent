from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

_Q = r"[`\"“「]([^`\"”」\n]{1,60})[`\"”」]"
QUOTED = re.compile(_Q)
TOKEN = r"#(?:DIV/0|[A-Z][A-Z0-9/]{1,10})[!?]"
BUDGET_MS = 70000
MORE_BUDGET_S = 150
RUN_TIMEOUT_S = 180

FOLLOW_TIMEOUT_S = 60
FOLLOW_BUDGET_MS = 45000
SERVER_START_S = 60
_BIND_FAILED = re.compile(r"EADDRINUSE|address already in use", re.I)

ERROR_KINDS: list[tuple[str, re.Pattern, list[str]]] = [
    ("division by zero", re.compile(r"divi(?:sion|ding|ded)\s+by\s+zero|divide\s+by\s+zero", re.I), ["=1/0"]),
    ("unsupported function", re.compile(r"(?:unsupported|unknown|unrecogni[sz]ed)\s+function", re.I), ["=NOSUCHFUNC(1)"]),
    ("malformed expression", re.compile(r"malformed\s+(?:expression|formula)|syntax\s+error", re.I), ["=(1+2", "=SUM(E1:E2"]),
    ("circular reference", re.compile(r"circular\s+reference", re.I), ["=SUM({self}:{next})"]),
]
DISPLAYS = re.compile(r"\b(?:displays?|shows?|returns?|yields?|produces?|results?\s+in|renders?)\s+(?:as\s+)?[`\"“]?(" + TOKEN + r")")

def _sentences(text: str) -> list[str]:
    blocks = re.split(r"\n\s*(?:-\s+)?[A-Za-z_]+:\s|\n\s*-\s+", "\n" + str(text or ""))
    out: list[str] = []
    for block in blocks:
        flat = " ".join(block.split())
        out.extend(s.strip() for s in re.split(r"(?<=[.;])\s+", flat) if len(s.strip()) > 3)
    return out

def _command(names: list[str], verb: str, axis: str, where: str) -> str:
    verbs = r"insert|add" if verb == "insert" else r"delete|remove"
    fits = [n for n in names if re.search(r"\b(?:" + verbs + r")\b", n, re.I) and re.search(r"\b" + axis + r"s?\b", n, re.I)
            and not re.search(r"\b(?:sheet|worksheet|workbook|tab|record|item)s?\b", n, re.I)]
    if verb == "insert":
        fits = [n for n in fits if re.search(r"\b" + where + r"\b", n, re.I)]
    return fits[0] if fits else ""

_NEGATION = re.compile(r"\b(?:not|never|no|none|cannot|neither|nor|without)\b|n't\b", re.IGNORECASE)

_CONDITION = re.compile(r"\b(?:if|unless|provided|except|only\s+(?:if|when)|as\s+long\s+as|when(?:ever)?|while|once)\b",
                        re.IGNORECASE)

_EDIT = re.compile(r"\b(?:insert\w*|delet\w*|remov\w*|add(?:s|ed|ing)?|structural\s+(?:edit|change|operation)s?)\b",
                   re.IGNORECASE)
_SETTING = re.compile(r"\b(?:enabl\w*|disabl\w*|options?|settings?|modes?|toggl\w*|switched|turned|checked|unchecked|"
                      r"preferences?|configur\w*)\b", re.IGNORECASE)

def _shift_negated(sentence: str) -> bool:
    return any(re.search(r"\bshift|\badjust", part, re.I) and _NEGATION.search(part)
               for part in re.split(r"[,;:]", sentence))

def _conditional(sentence: str) -> bool:
    for hit in _CONDITION.finditer(sentence):
        if re.match(r"unless|except", hit.group(0), re.I):
            return True
        part = re.split(r"[,;:]", sentence[hit.end():], maxsplit=1)[0][:80]
        if not _EDIT.search(part) or _SETTING.search(part):
            return True
    return False

def _for_insert(sentence: str) -> bool:
    return bool(re.search(r"\binsert", sentence, re.I)) or not re.search(r"\b(?:delet|remov)", sentence, re.I)

def _axes(sentence: str) -> set[str]:
    said = {axis for axis in ("row", "column") if re.search(r"\b" + axis + r"s?\b", sentence, re.I)}
    return said or {"row", "column"}

def structure_statement(whole: str) -> dict[str, Any] | None:
    text = str(whole or "")
    flat = " ".join(text.split())
    names = list(dict.fromkeys(m.group(1).strip() for m in QUOTED.finditer(flat)))

    sentences = sorted(dict.fromkeys(_sentences(text)), key=len)
    shifts = [s for s in sentences if re.search(r"\b(?:formulas?|references?)\b", s, re.I)
              and re.search(r"\bshift|\badjust", s, re.I) and re.search(r"\b(?:insert|row|column)", s, re.I)]

    blocked: set[str] = set()
    for s in shifts:
        if _for_insert(s) and (_shift_negated(s) or _conditional(s)):
            blocked |= _axes(s)
    shifts = [s for s in shifts if _for_insert(s) and not _shift_negated(s) and not _conditional(s)]
    shifts.sort(key=lambda s: 0 if re.search(r"\breferences?\s+shift|\bshift\b", s, re.I) else 1)
    shift = shifts[0] if shifts else ""
    if not shift:
        return None
    covered = set().union(*(_axes(s) for s in shifts)) - blocked
    for axis, where, header in (("row", "above", "1"), ("column", "left", "A")):
        if axis not in covered:
            continue
        insert, delete = _command(names, "insert", axis, where), _command(names, "delete", axis, where)
        if insert and delete:
            break
    else:
        return None
    absolute = next((s for s in sentences if re.search(r"\babsolute\s+(?:and\s+\w+\s+)?references?\b|\$[A-Z]+\$?\d", s, re.I)), "")

    lost = [s for s in sentences if re.search(r"\breferences?\b", s, re.I)
            and re.search(r"cannot\s+be\s+preserved|can\s*not\s+be\s+kept|deleted\s+(?:cell|row|column)s?|no\s+longer\s+exists?|"
                          r"invalid\s+reference", s, re.I)
            and (re.search(TOKEN, s) or re.search(r"\berror\b", s, re.I))]
    lost.sort(key=lambda s: 0 if re.search(TOKEN, s) else 1)
    lost_sentence = lost[0] if lost else ""
    lost_token = ""
    if lost_sentence:
        near = re.search(r"(?:preserved|kept|deleted[^.;]{0,30}?|exists?|invalid\s+reference)[^.;#]{0,40}?(" + TOKEN + ")", lost_sentence)
        lost_token = near.group(1) if near else ""
    errors = []
    for kind, pattern, formulas in ERROR_KINDS:
        for s in sentences:
            for m in pattern.finditer(s):
                shown = DISPLAYS.search(s, m.end(), m.end() + 60)
                if shown and not any(e["kind"] == kind for e in errors):
                    errors.append({"kind": kind, "token": shown.group(1), "formulas": formulas, "sentence": s[:240]})

    shrink = next((s for s in sentences if re.search(r"\branges?\b", s, re.I)
                   and re.search(r"\b(?:shrinks?|shrunk|contracts?|narrows?)\b", s, re.I)), "")
    other = None
    for o_axis, o_where, o_header in (("row", "above", "1"), ("column", "left", "A")):
        if o_axis == axis or o_axis not in covered:
            continue
        o_insert, o_delete = _command(names, "insert", o_axis, o_where), _command(names, "delete", o_axis, o_where)
        if o_insert and o_delete:
            other = {"axis": o_axis, "header": o_header, "insert": o_insert, "delete": o_delete}
    bar = next((n for n in names if re.search(r"\bformula\b", n, re.I)), "")
    create = [n for n in names if re.search(r"^(?:new|create|add)\b", n, re.I)
              and re.search(r"\b(?:workbook|spreadsheet|worksheet|sheet)s?\b", n, re.I) and len(n) <= 40]
    create.sort(key=lambda n: 0 if re.search(r"workbook|spreadsheet", n, re.I) else 1)
    submit = ""
    for name in create:
        for at in re.finditer(re.escape(name), flat):
            m = re.match(r"[^.]{0,160}?\bsubmit\s+button\s+(?:is\s+)?(?:named|labell?ed|called|titled)\s+" + _Q, flat[at.end():], re.I)
            if m:
                submit = m.group(1)
                break
        if submit:
            break
    return {"axis": axis, "header": header, "insert": insert, "delete": delete, "shift_sentence": shift[:240],
            "absolute": bool(absolute), "absolute_sentence": absolute[:240], "lost_sentence": lost_sentence[:240],
            "lost_token": lost_token, "errors": errors, "formula_bar": bar, "create": create[:3], "submit": submit,
            "shrink_sentence": shrink[:240], "other_axis": other,

            "delete_sentences": {a: _delete_shift(sentences, a)[:240] for a in ("row", "column")},

            "range_invalid": _range_invalid(sentences)[:240],
            "absolute_fixed": _absolute_fixed(sentences)[:240],
            "sort":_sort_statement(flat, names, sentences), "pivot": _pivot_statement(flat, names, sentences)}

def _range_invalid(sentences: list[str]) -> str:
    deleted = re.compile(r"\bdelet\w*\s+(?:cells?|rows?|columns?|lines?)\b|\b(?:cells?|rows?|columns?|lines?)\s+"
                         r"(?:(?:is|are|was|were|being|been|gets?|got)\s+)?(?:delet|remov)\w*|\b(?:delet|remov)(?:ion|ing)\b", re.I)
    for s in sentences:
        if (re.search(r"\branges?\b", s, re.I) and deleted.search(s)
                and (re.search(TOKEN, s) or re.search(r"\binvalid\w*|\berrors?\b", s, re.I))):
            return s
    return ""

def _delete_shift(sentences: list[str], axis: str) -> str:
    for s in sentences:
        if (re.search(r"\b(?:delet|remov)\w*", s, re.I) and re.search(r"\bshift|\bmov(?:e|es|ed|ing)\s+(?:up|left)", s, re.I)
                and axis in _axes(s) and not re.search(r"\b(?:sheet|worksheet|workbook|tab)s?\b", s, re.I)
                and not _shift_negated(s) and not _NEGATION.search(s) and not _conditional(s)):
            return s
    return ""

_ABSOLUTE = re.compile(r"\babsolute\b|\$[A-Z]{1,3}\$?\d|\b[A-Z]{1,3}\$\d", re.I)
_FIXED = re.compile(r"\b(?:remains?|remained|stays?|stayed|kept|keeps?|unchanged|fixed|locked|frozen|anchored|constant)\b|"
                    r"\b(?:not|never)\s+(?:be\s+)?(?:shift|mov|chang|adjust)\w*|n't\s+(?:shift|mov|chang|adjust)\w*", re.I)

_STRUCTURAL = re.compile(r"\b(?:insert\w*|delet\w*|remov\w*|structur\w*|(?:rows?|columns?)(?:\s*/\s*columns?)?\s+"
                         r"(?:changes?|operations?|edits?|insertions?|deletions?))\b", re.I)
_COPYING = re.compile(r"\b(?:cop(?:y|ies|ied|ying)|past(?:e|es|ed|ing)|fill\w*|drag\w*|duplicat\w*)\b", re.I)

def _absolute_fixed(sentences: list[str]) -> str:
    for s in sentences:
        if _ABSOLUTE.search(s) and _FIXED.search(s) and (_STRUCTURAL.search(s) or not _COPYING.search(s)):
            return s
    return ""

def _menu_of(flat: str, command: str) -> str:
    m = re.search(r"[\"“]" + re.escape(command) + r"[\"”]\s+(?:from|in|on|under)\s+the\s+[\"“]([^\"”\n]{1,30})[\"”]\s+menu",
                  flat, re.I)
    return m.group(1) if m else ""

def _named_in(sentence: str, kind: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"[\"“]([^\"”\n]{1,40})[\"”]\s+" + kind, sentence, re.I)]

def _sort_statement(flat: str, names: list[str], sentences: list[str]) -> dict[str, Any] | None:
    said = next((s for s in sentences if re.search(r"\bsort", s, re.I) and re.search(r"\b(?:formulas?|references?)\b", s, re.I)
                 and re.search(r"new\s+positions?|\badjust|consistent\s+with|\brewrit", s, re.I)), "")
    commands = [n for n in names if re.search(r"^sort\b", n, re.I) and not re.search(r"^sort$|\bby\b", n, re.I)]
    if not said or not commands:
        return None
    command = commands[0]
    dialog = next((s for s in sentences if re.search(r"\bdialog\b", s, re.I) and command in s
                   and re.search(r"combo\s*box|select|drop-?down", s, re.I)), "")
    quoted = [m.group(1) for m in QUOTED.finditer(dialog)]
    by = next((n for n in quoted if re.search(r"\bby\b|\bcolumn\b|\bkey\b", n, re.I) and n != command), "")
    order = next((n for n in quoted if re.search(r"^(?:sort\s+)?(?:order|direction)$", n, re.I)), "")
    ascending = next((n for n in names if re.search(r"^ascending$|^a\s*(?:-|to|→)\s*z$", n, re.I)), "")
    header_box = next((n for n in _named_in(dialog, r"check\s*box") if re.search(r"\bheader", n, re.I)), "")
    button = next(iter(_named_in(dialog, "button")), "")
    if not (by and order and ascending and header_box and button):
        return None
    return {"sentence": said[:240], "menu": _menu_of(flat, command), "command": command, "by": by, "order": order,
            "ascending": ascending, "header_box": header_box, "button": button}

def _pivot_statement(flat: str, names: list[str], sentences: list[str]) -> dict[str, Any] | None:
    said = next((s for s in sentences if re.search(r"\brefresh", s, re.I)
                 and re.search(r"(?:adjusted|shifted|updated|current)\s+(?:source\s+)?range", s, re.I)
                 and re.search(r"\b(?:rows?|insert|change)", s, re.I)), "")
    commands = [n for n in names if re.search(r"^(?:create|insert|new|add)\b", n, re.I) and re.search(r"\bpivot", n, re.I)]
    refresh = next((n for n in names if re.search(r"\brefresh\b", n, re.I) and re.search(r"\bpivot|summary", n, re.I)), "")
    insert = _command(names, "insert", "row", "above")
    if not (said and commands and refresh and insert):
        return None
    command = commands[0]
    dialog = next((s for s in sentences if re.search(r"\bdialog\b", s, re.I) and command in s and _named_in(s, "button")), "")
    editor = next((s for s in sentences if re.search(r"combo\s*box", s, re.I)
                   and any(re.search(r"^rows?$", n, re.I) for n in (m.group(1) for m in QUOTED.finditer(s)))), "")
    quoted = [m.group(1) for m in QUOTED.finditer(editor)]
    rows = next((n for n in quoted if re.search(r"^rows?(?:\s+field)?$", n, re.I)), "")
    values = next((n for n in quoted if re.search(r"^values?(?:\s+field)?$", n, re.I)), "")
    method = next((n for n in quoted if re.search(r"summari[sz]e|aggregat", n, re.I)), "")
    apply = next(iter(_named_in(editor, "button")), "")
    create_button = next(iter(_named_in(dialog, "button")), "")
    total = "SUM" if re.search(r"\bSUM\b", flat) else ""
    if not (rows and values and apply and create_button and (total or not method)):
        return None
    return {"sentence": said[:240], "menu": _menu_of(flat, command), "command": command, "create_button": create_button,
            "rows": rows, "values": values, "method": method, "sum": total, "apply": apply, "refresh": refresh,
            "insert": insert}

def _coord(col: int, row: int) -> str:
    return chr(ord("A") + col) + str(row)

def probe_job(statement: dict[str, Any] | None, snapshot: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not statement:
        return None
    from .entry_checks import aria_nodes

    rows = statement["axis"] == "row"
    at = (lambda i: _coord(0, 1 + i)) if rows else (lambda i: _coord(i, 1))
    value, rel, ab = at(0), at(1), at(2)
    rel_formula, abs_formula = f"={value}*10", "=${}${}+1".format(value[0], value[1:])
    moved = at(1)
    plan = {
        "value": value, "rel": rel, "abs": ab, "rel_formula": rel_formula, "abs_formula": abs_formula,

        "i_value": moved, "i_rel": at(2), "i_abs": at(3), "i_rel_expect": f"={moved}*10",
        "i_abs_expect": "=${}${}+1".format(moved[0], moved[1:]),

        "delete_header": moved[1:] if rows else moved[0], "d_rel": moved, "d_abs": at(2),
    }

    side = (lambda i: _coord(3, 1 + i)) if rows else (lambda i: _coord(i, 3))
    plan.update({"r_first": side(0), "r_last": side(1), "r_sum": side(2),
                 "r_formula": f"=SUM({side(0)}:{side(1)})", "i_r_sum": side(3),
                 "i_r_expect": f"=SUM({side(1)}:{side(2)})", "d_r_sum": side(2), "i_r_first": side(1), "i_r_last": side(2)})
    extra: list[str] = []
    if _part_on("INTERIOR"):

        col, num = value[0], value[1:]
        mixed = (lambda c, n: f"={c}${n}+5") if rows else (lambda c, n: f"=${c}{n}+5")
        plan.update({"mix": at(3), "mix_formula": mixed(col, num), "i_mix": at(4), "i_mix_expect": mixed(moved[0], moved[1:])})

        line = (lambda i: _coord(4, 1 + i)) if rows else (lambda i: _coord(i, 5))
        plan.update({"n_a": line(0), "n_b": line(1), "n_top": line(0), "n_mid": line(1), "n_low": line(2),
                     "d_n_top": line(0), "d_n_low": line(1)})
        if not statement.get("range_invalid"):
            plan.update({"n_sum": line(3), "n_formula": f"=SUM({line(0)}:{line(2)})", "d_n_sum": line(2),
                         "d_n_expect": f"=SUM({line(0)}:{line(1)})"})

        holder = (lambda i: _coord(6 + i, 1)) if rows else (lambda i: _coord(0, 7 + i))
        target, after = line(2), line(1)
        forms = (lambda c, n: f"={c}{n}*3", lambda c, n: f"=${c}${n}*3",
                 (lambda c, n: f"={c}${n}*3") if rows else (lambda c, n: f"=${c}{n}*3"))
        plan["beyond"] = [{"kind": kind, "cell": holder(i), "formula": form(target[0], target[1:]),
                           "expect": form(after[0], after[1:])}
                          for i, (kind, form) in enumerate(zip(("relative", "absolute", "mixed"), forms))]
        extra = [plan["mix"], plan["i_mix"], line(0), line(1), line(2), line(3)]
    samples, row = [], 1
    for error in statement.get("errors") or []:
        for formula in error["formulas"]:
            cell = _coord(5, row)
            samples.append({"cell": cell, "kind": error["kind"], "token": error["token"],
                            "formula": formula.format(self=cell, next=_coord(5, row + 1))})
            row += 2 if "{self}" in formula else 1
    hints: list[str] = []
    for entry in snapshot or []:
        route = str(entry.get("route") or "")
        if not route.startswith("/") or ":" in route or entry.get("error"):
            continue
        roles = {role for _i, role, _n in aria_nodes(str(entry.get("aria") or ""))}
        if roles & {"grid", "gridcell"} or entry.get("hasGrid"):
            hints.append(route)
    used = [plan[k] for k in ("value", "rel", "abs", "i_abs", "r_first", "r_last", "r_sum", "i_r_sum")]
    used += [s["cell"] for s in samples] + extra
    job = {"axis": statement["axis"], "header": statement["header"], "insert": statement["insert"],
           "delete": statement["delete"], "bar": statement.get("formula_bar") or "", "create": statement.get("create") or [],
           "submit": statement.get("submit") or "", "plan": plan, "samples": samples,
           "used": list(dict.fromkeys(used)), "routes": list(dict.fromkeys(hints))[:3]}

    more: list[dict[str, Any]] = []
    other = statement.get("other_axis")
    if other and not statement.get("_other") and _part_on("COLUMNS"):
        sub = probe_job(dict(statement, **other, errors=[], other_axis=None, sort=None, pivot=None, _other=True), snapshot)
        if sub:
            more.append(dict(sub, name="other_axis"))
    base = {k: job[k] for k in ("bar", "create", "submit", "routes")}
    sort = statement.get("sort") if _part_on("SORT") else None
    if sort:
        cells = [("A1", SORT_HEAD[0]), ("B1", SORT_HEAD[1]), ("C1", SORT_HEAD[2])]
        for row, (key, qty) in enumerate(SORT_ROWS, start=2):
            cells += [(f"A{row}", key), (f"B{row}", str(qty)), (f"C{row}", f"=B{row}*2")]
        last = 1 + len(SORT_ROWS)
        more.append(dict(base, name="sort", mode="sort", axis="row", header="1", insert="", delete="", **sort,
                         cells=cells, range=["A1", f"C{last}"], key=SORT_HEAD[1], plan={}, samples=[],
                         read=[f"{c}{r}" for r in range(2, last + 1) for c in "ABC"],
                         used=[f"{c}{r}" for r in range(1, last + 1) for c in "ABC"]))
    pivot = statement.get("pivot") if _part_on("PIVOT") else None
    if pivot:
        cells = [("A1", SORT_HEAD[0]), ("B1", SORT_HEAD[1])]
        for row, (key, qty) in enumerate(PIVOT_ROWS, start=2):
            cells += [(f"A{row}", key), (f"B{row}", str(qty))]
        last = 1 + len(PIVOT_ROWS)
        more.append(dict(base, name="pivot", mode="pivot", axis="row", header="1", delete="", **pivot, cells=cells,
                         range=["A1", f"B{last}"], key=SORT_HEAD[0], value=SORT_HEAD[1], at="3",
                         added=[("A3", PIVOT_ADDED[0]), ("B3", str(PIVOT_ADDED[1]))], moved=f"A{last + 1}",
                         result=[f"{c}{r}" for r in range(1, 9) for c in "AB"], plan={}, samples=[],
                         used=[f"{c}{r}" for r in range(1, last + 2) for c in "AB"]))
    if more:
        job["more"] = more
    return job

SORT_HEAD = ("Item", "Qty", "Twice")
SORT_ROWS = [("p1", 30), ("p2", 10), ("p3", 20)]
PIVOT_ROWS = [("p1", 1), ("p2", 2), ("p3", 4)]
PIVOT_ADDED = ("p4", 8)

PROBE_SCRIPT = r"""
const { chromium } = require('@playwright/test');
const BUDGET = Number(process.env.ARC_STRUCT_PROBE_BUDGET || 70000);
const SHOTS = process.env.ARC_STRUCT_PROBE_SHOTS || '';
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const exact = (s) => new RegExp('^\\s*' + esc(s) + '\\s*$', 'i');
(async () => {
  const [base, spec] = process.argv.slice(2);
  const job = JSON.parse(spec);
  const P = job.plan;
  const out = { axis: job.axis, route: null, created: null, bar: false, setup: {}, insert: null, afterInsert: {},
    del: null, afterDelete: {}, samples: [], visited: [], entry: {} };
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  let inflight = 0;
  page.on('request', () => { inflight += 1; });
  page.on('requestfinished', () => { inflight = Math.max(0, inflight - 1); });
  page.on('requestfailed', () => { inflight = Math.max(0, inflight - 1); });
  const settle = async () => {
    await page.waitForTimeout(500);
    const start = Date.now();
    while (inflight > 0 && Date.now() - start < 3000) await page.waitForTimeout(100);
    await page.waitForTimeout(250);
  };
  const shot = async (name) => { if (SHOTS) await page.screenshot({ path: SHOTS + '/' + (job.name ? job.name + '-' : '') + name + '.png' }).catch(() => {}); };
  const t0 = Date.now();
  const late = () => Date.now() - t0 > BUDGET;
  const named = async (roles, name) => {
    for (const role of roles) {
      const loc = page.getByRole(role, { name: exact(name) });
      const n = await loc.count();
      for (let i = 0; i < n; i++) if (await loc.nth(i).isVisible().catch(() => false)) return loc.nth(i);
    }
    return null;
  };
  // the middle of a cell: the gridcell named by its coordinate, else the crossing of its row number and column letter
  const cellPoint = async (coord) => {
    const byName = page.getByRole('gridcell', { name: coord, exact: true });
    if ((await byName.count()) === 1) {
      await byName.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
      const b = await byName.boundingBox();
      if (b && b.width > 4 && b.height > 4) return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    }
    const m = coord.match(/^([A-Z]+)(\d+)$/);
    const rh = page.getByRole('rowheader', { name: m[2], exact: true }).first();
    const ch = page.getByRole('columnheader', { name: m[1], exact: true }).first();
    if (!(await rh.count()) || !(await ch.count())) return null;
    const rb = await rh.boundingBox(), cb = await ch.boundingBox();
    if (!rb || !cb) return null;
    const p = { x: cb.x + cb.width / 2, y: rb.y + rb.height / 2 };
    const ok = await page.evaluate(([x, y]) => { const h = document.elementFromPoint(x, y);
      return !!(h && h.closest('[role=gridcell],[role=cell],td')); }, [p.x, p.y]);
    return ok ? p : null;
  };
  const textAt = (p) => page.evaluate(([x, y]) => {
    const h = document.elementFromPoint(x, y);
    const c = h && h.closest('[role=gridcell],[role=cell],td');
    if (!c) return null;
    const v = Array.from(c.querySelectorAll('input,textarea')).map((i) => i.value).join(' ');
    return ((c.innerText || '') + ' ' + v).replace(/\s+/g, ' ').trim();
  }, [p.x, p.y]);
  const bar = async () => {
    if (job.bar) { const b = await named(['textbox', 'combobox'], job.bar); if (b) return b; }
    const any = page.getByRole('textbox', { name: /formula/i });
    return (await any.count()) && (await any.first().isVisible()) ? any.first() : null;
  };
  const read = async (coord) => {
    const p = await cellPoint(coord);
    if (!p) return { missing: true };
    const text = await textAt(p);
    await page.mouse.click(p.x, p.y);
    await page.waitForTimeout(300);
    const b = await bar();
    const formula = b ? await b.inputValue().catch(() => null) : null;
    return { text, formula };
  };
  // v146d: platform sheet v144 showed its formula bar as <input readonly> (edits only in the cell's own editor): typing
  // into it stored nothing and every setup read back empty. The bar is typed into only when it is editable, and a bar
  // entry that left the cell empty is repeated once in the cell's editor; out.entry says which way each cell went.
  const viaCell = async (p, value) => {
    await page.mouse.dblclick(p.x, p.y); await page.waitForTimeout(250); await page.keyboard.press('Control+A');
    await page.keyboard.type(value, { delay: 10 });
    await page.keyboard.press('Enter');
    await settle();
  };
  const enter = async (coord, value) => {
    const p = await cellPoint(coord);
    if (!p) return false;
    await page.mouse.click(p.x, p.y);
    await page.waitForTimeout(200);
    const b = await bar();
    const editable = b ? await b.isEditable().catch(() => false) : false;
    if (b && !editable) out.barEditable = false;
    if (editable) {
      await b.click(); await page.keyboard.press('Control+A'); await page.keyboard.type(value, { delay: 10 });
      await page.keyboard.press('Enter');
      await settle();
      if ((await textAt(p)) !== '') { out.entry[coord] = 'bar'; return true; }
      out.entry[coord] = 'bar took nothing, cell editor';
    } else out.entry[coord] = b ? 'read-only bar, cell editor' : 'cell editor';
    await viaCell(p, value);
    return true;
  };
  const blank = async () => {
    const texts = {};
    for (const coord of job.used) { const p = await cellPoint(coord); texts[coord] = p ? await textAt(p) : null; }
    return texts;
  };
  const isBlank = (texts) => Object.values(texts).every((t) => t === '');
  // the stated command on the row number / column letter: right-click it, else click it, else a button inside it
  const structural = async (header, command) => {
    const role = job.axis === 'row' ? 'rowheader' : 'columnheader';
    const h = page.getByRole(role, { name: header, exact: true }).first();
    if (!(await h.count())) return { error: 'no ' + role + ' named ' + header };
    await h.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
    let how = 'right-click';
    await h.click({ button: 'right', timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(400);
    let item = await named(['menuitem', 'button', 'option'], command);
    if (!item) {
      await page.keyboard.press('Escape');
      how = 'click';
      await h.click({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(400);
      item = await named(['menuitem', 'button', 'option'], command);
    }
    if (!item) {
      const opener = h.locator('button,[role=button]').first();
      if (await opener.count()) {
        how = 'button in the header';
        await opener.click({ timeout: 3000 }).catch(() => {});
        await page.waitForTimeout(400);
        item = await named(['menuitem', 'button', 'option'], command);
      }
    }
    if (!item) { await page.keyboard.press('Escape'); return { error: 'command "' + command + '" not found' }; }
    await item.click({ timeout: 3000 });
    await settle();
    await page.waitForTimeout(400);
    return { how };
  };
  // ---- v144o helpers: a mouse drag over a range, a stated menu command, a combo box choice, texts of cells
  const drag = async (a, b) => {
    const p = await cellPoint(a), q = await cellPoint(b);
    if (!p || !q) return false;
    await page.mouse.move(p.x, p.y); await page.mouse.down();
    await page.mouse.move((p.x + q.x) / 2, (p.y + q.y) / 2, { steps: 6 });
    await page.mouse.move(q.x, q.y, { steps: 6 }); await page.mouse.up();
    await page.waitForTimeout(500);
    return true;
  };
  const command = async (menuName, name) => {
    if (menuName) {
      const m = await named(['button', 'menuitem'], menuName);
      if (!m) return 'no menu "' + menuName + '"';
      await m.click({ timeout: 3000 }); await page.waitForTimeout(400);
    }
    const item = await named(['menuitem', 'button', 'option'], name);
    if (!item) { await page.keyboard.press('Escape'); return 'no command "' + name + '"'; }
    await item.click({ timeout: 3000 }); await page.waitForTimeout(700);
    return '';
  };
  const choose = async (name, label) => {
    const c = await named(['combobox', 'listbox'], name);
    if (!c) return 'no combo box "' + name + '"';
    try { await c.selectOption({ label }, { timeout: 2000 }); await page.waitForTimeout(300); return ''; } catch (e) {}
    await c.click({ timeout: 2000 }).catch(() => {}); await page.waitForTimeout(300);
    const o = await named(['option', 'menuitem'], label);
    if (!o) { await page.keyboard.press('Escape'); return 'no option "' + label + '" in "' + name + '"'; }
    await o.click({ timeout: 2000 }); await page.waitForTimeout(300);
    return '';
  };
  const press = async (name) => {
    const b = await named(['button'], name);
    if (!b) return 'no button "' + name + '"';
    await b.click({ timeout: 3000 }); await settle(); await page.waitForTimeout(400);
    return '';
  };
  const texts = async (coords) => {
    const o = {};
    for (const c of coords) { const p = await cellPoint(c); o[c] = p ? await textAt(p) : null; }
    return o;
  };
  const alerts = async () => (await page.getByRole('alert').allInnerTexts().catch(() => []))
    .map((t) => t.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const fail = (why) => { out.error = why; throw null; };
  // ---- G3: records sorted by the stated dialog; do the moved formulas' references follow their rows?
  const sortPhase = async () => {
    for (const [c, v] of job.cells) if (!(await enter(c, v))) fail('cell ' + c + ' not found');
    for (const c of job.read) out.setup[c] = await read(c);
    await shot('s1-setup');
    if (!(await drag(job.range[0], job.range[1]))) fail('range ' + job.range.join(':') + ' not found');
    let why = await command(job.menu, job.command);
    if (why) fail(why);
    for (const [name, label] of [[job.by, job.key], [job.order, job.ascending]]) { why = await choose(name, label); if (why) { await shot('s2-dialog'); fail(why); } }
    const box = await named(['checkbox'], job.header_box);
    if (!box) fail('no checkbox "' + job.header_box + '"');
    await box.check({ timeout: 2000 }).catch(() => {});
    await shot('s2-dialog');
    why = await press(job.button);
    if (why) fail(why);
    out.sorted = true;
    out.alerts = await alerts();
    for (const c of job.read) out.afterInsert[c] = await read(c);
    await shot('s3-after-sort');
  };
  // ---- G1: a pivot over the probe's records; a row inserted inside its source; does refresh use the adjusted range?
  const pivotPhase = async () => {
    for (const [c, v] of job.cells) if (!(await enter(c, v))) fail('cell ' + c + ' not found');
    const tabs = page.getByRole('tab');
    const selected = async () => { const n = await tabs.count();
      for (let i = 0; i < n; i++) if ((await tabs.nth(i).getAttribute('aria-selected')) === 'true') return i; return -1; };
    const src = await selected();
    if (!(await drag(job.range[0], job.range[1]))) fail('range ' + job.range.join(':') + ' not found');
    let why = await command(job.menu, job.command);
    if (why) fail(why);
    why = await press(job.create_button);
    if (why) fail(why);
    const piv = await selected();
    out.tabs = { src, piv };
    if (src < 0 || piv < 0 || piv === src) fail('no pivot tab selected after "' + job.create_button + '"');
    for (const [name, label] of [[job.rows, job.key], [job.values, job.value], [job.method, job.sum]]) {
      if (!name) continue; why = await choose(name, label); if (why) { await shot('p1-editor'); fail(why); }
    }
    why = await press(job.apply);
    if (why) fail(why);
    out.setup = await texts(job.result);
    await shot('p1-applied');
    if (late()) { out.budget = true; throw null; }
    await tabs.nth(src).click({ timeout: 3000 }); await settle();
    out.insert = await structural(job.at, job.insert);
    if (out.insert.error) throw null;
    out.moved = await texts([job.moved, job.added[0][0]]);
    for (const [c, v] of job.added) if (!(await enter(c, v))) fail('cell ' + c + ' not found');
    await shot('p2-source-after-insert');
    await tabs.nth(piv).click({ timeout: 3000 }); await settle();
    out.beforeRefresh = await texts(job.result);
    why = await press(job.refresh);
    if (why) fail(why);
    out.refreshed = true;
    out.alerts = await alerts();
    out.afterInsert = await texts(job.result);
    await shot('p3-after-refresh');
  };
  try {
    // ---- 1. a blank sheet of the probe's own: a stated create control on "/" (and its submit), else on a grid page
    const visit = async (route) => {
      out.visited.push(route);
      try { await page.goto(base + route, { waitUntil: 'networkidle', timeout: 12000 }); } catch (e) { return false; }
      await page.waitForTimeout(300);
      return true;
    };
    for (const route of ['/', ...job.routes]) {
      if (out.created || late()) break;
      if (!(await visit(route))) continue;
      for (const name of job.create) {
        const control = await named(['button', 'link'], name);
        if (!control) continue;
        await control.click({ timeout: 3000 }).catch(() => {});
        await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(400);
        if (job.submit) {
          const submit = await named(['button'], job.submit);
          if (submit) { await submit.click({ timeout: 3000 }).catch(() => {}); await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {}); }
        }
        await settle();
        const texts = await blank();
        if (Object.values(texts).every((t) => t !== null) && isBlank(texts)) { out.created = name; break; }
        out.visited.push('not blank after ' + name + ': ' + JSON.stringify(texts).slice(0, 160));
        if (!(await visit(route))) break;
      }
    }
    if (!out.created) { out.error = 'no blank sheet of the probe\'s own (create controls: ' + JSON.stringify(job.create) + ')'; throw null; }
    const url = new URL(page.url());
    out.route = url.pathname + url.search;
    out.bar = !!(await bar());
    await shot('0-blank');
    if (job.mode === 'sort') { await sortPhase(); throw null; }
    if (job.mode === 'pivot') { await pivotPhase(); throw null; }
    // ---- 2. the value and the two formulas (and the v144o range beside them)
    const ranged = P.r_first ? [[P.r_first, '5'], [P.r_last, '7'], [P.r_sum, P.r_formula]] : [];
    // v149e: the mixed reference and the interior range's first two values
    const extra = P.mix ? [[P.mix, P.mix_formula], [P.n_a, '2'], [P.n_b, '4']] : [];
    for (const [coord, value] of [[P.value, '2'], [P.rel, P.rel_formula], [P.abs, P.abs_formula], ...ranged, ...extra]) {
      if (!(await enter(coord, value))) { out.error = 'cell ' + coord + ' not found'; throw null; }
    }
    for (const coord of [P.value, P.rel, P.abs, ...(P.r_sum ? [P.r_sum] : []), ...(P.mix ? [P.mix] : [])]) out.setup[coord] = await read(coord);
    await shot('1-setup');
    // ---- 3. insert before row 1 / column A
    if (late()) { out.budget = true; throw null; }
    out.insert = await structural(job.header, job.insert);
    if (!out.insert.error) {
      const old = await cellPoint(P.value);
      out.afterInsert.old = { text: old ? await textAt(old) : null };
      for (const coord of [P.i_value, P.i_rel, P.i_abs, ...(P.i_r_sum ? [P.i_r_first, P.i_r_last, P.i_r_sum] : []), ...(P.mix ? [P.i_mix] : [])]) out.afterInsert[coord] = await read(coord);
      // v149e: the interior range -- where its two values went, then the third value in the new blank cell and the SUM
      if (P.n_low && !late()) {
        out.interior = { moved: await texts([P.n_top, P.n_mid, P.n_low]) };
        if (P.n_sum && await enter(P.n_top, '1') && await enter(P.n_sum, P.n_formula)) out.interior.sum = await read(P.n_sum);
        // r56: relative / absolute / mixed references beyond the line the delete removes (typed now, read after it)
        out.beyond = {};
        for (const b of (P.beyond || [])) if (await enter(b.cell, b.formula)) out.beyond[b.cell] = { before: await read(b.cell) };
      }
      await shot('2-after-insert');
      // ---- 4. delete the row / column that now holds the value
      if (!late()) {
        out.del = await structural(P.delete_header, job.delete);
        if (!out.del.error) {
          const first = await cellPoint(P.value);
          out.afterDelete.old = { text: first ? await textAt(first) : null };
          for (const coord of [P.d_rel, P.d_abs, ...(P.d_r_sum ? [P.d_r_sum] : [])]) out.afterDelete[coord] = await read(coord);
          if (out.interior) {
            out.interior.after = await texts([P.d_n_top, P.d_n_low]);
            if (out.interior.sum) out.interior.dsum = await read(P.d_n_sum);
            for (const b of (P.beyond || [])) if (out.beyond && out.beyond[b.cell]) out.beyond[b.cell].after = await read(b.cell);
          }
          await shot('3-after-delete');
        }
      } else out.budget = true;
    }
    // ---- 5. the stated error kinds, one sample each in empty cells
    for (const sample of job.samples) {
      if (late()) { out.budget = true; break; }
      const p = await cellPoint(sample.cell);
      const before = p ? await textAt(p) : null;
      const row = { cell: sample.cell, formula: sample.formula, before };
      out.samples.push(row);
      if (before !== '') continue;
      await enter(sample.cell, sample.formula);
      Object.assign(row, await read(sample.cell));
    }
    if (job.samples.length) await shot('4-errors');
  } catch (error) {
    if (error) out.error = String(error).slice(0, 200);
  }
  out.ms = Date.now() - t0;
  await browser.close();
  console.log('ARC_STRUCT_JSON' + JSON.stringify(out));
})().catch((error) => { console.log('ARC_STRUCT_ERROR' + String(error).slice(0, 300)); process.exit(2); });
"""

def run_probe(backend: Path, base: str, job: dict[str, Any], log=print) -> dict[str, Any] | None:
    from .fsutil import run_cmd

    timeout = int(job.get("_timeout_s") or RUN_TIMEOUT_S)
    budget = int(job.get("_budget_ms") or BUDGET_MS)
    tmp = Path(tempfile.mkdtemp(prefix="arc-struct-"))
    try:
        script = tmp / "probe.cjs"
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
        code, output = run_cmd(["node", str(script), base, json.dumps(job)], cwd=backend, timeout=timeout,
                               env={"NODE_PATH": str(backend / "node_modules"),
                                    "ARC_STRUCT_PROBE_BUDGET": str(budget)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    match = re.search(r"ARC_STRUCT_JSON(.*)", output)
    if not match:
        log(f"[audit] structure probe unavailable ({output.strip()[-200:]!r})")
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

def _answers(url: str) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False

def _not_ours(server: Any) -> str:
    alive, output = getattr(server, "alive", None), getattr(server, "output_so_far", None)
    if alive is None or output is None:
        return ""
    try:
        if not alive():
            return "the started server process is not running"
        if _BIND_FAILED.search(output() or ""):
            return "the started server could not bind its port (EADDRINUSE)"
    except Exception as exc:
        return f"the started server could not be checked ({exc!r})"
    return ""

def probe(project_dir: Path, job: dict[str, Any] | None, log=print) -> dict[str, Any] | None:
    backend = project_dir / "backend"
    if not job or not (backend / "node_modules" / "@playwright" / "test").is_dir():
        return None
    import time

    from .fsutil import BackgroundProcess, free_port, fresh_data_env, npm_bin, wait_http

    port = free_port(3400)
    base = f"http://127.0.0.1:{port}"
    if _answers(base + "/"):
        log(f"[audit] structure probe: port {port} answers before the start (another application) -- not probed")
        return None
    server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **fresh_data_env(project_dir)})
    try:
        ready, _ = wait_http(base + "/", timeout=SERVER_START_S)
        why = _not_ours(server) if ready else "application did not start"
        if why:
            log(f"[audit] structure probe: {why} -- not probed")
            return None
        result = run_probe(backend, base, {k: v for k, v in job.items() if k != "more"}, log=log)
        if isinstance(result, dict):

            result["more"] = {}
            started = time.monotonic()
            for sub in job.get("more") or []:

                if time.monotonic() - started + FOLLOW_TIMEOUT_S > MORE_BUDGET_S:
                    log(f"[audit] structure probe: {sub.get('name')} skipped (time budget)")
                    continue
                try:
                    result["more"][sub["name"]] = run_probe(
                        backend, base, dict(sub, _timeout_s=FOLLOW_TIMEOUT_S, _budget_ms=FOLLOW_BUDGET_MS), log=log)
                except Exception as exc:
                    log(f"[audit] structure probe {sub.get('name')} crashed: {exc!r}")
        why = _not_ours(server)
        if why and result is not None:
            log(f"[audit] structure probe: {why} after the runs -- results discarded")
            return None
        return result
    finally:
        server.stop()

def _norm(formula: Any) -> str:
    return re.sub(r"\s+", "", str(formula or "")).upper()

def _number(text: Any) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None

def _cell(result: dict[str, Any], part: str, coord: str) -> dict[str, Any]:
    cell = (result.get(part) or {}).get(coord)
    return cell if isinstance(cell, dict) else {}

def structure_findings(statement: dict[str, Any] | None, job: dict[str, Any] | None, result: dict[str, Any] | None,
                       log=print) -> list[dict[str, str]]:
    if not statement or not job or not isinstance(result, dict):
        return []
    plan = job["plan"]
    axis, where = statement["axis"], f"route {result.get('route')}"
    line = "row" if axis == "row" else "column"
    findings: list[dict[str, str]] = []
    diag: list[str] = []
    value, rel, ab = (_cell(result, "setup", plan[k]) for k in ("value", "rel", "abs"))
    setup_ok = (_number(value.get("text")) == 2 and _norm(rel.get("formula")) == _norm(plan["rel_formula"])
                and _norm(ab.get("formula")) == _norm(plan["abs_formula"]) and _number(rel.get("text")) == 20
                and _number(ab.get("text")) == 3)

    if result.get("error") or not setup_ok:

        keys = ("error", "created", "bar", "barEditable", "entry", "setup")
        diag.append(f"{'probe error' if setup_ok else 'setup not verified'}:{json.dumps({k: result.get(k) for k in keys if k in result or k in ('error', 'created', 'bar', 'setup')})[:600]}")
    insert = result.get("insert") if isinstance(result.get("insert"), dict) else {}
    old = (result.get("afterInsert") or {}).get("old") or {}
    i_value, i_rel, i_abs = (_cell(result, "afterInsert", plan[k]) for k in ("i_value", "i_rel", "i_abs"))
    moved = (setup_ok and insert and not insert.get("error") and _number(i_value.get("text")) == 2
             and isinstance(old, dict) and old.get("text") == "" and str(i_rel.get("formula") or "").startswith("="))
    if setup_ok and not moved:
        diag.append(f"insert not verified: {json.dumps({'insert': insert, 'afterInsert': result.get('afterInsert')})[:400]}")
    command = f"“{statement['insert']}” on {line} {statement['header']}"
    if moved and _norm(i_rel.get("formula")) == _norm(plan["rel_formula"]):
        findings.append({
            "where": where,
            "requirement": f"(the requirement: formula references shift with the inserted {line}s) {statement['shift_sentence'][:200]}",
            "problem": f"References not shifted on insert: the probe entered 2 in {plan['value']} and {plan['rel_formula']} in "
                       f"{plan['rel']} on a blank sheet of its own, then used {command}. The value moved to "
                       f"{plan['i_value']}, the formula moved to {plan['i_rel']}, but it still reads “{i_rel.get('formula')}” "
                       f"(expected {plan['i_rel_expect']}) and shows “{i_rel.get('text')}” -- it now points at the new blank {line}.",
            "fix": f"On every {line} insert / delete rewrite each stored formula of the worksheet with one shared reference "
                   f"shifter (the same one copy / paste uses): every reference at or after the inserted {line} moves by the "
                   f"count, ranges grow or move; store the rewritten formula text and recompute.",
        })
    shifted_ok = moved and _norm(i_rel.get("formula")) == _norm(plan["i_rel_expect"])
    abs_text = str(i_abs.get("formula") or "")
    stripped = {_norm(plan["i_abs_expect"].replace("$", "")), _norm(plan["abs_formula"].replace("$", ""))}
    if moved and statement.get("absolute") and "$" not in abs_text and _norm(abs_text) in stripped:
        findings.append({
            "where": where,
            "requirement": f"(the requirement: absolute references; structural edits adjust references) "
                           f"{statement['absolute_sentence'][:120]} / {statement['shift_sentence'][:120]}",
            "problem": f"`$` markers removed by a {line} insert: the probe entered {plan['abs_formula']} in {plan['abs']} (the "
                       f"formula bar showed it), then used {command}. The formula moved to {plan['i_abs']} and now reads "
                       f"“{abs_text}” -- every `$` is gone (expected {plan['i_abs_expect']}), so it no longer is an absolute "
                       f"reference and a later copy / paste adjusts it.",
            "fix": "The reference shifter must parse each reference with its markers (`$`?column `$`?row), shift the "
                   "numbers and write the markers back exactly as they were; never rebuild a reference from bare "
                   "column / row numbers. Apply it on insert AND delete of rows AND columns; store the rewritten text.",
        })

    kept: list[str] = []
    for key, i_key, expect_key, before, value in (("abs", "i_abs", "i_abs_expect", ab, 3),
                                                   ("mix", "i_mix", "i_mix_expect", _cell(result, "setup", plan.get("mix") or ""), 7)):
        if not shifted_ok or not plan.get(key):
            continue
        typed = plan["abs_formula"] if key == "abs" else plan["mix_formula"]
        after = i_abs if key == "abs" else _cell(result, "afterInsert", plan["i_mix"])
        was, now = _number(before.get("text")), str(after.get("text") or "").strip()

        if (_norm(before.get("formula")) == _norm(typed) and was == value and "$" in str(after.get("formula") or "")
                and _norm(after.get("formula")) == _norm(typed) and now and _number(now) != was):
            kept.append(f"after the insert {plan[key]} {typed} (showed {before.get('text')}) moved to {plan[i_key]} but "
                        f"still reads “{after.get('formula')}” and shows “{now}” (expected {plan[expect_key]} = "
                        f"{before.get('text')})")
    delete = result.get("del") if isinstance(result.get("del"), dict) else {}
    d_rel = _cell(result, "afterDelete", plan["d_rel"])
    d_formula = str(d_rel.get("formula") or "")
    token = statement.get("lost_token") or ""
    text = str(d_rel.get("text") or "")

    shows_error = "#" in d_formula or (not token and text.startswith("#"))

    if not shows_error and text.startswith("#") and _norm(d_formula) == _norm(plan["i_rel_expect"]):
        shows_error = True
        diag.append(f"deleted reference: grid {text!r} over the unchanged formula {d_formula!r} (not reported)")

    first = (result.get("afterDelete") or {}).get("old") or {}
    deleted = (shifted_ok and delete and not delete.get("error") and d_formula.startswith("=")
               and ("*10" in _norm(d_formula) or "#" in d_formula) and isinstance(first, dict) and first.get("text") == "")
    if shifted_ok and not deleted:
        diag.append(f"delete not verified: {json.dumps({'del': delete, 'afterDelete': result.get('afterDelete')})[:300]}")

    kept_delete = _beyond_kept(plan, result, deleted, diag)
    if (kept or kept_delete) and statement.get("absolute_fixed"):
        diag.append(f"absolute part not moved ({'; '.join(kept + kept_delete)[:300]}), but the requirement says absolute "
                    f"references stay fixed: {statement['absolute_fixed'][:160]!r} (not reported)")
    elif kept or kept_delete:
        ops = " and ".join(op for op, hit in (("insert", kept), ("delete", kept_delete)) if hit)
        steps = []
        if kept:
            steps.append(f"on a blank sheet of its own the probe entered 2 in {plan['value']} and used {command}; the value "
                         f"moved to {plan['i_value']} and the relative {plan['rel_formula']} moved correctly to "
                         f"{plan['i_rel_expect']}, but " + "; ".join(kept))
        if kept_delete:
            steps.append(f"after “{statement['delete']}” on {line} {plan['delete_header']} the value 4 moved from "
                         f"{plan['n_low']} to {plan['d_n_low']} and the relative reference to it moved correctly, but "
                         + "; ".join(kept_delete))
        findings.append({
            "where": where,
            "requirement": f"(the requirement: formula references shift with inserted / deleted {line}s; it names no "
                           f"exception for absolute references) {statement['shift_sentence'][:200]}",
            "problem": f"Absolute reference not moved by a {line} {ops}: " + ". Then ".join(steps)
                       + f" -- it now reads another {line}.",
            "fix": f"`$` only freezes a reference against copy / paste / fill, never against structural edits: on a {line} "
                   f"insert / delete shift every reference at or after the edited {line}, absolute and mixed parts included, "
                   f"and write each `$` marker back where it was (=$A$1 -> =$A$2 after a row inserted above row 1; "
                   f"=$A$3 -> =$A$2 after row 2 is deleted).",
        })
    findings.extend(_interior_findings(statement, plan, result, deleted, where, line, diag))
    if deleted and statement.get("lost_sentence") and not shows_error and re.search(r"\$?[A-Z]+\$?\d+", d_formula):
        findings.append({
            "where": where,
            "requirement": f"(the requirement: a reference that cannot be preserved displays "
                           f"{token or 'an explicit error'}) {statement['lost_sentence'][:200]}",
            "problem": f"Reference to a deleted {line} silently re-pointed: after the insert {plan['i_rel']} read "
                       f"{plan['i_rel_expect']}; the probe then used “{statement['delete']}” on {line} "
                       f"{plan['delete_header']} (the {line} holding the referenced value 2). The formula moved to "
                       f"{plan['d_rel']}; its formula bar now reads “{d_formula}” (grid: “{text}”) instead of "
                       f"{('a formula carrying ' + token) if token else 'an error'}: the reference to the deleted cell was "
                       f"re-pointed at a cell the user never referenced.",
            "fix": f"When the {line} a reference points at is deleted, replace that reference by the stated error "
                   f"({token or 'e.g. #REF!'}) in the stored formula text (\"=#REF!*10\"), so the grid shows the error and the "
                   f"formula bar the rewritten formula; only references AFTER the deleted {line} move back by one.",
        })
    wrong = []
    for sample in result.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        spec = next((s for s in job.get("samples") or [] if s["cell"] == sample.get("cell")), None)
        if not spec:
            continue
        shown = str(sample.get("text") or "").strip()
        if sample.get("before") != "" or _norm(sample.get("formula")) != _norm(spec["formula"]):
            diag.append(f"error sample {spec['formula']!r} not stored as typed: {json.dumps(sample)[:160]}")
            continue
        if shown != spec["token"]:
            wrong.append((spec, shown))
    if wrong:
        said = {s["kind"]: s for s in statement.get("errors") or []}
        findings.append({
            "where": where,
            "requirement": "(the requirement's error values) " + " / ".join(
                dict.fromkeys(said[spec['kind']]["sentence"][:160] for spec, _ in wrong if spec["kind"] in said)),
            "problem": "Formula error values differ from the stated ones: " + "; ".join(
                f"{spec['formula']} ({spec['kind']}) shows “{shown}” instead of {spec['token']}" for spec, shown in wrong)
                + " (each typed into an empty cell, the formula bar showed it as typed).",
            "fix": "Evaluate formulas with one parser that rejects anything it cannot parse completely (unbalanced "
                   "parentheses, operators the requirement does not list) with the stated malformed-expression value, "
                   "reports unknown function names with the stated value, detects cycles through ranges as well as direct "
                   "references, and maps each failure to exactly the value the requirement names.",
        })

    r_setup = _cell(result, "setup", plan.get("r_sum") or "")
    if plan.get("r_sum") and r_setup:
        r_ins, r_del = _cell(result, "afterInsert", plan["i_r_sum"]), _cell(result, "afterDelete", plan["d_r_sum"])
        r_ok = (_number(r_setup.get("text")) == 12 and _norm(r_setup.get("formula")) == _norm(plan["r_formula"])
                and _norm(r_ins.get("formula")) == _norm(plan["i_r_expect"]))
        r_formula = str(r_del.get("formula") or "")

        r_first, r_last = _cell(result, "afterInsert", plan.get("i_r_first") or ""), _cell(result, "afterInsert", plan.get("i_r_last") or "")
        if (moved and _number(r_setup.get("text")) == 12 and _norm(r_setup.get("formula")) == _norm(plan["r_formula"])
                and _number(r_first.get("text")) == 5 and _number(r_last.get("text")) == 7
                and str(r_ins.get("formula") or "").startswith("=") and "#" not in str(r_ins.get("formula"))
                and _number(r_ins.get("text")) not in (12, None) and _norm(r_ins.get("formula")) != _norm(plan["i_r_expect"])):
            findings.append({
                "where": where,
                "requirement": f"(the requirement: formula references shift with the inserted {line}s) {statement['shift_sentence'][:200]}",
                "problem": f"Range reference broken by a {line} insert: the probe entered 5 and 7 in {plan['r_first']} and "
                           f"{plan['r_last']} and {plan['r_formula']} in {plan['r_sum']} (showed 12), then used {command}. "
                           f"Both values moved to {plan['i_r_first']} / {plan['i_r_last']}, but the formula now in "
                           f"{plan['i_r_sum']} reads “{r_ins.get('formula')}” and shows “{r_ins.get('text')}” (expected "
                           f"{plan['i_r_expect']} = 12).",
                "fix": f"Shift BOTH ends of every range reference with the same shifter as single references (each end "
                       f"independently: an end at or after the inserted {line} moves by the count); keep the start before "
                       f"the end; store the rewritten text and recompute.",
            })
        if deleted and r_ok and "#" in r_formula and statement.get("shrink_sentence"):
            findings.append({
                "where": where,
                "requirement": f"(the requirement: ranges shrink when an end {line} is deleted) {statement['shrink_sentence'][:200]}",
                "problem": f"A range lost its end {line} and became an error: after the insert {plan['i_r_sum']} read "
                           f"{plan['i_r_expect']}; the probe used “{statement['delete']}” on {line} {plan['delete_header']} "
                           f"(the range's first {line}); the formula moved to {plan['d_r_sum']} and now reads “{r_formula}” "
                           f"(grid “{r_del.get('text')}”) instead of the shrunk range.",
                "fix": f"When a deleted {line} is an END of a range reference, move that end inward (the range shrinks); "
                       f"only a range whose every {line} is deleted becomes the error value.",
            })
        elif deleted and r_ok and ("#" in r_formula or _norm(r_formula) == _norm(plan["i_r_expect"])):
            diag.append(f"range end deleted: {plan['i_r_expect']} became {r_formula!r} (grid {r_del.get('text')!r}); the "
                        f"requirement does not say ranges shrink (not reported)")

    more = result.get("more") if isinstance(result.get("more"), dict) else {}
    later: list[dict[str, str]] = []
    for sub in job.get("more") or []:
        res = more.get(sub.get("name"))
        if not isinstance(res, dict):
            continue
        if sub["name"] == "other_axis" and statement.get("other_axis"):
            said = dict(statement, **statement["other_axis"], errors=[], other_axis=None)
            later.extend(structure_findings(said, sub, res, log=log))
        elif sub["name"] == "sort" and statement.get("sort"):
            findings.extend(_sort_findings(statement["sort"], sub, res, diag))
        elif sub["name"] == "pivot" and statement.get("pivot"):
            findings.extend(_pivot_findings(statement["pivot"], sub, res, diag))
    findings.extend(later)
    for text_line in diag:
        log(f"[audit] (diagnostic, not reported) structure probe {where}: {text_line}")
    return findings

def _beyond_kept(plan: dict[str, Any], result: dict[str, Any], deleted: bool, diag: list[str]) -> list[str]:
    beyond = plan.get("beyond") or []
    seen = result.get("beyond") if isinstance(result.get("beyond"), dict) else {}
    inter = result.get("interior") if isinstance(result.get("interior"), dict) else {}
    if not beyond or not seen:
        return []
    moved = inter.get("moved") if isinstance(inter.get("moved"), dict) else {}
    after = inter.get("after") if isinstance(inter.get("after"), dict) else {}
    cells = {}
    for spec in beyond:
        got = seen.get(spec["cell"]) if isinstance(seen.get(spec["cell"]), dict) else {}
        cells[spec["kind"]] = (spec, got.get("before") if isinstance(got.get("before"), dict) else {},
                               got.get("after") if isinstance(got.get("after"), dict) else {})
    typed_ok = all(_norm(b.get("formula")) == _norm(s["formula"]) and _number(b.get("text")) == 12 for s, b, _a in cells.values())
    value_moved = (deleted and _number(moved.get(plan["n_mid"])) == 2 and _number(moved.get(plan["n_low"])) == 4
                   and _number(after.get(plan["d_n_low"])) == 4)
    rel = cells.get("relative")
    contrast = bool(rel) and _norm(rel[2].get("formula")) == _norm(rel[0]["expect"]) and _number(rel[2].get("text")) == 12
    if not (typed_ok and value_moved and contrast):
        if deleted and typed_ok:
            diag.append(f"references beyond the deleted line not judged (value moved={value_moved}, relative moved="
                        f"{contrast}): {json.dumps(seen)[:300]}")
        return []
    kept = []
    for kind in ("absolute", "mixed"):
        if kind not in cells:
            continue
        spec, before, now = cells[kind]
        shown = str(now.get("text") or "").strip()
        if ("$" in str(now.get("formula") or "") and _norm(now.get("formula")) == _norm(spec["formula"])
                and shown and _number(shown) != 12):
            kept.append(f"{spec['cell']} {spec['formula']} (showed 12) still reads “{now.get('formula')}” and shows "
                        f"“{shown}” (expected {spec['expect']} = 12; the relative {rel[0]['formula']} became "
                        f"{rel[0]['expect']} = 12)")
    return kept

def _interior_findings(statement: dict[str, Any], plan: dict[str, Any], result: dict[str, Any], deleted: bool, where: str,
                       line: str, diag: list[str]) -> list[dict[str, str]]:
    inter = result.get("interior")
    if statement.get("range_invalid"):
        if isinstance(inter, dict) and inter.get("dsum"):
            diag.append(f"interior range not judged: the requirement states a policy for ranges touching a deleted cell: "
                        f"{statement['range_invalid'][:160]!r} (not reported)")
        return []
    if not plan.get("n_sum") or not isinstance(inter, dict):
        return []
    moved = inter.get("moved") if isinstance(inter.get("moved"), dict) else {}
    after = inter.get("after") if isinstance(inter.get("after"), dict) else {}
    total = inter.get("sum") if isinstance(inter.get("sum"), dict) else {}
    dsum = inter.get("dsum") if isinstance(inter.get("dsum"), dict) else {}
    ready = (moved.get(plan["n_top"]) == "" and _number(moved.get(plan["n_mid"])) == 2
             and _number(moved.get(plan["n_low"])) == 4 and _norm(total.get("formula")) == _norm(plan["n_formula"])
             and _number(total.get("text")) == 7)
    shifted = deleted and ready and _number(after.get(plan["d_n_top"])) == 1 and _number(after.get(plan["d_n_low"])) == 4
    formula, shown = str(dsum.get("formula") or ""), str(dsum.get("text") or "").strip()
    if not shifted:
        if deleted or ready:
            diag.append(f"interior range not verified (ready={ready}): {json.dumps(inter)[:300]}")
        return []
    if _number(shown) == 5:
        if _norm(formula) != _norm(plan["d_n_expect"]):
            diag.append(f"interior range: {plan['d_n_sum']} reads {formula!r} showing 5 (expected {plan['d_n_expect']}; not reported)")
        return []
    if not (formula.startswith("=") and ("SUM" in _norm(formula) or "#" in formula)) or not shown:
        diag.append(f"interior range: {plan['d_n_sum']} reads {formula!r} / {shown!r} (not the moved SUM; not reported)")
        return []
    said = (statement.get("delete_sentences") or {}).get(statement.get("axis"), "")
    if not said:
        diag.append(f"interior range: {formula!r} shows {shown!r} instead of 5, but no sentence says {line}s shift on "
                    f"deletion (not reported)")
        return []
    return [{
        "where": where,
        "requirement": f"(the requirement: on deletion the following {line}s shift and references follow) "
                       f"{said[:200]}",
        "problem": f"Range broken by deleting a {line} inside it: the probe's {plan['n_sum']} read {plan['n_formula']} "
                   f"(showed 7 = 1 + 2 + 4); it used “{statement['delete']}” on {line} {plan['delete_header']} (the range's "
                   f"middle {line}, holding 2). The other values moved as expected ({plan['d_n_top']} = 1, "
                   f"{plan['d_n_low']} = 4), but the SUM now in {plan['d_n_sum']} reads “{formula}” and shows “{shown}” "
                   f"(expected {plan['d_n_expect']} = 5).",
        "fix": f"Shift each END of a range reference on its own: an end after the deleted {line} moves back by one, an end "
               f"before it stays, so a range with a deleted interior {line} shrinks by one and keeps summing the remaining "
               f"cells; only a reference whose every cell is deleted becomes the error value. Store the rewritten text.",
    }]

def _sort_findings(said: dict[str, Any], sub: dict[str, Any], res: dict[str, Any], diag: list[str]) -> list[dict[str, str]]:
    where = f"route {res.get('route')}"
    setup, after = res.get("setup") or {}, res.get("afterInsert") or {}
    rows = range(2, 2 + len(SORT_ROWS))
    expect = sorted(SORT_ROWS, key=lambda kv: kv[1])
    at = lambda part, coord: part.get(coord) if isinstance(part.get(coord), dict) else {}
    typed = all(_norm(at(setup, f"C{r}").get("formula")) == _norm(f"=B{r}*2")
                and _number(at(setup, f"C{r}").get("text")) == 2 * SORT_ROWS[r - 2][1] for r in rows)
    moved = bool(res.get("sorted")) and all(str(at(after, f"A{r}").get("text") or "").strip() == expect[r - 2][0]
                                            and _number(at(after, f"B{r}").get("text")) == expect[r - 2][1] for r in rows)
    if not (typed and moved):
        diag.append(f"sort not verified: {json.dumps({k: res.get(k) for k in ('error', 'sorted', 'alerts', 'afterInsert')})[:400]}")
        return []
    keys = [k for k, _ in SORT_ROWS]
    stale = []
    for r in rows:
        old = 2 + keys.index(expect[r - 2][0])
        formula = str(at(after, f"C{r}").get("formula") or "")
        if old != r and _norm(formula) == _norm(f"=B{old}*2"):
            stale.append(f"the record {expect[r - 2][0]} ({expect[r - 2][1]}) moved from row {old} to row {r}; its formula "
                         f"still reads “{formula}” and shows “{at(after, f'C{r}').get('text')}” (expected =B{r}*2 = "
                         f"{2 * expect[r - 2][1]})")
    if not stale:
        return []
    return [{
        "where": where,
        "requirement": f"(the requirement: after sorting, references follow the new positions) {said['sentence'][:200]}",
        "problem": f"Sorted formulas keep their old references: on a workbook of its own the probe typed a table "
                   f"(header row; C = B of the same row * 2), selected {sub['range'][0]}:{sub['range'][1]} and used "
                   f"“{said['command']}” (“{said['by']}” = {sub['key']}, “{said['ascending']}”, “{said['header_box']}”). "
                   f"The records moved, but " + "; ".join(stale) + " -- each now computes from another record.",
        "fix": "Sort whole records and rewrite every moved formula through the shared reference shifter: a reference into "
               "the sorted block that pointed at the record's own row points at its new row (relative row offset kept); "
               "store the rewritten formula text and recompute.",
    }]

def _pivot_findings(said: dict[str, Any], sub: dict[str, Any], res: dict[str, Any], diag: list[str]) -> list[dict[str, str]]:
    where = f"route {res.get('route')}"
    keys = [k for k, _ in PIVOT_ROWS]
    last, added = keys[-1], PIVOT_ADDED[0]
    shown = lambda part: {str(v or "").strip() for v in (res.get(part) or {}).values()} if isinstance(res.get(part), dict) else set()
    applied = set(keys) <= shown("setup")
    moved = res.get("moved") if isinstance(res.get("moved"), dict) else {}
    insert = res.get("insert") if isinstance(res.get("insert"), dict) else {}
    inserted = (bool(insert) and not insert.get("error") and str(moved.get(sub["moved"]) or "").strip() == last
                and moved.get(sub["added"][0][0]) == "")
    after = shown("afterInsert")
    refreshed = bool(res.get("refreshed")) and added in after
    if applied and inserted and refreshed and last not in after:
        return [{
            "where": where,
            "requirement": f"(the requirement: after a row change the refreshed pivot uses the adjusted range) {said['sentence'][:200]}",
            "problem": f"Pivot source range not adjusted by a row insert: on a workbook of its own the probe built a pivot over "
                       f"{sub['range'][0]}:{sub['range'][1]} (records {', '.join(keys)}; the result listed all of them), used "
                       f"“{said['insert']}” on row {sub['at']} of the source (the last record {last} moved to {sub['moved']}), "
                       f"typed the record {added} into the new row and clicked “{said['refresh']}”: {added} appears but {last} "
                       f"is gone -- the refresh still read the old range, which now ends one row early.",
            "fix": "Store every derived object's source range (pivot, filter, validation rule) with the sheet and shift it with "
                   "the same reference shifter on every row / column insert and delete (a range grows when a line is inserted "
                   "inside it); refresh reads the stored, shifted range.",
        }]
    diag.append(f"pivot refresh not judged (applied={applied} inserted={inserted} refreshed={refreshed}): "
                f"{json.dumps({k: res.get(k) for k in ('error', 'tabs', 'insert', 'moved', 'alerts', 'afterInsert')})[:400]}")
    return []

def _part_on(part: str) -> bool:
    return os.environ.get(f"ARC_STRUCT_{part}", "1").strip() not in ("0", "false", "off")

def enabled() -> bool:
    return os.environ.get("ARC_STRUCT_PROBE", "1").strip() not in ("0", "false", "off")
