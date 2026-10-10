from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import code_checks as cc

CASES = []

SCHEMA = """const SCHEMA = `
CREATE TABLE IF NOT EXISTS file_blobs (
  commit_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  content TEXT,
  PRIMARY KEY (commit_id, path)
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  "email" TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(title, body);
CREATE VIEW IF NOT EXISTS user_names AS SELECT username FROM users;
`;
module.exports = { SCHEMA };
"""

def case(function):
    CASES.append(function)
    return function

def sql(body: str, extra: list[tuple[str, str]] | None = None) -> list[dict[str, str]]:
    return cc.sql_column_findings([("backend/src/db/schema.js", SCHEMA), ("backend/src/routes/x.js", body)] + (extra or []))

def cols(found: list[dict[str, str]]) -> list[str]:
    import re
    return [c for f in found for c in re.split(r" (?:is|are) used by", f["problem"])[0].split(", ")]

def unawaited(files: dict[str, str]) -> list[dict[str, str]]:
    return cc.unawaited_async_findings(list(files.items()))

def login(body: str, requirement: str = "", extra: dict[str, str] | None = None) -> list[dict[str, str]]:
    files = [("backend/src/db/schema.js", SCHEMA), ("backend/src/routes/auth.js", body)] + list((extra or {}).items())
    return cc.login_session_findings(files, requirement)

@case
def c01_select_of_an_undeclared_column_is_reported():
    body = "async function f(tx, id, p) {\n  const have = await tx.get('SELECT id FROM file_blobs WHERE commit_id = ? AND path = ?', [id, p]);\n}\n"
    found = sql(body)
    assert cols(found) == ["file_blobs.id"], found
    assert found[0]["where"] == "sql_column: backend/src/routes/x.js:2", found[0]["where"]
    assert "commit_id, content, path" in found[0]["problem"] and found[0]["fix"], found[0]
    assert not sql("get('SELECT path, content FROM file_blobs WHERE commit_id = ? ORDER BY path', [1]);")

@case
def c02_insert_update_delete_where_and_order_by():
    assert cols(sql("run('INSERT INTO users (username, nickname) VALUES (?, ?)', [a, b]);")) == ["users.nickname"]
    assert cols(sql("run('INSERT OR IGNORE INTO users (username) VALUES (?)', [a]);")) == []
    assert cols(sql("run('UPDATE users SET username = ?, bio = ? WHERE id = ?', [a, b, c]);")) == ["users.bio"]
    assert cols(sql("run('UPDATE users SET username = ? WHERE uid = ?', [a, c]);")) == ["users.uid"]
    assert cols(sql("run('DELETE FROM sessions WHERE user_id = ?', [a]);")) == ["sessions.user_id"]
    assert cols(sql("all('SELECT username FROM users ORDER BY created_at DESC');")) == ["users.created_at"]
    assert cols(sql("all('SELECT username FROM users WHERE username LIKE ? AND email IS NOT NULL');")) == []
    two = sql("get('SELECT name, display_name FROM users WHERE id = ?', [1]);")
    assert len(two) == 1 and cols(two) == ["users.name", "users.display_name"], two
    multi = sql("const s = `\n  SELECT username\n  FROM users\n  WHERE name = ?`;\nget(s, [1]);\n")
    assert cols(multi) == ["users.name"] and multi[0]["where"].endswith("x.js:2"), multi

@case
def c03_table_alias():
    assert cols(sql("all('SELECT fb.path, fb.nope FROM file_blobs fb WHERE fb.commit_id = ?', [1]);")) == ["file_blobs.nope"]
    assert cols(sql("all('SELECT fb.path FROM file_blobs AS fb WHERE fb.commit_id = ?', [1]);")) == []
    assert cols(sql("all('SELECT u.nope FROM file_blobs fb WHERE fb.commit_id = ?', [1]);")) == []

@case
def c04_negatives_out_of_scope_statements():
    for statement in [
        "SELECT id FROM file_blobs JOIN users ON users.id = file_blobs.commit_id",
        "SELECT path FROM file_blobs WHERE commit_id IN (SELECT id FROM users)",
        "WITH x AS (SELECT 1) SELECT id FROM file_blobs",
        "SELECT id FROM file_blobs UNION SELECT id FROM users",
        "SELECT id FROM file_blobs, users WHERE path = ?",
        "SELECT nope FROM elsewhere WHERE x = ?",
        "SELECT title, rank FROM docs WHERE docs MATCH ?",
        "SELECT username, created FROM user_names",
        "SELECT rowid, oid, _rowid_ FROM file_blobs",
        "SELECT COUNT(*) AS n FROM file_blobs WHERE n > 0 ORDER BY n",
        "SELECT path AS p FROM file_blobs ORDER BY p",
        "SELECT path FROM file_blobs WHERE path = \\\"open\\\"",
        "SELECT lower(nope) AS x, path || nope FROM file_blobs",
        "SELECT path FROM json_each(?)",
    ]:
        assert not sql(f"all('{statement}');"), statement
    assert not sql("// get('SELECT id FROM file_blobs WHERE path = ?')\n/* run('UPDATE users SET bio = ?') */\n")

@case
def c05_template_interpolations():
    assert cols(sql("get(`SELECT nope FROM file_blobs WHERE commit_id = ${id} LIMIT ${n}`);")) == ["file_blobs.nope"]
    assert cols(sql("run(`INSERT INTO users (username, bio) VALUES ${marks}`, vals);")) == ["users.bio"]
    assert cols(sql("get(`SELECT path FROM file_blobs WHERE commit_id IN (${ids.map(() => '?').join(',')})`);")) == []
    assert not sql("get(`SELECT ${columns} FROM file_blobs`);")
    assert not sql("get(`SELECT nope FROM file_blobs ${join} WHERE x = 1`);")
    assert not sql("get(`SELECT nope FROM file_blobs WHERE ${where}`);")
    assert not sql("get(`SELECT nope FROM ${table}`);")
    assert not sql("get(`SELECT path FROM file_blobs ORDER BY ${sort}`);")

@case
def c06_statements_built_at_run_time():
    assert not sql("get('SELECT nope FROM file_blobs ' + where, []);")
    assert not sql("get(where + 'SELECT nope FROM file_blobs');")
    assert not sql("let q = 'SELECT nope FROM file_blobs';\nq += ' JOIN users ON 1';\nget(q);")
    assert not sql("const base = 'SELECT nope FROM file_blobs';\nget(base + joins);")
    assert not sql("const base = 'SELECT nope FROM file_blobs';\nget(`${base} JOIN users ON 1`);")
    assert not sql("get(['SELECT nope FROM file_blobs', joins].join(' '));")
    assert not sql("get('SELECT nope FROM file_blobs'.replace('file_blobs', t));")
    assert cols(sql("const q = 'SELECT nope FROM file_blobs';\nget(q);")) == ["file_blobs.nope"]

@case
def c07_schema_sources():
    alter = ("backend/src/db/migrate.js", "run('ALTER TABLE users ADD COLUMN bio TEXT');")
    assert not sql("run('UPDATE users SET bio = ?');", [alter])
    dyn_col = ("backend/src/db/migrate.js", "run(`ALTER TABLE users ADD COLUMN ${c} TEXT`);")
    assert not sql("run('UPDATE users SET bio = ?');", [dyn_col])
    assert cols(sql("all('SELECT nope FROM file_blobs');", [dyn_col])) == ["file_blobs.nope"]
    dyn_table = ("backend/src/db/migrate.js", "run(`ALTER TABLE ${t} ADD COLUMN bio TEXT`);")
    assert not sql("run('UPDATE users SET bio = ?');", [dyn_table])
    both = ("backend/src/db/migrate.js", "const extra = [['users', 'bio TEXT']];\n"
            "for (const [t, c] of extra) run(`ALTER TABLE ${t} ADD COLUMN ${c}`);")
    assert not sql("run('UPDATE users SET bio = ?');", [both])
    assert cols(sql("run('UPDATE users SET mood = ?');", [both])) == ["users.mood"]
    twice = ("backend/src/legacy.js", "exec('CREATE TABLE IF NOT EXISTS users (id INTEGER, bio TEXT)');")
    assert not sql("run('UPDATE users SET bio = ?');", [twice])
    sqlfile = ("backend/schema.sql", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT); -- notes\n")
    assert cols(sql("all('SELECT body, title FROM notes');", [sqlfile])) == ["notes.title"]
    dyn_create = ("backend/src/db/make.js", "run(`CREATE TABLE ${name} (${defs})`);")
    assert not sql("all('SELECT nope FROM file_blobs');", [dyn_create])
    concat_create = ("backend/src/db/make.js", "run('CREATE TABLE extra (' + defs + ')');")
    assert not sql("all('SELECT nope FROM extra');", [concat_create])

PERMS = ("async function isAdmin(repo, userId) {\n  return Boolean(await get('SELECT 1', [userId]));\n}\n"
         "module.exports = { isAdmin };\n")

def route(body: str) -> dict[str, str]:
    return {"backend/src/perms.js": PERMS,
            "backend/src/routes/r.js": "const { isAdmin } = require('../perms');\n" + body}

@case
def c08_condition_uses_are_reported():
    for body in [
        "async function h(repo, u) {\n  if (!isAdmin(repo, u)) return deny();\n}",
        "async function h(repo, u) {\n  if (isAdmin(repo, u)) allow();\n}",
        "async function h(repo, u, a) {\n  if (a && isAdmin(repo, u)) allow();\n}",
        "async function h(repo, u, a) {\n  while (isAdmin(repo, u) || a) step();\n}",
        "async function h(repo, u) {\n  const label = isAdmin(repo, u) ? 'admin' : 'user';\n}",
        "async function h(repo, u, a) {\n  const label = (a && !isAdmin(repo, u)) ? 'guest' : 'user';\n}",
        "async function h(repo, u, a) {\n  do { step(); } while (!isAdmin(repo, u));\n}",
        "async function h(repo, u, own) {\n  if (!own && !isAdmin(repo, u) && !(await other())) {\n    deny();\n  }\n}",
    ]:
        found = unawaited(route(body))
        assert len(found) == 1 and found[0]["where"] == "unawaited_async: backend/src/routes/r.js:3", (body, found)
        assert "always truthy" in found[0]["problem"], found

@case
def c09_no_inference_outside_a_condition():
    for body in [
        "async function h(repo, u, own) {\n  const privileged = (await other()) || isAdmin(repo, u);\n"
        "  if (!own && !privileged) return deny();\n}",
        "function action(enabled) {\n  const pending = enabled && isAdmin(1, 2);\n  if (pending) pending.catch(console.error);\n}",
        "async function h(repo, u) {\n  const p = isAdmin(repo, u);\n  if (p) allow();\n}",
        "async function h(repo, u, ok) {\n  return isAdmin(repo, u) && ok;\n}",
        "async function h(repo, u) {\n  const denied = !isAdmin(repo, u);\n}",
        "async function h(repo, u) {\n  if (check(!isAdmin(repo, u))) deny();\n}",
        "async function h(list, u) {\n  if (list.some((r) => isAdmin(r, u))) allow();\n}",
    ]:
        assert not unawaited(route(body)), body
    awaited = "async function h(repo, u) {\n  const p = isAdmin(repo, u);\n  log(1);\n  if (await p) allow();\n}"
    assert not unawaited(route(awaited))
    passed = "async function h(repo, u) {\n  const p = isAdmin(repo, u);\n  const ok = await p;\n  if (!ok) deny();\n}"
    assert not unawaited(route(passed))
    reassigned = "async function h(repo, u) {\n  let p = isAdmin(repo, u);\n  p = await p;\n  if (!p) deny();\n}"
    assert not unawaited(route(reassigned))
    other_scope = "async function h(repo, u) {\n  { const p = isAdmin(repo, u); use(p); }\n}\nfunction k(p) { if (p) x(); }"
    assert not unawaited(route(other_scope))

@case
def c10_definition_forms_and_re_exports():
    forms = {
        "const isAdmin = async (repo, u) => true;\nmodule.exports = { isAdmin };\n": "const { isAdmin } = require('../perms');",
        "module.exports = { isAdmin: async (repo, u) => true };\n": "const { isAdmin } = require('../perms');",
        "exports.isAdmin = async function (repo, u) { return true; };\n": "const { isAdmin } = require('../perms');",
        "async function isAdmin() { return true; }\nmodule.exports = { isAdmin };\n": "const perms = require('../perms');",
    }
    for definition, importer in forms.items():
        call = "perms.isAdmin(repo, u)" if "perms =" in importer else "isAdmin(repo, u)"
        files = {"backend/src/perms.js": definition,
                 "backend/src/routes/r.js": importer + f"\nasync function h(repo, u) {{\n  if (!{call}) deny();\n}}"}
        found = unawaited(files)
        assert len(found) == 1 and found[0]["where"].endswith("r.js:3"), (definition, found)
    same_file = {"backend/src/routes/r.js": "async function can(u) { return true; }\nasync function h(u) { if (!can(u)) deny(); }"}
    assert len(unawaited(same_file)) == 1
    unbound = {"backend/src/perms.js": PERMS, "backend/src/routes/r.js": "const x = require('../x');\nif (!isAdmin(1)) deny();"}
    assert not unawaited(unbound), "a name the file never imports is not the backend's function"

@case
def c11_negatives():
    for body in [
        "async function h(repo, u) {\n  if (!(await isAdmin(repo, u))) return deny();\n}",
        "async function h(repo, u) {\n  if (await isAdmin(repo, u)) allow();\n}",
        "function h(repo, u) {\n  return isAdmin(repo, u).then((ok) => ok && allow());\n}",
        "function h(repo, u) {\n  return isAdmin(repo, u);\n}",
        "async function h(repo, u) {\n  const p = isAdmin(repo, u);\n  return p;\n}",
        "async function h(repo, u) {\n  const ok = await isAdmin(repo, u) && other();\n}",
        "async function h(repo, u) {\n  const msg = 'if (!isAdmin(repo, u)) deny()';\n  // if (!isAdmin(repo, u)) deny();\n}",
        "async function h(repo, u) {\n  results.push(isAdmin(repo, u));\n  await Promise.all(results);\n}",
        "async function h(repo, u) {\n  if (isAdmin(repo, u) === true) allow();\n}",
        "const o = {\n  isAdmin(repo, u) {\n    return true;\n  },\n};",
    ]:
        assert not unawaited(route(body)), body
    sync_too = route("async function h(repo, u) {\n  if (!isAdmin(repo, u)) deny();\n}")
    sync_too["backend/src/other.js"] = "function isAdmin(a) { return a === 1; }\nmodule.exports = { isAdmin };"
    assert not unawaited(sync_too), "a name also defined non-async is skipped"
    param = route("async function h(repo, u) {\n  if (!isAdmin(repo, u)) deny();\n}")
    param["backend/src/wrap.js"] = "function wrap(isAdmin) { return (r) => isAdmin(r); }"
    assert not unawaited(param), "a parameter of the same name is skipped"
    pkg = {"backend/src/perms.js": PERMS,
           "backend/src/routes/r.js": "const { isAdmin } = require('some-package');\nif (!isAdmin(1)) deny();"}
    assert not unawaited(pkg), "a package's function of the same name is not the backend's"

REFUSING = """const express = require('express');
const router = express.Router();
router.post('/login', async (req, res, next) => {
  try {
    const row = await get('SELECT * FROM users WHERE username = ?', [req.body.username]);
    if (!row) return res.status(401).json({ error: 'Invalid credentials' });
    const activeSession = await get('SELECT id FROM sessions WHERE account_id = ? AND active = 1', [row.id]);
    if (activeSession) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    await createSession(res, row.id);
    res.json({ ok: true });
  } catch (e) { next(e); }
});
module.exports = router;
"""

@case
def c12_sign_in_refused_because_a_session_exists():
    found = login(REFUSING)
    assert len(found) == 1 and found[0]["where"] == "login_session_refusal: backend/src/routes/auth.js:8", found
    assert "POST /login" in found[0]["problem"] and "several sessions" in found[0]["fix"], found
    counted = REFUSING.replace(
        "const activeSession = await get('SELECT id FROM sessions WHERE account_id = ? AND active = 1', [row.id]);\n"
        "    if (activeSession) {\n      return res.status(401).json({ error: 'Invalid credentials' });\n    }",
        "const { n } = await get('SELECT COUNT(*) AS n FROM sessions WHERE account_id = ?', [row.id]);\n"
        "    if (n > 0) throw httpError(409, 'Already signed in');").replace("'/login'", "'/api/auth/sign-in'")
    assert len(login(counted)) == 1, counted

@case
def c13_ending_other_sessions_on_sign_in():
    ending = REFUSING.replace(
        "const activeSession = await get('SELECT id FROM sessions WHERE account_id = ? AND active = 1', [row.id]);\n"
        "    if (activeSession) {\n      return res.status(401).json({ error: 'Invalid credentials' });\n    }",
        "await run('UPDATE sessions SET active = 0 WHERE account_id = ?', [row.id]);")
    found = login(ending)
    assert len(found) == 1 and "ends every other session" in found[0]["problem"], found
    assert not login(ending, "An account may hold only one session at a time; signing in ends its other sessions.")
    assert not login(ending, "Signing in from a new browser signs out of all other devices.")
    assert len(login(ending, "Signing out affects only the current browser session.")) == 1
    deleting = REFUSING.replace(
        "const activeSession = await get('SELECT id FROM sessions WHERE account_id = ? AND active = 1', [row.id]);\n"
        "    if (activeSession) {\n      return res.status(401).json({ error: 'Invalid credentials' });\n    }", "")
    helper = {"backend/src/session.js": "async function createSession(res, accountId) {\n"
              "  await run('DELETE FROM sessions WHERE account_id = ? AND id != ?', [accountId, keep]);\n"
              "  await run('INSERT INTO sessions (id, account_id) VALUES (?, ?)', [id, accountId]);\n}\n"
              "module.exports = { createSession };\n"}
    found = login(deleting, "", helper)
    assert len(found) == 1 and found[0]["where"] == "login_session_refusal: backend/src/session.js:2", found
    assert "called from that route" in found[0]["problem"], found

@case
def c14_negatives():
    base = "router.post('/login', async (req, res) => {\n  const user = await check(req);\n  %s\n  res.json({});\n});\n"
    for body in [
        "const s = await get('SELECT * FROM sessions WHERE id = ? AND active = 1', [req.cookies.sid]);\n  if (s) return res.status(400).json({});",
        "const s = await get('SELECT * FROM sessions WHERE account_id = ?', [user.id]);\n  if (!s) return res.status(500).json({});",
        "const s = await get('SELECT * FROM sessions WHERE account_id = ?', [user.id]);\n  if (s) { reuse(s); }",
        "const s = await get('SELECT * FROM sessions WHERE account_id = ?', [user.id]);\n  if (s || x) return res.status(409).end();",
        "await run('DELETE FROM sessions WHERE account_id = ? AND expires_at < ?', [user.id, now]);",
        "await run('UPDATE sessions SET last_seen = ? WHERE account_id = ?', [now, user.id]);",
        "await run('UPDATE sessions SET active = 0 WHERE id = ?', [req.cookies.sid]);",
    ]:
        assert not login(base % body), body
    logout = "router.post('/logout-everywhere', async (req, res) => {\n  await run('DELETE FROM sessions WHERE account_id = ?', [u]);\n});"
    assert not login(logout)
    other = "router.post('/repos', async (req, res) => {\n  const s = await get('SELECT * FROM sessions WHERE account_id = ?', [u]);\n  if (s) return res.status(409).end();\n});"
    assert not login(other)

@case
def c15_entry_point_never_raises_and_skips_test_folders():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert cc.code_check_findings(root, "", log=lambda *_: None) == []
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "backend" / "src" / "db.js").write_text(SCHEMA, encoding="utf-8")
        (root / "backend" / "src" / "routes" / "auth.js").write_text(REFUSING, encoding="utf-8")
        lines = "\n".join(f"get('SELECT nope{k} FROM file_blobs');" for k in range(12))
        (root / "backend" / "src" / "routes" / "many.js").write_text(lines, encoding="utf-8")
        for skipped in ("node_modules/pkg", "test-e2e", "tests", "e2e-support"):
            (root / "backend" / skipped).mkdir(parents=True, exist_ok=True)
            (root / "backend" / skipped / "x.js").write_text("get('SELECT ghost FROM file_blobs');", encoding="utf-8")
        (root / "backend" / "src" / "a.spec.js").write_text("get('SELECT ghost FROM file_blobs');", encoding="utf-8")
        (root / "backend" / "src" / "bad.js").write_bytes(b"\xff\xfe`${{{{ unterminated '")
        notes: list[str] = []
        found = cc.code_check_findings(root, "", log=notes.append)
        kinds = [f["where"].split(":")[0] for f in found]
        assert kinds.count("sql_column") == 8 and kinds.count("login_session_refusal") == 1, kinds
        assert not any("ghost" in f["problem"] for f in found), found
        assert all(set(f) == {"where", "requirement", "problem", "fix"} for f in found)
        assert notes and notes[0].startswith("code checks:"), notes
    saved = cc.sql_column_findings
    try:
        cc.CHECKS[0] = ("sql_column", lambda *a: 1 / 0)
        diags: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            assert cc.code_check_findings(Path(tmp), "", log=lambda *_: None, diagnostics=diags) == []
        assert any("crashed" in d for d in diags), diags
    finally:
        cc.CHECKS[0] = ("sql_column", lambda src, req, diag: saved(src, diag))

@case
def c16_where_operands_next_to_other_sql_words_are_not_columns():
    for where in [
        "username COLLATE NOCASE = ?",
        "CASE WHEN nope = 1 THEN 1 ELSE 0 END = 1",
        "CAST(nope AS TEXT) = ?",
        "username || nope = ?",
        "username + nope > 1",
        "id BETWEEN ? AND nope",
        "username LIKE ? ESCAPE '!' AND id = ?",
        "lower(nope) = ?",
        "coalesce(nope, 1) = ?",
        "id IN (?, ?) AND username IS NOT NULL",
    ]:
        assert not sql(f"all('SELECT username FROM users WHERE {where}');"), where
    assert cols(sql("all('SELECT username FROM users WHERE username COLLATE NOCASE = ? AND nope = ?');")) == ["users.nope"]
    assert cols(sql("all('SELECT username FROM users WHERE NOT nope = 1');")) == ["users.nope"]
    assert cols(sql("all('SELECT username FROM users WHERE (nope = 1 OR id = 2)');")) == ["users.nope"]
    assert cols(sql("all('SELECT username FROM users WHERE id BETWEEN ? AND ? AND nope = 1');")) == ["users.nope"]
    assert cols(sql("all('SELECT username FROM users WHERE nope NOT IN (?, ?)');")) == ["users.nope"]

@case
def c17_session_rules_of_the_requirement_and_provable_invalidation():
    for single in ["An account may hold only one active session.",
                   "Sign-in is refused when the account already has an active session.",
                   "Accounts are limited to one session at a time.",
                   "Signing in on a new device signs out the other devices.",
                   "No account may hold more than one session."]:
        assert cc.session_policy(single)[0] == "single", single
        assert not login(REFUSING, single), single
    ending = REFUSING.replace(
        "const activeSession = await get('SELECT id FROM sessions WHERE account_id = ? AND active = 1', [row.id]);\n"
        "    if (activeSession) {\n      return res.status(401).json({ error: 'Invalid credentials' });\n    }",
        "await run('DELETE FROM sessions WHERE account_id = ?', [row.id]);")
    for supports in ["Signing in must not end other sessions of the account.",
                     "Signing in from another browser never signs out the existing sessions.",
                     "An account can be signed in from several browsers.", ""]:
        assert cc.session_policy(supports)[0] == "plain", supports
        assert len(login(ending, supports)) == 1, supports
    unsure = "The security page has a button to sign out all other devices."
    assert cc.session_policy(unsure)[0] == "unsure_ending"
    assert not login(ending, unsure) and len(login(REFUSING, unsure)) == 1

    def update(set_clause: str) -> list[dict[str, str]]:
        return login(ending.replace("'DELETE FROM sessions WHERE account_id = ?'",
                                    f"\"UPDATE sessions SET {set_clause} WHERE account_id = ?\""))
    for provable in ["active = 0", "active = false", "revoked = 1", "revoked_at = CURRENT_TIMESTAMP",
                     "expires_at = datetime('now')", "expires_at = datetime('now', '-1 second')", "status = 'revoked'",
                     "active = 0, revoked_at = ?"]:
        assert len(update(provable)) == 1, provable
    for renewal in ["expires_at = ?", "expires_at = datetime('now', '+30 days')", "active = ?", "status = ?",
                    "active = 1", "last_seen = datetime('now')", "status = 'active'"]:
        assert not update(renewal), renewal

@case
def c18_import_aliases_resolve_to_the_definition():
    for importer in ["import { isAdmin as allowed } from '../perms';",
                     "const { isAdmin: allowed } = require('../perms');",
                     "const { other, isAdmin: allowed } = require('../perms');"]:
        files = {"backend/src/perms.js": PERMS,
                 "backend/src/routes/r.js": importer + "\nasync function h(repo, u) {\n  if (!allowed(repo, u)) deny();\n}"}
        found = unawaited(files)
        assert len(found) == 1 and found[0]["where"].endswith("r.js:3") and "`isAdmin`, imported as `allowed`" in found[0]["problem"], (importer, found)
    shadowed = {"backend/src/perms.js": PERMS,
                "backend/src/routes/r.js": "const { isAdmin: allowed } = require('../perms');\n"
                                           "async function h(repo, u) {\n  if (!allowed(repo, u)) deny();\n}",
                "backend/src/other.js": "function allowed(x) { return x > 1; }"}
    assert not unawaited(shadowed), "an alias name that is also a sync function somewhere is skipped"
    unknown = {"backend/src/perms.js": PERMS,
               "backend/src/routes/r.js": "const { missing: allowed } = require('../perms');\nif (!allowed(1)) deny();"}
    assert not unawaited(unknown)

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
