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
from agent import outsider

SERIAL = True
CASES: list = []
NODE_MODULES = os.environ.get('ARC_TX_NODE_MODULES', str(ROOT / 'template' / 'backend' / 'node_modules'))
AMBIGUOUS = 'one of them is ambiguous'

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

@unit_case
def setting_on_patches_the_helper():
    assert outsider.STRICT_TARGETS_SETTING == 'spec_strict_targets'
    assert outsider.STRICT_TARGETS_APPLIED, 'an anchor of the v167 helper was not found: the strict check is not in'
    h = outsider.HELPER_JS
    assert h.count('async function strictTarget(') == 1
    assert h.count("strictTarget(page, root, name, target, STRICT_CLICK_ROLES, 'click', within)") == 1
    assert h.count("strictTarget(page, root, name, item, STRICT_FIELD_ROLES, 'fill', within)") == 2
    assert h.count("STRICT_CHECK_ROLES, 'check', within)") == 1 and h.count("STRICT_BOX_ROLES, 'choose', within)") == 1

    exports = lambda js: re.search(r'module\.exports = \{([^}]*)\}', js).group(1)
    assert exports(h) == exports(outsider.V167_HELPER_JS)
    sigs = lambda js: re.findall(r'^(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', js, re.M)
    added = ('strictCount', 'strictExemptSpec', 'strictTarget', 'choicePopup')
    assert [s for s in sigs(h) if s[0] not in added] == sigs(outsider.V167_HELPER_JS)
    assert 'function locate(page, name, within) { return locateAll(page, name, within).first(); }' in h

    assert outsider.CHOICE_POPUP_APPLIED and h == outsider.arg_check_helper_js(outsider.choice_popup_helper_js(outsider.strict_helper_js(outsider.V167_HELPER_JS)))
    assert h == outsider.arg_check_helper_js(outsider.strict_helper_js(outsider.choice_popup_helper_js(outsider.V167_HELPER_JS)))

    h = outsider.strict_helper_js(outsider.V167_HELPER_JS)
    start, end = h.index('// v168b (spec_strict_targets)'), h.index('// Click something the requirement names.')
    stripped = h[:start] + h[end:]
    stripped = '\n'.join(line for line in stripped.split('\n') if 'await strictTarget(' not in line)
    assert stripped == outsider.V167_HELPER_JS

    for words in ('strict mode', 'resolved to'):
        assert words not in h[start:end], words

@unit_case
def setting_off_is_v167_byte_for_byte():
    def helper(strict, popup):
        code = ('import sys; sys.path.insert(0, %r); from agent import settings; settings.SETTINGS["spec_strict_targets"] = %r; '
                'settings.SETTINGS["spec_choice_popup"] = %r; settings.SETTINGS["spec_helper_arg_check"] = False; '
                'from agent import outsider as o; '
                'print(o.HELPER_JS == o.V167_HELPER_JS, o.HELPER_JS == o.strict_helper_js(o.V167_HELPER_JS), '
                'o.HELPER_JS == o.choice_popup_helper_js(o.V167_HELPER_JS), o.STRICT_TARGETS_APPLIED, o.CHOICE_POPUP_APPLIED)'
                % (str(ROOT), strict, popup))
        return subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=120).stdout.split()
    assert helper(False, False) == ['True', 'False', 'False', 'False', 'False'], helper(False, False)
    assert helper(True, False) == ['False', 'True', 'False', 'True', 'False'], helper(True, False)
    assert helper(False, True) == ['False', 'False', 'True', 'False', 'True'], helper(False, True)

@unit_case
def inherited_manifest_written_once():
    with tempfile.TemporaryDirectory(prefix='v168b-manifest-') as tmp:
        root = Path(tmp)
        (root / 'backend' / 'test-e2e').mkdir(parents=True)
        for name in ('REQ-1.spec.js', 'REQ-2.spec.js'):
            (root / 'backend' / 'test-e2e' / name).write_text('// inherited', encoding='utf-8')
        logs: list = []
        outsider.ensure_helper(root, log=logs.append)
        manifest = root / outsider.STRICT_EXEMPT_PATH
        assert json.loads(manifest.read_text(encoding='utf-8'))['specs'] == ['REQ-1.spec.js', 'REQ-2.spec.js']
        assert (root / outsider.HELPER_PATH).read_text(encoding='utf-8') == outsider.HELPER_JS

        (root / 'backend' / 'test-e2e' / 'REQ-3.spec.js').write_text('// this run', encoding='utf-8')
        assert outsider.write_strict_exempt(root) is None
        outsider.ensure_helper(root, log=logs.append)
        assert json.loads(manifest.read_text(encoding='utf-8'))['specs'] == ['REQ-1.spec.js', 'REQ-2.spec.js']
        assert any('2 inherited spec file(s)' in line for line in logs), logs

    with tempfile.TemporaryDirectory(prefix='v168b-manifest-') as tmp:
        root = Path(tmp)
        (root / 'backend' / 'test-e2e').mkdir(parents=True)
        (root / 'backend' / 'test-e2e' / 'REQ-1.spec.js').write_text('// this run, before the restart', encoding='utf-8')
        (root / '.arc').mkdir()
        (root / outsider.RESUME_STATE).write_text('{}', encoding='utf-8')
        logs = []
        assert outsider.write_strict_exempt(root, log=logs.append) is None
        assert json.loads((root / outsider.STRICT_EXEMPT_PATH).read_text(encoding='utf-8'))['off'] is True
        assert any('a resume found no manifest' in line for line in logs), logs

    with tempfile.TemporaryDirectory(prefix='v168b-manifest-') as tmp:
        root = Path(tmp)
        (root / 'backend' / 'test-e2e').mkdir(parents=True)
        (root / outsider.STRICT_EXEMPT_PATH).parent.mkdir(parents=True)
        real, logs = outsider.write_text, []
        def refuse(*_a, **_k):
            raise OSError('disk full')
        outsider.write_text = refuse
        try:
            assert outsider.write_strict_exempt(root, log=logs.append) is None
        finally:
            outsider.write_text = real
        assert not (root / outsider.STRICT_EXEMPT_PATH).exists() and any('strict targets are off' in x for x in logs), logs

    with tempfile.TemporaryDirectory(prefix='v168b-manifest-') as tmp:
        root = Path(tmp)
        (root / 'backend' / 'test-e2e').mkdir(parents=True)
        outsider.ensure_helper(root, log=lambda *_: None)
        (root / outsider.STRICT_EXEMPT_PATH).unlink()
        (root / 'backend' / 'test-e2e' / 'REQ-9.spec.js').write_text('// this run', encoding='utf-8')
        outsider.ensure_helper(root, log=lambda *_: None)
        assert not (root / outsider.STRICT_EXEMPT_PATH).exists()

    code = ('import sys, tempfile, pathlib; sys.path.insert(0, %r); from agent import settings; '
            'settings.SETTINGS["spec_strict_targets"] = False; from agent import outsider as o; '
            't = pathlib.Path(tempfile.mkdtemp()); (t / "backend" / "test-e2e").mkdir(parents=True); '
            'o.ensure_helper(t, log=lambda *_: None); print((t / o.STRICT_EXEMPT_PATH).exists())' % str(ROOT))
    out = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=120).stdout.split()
    assert out == ['False'], out

@unit_case
def patch_is_all_or_nothing():
    base = outsider.V167_HELPER_JS
    assert outsider.arg_check_helper_js(outsider.choice_popup_helper_js(outsider.strict_helper_js(base))) == outsider.HELPER_JS
    assert outsider.strict_helper_js(outsider.HELPER_JS) == outsider.HELPER_JS
    assert outsider.choice_popup_helper_js(outsider.HELPER_JS) == outsider.HELPER_JS
    for anchor, _line in outsider._POPUP_EDITS:
        other = base.replace(anchor, anchor.replace('(', '( ', 1) if '(' in anchor else anchor.replace('`', '', 1), 1)
        assert other != base and outsider.choice_popup_helper_js(other) == other, anchor[:60]
    for anchor, _line in outsider._STRICT_EDITS:
        other = base.replace(anchor, anchor.replace('(', '( ', 1), 1)
        assert other != base and outsider.strict_helper_js(other) == other, anchor[:60]
    twice = base + '\n// ' + outsider._STRICT_EDITS[1][0]
    assert outsider.strict_helper_js(twice) == twice

DRIVER = r"""
const { chromium } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const helpers = { on: require(path.resolve('on/outsider.js')), off: require(path.resolve('off/outsider.js')) };
const first = (e) => String((e && e.message) || e).split('\n')[0].slice(0, 600);
const doc = (body) => `<!doctype html><html><head><meta charset="utf-8"><style>.sr{position:absolute;width:1px;height:1px;
overflow:hidden;clip:rect(0,0,0,0)}</style></head><body>${body}<p id=out></p><script>document.addEventListener('click',
(e) => { const t = e.target.closest('[id]'); if (t && t.id !== 'out') document.getElementById('out').textContent = 'clicked ' + t.id; });
document.addEventListener('input', (e) => { document.getElementById('out').textContent = 'typed ' + e.target.id; });
document.addEventListener('change', (e) => { document.getElementById('out').textContent = 'changed ' + e.target.id; });</script></body></html>`;
const ACTIONS = {
  click_link: (H, p) => H.click(p, { role: 'link', name: 'EVO-N02-FIND-NEXT' }),
  click_word: (H, p) => H.click(p, 'EVO-N02-FIND-NEXT'),
  dbl_link: (H, p) => H.doubleClick(p, { role: 'link', name: 'EVO-N02-FIND-NEXT' }),
  click_open_link: (H, p) => H.click(p, { role: 'link', name: 'Open' }),
  click_open_word: (H, p) => H.click(p, 'Open'),
  click_open_second: (H, p) => H.click(p, { role: 'link', name: 'Open' }, H.region(p, 'region', 'Second')),
  click_open_item: (H, p) => H.click(p, { role: 'link', name: 'Open' }, H.item(p, 'Budget 2026')),
  click_docs: (H, p) => H.click(p, { role: 'link', name: 'acme-docs' }),
  click_docs_item: (H, p) => H.click(p, { role: 'link', name: 'acme-docs' }, H.item(p, 'Acme Demo/acme-docs')),
  click_settings: (H, p) => H.click(p, { role: 'link', name: 'Settings' }),
  choose_status: async (H, p) => {
    await H.choose(p, 'Status', 'Closed');
    await p.evaluate(() => { document.getElementById('out').textContent += ' value=' + document.getElementById('cs').value; });
  },
  choose_country_plain: async (H, p) => {
    await H.choose(p, 'Country', 'Peru');
    await p.evaluate(() => { document.getElementById('out').textContent += ' value=' + document.getElementById('pc').value; });
  },
  choose_priority: (H, p) => H.choose(p, 'Priority', 'High'),
  click_budget: (H, p) => H.click(p, { role: 'link', name: 'Budget' }),
  click_delete: (H, p) => H.click(p, 'Delete'),
  click_signout: (H, p) => H.click(p, { role: 'button', name: 'Sign out' }),
  click_save: (H, p) => H.click(p, { role: 'button', name: 'Save' }),
  click_save_word: (H, p) => H.click(p, 'Save'),
  click_regex: (H, p) => H.click(p, { role: 'link', name: /EVO-N02/ }),
  click_short: (H, p) => H.click(p, { role: 'link', name: 'EVO-N02' }),
  fill_title: (H, p) => H.fill(p, { role: 'textbox', name: 'Title' }, 'x'),
  fill_title_word: (H, p) => H.fill(p, 'Title', 'x'),
  fill_title_scoped: (H, p) => H.fill(p, 'Title', 'x', H.region(p, 'form', 'Second form')),
  check_urgent: (H, p) => H.check(p, { role: 'checkbox', name: 'Urgent' }),
  choose_country: (H, p) => H.choose(p, 'Country', 'Peru'),
  see_dup: async (H, p) => { await H.see(p, { role: 'link', name: 'EVO-N02-FIND-NEXT' }); await H.see(p, 'EVO-N02-FIND-NEXT'); },
  locate_dup: async (H, p) => {
    await H.expect(H.locate(p, { role: 'link', name: 'EVO-N02-FIND-NEXT' })).toBeVisible();
    await H.expect(H.locateAll(p, { role: 'link', name: 'EVO-N02-FIND-NEXT' })).toHaveCount(2);
  },
};
(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [key, body, action] of fixtures) {
    for (const mode of ['on', 'off']) {
      const context = await browser.newContext();
      await context.route('http://probe.test/**', (r) => r.fulfill({ contentType: 'text/html', body: doc(body) }));
      const page = await context.newPage();
      await page.goto('http://probe.test/');
      try {
        await ACTIONS[action](helpers[mode], page);
        out[`${key}/${mode}`] = 'OK ' + (await page.locator('#out').innerText());
      } catch (e) {
        out[`${key}/${mode}`] = 'ERR ' + first(e) + ' || ' + (await page.locator('#out').innerText().catch(() => '?'));
      }
      await context.close();
    }
  }
  await browser.close();
  console.log('V168B' + JSON.stringify(out));
})().catch((e) => { console.log('V168B_FAIL ' + e); process.exit(2); });
"""

LINK = '<a href="#{id}" id="{id}">EVO-N02-FIND-NEXT</a>'
FIXTURES = [

    ('dup_links', '<ul><li>' + LINK.format(id='a1') + '</li><li>' + LINK.format(id='a2') + '</li></ul>', 'click_link'),
    ('dup_links_word', '<ul><li>' + LINK.format(id='a1') + '</li><li>' + LINK.format(id='a2') + '</li></ul>', 'click_word'),
    ('dup_links_dbl', '<ul><li>' + LINK.format(id='a1') + '</li><li>' + LINK.format(id='a2') + '</li></ul>', 'dbl_link'),
    ('dup_fields', '<label>Title <input id=t1></label><label>Title <input id=t2></label>', 'fill_title'),
    ('dup_fields_word', '<label>Title <input id=t1></label><label>Title <input id=t2></label>', 'fill_title_word'),
    ('dup_checks', '<label><input type=checkbox id=c1> Urgent</label><label><input type=checkbox id=c2> Urgent</label>',
     'check_urgent'),
    ('dup_selects', '<label>Country <select id=s1><option>Chile</option><option>Peru</option></select></label>'
                    '<label>Country <select id=s2><option>Chile</option><option>Peru</option></select></label>', 'choose_country'),
    ('scope_page_wide', '<section aria-label="First"><a href="#o1" id=o1>Open</a></section>'
                        '<section aria-label="Second"><a href="#o2" id=o2>Open</a></section>', 'click_open_link'),

    ('numbers_only', '<ul><li>' + LINK.format(id='w5') + ' Last updated 10/7/2026, 11:59:58 AM</li><li>' + LINK.format(id='w6')
     + ' Last updated 10/7/2026, 12:00:04 PM</li></ul>', 'click_link'),
    ('copies_plural', '<ul><li><a href="/w/1" id=p1>Budget</a> 3 sheets</li><li><a href="/w/2" id=p2>Budget</a> 1 sheet</li></ul>',
     'click_budget'),
    ('copies_month', '<ul><li><a href="/w/1" id=m1>Budget</a> edited Sep 30, 2 minutes ago</li>'
                     '<li><a href="/w/2" id=m2>Budget</a> edited Oct 1, just now</li></ul>', 'click_budget'),
    ('copies_slug', '<ul><li><a href="/workbooks/evo-n02-find-next" id=k1>EVO-N02-FIND-NEXT</a></li>'
                    '<li><a href="/workbooks/evo-n02-find-next-2" id=k2>EVO-N02-FIND-NEXT</a></li></ul>', 'click_link'),

    ('distinct_places', '<ul><li><a href="/acme-demo/acme-docs" id=r1>acme-docs</a></li>'
                        '<li><a href="/repo-owner/acme-docs" id=r2>acme-docs</a></li></ul>'
                        '<script>document.addEventListener("click", (e) => e.preventDefault())</script>', 'click_docs'),

    ('hash_links', '<ul><li>Alpha <a href="#" id=x1>Delete</a></li><li>Beta <a href="#" id=x2>Delete</a></li></ul>', 'click_delete'),

    ('distinct_entries', '<ul><li>Acme Demo/acme-docs <a href="#d1" id=d1>acme-docs</a></li>'
                         '<li>repo-owner/acme-docs <a href="#d2" id=d2>acme-docs</a></li></ul>', 'click_docs'),

    ('distinct_scoped', '<ul><li>Acme Demo/acme-docs <a href="#d1" id=d1>acme-docs</a></li>'
                        '<li>repo-owner/acme-docs <a href="#d2" id=d2>acme-docs</a></li></ul>', 'click_docs_item'),
    ('fixed_drawer', '<header><button id=q1>Sign out</button></header><aside style="position:fixed;top:0;right:0;width:300px;'
                     'height:100%;transform:translateX(100%)"><button id=q2>Sign out</button></aside>', 'click_signout'),
    ('same_address','<nav><a href="/settings" id=g1>Settings</a></nav><ul role=menu><li><a href="/settings" id=g2>Settings</a>'
                     '</li></ul><script>document.addEventListener("click", (e) => e.preventDefault())</script>', 'click_settings'),
    ('hidden_copies', LINK.format(id='a1') + '<div style="display:none">' + LINK.format(id='h1') + '</div>'
     '<div style="position:absolute;left:-9999px;top:0">' + LINK.format(id='h2') + '</div>'
     '<span class=sr>' + LINK.format(id='h3') + '</span><div style="opacity:0">' + LINK.format(id='h4') + '</div>'
     '<template>' + LINK.format(id='h5') + '</template>', 'click_link'),
    ('readonly_copies', '<label>Title <input id=t1 readonly></label><label>Title <input id=t2 readonly></label>'
                        '<label>Title <input id=t3></label>', 'fill_title'),
    ('disabled_copy','<button id=b1 disabled>Save</button><button id=b2>Save</button>', 'click_save'),
    ('disabled_copy_word', '<button id=b1 aria-disabled="true">Save</button><button id=b2>Save</button>', 'click_save_word'),
    ('other_roles', '<a href="#" id=o1>Open</a><button id=o2>Open</button>', 'click_open_link'),
    ('other_roles_word', '<a href="#" id=o1>Open</a><button id=o2>Open</button>', 'click_open_word'),
    ('scoped_region', '<section aria-label="First"><a href="#" id=o1>Open</a></section>'
                      '<section aria-label="Second"><a href="#" id=o2>Open</a></section>', 'click_open_second'),
    ('scoped_item', '<ul><li>Budget 2026 <a href="#" id=o1>Open</a></li><li>Budget 2025 <a href="#" id=o2>Open</a></li></ul>',
     'click_open_item'),
    ('scoped_form', '<form aria-label="First form"><label>Title <input id=t1></label></form>'
                    '<form aria-label="Second form"><label>Title <input id=t2></label></form>', 'fill_title_scoped'),
    ('nested_pair', '<div role="button" aria-label="Save" id=n1 tabindex=0><button id=n2>Save</button></div>', 'click_save'),
    ('contained_name', '<a href="#" id=s1>EVO-N02</a><a href="#" id=s2>EVO-N02-FIND-NEXT</a>', 'click_short'),
    ('regex_name', '<a href="#" id=r1>EVO-N02-FIND-NEXT</a><a href="#" id=r2>EVO-N02-FIND-NEXT</a>', 'click_regex'),

    ('see_dup', '<ul><li>' + LINK.format(id='a1') + '</li><li>' + LINK.format(id='a2') + '</li></ul>', 'see_dup'),
    ('locate_dup', '<ul><li>' + LINK.format(id='a1') + '</li><li>' + LINK.format(id='a2') + '</li></ul>', 'locate_dup'),
]
CHOICE = ('<span style="position:relative;display:inline-block"><label for=cs>Status</label> <select id=cs aria-controls="cs-list" '
          'aria-expanded="false"><option value="o" aria-hidden="true">Open</option><option value="c" aria-hidden="true">Closed'
          '</option></select></span><script>const BROKEN = __BROKEN__; const s = document.getElementById("cs");'
          's.addEventListener("mousedown", (e) => { e.preventDefault(); s.focus(); toggle(); });'
          'function toggle() { let l = document.getElementById("cs-list"); if (l) { l.remove(); return; }'
          ' l = document.createElement("ul"); l.id = "cs-list"; l.setAttribute("role", "listbox");'
          ' for (const o of s.options) { const li = document.createElement("li"); li.setAttribute("role", "option");'
          '  li.textContent = o.text; li.addEventListener("mousedown", (e) => e.preventDefault());'
          '  li.addEventListener("click", () => { if (!BROKEN) { s.value = o.value; s.dispatchEvent(new Event("change", { bubbles: true })); }'
          '   l.remove(); }); l.appendChild(li); }'
          ' s.parentElement.appendChild(l); }</script>')
FIXTURES += [
    ('choice_ok', CHOICE.replace('__BROKEN__', 'false'), 'choose_status'),
    ('choice_broken', CHOICE.replace('__BROKEN__', 'true'), 'choose_status'),
    ('plain_select', '<label>Country <select id=pc><option value="cl">Chile</option><option value="pe">Peru</option></select>'
                     '</label>', 'choose_country_plain'),
    ('custom_combobox', '<button role=combobox aria-label="Priority" id=cb aria-controls="pl">Low</button><ul id=pl role=listbox hidden>'
                        '<li role=option id=ph>High</li><li role=option id=pw>Low</li></ul><script>'
                        'const b = document.getElementById("cb"); const l = document.getElementById("pl");'
                        'b.addEventListener("click", () => { l.hidden = !l.hidden; });'
                        'for (const li of l.children) li.addEventListener("click", () => { b.textContent = li.textContent; l.hidden = true; });'
                        '</script>', 'choose_priority'),
]
ROW = '<tr><td>Budget 2026</td><td>{cell}</td></tr>'
FIXTURES += [
    ('row_copies_fill', '<table>' + ROW.format(cell='<label>Title <input id=t1></label>')
     + ROW.format(cell='<label>Title <input id=t2></label>') + '</table>', 'fill_title'),
    ('row_copies_check', '<table>' + ROW.format(cell='<label><input type=checkbox id=c1> Urgent</label>')
     + ROW.format(cell='<label><input type=checkbox id=c2> Urgent</label>') + '</table>', 'check_urgent'),
    ('row_copies_choose', '<table>' + ROW.format(cell='<label>Country <select id=s1><option>Chile</option><option>Peru</option>'
     '</select></label>') + ROW.format(cell='<label>Country <select id=s2><option>Chile</option><option>Peru</option></select>'
     '</label>') + '</table>', 'choose_country'),

    ('status_differs', '<ul><li><a href="/w/1" id=u1>Budget</a> Draft</li><li><a href="/w/2" id=u2>Budget</a> Ready</li></ul>'
     '<script>document.addEventListener("click", (e) => e.preventDefault())</script>',
     'click_budget'),
    ('unrelated_listbox', '<label>Country <select id=pc><option value="cl">Chile</option><option value="pe">Peru</option>'
     '</select></label><script>document.getElementById("pc").addEventListener("mousedown", () => {'
     ' const l = document.createElement("ul"); l.setAttribute("role", "listbox"); l.innerHTML = "<li role=option>Other</li>";'
     ' document.body.appendChild(l); });</script>', 'choose_country_plain'),
]
_RESULTS: dict | None = None

def results() -> dict:
    global _RESULTS
    if _RESULTS is None:
        with tempfile.TemporaryDirectory(prefix='v168b-strict-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
            for mode, js in (('on', outsider.HELPER_JS), ('off', outsider.V167_HELPER_JS)):
                (Path(tmp) / mode).mkdir()
                (Path(tmp) / mode / 'outsider.js').write_text(js, encoding='utf-8')
            (Path(tmp) / 'driver.cjs').write_text(DRIVER, encoding='utf-8')
            (Path(tmp) / 'fixtures.json').write_text(json.dumps(FIXTURES), encoding='utf-8')
            done = subprocess.run(['node', 'driver.cjs', 'fixtures.json'], cwd=tmp, capture_output=True, text=True, timeout=300,
                                  encoding='utf-8', errors='replace', env={**os.environ, 'NODE_PATH': NODE_MODULES})
        line = next((x for x in (done.stdout or '').splitlines() if x.startswith('V168B{')), '')
        assert line, ((done.stdout or '')[-800:], (done.stderr or '')[-800:])
        _RESULTS = json.loads(line[5:])
    return _RESULTS

def outcome(key: str, mode: str = 'on') -> str:
    return results()[f'{key}/{mode}']

@browser_case
def proven_copies_fail_with_our_message():
    for key, role, verb in (('dup_links', 'link', 'clicking'), ('dup_links_word', 'link', 'clicking'),
                            ('dup_links_dbl', 'link', 'clicking'), ('numbers_only', 'link', 'clicking'),
                            ('copies_plural', 'link', 'clicking'), ('copies_month', 'link', 'clicking'),
                            ('copies_slug', 'link', 'clicking'), ('row_copies_fill', 'textbox', 'typing into'),
                            ('row_copies_check', 'checkbox', 'checking'), ('row_copies_choose', 'combobox', 'choosing in')):
        got = outcome(key)
        assert got.startswith('ERR 2 visible elements of role ' + role + ' are named'), (key, got)
        assert 'each in an entry that reads the same' in got and f'{verb} one of them is ambiguous' in got, (key, got)
        assert 'on the page' in got and 'remove the copy' in got and 'pass that part as the scope argument' in got, (key, got)
        assert got.endswith('|| '), (key, 'the action must not have happened', got)
        assert 'strict mode' not in got and 'resolved to' not in got and 'SPEC DEFECT' not in got, (key, got)
    assert 'li "EVO-N02-FIND-NEXT"' in outcome('dup_links'), outcome('dup_links')

@browser_case
def unproven_duplicates_act_like_v167():

    expected = {'dup_fields': 'OK typed t1', 'dup_fields_word': 'OK typed t1', 'dup_checks': 'OK changed c1',
                'dup_selects': 'OK changed s1', 'scope_page_wide': 'OK clicked o1', 'distinct_entries': 'OK clicked d1',
                'distinct_places': 'OK clicked r1', 'hash_links': 'OK clicked x1', 'status_differs': 'OK clicked u1'}
    for key, want in expected.items():
        assert outcome(key) == outcome(key, 'off'), (key, outcome(key), outcome(key, 'off'))
        assert outcome(key).startswith(want), (key, outcome(key))

@browser_case
def not_ambiguous_passes():
    expected = {'hidden_copies': 'OK clicked a1', 'disabled_copy': 'OK clicked b2', 'disabled_copy_word': 'OK clicked b2',
                'other_roles': 'OK clicked o1', 'scoped_region': 'OK clicked o2', 'scoped_item': 'OK clicked o1',
                'scoped_form': 'OK typed t2', 'contained_name': 'OK clicked s1', 'regex_name': 'OK clicked r1',
                'readonly_copies': 'OK typed t3', 'distinct_scoped': 'OK clicked d1', 'same_address': 'OK clicked g1', 'fixed_drawer': 'OK clicked q1'}
    for key, want in expected.items():
        assert outcome(key) == want, (key, outcome(key))
    assert outcome('other_roles_word').startswith('OK clicked o'), outcome('other_roles_word')
    assert outcome('nested_pair').startswith('OK clicked n'), outcome('nested_pair')

@browser_case
def choice_popup_is_chosen_like_a_person():
    assert outcome('choice_ok') == 'OK clicked cs-list value=c', outcome('choice_ok')
    got = outcome('choice_broken')
    assert got.startswith("ERR Choosing Closed in the list the dropdown Status opened left the dropdown's value at \"o\""), got
    assert outcome('choice_broken', 'off') == 'OK changed cs value=c', outcome('choice_broken', 'off')
    assert outcome('plain_select') == 'OK changed pc value=pe', outcome('plain_select')
    assert outcome('custom_combobox') == 'OK clicked ph', outcome('custom_combobox')

    assert outcome('unrelated_listbox') == 'OK changed pc value=pe', outcome('unrelated_listbox')

@browser_case
def assertions_unchanged():
    assert outcome('see_dup') == 'OK ', outcome('see_dup')
    assert outcome('locate_dup') == 'OK ', outcome('locate_dup')

@browser_case
def setting_off_passes_as_v167():
    for key, _body, _action in FIXTURES:
        got = outcome(key, 'off')
        assert got.startswith('OK '), (key, got)
        if key not in ('see_dup', 'locate_dup', 'nested_pair', 'other_roles_word', 'choice_broken', 'choice_ok'):
            on = outcome(key)
            assert on == got or on.startswith('ERR') and AMBIGUOUS in on and 'SPEC DEFECT' not in on, (key, on, got)

EXEMPT_SPEC = """const { test } = require('@playwright/test');
const { click } = require('../e2e-support/outsider');
test('two links of one name', async ({ page }) => {
  await page.setContent('<ul><li><a href="#a1" id=a1>EVO-N02-FIND-NEXT</a></li><li><a href="#a2" id=a2>EVO-N02-FIND-NEXT</a></li></ul>');
  await click(page, { role: 'link', name: 'EVO-N02-FIND-NEXT' });
});
"""

def real_run(manifest: str | None) -> dict:
    cli = Path(NODE_MODULES) / '@playwright' / 'test' / 'cli.js'
    with tempfile.TemporaryDirectory(prefix='v168b-exempt-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        root = Path(tmp)
        (root / 'tests').mkdir()
        (root / 'e2e-support').mkdir()
        (root / 'e2e-support' / 'outsider.js').write_text(outsider.HELPER_JS, encoding='utf-8')
        if manifest is not None:
            (root / 'e2e-support' / 'strict-exempt.json').write_text(manifest, encoding='utf-8')
        for name in ('inherited.spec.js', 'written.spec.js'):
            (root / 'tests' / name).write_text(EXEMPT_SPEC, encoding='utf-8')
        (root / 'playwright.config.js').write_text("module.exports = { testDir: './tests', timeout: 20000, workers: 1 };\n", encoding='utf-8')
        done = subprocess.run(['node', str(cli), 'test', '--reporter=json'], cwd=tmp, capture_output=True, text=True, timeout=300,
                              encoding='utf-8', errors='replace', env={**os.environ, 'NODE_PATH': NODE_MODULES})
        report = json.loads(done.stdout[done.stdout.index('{'):])
    status = {}
    for suite in report['suites']:
        for spec in suite['specs']:
            result = spec['tests'][0]['results'][0]
            status[suite['file']] = (result['status'], (result.get('error') or {}).get('message', '')[:200])
    return status

@browser_case
def inherited_specs_keep_v167_in_a_real_run():
    status = real_run(json.dumps({'specs': ['inherited.spec.js']}))
    assert status['inherited.spec.js'][0] == 'passed', status
    assert status['written.spec.js'][0] == 'failed' and 'one of them is ambiguous' in status['written.spec.js'][1], status

@browser_case
def no_trustworthy_manifest_is_v167_for_every_spec():

    for manifest in (None, '{"specs": [', '{"specs": "written.spec.js"}', '{"specs": [1]}', '{"off": true}', '[]'):
        status = real_run(manifest)
        assert status['inherited.spec.js'][0] == 'passed' and status['written.spec.js'][0] == 'passed', (manifest, status)

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
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v168b strict cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
