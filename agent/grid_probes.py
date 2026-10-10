from __future__ import annotations

import ast
import json
import operator
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ITEMS = ("recalculation", "extent")
SCREEN_COLS, SCREEN_ROWS = 10, 20
CLEANUP_RESERVE = 10.0
ERROR_VALUE = re.compile(r"^#(?:REF!|VALUE!|NAME\?|DIV/0!|N/A|NUM!|NULL!|ERROR!?|CIRC\w*!?|SPILL!|CALC!)$", re.I)
CELL_SEG = re.compile(r"^cells?$", re.I)
COORD_KEYS = ("coord", "address", "ref", "cell", "cellId", "cell_id", "key", "position", "id", "name")
RAW_KEYS = ("raw", "input", "formula", "source", "rawValue", "raw_value", "text", "content")
VAL_KEYS = ("value", "display", "displayValue", "display_value", "computed", "computedValue", "computed_value",
            "result", "formatted", "shown")
MARK = {"control": "GPCTL01", "col": "GPCOL01", "row": "GPROW01", "corner": "GPEND01", "rcontrol": "GPCTL02"}
A_VALUE = 4321
CHAINS = (

    ("referenced value", (("A1", str(A_VALUE)), ("B1", "=A1+7000+13")), "B1", A_VALUE + 7013, "A1", 7013),
    ("referenced formula", (("A2", str(A_VALUE)), ("B2", "=A2*5"), ("C2", "=B2+8000+26")), "C2", A_VALUE * 5 + 8026,
     "B2", 8026),
)

def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n

def col_letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out

REF = re.compile(r"(?<![\w$#@-])\$?([A-Z]{1,2})\$?([1-9]\d{0,3})(?![\w-])")
RANGE = re.compile(r"(?<![\w-])([A-Z]{1,2})([1-9]\d{0,3}):([A-Z]{1,2})([1-9]\d{0,3})(?![\w-])")
CELL_CONTEXT = re.compile(r"(?i)\bcells?\s+\$?[A-Z]{1,2}\$?[1-9]\d{0,3}\b")
ROW_PHRASE = re.compile(r"(?i)\brows?\s+(?:through\s+|to\s+|up\s+to\s+)?([1-9]\d{0,3})\b")
COL_PHRASE = re.compile(r"\b[Cc]olumns?\s+(?:through\s+|to\s+|up\s+to\s+)?([A-Z]{1,2})\b(?![\w-])")
QUOTED = re.compile(r"`([^`\n]*)`|“([^”\n]*)”|\"([^\"\n]*)\"")

def _strip_literals(text: str) -> str:
    def keep(m):
        inner = next(g for g in m.groups() if g is not None)
        return " " + inner + " " if inner.startswith("=") else " "
    return QUOTED.sub(keep, text)

CELL_LIST = re.compile(r"\b[Cc]ells?\s+(\$?[A-Z]{1,2}\$?[1-9]\d{0,3}(?:\s*(?:,\s*and|,|and|or)\s*(?:cell\s+)?\$?[A-Z]{1,2}\$?[1-9]\d{0,3})*)")
REF_ROLE = re.compile(r"(?<![\w$#@-])([A-Z]{1,2})([1-9]\d{0,3})\s+(?:formula|displays|contains|value\b|is\s+(?:selected|empty)|and\s+its)")
FORMULA_TOKEN = re.compile(r"=[^\s]+")

def requirement_extent(requirement: str) -> dict[str, Any]:
    out: dict[str, Any] = {"grid": False, "col": 0, "row": 0, "refs": [], "phrases": []}
    try:
        text = _strip_literals(str(requirement or ""))
        refs = set()
        for m in RANGE.finditer(text):
            refs.add((m.group(1), int(m.group(2))))
            refs.add((m.group(3), int(m.group(4))))
        for m in CELL_LIST.finditer(text):
            for c, r in REF.findall(m.group(1)):
                refs.add((c, int(r)))
        for m in REF_ROLE.finditer(text):
            refs.add((m.group(1), int(m.group(2))))
        for m in FORMULA_TOKEN.finditer(text):
            around = text[m.start():m.end() + 80]
            if "#" in around or re.search(r"(?i)invalid|out of range", around):
                continue
            for c, r in REF.findall(m.group(0)):
                refs.add((c, int(r)))
        if len(refs) < 2:
            return out
        out["grid"] = True
        out["refs"] = sorted(f"{c}{r}" for c, r in refs)[:60]
        col = max(col_index(c) for c, _r in refs)
        row = max(r for _c, r in refs)
        for m in ROW_PHRASE.finditer(text):
            row = max(row, int(m.group(1)))
            out["phrases"].append(m.group(0))
        for m in COL_PHRASE.finditer(text):
            col = max(col, col_index(m.group(1)))
            out["phrases"].append(m.group(0))
        out["col"], out["row"] = col, row
        out["phrases"] = out["phrases"][:10]
    except Exception:
        pass
    return out

ZERO_SENTENCE = re.compile(
    r"(?i)\b(?:an?\s+|the\s+)?(?:empty|blank|cleared)\s+(?:or\s+\w+\s+)?(?:cells?|references?)\b[^.\n]{0,80}?"
    r"\b(?:counts?|count(?:ed)?|treated|evaluates?|evaluated|is\s+(?:read|taken|used)|are\s+(?:read|taken|used))\s+"
    r"(?:as\s+)?(?:0|zero)\b")
NEGATED = re.compile(r"(?i)\b(?:not|never|no\s+longer)\b|n't\b")
ERROR_SENTENCE = re.compile(r"(?i)\b(?:empty|blank|cleared)\s+(?:cells?|references?)\b[^.\n]{0,100}?"
                            r"#(?:REF!|VALUE!|N/A|NULL!|ERROR!?|NAME\?)")
_OPS ={ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

def _arith(node):
    if isinstance(node, ast.Expression):
        return _arith(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_arith(node.left), _arith(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _arith(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    raise ValueError("unsupported")

def eval_formula(formula: str, values: dict[str, Any], formulas: dict[str, str], depth: int = 0) -> float | None:
    try:
        if depth > 8 or not formula.startswith("="):
            return None
        body = formula[1:]

        def sub(m):
            coord = m.group(1) + m.group(2)
            if coord in formulas:
                value = eval_formula(formulas[coord], values, formulas, depth + 1)
            else:
                value = values.get(coord, 0.0)
            if value is None:
                raise ValueError("unresolved")
            return "(" + repr(float(value)) + ")"
        expr = REF.sub(sub, body)
        if not re.fullmatch(r"[\d.\s()+\-*/e]*", expr):
            return None
        return _arith(ast.parse(expr, mode="eval"))
    except Exception:
        return None

def _scenario_chunks(text: str) -> list[str]:
    parts = re.split(r"\n\s*-\s+name:", text)
    return parts[1:] if len(parts) > 1 else []

def empty_cell_rule(requirement: str) -> dict[str, Any] | None:
    try:
        text = str(requirement or "")
        if cleared_error_expected(text):
            return None
        for m in ZERO_SENTENCE.finditer(text):
            if not NEGATED.search(m.group(0)):
                return {"rule": "zero", "source": "sentence", "quote": m.group(0)[:200]}
        for chunk in _scenario_chunks(text):
            values: dict[str, float] = {}
            formulas: dict[str, str] = {}
            for vm in re.finditer(r"(?i)\bcell\s+([A-Z]{1,2}[1-9]\d{0,3})\s+(?:value|contains|=|with value)\s+`(-?\d+(?:\.\d+)?)`",
                                  chunk):
                values[vm.group(1)] = float(vm.group(2))
            for fm in re.finditer(r"(?i)\b(?:cell\s+)?([A-Z]{1,2}[1-9]\d{0,3})\s+formula\s+`(=[^`]+)`", chunk):
                formulas[fm.group(1)] = fm.group(2).strip()
            if not formulas:
                continue
            cleared = set()
            for cm in re.finditer(r"(?i)\b(?:selects?|clicks?)\s+(?:cell\s+)?([A-Z]{1,2}[1-9]\d{0,3})\b[^.\n]{0,60}?"
                                  r"\bpress(?:es)?\s+(?:Delete|Backspace)", chunk):
                cleared.add(cm.group(1))
            for cm in re.finditer(r"(?i)\bclears?\s+(?:cell\s+)?([A-Z]{1,2}[1-9]\d{0,3})\b", chunk):
                cleared.add(cm.group(1))
            if not cleared:
                continue
            for tm in re.finditer(r"(?i)\b(?:cell\s+)?([A-Z]{1,2}[1-9]\d{0,3})\s+(?:still\s+)?displays\s+`(-?\d+(?:\.\d+)?)`",
                                  chunk):
                target, shown = tm.group(1), float(tm.group(2))
                if target not in formulas or target in cleared:
                    continue
                vals = dict(values)
                forms = {k: v for k, v in formulas.items() if k not in cleared}
                for c in cleared:
                    vals[c] = 0.0
                computed = eval_formula(formulas[target], vals, forms)
                refs = set(a + b for a, b in REF.findall(formulas[target]))
                if computed is not None and abs(computed - shown) < 1e-9 and (refs & cleared or _depends(target, cleared, formulas)):
                    return {"rule": "zero", "source": "scenario",
                            "quote": f"{target} {formulas[target]} displays {tm.group(2)} after {', '.join(sorted(cleared))} is cleared"}
        return None
    except Exception:
        return None

def cleared_error_expected(requirement: str) -> str | None:
    try:
        text = str(requirement or "")
        m = ERROR_SENTENCE.search(text)
        if m:
            return m.group(0)[:200]
        for chunk in _scenario_chunks(text):
            if not re.search(r"(?i)\bpress(?:es)?\s+(?:Delete|Backspace)\b|\bclears?\s+(?:cell\s+)?[A-Z]{1,2}[1-9]", chunk):
                continue
            formulas = {fm.group(1) for fm in re.finditer(r"(?i)\b(?:cell\s+)?([A-Z]{1,2}[1-9]\d{0,3})\s+formula\s+`=", chunk)}
            for tm in re.finditer(r"(?i)\b(?:cell\s+)?([A-Z]{1,2}[1-9]\d{0,3})\s+(?:still\s+)?displays\s+`(#[^`]+)`", chunk):
                if tm.group(1) in formulas:
                    return tm.group(0)[:200]
        return None
    except Exception:
        return None

def _depends(target: str, cleared: set[str], formulas: dict[str, str], depth: int = 0) -> bool:
    if depth > 8 or target not in formulas:
        return False
    refs = set(a + b for a, b in REF.findall(formulas[target]))
    return bool(refs & cleared) or any(_depends(r, cleared, formulas, depth + 1) for r in refs)

def discover_api(project_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"routes": [], "cell": [], "notes": []}
    try:
        from . import role_ladder as js
        files = js.backend_files(Path(project_dir))
        prefixes = js.mount_prefixes(files)
        routes = []
        for f in files:
            bases = prefixes.get(f.rel)
            if not bases:
                continue
            for route in f.routes:
                for base in bases:
                    full = js.join_path(base, route["path"])
                    entry = {"method": route["method"], "path": full.rstrip("/") or "/", "where": f"{f.rel}:{route['line']}"}
                    if entry not in routes:
                        routes.append(entry)
        out["routes"] = routes
        cells = []
        for r in routes:
            if r["method"] not in ("put", "post", "patch"):
                continue
            segs = [s for s in r["path"].split("/") if s]
            idx = [i for i, s in enumerate(segs) if CELL_SEG.match(s)]
            if not idx:
                continue
            tail = segs[idx[-1] + 1:]
            if len(tail) > 1 or (tail and not tail[0].startswith(":")):
                continue
            r = dict(r, single=bool(tail))
            cells.append(r)
        method_rank = {"put": 0, "patch": 1, "post": 2}
        cells.sort(key=lambda r: (r["single"], method_rank.get(r["method"], 3), len(r["path"])))
        out["cell"] = cells[:3]
    except Exception as exc:
        out["notes"].append(f"route scan failed: {type(exc).__name__}: {str(exc)[:120]}")
    return out

def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]

def _collection(path: str) -> str | None:
    segs = _segments(path)
    for i, s in enumerate(segs):
        if s.startswith(":"):
            return "/" + "/".join(segs[:i]) if i else None
    return None

def _route(routes, method, path):
    norm = lambda p: re.sub(r":\w+", ":", p.rstrip("/"))
    return next((r for r in routes if r["method"] == method and norm(r["path"]) == norm(path)), None)

class _OutOfTime(Exception):
    pass

def _http(base: str, method: str, path: str, body, deadline: float) -> tuple[int, Any, str]:
    left = deadline - time.monotonic()
    if left < 1.0:
        raise _OutOfTime()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method.upper())
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=min(10.0, left)) as resp:
            status, raw = resp.status, resp.read(8_000_000)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(200_000)
        except Exception:
            raw = b""
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = None
    return status, parsed, text[:300]

def _unwrap(obj):
    for _ in range(3):
        if isinstance(obj, dict) and len(obj) == 1:
            inner = next(iter(obj.values()))
            if isinstance(inner, dict):
                obj = inner
                continue
        if isinstance(obj, dict) and isinstance(obj.get("data"), dict) and "id" not in obj:
            obj = obj["data"]
            continue
        break
    return obj

def _id_of(obj):
    obj = _unwrap(obj)
    if isinstance(obj, dict):
        for key in ("id", "_id", "uuid", "key", "slug"):
            if isinstance(obj.get(key), (int, str)) and str(obj.get(key)) != "":
                return str(obj[key])
    return None

def _param_stem(name: str) -> str:
    stem = re.sub(r"(?i)(?:_?id|_?key|_?uuid)$", "", name.lstrip(":"))
    return stem.lower()

def _child_id(record, stem: str):
    if not stem:
        return None
    seen = 0
    stack = [record]
    while stack and seen < 5000:
        node = stack.pop(0)
        seen += 1
        if isinstance(node, dict):
            for key, value in node.items():
                low = key.lower()
                if isinstance(value, list) and value and low.rstrip("s") == stem.rstrip("s") and isinstance(value[0], dict):
                    found = _id_of(value[0])
                    if found:
                        return found
            for key, value in node.items():
                low = key.lower().replace("_", "")
                if isinstance(value, (int, str)) and re.fullmatch(r"(?:active|current|default|first)?" + re.escape(stem) + r"id", low):
                    return str(value)
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(v for v in node[:50] if isinstance(v, (dict, list)))
    return None

def _cell_lookup(data, coord: str, scope_id: str | None = None):
    root = data
    if scope_id is not None:
        found = _find_scope(data, scope_id)
        if found is not None:
            root = found
    return _cell_in(root, coord, 0)

def _find_scope(data, scope_id, depth=0):
    if depth > 8:
        return None
    if isinstance(data, dict):
        if str(data.get("id", object())) == str(scope_id) and any(isinstance(v, (dict, list)) for v in data.values()):
            return data
        for v in data.values():
            hit = _find_scope(v, scope_id, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(data, list):
        for v in data[:200]:
            hit = _find_scope(v, scope_id, depth + 1)
            if hit is not None:
                return hit
    return None

def _cell_entry(entry):
    if isinstance(entry, dict):
        raw = next((entry[k] for k in RAW_KEYS if k in entry), None)
        has_value = any(k in entry for k in VAL_KEYS)
        value = next((entry[k] for k in VAL_KEYS if k in entry), None)
        return {"raw": raw, "value": value, "has_value": has_value}
    if isinstance(entry, (str, int, float)) or entry is None:
        return {"raw": entry, "value": entry, "has_value": not (isinstance(entry, str) and entry.startswith("="))}
    return None

HISTORY_KEY = re.compile(r"(?i)history|undo|redo|revision|version|audit|log|prev|snapshot|changes")

def _cell_in(node, coord, depth):
    if depth > 10:
        return None
    if isinstance(node, dict):
        if coord in node:
            hit = _cell_entry(node[coord])
            if hit is not None:
                return hit
        for k, v in node.items():
            if isinstance(v, (dict, list)) and not HISTORY_KEY.search(str(k)):
                hit = _cell_in(v, coord, depth + 1)
                if hit is not None:
                    return hit
    elif isinstance(node, list):
        for item in node[:20000]:
            if isinstance(item, dict) and any(str(item.get(k, "")).upper() == coord for k in COORD_KEYS):
                hit = _cell_entry(item)
                if hit is not None:
                    return hit
        for item in node[:2000]:
            if isinstance(item, (dict, list)):
                hit = _cell_in(item, coord, depth + 1)
                if hit is not None:
                    return hit
    return None

def _num(value):
    try:
        if isinstance(value, bool) or value is None:
            return None
        text = str(value).strip().replace(",", "")
        return float(text) if re.fullmatch(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", text, re.I) else None
    except Exception:
        return None

def _is_error(value) -> bool:
    return isinstance(value, str) and bool(ERROR_VALUE.match(value.strip()))

def _empty(cell) -> bool:
    return cell is None or (cell.get("raw") in ("", None) and cell.get("value") in ("", None))

BATCH_SHAPES = (
    ("cells map", lambda cs: {"cells": {c: r for c, r in cs}}),
    ("cells list coord/raw", lambda cs: {"cells": [{"coord": c, "raw": r} for c, r in cs]}),
    ("cells map raw", lambda cs: {"cells": {c: {"raw": r} for c, r in cs}}),
    ("cells list coord/value", lambda cs: {"cells": [{"coord": c, "value": r} for c, r in cs]}),
    ("cells list address/value", lambda cs: {"cells": [{"address": c, "value": r} for c, r in cs]}),
    ("updates list", lambda cs: {"updates": [{"coord": c, "raw": r} for c, r in cs]}),
    ("flat map", lambda cs: {c: r for c, r in cs}),
)
SINGLE_SHAPES = (
    ("raw", lambda r: {"raw": r}),
    ("value", lambda r: {"value": r}),
    ("input", lambda r: {"input": r}),
    ("content", lambda r: {"content": r}),
)

class _Api:

    def __init__(self, base, routes, route, deadline, notes):
        self.base, self.routes, self.route, self.deadline, self.notes = base, routes, route, deadline, notes
        self.shape = None
        self.read_paths: list[str] = []

    def create_record(self, label):
        coll = _collection(self.route["path"])
        if not coll or not _route(self.routes, "post", coll):
            self.notes.append(f"no create endpoint (POST {coll}) for the collection of {self.route['path']}")
            return None
        seen = []
        for body in ({"name": label}, {"name": label}, {"title": label}, {}):
            status, data, _t = _http(self.base, "post", coll, body, self.deadline)
            seen.append(status)
            if status >= 500:
                time.sleep(0.5)
            if 200 <= status < 300 and _id_of(data):
                record = {"id": _id_of(data), "created": _unwrap(data), "collection": coll, "params": {}}
                self._resolve(record)
                return record if record.get("cell_path") else None
        self.notes.append(f"POST {coll} did not create a record with an id (statuses {seen})")
        return None

    def _resolve(self, record):
        path = self.route["path"]
        segs = _segments(path)
        params = [s for s in segs if s.startswith(":")]
        if self.route.get("single"):
            params = params[:-1]
        values = {}
        source = record["created"]
        coll_get = _route(self.routes, "get", record["collection"] + "/:id")
        fetched = None
        for i, p in enumerate(params):
            if i == 0:
                values[p] = record["id"]
                continue
            stem = _param_stem(p)
            got = _child_id(source, stem)
            if got is None and coll_get is not None:
                if fetched is None:
                    status, data, _t = _http(self.base, "get", _fill(coll_get["path"], {coll_get["path"].split("/")[-1]: record["id"]}),
                                             None, self.deadline)
                    fetched = data if 200 <= status < 300 else {}
                got = _child_id(fetched, stem)
            if got is None:
                prefix = path[:path.index(p)].rstrip("/")
                if _route(self.routes, "post", prefix):
                    status, data, _t = _http(self.base, "post", _fill(prefix, values), {"name": "Probe"}, self.deadline)
                    if 200 <= status < 300:
                        got = _id_of(data)
            if got is None:
                self.notes.append(f"could not resolve {p} of {path}")
                return
            values[p] = got
        record["params"] = values
        record["scope"] = values[params[-1]] if len(params) > 1 else None
        record["cell_path"] = _fill(path, values)
        reads = []
        cut = path
        for _ in range(len(segs)):
            for cand in (cut,):
                r = _route(self.routes, "get", cand)
                if r and all(s in values for s in _segments(r["path"]) if s.startswith(":")):
                    reads.append(_fill(r["path"], values))
            cut = cut.rsplit("/", 1)[0]
            if not cut:
                break
        if coll_get is not None:
            reads.append(_fill(coll_get["path"], {coll_get["path"].split("/")[-1]: record["id"]}))
        record["reads"] = list(dict.fromkeys(p for p in reads if ":" not in p))

    def write(self, record, cells, shape=None):
        shape = shape or self.shape
        if self.route.get("single"):
            ok = True
            for coord, raw in cells:
                path = record["cell_path"].rstrip("/") + "/" + coord
                status, _d, text = _http(self.base, self.route["method"], path, shape[1](raw), self.deadline)
                ok = ok and 200 <= status < 300
                if not ok:
                    self.notes.append(f"{self.route['method'].upper()} {path} -> {status} {text[:80]}")
            return ok
        status, _d, text = _http(self.base, self.route["method"], record["cell_path"], shape[1](cells), self.deadline)
        if not 200 <= status < 300:
            self.notes.append(f"{self.route['method'].upper()} {record['cell_path']} [{shape[0]}] -> {status} {text[:80]}")
        return 200 <= status < 300

    def read(self, record, coords):
        last = {c: None for c in coords}
        for path in record.get("reads", []):
            status, data, _t = _http(self.base, "get", path, None, self.deadline)
            if not 200 <= status < 300 or data is None:
                continue
            got = {c: _cell_lookup(data, c, record.get("scope")) for c in coords}
            if any(v is not None for v in got.values()):
                record["read_path"] = path
                return got
        return last

    def calibrate(self, record, coord, raw):
        shapes = SINGLE_SHAPES if self.route.get("single") else BATCH_SHAPES
        for shape in shapes:
            if not self.write(record, [(coord, raw)], shape):
                continue
            cell = self.read(record, [coord]).get(coord)
            if cell and (str(cell.get("raw")) == raw or str(cell.get("value")) == raw):
                self.shape = shape
                return True
        return False

def _fill(path, values):
    return "/".join(str(values.get(s, s)) if s.startswith(":") else s for s in path.split("/"))

def page_routes(project_dir: Path) -> list[str]:
    paths: list[str] = []
    try:
        from .continuation import existing_app
        for entry in existing_app(Path(project_dir)).get("routes", []):
            p = entry.split(" -> ")[0].strip()
            if ":" in p and p not in paths:
                paths.append(p)
    except Exception:
        pass
    if not paths:
        try:
            assets = Path(project_dir) / "frontend" / "dist" / "assets"
            for js_file in sorted(assets.glob("*.js"))[:20]:
                text = js_file.read_text(encoding="utf-8", errors="replace")[:6_000_000]
                for m in re.finditer(r"path\s*:\s*[\"'`](/[^\"'`\s]*:[A-Za-z_]\w*[^\"'`\s]*)[\"'`]", text):
                    if m.group(1) not in paths:
                        paths.append(m.group(1))
        except Exception:
            pass
    return paths

def record_urls(routes: list[str], record: dict, api_route: str) -> list[str]:
    api_static = {s.lower() for s in _segments(api_route) if not s.startswith(":") and s.lower() != "api"}
    params = list(record.get("params", {}).items())
    out = []
    for path in routes:
        segs = _segments(path)
        names = [s for s in segs if s.startswith(":")]
        if not names or len(names) > len(params):
            continue
        values = {n: params[i][1] for i, n in enumerate(names)}
        static = {s.lower() for s in segs if not s.startswith(":")}
        out.append((0 if static & api_static else 1, len(segs), _fill(path, values)))
    out.sort()
    return [u for _a, _b, u in out][:3]

def _launch(project_dir: Path, end: float, nm: Path):
    from . import session_probe as sp
    from .outsider_walk import _record, _stop
    backend = project_dir / "backend"
    node = shutil.which("node")
    start = sp._start_file(backend)
    if not node or not start:
        raise RuntimeError("node or the backend start file is unavailable")
    if not (nm / "express").is_dir() and not (backend / "node_modules" / "express").is_dir():
        raise RuntimeError(f"no express in {nm}")
    port = next((p for p in range(4880, 4890) if not sp._port_taken(p)), None)
    if port is None:
        raise RuntimeError("ports 4880-4889 busy")
    scratch = os.environ.get("ARC_OUTSIDER_SCRATCH") or tempfile.gettempdir()
    work = Path(tempfile.mkdtemp(prefix="grid-probe-", dir=scratch))
    sink = server = None
    try:
        ignore = shutil.ignore_patterns("node_modules", ".git", ".config", "*key*.txt", "credentials.csv", ".env*",
                                        "*.db*", "*.sqlite*", "test", "tests", "test-e2e", "test-results",
                                        "playwright-report", ".arc-test-db", "__pycache__")
        shutil.copytree(backend, work / "backend", ignore=ignore)
        if (project_dir / "frontend" / "dist").is_dir():
            shutil.copytree(project_dir / "frontend" / "dist", work / "frontend" / "dist")
        data = work / "data"
        data.mkdir()
        env = {**os.environ, "PORT": str(port), "NODE_PATH": str(nm), "DATA_DIR": str(data),
               "ARC_DB_FILE": str(data / "database.db"), "DATABASE_FILE": str(data / "database.db"),
               "TEMP": str(work), "TMP": str(work)}
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        sink = (work / "backend.log").open("w", encoding="utf-8")
        server = subprocess.Popen([node, start], cwd=work / "backend", env=env, stdout=sink, stderr=sink, **flags)
        _record(server.pid)
        base = f"http://127.0.0.1:{port}"
        up = False
        while time.monotonic() < end - CLEANUP_RESERVE:
            if server.poll() is not None:
                raise RuntimeError("backend exited during startup")
            try:
                _http(base, "get", "/", None, end - CLEANUP_RESERVE)
                up = True
                break
            except (OSError, _OutOfTime):
                time.sleep(0.2)
        time.sleep(0.15)
        sink.flush()
        if not up or server.poll() is not None or "EADDRINUSE" in (work / "backend.log").read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError("backend did not come up (or port ownership uncertain)")
        return server, work, sink, base, node, env
    except Exception:
        try:
            _stop(server)
        except Exception:
            pass
        if sink:
            sink.close()
        shutil.rmtree(work, ignore_errors=True)
        raise

RUNNER = r'''
const {chromium}=require('@playwright/test');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>run(JSON.parse(input)));
const SNAP=([needles,control])=>{
 const textOf=root=>{const parts=[root.innerText||''];
   for(const e of root.querySelectorAll('input,textarea,select'))parts.push(e.value||'');
   for(const e of root.querySelectorAll('[title],[data-value]'))parts.push(e.getAttribute('title')||'',e.getAttribute('data-value')||'');
   return parts.join('\n')};
 const body=document.body||document.documentElement;
 const text=textOf(body);
 // the grid that holds the control value: a far value echoed in a toolbar, dialog or formula bar is not a rendered
 // cell, so "rendered" (clean) needs it inside that grid; "not rendered" (finding) needs it absent from the whole page
 // the control counts only as rendered cell text (a text node), or as an input value inside a grid -- a value seen
 // only in a formula bar (canvas grids) does not locate cells
 let grid=null,ctrl=false;
 const walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT);
 for(let n=walker.nextNode();n;n=walker.nextNode()){if(n.textContent.trim()===control&&n.parentElement){ctrl=true;grid=n.parentElement.closest('[role=grid],[role=treegrid],[role=table],table');if(grid)break}}
 if(!grid)for(const e of body.querySelectorAll('input,textarea')){if(e.value===control){grid=e.closest('[role=grid],[role=treegrid],[role=table],table');if(grid){ctrl=true;break}}}
 const gridText=grid?textOf(grid):'';
 const esc=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const has=(t,n)=>/^[\d.,-]+$/.test(n)?new RegExp('(^|[^0-9.,])'+esc(n)+'($|[^0-9.,])').test(t):t.includes(n);
 const found=needles.filter(n=>has(text,n));
 const inGrid=grid?needles.filter(n=>has(gridText,n)):[];
 const colOf=t=>{let n=0;for(const ch of t)n=n*26+ch.charCodeAt(0)-64;return n};
 let cols=0,rows=0,colName='';
 for(const e of document.querySelectorAll('[role=columnheader],th')){const t=(e.innerText||'').trim();if(/^[A-Z]{1,3}$/.test(t)&&colOf(t)>cols){cols=colOf(t);colName=t}}
 for(const e of document.querySelectorAll('[role=rowheader],tbody th,tr>td:first-child')){const t=(e.innerText||'').trim();if(/^\d{1,6}$/.test(t))rows=Math.max(rows,+t)}
 const shownText=(grid?gridText:(body.innerText||''));
 const errors=[...new Set(shownText.match(/#(?:REF!|VALUE!|NAME\?|DIV\/0!|N\/A|NUM!|NULL!|ERROR!?)/g)||[])];
 return {found,inGrid,grid:!!grid,ctrl,cols,colName,rows,errors};
};
const SCROLL=([axis,reset])=>{
 const els=[document.scrollingElement,...document.querySelectorAll('*')].filter(e=>{
   if(!e)return false;
   const over=axis==='y'?e.scrollHeight>e.clientHeight+4:e.scrollWidth>e.clientWidth+4;
   if(!over)return false;
   if(e===document.scrollingElement)return true;
   const st=getComputedStyle(e);return /(auto|scroll|overlay)/.test(axis==='y'?st.overflowY:st.overflowX);
 });
 let moved=false;
 for(const e of els){
   if(axis==='y'){const before=e.scrollTop;e.scrollTop=reset?0:e.scrollTop+Math.max(40,e.clientHeight*0.7);if(e.scrollTop!==before)moved=true}
   else{const before=e.scrollLeft;e.scrollLeft=reset?0:e.scrollLeft+Math.max(40,e.clientWidth*0.7);if(e.scrollLeft!==before)moved=true}
 }
 for(const e of els)e.dispatchEvent(new Event('scroll',{bubbles:true}));
 return moved;
};
async function run(cfg){
 const out={visits:[]};let browser;
 const left=()=>cfg.deadline-Date.now();
 const sleep=ms=>new Promise(z=>setTimeout(z,ms));
 try{
  browser=await chromium.launch({headless:true});
  const ctx=await browser.newContext({viewport:{width:1280,height:800}});
  for(const v of cfg.visits){
   const r={id:v.id,url:null,urls:[],loaded:false,control:false,grid:false,found:[],inGrid:[],how:{},cols:0,colName:'',
            rows:0,errors:[],last:null,complete:false,notes:[]};
   out.visits.push(r);
   // complete = every page that showed the control was swept to the end (scroll passes + arrow-key paths) without
   // error or deadline, and no candidate page was skipped while a far value was still missing
   let complete=true;
   const missing=()=>v.far.filter(n=>!(r.grid?r.inGrid:r.found).includes(n));
   for(const url of v.urls){
    if(left()<5000){r.notes.push('deadline');complete=false;break}
    const page=await ctx.newPage();
    try{
     await page.goto(url,{timeout:Math.max(1000,Math.min(15000,left()-3000)),waitUntil:'domcontentloaded'});
     try{await page.waitForLoadState('networkidle',{timeout:Math.max(300,Math.min(5000,left()-3000))})}catch{}
     const merge=(s,how)=>{for(const n of s.found)if(!r.found.includes(n)){r.found.push(n);r.how[n]=how}
       for(const n of s.inGrid)if(!r.inGrid.includes(n)){r.inGrid.push(n);r.how[n]=how}
       if(s.grid)r.grid=true;
       if(s.cols>r.cols){r.cols=s.cols;r.colName=s.colName}r.rows=Math.max(r.rows,s.rows);
       for(const e of s.errors)if(!r.errors.includes(e))r.errors.push(e)};
     const snap=async how=>{const s=await page.evaluate(SNAP,[v.needles,v.control]);merge(s,how);return s};
     const t0=Date.now();let s=await snap('initial');
     while(!s.ctrl&&Date.now()-t0<7000&&left()>4000){await sleep(250);s=await snap('initial')}
     if(!s.ctrl){r.notes.push('control not found as cell text at '+url);await page.close().catch(()=>{});continue}
     r.url=r.url||url;r.urls.push(url);r.loaded=true;r.control=true;
     if(v.settle){
      // two snapshots a second apart: only what both show counts (no early flash of a transient value or error)
      await sleep(1200);const a=await snap('settle');await sleep(1000);const b=await snap('settle');
      r.last={found:b.found.filter(n=>a.found.includes(n)),errors:b.errors.filter(e=>a.errors.includes(e))};
     }
     let done=true;
     if(v.far.length&&missing().length){
      for(const [axis,resetFirst] of [['y',['x']],['x',['y']],['y',[]]]){
        if(!missing().length)break;
        if(left()<4000){done=false;break}
        for(const other of resetFirst)await page.evaluate(SCROLL,[other,true]);
        await page.evaluate(SCROLL,[axis,true]);await sleep(150);await snap('scroll');
        for(let i=0;missing().length;i++){
          if(left()<4000||i>=600){done=false;break}
          const moved=await page.evaluate(SCROLL,[axis,false]);await sleep(120);await snap('scroll');
          if(!moved)break;
        }
      }
     }
     if(v.far.length&&missing().length&&v.keys){
      if(left()<6000)done=false;
      else{
       const reset=()=>page.evaluate(()=>{for(const e of [document.scrollingElement,...document.querySelectorAll('*')]){if(e){e.scrollLeft=0;e.scrollTop=0}}});
       for(const order of [['ArrowRight','ArrowDown'],['ArrowDown','ArrowRight']]){
        if(!missing().length)break;
        try{
         await reset();await sleep(150);
         await page.getByText(v.control,{exact:true}).first().click({timeout:2000});await sleep(200);
         for(const key of order){
          const n=key==='ArrowRight'?v.keys.right:v.keys.down;
          for(let i=1;i<n&&missing().length;i++){
           if(left()<4000){done=false;break}
           await page.keyboard.press(key);if(i%3===0)await snap('keys');
          }
          await sleep(150);await snap('keys');
         }
        }catch(e){done=false;r.notes.push('keys: '+String(e).slice(0,120))}
       }
      }
     }
     if(!done)complete=false;
     await page.close().catch(()=>{});
     if(!missing().length)break;
    }catch(e){complete=false;r.notes.push(String(e).slice(0,200));await page.close().catch(()=>{})}
   }
   r.complete=r.control&&complete;
  }
 }catch(e){out.error=String(e).slice(0,300)}
 finally{if(browser)await browser.close().catch(()=>{});process.stdout.write(JSON.stringify(out))}
}
'''

def _browse(node, env, work, nm, visits, end) -> dict[str, Any]:
    from .outsider_walk import _record, _stop
    runner = None
    try:
        if not (Path(nm) / "@playwright" / "test").is_dir():
            return {"error": "no @playwright/test in node_modules"}
        left = end - time.monotonic() - CLEANUP_RESERVE
        if left < 12:
            return {"error": "out of time before the browser check"}
        script = work / "grid-probe.cjs"
        script.write_text(RUNNER, encoding="utf-8")
        cfg = {"visits": visits, "deadline": int((time.time() + left - 3) * 1000)}
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        runner = subprocess.Popen([node, str(script)], cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8", **flags)
        _record(runner.pid)
        raw, err = runner.communicate(json.dumps(cfg), timeout=max(5.0, left))
        result = json.loads(raw or "{}")
        if not result:
            return {"error": "runner failed: " + (err or "")[-200:]}
        return result
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        try:
            _stop(runner)
        except Exception:
            pass

def _row(item, status, result, findings=None, evidence=None):
    return {"item": item, "status": status, "result": str(result)[:300],
            "findings": [str(f)[:500] for f in (findings or [])][:5], "evidence": evidence or {}}

def grid_probes(requirement: str, project_dir: Path, *, time_limit: float = 300.0, log=print,
                node_modules: str | None = None, items: tuple[str, ...] = ITEMS) -> list[dict]:
    def note(message):
        try:
            log("[grid-probes] " + str(message))
        except Exception:
            pass
    t0 = time.monotonic()
    try:
        wanted = [i for i in (items or ()) if i in ITEMS] or []
    except Exception:
        wanted = []
    rows = {i: _row(i, "not run", "not run: probe did not reach this item") for i in wanted}
    server = sink = work = None
    try:
        end = t0 + max(0.0, float(time_limit))
        project_dir = Path(project_dir)
        requirement = str(requirement or "")
        if not wanted:
            return []
        if not (project_dir / "backend").is_dir():
            for i in wanted:
                rows[i] = _row(i, "not run", "not run: no backend/ directory in the project")
            return [rows[i] for i in wanted]
        api = discover_api(project_dir)
        if not api["cell"]:
            reason = "not run: no cell-write endpoint (PUT/POST/PATCH on a path with a cells segment) in the backend routes"
            for i in wanted:
                rows[i] = _row(i, "not run", reason, evidence={"routes_scanned": len(api["routes"])})
            return [rows[i] for i in wanted]
        extent = requirement_extent(requirement)
        rule = empty_cell_rule(requirement)
        far_needed = extent["grid"] and (extent["col"] > SCREEN_COLS or extent["row"] > SCREEN_ROWS)
        if "extent" in wanted and not far_needed:
            rows["extent"] = _row("extent", "not run",
                                  "not run: the requirement addresses no cell beyond the first screenful "
                                  f"(A-{col_letters(SCREEN_COLS)} / 1-{SCREEN_ROWS})"
                                  + (f"; largest named {col_letters(extent['col'])}{extent['row']}" if extent["grid"] else ""),
                                  evidence={"requirement_extent": extent})
        nm = Path(node_modules or project_dir / "backend" / "node_modules").resolve()
        server, work, sink, base, node, env = _launch(project_dir, end, nm)
        note(f"app started at {base} ({round(time.monotonic() - t0, 1)}s)")
        deadline = end - CLEANUP_RESERVE
        notes: list[str] = []
        chosen = rec1 = None
        for route in api["cell"]:
            probe = _Api(base, api["routes"], route, deadline, notes)
            rec = probe.create_record("Grid probe")
            if rec and probe.calibrate(rec, "A1", str(A_VALUE)):
                chosen, rec1 = probe, rec
                break
        if chosen is None:
            reason = ("not run: no cell write could be read back through the app's API (" + "; ".join(notes[-3:]) + ")")[:300]
            for i in wanted:
                if rows[i]["result"].startswith("not run: probe did not"):
                    rows[i] = _row(i, "not run", reason, evidence={"cell_routes": [r["path"] for r in api["cell"]]})
            return [rows[i] for i in wanted]
        ev_api = {"cell_route": f"{chosen.route['method'].upper()} {chosen.route['path']}", "shape": chosen.shape[0],
                  "read_back": rec1.get("read_path"), "record": rec1["id"]}
        note(f"cell API: {ev_api}")
        routes_front = page_routes(project_dir)
        has_dist = (project_dir / "frontend" / "dist" / "index.html").is_file()

        recalc = None
        if "recalculation" in wanted:
            recalc = {"chains": [], "expects_error": cleared_error_expected(requirement)}
            writes = [w for chain in CHAINS for w in chain[1]] + [("D1", MARK["rcontrol"])]
            if not chosen.write(rec1, writes):
                rows["recalculation"] = _row("recalculation", "not run", "not run: the formula cells were refused by "
                                             + ev_api["cell_route"], evidence=ev_api)
                recalc = None
            else:
                got = chosen.read(rec1, [c for c, _ in writes])
                for name, cells, dep, before, cleared, after_zero in CHAINS:
                    cell = got.get(dep)
                    value = cell.get("value") if cell else None
                    formula = dict(cells)[dep]
                    server_has = bool(cell and cell.get("has_value") and value not in (None, "") and str(value) != formula)
                    recalc["chains"].append({"name": name, "dep": dep, "formula": formula, "cleared": cleared,
                                             "before_expected": before, "after_zero": after_zero, "server_before": value,
                                             "server_has": server_has,
                                             "server_ok": server_has and _num(value) is not None and abs(_num(value) - before) < 1e-6})

        ext = None
        if "extent" in wanted and far_needed:
            rec2 = chosen.create_record("Grid probe extent")
            if not rec2:
                rows["extent"] = _row("extent", "not run", "not run: could not create a second record for the extent markers",
                                      evidence=ev_api)
            else:
                C, R = max(extent["col"], 1), max(extent["row"], 1)
                markers = [("A1", MARK["control"])]
                if C > SCREEN_COLS:
                    markers.append((f"{col_letters(C)}1", MARK["col"]))
                if R > SCREEN_ROWS:
                    markers.append((f"A{R}", MARK["row"]))
                if C > SCREEN_COLS and R > SCREEN_ROWS:
                    markers.append((f"{col_letters(C)}{R}", MARK["corner"]))
                chosen.write(rec2, markers)
                back = chosen.read(rec2, [c for c, _ in markers])
                accepted = [(c, m) for c, m in markers if back.get(c) and m in (str(back[c].get("raw")), str(back[c].get("value")))]
                ext = {"record": rec2, "markers": markers, "accepted": accepted, "target": f"{col_letters(C)}{R}"}
                if not any(c == "A1" for c, _m in accepted) or len(accepted) < 2:
                    rows["extent"] = _row("extent", "not run",
                                          f"not run: the server did not store/read back the far markers "
                                          f"({', '.join(c for c, _ in markers)}; read back {', '.join(c for c, _ in accepted) or 'none'})",
                                          evidence={**ev_api, "markers": markers})
                    ext = None

        visits = []
        if has_dist and routes_front:
            if recalc:
                urls = record_urls(routes_front, rec1, chosen.route["path"])
                if urls:
                    needles = [MARK["rcontrol"]] + [str(c["before_expected"]) for c in recalc["chains"]] \
                        + [f"{c['before_expected']:,}" for c in recalc["chains"]]
                    visits.append({"id": "recalc-before", "urls": [base + u for u in urls], "control": MARK["rcontrol"],
                                   "needles": needles, "far": [], "keys": None, "settle": True})
            if ext:
                urls = record_urls(routes_front, ext["record"], chosen.route["path"])
                if urls:
                    C, R = max(extent["col"], 1), max(extent["row"], 1)
                    visits.append({"id": "extent", "urls": [base + u for u in urls], "control": MARK["control"],
                                   "needles": [m for _c, m in ext["accepted"]],
                                   "far": [m for c, m in ext["accepted"] if c != "A1"], "keys": {"right": C, "down": R}})
        browse1 = _browse(node, env, work, nm, visits, end) if visits else {"error": "no frontend/dist or no record page route"}
        by_id = {v.get("id"): v for v in browse1.get("visits", [])}

        if ext:
            rows["extent"] = _extent_verdict(ext, extent, by_id.get("extent"), browse1, ev_api)

        if recalc:
            clear_cells = [c["cleared"] for c in recalc["chains"]]
            deps = [c["dep"] for c in recalc["chains"]]
            cleared_ok, got = False, {}

            for empty in ("", None):
                chosen.write(rec1, [(c, empty) for c in clear_cells])
                now = chosen.read(rec1, clear_cells + deps)
                if all(now.get(c) is None for c in clear_cells):
                    cleared_ok, got = True, now
                    recalc["clear_sent"] = ("empty string" if empty == "" else "null") + " (cell absent after)"
                    break
                if not cleared_ok and all(_empty(now.get(c)) for c in clear_cells):
                    cleared_ok, got = True, now
                    recalc["clear_sent"] = ("empty string" if empty == "" else "null") + " (empty cell kept)"
            recalc["cleared"] = cleared_ok
            if cleared_ok:
                def suspicious(cells):
                    return any(_is_error((cells.get(c["dep"]) or {}).get("value"))
                               or _num((cells.get(c["dep"]) or {}).get("value")) == c["before_expected"]
                               for c in recalc["chains"])
                if suspicious(got) and deadline - time.monotonic() > 3:
                    time.sleep(1.0)
                    again = chosen.read(rec1, deps)
                    got.update({k: v for k, v in again.items() if v is not None})
                for c in recalc["chains"]:
                    cell = got.get(c["dep"])
                    c["server_after"] = cell.get("value") if cell else None
            before_visit = by_id.get("recalc-before")
            browse2 = {}
            if cleared_ok and before_visit and before_visit.get("control"):
                needles = [MARK["rcontrol"]]
                for c in recalc["chains"]:
                    needles += [str(c["before_expected"]), f"{c['before_expected']:,}", str(c["after_zero"]), f"{c['after_zero']:,}"]
                browse2 = _browse(node, env, work, nm, [{"id": "recalc-after", "urls": [before_visit["url"]],
                                                         "control": MARK["rcontrol"], "needles": needles, "far": [],
                                                         "keys": None, "settle": True}], end)
            after_visit = next((v for v in browse2.get("visits", []) if v.get("id") == "recalc-after"), None)
            rows["recalculation"] = _recalc_verdict(recalc, rule, before_visit, after_visit, browse1, browse2, ev_api)
        return [rows[i] for i in wanted]
    except _OutOfTime:
        for i in wanted:
            if rows[i]["result"].startswith("not run: probe did not"):
                rows[i] = _row(i, "not run", "not run: out of time")
        return [rows[i] for i in wanted]
    except Exception as exc:
        reason = f"not run: {type(exc).__name__}: {str(exc)[:200]}"
        for i in wanted:
            if rows[i]["result"].startswith("not run: probe did not"):
                rows[i] = _row(i, "not run", reason)
        return [rows[i] for i in wanted]
    finally:
        try:
            from .outsider_walk import _stop
            _stop(server)
        except Exception:
            note("cleanup could not confirm the server exit")
        if sink:
            try:
                sink.close()
            except Exception:
                pass
        if work:
            shutil.rmtree(work, ignore_errors=True)
        note(f"done in {round(time.monotonic() - t0, 1)}s: "
             + "; ".join(f"{i}={rows[i]['status']}" for i in wanted))

def _stable(visit, number) -> bool:
    if not visit:
        return False
    pool = (visit.get("last") or {}).get("found") if visit.get("last") is not None else visit.get("found", [])
    return str(number) in (pool or []) or f"{number:,}" in (pool or [])

def _recalc_verdict(recalc, rule, before_visit, after_visit, browse1, browse2, ev_api):
    item = "recalculation"
    evidence = {**ev_api, "rule": rule, "clear_sent": recalc.get("clear_sent"), "expects_error": recalc.get("expects_error"),
                "chains": [{k: c.get(k) for k in ("name", "dep", "formula", "cleared", "before_expected", "server_before",
                                                  "server_after", "after_zero")} for c in recalc["chains"]]}
    if before_visit:
        evidence["page_before"] = {"last": before_visit.get("last"), "url": before_visit.get("url")}
    if after_visit:
        evidence["page_after"] = {"last": after_visit.get("last"), "control": after_visit.get("control")}
    if browse2.get("error") or browse1.get("error"):
        evidence["browser"] = browse2.get("error") or browse1.get("error")
    if not recalc.get("cleared"):
        return _row(item, "not run", "not run: the referenced cells could not be cleared through the cell API "
                    "(empty raw and null both left a value)", evidence=evidence)
    rule_text = (f"the requirement's empty-cell rule ({rule['source']}: {rule['quote']})" if rule else None)
    page_ran = bool(after_visit and after_visit.get("control") and after_visit.get("last") is not None)
    before_errors = set(((before_visit or {}).get("last") or {}).get("errors") or [])
    new_errors = [e for e in ((after_visit or {}).get("last") or {}).get("errors") or [] if e not in before_errors]
    findings, clean, unrun = [], [], []
    for c in recalc["chains"]:
        label = f"{c['dep']} {c['formula']} after clearing {c['cleared']} ({c['name']})"
        page_ok = page_ran and _stable(after_visit, c["after_zero"])
        page_stale = page_ran and _stable(after_visit, c["before_expected"]) and not page_ok
        page_error = page_ran and bool(new_errors) and not page_ok
        page_bad = page_stale or page_error
        page_says = (f"the page shows {', '.join(new_errors)}" if page_error
                     else f"the page still shows {c['before_expected']}" if page_stale else "")
        if recalc.get("expects_error") and page_error and not page_stale:
            unrun.append(f"{label}: an error value is shown, and the requirement itself shows an error value for a "
                         "cleared/empty reference (not reported)")
            continue
        server_after = c.get("server_after")
        if c["server_ok"]:
            problem = None
            if _is_error(server_after):
                problem = f"the server returns {server_after}"
            elif _num(server_after) is not None and abs(_num(server_after) - c["before_expected"]) < 1e-6:
                problem = f"the server still returns the stale {server_after}"
            elif rule and (_num(server_after) is None or abs(_num(server_after) - c["after_zero"]) > 1e-6):
                problem = f"the server returns {server_after!r} where the empty-cell rule gives {c['after_zero']}"
            if problem and recalc.get("expects_error") and _is_error(server_after):
                unrun.append(f"{label}: {problem}, and the requirement itself shows an error value for a cleared/empty "
                             "reference (not reported)")
            elif problem and page_bad:
                findings.append((label, f"{problem}; {page_says}"))
            elif problem and page_ok:
                unrun.append(f"{label}: {problem} but the page displays {c['after_zero']} (not reported)")
            elif problem:
                unrun.append(f"{label}: {problem}; not confirmed on the page ("
                             + ("page check not run" if not page_ran else "page value not located") + ")")
            elif page_bad:
                findings.append((label, f"the server returns {server_after} but {page_says}"))
            elif _num(server_after) is not None:
                clean.append(f"{label}: server {server_after}" + (f", page {c['after_zero']}" if page_ok else ""))
            else:
                unrun.append(f"{label}: server value {server_after!r} not judged without a stated empty-cell rule")
            continue

        if c.get("server_has"):
            unrun.append(f"{c['dep']} {c['formula']}: formula not evaluated as expected (read back {c['server_before']!r}, "
                         f"expected {c['before_expected']})")
            continue
        if not _stable(before_visit, c["before_expected"]):
            unrun.append(f"{c['dep']} {c['formula']}: the server returned no computed value and the page did not show "
                         f"{c['before_expected']}")
            continue
        if not page_ran:
            unrun.append(f"{label}: page not re-checked after the clear")
        elif page_ok and not new_errors:
            clean.append(f"{label}: page {c['after_zero']}")
        elif page_bad:
            findings.append((label, page_says))
        else:
            unrun.append(f"{label}: page value after the clear not located")
    expected = "with empty = 0" if rule else "a number, not an error"
    if findings:
        shown = "; ".join(f"{lab}: {prob}" for lab, prob in findings)
        result = f"finding: cleared a referenced cell via {ev_api['cell_route']}; {shown}"
        texts = []
        for lab, prob in findings:
            texts.append(
                f"Recalculation: {lab}, {prob}. Fix: in the module that owns formula evaluation (server and page), a "
                f"cleared cell is an empty cell, not an invalid reference; every dependent recalculates at once"
                + (f" with {rule_text}" if rule_text else " to a number")
                + "; the reference error is only for a cell removed by a structural edit.")
        return _row(item, "finding", result, texts, evidence)
    if clean and not unrun:
        return _row(item, "clean", f"clean: cleared referenced cells via {ev_api['cell_route']}; dependents recalculated "
                    f"({expected}): " + "; ".join(clean), evidence=evidence)
    detail = "; ".join(unrun + [f"clean part: {x}" for x in clean])
    return _row(item, "not run", "not run: " + detail, evidence=evidence)

def _extent_verdict(ext, extent, visit, browse, ev_api):
    item = "extent"
    evidence = {**ev_api, "requirement_extent": {k: extent.get(k) for k in ("col", "row", "phrases")},
                "markers": ext["accepted"]}
    if not visit:
        why = browse.get("error") or "no record page route in the frontend sources"
        return _row(item, "not run", f"not run: browser check not executed ({why}); server stored "
                    + ", ".join(c for c, _ in ext["accepted"]), evidence=evidence)
    evidence.update({"page": visit.get("url"), "pages": visit.get("urls"), "complete": visit.get("complete"),
                     "found": visit.get("found"), "how": visit.get("how"),
                     "last_column_header": visit.get("colName"), "last_row_header": visit.get("rows"),
                     "notes": (visit.get("notes") or [])[:3]})
    if not visit.get("control"):
        return _row(item, "not run", "not run: could not locate cells in the UI (the A1 control value was not rendered "
                    "as cell text on any record page tried: " + "; ".join((visit.get("notes") or [])[:2]) + ")",
                    evidence=evidence)
    page_found = visit.get("found", [])
    rendered = visit.get("inGrid", []) if visit.get("grid") else page_found
    far = [(c, m) for c, m in ext["accepted"] if c != "A1"]
    missing = [(c, m) for c, m in far if m not in page_found]
    present = [c for c, m in far if m in rendered]
    elsewhere = [c for c, m in far if m in page_found and m not in rendered]
    evidence["in_grid"] = visit.get("inGrid")
    heads = []
    if visit.get("colName"):
        heads.append(f"last column header {visit['colName']}")
    if visit.get("rows"):
        heads.append(f"last row header {visit['rows']}")
    seen_extent = ", ".join(heads) or "no column/row headers recognised"
    if not missing and not elsewhere:
        return _row(item, "clean", f"clean: A1 control and far cells {', '.join(present)} rendered in the grid on "
                    f"{visit.get('url')} ({seen_extent})", evidence=evidence)
    if not missing:
        return _row(item, "not run", f"not run: far values {', '.join(elsewhere)} appear on the page but not inside the "
                    f"grid holding A1 ({seen_extent}); rendering not established", evidence=evidence)
    if not visit.get("complete") or browse.get("error"):
        why = browse.get("error") or "; ".join((visit.get("notes") or [])[:2]) or "deadline or step limit"
        return _row(item, "not run", f"not run: far values {', '.join(c for c, _ in missing)} not seen yet, but the "
                    f"scroll / arrow-key sweep did not complete ({why[:120]}); {seen_extent}", evidence=evidence)
    cells = ", ".join(c for c, _ in missing)
    req = f"{col_letters(extent['col'])}{extent['row']}"
    result = (f"finding: values stored at {cells} (accepted and read back by the server) never rendered on the record page "
              f"after scrolling every container and arrow-key moves, while A1 did ({seen_extent})"
              + (f"; {', '.join(elsewhere)} shown only outside the grid" if elsewhere else "")
              + (f"; {', '.join(present)} rendered" if present else ""))
    shown = f"the grid renders only up to {seen_extent}" if heads else "the grid does not render the far cells"
    fix = (f"Extent: {shown} -- a value the server stores at {cells} cannot be seen, "
           f"selected or edited, while the requirement addresses cells up to column {col_letters(extent['col'])} and row "
           f"{extent['row']} ({req}). Fix: render (or virtualise with scrolling) every column and row the requirement or "
           f"the stored data uses, and make coordinates, selection, keyboard moves, paste and Delete work there as in the "
           f"first screenful.")
    return _row(item, "finding", result, [fix], evidence)
