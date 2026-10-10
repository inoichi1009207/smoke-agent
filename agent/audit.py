from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .detaudit import deterministic_findings
from .fsutil import BackgroundProcess, free_port, fresh_data_env, npm_bin, run_cmd, wait_http
from .llm import LLM
from .settings import SETTINGS, setting_on
from .test_author import describe_node

SNAPSHOT_SCRIPT = """
const { chromium } = require('@playwright/test');
// v132 (research/gridlayout/gridcheck.js, verbatim logic): are the rows of a grid/table laid out as ROWS -- cells side by
// side, rows one below the other? Precision over recall: a container is reported only when MOST of its multi-cell rows
// are stacked, or MOST of its consecutive row pairs share one line. No backslashes (this is a non-raw Python string).
const GRID_CHECK = () => {
  const t0 = performance.now();
  const CONTAINERS = '[role=grid],[role=table],[role=treegrid],table:not([role])';
  const ROWSEL = '[role=row],tr:not([role])';
  const CELLSEL = '[role=gridcell],[role=cell],[role=columnheader],[role=rowheader],td:not([role]),th:not([role])';
  const box = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return null;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.position === 'fixed') return null;
    return r;
  };
  const vOverlap = (a, b) => Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const hOverlap = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const label = (el) => ((el.getAttribute('aria-label') || '') || (el.getAttribute('role') || el.tagName.toLowerCase())).slice(0, 60);
  const rr = (r) => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  const found = [];
  const seen = [];
  for (const grid of Array.from(document.querySelectorAll(CONTAINERS)).slice(0, 20)) {
    if (!box(grid)) continue;
    const rows = Array.from(grid.querySelectorAll(ROWSEL))
      .filter((row) => row.parentElement && row.parentElement.closest(CONTAINERS) === grid)
      .slice(0, 60);
    let multi = 0;
    let stacked = 0;
    let example = null;
    const rowBoxes = [];
    for (const row of rows) {
      const cells = Array.from(row.querySelectorAll(CELLSEL))
        .filter((c) => c.parentElement && c.parentElement.closest(ROWSEL) === row)
        .map(box).filter(Boolean).slice(0, 40);
      if (cells.length < 2) continue;
      multi += 1;
      let sideBySide = 0;
      let below = 0;
      for (let i = 1; i < cells.length; i += 1) {
        const a = cells[i - 1];
        const b = cells[i];
        if (vOverlap(a, b) >= 0.5 * Math.min(a.height, b.height)) sideBySide += 1;
        else if (b.top >= a.bottom - 2) below += 1;
      }
      if (sideBySide === 0 && below === cells.length - 1) {
        stacked += 1;
        if (!example) example = { row: (row.innerText || '').trim().replace(/[ ]+/g, ' ').slice(0, 60), cells: cells.slice(0, 3).map(rr) };
      }
      rowBoxes.push({
        left: Math.min(...cells.map((c) => c.left)), right: Math.max(...cells.map((c) => c.right)),
        top: Math.min(...cells.map((c) => c.top)), bottom: Math.max(...cells.map((c) => c.bottom)),
        height: Math.min(...cells.map((c) => c.height)),
      });
    }
    let pairs = 0;
    let sameLine = 0;
    for (let i = 1; i < rowBoxes.length; i += 1) {
      const a = rowBoxes[i - 1];
      const b = rowBoxes[i];
      pairs += 1;
      const aLine = { top: a.top, bottom: a.top + a.height };
      const bLine = { top: b.top, bottom: b.top + b.height };
      if (vOverlap(aLine, bLine) >= 0.5 * Math.min(a.height, b.height) && hOverlap(a, b) < 2) sameLine += 1;
    }
    seen.push({ grid: label(grid), rows: multi });
    const cellsStacked = multi >= 1 && stacked * 2 > multi;
    const rowsSideBySide = pairs >= 1 && sameLine * 2 > pairs;
    if (cellsStacked || rowsSideBySide) {
      found.push({
        grid: label(grid), rowsChecked: multi, stackedRows: stacked, rowPairs: pairs, sameLineRowPairs: sameLine,
        problem: cellsStacked
          ? 'the cells of a row are stacked vertically: one row is not laid out as one line of cells'
          : 'different rows are laid out side by side on the same line',
        example,
      });
    }
  }
  return { ms: Math.round(performance.now() - t0), seen: seen.slice(0, 10), found };
};
// v142: ONE row whose cells are stacked vertically. Sheet v141: the column-header row was a display:block div role=row, so
// the headers A-L stood one below the other in the first column while the data rows were flex -- GRID_CHECK's "most
// rows" rule, every accessibility-tree check and both red teams passed it. Every role=row and plain tr of the page, in a
// grid or not. Positive shape only: >= 2 visible cells and EVERY next cell starts below the previous one (its top at or
// below that cell's bottom, 2 px slack) in the SAME column (horizontal overlap >= half the narrower width). A wrapped
// row (some cells side by side) or a staircase is not this shape. No backslashes (non-raw Python string).
const ROW_CHECK = () => {
  const t0 = performance.now();
  const CONTAINERS = '[role=grid],[role=table],[role=treegrid],table:not([role])';
  const ROWSEL = '[role=row],tr:not([role])';
  const CELLSEL = '[role=gridcell],[role=cell],[role=columnheader],[role=rowheader],td:not([role]),th:not([role])';
  const box = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return null;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.position === 'fixed') return null;
    return r;
  };
  const hOverlap = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const flat = (text) => String(text || '').split(String.fromCharCode(10)).join(' ').trim().slice(0, 12);
  const label = (el) => ((el.getAttribute('aria-label') || '') || (el.getAttribute('role') || el.tagName.toLowerCase())).slice(0, 60);
  const rr = (r) => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  let checked = 0;
  let stackedRows = 0;
  const found = [];
  for (const row of Array.from(document.querySelectorAll(ROWSEL)).slice(0, 400)) {
    const cells = Array.from(row.querySelectorAll(CELLSEL))
      .filter((c) => c.parentElement && c.parentElement.closest(ROWSEL) === row)
      .map((c) => ({ el: c, r: box(c) })).filter((c) => c.r).slice(0, 40);
    if (cells.length < 2) continue;
    checked += 1;
    let stacked = true;
    for (let i = 1; i < cells.length && stacked; i += 1) {
      const a = cells[i - 1].r;
      const b = cells[i].r;
      stacked = b.top >= a.bottom - 2 && hOverlap(a, b) >= 0.5 * Math.min(a.width, b.width);
    }
    if (!stacked) continue;
    stackedRows += 1;
    if (found.length < 5) {
      const container = row.closest(CONTAINERS);
      found.push({ container: container ? label(container) : '', cells: cells.length,
        row: cells.slice(0, 4).map((c) => flat(c.el.innerText)).join(' | '), boxes: cells.slice(0, 3).map((c) => rr(c.r)) });
    }
  }
  return { ms: Math.round(performance.now() - t0), rowsChecked: checked, stackedRows, found };
};
// like GRID_TWICE: a stacked row counts only when a second look ~600 ms later still finds it (same container and cells)
const ROW_TWICE = async (page) => {
  const first = await page.evaluate(ROW_CHECK);
  if (!first.found.length) return first;
  await page.waitForTimeout(600);
  const second = await page.evaluate(ROW_CHECK);
  const key = (f) => f.container + '|' + f.row + '|' + f.cells;
  const before = new Set(first.found.map(key));
  return { ms: first.ms + second.ms, measured: 2, rowsChecked: second.rowsChecked, stackedRows: second.stackedRows,
    found: second.found.filter((f) => before.has(key(f))) };
};
const HAS_GRID = () => !!document.querySelector('[role=grid],[role=table],[role=treegrid]');
// audit r35 C4-3: a skeleton or a transient state is measured once and gone. Measure twice, ~600 ms apart, and keep a
// finding only when BOTH measurements find it (same grid, same problem). No backslashes here either.
const GRID_TWICE = async (page) => {
  const first = await page.evaluate(GRID_CHECK);
  if (!first.found.length) return first;
  await page.waitForTimeout(600);
  const second = await page.evaluate(GRID_CHECK);
  const key = (f) => f.grid + '|' + f.problem;
  const before = new Set(first.found.map(key));
  return { ms: first.ms + second.ms, measured: 2, seen: second.seen, found: second.found.filter((f) => before.has(key(f))) };
};
// v142 visual142 (research/v142-drafts/visual142.md; human exploratory screenshots of sheet v141 / github v129): what a
// person SEES on a page and its accessibility tree does not tell. Measured here (DOM, geometry, computed style), decided in
// agent/visual_checks.py. texts: visible text nodes (not pre/code/textarea) with a raw ISO timestamp, float noise or a
// leaked NaN / undefined / [object Object] / Invalid Date. popups: a visible menu / listbox with visible options, its
// opener (aria-controls, or a nearby aria-haspopup / aria-expanded control) and what it floats over. selected: an item
// marked aria-selected / aria-current / aria-pressed that looks EXACTLY like every unmarked sibling (own box, wrappers up
// to the group, ::before / ::after, visible descendants) and that no positioned decoration marks. blank: nothing
// visible at all. heading / fields: raw measurements, logged only. No backslashes (non-raw Python string).
const VISUAL_CHECK = () => {
  const t0 = performance.now();
  const body = document.body;
  if (!body) return { ms: 0, texts: [], popups: [], selected: [], checkedSelected: 0, blank: false, drawn: '', heading: null, fields: [] };
  const flat = (text) => String(text || '').split(String.fromCharCode(10)).join(' ').split(String.fromCharCode(9)).join(' ')
    .replace(/[ ]+/g, ' ').trim();
  const painted = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const shown = (el) => { if (!painted(el)) return false; const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const name = (el) => flat(el.getAttribute('aria-label') || el.innerText || el.textContent).slice(0, 60);
  const rr = (r) => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  // ---- texts
  const texts = [];
  const LOOSE = /[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}|[0-9][.][0-9]*(0{6,}|9{6,})[1-9]|Invalid Date|NaN|undefined|object Object/;
  const SKIP = 'pre,code,kbd,samp,script,style,textarea,noscript,template,[contenteditable=true],[contenteditable=""]';
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let visited = 0;
  for (let node = walker.nextNode(); node && texts.length < 30 && visited < 20000; node = walker.nextNode()) {
    visited += 1;
    const value = node.nodeValue || '';
    if (!LOOSE.test(value)) continue;
    const el = node.parentElement;
    if (!el || el.closest(SKIP) || !painted(el)) continue;
    texts.push({ text: flat(value).slice(0, 160), tag: el.tagName.toLowerCase(),
      mono: /mono|courier|consolas/i.test(getComputedStyle(el).fontFamily || ''),
      context: flat(el.parentElement ? el.parentElement.innerText : '').slice(0, 120) });
  }
  // ---- popups
  const OPENER = ':is(button,a,input,[role=button],[role=combobox],[role=menuitem],[role=tab]):is([aria-haspopup]:not([aria-haspopup=false]),[aria-expanded])';
  const ITEMS = '[role=option],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox]';
  const BELOW = 'a,button,input,select,textarea,img,svg,li,td,th,h1,h2,h3,h4,h5,h6,[role=button],[role=link],[role=row],[role=gridcell],[role=columnheader],[role=tab],[role=listitem]';
  const popups = [];
  for (const pop of Array.from(document.querySelectorAll('[role=menu],[role=listbox]')).slice(0, 30)) {
    if (!shown(pop) || pop.closest('[aria-hidden=true],[inert]')) continue;
    const items = Array.from(pop.querySelectorAll(ITEMS)).filter(shown);
    if (!items.length) continue;
    let trigger = null;
    let via = '';
    if (pop.id) {
      const id = CSS.escape(pop.id);
      trigger = document.querySelector('[aria-controls~="' + id + '"],[aria-owns~="' + id + '"]');
      if (trigger && pop.contains(trigger)) trigger = null;
      if (trigger) via = 'aria-controls';
    }
    for (let node = pop, depth = 0; !trigger && node && node !== body && depth < 3; node = node.parentElement, depth += 1) {
      for (let sib = node.previousElementSibling, hops = 0; !trigger && sib && hops < 3; sib = sib.previousElementSibling, hops += 1) {
        const found = sib.matches(OPENER) ? [sib] : Array.from(sib.querySelectorAll(OPENER));
        if (found.length) { trigger = found[found.length - 1]; via = 'nearby'; }
      }
    }
    const active = document.activeElement;
    const focused = !!active && active !== body && (pop.contains(active) || (!!trigger && (trigger === active || trigger.contains(active))));
    let overlay = false;
    for (let e = pop; e && e !== body; e = e.parentElement) {
      if (trigger && e.contains(trigger)) break;
      const position = getComputedStyle(e).position;
      if (position === 'absolute' || position === 'fixed') { overlay = true; break; }
    }
    const r = pop.getBoundingClientRect();
    const covers = [];
    let points = 0;
    let onTop = 0;
    for (const fx of [0.2, 0.5, 0.8]) {
      for (const fy of [0.2, 0.5, 0.8]) {
        const x = r.left + r.width * fx;
        const y = r.top + r.height * fy;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
        points += 1;
        const stack = document.elementsFromPoint(x, y);
        if (!stack.length || !pop.contains(stack[0])) continue;
        onTop += 1;
        for (const e of stack) {
          if (pop.contains(e) || e.contains(pop) || e === body || e === document.documentElement) continue;
          const own = Array.from(e.childNodes).some((c) => c.nodeType === 3 && c.nodeValue.trim());
          if (own || e.matches(BELOW)) {
            const label = name(e) || e.tagName.toLowerCase();
            if (!covers.includes(label)) covers.push(label);
            break;
          }
        }
      }
    }
    const t = trigger ? trigger.getBoundingClientRect() : null;
    popups.push({ role: pop.getAttribute('role'), label: flat(pop.getAttribute('aria-label') || '').slice(0, 60),
      items: items.length, first: items.slice(0, 3).map(name), rect: rr(r), position: getComputedStyle(pop).position, overlay,
      points, onTop, covers: covers.slice(0, 4), focused,
      trigger: trigger ? { name: name(trigger), tag: trigger.tagName.toLowerCase(), role: trigger.getAttribute('role') || '', via,
        expanded: trigger.getAttribute('aria-expanded'), haspopup: trigger.getAttribute('aria-haspopup'),
        controls: trigger.getAttribute('aria-controls') || '', shown: shown(trigger),
        gap: Math.round(Math.max(r.top - t.bottom, t.top - r.bottom, r.left - t.right, t.left - r.right)) } : null });
  }
  // ---- selected items that look like their siblings
  const STATE = '[aria-selected=true],[aria-current]:not([aria-current=false]),[aria-pressed=true]';
  const GROUP = '[role=tablist],[role=listbox],[role=tree],[role=row],[role=radiogroup],[role=toolbar],[role=menu],[role=menubar],[role=list],[role=navigation],nav,ul,ol';
  const CRUMB = '[aria-label*=breadcrumb i],[class*=breadcrumb i]';
  const PROPS = ['color', 'backgroundColor', 'backgroundImage', 'fontWeight', 'fontStyle', 'fontSize', 'textDecorationLine',
    'borderTopColor', 'borderTopWidth', 'borderTopStyle', 'borderRightColor', 'borderRightWidth', 'borderRightStyle',
    'borderBottomColor', 'borderBottomWidth', 'borderBottomStyle', 'borderLeftColor', 'borderLeftWidth', 'borderLeftStyle',
    'boxShadow', 'outlineStyle', 'outlineWidth', 'outlineColor', 'opacity', 'filter', 'transform',
    // r46v: SVG icons are told apart by fill / stroke, CSS icons by a mask image
    'fill', 'stroke', 'strokeWidth', 'maskImage', 'webkitMaskImage'];
  const look = (e, pseudo) => { const s = getComputedStyle(e, pseudo); return PROPS.map((p) => s[p]).join('|') + (pseudo ? '|' + s.content : ''); };
  // r46v: what a graphic draws is not in its computed style -- the svg markup (shape, <use href>) and the image source are
  // part of the signature; canvas / video / frames cannot be compared at all (opaque: logged, never reported)
  const OPAQUE = 'canvas,video,iframe,object,embed';
  const drawing = (d) => { const tag = d.tagName.toLowerCase();
    return tag === 'svg' ? '|svg:' + flat(d.outerHTML).slice(0, 4000) : (tag === 'img' ? '|img:' + (d.currentSrc || d.getAttribute('src') || '') : ''); };
  const signature = (item, group) => {
    const parts = [];
    for (let e = item; e && e !== group; e = e.parentElement) parts.push(look(e) + '#' + look(e, '::before') + '#' + look(e, '::after') + drawing(e));
    for (const d of Array.from(item.querySelectorAll('*')).slice(0, 30)) {
      if (painted(d)) parts.push(d.tagName + ':' + look(d) + '#' + look(d, '::before') + '#' + look(d, '::after') + drawing(d));
    }
    return parts.join('/');
  };
  let decorations = null;
  const decorationsOf = () => {
    if (decorations) return decorations;
    decorations = [];
    for (const e of Array.from(body.querySelectorAll('*')).slice(0, 8000)) {
      const position = getComputedStyle(e).position;
      if ((position === 'absolute' || position === 'fixed') && painted(e) && !flat(e.innerText)) decorations.push(e);
      if (decorations.length >= 400) break;
    }
    return decorations;
  };
  const touches = (d, el) => {
    const a = d.getBoundingClientRect();
    const b = el.getBoundingClientRect();
    const w = Math.min(a.right, b.right + 4) - Math.max(a.left, b.left - 4);
    const h = Math.min(a.bottom, b.bottom + 4) - Math.max(a.top, b.top - 4);
    return w >= 0.5 * Math.min(b.width, a.width) && h > 0;
  };
  const MARKS = [0x2713, 0x2714, 0x25CF, 0x2022, 0x25C9, 0x2605, 0x25B8, 0x25B6].map((c) => String.fromCharCode(c));
  const selected = [];
  let checkedSelected = 0;
  for (const el of Array.from(document.querySelectorAll(STATE)).slice(0, 80)) {
    if (!shown(el) || el.closest(CRUMB)) continue;
    const group = el.parentElement && el.parentElement.closest(GROUP);
    if (!group || group.closest(CRUMB)) continue;
    const role = el.getAttribute('role') || '';
    const kind = role ? '[role="' + role + '"]' : el.tagName.toLowerCase();
    const siblings = Array.from(group.querySelectorAll(kind)).filter((s) => s !== el && !s.contains(el) && !el.contains(s)
      && s.parentElement && s.parentElement.closest(GROUP) === group && !s.matches(STATE)
      && !s.matches('[disabled],[aria-disabled=true]') && shown(s)).slice(0, 40);
    if (!siblings.length) continue;
    checkedSelected += 1;
    const mine = signature(el, group);
    if (!siblings.every((s) => signature(s, group) === mine)) continue;
    const attr = el.getAttribute('aria-selected') === 'true' ? 'aria-selected' : (el.getAttribute('aria-pressed') === 'true' ? 'aria-pressed' : 'aria-current');
    const text = flat(el.innerText);
    const mark = MARKS.some((m) => text.includes(m) && !siblings.some((s) => flat(s.innerText).includes(m)));
    const indicator = decorationsOf().some((d) => !d.contains(el) && !el.contains(d) && touches(d, el)
      && !siblings.some((s) => s.contains(d) || touches(d, s)));
    const opaque = [el, ...siblings].some((s) => s.matches(OPAQUE) || Array.from(s.querySelectorAll(OPAQUE)).some(painted));
    selected.push({ attr, value: el.getAttribute(attr), role, tag: el.tagName.toLowerCase(), text: name(el),
      group: flat(group.getAttribute('aria-label') || '').slice(0, 40) || group.getAttribute('role') || group.tagName.toLowerCase(),
      siblings: siblings.length, siblingTexts: siblings.slice(0, 4).map(name), mark, indicator, opaque });
    if (selected.length >= 10) break;
  }
  // ---- blank page, first h1, fields without any visible edge
  const graphics = Array.from(body.querySelectorAll('img,svg,canvas,video,iframe,input,button,select,textarea,object,embed')).some(shown);
  // r46v: no text and none of those tags is not yet blank -- a control drawn with CSS only (a link with a background photo,
  // a role=button tile) or a pseudo element's text / image is something a person sees. drawn names the first such thing
  // (html / body backgrounds are the backdrop of every route and do not count).
  let drawn = '';
  if (!flat(body.innerText) && !graphics) {
    const CLICK = 'a[href],area[href],summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[onclick],[tabindex]:not([tabindex="-1"]),[contenteditable]:not([contenteditable=false])';
    const image = (s) => [s.backgroundImage, s.maskImage, s.webkitMaskImage].some((v) => !!v && v !== 'none');
    const pseudoShown = (e, p) => { const c = getComputedStyle(e, p).content; return !!c && c !== 'none' && c !== 'normal'; };
    const pseudoText = (e, p) => { const c = getComputedStyle(e, p).content; return pseudoShown(e, p) && c !== '""' && c !== "''"; };
    for (const e of Array.from(body.querySelectorAll('*')).slice(0, 5000)) {
      if (!shown(e)) continue;
      let why = '';
      if (e.matches(CLICK) || getComputedStyle(e).cursor === 'pointer') why = 'a visible control';
      else if (image(getComputedStyle(e))) why = 'a CSS background / mask image';
      else if (['::before', '::after'].some((p) => pseudoText(e, p))) why = 'text of a ::before / ::after';
      else if (['::before', '::after'].some((p) => pseudoShown(e, p) && image(getComputedStyle(e, p)))) why = 'an image in a ::before / ::after';
      if (why) { drawn = e.tagName.toLowerCase() + (name(e) ? ' "' + name(e) + '"' : '') + ': ' + why; break; }
    }
  }
  const blank = !flat(body.innerText) && !graphics && !drawn;
  // r46v: the h1 is compared with the page's body text in everything the eye uses besides size and weight -- colour, font,
  // style, case, spacing, decoration, the background it sits on (a dark title bar) and its own border / shadow
  const typeOf = (e) => { const s = getComputedStyle(e); return [s.color, s.fontFamily, s.fontStyle, s.textTransform, s.letterSpacing, s.textDecorationLine].join('|'); };
  const backdrop = (e) => {
    for (let x = e; x && x.nodeType === 1; x = x.parentElement) {
      const s = getComputedStyle(x);
      if (s.backgroundImage && s.backgroundImage !== 'none') return 'image ' + s.backgroundImage.slice(0, 80);
      if (s.backgroundColor && s.backgroundColor !== 'transparent' && !/rgba[(][^)]*, 0[)]/.test(s.backgroundColor)) return s.backgroundColor;
    }
    return 'none';
  };
  const h1 = Array.from(document.querySelectorAll('h1')).find(shown);
  let heading = null;
  if (h1) {
    const s = getComputedStyle(h1);
    const edge = s.boxShadow !== 'none' || ['Top', 'Right', 'Bottom', 'Left'].some((side) => parseFloat(s['border' + side + 'Width']) > 0 && s['border' + side + 'Style'] !== 'none');
    const BAR = 'h1,h2,h3,h4,h5,h6,header,nav,footer,aside,button,a,[role=banner],[role=navigation],[role=contentinfo]';
    const refEl = ['p', 'li,td,dd'].map((q) => Array.from(document.querySelectorAll(q)).slice(0, 200)
      .find((e) => shown(e) && !e.closest(BAR) && flat(e.innerText))).find((e) => !!e) || body;
    const r = getComputedStyle(refEl);
    heading = { text: name(h1), size: parseFloat(s.fontSize), weight: parseInt(s.fontWeight, 10) || 400,
      body: parseFloat(getComputedStyle(body).fontSize), type: typeOf(h1), backdrop: backdrop(h1), edge,
      ref: { tag: refEl.tagName.toLowerCase(), size: parseFloat(r.fontSize), weight: parseInt(r.fontWeight, 10) || 400, type: typeOf(refEl), backdrop: backdrop(refEl) } };
  }
  const fields = [];
  for (const f of Array.from(document.querySelectorAll('input,textarea,select')).slice(0, 60)) {
    if (['hidden', 'checkbox', 'radio', 'submit', 'button', 'reset', 'image', 'file', 'range', 'color'].includes(f.type) || !shown(f)) continue;
    const s = getComputedStyle(f);
    const edge = ['Top', 'Right', 'Bottom', 'Left'].some((side) => parseFloat(s['border' + side + 'Width']) > 0 && s['border' + side + 'Style'] !== 'none'
      && !/rgba[(][^)]*, 0[)]/.test(s['border' + side + 'Color']));
    let parentBg = 'rgba(0, 0, 0, 0)';
    for (let e = f.parentElement; e; e = e.parentElement) { const bg = getComputedStyle(e).backgroundColor; if (bg && !/rgba[(][^)]*, 0[)]/.test(bg)) { parentBg = bg; break; } }
    const ownBg = /rgba[(][^)]*, 0[)]/.test(s.backgroundColor) ? parentBg : s.backgroundColor;
    if (edge || s.boxShadow !== 'none' || ownBg !== parentBg) continue;
    fields.push({ name: flat(f.getAttribute('aria-label') || (f.labels && f.labels[0] ? f.labels[0].innerText : '') || f.getAttribute('placeholder') || '').slice(0, 60),
      tag: f.tagName.toLowerCase(), rect: rr(f.getBoundingClientRect()) });
    if (fields.length >= 5) break;
  }
  return { ms: Math.round(performance.now() - t0), texts, popups, selected, checkedSelected, blank, drawn, heading, fields };
};
// like GRID_TWICE: a text, popup, unmarked selection or blank page counts only when a second look ~600 ms later finds it again
const VISUAL_HIT = (v) => !!v && (v.texts.length > 0 || v.popups.length > 0 || v.selected.length > 0 || v.blank);
const VISUAL_TWICE = async (page) => {
  const first = await page.evaluate(VISUAL_CHECK);
  if (!VISUAL_HIT(first)) return first;
  await page.waitForTimeout(600);
  const second = await page.evaluate(VISUAL_CHECK);
  return { ...second, ms: first.ms + second.ms, measured: 2,
    texts: second.texts.filter((x) => first.texts.some((y) => y.text === x.text)),
    popups: second.popups.filter((x) => first.popups.some((y) => y.role === x.role && y.first.join('|') === x.first.join('|'))),
    selected: second.selected.filter((x) => first.selected.some((y) => y.attr === x.attr && y.text === x.text)),
    blank: first.blank && second.blank };
};
(async () => {
  const [base, ...routes] = process.argv.slice(2);
  const out = [];
  // v142 auditparam (platform sheet v141: the editor /workbooks/:id reached the model auditor in no batch -- routes with a
  // parameter are never snapshotted above and a crawled page records only its errors; "the editor does not render" was
  // reported in 8 batches). ARC_SNAPSHOT_PARAMS = {want: parameterised contract routes of this batch, all: every contract
  // route}. The first loaded page whose path is an instance of a wanted route (most specific route wins, as in a client
  // router) gets its accessibility snapshot and visible text recorded as a separate entry {instance: true} for the model
  // auditor only; agent/audit.py scripted_layer drops these entries before every scripted check, so the scripted layer
  // sees what it saw before. No backslashes in this block.
  const T0 = Date.now();
  const LATE_MS = 300000;
  const PARAMS = (() => { try { return JSON.parse(process.env.ARC_SNAPSHOT_PARAMS || '{}') || {}; } catch (e) { return {}; } })();
  const WANT = (Array.isArray(PARAMS.want) ? PARAMS.want : []).map(String).slice(0, 4);
  const ALL_ROUTES = (Array.isArray(PARAMS.all) ? PARAMS.all : []).map(String).concat(WANT);
  const captured = new Set();
  const instances = [];
  const pending = () => WANT.filter((t) => !captured.has(t));
  const segs = (p) => String(p || '').split('?')[0].split('#')[0].split('/').filter((s) => s);
  const fixedOf = (t) => segs(t).filter((s) => !s.startsWith(':') && !s.startsWith('*')).length;
  const decode = (s) => { try { return decodeURIComponent(s); } catch (e) { return s; } };
  const fits = (t, path) => {
    const a = segs(t);
    const b = segs(path);
    for (let i = 0; i < a.length; i += 1) {
      if (a[i].startsWith('*')) return true;
      if (i >= b.length) return false;
      if (!a[i].startsWith(':') && a[i].toLowerCase() !== decode(b[i]).toLowerCase()) return false;
    }
    return a.length === b.length;
  };
  const instanceOf = (path) => {
    if (!pending().length) return null;
    const hits = Array.from(new Set(ALL_ROUTES.filter((t) => fits(t, path))));
    if (!hits.length) return null;
    const best = Math.max(...hits.map(fixedOf));
    const top = hits.filter((t) => fixedOf(t) === best);
    return top.length === 1 && WANT.includes(top[0]) && !captured.has(top[0]) ? top[0] : null;
  };
  const pathOf = (url) => { try { const u = new URL(url); return u.pathname + u.search; } catch (e) { return ''; } };
  const takeInstance = async (page, via, errors) => {
    const route = pathOf(page.url());
    const template = instanceOf(route);
    if (!template) return;
    try {
      const aria = await page.locator('body').ariaSnapshot({ timeout: 5000 });
      const text = await page.locator('body').innerText({ timeout: 5000 });
      if (!String(text || '').trim()) return;  // a blank page: the crawl's own entry says so
      captured.add(template);
      instances.push({ route, instance: true, template, via, url: page.url(), console: errors,
        aria: String(aria || '').slice(0, 60000), ariaChars: String(aria || '').length,
        text: String(text).slice(0, 3000), textChars: String(text).length });
    } catch (error) {
      // not readable: nothing recorded, the crawl's own entry stands
    }
  };
  const browser = await chromium.launch();
  // at least 1280x800 (the default is 1280x720): responsive "card tables" stack their cells on narrow screens (v132)
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  for (const route of routes) {
    const page = await context.newPage();
    const entry = { route, console: [] };
    // A 401/403 on a signed-out page is the normal answer of a session probe, not a defect.
    page.on('console', (m) => { if (m.type() === 'error' && !/\\b(401|403)\\b/.test(m.text())) entry.console.push(m.text().slice(0, 200)); });
    page.on('pageerror', (e) => entry.console.push('uncaught: ' + String(e).slice(0, 200)));
    try {
      await page.goto(base + route, { waitUntil: 'networkidle', timeout: 15000 });
      entry.url = page.url();
      entry.aria = await page.locator('body').ariaSnapshot({ timeout: 5000 });
      entry.text = (await page.locator('body').innerText()).slice(0, 3000);
      entry.visual = await VISUAL_TWICE(page);  // v142 visual142: measured before the checks below scroll the page
      // Widgets the app renders on purpose (they carry a role or a test id) but that take up no space are
      // invisible to users and to visibility-based locators alike.
      entry.invisible = await page.evaluate(() => Array.from(document.querySelectorAll('[data-testid],[role]'))
        .filter((el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
          // a panel, menu or dialog that is CLOSED is meant to take no space: aria-hidden (on it or an ancestor),
          // aria-expanded="false" on its opener, or a closed state attribute (audit r41 #1)
          if (el.closest('[aria-hidden="true"],[inert]')) return false;
          if (el.getAttribute('data-state') === 'closed' || el.getAttribute('aria-expanded') === 'false') return false;
          if (/^(dialog|alertdialog|menu|listbox|tooltip|region|tabpanel)$/.test(el.getAttribute('role') || '') &&
              el.id && document.querySelector('[aria-controls~="' + el.id + '"][aria-expanded="false"]')) return false;
          return (r.width === 0 || r.height === 0) && s.display !== 'none' && !el.hasAttribute('hidden'); })
        .slice(0, 20)
        .map((el) => ({ testid: el.getAttribute('data-testid'), role: el.getAttribute('role'), html: el.outerHTML.slice(0, 160) })));
      entry.links = await page.evaluate(() => Array.from(document.querySelectorAll('a[href]'))
        .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .slice(0, 80)
        .map((el) => ({ name: (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 60), href: el.getAttribute('href'),
          inItem: !!el.closest('li,[role=listitem],article,tr,[role=row]') })));
      // The middle of an openable item (a heading carrying the item's control): a person opens the card by clicking
      // it, and a click there must not land on one of the item's OTHER controls (keep local v66: "Unpin note" filled
      // the middle of every note card, the card never opened). ADR-099.
      // Also for a card that opens on a click with no control in its heading (pointer cursor), and also once the card is
      // STRETCHED: in a grid row next to a taller card it grows, its content stays on top, and an action row that follows
      // the content lands in the middle (keep local v88, REQ-2.4: after a delete re-flowed the grid, the click on
      // "Project ideas" pressed "Pin note"). The card is stretched to 2.5x its height for the second look. ADR-099.
      entry.covered = await page.evaluate(() => {
        const found = [];
        const interactive = 'button,a[href],input,select,textarea,label,[role=button],[role=link],[role=checkbox],[role=switch],[role=menuitem]';
        const middle = (item) => {
          const r = item.getBoundingClientRect();
          const x = (Math.max(r.left, 0) + Math.min(r.right, innerWidth)) / 2;
          const y = (Math.max(r.top, 0) + Math.min(r.bottom, innerHeight)) / 2;
          return document.elementFromPoint(x, y);
        };
        for (const item of document.querySelectorAll('article,li,[role=listitem],[role=row]')) {
          const heading = item.querySelector('h1,h2,h3,h4,h5,h6,[role=heading]');
          if (!heading) continue;
          const opens = heading.querySelector(interactive) || getComputedStyle(item).cursor === 'pointer';
          if (!opens) continue;
          item.scrollIntoView({ block: 'center', inline: 'center' });  // audit r23: its middle may be off screen
          const r = item.getBoundingClientRect();
          if (r.width < 40 || r.height < 40) continue;
          const looks = [['', middle(item)]];
          const before = item.style.minHeight;
          item.style.minHeight = `${Math.round(r.height * 2.5)}px`;
          item.scrollIntoView({ block: 'center', inline: 'center' });
          looks.push([' (when the card is stretched, e.g. beside a taller card in a grid row)', middle(item)]);
          item.style.minHeight = before;
          for (const [when, hit] of looks) {
            const control = hit && hit.closest(interactive);
            if (control && item.contains(control) && !heading.contains(control)) {
              found.push({ title: heading.innerText.trim().slice(0, 60) + when,
                hit: (control.getAttribute('aria-label') || control.innerText || control.tagName).trim().slice(0, 40) });
              break;
            }
          }
          if (found.length >= 10) break;
        }
        return found;
      });
      // A title (tooltip) on an item's own container and on nothing inside it: whoever looks INSIDE the item for that
      // mark finds none (keep local v79: `<article title="Pinned">`, REQ-2.8.1/2.8.3). The conventions put it on the
      // container AND on a visible element inside it (ADR-089/090). ADR-099.
      entry.titleOutside = await page.evaluate(() => Array.from(document.querySelectorAll('article,li,[role=listitem],[role=row]'))
        .filter((el) => el.getAttribute('title'))
        .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .filter((el) => !Array.from(el.querySelectorAll('[title]')).some((inner) => inner.getAttribute('title') === el.getAttribute('title')))
        .slice(0, 10)
        .map((el) => ({ title: el.getAttribute('title'), item: (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 50) })));
      // One title twice INSIDE an item (keep local v99: `title="Pinned"` on the heading AND on the button in it): a locator
      // scoped to the item finds two and stops (REQ-2.8.1/2.8.3). ADR-099.
      entry.titleTwice = await page.evaluate(() => {
        const found = [];
        for (const item of document.querySelectorAll('article,li,[role=listitem],[role=row]')) {
          const counts = {};
          for (const inner of item.querySelectorAll('[title]')) {
            const r = inner.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) counts[inner.getAttribute('title')] = (counts[inner.getAttribute('title')] || 0) + 1;
          }
          for (const [title, count] of Object.entries(counts)) {
            if (title && count >= 2) found.push({ title, count, item: (item.getAttribute('aria-label') || item.innerText || '').trim().slice(0, 50) });
          }
          if (found.length >= 10) break;
        }
        return found;
      });
      // An item a person opens by clicking it (pointer cursor) whose heading carries no control: whoever opens it by its
      // title finds nothing to press (keep local v88: `<article class="cursor-pointer"><h3>Sprint goals</h3>`, the note
      // editor never opened -- REQ-2.4, 2.5.4, 2.7.1, 2.7.2). ADR-099.
      entry.titleNotControl = await page.evaluate(() => {
        const control = 'button,a[href],[role=button],[role=link]';
        return Array.from(document.querySelectorAll('article,li,[role=listitem]'))
          .filter((el) => getComputedStyle(el).cursor === 'pointer')
          .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
          .map((el) => el.querySelector('h1,h2,h3,h4,h5,h6,[role=heading]'))
          .filter((heading) => heading && heading.innerText.trim() && !heading.querySelector(control) && !heading.closest(control))
          .slice(0, 10)
          .map((heading) => ({ title: heading.innerText.trim().slice(0, 60) }));
      });
      // Form controls nobody can address by label: no <label>, no aria-label, no aria-labelledby.
      // An aria-label replaces the visible text as the accessible name: github v116 showed "Branch main" on the branch
      // selector while aria-label="main" named it "main"; the requirement names it "Branch <current branch name>" and 24
      // red-team tests stopped there (ADR-099 s63). detaudit compares both with the requirement's names.
      entry.relabeled = await page.evaluate(() => Array.from(document.querySelectorAll('[aria-label]'))
        .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map((el) => ({ tag: el.tagName.toLowerCase(), label: (el.getAttribute('aria-label') || '').trim(),
          text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80) }))
        .filter((x) => x.label && x.text && x.text !== x.label)
        .slice(0, 200));
      entry.unlabeled = await page.evaluate(() => Array.from(document.querySelectorAll('input,select,textarea'))
        .filter((el) => !['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type))
        .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .filter((el) => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
        .slice(0, 10)
        .map((el) => el.outerHTML.slice(0, 160)));
      entry.hasGrid = await page.evaluate(HAS_GRID);
      entry.gridLayout = await GRID_TWICE(page);
      entry.rowLayout = await ROW_TWICE(page);
    } catch (error) {
      entry.error = String(error).slice(0, 300);
    }
    out.push(entry);
    await page.close();
  }
  // Like a user: follow the links the pages show. They carry the real owners, repositories and ids that the contract
  // can only give as parameters (/:owner/:repo/compare is never snapshotted above), and a later batch that breaks an
  // earlier page -- github v112: the compare and settings pages threw "Objects are not valid as a React child" after
  // the branch API changed shape -- shows up here. Only pages that throw or come up blank are kept (ADR-099 s62).
  const seen = new Set(out.map((e) => e.route));
  const queue = [];
  for (const e of out) for (const l of (e.links || [])) queue.push(l.href);
  let crawled = 0;
  let reachedGrid = out.some((e) => e.hasGrid);
  while (queue.length && crawled < 30) {
    const href = queue.shift();
    if (!href || !href.startsWith('/') || href.startsWith('//') || seen.has(href) || /(log|sign)[-_ ]?out/i.test(href)) continue;
    seen.add(href);
    crawled += 1;
    const page = await context.newPage();
    const entry = { route: href, crawled: true, console: [] };
    page.on('console', (m) => { if (m.type() === 'error' && !/\\b(401|403|404)\\b/.test(m.text())) entry.console.push(m.text().slice(0, 200)); });
    page.on('pageerror', (e) => entry.console.push('uncaught: ' + String(e).slice(0, 200)));
    try {
      await page.goto(base + href, { waitUntil: 'networkidle', timeout: 10000 });
      entry.url = page.url();
      if (!(await page.locator('body').innerText()).trim()) entry.console.push('blank page: nothing is rendered');
      entry.relabeled = await page.evaluate(() => Array.from(document.querySelectorAll('[aria-label]'))
        .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map((el) => ({ tag: el.tagName.toLowerCase(), label: (el.getAttribute('aria-label') || '').trim(),
          text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80) }))
        .filter((x) => x.label && x.text && x.text !== x.label)
        .slice(0, 200));
      entry.hasGrid = await page.evaluate(HAS_GRID);
      entry.gridLayout = await GRID_TWICE(page);
      entry.rowLayout = await ROW_TWICE(page);
      entry.visual = await VISUAL_TWICE(page);  // v142 visual142
      if (entry.hasGrid) reachedGrid = true;
      const more = await page.evaluate(() => Array.from(document.querySelectorAll('a[href]')).map((a) => a.getAttribute('href')).slice(0, 60));
      for (const h of more) queue.push(h);
      await takeInstance(page, "following the pages' links", entry.console);  // v142 auditparam
    } catch (error) {
      entry.error = String(error).slice(0, 200);
    }
    if (entry.console.length || entry.error || (entry.relabeled || []).length) out.push(entry);  // audit r32: a page whose load throws is kept too
    else if (entry.gridLayout && entry.gridLayout.found.length) out.push(entry);  // v132: a grid whose rows are not rows
    else if (entry.rowLayout && entry.rowLayout.found.length) out.push(entry);  // v142: one row whose cells are stacked
    else if (VISUAL_HIT(entry.visual)) out.push(entry);  // v142 visual142: something a person sees is wrong
    await page.close();
  }
  // v132: no page reached so far shows a grid. Record pages are often opened by a BUTTON in a list item's heading, not a
  // link (sheet v110, platform aa68f1b3e760: <li><h3><button>Q3 Sales</button></h3></li>), so the crawl never gets there.
  // Click the first such button on "/" ONCE -- only one whose accessible name IS its heading's text, never a destructive
  // or session name -- and look at the page it opens. Kept only when the click changed the URL and that page has errors
  // or a grid finding.
  if (!reachedGrid) {
    const OPENERS = 'li :is(h1,h2,h3,h4,h5,h6) button, [role=listitem] :is(h1,h2,h3,h4,h5,h6) button';
    const page = await context.newPage();
    const entry = { route: '/', crawled: true, console: [] };
    page.on('console', (m) => { if (m.type() === 'error' && !/\\b(401|403|404)\\b/.test(m.text())) entry.console.push(m.text().slice(0, 200)); });
    page.on('pageerror', (e) => entry.console.push('uncaught: ' + String(e).slice(0, 200)));
    try {
      await page.goto(base + '/', { waitUntil: 'networkidle', timeout: 10000 });
      const index = await page.evaluate((selector) => {
        const risky = /delete|remove|trash|archive|discard|destroy|clear|reset|leave|unsubscribe|(log|sign)[-_ ]?(out|off)/i;
        return Array.from(document.querySelectorAll(selector)).findIndex((b) => {
          const heading = b.closest('h1,h2,h3,h4,h5,h6');
          const name = (b.getAttribute('aria-label') || b.innerText || '').trim();
          const r = b.getBoundingClientRect();
          return !!name && r.width > 0 && r.height > 0 && !!heading && name === (heading.innerText || '').trim() && !risky.test(name);
        });
      }, OPENERS);
      if (index >= 0) {
        const opener = page.locator(OPENERS).nth(index);
        const via = ((await opener.getAttribute('aria-label')) || (await opener.innerText()) || '').trim().slice(0, 60);
        entry.console.length = 0;  // errors of "/" itself are the snapshot's, not this page's
        const before = page.url();
        await opener.click({ timeout: 5000 });
        await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
        await page.waitForSelector('[role=grid],[role=table],[role=treegrid]', { timeout: 3000 }).catch(() => {});
        // audit r35 C4-2: a button named like its heading need not open anything ("Complete", "Toggle status"). The page
        // counts as the record's page only when the click NAVIGATED: same URL afterwards -> ignored, nothing kept.
        if (page.url() !== before) {
          const url = new URL(page.url());
          entry.route = url.pathname + url.search;
          entry.url = page.url();
          entry.via = via;
          entry.hasGrid = await page.evaluate(HAS_GRID);
          entry.gridLayout = await GRID_TWICE(page);
          entry.rowLayout = await ROW_TWICE(page);
          entry.visual = await VISUAL_TWICE(page);  // v142 visual142
          await takeInstance(page, 'clicking “' + via + '” on /', entry.console);  // v142 auditparam
        }
      }
    } catch (error) {
      entry.clickError = String(error).slice(0, 200);  // the click of this script failed: nothing about the application
    }
    if (entry.via && (entry.console.length || (entry.gridLayout && entry.gridLayout.found.length))) out.push(entry);
    else if (entry.via && entry.rowLayout && entry.rowLayout.found.length) out.push(entry);  // v142
    else if (entry.via && VISUAL_HIT(entry.visual)) out.push(entry);  // v142 visual142
    await page.close();
  }
  // v142 auditparam: a wanted route of which no loaded page was an instance. (1) A link the pages show (the crawl stops
  // after 30 pages) whose path is an instance -- at most 4 loads. (2) Still none: a record's title is a BUTTON by the
  // conventions (a list item's heading button), which the crawl above cannot follow -- on each snapshotted page (3 at
  // most; "/" only when the click above did not run there), click the first list-item heading button named like its
  // heading (never a destructive or session name), as the click above does. Only the instance entry is recorded;
  // nothing here reaches the scripted checks. Skipped once the script has run LATE_MS (the caller stops it at 480 s).
  const late = () => Date.now() - T0 > LATE_MS;
  if (pending().length && !late()) {
    const left = [];
    for (const e of out) for (const l of (e.links || [])) left.push(l.href);
    for (const h of queue) left.push(h);
    const tried = new Set();
    for (const href of left) {
      if (!pending().length || tried.size >= 4 || late()) break;
      if (!href || !href.startsWith('/') || href.startsWith('//') || seen.has(href) || tried.has(href) || /(log|sign)[-_ ]?out/i.test(href)) continue;
      if (!instanceOf(href)) continue;
      tried.add(href);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push('uncaught: ' + String(e).slice(0, 200)));
      try {
        await page.goto(base + href, { waitUntil: 'networkidle', timeout: 10000 });
        await takeInstance(page, "following the pages' links", errors);
      } catch (error) {
        // not loaded: nothing recorded
      }
      await page.close();
    }
  }
  const clickFrom = routes.filter((r) => !(r === '/' && !reachedGrid)).slice(0, 3);
  for (const from of clickFrom) {
    if (!pending().length || late()) break;
    const OPEN = 'li :is(h1,h2,h3,h4,h5,h6) button, [role=listitem] :is(h1,h2,h3,h4,h5,h6) button';
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push('uncaught: ' + String(e).slice(0, 200)));
    try {
      await page.goto(base + from, { waitUntil: 'networkidle', timeout: 10000 });
      const index = await page.evaluate((selector) => {
        const risky = /delete|remove|trash|archive|discard|destroy|clear|reset|leave|unsubscribe|(log|sign)[-_ ]?(out|off)/i;
        return Array.from(document.querySelectorAll(selector)).findIndex((b) => {
          const heading = b.closest('h1,h2,h3,h4,h5,h6');
          const name = (b.getAttribute('aria-label') || b.innerText || '').trim();
          const r = b.getBoundingClientRect();
          return !!name && r.width > 0 && r.height > 0 && !!heading && name === (heading.innerText || '').trim() && !risky.test(name);
        });
      }, OPEN);
      if (index >= 0) {
        const opener = page.locator(OPEN).nth(index);
        const via = ((await opener.getAttribute('aria-label')) || (await opener.innerText()) || '').trim().slice(0, 60);
        const start = page.url();
        await opener.click({ timeout: 5000 });
        await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
        if (page.url() !== start) await takeInstance(page, 'clicking “' + via + '” on ' + from, errors);
      }
    } catch (error) {
      // the click of this script failed: nothing recorded
    }
    await page.close();
  }
  for (const i of instances) out.push(i);
  await browser.close();
  console.log('ARC_SNAPSHOT_JSON' + JSON.stringify(out));
})().catch((error) => { console.log('ARC_SNAPSHOT_ERROR' + String(error).slice(0, 500)); process.exit(2); });
"""

SYSTEM_PROMPT = """You are an independent ACCEPTANCE AUDITOR. You receive (1) the requirement text of one feature, including
notes extracted from its reference screenshots, (2) the accessibility snapshot (ARIA roles, accessible names, values) and
visible text of the running application's pages, and (3) the source of the page components.
You have NOT seen any design document or test. Judge the application only against the requirement text.

Report every EXPLICIT requirement statement the application violates:
- a required page, link, button, field, option, checkbox, heading, section title or text that is missing;
- a wrong ARIA role (the requirement's word decides: 链接/link = link, 按钮/button = button, 下拉选择框 = combobox,
  复选框 = checkbox). "A link or button", "an entry" and 入口 leave the kind open; this application decides it in this
  order: (1) the kind another sentence of the requirement names for the same control; (2) the kind a control an
  earlier stage of the application already delivered has; (3) the defaults: a link for a pure navigation entry (its
  only effect is to open another page: the entries that take a signed-out visitor to the sign-in / sign-up pages, the
  shared header's navigation entries, a navigation bar's entries such as a repository's / organization's sections and
  a record's tabs, a record shown on a page that opens that record's page), ONE <button type="button"> for any other
  control (a list item's own title that opens the item in place: that button inside the item's heading). A kind that
  follows this order is never a finding; one that does not is (fix: render it as the kind the order gives, same name);
  a wrong accessible name or label; a label not associated with its control;
- a wrong default/initial value, missing option, wrong route or redirect, an error message not exposed with the role the
  requirement names for it (role="alert" when it names none);
- states the snapshot cannot show (signed-in header, error states): judge them from the component source.
Work systematically: go through the requirement sentence by sentence and check each statement against the snapshot and
the sources. The visual-reference notes show layout and structure only: nothing in them -- headings, section titles,
placeholders, helper texts -- is a finding unless the requirement's own text states it (ADR-058: screenshots never
outrank the requirement; the scripted layer already only logs missing screenshot titles). Report ONLY violations; never
list a statement that is satisfied. Do not invent requirements, do not comment on styling, code quality or anything
the requirement does not state.

Output ONE JSON object and nothing else:
{"findings": [{"where": "route or file", "requirement": "short verbatim quote", "problem": "what is wrong or missing",
               "fix": "the concrete change to make"}]}
Use an empty list when the application satisfies every explicit statement."""

def take_snapshot(project_dir: Path, routes: list[str], log=print, params: dict[str, list[str]] | None = None) -> list[dict[str, Any]] | None:
    backend = project_dir / "backend"
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        return None
    script = backend / ".arc-snapshot.cjs"
    script.write_text(SNAPSHOT_SCRIPT, encoding="utf-8")
    port = free_port(3300)
    server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **fresh_data_env(project_dir)})
    try:
        ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if not ready:
            log("[audit] application did not start; snapshot skipped")
            return None
        extra = {"ARC_SNAPSHOT_PARAMS": json.dumps(params)} if params and params.get("want") else None
        code, output = run_cmd(["node", str(script.name), f"http://127.0.0.1:{port}", *routes], cwd=backend, timeout=480, env=extra)
    finally:
        server.stop()
        try:
            script.unlink()
        except OSError:
            pass
    match = re.search(r"ARC_SNAPSHOT_JSON(.*)", output)
    if not match:
        log(f"[audit] snapshot unavailable ({output.strip()[-200:]!r})")
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

MAX_ROW_LAYOUT_FINDINGS = 3

def row_layout_findings(snapshot: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for page in snapshot or []:
        if not isinstance(page, dict):
            continue
        layout = page.get("rowLayout") if isinstance(page.get("rowLayout"), dict) else {}
        grid = page.get("gridLayout") if isinstance(page.get("gridLayout"), dict) else {}
        reported = {str(item.get("grid")) for item in grid.get("found") or [] if isinstance(item, dict)
                    and int(item.get("rowsChecked") or 0) and int(item.get("stackedRows") or 0) * 2 > int(item.get("rowsChecked") or 0)}
        rows = [item for item in layout.get("found") or [] if isinstance(item, dict)
                and int(item.get("cells") or 0) >= 2 and str(item.get("container") or "") not in reported]
        if not rows:
            continue
        first = rows[0]
        container = str(first.get("container") or "")
        where = f" of {container!r}" if container else ""
        text = " ".join(str(first.get("row") or "").split())[:60]

        left_to_grid = sum(int(item.get("stackedRows") or 0) for item in grid.get("found") or []
                           if isinstance(item, dict) and str(item.get("grid")) in reported)
        count = max(len(rows), int(layout.get("stackedRows") or 0) - left_to_grid)
        via = f" (reached by clicking “{page.get('via')}” on /)" if page.get("via") else ""
        out.append({
            "where": f"route {page.get('route')}",
            "requirement": "(a row shows its cells side by side on one line)",
            "problem": (f"The cells of a row are stacked vertically{via}: {count} row(s) show their cells one below the other in "
                        f"one column, e.g. the row{where} with {int(first.get('cells') or 0)} cells"
                        + (f" {text!r}" if text else "") + ". Whoever reads or clicks a cell by its row and column (the "
                        "column headers above their columns, the cell right of another) does not find it there."),
            "fix": ("The cells of a row are stacked vertically: give the row a horizontal layout (flex/grid/table-row, e.g. "
                    "`display: flex` like the other rows) so its cells sit side by side; keep the roles, names and order of "
                    "the cells as they are."),
        })
        if len(out) >= MAX_ROW_LAYOUT_FINDINGS:
            break
    return out

def page_sources(project_dir: Path, limit: int = 45000) -> str:
    chunks: list[str] = []
    total = 0
    base = project_dir / "frontend" / "src"
    for path in sorted(base.rglob("*.tsx")) if base.is_dir() else []:
        rel = path.relative_to(project_dir).as_posix()
        if rel.endswith("main.tsx"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if total + len(text) > limit:
            chunks.append(f"--- {rel} --- (omitted, {len(text)} chars)")
            continue
        chunks.append(f"--- {rel} ---\n{text}")
        total += len(text)
    return "\n\n".join(chunks)

def routes_from_contract(contract: str, members: list[str] | None = None, is_later=None) -> list[str]:
    from .contract import related

    own: list[str] = []
    others: list[str] = []
    try:
        for page in json.loads(contract).get("pages", []):
            route = str(page.get("route") or "").strip()
            if not route.startswith("/") or ":" in route or "*" in route or route == "/":
                continue
            bucket = own if members and related(page.get("requirements"), members) else others
            if route not in own and route not in others:
                bucket.append(route)
    except (json.JSONDecodeError, AttributeError, TypeError):

        for route in re.findall(r'"route"\s*:\s*"(/[^"]*)"', contract or ""):
            if ":" not in route and "*" not in route and route != "/" and route not in others:
                others.append(route)
    if is_later is not None:
        others = [r for r in others if not is_later(r)] + [r for r in others if is_later(r)]
    return (["/"] + own + others)[:8]

MAX_PARAM_ROUTES = 4
MAX_INSTANCE_ROWS = 8
INSTANCE_ARIA_CHARS = 6000
_ARIA_ROW = re.compile(r"^(\s*)- row\b")

def param_routes_from_contract(contract: str, members: list[str] | None) -> dict[str, list[str]]:
    from .contract import related

    want: list[str] = []
    every: list[str] = []
    try:
        for page in json.loads(contract).get("pages", []) or []:
            if not isinstance(page, dict):
                continue
            route = str(page.get("route") or "").strip()
            if not route.startswith("/"):
                continue
            if route not in every:
                every.append(route)
            if (":" in route and "*" not in route and members and related(page.get("requirements"), members)
                    and route not in want):
                want.append(route)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {"want": [], "all": []}
    return {"want": want[:MAX_PARAM_ROUTES], "all": every}

def condense_rows(aria: str, keep: int = MAX_INSTANCE_ROWS) -> str:
    lines = str(aria or "").splitlines()
    out: list[str] = []
    run_indent: int | None = None
    run_count = 0
    hidden = 0
    skip_deeper_than: int | None = None

    def flush() -> None:
        nonlocal hidden
        if hidden and run_indent is not None:
            out.append(" " * run_indent + f"- ({hidden} more row(s) of this grid/table not shown here)")
        hidden = 0

    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        if skip_deeper_than is not None and indent > skip_deeper_than:
            continue
        skip_deeper_than = None
        row = _ARIA_ROW.match(line)
        if run_indent is not None and (indent < run_indent or (indent == run_indent and not row)):
            flush()
            run_indent, run_count = None, 0
        if row:
            if run_indent != indent:
                flush()
                run_indent, run_count = indent, 0
            run_count += 1
            if run_count > keep:
                hidden += 1
                skip_deeper_than = indent
                continue
        out.append(line)
    flush()
    return "\n".join(out)

def instance_section(entry: dict[str, Any], errors: list[str] | None = None) -> str:
    aria = condense_rows(str(entry.get("aria") or ""))
    cut = len(aria) > INSTANCE_ARIA_CHARS or int(entry.get("ariaChars") or 0) > len(str(entry.get("aria") or ""))
    aria = aria[:INSTANCE_ARIA_CHARS]
    if cut:
        aria += "\n(... the snapshot is cut here at its size limit: what is not shown was not seen, not found missing)"
    text = str(entry.get("text") or "")
    if int(entry.get("textChars") or 0) > len(text):
        text += "\n(... the visible text is cut here at its size limit)"
    console = [str(x) for x in (errors or []) + list(entry.get("console") or []) if str(x).strip()]
    unique = list(dict.fromkeys(console))
    head = (f"### route {entry.get('route')} (a concrete page of the route {entry.get('template')}, reached by "
            f"{entry.get('via') or 'following the links'}; final url {entry.get('url', '')})")
    return (head + (f"\nconsole errors: {' | '.join(unique)}" if unique else "")
            + f"\nARIA snapshot:\n{aria}\n\nVisible text:\n{text}")

def _path_key(route: Any) -> str:
    path = str(route or "").split("#", 1)[0].split("?", 1)[0].strip()
    return path.rstrip("/").lower() or "/"

SPEC_REQUIREMENT_ID = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]+-\d+(?:[.\-]\d+)*)(?!\d)")

def authored_requirement_ids(project_dir: Path) -> set[str]:
    folder = project_dir / "backend" / "test-e2e"
    found: set[str] = set()
    for path in sorted(folder.glob("*.spec.*")) if folder.is_dir() else []:
        try:
            found.update(SPEC_REQUIREMENT_ID.findall(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return found

def _route_pattern(route: str) -> tuple[re.Pattern[str], int] | None:
    path = route.split("?", 1)[0].split("#", 1)[0].strip()
    if not path.startswith("/"):
        return None
    parts: list[str] = []
    fixed = 0
    for segment in [s for s in path.strip("/").split("/") if s]:
        if segment.startswith(":"):
            parts.append("[^/]+")
        elif segment == "*" or segment.startswith("*"):
            parts.append(".*")
        else:
            fixed += 1
            parts.append(re.escape(segment))
    return re.compile("^/" + "/".join(parts) + "/?$", re.IGNORECASE), fixed

def later_route_test(contract: str, members: list[str], authored: set[str]):
    from urllib.parse import unquote

    from .contract import related

    pages: list[tuple[re.Pattern[str], int, bool]] = []
    try:
        for page in json.loads(contract).get("pages", []) or []:
            if not isinstance(page, dict):
                continue
            compiled = _route_pattern(str(page.get("route") or ""))
            raw = page.get("requirements")
            requirements = [str(r).strip() for r in (raw if isinstance(raw, list) else [raw]) if str(r or "").strip()]
            if compiled is None:
                continue
            later = bool(requirements) and not related(requirements, list(members)) and not related(requirements, sorted(authored))
            pages.append((compiled[0], compiled[1], later))
    except (json.JSONDecodeError, AttributeError, TypeError):
        pages = []

    def is_later(path: str) -> bool:
        concrete = unquote(str(path).split("?", 1)[0].split("#", 1)[0].strip())
        matched = [(fixed, later) for pattern, fixed, later in pages if pattern.match(concrete)]
        if not matched:
            return False
        best = max(fixed for fixed, _ in matched)
        return all(later for fixed, later in matched if fixed == best)

    return is_later

def finding_routes(where: str) -> list[str]:
    text = str(where or "").strip()
    if text.lower().startswith("route "):
        text = text[6:]
    text = re.sub(r"\s*\(.*$", "", text, flags=re.DOTALL)
    parts = [part.strip() for part in text.split(",")]
    if not parts or any(not part.startswith("/") or part.startswith("//") for part in parts):
        return []
    return parts

def split_later_findings(findings: list[dict[str, str]], is_later) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    report: list[dict[str, str]] = []
    later: list[dict[str, str]] = []
    for finding in findings:
        routes = finding_routes(str(finding.get("where") or ""))
        (later if routes and all(is_later(route) for route in routes) else report).append(finding)
    return report, later

def _later_test_for(project_dir: Path, node: dict[str, Any], contract: str, members: list[str], log=print):
    try:
        return later_route_test(contract, members, authored_requirement_ids(project_dir))
    except Exception as exc:
        log(f"[audit] {node.get('id')}: later-batch routes not determined: {exc!r}")
        return lambda _path: False

def _log_later(node: dict[str, Any], later: list[dict[str, str]], log=print) -> None:
    if not later:
        return
    log(f"[audit] (diagnostic, not reported) {node.get('id')}: {len(later)} finding(s) on pages only later batches build "
        "(no spec of theirs on disk yet):")
    for finding in later[:12]:
        log(f"[audit]   (later) {str(finding.get('where', ''))[:60]}: {str(finding.get('problem', ''))[:140]}")

def scripted_layer(project_dir: Path, node: dict[str, Any], routes: list[str], log=print,
                   params: dict[str, list[str]] | None = None) -> tuple[list[dict[str, Any]] | None, list[dict[str, str]], bool]:
    snapshot = take_snapshot(project_dir, routes, log=log, params=params) if params else take_snapshot(project_dir, routes, log=log)
    checked = None if snapshot is None else [e for e in snapshot if not (isinstance(e, dict) and e.get("instance"))]
    deterministic: list[dict[str, str]] = []
    for entry in checked or []:
        for item in entry.get("invisible") or []:
            label = item.get("testid") or item.get("role") or "element"
            deterministic.append(
                {
                    "where": f"route {entry.get('route')}",
                    "requirement": "(rendered widget must be visible)",
                    "problem": f"`{label}` is rendered but has zero size, so users and visibility-based locators cannot see it: {item.get('html', '')}",
                    "fix": "Give it real dimensions in every state, including the empty/initial one (explicit height/width, visible background or text).",
                }
            )
    if deterministic:
        log(f"[audit] {node.get('id')}: {len(deterministic)} zero-size widget(s) found by the snapshot")
    stacked_rows = row_layout_findings(checked)
    if stacked_rows:
        log(f"[audit] {node.get('id')}: {len(stacked_rows)} page(s) with a row whose cells are stacked vertically")
    deterministic += stacked_rows
    crashed = False
    try:
        deterministic += deterministic_findings(project_dir, node, checked, log=log)
    except Exception as exc:
        crashed = True
        log(f"[audit] deterministic layer crashed: {exc!r}")
    for finding in deterministic:
        finding.setdefault("layer", "scripted")
    return snapshot, deterministic, crashed

def audit_node(llm: LLM, project_dir: Path, node: dict[str, Any], contract: str, log=print, use_llm: bool = True) -> list[dict[str, str]]:
    members = [str(m) for m in (node.get("members") or [node.get("id")])]
    is_later = _later_test_for(project_dir, node, contract, members, log=log)
    routes = routes_from_contract(contract, members, is_later=is_later)
    params = param_routes_from_contract(contract, members) if use_llm else None
    snapshot, deterministic, _crashed = scripted_layer(project_dir, node, routes, log=log, params=params)
    deterministic, later = split_later_findings(deterministic, is_later)
    if not use_llm:
        _log_later(node, later, log=log)
        log(f"[audit] {node.get('id')}: {len(deterministic)} finding(s) (scripted layer only)")
        for finding in deterministic[:12]:
            log(f"[audit]   - {str(finding.get('where', ''))[:40]}: {str(finding.get('problem', ''))[:160]}")
        return deterministic
    if snapshot is None:
        snapshot_text = "(no browser available in this environment; judge from the component sources)"
    else:
        parts = []

        instances = [e for e in snapshot if isinstance(e, dict) and e.get("instance")]
        shown = {_path_key(e.get("route")) for e in instances}
        carried: dict[str, list[str]] = {}
        for entry in snapshot:
            if isinstance(entry, dict) and entry.get("crawled") and _path_key(entry.get("route")) in shown:
                carried.setdefault(_path_key(entry.get("route")), []).extend(
                    [str(x) for x in entry.get("console") or []] + ([f"ERROR while loading: {entry['error']}"] if entry.get("error") else []))
        if params and params.get("want"):
            found = [str(e.get("template")) for e in instances]
            missing = [r for r in params["want"] if r not in found]
            log(f"[audit] {node.get('id')}: parameterised route(s) shown to the model auditor: "
                + (", ".join(f"{e.get('route')} ({e.get('template')})" for e in instances) or "none")
                + (f"; no concrete page found for {', '.join(missing)}" if missing else ""))
        for entry in snapshot:
            if entry.get("instance"):
                parts.append(instance_section(entry, carried.get(_path_key(entry.get("route")))))
            elif entry.get("crawled") and _path_key(entry.get("route")) in shown:
                continue
            elif entry.get("crawled"):

                errors = " | ".join(entry.get("console") or []) or "none recorded"
                load = f"\nERROR while loading: {entry['error']}" if entry.get("error") else ""
                parts.append(f"### route {entry['route']} (reached by following the pages' links; only its errors and layout "
                             f"checks were recorded, not its content)\nconsole errors: {errors}{load}")
            elif entry.get("error"):
                parts.append(f"### route {entry['route']}\nERROR while loading: {entry['error']}")
            else:
                parts.append(f"### route {entry['route']} (final url {entry.get('url', '')})\nARIA snapshot:\n{entry.get('aria', '')[:6000]}\n\nVisible text:\n{entry.get('text', '')}")
        snapshot_text = "\n\n".join(parts)
    user = (
        f"REQUIREMENT\n{describe_node(node)}\n\n"
        f"APPLICATION SNAPSHOT (signed-out visitor)\n{snapshot_text}\n\n"
        f"PAGE COMPONENT SOURCES\n{page_sources(project_dir)}"
    )
    try:
        message = llm.chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
            max_tokens=20000,
            tag=f"audit:{node.get('id')}",
            thinking=bool(SETTINGS.get("audit_thinking", True)),
        )
        raw = str(message.get("content") or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        findings = json.loads(raw).get("findings", [])
    except Exception as exc:
        log(f"[audit] {node.get('id')}: audit failed: {exc!r}")
        _log_later(node, later, log=log)
        return deterministic

    haystack = _normalize(describe_node(node))
    cleaned = []
    for finding in findings:
        if not isinstance(finding, dict) or not finding.get("problem"):
            continue
        quote = _normalize(str(finding.get("requirement") or ""))
        verdict = str(finding.get("problem") or "") + " " + str(finding.get("fix") or "")
        if re.search(r"符合要求|满足要求|无需修改|no change|compliant|satisfies the requirement", verdict, re.IGNORECASE) or str(finding.get("fix") or "").strip() in ("", "无", "none", "None", "N/A"):
            continue
        if len(quote) >= 4 and (quote in haystack or quote[:16] in haystack):
            finding["layer"] = "model"
            cleaned.append(finding)
        else:
            log(f"[audit]   dropped (quote not found in requirement): {str(finding.get('problem', ''))[:120]}")
    cleaned, later_model = split_later_findings(cleaned, is_later)
    _log_later(node, later + later_model, log=log)
    cleaned = deterministic + cleaned
    log(f"[audit] {node.get('id')}: {len(cleaned)} finding(s)")
    for finding in cleaned[:12]:
        log(f"[audit]   - {str(finding.get('where', ''))[:40]}: {str(finding.get('problem', ''))[:160]}")
    return cleaned

def _normalize(text: str) -> str:
    return re.sub(r"[\s`'\"“”‘’「」*_]+", "", text).lower()

MAX_REPAIR_FINDINGS = 6

_FILE_LINE = re.compile(r"(\.[A-Za-z]{1,5}):\d+(?::\d+)?\b")
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?)?"
                        r"|\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b|\b1\d{12}\b", re.IGNORECASE)

_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|milliseconds?)\b", re.IGNORECASE)

def finding_fingerprint(finding: dict[str, str]) -> str:
    if isinstance(finding, dict) and finding.get("audit_target"):
        return hashlib.sha1(("target:" + str(finding["audit_target"])).encode("utf-8")).hexdigest()[:16]
    return legacy_fingerprint(finding)

def legacy_fingerprint(finding: dict[str, str]) -> str:
    text = " ".join(f"{finding.get('where', '')}\n{finding.get('problem', '')}".split())
    text = _FILE_LINE.sub(r"\1:#", text)
    text = _TIMESTAMP.sub("#time", text)
    text = _DURATION.sub("#duration", text).lower()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

def select_for_repair(findings: list[dict[str, str]], sent: set[str] | list[str],
                      limit: int = MAX_REPAIR_FINDINGS) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    if setting_on("audit_rotate_unresolved", True):
        return _select_rotating(findings, sent, limit)
    return _select_v181(findings, sent, limit)

def _select_v181(findings: list[dict[str, str]], sent: set[str] | list[str],
                 limit: int) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    sent = set(sent)
    ordered = [f for f in findings if f.get("layer") != "model"] + [f for f in findings if f.get("layer") == "model"]
    now: list[dict[str, str]] = []
    repeated: list[dict[str, str]] = []
    later: list[dict[str, str]] = []
    taken: set[str] = set()
    for finding in ordered:
        fp = finding_fingerprint(finding)
        if fp in sent or (finding.get("audit_target") and legacy_fingerprint(finding) in sent):
            repeated.append(finding)
        elif fp in taken:
            continue
        elif finding.get("over_check_cap"):
            taken.add(fp)
            later.append(finding)
        elif len(now) < limit:
            taken.add(fp)
            now.append(finding)
        else:
            taken.add(fp)
            later.append(finding)
    return now, repeated, later

_TRIED: dict[str, int] = {}
CUT_LEDGER: dict[str, dict[str, Any]] = {}

def repair_ledger() -> dict[str, dict[str, Any]]:
    return {fp: dict(entry, sent=_TRIED.get(fp, 0)) for fp, entry in CUT_LEDGER.items()}

_SENT_SEEN: set[str] = set()

def reset_rotation() -> None:
    _TRIED.clear()
    CUT_LEDGER.clear()
    _SENT_SEEN.clear()

def rotation_rank(finding: dict[str, Any]) -> tuple[int, int]:
    fp = finding_fingerprint(finding)
    sent = fp in _SENT_SEEN or bool(finding.get("audit_target") and legacy_fingerprint(finding) in _SENT_SEEN)
    return (1 if sent else 0, _TRIED.get(fp, 0))

def book_cut(findings: list[dict[str, Any]], why: str) -> None:
    for finding in findings:
        if isinstance(finding, dict):
            _book_cut(finding_fingerprint(finding), finding, why)

def _book_cut(fp: str, finding: dict[str, Any], why: str) -> None:
    entry = CUT_LEDGER.setdefault(fp, {"where": str(finding.get("where") or ""), "problem": str(finding.get("problem") or ""),
                                       "check": str(finding.get("check") or ""), "times": 0})
    entry["times"] += 1
    entry["why"] = why

def _select_rotating(findings: list[dict[str, str]], sent: set[str] | list[str],
                     limit: int) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    sent = set(sent)
    _SENT_SEEN.update(sent)
    base_now, repeated, _base_later = _select_v181(findings, sent, limit)
    ordered = [f for f in findings if f.get("layer") != "model"] + [f for f in findings if f.get("layer") == "model"]
    candidates: list[tuple[str, dict[str, str]]] = []
    taken: set[str] = set()
    in_cap: dict[str, int] = {}
    capped: set[str] = set()
    counted: set[str] = set()
    for finding in ordered:
        fp = finding_fingerprint(finding)
        check = str(finding.get("check") or "")
        if fp not in counted:
            counted.add(fp)
            if finding.get("over_check_cap"):
                capped.add(check)
            else:
                in_cap[check] = in_cap.get(check, 0) + 1
        if fp in sent or (finding.get("audit_target") and legacy_fingerprint(finding) in sent):
            continue
        if fp in taken:
            continue
        taken.add(fp)
        candidates.append((fp, finding))
    base_ids = {id(f) for f in base_now}
    used: dict[str, int] = {}
    for finding in base_now:
        check = str(finding.get("check") or "")
        used[check] = used.get(check, 0) + 1
    rest = [(i, fp, f) for i, (fp, f) in enumerate(candidates) if id(f) not in base_ids]
    rest.sort(key=lambda c: (_TRIED.get(c[1], 0), c[0]))
    fill: list[dict[str, str]] = []
    why: dict[int, str] = {}
    for _, fp, finding in rest:
        check = str(finding.get("check") or "")
        if len(base_now) + len(fill) >= limit:
            why[id(finding)] = f"over the cap of {limit}"
        elif check in capped and used.get(check, 0) >= in_cap.get(check, 0):
            why[id(finding)] = "over its check's cap"
        elif finding.get("over_check_cap") and check not in capped:
            why[id(finding)] = "over its check's cap"
        else:
            used[check] = used.get(check, 0) + 1
            fill.append(finding)
    now = list(base_now) + fill
    chosen = {id(f) for f in now}
    later: list[dict[str, str]] = []
    for fp, finding in candidates:
        if id(finding) in chosen:
            _TRIED[fp] = _TRIED.get(fp, 0) + 1
        else:
            later.append(finding)
            _book_cut(fp, finding, why.get(id(finding), f"over the cap of {limit}"))
    return now, repeated, later

RECHECK_BLIND = re.compile(r"crashed|unavailable|did not start|probes error", re.IGNORECASE)

def finding_identities(finding: dict[str, Any]) -> set[str]:
    from .batch_ui_checks import finding_targets

    targets = finding_targets(finding)
    return set(targets) if targets else {finding_fingerprint(finding)}

def unrechecked_reason(finding: dict[str, Any], ledger: dict[str, Any]) -> str:
    from .batch_ui_checks import CHECK_SETTING

    check = str(finding.get("check") or "")
    if check in CHECK_SETTING:

        head = re.match(r"\w+ (.+):\d+$", str(finding.get("where") or ""))
        if head and head.group(1) in set(ledger.get("unreadable") or []):
            return f"{head.group(1)} not readable"
        status = (ledger.get("batch_ui") or {}).get(CHECK_SETTING[check], "not run")
        return "" if status == "ran" else f"{CHECK_SETTING[check]} {status}"
    incomplete = list(dict.fromkeys(ledger.get("incomplete") or []))
    return f"incomplete live check(s): {', '.join(incomplete)}" if incomplete else ""

def recheck_repaired(project_dir: Path, node: dict[str, Any], contract: str, repaired: list[dict[str, str]], log=print) -> dict[str, Any]:

    scripted = [f for f in repaired if f.get("layer") != "model" and not is_session_finding(f)]
    unchecked = [f for f in repaired if f.get("layer") == "model" or is_session_finding(f)]
    result: dict[str, Any] = {"ran": False, "gone": [], "still": [], "unchecked": unchecked}
    label = f"[audit] {node.get('id')}: re-check of the audited repair"
    if not scripted:
        log(f"{label}: no scripted finding to re-check ({len(unchecked)} of the model auditor's, not re-checked)")
        return result
    quiet: list[str] = []
    try:
        from .verify import build_frontend

        if not build_frontend(project_dir, log=quiet.append).ok:
            log(f"{label}: not possible, the frontend does not build ({len(scripted)} finding(s) not re-checked)")
            return result
        members = [str(m) for m in (node.get("members") or [node.get("id")])]
        is_later = _later_test_for(project_dir, node, contract, members, log=quiet.append)

        ledger: dict[str, Any] | None = {} if setting_on("audit_settle_rechecked") else None
        from .batch_ui_checks import RECHECK_LEDGER

        token = RECHECK_LEDGER.set(ledger) if ledger is not None else None
        try:
            snapshot, fresh, crashed = scripted_layer(project_dir, node, routes_from_contract(contract, members, is_later=is_later),
                                                      log=quiet.append)
        finally:
            if token is not None:
                RECHECK_LEDGER.reset(token)
    except Exception as exc:
        log(f"{label}: crashed: {exc!r}")
        return result
    blind = [line for line in quiet if RECHECK_BLIND.search(line)]
    if snapshot is None or crashed or blind:
        why = ("no snapshot (no browser, or the application did not start)" if snapshot is None
               else f"a scripted check did not run ({(blind or ['deterministic layer crashed'])[0][:160]})")
        log(f"{label}: not possible, {why} ({len(scripted)} finding(s) not re-checked)")
        return result
    result["ran"] = True
    not_rechecked: list[tuple[dict[str, str], str]] = []
    if ledger is not None:

        now_ids: set[str] = set()
        for f in fresh:
            now_ids |= finding_identities(f)
        for finding in scripted:
            why = unrechecked_reason(finding, ledger)
            if why:
                not_rechecked.append((finding, why))
            else:
                result["still" if finding_identities(finding) & now_ids else "gone"].append(finding)
        result["not_rechecked"] = [finding for finding, _ in not_rechecked]
    else:
        now = {finding_fingerprint(f) for f in fresh}
        for finding in scripted:
            result["still" if finding_fingerprint(finding) in now else "gone"].append(finding)
    log(f"{label} (scripted checks, once): {len(result['gone'])} of {len(scripted)} repaired finding(s) gone, "
        f"{len(result['still'])} still there" + (f"; {len(unchecked)} of the model auditor's not re-checked" if unchecked else ""))
    if not_rechecked:
        log(f"{label}: {len(not_rechecked)} repaired finding(s) not re-checked, their check did not run completely "
            f"({not_rechecked[0][1][:120]}): not settled")
    for finding in result["still"][:MAX_REPAIR_FINDINGS]:
        log(f"[audit]   still there: {str(finding.get('where', ''))[:60]}: {str(finding.get('problem', ''))[:140]}")
    return result

SESSION_SOURCE = "session_revoke"

def is_session_finding(finding: Any) -> bool:
    return isinstance(finding, dict) and (finding.get("source") == SESSION_SOURCE
                                          or str(finding.get("where") or "").startswith(SESSION_SOURCE + " probe"))

def recheck_session_findings(project_dir: Path, whole_requirement: str, member_ids: list[str],
                             repaired: list[dict[str, str]], log=print) -> dict[str, Any]:
    result: dict[str, Any] = {"ran": False, "gone": [], "still": [], "unchecked": list(repaired)}
    label = "[audit] re-check of the session-revoke finding(s)"
    lines: list[str] = []

    def tap(line: Any) -> None:
        lines.append(str(line))
        log(line)

    try:
        from . import session_chain

        fresh = session_chain.batch_findings(project_dir, whole_requirement, [str(m) for m in member_ids], log=tap)
        head = f"[{session_chain.REVOKE_ITEM}] after batch "
        verdict = next((line[len(head):].partition(": ")[2] for line in lines if line.startswith(head)), "")
    except Exception as exc:
        log(f"{label}: crashed: {exc!r} ({len(repaired)} finding(s) stay open)")
        return result
    now = {finding_fingerprint(f) for f in fresh or [] if isinstance(f, dict)}
    still = [f for f in repaired if finding_fingerprint(f) in now]
    rest = [f for f in repaired if finding_fingerprint(f) not in now]
    clean = verdict.startswith("clean") and "not run" not in verdict
    result.update(ran=bool(verdict) and not verdict.startswith("not run"), still=still,
                  gone=rest if clean else [], unchecked=[] if clean else rest)
    log(f"{label}: probe {verdict[:160] or 'gave no result'} -- {len(result['gone'])} gone, {len(still)} still there, "
        f"{len(result['unchecked'])} not re-checked (stay open)")
    return result

def findings_brief(findings: list[dict[str, str]]) -> str:
    lines = [
        "An independent audit compared the running application with the requirement text and found these violations.",
        "These are an auditor's CLAIMS, not verdicts. A claim whose requirement is a quote: check it against the requirement "
        "text first; fix it when the requirement supports it, skip it when the requirement does not.",

        "A claim whose requirement STARTS IN PARENTHESES comes from a scripted check that measured the running application "
        "against this application's own conventions (a page that throws or shows nothing, a raw timestamp, a page title set "
        "like body text, a row whose cells are stacked, a selected item that looks like the others): no requirement sentence "
        "is quoted for it, and that is no reason to skip it -- fix it, unless the requirement text states the opposite.",
        "If a supported fix contradicts a test, do not guess which side is wrong: output a TEST DEFECT block that quotes the "
        "requirement sentence.",
    ]
    for index, finding in enumerate(findings, start=1):
        lines.append(
            f"{index}. [{finding.get('where', '')}] requirement: \"{finding.get('requirement', '')}\" -> problem: {finding.get('problem', '')}"
            f" -> fix: {finding.get('fix', '')}"
        )
    return "\n".join(lines)
