from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb
import cases_v145a as v145a
import cases_v157a as a

NL = chr(10)
CASES = []
NAME = "REQ-1-1-1.spec.js"
TWO_RECORDS = ("Seed data:" + NL + "- Workbook `Alpha`: rows `North/800`" + NL + "- Workbook `Beta`: rows `East/1200`")
HEAD = ('const { test } = require("@playwright/test");' + NL
        + 'const { click, notSee, region, item } = require("../e2e-support/outsider");' + NL + NL)

def case(function):
    CASES.append(function)
    return function

def spec(body: str, title: str = rb.S1, head: str = HEAD) -> str:
    return head + f'test("{title}", async ({{ page }}) => {{' + NL + body + NL + "});" + NL

def opening(record: str) -> str:
    return ('  await page.goto("/");' + NL + f'  await click(page, {{ role: "link", name: "{record}" }});' + NL
            + '  const grid = region(page, "grid", "Worksheet grid");' + NL)

def bindings(text: str, seeds: str = a.SEEDS) -> set:
    from agent.outsider import seed_conflict_bindings
    return seed_conflict_bindings(text, dict(a.NODE, seed_sentences=seeds))

@case
def the_value_must_belong_to_the_record_the_test_opened():
    from agent.outsider import seed_absence_assertions
    alpha = spec(opening("Alpha") + '  await notSee(page, "East", grid);')
    beta = spec(opening("Beta") + '  await notSee(page, "East", grid);')
    node = dict(a.NODE, seed_sentences=TWO_RECORDS)
    assert seed_absence_assertions(alpha, node), "precondition: the global check flags the Alpha case"
    assert bindings(alpha, TWO_RECORDS) == set(), "East is Beta's value, the test opened Alpha"
    assert bindings(beta, TWO_RECORDS) == {(rb.S1, "East")}
    assert bindings(a.F1_TEXT) == {(rb.S1, "East"), (rb.S2, "East")}

@case
def a_pair_seed_absence_assertions_does_not_flag_is_never_bound():

    filtered = opening("Q3 Sales") + '  await fill(page, "Filter", "North");' + NL + '  await notSee(page, "East", grid);'
    text = spec(filtered) + NL + spec(opening("Q3 Sales") + '  await notSee(page, "East", grid);', title=rb.S2, head="")
    assert bindings(text) == {(rb.S2, "East")}, bindings(text)

@case
def a_line_naming_two_records_and_a_spec_defined_helper_unbind():
    two = "Seed data:" + NL + "- Workbook `Alpha` and workbook `Beta`: rows `North/800` and `East/1200`"
    assert bindings(spec(opening("Alpha") + '  await notSee(page, "East", grid);'), two) == set()
    helper = HEAD + "async function settle(page) { await page.locator(\"#grid\").waitFor(); }" + NL + NL
    body = opening("Q3 Sales") + "  await settle(page);" + NL + '  await notSee(page, "East", grid);'
    assert bindings(spec(body, head=helper)) == set()
    bracket = opening("Q3 Sales") + '  await page["fill"]("#x", "y");' + NL + '  await notSee(page, "East", grid);'
    assert bindings(spec(bracket)) == set()

@case
def only_a_grid_region_or_no_scope_binds():
    assert bindings(spec(opening("Q3 Sales") + '  await notSee(page, "East");')) == {(rb.S1, "East")}
    assert bindings(spec(opening("Q3 Sales") + '  await notSee(page, "East", "grid");')) == set()
    other = opening("Q3 Sales") + '  const cell = region(page, "gridcell", "A2", grid);' + NL + '  await notSee(page, "East", cell);'
    assert bindings(spec(other)) == set()
    assert bindings(spec(opening("Q3 Sales") + '  await notSee(page, "East", item(page, "row", "x"));')) == set()

@case
def unknown_methods_and_any_hook_unbind():
    waits = opening("Q3 Sales") + '  await page.locator("x").waitFor();' + NL + '  await notSee(page, "East", grid);'
    assert bindings(spec(waits)) == set()
    named_hook = HEAD + "async function setup({ page }) { await page.goto(\"/\"); }" + NL + "test.beforeEach(setup);" + NL
    assert bindings(spec(opening("Q3 Sales") + '  await notSee(page, "East", grid);', head=named_hook)) == set()

@case
def the_verdict_rejects_a_value_of_another_record():
    seeds = ("Seed data:" + NL + "- Workbook `Q3 Sales`: header `Region/Amount` and rows `North/800`" + NL
             + "- Workbook `Q4 Plan`: rows `East/1200`")
    assert a.direct(node=dict(a.NODE, seed_sentences=seeds)) == ([], [])
    assert a.direct() == ([NAME], ["East"])

@case
def a_disguised_error_and_an_unknown_block_line_are_not_clean():
    import main as m
    clean = rb.spec_output(NAME, [rb.S1]).replace(a.WORLD_ERROR, a.NOTSEE_EAST)
    assert m.seed_conflict_output_clean(clean)
    assert not m.seed_conflict_output_clean(clean + NL + "Error: cleanup failed at old.spec.js:12:3 › seed visibility")
    odd = clean.replace(a.NOTSEE_EAST, a.NOTSEE_EAST + NL + "    the row was written twice")
    assert not m.seed_conflict_output_clean(odd)
    framed = clean.replace(a.NOTSEE_EAST, a.NOTSEE_EAST + NL + "  url: http://localhost:5173/w/1" + NL
                           + "    > 38 |   await notSee(page, \"East\", grid);" + NL + "         |   ^")
    assert m.seed_conflict_output_clean(framed), "fail()'s url line and a code frame are known lines"
    assert a.direct(output_error=a.NOTSEE_EAST + NL + "    the row was written twice") == ([], [])

    assert not m.seed_conflict_output_clean(clean + NL + "  2 failed, server error after teardown")
    assert not m.seed_conflict_output_clean(clean.replace("  1 failed", "  1 failed -- Error: db crashed"))

@case
def a_line_reporter_block_with_a_trace_attachment_is_clean_page_text_with_a_problem_is_not():
    import main as m
    block = NL.join([
        "FAILED SPEC FILES: REQ-1-1-1.spec.js",
        f"  1) [chromium] › test-e2e\\REQ-1-1-1.spec.js:28:1 › {rb.S1} ──────────",
        "",
        "    " + a.NOTSEE_EAST,
        "      url: http://localhost:5173/w/1",
        "      on the page now -> clickable: [\"Q3 Sales\"]",
        "      fields: []",
        "      headings: [\"Q3 Sales\"]",
        "      messages: []",
        "",
        "      36 |   await see(page, \"Region\", region(page, \"gridcell\", \"A1\", grid));",
        "    > 38 |   await notSee(page, \"East\", grid);",
        "         |   ^",
        "",
        "        at notSee (D:\\w\\backend\\e2e-support\\outsider.js:654:9)",
        "        at D:\\w\\backend\\test-e2e\\REQ-1-1-1.spec.js:38:3",
        "",
        "    attachment #1: trace (application/zip) ──────────",
        "    test-results\\REQ-1-1-1-chromium\\trace.zip",
        "    Usage:",
        "",
        "        npx playwright show-trace test-results\\REQ-1-1-1-chromium\\trace.zip",
        "",
        "    ────────────────────────",
        "",
        "    Error Context: test-results\\REQ-1-1-1-chromium\\error-context.md",
        "",
        "  1 failed",
        f"    [chromium] › test-e2e\\REQ-1-1-1.spec.js:28:1 › {rb.S1} ──────────"])
    assert m.seed_conflict_output_clean(block)
    assert m.notsee_values_by_test(block) == {(NAME, rb.S1): ["East"]}, m.notsee_values_by_test(block)
    assert not m.seed_conflict_output_clean(block.replace('messages: []', 'messages: ["Server error: rows.id"]'))
    assert not m.seed_conflict_output_clean(block.replace("    Usage:", "    Usage: retry later"))
    assert not m.seed_conflict_output_clean(block.replace('messages: []', 'messages: ["Could not save"]'))

    helper_frame = block.replace('    > 38 |   await notSee(page, "East", grid);',
                                 "    > 111 |   throw new Error(`${what}\\n  url: ${page.url()}\\n  on the page now -> clickable: ` +")
    assert m.seed_conflict_output_clean(helper_frame)
    assert not m.seed_conflict_output_clean(block.replace('    > 38 |   await notSee(page, "East", grid);',
                                                          "    > 12 |   throw new Error('db write failed');"))

@case
def a_second_failing_test_with_known_lines_only_and_no_notsee_is_not_excused():

    assert a.direct(second_error="at fail (D:\\w\\backend\\e2e-support\\outsider.js:43:9)") == ([], [])
    assert a.direct(second_error=a.NOTSEE_EAST)[0] == [NAME]

def factory(root: Path):
    f = rb.make_factory(root, "base")
    f.seed_quarantine = {NAME: ["East"]}
    return f

@case
def the_full_check_runs_no_quarantined_spec_unless_switched_off():
    import main as m
    from agent.testrun import TestOutcome
    from agent.verify import StepResult

    ran: list = []
    names = ("run_e2e", "build_frontend", "run_node_tests", "lint_application", "log")
    saved = {n: getattr(m, n) for n in names}
    try:
        m.run_e2e = lambda project_dir, specs, log=print, **kw: (ran.append(list(specs)), StepResult(True, "ok"))[1]
        m.build_frontend = lambda project_dir, log=print: StepResult(True, "")
        m.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
        m.lint_application = lambda project_dir, native: ""
        lines: list = []
        m.log = lines.append
        for on, expect in ((True, [rb.F2]), (False, [rb.F1, rb.F2])):
            ran.clear()
            with v145a.Settings(seed_conflict_quarantine=on), tempfile.TemporaryDirectory() as tmp:
                f = factory(Path(tmp))
                f.restore_tests = lambda: None
                f.full_check()
            assert ran == [expect], (on, ran)
        assert any("full check: skips seed-quarantined spec(s) ['REQ-1-1-1.spec.js']" in l for l in lines), lines
    finally:
        for n, v in saved.items():
            setattr(m, n, v)

@case
def source_shape_the_guard_and_the_predecessor_replay_use_the_filter():
    import inspect

    import main as m
    text = inspect.getsource(m.Factory.repair_failing_specs)
    assert 'green_pool = self.without_seed_quarantined(green_pool, "closing repair guard")' in text
    assert 'live = self.without_seed_quarantined(list(self.e2e_specs), "closing repair, predecessor replay")' in text
    assert "target = [live[index - 1], spec]" in text and "self.e2e_specs[index - 1]" not in text

@case
def switched_off_at_restore_the_saved_quarantine_is_ignored_and_logged():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app"
        (out / ".arc").mkdir(parents=True)
        dict_fields = ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "report", "seed_checks")
        first = m.Factory.__new__(m.Factory)
        first.project_dir, first.revised_specs = out, set()
        for field in m.Factory.CHECKPOINT_FIELDS:
            setattr(first, field, {} if field in dict_fields else [])
        first.started, first.spent_before = time.time(), 0.0
        first.seed_quarantine = {NAME: ["East"]}
        first.save_checkpoint("digest-1", ["REQ-9"], "")
        saved = json.loads((out / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert saved.get("seed_quarantine") == {NAME: ["East"]} and saved.get("seed_quarantine_switch") is True, saved
        lines: list = []
        old_log = m.log
        m.log = lines.append
        try:
            with v145a.Settings(seed_conflict_quarantine=False):
                second = m.Factory.__new__(m.Factory)
                second.project_dir, second.revised_specs = out, set()
                assert second.load_checkpoint("digest-1") == ["REQ-9"]
                assert "seed_quarantine" not in second.__dict__, second.__dict__.get("seed_quarantine")
                assert second.without_seed_quarantined([rb.F1], "x") == [rb.F1]
        finally:
            m.log = old_log
        assert any("seed_conflict_quarantine is off: the saved seed quarantine ['REQ-1-1-1.spec.js'] is ignored" in l
                   for l in lines), lines

def _v157f_spec(body):
    head = ('const { test } = require("@playwright/test");\n'
            'const { click, see, notSee, region, freshSession } = require("../e2e-support/outsider");\n')
    return head + 'test("t", async ({ page }) => {\n  await page.goto("/");\n' + body + '});\n'

@case
def v157f_goto_and_scope_must_be_provably_the_opened_record():
    from agent import outsider as o
    seeds = ("SEED\n- The evaluation seed contains the seeded workbook `Q3 Sales` with `Sheet1` and `Sheet2`, rows "
             "`East/1200` and `North/800`.\n- The evaluation seed contains the seeded workbook `Beta Book`, rows `West/5`.")
    node = {"seed_sentences": seeds, "id": "REQ-1"}
    opened = '  await click(page, { role: "link", name: "Q3 Sales" });\n'
    good = _v157f_spec(opened + '  const url = page.url();\n  await freshSession(page);\n  await page.goto(url);\n'
                       '  const grid = region(page, "grid", "Worksheet grid");\n  await notSee(page, "East", grid);\n')
    assert o.seed_conflict_bindings(good, node) == {("t", "East")}
    bad = {
        "goto to another variable": opened + '  await page.goto(betaUrl);\n  await notSee(page, "East");\n',
        "url held in a let": opened + '  let url = page.url();\n  await page.goto(url);\n  await notSee(page, "East");\n',
        "scope in a let": opened + '  let g = region(page, "grid", "x");\n  await notSee(page, "East", g);\n',
        "scope reassigned": opened + '  const g = region(page, "grid", "x");\n  g = region(page, "dialog", "y");\n'
                                     '  await notSee(page, "East", g);\n',
        "scope only in a comment": opened + '  // const g = region(page, "grid", "x");\n  await notSee(page, "East", g);\n',
        "scope not a grid": opened + '  const g = region(page, "dialog", "x");\n  await notSee(page, "East", g);\n',
    }
    for why, body in bad.items():
        assert o.seed_conflict_bindings(_v157f_spec(body), node) == set(), why

@case
def v158_declarations_must_be_exactly_the_url_and_the_grid_region():

    from agent import outsider as o
    seeds = ("SEED\n- The evaluation seed contains the seeded workbook `Q3 Sales` with `Sheet1` and `Sheet2`, rows "
             "`East/1200` and `North/800`.")
    node = {"seed_sentences": seeds, "id": "REQ-1"}
    opened = '  await click(page, { role: "link", name: "Q3 Sales" });\n'
    good = _v157f_spec(opened + '  const url = page.url();\n  await page.goto(url);\n'
                       '  const grid = region(page, "grid", "Worksheet grid");\n  await notSee(page, "East", grid);\n')
    assert o.seed_conflict_bindings(good, node) == {("t", "East")}
    bad = {
        "url with a suffix": opened + '  const x = page.url() + "/../Beta";\n  await page.goto(x);\n  await notSee(page, "East");\n',
        "url in a ternary": opened + '  const x = page.url() ? "/b" : "/c";\n  await page.goto(x);\n  await notSee(page, "East");\n',
        "grid and dialog": opened + '  const g = region(page, "grid") && region(page, "dialog");\n  await notSee(page, "East", g);\n',
        "grid then a call": opened + '  const g = region(page, "grid").first();\n  await notSee(page, "East", g);\n',
    }
    for why, body in bad.items():
        assert o.seed_conflict_bindings(_v157f_spec(body), node) == set(), why

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
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v157d cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
