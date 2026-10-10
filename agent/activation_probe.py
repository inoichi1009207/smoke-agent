from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

_Q = r"[`\"“「]([^`\"”」\n]{1,60})[`\"”」]"
CONTAINER_ROLES = {"article": "article", "row": "row", "list item": "listitem", "listitem": "listitem"}
ACTIVATE = re.compile(
    r"\b(?:activat(?:e|es|ing)|click(?:s|ing)?|tap(?:s|ping)?|press(?:es|ing)?|select(?:s|ing)?)\s+(?:on\s+)?"
    r"(?:the|a|an|any|each)\s+(?P<thing>(?:[a-z][\w-]*\s+){0,2}?)(?P<container>article|row|list\s*item)\b"
    r"(?P<rest>[^.;\n]{0,160})", re.IGNORECASE)
OPENS = re.compile(r"^\s*(?:itself\s+)?opens\s+(?:exactly\s+one\s+|a\s+|the\s+)?(?:(?:dialog|modal)\s+(?:named|called|labell?ed|titled)\s+"
                   + _Q + r"|" + _Q + r"\s+(?:dialog|modal)\b)", re.IGNORECASE)
WAIT_MS = 5000

def activation_statements(whole: str) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    text = " ".join(str(whole or "").split())
    for match in ACTIVATE.finditer(text):
        opens = OPENS.match(match.group("rest"))
        if not opens:
            continue
        start = max(text.rfind(". ", 0, match.start()), text.rfind("; ", 0, match.start()), -1) + 1
        sentence = text[start: match.end()].strip()
        role = CONTAINER_ROLES[" ".join(match.group("container").lower().split())]
        dialog = (opens.group(1) or opens.group(2) or "").strip()
        found.setdefault((role, dialog.casefold()), {"role": role, "thing": match.group("thing").strip(),
                                                     "dialog": dialog, "sentence": sentence[:240]})
    return list(found.values())

def _route_names_thing(route: str, thing: str) -> bool:
    word = (thing.split() or [""])[-1].casefold()
    if len(word) < 3:
        return False
    segments = [s.casefold() for s in str(route).split("?")[0].split("/") if s]
    return any(s in (word, word + "s", word + "es") for s in segments)

def probe_routes(statement: dict[str, str], snapshot: list[dict[str, Any]] | None) -> list[str]:
    from .entry_checks import aria_nodes, reached_pages

    thing = str(statement.get("thing") or "")
    pages = sorted(reached_pages(snapshot), key=lambda p: (0 if _route_names_thing(str(p["route"]), thing) else 1,
                                                           0 if str(p["route"]).rstrip("/") == "" else 1))
    for entry in pages:
        if any(role == statement["role"] and name.strip() for _i, role, name in aria_nodes(entry["aria"])):
            return [str(entry["route"])]
    return []

PROBE_SCRIPT = r"""
const { chromium } = require('@playwright/test');
const WAIT = Number(process.env.ARC_PROBE_WAIT || 5000);
const INTERACTIVE = 'button,a[href],input,select,textarea,label,summary,[role=button],[role=link],[role=checkbox],' +
  '[role=switch],[role=menuitem],[role=textbox],[role=combobox],[contenteditable=true]';
const dialogs = (page) => page.evaluate(() => Array.from(document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog]'))
  .filter((el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('[aria-hidden=true]'); })
  .map((el) => {
    // the dialog's name as a findings check compares it with the stated one: aria-labelledby, aria-label, its heading
    const by = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => id && document.getElementById(id))
      .filter(Boolean).map((n) => n.innerText || n.textContent || '').join(' ').trim();
    return (by || el.getAttribute('aria-label') || (el.querySelector('h1,h2,h3,[role=heading]') || {}).innerText || '').trim().slice(0, 60);
  }));
async function opened(page, before, url) {
  const start = Date.now();
  while (Date.now() - start < WAIT) {
    const now = await dialogs(page);
    if (now.length > before.length) return { dialog: now.find((n) => !before.includes(n)) || now[now.length - 1] };
    if (page.url() !== url) return { navigated: page.url() };
    await page.waitForTimeout(100);
  }
  return null;
}
(async () => {
  const [base, spec] = process.argv.slice(2);
  const jobs = JSON.parse(spec);
  const out = [];
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  for (const job of jobs) {
    const result = { route: job.route, role: job.role, skipped: [] };
    const page = await context.newPage();
    try {
      await page.goto(base + job.route, { waitUntil: 'networkidle', timeout: 15000 });
      const items = page.getByRole(job.role);
      const count = Math.min(await items.count(), 12);
      for (let i = 0; i < count && !result.item; i++) {
        const item = items.nth(i);
        if (!(await item.isVisible())) continue;
        const where = await item.evaluate((el, interactive) => {
          el.scrollIntoView({ block: 'center', inline: 'center' });
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          const control = hit && hit.closest(interactive);
          const heading = el.querySelector('h1,h2,h3,h4,h5,h6,[role=heading]');
          return { inside: !!hit && el.contains(hit), control: control && el.contains(control)
            ? (control.getAttribute('aria-label') || control.innerText || control.tagName).trim().slice(0, 40) : '',
            title: ((heading && heading.innerText) || el.getAttribute('aria-label') || '').trim().slice(0, 60),
            titleControl: !!(heading && heading.querySelector('button,a[href],[role=button],[role=link]')) };
        }, INTERACTIVE);
        if (!where.inside || where.control) { result.skipped.push(where.title + ' (middle on ' + (where.control || 'another element') + ')'); continue; }
        result.item = where.title;
        let before = await dialogs(page);
        let url = page.url();
        await item.click({ timeout: 5000 });
        result.container = await opened(page, before, url);
        if (result.container || !where.titleControl) break;
        await page.goto(base + job.route, { waitUntil: 'networkidle', timeout: 15000 });
        const control = page.getByRole(job.role).nth(i).locator('h1,h2,h3,h4,h5,h6,[role=heading]').first()
          .locator('button,a[href],[role=button],[role=link]').first();
        before = await dialogs(page);
        url = page.url();
        await control.click({ timeout: 5000 });
        result.title = await opened(page, before, url);
      }
    } catch (error) {
      result.error = String(error).slice(0, 200);
    }
    out.push(result);
    await page.close();
  }
  await browser.close();
  console.log('ARC_ACTIVATION_JSON' + JSON.stringify(out));
})().catch((error) => { console.log('ARC_ACTIVATION_ERROR' + String(error).slice(0, 300)); process.exit(2); });
"""

def run_probe(backend: Path, base: str, jobs: list[dict[str, str]], log=print) -> list[dict[str, Any]] | None:
    from .fsutil import run_cmd

    tmp = Path(tempfile.mkdtemp(prefix="arc-activation-"))
    try:
        script = tmp / "probe.cjs"
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
        code, output = run_cmd(["node", str(script), base, json.dumps(jobs)], cwd=backend, timeout=120,
                               env={"NODE_PATH": str(backend / "node_modules")})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    match = re.search(r"ARC_ACTIVATION_JSON(.*)", output)
    if not match:
        log(f"[audit] activation probe unavailable ({output.strip()[-200:]!r})")
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

def probe(project_dir: Path, jobs: list[dict[str, str]], log=print) -> list[dict[str, Any]] | None:
    backend = project_dir / "backend"
    if not jobs or not (backend / "node_modules" / "@playwright" / "test").is_dir():
        return None
    from .fsutil import BackgroundProcess, free_port, fresh_data_env, npm_bin, wait_http

    port = free_port(3400)
    server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **fresh_data_env(project_dir)})
    try:
        ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if not ready:
            log("[audit] activation probe: application did not start")
            return None
        return run_probe(backend, f"http://127.0.0.1:{port}", jobs, log=log)
    finally:
        server.stop()

def _norm(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()

def activation_findings(statements: list[dict[str, str]], results: list[dict[str, Any]] | None,
                        log=print) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for result in results or []:
        same_role = [s for s in statements if s["role"] == str(result.get("role"))]
        if not same_role:
            continue
        name = str(result.get("item") or "")
        title = result.get("title") if isinstance(result.get("title"), dict) else {}

        statement = next((s for s in same_role if _norm(s["dialog"]) == _norm(title.get("dialog"))), None)

        if result.get("error") or not name or result.get("container") or "dialog" not in title or statement is None:
            log(f"[audit] (diagnostic, not reported) activation probe {result.get('route')} {same_role[0]['role']}: "
                f"{json.dumps({k: result.get(k) for k in ('item', 'container', 'title', 'skipped', 'error')})[:300]}"
                + ("" if statement or "dialog" not in title else
                   f" -- the title opened no stated dialog ({', '.join(repr(s['dialog']) for s in same_role)})"))
            continue
        thing = (statement["thing"] + " " if statement["thing"] else "") + ("list item" if statement["role"] == "listitem" else statement["role"])
        findings.append({
            "where": f"route {result.get('route')}",
            "requirement": f"(the requirement: activating the {thing} opens the dialog `{statement['dialog']}`) "
                           f"{statement['sentence'][:200]}",
            "problem": f"Clicking the {thing} “{name}” itself (its middle, where no control is) opens nothing: no dialog "
                       f"appeared within {WAIT_MS // 1000} s and the page stayed. Only the title control inside its heading "
                       f"opens it. Whoever activates the {thing}, as the requirement says, never gets the dialog.",
            "fix": f"Make the {thing} element itself open it: `onClick={{(e) => {{ if ((e.target as HTMLElement).closest("
                   "'button,a,input,select,textarea,label,[role=menu],[role=menuitem],[role=dialog]')) return; open(); }}}}` "
                   "on the container (plus cursor: pointer). Keep the title control as it is; the guard keeps the other "
                   "controls inside it (and menus or dialogs rendered from it) from also opening the editor.",
        })
    return findings

def enabled() -> bool:
    return os.environ.get("ARC_ACTIVATION_PROBE", "1").strip() not in ("0", "false", "off")
