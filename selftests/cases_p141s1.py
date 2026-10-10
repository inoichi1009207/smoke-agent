from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl

NL = chr(10)
CASES = []

V141_SUITE_EST = 1.2 * 781.0
V141_LEFT, V141_LEAVES = 25262.0, 23
V141_AUTHOR_SEC = 237.0
V142_LEFT = 10800.0 - 781.0

BAD = 'role: "password-input"'
SPEC_DEFECT_LINE = ('    Error: SPEC DEFECT: "password-input" is no ARIA role -- use one (button, link, textbox, checkbox, ...) '
                    'or the plain name the requirement quotes (ADR-099).')

def case(function):
    CASES.append(function)
    return function

def old_share(left: float, leaves_left: int) -> float:
    return max(1200.0, min(3600.0, left / max(1, leaves_left)))

@case
def on_the_github_v141_figures_the_old_share_can_never_repair_and_the_new_one_can():
    import agent.description_pass as dp

    old = old_share(V141_LEFT, V141_LEAVES)
    assert old == 1200.0 and old - V141_SUITE_EST - V141_AUTHOR_SEC < dp.REPAIR_MIN_SEC

    assert old - V141_SUITE_EST < dp.REPAIR_MIN_SEC
    for left in (V141_LEFT, V142_LEFT):
        new = dp.leaf_share(left, V141_LEAVES, V141_SUITE_EST)
        assert round(new) == 2137, new

        assert new - V141_SUITE_EST - V141_AUTHOR_SEC >= dp.REPAIR_MIN_SEC
        assert new - V141_SUITE_EST - dp.LEAF_AUTHOR_SEC >= dp.REPAIR_MIN_SEC

    assert dp.leaf_share(V142_LEFT, 5, 3000.0) == dp.LEAF_AUTHOR_SEC + dp.REPAIR_WORK_SEC + 3000.0 > dp.LEAF_SHARE_MAX_SEC

    assert dp.leaf_share(10000.0, 2, 480.0) == old_share(10000.0, 2) == 3600.0
    assert dp.leaf_share(3000.0, 1, 0.0) == old_share(3000.0, 1) == 3000.0
    assert dp.leaf_share(500.0, 3, 0.0) == 1200.0
    assert dp.REPAIR_WORK_SEC >= dp.REPAIR_MIN_SEC

@case
def a_large_suite_with_a_tight_window_now_gets_its_repair_and_the_next_leaf_waits():
    tmp, root, world, f = cl.with_world()
    with tmp:

        world.run_sec = 400.0
        world.titles["REQ-3"] = [cl.T3A]
        world.green = {cl.T4A}
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}, "check": True}]
        p, summary, lines = cl.run_pass(world, f, scope=("REQ-3", "REQ-4"), settings={"description_pass_max_sec": 3200})
        assert len(world.implementers) == 1, lines
        assert not any("no repair --" in line for line in lines), lines
        made = world.implementers[0]
        assert made["deadline"] - made["now"] >= 300, made
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after a kept repair" and cl.app_text(root) == "base " + cl.ok(cl.T3A), entry

        assert summary["status"] == "time" and f.description_pass["done"] == ["REQ-3"], summary
        assert ("REQ-4", False) not in world.author_calls

@case
def the_repair_deadline_still_keeps_one_suite_run_and_the_window_end():
    tmp, root, world, f = cl.with_world()
    with tmp:
        world.run_sec = 400.0
        world.titles["REQ-3"] = [cl.T3A]
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}, "check": True}]
        p, summary, lines = cl.run_pass(world, f, settings={"description_pass_max_sec": 3200})
        made = world.implementers[0]
        elapsed = f.spent_before + (made["now"] - f.started)
        suite_then = 1.2 * p.suite_per_file * 2
        assert made["deadline"] - made["now"] <= (p.pass_end - elapsed) - suite_then + 1e-6, (made, p.pass_end)

def defect_output(name: str, titles: list[str]) -> str:
    out = cl.spec_output(name, titles)
    return out.replace("    Error: The requirement says X is shown, but nothing visible matches.",
                       SPEC_DEFECT_LINE + NL + "        at byRole (D:\\w\\backend\\e2e-support\\outsider.js:168:13)")

def bad_spec(titles: list[str]) -> str:
    return cl.spec_text(titles).replace('await see(page, "x");', "await click(page, { " + BAD + ', name: "Password" });')

def defect_world(authored, frames_in_spec=True):
    from agent.test_author import AuthoredFile
    from agent.verify import StepResult

    tmp, root, world, f = cl.with_world()
    problems: list[str] = []

    def author(llm, node, prefix, problem="", current="", log=print):
        world.author_calls.append((str(node.get("id")), bool(problem)))
        problems.append(problem)
        world.tokens += 1000
        text = authored[min(len(problems) - 1, len(authored) - 1)]
        return None if text is None else AuthoredFile(path=f"backend/test-e2e/{prefix}.spec.js", content=text)

    def run_e2e(project_dir, specs, log=print, shared_data=False):
        result = world.run_e2e(project_dir, specs, log, shared_data)
        if len(specs) == 1 and specs[0] == cl.D3 and BAD in (root / cl.D3).read_text(encoding="utf-8"):
            titles = world.spec_titles(cl.D3)
            output = defect_output(Path(cl.D3).name, titles)
            if not frames_in_spec:
                output = "\n".join(line for line in output.splitlines() if "test-e2e\\DESC" not in line or "›" in line)
            return StepResult(False, output, ran=len(titles))
        return result

    return tmp, root, world, f, problems, {"author": author, "run_e2e": run_e2e}

@case
def a_spec_defect_goes_back_to_the_author_once_and_the_rewrite_is_admitted():
    tmp, root, world, f, problems, patch = defect_world([bad_spec([cl.T3A, cl.T3B]), cl.spec_text([cl.T3A, cl.T3B])])
    with tmp:
        world.green = {cl.T3A, cl.T3B}
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False), ("REQ-3", True)], world.author_calls
        assert "SPEC DEFECT" in problems[1] and "DESC-REQ-3.spec.js: Error: SPEC DEFECT" in problems[1], problems[1]
        assert problems[1].startswith("Your spec throws by itself")
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted" and summary["admitted"] == [cl.D3], (entry, lines)
        assert entry["spec_defect_hand_back"]["result"] == "rewritten: green", entry
        assert BAD not in (root / cl.D3).read_text(encoding="utf-8")
        assert world.implementers == [] and cl.D3 in f.revised_specs
        assert {t["title"]: t.get("final") for t in entry["tests"]} == {cl.T3A: "admitted", cl.T3B: "admitted"}, entry["tests"]

@case
def only_once_a_second_spec_defect_goes_the_old_way():
    tmp, root, world, f, problems, patch = defect_world([bad_spec([cl.T3A])])
    with tmp:
        world.steps = [{"status": "test_defect"}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False), ("REQ-3", True)], world.author_calls
        entry = cl.ledger(f)[0]
        assert entry["spec_defect_hand_back"]["result"] == "rewritten: red", entry
        assert entry["outcome"] == "dropped: every test red (test-defect)", entry
        assert cl.D3 not in f.e2e_specs and cl.app_text(root) == "base"

@case
def no_proof_no_hand_back():

    tmp, root, world, f = cl.with_world()
    with tmp:
        world.titles["REQ-3"] = [cl.T3A]
        world.steps = [{"status": "test_defect"}]
        p, summary, lines = cl.run_pass(world, f)
        assert world.author_calls == [("REQ-3", False)] and "spec_defect_hand_back" not in cl.ledger(f)[0]

    tmp, root, world, f, problems, patch = defect_world([bad_spec([cl.T3A])], frames_in_spec=False)
    with tmp:
        world.steps = [{"status": "test_defect"}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False)], world.author_calls

@case
def a_rewrite_that_fails_the_gate_or_e1_leaves_the_first_spec_standing():
    first = bad_spec([cl.T3A])

    tmp, root, world, f, problems, patch = defect_world([first, cl.spec_text([cl.T3X])])
    with tmp:
        world.steps = [{"status": "test_defect"}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert entry["spec_defect_hand_back"]["result"] == "the rewrite keeps no test quoting the description", entry
        dropped = root / ".arc" / "description-pass" / "dropped" / "DESC-REQ-3.spec.js"
        assert BAD in dropped.read_text(encoding="utf-8") and entry["outcome"] == "dropped: every test red (test-defect)"
        assert world.desc_runs(cl.D3) == 1

    tmp, root, world, f, problems, patch = defect_world([first, cl.spec_text([cl.T3A]) + "SYNTAX("])
    with tmp:
        world.steps = [{"status": "test_defect"}]
        patch["static_defects"] = lambda project_dir, files: (
            "does not parse" if "SYNTAX(" in (project_dir / files[0]).read_text(encoding="utf-8") else "")
        on_disk: list[str] = []

        class Watching(cl.implementer_class(world)):
            def implement(self, node, tests, outcome, extra_context=""):
                on_disk.append((root / cl.D3).read_text(encoding="utf-8"))
                return super().implement(node, tests, outcome, extra_context)

        patch["Implementer"] = Watching
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert entry["spec_defect_hand_back"]["result"] == "the rewrite does not parse / load", entry
        assert on_disk == [first], on_disk
        dropped = root / ".arc" / "description-pass" / "dropped" / "DESC-REQ-3.spec.js"
        assert dropped.read_text(encoding="utf-8") == first, dropped.read_text(encoding="utf-8")

    tmp, root, world, f, problems, patch = defect_world([first, None])
    with tmp:
        world.steps = [{"status": "test_defect"}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert cl.ledger(f)[0]["spec_defect_hand_back"]["result"] == "no usable rewrite"

@case
def a_rewrite_that_drops_a_title_books_it_and_its_new_tests_follow_the_normal_path():

    tmp, root, world, f, problems, patch = defect_world([bad_spec([cl.T3A, cl.T3B]), cl.spec_text([cl.T3A, cl.T3X])])
    with tmp:
        world.green = {cl.T3A, cl.T3X}
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        tests = {t["title"]: t for t in cl.ledger(f)[0]["tests"]}
        assert tests[cl.T3A]["final"] == "admitted"
        assert tests[cl.T3B]["final"] == "dropped" and tests[cl.T3B]["reason"] == "spec-defect-rewritten", tests
        assert tests[cl.T3X]["reason"] == "invented-quote", tests
        content = (root / cl.D3).read_text(encoding="utf-8")
        assert cl.T3A in content and cl.T3B not in content and cl.T3X not in content

@case
def no_hand_back_past_the_leaf_share():
    tmp, root, world, f, problems, patch = defect_world([bad_spec([cl.T3A]), cl.spec_text([cl.T3A])])
    with tmp:
        author = patch["author"]

        def slow_author(llm, node, prefix, problem="", current="", log=print):
            world.now += 5000.0
            return author(llm, node, prefix, problem=problem, current=current, log=log)

        patch["author"] = slow_author
        world.green = {cl.T3A}
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False)], world.author_calls
        assert cl.ledger(f)[0]["spec_defect_hand_back"]["result"] == "not handed back: time"

def closing_view(unattempted: list[str], channel: str = "pass_note"):
    from types import SimpleNamespace

    from agent.description_pass import DescriptionPass
    work = [{"id": "B1", "members": ["REQ-1"], "description": "Insert a column into the sheet."},
            {"id": "B2", "members": ["REQ-2"], "description": "The `Add filter` button creates a filter."},
            {"id": "B3", "members": ["REQ-3"], "description": "The `Export` button downloads the sheet."}]
    batch = dict(work[0], later_names=["Add filter", "Export"], earlier_names=[], seed_sentences="SEEDS")
    leaf = {"id": "REQ-1", "name": "Insert column", "description": "Inserting a column keeps the filter working."}
    owner = SimpleNamespace(whole="", work=work, f=SimpleNamespace(unattempted_members=unattempted),
                            tools=SimpleNamespace(note_channel=channel))
    return DescriptionPass.leaf_view(owner, leaf, batch)

SPEC9 = ('const { test } = require("@playwright/test");' + NL + 'test("REQ-1: x", async ({ page }) => {' + NL
         + '  await click(page, "Add filter");' + NL + '  await click(page, "Export");' + NL + '});' + NL)

@case
def the_closing_note_reads_later_as_never_built_like_the_script_gate():
    import agent.description_pass as dp
    from agent.outsider import later_controls

    everything_built = dp.closing_note([])
    assert everything_built.startswith(dp.DESCRIPTION_PASS_NOTE) and dp.LATER_BUILT_NOTE in everything_built
    assert "there is none: any control the requirement text names may be used" in everything_built
    assert "`" not in everything_built and "`" not in dp.closing_note(["a`b"])

    view = closing_view([])
    assert view["unbuilt_names"] == [] and later_controls(SPEC9, view) == [] and view["pass_note"] == everything_built
    view = closing_view(["REQ-3"])
    assert later_controls(SPEC9, view) == ["Export"] and view["unbuilt_names"] == ["Export"]
    assert '"Export" -- never use these.' in view["pass_note"] and "Add filter" not in view["pass_note"], view["pass_note"]

    view = closing_view(["REQ-3"], channel="seed_sentences")
    assert view["seed_sentences"].startswith("SEEDS") and view["seed_sentences"].count(dp.LATER_BUILT_NOTE) == 1
    assert '"Export"' in view["seed_sentences"]

@case
def the_author_prompt_carries_the_closing_reading_once_and_a_batch_prompt_does_not():
    from agent.outsider import author_acceptance
    import agent.description_pass as dp

    class Model:
        last_finish_reason = "stop"

        def chat(self, messages, **options):
            self.user = messages[1]["content"]
            return {"content": ""}

    model = Model()
    author_acceptance(model, closing_view(["REQ-3"]), "DESC-REQ-1", log=lambda line: None)
    assert model.user.count(dp.LATER_BUILT_NOTE) == 1 and '"Export" -- never use these.' in model.user

    assert model.user.index(dp.LATER_BUILT_NOTE) > model.user.find("GENERATED SCENARIOS")
    batch = {"id": "REQ-1", "name": "Insert column", "description": "Inserting a column keeps the filter working.",
             "later_names": ["Add filter", "Export"], "members": ["REQ-1"]}
    author_acceptance(model, batch, "REQ-1", log=lambda line: None)
    assert dp.LATER_BUILT_NOTE not in model.user

@case
def the_review_message_in_the_closing_pass_says_never_built():
    from agent import outsider
    from agent.test_author import AuthoredFile

    view = closing_view(["REQ-3"])
    problems = outsider.deterministic_review(view, AuthoredFile("x", SPEC9), "DESC-REQ-1", log=lambda line: None)
    assert any('"Export", which only a requirement that was never built names' in p for p in problems), problems
    later = [p for p in problems if " names -- that feature is not built" in p]
    assert len(later) == 1 and "LATER" not in later[0], later
    batch = {"id": "REQ-1", "description": "Inserting a column keeps the filter working.", "later_names": ["Export"],
             "members": ["REQ-1"]}
    problems = outsider.deterministic_review(batch, AuthoredFile("x", SPEC9), "REQ-1", log=lambda line: None)
    assert any('"Export", which only a LATER requirement names' in p for p in problems), problems

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
    print(f"{len(CASES) - failed}/{len(CASES)} p141s1 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
