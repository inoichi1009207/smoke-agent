from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl
import cases_rollback as rb

NL = chr(10)
CASES = []
SKIPPED: list[str] = []
MISSING = object()

def case(function):
    CASES.append(function)
    return function

def samples_dir() -> Path | None:
    explicit = str(os.environ.get("ARC_REQUIREMENT_SAMPLES") or "").strip()
    if explicit:
        return Path(explicit) if Path(explicit).is_dir() else None
    for base in (ROOT, *list(ROOT.parents)[:2]):
        for candidate in (base / "requirements-samples", base / "codex-audit" / "requirements-samples"):
            if candidate.is_dir():
                return candidate
    return None

SAMPLES = samples_dir()

def census(*names: str):
    def register(function):
        missing = [n for n in names if SAMPLES is None or not (SAMPLES / n).is_file()]
        if missing and os.environ.get("ARC_REQUIRE_SAMPLES") != "1":
            line = f"SKIP  {function.__name__}: sample(s) {', '.join(missing)} not found"
            SKIPPED.append(line)
            print(line)
            return function
        CASES.append(function)
        return function
    return register

def sample_tree(name: str) -> tuple[dict, str]:
    import yaml

    text = (SAMPLES / name).read_text(encoding="utf-8")
    return yaml.safe_load(text), text

def leaves_of(tree: dict) -> list[dict]:
    import main as factory_module

    return [leaf for leaf, _ in factory_module.atomic_nodes(tree)]

class Settings:

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, MISSING) for key in self.values}
        SETTINGS.update(self.values)

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

@case
def dependents_are_transitive_and_follow_the_batch_order_coverage_rule():
    from agent.batching import transitive_dependents

    leaves = [{"id": "REQ-2-1"}, {"id": "REQ-2-2"}, {"id": "REQ-20"},
              {"id": "X", "dependencies": ["REQ-2"]},
              {"id": "Y", "dependencies": ["X"]},
              {"id": "P", "dependencies": ["Q"]}, {"id": "Q", "dependencies": ["P"]},
              {"id": "Z", "dependencies": "REQ-2-1"}]
    found = transitive_dependents(leaves)
    assert found["REQ-2-1"] == {"X", "Y"} and found["REQ-2-2"] == {"X", "Y"}, found
    assert found["REQ-20"] == set()
    assert found["X"] == {"Y"} and found["Y"] == set()
    assert found["P"] == {"Q"} and found["Q"] == {"P"}

@census("hackathon-github-platform.yaml", "hackathon-sheet-platform.yaml", "keep.yaml", "ticket-booking.yaml")
def the_platform_hubs_have_the_counts_of_the_evidence():
    from agent.batching import build_batches, transitive_dependents

    github, _ = sample_tree("hackathon-github-platform.yaml")
    counts = {k: len(v) for k, v in transitive_dependents(leaves_of(github)).items()}
    assert (counts["REQ-4-1"], counts["REQ-3-3"], counts["REQ-1-1-2"]) == (18, 32, 27), counts
    sheet, _ = sample_tree("hackathon-sheet-platform.yaml")
    counts = {k: len(v) for k, v in transitive_dependents(leaves_of(sheet)).items()}
    assert (counts["REQ-1-1-1"], counts["REQ-3-1-1"], counts["REQ-3-1-3"]) == (21, 10, 10), counts

    keep, _ = sample_tree("keep.yaml")
    counts = {k: len(v) for k, v in transitive_dependents(leaves_of(keep)).items()}
    work = build_batches(keep)
    hubs = [b["id"] for b in work if any(counts.get(str(m), 0) >= 5 for m in b["members"])]
    assert hubs == [work[0]["id"]], hubs
    tb, _ = sample_tree("ticket-booking.yaml")
    assert max(len(v) for v in transitive_dependents(leaves_of(tb)).values()) < 5

def _hub_engine(outcomes: dict, dependents: dict, time_left: float = 10 ** 6):
    f, calls = cl._engine(outcomes, time_left=time_left)
    f.leaf_dependents = dict(dependents)
    return f, calls

@case
def a_red_hub_batch_after_the_first_three_gets_exactly_one_more_attempt():
    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, False]}, {"D": 5})
    result, lines = cl._queue(f, "A", "B", "C", "D")
    assert calls == ["A", "B", "C", "D", "D+retry"] and result is False and f.failed_members == ["D"], calls
    assert f.hub_retried == ["D"] and f.foundation_retried == [], (f.hub_retried, f.foundation_retried)
    assert any("hub batch ended budget (D: 5 leaves depend on it)" in line for line in lines), lines
    assert any("D: hub retry ended red" in line for line in lines), lines
    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True]}, {"D": 5})
    result, lines = cl._queue(f, "A", "B", "C", "D")
    assert calls == ["A", "B", "C", "D", "D+retry"] and result is True and f.failed_members == [], calls
    assert any("D: hub retry ended GREEN" in line for line in lines), lines

    f, calls = cl._engine({"A": [True], "B": [True], "C": [True], "D": [False, True]})
    f.leaf_dependents = {"D-1": 1, "D-2": 7}
    from agent.llm import LLM
    import main as factory_module

    saved = (LLM.quota_exhausted, factory_module.log)
    try:
        LLM.quota_exhausted, factory_module.log = False, (lambda line: None)
        ok = f.run_queue([({"id": i, "members": [i]}, []) for i in "ABC"] + [({"id": "D", "members": ["D-1", "D-2"]}, [])], [], "d", "")
    finally:
        LLM.quota_exhausted, factory_module.log = saved
    assert ok is True and calls == ["A", "B", "C", "D", "D+retry"], calls

@case
def only_hubs_only_red_ends_only_once_only_with_time():

    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True]}, {"D": 4})
    assert cl._queue(f, "A", "B", "C", "D")[0] is False and calls == ["A", "B", "C", "D"], calls
    with Settings(hub_min_dependents=4):
        f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True]}, {"D": 4})
        assert cl._queue(f, "A", "B", "C", "D")[0] is True and calls[-1] == "D+retry", calls
    with Settings(hub_min_dependents="6"):
        f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True]}, {"D": 5})
        assert cl._queue(f, "A", "B", "C", "D")[0] is False and calls[-1] == "D", calls

    with Settings(hub_retry=False):
        f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True]}, {"D": 9})
        assert cl._queue(f, "A", "B", "C", "D")[0] is False and calls[-1] == "D", calls

    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [RuntimeError("bug"), True]}, {"D": 9})
    f.report["nodes"]["D"] = {"status": "budget", "tests": ["backend/test-e2e/D.spec.js"]}
    assert cl._queue(f, "A", "B", "C", "D")[0] is False and calls == ["A", "B", "C", "D"], calls

    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True]}, {"D": 9})
    f.hub_retried = ["D"]
    assert cl._queue(f, "A", "B", "C", "D")[0] is False and calls[-1] == "D", calls

    import main as factory_module

    need = factory_module.MIN_BATCH_SEC * (1 + 2)
    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True], "E": [True]}, {"D": 9}, time_left=need - 1)
    result, lines = cl._queue(f, "A", "B", "C", "D", "E")
    assert calls == ["A", "B", "C", "D", "E"] and any("D: no hub retry" in line for line in lines), (calls, lines)
    f, calls = _hub_engine({"A": [True], "B": [True], "C": [True], "D": [False, True], "E": [True]}, {"D": 9}, time_left=need)
    assert cl._queue(f, "A", "B", "C", "D", "E")[0] is True and calls == ["A", "B", "C", "D", "D+retry", "E"], calls

@case
def the_first_three_rule_is_kept():

    f, calls = _hub_engine({"A": [False, True], "B": [True]}, {})
    result, lines = cl._queue(f, "A", "B")
    assert result is True and calls == ["A", "A+retry", "B"] and f.foundation_retried == ["A"] and f.hub_retried == []
    assert any("foundation retry ended GREEN" in line for line in lines)

    f, calls = _hub_engine({"A": [False, False], "B": [True]}, {"A": 30})
    result, lines = cl._queue(f, "A", "B")
    assert calls == ["A", "A+retry", "B"] and f.foundation_retried == ["A"] and f.hub_retried == [], calls

    with Settings(foundation_retry=False):
        f, calls = _hub_engine({"A": [False, True], "B": [True]}, {"A": 30})
        assert cl._queue(f, "A", "B")[0] is True and f.hub_retried == ["A"], calls

SPEC = "backend/test-e2e/H.spec.js"
T1 = "test('H: shows the first title', async ({ page }) => {});" + NL
T2 = "test('H: shows the repaired title', async ({ page }) => {});" + NL

@case
def a_red_hub_retry_puts_the_first_attempts_state_back_with_one_test_version():
    from agent.test_author import AuthoredFile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "template")
        f.green_specs, f.e2e_specs, f.file_specs = [], [], {}
        f.description_pass, f.description_specs, f.llm = {}, [], None
        f.over_budget = lambda: False
        f.time_left = lambda: 10 ** 6
        f.GATEWAY_PAUSE_SEC = 0.0
        f.FOUNDATION_BATCHES = 0
        f.leaf_dependents = {"H": 18}

        def process_node(node, nodes_left, retry=False):
            req = node["id"]
            f.current_members = [req]
            content = T2 if retry else T1
            f.write_tests(req, [AuthoredFile(path=SPEC, content=content)])
            (root / rb.APP).write_text("worse" if retry else "first attempt", encoding="utf-8")
            (root / "frontend" / "src" / "Extra.tsx").write_text("retry only", encoding="utf-8") if retry else None
            f.spec_context[SPEC] = {"node": node, "contract": "", "relevant": [], "tests": {SPEC: content}, "members": [req], "written": [rb.APP]}
            f.report["nodes"][req] = {"status": "budget", "e2e": "failed", "tests": [SPEC], "written": [rb.APP], "test_runs": 1}
            if SPEC not in f.e2e_specs:
                f.e2e_specs.append(SPEC)
            return False
        f.process_node = process_node
        result, lines = cl._queue(f, "H")
        assert result is False and f.hub_retried == ["H"] and f.foundation_retried == [], lines
        assert any("H: hub retry ended red" in line for line in lines), lines
        assert any("the retry is not kept" in line for line in lines), lines
        assert (root / rb.APP).read_text(encoding="utf-8") == "first attempt"
        assert not (root / "frontend" / "src" / "Extra.tsx").exists()
        assert (root / SPEC).read_text(encoding="utf-8") == T2 and f.test_snapshot[SPEC] == T2
        assert f.spec_context[SPEC]["tests"] == {SPEC: T2}, f.spec_context[SPEC]["tests"]
        state = json.loads((root / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert state["spec_context"][SPEC]["tests"][SPEC] == state["test_snapshot"][SPEC] == T2

@case
def run_counts_the_dependents_before_the_batches_and_logs_the_hubs():
    import inspect

    import main as factory_module

    source = inspect.getsource(factory_module.Factory.run)

    counted = source.index("self.index_leaves([leaf for leaf, _ in leaves])")
    assert "transitive_dependents(leaves)" in inspect.getsource(factory_module.Factory.index_leaves)
    assert source.index("work = build_batches(tree)") < counted < source.index("self.run_queue(")
    assert "hub batches (a leaf with >=" in source

def hub_tree() -> dict:
    def leaf(i, deps=()):
        return {"id": f"REQ-{i}", "name": f"Feature {i}", "description": f"Feature number {i} shows its own page with its own list.",
                "dependencies": list(deps),
                "scenarios": [{"name": f"s-REQ-{i}", "steps": [{"keyword": "GIVEN", "content": "the user is on the home page"},
                                                               {"keyword": "THEN", "content": f"feature {i} is shown"}]}]}
    children = [leaf(1), leaf(2), leaf(3), leaf(4), leaf(5, ["REQ-1"]), leaf(6, ["REQ-5"]), leaf(7, ["REQ-5"]),
                leaf(8, ["REQ-5"]), leaf(9, ["REQ-5"]), leaf(10, ["REQ-6"])]
    return {"id": "ROOT", "name": "R", "description": "root", "children": children}

@case
def red_hub_leaves_is_empty_when_no_hub_batch_is_red():
    import agent.description_pass as dp

    tree = hub_tree()
    work = [{"id": str(leaf["id"]), "members": [str(leaf["id"])]} for leaf in dp.all_leaves(tree)]
    assert dp.red_hub_leaves(tree, work, [], 5) == {}
    assert dp.red_hub_leaves(tree, work, ["REQ-4", "REQ-6"], 5) == {}
    assert dp.red_hub_leaves(tree, work, ["REQ-5"], 5) == {"REQ-5": ("REQ-5", 5)}
    assert dp.red_hub_leaves(tree, work, ["REQ-5"], 6) == {}

    merged = [{"id": "REQ-4+1", "members": ["REQ-4", "REQ-5"]}]
    assert dp.red_hub_leaves(tree, merged, ["REQ-4", "REQ-5"], 5) == {"REQ-4": ("REQ-5", 5), "REQ-5": ("REQ-5", 5)}

@census("keep.yaml", "bookstack.yaml", "ticket-booking.yaml")
def keep_bookstack_ticket_booking_run_no_pass_without_a_red_hub():
    from agent.batching import build_batches
    import agent.description_pass as dp

    for name in ("keep.yaml", "bookstack.yaml", "ticket-booking.yaml"):
        tree, text = sample_tree(name)
        tmp, root, world, f = cl.with_world()
        with tmp:
            f.failed_members = []
            before = f.snapshot_sources()
            p, summary, lines = cl.run_pass(world, f, tree=tree, whole=text, real_boilerplate=True)
            assert summary["status"] == "no leaf in scope", (name, summary)
            assert world.runs == [] and world.author_calls == [] and world.builds == 0 and world.implementers == [], name
            assert f.snapshot_sources() == before and f.description_specs == [], name

        assert dp.red_hub_leaves(tree, build_batches(tree), [], 5) == {}, name

    tree, _ = sample_tree("ticket-booking.yaml")
    assert dp.red_hub_leaves(tree, build_batches(tree), ["REQ-1", "REQ-2"], 5) == {}

    tree, text = sample_tree("keep.yaml")
    found = dp.red_hub_leaves(tree, build_batches(tree), ["REQ-2.1", "REQ-2.2"], 5)
    assert found == {"REQ-2.1": ("REQ-1.1", 28), "REQ-2.2": ("REQ-1.1", 28)}, found
    tmp, root, world, f = cl.with_world()
    with tmp:
        f.failed_members = ["REQ-1.1"]
        p, summary, lines = cl.run_pass(world, f, tree=tree, whole=text, real_boilerplate=True,
                                        settings={"description_pass_max_sec": 0})
        assert [str(leaf["id"]) for leaf in p.in_scope()] == ["REQ-1.1"], p.in_scope()

@case
def red_hub_leaves_come_into_scope_and_first():
    tree = hub_tree()
    ids = tuple(f"REQ-{i}" for i in range(1, 11))

    tmp, root, world, f = cl.with_world()
    with tmp:
        f.failed_members = ["REQ-4", "REQ-5"]
        p, summary, lines = cl.run_pass(world, f, scope=("REQ-4", "REQ-9"), tree=tree, done_ids=ids)
        assert [c[0] for c in world.author_calls if not c[1]] == ["REQ-5", "REQ-4", "REQ-9"], (world.author_calls, lines)
        assert any("leaves of red hub batches in scope (first): REQ-5 (REQ-5: 5 dependents)" in line for line in lines), lines
        assert any("leaf order" in line and "REQ-5*" in line for line in lines), lines

    for failed, settings in ((["REQ-4"], {}), (["REQ-4", "REQ-5"], {"description_pass_hubs": False})):
        tmp, root, world, f = cl.with_world()
        with tmp:
            f.failed_members = failed
            p, summary, lines = cl.run_pass(world, f, scope=("REQ-4", "REQ-9"), tree=tree, done_ids=ids, settings=settings)
            assert [c[0] for c in world.author_calls if not c[1]] == ["REQ-4", "REQ-9"], (failed, settings, world.author_calls)

@case
def without_boilerplate_detection_only_red_hub_leaves_are_in_scope():
    tree = hub_tree()
    tmp, root, world, f = cl.with_world()
    with tmp:
        f.failed_members = ["REQ-5"]
        p, summary, lines = cl.run_pass(world, f, tree=tree, patch={"boilerplate_of_text": None},
                                        settings={"description_pass_max_sec": 0})
        assert [str(leaf["id"]) for leaf in p.in_scope()] == ["REQ-5"]
        assert not summary["status"].startswith("no boilerplate detection"), summary
        assert any("only the leaves of red hub batches are in scope" in line for line in lines), lines
    tmp, root, world, f = cl.with_world()
    with tmp:
        f.failed_members = []
        p, summary, lines = cl.run_pass(world, f, tree=tree, patch={"boilerplate_of_text": None})
        assert summary["status"].startswith("no boilerplate detection") and world.runs == [], summary

@case
def leaf_ranks_puts_red_hub_leaves_with_the_red_foundation():
    import agent.description_pass as dp

    work = [{"id": f"B{i}", "members": [f"REQ-{i}"]} for i in range(1, 7)]
    ranks = dp.leaf_ranks(work, ["REQ-1", "REQ-5", "REQ-6"], [], {}, 3, hub_leaves={"REQ-5"})
    assert ranks == {"REQ-1": 0, "REQ-5": 0, "REQ-6": 1}, ranks
    assert dp.leaf_ranks(work, ["REQ-5"], [], {}, 3) == {"REQ-5": 1}

V141_LOGGED = r"""E2E FAILED against the real application
FAILED SPEC FILES: REQ-4-1-2.spec.js
[1/1] test-e2e/REQ-4-1-2.spec.js:14:1 › REQ-4-1: Browse Repository Files and Directories - Scenario 1
  1) test-e2e/REQ-4-1-2.spec.js:14:1 › REQ-4-1: Browse Repository Files and Directories - Scenario 1
    Error: An outsider who only read the requirement could not find anything to click: link named docs.
    > 45 |   throw new Error(`${what}\n  url: ${page.url()}\n  on the page now -> clickable: ${JSON.stringify(inv.clickable || [])}` +
    Error Context: test-results/REQ-4-1-2-REQ-4-1-Browse-R-e5018-nd-Directories---Scenario-1/error-context.md
    test-e2e/REQ-4-1-2.spec.js:14:1 › REQ-4-1: Browse Repository Files and Directories - Scenario 1
[1/1] test-e2e/REQ-4-1-2.spec.js:34:1 › REQ-4-2-1: View Repository Commit History - Scenario 1
  1) test-e2e/REQ-4-1-2.spec.js:34:1 › REQ-4-2-1: View Repository Commit History - Scenario 1 ──────
    Error: locator.textContent: Test timeout of 10000ms exceeded.
    test-e2e/REQ-4-1-2.spec.js:34:1 › REQ-4-2-1: View Repository Commit History - Scenario 1 ───────
[1/1] test-e2e/REQ-4-1-2.spec.js:72:1 › REQ-4-2-2: Inspect Commit and Revision Differences - Scenario 1
  1) test-e2e/REQ-4-1-2.spec.js:72:1 › REQ-4-2-2: Inspect Commit and Revision Differences - Scenario 1
    Error: locator.textContent: Test timeout of 10000ms exceeded.
    test-e2e/REQ-4-1-2.spec.js:72:1 › REQ-4-2-2: Inspect Commit and Revision Differences - Scenario 1
    test-e2e/REQ-4-1-2.spec.js:14:1 › REQ-4-1: Browse Repository Files and Directories - Scenario 1 ───
    test-e2e/REQ-4-1-2.spec.js:34:1 › REQ-4-2-1: View Repository Commit History - Scenario 1 ───
    test-e2e/REQ-4-1-2.spec.js:72:1 › REQ-4-2-2: Inspect Commit and Revision Differences - Scenario 1 ───"""
V141_SPEC = "backend/test-e2e/REQ-4-1-2.spec.js"
V141_FILES = {V141_SPEC: "// the batch's spec", "backend/tests/REQ-4-1-2.test.js": "// advisory"}
T_HISTORY = "REQ-4-2-1: View Repository Commit History - Scenario 1"
T_DIFF = "REQ-4-2-2: Inspect Commit and Revision Differences - Scenario 1"
TIMEOUT_LINE = "Error: locator.textContent: Test timeout of 10000ms exceeded."

def with_frames(logged: str, helper_first: str = "") -> str:
    out = []
    for line in logged.split(NL):
        out.append(line)
        if "could not find anything to click" in line:
            out += ["        at fail (/app/backend/e2e-support/outsider.js:45:9)",
                    "        at click (/app/backend/e2e-support/outsider.js:231:5)",
                    "        at /app/backend/test-e2e/REQ-4-1-2.spec.js:22:3"]
        if line.strip() == TIMEOUT_LINE:
            out += ["    Call log:", "      - waiting for locator('.commit-list li').first()", "",
                    "      39 |   await see(page, 'acme-docs');",
                    "    > 40 |   const first = await page.locator('.commit-list li').first().textContent();",
                    "         |                                                          ^"]
            if helper_first:
                out.append(f"        at {helper_first} (/app/backend/e2e-support/outsider.js:264:20)")
            out.append("        at /app/backend/test-e2e/REQ-4-1-2.spec.js:40:58")
    return NL.join(out)

def one_failure(spec: str, title: str, body: list[str]) -> str:
    return NL.join([f"FAILED SPEC FILES: {spec}", f"  1) test-e2e/{spec}:5:1 › {title} ──────────", ""] + body)

REQ9 = "REQ-9.spec.js"
REQ9_FILES = {"backend/test-e2e/REQ-9.spec.js": "// spec"}
AT_SPEC = "        at /app/backend/test-e2e/REQ-9.spec.js:7:14"

@case
def the_real_v141_raw_waits_are_recognised_and_the_helpers_error_is_not():
    from agent.codegen import spec_unexplained_waits

    found = spec_unexplained_waits(with_frames(V141_LOGGED), V141_FILES)
    assert [text for _key, text in found] == [f"REQ-4-1-2.spec.js: {T_HISTORY}: {TIMEOUT_LINE}",
                                              f"REQ-4-1-2.spec.js: {T_DIFF}: {TIMEOUT_LINE}"], found

    assert spec_unexplained_waits(V141_LOGGED, V141_FILES) == []

    assert spec_unexplained_waits(with_frames(V141_LOGGED, helper_first="click"), V141_FILES) == []

    assert spec_unexplained_waits(with_frames(V141_LOGGED), {"backend/test-e2e/REQ-1-1-1.spec.js": ""}) == []

@case
def other_waits_that_name_no_expectation_are_recognised():
    from agent.codegen import spec_unexplained_waits

    wait_for = one_failure(REQ9, "REQ-9: lists the commits", [
        "    TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.", "    Call log:",
        "      - waiting for locator('.commit-row') to be visible", "",
        "    >  7 |   await page.waitForSelector('.commit-row');", "         |              ^", AT_SPEC])
    assert [t for _k, t in spec_unexplained_waits(wait_for, REQ9_FILES)] == [
        "REQ-9.spec.js: REQ-9: lists the commits: TimeoutError: page.waitForSelector: Timeout 5000ms exceeded."]

    coloured = one_failure(REQ9, "REQ-9: undo", [
        "    \x1b[31mTest timeout of 40000ms exceeded.\x1b[39m", "",
        "    Error: locator.click: Test timeout of 40000ms exceeded.", "    Call log:",
        "    \x1b[2m  - waiting for getByRole('button', { name: 'Undo' })\x1b[22m", "",
        "    > 178 |   await page.getByRole(\"button\", { name: \"Undo\" }).click();",
        "        at D:\\w\\backend\\test-e2e\\REQ-9.spec.js:178:52"])
    assert len(spec_unexplained_waits(coloured, REQ9_FILES)) == 1
    other_page = one_failure(REQ9, "REQ-9: waits for the url", [
        "    Error: page.waitForURL: Test timeout of 10000ms exceeded.", AT_SPEC])
    assert len(spec_unexplained_waits(other_page, REQ9_FILES)) == 1
    bare_expect = one_failure(REQ9, "REQ-9: shows the list", [
        "    Error: Timed out 5000ms waiting for expect(locator).toBeVisible()", "", "    Call log:",
        "      - waiting for locator('.list')", AT_SPEC])
    assert len(spec_unexplained_waits(bare_expect, REQ9_FILES)) == 1
    bare_timeout = one_failure(REQ9, "REQ-9: polls", ["    Test timeout of 10000ms exceeded.", "", AT_SPEC])
    assert len(spec_unexplained_waits(bare_timeout, REQ9_FILES)) == 1

@case
def errors_that_say_what_was_expected_or_that_the_app_owns_are_not_recognised():
    from agent.codegen import spec_unexplained_waits

    helper_nothing = one_failure(REQ9, "REQ-9: shows main", [
        "    Error: The requirement says main is shown, but nothing visible matches.",
        "      url: http://127.0.0.1:3100/acme/docs",
        "    > 45 |   throw new Error(`${what}` +",
        "        at fail (/app/backend/e2e-support/outsider.js:45:9)",
        "        at see (/app/backend/e2e-support/outsider.js:391:5)", AT_SPEC])
    expect_named = one_failure(REQ9, "REQ-9: names the repo", [
        "    Error: expect(locator).toHaveText(expected) failed", "",
        "    Locator:  getByRole('heading', { level: 1 })", "    Expected string: \"acme-docs\"",
        "    Received string: \"acme\"", "    Timeout:  5000ms", AT_SPEC])
    expect_visible = one_failure(REQ9, "REQ-9: shows the badge", [
        "    Error: expect(locator).toBeVisible() failed", "", "    Locator:  locator('.badge')",
        "    Expected: visible", "    Timeout:  5000ms", "    Error: element(s) not found", AT_SPEC])
    equality = one_failure(REQ9, "REQ-9: counts", [
        "    Error: expect(received).toBe(expected) // Object.is equality", "", "    Expected: 2", "    Received: 1", AT_SPEC])
    helper_click = one_failure(REQ9, "REQ-9: opens", [
        "    TimeoutError: locator.click: Timeout 4000ms exceeded.", "    Call log:",
        "        at click (/app/backend/e2e-support/outsider.js:264:20)", AT_SPEC])
    goto = one_failure(REQ9, "REQ-9: opens the page", ["    Error: page.goto: Test timeout of 10000ms exceeded.", AT_SPEC])
    bare_no_frame = one_failure(REQ9, "REQ-9: polls", ["    Test timeout of 10000ms exceeded.", "",
                                                       "    Error Context: test-results/x/error-context.md"])
    thrown = one_failure(REQ9, "REQ-9: goes", ["    TypeError: goto is not a function", AT_SPEC])
    for name, output in (("helper nothing visible", helper_nothing), ("expect with expected / received", expect_named),
                         ("expect visible", expect_visible), ("equality", equality), ("helper click", helper_click),
                         ("goto", goto), ("bare without frame", bare_no_frame), ("thrown", thrown)):
        assert spec_unexplained_waits(output, REQ9_FILES) == [], name

def _implementer(root: Path, outcomes, max_rounds: int = 3):
    import cases_engine as ce
    from agent.codegen import CodegenImplementer

    class Model:
        def __init__(self):
            self.calls, self.last_finish_reason, self.model = [], "stop", "glm-test"

        def chat(self, messages, **options):
            self.calls.append(options.get("tag", ""))
            if ":diagnose" in options.get("tag", ""):
                return {"content": "frontend/src/pages/HomePage.tsx -- render it"}
            return {"content": ce.block("frontend/src/pages/HomePage.tsx", f"export default function HomePage() {{ return {len(self.calls)}; }}")}

    results = iter(outcomes)
    lines: list[str] = []

    implementer = CodegenImplementer(Model(), root, lambda: next(results), max_rounds=max_rounds, extra_rounds=0,
                                     log=lines.append, route_raw_waits=True)
    return implementer, lines

def _red(output: str):
    from agent.testrun import TestOutcome

    return TestOutcome(False, "E2E FAILED against the real application" + NL + output)

@case
def the_second_check_with_the_same_raw_wait_goes_to_the_test_author_once():
    from agent.testrun import TestOutcome

    node = {"id": "REQ-4-1+2", "name": "n", "description": "Browse files."}

    first_check = with_frames(V141_LOGGED)
    second_check = with_frames(V141_LOGGED.replace("could not find anything to click: link named docs.",
                                                   "could not find anything to click: link named main."))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        implementer, lines = _implementer(root, [_red(first_check), _red(second_check)] + [_red(second_check)] * 6)
        result = implementer.implement(node, V141_FILES, TestOutcome(False, "RED as expected"))
        assert result.status == "test_defect" and result.test_runs == 2, (result.status, result.test_runs, lines)
        assert "REQ-4-1-2.spec.js" in result.detail
        assert f"REQ-4-1-2.spec.js: {T_HISTORY}: {TIMEOUT_LINE}" in result.detail
        assert "says nothing about the application" in result.detail and "see()" in result.detail
        assert any("handing it to the test author" in line for line in lines), lines

        from agent.codegen import CodegenImplementer
        CodegenImplementer.raw_wait_routed.update(tuple(key) for key in (getattr(result, "routed", None) or []))

        again, lines = _implementer(root, [_red(first_check)] * 8)
        result = again.implement(node, V141_FILES, TestOutcome(False, "RED as expected"))
        assert result.status != "test_defect" and not any("handing it to the test author" in line for line in lines), result.status

@case
def one_check_alone_or_the_setting_off_routes_nothing():
    from agent.testrun import TestOutcome

    node = {"id": "REQ-4-1+2", "name": "n", "description": "Browse files."}
    helper_only = with_frames(V141_LOGGED.replace(TIMEOUT_LINE, "Error: The requirement says main is shown, but nothing visible matches."))
    with tempfile.TemporaryDirectory() as tmp:

        implementer, lines = _implementer(Path(tmp), [_red(with_frames(V141_LOGGED)), _red(helper_only)] * 5)
        result = implementer.implement(node, V141_FILES, TestOutcome(False, "RED as expected"))
        assert result.status != "test_defect" and not any("handing it to the test author" in line for line in lines), lines
    with tempfile.TemporaryDirectory() as tmp, Settings(raw_wait_to_author=False):
        implementer, lines = _implementer(Path(tmp), [_red(with_frames(V141_LOGGED))] * 10)
        result = implementer.implement(node, V141_FILES, TestOutcome(False, "RED as expected"))
        assert not any("handing it to the test author" in line for line in lines), lines

@case
def round_caps_grow_with_dependents_and_spec_size_and_stay_bounded():
    from agent.codegen import EXTRA_ROUNDS, count_spec_tests, round_caps

    assert round_caps(0, 3) == (4, EXTRA_ROUNDS, 0) and round_caps(4, 6) == (4, 3, 0)
    assert round_caps(5, 3) == (5, 3, 1) and round_caps(0, 7) == (5, 3, 1)
    assert round_caps(18, 3) == (6, 4, 2)
    assert round_caps(10, 7) == (6, 4, 2)
    assert round_caps(32, 20) == (6, 4, 2)
    assert round_caps(5, 3, hub_min=6) == (4, 3, 0)
    for dependents in range(0, 40):
        for tests in range(0, 30):
            most, extra, _points = round_caps(dependents, tests)
            assert most + extra <= 1.5 * (4 + 3), (dependents, tests)
    spec = NL.join(["test('REQ-9: one', async () => {});", "  test.skip(\"REQ-9: two\", async () => {});",
                    "// test('REQ-9: not code', ...)", "test.describe('group', () => {});", "test(`REQ-9: three`, async () => {});"])
    assert count_spec_tests({"backend/test-e2e/REQ-9.spec.js": spec, "backend/tests/REQ-9.test.js": "test('unit', () => {});"}) == 3

def _process(dependents: dict, spec_tests: int, settings: dict | None = None):
    import main as factory_module
    from agent.codegen import ImplementResult
    from agent.test_author import AuthoredFile
    from agent.testrun import TestOutcome

    seen: list[dict] = []

    class Recording:
        def __init__(self, llm, project_dir, check, **options):
            seen.append(options)
            self.check = check

        def implement(self, node, tests, outcome, extra_context=""):
            result = self.check()
            return ImplementResult("passed" if result.passed else "budget", [], "recorded", 1, last_check=result.output if result.passed else "")

    lines: list[str] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output", "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    with tempfile.TemporaryDirectory() as tmp, Settings(audit_enabled=False, **(settings or {})):
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "OWN-OK")
        f.leaf_dependents = dict(dependents)
        body = "".join(f"test('{rb.OWN_TITLE} {i}', async () => {{}});" + NL for i in range(spec_tests))
        f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=rb.OWN, content=body)]
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = Recording
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-8", "REQ-9"]}, 1)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
    return seen, lines

@case
def process_node_gives_a_hub_more_rounds_and_a_small_leaf_todays_call():
    seen, lines = _process({"REQ-9": 18, "REQ-8": 2}, 3)
    assert seen and seen[0].get("max_rounds") == 6 and seen[0].get("extra_rounds") == 4, seen
    assert "[codegen] REQ-9: round cap 6 + 4 extra (dependents 18, tests 3)" in lines, [l for l in lines if "round cap" in l]
    seen, lines = _process({"REQ-9": 0}, 7)
    assert seen[0].get("max_rounds") == 5 and seen[0].get("extra_rounds") == 3, seen
    seen, lines = _process({}, 3)
    assert "max_rounds" not in seen[0] and "extra_rounds" not in seen[0] and not any("round cap" in l for l in lines), seen
    seen, lines = _process({"REQ-9": 18}, 3, settings={"round_bonus": False})
    assert "max_rounds" not in seen[0], seen

    import inspect

    import main as factory_module

    assert inspect.getsource(factory_module.Factory.process_node).count("**self.round_caps(node, test_files)") == 2

@case
def the_time_share_still_stops_a_hubs_rounds():
    from agent import clock
    from agent.codegen import round_caps
    from agent.testrun import TestOutcome

    most, extra, _ = round_caps(18, 3)
    fake = [0.0]
    checks: list[float] = []

    def check():
        fake[0] += 1000.0
        checks.append(fake[0])
        return TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 › REQ-9: opens" + NL + f"    Error: heading {len(checks)} not found")

    saved = clock.now
    try:
        clock.now = lambda: fake[0]
        with tempfile.TemporaryDirectory() as tmp:
            implementer, _lines = _implementer(Path(tmp), [])
            implementer.check, implementer.max_rounds, implementer.extra_rounds = check, most, extra
            implementer.deadline = 2500.0
            result = implementer.implement({"id": "REQ-9", "name": "n", "description": "d"}, {}, TestOutcome(False, "red"))
    finally:
        clock.now = saved
    assert result.status == "budget" and "deadline" in result.detail and len(checks) == 3, (result.status, result.detail, checks)

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
    print(f"{len(CASES) - failed}/{len(CASES)} hub cases passed" + (f" ({len(SKIPPED)} skipped: samples missing)" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
