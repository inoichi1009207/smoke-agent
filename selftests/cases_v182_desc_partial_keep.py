from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl

CASES = []
KEY = "description_pass_partial_keep"

def case(function):
    CASES.append(function)
    return function

def half_repair(extra: str = "", app: str = "base"):
    tmp, root, world, f = cl.with_world(app=app)
    world.titles["REQ-3"] = [cl.T3A, cl.T3B]
    world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A) + extra}, "check": True}]
    return tmp, root, world, f

@case
def a_strict_improvement_keeps_the_work_and_cuts_out_the_still_red_test():
    tmp, root, world, f = half_repair()
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after a partly kept repair (app-defect-unrepaired)", (entry, lines)
        assert not entry["rollback"]["done"], entry
        assert entry["partial_keep"]["first_red"] == sorted([cl.T3A, cl.T3B]) and entry["partial_keep"]["own_red"] == [cl.T3B]
        assert cl.app_text(root) == "base " + cl.ok(cl.T3A), cl.app_text(root)
        assert cl.D3 in f.e2e_specs and f.description_specs == [cl.D3]
        assert world.spec_titles(cl.D3) == [cl.T3A]
        tests = {t["title"]: t for t in entry["tests"]}
        assert tests[cl.T3A]["final"] == "admitted" and tests[cl.T3B]["final"] == "dropped", tests
        assert tests[cl.T3B]["reason"] == "app-defect-unrepaired", tests
        assert summary["repairs_partly_kept"] == 1 and summary["repairs_kept"] == 0 and summary["repairs_rolled_back"] == 0, summary

        assert len(world.check_outputs) == 1 and world.check_outputs[0].startswith("E2E FAILED against the real application")
        assert len(world.implementers) == 1
        assert any("repair kept in part" in line for line in lines), lines

@case
def a_new_failure_in_the_suite_rolls_back():
    tmp, root, world, f = half_repair(" " + cl.brk(cl.T1B))
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base", cl.app_text(root)
        assert entry["rollback"]["done"] and entry["rollback"]["reason"] == "own-red", entry
        assert entry["rollback"]["partial_why_not"].startswith("new-red: REQ-1.spec.js"), entry
        assert "partial_keep" not in entry and "repairs_partly_kept" not in summary, summary

@case
def a_lost_seed_value_rolls_back():
    seeds = "SEED SENTENCES OF THE WHOLE REQUIREMENT:" + cl.NL + "- The evaluation seed contains the row `East/1200`."
    tmp, root, world, f = half_repair(app="base East 1200")
    with tmp:
        p, summary, lines = cl.run_pass(world, f, seeds=seeds)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base East 1200" and cl.D3 not in f.e2e_specs, (cl.app_text(root), entry)
        assert entry["rollback"]["done"] and "lost-seed" in entry["rollback"]["partial_why_not"], entry

@case
def an_incomplete_suite_rolls_back():
    import agent.description_pass as dp

    original = dp.DescriptionPass.run_suite
    calls = []

    def run_suite(self, specs, strict=False):
        calls.append(list(specs))
        if len(calls) == 1:
            return original(self, specs, strict=strict)
        return dp.Suite(incomplete="the hard end is near (REQ-2.spec.js not run)")

    tmp, root, world, f = half_repair()
    with tmp:
        dp.DescriptionPass.run_suite = run_suite
        try:
            p, summary, lines = cl.run_pass(world, f)
        finally:
            dp.DescriptionPass.run_suite = original
        entry = cl.ledger(f)[0]
        assert len(calls) == 2, calls
        assert cl.app_text(root) == "base" and cl.D3 not in f.e2e_specs, (cl.app_text(root), entry)
        assert entry["rollback"]["done"] and entry["rollback"]["partial_why_not"] == "suite-incomplete", entry

@case
def an_own_test_green_before_and_red_now_rolls_back():

    tmp, root, world, f = cl.with_world(app="base " + cl.ok(cl.T3A))
    with tmp:
        world.titles["REQ-3"] = [cl.T3A, cl.T3B]
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3B)}, "check": True}]
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base " + cl.ok(cl.T3A), cl.app_text(root)
        assert entry["rollback"]["done"] and entry["rollback"]["partial_why_not"].startswith("no strict improvement"), entry
        assert entry["outcome"] == "admitted after pruning (app-defect-unrepaired)", entry

@case
def no_improvement_rolls_back_without_a_suite_run():
    tmp, root, world, f = half_repair()
    with tmp:
        world.steps = [{"write": {cl.APP: "base touched"}, "check": True}]
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base" and entry["rollback"]["done"], entry
        assert entry["rollback"]["partial_why_not"] == "no strict improvement (as many own tests red)", entry
        repair_runs = [specs for specs, text in world.runs if "touched" in text]
        assert repair_runs == [[cl.D3]], repair_runs

@case
def a_red_confirming_run_puts_the_partly_kept_repair_back():
    tmp, root, world, f = half_repair()
    with tmp:
        world.flaky["DESC-REQ-3.spec.js"] = (cl.ok(cl.T3A), 1)
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base", cl.app_text(root)
        assert entry["rollback"] == {"done": True, "reason": "partial keep undone: the pruned spec is not green"}, entry
        assert entry["outcome"] == "dropped: the pruned spec is not green" and cl.D3 not in f.e2e_specs, entry
        assert p.baseline == {}, p.baseline

@case
def a_test_defect_verdict_is_not_kept_in_part():
    tmp, root, world, f = half_repair()
    with tmp:
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}, "check": True, "status": "test_defect"}]
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert "partial_keep" not in entry and not entry["outcome"].startswith("admitted after a partly"), entry

@case
def off_is_the_old_gate():
    tmp, root, world, f = half_repair()
    with tmp:
        p, summary, lines = cl.run_pass(world, f, settings={KEY: False})
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base" and cl.D3 not in f.e2e_specs, (cl.app_text(root), entry)
        assert entry["rollback"] == {"done": True, "reason": "own-red"}, entry
        assert entry["outcome"] == "dropped: every test red (app-defect-unrepaired)", entry
        assert "partial_keep" not in entry and "repairs_partly_kept" not in summary, summary
        repair_runs = [specs for specs, text in world.runs if cl.ok(cl.T3A) in text]
        assert repair_runs == [[cl.D3]], repair_runs

T2D = "REQ-2: shows the second row"

def with_two_test_f2(f2_text: str, unrun_marker: str, ran_when_marked: int):
    tmp, root, world, f = half_repair(" " + unrun_marker if unrun_marker else "")
    (root / cl.F2).write_text(f2_text, encoding="utf-8")
    f.test_snapshot[cl.F2] = f2_text
    original = world.run_e2e

    def run_e2e(project_dir, specs, log=print, shared_data=False):
        from agent.verify import StepResult

        if list(specs) == [cl.F2] and (not unrun_marker or unrun_marker in world.sources()):
            world.now += world.run_sec
            world.runs.append((list(specs), world.sources()))
            return StepResult(True, "1 passed", ran=ran_when_marked)
        return original(project_dir, specs, log=log, shared_data=shared_data)

    world.run_e2e = run_e2e
    return tmp, root, world, f

@case
def a_suite_spec_green_with_a_test_not_run_rolls_back():

    tmp, root, world, f = with_two_test_f2(cl.spec_text([cl.T2A, T2D]), cl.brk(T2D), 1)
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base", (cl.app_text(root), entry)
        assert entry["rollback"]["done"], entry
        assert entry["rollback"]["partial_why_not"] == "suite-incomplete (not every test of REQ-2.spec.js ran (1 of 2))", entry
        assert "partial_keep" not in entry and "repairs_partly_kept" not in summary, summary

@case
def a_suite_spec_with_a_static_skip_still_keeps_in_part():

    text = cl.spec_text([cl.T2A]) + 'test.skip("' + T2D + '", async ({ page }) => {});' + cl.NL
    tmp, root, world, f = with_two_test_f2(text, "", 1)
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after a partly kept repair (app-defect-unrepaired)", (entry, lines)
        assert cl.app_text(root) == "base " + cl.ok(cl.T3A) and summary["repairs_partly_kept"] == 1, summary

@case
def an_empty_suite_is_no_complete_suite():

    tmp, root, world, f = half_repair()
    with tmp:
        f.e2e_specs, f.green_specs = [], []
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base", (cl.app_text(root), entry)
        assert entry["rollback"]["done"], entry
        assert entry["rollback"]["partial_why_not"] == "suite-incomplete (no spec file in the suite)", entry
        assert "repairs_partly_kept" not in summary, summary

XA, XB = "backend/test-e2e/a/x.spec.js", "backend/test-e2e/b/x.spec.js"
TX = "X: shows the shared title"

def two_folders(extra: str, steps_app: str | None = None):
    tmp, root, world, f = half_repair(extra)
    if steps_app is not None:
        world.steps = [{"write": {cl.APP: steps_app}, "check": True}]
    text = cl.spec_text([TX])
    for rel in (XA, XB):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
        f.test_snapshot[rel] = text
    f.all_test_files, f.e2e_specs, f.green_specs = [XA, XB], [XA, XB], [XA]
    f.spec_context = {XA: dict(f.spec_context[cl.F1]), XB: dict(f.spec_context[cl.F2])}
    original = world.failing

    def failing(rel, text_now):
        if rel == XB:
            return [TX]
        if rel == XA:
            return [TX] if "BREAK-A" in text_now else []
        return original(rel, text_now)

    world.failing = failing
    return tmp, root, world, f

@case
def same_file_name_in_two_folders_does_not_mask_a_new_failure_partial():
    tmp, root, world, f = two_folders(" BREAK-A")
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base", (cl.app_text(root), entry)

        assert entry["rollback"]["done"] and entry["rollback"]["partial_why_not"] == "new-red: x.spec.js", entry
        assert "repairs_partly_kept" not in summary and p.baseline == {XB: [TX]}, (summary, p.baseline)

@case
def same_file_name_in_two_folders_does_not_mask_a_new_failure_green_gate():
    tmp, root, world, f = two_folders("", "base " + cl.ok(cl.T3A) + " " + cl.ok(cl.T3B) + " BREAK-A")
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert cl.app_text(root) == "base", (cl.app_text(root), entry)
        assert entry["rollback"]["done"] and entry["rollback"]["reason"].startswith("new-red: x.spec.js: " + TX), entry
        assert p.baseline == {XB: [TX]}, p.baseline

@case
def same_file_name_in_two_folders_still_keeps_without_a_new_failure():

    tmp, root, world, f = two_folders("")
    with tmp:
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after a partly kept repair (app-defect-unrepaired)", (entry, lines)
        assert summary["repairs_partly_kept"] == 1 and p.baseline == {XB: [TX]}, (summary, p.baseline)

RESUMED = [{"id": "REQ-9", "outcome": "admitted after a partly kept repair (app-defect-unrepaired)", "tests": []}]

@case
def off_adds_no_partly_kept_field_from_a_resumed_ledger():

    tmp, root, world, f = half_repair()
    with tmp:
        f.description_pass = {"ledger": [dict(e) for e in RESUMED]}
        p, summary, lines = cl.run_pass(world, f, settings={KEY: False})
        assert any(str(e.get("outcome", "")).startswith("admitted after a partly kept") for e in cl.ledger(f)), cl.ledger(f)
        assert "repairs_partly_kept" not in summary, summary

@case
def on_counts_a_resumed_partly_kept_outcome():
    tmp, root, world, f = half_repair()
    with tmp:
        f.description_pass = {"ledger": [dict(e) for e in RESUMED]}
        p, summary, lines = cl.run_pass(world, f)
        assert summary["repairs_partly_kept"] == 2, summary

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
    print(f"{len(CASES) - failed}/{len(CASES)} v182 description partial keep cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
