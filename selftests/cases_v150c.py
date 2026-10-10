from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
import functools
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent import outsider_walk as ow

SERIAL = True
CASES = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))

def case(fn):
    @functools.wraps(fn)
    def checked():
        if not (Path(NODE_MODULES) / '@playwright' / 'test').is_dir():
            print('SKIP', fn.__name__, ': ARC_TX_NODE_MODULES has no @playwright/test')
            return False
        fn()
        return True
    CASES.append(checked)
    return checked

REQUIREMENT = '''description: >-
  Visitors use the "Sign in" link. The sign-in page has fields "Username or email"
  and "Password", and a "Sign in" button. The authenticated account menu is "Account menu".
scenarios:
- name: Open a seeded destination
  steps:
  - keyword: GIVEN
    content: 'Seed values: account `sample-user`, email `sample@example.test`, password `Fixture-pass-123!`.'
  - keyword: WHEN
    content: 'The user signs in as `sample-user`, opens "Account menu", and clicks the "Reports" link.'
- name: Create a new record
  steps:
  - keyword: GIVEN
    content: 'Seed values: account `sample-user`, email `sample@example.test`, password `Fixture-pass-123!`.'
  - keyword: WHEN
    content: 'The user signs in as `sample-user`, enters `new-record` in "Name", clicks "Create record", and opens `new-record`.'
- name: Visitor navigation
  steps:
  - keyword: WHEN
    content: 'The visitor opens the "Public only" link.'
'''

APP = r'''
const express=require('express'),fs=require('fs'),path=require('path');
const app=express();app.use(express.json());
app.post('/api/login',(req,res)=>{
 const {username,password}=req.body;
 if(FAIL||username!=='sample-user'||password!=='Fixture-pass-123!')return res.status(401).json({error:'Invalid credentials'});
 res.setHeader('Set-Cookie','session=fixture; HttpOnly; Path=/; SameSite=Lax');res.json({ok:true});
});
app.get('/api/me',(req,res)=>{if(!String(req.headers.cookie||'').includes('session=fixture'))return res.status(401).json({});res.json({username:'sample-user'})});
app.use(express.static(path.join(__dirname,'../frontend/dist')));
app.use((req,res)=>res.sendFile(path.join(__dirname,'../frontend/dist/index.html')));
app.listen(Number(process.env.PORT),'127.0.0.1');
'''

HTML = r'''<!doctype html><html><body><div id="app"></div><script>
async function render(){
 const me=await fetch('/api/me');const auth=me.ok;const root=document.getElementById('app');
 if(location.pathname==='/login'&&!auth){
  root.innerHTML='<form><label>Username or email<input name="username"></label><label>Password<input name="password" type="password"></label><button>Sign in</button></form>';
  document.querySelector('form').onsubmit=async e=>{e.preventDefault();const f=e.target;const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.username.value,password:f.password.value})});if(r.ok){location.href='/workspace'}else{root.insertAdjacentHTML('beforeend','<p>Invalid credentials</p>')}};return;
 }
 if(!auth){root.innerHTML='<a href="/login">Sign in</a><p>Welcome. Use the Sign in link to access your account.</p>';return}
 root.innerHTML='<header><button aria-label="Account menu" aria-expanded="false">sample-user</button></header><h1>Workspace</h1>'+VISITOR+'<section id="menu" hidden>'+TARGET+'</section><span hidden>Reports</span>';
 document.querySelector('button').onclick=e=>{const m=document.getElementById('menu');m.hidden=!m.hidden;e.currentTarget.setAttribute('aria-expanded',String(!m.hidden))};
}
render();</script></body></html>'''

def fixture(root, reachable=True, visitor=False, fail=False, wrong_account=False, stuck_login=False, depth=None):
    html = HTML.replace('VISITOR', json.dumps('<p>Welcome. Use the Sign in link to access your account.</p>' if visitor else '')).replace('TARGET', json.dumps('<a href="/reports">Reports</a>' if reachable else ''))
    if wrong_account:
        html = html.replace('>sample-user</button>', '>another-user</button>')
    if stuck_login:
        html = html.replace("location.pathname==='/login'&&!auth", "location.pathname==='/login'").replace('Username or email<input', 'Unstated identity<input')
        html = html.replace("document.querySelector('form').onsubmit", "if(auth)root.insertAdjacentHTML('afterbegin','<header><button aria-label=\"Account menu\">sample-user</button></header>'); document.querySelector('form').onsubmit")
    if depth is not None:
        html = html.replace('<a href=\\"/reports\\">Reports</a>', '')
        chain = "const step=Number(location.pathname.match(/step-(\\d+)/)?.[1]||0); root.insertAdjacentHTML('beforeend',step===" + str(depth) + "?'<a href=\"/reports\">Reports</a>':'<a href=\"/step-'+(step+1)+'\">Step '+(step+1)+'</a>'); "
        html = html.replace("document.querySelector('button').onclick", chain + "document.querySelector('button').onclick")
    files = {
        'backend/package.json': json.dumps({'scripts': {'start': 'node server.js'}}),
        'backend/server.js': APP.replace('FAIL', 'true' if fail else 'false'),
        'frontend/dist/index.html': html,
    }
    for rel, value in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding='utf-8')
    return root

def run_fixture(requirement=REQUIREMENT, **kwargs):
    with tempfile.TemporaryDirectory(prefix='outsider-fixture-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = fixture(Path(tmp), **kwargs)
        before = sorted(str(p.relative_to(app)) for p in app.rglob('*') if p.is_file())
        result = ow.outsider_walk_check(requirement, app, time_limit=45, node_modules=NODE_MODULES)
        assert before == sorted(str(p.relative_to(app)) for p in app.rglob('*') if p.is_file()), 'probe wrote to source app'
        return result

@case
def reachable():
    findings, completed = run_fixture()
    assert completed and not findings, (completed, findings)

    graph_req = REQUIREMENT.replace('keyword: WHEN', 'keyword: THEN')
    assert run_fixture(requirement=graph_req, depth=3) == ([], True), 'third-click destination was missed'
    names = ow._plans(REQUIREMENT, ow._credentials(REQUIREMENT))[0]['targets']
    assert 'Reports' in names and 'new-record' not in names and 'Public only' not in names, names
    assert ow._navigation('The signed-in user cannot open "Private records".') == {}
    assert ow.outsider_walk_check(None, None, log=lambda _: 1/0) == ([], False)
    scoped = json.dumps({'description': 'The administration page has a "Billing" link.', 'scenarios': [
        {'steps': [{'content': 'Seed values: account `first-user`, email `first@example.test`, password `One-pass-123!`. Seed values: account `second-user`, email `second@example.test`, password `Two-pass-123!`. The user signs in as `first-user` and manages `second-user`.'}]}]})
    second = next(c for c in ow._credentials(scoped) if c['account'] == 'second-user')
    assert 'Billing' not in ow._plans(scoped, [second])[0]['targets'], 'description leaked to a different actor'

@case
def unreachable():
    graph_req = REQUIREMENT.replace('keyword: WHEN', 'keyword: THEN')
    findings, completed = run_fixture(requirement=graph_req, reachable=False)
    assert completed and len(findings) == 1, (completed, findings)
    assert '“Reports”' in findings[0]['problem'] and '3 clicks' in findings[0]['problem'], findings
    findings, completed = run_fixture(requirement=graph_req, depth=4)
    assert completed and len(findings) == 1 and '“Reports”' in findings[0]['problem'], (completed, findings)

@case
def visitor_wording():
    findings, completed = run_fixture(visitor=True)
    assert completed and len(findings) == 1, (completed, findings)
    assert 'visitor sign-in wording' in findings[0]['problem'], findings

@case
def failed_sign_in():
    for options in ({'fail': True}, {'wrong_account': True}, {'stuck_login': True}):
        findings, completed = run_fixture(**options)
        assert not completed and findings == [], (options, completed, findings)

FIRST_REQUIREMENT = REQUIREMENT.replace('opens "Account menu", and clicks the "Reports" link', 'clicks the "Reports" link')

@case
def zero_click_hit():
    findings, completed = run_fixture(requirement=FIRST_REQUIREMENT)
    assert completed and len(findings) == 1 and 'zero clicks' in findings[0]['problem'], (completed, findings)
    assert 'Open a seeded destination' in findings[0]['problem'] and 'Reports' in findings[0]['problem']

@case
def zero_click_miss():

    with tempfile.TemporaryDirectory(prefix='outsider-zero-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = fixture(Path(tmp))
        p = app/'frontend/dist/index.html'
        p.write_text(p.read_text(encoding='utf-8').replace('id="menu" hidden', 'id="menu"'), encoding='utf-8')
        assert ow.outsider_walk_check(FIRST_REQUIREMENT, app, time_limit=45, node_modules=NODE_MODULES) == ([], True)
        p.write_text(p.read_text(encoding='utf-8').replace('<span hidden>Reports</span>', '<a href="/reports">Reports</a>'), encoding='utf-8')
        assert ow.outsider_walk_check(FIRST_REQUIREMENT, app, time_limit=45, node_modules=NODE_MODULES) == ([], True)
    menu_req = FIRST_REQUIREMENT.replace('Visitors use', 'The account menu contains one link named "Reports". Visitors use')
    cred = ow._credentials(menu_req)[0]
    assert ow._first_targets(menu_req, cred)[0]['menu'], 'explicit menu placement was lost'
    menu_req = menu_req.replace('clicks the "Reports" link', 'opens "Account menu", clicks the "Reports" link')
    assert run_fixture(requirement=menu_req) == ([], True), 'the stated menu click should expose its target'
    cred = ow._credentials(FIRST_REQUIREMENT)[0]
    assert not ow._first_targets(FIRST_REQUIREMENT.replace('"Reports" link', '"A1" cell'), cred)
    for wording in ('cannot click', 'must not click', 'never clicks'):
        req = FIRST_REQUIREMENT.replace('clicks the "Reports" link', wording + ' the "Reports" link')
        assert not ow._first_targets(req, cred), wording
    req = FIRST_REQUIREMENT.replace('The user signs in as `sample-user`, clicks', 'The user is signed in as `sample-user` and is on the editor page, clicks')

    req = req.replace('keyword: WHEN\n    content: \'The user is signed', 'keyword: GIVEN\n    content: \'The user is signed')
    assert not ow._first_targets(req, cred)

EXIT_REQUIREMENT = '''description: >-
  The home page provides a "Ledger" link that opens the workbook editor page.
  The editor provides a "Home" link that returns to the home page.
'''

def run_exit(broken=False, missing=False, same_url=False):
    with tempfile.TemporaryDirectory(prefix='outsider-exit-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = fixture(Path(tmp))
        html = '''<!doctype html><html><body><script>
        const editor=location.pathname==='/ledger';
        document.write(editor?'<h1>Ledger</h1><a href="/ledger" aria-current="page">Ledger</a><a href="DEST">Home</a>':
          location.pathname==='/gone'?'<h1>Page not found</h1>':'<h1>Workbooks</h1><a href="/ledger">Ledger</a>');
        </script></body></html>'''.replace('DEST', '/ledger' if broken else '/gone' if missing else '/')
        if same_url:
            html = html.replace('<a href="/">Home</a>', '<a href="/ledger">Home</a>')
            html = html.replace('</script>', '''if(editor)document.querySelector('a:last-child').onclick=e=>{
                e.preventDefault();document.body.innerHTML='<h1>Workbooks</h1>'};</script>''')
        (app/'frontend/dist/index.html').write_text(html, encoding='utf-8')
        return ow.outsider_walk_check(EXIT_REQUIREMENT, app, time_limit=45, node_modules=NODE_MODULES)

@case
def exit_stays_on_page():
    findings, completed = run_exit(broken=True)
    assert completed and len(findings) == 1, (completed, findings)
    assert 'Home' in findings[0]['problem'] and 'unchanged' in findings[0]['problem'], findings
    findings, completed = run_exit(missing=True)
    assert completed and len(findings) == 1 and 'not-found' in findings[0]['problem'], (completed, findings)

@case
def correct_exit():
    assert run_exit() == ([], True)
    assert run_exit(same_url=True) == ([], True), 'same-URL navigation with a different heading is valid'
    rules = ow._anonymous_plan('The "Home" link opens the "Workbooks" page and shows a "Recent" tab.')['exits']
    assert [r['name'] for r in rules] == ['Home'], rules

@case
def creation_success_landing():
    requirement = '''description: >-
      Users create from the home page with a button with the accessible name "New record";
      its submit button is named "Create". After creation succeeds, the editor opens.
    scenarios:
    - name: Create a concrete record
      steps:
      - keyword: WHEN
        content: 'The user clicks "New record", enters `Budget` in "Record name", and clicks "Create".'
    '''
    requirement = requirement.replace('\n    ', '\n')
    for broken in (False, True):
        with tempfile.TemporaryDirectory(prefix='outsider-create-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
            app = fixture(Path(tmp))
            server = APP.replace('FAIL', 'false').replace("app.use(express.static", """
            let names=[];
            app.get('/api/records',(req,res)=>res.json(names));
            app.post('/api/records',(req,res)=>{names.push(req.body.name);res.json({ok:true})});
            app.use(express.static""")
            (app/'backend/server.js').write_text(server, encoding='utf-8')
            html = '''<!doctype html><html><body><script>
            async function render(){
              const names=await (await fetch('/api/records')).json();
              if(location.pathname==='/new'){
                document.body.innerHTML='<h1>New record</h1><label>Record name<input></label><button>Create</button>';
                document.querySelector('button').onclick=async()=>{
                  await fetch('/api/records',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.querySelector('input').value})});
                  location.href=DEST;
                };
              }else if(location.pathname==='/record'){
                document.body.innerHTML='<h1>'+names[0]+'</h1><div role="grid" aria-label="Record grid" style="height:50px;width:100px"></div>';
              }else{
                document.body.innerHTML='<h1>Records</h1><button>New record</button>'+names.map(n=>'<a href="/record">'+n+'</a>').join('');
                document.querySelector('button').onclick=()=>location.href='/new';
              }
            }render();</script></body></html>'''.replace('DEST', json.dumps('/' if broken else '/record'))
            (app/'frontend/dist/index.html').write_text(html, encoding='utf-8')
            findings, completed = ow.outsider_walk_check(requirement, app, time_limit=45, node_modules=NODE_MODULES)
            assert completed and len(findings) == int(broken), (broken, completed, findings)
            if broken:
                assert 'completed create flow' in findings[0]['problem'], findings

PATH_REQUIREMENT = REQUIREMENT.replace(
    'opens "Account menu", and clicks the "Reports" link',
    'opens "Account menu", clicks "Your spaces", opens `Alpha space`, '
    'opens `Record beta`, and select the tab "Settings"')

def run_path(broken='', fail=False, suffix=False, ambiguous=False, menu_outside=False,
             fail_bfs=False, no_record_header=False, requirement=PATH_REQUIREMENT,
             fetch_delay=None, preload=False, logs=None):
    with tempfile.TemporaryDirectory(prefix='outsider-path-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = fixture(Path(tmp), fail=fail)
        html = HTML.split(" root.innerHTML='<header>")[0] + r'''
 root.innerHTML='<header><button aria-label="Account menu" aria-expanded="false" aria-controls="menu">sample-user</button></header>'+
   '<section id="menu" hidden><a href="/spaces">Your spaces</a><a href="/account-settings">Settings</a></section>';
 if(location.pathname==='/spaces')root.insertAdjacentHTML('beforeend', BROKEN3?'<h1>Alpha space</h1>':'<a href="/space">Alpha space</a>');
 else if(location.pathname==='/space')root.insertAdjacentHTML('beforeend','<a href="/record">Record beta</a>');
 else if(location.pathname==='/record')root.insertAdjacentHTML('beforeend','<h1>Record beta</h1><a href="/space">Overview</a>'+ (BROKEN5?'':'<a role="tab" href="/record/settings">'+LABEL+'</a>'));
 else root.insertAdjacentHTML('beforeend','<h1>Workspace</h1>');
 if(AMBIGUOUS&&location.pathname==='/spaces')root.insertAdjacentHTML('beforeend','<button>Alpha space</button>');
 if(MENUOUTSIDE){document.querySelector('#menu a').remove();root.insertAdjacentHTML('beforeend','<a href="/spaces">Your spaces</a>')}
 document.querySelector('button').onclick=e=>{const m=document.getElementById('menu');m.hidden=!m.hidden;e.currentTarget.setAttribute('aria-expanded',String(!m.hidden))};
}
render();</script></body></html>'''

        html = html.replace('const me=await fetch(\'/api/me\');const auth=me.ok;',
                            'const auth=window.fixtureAuth??(window.fixtureAuth=(await fetch(\'/api/me\')).ok);')
        html = html.replace('render();</script>', '''
document.addEventListener('click',async e=>{
 const a=e.target.closest('a[href]');if(!a)return;
 e.preventDefault();
 PRELOAD
 history.pushState({},'',a.getAttribute('href'));render();
});
render();</script>''')
        html = html.replace('PRELOAD', "if(a.getAttribute('href')==='/spaces')window.listData=await (await fetch('/api/spaces')).json();" if preload else '')
        if fetch_delay is not None:
            server = APP.replace('FAIL', json.dumps(fail)).replace('app.use(express.static',
                "app.get('/api/spaces',(req,res)=>setTimeout(()=>res.json(['Alpha space'])," + str(fetch_delay) + "));\napp.use(express.static")
            (app/'backend/server.js').write_text(server, encoding='utf-8')
            html = html.replace("if(location.pathname==='/spaces')root.insertAdjacentHTML('beforeend', BROKEN3?'<h1>Alpha space</h1>':'<a href=\"/space\">Alpha space</a>');",
                "if(location.pathname==='/spaces'){const names=window.listData??await (await fetch('/api/spaces')).json();root.insertAdjacentHTML('beforeend','<a href=\"/space\">'+names[0]+'</a>')}")
        html = html.replace('BROKEN3', json.dumps(broken=='third')).replace('BROKEN5', json.dumps(broken=='settings'))
        html = html.replace('AMBIGUOUS', json.dumps(ambiguous)).replace('MENUOUTSIDE', json.dumps(menu_outside))
        html = html.replace('LABEL', json.dumps('Settings extra' if suffix else 'Settings   '))
        if no_record_header:
            html = html.replace("}\nrender();", " if(location.pathname==='/record'){document.querySelector('header').remove();document.querySelector('section').remove()}\n}\nrender();")
        if fail_bfs:
            html=html.replace(" if(location.pathname==='/spaces')", " if(location.pathname==='/later-error')throw Error('fixture BFS failure');\n root.insertAdjacentHTML('beforeend','<a href=\"/later-error\">Other destination</a>');\n if(location.pathname==='/spaces')")
        (app/'frontend/dist/index.html').write_text(html, encoding='utf-8')
        return ow.outsider_walk_check(requirement, app, time_limit=45, node_modules=NODE_MODULES,
                                      log=logs.append if logs is not None else print)

@case
def stated_path_works():
    findings, completed = run_path()
    assert completed and not findings, (completed, findings)

@case
def stated_path_broken_third():
    findings, completed = run_path(broken='third')
    assert completed and len(findings)==1, (completed, findings)
    problem=findings[0]['problem']
    assert 'step 3 “Alpha space”' in problem and '/spaces' in problem and 'Your spaces' in problem, findings
    assert '"Your spaces"' in findings[0]['fix'] and '"Alpha space"' in findings[0]['fix']

@case
def stated_path_other_context():
    findings, completed = run_path(broken='settings')
    assert completed and len(findings)==1, (completed, findings)
    assert 'step 5 “Settings”' in findings[0]['problem'] and '/record' in findings[0]['problem'], findings
    findings, completed = run_path(broken='settings', no_record_header=True)
    assert completed and len(findings)==1 and 'step 5 “Settings”' in findings[0]['problem'], (completed, findings)

@case
def stated_path_failed_sign_in():
    assert run_path(fail=True)==([], False)

@case
def stated_path_exact_and_uncertain():
    findings, completed = run_path(suffix=True)
    assert completed and len(findings)==1 and 'step 5 “Settings”' in findings[0]['problem'], (completed, findings)
    findings, _ = run_path(ambiguous=True)
    assert not findings, findings
    findings, completed = run_path(menu_outside=True)
    assert completed and len(findings)==1 and 'step 2 “Your spaces”' in findings[0]['problem'], (completed, findings)
    assert 'inside the opened “Account menu” menu' in findings[0]['problem'], findings

@case
def stated_path_parser_boundaries():
    def names(text):
        return [s['name'] for s in ow._path_steps(text)]
    assert names('opens “Account menu”, opens `First`, select the tab "Settings"')==['Account menu','First','Settings']
    for stop in ('enters `x` in "Name"', 'types "x"', 'fills "Name"', 'selects an option "One"',
                 'submits "Form"', 'creates "New"', 'adds "User"', 'removes "User"', 'deletes "User"',
                 'signs out', 'clicks "Save changes"', 'clicks "Create team"', 'clicks "Confirm"',
                 'opens `<record>`', 'opens "{name}"', 'opens "${name}"', 'opens unquoted',
                 'does not click "Forbidden"', 'should not click "Forbidden"', 'doesn\'t click "Forbidden"'):
        assert names('opens "First", '+stop+', opens "Later"')==['First'], stop
    cred=ow._credentials(PATH_REQUIREMENT)[0]
    paths=ow._stated_paths(PATH_REQUIREMENT,cred)
    assert len(paths)==1 and len(paths[0]['steps'])==5, paths
    assert not ow._stated_paths(PATH_REQUIREMENT.replace('signs in as `sample-user`','is not signed in as `sample-user`'),cred)
    assert not ow._stated_paths(PATH_REQUIREMENT,{**cred,'account':'another-user','email':'another@example.test'})

@case
def stated_path_survives_incomplete_bfs():
    findings, completed = run_path(broken='third', fail_bfs=True)
    assert not completed and len(findings)==1 and 'step 3 “Alpha space”' in findings[0]['problem'], (completed, findings)

@case
def stated_path_grouping():
    paths = []
    for index in range(12):
        paths.append({'key':str(index),'leaf':'leaf','scenario':f'scenario {index}',
                      'quote':'First → Missing','status':'missing','done':['First'],
                      'route':f'/page/{index}','missing':'Missing','visible':['First']})
    result = {'walks':[{'account':'sample-user','paths':paths + [paths[0]]}]}
    assert len(ow._path_findings(result))==8
    for p in paths:p['route']='/page'
    findings=ow._path_findings(result)
    assert len(findings)==1 and findings[0]['problem'].count('scenario 0;')==1, findings

@case
def stated_path_late_fetch():
    logs = []
    findings, completed = run_path(fetch_delay=700, logs=logs)
    assert completed and len(findings)==1, (completed, findings)

    assert findings[0]['problem'] == ('“Alpha space” is missing when a user looks right after opening /spaces and '
                                     'appears only later, once the page has finished loading.'), findings
    assert 'before the router navigates' in findings[0]['fix'] and 'instead of fetching after' in findings[0]['fix']
    assert any('first-look' in line and 'count=0;' in line and 'count=1;' in line for line in logs), logs
    findings, completed = run_path(fetch_delay=700, fail_bfs=True)
    assert not completed and len(findings)==1 and 'appears only later' in findings[0]['problem'], (completed, findings)

@case
def stated_path_immediate_list():
    logs = []
    assert run_path(logs=logs)==([], True)
    assert any('step 3 "Alpha space"' in line and 'count=1;' in line for line in logs), logs

    assert run_path(fetch_delay=700, preload=True)==([], True)

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
    print(f'{len(CASES)-failed-skipped}/{len(CASES)} passed; {skipped} skipped')
    raise SystemExit(bool(failed))
