from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

THING_ROLES: dict[str, list[str]] = {
    "cell": ["gridcell", "cell"],
    "row": ["row"],
    "list item": ["listitem"],
    "item": ["listitem"],
    "card": ["article", "listitem"],
    "article": ["article"],
}
_CHANGE = r"(?:change|edit|value|entry|input|content|text)s?"
COMMIT = re.compile(
    r"\b(?:click(?:s|ing)?|select(?:s|ing)?|tap(?:s|ping)?|mov(?:e|es|ing)\s+to|focus(?:es|ing)?)\s+(?:on\s+)?"
    r"(?:another|a\s+different|any\s+other|a\s+new)\s+(?P<thing>[a-z][\w-]*(?:\s+(?!(?:never|not|no|does|doesn't|cannot|won't)\b)[a-z][\w-]*)?)\s+"
    r"(?:(?:also|then|immediately|automatically)\s+)?(?P<verb>commits|saves|confirms|applies|submits|persists)\s+"
    r"(?:the\s+|an?\s+|any\s+|its\s+|their\s+)?(?:(?:pending|typed|current|uncommitted|unsaved|in-progress|edited)\s+)?"
    + _CHANGE + r"\b", re.IGNORECASE)
ESCAPE = re.compile(
    r"\b(?:(?:press(?:es|ing)?|hit(?:s|ting)?|typ(?:e|es|ing))\s+(?:the\s+)?)?(?:Esc|Escape)(?:\s+key)?\s+"
    r"(?:(?:also|then|immediately)\s+)?(?:cancels|discards|reverts|abandons|undoes|drops)\b", re.IGNORECASE)
BUDGET_MS = 25000

OTHER_OBJECT = re.compile(r"\b(?:dialog|modal|menu|popover|pop-?up|drawer|overlay|picker|dropdown|lightbox|search|"
                          r"filter|palette|tooltip|toast|banner|panel|sidebar)s?\b", re.IGNORECASE)
SENTENCE_END = re.compile(r"[.!?]\s")

def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start), -1) + 1
    right = min([i for i in (text.find(". ", end), text.find("; ", end)) if i >= 0] or [len(text)])
    return text[left: right + 1].strip()[:240]

def _span(text: str, pos: int, ends: re.Pattern) -> tuple[int, int]:
    left = max([m.end() for m in ends.finditer(text, 0, pos)] or [0])
    right = next((m.start() + 1 for m in ends.finditer(text, pos)), len(text))
    return left, right

def _escape_for(text: str, commit: re.Match, thing: str) -> re.Match | None:
    own = _span(text, commit.start(), SENTENCE_END)
    after = _span(text, own[1] + 1, SENTENCE_END) if own[1] < len(text) else own
    thing_word = re.compile(r"\b" + re.escape(thing) + r"s?\b", re.IGNORECASE)
    for escape in ESCAPE.finditer(text):
        span = _span(text, escape.start(), SENTENCE_END)
        sentence = text[slice(*span)]
        if OTHER_OBJECT.search(sentence):
            continue
        if span in (own, after) or thing_word.search(sentence):
            return escape
    return None

def commit_statements(whole: str) -> list[dict[str, Any]]:
    text = " ".join(str(whole or "").split())
    found: dict[str, dict[str, Any]] = {}
    for match in COMMIT.finditer(text):
        words = match.group("thing").lower().split()

        thing = " ".join(words[-2:]) if " ".join(words[-2:]) in THING_ROLES else words[-1]
        if thing in found:
            continue
        escape = _escape_for(text, match, thing)
        found[thing] = {
            "thing": thing, "roles": list(THING_ROLES.get(thing, [])),
            "sentence": _sentence(text, match.start(), match.end()),
            "escape": bool(escape), "escape_sentence": _sentence(text, escape.start(), escape.end()) if escape else "",
        }
    return list(found.values())

def probe_job(statement: dict[str, Any], snapshot: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not statement.get("roles"):
        return None
    from .entry_checks import aria_nodes

    hints: list[str] = []
    for entry in snapshot or []:
        route = str(entry.get("route") or "")
        if not route.startswith("/") or ":" in route or entry.get("error"):
            continue
        roles = {role for _i, role, _n in aria_nodes(str(entry.get("aria") or ""))}
        if roles & set(statement["roles"]) or (entry.get("hasGrid") and "gridcell" in statement["roles"]):
            hints.append(route)
    return {"thing": statement["thing"], "roles": statement["roles"], "escape": bool(statement.get("escape")),
            "routes": list(dict.fromkeys(hints))[:4]}

PROBE_SCRIPT = r"""
const { chromium } = require('@playwright/test');
const BUDGET = Number(process.env.ARC_COMMIT_PROBE_BUDGET || 25000);
const INTERACTIVE = 'button,a[href],input,select,textarea,label,summary,[role=button],[role=link],[role=checkbox],' +
  '[role=switch],[role=menuitem],[role=combobox],[contenteditable=true],[contenteditable=""]';
const EDITABLE = 'input,textarea,[contenteditable=true],[contenteditable=""],[contenteditable=plaintext-only]';
const RISKY = /delete|remove|trash|archive|discard|destroy|clear|reset|leave|unsubscribe|(log|sign)[-_ ]?(out|off)/i;
const token = (n) => 'zq' + String(Date.now() % 100000).padStart(5, '0') + 'k' + n;
(async () => {
  const [base, spec] = process.argv.slice(2);
  const job = JSON.parse(spec);
  const out = { thing: job.thing, route: null, role: null, instances: 0, modes: [], escapes: [], visited: [] };
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  let inflight = 0;
  page.on('request', () => { inflight += 1; });
  page.on('requestfinished', () => { inflight = Math.max(0, inflight - 1); });
  page.on('requestfailed', () => { inflight = Math.max(0, inflight - 1); });
  const settle = async () => {
    await page.waitForTimeout(700);
    const start = Date.now();
    while (inflight > 0 && Date.now() - start < 3000) await page.waitForTimeout(100);
    await page.waitForTimeout(200);
  };
  // every visible instance of `role`: index, name, text, middle; `free` = its middle lands on it and on no other control
  const scan = (role) => page.getByRole(role).evaluateAll((els, interactive) => els.map((el, index) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const visible = r.width >= 16 && r.height >= 10 && s.visibility !== 'hidden' && s.display !== 'none'
      && r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const hit = visible ? document.elementFromPoint(x, y) : null;
    const control = hit && hit.closest(interactive);
    const free = !!hit && el.contains(hit) && (!control || control === el || !el.contains(control));
    const values = Array.from(el.querySelectorAll('input,textarea')).map((i) => i.value).join(' ');
    return { index, name: (el.getAttribute('aria-label') || '').trim(), text: ((el.innerText || '') + ' ' + values).trim(),
      x, y, top: Math.round(r.top), visible, free };
  }), INTERACTIVE);
  const count = async (role) => (await scan(role)).filter((c) => c.visible && c.free).length;
  const findRole = async () => {
    for (const role of job.roles) if ((await count(role)) >= 3) return role;
    return null;
  };
  const visit = async (route) => {
    out.visited.push(route);
    try { await page.goto(base + route, { waitUntil: 'networkidle', timeout: 10000 }); } catch (e) { return null; }
    await page.waitForTimeout(300);
    return findRole();
  };
  const t0 = Date.now();
  try {
    // ---- the page: the snapshot's routes, "/", then the pages "/" links to (or its first item-title button opens)
    let role = null;
    for (const route of [...job.routes, '/']) { if ((role = await visit(route))) break; }
    if (!role) {
      await visit('/');
      const hrefs = await page.evaluate((risky) => Array.from(document.querySelectorAll('a[href]'))
        .map((a) => new URL(a.getAttribute('href'), location.href))
        .filter((u) => u.origin === location.origin && !RegExp(risky, 'i').test(u.pathname))
        .map((u) => u.pathname + u.search), RISKY.source);
      for (const href of [...new Set(hrefs)].filter((h) => h !== '/').slice(0, 6)) { if ((role = await visit(href))) break; }
    }
    if (!role) {
      await visit('/');
      const opener = page.locator(':is(li,[role=listitem],article) :is(h1,h2,h3,h4,h5,h6) :is(button,[role=button])').first();
      if (await opener.count()) {
        const name = ((await opener.innerText()) || '').trim();
        if (name && !RISKY.test(name)) {
          await opener.click({ timeout: 3000 }).catch(() => {});
          await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
          await page.waitForTimeout(300);
          role = await findRole();
        }
      }
    }
    if (!role) { out.error = 'no page shows 3 visible instances of ' + job.roles.join('/'); throw null; }
    const url = new URL(page.url());
    out.route = url.pathname + url.search;
    out.role = role;
    const route = out.route;
    const all = (await scan(role)).filter((c) => c.visible && c.free);
    out.instances = all.length;
    const names = all.map((c) => c.name);
    const unique = (c) => c.name && names.filter((n) => n === c.name).length === 1;
    const key = (c) => ({ index: c.index, name: unique(c) ? c.name : '' });
    const label = (c) => c.name || ('#' + c.index);
    const empties = all.filter((c) => !c.text);
    const filled = all.filter((c) => c.text);
    // the pool of instances to edit: empty ones, spread out; B (the one clicked) is another empty one, in another line
    const pool = empties.filter((c, i) => i % 3 === 1);
    const other = [...empties].reverse().find((c) => pool.length && c.top !== pool[0].top && !pool.slice(0, 5).includes(c));
    if (pool.length < 3 || !other) { out.error = 'too few empty instances (' + empties.length + ')'; throw null; }
    const find = async (k) => {
      const now = await scan(role);
      return (k.name && now.find((c) => c.name === k.name)) || now[k.index] || null;
    };
    const reload = async () => {
      await page.goto(base + route, { waitUntil: 'networkidle', timeout: 10000 });
      for (let i = 0; i < 20 && (await count(role)) < 3; i++) await page.waitForTimeout(150);
    };
    const holds = async (k, value) => { const c = await find(k); return !!c && c.text.includes(value); };
    // edit instance k in `mode` with `value`: shown = the value is visible in the instance or in the editor it opened
    const edit = async (mode, k, value) => {
      const c = await find(k);
      if (!c) return { shown: false, why: 'instance gone' };
      await page.evaluate((sel) => document.querySelectorAll(sel).forEach((e) => e.setAttribute('data-arc-pre', '1')), EDITABLE);
      if (mode === 'dblclick') await page.mouse.dblclick(c.x, c.y); else await page.mouse.click(c.x, c.y);
      await page.waitForTimeout(250);
      if (mode === 'dblclick') await page.keyboard.press('Control+A');
      await page.keyboard.type(value, { delay: 15 });
      await page.waitForTimeout(200);
      const own = await page.getByRole(role).nth(c.index).evaluate((el, [value, sel]) => {
        const a = document.activeElement;
        const editor = a && a.matches && a.matches(sel) && (el.contains(a) || !a.hasAttribute('data-arc-pre'));
        const typed = editor && ((a.value || '') + ' ' + (a.innerText || '')).includes(value);
        const values = Array.from(el.querySelectorAll('input,textarea')).map((i) => i.value).join(' ');
        return { inCell: ((el.innerText || '') + ' ' + values).includes(value), inEditor: !!typed,
          editor: editor ? (a.getAttribute('aria-label') || a.tagName.toLowerCase()) : '' };
      }, [value, EDITABLE]).catch(() => ({ inCell: false, inEditor: false, editor: '' }));
      return { shown: own.inCell || own.inEditor, editor: own.editor };
    };
    let next = 0;
    const take = () => pool[next++] || null;
    const late = () => Date.now() - t0 > BUDGET;
    const doCommit = !job.keys;  // a keys job (key_statements) runs the keyboard part only
    for (const mode of doCommit ? ['dblclick', 'type'] : []) {
      if (late()) { out.budget = true; break; }
      const a = take();
      if (!a) break;
      const value = token(out.modes.length * 3 + 1);
      const result = { mode, cell: label(a), other: label(other) };
      out.modes.push(result);
      const typed = await edit(mode, key(a), value);
      result.shown = typed.shown;
      result.editor = typed.editor;
      if (!typed.shown) { await reload(); continue; }
      const b = await find(key(other));
      const target = b && await page.getByRole(role).nth(b.index).evaluate((el, [x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return !!hit && el.contains(hit);
      }, [b.x, b.y]).catch(() => false);
      if (!target) { result.error = 'the other instance is covered'; await reload(); continue; }
      await page.mouse.click(b.x, b.y);
      await settle();
      result.afterClick = await holds(key(a), value);
      await reload();
      result.persisted = await holds(key(a), value);
      if (result.persisted) continue;
      // the control: the same edit, committed with Enter
      const d = take();
      if (!d || late()) { result.enter = null; continue; }
      const second = token(out.modes.length * 3 + 2);
      const again = await edit(mode, key(d), second);
      result.enter = { cell: label(d), shown: again.shown };
      if (again.shown) {
        await page.keyboard.press('Enter');
        await settle();
        await reload();
        result.enter.persisted = await holds(key(d), second);
      }
      if (result.enter.persisted) break;  // the contrast is found: the other mode is not needed
    }
    // ---- Escape: in every mode whose typing showed (platform sheet v141: Escape after a double-click edit cancelled,
    // Escape after typing straight into a selected cell saved), edit one instance (a filled one when there is one),
    // press Escape, reload. Each mode takes an instance of its own.
    const usedEsc = [];
    // (a mode whose typing did not show above is skipped; one the commit part did not reach is tried)
    const escModes = job.escape && doCommit ? ['dblclick', 'type'].filter((m) => !out.modes.some((r) => r.mode === m && r.shown === false)) : [];
    for (const mode of [...new Set(escModes)]) {
      if (late()) { out.budget = true; break; }
      const e = filled.find((c) => c.text.length <= 40 && c.top !== other.top && !usedEsc.includes(c)) || take();
      if (!e) break;
      usedEsc.push(e);
      const value = token(9 + usedEsc.length);
      const old = (await find(key(e)) || {}).text || '';
      const typed = await edit(mode, key(e), value);
      const esc = { mode, cell: label(e), old, shown: typed.shown };
      out.escapes.push(esc);
      if (!typed.shown) { await reload(); continue; }
      await page.keyboard.press('Escape');
      await page.waitForTimeout(400);
      esc.afterEscape = await holds(key(e), value);
      await settle();
      await reload();
      const now = await find(key(e));
      esc.persisted = !!now && now.text.includes(value);
      esc.oldStays = !!now && (!old || now.text.includes(old));
    }
    // ---- keys (v144g, key_statements): Enter / Tab / an arrow key / typing over a filled instance, each on instances of
    // its own (an empty one whose right and lower neighbours are empty too), each read back after a reload
    if (job.keys) {
      out.keys = {};
      const k0 = Date.now();
      const keysLate = () => Date.now() - k0 > Number(process.env.ARC_COMMIT_KEYS_BUDGET || 30000);
      const near = (c, dx, dy) => all.filter((o) => o !== c && (dx ? Math.abs(o.top - c.top) <= 3 && o.x > c.x
        : Math.abs(o.x - c.x) <= 3 && o.y > c.y)).sort((p, q) => (dx ? p.x - q.x : p.y - q.y))[0] || null;
      const used = new Set();
      const triple = () => {
        for (const c of empties) {
          const r = near(c, 1, 0), b = near(c, 0, 1);
          if (!r || !b || r.text || b.text || [c, r, b].some((o) => used.has(o))) continue;
          [c, r, b].forEach((o) => used.add(o));
          return { a: c, right: r, below: b };
        }
        return null;
      };
      // where the selection and the focus are: indices with aria-selected, the instance holding the focus, and whether
      // the focus is still in the grid (or an editor) -- typing / Enter never go to a control outside it
      const where = () => page.getByRole(role).evaluateAll((els, sel) => {
        const a = document.activeElement;
        const selected = []; let focus = -1;
        els.forEach((el, i) => {
          if (el.getAttribute('aria-selected') === 'true') selected.push(i);
          if (a && a !== document.body && el.contains(a)) focus = i;
        });
        const editing = !!(a && a.matches && a.matches(sel));
        const box = a && a.closest ? a.closest('[role=grid],[role=treegrid],[role=table],table') : null;
        return { selected, focus, editing, inGrid: !a || a === document.body || !!box || focus >= 0 || editing,
          active: a ? ((a.getAttribute && a.getAttribute('aria-label')) || a.tagName.toLowerCase()) : '' };
      }, EDITABLE).catch(() => ({ selected: [], focus: -1, editing: false, inGrid: false, active: '' }));
      const at = async (c) => { const now = await find(key(c)); return now ? now.index : -1; };
      const on = async (st, c) => { const i = await at(c); return i >= 0 && (st.selected.includes(i) || (!st.selected.length && st.focus === i)); };
      const text = async (c) => ((await find(key(c))) || {}).text || '';
      let mode = 'type';
      let n = 20;
      // Enter: typed, Enter, where is the selection, reload -- stored?
      const te = triple();
      if (te) {
        const value = token(n++);
        let typed = await edit(mode, key(te.a), value);
        if (!typed.shown) { await reload(); mode = 'dblclick'; typed = await edit(mode, key(te.a), value); }
        const r = { mode, cell: label(te.a), below: label(te.below), shown: typed.shown };
        out.keys.enter = r;
        if (typed.shown) {
          await page.keyboard.press('Enter');
          await page.waitForTimeout(400);
          const st = await where();
          r.movedDown = await on(st, te.below);
          r.stayed = await on(st, te.a);
          await settle();
          await reload();
          r.stored = (await text(te.a)).includes(value);
        } else await reload();
      }
      // Tab: typed X, Tab, where is the selection; then (focus still in the grid) typed Y, Enter; reload: X stored in A,
      // Y in A too (merged), Y in the right neighbour?
      const tt = !keysLate() && triple();
      if (tt) {
        const x = token(n++), y = token(n++);
        const typed = await edit(mode, key(tt.a), x);
        const r = { mode, cell: label(tt.a), right: label(tt.right), shown: typed.shown };
        out.keys.tab = r;
        if (typed.shown) {
          await page.keyboard.press('Tab');
          await page.waitForTimeout(400);
          const st = await where();
          r.movedRight = await on(st, tt.right);
          r.stayed = await on(st, tt.a);
          r.active = st.active;
          r.inGrid = st.inGrid;
          if (st.inGrid) {
            await page.keyboard.type(y, { delay: 15 });
            await page.waitForTimeout(150);
            await page.keyboard.press('Enter');
            r.typedNext = true;
          }
          await settle();
          await reload();
          const a = await text(tt.a), right = await text(tt.right);
          r.stored = a.includes(x);
          r.merged = a.includes(x) && a.includes(y);
          r.nextInA = !!r.typedNext && a.includes(y);
          r.nextInRight = !!r.typedNext && right.includes(y);
          r.after = a.slice(0, 60);
        } else await reload();
      }
      // an arrow key (typing straight into a selected instance only: in an open editor an arrow moves the caret)
      const ta = mode === 'type' && !keysLate() && triple();
      if (ta) {
        const value = token(n++);
        const typed = await edit('type', key(ta.a), value);
        const r = { mode: 'type', key: 'ArrowDown', cell: label(ta.a), below: label(ta.below), shown: typed.shown };
        out.keys.arrow = r;
        if (typed.shown) {
          await page.keyboard.press('ArrowDown');
          await page.waitForTimeout(400);
          const st = await where();
          r.movedDown = await on(st, ta.below);
          r.stayed = await on(st, ta.a);
          r.shownAfter = (await text(ta.a)).includes(value);
          await settle();
          r.shownSettled = (await text(ta.a)).includes(value);
          await reload();
          r.stored = (await text(ta.a)).includes(value);
        } else await reload();
      }
      // an arrow key with NO edit open (r49 #9: only for a stated "arrow keys move the selection [when no edit is in
      // progress]"): click an empty cell, it must be selected with no editor focused, then ArrowDown; nothing typed
      const ti = (job.idle || []).includes('arrow_moves') && !keysLate() && triple();
      if (ti) {
        const c = await find(key(ti.a));
        const r = { mode: 'idle', key: 'ArrowDown', cell: label(ti.a), below: label(ti.below) };
        out.keys.arrowIdle = r;
        if (c) {
          await page.mouse.click(c.x, c.y);
          await page.waitForTimeout(300);
          const st0 = await where();
          r.selected = await on(st0, ti.a);
          r.editing = st0.editing;
          if (r.selected && !r.editing && st0.inGrid) {
            await page.keyboard.press('ArrowDown');
            await page.waitForTimeout(400);
            const st = await where();
            r.movedDown = await on(st, ti.below);
            r.stayed = await on(st, ti.a);
            r.editingAfter = st.editing;
          }
        }
      }
      // typing over a filled instance (straight, no double-click), Enter, reload: replaced or appended?
      const tf = mode === 'type' && !keysLate()
        && filled.find((c) => c.text.length <= 40 && !/\n/.test(c.text) && !used.has(c) && !usedEsc.includes(c));
      if (tf) {
        used.add(tf);
        const value = token(n++);
        const old = tf.text;
        const typed = await edit('type', key(tf), value);
        const r = { mode: 'type', cell: label(tf), old, shown: typed.shown };
        out.keys.replace = r;
        if (typed.shown) {
          await page.keyboard.press('Enter');
          await settle();
          await reload();
          const now = await text(tf);
          r.stored = now.includes(value);
          r.oldKept = now.split(value).join('').includes(old);  // the token's digits never count as the old text
          r.after = now.slice(0, 80);
        } else await reload();
      }
      // a formula cell typed over with the value it DISPLAYS (v144p; explore-sheet-v141b A11: double-click, replace
      // "=A1+B1" by its value 5, Enter -- ignored, the formula stayed). Only in a grid where Enter stored above, with a
      // constant formula (no cell reference); the raw content is read where a selected cell shows it (an input /
      // textarea holding exactly the formula: a formula bar) or else in the editor a double-click opens (then Escape).
      const norm = (s) => String(s || '').replace(/\s+/g, '').toUpperCase();
      const raw = async (c, formula) => {
        const k = await find(key(c));
        if (!k) return { formula: false, via: 'gone' };
        await page.mouse.click(k.x, k.y);
        await page.waitForTimeout(300);
        const bar = await page.evaluate((f) => Array.from(document.querySelectorAll('input,textarea'))
          .some((i) => String(i.value || '').replace(/\s+/g, '').toUpperCase() === f), norm(formula));
        if (bar) return { formula: true, via: 'selected' };
        await page.mouse.dblclick(k.x, k.y);
        await page.waitForTimeout(300);
        const inEditor = await page.evaluate(() => {
          const a = document.activeElement;
          return a && a !== document.body ? String(a.value !== undefined ? a.value : a.innerText || '') : '';
        });
        await page.keyboard.press('Escape');
        await page.waitForTimeout(200);
        return { formula: norm(inEditor) === norm(formula), via: norm(inEditor) === norm(formula) ? 'editor' : 'none',
          seen: String(inEditor).slice(0, 40) };
      };
      const fe = out.keys.enter;
      const tq = fe && fe.stored === true && !keysLate() && empties.find((c) => !used.has(c));
      if (tq) {
        used.add(tq);
        const p = 100 + (Date.now() % 800), q = 7 + (Date.now() % 13);
        const formula = '=' + p + '+' + q, value = String(p + q);
        const r = { mode: fe.mode, cell: label(tq), formula, value };
        out.keys.formula = r;
        const typed = await edit(fe.mode, key(tq), formula);
        r.typed = typed.shown;
        if (typed.shown) {
          await page.keyboard.press('Enter');
          await settle();
          await reload();
          const display = await text(tq);
          r.display = display.slice(0, 40);
          r.evaluated = display.includes(value) && !display.includes('=');
          if (r.evaluated && !keysLate()) {
            r.rawBefore = await raw(tq, formula);
            await reload();
            // double-click, select all, type the displayed value: the editor must hold exactly that value (the cell
            // shows it anyway, so "visible" proves nothing here)
            await edit('dblclick', key(tq), value);
            r.overEditor = await page.evaluate(() => {
              const a = document.activeElement;
              return a && a !== document.body ? String(a.value !== undefined ? a.value : a.innerText || '').trim() : '';
            });
            r.overShown = norm(r.overEditor) === norm(value);
            if (r.overShown) {
              await page.keyboard.press('Enter');
              await settle();
              await reload();
              r.displayAfter = (await text(tq)).slice(0, 40);
              r.rawAfter = await raw(tq, formula);
              r.kept = r.rawAfter.formula;
            } else { await page.keyboard.press('Escape'); await reload(); }
          }
        } else await reload();
      }
      out.keys.ms = Date.now() - k0;
    }
  } catch (error) {
    if (error) out.error = String(error).slice(0, 200);
  }
  out.ms = Date.now() - t0;
  await browser.close();
  console.log('ARC_COMMIT_JSON' + JSON.stringify(out));
})().catch((error) => { console.log('ARC_COMMIT_ERROR' + String(error).slice(0, 300)); process.exit(2); });
"""

def run_probe(backend: Path, base: str, job: dict[str, Any], log=print) -> dict[str, Any] | None:
    from .fsutil import run_cmd

    tmp = Path(tempfile.mkdtemp(prefix="arc-commit-"))
    try:
        script = tmp / "probe.cjs"
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
        code, output = run_cmd(["node", str(script), base, json.dumps(job)], cwd=backend, timeout=120,
                               env={"NODE_PATH": str(backend / "node_modules"),
                                    "ARC_COMMIT_PROBE_BUDGET": str(BUDGET_MS)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    match = re.search(r"ARC_COMMIT_JSON(.*)", output)
    if not match:
        log(f"[audit] commit probe unavailable ({output.strip()[-200:]!r})")
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

def probe(project_dir: Path, job: dict[str, Any] | None, log=print) -> dict[str, Any] | None:
    backend = project_dir / "backend"
    if not job or not (backend / "node_modules" / "@playwright" / "test").is_dir():
        return None
    from .fsutil import BackgroundProcess, free_port, fresh_data_env, npm_bin, wait_http

    port = free_port(3400)
    server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **fresh_data_env(project_dir)})
    try:
        ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if not ready:
            log("[audit] commit probe: application did not start")
            return None
        return run_probe(backend, f"http://127.0.0.1:{port}", job, log=log)
    finally:
        server.stop()

def commit_findings(statement: dict[str, Any], result: dict[str, Any] | None, log=print) -> list[dict[str, str]]:
    if not isinstance(result, dict):
        return []
    thing = statement["thing"]
    where = f"route {result.get('route')}"
    findings: list[dict[str, str]] = []
    lost = [m for m in result.get("modes") or [] if isinstance(m, dict) and m.get("shown") and m.get("persisted") is False
            and isinstance(m.get("enter"), dict) and m["enter"].get("persisted") is True and not m.get("error")]
    if lost:
        m = lost[0]
        how = "double-clicked it and typed" if m["mode"] == "dblclick" else "clicked it and typed"
        findings.append({
            "where": where,
            "requirement": f"(the requirement: clicking another {thing} commits the change) {statement['sentence'][:200]}",
            "problem": f"Typed value lost after clicking another {thing}: in the {thing} “{m.get('cell')}” the probe {how} "
                       f"a new value (it was visible), then clicked the middle of the {thing} “{m.get('other')}” with the "
                       f"mouse; after the requests settled and a reload, “{m.get('cell')}” did not hold the value"
                       + (" (it was already gone before the reload)" if m.get("afterClick") is False else "")
                       + f". The same edit committed with Enter (“{m['enter'].get('cell')}”) was kept: the save works, only "
                       f"the click on another {thing} throws the typed text away.",
            "fix": f"Keep the text being typed in state as it is typed (onChange / onInput -> a draft), and commit that draft "
                   f"when the mouse goes down on another {thing} -- before the selection change re-renders anything (e.g. a "
                   f"capture-phase onMouseDown on the grid / list container that commits an open edit first) -- instead of "
                   f"reading the element's DOM text in onBlur after the re-render. Never re-write the content of the {thing} "
                   f"being edited from a ref callback or an effect while it is being edited (an inline `ref={{(el) => ...}}` "
                   f"runs on every render). Enter and Escape keep working as they do.",
        })
    else:
        log(f"[audit] (diagnostic, not reported) commit probe {where} {thing}: "
            f"{json.dumps({k: result.get(k) for k in ('role', 'instances', 'modes', 'error', 'budget')})[:400]}")
    escapes = [e for e in (result.get("escapes") or []) if isinstance(e, dict)]
    saved = [e for e in escapes if e.get("shown") and e.get("persisted") is True]
    if statement.get("escape") and saved:
        esc = saved[0]
        how = "double-clicked it and typed" if esc.get("mode") == "dblclick" else "clicked it and typed"
        kept = [e for e in escapes if e.get("shown") and e.get("persisted") is False and e.get("mode") != esc.get("mode")]
        findings.append({
            "where": where,
            "requirement": f"(the requirement: Escape cancels an uncommitted change) {statement['escape_sentence'][:200]}",
            "problem": f"Escape did not cancel: in the {thing} “{esc.get('cell')}” the probe {how} a new value (it was "
                       f"visible), pressed Escape and reloaded the page -- the typed value was saved"
                       + (f" and the old value “{str(esc.get('old'))[:40]}” was gone" if esc.get("old") and not esc.get("oldStays") else "")
                       + "."
                       + (" After a double-click edit Escape did cancel: only typing straight into a selected "
                          f"{thing} ignores it." if kept and esc.get("mode") == "type" else ""),
            "fix": "On Escape discard the draft and show the last committed value again -- in every way of starting an edit "
                   "(double-click, and typing straight into a selected one); make the blur (or unmount) that follows "
                   "closing the editor NOT save (a flag set by the Escape handler that the blur handler honours, or a "
                   "draft cleared before the editor closes).",
        })
    elif escapes:
        log(f"[audit] (diagnostic, not reported) commit probe Escape {where}: {json.dumps(escapes)[:300]}")
    return findings

def enabled() -> bool:
    return os.environ.get("ARC_COMMIT_PROBE", "1").strip() not in ("0", "false", "off")

_NEG = re.compile(r"\b(?:not|never|no|cannot|without)\b|n't\b", re.IGNORECASE)
_COMMITS = r"(?:commits?|saves?|confirms?|stores?|applies)"
_MOVES = r"(?:moves?|advances?|goes|jumps?|navigates?)"
KEY_PATTERNS: dict[str, re.Pattern] = {
    "enter_commits": re.compile(r"\b(?:[Pp]ress(?:es|ing)?\s+(?:the\s+)?)?Enter\b(?:\s+key)?[^.;]{0,60}?\b" + _COMMITS + r"\b"),
    "enter_moves": re.compile(r"\bEnter\b(?:\s+key)?[^.;]{0,60}?\b" + _MOVES + r"\b[^.;]{0,40}?\b(?:down|below|next\s+row)\b"),

    "tab_commits": re.compile(r"(?:\b[Pp]ress(?:es|ing)?\s+(?:the\s+)?Tab\b|\bTab\s+key\b|(?:^|[.;,:]\s+|\b(?:and|or)\s+)Tab\b)"
                              r"[^.;]{0,60}?\b" + _COMMITS + r"\b"),
    "tab_moves": re.compile(r"(?:\b[Pp]ress(?:es|ing)?\s+(?:the\s+)?Tab\b|\bTab\s+key\b|(?:^|[.;,:]\s+|\b(?:and|or)\s+)Tab\b)"
                            r"[^.;]{0,60}?\b" + _MOVES + r"\b[^.;]{0,40}?\b(?:right|next\s+(?:cell|column))\b"),
    "arrow_commits": re.compile(r"\barrow(?:\s+keys?)?\b[^.;]{0,60}?\b" + _COMMITS + r"\b", re.IGNORECASE),
    "arrow_moves": re.compile(r"\barrow\s+keys?\b[^.;]{0,60}?\b" + _MOVES + r"\b", re.IGNORECASE),
    "replace": re.compile(r"\btyp(?:e|es|ing)\b[^.;]{0,60}?\breplaces?\b[^.;]{0,30}?\b(?:content|contents|value|text)\b",
                          re.IGNORECASE),

    "as_typed": re.compile(r"\b(?:stor(?:e|es|ed|ing)|sav(?:e|es|ed|ing)|kept|keeps?|persist(?:s|ed)?|record(?:s|ed)?)\b"
                           r"[^.;]{0,40}?\b(?:exactly\s+)?as\s+(?:typed|entered)\b|"
                           r"\b(?:commit(?:s|ted|ting)?|enter(?:s|ed|ing)?)\b[^.;]{0,60}?\breplaces?\b[^.;]{0,30}?"
                           r"\b(?:content|contents|value|text|formula)\b", re.IGNORECASE),
}
_CELL = re.compile(r"\bcells?\b", re.IGNORECASE)

_EDIT_WORD = r"(?:edit(?:s|ing|ed|or)?|typing)"
MODE_IDLE = re.compile(
    r"\b(?:(?:when|while|whenever|if|as\s+long\s+as)\s+)?(?:there\s+is\s+)?no\s+(?:\w+\s+){0,3}?" + _EDIT_WORD
    + r"(?:\s+(?:is|are))?(?:\s+(?:in\s+progress|open|active|underway))?"
    r"|\b(?:(?:when|while|whenever|if)\s+)?(?:(?:the|a)\s+cell\s+is\s+|the\s+user\s+is\s+|you\s+are\s+)?not\s+(?:currently\s+)?"
    r"(?:being\s+)?(?:in\s+)?" + _EDIT_WORD + r"(?:\s+mode)?"
    r"|\b(?:outside|out)\s+(?:of\s+)?(?:an?\s+|the\s+)?" + _EDIT_WORD + r"(?:\s+mode)?"
    r"|\bwithout\s+(?:an?\s+|any\s+)?(?:open\s+|active\s+)?" + _EDIT_WORD + r"(?:\s+(?:in\s+progress|open))?"
    r"|\b(?:in|during)\s+navigation\s+mode\b", re.IGNORECASE)
MODE_EDITING = re.compile(
    r"\b(?:while|when|whenever|during|whilst|if)\s+(?:(?:a|the)\s+)?(?:cell\s+|user\s+|you\s+)?(?:is\s+|are\s+)?(?:being\s+)?"
    + _EDIT_WORD + r"(?:\s+(?:a|the)\s+cell)?"
    r"|\b(?:while|when|whenever|if)\s+(?:an?|the)\s+(?:cell\s+)?(?:edit|editor)\s+is\s+(?:in\s+progress|open|active)"
    r"|\bin\s+edit(?:ing)?\s+mode\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[.;!?](?:\s|$)")

_CARET = re.compile(r"\b(?:caret|insertion\s+point|text\s+cursor)\b", re.IGNORECASE)

PROBE_MODE = {"enter_commits": "editing", "enter_moves": "editing", "tab_commits": "editing", "tab_moves": "editing",
              "arrow_commits": "editing", "arrow_moves": "editing", "replace": "idle", "as_typed": "editing"}

def statement_mode(clause: str, typing_is_mode: bool = True) -> tuple[str | None, str]:
    for mode, pattern in (("idle", MODE_IDLE), ("editing", MODE_EDITING)):
        hit = pattern.search(clause)
        if hit and (typing_is_mode or not re.search(r"\btyping\b", hit.group(0), re.I)):
            return mode, clause[:hit.start()] + " " + clause[hit.end():]
    return None, clause

_TAB_KEY = re.compile(r"\b[Pp]ress(?:es|ing)?\s+(?:the\s+)?Tab\b|\bTab\s+key\b|(?:^|[.;,:]\s+|\b(?:and|or)\s+)Tab\b")

_SUBCLAUSE = re.compile(r",\s+(?:and|but|whereas|yet)\b", re.IGNORECASE)

def _own_part(clause: str, start: int, end: int) -> str:
    lo, hi = 0, len(clause)
    for cut in _SUBCLAUSE.finditer(clause):
        if cut.end() <= start:
            lo = cut.end()
        elif cut.start() >= end:
            hi = cut.start()
            break
    return clause[lo:hi]

def key_statements(whole: str) -> dict[str, Any] | None:
    text = " ".join(str(whole or "").split())
    said: dict[str, str] = {}
    modes: dict[str, str] = {}
    for name, pattern in KEY_PATTERNS.items():
        for match in pattern.finditer(text):
            sentence = text[slice(*_span(text, match.start(), SENTENCE_END))]

            before = text[max(0, match.start() - 14): match.start()] if name == "as_typed" else ""
            if _NEG.search(match.group(0) + " " + before) or not _CELL.search(sentence) or OTHER_OBJECT.search(sentence):
                continue
            left, right = _span(text, match.start(), CLAUSE_END)
            mode, rest = statement_mode(text[left:right], name != "replace")

            own = _own_part(text[left:right], match.start() - left, match.end() - left)
            if _NEG.search(statement_mode(own, name != "replace")[1]) or (name == "arrow_moves" and _CARET.search(rest)):
                continue
            said[name] = _sentence(text, match.start(), match.end())
            if mode:
                modes[name] = mode
            break
    if not said:
        return None
    out: dict[str, Any] = {"thing": "cell", "roles": list(THING_ROLES["cell"]), "said": said}
    if modes:
        out["modes"] = modes

    tab_stated: list[str] = []
    for hit in _TAB_KEY.finditer(text):
        sentence = text[slice(*_span(text, hit.start(), SENTENCE_END))]
        if (_CELL.search(sentence) or re.search(r"\b" + _EDIT_WORD + r"\b", sentence, re.I)) and not OTHER_OBJECT.search(sentence):
            tab_stated.append(_sentence(text, hit.start(), hit.end()))
    if tab_stated:
        out["tab_stated"] = list(dict.fromkeys(tab_stated))
    return out

def _in_mode(statement: dict[str, Any], name: str, mode: str) -> bool:
    if name not in (statement.get("said") or {}):
        return False
    stated = (statement.get("modes") or {}).get(name)
    if stated:
        return stated == mode
    return mode == ("idle" if name == "arrow_moves" else PROBE_MODE.get(name))

def keys_job(statement: dict[str, Any] | None, snapshot: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not statement or not statement.get("said"):
        return None
    job = probe_job({"thing": statement["thing"], "roles": statement["roles"], "escape": False}, snapshot)
    if not job:
        return None
    job = {**job, "keys": sorted(statement["said"])}
    if _in_mode(statement, "arrow_moves", "idle"):
        job["idle"] = ["arrow_moves"]
    return job

def keys_probe(project_dir: Path, job: dict[str, Any] | None, log=print) -> dict[str, Any] | None:
    return probe(project_dir, job, log=log)

def keys_enabled() -> bool:
    return enabled() and os.environ.get("ARC_COMMIT_KEYS", "1").strip() not in ("0", "false", "off")

_FIX_ENTER_COMMITS = ("Make Enter commit the value typed into the cell, as the requirement states: store it (after a reload "
                      "the cell holds it).")
_FIX_ENTER_MOVES = "After Enter commits a cell, move the selection to the cell below, as the requirement states."
_FIX_TAB_MERGED = ("Pressing Tab must end the edit of the cell: store the value typed so far in that cell and never leave the "
                   "caret in it, so that what is typed next is not appended to the stored value.")
_FIX_TAB_LOST = "Pressing Tab must not drop the value typed into the cell: store it (as Enter does)."
_FIX_TAB_MOVES = "After typing into a cell, Tab moves the selection to the next cell on the right, as the requirement states."
_FIX_ARROW_LOST = ("An arrow key pressed while a value is being typed into a cell must not drop it: either store the typed "
                   "value before the selection moves (as Enter does), or keep the edit open and the selection where it is; "
                   "a cell must never keep showing text that was not stored.")
_FIX_ARROW_COMMITS = ("An arrow key commits the value being typed into a cell, as the requirement states: store it before "
                      "the selection moves; a cell must never keep showing text that was not stored.")
_FIX_ARROW_MOVES = "While a cell is being edited, an arrow key moves the selection to the neighbouring cell, as the requirement states."
_FIX_ARROW_IDLE = ("With a cell selected and no edit open, an arrow key moves the selection to the neighbouring cell "
                   "(ArrowDown: the cell below), as the requirement states.")
_FIX_REPLACE = ("Typing on a selected cell that is not being edited replaces its content, as the requirement states: the "
                "edit starts from the typed characters, not from the old content.")

def keys_findings(statement: dict[str, Any] | None, result: dict[str, Any] | None, log=print) -> list[dict[str, str]]:
    if not statement or not isinstance(result, dict) or not isinstance(result.get("keys"), dict):
        return []
    all_said = statement.get("said") or {}

    said = {name: text for name, text in all_said.items() if _in_mode(statement, name, PROBE_MODE.get(name, ""))}
    idle_arrow = all_said.get("arrow_moves") if _in_mode(statement, "arrow_moves", "idle") else None
    keys = result["keys"]
    get = lambda name: keys.get(name) if isinstance(keys.get(name), dict) else {}
    enter, tab, arrow, replace = get("enter"), get("tab"), get("arrow"), get("replace")
    where = f"route {result.get('route')}"
    enter_ok = bool(enter.get("shown")) and enter.get("stored") is True
    contrast = ("(no key statement; the contrast: in this grid a typed value committed with Enter was stored after a "
                f"reload — “{enter.get('cell')}”)")
    how = lambda r: "clicked it and typed" if r.get("mode") == "type" else "double-clicked it and typed"
    findings: list[dict[str, str]] = []

    def add(requirement: str, problem: str, fix: str) -> None:
        findings.append({"where": where, "requirement": requirement[:320], "problem": problem, "fix": fix})

    others_store = any(r.get("stored") is True for r in (tab, arrow, replace))
    if "enter_commits" in said and enter.get("shown") and enter.get("stored") is False and others_store:
        add(f"(the requirement: Enter commits) {said['enter_commits']}",
            f"Enter did not commit: in the cell “{enter.get('cell')}” the probe {how(enter)} a new value (it was "
            f"visible), pressed Enter and reloaded -- the value was not stored (another way of committing in the same grid "
            f"did store its value).", _FIX_ENTER_COMMITS)
    if "enter_moves" in said and enter_ok and enter.get("movedDown") is False:
        add(f"(the requirement: Enter moves down) {said['enter_moves']}",
            f"Enter did not move the selection down: after typing into “{enter.get('cell')}” and Enter the cell below "
            f"(“{enter.get('below')}”) was not selected" + (" -- the selection stayed on the edited cell" if enter.get("stayed") else "") + ".",
            _FIX_ENTER_MOVES)

    tab_said = said.get("tab_commits") or said.get("tab_moves")
    moves_too = (" " + _FIX_TAB_MOVES) if said.get("tab_moves") else ""

    judged = [t for t in (said.get("tab_commits"), said.get("tab_moves")) if t]
    other_tab = [s for s in statement.get("tab_stated") or [] if not any(s in t or t in s for t in judged)]
    tab_contrast = enter_ok and not other_tab
    if tab.get("shown"):
        basis = f"(the requirement: Tab) {tab_said}" if tab_said else contrast
        if tab.get("merged") and (tab_said or tab_contrast):
            add(basis, f"Tab merged two entries into one cell: in “{tab.get('cell')}” the probe {how(tab)} a value, "
                       f"pressed Tab, typed a second value and pressed Enter (as a person filling a row does); after a "
                       f"reload “{tab.get('cell')}” held BOTH values (“{tab.get('after')}”) and “{tab.get('right')}” "
                       f"did not get the second one -- Tab left the caret in the same cell"
                       + (" (the selection stayed on it)" if tab.get("stayed") else "") + ".", _FIX_TAB_MERGED + moves_too)
        elif tab.get("stored") is False and (said.get("tab_commits") or tab_contrast):
            add(basis, f"Tab lost the typed value: in “{tab.get('cell')}” the probe {how(tab)} a value (it was visible), "
                       f"pressed Tab" + (", typed a second value and pressed Enter" if tab.get("typedNext") else "")
                       + f"; after a reload “{tab.get('cell')}” did not hold the first value"
                       + (" (the second value had replaced it)" if tab.get("nextInA") else "") + ".", _FIX_TAB_LOST + moves_too)
        elif said.get("tab_moves") and tab.get("movedRight") is False:
            add(f"(the requirement: Tab moves right) {said['tab_moves']}",
                f"Tab did not move the selection right: after typing into “{tab.get('cell')}” and Tab, "
                f"“{tab.get('right')}” was not selected (focus: {tab.get('active')!r}).", _FIX_TAB_MOVES)

    arrow_said = said.get("arrow_commits")
    if arrow.get("shown") and arrow.get("stored") is False and arrow.get("movedDown") and (arrow_said or enter_ok):
        ghost = arrow.get("shownSettled") is True
        add(f"(the requirement: an arrow key commits) {arrow_said}" if arrow_said else contrast,
            f"An arrow key moved the selection but dropped the edit: in “{arrow.get('cell')}” the probe clicked it and typed "
            f"a value (it was visible) and pressed ArrowDown; the selection moved to “{arrow.get('below')}”"
            + (f" while “{arrow.get('cell')}” kept SHOWING the typed value" if ghost else "")
            + " -- after a reload the value was not stored" + (" (the grid showed text that was never saved)" if ghost else "") + ".",
            _FIX_ARROW_COMMITS if arrow_said else _FIX_ARROW_LOST)
    elif said.get("arrow_moves") and arrow.get("shown") and arrow.get("movedDown") is False and arrow.get("stayed"):

        add(f"(the requirement: while editing, arrow keys move) {said['arrow_moves']}",
            f"ArrowDown did not move the selection while editing: after typing into “{arrow.get('cell')}” the selection "
            f"stayed on it.", _FIX_ARROW_MOVES)

    idle = get("arrowIdle")
    if (idle_arrow and idle.get("selected") is True and idle.get("editing") is False and idle.get("movedDown") is False
            and idle.get("stayed") is True):
        add(f"(the requirement: arrow keys move the selection) {idle_arrow}",
            f"ArrowDown did not move the selection: the probe clicked the empty cell “{idle.get('cell')}” (it was selected, "
            f"no editor open), typed nothing and pressed ArrowDown; the selection stayed on it instead of moving to "
            f"“{idle.get('below')}”.", _FIX_ARROW_IDLE)

    if "replace" in said and replace.get("shown") and replace.get("stored") and replace.get("oldKept"):
        add(f"(the requirement: typing replaces the content) {said['replace']}",
            f"Typing over a filled cell appended instead of replacing: the probe clicked “{replace.get('cell')}” (holding "
            f"“{str(replace.get('old'))[:40]}”), typed a new value and pressed Enter; after a reload it held "
            f"“{replace.get('after')}” -- the old content was kept.", _FIX_REPLACE)
    off_mode = sorted(set(all_said) - set(said) - ({"arrow_moves"} if idle_arrow else set()))
    if off_mode:
        log(f"[audit] (diagnostic, not reported) commit probe keys {where}: stated only in another mode than the probe's "
            f"step: {json.dumps({n: (statement.get('modes') or {}).get(n) for n in off_mode})}")

    formula = get("formula")

    over = {n: all_said[n] for n in ("replace", "as_typed")
            if n in all_said and (statement.get("modes") or {}).get(n) in (None, "editing")}
    over_said = over.get("replace") or over.get("as_typed")
    if (over_said and formula.get("evaluated") is True and formula.get("overShown") is True and formula.get("kept") is True
            and (formula.get("rawBefore") or {}).get("formula") is True):
        what = "typing replaces the content" if over.get("replace") else "a committed value is stored as typed"
        via = (formula.get("rawAfter") or {}).get("via")
        add(f"(the requirement: {what}) {over_said}",
            f"Typing a formula cell's displayed value over it was ignored: the probe entered the formula "
            f"“{formula.get('formula')}” into “{formula.get('cell')}” (after a reload it showed “{formula.get('value')}”), "
            f"double-clicked it, selected all, typed “{formula.get('value')}” (the editor held exactly that) and pressed "
            f"Enter; after a reload the cell still held the formula (read "
            + ("in the input showing the selected cell's content" if via == "selected" else "in the editor a double-click opens")
            + ") -- an entry equal to the value the cell displays is not committed.",
            "Decide whether an edit changed anything by comparing the entry with the cell's stored RAW content (the "
            "formula text), never with the displayed (computed) value: an entry that differs from the raw content is "
            "committed -- and stored as typed -- even when it equals what the cell shows.")
    log(f"[audit] (diagnostic, not reported) commit probe keys {where}: stated={sorted(all_said)} "
        f"{json.dumps(keys)[:1100]} -> {len(findings)} finding(s)")
    return findings
