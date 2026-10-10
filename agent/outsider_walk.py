from __future__ import annotations

import json
import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import nav_edges, session_probe as sp
from .seed_identity import requirement_texts

QUOTE = r'[`"“]([^`"”\n]{1,120})[`"”]'
SIGNED = re.compile(r'(?i)\b(?:signs?[- ]in as|signed[- ]in|logs?[- ]in as|logged[- ]in|authenticated)\b')
MUTATION = re.compile(r'(?i)^(?:sign\s*out|log\s*out|delete|remove|save|submit|confirm|update|create|add|send|cancel|reset)\b')

def _flat(text):
    return ' '.join(str(text).split())

def _credentials(requirement):

    extra = []
    for text in requirement_texts(requirement):
        for match in re.finditer(r'(?i)\b(?:the system (?:has )?)?pre[- ]provisions?\s+|\bpre[- ]provisioned\s+', text):
            tail = text[match.end():]
            tail = re.split(r'\.\s+(?=[A-Z])', tail, maxsplit=1)[0]
            if re.match(r'\s*[`"“]', tail):
                tail = 'account ' + tail
            if len(re.findall(r'(?i)\bpassword\s+' + QUOTE, tail)) == 1 and len(re.findall(r'(?i)\b(?:account|username|user)\s+' + QUOTE, tail)) == 1:
                extra.append('Seed values: ' + tail)
    creds = sp.credentials(requirement)
    for text in extra:
        for cred in sp.credentials(json.dumps({'description': text})):
            if cred not in creds:
                creds.append(cred)

    merged = []
    for cred in creds:
        if not cred['account'] and cred['email']:
            named = [c for c in creds if c['account'] and c['email'] == cred['email'] and c['password'] == cred['password']]
            if len({c['account'] for c in named}) == 1:
                cred = named[0]
        if cred not in merged:
            merged.append(cred)
    return merged

def _sections(requirement):
    yaml = getattr(nav_edges, 'yaml', None)
    try:
        data = yaml.safe_load(requirement) if yaml else None
    except Exception:
        data = None
    if not isinstance(data, (dict, list)):
        if re.search(r'(?m)^\s*(?:scenarios|children|steps):', requirement):
            raise ValueError('structured requirement could not be parsed')
        return [(_flat(requirement), [_flat(requirement)])]
    out = []

    def visit(value):
        if isinstance(value, dict):
            desc = _flat(value.get('description', ''))
            scenarios = []
            for scenario in value.get('scenarios', []):
                scenarios.append(' '.join(_flat(step.get('content', '')) for step in scenario.get('steps', [])))
            if desc or scenarios:
                out.append((desc, scenarios))
            for child in value.get('children', []):
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    return out

def _navigation(text):

    prefix = re.split(r'(?i)\b(?:enters?|types?|fills?|selects?|submits?|creates?|adds?|removes?|deletes?|signs? out)\b', text, maxsplit=1)[0]
    result = {}
    for m in re.finditer(r'(?i)\b(?:clicks?|opens?|visits?|navigates? to)\s+([^.;,`"“]{0,65})' + QUOTE, prefix):
        lead, name = m.group(1), m.group(2)
        before = prefix[max(0, m.start()-60):m.start()]
        if re.search(r"(?i)\b(?:cannot|can't|must not|does not|do not|never|not allowed to|unable to)\s+(?:\w+\s+){0,3}$", before):
            continue
        if re.search(r'(?i)\b(?:not|never|cannot|no|without|checks?|verifies?|heading|textbox|field|option)\b', lead):
            continue
        if MUTATION.search(name) or re.search(r'(?i)^(?:sign[- ]?in|log[- ]?in)$', name):
            continue
        result[name] = prefix[max(0, m.start()-30):m.end()]
    return result

def _plans(requirement, creds):
    sections = _sections(requirement)
    plans = []
    for cred in creds:
        names = [x for x in (cred['account'], cred['email']) if x]
        targets = {}
        for desc, scenarios in sections:
            eligible = []
            for scenario in scenarios:
                if not SIGNED.search(scenario) or not set(names).intersection(re.findall(QUOTE, scenario)):
                    continue

                actor = re.search(r'(?i)\b(?:signs? in|logs? in) as\s+' + QUOTE, scenario)
                if actor and cred['account'] and actor.group(1) != cred['account']:
                    continue
                if re.search(r'(?i)\b(?:incorrect password|unknown username|unknown address)\b', scenario):
                    continue
                eligible.append(scenario)
                start = SIGNED.search(scenario).start()
                targets.update(_navigation(scenario[start:]))

            generic = bool(re.search(r'(?i)\b(?:signed-in users|authenticated account menu)\b', desc))
            if eligible or generic:
                for edge in nav_edges.extract_edges(desc):
                    visitor_source = re.search(r'(?i)sign[- ]in|log[- ]in|sign[- ]up|registration|recovery|account-access', edge.get('source') or '')
                    if not visitor_source and not edge.get('data') and not MUTATION.search(edge['name']):
                        targets.setdefault(edge['name'], edge['quote'])

                for m in re.finditer(QUOTE + r'\s+is (?:the |a )?(?:\w+ ){0,3}page', desc):
                    targets.setdefault(m.group(1), m.group(0))
        targets = {n: q for n, q in targets.items() if '<' not in n and not re.search(r'(?i)^(?:sign[- ]?in|log[- ]?in)$', n)}
        plans.append({'credential': cred, 'targets': targets,
                      'first': _first_targets(requirement, cred),
                      'paths': _stated_paths(requirement, cred)})

    selected, covered = [], set()
    while plans:
        plan = max(plans, key=lambda p: len(set(p['targets']) - covered))
        plans.remove(plan)
        if selected and not set(plan['targets']) - covered and not plan['first'] and not plan['paths']:
            continue
        selected.append(plan)
        covered.update(plan['targets'])
    return selected

def _leaves(requirement):
    yaml = getattr(nav_edges, 'yaml', None)
    data = yaml.safe_load(requirement) if yaml else None
    out = []
    def visit(n):
        if isinstance(n, list):
            for c in n:
                visit(c)
        elif isinstance(n, dict):
            if not n.get('children'):
                out.append(n)
            for c in n.get('children', []):
                visit(c)
    visit(data)
    return out or [{'description': requirement, 'scenarios': []}]

def _path_steps(when):

    masked = re.sub(QUOTE, lambda m: ' ' * len(m.group(0)), when)
    actions = re.compile(r'(?i)\b(?:opens?|clicks?|selects?|enters?|types?|fills?|submits?|'
                         r'creates?|adds?|removes?|deletes?|signs? out|logs? out|'
                         r'visits?|navigates?|imports?|uploads?|checks?|unchecks?|'
                         r'changes?|chooses?|press(?:es)?|saves?|confirms?)\b')
    result = []
    for action in actions.finditer(masked):
        verb = action.group().lower()
        if verb not in ('open', 'opens', 'click', 'clicks', 'select', 'selects'):
            break
        if re.search(r"(?i)\b(?:cannot|can't|not|doesn't|don't|shouldn't|never|unable to)\s*$", masked[:action.start()]):
            break
        tail = when[action.end():]
        if verb in ('select', 'selects'):
            match = re.match(r'\s+the\s+tab\s+' + QUOTE, tail, re.I)
        else:
            match = re.match(r'\s+(?:(?:the|a|an|visible|exact|link|tab|menu|button|menuitem|named|record|workbook|repository|organization|team)\s+)*' + QUOTE, tail, re.I)
        if not match:
            break
        name = match.group(1)
        if MUTATION.search(name) or re.search(r'[<>{}\[\]]|\$\w|\.\.\.|…', name):
            break
        if re.search(r'(?i)^(?:sign|log)[ -]?in$', name):
            break
        if re.match(r'(?i)\s+(?:cell|gridcell|textbox|field|input|checkbox|radio|option)\b', tail[match.end():]):
            break
        result.append({'name': name, 'menu': verb in ('open', 'opens') and bool(re.search(r'(?i)\bmenu$', name))})
    return result

def _stated_paths(requirement, cred):
    out = []
    for leaf_index, leaf in enumerate(_leaves(requirement)):
        for scenario_index, scenario in enumerate(leaf.get('scenarios', [])):
            steps = scenario.get('steps', [])
            given = ' '.join(_flat(s.get('content', '')) for s in steps if s.get('keyword', '').upper() == 'GIVEN')
            when = ' '.join(_flat(s.get('content', '')) for s in steps if s.get('keyword', '').upper() == 'WHEN')
            actors = re.findall(r'(?i)\b(?:signs?[- ]in|logs?[- ]in|signed[- ]in|logged[- ]in) as\s+' + QUOTE, given + ' ' + when)
            if len(set(actors)) != 1 or actors[0] not in (cred['account'], cred['email']):
                continue
            if re.search(r"(?i)\b(?:not|never|without)\s+(?:being\s+)?(?:signed|logged)[- ]in", given + ' ' + when):
                continue
            if re.search(r'(?i)incorrect password|unknown username|unknown address', given + ' ' + when):
                continue
            if re.search(r'(?i)\b(?:(?:already|currently|is) (?:on|at)|has opened|has navigated|newly created|just imported)\b', given):
                continue
            path = _path_steps(when)
            if not path:
                continue

            if re.search(r'(?i)\b(?:created|creates?|imported|imports?|renamed|renames?)\b', given) and any(s['name'] in re.findall(QUOTE, given) for s in path):
                continue
            out.append({'key': f'{leaf_index}:{scenario_index}',
                        'leaf': str(leaf.get('id', leaf.get('name', 'leaf'))),
                        'scenario': str(scenario.get('name', f'scenario {scenario_index + 1}')),
                        'steps': path, 'quote': ' → '.join(s['name'] for s in path)})
    return out

def _first_targets(requirement, cred):
    out = []
    texts = ' '.join(requirement_texts(requirement))
    fixed = set(re.findall(QUOTE + r'\s+(?:link|tab|menu)\b', texts, re.I))
    fixed.update(edge['name'] for edge in nav_edges.extract_edges(requirement) if not edge.get('data'))
    for leaf in _leaves(requirement):
        desc = _flat(leaf.get('description', ''))
        for scenario in leaf.get('scenarios', []):
            steps = scenario.get('steps', [])
            given = ' '.join(_flat(s.get('content', '')) for s in steps if s.get('keyword', '').upper() == 'GIVEN')
            when = ' '.join(_flat(s.get('content', '')) for s in steps if s.get('keyword', '').upper() == 'WHEN')
            actors = re.findall(r'(?i)\b(?:signs? in|logs? in|signed in|logged in) as\s+' + QUOTE, given + ' ' + when)
            if len(set(actors)) != 1:
                continue
            actor = re.search(r'(?i)\b(?:signs? in|logs? in|signed in|logged in) as\s+' + QUOTE, given + ' ' + when)
            if not actor or actor.group(1) not in (cred['account'], cred['email']):
                continue
            if re.search(r'(?i)\b(?:(?:already|currently|is) (?:on|at)|has opened|has navigated)\b', given):
                continue
            if re.search(r'(?i)incorrect password|unknown username|unknown address', when):
                continue
            signed = SIGNED.search(when)
            flow = when[signed.end():] if signed else when

            prefix = re.split(r'(?i)\b(?:enters?|types?|fills?|selects?|submits?|creates?|adds?|removes?|deletes?|signs? out)\b', flow, 1)[0]
            action = re.search(r'(?i)\b(?:clicks?|opens?|visits?|navigates? to)\b', prefix)
            source = 'WHEN'
            if not action and not re.search(r'(?i)\b(?:enters?|types?|fills?|creates?|submits?)\b', flow):
                prefix = desc
                action = re.search(r'(?i)\b(?:clicks?|opens?|visits?|navigates? to)\b', prefix)
                source = 'description'
            if not action:
                continue
            if re.search(r"(?i)\b(?:cannot|can't|must not|does not|never|unable to)\s*$", prefix[:action.start()]):
                continue

            if source == 'description' and re.search(r'(?i)\b(?:on|from|in|inside|within) (?:the|a|an|its|their|that) [\w\s/-]{0,40}\b(?:page|tab|view|overview|settings|editor|list|dialog|panel)\b', prefix[:action.start()]):
                continue
            tail = prefix[action.start():]
            m = re.match(r'(?i)(?:clicks?|opens?|visits?|navigates? to)\s+(?:(?:the|a|visible|exact|link|tab|menu|named|record|workbook|repository|organization|team)\s+)*' + QUOTE, tail)
            if not m or MUTATION.search(m.group(1)) or re.search(r'(?i)^(?:sign|log)[ -]?in$', m.group(1)):
                continue
            name = m.group(1)
            if re.match(r'(?i)\s+(?:cell|gridcell|textbox|field|input|checkbox|radio|option|button)\b', tail[m.end():]) and not re.search(r'(?i)menu$', name):
                continue
            typed = re.search(r'(?i)\b(?:link|tab|menu|record|workbook|repository|organization|team)\b', m.group(0).split(name)[0])
            seeded = re.search(r'(?i)\bseed(?:ed| values)?\b', given) and name in re.findall(QUOTE, given)
            if name not in fixed and not typed and not seeded and not re.search(r'(?i)menu$', name):
                continue
            if '<' in name or re.search(r'(?i)\b(?:newly|just) (?:created|imported)\b', given):
                continue
            prior = given + ' ' + (when[:signed.start()] if signed else '')
            if re.search(r'(?i)\b(?:creates?|created|imports?|imported|renames?|renamed)\b', prior) and name in re.findall(QUOTE, prior):
                continue

            menu = None
            for edge in nav_edges.extract_edges(desc):
                if edge['name'] == name and re.search(r'(?i)(?:account|user|profile) menu', edge.get('source') or ''):
                    menu = edge['source']
            if re.search(re.escape(m.group(0)) + r'\s+(?:link\s+)?(?:in|from) the account menu', tail, re.I):
                menu = 'account menu'
            out.append({'name': name, 'menu': menu, 'leaf': str(leaf.get('id', leaf.get('name', 'leaf'))),
                        'scenario': str(scenario.get('name', 'scenario')), 'source': source, 'quote': m.group(0)})
    return out

def _anonymous_plan(requirement):
    exits, flows, notes = {}, [], []
    for desc, _ in _sections(requirement):
        for sentence in re.split(r'(?<=[.;])\s+', desc):
            predicate = r'\s+(?:that |which )?(?:opens?|returns?|navigates?|takes?)\b'
            names = re.findall(QUOTE + r'\s+(?:navigation )?(?:link|tab)' + predicate, sentence, re.I)
            names += re.findall(r'(?i)(?:navigation )?(?:link|tab) (?:named )?' + QUOTE + predicate, sentence)
            for name in names:
                if re.search(r'(?i)\b(?:link|tab)\b', sentence) and re.search(r'(?i)\b(?:opens?|returns?|navigates?|takes?)\b', sentence):
                    if re.search(r'(?i)\b(?:page|editor|overview|list)\b', sentence) and not MUTATION.search(name):
                        lead = sentence.split(name, 1)[0]
                        exits[name] = {'name': name, 'quote': sentence,
                                       'source': 'home' if re.search(r'(?i)home page', lead) else 'editor' if re.search(r'(?i)editor', lead) else None,
                                       'home': bool(re.search(r'(?i)(?:to|opens?|returns? to) (?:the )?(?:workbook )?home page', sentence)), 'record': False}
    for leaf in _leaves(requirement):
        desc = _flat(leaf.get('description', ''))
        scenarios = leaf.get('scenarios', [])
        if re.search(r'(?i)link whose accessible name is the (\w+) name', desc) and re.search(r'(?i)clicks the link.{0,40}editor', desc):
            for scenario in scenarios:
                for step in scenario.get('steps', []):
                    if step.get('keyword', '').upper() == 'GIVEN':
                        for name in re.findall(r'(?i)seeded (?:workbook|record|repository)\s+' + QUOTE, _flat(step.get('content', ''))):
                            exits[name] = {'name': name, 'quote': desc.split('Page reference:')[0], 'source': 'home', 'home': False, 'record': True}
        if not re.search(r'(?i)(?:after (?:a )?successful import|after creation succeeds|new workbook opens)', desc):
            continue
        leaf_id = str(leaf.get('id', leaf.get('name', 'leaf')))
        if re.search(r'(?i)\bCSV\b', desc):
            entry = re.search(r'(?i)clicking the\s+' + QUOTE + r'\s+button', desc)
            field = re.search(r'(?i)file control labeled\s+' + QUOTE, desc)
            submit = re.search(r'(?i)and a\s+' + QUOTE + r'\s+button', desc)
            values = []
            for scenario in scenarios:
                for step in scenario.get('steps', []):
                    text = _flat(step.get('content', ''))
                    if step.get('keyword', '').upper() == 'WHEN' and 'concrete values' in text:
                        values = re.findall(QUOTE, text.split('concrete values', 1)[1].split('.', 1)[0])
                        break
                if values:
                    break
            if entry and field and submit and values and re.search(r'(?i)file name.{0,50}\.csv extension removed', desc) and re.search(r'(?i)Sheet1 opens', desc):
                stream = io.StringIO(newline='')
                csv.writer(stream).writerow(values)
                flows.append({'kind': 'csv', 'entry': entry.group(1), 'field': field.group(1), 'submit': submit.group(1),
                              'csv': stream.getvalue(), 'values': values, 'leaf': leaf_id, 'quote': desc})
            else:
                notes.append(leaf_id + ': import controls, values or success destination uncertain')
        else:
            entry = re.search(r'(?i)button with (?:the )?accessible name\s+' + QUOTE, desc)
            submit = re.search(r'(?i)submit button is named\s+' + QUOTE, desc)
            for scenario in scenarios:
                when = ' '.join(_flat(s.get('content', '')) for s in scenario.get('steps', []) if s.get('keyword', '').upper() == 'WHEN')
                fields = re.findall(r'(?i)enters?\s+' + QUOTE + r'\s+in\s+(?:(?:the|visibly|labeled|textbox|field)\s+)*' + QUOTE, when)
                if entry and submit and fields and len(fields) == 1 and re.search(r'(?i)name', fields[0][1]):
                    flows.append({'kind': 'create', 'entry': entry.group(1), 'submit': submit.group(1), 'fields': fields,
                                  'name': fields[0][0], 'leaf': leaf_id, 'quote': desc})
                    break
            else:
                notes.append(leaf_id + ': creation input is not explicit; skipped')
    return {'credential': {'account': '', 'email': ''}, 'anonymous': True, 'targets': {}, 'first': [],
            'exits': list(exits.values()), 'flows': flows, 'notes': notes}

RUNNER = r'''
const fs = require('fs'), path = require('path');
const {chromium} = require('@playwright/test');
let input=''; process.stdin.on('data', x=>input+=x);
process.stdin.on('end', ()=>run(JSON.parse(input)));
async function run(cfg) {
 let browser;
 const result={completed:false, walks:[], diagnostic:'', notes:[]};
 const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
 const relative=u=>{const x=new URL(u);return x.pathname+x.search+x.hash};
 const left=()=>Math.max(1,cfg.deadline-Date.now());
 const check=()=>{if(left()<700)throw Error('deadline reached')};
 const record=pid=>{if(cfg.pidFile&&pid)fs.appendFileSync(cfg.pidFile,String(pid)+'\n')};
 const danger=/^(sign\s*out|log\s*out|delete|remove|save|submit|confirm|update|create|add|send|cancel|reset|close|merge|archive|leave|transfer|revoke|approve|reject|block|fork|publish|deactivate|disable|lock|unlock|make (?:private|public)|change visibility)\b/i;
 const selector='a[href],button,[role="link"],[role="tab"],[role="menuitem"],summary,[role="button"]';
 const network=new WeakMap();
 async function settle(page) {
   check();
   await page.waitForLoadState('domcontentloaded',{timeout:Math.min(5000,left())});
   const limit=Math.min(cfg.deadline,Date.now()+5000), net=network.get(page);
   while(Date.now()<limit){
     if(net.error)throw Error('page execution or server error');
     if(net.pending.size===0&&Date.now()-net.last>=200)return;
     await page.waitForTimeout(40);
   }
   throw Error('page never reached network quiescence');
 }
 async function controls(page) {
   return page.locator(selector).evaluateAll(elements=>{
     const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
     return elements.flatMap((e,index)=>{
       const style=getComputedStyle(e),rect=e.getBoundingClientRect();
       if(!rect.width||!rect.height||style.visibility==='hidden'||style.display==='none'||e.closest('[hidden],[inert],[aria-hidden="true"]'))return [];
       const refs=(e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ');
       const name=norm(refs||e.getAttribute('aria-label')||e.innerText||e.getAttribute('title')||e.querySelector('img')?.alt);
       const role=e.getAttribute('role')||(e.tagName==='A'?'link':'button');
       return [{index,name,role,href:e.getAttribute('href')||'',expanded:e.getAttribute('aria-expanded'),selected:e.getAttribute('aria-selected'),
         current:e.getAttribute('aria-current'),
         menu:e.hasAttribute('aria-haspopup')||e.hasAttribute('aria-expanded')||e.tagName==='SUMMARY'||/\bmenu\b/i.test(name),
         disabled:e.disabled||e.getAttribute('aria-disabled')==='true',form:!!e.closest('form'),tag:e.tagName,
         text:norm(e.innerText),groupText:norm((e.closest('header,nav,[role="banner"]')||e.parentElement)?.innerText),header:!!e.closest('header,nav,[role="banner"]')}];
     });
   });
 }
 function nav(c, account) {
   if(c.disabled||danger.test(c.name)||/\/(logout|signout|sign-out)(?:[/?#]|$)/i.test(c.href))return false;
   if(c.href){let u;try{u=new URL(c.href,cfg.base)}catch{return false}return u.origin===cfg.base && !/^(javascript|mailto):/i.test(c.href)}
   return ['link','tab','menuitem'].includes(c.role)||c.menu||c.name===account;
 }
 async function accountControl(page,cred) {
   const cs=await controls(page);
   return cs.find(c=>!c.disabled && [cred.account,cred.email].filter(Boolean).some(x=>c.name===x||c.text===x||
     (cfg.accountNames.includes(c.name)&&c.header&&c.groupText.length<600&&(' '+c.groupText+' ').includes(' '+x+' '))) &&
     (cfg.accountNames.includes(c.name)||((c.role==='button'||c.role==='link')&&c.header)));
 }
 async function capture(page,walk,pathTaken,cs) {
   const state={route:relative(page.url()),heading:await heading(page),path:pathTaken.map(c=>c.name),controls:cs.map(c=>({name:c.name,role:c.role,href:c.href}))};
   if(cfg.evidence){state.screenshot=`walk-${walk.id}-${walk.states.length}.png`;await page.screenshot({path:path.join(cfg.evidence,state.screenshot),fullPage:true,timeout:Math.min(5000,left())})}
   walk.states.push(state);
 }
 async function heading(page){return norm((await page.getByRole('heading').allTextContents()).join(' | '))}
 async function visibleNamed(page,role,name){
   for(const l of await page.getByRole(role,{name,exact:true}).all())if(await l.isVisible()&&await l.isEnabled())return true;
   return false;
 }
 async function replay(page,landing,taken){
   await page.goto(landing,{waitUntil:'domcontentloaded'});await settle(page);
   for(const action of taken){
     const matches=(await controls(page)).filter(c=>c.name===action.name&&c.role===action.role&&c.href===action.href);
     if(!matches.length||matches.length>1&&!action.href)throw Error('ambiguous or vanished navigation on replay');
     const locator=page.locator(selector).nth(matches[0].index).and(page.getByRole(action.role,{name:action.name,exact:true}));
     if(await locator.count()!==1||!await locator.isVisible())throw Error('navigation accessible name uncertain');
     await locator.click();await settle(page);
     if(new URL(page.url()).origin!==cfg.base)throw Error('external navigation');
   }
 }
 async function exactControls(page,name,scope){
   const matches=[];
   const exact=new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\s*$');
   for(const role of ['link','button','tab','menuitem']){
     for(const loc of await page.getByRole(role,{name:exact}).all()){
       if(!await loc.isVisible())continue;
       if(scope&&!await loc.evaluate((e,s)=>s.contains(e),scope))continue;
       matches.push({loc,role});
     }
   }
   return matches;
 }
 async function namedSnapshot(page,name,scope){
   const exact=new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\s*$');
   const roles=['link','button','tab','menuitem'];
   const locator=roles.map(role=>page.getByRole(role,{name:exact})).reduce((a,b)=>a.or(b));
   // One DOM snapshot, no locator auto-wait/retry and no network wait.
   return locator.evaluateAll((elements,scope)=>({at:Date.now(),count:elements.filter(e=>{
     const r=e.getBoundingClientRect(),s=getComputedStyle(e);
     return (!scope||scope.contains(e))&&r.width>0&&r.height>0&&s.visibility!=='hidden'&&
       s.display!=='none'&&!e.closest('[hidden],[inert],[aria-hidden="true"]');
   }).length}),scope);
 }
 async function clickNavigation(page,loc,menu){
   const started=Date.now(),beforeURL=page.url();
   const href=await loc.getAttribute('href');
   let documentNavigation=false,event=null,timer,resolve;
   const navigated=new Promise(r=>{resolve=r});
   const finish=kind=>{if(!event){event=kind;resolve()}};
   const request=r=>{if(r.isNavigationRequest()&&r.frame()===page.mainFrame())documentNavigation=true};
   const frame=f=>{if(f===page.mainFrame()&&!documentNavigation)finish('url-change')};
   const ready=()=>finish('domcontentloaded');
   const loaded=()=>finish('load');
   page.on('request',request);page.on('framenavigated',frame);
   page.on('domcontentloaded',ready);page.on('load',loaded);
   try{
     // In particular do not let Playwright's implicit navigation wait hide
     // post-navigation fetches. Full documents need their DOM, SPA URL changes do not.
     await loc.click({noWaitAfter:true,timeout:Math.min(5000,left())});
     const clicked=Date.now();
     const expectsNavigation=href&&new URL(href,beforeURL).href!==beforeURL;
     if(!event&&(documentNavigation||!menu&&expectsNavigation)){
       await Promise.race([navigated,new Promise(r=>{timer=setTimeout(r,Math.min(1500,left()))})]);
     }
     return {started,clickMs:clicked-started,navigationMs:Date.now()-clicked,
       navigation:event||((documentNavigation||!menu&&expectsNavigation)?'timeout':'same-page')};
   }finally{
     clearTimeout(timer);page.off('request',request);page.off('framenavigated',frame);
     page.off('domcontentloaded',ready);page.off('load',loaded);
   }
 }
 async function statedPaths(page,walk,plan,landing){
   walk.paths=[];
   if(walk.mode!=='UI'){
     if(plan.paths?.length)result.notes.push(walk.account+': stated paths skipped: UI sign-in not confirmed');
     return;
   }
   for(const pathPlan of plan.paths||[]){
     check();
     const evidence={...pathPlan,done:[],status:'uncertain',observations:[],late:[]};
     walk.paths.push(evidence);
     let scope=null;
     try{
       await replay(page,landing,[]);
       if(!await accountControl(page,plan.credential))throw Error('session not confirmed at scenario start');
       for(const [stepIndex,step] of pathPlan.steps.entries()){
         check();
         const observation=evidence.observations.at(-1);
         const matches=await exactControls(page,step.name,scope);
         if(!matches.length){
           if(observation?.step===stepIndex+1&&observation.firstCount)throw Error('target vanished after first look');
           await settle(page);
           if((await exactControls(page,step.name,scope)).length)throw Error('target changed during observation');
           // Record pages need not repeat the account header. Authentication
           // was proved at this scenario's start; a visible login form or lost
           // session cookies makes a later absence uncertain.
           if(!(await page.context().cookies(cfg.base)).length||await page.locator('input[type="password"]:visible').count())throw Error('session no longer certain');
           evidence.missing=step.name;evidence.route=relative(page.url());
           evidence.previous=evidence.done.at(-1)||'sign-in landing';
           evidence.menu=scope?evidence.previous:null;
           evidence.visible=[...new Set((await controls(page)).filter(c=>['link','button','tab','menuitem'].includes(c.role)).map(c=>c.name))].slice(0,8);
           await capture(page,walk,evidence.done.map(name=>({name})),await controls(page));
           evidence.screenshot=walk.states.at(-1).screenshot;
           evidence.status='missing';break;
         }
         if(matches.length!==1)throw Error('multiple visible controls named '+step.name);
         // coordinator review (Codex r57 #7): the replay never clicks a data-changing control (Archive, Close, Merge...)
         if(danger.test(step.name))throw Error('stated step is a data-changing action: '+step.name);
         const {loc}=matches[0];
         if(!await loc.isEnabled())throw Error('disabled control '+step.name);
         const href=await loc.getAttribute('href');
         if(href&&new URL(href,page.url()).origin!==cfg.base)throw Error('external navigation');
         // late only after a click that LEFT the page (Codex r57 #6): an overlay drawn on the same page (a menu, a
         // popover) is no page that opened incomplete
         if(observation?.step===stepIndex+1&&observation.urlChanged&&observation.firstCount===0&&observation.settledCount===1){
           if(!(await page.context().cookies(cfg.base)).length||await page.locator('input[type="password"]:visible').count())throw Error('session no longer certain');
           evidence.late.push({name:step.name,route:observation.route,done:[...evidence.done],
             firstMs:observation.firstMs,settledMs:observation.settledMs});
         }
         let before,controlled,triggerParent;
         if(step.menu){
           controlled=await loc.getAttribute('aria-controls');
           triggerParent=await loc.evaluateHandle(e=>e.parentElement);
           before=await page.evaluateHandle(()=>new Set([...document.querySelectorAll('*')].filter(e=>{
             const r=e.getBoundingClientRect(),s=getComputedStyle(e);
             return r.width&&r.height&&s.visibility!=='hidden'&&s.display!=='none';
           })));
         }
         await capture(page,walk,evidence.done.map(name=>({name})),await controls(page));
         const beforeURL=page.url();
         const timing=await clickNavigation(page,loc,step.menu);
         if(new URL(page.url()).origin!==cfg.base)throw Error('external navigation');
         evidence.done.push(step.name);
         if(scope){await scope.dispose();scope=null}
         if(step.menu){
           if(page.url()!==beforeURL){await before.dispose();await triggerParent.dispose();throw Error('menu opener navigated to another page')}
           const opened=await page.evaluateHandle(({before,controlled,triggerParent})=>{
             const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width&&r.height&&s.visibility!=='hidden'&&s.display!=='none'};
             if(controlled){const e=document.getElementById(controlled);if(e)return e}
             const newRoots=[...document.querySelectorAll('div,section,ul,nav,[role="menu"]')].filter(e=>
               visible(e)&&!before.has(e)&&e.querySelector('a[href],button,[role="menuitem"],[role="link"],[role="tab"]'));
             const roots=newRoots.filter(e=>!newRoots.some(p=>p!==e&&p.contains(e)));
             const related=roots.filter(e=>e.getAttribute('role')==='menu'||triggerParent.contains(e));
             return related.length===1?related[0]:null;
           },{before,controlled,triggerParent});
           await before.dispose();
           await triggerParent.dispose();
           scope=opened.asElement();
           if(!scope){await opened.dispose();throw Error('opened menu scope uncertain')}
         }
         const next=pathPlan.steps[stepIndex+1];
         if(next){
           const first=await namedSnapshot(page,next.name,scope),route=relative(page.url());
           const observation={step:stepIndex+2,name:next.name,route,firstCount:first.count,
             firstMs:first.at-timing.started,clickMs:timing.clickMs,navigationMs:timing.navigationMs,
             navigation:timing.navigation,
             // the document's own location: page.url() lags a pushState until Playwright's navigation event arrives
             urlChanged:(await page.evaluate(()=>location.href).catch(()=>page.url()))!==beforeURL};
           evidence.observations.push(observation);
           await settle(page);
           const settled=await namedSnapshot(page,next.name,scope);
           observation.settledCount=settled.count;observation.settledMs=settled.at-timing.started;
           result.notes.push(pathPlan.leaf+'; '+pathPlan.scenario+': step '+observation.step+' '+JSON.stringify(next.name)+
             ' on '+route+': first-look '+observation.firstMs+'ms count='+first.count+
             '; settled '+observation.settledMs+'ms count='+settled.count+'; navigation='+timing.navigation);
           if(relative(page.url())!==route)throw Error('route changed between first and settled looks');
         }else await settle(page);
       }
       if(evidence.status!=='missing'){
         await capture(page,walk,evidence.done.map(name=>({name})),await controls(page));
         evidence.status='passed';
       }
     }catch(e){
       result.notes.push(pathPlan.leaf+'; '+pathPlan.scenario+': stated path uncertain: '+e.message);
       check();
     }finally{if(scope)await scope.dispose().catch(()=>{})}
   }
 }
 async function zeroClick(page,walk,plan,landing,cs){
   walk.zeroMissing=[];
   for(const target of plan.first||[]){
     let found=false;
     for(const role of ['link','tab','menuitem','button']){
       if(await visibleNamed(page,role,target.name))found=true;
     }
     if(!found&&target.menu){
       const menus=cs.filter(c=>c.menu&&(cfg.accountNames.includes(c.name)||c.name.toLowerCase()===target.menu.toLowerCase()));
       if(menus.length!==1){result.notes.push(target.leaf+': account menu is ambiguous');continue}
       await page.locator(selector).nth(menus[0].index).click();await settle(page);
       if(page.url()!==landing){await replay(page,landing,[]);result.notes.push(target.leaf+': account menu navigated');continue}
       await capture(page,walk,[menus[0]],await controls(page));
       for(const role of ['link','tab','menuitem']){
         if(await visibleNamed(page,role,target.name))found=true;
       }
       await replay(page,landing,[]);
     }
     if(!found)walk.zeroMissing.push(target);
   }
 }
 async function exits(page,walk,plan,landing,taken,cs){
   for(const rule of plan.exits||[]){
     const matches=cs.filter(c=>c.name===rule.name&&['link','tab'].includes(c.role)&&nav(c,'')&&c.selected!=='true');
     for(const match of matches){
     const before={route:relative(page.url()),heading:await heading(page)};
     if(rule.source==='home'&&before.route!==relative(landing))continue;
     if(rule.source==='editor'&&before.route===relative(landing))continue;
     if(rule.home&&before.route===relative(landing))continue;
     if(before.route!==relative(landing)&&before.heading.split(' | ').includes(rule.name)||match.current&&match.current!=='false')continue;
     const loc=page.locator(selector).nth(match.index).and(page.getByRole(match.role,{name:rule.name,exact:true}));
     if(await loc.count()!==1||!await loc.isVisible())continue;
     const beforeText=norm(await page.locator('body').innerText());
     await loc.click();await settle(page);
     const after={route:relative(page.url()),heading:await heading(page)};
     const text=norm(await page.locator('body').innerText());
     const missing=/^(?:404(?:\s|$)|(?:page |workbook |record )?not found\b)/i.test(after.heading)||/^(?:404|page not found|workbook not found|record not found)[.!]?$/i.test(text);
     // coordinator review (v150): an in-page tab changes the content without changing the URL or the h1 -- only a
     // click that changes NOTHING (route, heading AND body text) is a dead exit
     if(missing||(before.route===after.route&&before.heading===after.heading&&beforeText===text)){
       walk.exits.push({name:rule.name,quote:rule.quote,before,after,notFound:missing});
       await capture(page,walk,[...taken,match],await controls(page));
     }
     await replay(page,landing,taken);
     }
   }
 }
 async function successFlows(page,walk,plan,landing){
   for(const flow of plan.flows||[]){
     check();
     try {
       await replay(page,landing,[]);
       const initialHeading=await heading(page);
       const initialLinks=(await controls(page)).filter(c=>c.role==='link').map(c=>c.name);
       const entry=page.getByRole('button',{name:flow.entry,exact:true});
       if(await entry.count()!==1||!await entry.isVisible())throw Error('entry not uniquely visible');
       await entry.click();await settle(page);
       let name=flow.name;
       if(flow.kind==='csv'){
         name='outsider-import-'+Date.now();
         const file=page.getByLabel(flow.field,{exact:true});
         if(await file.count()!==1||!await file.isVisible()||await file.getAttribute('type')!=='file')throw Error('file control not uniquely visible');
         await file.setInputFiles({name:name+'.csv',mimeType:'text/csv',buffer:Buffer.from(flow.csv,'utf8')});
       }else{
         if(initialLinks.includes(name))throw Error('created name already exists');
         for(const [value,label] of flow.fields){
           const field=page.getByLabel(label,{exact:true});
           if(await field.count()!==1||!await field.isVisible())throw Error('input not uniquely visible');
           await field.fill(value);
         }
       }
       const submit=page.getByRole('button',{name:flow.submit,exact:true});
       if(await submit.count()!==1||!await submit.isVisible()||!await submit.isEnabled())throw Error('submit not uniquely enabled');
       let accepted=false;
       const response=r=>{if(['POST','PUT'].includes(r.request().method())&&r.status()>=200&&r.status()<300)accepted=true};
       page.on('response',response);
       try{await submit.click();await settle(page)}finally{page.off('response',response)}
       const after={route:relative(page.url()),heading:await heading(page)};
       await capture(page,walk,[{name:flow.entry},{name:flow.submit}],await controls(page));
       const grid=page.getByRole('grid');
       if(await grid.count()&&await grid.first().isVisible()&&after.heading.split(' | ').includes(name)){
         result.notes.push(flow.leaf+': '+flow.kind+' reached the created record');continue;
       }
       const created=page.getByRole('link',{name,exact:true});
       const isList=accepted&&after.route===relative(landing)&&after.heading===initialHeading&&
         await created.count()===1&&await created.isVisible()&&!await page.getByRole('dialog').count()&&
         !await page.getByRole('alert').filter({hasText:/\S/}).count()&&!await grid.count();
       if(!isList)throw Error('submission outcome/destination uncertain');
       await created.click();await settle(page);
       const title=await heading(page);
       if(!title.split(' | ').includes(name)||!await page.getByRole('grid').count())throw Error('new record is not an editor');
       if(flow.kind==='csv'){
         const cells=await page.getByRole('gridcell').allTextContents();
         if(!flow.values.every(value=>cells.some(text=>norm(text)===value)))throw Error('imported values not corroborated');
       }
       await capture(page,walk,[{name:flow.entry},{name:flow.submit},{name}],await controls(page));
       walk.flows.push({kind:flow.kind,leaf:flow.leaf,quote:flow.quote,name,after});
     }catch(e){check();result.notes.push(flow.leaf+': skipped '+flow.kind+' flow: '+e.message)}
   }
 }
 try {
   record(process.pid);
   // Pipe transport: no extra browser debug/listening port.
   browser=await chromium.launch({headless:true,timeout:Math.min(15000,left())});
   // Every stated path runs before any optional breadth-first work.
   const sessions=[];
   let loginIncomplete=false;
   for(const [planIndex,plan] of cfg.plans.entries()){
     check();
     let context;
     try{
     const cred=plan.credential;
     context=await browser.newContext({viewport:{width:1280,height:900}});
     await context.route('**/*', route=>{
       const u=new URL(route.request().url());
       return ['data:','blob:'].includes(u.protocol)||u.origin===cfg.base ? route.continue():route.abort();
     });
     const page=await context.newPage();
     const net={pending:new Set(),last:Date.now()};network.set(page,net);
     page.on('pageerror',()=>{net.error=true});
     page.on('response',r=>{if(r.status()>=500)net.error=true});
     page.on('request',r=>{net.pending.add(r);net.last=Date.now()});
     for(const event of ['requestfinished','requestfailed'])page.on(event,r=>{net.pending.delete(r);net.last=Date.now()});
     page.setDefaultTimeout(Math.min(5000,left()));
     page.on('dialog',dialog=>dialog.dismiss());
     await page.goto(cfg.base,{waitUntil:'domcontentloaded',timeout:Math.min(10000,left())});await settle(page);
     const beforeText=await page.locator('body').innerText();
     const beforeAccount=await accountControl(page,cred);
     let signed=false, mode=plan.anonymous?'anonymous':'UI';
     if(!plan.anonymous){
     let loginResponse=false;
     page.on('response',response=>{if(cfg.logins.some(l=>new URL(response.url()).pathname===l.path)&&response.request().method()==='POST'&&response.status()>=200&&response.status()<300)loginResponse=true});
     const signInLinks=[];
     for(const name of cfg.signInNames){const l=page.getByRole('link',{name,exact:true});if(await l.count()===1&&await l.isVisible())signInLinks.push(l)}
     if(signInLinks.length===1){await signInLinks[0].click();await settle(page)}
     let ids=[],passwords=[],buttons=[];
     for(const name of cfg.identityLabels){const l=page.getByLabel(name,{exact:true});if(await l.count()===1&&await l.isVisible())ids.push({l,name})}
     for(const name of cfg.passwordLabels){const l=page.getByLabel(name,{exact:true});if(await l.count()===1&&await l.isVisible())passwords.push(l)}
     for(const name of cfg.signInNames){const l=page.getByRole('button',{name,exact:true});if(await l.count()===1&&await l.isVisible())buttons.push(l)}
     if(ids.length===1&&passwords.length===1&&buttons.length===1){
       const identity=/^e-?mail$/i.test(ids[0].name)?cred.email||cred.account:cred.account||cred.email;
       await ids[0].l.fill(identity);await passwords[0].fill(cred.password);await buttons[0].click();await settle(page);
       const authEnd=Math.min(cfg.deadline,Date.now()+5000);
       do {
         signed=loginResponse&&(await context.cookies()).length>0&&!beforeAccount&&!!await accountControl(page,cred);
         if(signed)break;
         await page.waitForTimeout(100);
       } while(Date.now()<authEnd);
       if(!signed)throw Error('UI sign-in did not establish a session and visible account control');
     } else {
       mode='API fallback';
       const apiStart=page.url();
       for(const candidate of plan.requests){
         const response=await context.request.post(cfg.base+candidate.path,{data:candidate.body,timeout:Math.min(5000,left()),maxRedirects:0});
         if(response.ok()&&(await context.cookies()).length){
           // Reload the page the user reached; accept only the app's own redirect.
           await page.reload({waitUntil:'domcontentloaded'});await settle(page);
           signed=!beforeAccount&&!!await accountControl(page,cred)&&
             (page.url()!==apiStart||!(await page.locator('input[type="password"]:visible').count()));
           if(signed)break;
         }
       }
       if(!signed)throw Error('API fallback has no confirmed landing/account control');
     }
     }
     const landing=page.url();
     if(new URL(landing).origin!==cfg.base)throw Error('external sign-in landing');
     const walk={id:planIndex+1,account:cred.account||cred.email,route:relative(landing),mode,found:{},missing:[],visitor:[],states:[],exits:[],flows:[]};
     result.walks[planIndex]=walk;
     const landingText=await page.locator('body').innerText();
     const landingControls=await controls(page);
     await capture(page,walk,[],landingControls);
     await statedPaths(page,walk,plan,landing);
     for(const name of (plan.anonymous?[]:cfg.visitorSignInNames)){
       if(landingControls.some(c=>c.name===name&&['link','button'].includes(c.role)))walk.visitor.push(name);
       for(const line of beforeText.split('\n').map(norm).filter(Boolean)){
         if(line.toLowerCase().includes(name.toLowerCase())&&/\b(use|click|please|must|need|to access|to continue)\b/i.test(line)&&norm(landingText).includes(line))walk.visitor.push(line);
       }
     }
     sessions.push({context,page,plan,walk,landing,landingControls});
     }catch(e){
       loginIncomplete=true;
       result.notes.push((plan.credential.account||plan.credential.email)+': authentication/replay incomplete: '+e.message);
       if(context)await context.close().catch(()=>{});
       check();
     }
   }
   for(const {context,page,plan,walk,landing,landingControls} of sessions){
     check();
     const cred=plan.credential;
     await replay(page,landing,[]);
     await zeroClick(page,walk,plan,landing,landingControls);
     const queue=[[]], seen=new Set(), scheduled=new Set();
     while(queue.length){
       check();
       const taken=queue.shift();
       if(taken.length){
         await replay(page,landing,taken);
       }
       const cs=await controls(page);
       const key=JSON.stringify([relative(page.url()),cs.map(c=>[c.name,c.role,c.href,c.expanded,c.selected])]);
       if(seen.has(key))continue;
       if(seen.size>=40)throw Error('40-state cap reached before frontier exhausted');
       seen.add(key);
       await capture(page,walk,taken,cs);
       await exits(page,walk,plan,landing,taken,cs);
       for(const name of Object.keys(plan.targets))if(cs.some(c=>c.name===name&&!c.disabled))walk.found[name]=walk.found[name]||taken.map(c=>c.name);
       if(taken.length>=3)continue;
       for(const c of cs.filter(c=>nav(c,cred.account))){
         if(plan.anonymous&&!['link','tab'].includes(c.role))continue;
         // Avoid harmless self-links and already toggled ancestor menus.
         if(c.href&&new URL(c.href,page.url()).href===page.url()&&c.expanded===null&&!c.menu)continue;
         if(c.expanded==='true')continue;
         if(taken.some(x=>x.name===c.name&&x.href===c.href&&x.role===c.role))continue;
         const destination=c.href&&!c.menu ? new URL(c.href,page.url()).href : key+'|'+c.role+'|'+c.name;
         if(scheduled.has(destination))continue;
         scheduled.add(destination);
         queue.push([...taken,c]);
       }
     }
     // Reconfirm authentication at the landing route after the whole walk.
     await page.goto(landing,{waitUntil:'domcontentloaded'});await settle(page);
     if(!plan.anonymous&&!await accountControl(page,cred))throw Error('session no longer confirmed after walk');
     if(plan.anonymous)await successFlows(page,walk,plan,landing);
     walk.missing=Object.keys(plan.targets).filter(n=>!(n in walk.found));
     walk.visitor=[...new Set(walk.visitor)];
     await context.close();
   }
   result.completed=!loginIncomplete;
   if(loginIncomplete)result.diagnostic='one or more accounts could not complete authentication/replay';
 } catch(e){result.diagnostic=String(e.message||e).replace(/https?:\/\/[^\s]+/g,'<url>')}
 finally {if(browser)await browser.close().catch(()=>{});process.stdout.write(JSON.stringify(result))}
}
'''

def _stating_leaves(requirement, creds):
    try:
        from .finding_owner import enabled
        if not enabled():
            return None
        out = {}
        for cred in creds or []:
            for path in _stated_paths(requirement, cred):
                for step in path.get('steps') or []:
                    out.setdefault(step['name'], set()).add(path['leaf'])
            for target in _first_targets(requirement, cred):
                out.setdefault(target['name'], set()).add(target['leaf'])
        return out
    except Exception:
        return None

def _owned(finding, leaves, names, stating):
    if stating is None:
        return finding
    from .finding_owner import tag
    owners = set(leaves)
    for name in names:
        owners |= stating.get(name, set())
    return tag(finding, owners)

def _path_findings(result, stating=None):
    groups = {}
    seen = set()
    for walk in filter(None, result.get('walks', [])):
        for path in walk.get('paths', []):
            failures = [{**path, **late, 'missing': late['name'], 'status': 'late', 'visible': []}
                        for late in path.get('late', [])]
            if path['status'] == 'missing':
                failures.append(path)
            for failure in failures:
                identity = (path['key'], failure['status'], tuple(failure['done']), failure['missing'])
                if identity in seen:
                    continue
                seen.add(identity)
                key = (failure['status'], failure['route'], tuple(failure['done']), failure['missing'])
                groups.setdefault(key, []).append((walk, failure))
    findings = []
    for (status, route, done, name), items in groups.items():
        contexts = '; '.join(f"{p['leaf']}; {p['scenario']}; account {w['account']}" for w, p in items)
        previous = done[-1] if done else 'sign-in landing'
        menu = items[0][1].get('menu')
        scope_text = f' inside the opened “{menu}” menu' if menu else ''
        visible = list(dict.fromkeys(n for _, p in items for n in p['visible']))[:8]
        if status == 'late':
            findings.append(_owned({
                'where': f'outsider-walk: rendered app, frontend/src (route {route})',
                'requirement': ' | '.join(dict.fromkeys(p['quote'] for _, p in items)),
                'problem': f'“{name}” is missing when a user looks right after opening {route} and appears only '
                           f'later, once the page has finished loading.',
                'fix': 'Make it present when the page opens: if it depends on server data, load that data '
                       'before navigating (in the click handler before the router navigates, or from a '
                       'start-up preload) instead of fetching after the page has opened.',
            }, [p.get('leaf') for _, p in items], [name], stating))
            continue
        findings.append(_owned({
            'where': f'outsider-walk: rendered app, frontend/src (route {route})',
            'requirement': ' | '.join(dict.fromkeys(p['quote'] for _, p in items)),
            'problem': f'Stated path ({contexts}): after signing in and clicking ' +
                       (' → '.join(f'“{n}”' for n in done) if done else 'no navigation controls (zero clicks)') +
                       f', step {len(done) + 1} “{name}” has no visible link/button/tab/menuitem{scope_text} on {route}. ' +
                       'Visible controls: ' + (', '.join(f'“{n}”' for n in visible) or '(none)') + '.',
            'fix': f'The page reached by "{previous}" must expose a visible link/tab/button named exactly "{name}" (the requirement\'s path).',
        }, [p.get('leaf') for _, p in items], [name], stating))
    return findings[:8]

def _record(pid):
    dest = os.environ.get('ARC_OUTSIDER_PID_FILE')
    if dest:
        with open(dest, 'a', encoding='utf-8') as stream:
            stream.write(str(pid) + '\n')

def _stop(process):
    if process is None or process.poll() is not None:
        return
    if os.name == 'nt':
        killer = subprocess.Popen(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        _record(killer.pid)
        try:
            killer.wait(timeout=3)
        except subprocess.TimeoutExpired:
            killer.kill()
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()

def outsider_walk_check(requirement: str, project_dir: Path, time_limit: float = 90.0,
                        log=print, node_modules=None) -> tuple[list[dict], bool]:
    def note(message):
        try:
            log('[outsider-walk] ' + message)
        except Exception:
            pass
    server = runner = None
    work = None
    sink = None
    try:
        end = time.monotonic() + max(0, float(time_limit))
        project_dir = Path(project_dir)
        if not (project_dir / 'frontend' / 'dist' / 'index.html').is_file():
            note('skipped: frontend/dist is not built; no build attempted')
            return [], False
        creds = _credentials(requirement)
        plans = _plans(requirement, creds) if creds else [_anonymous_plan(requirement)]
        if creds:
            note('zero-click targets: ' + json.dumps(
                [(p['credential']['account'] or p['credential']['email'], [(t['name'], t['leaf']) for t in p['first']])
                 for p in plans], ensure_ascii=False))
            note('unquoted, noninitial, negative or otherwise uncertain scenario targets are not judged at zero clicks')
        if not creds:
            note('no stated credentials: starting an anonymous walk from /')
            for message in plans[0]['notes']:
                note(message)
        backend = project_dir / 'backend'
        nm = Path(node_modules or backend / 'node_modules').resolve()
        node = shutil.which('node')
        start = sp._start_file(backend)
        if not node or not start or not (nm / '@playwright' / 'test').is_dir():
            note('skipped: node, backend start file or @playwright/test unavailable')
            return [], False
        logins, _ = sp.backend_routes(project_dir)
        front = sp.frontend_login_keys(project_dir)
        if creds and not logins:
            note('skipped: no certain backend login route')
            return [], False
        for plan in plans:
            plan['requests'] = []
            if plan.get('anonymous'):
                continue
            for login in logins:
                fields = [keys for path, keys in front if login['path'].endswith(path) or path.endswith(login['path'])]
                for body in sp.body_variants(plan['credential'], fields, login['keys']):
                    plan['requests'].append({'path': login['path'], 'body': body})
        if end - time.monotonic() < 10:
            note('skipped: insufficient startup/cleanup budget')
            return [], False

        port = next((p for p in range(4890, 4900) if not sp._port_taken(p)), None)
        if port is None:
            note('skipped: ports 4890-4899 busy')
            return [], False
        scratch = os.environ.get('ARC_OUTSIDER_SCRATCH') or tempfile.gettempdir()
        work = Path(tempfile.mkdtemp(prefix='outsider-walk-', dir=scratch))
        def copy_file(src, dst):
            if time.monotonic() > end - 7:
                raise TimeoutError('copy deadline')
            return shutil.copy2(src, dst)
        ignore = shutil.ignore_patterns('node_modules', '.git', '.config', '*key*.txt', 'credentials.csv', '.env*',
                                        '*.db*', '*.sqlite*', 'test', 'tests', 'test-e2e', 'test-results',
                                        'playwright-report', '.arc-test-db', '__pycache__')
        shutil.copytree(backend, work / 'backend', ignore=ignore, copy_function=copy_file)
        shutil.copytree(project_dir / 'frontend' / 'dist', work / 'frontend' / 'dist', copy_function=copy_file)
        data = work / 'data'
        data.mkdir()
        env = {**os.environ, 'PORT': str(port), 'NODE_PATH': str(nm), 'DATA_DIR': str(data),
               'ARC_DB_FILE': str(data / 'database.db'), 'DATABASE_FILE': str(data / 'database.db'),
               'TEMP': str(work), 'TMP': str(work)}
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        sink = (work / 'backend.log').open('w', encoding='utf-8')
        server = subprocess.Popen([node, start], cwd=work / 'backend', env=env, stdout=sink, stderr=sink, **flags)
        _record(server.pid)
        base = f'http://127.0.0.1:{port}'
        while time.monotonic() < end - 7:
            if server.poll() is not None:
                raise RuntimeError('backend exited during startup')
            try:
                sp._request(base, 'GET', '/', None, {}, min(1, end-time.monotonic()))
                break
            except OSError:
                time.sleep(0.1)
        time.sleep(0.15)
        sink.flush()
        if server.poll() is not None or 'EADDRINUSE' in (work / 'backend.log').read_text(encoding='utf-8', errors='replace'):
            raise RuntimeError('backend failed or port ownership uncertain')
        quoted = list(dict.fromkeys(re.findall(QUOTE, ' '.join(requirement_texts(requirement)))))
        visitor_names = set()
        for text in requirement_texts(requirement):
            for sentence in re.split(r'(?<=[.;])\s+', text):
                if re.search(r'(?i)\bvisitors?|signed[- ]out|unauthenticated\b', sentence):
                    visitor_names.update(re.findall(QUOTE, sentence))
        evidence = os.environ.get('ARC_OUTSIDER_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
        cfg = {'base': base, 'plans': plans, 'logins': [{'path': c['path']} for c in logins],
               'deadline': int((time.time()+max(0, end-time.monotonic()-6))*1000),
               'identityLabels': [n for n in quoted if re.fullmatch(r'(?i)(?:username(?: or e-?mail)?|e-?mail|login|identifier)', n)],
               'passwordLabels': [n for n in quoted if re.fullmatch(r'(?i)password', n)],
               'signInNames': [n for n in quoted if re.fullmatch(r'(?i)(?:sign|log)[ -]?in', n)],
               'visitorSignInNames': [n for n in quoted if n in visitor_names and re.fullmatch(r'(?i)(?:sign|log)[ -]?in', n)],
               'accountNames': [n for n in quoted if re.fullmatch(r'(?i)(?:account|user|profile) menu', n)],
               'evidence': evidence, 'pidFile': os.environ.get('ARC_OUTSIDER_PID_FILE')}
        (work / 'walk.cjs').write_text(RUNNER, encoding='utf-8')
        runner = subprocess.Popen([node, str(work / 'walk.cjs')], cwd=work, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **flags)
        _record(runner.pid)
        raw, _ = runner.communicate(json.dumps(cfg), timeout=max(0.1, end-time.monotonic()-4))
        if runner.returncode:
            raise RuntimeError('browser runner failed (browser installation or runtime unavailable)')
        result = json.loads(raw)
        for message in result.get('notes', []):
            note(message)
        if evidence:
            output = Path(evidence) / 'walk.json'
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            if json.loads(output.read_text(encoding='utf-8')) != result:
                raise OSError('evidence readback mismatch')
        stating = _stating_leaves(requirement, creds) if creds else None
        findings = _path_findings(result, stating)
        if not result['completed']:
            note('incomplete: ' + result.get('diagnostic', 'unknown browser failure'))
            return findings[:8], False
        by_route = {}
        for walk in filter(None, result['walks']):
            note(f"{walk['account']} at {walk['route']}: {len(walk['states'])} states, "
                 f"found {json.dumps(walk['found'], ensure_ascii=False)}, missing {walk['missing']}")
            by_route.setdefault(walk['route'], []).append(walk)
        for route, walks in by_route.items():
            zero = [(w, target) for w in walks for target in w.get('zeroMissing', [])
                    if not any(p['leaf'] == target['leaf'] and p['scenario'] == target['scenario']
                               for p in w.get('paths', []))]
            if zero:
                statements = [f"“{t['name']}” ({t['leaf']}; {t['scenario']}; account {w['account']})" for w, t in zero]
                findings.append(_owned({'where': f'outsider-walk: rendered app, frontend/src (landing {route})',
                                 'requirement': ' '.join(dict.fromkeys(t['quote'] for _, t in zero)),
                                 'problem': f'After sign-in, these first navigation targets are not visible at zero clicks on {route}: ' + '; '.join(statements) + '.',
                                 'fix': 'Expose each scenario\'s first stated destination on its authenticated landing page; use an account menu only where the requirement explicitly places that destination there.'},
                                 [t.get('leaf') for _, t in zero], [t.get('name') for _, t in zero], stating))
            exits_seen = set()
            for walk in walks:
                for edge in walk.get('exits', []):
                    key = (edge['before']['route'], edge['name'])
                    if key in exits_seen:
                        continue
                    exits_seen.add(key)
                    evidence = 'landed on a not-found page' if edge['notFound'] else f"left both the URL and heading unchanged ({edge['after']['heading']!r})"
                    findings.append({'where': f"outsider-walk: rendered app, frontend/src (route {edge['before']['route']})",
                                     'requirement': edge['quote'],
                                     'problem': f"Clicking the visible navigation target “{edge['name']}” {evidence}, although it must open another page.",
                                     'fix': 'Wire the navigation control to the required destination and verify the click leaves the source page.'})
                for flow in walk.get('flows', []):
                    findings.append(_owned({'where': f"outsider-walk: rendered app, frontend/src (landing {flow['after']['route']})",
                                     'requirement': flow['quote'],
                                     'problem': f"The completed {flow['kind']} flow ({flow['leaf']}) stayed on the list instead of opening “{flow['name']}”. Its new link opens the successfully created record through the UI.",
                                     'fix': 'Navigate to the created or imported record after successful submission and render its editor.'},
                                     [flow.get('leaf')], [], stating))
            missing = []
            for w in walks:

                path_names = {s['name'] for p in w.get('paths', []) for s in p['steps']}
                remaining = [n for n in w['missing'] if n not in path_names]
                if remaining:
                    missing.append({**w, 'missing': remaining})
            if missing:
                statements = [f"After signing in as {w['account']}, nothing visible named " +
                              ', '.join('“'+n+'”' for n in w['missing']) +
                              f" is reachable within 3 clicks from {route}." for w in missing]
                quotes = list(dict.fromkeys(q for p in plans for n, q in p['targets'].items()
                                            if any(n in w['missing'] for w in missing)))
                findings.append({'where': f'outsider-walk: rendered app, frontend/src (landing {route})',
                                 'requirement': ' '.join(quotes), 'problem': ' '.join(statements),
                                 'fix': 'Expose the stated destinations through visible links, tabs or menu controls from the authenticated workspace.'})
            visitors = [w for w in walks if w['visitor']]
            if visitors:
                wording = list(dict.fromkeys(t for w in visitors for t in w['visitor']))
                findings.append({'where': f'outsider-walk: rendered app, frontend/src (landing {route})',
                                 'requirement': ' '.join(cfg['signInNames']),
                                 'problem': f"After successful sign-in at {route}, the visible account control coexists with visitor sign-in wording: " + ' / '.join(wording),
                                 'fix': 'Render the authenticated workspace state after sign-in and remove instructions or entries asking that user to sign in.'})
        return findings[:8], True
    except Exception as exc:
        note('incomplete: ' + type(exc).__name__)
        return [], False
    finally:
        for process in (runner, server):
            try:
                _stop(process)
            except Exception:
                note('cleanup could not confirm child exit')
        if sink:
            try:
                sink.close()
            except Exception:
                pass
        if work:
            shutil.rmtree(work, ignore_errors=True)

PLAN_RUNNER = r'''
const {chromium}=require('@playwright/test');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>run(JSON.parse(input)));
async function run(cfg){
 let browser;const out={walks:[],notes:[],completed:false};
 const left=()=>cfg.deadline-Date.now();
 const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
 const rel=u=>{try{const x=new URL(u);return x.pathname+x.search}catch{return String(u)}};
 const sleep=ms=>new Promise(z=>setTimeout(z,ms));
 async function settle(page){
   try{await page.waitForLoadState('domcontentloaded',{timeout:Math.max(500,Math.min(5000,left()))})}catch{}
   try{await page.waitForLoadState('networkidle',{timeout:Math.max(300,Math.min(3000,left()))})}catch{}
 }
 async function visible(loc){try{return await loc.isVisible()}catch{return false}}
 async function inScope(loc,scope){if(!scope)return true;try{return await loc.evaluate((e,s)=>s.contains(e),scope)}catch{return false}}
 async function matches(page,name,roles,scope,exact){
   const found=[];
   for(const role of roles){
     let all=[];try{all=await page.getByRole(role,{name,exact:exact!==false}).all()}catch{}
     for(const loc of all){
       if(!await visible(loc)||!await inScope(loc,scope))continue;
       let label='';try{label=norm(await loc.evaluate(e=>e.getAttribute('aria-label')||e.innerText||e.textContent||e.value||''))}catch{}
       if(exact===false&&!label.includes(name))continue;
       found.push({loc,role,href:await loc.getAttribute('href').catch(()=>null),label});
     }
   }
   return found;
 }
 async function poll(fn,ms){const end=Date.now()+Math.min(ms,Math.max(0,left()-500));let r=await fn();while(!r.length&&Date.now()<end){await sleep(250);r=await fn()}return r}
 const ALL=['link','button','tab','menuitem','option','menuitemcheckbox','menuitemradio','treeitem'];
 async function locate(page,step,scope){
   const stated=step.role?[step.role]:['link','button','tab','menuitem','option'];
   const names=[step.name,...(step.alternatives||[])];
   for(const name of names){
     const hit=await poll(()=>matches(page,name,stated,scope,true),names.length>1?1200:3000);
     if(hit.length)return {hit,name,how:'exact'};
   }
   const other=ALL.filter(r=>!stated.includes(r));
   for(const name of names){
     const hit=await matches(page,name,other,scope,true);
     if(hit.length)return {hit,name,how:'other-role'};
   }
   if(step.entity){
     const hit=await matches(page,step.name,['link','button'],scope,false);
     if(hit.length)return {hit,name:step.name,how:'contains'};
   }
   let hidden=0;const cased=[];
   try{for(const r of stated)hidden+=await page.getByRole(r,{name:step.name,exact:true,includeHidden:true}).count()}catch{}
   try{for(const r of stated)for(const l of await page.getByRole(r,{name:step.name}).all())if(await visible(l)){const t=norm(await l.innerText().catch(()=>''));if(t.toLowerCase()===step.name.toLowerCase()&&t!==step.name)cased.push(t)}}catch{}
   return {hit:[],name:step.name,how:'none',hidden,cased};
 }
 async function controlsSummary(page){
   try{return (await page.locator('a[href],button,[role="link"],[role="tab"],[role="menuitem"]').evaluateAll(es=>es.filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.height}).map(e=>(e.getAttribute('aria-label')||e.innerText||'').replace(/\s+/g,' ').trim()).filter(Boolean))).slice(0,12)}catch{return []}
 }
 async function landing(page,name){
   const end=Date.now()+Math.min(3000,Math.max(0,left()-500));
   do{
     let heads=[];try{heads=(await page.getByRole('heading').allInnerTexts()).map(norm)}catch{}
     const title=norm(await page.title().catch(()=>''));
     // the record's name or its identifier form ("Acme Demo" / "acme-demo"): letters and digits, case folded
     const fold=x=>String(x).toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'');
     if(heads.some(h=>fold(h).includes(fold(name)))||fold(title).includes(fold(name)))return 'heading';
     await sleep(250);
   }while(Date.now()<end);
   // a page that shows the record's display name outside its heading (heading = identifier "acme-org") is not judged
   const body=await page.locator('body').innerText().catch(()=>'');
   return String(body).toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'').includes(String(name).toLowerCase().replace(/[^\p{L}\p{N}]+/gu,''))?'body':false;
 }
 // replay 10-06: a backend that died mid-walk left an empty page and a false "missing" -- an absence counts only on a
 // live page (the app answers below 500 and the page shows at least one control)
 async function alive(page){
   try{const r=await page.request.get(cfg.base+'/',{timeout:3000});if(r.status()>=500)return false}catch{return false}
   return (await controlsSummary(page)).length>0;
 }
 async function pwVisible(page){for(const l of await page.locator('input[type="password"]').all())if(await visible(l))return true;return false}
 async function signIn(page,step,res){
   const r=cfg.recipe;
   res.name=r.entry;
   if(!await pwVisible(page)){
     const got=await locate(page,{name:r.entry,role:step.role,alternatives:[]},null);
     if(!got.hit.length&&!await alive(page)){res.outcome='uncertain';res.note='application not answering or page empty';return false}
     if(!got.hit.length){res.outcome='missing';res.detail='no visible control named "'+r.entry+'"'+(got.hidden?' (present but hidden)':'');res.visible=await controlsSummary(page);return false}
     if(got.how==='other-role'){res.outcome='role-mismatch';res.detail='the requirement states a '+step.role+' "'+r.entry+'"; the page has only a '+[...new Set(got.hit.map(h=>h.role))].join('/')}
     await got.hit[0].loc.click({timeout:Math.min(5000,Math.max(500,left()))});await settle(page);
   }
   const field=async labels=>{for(const l of labels){const vis=[];for(const x of await page.getByLabel(l,{exact:true}).all())if(await visible(x))vis.push(x);if(vis.length===1)return {f:vis[0],label:l}}return null};
   let id=null,pass=null;const end=Date.now()+3000;
   do{id=await field(r.identity);pass=await field(r.password);if(id&&pass)break;await sleep(250)}while(Date.now()<end&&left()>1000);
   if(!id||!pass){
     if(!await pwVisible(page)&&res.outcome==='ok'){res.outcome='sign-in-failed';res.detail='after "'+r.entry+'" no visible password field at '+rel(page.url())}
     else res.note=(res.note||'')+' sign-in fields not found by the stated labels '+JSON.stringify([...r.identity,...r.password]);
     return false;
   }
   const ident=/^e-?mail/i.test(id.label)?(step.email||step.identity):(step.identity||step.email);
   await id.f.fill(ident);await pass.f.fill(step.password);
   let submit=null;
   const form=pass.f.locator('xpath=ancestor::form[1]');
   const scope=(await form.count())?form:page;
   for(const name of r.submit){for(const x of await scope.getByRole('button',{name,exact:true}).all())if(await visible(x)){submit=x;break}if(submit)break}
   if(!submit){res.note=(res.note||'')+' no submit button '+JSON.stringify(r.submit);return false}
   const before=page.url();
   await submit.click({timeout:Math.min(5000,Math.max(500,left()))});await settle(page);
   const stop=Date.now()+5000;
   do{
     const body=norm(await page.locator('body').innerText().catch(()=>''));
     if(!await pwVisible(page)&&(page.url()!==before||(step.identity&&body.includes(step.identity))||(step.email&&body.includes(step.email))))return true;
     await sleep(250);
   }while(Date.now()<stop&&left()>800);
   res.note=(res.note||'')+' session not confirmed after submit';
   return false;
 }
 try{
   browser=await chromium.launch({headless:true,timeout:Math.min(20000,Math.max(1000,left()))});
   for(const walk of cfg.walks){
     if(left()<4000){out.notes.push('deadline: walk '+walk.id+' and later not run');break}
     const w={id:walk.id,steps:[],status:'clean',stop:null};out.walks.push(w);
     let context;
     try{
       context=await browser.newContext({viewport:{width:1280,height:900}});
       await context.route('**/*',route=>{const u=new URL(route.request().url());return ['data:','blob:'].includes(u.protocol)||u.origin===cfg.base?route.continue():route.abort()});
       const page=await context.newPage();page.setDefaultTimeout(5000);page.on('dialog',d=>d.dismiss().catch(()=>{}));
       await page.goto(cfg.base+'/',{waitUntil:'domcontentloaded',timeout:Math.min(15000,Math.max(1000,left()))});await settle(page);
       let scope=null,guessed=null;
       for(const [i,step] of walk.steps.entries()){
         if(left()<2500){w.status=w.status==='finding'?'finding':'unrun';w.stop={kind:'unrun',reason:'deadline'};break}
         const res={i,kind:step.kind,name:step.name||'',outcome:'ok',route:rel(page.url()),implied:!!step.implied};w.steps.push(res);
         if(step.kind==='home'){await page.goto(cfg.base+'/',{waitUntil:'domcontentloaded'});await settle(page);scope=null;continue}
         if(step.kind==='reload'){await page.reload({waitUntil:'domcontentloaded'});await settle(page);continue}
         if(step.kind==='back'){await page.goBack({waitUntil:'domcontentloaded'}).catch(()=>{});await settle(page);scope=null;continue}
         if(step.kind==='sign_in'){
           const ok=await signIn(page,step,res);
           if(res.outcome!=='ok'&&res.outcome!=='uncertain')w.status='finding';
           if(!ok){if(w.status!=='finding'){w.status='unrun';w.stop={kind:'unrun',reason:'sign-in uncertain:'+(res.note||'')}}break}
           scope=null;continue;
         }
         const got=await locate(page,step,scope);
         if(!got.hit.length&&guessed){
           // an earlier step had several same-named controls and the walk took the first: a later absence may be
           // the other one's page -- uncertain, never a finding
           res.outcome='uncertain';res.detail='not found after the ambiguous step "'+guessed+'"';
           if(w.status!=='finding'){w.status='unrun';w.stop={kind:'unrun',reason:'after an ambiguous step'}}break;
         }
         if(!got.hit.length&&!await alive(page)){
           res.outcome='uncertain';res.detail='application not answering or page empty';
           if(w.status!=='finding'){w.status='unrun';w.stop={kind:'unrun',reason:'application not answering'}}break;
         }
         if(!got.hit.length){
           res.outcome='missing';res.detail=(got.hidden?'present but hidden':'')+(got.cased&&got.cased.length?' only differently cased: '+got.cased.join(', '):'');
           res.visible=await controlsSummary(page);res.scope=scope?(step.scopeName||'menu'):null;w.status='finding';break;
         }
         if(step.region&&!scope){
           // v163b: the requirement places the target in a landmark region ("in the main content"): a hit outside
           // every such landmark is a finding; no landmark at all on the page is a finding of its own kind
           const sel=step.region.selector;let marks=0;
           for(let k=0;k<5&&!marks;k++){try{marks=await page.locator(sel).count()}catch{}if(!marks)await sleep(250)}
           const inside=[];for(const h of got.hit){if(await h.loc.evaluate((e,s)=>!!e.closest(s),sel).catch(()=>false))inside.push(h)}
           if(!marks){res.outcome='no-landmark';res.detail=step.region.word+'|'+step.region.element;res.visible=await controlsSummary(page);w.status='finding';break}
           if(!inside.length){res.outcome='outside-region';res.detail=step.region.word+'|'+step.region.element;res.visible=await controlsSummary(page);w.status='finding';break}
           got.hit=inside;
         }
         if(got.how==='other-role'){res.outcome='role-mismatch';res.detail='stated '+step.role+'; found '+[...new Set(got.hit.map(h=>h.role))].join('/');w.status='finding'}
         if(got.how==='contains')res.note='matched by a longer accessible name: '+got.hit[0].label.slice(0,80);
         const hrefs=new Set(got.hit.map(h=>h.href||('#'+h.role+':'+h.label)));
         if(got.hit.length>1&&hrefs.size>1){res.ambiguous=[...hrefs].slice(0,4);if(res.outcome==='ok')res.outcome='ambiguous';guessed=step.name}
         const loc=got.hit[0].loc;
         if(!await loc.isEnabled().catch(()=>true)){res.outcome='disabled';w.status='finding';break}
         if(step.kind==='check'){res.note=(res.note||'')+' mutating control: checked present, not clicked';break}
         const before=page.url();
         try{await loc.click({timeout:Math.min(5000,Math.max(500,left()))})}catch(e){res.outcome='uncertain';res.detail='click failed: '+String(e.message||e).split('\n')[0].slice(0,120);if(w.status!=='finding'){w.status='unrun';w.stop={kind:'unrun',reason:'click failed'}}break}
         await settle(page);
         if(new URL(page.url()).origin!==cfg.base){res.outcome='uncertain';res.detail='left the application';if(w.status!=='finding'){w.status='unrun';w.stop={kind:'unrun',reason:'external'}}break}
         res.after=rel(page.url());
         scope=null;
         if(step.menu){
           const ctl=await loc.getAttribute('aria-controls').catch(()=>null);
           if(ctl){const c=page.locator('[id="'+ctl.replace(/"/g,'')+'"]');if(await c.count()&&await visible(c.first()))scope=await c.first().elementHandle()}
           if(!scope){const menus=[];for(const m of await page.locator('[role="menu"],[role="listbox"]').all())if(await visible(m))menus.push(m);if(menus.length===1)scope=await menus[0].elementHandle()}
           if(!scope)res.note=(res.note||'')+' opened menu scope uncertain; next step looked up page-wide';
         }
         const landed=step.entity?await landing(page,step.name):'heading';
         if(landed==='body')res.note=(res.note||'')+' the record name is on the page but in no heading';
         if(!landed){
           if(page.url()===before)res.note=(res.note||'')+' same URL after click; landing not judged';
           else if(guessed){res.outcome='uncertain';res.detail='landing after the ambiguous step "'+guessed+'"';if(w.status!=='finding'){w.status='unrun';w.stop={kind:'unrun',reason:'after an ambiguous step'}}break}
           else{res.outcome='wrong-landing';res.detail='no heading or title names "'+step.name+'" at '+rel(page.url());w.status='finding';break}
         }
       }
     }catch(e){if(w.status!=='finding'){w.status='unrun'};w.stop={kind:'unrun',reason:'error: '+String(e.message||e).split('\n')[0].slice(0,140)}}
     finally{if(context)await context.close().catch(()=>{})}
   }
   out.completed=out.walks.length===cfg.walks.length;
 }catch(e){out.notes.push('runner: '+String(e.message||e).split('\n')[0].slice(0,200))}
 finally{if(browser)await browser.close().catch(()=>{});process.stdout.write(JSON.stringify(out))}
}
'''

def _step_region(s):
    from .when_plan import REGION_LANDMARKS, region_key, stated_region_on
    scope = s.get('scope') or {}
    if scope.get('kind') != 'region' or not stated_region_on():
        return None
    key = region_key(scope.get('name'))
    if key is None:
        return None
    role, selector, element = REGION_LANDMARKS[key]
    return {'word': key, 'role': role, 'selector': selector, 'element': element}

def _plan_walk_steps(plan):
    from .when_plan import MUTATING
    steps, stop, notes = [], ('end', 'all steps'), []
    if plan.get('sessions', 1) > 1:
        notes.append('multi-session scenario: only browser 1 is walked')
    for s in plan.get('steps') or []:
        if s.get('session', 1) != 1:
            continue
        action, status = s.get('action'), s.get('status')
        if s.get('part') == 'THEN':
            stop = ('end', 'THEN')
            break
        if action in ('observe', 'destination'):
            continue
        if status == 'unresolved':
            stop = ('unrun', f"unresolved step {s.get('n')} ({action}): {s.get('why') or s.get('text', '')}"[:160])
            break
        if status == 'conditional':
            stop = ('unrun', f"conditional step {s.get('n')} ({action} {s.get('target') or ''})")
            break
        implied = status == 'implied'
        if action == 'sign_in':
            acc = s.get('account') or {}
            if not acc.get('password') or not (acc.get('account') or acc.get('email')):
                stop = ('unrun', f"sign-in step {s.get('n')}: no stated credentials")
                break
            steps.append({'kind': 'sign_in', 'role': s.get('role'), 'identity': acc.get('account') or acc.get('email'),
                          'email': acc.get('email') or '', 'password': acc.get('password'), 'implied': implied,
                          'text': s.get('text', '')})
        elif action in ('open', 'click', 'menu'):
            target = s.get('target')
            if not target:
                stop = ('unrun', f"step {s.get('n')} has no target")
                break
            step = {'kind': 'click', 'name': target, 'role': s.get('role'), 'menu': action == 'menu',
                    'alternatives': list(s.get('alternatives') or []), 'entity': bool(s.get('entity')),
                    'scopeName': (s.get('scope') or {}).get('name'), 'implied': implied, 'text': s.get('text', '')}
            region = _step_region(s)
            if region:
                step['region'] = region
            if MUTATING.search(target) and not step['entity']:
                step['kind'] = 'check'
                steps.append(step)
                stop = ('end', f'mutating control "{target}" (checked, not clicked)')
                break
            steps.append(step)
        elif action in ('home', 'reload', 'back'):
            steps.append({'kind': action, 'implied': implied, 'text': s.get('text', '')})
        else:
            stop = ('end', f'first non-navigation step: {action}')
            break
    return steps, stop, notes

def _recipe(requirement):
    from .when_plan import requirement_context
    ctx = requirement_context(requirement)
    quoted = list(ctx.quoted.values())
    sign = [q for q in quoted if re.fullmatch(r'(?i)(?:sign|log)\s*-?\s*in', q)]
    ident = sorted({q for q in quoted if re.fullmatch(r'(?i)(?:username|user\s*name)(?:\s+or\s+e-?mail(?:\s+address)?)?|'
                                                       r'e-?mail(?:\s+address)?(?:\s+or\s+username)?|login|identifier', q)},
                   key=lambda q: (-len(q), q))
    pw = sorted({q for q in quoted if re.fullmatch(r'(?i)password', q)}, key=len)
    entry = ctx.signin.get('entry') or (sign[0] if sign else 'Sign in')
    return {'entry': entry, 'identity': ident or ['Username or email', 'Username', 'Email'], 'password': pw or ['Password'],
            'submit': [entry] + [q for q in sign if q != entry]}

def _start_app(project_dir, end, node_modules=None, ports=range(4880, 4890)):
    backend = project_dir / 'backend'
    nm = Path(node_modules or backend / 'node_modules').resolve()
    node = shutil.which('node')
    start = sp._start_file(backend)
    if not node or not start or not (nm / '@playwright' / 'test').is_dir():
        raise RuntimeError('node, backend start file or @playwright/test unavailable')
    port = next((p for p in ports if not sp._port_taken(p)), None)
    if port is None:
        raise RuntimeError('ports busy')
    scratch = os.environ.get('ARC_OUTSIDER_SCRATCH') or tempfile.gettempdir()
    work = Path(tempfile.mkdtemp(prefix='plan-walk-', dir=scratch))
    sink = None
    try:
        ignore = shutil.ignore_patterns('node_modules', '.git', '.config', '*key*.txt', 'credentials.csv', '.env*',
                                        '*.db*', '*.sqlite*', 'test', 'tests', 'test-e2e', 'test-results',
                                        'playwright-report', '.arc-test-db', '__pycache__')
        shutil.copytree(backend, work / 'backend', ignore=ignore)
        shutil.copytree(project_dir / 'frontend' / 'dist', work / 'frontend' / 'dist')
        data = work / 'data'
        data.mkdir()
        env = {**os.environ, 'PORT': str(port), 'NODE_PATH': str(nm), 'DATA_DIR': str(data),
               'ARC_DB_FILE': str(data / 'database.db'), 'DATABASE_FILE': str(data / 'database.db'),
               'TEMP': str(work), 'TMP': str(work)}
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        sink = (work / 'backend.log').open('w', encoding='utf-8')
        server = subprocess.Popen([node, start], cwd=work / 'backend', env=env, stdout=sink, stderr=sink, **flags)
        _record(server.pid)
    except Exception:
        if sink:
            sink.close()
        shutil.rmtree(work, ignore_errors=True)
        raise
    base = f'http://127.0.0.1:{port}'
    try:
        while time.monotonic() < end - 7:
            if server.poll() is not None:
                raise RuntimeError('backend exited during startup')
            try:
                sp._request(base, 'GET', '/', None, {}, min(1, max(0.1, end - time.monotonic())))
                break
            except OSError:
                time.sleep(0.2)
        time.sleep(0.15)
        sink.flush()
        if server.poll() is not None or 'EADDRINUSE' in (work / 'backend.log').read_text(encoding='utf-8', errors='replace'):
            raise RuntimeError('backend failed or port ownership uncertain')
    except Exception:
        _stop(server)
        sink.close()
        shutil.rmtree(work, ignore_errors=True)
        raise
    return server, work, sink, base, node, env

def plan_walk_report(requirement: str, project_dir: Path, time_limit: float = 240.0, log=print, node_modules=None,
                     leaf_ids=None, include_generated: bool = False) -> dict:
    def note(message):
        try:
            log('[plan-walk] ' + message)
        except Exception:
            pass
    report = {'completed': False, 'scenarios': [], 'notes': [], 'runtime': 0.0, 'walks': 0}
    server = runner = sink = work = None
    t0 = time.monotonic()
    try:
        from .when_plan import plans_for
        end = t0 + max(0.0, float(time_limit))
        project_dir = Path(project_dir)
        if not (project_dir / 'frontend' / 'dist' / 'index.html').is_file():
            report['notes'].append('skipped: frontend/dist is not built')
            return report
        plans = [p for p in plans_for(requirement, leaf_ids) if include_generated or not p.get('generated')]
        rows, walks, by_sig = [], [], {}
        for plan in plans:
            steps, stop, notes = _plan_walk_steps(plan)
            row = {'leaf': plan.get('leaf'), 'scenario': plan.get('scenario'), 'index': plan.get('index'),
                   'nav': len(steps), 'stop': {'kind': stop[0], 'reason': stop[1]}, 'notes': notes, 'findings': [],
                   'checked': 0, 'status': 'pending'}
            rows.append(row)
            if not steps:
                row['status'] = 'unrun' if stop[0] == 'unrun' else 'no-navigation'
                continue
            sig = json.dumps([{k: v for k, v in s.items() if k != 'text'} for s in steps], sort_keys=True)
            if sig not in by_sig:
                by_sig[sig] = len(walks)
                walks.append({'id': len(walks), 'steps': steps})
            row['walk'] = by_sig[sig]
        report['scenarios'] = rows
        report['walks'] = len(walks)
        note(f'{len(plans)} plans, {len(walks)} distinct navigation prefixes')
        if not walks:
            report['completed'] = True
            return report
        server, work, sink, base, node, env = _start_app(project_dir, end, node_modules)
        cfg = {'base': base, 'walks': walks, 'recipe': _recipe(requirement),
               'deadline': int((time.time() + max(0, end - time.monotonic() - 5)) * 1000)}
        (work / 'plan-walk.cjs').write_text(PLAN_RUNNER, encoding='utf-8')
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        runner = subprocess.Popen([node, str(work / 'plan-walk.cjs')], cwd=work, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **flags)
        _record(runner.pid)
        raw, err = runner.communicate(json.dumps(cfg), timeout=max(1.0, end - time.monotonic() - 2))
        result = json.loads(raw or '{}')
        report['notes'].extend(result.get('notes', []))
        if not result:
            report['notes'].append('runner failed: ' + (err or '')[-300:])
        done = {w['id']: w for w in result.get('walks', [])}
        for row in rows:
            if 'walk' not in row:
                continue
            w = done.get(row.pop('walk'))
            if w is None:
                row['status'] = 'unrun'
                row['stop'] = {'kind': 'unrun', 'reason': 'not walked (deadline)'}
                continue
            row['checked'] = len(w['steps'])
            row['steps'] = w['steps']
            for s in w['steps']:
                if s['outcome'] not in ('ok', 'uncertain'):
                    row['findings'].append({k: s.get(k) for k in ('kind', 'name', 'outcome', 'implied', 'route', 'detail',
                                                                 'visible', 'ambiguous', 'after')})
                if s.get('note'):
                    row['notes'].append(f"step {s['i'] + 1} {s.get('name') or s['kind']}: {s['note'].strip()}")
            if any(f['outcome'] != 'ambiguous' for f in row['findings']):
                row['status'] = 'finding'
            elif w['status'] == 'unrun':
                row['status'] = 'unrun'
                row['stop'] = w.get('stop') or row['stop']
            elif row['stop']['kind'] == 'unrun':
                row['status'] = 'unrun'
                row['partial'] = True
            else:
                row['status'] = 'clean'
        report['completed'] = bool(result.get('completed'))
        return report
    except Exception as exc:
        report['notes'].append('incomplete: ' + type(exc).__name__ + ': ' + str(exc)[:200])
        return report
    finally:
        for process in (runner, server):
            try:
                _stop(process)
            except Exception:
                report['notes'].append('cleanup could not confirm child exit')
        if sink:
            try:
                sink.close()
            except Exception:
                pass
        if work:
            shutil.rmtree(work, ignore_errors=True)
        report['runtime'] = round(time.monotonic() - t0, 1)
        for message in report['notes']:
            note(message)

def _finding_text(row, f):
    implied = (' -- an IMPLIED step: the scenario acts on it without saying how it is reached' if f.get('implied') else '')
    name = f.get('name') or 'Sign in'
    route = f.get('route')
    if f['outcome'] == 'role-mismatch':
        return (f'“{name}” on {route}: {f.get("detail")}{implied}.',
                f'Render “{name}” as the kind of control the requirement states (a link stays a link); keep every other '
                'control of that name as it is.')
    if f['outcome'] == 'missing':
        vis = ', '.join(f'“{v}”' for v in (f.get('visible') or [])[:8])
        return (f'Step “{name}”{implied}: no visible link/button/tab/menu item with exactly that name on {route}'
                + (f' ({f["detail"].strip()})' if (f.get('detail') or '').strip() else '') + f'. Visible controls: {vis or "(none)"}.',
                f'Expose a visible control named exactly “{name}” on the page the scenario reaches at this step '
                '(the requirement\'s path); do not move it behind a control the scenario never names.')
    if f['outcome'] in ('outside-region', 'no-landmark'):
        word, _, element = str(f.get('detail') or '').partition('|')
        element = element or 'landmark'
        if f['outcome'] == 'no-landmark':
            return (f'Step “{name}”{implied} on {route}: the page has no {element} landmark; the requirement names the '
                    f'{word} for this step.',
                    f'Add the {element} landmark (the page\'s primary content) to that page and put the control '
                    f'“{name}” inside it; keep every other control of that name as it is.')
        return (f'Step “{name}”{implied} on {route}: the requirement places “{name}” in the {word}, but no control of that '
                f'name is inside the {element} landmark of that page (it is only elsewhere on the page).',
                f'Show “{name}” inside the {element} landmark of that page (the {word}), with the role the requirement '
                'states; keep the other one as it is.')
    if f['outcome'] == 'wrong-landing':
        return (f'Clicking “{name}”{implied} on {route} did not open a page whose heading names it ({f.get("detail")}).',
                f'Make “{name}” open its own page with a heading that shows its name.')
    if f['outcome'] == 'disabled':
        return (f'“{name}”{implied} is visible but disabled on {route}.', 'Enable it for this user and state.')
    if f['outcome'] == 'sign-in-failed':
        return (f'Signing in through “{name}” fails: {f.get("detail")}.',
                'The sign-in entry must open the sign-in form with the labelled fields the requirement names.')
    return (f'“{name}”: {f["outcome"]} {f.get("detail") or ""}', 'Check this navigation step.')

def plan_walk_check(requirement: str, project_dir: Path, time_limit: float = 240.0, log=print, node_modules=None,
                    leaf_ids=None) -> tuple[list[dict], bool]:
    def say(message):
        try:
            log('[plan-walk] ' + message)
        except Exception:
            pass
    try:
        report = plan_walk_report(requirement, project_dir, time_limit=time_limit, log=log, node_modules=node_modules,
                                  leaf_ids=leaf_ids)
        groups = {}
        for row in report['scenarios']:
            for f in row.get('findings') or []:
                if f['outcome'] == 'ambiguous':
                    say(f"advisory: {row['scenario']}: “{f.get('name')}” is ambiguous on {f.get('route')}")
                    continue
                groups.setdefault((f['outcome'], f.get('route'), f.get('name'), bool(f.get('implied'))), []).append((row, f))
        findings = []
        for items in groups.values():
            problem, fix = _finding_text(*items[0])
            scen = '; '.join(dict.fromkeys(f"{r['leaf']}: {r['scenario']}" for r, _ in items))[:600]
            findings.append({'where': f'plan-walk: rendered app, frontend/src (route {items[0][1].get("route")})',
                             'requirement': scen, 'problem': problem + f' Scenarios: {len(items)}.', 'fix': fix,
                             'implied': bool(items[0][1].get('implied')), '_n': len(items)})
        findings.sort(key=lambda f: (f['implied'], -f['_n']))
        for f in findings:
            f.pop('_n', None)
            f.pop('implied', None)
        rows = report['scenarios']
        say(f"{len(rows)} scenarios: " + ', '.join(f"{k} {sum(1 for r in rows if r['status'] == k)}"
                                                    for k in ('clean', 'finding', 'unrun', 'no-navigation'))
            + f" (unrun = not run or only partly run, never counted as passed); {report['runtime']} s")
        return findings[:10], bool(report.get('completed'))
    except Exception as exc:
        say('incomplete: ' + type(exc).__name__)
        return [], False
