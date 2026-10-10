from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import interaction_probes as ip

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

ACCOUNT = (" - id: REQ-9 name: Accounts description: x scenarios: - keyword: WHEN content: 'The user signs in as `alice-dev` "
           "with `Valid-password-123!`.'")
BRANCH = ("- id: REQ-4-3-1 name: Branches description: The selector is a unique button named “Branch <current branch name>”; "
          "it opens a textbox named “Find branch”. Escape closes the selector.")

@case
def p1_escape_of_another_object_is_not_bound_to_the_only_opener():
    text = ('- id: R description: The button named “Edit” opens a dialog named “Editor”. In the search menu, pressing '
            'Escape closes the search menu.')
    p1 = ip.build_jobs(text)["p1"]
    assert len(p1) == 1 and p1[0]["escape"] is False, p1
    pronoun = '- id: R description: The button named “Edit” opens a dialog named “Editor”. Escape closes it.'
    assert ip.build_jobs(pronoun)["p1"][0]["escape"] is False
    named = '- id: R description: The button named “Edit” opens a dialog named “Editor”. Escape closes the dialog.'
    assert ip.build_jobs(named)["p1"][0]["escape"] is True
    assert ip.build_jobs(BRANCH)["p1"][0]["escape"] is True

@case
def p1_a_same_named_control_elsewhere_is_not_the_popover_open_on_load():
    job = dict(ip.build_jobs(BRANCH)["p1"][0])
    res = {"id": job["id"], "route": "/r", "openerName": "Branch main", "onLoad": True}
    assert len(ip.p1_findings([job], [res], log=QUIET)) == 1
    assert ip.p1_findings([job], [dict(res, twin=True)], log=QUIET) == []
    script = ip.PROBE_SCRIPT
    assert "twin = await waitUntil(async () => (await count()) > before, 1500);" in script
    assert "res.onLoad = before > 0 && !twin;" in script and "const floor = tgt.name && !twin ? 0 : before;" in script

ENTER = ("- id: R description: The button named “Rename” opens a dialog named “Rename item” with a “Save” button. Pressing "
         "Enter in the dialog's text box saves it.")

@case
def p1_enter_is_credited_when_it_sent_a_write():
    job = dict(ip.build_jobs(ENTER)["p1"][0])
    base = {"id": job["id"], "route": "/w/1", "onLoad": False, "opened": True, "enterClosed": False, "buttonClosed": True}
    assert len(ip.p1_findings([job], [dict(base, enterWrites=0)], log=QUIET)) == 1
    assert ip.p1_findings([job], [dict(base, enterWrites=1)], log=QUIET) == []
    assert ip.p1_findings([job], [base], log=QUIET) == []
    script = ip.PROBE_SCRIPT
    assert "await settle(page, 1200);" in script and "res.enterWrites = writes.length;" in script
    assert "res.sameValue" in script
    fresh = script.index("if (!res.enterClosed && !writes.length && job.submit")
    assert script.index("await goto(page, hit.path);", fresh) < script.index("res.sameValue", fresh)

TASKS = ("- id: R description: Open displays open tasks; Closed displays closed tasks; All displays every task. Open, "
         "Closed, and All are navigation links, even when visually styled as tabs.")
PR_TABS = ("- id: REQ-6-3 name: PR detail description: On the PR detail page, Conversation displays the title; Commits "
           "displays commits on the compare branch; Files changed displays per-file differences; Checks displays check "
           "status. Conversation, Commits, Files changed, and Checks are navigation links, even when visually styled as tabs.")

def _views(names, text, headings, anchors, sig=None):
    return [{"name": n, "hash": "#" + n.lower(), "samePage": True, "anchor": a, "text": text, "length": len(text),
             "sig": sig(n) if sig else f"{len(text)}:1", "headings": headings} for n, a in zip(names, anchors)]

@case
def p2_equal_empty_views_are_not_a_finding():
    job = ip.build_jobs(TASKS)["p2"][0]
    res = {"id": job["id"], "route": "/tasks", "views": _views(["Open", "Closed", "All"], "No tasks", ["Tasks"], [False] * 3)}
    assert ip.p2_findings([job], [res], log=QUIET) == []
    res = {"id": job["id"], "route": "/tasks", "views": _views(["Open", "Closed", "All"], "No tasks", [], [False] * 3)}
    assert ip.p2_findings([job], [res], log=QUIET) == []

@case
def p2_another_entrys_heading_on_every_view_is_a_finding():
    job = ip.build_jobs(PR_TABS)["p2"][0]
    names = ["Conversation", "Commits", "Files changed", "Checks"]
    text = "Improve onboarding #1 Conversation Improves the onboarding."
    heads = ["Improve onboarding", "Conversation"]
    fake = {"id": job["id"], "route": "/alice-dev/acme-docs/pull/1", "views": _views(names, text, heads, [True, False, False, False])}
    found = ip.p2_findings([job], [fake], log=QUIET)
    assert len(found) == 1 and "its heading “Conversation” is shown" in found[0]["problem"], found
    own = [dict(v, headings=heads + [v["name"]]) for v in fake["views"]]
    assert ip.p2_findings([job], [dict(fake, views=own)], log=QUIET) == []

    later = dict(fake, views=_views(names, text, heads, [True, False, False, False], sig=lambda n: "9999:" + n))
    assert ip.p2_findings([job], [later], log=QUIET) == []
    assert "sig: all.length + ':' + hash" in ip.PROBE_SCRIPT and "headings" in ip.PROBE_SCRIPT

@case
def the_crawl_also_opens_record_title_buttons():
    script = ip.PROBE_SCRIPT
    assert "rec.links = rec.links.concat(await titleButtonLinks(page, rec));" in script
    fn = script[script.index("async function titleButtonLinks"): script.index("async function crawl(")]
    assert "a.role === 'heading'" in fn and "RISKY.test(x.name)" in fn and "a.role === 'navigation'" in fn
    assert fn.count(".click(") == 1

@case
def p2_fix_keeps_the_stated_roles():
    names = ["Conversation", "Commits", "Files changed", "Checks"]
    text = "Improve onboarding Conversation Improves the onboarding."
    heads = ["Conversation"]
    for requirement in (PR_TABS, PR_TABS.replace("are navigation links, even when visually styled as tabs", "are tabs")):
        job = ip.build_jobs(requirement)["p2"][0]
        res = {"id": job["id"], "route": "/p/1", "views": _views(names, text, heads, [True, False, False, False])}
        fix = ip.p2_findings([job], [res], log=QUIET)[0]["fix"]
        assert "keep the entries' roles and names as they are" in fix and "as links" not in fix, fix

@case
def p3_a_neighbour_control_needs_the_acts_verb_and_no_open_rule():
    text = ('- id: R description: Only Admin may delete members. Everyone may search members using the button named '
            '“Search members”.')
    assert ip.build_jobs(text)["p3"] == []
    other_verb = '- id: R description: Only Admin may delete members. Members are found with the button named “Search members”.'
    assert ip.build_jobs(other_verb)["p3"] == []
    same_verb = '- id: R description: Only Admin may delete members. Everyone may use the button named “Delete member”.'
    assert ip.build_jobs(same_verb)["p3"] == []
    kept = '- id: R description: Only Admin may delete members. The button named “Delete member” is on each row.'
    assert [c["name"] for c in ip.build_jobs(kept)["p3"][0]["controls"]] == ["Delete member"]
    members = ("- id: REQ-2-2-2 name: Team members description: Only an organization Owner may maintain members or hierarchy "
               "on an organization team page. On Members, the button “Add member” opens a textbox labeled “Username” and "
               "the submit button “Add member”.")
    p3 = ip.build_jobs(members)["p3"]
    assert [[(c["role"], c["name"]) for c in s["controls"]] for s in p3] == [[("button", "Add member")]], p3

@case
def p3_words_and_stop_words_share_one_normalisation():
    assert ip._stem("policies") == ip._stem("policy") == "policy"
    assert ip._stem("status") == "status" and ip._stem("branches") == "branch" and ip._stem("members") == "member"
    assert ip._stem("access") == "access"
    text = '- id: R description: Only Admin may delete policies. The button named “Policy” is on the page.'
    assert [c["name"] for c in ip.build_jobs(text)["p3"][0]["controls"]] == ["Policy"]

    assert ip._ACT_STOP == {ip._stem(w) for w in ip.ACT_STOP} and "page" in ip._ACT_STOP

    text = '- id: R description: Only Admin may maintain members. The button named “Add member” is on the page.'
    assert ip.build_jobs(text)["p3"]
    saved = ip._ACT_STOP
    try:
        ip._ACT_STOP = saved | {"member"}
        assert ip.build_jobs(text)["p3"] == []
    finally:
        ip._ACT_STOP = saved

PRIVATE = ("- id: REQ-3-1 name: Search description: The seed organization has a public repository and a distinct private "
           "repository inaccessible to visitors. scenarios: - keyword: GIVEN content: 'The seeded data is public repository "
           "`acme-docs`, private repository `secret-research`, owner `alice-dev`.' - keyword: WHEN content: 'The user signs "
           "in as `alice-dev` with `Valid-password-123!` and follows the visible controls.'")

def _p3b(sub):
    return {"id": "p3b-0", "root": "/alice-dev/secret-research", "ownerRoot": {"denied": False, "hasName": True},
            "visitorRoot": {"denied": True, "text": "Access denied"}, "subs": [sub]}

@case
def p3b_an_unrecognised_refusal_is_no_leak():
    job = ip.build_jobs(PRIVATE)["p3b"]
    sub = {"path": "/alice-dev/secret-research/wiki", "ownerOk": True, "visitorDenied": False, "visitorHasName": True,
           "visitorText": "secret-research Members only Request access", "ownLines": 6, "shared": 0, "sharedSample": []}
    assert ip.p3b_findings(job, [_p3b(sub)], log=QUIET) == []
    assert ip.p3b_findings(job, [_p3b(dict(sub, shared=1))], log=QUIET) == []
    assert ip.p3b_findings(job, [_p3b(dict(sub, shared=2, ownLines=7))], log=QUIET) == []
    assert len(ip.p3b_findings(job, [_p3b(dict(sub, shared=2, ownLines=6))], log=QUIET)) == 1
    unmeasured = dict(sub)
    for key in ("ownLines", "shared", "sharedSample"):
        unmeasured.pop(key)
    assert ip.p3b_findings(job, [_p3b(unmeasured)], log=QUIET) == []
    leak = dict(sub, path="/alice-dev/secret-research/settings", shared=4, ownLines=8,
                sharedSample=["General", "Branches", "Manage access", "Danger Zone"])
    found = ip.p3b_findings(job, [_p3b(leak)], log=QUIET)
    assert len(found) == 1 and "“Manage access”" in found[0]["problem"] and "4 of the 8 lines" in found[0]["problem"], found
    assert "const shared = own.filter((l) => visitorLines[sub.path].has(l));" in ip.PROBE_SCRIPT

SHEET = ("- id: REQ-1-1-1 name: View description: | Each record displays \"Last updated: <last updated value>\" and provides "
         "a link. - id: REQ-3-1-1 name: Edit description: 'After selecting a cell users can modify its content directly in "
         "the grid or formula bar. Pressing Enter or clicking another cell commits the change; pressing Escape cancels an "
         "uncommitted change. The text box labeled \"Formula bar\" shows the input.'")

@case
def p4_relative_times_compare_by_age():
    job = ip.build_jobs(SHEET)["p4"]
    base = {"id": job[0]["id"], "route": "/w/1", "cell": "B2", "typed": True, "persisted": True}
    assert ip.p4_findings(job, [dict(base, v0="1 second ago", v1="1 second ago", v2="2 seconds ago")], log=QUIET) == []
    assert ip.p4_findings(job, [dict(base, v0="just now", v1="just now", v2="a minute ago")], log=QUIET) == []
    assert len(ip.p4_findings(job, [dict(base, v0="3 days ago", v1="3 days ago", v2="1 second ago")], log=QUIET)) == 1
    assert len(ip.p4_findings(job, [dict(base, v0="3 days ago", v1="3 days ago", v2="just now")], log=QUIET)) == 1
    assert ip.p4_findings(job, [dict(base, v0="yesterday", v1="yesterday", v2="today")], log=QUIET) == []
    iso = dict(base, v0="2026-09-29T11:58:48.137Z", v1="2026-09-29T11:58:48.137Z", v2="2026-09-29T11:58:52.269Z")
    assert len(ip.p4_findings(job, [iso], log=QUIET)) == 1
    assert ip.relative_age("5 minutes ago") == 300 and ip.relative_age("2026-09-29") is None

@case
def p5_takes_the_escape_statement_commit_probe_binds():
    other = ("- id: R description: Users can modify the content in the grid or formula bar. Clicking another cell commits "
             "the change. Cells save immediately. In the search dialog, pressing Escape cancels the search.")
    p5 = ip.build_jobs(other)["p5"]
    assert len(p5) == 1 and p5[0]["escape"] is False and p5[0]["escape_sentence"] == "", p5
    p5 = ip.build_jobs(SHEET)["p5"]
    assert p5[0]["escape"] is True and "Escape" in p5[0]["escape_sentence"], p5
    assert "ESCAPE.search(body)" not in Path(ip.__file__).read_text(encoding="utf-8")

ORG = ("- id: REQ-2-1 name: Org description: The Repositories tab lists repositories owned by the team. The user clicks a "
       "repository name in the list to open its overview.")

def _p6(item):
    job = ip.build_jobs(ORG)["p6"]
    return ip.p6_findings(job, [{"id": job[0]["id"], "items": [item]}], log=QUIET)

@case
def p6_the_same_description_is_no_identity():
    item = {"route": "/teams/red", "name": "starter", "path": "/red/starter", "facts": ["Shared starter project"],
            "texts": ["Shared starter project"], "target": {"denied": False, "first": "red/starter Files", "facts": 0},
            "partners": [{"path": "/blue/starter", "denied": False, "hasName": True, "facts": 1}]}
    assert _p6(item) == []
    assert _p6(dict(item, texts=["Shared starter project", "blue"])) and "names “blue”" in _p6(dict(item, texts=["blue"]))[0]["problem"]
    assert _p6(dict(item, texts=["blue", "red"])) == []
    denied = dict(item, target={"denied": True, "first": "Access denied", "facts": 0})
    assert _p6(denied) == []
    found = _p6(dict(denied, texts=["Public", "Shared starter project"]))
    assert len(found) == 1 and "marks it public" in found[0]["problem"], found

def _count_probes(result: dict | None) -> int:
    calls = []
    original = ip.probe
    ip.probe = lambda project, spec, log=print: (calls.append(1), result)[1]
    tmp = Path(tempfile.mkdtemp(prefix="r46p-"))
    try:
        (tmp / "frontend" / "src").mkdir(parents=True)
        (tmp / "frontend" / "src" / "App.tsx").write_text("a", encoding="utf-8")
        for _ in range(3):
            ip.interaction_findings(BRANCH, tmp, [], log=QUIET)
    finally:
        ip.probe = original
        shutil.rmtree(tmp, ignore_errors=True)
    return len(calls)

@case
def errors_and_cut_short_results_are_not_cached():

    assert ip.INCOMPLETE_TRIES == 2
    assert _count_probes({"error": "Target page, context or browser has been closed", "p1": []}) == 2
    assert _count_probes({"p1": [{"id": "p1-0", "error": "TimeoutError"}]}) == 2
    assert _count_probes({"p1": [], "p2": [{"id": "p2-0", "budget": True, "views": []}]}) == 2
    assert _count_probes(None) == 2
    assert _count_probes({"p1": [{"id": "p1-0", "route": "/r", "onLoad": False, "opened": True}]}) == 1

@case
def the_fingerprint_hashes_contents_and_start_inputs():
    tmp = Path(tempfile.mkdtemp(prefix="r46p-fp-"))
    try:
        app = tmp / "frontend" / "src" / "App.tsx"
        app.parent.mkdir(parents=True)
        (tmp / "backend").mkdir()
        app.write_text("aaaa", encoding="utf-8")
        before = ip.source_fingerprint(tmp)
        stat = app.stat()
        app.write_text("bbbb", encoding="utf-8")
        os.utime(app, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        second = ip.source_fingerprint(tmp)
        assert second != before
        (tmp / "backend" / "package.json").write_text('{"scripts": {"start": "node src/server.js"}}', encoding="utf-8")
        third = ip.source_fingerprint(tmp)
        assert third != second
        (tmp / "frontend" / "vite.config.ts").write_text("export default {}", encoding="utf-8")
        fourth = ip.source_fingerprint(tmp)
        assert fourth != third
        (tmp / "backend" / "server.log").write_text("listening", encoding="utf-8")
        assert ip.source_fingerprint(tmp) == fourth
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@case
def p1_p4_p5_alone_get_the_stated_owner():
    assert ip.build_jobs(BRANCH + ACCOUNT)["owner"] == {"user": "alice-dev", "password": "Valid-password-123!"}
    only45 = ip.build_jobs(SHEET + ACCOUNT)
    assert only45["p4"] and only45["p5"] and not only45["p1"] and only45["owner"]["user"] == "alice-dev"
    assert ip.build_jobs(BRANCH)["owner"] is None

PAGES = {
    "/": '<main><h1>Home</h1><p><a href="/twin">Twin</a> <a href="/branch">Branch</a> <a href="/slow">Slow</a> '
         '<a href="/broken">Broken</a> <a href="/pr">PR</a> <a href="/tasks">Tasks</a> <a href="/long">Long</a> '
         '<a href="/books">Books</a></p></main>',

    "/books": '<main><h1>Books</h1><ul><li><h3><button id="b5">Book 5</button></h3><p>A novel</p></li></ul></main><script>'
              'document.getElementById("b5").onclick = () => { history.pushState({}, "", "/books/5"); '
              'document.querySelector("main").innerHTML = "<h1>Book 5</h1><p>A novel</p>"; };</script>',
    "/books/5": '<main><h1>Book 5</h1><p>A novel</p></main>',

    "/twin": '<header><input aria-label="Search" placeholder="Search site"></header><main><h1>Tasks</h1>'
             '<button id="f">Filter</button><div id="pop"></div></main><script>'
             'const pop = document.getElementById("pop");'
             'document.getElementById("f").onclick = () => { pop.innerHTML = pop.innerHTML ? "" : '
             '\'<div role="group" aria-label="Filter options"><input aria-label="Search" placeholder="Search tasks"></div>\'; };'
             'document.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.innerHTML = ""; });</script>',

    "/branch": '<main><h1>Code</h1><button id="b">Branch main</button><div id="panel"><input aria-label="Find branch"></div>'
               '</main><script>document.getElementById("b").onclick = () => { const p = document.getElementById("panel"); '
               'p.hidden = !p.hidden; };</script>',

    "/slow": '<main><h1>Items</h1><p id="name">Item 1</p><button id="r">Rename</button><div id="host"></div></main><script>'
             'let busy = false; const host = document.getElementById("host");'
             'document.getElementById("r").onclick = () => { host.innerHTML = \'<div role="dialog" aria-label="Rename item">'
             '<form id="fm"><input aria-label="Item name" value="Item 1"><button type="submit">Save</button></form></div>\';'
             'document.getElementById("fm").onsubmit = async (e) => { e.preventDefault(); if (busy) return; busy = true;'
             'const v = e.target.querySelector("input").value; await fetch("/api/slow", { method: "POST", body: v });'
             'busy = false; document.getElementById("name").textContent = v; host.innerHTML = ""; }; };</script>',

    "/broken": '<main><h1>Notes</h1><button id="r">Rename note</button><div id="host"></div></main><script>'
               'const host = document.getElementById("host");'
               'document.getElementById("r").onclick = () => { host.innerHTML = \'<div role="dialog" aria-label="Rename note dialog">'
               '<input aria-label="Note name" value="Note 1"><button id="s">Save</button></div>\';'
               'document.getElementById("s").onclick = async () => { await fetch("/api/fast", { method: "POST", body: "x" });'
               'host.innerHTML = ""; }; };</script>',

    "/pr": '<main><h1>Improve onboarding</h1><nav aria-label="Pull request views"><a href="#conversation">Conversation</a> '
           '<a href="#commits">Commits</a> <a href="#checks">Checks</a></nav><section id="conversation"><h2>Conversation</h2>'
           '<p>Improves the onboarding and the search flow.</p></section></main>',

    "/tasks": '<main><h1>Task list</h1><nav><a href="#open">Open</a> <a href="#closed">Closed</a> <a href="#all">All</a></nav>'
              '<div id="view"><p>No tasks</p></div></main><script>addEventListener("hashchange", () => { '
              'document.getElementById("view").innerHTML = "<p>No tasks</p>"; });</script>',

    "/long": '<main><h1>Report</h1><nav><a href="#overview">Overview</a> <a href="#history">History</a> <a href="#notes">Notes</a>'
             '</nav><section><h2>Overview</h2><p>' + ("Lorem ipsum dolor sit amet. " * 130) + '</p><p id="tail">overview</p>'
             '</section></main><script>addEventListener("hashchange", () => { document.getElementById("tail").textContent = '
             'location.hash.slice(1) + " rows"; });</script>',
}
REQUIREMENT = (
    "- id: A description: On the tasks page the button named “Filter” opens a textbox named “Search”. Escape closes the filter. "
    "- id: B description: The selector is a unique button named “Branch <current branch name>”; it opens a textbox named "
    "“Find branch”. Escape closes the selector. "
    "- id: C description: The button named “Rename” opens a dialog named “Rename item” with a “Save” button. Pressing Enter "
    "in the dialog's text box saves it. "
    "- id: D description: The button named “Rename note” opens a dialog named “Rename note dialog” with a “Save” button. "
    "Pressing Enter in the dialog's text box saves it. "
    "- id: E description: On the PR page, Conversation displays the title; Commits displays commits; Checks displays checks. "
    "Conversation, Commits, and Checks are navigation links, even when visually styled as tabs. "
    "- id: F description: Open displays open tasks; Closed displays closed tasks; All displays all tasks. Open, Closed, and "
    "All are navigation links, even when visually styled as tabs. "
    "- id: G description: Overview displays the summary; History displays the history; Notes displays the notes. Overview, "
    "History, and Notes are navigation links, even when visually styled as tabs.")

class _Site(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0].split("#")[0])
        page = ("<!doctype html><html><head><meta charset='utf-8'></head><body>" + body + "</body></html>") if body else ""
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(1.6 if self.path == "/api/slow" else 0.2)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

def browser_run(backend: Path) -> tuple[dict, dict, list]:
    jobs = ip.build_jobs(REQUIREMENT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        spec = dict(ip.spec_of(jobs, None), p9=[])
        result = ip.run_probe(backend, f"http://127.0.0.1:{server.server_address[1]}", spec, log=QUIET)
    finally:
        server.shutdown()
        server.server_close()

    found = []
    for kind in ("p1", "p2"):
        for job, res in zip(jobs[kind], (result or {}).get(kind) or []):
            found += ip.findings_from({kind: [job]}, {kind: [res]}, log=QUIET)
    return jobs, result or {}, found

def browser_main() -> int:
    candidates = [os.environ.get("ARC_PLAYWRIGHT_BACKEND") or "", str(ROOT / "template" / "backend")]
    backend = next((Path(c) for c in candidates if c and (Path(c) / "node_modules" / "@playwright" / "test").is_dir()), None)
    if not backend:
        print("skipped  browser checks: no node_modules/@playwright/test (set ARC_PLAYWRIGHT_BACKEND)")
        return 0
    jobs, result, found = browser_run(backend)
    by = {}
    for kind in ("p1", "p2"):
        for job, res in zip(jobs[kind], result.get(kind) or []):
            by[job.get("opener_name") or job["names"][0]] = res
    where = {f["where"] for f in found}
    checks = [
        ("#1 a header “Search” beside the popover's own is not open on load", by.get("Filter", {}).get("twin") is True
         and "route /twin" not in where),
        ("#1 control: the branch selector open on load and Escape ignored is reported", "route /branch" in where),
        ("#2 Enter with a 1.6 s request is not credited to the button", by.get("Rename", {}).get("enterClosed") is True
         and "route /slow" not in where),
        ("#2 control: Enter that does nothing while the button submits is reported", "route /broken" in where),
        ("#3 control: fake tabs keeping “Conversation” are reported", "route /pr" in where),
        ("#3 empty views all saying “No tasks” are not reported", "route /tasks" not in where),
        ("#3 views differing after 3000 characters are not reported", "route /long" not in where),
        ("C8 the crawl reaches a record page opened by a title button", "/books/5" in ((result.get("crawl") or {}).get("visitor") or [])),
    ]
    failed = 0
    for name, ok in checks:
        print(("ok    browser " if ok else "FAIL  browser ") + name)
        failed += 0 if ok else 1
    if failed:
        import json
        print("RESULT", json.dumps({k: result.get(k) for k in ("p1", "p2", "crawl", "error")}, ensure_ascii=False)[:3000])
        print("FOUND", [(f["where"], f["problem"][:120]) for f in found])
    return failed

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} r46p cases passed")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
