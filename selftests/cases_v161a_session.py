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

SERIAL = True
CASES = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))

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

REQUIREMENT = '''id: ROOT
description: >-
  A session is the current-browser login record saved after a successful sign-in. Signing out ends only the current
  browser session. The Active sessions page lists the sessions of the account; revoking a session marks it inactive,
  the revoked browser returns to sign-in, and the current session remains active.
children:
- id: REQ-1
  description: Sessions.
  scenarios:
  - name: 'REQ-1: Sessions - Scenario 1'
    steps:
    - keyword: GIVEN
      content: The system pre-provisions the verified account `owner` with email `owner@example.test` and password `Owner-pass-123!`. Two browser sessions for that account are active.
    - keyword: WHEN
      content: The visitor revokes the other session.
'''

NO_CREDENTIALS = '''id: ROOT
description: A spreadsheet. The visitor is unauthenticated and edits cells.
'''

ENDS_OTHERS = REQUIREMENT.replace('Signing out ends only the current\n  browser session.',
                                  'Signing in ends the other sessions of the account.')

SERVER = r'''
const express = require('express');
const crypto = require('crypto');
const CFG = __CFG__;
const app = express();
app.use(express.json());
const users = [{ id: 1, username: 'owner', email: 'owner@example.test', password: 'Owner-pass-123!' }];
let sessions = [];
let shared = true;
let logins = 0;
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
  if (CFG.shared && !shared) return null;
  if (!s.active && !CFG.acceptInactive) return null;
  return s;
}
function requireAuth(req, res, next) {
  const s = current(req);
  if (!s) { res.status(401).json({ error: 'Access denied' }); return; }
  req.session = s;
  next();
}
app.get('/api/health', (req, res) => res.json({ ok: true }));
// BEGIN login
{
  app.post('/api/auth/login', (req, res) => {
    logins += 1;
    if (CFG.rateLimit && logins > 1) { res.status(429).json({ error: 'Too many requests' }); return; }
    const { username, password } = req.body || {};
    const user = users.find((u) => (u.username === username || u.email === username) && u.password === password);
    if (!user) { res.status(401).json({ error: 'Invalid credentials' }); return; }
    if (CFG.kick) sessions.forEach((x) => { x.active = false; });
    const s = { id: String(sessions.length + 101), token: crypto.randomBytes(16).toString('hex'), userId: user.id, active: true };
    sessions.push(s);
    shared = true;
    res.setHeader('Set-Cookie', `sid=${s.token}; Path=/; HttpOnly`);
    res.json({ user: { username: user.username } });
  });
}
// END login
app.post('/api/auth/logout', (req, res) => {
  const s = current(req);
  if (CFG.shared) shared = false;
  else if (s && !CFG.cookieOnlyLogout) sessions = sessions.filter((x) => x !== s);
  res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
  res.status(204).end();
});
app.get('/api/auth/me', (req, res) => {
  const s = current(req);
  if (!s) { res.status(CFG.anonOk ? 200 : 401).json({ ok: true }); return; }
  if (CFG.anonOk) { res.status(200).json({ ok: true }); return; }
  res.json({ username: 'owner', email: 'owner@example.test' });
});
// BEGIN revoke
{
  app.get('/api/sessions', requireAuth, (req, res) => {
    res.json({ sessions: sessions.filter((x) => x.userId === req.session.userId).map((x) => {
      const row = { id: x.id, active: x.active, device: 'Browser' };
      if (!CFG.noCurrent) row.current = x === req.session;
      return row;
    }) });
  });
  app.delete('/api/sessions/:id', requireAuth, (req, res) => {
    const s = sessions.find((x) => x.id === req.params.id && x.userId === req.session.userId);
    if (!s) { res.status(404).json({ error: 'Not found' }); return; }
    s.active = false;
    res.status(204).end();
  });
}
// END revoke
app.listen(Number(process.env.PORT || 3000), () => console.log('listening'));
'''

def make_app(**cfg) -> Path:
    root = Path(tempfile.mkdtemp(prefix='v161a-session-'))
    src = root / 'backend' / 'src'
    src.mkdir(parents=True)
    (root / 'backend' / 'package.json').write_text(json.dumps({'name': 'b', 'scripts': {'start': 'node src/index.js'}}),
                                                     encoding='utf-8')
    code = SERVER.replace('__CFG__', json.dumps(cfg))

    for block, on in (('login', not cfg.get('noLogin')), ('revoke', cfg.get('revoke'))):
        if not on:
            code = re.sub(r'// BEGIN ' + block + r'\n.*?// END ' + block + r'\n', '', code, flags=re.S)
    (src / 'index.js').write_text(code, encoding='utf-8')
    return root

def probe(requirement=REQUIREMENT, **cfg):
    root = make_app(**cfg)
    logs: list[str] = []
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob('*'))
    try:
        t = time.time()
        res = sc.session_chain_probe(requirement, root, time_limit=60, log=logs.append, node_modules=NODE_MODULES)
        took = time.time() - t
        after = sorted(p.relative_to(root).as_posix() for p in root.rglob('*'))
        assert before == after, ('the probe wrote in project_dir', set(after) - set(before))
        check_shape(res)
        assert took < 45, took
        return res, logs
    finally:
        shutil.rmtree(root, ignore_errors=True)

def check_shape(res):
    assert set(res) == {'item', 'status', 'result', 'findings', 'parts', 'evidence'}, res
    assert res['item'] == 'session' and res['status'] in ('finding', 'clean', 'not run'), res
    assert len(res['result']) <= 300 and res['result'], res
    assert len(res['findings']) <= 5 and all(isinstance(f, str) and len(f) <= 500 for f in res['findings']), res
    assert set(res['parts']) == set(sc.PARTS), res
    assert all(v in ('finding', 'clean', 'not run') for v in res['parts'].values()), res
    json.dumps(res['evidence'])
    ran = [v for v in res['parts'].values() if v != 'not run']
    if 'finding' in res['parts'].values():
        assert res['status'] == 'finding' and res['findings'], res
    elif ran:
        assert res['status'] == 'clean' and not res['findings'], res
    else:
        assert res['status'] == 'not run' and not res['findings'], res

@live_case
def p1_signout_ends_the_other_browser_too():
    res, logs = probe(shared=True)
    assert res['parts']['signout_isolation'] == 'finding', (res, logs)
    assert res['status'] == 'finding', res
    assert any('another browser' in f for f in res['findings']), res['findings']
    assert res['parts']['revoke'] == 'not run', res

@live_case
def p2_revoked_session_still_accepted():
    res, logs = probe(revoke=True, acceptInactive=True)
    assert res['parts']['revoke'] == 'finding', (res, logs)
    assert res['parts']['signout_isolation'] == 'clean' and res['parts']['inactive_rejected'] == 'clean', res
    assert res['evidence'].get('revoked_row') == 'inactive', res['evidence']
    assert any('revoked session keeps working' in f for f in res['findings']), res['findings']

@live_case
def p3_signout_leaves_the_stored_session_valid():
    res, logs = probe(cookieOnlyLogout=True)
    assert res['parts']['inactive_rejected'] == 'finding', (res, logs)
    assert res['parts']['signout_isolation'] == 'clean', res

    plain = REQUIREMENT.replace('The Active sessions page lists the sessions of the account; revoking a session marks it '
                                'inactive,\n  the revoked browser returns to sign-in, and the current session remains '
                                'active.', '').replace('The visitor revokes the other session.', 'The visitor signs out.')
    assert 'revok' not in plain.lower(), plain
    res2, _ = probe(plain, cookieOnlyLogout=True)
    assert res2['parts']['inactive_rejected'] == 'not run' and res2['status'] == 'clean', res2

@live_case
def n1_isolation_clean_no_revoke_route():
    res, logs = probe()
    assert res['status'] == 'clean', (res, logs)
    assert res['parts'] == {'signout_isolation': 'clean', 'inactive_rejected': 'clean', 'revoke': 'not run'}, res
    assert 'revoke not run: no revoke route' in res['result'], res['result']

@live_case
def n2_correct_revoke_is_clean():
    res, logs = probe(revoke=True)
    assert res['status'] == 'clean' and set(res['parts'].values()) == {'clean'}, (res, logs)
    assert res['evidence'].get('revoke_target') == 'current marker', res['evidence']
    res2, logs2 = probe(revoke=True, noCurrent=True)
    assert set(res2['parts'].values()) == {'clean'}, (res2, logs2)
    assert res2['evidence'].get('revoke_target') == 'new row', res2['evidence']

@live_case
def n3_no_sign_in_route_or_no_credentials():
    res, _ = probe(noLogin=True)
    assert res['status'] == 'not run' and 'no sign-in route' in res['result'], res
    assert res['evidence']['calls'] == [], res
    res2, _ = probe(NO_CREDENTIALS)
    assert res2['status'] == 'not run' and 'credentials' in res2['result'], res2

@live_case
def n4_rate_limited_sign_in_is_no_finding():
    res, logs = probe(rateLimit=True, revoke=True, acceptInactive=True)
    assert res['status'] == 'not run' and res['findings'] == [], (res, logs)
    assert '429' in res['result'], res['result']

@live_case
def n5_stated_new_sign_in_ends_others():
    res, logs = probe(ENDS_OTHERS, kick=True)
    assert res['status'] == 'not run' and res['findings'] == [], (res, logs)
    assert 'new sign-in ends the other sessions' in res['result'], res['result']

@live_case
def n6_no_who_am_i_contrast():
    res, logs = probe(anonOk=True, shared=True)
    assert res['status'] == 'not run' and res['findings'] == [], (res, logs)
    assert 'contrast' in res['result'], res['result']

@live_case
def n7_stray_node_after_its_shell_died_is_killed():
    import subprocess

    from agent import session_probe as sp
    from agent.fsutil import IS_WINDOWS, BackgroundProcess
    if not IS_WINDOWS:
        print('SKIP', 'n7 (Windows only)')
        return
    root = make_app()
    port = sp.pick_port()
    server = BackgroundProcess([shutil.which('node'), 'src/index.js'], cwd=root / 'backend',
                               env={'PORT': str(port), 'NODE_PATH': NODE_MODULES})
    try:
        end = time.time() + 20
        while time.time() < end and not sc._answers(f'http://127.0.0.1:{port}', 1.0):
            time.sleep(0.3)
        assert sc._answers(f'http://127.0.0.1:{port}', 1.0), server.output_so_far()
        subprocess.run(['taskkill', '/PID', str(server.process.pid), '/F'], capture_output=True, timeout=10)
        time.sleep(0.5)
        assert not server.alive() and sp._port_taken(port), 'the node child should survive its shell here'
        sc._kill_strays(server, port, time.time() + 20)
        time.sleep(0.5)
        assert not sp._port_taken(port), 'the stray node still holds the port'
    finally:
        sp._stop(server, 5)
        sc._kill_strays(server, port, time.time() + 10)
        shutil.rmtree(root, ignore_errors=True)

@unit_case
def u1_never_raises_on_garbage():
    tmp = Path(tempfile.mkdtemp(prefix='v161a-garbage-'))
    try:
        (tmp / 'backend').mkdir()
        (tmp / 'backend' / 'index.js').write_text('app.post("/api/login", (req, res) => { const {', encoding='utf-8')
        for req, proj in ((None, tmp / 'missing'), ('', tmp), (REQUIREMENT, tmp / 'missing'), (REQUIREMENT, tmp),
                          ('\x00\xff garbage', tmp), (REQUIREMENT, Path(__file__))):
            res = sc.session_chain_probe(req, proj, time_limit=10, log=lambda m: None)
            check_shape(res)
            assert res['status'] == 'not run', res
        res = sc.session_chain_probe(REQUIREMENT, tmp, time_limit=-5, log=None)
        check_shape(res)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@unit_case
def u2_route_classification():
    tmp = Path(tempfile.mkdtemp(prefix='v161a-routes-'))
    try:
        src = tmp / 'backend' / 'src'
        src.mkdir(parents=True)
        (src / 'index.js').write_text('''
const express = require('express');
const app = express();
app.post('/api/auth/login', (req, res) => { const { username, password } = req.body; });
app.post('/api/auth/logout', (req, res) => res.end());
app.get('/api/auth/me', (req, res) => res.end());
app.get('/api/account/sessions', (req, res) => res.end());
app.delete('/api/account/sessions/:sessionId', (req, res) => res.end());
app.post('/api/account/sessions/:id/revoke', (req, res) => res.end());
app.post('/api/account/sessions/revoke-others', (req, res) => res.end());
app.post('/api/session/end', (req, res) => res.end());
app.post('/api/sessions/revoke', (req, res) => { const id = req.body.sessionId; });
app.delete('/api/friends/:id', (req, res) => res.end());
app.post('/api/send-all', (req, res) => res.end());
app.delete('/api/session', (req, res) => res.end());
app.delete('/api/sessions', (req, res) => res.end());
app.listen(3000);
''', encoding='utf-8')
        routes = sc.session_routes(tmp)
        assert sorted(r['path'] for r in routes['logout']) == ['/api/auth/logout', '/api/session'], routes['logout']
        assert [r['path'] for r in routes['list']] == ['/api/account/sessions'], routes['list']
        got = {(r['method'], r['path'], r['kind']) for r in routes['revoke']}
        assert got == {('delete', '/api/account/sessions/:sessionId', 'id'), ('post', '/api/account/sessions/:id/revoke', 'id'),
                       ('post', '/api/account/sessions/revoke-others', 'others'), ('post', '/api/sessions/revoke', 'body')}, got
        assert routes['revoke'][0]['method'] == 'delete', routes['revoke']
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@unit_case
def u3_rows_and_markers():
    rows = sc._rows({'data': {'sessions': [{'id': 1, 'current': True}, {'id': 2, 'status': 'revoked'}]}})
    assert rows and sc._rid(rows[0]) == '1' and sc._is_current(rows[0]) and sc._inactive(rows[1]), rows
    assert sc._ids(rows) == ['1'], sc._ids(rows)
    assert sc._rows({'sessions': []}) is None and sc._rows('x') is None and sc._rows([1, 2]) is None
    assert sc._token({'data': {'accessToken': 'a' * 20}}) == 'a' * 20 and sc._token({'token': 'short'}) is None
    assert sc.SIGNOUT_ALL.search('Signing out ends all sessions of the account.')
    assert not sc.SIGNOUT_ALL.search('The dialog explains that sign-out affects only the current browser session.')

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
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v161a session cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
