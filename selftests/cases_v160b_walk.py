from __future__ import annotations

import functools
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from agent import outsider_walk as ow
from agent.when_plan import plans_for

SERIAL = True
CASES = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))

def browser_case(fn):
    @functools.wraps(fn)
    def checked():
        if not (Path(NODE_MODULES) / '@playwright' / 'test').is_dir() or not (Path(NODE_MODULES) / 'express').is_dir():
            print('SKIP', fn.__name__, ': ARC_TX_NODE_MODULES has no @playwright/test + express')
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
  Signed-in scenarios begin with the home-page “Sign in” link, fields labeled “Username or email” and
  “Password”, and the “Sign in” button. The repository page has an “Issues” link; the Issues page has an
  “Open” link and a “New issue” link.
children:
- id: REQ-1
  description: Issues and pull requests.
  scenarios:
  - name: 'REQ-1: Issues - Scenario 1'
    steps:
    - keyword: GIVEN
      content: The visitor starts at the application home page in a fresh unauthenticated browser session. The system pre-provisions public repository `demo-repo` with an Open issue titled `Fix the docs`.
    - keyword: WHEN
      content: The visitor opens the Issues page and clicks the “Open” link.
  - name: 'REQ-1: Issues - Scenario 2'
    steps:
    - keyword: GIVEN
      content: The visitor starts at the application home page in a fresh unauthenticated browser session. The system pre-provisions public repository `demo-repo` with Open issue `Fix the docs`.
    - keyword: WHEN
      content: The visitor opens the visible `Fix the docs` issue entry.
  - name: 'REQ-1: Issues - Scenario 3'
    steps:
    - keyword: GIVEN
      content: The visitor starts at the application home page in a fresh browser session. The system pre-provisions public repository `demo-repo` and the account `writer` with email `writer@example.test` and password `Writer-pass-123!`.
    - keyword: WHEN
      content: The writer signs in as `writer`, opens the Issues page, and clicks the `New issue` link.
  - name: 'REQ-1: Issues - Scenario 4'
    steps:
    - keyword: GIVEN
      content: The system pre-provisions the separate Open PR `Merge me PR` and the author account `writer` with email `writer@example.test`, password `Writer-pass-123!`. The visitor starts at the application home page.
    - keyword: WHEN
      content: The author clicks `Merge pull request`.
  - name: 'REQ-1: Issues - Scenario 5'
    steps:
    - keyword: GIVEN
      content: The visitor starts at the application home page in a fresh unauthenticated browser session. The system pre-provisions public repository `demo-repo`.
    - keyword: WHEN
      content: The visitor opens the branch selector and enters `x` in the `Find branch` textbox.
'''

APP = r'''
const express=require('express'),fs=require('fs'),path=require('path');
const app=express();app.use(express.json());
app.post('/api/login',(req,res)=>{const {username,password}=req.body;
 if(!['writer','writer@example.test'].includes(username)||password!=='Writer-pass-123!')return res.status(401).json({error:'Invalid credentials'});
 res.setHeader('Set-Cookie','session=fixture; HttpOnly; Path=/; SameSite=Lax');res.json({ok:true})});
app.get('/api/me',(req,res)=>{if(!String(req.headers.cookie||'').includes('session=fixture'))return res.status(401).json({});res.json({username:'writer'})});
app.post('/api/merge',(req,res)=>{if(process.env.V160B_MERGE_LOG)fs.appendFileSync(process.env.V160B_MERGE_LOG,'merge\n');res.json({ok:true})});
app.use(express.static(path.join(__dirname,'../frontend/dist')));
app.use((req,res)=>res.sendFile(path.join(__dirname,'../frontend/dist/index.html')));
app.listen(Number(process.env.PORT),'127.0.0.1');
'''

HTML = r'''<!doctype html><html><head><title>Fixture</title></head><body><div id="app"></div><script>
const CFG=__CFG__;
const go=href=>{history.pushState({},'',href);render()};
document.addEventListener('click',e=>{const a=e.target.closest('a[href^="/"]');if(a){e.preventDefault();go(a.getAttribute('href'))}});
window.onpopstate=render;
async function render(){
 const auth=(await fetch('/api/me')).ok;const root=document.getElementById('app');const p=location.pathname;
 const head='<header>'+(auth?'<span>writer</span>':(CFG.entry==='link'?'<a href="/signin">Sign in</a>':'<button id="si">Sign in</button>'))+'</header>';
 let body='';
 if(p==='/signin'){
   root.innerHTML='<form><label>Username or email<input name="u"></label><label>Password<input name="p" type="password"></label><button>Sign in</button></form>';
   document.querySelector('form').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:e.target.u.value,password:e.target.p.value})});if(r.ok)go('/')};
   return;
 }
 if(p==='/'){
   body='<h1>Home</h1><a href="/repo">demo-repo</a>';
   if(CFG.homeIssues)body+='<a href="/issues">Issues</a>';
   if(CFG.dupIssues)body+='<a href="/other/issues">Issues</a>';
   if(CFG.titles)body+='<a href="'+(CFG.wrongLanding?'/issue/2':'/issue/1')+'">Fix the docs</a>';
   if(CFG.prTitle)body+='<a href="/pr/1">Merge me PR</a>';
 }else if(p==='/repo')body='<h1>demo-repo</h1><a href="/issues">Issues</a>';
 else if(p==='/issues'||p==='/other/issues')body='<h1>Issues</h1><a href="/issues?state=open">Open</a><a href="/issues/new">New issue</a>';
 else if(p==='/issue/1')body='<h1>Fix the docs</h1>';
 else if(p==='/issue/2')body='<h1>Other issue</h1>';
 else if(p==='/pr/1')body='<h1>Merge me PR</h1><button id="merge">Merge pull request</button>';
 else body='<h1>Page</h1>';
 root.innerHTML=head+body;
 const si=document.getElementById('si');if(si)si.onclick=()=>go('/signin');
 const m=document.getElementById('merge');if(m)m.onclick=()=>fetch('/api/merge',{method:'POST'});
}
render();</script></body></html>'''

def fixture(root, **cfg):
    conf = {'entry': 'link', 'homeIssues': True, 'dupIssues': False, 'titles': True, 'wrongLanding': False, 'prTitle': True}
    conf.update(cfg)
    files = {'backend/package.json': json.dumps({'scripts': {'start': 'node server.js'}}),
             'backend/server.js': APP,
             'frontend/dist/index.html': HTML.replace('__CFG__', json.dumps(conf))}
    for rel, value in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding='utf-8')
    return root

def walk(**cfg):
    with tempfile.TemporaryDirectory(prefix='plan-walk-fixture-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = fixture(Path(tmp) / 'app', **cfg)
        merges = Path(tmp) / 'merges.log'
        os.environ['V160B_MERGE_LOG'] = str(merges)
        try:
            before = sorted(str(p.relative_to(app)) for p in app.rglob('*') if p.is_file())
            logs = []
            report = ow.plan_walk_report(REQUIREMENT, app, time_limit=150, log=logs.append, node_modules=NODE_MODULES)
            findings, completed = ow.plan_walk_check(REQUIREMENT, app, time_limit=150, log=logs.append, node_modules=NODE_MODULES)
            assert before == sorted(str(p.relative_to(app)) for p in app.rglob('*') if p.is_file()), 'walk wrote to the source app'
            rows = {r['scenario'].split(' - ')[-1]: r for r in report['scenarios']}
            return rows, findings, completed, (merges.read_text() if merges.exists() else ''), logs
        finally:
            os.environ.pop('V160B_MERGE_LOG', None)

def kinds(row):
    return [(f['outcome'], f.get('name'), bool(f.get('implied'))) for f in row['findings']]

@unit_case
def unit_step_conversion():
    plans = {p['scenario'].split(' - ')[-1]: p for p in plans_for(REQUIREMENT)}
    steps, stop, _ = ow._plan_walk_steps(plans['Scenario 1'])
    assert [s.get('name') for s in steps] == ['Issues', 'Open'] and stop[0] == 'end', (steps, stop)
    steps, stop, _ = ow._plan_walk_steps(plans['Scenario 4'])
    assert [s['kind'] for s in steps] == ['sign_in', 'click', 'check'], steps
    assert steps[0]['implied'] and steps[1]['implied'] and steps[1]['name'] == 'Merge me PR', steps
    assert 'mutating' in stop[1], stop
    steps, stop, _ = ow._plan_walk_steps(plans['Scenario 5'])
    assert steps == [] and stop[0] == 'unrun' and 'unresolved' in stop[1], (steps, stop)

    plan = {'steps': [{'n': 1, 'part': 'WHEN', 'action': 'open', 'status': 'resolved', 'target': 'Comment validation issue',
                       'entity': {'kind': 'issue', 'name': 'Comment validation issue'}, 'session': 1},
                      {'n': 2, 'part': 'WHEN', 'action': 'click', 'status': 'conditional', 'target': 'Confirm', 'session': 1}]}
    steps, stop, _ = ow._plan_walk_steps(plan)
    assert [s['kind'] for s in steps] == ['click'] and stop[0] == 'unrun' and 'conditional' in stop[1], (steps, stop)

    plan = {'sessions': 2, 'steps': [{'n': 1, 'part': 'WHEN', 'action': 'open', 'status': 'resolved', 'target': 'Sessions', 'session': 2}]}
    steps, stop, notes = ow._plan_walk_steps(plan)
    assert steps == [] and notes and 'browser 1' in notes[0], (steps, notes)
    recipe = ow._recipe(REQUIREMENT)
    assert recipe['entry'] == 'Sign in' and recipe['identity'][0] == 'Username or email' and recipe['password'] == ['Password'], recipe
    assert ow.plan_walk_check(None, None, log=lambda _: 1 / 0) == ([], False)

@browser_case
def n1_good_app_is_clean_and_merge_never_clicked():
    rows, findings, completed, merges, logs = walk()
    assert completed and findings == [], (completed, findings, logs[-5:])
    for name in ('Scenario 1', 'Scenario 2', 'Scenario 3', 'Scenario 4'):
        assert rows[name]['status'] == 'clean', (name, rows[name])
    assert rows['Scenario 5']['status'] == 'unrun' and 'unresolved' in rows['Scenario 5']['stop']['reason'], rows['Scenario 5']
    assert merges == '', 'the mutating control was clicked'

@browser_case
def p1_sign_in_button_instead_of_link():
    rows, findings, completed, _m, _l = walk(entry='button')
    assert ('role-mismatch', 'Sign in', False) in kinds(rows['Scenario 3']), rows['Scenario 3']
    assert ('role-mismatch', 'Sign in', True) in kinds(rows['Scenario 4']), rows['Scenario 4']
    assert rows['Scenario 1']['status'] == 'clean', rows['Scenario 1']
    assert any('Sign in' in f['problem'] and 'link' in f['problem'] for f in findings), findings

@browser_case
def p2_entry_only_behind_the_repository():
    rows, findings, _c, _m, _l = walk(homeIssues=False)
    assert kinds(rows['Scenario 1']) == [('missing', 'Issues', False)], rows['Scenario 1']
    assert rows['Scenario 1']['findings'][0]['route'] == '/', rows['Scenario 1']
    assert kinds(rows['Scenario 3']) == [('missing', 'Issues', False)], rows['Scenario 3']
    assert rows['Scenario 2']['status'] == 'clean', rows['Scenario 2']
    assert any('“Issues”' in f['problem'] for f in findings), findings

@browser_case
def p3_wrong_landing_and_n2_ambiguity_is_advisory():
    rows, findings, _c, _m, logs = walk(wrongLanding=True, dupIssues=True)
    assert kinds(rows['Scenario 2']) == [('wrong-landing', 'Fix the docs', False)], rows['Scenario 2']
    assert ('ambiguous', 'Issues', False) in kinds(rows['Scenario 1']) and rows['Scenario 1']['status'] == 'clean', rows['Scenario 1']
    assert not any('Issues' in f['problem'] for f in findings), findings
    assert any('advisory' in line and 'Issues' in line for line in logs), logs[-5:]

@browser_case
def p4_implied_subject_unreachable():
    rows, findings, _c, merges, _l = walk(prTitle=False)
    assert kinds(rows['Scenario 4']) == [('missing', 'Merge me PR', True)], rows['Scenario 4']
    assert any('IMPLIED' in f['problem'] for f in findings), findings
    assert all(set(f) == {'where', 'requirement', 'problem', 'fix'} for f in findings), findings
    assert merges == ''

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
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v160b walk cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
