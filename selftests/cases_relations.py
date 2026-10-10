from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases_boilerplate import find_samples

SAMPLES = find_samples() or ROOT / "requirements-samples (not found)"
CASES: list = []
SKIPPED: list[str] = []
SHEET = "hackathon-sheet-platform.yaml"

UNRELATED = ("keep.yaml", "keep-lite.yaml", "bookstack.yaml", "bookstack-lite.yaml", "hackathon-github-platform.yaml",
             "ctrip.yaml", "ticket-booking.yaml")

def case(function):
    CASES.append(function)
    return function

def census(*names: str):
    def register(function):
        missing = [name for name in names if not (SAMPLES / name).is_file()]
        if missing and str(os.environ.get("ARC_REQUIRE_SAMPLES") or "") != "1":
            line = f"SKIP  {function.__name__}: sample(s) {', '.join(missing)} not found in {SAMPLES}"
            SKIPPED.append(line)
            print(line)
            return function
        CASES.append(function)
        return function
    return register

def _batches(name: str) -> list[dict]:
    import yaml
    import main as factory
    from agent.batching import build_batches
    text = (SAMPLES / name).read_text(encoding="utf-8")
    tree = yaml.safe_load(text)
    factory.attach_ancestor_context(tree)
    work = build_batches(tree)
    digest, seeds = factory.requirement_digest(tree), factory.seed_digest(tree)
    for batch in work:
        batch.update(reference_requirements=digest, requirement_text=text, seed_sentences=seeds)
    factory.stamp_batch_names(work)
    return work

def _batch_of(name: str, leaf_id: str) -> dict:
    return next(b for b in _batches(name) if leaf_id in b["members"])

def _leaf(leaf_id: str, name: str, description: str) -> dict:
    leaf = {"id": leaf_id, "name": name, "description": description, "scenarios": []}
    return {"id": leaf_id, "name": name, "description": description, "scenarios": [], "members": [leaf_id], "_leaves": [leaf]}

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

@case
def kept_consistent_wording_is_recognised():
    from agent.outsider import relation_sentences
    positives = [
        "Renaming a page updates every link that refers to it.",
        "On insertion, all later entries and their references shift down together.",
        "Affected entries show the adjusted original expression, and references that cannot be preserved show an error.",
        "After a source value changes, directly and indirectly dependent totals update their results.",
        "Links between cards are kept in sync when a card moves to another board.",
        "移动条目后，所有引用会随之调整。",
    ]
    for text in positives:
        assert relation_sentences(_leaf("REQ-9", "Thing", text)), text

@case
def near_misses_are_not_recognised():
    from agent.outsider import relation_sentences
    negatives = [
        "Each row references a persisted issue number and displays the title, status, author, labels, and update time.",
        "Each entry references its parent and shows its update time.",
        "Each worksheet's data remains independent and is updated after a save.",
        "The home page provides a link whose accessible name is the note title; after an edit the list is updated.",
        "The user can shift the panel to the left.",

        "Each card references its board and shows its title, owner, colour, due date, tags and comments, and the list is updated.",
        "",
    ]
    for text in negatives:
        assert relation_sentences(_leaf("REQ-9", "Thing", text)) == [], text

@case
def only_the_leaves_own_words_count_not_ancestors_or_scenarios():
    from agent.outsider import relation_sentences
    node = _leaf("REQ-9", "Thing", "Users rename a thing.")
    node["ancestor_context"] = "[REQ-1] Folder: references shift together after every change."
    node["_leaves"][0]["scenarios"] = [{"name": "s", "steps": [{"keyword": "THEN", "content": "references shift together"}]}]
    assert relation_sentences(node) == []

@case
def the_block_is_generic_short_and_names_all_four_cases():
    from agent import outsider
    text = outsider.RELATION_HEAD + outsider.RELATION_RULES + outsider.REVIEW_RELATION_NOTE
    for marker in ("(i)", "(ii)", "(iii)", "(iv)", "stays", "two paths", "exactly what the description says"):
        assert marker in text, marker
    for literal in ("formula", "cell", "row", "column", "#REF", "sheet", "workbook", "=", "SUM", "Insert"):
        assert literal.lower() not in text.lower(), literal
    assert len(outsider.RELATION_HEAD + outsider.RELATION_RULES) < 1000 and len(outsider.REVIEW_RELATION_NOTE) < 500

    for prompt in (outsider.SYSTEM_PROMPT, outsider.REVIEW_PROMPT):
        assert "RELATIONS" not in prompt and "two paths" not in prompt and "BOTH ends" not in prompt

@case
def a_synthetic_relation_reaches_writer_and_reviewer_and_a_plain_one_is_unchanged():
    from agent import outsider
    node = _leaf("REQ-9", "Move entries", "Moving an entry shifts every reference to it; references that cannot be preserved show Broken link.")
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, node, "REQ-9", log=lambda *_: None)
    user = llm.calls[0][1]["content"]
    assert "RELATIONS." in user and "(ii)" in user and "REQ-9:" in user and user.rstrip().endswith("Write the spec now.")
    assert user.index("RELATIONS.") > user.index("Moving an entry")

    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, node, "REQ-9", problem="PROBLEM: x", current="y", log=lambda *_: None)
    assert "RELATIONS." in llm.calls[0][1]["content"]

    llm = _LLM("OK")
    outsider.review_acceptance(llm, node, outsider.AuthoredFile(path="backend/test-e2e/REQ-9.spec.js", content="x"), "REQ-9", log=lambda *_: None)
    review = llm.calls[0][1]["content"]
    assert "Report MISSING" in review and "stays while" in review and "shifts every reference" in review

    plain = _leaf("REQ-8", "Rename", "Users rename an entry; the new name is shown after a reload.")
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, plain, "REQ-8", log=lambda *_: None)
    assert llm.calls[0][1]["content"] == (outsider.reference_block(plain) + f"{outsider.describe_node(plain)}\n\n"
                                          "Name the file after the requirement id `REQ-8`. Write the spec now.")
    llm = _LLM("OK")
    outsider.review_acceptance(llm, plain, outsider.AuthoredFile(path="p", content="x"), "REQ-8", log=lambda *_: None)
    assert llm.calls[0][1]["content"] == (outsider.reference_block(plain) + f"{outsider.describe_node(plain)}\n\n"
                                          "The acceptance spec:\nx\n\nCheck it now.")

@case
def a_review_missing_claim_quoting_the_relation_sentence_is_kept():
    from agent import outsider
    sentence = "Moving an entry shifts every reference to it"
    node = _leaf("REQ-9", "Move entries", sentence + "; references that cannot be preserved show Broken link.")
    node["requirement_text"] = node["description"]
    claim = (f'PROBLEM: MISSING | requirement: "{sentence}" | spec: "x" | fix: add a test where the referring entry '
             "stays while the entry it refers to moves")
    kept = outsider.review_acceptance(_LLM(claim), node, outsider.AuthoredFile(path="p", content="x"), "REQ-9", log=lambda *_: None)
    assert kept.startswith("PROBLEM: MISSING"), kept

@census(SHEET)
def the_sheet_leaves_that_lost_tests_get_the_block():
    from agent import outsider
    for leaf_id in ("REQ-2-2-1", "REQ-2-2-2", "REQ-4-2-1"):
        batch = _batch_of(SHEET, leaf_id)
        found = [leaf for leaf, _s in outsider.relation_sentences(batch)]
        assert leaf_id in found, (leaf_id, outsider.relation_sentences(batch))
        llm = _LLM(SPEC_ANSWER)
        outsider.author_acceptance(llm, batch, leaf_id, log=lambda *_: None)
        user = llm.calls[0][1]["content"]
        assert "RELATIONS." in user and user.index("RELATIONS.") > user.index("GENERATED SCENARIOS"), leaf_id

    for batch in _batches(SHEET):
        if all(m.startswith("REQ-1-") for m in batch["members"]):
            assert outsider.relation_block(batch) == "", batch["members"]

@census(*UNRELATED)
def samples_without_relation_wording_are_unchanged():
    from agent import outsider
    for name in UNRELATED:
        for batch in _batches(name):
            assert outsider.relation_sentences(batch) == [], (name, batch["members"], outsider.relation_sentences(batch))
            assert outsider.relation_block(batch) == "" and outsider.review_relation_note(batch) == ""
    keep = _batches("keep.yaml")[0]
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, keep, "K", log=lambda *_: None)
    assert llm.calls[0][1]["content"] == (outsider.reference_block(keep) + f"{outsider.describe_node(keep)}\n\n"
                                          "Name the file after the requirement id `K`. Write the spec now.")

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
    print(f"{len(CASES) - failed}/{len(CASES)} relation cases passed" + (f"; {len(SKIPPED)} SKIPPED (no samples)" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
