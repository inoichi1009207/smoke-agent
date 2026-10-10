from __future__ import annotations

import functools
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES_ENV = "ARC_REQUIREMENT_SAMPLES"
REQUIRE_ENV = "ARC_REQUIRE_SAMPLES"

def find_samples(root: Path = ROOT, environ=os.environ) -> Path | None:
    explicit = str(environ.get(SAMPLES_ENV) or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_dir() else None
    for base in (root, *list(root.parents)[:2]):
        for candidate in (base / "requirements-samples", base / "codex-audit" / "requirements-samples"):
            if candidate.is_dir():
                return candidate
    return None

SAMPLES = find_samples() or ROOT / "requirements-samples (not found)"
CASES = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

def census(*names: str, cases: list | None = None, samples: Path | None = None, environ=os.environ, log=print):
    target = CASES if cases is None else cases
    folder = SAMPLES if samples is None else samples

    def register(function):
        missing = [name for name in names if not (folder / name).is_file()]
        if missing and str(environ.get(REQUIRE_ENV) or "") != "1":
            line = (f"SKIP  {function.__name__}: sample(s) {', '.join(missing)} not found in {folder} "
                    f"(set {SAMPLES_ENV}; {REQUIRE_ENV}=1 turns this into a failure)")
            SKIPPED.append(line)
            log(line)
            return function
        target.append(function)
        return function
    return register

def _sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")

def _tree(text: str) -> dict:
    import yaml
    return yaml.safe_load(text)

def _scenario_count(tree: dict, found: set) -> int:
    from agent.boilerplate import leaves_of, scenarios_of
    return sum(1 for leaf in leaves_of(tree) for i, _s in scenarios_of(leaf) if (str(leaf.get("id")), i) in found)

@functools.lru_cache(maxsize=4)
def _batches(name: str) -> tuple:
    import main as factory
    from agent.batching import build_batches
    text = (SAMPLES / name).read_text(encoding="utf-8")
    tree = _tree(text)
    factory.attach_ancestor_context(tree)
    work = build_batches(tree)
    digest, seeds = factory.requirement_digest(tree), factory.seed_digest(tree)
    for batch in work:
        batch.update(reference_requirements=digest, requirement_text=text, seed_sentences=seeds)
    factory.stamp_batch_names(work)
    return tuple(work)

def _batch_of(name: str, leaf_id: str) -> dict:
    return next(b for b in _batches(name) if leaf_id in b["members"])

GENERATED_WHEN = ("The user opens the home page, clicks the visible `{rec}` entry, and {phrase} with concrete values `a{i}`, "
                  "`b{i}`. Every value is entered through a visible, labelled control; nothing internal is assumed.")
GENERATED_THEN = ("The application exposes the observable result for \"{phrase}\" using the same seeded names and values "
                  "(the seeded record `{rec}` with `c{i}`); failures are shown beside the named control.")

def _generated_tree(leaves: int, concrete_then_in: int = -1) -> dict:
    children = []
    for i in range(leaves):
        phrase = f"the requested workflow number {i}" if i % 2 else "the requested workflow"
        then = GENERATED_THEN.format(phrase=phrase, rec=f"Record {i}", i=i)
        if i == concrete_then_in:
            then = "The invoice total shows 42.00 and the Paid badge appears next to the invoice number in the list."
        children.append({
            "id": f"REQ-{i + 1}", "name": f"Feature {i}", "description": f"Users do thing {i}.",
            "scenarios": [{"name": f"REQ-{i + 1} -{phrase}", "steps": [
                {"keyword": "GIVEN", "content": f"The evaluation seed contains the seeded record `Record {i}` with `v{i}`."},
                {"keyword": "WHEN", "content": GENERATED_WHEN.format(rec=f"Record {i}", phrase=phrase, i=i)},
                {"keyword": "THEN", "content": then},
            ]}],
        })
    return {"id": "ROOT", "name": "R", "children": children}

@case
def k_is_five_or_ten_percent_of_the_leaves():
    from agent.boilerplate import threshold
    assert threshold(2) == 5 and threshold(24) == 5 and threshold(47) == 5 and threshold(50) == 5
    assert threshold(51) == 6 and threshold(133) == 14

@case
def generated_steps_repeated_in_k_leaves_are_boilerplate_and_fewer_are_not():
    from agent.boilerplate import boilerplate_scenarios, leaf_is_all_boilerplate
    tree = _generated_tree(6)
    found = boilerplate_scenarios(tree)
    assert len(found) == 6, found
    assert all(leaf_is_all_boilerplate(leaf, tree) for leaf in tree["children"])

    assert boilerplate_scenarios(_generated_tree(4)) == set()

@case
def one_concrete_step_makes_the_scenario_concrete():
    from agent.boilerplate import boilerplate_scenarios, leaf_is_all_boilerplate
    tree = _generated_tree(7, concrete_then_in=3)
    found = boilerplate_scenarios(tree)
    assert ("REQ-4", 0) not in found and len(found) == 6, found
    assert not leaf_is_all_boilerplate(tree["children"][3], tree)

@case
def short_common_steps_and_distinct_chinese_steps_are_never_boilerplate():
    from agent.boilerplate import boilerplate_scenarios
    short = {"id": "ROOT", "children": [{"id": f"REQ-{i}", "name": f"F{i}", "scenarios": [{"name": f"S{i}", "steps": [
        {"keyword": "WHEN", "content": "The user saves `x`."}, {"keyword": "THEN", "content": "The note is saved."}]}]}
        for i in range(12)]}
    assert boilerplate_scenarios(short) == set()

    cities = "北京上海广州深圳杭州南京成都武汉西安重庆天津苏州"
    chinese = {"id": "ROOT", "children": [{"id": f"REQ-{i}", "name": f"功能{cities[i]}", "scenarios": [{"name": f"场景{cities[i]}", "steps": [
        {"keyword": "WHEN", "content": f"用户选择{cities[i]}站 and opens the booking form, fills every visible field and presses Submit"},
        {"keyword": "THEN", "content": f"系统显示{cities[i]}站 in the confirmation dialog together with the order number and the price"}]}]}
        for i in range(12)]}
    assert boilerplate_scenarios(chinese) == set()

@case
def a_yaml_that_does_not_parse_has_no_boilerplate():
    from agent.boilerplate import batch_report, boilerplate_of_text
    assert boilerplate_of_text("id: ROOT\n  bad: [") == set() and batch_report("not: [yaml", ["REQ-1"]) == []
    assert boilerplate_of_text("") == set()

@census("hackathon-sheet-platform.yaml")
def the_platform_sheet_requirement_is_boilerplate_throughout():
    text = _sample("hackathon-sheet-platform.yaml")
    from agent.boilerplate import boilerplate_scenarios, leaf_is_all_boilerplate, leaves_of, scenarios_of
    tree = _tree(text)
    found = boilerplate_scenarios(tree)

    instances = [(str(leaf["id"]), i) for leaf in leaves_of(tree) for i, _s in scenarios_of(leaf)]
    assert len(instances) == 100 and found == set(instances), len(found)
    assert len({(str(leaf["id"]), str(s.get("name"))) for leaf in leaves_of(tree) for _i, s in scenarios_of(leaf)}) == 58
    assert all(leaf_is_all_boilerplate(leaf, tree) for leaf in leaves_of(tree)) and len(leaves_of(tree)) == 24

@census("hackathon-github-platform.yaml")
def the_platform_github_requirement_has_exactly_its_36_generated_scenarios():
    text = _sample("hackathon-github-platform.yaml")
    from agent.boilerplate import boilerplate_scenarios, leaf_is_all_boilerplate, leaves_of, scenarios_of
    tree = _tree(text)
    found = boilerplate_scenarios(tree)
    assert _scenario_count(tree, found) == 36

    marked = {(str(leaf["id"]), i) for leaf in leaves_of(tree) for i, s in scenarios_of(leaf)
              if any("follows the visible controls for the" in " ".join(str(st.get("content")).split()) for st in s["steps"])}
    assert len(marked) == 36 and found == marked, found ^ marked
    assert not any(leaf_is_all_boilerplate(leaf, tree) for leaf in leaves_of(tree))

CONCRETE_SAMPLES = ("keep.yaml", "keep-lite.yaml", "bookstack.yaml", "bookstack-lite.yaml", "ticket-booking.yaml", "ctrip.yaml")

@census(*CONCRETE_SAMPLES)
def concrete_requirements_have_no_boilerplate():
    from agent.boilerplate import boilerplate_scenarios
    for name in CONCRETE_SAMPLES:
        assert boilerplate_scenarios(_tree(_sample(name))) == set(), name

class _LLM:
    def __init__(self, answer: str):
        self.answer, self.calls, self.last_finish_reason, self.model = answer, [], "stop", "glm-test"

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return {"content": self.answer}

SPEC_ANSWER = """=== FILE: backend/test-e2e/X.spec.js ===
const { test } = require("@playwright/test");
const { click, see } = require("../e2e-support/outsider");
test("X: y", async ({ page }) => { await page.goto("/"); await see(page, "y"); });
=== END FILE ==="""

@census("hackathon-sheet-platform.yaml", "keep.yaml", "hackathon-github-platform.yaml")
def the_generated_note_reaches_a_boilerplate_batch_and_no_other():
    from agent import outsider
    sheet = _batch_of("hackathon-sheet-platform.yaml", "REQ-3-1-3")
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, sheet, "REQ-3-1-3", log=lambda *_: None)
    user = llm.calls[0][1]["content"]
    assert "GENERATED SCENARIOS" in user and "REQ-3-1-3: all 2 scenario(s)" in user and "DESCRIPTION" in user, user[-2500:]
    assert user.index("GENERATED SCENARIOS") > user.index("### REQ-3-1-3") and user.rstrip().endswith("Write the spec now.")

    rules = outsider.BOILERPLATE_HEAD + outsider.BOILERPLATE_RULES + outsider.REVIEW_BOILERPLATE_NOTE
    for literal in ("Q3 Sales", "East", "1200", "alice-dev", "workbook", "repository", "Sheet1"):
        assert literal not in rules, literal

    github = _batch_of("hackathon-github-platform.yaml", "REQ-1-3")
    block = outsider.boilerplate_block(github)
    assert '"REQ-1-3: Change Account Password - Scenario 2"' in block and "Scenario 1" not in block, block

    keep = _batch_of("keep.yaml", _batches("keep.yaml")[0]["members"][0])
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, keep, "K", log=lambda *_: None)
    assert llm.calls[0][1]["content"] == (outsider.reference_block(keep) + f"{outsider.describe_node(keep)}\n\n"
                                          "Name the file after the requirement id `K`. Write the spec now.")
    assert outsider.boilerplate_block(keep) == "" and outsider.review_boilerplate_note(keep) == ""

@census("hackathon-sheet-platform.yaml")
def a_review_claim_resting_only_on_a_boilerplate_step_is_dropped():
    from agent import outsider
    from agent.test_author import AuthoredFile
    node = _batch_of("hackathon-sheet-platform.yaml", "REQ-3-1-3")
    spec = AuthoredFile("backend/test-e2e/REQ-3-1-3.spec.js", 'const { test } = require("@playwright/test");\n'
                        'test("REQ-3-1-3: drag selects a rectangle", async ({ page }) => { await page.goto("/"); });\n')
    boiler = ('PROBLEM: MISSING | requirement: "After the user refreshes the page or reopens the visible destination from the '
              'application entry point" | spec: "await page.goto(\\"/\\");" | fix: add it')
    real = ('PROBLEM: MISSING | requirement: "switching to another worksheet must not overwrite the original worksheet" | '
            'spec: "await page.goto(\\"/\\");" | fix: check it')
    llm = _LLM(boiler + "\n" + real)
    problems = outsider.review_acceptance(llm, node, spec, "REQ-3-1-3", log=lambda *_: None)
    assert real in problems and boiler not in problems, problems
    assert "generated boilerplate" in llm.calls[0][1]["content"]

    concrete = dict(node, requirement_text="")
    assert not outsider.quotes_only_boilerplate("After the user refreshes the page or reopens", concrete)

SHEET_DESCRIPTION_SPEC = '''const { test } = require("@playwright/test");
const { click, fill, press, see, locate, expect } = require("../e2e-support/outsider");

async function enter(page, cell, value) {
  await click(page, { role: "gridcell", name: cell });
  await fill(page, { role: "textbox", name: "Formula bar" }, value);
  await press(page, "Enter");
}

test("REQ-4-1-1: SUM over a range shows the result in the grid and the formula in the formula bar", async ({ page }) => {
  await page.goto("/");
  await click(page, { role: "link", name: "Q3 Sales" });
  await enter(page, "F1", "7");
  await enter(page, "F2", "5");
  await enter(page, "F3", "=SUM(F1:F2)");
  await expect(locate(page, { role: "gridcell", name: "F3" })).toHaveText("12");
  await click(page, { role: "gridcell", name: "F3" });
  await expect(locate(page, { role: "textbox", name: "Formula bar" })).toHaveValue("=SUM(F1:F2)");
  await page.reload();
  await expect(locate(page, { role: "gridcell", name: "F3" })).toHaveText("12");
});

test("REQ-4-1-1: COUNT counts only numeric cells", async ({ page }) => {
  await page.goto("/");
  await click(page, { role: "link", name: "Q3 Sales" });
  const label = "Apples";
  await enter(page, "G1", label);
  await enter(page, "G2", "4");
  await enter(page, "G3", "=count(G1:G2)");
  await see(page, label, locate(page, { role: "gridcell", name: "G1" }));
  await expect(locate(page, { role: "gridcell", name: "G3" })).toHaveText("1");
});
'''

@census("hackathon-sheet-platform.yaml", "hackathon-github-platform.yaml")
def the_deterministic_review_accepts_a_description_derived_test():
    from agent import outsider
    from agent.test_author import AuthoredFile
    node = _batch_of("hackathon-sheet-platform.yaml", "REQ-4-1-1")
    spec = AuthoredFile("backend/test-e2e/REQ-4-1-1.spec.js", SHEET_DESCRIPTION_SPEC)
    assert outsider.deterministic_review(node, spec, "REQ-4-1-1", log=lambda *_: None) == []

    assert outsider.helper_typed_values(SHEET_DESCRIPTION_SPEC) == {"7", "5", "=SUM(F1:F2)", "Apples", "4", "=count(G1:G2)"}
    real = outsider.batch_boilerplate
    try:
        outsider.batch_boilerplate = lambda _node: []
        assert outsider.invented_records(SHEET_DESCRIPTION_SPEC, node) == ["Apples"]
    finally:
        outsider.batch_boilerplate = real
    github = _batch_of("hackathon-github-platform.yaml", "REQ-1-3")
    text = '''const { test } = require("@playwright/test");
const { click, fill, see } = require("../e2e-support/outsider");
test("REQ-1-3: an empty current password is refused", async ({ page }) => {
  await page.goto("/");
  await click(page, "Sign in");
  await fill(page, "Username or email", "alice-dev");
  await fill(page, "Password", "Valid-password-123!");
  await click(page, { role: "button", name: "Sign in" });
  await fill(page, "New password", "Required-password-789!");
  await fill(page, "Confirm password", "Required-password-789!");
  await click(page, { role: "button", name: "Update password" });
  await see(page, "Current password is required");
});
'''
    assert outsider.deterministic_review(github, AuthoredFile("x", text), "REQ-1-3", log=lambda *_: None) == []

@case
def a_helper_that_types_nothing_makes_nothing():
    from agent.outsider import helper_typed_values
    text = '''async function open(page, name) {
  await click(page, { role: "link", name: name });
}
test("REQ-1: x", async ({ page }) => { await open(page, "Ghost record"); });
'''
    assert helper_typed_values(text) == set()

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
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} boilerplate cases passed" + (f"; {len(SKIPPED)} SKIPPED (no samples)" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
