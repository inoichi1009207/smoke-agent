from __future__ import annotations

import functools
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from agent import outsider, settings
from agent import outsider_walk as ow
from agent.when_plan import plans_for, render_plan, scenario_plan

SERIAL = True
CASES: list = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))

def unit_case(fn):
    CASES.append(fn)
    return fn

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

class setting:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.had = 'stated_region_scope' in settings.SETTINGS
        self.old = settings.SETTINGS.get('stated_region_scope')
        settings.SETTINGS['stated_region_scope'] = self.value

    def __exit__(self, *exc):
        if self.had:
            settings.SETTINGS['stated_region_scope'] = self.old
        else:
            settings.SETTINGS.pop('stated_region_scope', None)

S1 = ('The visitor opens the application home page, locates the visible link named “Sign in” in the main content, and '
      'clicks it. On the sign-in page, the visitor enters `evo-login-case` in the visibly labeled “Username or email” '
      'field, enters `Evo-Password-987!` in the visibly labeled “Password” field, and clicks the button named “Sign in”.')
S2 = ('The visitor opens the application home page, clicks the visible link named “Sign in”, enters `evo-login-case` in '
      'the visibly labeled “Username or email” field, enters `Evo-Password-987!` in the visibly labeled “Password” field, '
      'and clicks the button named “Sign in”.')
GIVEN = ('The visitor starts in a fresh unauthenticated browser session. The system has pre-provisioned a verified and '
         'available account with username `evo-login-case`, email `evo.login.case@evolution.test`, and password '
         '`Evo-Password-987!`.')
TREE = {'id': 'ROOT', 'name': 'Hub', 'description': 'The sign-in page has “Username or email”, “Password” and “Sign in”.',
        'children': [{'id': 'REQ-1-1-2', 'name': 'Sign In with an Existing Account', 'type': 'ATOMIC',
                      'description': 'The visitor signs in. The sign-in form contains “Username or email”, “Password”, and “Sign in”.',
                      'scenarios': [
                          {'name': 'REQ-1-1-2: Sign In with an Existing Account - Evolution Scenario 1',
                           'steps': [{'keyword': 'GIVEN', 'content': GIVEN}, {'keyword': 'WHEN', 'content': S1},
                                     {'keyword': 'THEN', 'content': 'The workspace shows `evo-login-case`.'}]},
                          {'name': 'REQ-1-1-2: Sign In with an Existing Account - Evolution Scenario 2',
                           'steps': [{'keyword': 'GIVEN', 'content': GIVEN}, {'keyword': 'WHEN', 'content': S2},
                                     {'keyword': 'THEN', 'content': 'The workspace shows `evo-login-case`.'}]}]}]}
REQUIREMENT = yaml.safe_dump(TREE, allow_unicode=True, sort_keys=False)

def plan_of(n):
    return [p for p in plans_for(REQUIREMENT, ['REQ-1-1-2']) if p['scenario'].endswith(f'Scenario {n}')][0]

def clicks(plan):
    return [(s['action'], s.get('target'), s.get('role'), (s.get('scope') or {}).get('name')) for s in plan['steps']
            if s['action'] in ('click', 'open')]

@unit_case
def region_scope_parsed():
    with setting(True):
        assert clicks(plan_of(1))[0] == ('click', 'Sign in', 'link', 'main content'), clicks(plan_of(1))
        assert 'in the "main content" region' in render_plan(plan_of(1))
        assert clicks(plan_of(2))[0] == ('click', 'Sign in', 'link', None), clicks(plan_of(2))

        steps = ow._plan_walk_steps(plan_of(1))[0]
        click = [s for s in steps if s['kind'] == 'click'][0]
        assert click['region'] == {'word': 'main content', 'role': 'main', 'selector': 'main,[role="main"]', 'element': '<main>'}
        assert 'region' not in [s for s in ow._plan_walk_steps(plan_of(2))[0] if s['kind'] == 'click'][0]

        sc = {'name': 'X - Scenario 1', 'steps': [{'keyword': 'WHEN', 'content': 'The user clicks “Notes” in the sidebar.'}]}
        assert clicks(scenario_plan(sc)) == [('click', 'Notes', None, 'sidebar')], clicks(scenario_plan(sc))
        sc = {'name': 'X - Scenario 1', 'steps': [{'keyword': 'WHEN', 'content':
              'The user locates the button named “Save” in the toolbar, and clicks it.'}]}
        assert clicks(scenario_plan(sc)) == [('click', 'Save', 'button', 'toolbar')], clicks(scenario_plan(sc))

        sc = {'name': 'X - Scenario 1', 'steps': [{'keyword': 'WHEN', 'content':
              'The user locates the link named “Code” in the repository header, and clicks it.'}]}
        assert clicks(scenario_plan(sc)) == [('click', 'Code', None, None)], clicks(scenario_plan(sc))

@unit_case
def setting_off_keeps_v162_plan():
    with setting(False):
        assert clicks(plan_of(1))[0] == ('click', 'Sign in', None, None), clicks(plan_of(1))
        assert 'region' not in render_plan(plan_of(1))
        assert all('region' not in s for s in ow._plan_walk_steps(plan_of(1))[0])

HEAD = 'const { test } = require("@playwright/test");\nconst { click, fill, see, region } = require("../e2e-support/outsider");\n\n'

def spec(entry_call, prelude=''):
    t1 = ('test("REQ-1-1-2: Sign In with an Existing Account - Evolution Scenario 1", async ({ page }) => {\n'
          '  await page.goto("/");\n' + prelude + f'  await {entry_call};\n'
          '  await fill(page, { role: "textbox", name: "Username or email" }, "evo-login-case");\n'
          '  await fill(page, { role: "textbox", name: "Password" }, "Evo-Password-987!");\n'
          '  await click(page, { role: "button", name: "Sign in" });\n'
          '  await see(page, "evo-login-case");\n});\n\n')
    t2 = ('test("REQ-1-1-2: Sign In with an Existing Account - Evolution Scenario 2", async ({ page }) => {\n'
          '  await page.goto("/");\n  await click(page, { role: "link", name: "Sign in" });\n'
          '  await fill(page, { role: "textbox", name: "Username or email" }, "evo-login-case");\n'
          '  await fill(page, { role: "textbox", name: "Password" }, "Evo-Password-987!");\n'
          '  await click(page, { role: "button", name: "Sign in" });\n});\n')
    return HEAD + t1 + t2

NODE = {'id': 'REQ-1-1-2', 'members': ['REQ-1-1-2'], 'requirement_text': REQUIREMENT}

class _Spec:
    def __init__(self, content):
        self.content = content
        self.path = 'backend/test-e2e/REQ-1-1-2.evo.spec.js'

def review(text):
    return [p for p in outsider.deterministic_review(dict(NODE), _Spec(text), 'REQ-1-1-2', log=lambda *_: None,
                                                     ledger_hint=False) if p.startswith('PROBLEM: REGION')]

@unit_case
def spec_unscoped_goes_to_author():
    with setting(True):
        text = spec('click(page, { role: "link", name: "Sign in" })')
        found = outsider.plan_region_conflicts(text, dict(NODE))
        assert len(found) == 1 and 'Evolution Scenario 1' in found[0] and 'main content' in found[0], found
        assert 'region(page, "main")' in found[0], found
        lines = review(text)
        assert len(lines) == 1 and lines[0].startswith('PROBLEM: REGION | STEP |'), lines
        assert outsider.certain_spec_defects(lines) == lines, 'a region problem must be a certain spec defect'

        assert len(outsider.plan_region_conflicts(spec('click(page, "Sign in")'), dict(NODE))) == 1

        other = outsider.plan_region_conflicts(spec('click(page, { role: "link", name: "Sign in" }, region(page, "banner"))'), dict(NODE))
        assert len(other) == 1 and 'inside the header' in other[0], other

@unit_case
def spec_scoped_passes():
    with setting(True):
        forms = [
            ('click(page, { role: "link", name: "Sign in" }, main)', '  const main = region(page, "main");\n'),
            ('click(page, { role: "link", name: "Sign in" }, region(page, "main"))', ''),
            ('click(page, { role: "link", name: "Sign in" }, content)', '  const content = page.getByRole("main");\n'),
            ('click(page, { role: "link", name: "Sign in" }, page.locator("main"))', ''),
            ('click(page, { role: "link", name: "Sign in" }, mainContent(page))', ''),
            ('page.getByRole("main").getByRole("link", { name: "Sign in" }).click()', ''),
        ]
        for call, prelude in forms:
            text = spec(call, prelude)
            assert outsider.plan_region_conflicts(text, dict(NODE)) == [], call
            assert review(text) == [], call

@unit_case
def spec_scope_per_test_body():
    t1 = ('test("REQ-1-1-2: Sign In with an Existing Account - Evolution Scenario 1", async ({ page }) => {\n'
          '  await page.goto("/");\n  const content = mainContent(page);\n'
          '  await click(page, { role: "link", name: "Sign in" }, content);\n'
          '  await fill(page, { role: "textbox", name: "Username or email" }, "evo-login-case");\n});\n\n')
    t2 = ('test("REQ-1-1-2: Sign In with an Existing Account - Evolution Scenario 2", async ({ page }) => {\n'
          '  await page.goto("/");\n  const content = region(page, "banner");\n'
          '  await click(page, { role: "link", name: "Sign in" }, content);\n});\n')
    with setting(True):
        assert outsider.plan_region_conflicts(HEAD + t1 + t2, dict(NODE)) == []
        assert outsider.plan_region_conflicts(HEAD + t2 + t1, dict(NODE)) == []
        assert review(HEAD + t1 + t2) == []

        own = t1.replace('mainContent(page)', 'region(page, "banner")')
        found = outsider.plan_region_conflicts(HEAD + own + t2, dict(NODE))
        assert len(found) == 1 and 'inside the header' in found[0], found

        twice = t1.replace('  const content = mainContent(page);\n',
                           '  const content = region(page, "banner");\n  {\n  const content = region(page, "main");\n')
        twice = twice.replace('content);\n  await fill', 'content);\n  }\n  await fill')
        assert outsider.plan_region_conflicts(HEAD + twice, dict(NODE)) == []

        t3 = t1.replace('name: "Sign in" }, content', 'name: label }').replace('  const content = mainContent(page);\n', '')
        t4 = t2.replace('  const content = region(page, "banner");\n', '  const label = "Sign in";\n')
        assert outsider.plan_region_conflicts(HEAD + t3 + t4, dict(NODE)) == []
        found = outsider.plan_region_conflicts(HEAD + 'const label = "Sign in";\n' + t3, dict(NODE))
        assert len(found) == 1 and 'on the whole page' in found[0], found

@unit_case
def spec_gate_setting_off():
    with setting(False):
        assert review(spec('click(page, { role: "link", name: "Sign in" })')) == []

REVOKE = {'id': 'ROOT', 'name': 'Hub', 'description': 'The sign-in form has “Username or email”, “Password” and “Sign in”.',
          'children': [{'id': 'REQ-1-4', 'name': 'Manage Active Browser Sessions', 'type': 'ATOMIC', 'description': 'Sessions.',
                        'scenarios': [{'name': 'REQ-1-4: Manage Active Browser Sessions - Scenario 3', 'steps': [
                            {'keyword': 'GIVEN', 'content': 'Two active browser sessions.'},
                            {'keyword': 'WHEN', 'content': 'The visitor revokes one session from the other and then reloads the revoked browser.'},
                            {'keyword': 'THEN', 'content': 'The revoked browser is redirected to the sign-in page and cannot access the protected workspace.'}]},
                            {'name': 'REQ-1-4: Manage Active Browser Sessions - Scenario 4', 'steps': [
                            {'keyword': 'WHEN', 'content': 'The visitor signs out.'},
                            {'keyword': 'THEN', 'content': 'The page displays the “Sign in” link.'}]}]}]}
RNODE = {'id': 'REQ-1-4', 'members': ['REQ-1-4'], 'requirement_text': yaml.safe_dump(REVOKE, allow_unicode=True, sort_keys=False)}

def revoke_spec(check, n=3):
    return (HEAD + f'test("REQ-1-4: Manage Active Browser Sessions - Scenario {n}", async ({{ page }}) => {{\n'
            '  await click(page, { role: "button", name: "Revoke session" });\n  await page2.reload();\n'
            f'  await {check};\n}});\n')

@unit_case
def signin_page_by_words_goes_to_author():
    found = outsider.signin_page_by_words(revoke_spec('see(page2, "Sign in")'), dict(RNODE))
    assert len(found) == 1 and 'Scenario 3' in found[0] and 'name: "Username or email"' in found[0], found
    assert len(outsider.signin_page_by_words(revoke_spec('see(page2, { role: "link", name: "Sign in" })'), dict(RNODE))) == 1
    lines = [p for p in outsider.deterministic_review(dict(RNODE), _Spec(revoke_spec('see(page2, "Sign in")')), 'REQ-1-4',
                                                      log=lambda *_: None, ledger_hint=False) if p.startswith('PROBLEM: SIGN-IN PAGE')]
    assert len(lines) == 1, lines

    for check in ('see(page2, { role: "textbox", name: "Username or email" })', 'expect(page2).toHaveURL(/signin/)'):
        assert outsider.signin_page_by_words(revoke_spec(check), dict(RNODE)) == [], check
    assert outsider.signin_page_by_words(revoke_spec('see(page2, "Sign in")', n=4), dict(RNODE)) == []
    old = settings.SETTINGS.get('signin_page_by_form')
    settings.SETTINGS['signin_page_by_form'] = False
    try:
        assert [p for p in outsider.deterministic_review(dict(RNODE), _Spec(revoke_spec('see(page2, "Sign in")')), 'REQ-1-4',
                                                         log=lambda *_: None, ledger_hint=False) if 'SIGN-IN PAGE' in p] == []
    finally:
        if old is None:
            settings.SETTINGS.pop('signin_page_by_form', None)
        else:
            settings.SETTINGS['signin_page_by_form'] = old

def revoke_spec2(before, after):
    return (HEAD + 'test("REQ-1-4: Manage Active Browser Sessions - Scenario 3", async ({ page }) => {\n'
            f'{before}  await click(page, {{ role: "button", name: "Revoke session" }});\n  await page2.reload();\n'
            f'{after}  await see(page2, "Sign in");\n}});\n')

@unit_case
def signin_page_evidence_after_reload_only():
    def found(before='', after=''):
        return outsider.signin_page_by_words(revoke_spec2(before, after), dict(RNODE))
    early_form = '  await see(page2, { role: "textbox", name: "Username or email" });\n'
    other_browser = '  await see(page, { role: "textbox", name: "Password" });\n'
    other_url = '  await expect(page).toHaveURL(/settings/);\n'
    for before, after in ((early_form, ''), ('', other_browser), ('', other_url), (other_url, '')):
        assert len(found(before, after)) == 1, (before, after)
    for after in ('  await see(page2, { role: "textbox", name: "Username or email" });\n',
                  '  await expect(page2.getByLabel("Password")).toBeVisible();\n',
                  '  await expect(page2).toHaveURL(/sign-?in/);\n',
                  '  await expect(page2)\n    .toHaveURL(/sign-?in/);\n'):
        assert found('', after) == [], after

    text = revoke_spec2('', '').replace('  await see(page2, "Sign in");\n',
                                        '  await see(page2, "Sign in");\n  await see(page2, { role: "textbox", name: "Password" });\n')
    assert outsider.signin_page_by_words(text, dict(RNODE)) == []

    no_load = revoke_spec2(early_form, '').replace('  await page2.reload();\n', '')
    assert outsider.signin_page_by_words(no_load, dict(RNODE)) == []

@unit_case
def signin_page_prompt_rule():
    import subprocess
    rule = 'is checked by the sign-in FORM itself'
    assert rule in outsider.SYSTEM_PROMPT
    code = ('import sys; sys.path.insert(0, %r); from agent import settings; settings.SETTINGS["signin_page_by_form"] = False; '
            'from agent import outsider; print(%r in outsider.SYSTEM_PROMPT, outsider._SIGNIN_PAGE_ANCHOR in outsider.SYSTEM_PROMPT)'
            % (str(ROOT), rule))
    out = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=120).stdout.split()
    assert out == ['False', 'True'], out

APP = r'''
const express=require('express'),path=require('path');
const app=express();
app.use(express.static(path.join(__dirname,'../frontend/dist')));
app.use((req,res)=>res.sendFile(path.join(__dirname,'../frontend/dist/index.html')));
app.listen(Number(process.env.PORT),'127.0.0.1');
'''

HTML = r'''<!doctype html><html><head><title>Fixture</title></head><body><div id="app"></div><script>
const CFG=__CFG__;
const go=href=>{history.pushState({},'',href);render()};
document.addEventListener('click',e=>{const a=e.target.closest('a[href^="/"]');if(a){e.preventDefault();go(a.getAttribute('href'))}});
window.onpopstate=render;
function render(){
 const root=document.getElementById('app');const p=location.pathname;
 if(p==='/signin'){root.innerHTML='<main><h1>Sign in</h1><form><label>Username or email<input name="u"></label><label>Password<input name="p" type="password"></label><button>Sign in</button></form></main>';return}
 const link='<a href="/signin">Sign in</a>';
 if(CFG.place==='header')root.innerHTML='<header>'+link+'</header><main><h1>Home</h1><p>Welcome</p></main>';
 else if(CFG.place==='main')root.innerHTML='<header><a href="/">Hub</a></header><main><h1>Home</h1>'+link+'</main>';
 else if(CFG.place==='nolandmark')root.innerHTML='<div class="top">'+link+'</div><div class="content"><h1>Home</h1></div>';
 else if(CFG.place==='nested')root.innerHTML='<main><header>'+link+'</header><h1>Home</h1></main>';
}
render();</script></body></html>'''

def walk(place, on=True):
    with setting(on), tempfile.TemporaryDirectory(prefix='v163b-region-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = Path(tmp) / 'app'
        files = {'backend/package.json': json.dumps({'scripts': {'start': 'node server.js'}}), 'backend/server.js': APP,
                 'frontend/dist/index.html': HTML.replace('__CFG__', json.dumps({'place': place}))}
        for rel, value in files.items():
            p = app / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value, encoding='utf-8')
        logs: list = []
        report = ow.plan_walk_report(REQUIREMENT, app, time_limit=120, log=logs.append, node_modules=NODE_MODULES)
        findings, completed = ow.plan_walk_check(REQUIREMENT, app, time_limit=120, log=logs.append, node_modules=NODE_MODULES)
        assert report['completed'] and completed, logs[-5:]
        rows = {r['scenario'].split(' - ')[-1]: r for r in report['scenarios']}
        return rows, findings

def outcomes(row):
    return [f['outcome'] for f in row['findings']]

@browser_case
def walk_header_only_is_a_finding():
    rows, findings = walk('header')
    assert outcomes(rows['Evolution Scenario 1']) == ['outside-region'], rows['Evolution Scenario 1']
    assert outcomes(rows['Evolution Scenario 2']) == [], rows['Evolution Scenario 2']
    assert len(findings) == 1 and '<main>' in findings[0]['problem'] and 'main content' in findings[0]['problem'], findings
    assert set(findings[0]) == {'where', 'requirement', 'problem', 'fix'}, findings

@browser_case
def walk_inside_main_is_clean():
    rows, findings = walk('main')
    assert rows['Evolution Scenario 1']['status'] == 'clean', rows['Evolution Scenario 1']
    assert findings == [], findings

@browser_case
def walk_no_landmark_is_a_finding():
    rows, findings = walk('nolandmark')
    assert outcomes(rows['Evolution Scenario 1']) == ['no-landmark'], rows['Evolution Scenario 1']
    assert len(findings) == 1, findings
    assert 'the page has no <main> landmark; the requirement names the main content' in findings[0]['problem'], findings

@browser_case
def walk_nested_header_in_main_passes():
    rows, findings = walk('nested')
    assert rows['Evolution Scenario 1']['status'] == 'clean', rows['Evolution Scenario 1']
    assert findings == [], findings

@browser_case
def walk_setting_off_unchanged():
    rows, findings = walk('header', on=False)
    assert rows['Evolution Scenario 1']['status'] == 'clean', rows['Evolution Scenario 1']
    assert findings == [], findings

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
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v163b region cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
