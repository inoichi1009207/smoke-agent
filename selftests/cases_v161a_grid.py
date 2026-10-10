from __future__ import annotations

import functools
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from agent import grid_probes as gp

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
  A small ledger editor. Pressing Delete clears the selected cells and recalculates dependent formulas.
children:
- id: REQ-1
  description: Editing cells.
  scenarios:
  - name: 'REQ-1: clear a formula and recalculate'
    steps:
    - keyword: GIVEN
      content: The system pre-provisions ledger `Cost plan` with cell B3 value `4`, cell C3 formula `=B3*3`, and cell D3 formula `=C3+5`.
    - keyword: WHEN
      content: The visitor opens `Cost plan`, selects cell C3, and presses Delete.
    - keyword: THEN
      content: Cell C3 is empty and cell D3 displays `5` after refresh.
  - name: 'REQ-1: far data'
    steps:
    - keyword: GIVEN
      content: The system pre-provisions ledger `Wide plan` with headers in row 1, data through row 30, and cell L30 value `Closing`; cell H4 contains `Manifest R41`.
    - keyword: THEN
      content: Cell L30 displays `Closing` and the range H4:L30 can be selected.
'''

APP = r'''
const express=require('express'),path=require('path');
const app=express();app.use(express.json());
const MODE=process.env.GP_MODE||'ref', SERVERVAL=process.env.GP_SERVERVAL!=='0', NOCELLS=process.env.GP_NOCELLS==='1';
const books=new Map();let next=1;
function evaluate(cells,coord,depth){
  const raw=cells[coord];
  if(raw===undefined||raw==='')return MODE==='zero'?0:'#REF!';
  if(!String(raw).startsWith('='))return isNaN(Number(raw))?raw:Number(raw);
  if(depth>8)return '#REF!';
  let err=null;
  const expr=String(raw).slice(1).replace(/[A-Z]{1,2}[0-9]+/g,ref=>{
    const v=cells[ref]===undefined||cells[ref]===''?(MODE==='zero'?0:'#REF!'):evaluate(cells,ref,depth+1);
    if(typeof v==='string'&&v.startsWith('#'))err=v;return '('+Number(v)+')'});
  if(err)return err;
  if(!/^[0-9+\-*/(). ]*$/.test(expr))return '#NAME?';
  return Function('return ('+expr+')')();
}
function view(b){return {id:b.id,name:b.name,sheets:b.sheets.map(s=>{const cells={};
  for(const [c,raw] of Object.entries(s.cells))cells[c]=SERVERVAL?{raw,value:String(evaluate(s.cells,c,0))}:raw;
  return {id:s.id,name:s.name,cells}})}}
app.get('/api/books',(req,res)=>res.json([...books.values()].map(b=>({id:b.id,name:b.name}))));
app.post('/api/books',(req,res)=>{const b={id:next++,name:(req.body&&req.body.name)||'Book',sheets:[{id:next++,name:'Sheet1',cells:{}}]};
  books.set(String(b.id),b);res.status(201).json({book:view(b)})});
app.get('/api/books/:id',(req,res)=>{const b=books.get(req.params.id);if(!b)return res.status(404).json({error:'nf'});res.json({book:view(b)})});
if(!NOCELLS)app.put('/api/books/:id/sheets/:sheetId/cells',(req,res)=>{
  const b=books.get(req.params.id);const s=b&&b.sheets.find(x=>String(x.id)===req.params.sheetId);
  if(!s)return res.status(404).json({error:'nf'});
  const cells=req.body&&req.body.cells;
  if(!cells||typeof cells!=='object'||Array.isArray(cells))return res.status(400).json({error:'bad payload'});
  for(const [c,raw] of Object.entries(cells)){if(raw===null||raw==='')delete s.cells[c];else s.cells[c]=String(raw)}
  res.json({ok:true})});
app.use(express.static(path.join(__dirname,'../frontend/dist')));
app.use((req,res)=>res.sendFile(path.join(__dirname,'../frontend/dist/index.html')));
app.listen(Number(process.env.PORT),'127.0.0.1');
'''

HTML = r'''<!doctype html><html><head><title>Ledger</title><script src="/assets/app.js"></script></head>
<body><div id="app"></div><script>
const CAP=__CAP__;
const letters=n=>{let s='';while(n>0){const r=(n-1)%26;s=String.fromCharCode(65+r)+s;n=Math.floor((n-1)/26)}return s};
const colOf=t=>{let n=0;for(const ch of t)n=n*26+ch.charCodeAt(0)-64;return n};
function evaluate(cells,coord,depth){
  const raw=cells[coord];
  if(raw===undefined||raw==='')return '#REF!';
  if(!String(raw).startsWith('='))return raw;
  let err=null;
  const expr=String(raw).slice(1).replace(/[A-Z]{1,2}[0-9]+/g,ref=>{const v=cells[ref]===undefined?'#REF!':evaluate(cells,ref,depth+1);
    if(String(v).startsWith('#'))err=v;return '('+Number(v)+')'});
  return err||String(Function('return ('+expr+')')());
}
async function render(){
  const m=location.pathname.match(/^\/books\/(\d+)$/);const root=document.getElementById('app');
  if(!m){root.innerHTML='<h1>Ledgers</h1>';return}
  const book=(await (await fetch('/api/books/'+m[1])).json()).book;const sheet=book.sheets[0];
  const raws={},shown={};
  for(const [c,e] of Object.entries(sheet.cells)){raws[c]=typeof e==='string'?e:e.raw;if(typeof e!=='string')shown[c]=e.value}
  let maxC=10,maxR=20;
  for(const c of Object.keys(raws)){const mm=c.match(/^([A-Z]+)(\d+)$/);maxC=Math.max(maxC,colOf(mm[1]));maxR=Math.max(maxR,+mm[2])}
  const C=CAP?10:maxC,R=CAP?20:maxR;
  let h='<h1>'+book.name+'</h1><button>'+(raws.M1||raws.L1||'')+'</button><div style="overflow:auto;height:300px;width:600px"><table role="grid"><thead><tr><th></th>';
  for(let c=1;c<=C;c++)h+='<th role="columnheader">'+letters(c)+'</th>';
  h+='</tr></thead><tbody>';
  for(let r=1;r<=R;r++){h+='<tr><th role="rowheader">'+r+'</th>';
    for(let c=1;c<=C;c++){const k=letters(c)+r;const v=k in shown?shown[k]:(k in raws?evaluate(raws,k,0):'');h+='<td role="gridcell" style="min-width:80px">'+v+'</td>'}
    h+='</tr>'}
  root.innerHTML=h+'</tbody></table></div>';
}
render();</script></body></html>'''

ROUTES_JS = 'window.__routes=[{path:"/"},{path:"/books/:id"}];'

def fixture(root, cap=True, nocells=False):
    files = {'backend/package.json': json.dumps({'scripts': {'start': 'node server.js'}}),
             'backend/server.js': APP.replace("process.env.GP_NOCELLS==='1'", 'true' if nocells else 'false'),
             'frontend/dist/index.html': HTML.replace('__CAP__', 'true' if cap else 'false'),
             'frontend/dist/assets/app.js': ROUTES_JS}
    for rel, value in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding='utf-8')
    return root

def probe(cap=True, mode='ref', serverval=True, nocells=False, requirement=REQUIREMENT, time_limit=150):
    with tempfile.TemporaryDirectory(prefix='grid-probe-fixture-', dir=os.environ.get('ARC_OUTSIDER_SCRATCH')) as tmp:
        app = fixture(Path(tmp) / 'app', cap=cap, nocells=nocells)
        saved = {k: os.environ.get(k) for k in ('GP_MODE', 'GP_SERVERVAL')}
        os.environ['GP_MODE'] = mode
        os.environ['GP_SERVERVAL'] = '1' if serverval else '0'
        try:
            before = sorted(str(p.relative_to(app)) for p in app.rglob('*'))
            logs = []
            t0 = time.monotonic()
            rows = gp.grid_probes(requirement, app, time_limit=time_limit, log=logs.append, node_modules=NODE_MODULES)
            took = time.monotonic() - t0
            assert before == sorted(str(p.relative_to(app)) for p in app.rglob('*')), 'probe wrote to the source app'
            assert [r['item'] for r in rows] == ['recalculation', 'extent'], rows
            for r in rows:
                assert set(r) == {'item', 'status', 'result', 'findings', 'evidence'}, r
                assert r['status'] in ('finding', 'clean', 'not run') and len(r['result']) <= 300, r
                assert len(r['findings']) <= 5 and all(len(f) <= 500 for f in r['findings']), r
                assert (r['status'] == 'finding') == bool(r['findings']), r
                json.dumps(r['evidence'])
            return {r['item']: r for r in rows}, logs, took
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

_CACHE = {}

def cached(key, **kw):
    if key not in _CACHE:
        _CACHE[key] = probe(**kw)
    return _CACHE[key]

@unit_case
def unit_requirement_extent():
    ext = gp.requirement_extent(REQUIREMENT)
    assert ext['grid'] and ext['col'] == 12 and ext['row'] == 30, ext
    assert 'R41' not in ext['refs'], ext
    assert not gp.requirement_extent('Users sign in with “Email” and see A4 paper prices.')['grid']
    small = gp.requirement_extent('Cell B2 value `3` and cell C4 formula `=B2*2`.')
    assert small['grid'] and small['col'] == 3 and small['row'] == 4, small
    assert gp.requirement_extent(None)['grid'] is False and gp.requirement_extent(12345)['grid'] is False
    assert gp.col_index('A') == 1 and gp.col_index('J') == 10 and gp.col_index('AA') == 27
    assert gp.col_letters(13) == 'M' and gp.col_letters(28) == 'AB'

@unit_case
def unit_empty_cell_rule():
    rule = gp.empty_cell_rule(REQUIREMENT)
    assert rule and rule['rule'] == 'zero' and rule['source'] == 'scenario' and 'D3' in rule['quote'], rule
    stated = gp.empty_cell_rule('In arithmetic an empty cell counts as 0, text cells are ignored.')
    assert stated and stated['source'] == 'sentence', stated
    assert gp.empty_cell_rule('Formulas recalculate when a cell changes.') is None

    wrong = REQUIREMENT.replace('cell D3 displays `5`', 'cell D3 displays `17`')
    assert gp.empty_cell_rule(wrong) is None
    assert gp.empty_cell_rule(None) is None and gp.empty_cell_rule(object()) is None

@unit_case
def unit_guards_against_false_findings():

    noisy = REQUIREMENT + "\n  Ticket AC12 and build ES6 are listed. Typing `=ZZ1` shows #REF! in the cell.\n"
    ext = gp.requirement_extent(noisy)
    assert ext['col'] == 12 and 'AC12' not in ext['refs'] and 'ZZ1' not in ext['refs'], ext

    assert gp.empty_cell_rule('A blank cell is not treated as zero in sums.') is None

    expects = REQUIREMENT.replace('cell D3 displays `5`', 'cell D3 displays `#REF!`')
    assert gp.cleared_error_expected(expects) and gp.empty_cell_rule(expects) is None
    assert gp.cleared_error_expected(REQUIREMENT) is None
    assert gp.cleared_error_expected('An empty cell reference shows #VALUE! in arithmetic.')

    data = {'history': [{'coord': 'B1', 'value': 11334}], 'cells': {'B1': {'raw': '=A1+1', 'value': '#REF!'}}}
    assert gp._cell_lookup(data, 'B1')['value'] == '#REF!'

@unit_case
def unit_verdicts_need_page_agreement_and_a_complete_sweep():
    ev = {'cell_route': 'PUT /api/x/:id/cells'}
    chain = {'name': 'referenced value', 'dep': 'B1', 'formula': '=A1+7000+13', 'cleared': 'A1', 'before_expected': 11334,
             'after_zero': 7013, 'server_before': 11334, 'server_after': '#REF!', 'server_ok': True, 'server_has': True}
    recalc = {'chains': [dict(chain)], 'cleared': True, 'clear_sent': 'empty string'}

    row = gp._recalc_verdict(recalc, None, None, None, {'error': 'no browser'}, {}, ev)
    assert row['status'] == 'not run' and 'not confirmed on the page' in row['result'], row

    before = {'control': True, 'last': {'found': ['11334'], 'errors': []}}
    after = {'control': True, 'last': {'found': [], 'errors': ['#REF!']}}
    assert gp._recalc_verdict(recalc, None, before, after, {}, {}, ev)['status'] == 'finding'

    before_err = {'control': True, 'last': {'found': ['11334'], 'errors': ['#REF!']}}
    assert gp._recalc_verdict(recalc, None, before_err, after, {}, {}, ev)['status'] == 'not run'

    ext = {'accepted': [('A1', 'GPCTL01'), ('L30', 'GPEND01')]}
    visit = {'control': True, 'grid': True, 'found': ['GPCTL01'], 'inGrid': ['GPCTL01'], 'complete': False,
             'colName': 'J', 'rows': 20, 'notes': ['deadline']}
    row = gp._extent_verdict(ext, {'col': 12, 'row': 30}, visit, {}, ev)
    assert row['status'] == 'not run' and 'did not complete' in row['result'], row
    visit['complete'] = True
    assert gp._extent_verdict(ext, {'col': 12, 'row': 30}, visit, {}, ev)['status'] == 'finding'

@unit_case
def unit_formula_and_lookup():
    assert gp.eval_formula('=B3*3+5', {'B3': 4}, {}) == 17
    assert gp.eval_formula('=C3+5', {'B3': 0}, {'C3': '=B3*3'}) == 5
    assert gp.eval_formula('=SUM(A1:A3)', {}, {}) is None and gp.eval_formula('A1', {}, {}) is None
    data = {'book': {'id': 1, 'sheets': [{'id': 2, 'cells': {'A1': {'raw': '4', 'value': '4'}}},
                                         {'id': 3, 'cells': {'A1': {'raw': 'x', 'value': 'x'}}}]}}
    assert gp._cell_lookup(data, 'A1', '3')['raw'] == 'x' and gp._cell_lookup(data, 'A1', '2')['raw'] == '4'
    assert gp._cell_lookup(data, 'B9', '2') is None
    listed = {'cells': [{'address': 'B2', 'value': 7}, {'coord': 'C3', 'raw': '=B2', 'computed': 7}]}
    assert gp._cell_lookup(listed, 'B2')['value'] == 7 and gp._cell_lookup(listed, 'C3')['value'] == 7
    assert gp._child_id({'id': 5, 'sheets': [{'id': 9}]}, 'sheet') == '9'
    assert gp._child_id({'id': 5, 'activeTabId': 4}, 'tab') == '4'
    assert gp._is_error('#REF!') and gp._is_error('#DIV/0!') and not gp._is_error('7013')
    urls = gp.record_urls(['/', '/books/:id', '/books/:id/edit', '/users/:name'], {'params': {':id': '4'}},
                          '/api/books/:id/sheets/:sheetId/cells')
    assert urls[0] == '/books/4', urls

@unit_case
def unit_never_raises():
    rows = gp.grid_probes(None, None, log=lambda _m: 1 / 0)
    assert [r['status'] for r in rows] == ['not run', 'not run'], rows
    rows = gp.grid_probes('x', Path(tempfile.gettempdir()) / 'grid-probe-missing-dir', time_limit=-5, log=lambda _m: None)
    assert [r['status'] for r in rows] == ['not run', 'not run'] and 'backend' in rows[0]['result'], rows
    assert gp.grid_probes(REQUIREMENT, Path('.'), items=('bogus',), log=lambda _m: None) == []
    assert [r['item'] for r in gp.grid_probes(REQUIREMENT, None, items=('extent',), log=lambda _m: None)] == ['extent']

@unit_case
def n3_no_cell_api_is_not_run_without_a_server():
    with tempfile.TemporaryDirectory(prefix='grid-probe-fixture-') as tmp:
        app = fixture(Path(tmp) / 'app', nocells=True)

        t0 = time.monotonic()
        rows = gp.grid_probes(REQUIREMENT, app, time_limit=60, log=lambda _m: None, node_modules=NODE_MODULES)
        assert time.monotonic() - t0 < 10, 'took too long for an app without a cell API'
        assert [(r['item'], r['status']) for r in rows] == [('recalculation', 'not run'), ('extent', 'not run')], rows
        assert all('cell' in r['result'] for r in rows), rows

@browser_case
def p1_cleared_reference_shows_ref_error():
    rows, logs, _t = cached('bad', cap=True, mode='ref')
    r = rows['recalculation']
    assert r['status'] == 'finding', (r, logs[-4:])
    assert '#REF!' in r['result'] and 'the server returns #REF!' in r['result'], r
    assert any('empty' in f and 'Fix' in f for f in r['findings']), r['findings']
    assert r['evidence']['rule'] and r['evidence']['rule']['rule'] == 'zero', r['evidence']

@browser_case
def p2_fixed_first_screenful_is_an_extent_finding():
    rows, logs, _t = cached('bad', cap=True, mode='ref')
    r = rows['extent']
    assert r['status'] == 'finding', (r, logs[-4:])
    assert 'last column header J' in r['result'] and 'last row header 20' in r['result'], r
    assert 'L30' in r['result'] and 'A30' in r['result'], r
    assert 'L1 shown only outside the grid' in r['result'], r
    assert 'column L and row 30' in r['findings'][0], r['findings']

@browser_case
def n1_recalculation_with_empty_as_zero_is_clean():
    rows, logs, _t = cached('good', cap=False, mode='zero')
    r = rows['recalculation']
    assert r['status'] == 'clean', (r, logs[-4:])
    assert '7013' in r['result'] and '8026' in r['result'], r

@browser_case
def n2_grid_rendering_the_used_range_is_clean():
    rows, logs, _t = cached('good', cap=False, mode='zero')
    r = rows['extent']
    assert r['status'] == 'clean', (r, logs[-4:])
    assert 'L30' in r['result'], r

@browser_case
def p3_page_only_evaluation_still_reports():
    rows, logs, _t = probe(cap=False, mode='ref', serverval=False)
    r = rows['recalculation']
    assert r['status'] == 'finding' and 'the page shows #REF!' in r['result'], (r, logs[-4:])
    assert rows['extent']['status'] == 'clean', rows['extent']

@browser_case
def n5_error_value_the_requirement_expects_is_not_reported():
    expects = REQUIREMENT.replace('cell D3 displays `5`', 'cell D3 displays `#REF!`')
    rows, logs, _t = probe(cap=False, mode='ref', requirement=expects)
    r = rows['recalculation']
    assert r['status'] == 'not run' and 'requirement itself shows an error' in r['result'], (r, logs[-4:])

@browser_case
def n4_requirement_without_far_cells_leaves_extent_not_run():
    small = REQUIREMENT.split("  - name: 'REQ-1: far data'")[0]
    rows, logs, _t = probe(cap=True, mode='zero', requirement=small)
    assert rows['extent']['status'] == 'not run' and 'first screenful' in rows['extent']['result'], rows['extent']
    assert rows['recalculation']['status'] == 'clean', (rows['recalculation'], logs[-4:])

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
    print(f'{len(CASES) - failed - skipped}/{len(CASES)} v161a grid cases passed; {skipped} skipped')
    raise SystemExit(bool(failed))
