from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import role_ladder as rl
from agent import session_probe as sp

SERIAL = True
NL = chr(10)
CASES = []
NODE_MODULES = os.environ.get("ARC_TX_NODE_MODULES", "local-data/try/sheet-v141/template/backend/node_modules")

def case(function):
    CASES.append(function)
    return function

def have_node() -> bool:
    if not shutil.which("node") or not os.path.isdir(os.path.join(NODE_MODULES, "express")):
        print(f"note  node or {NODE_MODULES}/express missing: node fixture skipped (set ARC_TX_NODE_MODULES)")
        return False
    return True

def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

GATE = "Read, Triage, Write, Maintain, and Admin follow the rules below; these roles are not an automatic cumulative ladder."
ONLY_ASSIGN = "Only a signed-in user with Triage, Maintain, or Admin may assign or unassign members in the Assignees area."
ALL_ASSIGN = "Only a signed-in user with Triage, Write, Maintain, or Admin may assign or unassign members in the Assignees area."
WRITE_LIST = "Only Write, Maintain, or Admin may create issues."

def requirement(*sentences: str) -> str:
    return "description: '" + " ".join(sentences) + "'" + NL

APP = NL.join([
    "const express = require('express');",
    "const app = express();",
    "app.use('/api/repos', require('./routes/assignees'));",
    "module.exports = app;",
])
PERMS = NL.join([
    "const RANK = { read: 1, triage: 2, write: 3, maintain: 4, admin: 5 };",
    "async function effectiveRole(repo, userId) {",
    "  let best = null;",
    "  for (const role of repo.roles) {",
    "    if (!best || (RANK[role] || 0) > (RANK[best] || 0)) best = role;",
    "  }",
    "  return best;",
    "}",
    "async function roleAtLeast(repo, userId, minimum) {",
    "  const role = await effectiveRole(repo, userId);",
    "  return (RANK[role] || 0) >= (RANK[minimum] || 0);",
    "}",
    "module.exports = { roleAtLeast, effectiveRole, RANK };",
])
ASSIGNEES = NL.join([
    "const express = require('express');",
    "const { roleAtLeast } = require('../perms');",
    "const router = express.Router();",
    "async function postHandler(req, res) {",
    "  if (!(await roleAtLeast(req.repo, req.user.id, 'triage'))) {",
    "    return res.status(403).json({ error: 'Triage required' });",
    "  }",
    "  res.json({ ok: true });",
    "}",
    "router.post('/:owner/:repo/issues/:number/assignees', postHandler);",
    "router.delete('/:owner/:repo/issues/:number/assignees', async (req, res) => {",
    "  if (!(await roleAtLeast(req.repo, req.user.id, 'triage'))) return res.status(403).end();",
    "  res.json({ ok: true });",
    "});",
    "module.exports = router;",
])

def ladder_project(root: Path, assignees: str = ASSIGNEES, perms: str = PERMS, extra: dict | None = None) -> Path:
    files = {"backend/src/app.js": APP, "backend/src/perms.js": perms, "backend/src/routes/assignees.js": assignees,
             "backend/node_modules/x/index.js": "const RANK = { read: 1, triage: 2, write: 3 }; if (RANK.a >= RANK.triage) {}"}
    files.update(extra or {})
    return write(root, files)

@case
def ladder_1_rank_threshold_on_assignees_is_reported_and_an_upward_closed_list_is_not():
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp))
        found = rl.role_ladder_findings(requirement(GATE, ONLY_ASSIGN, WRITE_LIST), project)
        assert len(found) == 1, found
        f = found[0]
        assert f["where"].startswith("role_ladder: backend/src/routes/assignees.js:5"), f
        assert "Write" in f["problem"] and "Triage, Maintain, Admin" in f["problem"], f
        assert "assignees.js:12" in f["problem"] and "perms.js:1" in f["problem"], f
        assert f["requirement"].startswith("Only a signed-in user with Triage"), f
        assert set(f) == {"where", "requirement", "problem", "fix"}
        assert rl.role_ladder_findings(requirement(GATE, ALL_ASSIGN, WRITE_LIST), project) == []

@case
def ladder_2_the_best_role_loop_alone_is_no_threshold():
    perms = NL.join(PERMS.split(NL)[:8] + ["function canTriage(role) { return ['triage', 'maintain', 'admin'].includes(role); }",
                                           "module.exports = { effectiveRole, canTriage, RANK };"])
    assignees = ASSIGNEES.replace("roleAtLeast", "canTriage").replace("(req.repo, req.user.id, 'triage')", "(req.role)")
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=assignees, perms=perms)
        assert rl.role_ladder_findings(requirement(GATE, ONLY_ASSIGN, WRITE_LIST), project) == []

@case
def ladder_3_an_unlinked_rank_threshold_is_only_a_diagnostic():
    helper = NL.join([
        "const { RANK } = require('./perms');",
        "function assignable(grants) {",
        "  return grants.filter((g) => (RANK[g.role] || 0) >= RANK.triage);",
        "}",
        "module.exports = { assignable };",
    ])
    assignees = NL.join(["const express = require('express');", "const router = express.Router();",
                         "module.exports = router;"])
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=assignees, extra={"backend/src/assignable.js": helper})
        logs: list[str] = []
        assert rl.role_ladder_findings(requirement(GATE, ONLY_ASSIGN, WRITE_LIST), project, log=logs.append) == []
        assert any("assignable.js:3 not linked" in line for line in logs), logs
        assert rl.role_ladder_findings(requirement(ONLY_ASSIGN, WRITE_LIST), project) == []

LABELS_DENY = NL.join([
    "const express = require('express');",
    "const router = express.Router();",
    "const ROLE_RANK = { Read: 1, Triage: 2, Write: 3, Maintain: 4, Admin: 5 };",
    "router.post('/:owner/:repo/issues/:number/labels', (req, res) => {",
    "  if (!req.role || ROLE_RANK[req.role] < ROLE_RANK.Triage) {",
    "    return res.status(403).json({ error: 'no' });",
    "  }",
    "  res.json({ ok: true });",
    "});",
    "module.exports = router;",
])
LABELS_LIST = "Only users with Triage, Maintain, or Admin may apply or remove labels."

@case
def ladder_4_a_deny_comparison_counts_and_other_uses_of_less_than_do_not():
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=LABELS_DENY)
        found = rl.role_ladder_findings(requirement(LABELS_LIST, WRITE_LIST), project)
        assert len(found) == 1 and "Write" in found[0]["problem"], found
        assert found[0]["where"] == "role_ladder: backend/src/routes/assignees.js:5", found

        assert rl.role_ladder_findings(requirement("Triage, Maintain, or Admin may apply or remove labels.", WRITE_LIST),
                                        project) == []
    plain = LABELS_DENY.replace("return res.status(403).json({ error: 'no' });", "req.limited = true;")
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=plain)
        assert rl.role_ladder_findings(requirement(LABELS_LIST, WRITE_LIST), project) == []

@case
def ladder_5_array_membership_is_no_rank_but_an_array_order_is():
    member = NL.join([
        "const express = require('express');",
        "const router = express.Router();",
        "const TRIAGE_ROLES = ['triage', 'write', 'maintain', 'admin'];",
        "router.post('/:repo/labels', (req, res) => {",
        "  if (TRIAGE_ROLES.indexOf(req.role) < 0) return res.status(403).end();",
        "  res.json({ ok: true });",
        "});",
        "module.exports = router;",
    ])
    order = member.replace("const TRIAGE_ROLES = ['triage', 'write', 'maintain', 'admin'];",
                           "const ORDER = ['read', 'triage', 'write', 'maintain', 'admin'];").replace(
        "TRIAGE_ROLES.indexOf(req.role) < 0", "ORDER.indexOf(req.role) < ORDER.indexOf('triage')")
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=member)
        assert rl.role_ladder_findings(requirement(LABELS_LIST, WRITE_LIST), project) == []
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=order)
        found = rl.role_ladder_findings(requirement(LABELS_LIST, WRITE_LIST), project)
        assert len(found) == 1 and "Write" in found[0]["problem"], found

@case
def ladder_6_cannot_clauses_do_not_link_and_nothing_raises():
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp))

        for sentence in ("Write can create, edit, and comment but cannot assign members or set Assignees.",
                         "Only Triage can comment but cannot assign members or set Assignees.",
                         "Only Maintain or Admin may assign members in the Assignees area."):
            assert rl.role_ladder_findings(requirement(GATE, sentence), project) == [], sentence
        logs: list[str] = []
        assert rl.role_ladder_findings(requirement(GATE, ONLY_ASSIGN), Path(tmp) / "missing", log=logs.append) == []
        assert rl.role_ladder_findings(None, project, log=logs.append) == []
        assert any("crashed" in line for line in logs), logs

def guarded(guard_line: str = "", condition: str = "ROLE_RANK[req.role] < ROLE_RANK.Triage", middleware: str = "",
            method: str = "post") -> str:
    return NL.join([
        "const express = require('express');",
        "const router = express.Router();",
        "const ROLE_RANK = { Read: 1, Triage: 2, Write: 3, Maintain: 4, Admin: 5 };",
        "router." + method + "('/:owner/:repo/issues/:number/labels', " + middleware + "(req, res) => {",
        guard_line,
        "  if (" + condition + ") {",
        "    return res.status(403).json({ error: 'no' });",
        "  }",
        "  res.json({ ok: true });",
        "});",
        "module.exports = router;",
    ])

@case
def ladder_7_the_method_must_match_the_action_and_the_rank_must_be_the_only_guard():
    req = requirement(GATE, LABELS_LIST, WRITE_LIST)
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=guarded())
        assert len(rl.role_ladder_findings(req, project)) == 1
    variants = {
        "GET for a change list": guarded(method="get"),
        "early 403 on a role": guarded(guard_line="  if (req.role === 'Write') return res.status(403).end();"),
        "includes list": guarded(guard_line="  if (!['Triage', 'Maintain', 'Admin'].includes(req.role)) return res.status(403).end();"),
        "compound condition": guarded(condition="!req.isAuthor && ROLE_RANK[req.role] < ROLE_RANK.Triage"),
        "role middleware": guarded(middleware="requireRole('Triage'), "),
        "helper guard after": guarded(guard_line="").replace("  res.json({ ok: true });",
                                                             "  if (!canLabel(req)) return res.status(400).end();" + NL + "  res.json({ ok: true });"),
    }
    for label, source in variants.items():
        with tempfile.TemporaryDirectory() as tmp:
            project = ladder_project(Path(tmp), assignees=source)
            logs: list[str] = []
            assert rl.role_ladder_findings(req, project, log=logs.append) == [], label
            assert any("not linked" in line or "not reported" in line for line in logs), (label, logs)

    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=guarded(middleware="requireAuth, ",
                                                              condition="!req.role || ROLE_RANK[req.role] < ROLE_RANK.Triage"))
        assert len(rl.role_ladder_findings(req, project)) == 1

@case
def ladder_8_a_view_route_is_not_held_to_the_delete_list():
    reports = NL.join([
        "const express = require('express');",
        "const router = express.Router();",
        "const ROLE_RANK = { Read: 1, Triage: 2, Write: 3, Maintain: 4, Admin: 5 };",
        "router.get('/:repo/reports', (req, res) => {",
        "  if (ROLE_RANK[req.role] < ROLE_RANK.Write) return res.status(403).end();",
        "  res.json([]);",
        "});",
        "module.exports = router;",
    ])
    req = requirement(GATE, "Write, Maintain, or Admin may view the reports.", "Only Write may delete reports.",
                      "Triage may view issues.")
    with tempfile.TemporaryDirectory() as tmp:
        project = ladder_project(Path(tmp), assignees=reports)
        assert rl.role_ladder_findings(req, project) == []

SEED = "Seed values: account `alice`, email `alice@example.test`, password `Pw-123456!`."
SESSION_DEF = "A session is the current-browser login record saved after successful sign-in."
INDEX = NL.join([
    "const app = require('./app');",
    "app.listen(Number(process.env.PORT || 3000), () => console.log('listening'));",
])
SESSION_APP = NL.join([
    "const express = require('express');",
    "const app = express();",
    "app.use(express.json());",
    "const authRoutes = require('./routes/auth');",
    "app.use('/api', authRoutes);",
    "module.exports = app;",
])
AUTH = NL.join([
    "const express = require('express');",
    "const crypto = require('crypto');",
    "const router = express.Router();",
    "const MODE = '__MODE__';",
    "const sessions = new Map();",
    "let logins = 0;",
    "function cookieOf(req) {",
    "  const m = (req.headers.cookie || '').match(/sid=([^;]+)/);",
    "  return m ? m[1] : null;",
    "}",
    "router.post('/login', (req, res) => {",
    "  const { __KEY__, password } = req.body || {};",
    "  if ((__KEY__ !== 'alice' && __KEY__ !== 'alice@example.test') || password !== 'Pw-123456!') {",
    "    return res.status(401).json({ error: 'Invalid credentials' });",
    "  }",
    "  logins += 1;",
    "  const active = [...sessions.values()].some((who) => who === 'alice');",
    "  if (MODE === 'refuse' && active) return res.status(401).json({ error: 'Invalid credentials' });",
    "  if (MODE === 'rate' && logins > 1) return res.status(429).json({ error: 'Too many' });",
    "  if (MODE === 'lock' && logins > 1) return res.status(401).json({ error: 'Invalid credentials' });",
    "  if (MODE === 'once' && logins === 2) return res.status(403).json({ error: 'Slow down' });",
    "  if (MODE === 'kick') for (const [k, who] of sessions) if (who === 'alice') sessions.delete(k);",
    "  const id = crypto.randomUUID();",
    "  sessions.set(id, 'alice');",
    "  res.cookie('sid', id, { httpOnly: true, path: '/' });",
    "  res.json({ user: { username: 'alice' } });",
    "});",
    "__LOGOUT__",
    "router.get('/me', (req, res) => {",
    "  const id = cookieOf(req);",
    "  if (!id || !sessions.has(id)) return res.status(401).json({ user: null });",
    "  res.json({ user: { username: sessions.get(id) } });",
    "});",
    "module.exports = router;",
])
LOGOUT = NL.join([
    "router.post('/logout', (req, res) => {",
    "  sessions.delete(cookieOf(req));",
    "  res.clearCookie('sid', { path: '/' });",
    "  res.status(204).end();",
    "});",
])
FRONT = "export const signIn = (u: string, p: string) => client.post('/login', { __FKEY__: u, password: p });" + NL

def session_project(root: Path, mode: str, logout: bool = True, key: str = "username", fkey: str = "username") -> Path:
    auth = AUTH.replace("__MODE__", mode).replace("__KEY__", key).replace("__LOGOUT__", LOGOUT if logout else "")
    return write(root, {
        "backend/package.json": '{"name": "fx", "scripts": {"start": "node src/index.js"}}' + NL,
        "backend/src/index.js": INDEX, "backend/src/app.js": SESSION_APP, "backend/src/routes/auth.js": auth,
        "frontend/src/api/auth.ts": FRONT.replace("__FKEY__", fkey),
    })

def snapshot(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))

@case
def session_7_parsers_and_the_one_session_requirement():
    creds = sp.credentials(requirement("The seeded data is account `alice-dev`, email `alice.dev@example.test`, "
                                       "password `Valid-password-123!`.", "Seed values: account `bob`."))
    assert creds == [{"account": "alice-dev", "email": "alice.dev@example.test", "password": "Valid-password-123!"}], creds
    assert sp.credentials(requirement("Seed values: account `alice`, email `alice@example.test`.")) == []
    with tempfile.TemporaryDirectory() as tmp:
        project = session_project(Path(tmp), "refuse")
        logins, logouts = sp.backend_routes(project)
        assert [(e["path"], e["where"]) for e in logins] == [("/api/login", "backend/src/routes/auth.js:11")], logins
        assert logins[0]["keys"][:2] == ["username", "password"], logins
        assert [e["path"] for e in logouts] == ["/api/logout"], logouts
        assert sp.frontend_login_keys(project) == [("/login", ["username", "password"])]
        logs: list[str] = []
        req = requirement(SEED, "An account may hold only one active session; a second sign-in is refused.")
        assert sp.session_findings(req, project, log=logs.append, node_modules=NODE_MODULES) == []
        assert any("limits sessions" in line for line in logs), logs
        assert sp.session_findings(requirement(SESSION_DEF), project, node_modules=NODE_MODULES) == []

@case
def session_8_a_refused_second_browser_is_reported():
    if not have_node():
        return
    with tempfile.TemporaryDirectory() as tmp:
        project = session_project(Path(tmp), "refuse")
        before = snapshot(project)
        found = sp.session_findings(requirement(SEED, SESSION_DEF), project, time_limit=60, log=lambda m: None,
                                    node_modules=NODE_MODULES)
        assert len(found) == 1, found
        f = found[0]
        assert f["where"] == "session: backend/src/routes/auth.js:11", f
        assert "cannot sign in from a second browser" in f["problem"] and "answered 401" in f["problem"], f
        assert "signed out (POST /api/logout -> 204)" in f["problem"] and "got 200" in f["problem"], f
        assert "Pw-123456!" not in f["problem"], f
        assert f["requirement"] == SESSION_DEF, f
        assert snapshot(project) == before, "the probe wrote into the project dir"
    leftovers = [p for p in Path(tempfile.gettempdir()).glob("arc-session-probe-*")]
    assert not leftovers, leftovers

@case
def session_9_a_new_session_per_browser_a_rate_limit_and_a_lock_are_not_reported():
    if not have_node():
        return
    for mode, logout in (("allow", True), ("rate", True), ("rate", False), ("lock", True), ("once", True)):
        with tempfile.TemporaryDirectory() as tmp:
            project = session_project(Path(tmp), mode, logout=logout)
            logs: list[str] = []
            found = sp.session_findings(requirement(SEED, SESSION_DEF), project, time_limit=60, log=logs.append,
                                        node_modules=NODE_MODULES)
            assert found == [], (mode, found, logs)

@case
def session_10_without_a_confirming_sign_out_nothing_is_reported():
    if not have_node():
        return
    for fkey in ("username", "login"):
        with tempfile.TemporaryDirectory() as tmp:
            project = session_project(Path(tmp), "refuse", logout=False, fkey=fkey)
            logs: list[str] = []
            found, completed = sp.session_check(requirement(SEED, SESSION_DEF), project, time_limit=60, log=logs.append,
                                                node_modules=NODE_MODULES)
            assert found == [] and completed is False, (fkey, found, completed)
            assert any("no sign-out could confirm" in line for line in logs), logs

@case
def session_13_a_second_sign_in_that_ends_the_first_session_is_reported():
    if not have_node():
        return
    with tempfile.TemporaryDirectory() as tmp:
        project = session_project(Path(tmp), "kick")
        found, completed = sp.session_check(requirement(SEED, SESSION_DEF), project, time_limit=60, log=lambda m: None,
                                            node_modules=NODE_MODULES)
        assert completed and len(found) == 1, (completed, found)
        assert "ends the first browser's session" in found[0]["problem"], found
        assert "GET /api/me with jar 1 answered 401" in found[0]["problem"], found

        req = requirement(SEED, SESSION_DEF, "Signing in on a new device ends all other sessions of the account.")
        logs: list[str] = []
        assert sp.session_findings(req, project, time_limit=60, log=logs.append, node_modules=NODE_MODULES) == []
        assert any("no kick-out check" in line for line in logs), logs

        req = requirement(SEED, SESSION_DEF, "Signing in must not end other sessions of the account.")
        assert len(sp.session_findings(req, project, time_limit=60, log=lambda m: None, node_modules=NODE_MODULES)) == 1
    with tempfile.TemporaryDirectory() as tmp:
        project = session_project(Path(tmp), "allow")
        found, completed = sp.session_check(requirement(SEED, SESSION_DEF), project, time_limit=60, log=lambda m: None,
                                            node_modules=NODE_MODULES)
        assert found == [] and completed is True, (found, completed)

@case
def session_14_completed_flag_and_one_deadline():
    import time as _time
    with tempfile.TemporaryDirectory() as tmp:
        project = session_project(Path(tmp), "refuse")

        assert sp.session_check(requirement(SESSION_DEF), project) == ([], True)
        assert sp.session_check(requirement(SEED, SESSION_DEF), project, log=lambda m: None,
                                node_modules=str(Path(tmp) / "none")) == ([], False)
        (project / "backend/src/routes/auth.js").write_text("module.exports = {};" + NL, encoding="utf-8")
        assert sp.session_check(requirement(SEED, SESSION_DEF), project, log=lambda m: None,
                                node_modules=NODE_MODULES) == ([], False)
    if not have_node():
        return
    for limit in (0.5, 3.0, 9.0, 14.0):
        with tempfile.TemporaryDirectory() as tmp:
            project = session_project(Path(tmp), "refuse")
            t0 = _time.time()
            logs: list[str] = []
            found, completed = sp.session_check(requirement(SEED, SESSION_DEF), project, time_limit=limit,
                                                log=logs.append, node_modules=NODE_MODULES)
            took = _time.time() - t0
            assert took <= limit + 1.0, (limit, took)
            assert completed or found == [], (limit, found)
            if limit < 6:
                assert not completed and any("too little time" in line for line in logs), (limit, logs)
    for _ in range(40):
        leftovers = list(Path(tempfile.gettempdir()).glob("arc-session-probe-*"))
        if not leftovers:
            break
        _time.sleep(0.5)
    assert not leftovers, leftovers

RAW_YAML = NL.join([
    "modules:",
    "  - id: REQ-1",
    "    description: A session is the current-browser login record saved after successful sign-in.",
    "    scenarios:",
    "      - steps:",
    "        - keyword: GIVEN",
    "          content: 'The visitor starts at the application home page in a fresh unauthenticated",
    "            browser session. The seeded data is account `alice`, email `alice@example.test`,",
    "            password `Pw-123456!`. The visitor clicks “Sign in” on the home",
    "            page. Seed values: account `alice`, email",
    "            `alice@example.test`, password `Pw-123456!`.'",
    "",
])

@case
def session_11_raw_yaml_seed_sentence_wrapped_over_lines():
    assert sp.credentials(RAW_YAML) == [{"account": "alice", "email": "alice@example.test", "password": "Pw-123456!"}], \
        sp.credentials(RAW_YAML)

    import agent.seed_identity as si
    original = si.requirement_texts
    si.requirement_texts = lambda text: [" ".join(text.split())]
    try:
        assert sp.credentials(RAW_YAML)[0]["password"] == "Pw-123456!", sp.credentials(RAW_YAML)
    finally:
        si.requirement_texts = original
    assert sp.session_sentence(RAW_YAML) == SESSION_DEF, sp.session_sentence(RAW_YAML)
    if not have_node():
        return
    with tempfile.TemporaryDirectory() as tmp:
        project = session_project(Path(tmp), "refuse")
        found = sp.session_findings(RAW_YAML, project, time_limit=60, log=lambda m: None, node_modules=NODE_MODULES)
        assert len(found) == 1 and "answered 401" in found[0]["problem"], found

@case
def session_12_a_port_another_node_server_holds_is_never_used():
    if not have_node():
        return
    import subprocess
    port = sp.pick_port()
    assert port is not None
    script = "require('http').createServer((q, s) => s.end('other')).listen(" + str(port) + ", () => console.log('up'));"
    other = subprocess.Popen(["node", "-e", script], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "up"
        assert sp._port_taken(port), port
        assert sp.pick_port() != port
        with tempfile.TemporaryDirectory() as tmp:
            project = session_project(Path(tmp), "refuse")
            found = sp.session_findings(requirement(SEED, SESSION_DEF), project, time_limit=60, log=lambda m: None,
                                        node_modules=NODE_MODULES)
            assert len(found) == 1, found
    finally:
        other.kill()

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
