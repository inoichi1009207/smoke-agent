from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

from .seed_identity import HARNESS_JS, _backend_dir
from .seed_restart import (CLEANUP_RESERVE, GROWING_TABLE, INIT_DONE, SKIP_DIRS, TRANSIENT, _Deadline, _default_where,
                           _failure_line, _given_texts, _insert_site, _run, _snap, _stack_site, compare_snapshots,
                           seed_names)
from .settings import setting_on

CHECK = "data-lifecycle"
MAX_FINDINGS = 8
MAX_PROBES = 3
MIN_START_SECONDS = 8.0
PROBE_SUFFIX = "-renamed-probe"
SENTINEL = "arc_lifecycle_sentinel"
CORPUS_CAP = 40_000_000
COPY_IGNORE = shutil.ignore_patterns("node_modules", "*.db", "*.db-wal", "*.db-shm", "*.db-journal", "*.sqlite",
                                     "*.sqlite3", ".arc-test-db", "test-results", "playwright-report", ".git")
SOURCE_EXT = {".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".json", ".sql"}
SOURCE_SKIP = set(SKIP_DIRS) | {"e2e-support", "playwright-report", "test-results", ".git", "coverage", "build"}
BACKTICK = re.compile(r"`([^`\n]{1,120})`")
PREPROVISION = re.compile(r"(?i)\bpre-?provision")
NEGATION = re.compile(r"(?i)(?:\b(?:none|not|no|nobody|nothing|unused|nonexistent|non-existent|absent|never|without|"
                      r"neither|nor)\b|n't\b)")
CLAUSE_END = re.compile(r"`|[.;:!?](?:\s|$)")

ENVIRONMENT = re.compile(r"(?i)cannot find module '(?![A-Za-z]:[\\/]|[./\\])|no init entry point|ERR_DLOPEN|"
                         r"was compiled against a different Node\.js version|NODE_MODULE_VERSION")
MISSING_COLUMN = re.compile(r"(?i)(?:table\s+[`\"']?(\w+)[`\"']?\s+has no column named\s+[`\"']?(\w+))|"
                            r"no such column:?\s+[`\"']?([\w.]+)")
KIND_ORDER = ("empty_start_failed", "upgrade_failed", "restart_failed", "renamed_resurrects", "renamed_reverted",
              "deleted_resurrects", "restart_duplicates", "upgrade_missing_seed", "scenario_seed_missing", "duplicate_fixture")

FIX_SEED = ("Seed each pre-provisioned record exactly ONCE -- e.g. this round's records as one versioned data migration run "
            "once in one transaction and recorded as applied -- guarded by a seed-marker table keyed by a stable seed key, "
            "never by looking the record up by its display name: CREATE TABLE IF NOT EXISTS seed_markers (key TEXT "
            "PRIMARY KEY); for each record (key e.g. 'workbook:EVO-M03-CLEAR-TEXT') run SELECT 1 FROM seed_markers WHERE "
            "key = ?, and only when that finds nothing insert the record AND its marker in the same transaction. Run this "
            "seeding step on EVERY start, for an empty database and for an existing (upgraded) one alike, not only when "
            "a table was just created or the database was empty. When the marker table is first created on an existing "
            "database, write the markers of the records the older version already created (looked up once by their "
            "natural key) instead of inserting them again. A record a user renamed or deleted must stay renamed or "
            "deleted after a restart, and a restart must not add a second copy.")
FIX_DUPLICATE = ('The fixture check counts more than one record of this kind with this stated name; it cannot see every owner or parent column, so first read these records and check whether they belong to the same owner or parent. Only if they do (the same record created twice): keep the valid seed record (the one the seed foundation created) with everything that references it, and remove only the second creation, moving any contents, cells or settings only the second creation wrote onto the record that stays. If they belong to different owners or parents they are separate records: change nothing for them. Do not merge, rename or remove any other record. '
                 "A migration that adds records recognises those the seed "
                 "foundation already created only inside its first, not yet applied run (same kind, same owner or "
                 "parent, stated name); whether it was applied is decided by its stable version key, never by a name a "
                 "user can rename.")
FIX_MIGRATION = ("Make schema evolution idempotent and run it on every start, before any seeding: CREATE TABLE/INDEX IF "
                 "NOT EXISTS; for each column the current code uses that an older database may lack, read PRAGMA "
                 "table_info(<table>) and, when the column is missing, ALTER TABLE <table> ADD COLUMN <column> <type> "
                 "DEFAULT <value>, then backfill the existing rows -- all inside one transaction (BEGIN ... COMMIT, "
                 "ROLLBACK on error). Before creating a case-insensitive UNIQUE index, check for existing rows that "
                 "differ only by letter case and resolve them first. Every start on an existing database -- one created "
                 "by this version or by the inherited version -- must succeed and keep the data.")
FIX_EMPTY = ("The delivered package starts without database.db, so the first start must build everything from nothing: "
             "create every table before any index, trigger, migration or seed that uses it (CREATE TABLE/INDEX IF NOT "
             "EXISTS), run ALTER TABLE ADD COLUMN only when PRAGMA table_info lacks the column, then seed the "
             "pre-provisioned records once, guarded by a seed-marker table keyed by a stable seed key (seed_markers(key "
             "TEXT PRIMARY KEY), written in the same transaction as the records). Schema steps and seeding must be "
             "idempotent: the same code runs on an empty, an upgraded and an already initialised database.")

class _Stop(Exception):
    pass

class _ProbeUnknown(Exception):
    pass

def scenario_seed_names(requirement_text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not requirement_text or not requirement_text.strip():
        return out
    tree = None
    try:
        import yaml
        tree = yaml.safe_load(requirement_text)
    except Exception:
        tree = None
    if isinstance(tree, (dict, list)):
        _walk_scenarios(tree, out)
        return out
    for i, line in enumerate(requirement_text.splitlines(), 1):
        if re.match(r"(?i)^\s*(?:-\s*)?given\b", line) and PREPROVISION.search(BACKTICK.sub(" ", line)):
            names = _literals(line)
            if names:
                out[f"line {i}"] = names
    return out

def _literals(text: str) -> list[str]:
    names: list[str] = []
    for m in BACKTICK.finditer(re.sub(r"\s+", " ", text)):
        lit = m.group(1).strip()
        if lit and lit not in names:
            names.append(lit)
    return names

def _walk_scenarios(node, out: dict[str, list[str]]) -> None:
    if isinstance(node, dict):
        steps = node.get("steps")
        if isinstance(steps, list) and isinstance(node.get("name"), str):
            names: list[str] = []
            for step in steps:
                if (isinstance(step, dict) and str(step.get("keyword", "")).strip().upper() == "GIVEN"
                        and isinstance(step.get("content"), str)
                        and PREPROVISION.search(BACKTICK.sub(" ", step["content"]))):
                    names += [n for n in _literals(step["content"]) if n not in names]
            if names:
                key = re.sub(r"\s+", " ", node["name"]).strip()
                k, i = key, 2
                while k in out:
                    k, i = f"{key} ({i})", i + 1
                out[k] = names
        for v in node.values():
            _walk_scenarios(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_scenarios(v, out)

def _given_step_texts(requirement_text: str) -> list[str]:
    texts: list[str] = []
    try:
        import yaml
        tree = yaml.safe_load(requirement_text)
        if isinstance(tree, (dict, list)):
            _given_texts(tree, texts)
    except Exception:
        pass
    texts += [ln for ln in requirement_text.splitlines() if re.match(r"(?i)^\s*(?:-\s*)?given\b", ln)]
    return [re.sub(r"\s+", " ", t) for t in texts]

def vacancy_names(requirement_text: str, names) -> set[str]:
    wanted = {n.strip() for n in names if isinstance(n, str) and n.strip()}
    out: set[str] = set()
    if not requirement_text or not wanted:
        return out
    for text in _given_step_texts(requirement_text):
        for m in BACKTICK.finditer(text):
            lit = m.group(1).strip()
            if lit not in wanted or lit in out:
                continue
            before = CLAUSE_END.split(text[:m.start()])[-1]
            after = CLAUSE_END.split(text[m.end():])[0]
            if NEGATION.search(before + " " + after):
                out.add(lit)
    return out

def _q(ident) -> str:
    return '"' + str(ident).replace('"', '""') + '"'

def _connect_ro(path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)

def _user_tables(con) -> list[str]:
    return [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' "
                                      "order by name") if r[0] != SENTINEL]

def names_in_db(db_path, names, skip_growing: bool = False) -> dict[str, list[tuple[str, str, int | None]]]:
    wanted = sorted({n.strip() for n in names if isinstance(n, str) and n.strip()})
    out: dict[str, list[tuple[str, str, int | None]]] = {n: [] for n in wanted}
    if not wanted:
        return out
    con = _connect_ro(db_path)
    try:
        for table in _user_tables(con):
            if skip_growing and GROWING_TABLE.search(table):
                continue
            try:
                cols = [r[1] for r in con.execute(f"pragma table_info({_q(table)})")]
            except sqlite3.Error:
                continue
            for col in cols:
                trimmed = f"trim({_q(col)}, ' ' || char(9, 10, 13))"
                for i in range(0, len(wanted), 400):
                    chunk = wanted[i:i + 400]
                    cond = f"typeof({_q(col)}) = 'text' and {trimmed} in ({','.join('?' * len(chunk))})"
                    try:
                        rows = con.execute(f"select rowid, {trimmed} from {_q(table)} where {cond}", chunk).fetchall()
                    except sqlite3.OperationalError:
                        try:
                            rows = [(None, v) for (v,) in
                                    con.execute(f"select {trimmed} from {_q(table)} where {cond}", chunk)]
                        except sqlite3.Error:
                            rows = []
                    for rid, value in rows:
                        if value in out:
                            out[value].append((table, col, rid))
        return out
    finally:
        con.close()

def _cell_text(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="ignore")
    return str(value)

def db_corpus(db_paths) -> tuple[set[str], str] | None:
    exact: set[str] = set()
    parts: list[str] = []
    size = 0
    for path in db_paths:
        if not path:
            continue
        con = _connect_ro(path)
        try:
            for table in _user_tables(con):
                try:
                    cursor = con.execute(f"select * from {_q(table)}")
                except sqlite3.Error:
                    continue
                for row in cursor:
                    for value in row:
                        text = _cell_text(value)
                        if text is None:
                            continue
                        exact.add(text.strip())
                        parts.append(text)
                        size += len(text)
                        if size > CORPUS_CAP:
                            return None
        finally:
            con.close()
    return exact, "\x00".join(parts).casefold()

def name_present(corpus, name: str) -> bool:
    if corpus is None:
        return True
    exact, blob = corpus
    n = name.strip()
    return n in exact or n.casefold() in blob

def resurrected(db_path, table: str, column: str, name: str, kind: str) -> str:
    found = names_in_db(db_path, [name, name + PROBE_SUFFIX])
    has_name = any(t == table and c == column for t, c, _ in found.get(name, []))
    if not has_name:
        return ""
    if kind == "delete":
        return "new"
    renamed_left = any(t == table and c == column for t, c, _ in found.get(name + PROBE_SUFFIX, []))
    return "new" if renamed_left else "restored"

def duplicate_seeds(db_a, db_b, names, deadline: float | None = None) -> tuple[list[dict], list[dict]]:
    items = compare_snapshots(str(db_a), str(db_b), deadline, set(names))
    return [d for d in items if d["seed_name"]], [d for d in items if not d["seed_name"]]

def _has_token(db_path, token: str) -> bool:
    if not db_path:
        return False
    try:
        con = _connect_ro(db_path)
        try:
            return bool(con.execute(f"select 1 from {_q(SENTINEL)} where token = ?", [token]).fetchone())
        finally:
            con.close()
    except sqlite3.Error:
        return False

def _count_in(found: dict, name: str, table: str, column: str) -> int:
    return sum(1 for t, c, _ in found.get(name, []) if t == table and c == column)

def _source_roots(project: Path, backend: Path) -> list[Path]:
    roots = []
    for root in (backend / "src", project / "frontend" / "src", project / "src"):
        if root.is_dir() and all(root.resolve() != r.resolve() for r in roots):
            roots.append(root)
    return roots

def _source_hits(project: Path, backend: Path, names) -> dict[str, str]:
    remaining = {n for n in names if n}
    hits: dict[str, str] = {}
    files: list[Path] = []
    for root in _source_roots(project, backend):
        for dirpath, dirs, fnames in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SOURCE_SKIP)
            for f in sorted(fnames):
                if os.path.splitext(f)[1].lower() in SOURCE_EXT and ".spec." not in f and ".test." not in f:
                    files.append(Path(dirpath) / f)
    files.sort(key=lambda p: (0 if "database" in p.parts else 1, str(p)))
    for path in files:
        if not remaining:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n in [n for n in remaining if n in text]:
            line = text.count("\n", 0, text.index(n)) + 1
            try:
                rel = path.relative_to(project).as_posix()
            except ValueError:
                rel = path.as_posix()
            hits[n] = f"{rel}:{line}"
            remaining.discard(n)
    return hits

def _error_site(text: str, copy_dir: str, backend: Path, project: Path, err: str) -> str:
    site = _stack_site(text, copy_dir, backend, project)
    if site:
        return site
    m = MISSING_COLUMN.search(err) or re.search(r"(?i)constraint failed: ([A-Za-z_]\w*)\.", err) or \
        re.search(r"(?i)table [`\"']?([A-Za-z_]\w*)[`\"']? already exists", err)
    table = next((g for g in (m.groups() if m else ()) if g), None)
    if table and "." in table:
        table = table.split(".", 1)[0]
    found = _insert_site(backend, project, table) if table else None
    return found[0] if found else _default_where(backend, project)

class _Start:
    def __init__(self, tag: str, rc, text: str, why: str, out_dir: str):
        self.tag, self.rc, self.text, self.why = tag, rc, text or "", why or ""
        self.boot = _snap(out_dir, "boot.db")
        self.lazy = _snap(out_dir, "lazy.db")
        self.ok = not self.why and rc == 0 and bool(self.boot) and bool(INIT_DONE.search(self.text))

    def snaps(self) -> list[str]:
        return [p for p in (self.boot, self.lazy) if p]

class _Ctx:
    def __init__(self, project: Path, backend: Path, nm: str, node: str, deadline: float, log, wd: str):
        self.project, self.backend, self.nm, self.node, self.deadline, self.log, self.wd = \
            project, backend, nm, node, deadline, log, wd
        self.harness = os.path.join(wd, "seed_harness.js")
        with open(self.harness, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(HARNESS_JS)
        self.db_name = "database.db"
        self.starts = 0

    def left(self) -> float:
        return self.deadline - CLEANUP_RESERVE - time.time()

    def start(self, copy_dir: str, data_dir: str, tag: str) -> _Start:
        if self.left() < MIN_START_SECONDS:
            raise _Stop(f"{self.left():.0f}s left before start {tag}")
        os.makedirs(data_dir, exist_ok=True)
        dbfile = os.path.join(data_dir, self.db_name)
        env = dict(os.environ)
        env.update(NODE_PATH=self.nm, ARC_DB_FILE=dbfile, DATABASE_FILE=dbfile, DATA_DIR=data_dir, PORT="0",
                   NODE_ENV="test")
        out = os.path.join(self.wd, "out-" + tag)
        t0 = time.time()
        rc, text, why = _run(self.node, self.harness, copy_dir, out, env, self.deadline)
        self.starts += 1
        st = _Start(tag, rc, text, why, out)
        state = "ok" if st.ok else f"rc={rc}{' (' + why + ')' if why else ''}"
        self.log(f"[lifecycle]   start {tag}: {state}, {time.time() - t0:.1f}s")
        return st

    def norm(self, line: str) -> str:
        out = line
        for base in {self.wd, self.wd.replace("\\", "/"), self.wd.replace("/", "\\")}:
            out = re.sub(re.escape(base) + r"[\\/]?[^\s'\"`),]*", "<tmp>", out, flags=re.I)
        return out

    def definite_error(self, st: _Start) -> str | None:
        if st.ok or st.why or st.rc in (0, None) or INIT_DONE.search(st.text) or ENVIRONMENT.search(st.text):
            return None
        err = _failure_line(st.text)
        if not err or TRANSIENT.search(err):
            return None
        return self.norm(err)

def _sqlite_files(data_dir: str) -> list[str]:
    out = []
    try:
        for f in sorted(os.listdir(data_dir)):
            p = os.path.join(data_dir, f)
            if os.path.isfile(p) and not re.search(r"-(?:wal|shm|journal)$", f):
                try:
                    with open(p, "rb") as fh:
                        if fh.read(16) == b"SQLite format 3\x00":
                            out.append(f)
                except OSError:
                    pass
    except OSError:
        pass
    return out

def _clone(src: str, dst: str, token: str | None, mutate=None) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
        s.close()
        d.execute("PRAGMA foreign_keys = OFF")
        if mutate:
            mutate(d)
        if token:
            d.execute(f"CREATE TABLE IF NOT EXISTS {_q(SENTINEL)} (token TEXT)")
            d.execute(f"INSERT INTO {_q(SENTINEL)} (token) VALUES (?)", [token])
        d.commit()
    finally:
        try:
            s.close()
        except Exception:
            pass
        d.close()

class _State:
    def __init__(self, inherited: bool):
        self.items: list[tuple[str, dict]] = []
        self.paths = {"empty": "unknown", "restart": "unknown", "upgrade": "unknown" if inherited else "skipped",
                      "resurrection": "unknown"}
        self.missing: dict[str, list[str]] = {}
        self.missing_secondary: dict[str, list[str]] = {}
        self.notes: list[str] = []
        self.duplicated: set[str] = set()
        self.strict = False
        self.misplaced: dict[str, str] = {}
        self.kinds: dict[str, str] = {}
        self.duplicates: dict[str, str] = {}
        self.duplicate_counts: dict[str, int] = {}

    def add(self, kind: str, where: str, problem: str, fix: str, requirement: str = "") -> None:
        self.items.append((kind, {"where": f"{CHECK}: {where}", "requirement": requirement, "problem": problem,
                                  "fix": fix, "check": f"{CHECK}/{kind}"}))

    def note(self, log, text: str) -> None:
        self.notes.append(text)
        log(f"[lifecycle] {text}")

    def findings(self) -> list[dict]:
        order = {k: i for i, k in enumerate(KIND_ORDER)}
        return [d for _, d in sorted(self.items, key=lambda kd: order.get(kd[0], 99))][:MAX_FINDINGS]

def _listed(names, limit: int = 30) -> str:
    names = list(names)
    text = ", ".join(f"`{n}`" for n in names[:limit])
    return text + (f" and {len(names) - limit} more" if len(names) > limit else "")

def _scenario_label(scenarios) -> str:
    scenarios = list(dict.fromkeys(scenarios))
    text = "; ".join(scenarios[:3])
    return text + (f" (+{len(scenarios) - 3} more)" if len(scenarios) > 3 else "")

ALIAS_COLUMN = re.compile(r"(?i)(?:^|_)(?:code|alias|aliases|key|slug|ext(?:ernal)?|ext_?id|external_?id|ref|reference|"
                          r"fixture|seed|evo|legacy|import)(?:_?(?:id|key|code|name|ref))?$|^evo|^seed_|^fixture_")
DISPLAY_COLUMN = re.compile(r"(?i)^(?:name|title|display_?name|username|user_?name|login|full_?name|label|subject|"
                            r"\w+_name|\w+_title)$")

SHOWN_NAME_COLUMN = re.compile(r"(?i)^display_?name$")
HOLDER_COLUMN = re.compile(r"(?i)^(?:name|slug|handle|login)$")

ACCOUNT_TABLE = re.compile(r"(?i)user|account|member|profile|person|people|contact|employee|customer|staff|owner|author|"
                           r"assignee|reviewer|collaborator|admin")
DISPLAY_NAME_EVIDENCE = "pages that show the display name"

def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()

def _display_name_differs(rows, name: str, stated: set[str]) -> str:
    for path, table, column, rid in rows:
        if rid is None or not HOLDER_COLUMN.match(column) or ACCOUNT_TABLE.search(table):
            continue
        try:
            con = _connect_ro(path)
            try:
                cols = [r[1] for r in con.execute(f"pragma table_info({_q(table)})")]
                shown = [c for c in cols if SHOWN_NAME_COLUMN.match(c)]
                if not shown:
                    continue
                row = con.execute(f"select {_q(shown[0])} from {_q(table)} where rowid = ?", [rid]).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        value = row[0] if row else None
        if (isinstance(value, str) and value.strip() and not _same_text(value, name)
                and not any(_same_text(value, s) for s in stated)):
            return (f"table `{table}` holds it in `{column}`, but that row's `{shown[0]}` is `{value.strip()[:80]}` -- "
                    f"{DISPLAY_NAME_EVIDENCE} list the record under that other name")
    return ""

def display_misplaced(db_paths, names) -> dict[str, str]:
    out: dict[str, str] = {}
    wanted = [n.strip() for n in names if isinstance(n, str) and n.strip()]
    if not wanted:
        return out
    hits: dict[str, list[tuple[str, str, str, int | None]]] = {n: [] for n in wanted}
    for path in [p for p in db_paths or [] if p]:
        try:
            for name, rows in names_in_db(path, wanted).items():
                hits.setdefault(name, []).extend((path, t, c, rid) for t, c, rid in rows)
        except sqlite3.Error:
            return {}
    for name, rows in hits.items():
        if rows and any(not ALIAS_COLUMN.search(c) for _p, _t, c, _r in rows):

            evidence = _display_name_differs(rows, name, set(wanted))
            if evidence:
                out[name] = evidence
            continue
        if not rows:
            continue
        evidence = ""
        for path, table, column, rid in rows:
            if rid is None:
                continue
            try:
                con = _connect_ro(path)
                try:
                    cols = [r[1] for r in con.execute(f"pragma table_info({_q(table)})")]
                    shown = [c for c in cols if DISPLAY_COLUMN.match(c) and not ALIAS_COLUMN.search(c) and c != column]
                    if not shown:
                        continue
                    row = con.execute(f"select {', '.join(_q(c) for c in shown)} from {_q(table)} where rowid = ?", [rid]).fetchone()
                finally:
                    con.close()
            except sqlite3.Error:
                continue
            values = [(c, v) for c, v in zip(shown, row or ()) if isinstance(v, str) and v.strip()]
            if values and all(v.strip() != name for _c, v in values):
                c, v = values[0]
                evidence = f"table `{table}` holds it only in column `{column}`; that row's `{c}` is `{v.strip()[:80]}`"
                break
        if evidence:
            out[name] = evidence
    return out

OWN_NAME_COLUMN = re.compile(r"(?i)^(?:name|title|display_?name|username|user_?name|login|handle|full_?name)$")
KIND_TABLE = {"workbook": re.compile(r"(?i)workbook|spreadsheet"), "organization": re.compile(r"(?i)org"),
              "repository": re.compile(r"(?i)repo"), "account": re.compile(r"(?i)user|account"),
              "team": re.compile(r"(?i)team")}
LINK_TABLE = re.compile(r"(?i)member|share|collab|permission|access|invit|log|histor|event|activit|audit|star|watch|"
                        r"follow|session|token|notif|recent|favou?rite|pin|bookmark|request|map|link|assoc|role|setting|"
                        r"pref|cell|range|branch|issue|label|tag|file|commit|alias|redirect|rename|version|"
                        r"migration|seed|marker|snapshot|backup|archive|trash|deleted")

SCOPE_LIKE = re.compile(r"(?:_id|_by|_uuid|_key|_ref|_type|Id|By|Uuid|Ref|Type)$|"
                        r"(?i:^(?:owner|parent|user|org|organi[sz]ation|team|account|creator|author|namespace|workspace|"
                        r"tenant|project|group|scope|realm|space))")

CHILD_POINTER = re.compile(r"^(?:(?i:active|current|selected)_(?P<stem>[A-Za-z]+?)_?(?i:id)|"
                           r"(?:active|current|selected)(?P<camel>[A-Z][a-z]+)Id)$")

OWNERISH_TABLE = re.compile(r"(?i)owner|parent|user|org|team|account|creator|member|author|principal|group|tenant|"
                            r"namespace|workspace|admin|person|people|profile|project|board|space|folder|collection")

def _plural_forms(stem: str) -> set[str]:
    stem = stem.lower()
    forms = {stem, stem + "s", stem + "es"}
    if stem.endswith("y"):
        forms.add(stem[:-1] + "ies")
    return forms

def _singular(table: str) -> str:
    t = table.lower()
    return t[:-3] + "y" if t.endswith("ies") else t[:-1] if t.endswith("s") else t

def _child_table(con, tables: dict[str, str], table: str, column: str, stem: str) -> str | None:
    try:
        declared = {str(r[2]).lower() for r in con.execute(f"pragma foreign_key_list({_q(table)})") if str(r[3]) == column}
    except sqlite3.Error:
        return None
    if declared:
        if len(declared) != 1:
            return None
        target = next(iter(declared))
        candidates = [tables[target]] if target in tables else []
    else:
        forms = _plural_forms(stem)
        forms |= {"work" + f for f in forms}
        candidates = [tables[f] for f in sorted(forms) if f in tables]
    if len(candidates) != 1:
        return None
    child = candidates[0]
    if child.lower() == table.lower() or OWNERISH_TABLE.search(child):
        return None
    return child

def _refers_back(con, child: str, table: str) -> bool:
    want = _singular(table) + "_id"
    try:
        cols = [str(r[1]) for r in con.execute(f"pragma table_info({_q(child)})")]
        fks = [(str(r[3]), str(r[2]).lower()) for r in con.execute(f"pragma foreign_key_list({_q(child)})")]
    except sqlite3.Error:
        return False
    col = next((c for c in cols if c.lower() == want), None)
    if col is None:
        return False
    targets = {t for c, t in fks if c == col}
    return not targets or targets == {table.lower()}

def top_level_entity(con, table: str, name_column: str) -> bool:
    try:
        info = list(con.execute(f"pragma table_info({_q(table)})"))
        tables = {str(r[0]).lower(): str(r[0]) for r in con.execute("select name from sqlite_master where type='table'")}
        declared = {str(r[3]) for r in con.execute(f"pragma foreign_key_list({_q(table)})")}
        con.execute(f"select rowid from {_q(table)} limit 1").fetchall()
    except sqlite3.Error:
        return False
    if not info:
        return False
    composite = sum(1 for r in info if r[5]) > 1
    for _cid, col, _type, _notnull, _default, pk in info:
        if col == name_column:
            continue
        if col in declared:
            scope = True
        elif pk:
            scope = composite
        elif col.lower() == "id":
            continue
        else:
            scope = bool(SCOPE_LIKE.search(col))
        if not scope:
            continue
        m = None if pk else CHILD_POINTER.match(col)
        child = _child_table(con, tables, table, col, m.group("stem") or m.group("camel")) if m else None
        if child is None or not _refers_back(con, child, table):
            return False
    return True

def _duplicate_places(db_path, kinds: dict[str, str]) -> dict[tuple[str, str, str, str], int]:
    wanted = {str(n).strip(): k for n, k in (kinds or {}).items() if str(n).strip() and k in KIND_TABLE}
    out: dict[tuple[str, str, str, str], int] = {}
    if not wanted or not db_path:
        return out
    try:
        found = names_in_db(db_path, list(wanted), skip_growing=True)
    except sqlite3.Error:
        return out
    try:
        con = _connect_ro(db_path)
    except sqlite3.Error:
        return out
    try:
        entity: dict[tuple[str, str], bool] = {}
        for name, rows in found.items():
            kind = wanted.get(name)
            places: dict[tuple[str, str], int] = {}
            for table, column, _rid in rows:
                if OWN_NAME_COLUMN.match(column) and KIND_TABLE[kind].search(table) and not LINK_TABLE.search(table):
                    places[(table, column)] = places.get((table, column), 0) + 1
            for (table, column), n in places.items():
                if n < 2:
                    continue
                if (table, column) not in entity:
                    entity[(table, column)] = top_level_entity(con, table, column)
                if entity[(table, column)]:
                    out[(kind, table, column, name)] = n
    finally:
        con.close()
    return out

def count_key(kind: str, table: str, column: str, name: str) -> str:
    return f"{kind}|{table}|{column}|{name}"

def duplicate_fixtures(db_path, kinds: dict[str, str]) -> dict[str, tuple[int, str, str]]:
    out: dict[str, tuple[int, str, str]] = {}
    for (_kind, table, column, name), n in _duplicate_places(db_path, kinds).items():
        if name not in out or n > out[name][0]:
            out[name] = (n, table, column)
    return out

def _name_files(project: Path, backend: Path, names, limit: int = 4) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {n: [] for n in names if n}
    root = backend / "src"
    if not out or not root.is_dir():
        return out
    files: list[Path] = []
    for dirpath, dirs, fnames in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SOURCE_SKIP)
        files += [Path(dirpath) / f for f in sorted(fnames)
                  if os.path.splitext(f)[1].lower() in SOURCE_EXT and ".spec." not in f and ".test." not in f]
    files.sort(key=lambda q: (0 if "database" in q.parts else 1, str(q)))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, where in out.items():
            if n in text and len(where) < limit:
                try:
                    where.append(path.relative_to(project).as_posix())
                except ValueError:
                    where.append(path.as_posix())
    return out

def _duplicate_check(ctx, st: _State, scen: dict[str, list[str]], vacant: set[str], db_paths, backend: Path,
                     project: Path) -> None:
    log = ctx.log
    names = {n for ns in scen.values() for n in ns if n not in vacant}
    kinds = {n: k for n, k in (st.kinds or {}).items() if n in names}
    if not kinds or not db_paths:
        return
    try:
        places = _duplicate_places(db_paths[-1], kinds)
    except Exception as exc:
        st.note(log, f"duplicate-fixture rule not applied: {exc!r}")
        return
    dups: dict[str, tuple[int, str, str]] = {}
    for (_kind, table, column, name), n in places.items():
        if name not in dups or n > dups[name][0]:
            dups[name] = (n, table, column)
    if not dups:
        return
    st.duplicates.update({n: f"{c} rows in {t}" for n, (c, t, _col) in dups.items()})
    st.duplicate_counts.update({count_key(*key): int(n) for key, n in places.items()})
    by_scenario = {s: [n for n in ns if n in dups] for s, ns in scen.items()}
    by_scenario = {s: ns for s, ns in by_scenario.items() if ns}
    try:
        where = _name_files(project, backend, list(dups))
    except Exception:
        where = {}
    items = "; ".join(f"duplicate fixture `{n}` ({c} rows in {t}: the database holds {c} records of that kind with that name)"
                      + (f" -- named in {', '.join(where[n])}" if where.get(n) else "")
                      for n, (c, t, _col) in list(dups.items())[:20])
    more = f" and {len(dups) - 20} more" if len(dups) > 20 else ""
    site = _insert_site(backend, project, next(iter(dups.values()))[1])
    st.add("duplicate_fixture", site[0] if site else _default_where(backend, project),
           "After the first start on an empty database (initializeDatabase plus the ensure*/seed* helpers), "
           f"{len(dups)} pre-provisioned record(s) exist more than once under their stated name in a table the check "
           f"takes for top-level: {items}{more}. When the copies share the same owner or parent, the requirement "
           "states each of them as ONE record."
           + (f" Insert: {site[1]}" if site else ""), FIX_DUPLICATE, requirement=_scenario_label(by_scenario))
    for n, (c, t, col) in dups.items():
        log(f"[lifecycle]   duplicate fixture {n!r}: {c} rows in {t}.{col}")

def _strict_missing(ctx, st: _State, scen: dict[str, list[str]], corpus, hits: dict[str, str], vacant: set[str],
                    db_paths, backend: Path, project: Path) -> None:
    log = ctx.log
    names_all = list(dict.fromkeys(n for names in scen.values() for n in names))
    try:
        misplaced = display_misplaced(db_paths, [n for n in names_all if name_present(corpus, n) and n not in vacant])
    except Exception as exc:
        st.note(log, f"display-name rule not applied: {exc!r}")
        misplaced = {}
    st.misplaced.update(misplaced)
    by_scenario: dict[str, list[str]] = {}
    for s, names in scen.items():
        bad: list[str] = []
        for n in names:
            if n in vacant:
                continue
            if n in misplaced:
                bad.append(n)
            elif not name_present(corpus, n):
                if n in hits:
                    st.note(log, f"`{n}` ({s}) is in the source ({hits[n]}) but no table holds it after the empty start: "
                                 "a seeding path the harness does not run (unverified), not reported")
                else:
                    bad.append(n)
        if bad:
            by_scenario[s] = bad
            st.missing[s] = bad
    if not by_scenario:
        return
    absent = list(dict.fromkeys(n for bad in by_scenario.values() for n in bad if n not in misplaced))
    wrong = [n for n in dict.fromkeys(n for bad in by_scenario.values() for n in bad) if n in misplaced]
    parts = []
    if absent:
        parts.append(f"no table holds {len(absent)} record(s) the requirement's scenarios pre-provision: {_listed(absent)}")
    if wrong:
        parts.append(f"{len(wrong)} record(s) exist only under ANOTHER display name (the stated name sits in an alias / "
                     "code column; the tests open the record by the stated name): "
                     + "; ".join(f"`{n}` ({misplaced[n]})" for n in wrong[:10]))
    st.add("scenario_seed_missing", _default_where(backend, project),
           "After the first start on an empty database (initializeDatabase plus the ensure*/seed* helpers), "
           + "; ".join(parts) + ". These records must exist under exactly the stated name (their own name / title / "
           "username, never an alias) on every path: an empty database, a database upgraded from the inherited version, "
           "and after restarts.", FIX_SEED, requirement=_scenario_label(by_scenario))
    for s, bad in list(by_scenario.items())[:20]:
        log(f"[lifecycle]   {s}: missing {bad}")
    log(f"[lifecycle]   {len(absent)} pre-provisioned record(s) missing, {len(wrong)} under another display name, after the empty start")

def _lifecycle(ctx: _Ctx, st: _State, scen: dict[str, list[str]], requirement: str, inherited: Path | None) -> None:
    log, wd, project, backend = ctx.log, ctx.wd, ctx.project, ctx.backend
    all_names = list(dict.fromkeys(n for names in scen.values() for n in names))
    primaries = {s: names[0] for s, names in scen.items() if names}
    seed_set = set(all_names) | seed_names(requirement)
    cur = os.path.join(wd, "cur")
    shutil.copytree(str(backend), cur, ignore=COPY_IGNORE)

    d_main = os.path.join(wd, "d-main")
    e1 = ctx.start(cur, d_main, "empty1")
    if not e1.ok:
        err = ctx.definite_error(e1)
        if not err:
            st.note(log, f"empty start did not finish ({'timeout' if e1.why else 'rc=' + str(e1.rc)}); nothing "
                         f"reported: {e1.text[-240:]!r}")
            return
        e2 = ctx.start(cur, os.path.join(wd, "d-empty2"), "empty2")
        if ctx.definite_error(e2) != err:
            st.note(log, f"empty start error not reproduced by a second fresh start: {err!r}")
            return
        site = _error_site(e1.text, cur, backend, project, err)
        st.add("empty_start_failed", site,
               "The first start on an EMPTY database fails during the database initialisation (reproduced by a second "
               f"start on another empty database file): {err}. The delivered package carries no database.db, so the "
               "application must create every table, index and pre-provisioned record from nothing.", FIX_EMPTY)
        st.paths["empty"] = "failed"
        log(f"[lifecycle]   empty start failed: {err}")
        return
    files = _sqlite_files(d_main)
    if ctx.db_name not in files:
        if len(files) == 1:
            ctx.db_name = files[0]
        else:
            ctx.db_name = ""
            st.note(log, f"the app did not create its database at ARC_DB_FILE/DATABASE_FILE/DATA_DIR (found {files}); "
                         "restart, upgrade and resurrection paths not checked")
    empty_corpus = db_corpus(e1.snaps())
    hits = _source_hits(project, backend, all_names)
    vacant = vacancy_names(requirement, all_names)
    missing_first: dict[str, str] = {}
    if st.strict:
        _strict_missing(ctx, st, scen, empty_corpus, hits, vacant, e1.snaps(), backend, project)
    if st.kinds:
        _duplicate_check(ctx, st, scen, vacant, e1.snaps(), backend, project)
    for s, names in ({} if st.strict else scen).items():
        absent = [n for n in names if not name_present(empty_corpus, n) and n not in vacant]
        first = names[0] if names else None
        if first in absent:
            if first in hits:
                st.note(log, f"`{first}` ({s}) is in the source ({hits[first]}) but no table holds it after the empty "
                             "start: a seeding path the harness does not run (unverified), not reported")
            else:
                missing_first[s] = first
                st.missing[s] = [first]
        rest = [n for n in absent if n != first]
        if rest:
            st.missing_secondary[s] = rest
    if missing_first:
        names = list(dict.fromkeys(missing_first.values()))
        st.add("scenario_seed_missing", _default_where(backend, project),
               f"After the first start on an empty database (initializeDatabase plus the ensure*/seed* helpers), no "
               f"table holds {len(names)} record(s) the requirement's scenarios pre-provision: {_listed(names)}. These "
               "records must exist on every path: an empty database, a database upgraded from the inherited version, "
               "and after restarts.", FIX_SEED, requirement=_scenario_label(missing_first))
        log(f"[lifecycle]   {len(names)} pre-provisioned record(s) missing after the empty start")
    st.paths["empty"] = "failed" if (missing_first or (st.strict and st.missing)) else "ok"
    if not ctx.db_name:
        return
    main_db = os.path.join(d_main, ctx.db_name)
    base_db = os.path.join(wd, "base", ctx.db_name)
    _clone(main_db, base_db, None)

    restarts_finished = False
    before = e1
    for k in (2, 3):
        r = ctx.start(cur, d_main, f"restart{k}")
        if not r.ok:
            err = ctx.definite_error(r)
            if not err:
                st.note(log, f"start #{k} on the same file did not finish ({r.why or 'rc=' + str(r.rc)}); nothing reported")
                break
            c = ctx.start(cur, d_main, f"restart{k}-confirm")
            if ctx.definite_error(c) != err:
                st.note(log, f"start #{k} error not reproduced by another start on the same file: {err!r}")
                break
            st.add("restart_failed", _error_site(r.text, cur, backend, project, err),
                   f"The first start on an empty database succeeds, but start #{k} on that same database file fails "
                   f"during the database initialisation (reproduced by start #{k + 1}): {err}", FIX_MIGRATION)
            log(f"[lifecycle]   start #{k} failed: {err}")
            break
        try:
            named, unnamed = duplicate_seeds(before.lazy or before.boot, r.boot, seed_set, ctx.deadline - CLEANUP_RESERVE)
        except _Deadline:
            raise _Stop("deadline during the restart comparison")
        for d in unnamed:
            log(f"[lifecycle]   (log only: no seed name) start #{k} grew {d['table']}: {d['count_a']} -> "
                f"{d['count_b']} {d['sample'][:100]}")
        for d in named:
            site = _insert_site(backend, project, d["table"])
            st.add("restart_duplicates", site[0] if site else _default_where(backend, project),
                   f"Start #{k} on the same database file duplicates the pre-provisioned record(s) "
                   f"{_listed(d['seed_names'], 6)}: table `{d['table']}` grew from {d['count_a']} to {d['count_b']} rows "
                   f"during the initialisation; the record ({d['sample']}) was there {d['copies_a']} time(s) and is now "
                   f"there {d['copies_b']} times" + (f". Insert: {site[1]}" if site else "."), FIX_SEED)
            log(f"[lifecycle]   start #{k} duplicated {d['table']}: {d['count_a']} -> {d['count_b']}")
            st.duplicated |= set(d["seed_names"])
        if named:
            restarts_finished = True
            break
        before = r
    else:
        st.paths["restart"] = "ok"
        restarts_finished = True
    if any(k in ("restart_failed", "restart_duplicates") for k, _ in st.items):
        st.paths["restart"] = "failed"

    if inherited is not None:
        _upgrade(ctx, st, inherited, cur, e1, all_names, seed_set, scen)

    if not restarts_finished:
        st.note(log, "resurrection probes not run: the restarts on the same file did not all finish")
        return
    _probes(ctx, st, scen, primaries, base_db, cur, hits)

def _upgrade(ctx: _Ctx, st: _State, inherited: Path, cur: str, e1: _Start, all_names, seed_set, scen) -> None:
    first_item = len(st.items)
    try:
        complete = _upgrade_paths(ctx, st, inherited, cur, e1, all_names, seed_set, scen)
    finally:
        if len(st.items) > first_item:
            st.paths["upgrade"] = "failed"
    if len(st.items) == first_item and complete:
        st.paths["upgrade"] = "ok"

def _upgrade_paths(ctx: _Ctx, st: _State, inherited: Path, cur: str, e1: _Start, all_names, seed_set, scen) -> bool:
    log, wd, project, backend = ctx.log, ctx.wd, ctx.project, ctx.backend
    old = os.path.join(wd, "old")
    shutil.copytree(str(inherited), old, ignore=COPY_IGNORE)
    d_old = os.path.join(wd, "d-old")
    o = ctx.start(old, d_old, "old")
    files = _sqlite_files(d_old)
    if not o.ok or not files:
        st.note(log, f"the inherited backend did not build its database (rc={o.rc}{', ' + o.why if o.why else ''}, "
                     f"files {files}); upgrade not checked: {o.text[-200:]!r}")
        return False
    old_db = os.path.join(d_old, ctx.db_name if ctx.db_name in files else files[0])

    def upgraded_start(tag: str, data_dir: str) -> tuple[_Start, str]:
        token = uuid.uuid4().hex
        _clone(old_db, os.path.join(data_dir, ctx.db_name), token)
        return ctx.start(cur, data_dir, tag), token

    d_up = os.path.join(wd, "d-up")
    u1, token = upgraded_start("upgrade1", d_up)
    if not u1.ok:
        err = ctx.definite_error(u1)
        if not err:
            st.note(log, f"the current backend did not finish on the upgraded database ({u1.why or 'rc=' + str(u1.rc)}); "
                         "nothing reported")
            return False
        c, _ = upgraded_start("upgrade1-confirm", os.path.join(wd, "d-up-confirm"))
        if ctx.definite_error(c) != err:
            st.note(log, f"upgrade error not reproduced on a second copy: {err!r}")
            return False
        hint = (" Typical cause: the current code uses a column that the inherited database lacks, and no ALTER TABLE "
                "ADD COLUMN migration adds it to an existing table (CREATE TABLE IF NOT EXISTS does not change a table "
                "that already exists).") if MISSING_COLUMN.search(err) else ""
        st.add("upgrade_failed", _error_site(u1.text, cur, backend, project, err),
               "Starting the current backend on a database created by the INHERITED version fails during the database "
               f"initialisation (reproduced on a second copy of that database): {err}.{hint}", FIX_MIGRATION)
        log(f"[lifecycle]   upgrade failed: {err}")
        return True
    if not _has_token(u1.boot, token):
        st.note(log, "the current backend did not open the upgraded database file it was given; upgrade not checked")
        return False
    complete = True
    u2 = ctx.start(cur, d_up, "upgrade2")
    if not u2.ok:
        complete = False
        err = ctx.definite_error(u2)
        if err:
            c = ctx.start(cur, d_up, "upgrade2-confirm")
            if ctx.definite_error(c) == err:
                st.add("upgrade_failed", _error_site(u2.text, cur, backend, project, err),
                       "The current backend starts once on a database created by the INHERITED version, but the next "
                       f"start on that upgraded file fails during the initialisation (reproduced by a third start): {err}",
                       FIX_MIGRATION)
                log(f"[lifecycle]   second start on the upgraded database failed: {err}")
            else:
                st.note(log, f"second upgraded start error not reproduced: {err!r}")
        else:
            st.note(log, f"second start on the upgraded database did not finish ({u2.why or 'rc=' + str(u2.rc)})")

    present_empty = {n for n, where in names_in_db(e1.lazy or e1.boot, all_names).items() if where}
    if e1.lazy and e1.boot:
        present_empty |= {n for n, where in names_in_db(e1.boot, all_names).items() if where}
    up_corpus = db_corpus(u1.snaps() + (u2.snaps() if u2.ok else []))
    lost = [n for n in all_names if n in present_empty and not name_present(up_corpus, n)]
    if lost:
        lost_scen = [s for s, names in scen.items() if any(n in lost for n in names)]
        hits = _source_hits(project, backend, lost[:1])
        st.add("upgrade_missing_seed", hits.get(lost[0]) or _default_where(backend, project),
               f"{len(lost)} record(s) the scenarios pre-provision exist after a start on an EMPTY database but not after "
               f"the current version starts on a database created by the INHERITED version: {_listed(lost)}. New seed "
               "records are only added to new databases (seeding runs only when a table was just created or the "
               "database was empty), so an upgraded deployment lacks them.", FIX_SEED,
               requirement=_scenario_label(lost_scen))
        log(f"[lifecycle]   {len(lost)} record(s) missing after the upgrade")

    try:
        pairs = [(o.lazy or o.boot, u1.boot, "the first start of the current version on a database created by the "
                                              "inherited version")]
        if u2.ok:
            pairs.append((u1.lazy or u1.boot, u2.boot, "the second start of the current version on the upgraded database"))
        for a, b, what in pairs:
            named, _ = duplicate_seeds(a, b, seed_set, ctx.deadline - CLEANUP_RESERVE)
            for d in named:
                site = _insert_site(backend, project, d["table"])
                st.add("restart_duplicates", site[0] if site else _default_where(backend, project),
                       f"{what[0].upper() + what[1:]} duplicates the pre-provisioned record(s) "
                       f"{_listed(d['seed_names'], 6)}: table `{d['table']}` grew from {d['count_a']} to "
                       f"{d['count_b']} rows; the record ({d['sample']}) was there {d['copies_a']} time(s) and is now "
                       f"there {d['copies_b']} times" + (f". Insert: {site[1]}" if site else "."), FIX_SEED)
                log(f"[lifecycle]   upgrade duplicated {d['table']}: {d['count_a']} -> {d['count_b']}")
    except _Deadline:
        complete = False
        st.note(log, "upgrade duplicate comparison stopped by the deadline")
    return complete

def _plan_probes(base_db: str, scen: dict[str, list[str]], primaries: dict[str, str]):
    wanted = list(dict.fromkeys(primaries.values()))
    found = names_in_db(base_db, wanted)
    cands = []
    for name in wanted:
        where = found.get(name, [])
        if len(where) != 1:
            continue
        table, col, rid = where[0]
        if rid is None or GROWING_TABLE.search(table):
            continue
        scenario = next(s for s, n in primaries.items() if n == name)
        cands.append((name, scenario, table, col, rid))
    seen: dict[str, int] = {}
    ranked = []
    for c in cands:
        seen[c[2]] = seen.get(c[2], 0) + 1
        ranked.append((seen[c[2]], len(ranked), c))
    cands = [c for _, _, c in sorted(ranked)]
    rename = cands[0::2][:MAX_PROBES]
    delete = cands[1::2][:MAX_PROBES] or cands[:1]
    plan = []
    for i in range(MAX_PROBES):
        if i < len(rename):
            plan.append(("rename",) + rename[i])
        if i < len(delete):
            plan.append(("delete",) + delete[i])
    return plan

def _probes(ctx: _Ctx, st: _State, scen, primaries, base_db: str, cur: str, hits: dict[str, str]) -> None:
    log = ctx.log
    plan = _plan_probes(base_db, scen, primaries)
    if not plan:
        st.paths["resurrection"] = "skipped"
        st.note(log, "resurrection: no scenario record is held in exactly one row of a table; nothing to probe")
        return

    done = 0
    by_kind: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    try:
        for i, (kind, name, scenario, table, col, rid) in enumerate(plan, 1):
            if name in st.duplicated:
                st.note(log, f"probe {kind} `{name}` skipped: a plain restart already duplicates it")
                done += 1
                continue
            try:
                outcome = _probe(ctx, st, i, kind, name, table, col, rid, base_db, cur)
                if outcome:
                    by_kind.setdefault((kind if outcome is True else "rename_undone", table), []).append(
                        (name, scenario, col))
                done += 1
            except _ProbeUnknown:
                continue
    except _Stop:
        _probe_findings(ctx, st, by_kind, hits)
        raise
    _probe_findings(ctx, st, by_kind, hits)
    if by_kind:
        st.paths["resurrection"] = "failed"
    elif done == len(plan):
        st.paths["resurrection"] = "ok"

def _rename_undone_same_row(db_path, table: str, col: str, name: str, rid: int) -> bool:
    if rid is None:
        return False
    found = names_in_db(db_path, [name, name + PROBE_SUFFIX])
    rows = [r for t, c, r in found.get(name, []) if t == table and c == col]
    renamed_left = any(t == table and c == col for t, c, _ in found.get(name + PROBE_SUFFIX, []))
    return not renamed_left and rows == [rid]

def _probe(ctx: _Ctx, st: _State, i: int, kind: str, name: str, table: str, col: str, rid: int, base_db: str,
           cur: str) -> bool | str:
    log = ctx.log
    token = uuid.uuid4().hex

    def mutate(con):
        if kind == "rename":
            con.execute(f"UPDATE {_q(table)} SET {_q(col)} = ? WHERE rowid = ?", [name + PROBE_SUFFIX, rid])
        else:
            con.execute(f"DELETE FROM {_q(table)} WHERE rowid = ?", [rid])
    data_dir = os.path.join(ctx.wd, f"d-probe{i}")
    _clone(base_db, os.path.join(data_dir, ctx.db_name), token, mutate)
    p = ctx.start(cur, data_dir, f"probe{i}-{kind}")
    if not p.ok or not _has_token(p.boot, token):
        why = (p.why or f"rc={p.rc}") if not p.ok else "the start did not open the probe copy"
        st.note(log, f"probe {kind} `{name}`: no result ({why}); nothing reported")
        raise _ProbeUnknown()
    result = resurrected(p.boot, table, col, name, kind)
    if result == "new":
        log(f"[lifecycle]   probe {kind} `{name}` in {table}.{col}: back after the restart")
        return True
    if result == "restored":

        if setting_on("lifecycle_report_rename_undo", True) and _rename_undone_same_row(p.boot, table, col, name, rid):
            log(f"[lifecycle]   probe rename `{name}` in {table}.{col}: the start renamed the same row (rowid {rid}) "
                "back in place")
            return "undone"
        st.note(log, f"probe rename `{name}` in {table}.{col}: the start renamed the row back in place (log only)")
    elif p.lazy and resurrected(p.lazy, table, col, name, kind) == "new":
        st.note(log, f"probe {kind} `{name}` in {table}.{col}: back only after the ensure*/seed* helpers ran (log only)")
    else:
        log(f"[lifecycle]   probe {kind} `{name}` in {table}.{col}: stays {'renamed' if kind == 'rename' else 'deleted'}")
    return False

def _probe_findings(ctx: _Ctx, st: _State, by_kind, hits: dict[str, str]) -> None:
    project, backend = ctx.project, ctx.backend
    for (kind, table), items in by_kind.items():
        names = [n for n, _, _ in items]
        col = items[0][2]
        site = hits.get(names[0])
        if not site:
            found = _insert_site(backend, project, table)
            site = found[0] if found else _default_where(backend, project)
        if kind == "rename":
            problem = (f"Seed identity is tied to the display name: on a copy of the started database the only row of "
                       f"table `{table}` whose `{col}` is {_listed(names)} was renamed to <name>{PROBE_SUFFIX} (as a user "
                       f"renaming the pre-provisioned record would); the next start inserted a NEW `{table}` row named "
                       f"{_listed(names)} again, next to the renamed one. A restart must not resurrect a pre-provisioned "
                       "record a user renamed.")
            k = "renamed_resurrects"
        elif kind == "rename_undone":
            problem = (f"Seed identity is tied to the display name: on a copy of the started database the only row of "
                       f"table `{table}` whose `{col}` is {_listed(names)} was renamed to <name>{PROBE_SUFFIX} (as a user "
                       f"renaming the pre-provisioned record would); the next start renamed that same row (same rowid) "
                       f"back to {_listed(names)} during the initialisation, undoing the rename. A restart must not "
                       "revert a pre-provisioned record a user renamed.")
            k = "renamed_reverted"
        else:
            problem = (f"A deleted pre-provisioned record comes back: on a copy of the started database the only row of "
                       f"table `{table}` whose `{col}` is {_listed(names)} was deleted (as a user deleting it would); "
                       f"the next start inserted it again. A restart must not resurrect a pre-provisioned record a user "
                       "deleted.")
            k = "deleted_resurrects"
        st.add(k, site, problem, FIX_SEED, requirement=_scenario_label(s for _, s, _ in items))

def _remove(path: str, budget: float) -> None:
    end = time.time() + max(0.5, budget)
    while True:
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path) or time.time() > end:
            return
        time.sleep(0.3)

def lifecycle_check(project_dir, requirement_text: str = "", *, scenario_names: dict[str, list[str]] | None = None,
                    inherited_backend: str | Path | None = None, time_limit: float = 150.0, log=print,
                    node_modules: str | None = None, strict: bool = False,
                    name_kinds: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    t0 = time.time()
    deadline = t0 + time_limit
    st = _State(inherited_backend is not None)
    st.strict = bool(strict)
    st.kinds = {str(n).strip(): str(k) for n, k in (name_kinds or {}).items() if str(n).strip() and k}
    wd = None

    def report() -> dict:
        done = all(v in ("ok", "failed", "skipped") for v in st.paths.values())
        return {"completed": done, "paths": dict(st.paths), "missing": st.missing,
                "missing_secondary": st.missing_secondary, "seconds": round(time.time() - t0, 1), "notes": st.notes,
                "misplaced": dict(st.misplaced), "strict": st.strict,
                **({"duplicates": dict(st.duplicates), "duplicate_counts": dict(st.duplicate_counts)} if st.kinds else {})}

    try:
        requirement = requirement_text or ""
        scen = scenario_names if scenario_names is not None else scenario_seed_names(requirement)
        scen = {str(s): [n.strip() for n in names if isinstance(n, str) and n.strip()]
                for s, names in (scen or {}).items() if names}
        project = Path(project_dir).resolve()
        b = _backend_dir(str(project))
        if not b:
            st.note(log, "skipped: no backend/src/database")
            return [], report()
        backend = Path(b).resolve()
        nm = node_modules or str(backend / "node_modules")
        if not (os.path.isdir(os.path.join(nm, "sqlite3")) or os.path.isdir(os.path.join(nm, "better-sqlite3"))):
            st.note(log, "skipped: node_modules has neither sqlite3 nor better-sqlite3")
            return [], report()
        node = shutil.which("node")
        if not node:
            st.note(log, "skipped: no node on PATH")
            return [], report()
        inherited = None
        if inherited_backend is not None:
            ib = _backend_dir(str(Path(inherited_backend).resolve()))
            if ib:
                inherited = Path(ib).resolve()
            else:
                st.note(log, f"inherited backend {inherited_backend} has no src/database; upgrade not checked")
        log(f"[lifecycle] {len(scen)} scenario(s) with pre-provisioned names, budget {time_limit:.0f}s")
        wd = tempfile.mkdtemp(prefix="arc-lifecycle-")
        ctx = _Ctx(project, backend, nm, node, deadline, log, wd)
        try:
            _lifecycle(ctx, st, scen, requirement, inherited)
        except _Stop as why:
            st.note(log, f"not completed: {why}")
        except _Deadline:
            st.note(log, "not completed: the deadline passed during a comparison")
        findings = st.findings()
        log(f"[lifecycle] {len(findings)} finding(s), paths {st.paths}, {ctx.starts} start(s), "
            f"{time.time() - t0:.1f}s")
        return findings, report()
    except Exception as exc:
        try:
            st.note(log, f"check failed: {exc!r}")
        except Exception:
            pass
        try:
            return st.findings(), report()
        except Exception:
            return [], {"completed": False, "paths": {}, "missing": {}, "missing_secondary": {}, "seconds": 0.0,
                        "notes": [repr(exc)]}
    finally:
        if wd:
            _remove(wd, deadline - time.time())
