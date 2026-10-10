from __future__ import annotations

import functools
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from agent import session_chain as sc
from agent import settings as agent_settings

SERIAL = True
CASES = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))

def has_playwright() -> bool:
    return (Path(NODE_MODULES) / '@playwright' / 'test').is_dir()

def live_case(fn):
    @functools.wraps(fn)
    def checked():
        if not (Path(NODE_MODULES) / 'express').is_dir() or not shutil.which('node'):
            print('SKIP', fn.__name__, ': ARC_TX_NODE_MODULES has no express (or no node)')
            return False
        fn()
        return True
    CASES.append(checked)
    return checked

def unit_case(fn):
    CASES.append(fn)
    return fn

REQUIREMENT = '''- id: REQ-9
  name: Manage sessions
  description: |-
    The Active sessions page lists the sessions of the account. Revoking a session immediately marks it inactive; the
    revoked browser can no longer open protected pages and must return to the sign-in page, while the current session
    remains active. To sign in, the visitor opens the home page, clicks the link named “Sign in”, enters the account
    in the field labelled “Username or email” and the password in the field labelled “Password”, and clicks the
    button named “Sign in”.
  scenarios:
  - name: 'REQ-9: Manage sessions - Scenario 1'
    steps:
    - keyword: GIVEN
      content: The system pre-provisions the verified account `owner` with email `owner@example.test` and password `Owner-pass-123!`. Two browser sessions for that account are active.
    - keyword: WHEN
      content: The visitor revokes the other session and then reloads the revoked browser.
    - keyword: THEN
      content: The revoked browser is redirected to the sign-in page.
- id: REQ-8
  name: Profile
  description: The signed-in visitor edits the display name.
'''

SERVER = r'''
const express = require('express');
const path = require('path');
const crypto = require('crypto');
const CFG = __CFG__;
const app = express();
app.use(express.json());
const users = [{ id: 1, username: 'owner', email: 'owner@example.test', password: 'Owner-pass-123!' }];
let sessions = [];
function cookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i > -1) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}
function current(req) {
  const s = sessions.find((x) => x.token === cookies(req).sid);
  if (!s) return null;
  if (!s.active && !CFG.acceptInactive) return null;
  return s;
}
function requireAuth(req, res, next) {
  const s = current(req);
  if (!s) { res.status(401).json({ error: 'Access denied' }); return; }
  req.session = s;
  next();
}
app.post('/api/auth/login', (req, res) => {
  const { username, password } = req.body || {};
  const user = users.find((u) => (u.username === username || u.email === username) && u.password === password);
  if (!user) { res.status(401).json({ error: 'Invalid credentials' }); return; }
  const s = { id: String(sessions.length + 101), token: crypto.randomBytes(16).toString('hex'), userId: user.id, active: true };
  sessions.push(s);
  res.setHeader('Set-Cookie', `sid=${s.token}; Path=/; HttpOnly`);
  res.json({ user: { username: user.username } });
});
app.post('/api/auth/logout', (req, res) => {
  const s = current(req);
  if (s) sessions = sessions.filter((x) => x !== s);
  res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
  res.status(204).end();
});
app.get('/api/auth/me', (req, res) => {
  const s = current(req);
  if (!s) {
    const held = sessions.find((x) => x.token === cookies(req).sid);
    res.status(401).json({ user: null, revoked: !!held });
    return;
  }
  res.json({ username: 'owner', email: 'owner@example.test' });
});
// BEGIN revoke
{
  app.get('/api/sessions', requireAuth, (req, res) => {
    res.json({ sessions: sessions.filter((x) => x.userId === req.session.userId).map((x) => (
      { id: x.id, active: x.active, device: 'Browser', current: x === req.session })) });
  });
  app.delete('/api/sessions/:id', requireAuth, (req, res) => {
    const s = sessions.find((x) => x.id === req.params.id && x.userId === req.session.userId);
    if (!s) { res.status(404).json({ error: 'Not found' }); return; }
    s.active = false;
    res.status(204).end();
  });
}
// END revoke
const dist = path.resolve(__dirname, '../../frontend/dist');
app.use(express.static(dist));
app.get(/^(?!\/api(?:\/|$)).*/, (req, res) => res.sendFile(path.join(dist, 'index.html')));
app.listen(Number(process.env.PORT || 3000), () => console.log('listening'));
'''

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>t</title></head><body><div id="root"></div><script>
const REDIRECT = __REDIRECT__;
const HOME_LINK = __HOME_LINK__;
const root = document.getElementById('root');
if (location.pathname === '/login') {
  root.innerHTML = '<h1>Sign in</h1><form id="f"><label>Username or email <input name="u"></label><label>Password <input type="password" name="p"></label><button type="submit">Sign in</button></form>';
  document.getElementById('f').addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: e.target.u.value, password: e.target.p.value }) });
    if (r.ok) { sessionStorage.setItem('hadSession', '1'); location.href = '/'; }
  });
} else {
  fetch('/api/auth/me').then(async (r) => {
    const body = await r.json().catch(() => ({}));
    if (r.ok) { root.innerHTML = '<header><button>Account menu</button> ' + body.username + '</header><h1>Workspace</h1>'; return; }
    if (REDIRECT === true && body.revoked) { location.replace('/login'); return; }
    if (REDIRECT === 'hint' && sessionStorage.getItem('hadSession')) {
      sessionStorage.removeItem('hadSession'); location.replace('/login'); return;
    }
    root.innerHTML = '<header>' + (HOME_LINK ? '<a href="/login">Sign in</a>' : '') + '</header><h1>Welcome</h1>';
  });
}
</script></body></html>
'''

APP_SRC = '''import { Route } from 'react-router-dom';
export const routes = [<Route path="/" />, <Route path="/login" />, <Route path="/settings/sessions" />];
'''

def make_app(redirect=True, home_link=True, **cfg) -> Path:
    root = Path(tempfile.mkdtemp(prefix='v163c-session-'))
    src = root / 'backend' / 'src'
    src.mkdir(parents=True)
    (root / 'backend' / 'package.json').write_text(json.dumps({'name': 'b', 'scripts': {'start': 'node src/index.js'}}),
                                                     encoding='utf-8')
    code = SERVER.replace('__CFG__', json.dumps(cfg))
    if not cfg.get('revoke', True):
        code = re.sub(r'// BEGIN revoke\n.*?// END revoke\n', '', code, flags=re.S)
    (src / 'index.js').write_text(code, encoding='utf-8')
    (root / 'frontend' / 'dist').mkdir(parents=True)
    (root / 'frontend' / 'dist' / 'index.html').write_text(
        PAGE.replace('__REDIRECT__', json.dumps(redirect)).replace('__HOME_LINK__', json.dumps(home_link)),
        encoding='utf-8')
    (root / 'frontend' / 'src').mkdir(parents=True)
    (root / 'frontend' / 'src' / 'App.jsx').write_text(APP_SRC, encoding='utf-8')
    return root

def probe(redirect=True, home_link=True, **cfg):
    root = make_app(redirect, home_link, **cfg)
    logs: list[str] = []
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob('*'))
    try:
        t = time.time()
        res = sc.revoke_probe(root, REQUIREMENT, time_limit=90, log=logs.append, node_modules=NODE_MODULES)
        took = time.time() - t
        after = sorted(p.relative_to(root).as_posix() for p in root.rglob('*'))
        assert before == after, ('the probe wrote in project_dir', set(after) - set(before))
        check_shape(res)
        assert took < 80, took
        return res, logs
    finally:
        shutil.rmtree(root, ignore_errors=True)

def check_shape(res):
    assert set(res) == {'item', 'status', 'result', 'findings', 'parts', 'evidence'}, res
    assert res['item'] == 'session_revoke' and res['status'] in ('finding', 'clean', 'not run'), res
    assert set(res['parts']) == set(sc.REVOKE_PARTS), res
    json.dumps(res['evidence'])
    if 'finding' in res['parts'].values():
        assert res['status'] == 'finding' and res['findings'], res
    elif any(v != 'not run' for v in res['parts'].values()):
        assert res['status'] == 'clean' and not res['findings'], res
    else:
        assert res['status'] == 'not run' and not res['findings'], res

@live_case
def c1_revoke_works_and_lands_on_sign_in():
    res, logs = probe(redirect=True)
    assert res['parts']['revoke'] == 'clean', (res, logs)
    if has_playwright():
        assert res['parts']['revoked_page'] == 'clean', (res, logs)
    else:
        assert res['parts']['revoked_page'] == 'not run' and 'playwright' in res['result'], res
    assert res['status'] == 'clean' and res['findings'] == [], res

@live_case
def f1_revoked_session_still_accepted():
    res, logs = probe(acceptInactive=True)
    assert res['parts']['revoke'] == 'finding', (res, logs)
    assert res['parts']['revoked_page'] == 'not run', res
    assert any('revoked session keeps working' in f for f in res['findings']), res['findings']

@live_case
def f2_revoked_browser_not_sent_to_sign_in():
    if not has_playwright():
        print('SKIP f2: no @playwright/test in ARC_TX_NODE_MODULES')
        return
    res, logs = probe(redirect=False)
    assert res['parts']['revoke'] == 'clean', (res, logs)
    assert res['parts']['revoked_page'] == 'finding', (res, logs)
    text = res['findings'][0]
    assert 'not sent to the sign-in page' in text and 'must return to the sign-in page' in text, text
    assert 'never signed in keeps' in text, text

@live_case
def n1_no_revoke_route_is_not_run():
    res, logs = probe(revoke=False)
    assert res['status'] == 'not run' and res['findings'] == [], (res, logs)
    assert 'no revoke route' in res['result'], res['result']

@live_case
def h1_hint_redirect_of_the_signed_in_browser_is_clean():
    if not has_playwright():
        print('SKIP h1: no @playwright/test in ARC_TX_NODE_MODULES')
        return
    res, logs = probe(redirect='hint')
    assert res['parts']['revoke'] == 'clean', (res, logs)
    assert res['parts']['revoked_page'] == 'clean', (res, logs)
    assert res['status'] == 'clean' and res['findings'] == [], res
    calls = res['evidence']['calls']
    assert any(c.startswith('revoke A browser from B') for c in calls), calls
    assert res['evidence'].get('page', {}).get('signin', {}).get('ok') is True, res['evidence'].get('page')

@live_case
def u2_ui_sign_in_not_completed_is_not_run():
    if not has_playwright():
        print('SKIP u2: no @playwright/test in ARC_TX_NODE_MODULES')
        return
    res, logs = probe(redirect=False, home_link=False)
    assert res['parts']['revoke'] == 'clean', (res, logs)
    assert res['parts']['revoked_page'] == 'not run', (res, logs)
    assert 'UI sign-in of browser A did not complete' in res['result'] and 'Sign in' in res['result'], res['result']
    assert res['findings'] == [], res

TREE_CHILD = ("import json, subprocess, sys, time; "
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
              "print(json.dumps({'child': c.pid}), flush=True); time.sleep(120)")

def pid_alive(pid: int) -> bool:
    if os.name == 'nt':
        import subprocess
        for _ in range(5):
            out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True,
                                 errors='replace').stdout
            if out:
                return str(pid) in out
            time.sleep(0.5)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f'/proc/{pid}/stat', 'rb') as handle:
            return handle.read().rsplit(b')', 1)[1].split()[0] != b'Z'
    except OSError:
        return True

@unit_case
def p1_overrun_kills_the_whole_tree():
    import sys as _sys
    work = Path(tempfile.mkdtemp(prefix='v163c-tree-'))
    try:
        runner = sc._PageRunner(None, work, '', command=[_sys.executable, '-c', TREE_CHILD])
        first = runner.read(time.time() + 30)
        child, parent = first.get('child'), runner.proc.pid
        assert child and pid_alive(child) and pid_alive(parent), first
        late = runner.read(time.time() + 1)
        assert 'overran' in late.get('error', ''), late
        runner.close(orderly=False)
        t = time.time()
        while (pid_alive(child) or pid_alive(parent)) and time.time() - t < 10:
            time.sleep(0.2)
        assert not pid_alive(parent) and not pid_alive(child), ('left running', parent, child)
        if os.name != 'nt':
            import signal
            try:
                os.killpg(parent, 0)
                group_left = True
            except ProcessLookupError:
                group_left = False
            assert not group_left, 'the process group still has members'
    finally:
        shutil.rmtree(work, ignore_errors=True)

@unit_case
def p2_orderly_end_needs_no_kill():
    import sys as _sys
    work = Path(tempfile.mkdtemp(prefix='v163c-tree-'))
    try:
        runner = sc._PageRunner(None, work, '', command=[_sys.executable, '-c',
                                                         "import json; print(json.dumps({'stage': 'done'}), flush=True)"])
        assert runner.read(time.time() + 30) == {'stage': 'done'}
        runner.close(orderly=True)
        assert runner.proc.poll() is not None
        assert sc._PageRunner(None, work, '').read(time.time() + 1) == {'error': 'no node'}
    finally:
        shutil.rmtree(work, ignore_errors=True)

@unit_case
def s1_setting_off_means_not_called():
    calls = []
    real = sc.revoke_probe
    sc.revoke_probe = lambda *a, **k: calls.append(1) or {'findings': ['x Fix: y']}
    saved = agent_settings.SETTINGS.get(sc.SETTING, '<absent>')
    try:
        agent_settings.SETTINGS[sc.SETTING] = False
        assert sc.batch_findings(Path('.'), REQUIREMENT, ['REQ-9'], log=lambda m: None) == [] and not calls
        agent_settings.SETTINGS.pop(sc.SETTING, None)
        got = sc.batch_findings(Path('.'), REQUIREMENT, ['REQ-9'], log=lambda m: None)
        assert calls and got and set(got[0]) == {'where', 'requirement', 'problem', 'fix'}, got
        assert got[0]['problem'] == 'x' and got[0]['fix'] == 'y', got
        assert 'must return to the sign-in page' in got[0]['requirement'], got
    finally:
        sc.revoke_probe = real
        if saved == '<absent>':
            agent_settings.SETTINGS.pop(sc.SETTING, None)
        else:
            agent_settings.SETTINGS[sc.SETTING] = saved

@unit_case
def s2_batch_without_revocation_is_not_probed():
    calls = []
    real = sc.revoke_probe
    sc.revoke_probe = lambda *a, **k: calls.append(1) or {'findings': []}
    saved_on = agent_settings.SETTINGS.get('session_revoke_after_batch')
    agent_settings.SETTINGS['session_revoke_after_batch'] = True
    try:
        assert sc.batch_findings(Path('.'), REQUIREMENT, ['REQ-8'], log=lambda m: None) == [] and not calls
        assert sc.batch_findings(Path('.'), 'not: [yaml', ['REQ-9'], log=lambda m: None) == [] and not calls
        assert sc.batch_findings(Path('.'), REQUIREMENT, ['REQ-9'], log=lambda m: None) == [] and calls == [1]
    finally:
        sc.revoke_probe = real
        agent_settings.SETTINGS['session_revoke_after_batch'] = saved_on
    assert sc.revoke_wanted('Each row has a "Revoke session" button') and not sc.revoke_wanted('Sign out of this browser.')
    assert sc.revoke_redirect_quote('A user signs out and is redirected to the sign-in page.') == ''
    assert sc._front_route('<Route path="/login" /> {path: "/settings/sessions"}', sc.LOGIN_ROUTE) == '/login'
    assert sc._front_route('<Route path="/login" /> {path: "/settings/sessions"}', sc.SESSIONS_ROUTE) == '/settings/sessions'

@unit_case
def u1_never_raises():
    tmp = Path(tempfile.mkdtemp(prefix='v163c-garbage-'))
    try:
        for req, proj in ((None, tmp / 'missing'), ('', tmp), (REQUIREMENT, tmp), (REQUIREMENT, Path(__file__))):
            res = sc.revoke_probe(proj, req, time_limit=10, log=lambda m: None)
            check_shape(res)
            assert res['status'] == 'not run', res
        check_shape(sc.revoke_probe(tmp, REQUIREMENT, deadline=time.time() - 5, log=None))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    failed = skipped = 0
    for test in CASES:
        try:
            if test() is False:
                skipped += 1
                continue
            print('PASS', test.__name__)
        except Exception:
            failed += 1
            traceback.print_exc()
            print('FAIL', test.__name__)
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v163c session cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
