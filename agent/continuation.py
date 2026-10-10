from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

CONTRACT_HEAD = "EXISTING APPLICATION"
OUTSIDER_HEAD = "EXISTING APPLICATION (earlier stage)"

SPEC_ID = re.compile(r"^(?:DESC-)?(REQ(?:-\d+)+)")
SOURCE_SUFFIXES = (".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".css", ".html", ".json")
SKIP_PARTS = {"node_modules", "dist", "test-results", ".arc-test-db", "playwright-report", ".vite"}
SCAN_FILES_MAX = 4000

def requirement_sha(requirements_dir: Path) -> str:
    try:
        return hashlib.sha256((Path(requirements_dir) / "requirements.yaml").read_bytes()).hexdigest()
    except OSError:
        return ""

def tree_ids(tree: dict[str, Any]) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    leaves: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node_id = str(node.get("id") or "").strip()
        children = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        if node_id and node_id != "ROOT":
            ids.add(node_id)
            if not children:
                leaves.add(node_id)
        for child in children:
            walk(child)

    walk(tree)
    return ids, leaves

def spec_req_id(name: str) -> str | None:
    match = SPEC_ID.match(Path(name).name)
    return match.group(1) if match else None

def belongs_here(spec_id: str | None, ids: set[str], leaves: set[str]) -> bool:
    if not spec_id:
        return False
    if spec_id in ids or any(i.startswith(spec_id + "-") or i.startswith(spec_id + ".") for i in ids):
        return True
    parts = spec_id.split("-")
    return any("-".join(parts[:k]) in leaves for k in range(2, len(parts)))

def _natural(rel: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", Path(rel).name)]

def earlier_specs(project_dir: Path, tree: dict[str, Any]) -> list[str]:
    folder = Path(project_dir) / "backend" / "test-e2e"
    if not folder.is_dir():
        return []
    ids, leaves = tree_ids(tree)
    found = []
    for path in folder.glob("*.spec.js"):
        if path.is_file() and not belongs_here(spec_req_id(path.name), ids, leaves):
            found.append(path.relative_to(project_dir).as_posix())
    return sorted(found, key=_natural)

def sources_beyond_template(project_dir: Path, template_dir: Path) -> list[str]:
    project_dir, template_dir = Path(project_dir), Path(template_dir)
    beyond: list[str] = []
    seen = 0
    for top in ("frontend/src", "backend/src"):
        base = project_dir / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if seen >= SCAN_FILES_MAX:
                break
            if not path.is_file() or SKIP_PARTS & set(path.relative_to(project_dir).parts):
                continue
            seen += 1
            rel = path.relative_to(project_dir).as_posix()
            original = template_dir / rel
            try:
                if not original.is_file() or original.read_bytes() != path.read_bytes():
                    beyond.append(rel)
            except OSError:
                beyond.append(rel)
    return beyond

def detect(project_dir: Path, tree: dict[str, Any], template_dir: Path) -> dict[str, Any] | None:
    try:
        earlier = earlier_specs(project_dir, tree)
        if not earlier:
            return None
        beyond = sources_beyond_template(project_dir, template_dir)
        if not beyond:
            return None
        return {"earlier": earlier, "beyond": len(beyond), "beyond_sample": beyond[:12]}
    except Exception:
        return None

ROUTE_ELEMENT = re.compile(r"<Route\b[^>]*?\bpath=\{?\s*[\"'`]([^\"'`]+)[\"'`]\s*\}?[^>]*?(?:element=\{\s*<\s*([A-Za-z0-9_]+))?")
ROUTE_OBJECT = re.compile(r"\bpath\s*:\s*[\"'`](/[^\"'`]*)[\"'`]")
USE_REQUIRE = re.compile(r"\bapp\.use\(\s*[\"'`]([^\"'`]+)[\"'`]\s*,\s*require\(\s*[\"'`]\.\/([^\"'`]+)[\"'`]\s*\)")
USE_NAME = re.compile(r"\bapp\.use\(\s*[\"'`]([^\"'`]+)[\"'`]\s*,\s*([A-Za-z_$][\w$]*)\s*[,)]")
REQUIRE_NAME = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*[\"'`]\.\/([^\"'`]+)[\"'`]\s*\)")
ENDPOINT = re.compile(r"\b(?:router|app|api|r)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]*)[\"'`]")
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w+)[\"`\]]?\s*\(", re.IGNORECASE)
NOT_A_COLUMN = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}
SEED_FILE = re.compile(r"(?:^|/)(?:init_db|seed[^/]*)\.[cm]?[jt]s$", re.IGNORECASE)
STRING = re.compile(r"'([^'\n\\]{2,60})'|\"([^\"\n\\]{2,60})\"")
SQLISH = re.compile(r"\b(?:SELECT|INSERT|CREATE|UPDATE|DELETE|PRAGMA|FROM|WHERE|VALUES|INTEGER|TEXT|NULL|JOIN|INTO)\b")
ROLE_NAME = re.compile(r"\{\s*role\s*:\s*[\"']([a-z]+)[\"']\s*,\s*name\s*:\s*[\"']([^\"'\n]{1,80})[\"']")
GET_BY_ROLE = re.compile(r"getByRole\(\s*[\"']([a-z]+)[\"']\s*,\s*\{\s*name\s*:\s*[\"']([^\"'\n]{1,80})[\"']")
GET_BY_LABEL = re.compile(r"getByLabel\(\s*[\"']([^\"'\n]{1,80})[\"']")
CLICK_TEXT = re.compile(r"\bclick\(\s*page\s*,\s*[\"']([^\"'\n]{1,60})[\"']\s*\)")
CODE_CONTEXT = re.compile(r"(?:require|datetime|import|from|PRAGMA|process\.env\.?)\s*\(?\s*$|\b(?:require|datetime)\(\s*$")
FILE_NAME = re.compile(r"\.(?:js|cjs|mjs|ts|db|json|sqlite3?|sql)$", re.IGNORECASE)
SIGN_IN_BUTTON = re.compile(r"(?i)click\(.*role\s*:\s*[\"']button[\"']\s*,\s*name\s*:\s*[\"'](?:sign\s*in|log\s*in|login)[\"']")
GOTO = re.compile(r"\bpage\.goto\(\s*[\"'`]([^\"'`$\n]{1,80})[\"'`]")

def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""

def _files(base: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.rglob("*")):
        if len(out) >= SCAN_FILES_MAX:
            break
        if path.is_file() and path.suffix in suffixes and not (SKIP_PARTS & set(path.parts)) \
                and not any(p in ("tests", "test", "__tests__") for p in path.relative_to(base).parts[:-1]):
            out.append(path)
    return out

def _table_columns(text: str, start: int) -> list[str]:
    depth, i, body = 1, start, []
    while i < len(text) and depth:
        ch = text[i]
        depth += ch == "("
        depth -= ch == ")"
        if depth:
            body.append(ch)
        i += 1
    parts, level, current = [], 0, []
    for ch in "".join(body):
        if ch == "(":
            level += 1
        elif ch == ")":
            level -= 1
        if ch == "," and level == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    columns = []
    for part in parts:
        words = part.strip().strip("\"`[]").split()
        if words and words[0].upper() not in NOT_A_COLUMN and re.fullmatch(r"[\"`\[]?\w+[\"`\]]?", words[0]):
            columns.append(words[0].strip("\"`[]"))
    return columns

def existing_app(project_dir: Path, specs: list[str] | None = None) -> dict[str, Any]:
    project_dir = Path(project_dir)
    app: dict[str, Any] = {"routes": [], "pages": [], "api": [], "tables": [], "seeds": [], "names": [], "visits": []}
    try:
        frontend = project_dir / "frontend" / "src"
        routes: list[str] = []
        for path in _files(frontend, (".tsx", ".jsx", ".ts", ".js")):
            text = _read(path)
            for match in ROUTE_ELEMENT.finditer(text):
                entry = match.group(1) + (f" -> {match.group(2)}" if match.group(2) else "")
                if entry not in routes:
                    routes.append(entry)
            if "createBrowserRouter" in text or "RouteObject" in text:
                for match in ROUTE_OBJECT.finditer(text):
                    if match.group(1) not in routes:
                        routes.append(match.group(1))
        app["routes"] = routes
        pages = frontend / "pages"
        app["pages"] = [p.relative_to(project_dir).as_posix() for p in _files(pages, (".tsx", ".jsx"))]

        backend = project_dir / "backend" / "src"
        mounts: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        for path in _files(backend, (".js", ".cjs", ".mjs", ".ts")):
            text = _read(path)
            here = path.parent
            for match in REQUIRE_NAME.finditer(text):
                names[match.group(1)] = (here / match.group(2)).resolve().as_posix()
            for match in USE_REQUIRE.finditer(text):
                mounts.setdefault(_module_path(here / match.group(2)), []).append(match.group(1))
            for match in USE_NAME.finditer(text):
                target = names.get(match.group(2))
                if target:
                    mounts.setdefault(_module_path(Path(target)), []).append(match.group(1))
        api: list[str] = []
        for path in _files(backend, (".js", ".cjs", ".mjs", ".ts")):
            rel = path.relative_to(project_dir).as_posix()
            prefixes = mounts.get(_module_path(path), [""])
            for match in ENDPOINT.finditer(_read(path)):
                for prefix in dict.fromkeys(prefixes):
                    route = (prefix.rstrip("/") + "/" + match.group(2).lstrip("/")).rstrip("/") or "/"
                    line = f"{match.group(1).upper()} {route} ({rel})"
                    if line not in api:
                        api.append(line)
        app["api"] = api
        tables: list[str] = []
        seeds: list[str] = []
        for path in _files(backend, (".js", ".cjs", ".mjs", ".ts", ".sql")):
            rel = path.relative_to(project_dir).as_posix()
            text = _read(path)
            for match in CREATE_TABLE.finditer(text):
                columns = _table_columns(text, match.end())
                entry = f"{match.group(1)}({', '.join(columns)})"
                if not any(t.startswith(match.group(1) + "(") for t in tables):
                    tables.append(entry)
            if SEED_FILE.search(rel):
                for match in STRING.finditer(text):
                    value = (match.group(1) or match.group(2) or "").strip()
                    before = text[max(0, match.start() - 12):match.start()]
                    if (value and not SQLISH.search(value) and not value.startswith((".", "/")) and "/" not in value
                            and "${" not in value and not re.search(r"[;{}()=<>]", value) and value not in seeds
                            and not CODE_CONTEXT.search(before) and not FILE_NAME.search(value)
                            and not value.endswith(":") and not re.fullmatch(r"[A-Z]+(?: [A-Z]+)+", value)):
                        seeds.append(value)
        app["tables"] = tables
        app["seeds"] = seeds

        counter: Counter = Counter()
        visits: list[str] = []
        for rel in specs or []:
            text = _read(project_dir / rel)
            for regex, kind in ((ROLE_NAME, None), (GET_BY_ROLE, None)):
                for match in regex.finditer(text):
                    counter[(match.group(1), match.group(2))] += 1
            for match in GET_BY_LABEL.finditer(text):
                counter[("label", match.group(1))] += 1
            for match in CLICK_TEXT.finditer(text):
                counter[("text", match.group(1))] += 1
            for match in GOTO.finditer(text):
                if match.group(1) not in visits:
                    visits.append(match.group(1))
        app["names"] = [f'{role} "{name}"' for (role, name), _ in counter.most_common()]
        app["visits"] = visits
        from .stage_controls import controls
        app["links"] = sorted(controls(project_dir)["link"])
    except Exception:
        pass
    return app

def _module_path(path: Path) -> str:
    text = Path(path).resolve().as_posix()
    for suffix in (".js", ".cjs", ".mjs", ".ts"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text

def _capped(items: list[str], limit: int, sep: str = "; ") -> str:
    shown, used = [], 0
    for item in items:
        if used + len(item) + len(sep) > limit:
            break
        shown.append(item)
        used += len(item) + len(sep)
    rest = len(items) - len(shown)
    return sep.join(shown) + (f"{sep}... ({rest} more)" if rest > 0 else "")

def contract_note(app: dict[str, Any], specs: list[str], limit: int = 7000, lead: str | None = None) -> str:
    parts = [lead.strip() if lead else (
        f"{CONTRACT_HEAD} (delivered by an earlier stage of this same task; its {len(specs)} acceptance spec file(s) "
        "pass on it and keep guarding every batch): this requirement EXTENDS that application. Keep every route, page, "
        "API endpoint, table, column, seed record and accessible name listed here working exactly as it does -- do not "
        "re-create, rename, move or drop any of them, and do not replace the existing sign-up / sign-in / navigation. New "
        "features get new routes, new pages, new endpoints, new tables or new columns (with defaults, so the existing "
        "inserts keep working) and new seed rows. Change existing code where a stated change of this requirement needs "
        "it (a new check in a shared middleware, a new column a shared query must read) -- and protect everything else "
        "that code does.")]
    parts.append("Every existing control keeps its KIND as well as its name (unless this requirement names another kind "
                 "for it): a link (<a href> / <Link>) stays a link and a button stays a button, also when it moves into a "
                 "shared header or component -- the earlier tests, and this round's, look for it by that role (a sign-in "
                 "link re-made as a button styled like it is a different control to them).")
    if app.get("routes"):
        parts.append("Frontend routes: " + _capped(list(app["routes"]), 1600))
    if app.get("api"):
        parts.append("API endpoints: " + _capped(_api_by_file(list(app["api"])), 2200))
    if app.get("tables"):
        parts.append("Tables: " + _capped(list(app["tables"]), 1400))
    if app.get("seeds"):
        parts.append("Literals of the existing seed code (records and values the earlier specs rely on): "
                     + _capped([f'"{s}"' for s in app["seeds"]], 700, ", "))
    if app.get("names"):
        parts.append("Accessible names the earlier specs use: " + _capped(list(app["names"]), 800, ", "))
    if app.get("links"):
        parts.append("Links of the existing pages (they stay links): " + _capped([f'"{s}"' for s in app["links"]], 600, ", "))
    text = "\n".join(parts)
    return text[:limit]

def _api_by_file(api: list[str]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for line in api:
        match = re.match(r"^(\S+ \S+) \((.+)\)$", line)
        endpoint, where = (match.group(1), match.group(2)) if match else (line, "")
        grouped.setdefault(where.replace("backend/src/", ""), []).append(endpoint)
    return [f"{where}: {', '.join(endpoints)}" if where else ", ".join(endpoints) for where, endpoints in grouped.items()]

def architect_block(app: dict[str, Any], specs: list[str], lead: str | None = None) -> str:
    if not any(app.get(k) for k in ("routes", "api", "tables", "pages")):
        return ""
    lines = [lead.strip() if lead else (
        "EXISTING APPLICATION. The project folder already holds a working application that an earlier stage of this "
        f"task delivered; {len(specs)} acceptance spec file(s) of the earlier requirements pass on it. Write the contract "
        "for THIS requirement as an extension of it: reuse its routes, pages, endpoints, tables and shared files; list "
        "only what this requirement adds or changes, and never rename, re-create or drop what exists.")]
    if app.get("pages"):
        lines.append("Existing pages: " + _capped(list(app["pages"]), 1500, ", "))
    if app.get("routes"):
        lines.append("Existing frontend routes: " + _capped(list(app["routes"]), 1800))
    if app.get("api"):
        lines.append("Existing API endpoints: " + _capped(_api_by_file(list(app["api"])), 3000))
    if app.get("tables"):
        lines.append("Existing tables: " + _capped(list(app["tables"]), 2000))
    return "\n".join(lines)

def _sign_in_excerpt(project_dir: Path, specs: list[str], limit: int = 14) -> list[str]:
    for rel in specs:
        lines = _read(Path(project_dir) / rel).splitlines()
        for index, line in enumerate(lines):
            if SIGN_IN_BUTTON.search(line):
                start = index
                while start > 0 and index - start < limit and "page.goto(" not in lines[start]:
                    start -= 1
                block = [ln.strip().replace("`", "'") for ln in lines[start:index + 1] if ln.strip()]
                if block and "page.goto(" in block[0]:
                    return block[:limit]
    for rel in specs:
        lines = _read(Path(project_dir) / rel).splitlines()
        for index, line in enumerate(lines):
            if re.search(r"(?i)name\s*:\s*[\"'][^\"']*password", line) and "fill(" in line:
                start = index
                while start > 0 and index - start < limit and "page.goto(" not in lines[start]:
                    start -= 1
                end = index
                while end < len(lines) - 1 and end - start < limit and not re.search(r"click\(.*role\s*:\s*[\"']button", lines[end]):
                    end += 1
                block = [ln.strip().replace("`", "'") for ln in lines[start:end + 1] if ln.strip()]
                if any("page.goto(" in ln for ln in block):
                    return block[:limit]
    return []

def outsider_note(project_dir: Path, app: dict[str, Any], specs: list[str], limit: int = 2600, lead: str | None = None) -> str:
    if not app.get("names") and not app.get("visits"):
        return ""
    parts = [lead.strip() if lead else (
             f"{OUTSIDER_HEAD}: this requirement extends an application an earlier stage of the same task already "
             "delivered; its sign-up, sign-in and navigation already exist. A step of yours that passes through them "
             "uses exactly the names the earlier acceptance specs use.")]
    if app.get("names"):
        parts.append("Names used by the earlier specs: " + _capped(list(app["names"]), 1200, ", "))
    excerpt = _sign_in_excerpt(project_dir, specs)
    if excerpt:
        parts.append("How an earlier spec signs in:\n" + "\n".join("    " + ln for ln in excerpt))
    if app.get("visits"):
        parts.append("Addresses the earlier specs open: " + _capped(list(app["visits"]), 400, ", "))
    return "\n".join(parts)[:limit]
