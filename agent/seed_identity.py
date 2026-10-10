from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time

MARKER = re.compile(r"(?i)\b(?:seed values?|seed data|seeded data (?:is|are|includes(?: the existing)?)|pre-?provisions(?: the)?)\s*:?\s*")
QUOTE_OPEN = '`"“'
QUOTE_CLOSE = {'`': '`', '"': '"', '“': '”'}

USERISH = {'account', 'user', 'member', 'owner', 'reviewer', 'collaborator', 'author',
           'assignee', 'admin', 'administrator', 'maintainer', 'email', 'username', 'login'}

ATTRIBUTE_WORDS = {'password', 'content', 'description', 'body', 'title', 'nickname', 'tag',
                   'tags', 'value', 'values', 'message', 'background', 'color', 'text', 'name',
                   'role', 'permission', 'status', 'state', 'visibility'}
ABBREV = {'repo': 'repository', 'org': 'organization', 'pr': 'pull_request', 'pull': 'pull_request'}

def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj

def requirement_texts(requirement_text: str) -> list[str]:
    try:
        import yaml
        data = yaml.safe_load(requirement_text)
        if isinstance(data, (dict, list)):
            return [re.sub(r'\s+', ' ', s) for s in _strings(data)]
    except Exception:
        pass
    return [re.sub(r'\s+', ' ', requirement_text)]

def _seed_segments(text: str):
    for m in MARKER.finditer(text):
        i, n, q = m.end(), len(text), None
        start = i
        while i < n:
            ch = text[i]
            if q:
                if ch == QUOTE_CLOSE[q]:
                    q = None
            elif ch in QUOTE_OPEN:
                q = ch
            elif ch == '.' and (i + 1 == n or text[i + 1] in " '\n"):
                break
            i += 1
        yield text[start:i]

def _singular(w: str) -> str:
    w = w.lower()
    if w.endswith('ies') and len(w) > 4:
        return w[:-3] + 'y'
    if w.endswith('ves'):
        return w[:-3] + 'f'
    for suf in ('ches', 'shes', 'sses', 'xes'):
        if w.endswith(suf):
            return w[:-2]
    if w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w

def parse_seed_names(requirement_text: str):
    return _seed_names_in(requirement_texts(requirement_text))

def _seed_names_in(texts):
    out: dict[tuple[str, str], int] = {}
    for text in texts:
        for seg in _seed_segments(text):
            pos, kind = 0, None
            for m in re.finditer(r'[`"“]([^`"”]{1,80})[`"”]', seg):
                gap = seg[pos:m.start()]
                pos = m.end()
                words = re.findall(r'[A-Za-z]+', gap)
                lw = [w.lower() for w in words]
                filler = {'and', 'or', 'the', 'a', 'an'}
                if lw and all(w in filler | {'with', 'without', 'in', 'on', 'of', 'to', 'from'} for w in lw) \
                        and any(w not in {'and', 'or'} for w in lw):

                    kind = None
                    continue
                if lw and not all(w in filler for w in lw):
                    last = [w for w in lw if w not in filler]
                    if len(last) >= 2 and _singular(last[-1]) == 'request' and last[-2] == 'pull':
                        kind = 'pull_request'
                    else:
                        kind = _singular(last[-1])
                if not kind or kind in ATTRIBUTE_WORDS:
                    continue
                key = (kind, m.group(1).strip())
                out[key] = out.get(key, 0) + 1
    return out

def credential_accounts(requirement_text: str) -> set[str]:
    accts = set()
    for text in requirement_texts(requirement_text):
        for seg in _seed_segments(text):
            if not re.search(r'(?i)\bpassword\s+[`"“]', seg):
                continue
            m = re.search(r'(?i)\b(account|user|username|login)\s+[`"“]([^`"”]+)[`"”]', seg)
            if m:
                accts.add(m.group(2).strip())
    return accts

ROLE_GIVEN = re.compile(
    r"(?i)\b(?:an?|the)\s+(organization|org|workspace|team|project|group)\s+"
    r"(owner|admin(?:istrator)?|maintainer|member|editor)\s+is\s+signed\s+in")

def role_requirements(requirement_text: str):
    res = []
    for text in requirement_texts(requirement_text):
        for m in ROLE_GIVEN.finditer(text):
            ckind = ABBREV.get(m.group(1).lower(), m.group(1).lower())
            names = sorted({n for (k, n) in _seed_names_in([text]) if k == ckind})
            if names:
                res.append((ckind, m.group(2).lower(), tuple(names)))
    return sorted(set(res))

def _tables(con):
    t = {}
    for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
        cols = [(r[1], (r[2] or '').upper()) for r in con.execute(f'pragma table_info("{name}")')]
        t[name] = cols
    return t

def _table_for(word: str, tables: dict) -> str | None:
    w = ABBREV.get(word, word)
    if w in USERISH:
        cands = ['users', 'accounts', 'user', 'account']
    else:
        cands = [w + 's', w + 'es', w[:-1] + 'ies' if w.endswith('y') else None,
                 w[:-1] + 'ves' if w.endswith('f') else None, w]
        if w == 'pull_request':
            cands += ['pulls', 'prs', 'pullrequests']
        if w == 'repository':
            cands += ['repos']
        if w == 'organization':
            cands += ['orgs', 'organisations']
    lower = {k.lower(): k for k in tables}
    for c in cands:
        if c and c in lower:
            return lower[c]
    return None

OWNERISH_FK = re.compile(r'(?i)^(owner|user|account|author|creator|created_by|setter|grantor|actor|'
                         r'reviewer|assignee|merged_by|updated_by|modified_by|subject|member)(_id)?$')

def _container_fks(table: str, cols, tables):
    res = []
    for c, _ in cols:

        if re.match(r'(?i)^(pull|pr|issue|pull_request|merge_request|note|page|sheet)_?(number|num|no)$', c):
            res.append((c, None))
            continue
        m = re.match(r'(?i)^(.+?)_?id$', c)
        if not m or c.lower() == 'id' or OWNERISH_FK.match(c) or OWNERISH_FK.match(m.group(1)):
            continue
        prefix = m.group(1).rstrip('_').lower()
        if prefix.startswith('parent') or prefix.startswith('source'):
            continue
        tgt = _table_for(_singular(prefix), tables)
        if tgt and tgt != table and _table_for('user', tables) != tgt:
            res.append((c, tgt))
    return res

def _label(con, tables, table, rowid_val):
    cols = [c for c, _ in tables[table]]
    namecol = next((c for c in ('name', 'username', 'title', 'login') if c in cols), None)
    if not namecol:
        return str(rowid_val)
    r = con.execute(f'select "{namecol}" from "{table}" where rowid=?', (rowid_val,)).fetchone()
    return f'{r[0]}' if r else str(rowid_val)

def _owner_desc(con, tables, table, row: dict):
    parts = []
    for c, v in row.items():
        if v is None or c.lower() in ('id',):
            continue
        cl = c.lower()
        if cl in ('owner_id', 'user_id', 'account_id', 'org_id', 'organization_id', 'repo_id',
                  'repository_id', 'book_id', 'shelf_id', 'workbook_id', 'note_id') or cl == 'owner_type':
            tgt = None
            if cl == 'owner_id' and row.get('owner_type'):
                tgt = _table_for(_singular(str(row['owner_type']).lower()), tables)
            elif cl != 'owner_type':
                tgt = _table_for(_singular(cl[:-3].rstrip('_')), tables)
            if cl == 'owner_type':
                continue
            parts.append(f'{c}={v}' + (f'({_label(con, tables, tgt, v)})' if tgt else ''))
    return ', '.join(parts)

def find_duplicates(db_path: str, seeds: dict):
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    tables = _tables(con)
    problems, info = [], []
    stats = {'names_checked': 0, 'names_found': 0}
    for (kind, name), mentions in sorted(seeds.items()):
        table = _table_for(kind, tables)
        if not table:
            continue
        textcols = [c for c, t in tables[table] if ('CHAR' in t or 'TEXT' in t or 'CLOB' in t or t == '')
                    and 'password' not in c.lower() and 'hash' not in c.lower()]

        idcols = [c for c in textcols if re.search(r'(?i)^(name|title|username|login|email|slug|handle|display_name|full_name)$', c)]
        if not idcols:
            continue
        stats['names_checked'] += 1
        where = ' or '.join(f'"{c}" = ?' for c in idcols)
        rows = [dict(r) | {'__rowid': r['__rowid']} for r in
                con.execute(f'select rowid as __rowid, * from "{table}" where {where}', [name] * len(idcols))]
        if len(rows) == 0:
            info.append({'kind': 'seed_missing', 'record_kind': kind, 'name': name, 'table': table})
            continue
        stats['names_found'] += 1
        if len(rows) < 2:
            continue
        fks = _container_fks(table, tables[table], tables)
        if fks and kind not in USERISH:
            groups = {}
            for r in rows:
                groups.setdefault(tuple(r.get(c) for c, _ in fks), []).append(r)
            dup_groups = [g for g in groups.values() if len(g) > 1]
            if not dup_groups:
                info.append({'kind': 'scoped_name_in_several_containers', 'record_kind': kind, 'name': name,
                             'table': table, 'rows': len(rows), 'containers': len(groups)})
                continue
            rows = [r for g in dup_groups for r in g]
            scope = 'same container (' + ', '.join(c for c, _ in fks) + ')'
        else:
            scope = 'global'
        problems.append({
            'kind': 'seed_duplicate', 'record_kind': kind, 'name': name, 'table': table,
            'rows': len(rows), 'scope': scope, 'mentions': mentions,
            'where': [_owner_desc(con, tables, table, {k: v for k, v in r.items() if k != '__rowid'}) or f'rowid={r["__rowid"]}'
                      for r in rows][:6],
        })
    con.close()
    info.append({'kind': 'coverage', **stats})
    return problems, info

def requirement_vacancies(requirement_text: str) -> list[dict]:
    from .seed_presence import _tree_of, vacancies
    tree = _tree_of(requirement_text)
    return vacancies(tree) if tree is not None else []

def _row_cells(con, tables, table: str, row: dict) -> set[str]:
    cells = {str(v) for v in row.values() if isinstance(v, str)}
    for c, v in row.items():
        m = re.match(r'(?i)^(.+?)_?id$', c)
        if v is None or not m or c.lower() == 'id':
            continue
        target = _table_for(_singular(m.group(1).rstrip('_').split('_')[-1]), tables)
        if target:
            try:
                cells.add(_label(con, tables, target, v))
            except sqlite3.Error:
                pass
    return cells

def _state_ok(row: dict, qualifiers: list[str]) -> bool:
    wanted = {q for q in qualifiers if q in ('open', 'draft')}
    if not wanted:
        return True
    states = [str(v).lower() for c, v in row.items() if re.match(r'(?i)^(state|status)$', c) and v is not None]
    drafts = [v for c, v in row.items() if re.match(r'(?i)^(is_)?draft$', c)]
    if 'draft' in wanted and any(v not in (None, 0, '0', 'false', False) for v in drafts):
        return True
    return not states or any(s in wanted for s in states)

def check_vacancies(db_path: str, vacs: list[dict]):
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    tables = _tables(con)
    probs, info = [], []
    for vac in vacs:
        if vac.get('kind') == 'unbound' or not vac.get('values'):
            continue
        words = re.findall(r'[A-Za-z]+', str(vac.get('thing') or ''))
        candidates = [t for t in (_table_for(_singular(w), tables) for w in reversed(words)) if t]
        if vac['kind'] == 'free' and not candidates:
            info.append({'kind': 'vacancy_unchecked', 'req': vac['req'], 'thing': vac.get('thing'), 'reason': 'no table for the stated kind'})
            continue
        hits = []
        for table in (candidates[:1] if candidates else list(tables)):
            for r in con.execute(f'select rowid as __rowid, * from "{table}" limit 5000'):
                row = {k: r[k] for k in r.keys() if k != '__rowid'}
                if vac['kind'] == 'absent':
                    if any(isinstance(v, str) and v in vac['values'] for v in row.values()):
                        hits.append(f'{table} rowid={r["__rowid"]}')
                elif all(v in _row_cells(con, tables, table, row) for v in vac['values']) and _state_ok(row, vac.get('qualifiers') or []):
                    state = next((f' {c}={v}' for c, v in row.items() if re.match(r'(?i)^(state|status)$', c)), '')
                    hits.append(f'{table} rowid={r["__rowid"]} `{_label(con, tables, table, r["__rowid"])}`{state} '
                                f'({_owner_desc(con, tables, table, row)[:120]})')
                if len(hits) >= 3:
                    break
        if hits:
            probs.append({'kind': 'vacancy_occupied', 'req': vac['req'], 'vacancy': vac['kind'], 'values': list(vac['values']),
                          'thing': vac.get('thing'), 'qualifiers': list(vac.get('qualifiers') or []), 'where': hits})
    con.close()
    return probs, info

def check_roles(db_path: str, requirement_text: str):
    accts = credential_accounts(requirement_text)
    reqs = role_requirements(requirement_text)
    if len(accts) != 1 or not reqs:
        return [], ([{'kind': 'role_check_skipped', 'reason': f'credentialed accounts={sorted(accts)}, role GIVENs={len(reqs)}'}] if reqs else [])
    acct = next(iter(accts))
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    tables = _tables(con)
    probs, info = [], []
    utab = _table_for('user', tables)
    if not utab:
        return [], [{'kind': 'role_check_skipped', 'reason': 'no user table'}]
    ucols = [c for c, _ in tables[utab]]
    ucol = next((c for c in ('username', 'login', 'name', 'email') if c in ucols), None)
    uids = [r[0] for r in con.execute(f'select id from "{utab}" where "{ucol}"=?', (acct,))] if ucol and 'id' in ucols else []
    if not uids:
        con.close()
        return [], [{'kind': 'role_check_skipped', 'reason': f'account {acct} not found in {utab}'}]
    for ckind, role, cnames in reqs:
        ctab = _table_for(ckind, tables)
        if not ctab:
            continue
        ccols = [c for c, _ in tables[ctab]]
        ncol = next((c for c in ('name', 'display_name', 'title', 'slug') if c in ccols), None)
        cids = []
        for nm in cnames:
            for c in [x for x in ('name', 'display_name', 'title', 'slug') if x in ccols]:
                cids += [r[0] for r in con.execute(f'select id from "{ctab}" where "{c}"=?', (nm,))]
        cids = sorted(set(cids))
        if not cids:
            continue

        owner_cols = [c for c in ccols if re.match(r'(?i)^(owner|owner_user|owner_account|created_by|creator)(_id)?$', c)]
        owned = any(con.execute(f'select 1 from "{ctab}" where id=? and "{oc}"=?', (cid, uid)).fetchone()
                    for cid in cids for uid in uids for oc in owner_cols)
        if owned and role in ('owner', 'admin', 'administrator'):
            continue

        cfk_names = {f'{ckind}_id', f'{ckind[:3]}_id', f'{ckind}id', 'organization_id' if ckind == 'organization' else ''}
        found = []
        for t, cols in tables.items():
            names = [c for c, _ in cols]
            cfk = next((c for c in names if c.lower() in cfk_names), None)
            ufk = next((c for c in names if re.match(r'(?i)^(user|account|member)_?id$', c)), None)
            rcol = next((c for c in names if re.match(r'(?i)^(role|permission|access|level)$', c)), None)
            if not (cfk and ufk and rcol):
                continue
            for cid in cids:
                for uid in uids:
                    for (rv,) in con.execute(f'select "{rcol}" from "{t}" where "{cfk}"=? and "{ufk}"=?', (cid, uid)):
                        found.append((t, cid, str(rv)))
        want = {'owner': {'owner'}, 'admin': {'admin', 'owner', 'administrator'},
                'administrator': {'admin', 'owner', 'administrator'},
                'maintainer': {'maintainer', 'maintain', 'admin', 'owner'},
                'member': None, 'editor': {'editor', 'admin', 'owner'}}[role]
        if want is None:
            continue
        if found and not any(r.lower() in want for (_, _, r) in found):
            probs.append({'kind': 'credential_role', 'account': acct, 'container_kind': ckind,
                          'containers': list(cnames), 'required_role': role,
                          'data_roles': sorted({f'{t}:{r}' for (t, _, r) in found})})
        elif not found:
            info.append({'kind': 'credential_role_unlinked', 'account': acct, 'container_kind': ckind,
                         'containers': list(cnames), 'required_role': role})
    con.close()
    return probs, info

HARNESS_JS = r"""// Seed harness: runs a deliverable's own DB initialisation against a fresh SQLite file, the way the app does at
// start, then (phase 2) the zero-argument "ensure*/seed*" routines that routes call lazily. Writes two snapshots:
//   <out>/boot.db  - state after initialisation;  <out>/lazy.db - state after the lazy seed routines as well
// Usage: node seed_harness.js <backendCopyDir> <outDir>  (NODE_PATH=<deliverable>/backend/node_modules,
// cwd=<backendCopyDir>; ARC_DB_FILE/DATABASE_FILE point into <outDir>)
'use strict';
const fs = require('fs');
const path = require('path');

const backendDir = path.resolve(process.argv[2]);
const outDir = path.resolve(process.argv[3]);
const dbDir = path.join(backendDir, 'src', 'database');
const log = (m) => process.stdout.write(`[harness] ${m}\n`);
const withTimeout = (p, ms, label) => Promise.race([
  Promise.resolve(p),
  new Promise((_, rej) => setTimeout(() => rej(new Error(`timeout ${label}`)), ms)),
]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

process.on('unhandledRejection', (e) => log(`unhandledRejection: ${e && e.message}`));
process.on('uncaughtException', (e) => log(`uncaughtException: ${e && e.message}`));

async function snapshot(runtime, initDb, name) {
  const target = path.join(outDir, name).replace(/\\/g, '/');
  try { fs.unlinkSync(target); } catch (_) { /* none */ }
  const sql = `VACUUM INTO '${target.replace(/'/g, "''")}'`;
  try {
    await runtime.exec(sql);
  } catch (e) {
    if (!initDb || typeof initDb.getDb !== 'function') throw e;
    const db = initDb.getDb();
    await new Promise((res, rej) => db.exec(sql, (err) => (err ? rej(err) : res())));
    log(`snapshot via getDb (runtime.exec failed: ${e.message})`);
  }
  log(`snapshot ${name}`);
}

async function main() {
  process.chdir(backendDir);
  let initDb = null;
  let runtime = null;
  try { initDb = require(path.join(dbDir, 'init_db.js')); } catch (e) { log(`init_db load failed: ${e.message}`); }
  try { runtime = require(path.join(dbDir, 'db_runtime.js')); } catch (e) { log(`db_runtime load failed: ${e.message}`); }
  try { require(path.join(backendDir, 'src', 'app.js')); log('app loaded'); } catch (e) { log(`app load failed: ${e.message}`); }
  if (initDb && typeof initDb.initializeDatabase === 'function') {
    await withTimeout(initDb.initializeDatabase(), 35000, 'initializeDatabase');
    log('initializeDatabase done');
  } else if (runtime) {
    await withTimeout(runtime.get('SELECT 1'), 35000, 'runtime init');
    log('runtime init done');
  } else {
    throw new Error('no init entry point');
  }
  await sleep(1500);
  await snapshot(runtime, initDb, 'boot.db');

  const invoked = [];
  const files = fs.readdirSync(dbDir).filter((f) => /seed|ensure|fixture/i.test(f) && f.endsWith('.js'));
  for (const f of files) {
    let mod;
    try { mod = require(path.join(dbDir, f)); } catch (e) { log(`load ${f} failed: ${e.message}`); continue; }
    const cands = [];
    if (typeof mod === 'function' && mod.length === 0) cands.push([`${f}:default`, mod]);
    if (mod && typeof mod === 'object' || typeof mod === 'function') {
      for (const [k, v] of Object.entries(mod)) {
        if (typeof v === 'function' && v.length === 0 && /^(ensure|seed)/i.test(k) && v !== mod) cands.push([`${f}:${k}`, v]);
      }
    }
    const seen = new Set();
    for (const [label, fn] of cands) {
      if (seen.has(fn)) continue;
      seen.add(fn);
      try { await withTimeout(fn(), 8000, label); invoked.push(`${label}:ok`); } catch (e) { invoked.push(`${label}:err(${String(e && e.message).slice(0, 80)})`); }
    }
  }
  log(`lazy invoked: ${JSON.stringify(invoked)}`);
  await sleep(500);
  await snapshot(runtime, initDb, 'lazy.db');
  if (initDb && typeof initDb.closeDb === 'function') { try { await initDb.closeDb(); } catch (_) { /* ignore */ } }
}

main().then(() => { log('done'); process.exit(0); }, (e) => { log(`FAILED: ${e && e.stack}`); process.exit(2); });
"""

def _backend_dir(project_dir: str) -> str | None:
    for cand in (os.path.join(project_dir, 'backend'), project_dir):
        if os.path.isfile(os.path.join(cand, 'src', 'database', 'init_db.js')) or \
           os.path.isfile(os.path.join(cand, 'src', 'database', 'db_runtime.js')):
            return cand
    return None

def run_seed(project_dir: str, deadline: float, node_modules: str | None = None):
    backend = _backend_dir(project_dir)
    if not backend:
        return None, {'skipped': 'no backend/src/database'}, None
    nm = node_modules or os.path.join(backend, 'node_modules')
    if not os.path.isdir(os.path.join(nm, 'sqlite3')):
        return None, {'skipped': 'node_modules/sqlite3 missing'}, None
    node = shutil.which('node')
    if not node:
        return None, {'skipped': 'no node on PATH'}, None
    wd = tempfile.mkdtemp(prefix='arc-seed-identity-')
    harness = os.path.join(wd, 'seed_harness.js')
    with open(harness, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(HARNESS_JS)
    dst = os.path.join(wd, 'backend')
    ignore = shutil.ignore_patterns('node_modules', '*.db', '*.db-wal', '*.db-shm', '*.sqlite', '*.sqlite3',
                                    '.arc-test-db', 'test-results', 'playwright-report', '.git')
    out = os.path.join(wd, 'out')
    dbfile = os.path.join(out, 'app.db')
    env = dict(os.environ)
    env.update(NODE_PATH=nm, ARC_DB_FILE=dbfile, DATABASE_FILE=dbfile, PORT='0', NODE_ENV='test')
    t0 = time.time()
    logs = []
    rc, text = None, ''

    for attempt in (1, 2):
        left = deadline - time.time()
        if left < 5:
            logs.append(f'attempt {attempt} not started: {left:.0f}s left')
            break
        shutil.rmtree(dst, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(backend, dst, ignore=ignore)
        os.makedirs(out)
        try:
            p = subprocess.run([node, harness, dst, out], cwd=dst, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=left)
            text, rc = (p.stdout + p.stderr)[-4000:], p.returncode
        except subprocess.TimeoutExpired as e:
            text, rc = f'timeout: {str(e.stdout)[-2000:] if e.stdout else ""}', -1
        logs.append(f'attempt {attempt} rc={rc}')
        if rc == 0 or text.startswith('timeout'):
            break
    meta = {'seconds': round(time.time() - t0, 1), 'rc': rc, 'attempts': logs, 'log_tail': text[-600:]}
    boot, lazy = os.path.join(out, 'boot.db'), os.path.join(out, 'lazy.db')
    meta['boot'] = os.path.isfile(boot)
    meta['lazy'] = os.path.isfile(lazy)
    return (boot if meta['boot'] else None, lazy if meta['lazy'] else None), meta, wd

def check(requirement_text: str, project_dir: str, time_limit: float = 60.0, node_modules: str | None = None) -> dict:
    deadline = time.time() + time_limit
    seeds = parse_seed_names(requirement_text)
    try:
        vacs = requirement_vacancies(requirement_text)
    except Exception as exc:
        vacs = []
        seeds_meta_error = repr(exc)
    else:
        seeds_meta_error = None
    res = {'problems': [], 'info': [], 'meta': {'seed_names': len(seeds), 'vacancies': vacs}}
    if seeds_meta_error:
        res['meta']['vacancy_error'] = seeds_meta_error
    if not seeds and not vacs:
        res['meta']['skipped'] = 'no seed sentences'
        return res
    dbs, meta, wd = run_seed(str(project_dir), deadline, node_modules=node_modules)
    try:
        res['meta'].update(meta)
        if not dbs or not (dbs[0] or dbs[1]):
            res['meta'].setdefault('skipped', 'seed run produced no database')
            return res
        boot, lazy = dbs
        final = lazy or boot
        probs, info = find_duplicates(final, seeds)
        rprobs, rinfo = check_roles(final, requirement_text)
        boot_keys = set()
        if boot and lazy:
            bp, _ = find_duplicates(boot, seeds)
            brp, _ = check_roles(boot, requirement_text)
            boot_keys = {(p['record_kind'], p['name']) for p in bp} | {('role', p['account']) for p in brp}
        for p in probs:
            p['phase'] = 'boot' if (p['record_kind'], p['name']) in boot_keys or not lazy else 'lazy'
        for p in rprobs:
            p['phase'] = 'boot' if ('role', p['account']) in boot_keys or not lazy else 'lazy'
        vprobs, vinfo = check_vacancies(final, vacs) if vacs else ([], [])
        if vprobs and boot and lazy:
            boot_vac = {(p['req'], tuple(p['values'])) for p in check_vacancies(boot, vacs)[0]}
        else:
            boot_vac = set()
        for p in vprobs:
            p['phase'] = 'boot' if (p['req'], tuple(p['values'])) in boot_vac or not lazy else 'lazy'
        res['problems'] = probs + rprobs + vprobs
        res['info'] = info + rinfo + vinfo
        return res
    finally:
        if wd:
            shutil.rmtree(wd, ignore_errors=True)

def format_vacancy(v: dict) -> str:
    values = ' + '.join(f"`{x}`" for x in v.get('values') or [])
    if v.get('kind') == 'absent':
        return f"{v['req']}: {values} ({v.get('thing')}) must not exist"
    if v.get('kind') == 'free':
        return f"{v['req']}: no {' or '.join(v.get('qualifiers') or [])} {v.get('thing')} on {values}"
    return f"{v['req']}: (no values) {str(v.get('sentence'))[:140]}"

def format_problem(p: dict) -> str:
    if p['kind'] == 'vacancy_occupied':
        what = ' + '.join(f"`{x}`" for x in p['values'])
        state = ' or '.join(p.get('qualifiers') or [])
        return (f"[{p['phase']}] vacancy of {p['req']} occupied: the requirement says no {state + ' ' if state else ''}"
                f"{p.get('thing')} {'on' if p['vacancy'] == 'free' else 'named'} {what} exists when the scenario starts, "
                f"the seeded data has " + ' | '.join(p['where']))
    if p['kind'] == 'seed_duplicate':
        return (f"[{p['phase']}] seed {p['record_kind']} `{p['name']}` exists in {p['rows']} rows of "
                f"`{p['table']}` ({p['scope']}): " + ' | '.join(p['where']))
    return (f"[{p['phase']}] credentialed account `{p['account']}` needs {p['container_kind']} role "
            f"{p['required_role']} in {p['containers']} but data has {p['data_roles']}")

def log_seed_identity(requirement_text: str, project_dir, log=print, time_limit: float = 60.0) -> list[str]:
    lines: list[str] = []
    try:
        res = check(requirement_text, str(project_dir), time_limit=time_limit)
        meta = res.get('meta', {})
        vacs = meta.get('vacancies') or []
        if vacs:
            log(f"[seed-identity] {len(vacs)} vacancy precondition(s) stated: " + "; ".join(format_vacancy(v) for v in vacs[:10])[:900])
        if meta.get('skipped'):
            log(f"[seed-identity] skipped: {meta['skipped']}"
                + (f" (rc={meta.get('rc')}, {meta.get('seconds')}s)" if meta.get('rc') is not None else ""))
            return lines
        coverage = next((i for i in res.get('info', []) if i.get('kind') == 'coverage'), {})
        lines = [format_problem(p) for p in res.get('problems', [])]
        log(f"[seed-identity] (diagnostic, not reported) {len(lines)} problem(s); {meta.get('seconds')}s, "
            f"{coverage.get('names_found', 0)}/{coverage.get('names_checked', 0)} stated seed name(s) found in the data")
        for line in lines[:12]:
            log(f"[seed-identity]   {line[:300]}")
    except Exception as exc:
        log(f"[seed-identity] check failed: {exc!r}")
    return lines

def _vacancy_table_stated(p: dict) -> bool:
    if p.get('vacancy') == 'free':
        return True
    hit_tables = {str(w).split(' rowid=')[0] for w in p.get('where') or [] if ' rowid=' in str(w)}
    if not hit_tables:
        return False
    tables = {t: [] for t in hit_tables}
    words = re.findall(r'[A-Za-z]+', str(p.get('thing') or ''))
    return any(_table_for(_singular(w), tables) for w in words)

def clear_problems(problems) -> list[dict]:
    out = []
    for p in problems or []:
        if not isinstance(p, dict):
            continue
        if p.get('kind') == 'credential_role':
            if p.get('account') and p.get('required_role') and p.get('containers') and p.get('data_roles'):
                out.append(p)
        elif p.get('kind') == 'vacancy_occupied':
            if p.get('req') and p.get('values') and p.get('where') and _vacancy_table_stated(p):
                out.append(p)
    return out

def repair_findings(problems) -> list[dict]:
    from .settings import setting_on

    if not setting_on("seed_identity_findings", True):
        return []
    findings = []
    for p in clear_problems(problems):
        p = dict(p, phase=p.get('phase') or 'boot')
        if p['kind'] == 'credential_role':
            containers = ', '.join(map(str, p['containers']))
            findings.append({
                'check': 'seed-identity/credential_role',
                'where': f"seed data: membership of `{p['account']}` in {containers}",
                'requirement': (f"(a GIVEN says a {p.get('container_kind')} {p['required_role']} is signed in, and the "
                                f"requirement states the password of `{p['account']}`)"),
                'problem': format_problem(p),
                'fix': (f"make the application's seeding give `{p['account']}` the {p['required_role']} role in "
                        f"{containers} (initializeDatabase / the ensure*/seed* routines); keep every other stated seed "
                        "record as it is"),
            })
        else:
            findings.append({
                'check': 'seed-identity/vacancy_occupied',
                'where': ('seed data: ' + ' | '.join(map(str, p['where'])))[:300],
                'requirement': f"({p['req']} states that this record does not exist when the scenario starts)",
                'problem': format_problem(p),
                'fix': ("make the application's seeding (initializeDatabase / the ensure*/seed* routines) leave this "
                        "vacancy free -- do not seed that record; keep every other stated seed record as it is"),
            })
    return findings

def log_seed_identity_findings(requirement_text: str, project_dir, log=print,
                               time_limit: float = 60.0) -> tuple[list[str], list[dict]]:
    lines: list[str] = []
    findings: list[dict] = []
    try:
        res = check(requirement_text, str(project_dir), time_limit=time_limit)
        meta = res.get('meta', {})
        vacs = meta.get('vacancies') or []
        if vacs:
            log(f"[seed-identity] {len(vacs)} vacancy precondition(s) stated: " + "; ".join(format_vacancy(v) for v in vacs[:10])[:900])
        if meta.get('skipped'):
            log(f"[seed-identity] skipped: {meta['skipped']}"
                + (f" (rc={meta.get('rc')}, {meta.get('seconds')}s)" if meta.get('rc') is not None else ""))
            return lines, findings
        coverage = next((i for i in res.get('info', []) if i.get('kind') == 'coverage'), {})
        lines = [format_problem(p) for p in res.get('problems', [])]
        log(f"[seed-identity] (diagnostic, not reported) {len(lines)} problem(s); {meta.get('seconds')}s, "
            f"{coverage.get('names_found', 0)}/{coverage.get('names_checked', 0)} stated seed name(s) found in the data")
        for line in lines[:12]:
            log(f"[seed-identity]   {line[:300]}")
        findings = repair_findings(res.get('problems', []))
        if findings:
            log(f"[seed-identity] {len(findings)} problem(s) with clear evidence (role / vacancy) handed to the repair")
    except Exception as exc:
        log(f"[seed-identity] check failed: {exc!r}")
    return lines, findings
