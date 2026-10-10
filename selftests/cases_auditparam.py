from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES: list = []
QUIET = lambda message: None
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

SHEET = json.dumps({"pages": [
    {"route": "/", "requirements": ["REQ-1-1", "REQ-1-1-1"]},
    {"route": "/workbooks/new", "requirements": ["REQ-1-2-1"]},
    {"route": "/workbooks/:id", "requirements": ["REQ-1", "REQ-2", "REQ-3", "REQ-4", "REQ-5"]},
]})
GITHUB = json.dumps({"pages": [
    {"route": "/", "requirements": ["REQ-1"]},
    {"route": "/orgs/:org", "requirements": ["REQ-2-1-1"]},
    {"route": "/orgs/:org/people", "requirements": ["REQ-2-2-3"]},
    {"route": "/:owner/:repo", "requirements": ["REQ-3-1"]},
    {"route": "/:owner/:repo/issues/:number", "requirements": ["REQ-2-1"]},
    {"route": "/files/*", "requirements": ["REQ-2-1-1"]},
    {"route": "/explore", "requirements": ["REQ-2-1-1"]},
]})

@case
def the_batch_wants_its_own_parameterised_routes_only():
    from agent.audit import MAX_PARAM_ROUTES, param_routes_from_contract
    sheet = param_routes_from_contract(SHEET, ["REQ-3-1-1"])
    assert sheet == {"want": ["/workbooks/:id"], "all": ["/", "/workbooks/new", "/workbooks/:id"]}, sheet
    github = param_routes_from_contract(GITHUB, ["REQ-2-1-1"])

    assert github["want"] == ["/orgs/:org", "/:owner/:repo/issues/:number"], github
    assert "/files/*" in github["all"] and "/explore" in github["all"] and len(github["all"]) == 7
    assert param_routes_from_contract(SHEET, []) ["want"] == []
    assert param_routes_from_contract("not json {", ["REQ-1"]) == {"want": [], "all": []}
    many = json.dumps({"pages": [{"route": f"/t{i}/:id", "requirements": ["REQ-1"]} for i in range(9)]})
    assert len(param_routes_from_contract(many, ["REQ-1-1"])["want"]) == MAX_PARAM_ROUTES == 4

def _script_block() -> str:
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    return script[script.index("  const T0 = Date.now();"):script.index("  const browser = await chromium.launch();")]

def _node(program: str) -> list:
    node = shutil.which("node")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.cjs"
        path.write_text(program, encoding="utf-8")
        done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout.strip().splitlines()[-1])

@case
def the_script_records_one_instance_per_wanted_route_most_specific_route_wins():
    if not shutil.which("node"):
        return
    params = {"want": ["/workbooks/:id", "/orgs/:org"],
              "all": ["/", "/workbooks/new", "/workbooks/:id", "/orgs/:org", "/files/*", "/:a/x", "/y/:b", "/y/x/:c"]}
    program = NL.join([
        "process.env.ARC_SNAPSHOT_PARAMS = " + json.dumps(json.dumps(params)) + ";",
        "const page = (url, aria, text) => ({ url: () => url, locator: () => ({",
        "  ariaSnapshot: async () => aria, innerText: async () => text }) });",
        "(async () => {",
        _script_block(),
        "  const r = [];",
        "  r.push([instanceOf('/workbooks/new'), instanceOf('/workbooks/1'), instanceOf('/workbooks/1/x'), instanceOf('/files/a/b'),",
        "          instanceOf('/y/x'), instanceOf('/Orgs/acme%20demo?tab=1'), instanceOf('/')]);",
        "  await takeInstance(page('http://h/workbooks/5', '- main', '  '), 'links', []);",
        "  r.push(instances.length);",
        "  await takeInstance(page('http://h/workbooks/new', '- main', 'New'), 'links', []);",
        "  r.push(instances.length);",
        "  const errors = ['uncaught: boom'];",
        "  await takeInstance(page('http://h/workbooks/5?tab=2', '- heading \"Q3\" [level=1]', 'Q3 Sales'), 'following', errors);",
        "  errors.push('later error');",
        "  await takeInstance(page('http://h/workbooks/6', '- heading \"Other\"', 'Other'), 'following', []);",
        "  r.push(instances.map((i) => [i.route, i.instance, i.template, i.via, i.aria, i.text, i.ariaChars, i.textChars, i.console]));",
        "  r.push([instanceOf('/workbooks/7'), pending()]);",
        "  const big = 'x'.repeat(70000);",
        "  await takeInstance(page('http://h/orgs/acme', big, big), 'clicking', []);",
        "  r.push([instances[1].aria.length, instances[1].ariaChars, instances[1].text.length, instances[1].textChars, pending()]);",
        "  console.log(JSON.stringify(r));",
        "})();",
    ])
    got = _node(program)
    assert got[0] == [None, "/workbooks/:id", None, None, None, "/orgs/:org", None], got[0]
    assert got[1] == 0 and got[2] == 0, got
    assert got[3] == [["/workbooks/5?tab=2", True, "/workbooks/:id", "following", '- heading "Q3" [level=1]', "Q3 Sales", 24, 8,
                       ["uncaught: boom", "later error"]]], got[3]
    assert got[4] == [None, ["/orgs/:org"]], got[4]
    assert got[5] == [60000, 70000, 3000, 70000, []], got[5]

@case
def the_script_wires_the_instances_in_and_bounds_them():
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    block = _script_block()
    assert chr(92) not in block, "no backslash in the instance block (non-raw Python string)"
    assert "ARC_SNAPSHOT_PARAMS" in block and ".slice(0, 4)" in block
    crawl = script[script.index("while (queue.length && crawled < 30)"):script.index("if (!reachedGrid)")]
    assert "await takeInstance(page, \"following the pages' links\", entry.console);" in crawl
    click = script[script.index("if (!reachedGrid)"):script.index("const late = () =>")]
    assert "await takeInstance(page, 'clicking “' + via + '” on /', entry.console);" in click
    assert click.index("if (page.url() !== before) {") < click.index("await takeInstance(")
    tail = script[script.index("const late = () =>"):script.index("await browser.close();")]
    assert "const clickFrom = routes.filter((r) => !(r === '/' && !reachedGrid)).slice(0, 3);" in tail
    assert tail.count("late()") >= 3 and "tried.size >= 4" in tail and "LATE_MS = 300000" in script
    assert "if (page.url() !== start) await takeInstance(" in tail and "risky.test(name)" in tail
    assert tail.rstrip().endswith("for (const i of instances) out.push(i);"), tail[-200:]

    for kept, count in (("entry.relabeled = ", 2), ("entry.gridLayout = await GRID_TWICE(page)", 3),
                        ("entry.rowLayout = await ROW_TWICE(page);", 3), ("entry.visual = await VISUAL_TWICE(page);", 3)):
        assert script.count(kept) == count, kept
    node = shutil.which("node")
    if node:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.cjs"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=60)
            assert done.returncode == 0, done.stderr[-500:]

INSTANCE = {"route": "/workbooks/1", "instance": True, "template": "/workbooks/:id", "via": "following the pages' links",
            "url": "http://h/workbooks/1", "console": ["uncaught: TypeError: x"],
            "aria": NL.join(['- heading "Q3 Sales" [level=1]', '- tablist "Worksheets":', '  - tab "Sheet1" [selected]']),
            "ariaChars": 60, "text": "Q3 Sales" + NL + "Sheet1", "textChars": 15,

            "invisible": [{"testid": "grid", "role": "grid", "html": "<div>"}],
            "rowLayout": {"rowsChecked": 3, "stackedRows": 1, "found": [{"container": "g", "cells": 3, "row": "A | B"}]}}

@case
def no_scripted_check_sees_an_instance_entry():
    from agent import audit
    seen: list = []
    saved = (audit.take_snapshot, audit.deterministic_findings)
    try:
        audit.take_snapshot = lambda *a, **k: [{"route": "/", "aria": "- heading"}, dict(INSTANCE)]
        audit.deterministic_findings = lambda project_dir, node, snapshot, log=None: seen.append(snapshot) or []
        snapshot, found, crashed = audit.scripted_layer(Path("."), {"id": "REQ-3-1-1"}, ["/"], log=QUIET,
                                                        params={"want": ["/workbooks/:id"], "all": []})
    finally:
        audit.take_snapshot, audit.deterministic_findings = saved
    assert found == [] and crashed is False, found
    assert [e["route"] for e in seen[0]] == ["/"], seen
    assert any(e.get("instance") for e in snapshot), snapshot

class FakeLLM:
    def __init__(self, findings=()):
        self.findings = list(findings)
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return {"content": json.dumps({"findings": self.findings})}

def run_audit(snapshot, use_llm=True, contract=SHEET, llm=None):
    from agent import audit
    calls: list = []
    logs: list[str] = []
    saved = (audit.take_snapshot, audit.deterministic_findings)
    try:
        audit.take_snapshot = lambda *a, **k: calls.append(k.get("params")) or [dict(e) for e in snapshot]
        audit.deterministic_findings = lambda *a, **k: []
        with tempfile.TemporaryDirectory() as tmp:
            node = {"id": "REQ-3-1-1", "members": ["REQ-3-1-1"], "name": "Edit a cell",
                    "description": "Selecting a cell and typing stores the value; the sheet tabs stay below the grid."}
            out = audit.audit_node(llm or FakeLLM(), Path(tmp), node, contract, log=logs.append, use_llm=use_llm)
    finally:
        audit.take_snapshot, audit.deterministic_findings = saved
    return out, logs, calls

@case
def the_model_auditor_sees_the_concrete_page_instead_of_errors_only():
    llm = FakeLLM()
    snapshot = [{"route": "/", "url": "http://h/", "aria": '- link "Q3 Sales"', "text": "Q3 Sales"},
                {"route": "/workbooks/1", "crawled": True, "console": ["uncaught: TypeError: x"], "relabeled": [{"tag": "div"}]},
                {"route": "/workbooks/2", "crawled": True, "console": []},
                dict(INSTANCE)]
    _, logs, calls = run_audit(snapshot, llm=llm)
    assert calls == [{"want": ["/workbooks/:id"], "all": ["/", "/workbooks/new", "/workbooks/:id"]}], calls
    prompt = llm.messages[1]["content"]
    section = prompt[prompt.index("### route /workbooks/1 "):]
    assert section.startswith("### route /workbooks/1 (a concrete page of the route /workbooks/:id, reached by following "
                              "the pages' links; final url http://h/workbooks/1)"), section[:200]
    assert '- tab "Sheet1" [selected]' in section and "Visible text:" + NL + "Q3 Sales" in section, section
    assert section.count("uncaught: TypeError: x") == 1, section
    assert prompt.count("### route /workbooks/1") == 1, prompt

    assert "### route /workbooks/2 (reached by following the pages' links; only its errors and layout checks were recorded" in prompt
    assert any("parameterised route(s) shown to the model auditor: /workbooks/1 (/workbooks/:id)" in line for line in logs), logs

@case
def a_wanted_route_without_a_page_is_logged_and_the_scripted_layer_asks_for_none():
    llm = FakeLLM()
    snapshot = [{"route": "/", "url": "http://h/", "aria": "- heading", "text": "Home"},
                {"route": "/workbooks/1", "crawled": True, "console": []}]
    _, logs, _ = run_audit(snapshot, llm=llm)
    assert any("shown to the model auditor: none; no concrete page found for /workbooks/:id" in line for line in logs), logs
    assert "console errors: none recorded" in llm.messages[1]["content"]
    _, _, calls = run_audit(snapshot, use_llm=False)
    assert calls == [None], calls

@case
def a_long_grid_keeps_its_first_rows_and_counts_the_rest():
    from agent.audit import MAX_INSTANCE_ROWS, condense_rows
    rows = []
    for number in range(1, 31):
        rows += [f'    - row "{number}":', f'      - rowheader "{number}"', f'      - gridcell "A{number}": v{number}']
    aria = NL.join(['- grid "Worksheet grid":', '  - rowgroup:', '    - row "A B":', '      - columnheader "A"',
                    '  - rowgroup:', *rows, '- tablist "Worksheets":', '  - tab "Sheet1" [selected]'])
    out = condense_rows(aria)
    assert MAX_INSTANCE_ROWS == 8
    assert '- row "8":' in out and '- row "9":' not in out and 'gridcell "A9"' not in out, out
    assert '    - (22 more row(s) of this grid/table not shown here)' in out.splitlines(), out
    assert out.splitlines()[-2:] == ['- tablist "Worksheets":', '  - tab "Sheet1" [selected]'], out
    assert '- row "A B":' in out and 'columnheader "A"' in out
    assert condense_rows('- row "x"' + NL + '- row "y"') == '- row "x"' + NL + '- row "y"'

@case
def a_cut_snapshot_says_it_was_cut():
    from agent.audit import INSTANCE_ARIA_CHARS, instance_section
    small = instance_section(dict(INSTANCE))
    assert "cut here" not in small and small.count("console errors: uncaught: TypeError: x") == 1, small
    big = dict(INSTANCE, aria="- button " + "x" * (INSTANCE_ARIA_CHARS + 50), ariaChars=INSTANCE_ARIA_CHARS + 60)
    text = instance_section(big)
    assert "the snapshot is cut here at its size limit: what is not shown was not seen, not found missing" in text
    assert len(text) < INSTANCE_ARIA_CHARS + 600
    lost = dict(INSTANCE, ariaChars=90000, textChars=9000)
    lost_text = instance_section(lost)
    assert "the snapshot is cut here" in lost_text and "the visible text is cut here" in lost_text

@case
def take_snapshot_passes_the_wanted_routes_to_the_script():
    from agent import audit
    seen: list = []

    class Server:
        def __init__(self, *a, **k):
            pass

        def stop(self):
            pass

    saved = (audit.BackgroundProcess, audit.wait_http, audit.run_cmd, audit.free_port)
    try:
        audit.BackgroundProcess = Server
        audit.wait_http = lambda *a, **k: (True, 200)
        audit.free_port = lambda *a, **k: 4999
        audit.run_cmd = lambda args, **k: seen.append((args, k.get("env"))) or (0, 'ARC_SNAPSHOT_JSON[{"route": "/"}]')
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
            params = {"want": ["/workbooks/:id"], "all": ["/", "/workbooks/:id"]}
            assert audit.take_snapshot(Path(tmp), ["/"], log=QUIET, params=params) == [{"route": "/"}]
            audit.take_snapshot(Path(tmp), ["/"], log=QUIET, params={"want": [], "all": ["/"]})
            audit.take_snapshot(Path(tmp), ["/"], log=QUIET)
    finally:
        audit.BackgroundProcess, audit.wait_http, audit.run_cmd, audit.free_port = saved
    assert json.loads(seen[0][1]["ARC_SNAPSHOT_PARAMS"]) == params, seen[0]
    assert seen[1][1] is None and seen[2][1] is None, seen
    assert seen[0][0][-1] == "/" and all(a[0] == "node" for a, _ in seen)

@case
def the_repair_brief_does_not_skip_scripted_findings_for_want_of_a_quote():
    from agent.audit import findings_brief
    brief = findings_brief([{"where": "route /", "requirement": "(a page title looks like a title)", "problem": "h1 like body",
                             "fix": "style it"}])
    flat = " ".join(brief.split())
    assert "A claim whose requirement STARTS IN PARENTHESES comes from a scripted check" in flat, flat
    assert "that is no reason to skip it -- fix it, unless the requirement text states the opposite" in flat, flat
    assert "A claim whose requirement is a quote: check it against the requirement text first" in flat
    assert "skip it when the requirement does not" in flat and "TEST DEFECT block" in flat
    assert "Skip a claim the requirement does not support." not in flat
    assert 'requirement: "(a page title looks like a title)"' in brief

@case
def the_model_auditor_prompt_follows_the_open_kind_convention():
    from agent.audit import SYSTEM_PROMPT
    from agent.conventions import __file__ as conventions_file
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "neither kind is a finding" not in flat
    assert ("leave the kind open; this application decides it in this order: (1) the kind another sentence of the "
            "requirement names for the same control") in flat, flat
    assert "A kind that follows this order is never a finding" in flat

    source = " ".join(Path(conventions_file).read_text(encoding="utf-8").split())
    assert 'When the requirement leaves the kind open ("a link or button named X", "the X entry"), the kind is decided in this order' in source

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
