from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from agent import codegen, outsider, settings

SERIAL = True
CASES: list = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))
SPEC = 'backend/test-e2e/REQ-9.spec.js'

def unit_case(fn):
    CASES.append(fn)
    return fn

def browser_case(fn):
    @functools.wraps(fn)
    def checked():
        if not (Path(NODE_MODULES) / '@playwright' / 'test').is_dir() or not shutil.which('node'):
            print('SKIP', fn.__name__, ': ARC_TX_NODE_MODULES has no @playwright/test (or no node)')
            return False
        fn()
        return True
    CASES.append(checked)
    return checked

def before_js() -> str:
    return outsider.HELPER_JS.replace(outsider._ARG_CHECK_DEFS, '', 1)

@unit_case
def setting_on_appends_the_checks_only():
    assert outsider.ARG_CHECK_SETTING == 'spec_helper_arg_check'
    assert outsider.ARG_CHECK_APPLIED and outsider.STRICT_TARGETS_APPLIED and outsider.CHOICE_POPUP_APPLIED
    h, old = outsider.HELPER_JS, before_js()
    assert old == outsider.choice_popup_helper_js(outsider.strict_helper_js(outsider.V167_HELPER_JS))
    assert h == outsider.arg_check_helper_js(old) and h.count('const ARG_SLOTS = ') == 1
    assert outsider.arg_check_helper_js(h) == h
    other = old.replace('locateAll, fill,', 'locateAll,  fill,', 1)
    assert other != old and outsider.arg_check_helper_js(other) == other

    exports = lambda js: re.search(r'module\.exports\s*=\s*\{([^}]*)\}', js).group(1)
    sigs = lambda js: re.findall(r'^(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', js, re.M)
    assert exports(h) == exports(old) == exports(outsider.V167_HELPER_JS) and sigs(h) == sigs(old)

    slots = set(re.findall(r'(\w+): \[\[', h[h.index('const ARG_SLOTS = '):h.index('const argIsScope')]))
    exported = {x.strip() for x in exports(h).split(',')}
    assert slots and slots <= exported and not slots & {'test', 'expect', 'inventory', 'allowClipboard'}, slots - exported

@unit_case
def setting_off_is_byte_for_byte():
    def helper(**switches):
        sets = '; '.join(f'settings.SETTINGS[{k!r}] = {v!r}' for k, v in switches.items())
        code = ('import sys; sys.path.insert(0, %r); from agent import settings; %s; from agent import outsider as o; '
                'print(o.HELPER_JS == o.choice_popup_helper_js(o.strict_helper_js(o.V167_HELPER_JS)), '
                'o.HELPER_JS == o.V167_HELPER_JS, o.ARG_CHECK_APPLIED, o.STRICT_TARGETS_APPLIED, o.CHOICE_POPUP_APPLIED)'
                % (str(ROOT), sets or 'pass'))
        done = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=120)
        return done.stdout.split() or [done.stderr[-400:]]
    assert helper(spec_helper_arg_check=False) == ['True', 'False', 'False', 'True', 'True'], helper(spec_helper_arg_check=False)
    assert helper() == ['False', 'False', 'True', 'True', 'True'], helper()
    off = helper(spec_helper_arg_check=False, spec_strict_targets=False, spec_choice_popup=False)
    assert off == ['False', 'True', 'False', 'False', 'False'], off

    only = helper(spec_strict_targets=False, spec_choice_popup=False)
    assert only == ['False', 'False', 'True', 'False', 'False'], only

NODE_DRIVER = r"""
const H = require(process.argv[2]);
const scope = { getByRole() {}, locator() {} };
const say = (e) => String((e && e.message) || e).split('\n')[0];
(async () => {
  const out = {};
  const run = async (key, f) => { try { const r = f(); out[key] = (r && typeof r.then === 'function') ? (await r, 'OK') : 'OK'; }
    catch (e) { out[key] = 'ERR ' + say(e); } };
  const sync = (key, f) => { try { f(); out[key + '_sync'] = 'returned'; } catch (e) { out[key + '_sync'] = 'threw ' + say(e); } };
  await run('promise_page', () => H.click(Promise.resolve(scope), 'Save'));
  await run('string_page', () => H.see('page', 'Hello'));
  await run('undefined_page', () => H.fill(undefined, 'Name', 'x'));
  await run('string_scope', () => H.see(scope, 'Hello', 'Formula bar'));
  await run('promise_scope', () => H.click(scope, 'Save', Promise.resolve(scope)));
  await run('object_scope', () => H.click(scope, 'Save', { timeout: 500 }));
  await run('fill_scope', () => H.fill(scope, 'Name', 'Bob', 'dialog'));
  await run('locator_name', () => H.click(scope, scope));
  await run('promise_name', () => H.see(scope, ['Hello', Promise.resolve('x')]));
  await run('item_page', () => H.item('page', 'Row 1'));
  await run('region_page', () => H.region(Promise.resolve(scope), 'dialog'));
  await run('locate_scope', () => H.locate(scope, 'Total', 'main'));
  await run('drag_to', () => H.drag(scope, 'A1', scope));
  // an async helper rejects (a .catch() of the spec still catches it), a plain one throws
  sync('click', () => { H.click('page', 'Save').catch(() => {}); });
  sync('locate', () => H.locate('page', 'Save'));
  console.log('V168W' + JSON.stringify(out));
})();
"""

@browser_case
def helper_says_spec_defect_without_a_browser():
    with tempfile.TemporaryDirectory(prefix='v168w-node-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        (Path(tmp) / 'outsider.js').write_text(outsider.HELPER_JS, encoding='utf-8')
        (Path(tmp) / 'driver.cjs').write_text(NODE_DRIVER, encoding='utf-8')
        done = subprocess.run(['node', 'driver.cjs', str(Path(tmp) / 'outsider.js')], cwd=tmp, capture_output=True, text=True,
                              timeout=120, encoding='utf-8', errors='replace', env={**os.environ, 'NODE_PATH': NODE_MODULES})
    line = next((x for x in (done.stdout or '').splitlines() if x.startswith('V168W{')), '')
    assert line, ((done.stdout or '')[-800:], (done.stderr or '')[-800:])
    got = json.loads(line[5:])
    first = 'as its first argument; pass the page or a locator from region()/item() (await them)'
    expect = {
        'promise_page': 'ERR SPEC DEFECT: click() got a Promise (a call that was not awaited) ' + first,
        'string_page': 'ERR SPEC DEFECT: see() got the string "page" ' + first,
        'undefined_page': 'ERR SPEC DEFECT: fill() got undefined ' + first,
        'string_scope': 'ERR SPEC DEFECT: see() got the string "Formula bar" as its scope argument (argument 3)',
        'promise_scope': 'ERR SPEC DEFECT: click() got a Promise (a call that was not awaited) as its scope argument (argument 3)',
        'object_scope': 'ERR SPEC DEFECT: click() got an object with keys timeout as its scope argument (argument 3)',
        'fill_scope': 'ERR SPEC DEFECT: fill() got the string "dialog" as its scope argument (argument 4)',
        'locator_name': 'ERR SPEC DEFECT: click() got a page or locator as the name of what it looks for (argument 2)',
        'promise_name': 'ERR SPEC DEFECT: see() got a Promise (a call that was not awaited) as the name of what it looks for',
        'item_page': 'ERR SPEC DEFECT: item() got the string "page" ' + first,
        'region_page': 'ERR SPEC DEFECT: region() got a Promise (a call that was not awaited) ' + first,
        'locate_scope': 'ERR SPEC DEFECT: locate() got the string "main" as its scope argument (argument 3)',
        'drag_to': 'ERR SPEC DEFECT: drag() got a page or locator as the name of what it looks for (argument 3)',
        'click_sync': 'returned',
        'locate_sync': 'threw SPEC DEFECT: locate() got the string "page" ' + first,
    }
    for key, want in expect.items():
        assert got.get(key, '').startswith(want), (key, got.get(key))
    assert 'a scope goes in the scope argument after it' in got['locator_name'], got['locator_name']

def thrown(error: str, frames: list[str], spec_text: str = 'test("x", async ({ page }) => {});\n') -> list[str]:
    output = '\n'.join(['  1) test-e2e/REQ-9.spec.js:5:1 › REQ-9: one', '', '    ' + error, ''] +
                       ['        ' + f for f in frames] + ['', '  2) test-e2e/REQ-9.spec.js:30:1 › REQ-9: two', '    Error: other'])
    return codegen.spec_thrown_errors(output, {SPEC: spec_text})

SPEC_FRAME = 'at D:\\w\\backend\\test-e2e\\REQ-9.spec.js:12:51'
HELPER_FRAME = 'at see (D:\\w\\backend\\e2e-support\\outsider.js:1004:21)'
APP_FRAME = 'at coerceValue (D:\\w\\backend\\src\\cells.js:40:11)'
BUNDLE_FRAME = 'at http://localhost:5173/assets/index-4f2a9c.js:1:20345'
CHAINED = 'TypeError: page.context(...).browser(...).newContext(...).newPage is not a function'

@unit_case
def helper_spec_defect_routes_to_the_author():

    message = ('Error: SPEC DEFECT: see() got the string "Formula bar" as its scope argument (argument 3); pass a locator '
               'from region()/item() (await them), or nothing to look in the whole page')
    found = thrown(message, ['at see (D:\\w\\backend\\e2e-support\\outsider.js:1400:9)', SPEC_FRAME])
    assert found == ['REQ-9.spec.js: ' + message], found

    assert thrown('TypeError: root.getByText is not a function', [HELPER_FRAME, SPEC_FRAME]) == []

@unit_case
def chained_call_routes_to_the_author():
    assert thrown(CHAINED, [SPEC_FRAME]) == ['REQ-9.spec.js: ' + CHAINED]
    for line in ('TypeError: page.locator(...).textContent(...).trim is not a function',
                 'TypeError: page.getByRole(...).first(...).fillIn is not a function',
                 'TypeError: page.keyboard.press(...).then2 is not a function',
                 'TypeError: expect(...).not.toBeVisble(...).x is not a function'):
        assert thrown(line, [SPEC_FRAME]) == ['REQ-9.spec.js: ' + line], line

    settings.SETTINGS[codegen.CHAINED_CALL_SETTING] = False
    try:
        assert thrown(CHAINED, [SPEC_FRAME]) == []
        assert thrown('TypeError: page.goTo is not a function', [SPEC_FRAME]) == ['REQ-9.spec.js: TypeError: page.goTo is not a function']
    finally:
        del settings.SETTINGS[codegen.CHAINED_CALL_SETTING]
    assert codegen.CHAINED_CALL_SETTING == 'spec_chained_call_defect'

@unit_case
def application_failures_stay_with_the_implementer():

    for frames in ([BUNDLE_FRAME, SPEC_FRAME], [SPEC_FRAME]):
        for line in ('TypeError: o.map is not a function', 'TypeError: c.map is not a function',
                     'TypeError: o.map(...).filter is not a function'):
            assert thrown(line, frames) == [], (line, frames)

    assert thrown('TypeError: coerceValue is not a function', [APP_FRAME, SPEC_FRAME]) == []

    for line in ('TypeError: res.body.find(...).name.toUpperCase is not a function',
                 'TypeError: rows.find(...).click is not a function',
                 'TypeError: (intermediate value).map(...).join is not a function',
                 'TypeError: context.newPage(...).goto is not a function',
                 'TypeError: mypage.locator(...).click2 is not a function',
                 'TypeError: page.context is not a function(...)'):
        assert thrown(line, [SPEC_FRAME]) == [], line

    assert thrown('TypeError: page.keyboard.tap is not a function', [SPEC_FRAME]) == []

    assert thrown(CHAINED, [HELPER_FRAME, SPEC_FRAME]) == [] and thrown(CHAINED, [APP_FRAME, SPEC_FRAME]) == []

    binding = 'test("x", async ({ page }) => { const newPage = await pick(); });\n'
    assert thrown(CHAINED, [SPEC_FRAME], binding) == []

    rows = 'TypeError: page.rows(...).map is not a function'
    for own in ("const page = require('../src/page');\ntest('x', async () => { page.rows().map((x) => x); });\n",
                "import page from '../src/page';\n", "const { page } = require('../src/pages');\n",
                "let page = new AppPage();\n"):
        assert codegen.page_from_application(own), own
        assert thrown(rows, [SPEC_FRAME], own) == [], own
        assert thrown(CHAINED, [SPEC_FRAME], own) == [], own

    for fixture in ('test("x", async ({ page }) => {});\n',
                    'test("x", async ({ page }) => { /* const page = require("x") */ const s = "import page from";\n});\n'):
        assert not codegen.page_from_application(fixture), fixture
        assert thrown(CHAINED, [SPEC_FRAME], fixture) == ['REQ-9.spec.js: ' + CHAINED], fixture

REAL_SPEC = r"""const { test, see, click, region, item } = require('../e2e-support/outsider');
const HTML = '<main><h1>Hello</h1><section aria-label="Formula bar"><button>Save</button></section></main>';

test('promise scope', async ({ page }) => {
  await page.setContent(HTML);
  const bar = (async () => region(page, 'region', 'Formula bar'))();
  await click(page, 'Save', bar);
});

test('string scope', async ({ page }) => {
  await page.setContent(HTML);
  await see(page, 'Hello', 'Formula bar');
});

test('promise page', async ({ page }) => {
  await page.setContent(HTML);
  await see(Promise.resolve(page), 'Hello');
});

test('valid calls', async ({ page }) => {
  await page.setContent(HTML);
  await click(page, 'Save', region(page, 'region', 'Formula bar'));
  await see(page, 'Hello', region(page, 'main'));
  await click(page, 'Nothing', 'Formula bar').catch(() => {});
});

test('chained call', async ({ page }) => {
  await page.setContent(HTML);
  const other = await page.context().browser().newContext().newPage();
  await other.close();
});
"""

def real_run(js: str) -> tuple[dict, str]:
    cli = Path(NODE_MODULES) / '@playwright' / 'test' / 'cli.js'
    with tempfile.TemporaryDirectory(prefix='v168w-real-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        root = Path(tmp)
        (root / 'test-e2e').mkdir()
        (root / 'e2e-support').mkdir()
        (root / 'e2e-support' / 'outsider.js').write_text(js, encoding='utf-8')
        (root / 'test-e2e' / 'REQ-9.spec.js').write_text(REAL_SPEC, encoding='utf-8')
        (root / 'playwright.config.js').write_text(
            "module.exports = { testDir: './test-e2e', timeout: 20000, workers: 1, reporter: [['line'], "
            "['json', { outputFile: 'report.json' }]] };\n", encoding='utf-8')
        done = subprocess.run(['node', str(cli), 'test'], cwd=tmp, capture_output=True, text=True, timeout=300,
                              encoding='utf-8', errors='replace', env={**os.environ, 'NODE_PATH': NODE_MODULES})
        report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    status = {}
    for suite in report['suites']:
        for spec in suite['specs']:
            result = spec['tests'][0]['results'][0]
            status[spec['title']] = (result['status'], ((result.get('error') or {}).get('message', '') or '').split('\n')[0][:300])
    return status, done.stdout or ''

@browser_case
def real_run_routes_misuse_to_the_test_author():
    status, output = real_run(outsider.HELPER_JS)
    assert status['valid calls'][0] == 'passed', status
    for title, want in (('promise scope', 'click() got a Promise (a call that was not awaited) as its scope argument'),
                        ('string scope', 'see() got the string "Formula bar" as its scope argument'),
                        ('promise page', 'see() got a Promise (a call that was not awaited) as its first argument')):
        assert status[title][0] == 'failed' and status[title][1].startswith('Error: SPEC DEFECT: ' + want), (title, status[title])
    assert status['chained call'][0] == 'failed' and status['chained call'][1] == CHAINED, status['chained call']
    found = codegen.spec_thrown_errors(output, {SPEC: REAL_SPEC})
    assert len(found) == 4 and all(x.startswith('REQ-9.spec.js: ') for x in found), (found, output[-2000:])
    assert sum('SPEC DEFECT' in x for x in found) == 3 and any(x.endswith(CHAINED) for x in found), found

    status, output = real_run(before_js())
    assert status['valid calls'][0] == 'passed', status
    for title in ('promise scope', 'string scope', 'promise page'):
        assert status[title][0] == 'failed' and status[title][1].startswith('TypeError: '), (title, status[title])
        assert 'SPEC DEFECT' not in status[title][1], status[title]
    found = codegen.spec_thrown_errors(output, {SPEC: REAL_SPEC})
    assert len(found) == 1 and found[0].endswith(CHAINED), found

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
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v168w misuse cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
