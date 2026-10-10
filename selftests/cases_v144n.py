from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import interaction_probes as ip

CASES = []
QUIET = lambda m: None

def case(function):
    CASES.append(function)
    return function

def with_env(name: str, value: str | None):
    class _Env:
        def __enter__(self):
            self.old = os.environ.get(name)
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

        def __exit__(self, *exc):
            if self.old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = self.old
    return _Env()

ORG = ("- id: R1 name: Organization description: The organization overview page uses the organization name as a heading; "
       "its “Repositories”, “People”, and “Teams” navigation entries have link roles even when visually styled as tabs. "
       "The Repositories tab lists repositories owned by the organization, the People tab lists members. ")
ISSUES = ("- id: R2 name: Issues description: Users can filter by Open/Closed, title or body keywords, and labels. “Open” "
          "and “Closed” are links, not buttons or tabs. State and keyword filters combine, and refreshing retains the "
          "chosen filter context and matching results. ")
PR = ("- id: R3 name: PR description: Conversation, Commits, Files changed, and Checks are navigation links, even when "
      "visually styled as tabs. ")
TEXTBOX = ("- id: R4 name: Org repos description: The organization page presents “Repositories” as a navigation link, "
           "visually usable as a tab, and a textbox labeled “Find a repository” that filters the list. ")
CLONE = ("- id: R5 name: Clone description: Protocol controls are tabs “HTTPS” and “SSH”, and the selected clone value "
         "has a button “Copy clone value”. ")
SHEET = ("- id: R6 name: Sheets description: Worksheet tabs on the same editor page use the ARIA tab role, with the active "
         "tab indicated by aria-selected=\"true\"; the active worksheet grid has the accessible name \"Worksheet grid\". "
         "After refresh or reopening, the same rows remain visible. ")

@case
def p13_takes_named_navigation_entries_of_one_page():
    jobs = ip.build_jobs(ORG)["p13"]
    assert [j["names"] for j in jobs] == [["Repositories", "People", "Teams"]], jobs

@case
def p13_takes_filter_links_and_keeps_the_stated_refresh():
    jobs = ip.build_jobs(ISSUES)["p13"]
    assert [j["names"] for j in jobs] == [["Open", "Closed"]], jobs
    assert "refreshing retains" in jobs[0]["refresh"], jobs

@case
def p13_takes_an_unquoted_list_of_navigation_links():
    jobs = ip.build_jobs(PR)["p13"]
    assert [j["names"] for j in jobs] == [["Conversation", "Commits", "Files changed", "Checks"]], jobs

@case
def p13_never_takes_a_textbox_named_beside_a_tab():
    assert not any("Find a repository" in j["names"] for j in ip.build_jobs(TEXTBOX)["p13"])
    assert not any("Find a repository" in n for n in ip.build_jobs(TEXTBOX)["tabs"])

@case
def p13_needs_evidence_the_entries_are_views():

    assert ip.build_jobs(CLONE)["p13"] == []

    assert ip.build_jobs(SHEET)["p13"] == [] and ip.build_jobs(SHEET)["tabs"] == []

@case
def p13_and_the_tab_crawl_have_their_own_switches():
    with with_env("ARC_IPROBE_P13", "0"):
        jobs = ip.build_jobs(ORG)
        assert jobs["p13"] == [] and jobs["tabs"] == ["Repositories", "People", "Teams"], jobs
    with with_env("ARC_IPROBE_TABCRAWL", "0"):
        jobs = ip.build_jobs(ORG)
        assert jobs["tabs"] == [] and jobs["p13"], jobs
    with with_env("ARC_IPROBE_P2", "0"):
        assert ip.build_jobs(ORG + PR)["tabs"][:3] == ["Repositories", "People", "Teams"]

@case
def p13_triggers_the_run_and_its_spec_carries_the_entries():
    with with_env("ARC_IPROBE_P2", "0"):
        jobs = ip.build_jobs(PR)
        assert ip.triggered(jobs), jobs
        spec = ip.spec_of(jobs, None)
        assert spec["p13"] and spec["tabs"] == ["Conversation", "Commits", "Files changed", "Checks"], spec
    assert ip.PROBE_KINDS[-2:] == ("p11", "p12") and ip.VIEW_KINDS[-1] == "p13"

    assert not ip._complete({"p13": [{"id": "p13-0", "budget": True}]})

DEFAULT = {"url": "/r/pull/4", "active": [], "headings": ["Title", "Conversation"], "length": 364}

def p13_res(**reload):
    view = {"name": "Commits", "changed": True, "url": "/r/pull/4", "active": [], "headings": ["Title", "Commit summary"],
            "length": 404, "ownText": ["0e44da7"]}
    view["reload"] = dict({"sameAsDefault": True, "sameAsView": False, "url": "/r/pull/4", "active": [],
                           "headings": ["Title", "Conversation"], "length": 364}, **reload)
    return {"id": "p13-0", "route": "/r/pull/4", "defaultView": dict(DEFAULT),
            "tried": [{"name": "Conversation", "changed": False, "url": "/r/pull/4", "active": [], "headings": [], "length": 364}, view]}

def p13_run(res):
    jobs = ip.build_jobs(PR)["p13"]
    return ip.p13_findings(jobs, [res], log=QUIET)

@case
def p13_reports_a_reload_that_shows_the_default_view_again():
    found = p13_run(p13_res())
    assert len(found) == 1, found
    f = found[0]
    assert f["where"] == "route /r/pull/4" and "“Commits”" in f["problem"] and "exactly the text shown on load" in f["problem"], f
    assert "convention" in f["requirement"] and "addressable" in f["fix"] and "keep the entries' roles" in f["fix"], f

@case
def p13_is_silent_unless_the_reload_is_exactly_the_default():
    assert p13_run(p13_res(sameAsDefault=False, sameAsView=True)) == []
    assert p13_run(p13_res(sameAsDefault=False, sameAsView=False)) == []
    assert p13_run(p13_res(active=["Commits"])) == []
    unchanged = p13_res()
    unchanged["tried"][1]["changed"] = False
    assert p13_run(unchanged) == []
    assert p13_run(dict(p13_res(), error="boom")) == []
    no_default = p13_res()
    no_default["defaultView"] = {}
    assert p13_run(no_default) == []

@case
def p13_names_a_changed_address_that_the_reload_ignores():
    res = p13_res(url="/r/pull/4?tab=commits")
    res["tried"][1]["url"] = "/r/pull/4?tab=commits"
    found = p13_run(res)
    assert found and "The address changed to /r/pull/4?tab=commits, but reloading it" in found[0]["problem"], found

@case
def goto_strips_the_view_marker_and_activates_the_entry_again():
    script = ip.PROBE_SCRIPT
    assert "const TAB_MARK = '#arc-tab=';" in script
    body = script[script.index("async function goto(page, path)"):script.index("async function waitUntil")]
    assert "path = String(path).slice(0, at);" in body and "if (entry) await activateEntry(page, entry);" in body, body
    assert body.index("slice(0, at)") < body.index("page.goto("), body

@case
def the_crawl_activates_stated_entries_once_per_page_shape_inside_its_budget():
    script = ip.PROBE_SCRIPT
    tv = script[script.index("async function tabViews"):script.index("async function crawl(")]
    assert "if (Date.now() - t0 > budget || state.spent > cap) break;" in tv and "entries.slice(0, 4)" in tv, tv
    assert "const cap = Math.min(8000, budget / 4);" in tv and "finally { state.spent += Date.now() - s0; }" in tv

    assert "!(n.role === 'link' && hrefOf(n) && !hrefOf(n).startsWith('#'))" in tv
    assert "if (state.strips.has(strip)) return res;" in tv
    crawl0 = script[script.index("async function crawl("):script.index("async function signIn")]
    assert "while (queue.length && loaded < maxPages" in crawl0
    assert "if (stated.length < 2) return res;" in tv
    assert "u.pathname !== own.pathname" in tv and "res.links.push" in tv
    assert "TAB_MARK + encodeURIComponent(baseName(n.name))" in tv and "v.tab = baseName(n.name);" in tv
    assert "baseName(x.name) === nm" in tv
    crawl = script[script.index("async function crawl("):script.index("async function signIn")]
    assert "!tabbed.has(sh)" in crawl and "if (tv.tried) tabbed.add(sh);" in crawl, crawl
    assert "for (const v of tabbedViews) pages.push(v);" in crawl

@case
def views_behind_a_tab_serve_p1_p3_p11_p12_and_not_the_page_judging_probes():
    script = ip.PROBE_SCRIPT
    main = script[script.index("(async () => {\n  const browser"):]
    assert "owner.paths = owner.pages.filter((p) => !p.tab).map((p) => p.path);" in main
    assert "const asLoaded = fn !== p1 && fn !== p11;" in main
    assert "p6(visitor, job, vbase)" in main and "p7(browser, job, vbase, baseOf(owner))" in main
    assert "p3b(browser, job, baseOf(owner))" in main and "p9(visitor, job, vbase, '')" in main
    assert "p3(browser, job, vpages, owner)" in main
    assert "const more = vbase.map" in main
    assert "const vbase = vpages.filter((p) => !p.tab);" in main

    assert main.index("out.p13.push(await reach(p13, job))") > main.index("out.p12.push(")
    assert "if (Date.now() - t0 > Number(spec.lateMs || 170000)) { out.p13.push({ id: job.id, skip: 'late in the run' }); continue; }" in main
    assert ip.spec_of({}, None)["lateMs"] == (ip.TIMEOUT_S - 70) * 1000
    p13 = script[script.index("async function p13("):script.index("async function guard")]
    assert "!p.tab && score(p) >= 2" in p13 and "res.tried.length >= 2" in p13 and "page.reload(" in p13, p13

@case
def findings_name_a_view_behind_a_tab_in_words():
    jobs = {"p13": ip.build_jobs(PR)["p13"]}
    res = dict(p13_res(), route="/r/pull/4#arc-tab=Files%20changed")
    found = ip.findings_from(jobs, {"p13": [res]}, log=QUIET)
    assert found and found[0]["where"] == "route /r/pull/4 (after activating its “Files changed” entry)", found
    assert "#arc-tab=" not in " ".join(found[0].values()), found
    text = ip._tab_route_text("route /orgs/acme#arc-tab=People%20and%20teams, signed out")
    assert text == "route /orgs/acme (after activating its “People and teams” entry), signed out", text

APP_HTML = """<!doctype html><html><head><title>t</title></head><body>
<header><a href="/signin">Sign in</a> <a href="/page">Page</a> <a href="/addr">Addr</a></header>
<main><h1>Project</h1><div role="tablist" id="strip"></div><section id="view"></section></main>
<script>
const ADDR = location.pathname === '/addr';
const NAMES = ADDR ? ['Summary', 'Members'] : ['Overview', 'People'];
const VIEWS = {
  Overview: '<h2>Overview</h2><p>The overview of the project.</p>',
  People: '<h2>People</h2><p>alice bob carol</p><button>Add member</button>',
  Summary: '<h2>Summary</h2><p>The summary of the project.</p>',
  Members: '<h2>Members</h2><p>dave erin frank</p>'};
function show(name, keep) {
  // /page's People entry carries a count ("People (3)"): matched by its stated name
  // /page's strip also holds a button that opens another page (a link for the crawl, never a view) and a link with an
  // address of its own (never clicked by the tab crawl)
  document.getElementById('strip').innerHTML = NAMES.map((n) => '<button role="tab" data-n="' + n + '" aria-selected="' + (n === name) + '">' + n + (n === 'People' ? ' (3)' : '') + '</button>').join('')
    + (ADDR ? '' : '<button data-go="/docs">Docs</button> <a href="/files">Files</a>');
  document.getElementById('view').innerHTML = VIEWS[name];
  document.querySelectorAll('[role=tab]').forEach((b) => b.addEventListener('click', () => show(b.dataset.n, true)));
  if (ADDR && keep) history.replaceState(null, '', '/addr?tab=' + name);
}
document.addEventListener('click', (e) => {
  if (e.target.dataset && e.target.dataset.go) location.href = e.target.dataset.go;
  if (e.target.textContent === 'Add member') document.getElementById('view').insertAdjacentHTML('beforeend', '<div role="dialog" aria-label="Add member"><input aria-label="Username"></div>');
});
const asked = new URLSearchParams(location.search).get('tab');
show(ADDR && NAMES.includes(asked) ? asked : NAMES[0], false);
</script></body></html>"""

def _serve():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            index = '<a href="/signin">Sign in</a> <a href="/page">Page</a> <a href="/addr">Addr</a><main><h1>Home</h1></main>'
            other = f"<main><h1>{path.strip('/').title()} page</h1></main>"
            body = (APP_HTML if path in ("/page", "/addr") else index if path == "/" else other).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

@case
def browser_p13_reports_a_lost_view_and_the_tab_crawl_reaches_add_member():
    backend = os.environ.get("IPROBE_PLAYWRIGHT_BACKEND", "")
    if not backend or not (Path(backend) / "node_modules" / "@playwright" / "test").is_dir():
        print("     (skipped: set IPROBE_PLAYWRIGHT_BACKEND to a backend with node_modules/@playwright/test)")
        return
    jobs = {k: [] for k in ip.VIEW_KINDS}
    jobs["p13"] = [{"id": "p13-0", "names": ["Overview", "People"], "sentence": "Overview and People are tabs.", "refresh": ""},
                   {"id": "p13-1", "names": ["Summary", "Members"], "sentence": "Summary and Members are tabs.", "refresh": ""}]
    jobs["p3"] = [{"id": "p3-0", "controls": [{"role": "button", "name": "Add member"}], "restricted": []}]

    jobs["p1"] = [{"id": "p1-0", "opener": "^Add member$", "target": {"role": "dialog", "name": ""}, "escape": False,
                   "outside": False, "enter": False, "sentence": "“Add member” opens a dialog."}]
    jobs["tabs"] = ["Overview", "People", "Summary", "Members", "Docs", "Files"]
    jobs["owner"] = None
    server = _serve()
    try:
        with with_env("ARC_IPROBE_CRAWL_MS", "15000"):
            spec = ip.spec_of(jobs, None)
        result = ip.run_probe(Path(backend), f"http://127.0.0.1:{server.server_address[1]}", spec, log=print)
    finally:
        server.shutdown()
    assert result, result
    visited = result["crawl"]["visitor"]
    assert "/page#arc-tab=People" in visited, visited
    assert any(v.get("route") == "/page#arc-tab=People" for v in result["p3"][0]["visitor"]), result["p3"]
    p1 = result["p1"][0]
    assert p1.get("route") == "/page#arc-tab=People" and p1.get("opened") and not p1.get("skip"), p1
    tabs = result["crawl"]["tabs"]
    assert not any("#arc-tab=Docs" in v for v in visited) and "/docs" in visited, (visited, tabs)
    assert not any(t.get("entry") == "Files" for t in tabs), tabs
    found = ip.p13_findings(jobs["p13"], result["p13"], log=print)
    assert [f["where"] for f in found] == ["route /page"], (found, result["p13"])
    assert "“People”" in found[0]["problem"], found

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok   {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
