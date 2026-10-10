from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases_boilerplate import find_samples

SAMPLES = find_samples() or ROOT / "requirements-samples (not found)"
CASES: list = []
SKIPPED: list[str] = []
GITHUB = "hackathon-github-platform.yaml"
SHEET = "hackathon-sheet-platform.yaml"
TICKET = "ticket-booking.yaml"

PLAIN = ("keep.yaml", "keep-lite.yaml", "bookstack.yaml", "bookstack-lite.yaml", "ctrip.yaml")

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

def _leaf(leaf_id: str, description: str, name: str = "Drafts") -> dict:
    leaf = {"id": leaf_id, "name": name, "description": description, "scenarios": []}
    return {"id": leaf_id, "name": name, "description": description, "scenarios": [], "members": [leaf_id], "_leaves": [leaf]}

DRAFTS = ("The page has a button named “Save draft”. Saving shows the message “Draft saved”. The draft remains listed "
          "after a reload of the page. An empty title is rejected with “Title is required”. Only the author can see the "
          "button “Publish”. Drafts are stored in the database for later use.")

HEAD = """const { test } = require("@playwright/test");
const { click, see } = require("../e2e-support/outsider");
"""

def _spec(*tests: str) -> str:
    return HEAD + "\n".join(tests) + "\n"

def _test(title: str, above: str = "", body: str = 'await page.goto("/"); await see(page, "Draft saved");') -> str:
    return (above + "\n" if above else "") + f'test("{title}", async ({{ page }}) => {{ {body} }});'

class _LLM:
    def __init__(self, answer: str):
        self.answer, self.calls, self.last_finish_reason, self.model = answer, [], "stop", "glm-test"

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return {"content": self.answer}

def _file(body: str, name: str = "REQ-9") -> str:
    return f"=== FILE: backend/test-e2e/{name}.spec.js ===\n{body}=== END FILE ==="

@case
def observable_clauses_are_kept_and_the_rest_dropped():
    from agent import coverage
    found = [s.text for s in coverage.statements_of(DRAFTS)]
    assert len(found) == 5, found
    assert not any("stored in the database" in s for s in found), found
    assert found[0].startswith("The page has a button") and found[2].startswith("The draft remains listed"), found

    seeded = ("The seed contains a draft titled “Plans”. Seed history includes a known message, displayed in full, and "
              "a relative timestamp containing “ago”. The list shows every draft of the author.")
    texts = [s.text for s in coverage.statements_of(seeded)]
    assert not any("titled “Plans”" in t for t in texts) and any("“ago”" in t for t in texts), texts

    quoted = 'A broken file is rejected with "Invalid file format. Import failed." and the list stays unchanged.'
    assert [s.text for s in coverage.statements_of(quoted)] == [quoted], coverage.statements_of(quoted)

    tailed = ("The toolbar shows a button named “Undo”;\nit restores the previous\nvalue after a click.\n\n"
              "Screenshot reference:\n![image](reference/x.png) The screenshot shows a button named “Nothing”.")
    texts = [s.text for s in coverage.statements_of(tailed)]
    assert texts == ["The toolbar shows a button named “Undo”;", "it restores the previous value after a click."], texts

    chinese = "注册页必须提供名为 `下一步` 的提交按钮；刷新后仍保持登录状态并显示用户名。实现页面。"
    texts = [s.text for s in coverage.statements_of(chinese)]
    assert len(texts) == 2 and "下一步" in texts[0], texts

@case
def the_most_concrete_sixteen_are_kept_in_reading_order():
    from agent import coverage
    assert coverage.CAP == 16
    plain = [f"Clicking item {n} opens its page." for n in range(1, 15)]
    strong = [f"The list keeps “Entry {n}” after a reload." for n in range(1, 6)]
    text = " ".join(plain[:7] + strong + plain[7:])
    kept = coverage.statements_of(text)
    assert len(kept) == coverage.CAP, kept
    assert all(any(s.text == t for s in kept) for t in strong), "the persistence statements must survive the cap"
    assert [s.position for s in kept] == sorted(s.position for s in kept), "reading order"
    dropped = [t for t in plain if not any(s.text == t for s in kept)]
    assert dropped == plain[-3:], dropped

    control = coverage.score("On an open item, Merge item and then Confirm merge are buttons.")
    behind = coverage.score("The system atomically stores the item and opens the next page.")
    assert control > coverage.score("Clicking item 1 opens its page.") > behind >= 1, (control, behind)
    assert (coverage.score("The system atomically writes the “Draft” record and opens it.")
            == coverage.score("The system quickly writes the “Draft” record and opens it.") - 2)

@case
def clause_ends_respect_error_values_quotes_keys_and_abbreviations():
    from agent import coverage
    texts = [s.text for s in coverage.statements_of(
        "If the offset leaves the sheet, the formula bar displays =#REF! and the grid displays #REF!. "
        "A broken file is rejected with \"Invalid file format. Import failed.\" After a successful import the new "
        "workbook opens with its rows. Pressing Enter commits the change; pressing Escape cancels an uncommitted change. "
        "Values keep their type, e.g. dates stay dates after a reload of the page.")]
    assert texts[0] == "If the offset leaves the sheet, the formula bar displays =#REF! and the grid displays #REF!.", texts
    assert texts[1] == "A broken file is rejected with \"Invalid file format. Import failed.\"", texts
    assert texts[2].startswith("After a successful import"), texts
    assert "pressing Escape cancels an uncommitted change." in texts, texts
    assert texts[-1].startswith("Values keep their type, e.g. dates"), texts

    assert not coverage.CONTROL.search("users enter a name") and coverage.CONTROL.search("pressing Enter")

@case
def numbers_run_across_the_batch_and_short_leaves_get_no_ledger():
    from agent import coverage
    other = ("The menu has a link named “Archive”. Archived drafts remain hidden after a reload. The archive shows "
             "the date of each draft. Restoring is only possible for the author. A restored draft appears first in the list.")
    batch = _leaf("REQ-1", DRAFTS)
    batch["_leaves"].append({"id": "REQ-2", "name": "Archive", "description": other, "scenarios": []})
    batch["members"] = ["REQ-1", "REQ-2"]
    found = coverage.ledger(batch)
    assert [leaf for leaf, _n in found] == ["REQ-1", "REQ-2"]
    assert [n for _l, numbered in found for n, _t in numbered] == list(range(1, 11)), found
    block = coverage.ledger_block(batch)
    assert "S6: The menu has a link named “Archive”." in block and "  REQ-2:" in block, block

    short = _leaf("REQ-3", "The page has a button named “Save”. Saving the form shows “Saved”. The list keeps it after a "
                           "reload. Only the author sees the button “Edit”.")
    assert len(coverage.statements_of(short["description"])) == 4 and coverage.ledger(short) == []

    assert coverage.ledger({"id": "X", "description": DRAFTS}) == []

@case
def covers_comments_and_titles_count_only_right_above_a_running_test():
    from agent import coverage
    node = _leaf("REQ-9", DRAFTS)
    everything = _spec(_test("REQ-9: save a draft", "// covers S1, S2"),
                       _test("REQ-9: kept after reload (S3)"),
                       _test("REQ-9: empty title", "// covers S4\n"),
                       _test("REQ-9: publish", "// covers S5"))
    assert coverage.uncovered(node, everything) == [] and coverage.not_testable(node, everything) == []

    assert coverage.uncovered(node, _spec(_test("REQ-9: all", "// covers S1-S5"))) == []

    top = HEAD.replace('const { click', "// covers S1, S2, S3, S4, S5\nconst { click") + _test("REQ-9: x") + "\n"
    assert [n for _l, n, _t in coverage.uncovered(node, top)] == [1, 2, 3, 4, 5]

    separated = _spec("// covers S1\nconst name = \"x\";\n" + _test("REQ-9: y"))
    assert 1 in [n for _l, n, _t in coverage.uncovered(node, separated)]

    skipped = _spec("// covers S1\ntest.skip(\"REQ-9: z\", async ({ page }) => {});")
    assert 1 in [n for _l, n, _t in coverage.uncovered(node, skipped)]

    assert len(coverage.uncovered(node, _spec("// not testable -- everything"))) == 5

INTERNAL = ("The server validates every upload before it opens the preview.")
UPLOADS = ("The page has a button named “Upload”. Uploading shows the message “Upload done”. The file remains listed "
           "after a reload of the page. A file over the limit is rejected with “File too large”. Only the owner can "
           "see the button “Delete”. The list shows the name “Size” for every file. The preview keeps its zoom after "
           "a refresh of the page. " + INTERNAL)

@case
def a_not_testable_mark_is_refused_when_testable_by_construction_or_too_many():
    from agent import coverage, outsider
    node = _leaf("REQ-9", UPLOADS)
    found = [t for _l, numbered in coverage.ledger(node) for _n, t in numbered]
    assert len(found) == 8 and found[-1] == INTERNAL, found
    assert not coverage.testable_by_construction(INTERNAL) and all(coverage.testable_by_construction(t) for t in found[:-1])

    genuine = _spec(_test("REQ-9: upload", "// covers S1-S7"), "// not testable: S8 -- validation happens on the server")
    assert coverage.uncovered(node, genuine) == [] and coverage.not_testable(node, genuine) == [8]
    assert coverage.refused_marks(node, genuine) == []
    spec = outsider.AuthoredFile(path="backend/test-e2e/REQ-9.spec.js", content=genuine)
    assert outsider.review_acceptance(_LLM("OK"), node, spec, "REQ-9", log=lambda *_: None) == ""

    quoted = _spec(_test("REQ-9: upload", "// covers S1, S3-S7"), "// not testable: S2 -- messages vanish too fast")
    assert [n for _l, n, _t in coverage.uncovered(node, quoted)] == [2, 8]

    lazy = _spec(_test("REQ-9: open", "// covers S1"), "// not testable: S2-S8 -- needs a real server")
    assert [n for _l, n, _t in coverage.uncovered(node, lazy)] == [2, 3, 4, 5, 6, 7, 8]
    spec = outsider.AuthoredFile(path="backend/test-e2e/REQ-9.spec.js", content=lazy)
    problem = outsider.review_acceptance(_LLM("OK"), node, spec, "REQ-9", log=lambda *_: None)
    assert problem.startswith("PROBLEM: MISSING") and "S2, S3, S4, S5, S6, S7, S8" in problem, problem
    assert "not-testable mark is refused" in problem and "7 of 8 marked, at most 2" in problem, problem

    assert outsider.deterministic_review(node, spec, "REQ-9", log=lambda *_: None) == []

@case
def the_writer_gets_the_ledger_and_the_rule_text_is_generic_and_short():
    from agent import coverage, outsider
    node = _leaf("REQ-9", DRAFTS)
    llm = _LLM(_file(_spec(_test("REQ-9: x"))))
    outsider.author_acceptance(llm, node, "REQ-9", log=lambda *_: None)
    user = llm.calls[0][1]["content"]
    assert "DESCRIPTION LEDGER" in user and "S5: Only the author can see" in user and "// covers S2, S5" in user, user
    assert user.index("DESCRIPTION LEDGER") > user.index("Drafts are stored") and user.rstrip().endswith("Write the spec now.")

    llm = _LLM(_file(_spec(_test("REQ-9: x"))))
    outsider.author_acceptance(llm, node, "REQ-9", problem="PROBLEM: x", current="y", log=lambda *_: None)
    assert "DESCRIPTION LEDGER" in llm.calls[0][1]["content"]
    rules = coverage.LEDGER_HEAD + coverage.LEDGER_RULES
    assert len(rules) < 700, len(rules)
    for literal in ("Q3 Sales", "alice-dev", "workbook", "repository", "#REF", "Sheet1", "ago", "clone", "commit"):
        assert literal not in rules, literal
    for prompt in (outsider.SYSTEM_PROMPT, outsider.REVIEW_PROMPT):
        assert "LEDGER" not in prompt and "covers S" not in prompt

@case
def the_closing_pass_gets_no_ledger():
    from agent import coverage, outsider
    from agent.description_pass import DESCRIPTION_PASS_NOTE
    view = _leaf("REQ-9", DRAFTS)
    view["pass_note"] = DESCRIPTION_PASS_NOTE
    assert coverage.ledger(view) == [] and coverage.ledger_block(view) == ""
    llm = _LLM(_file(_spec(_test("REQ-9: x")), "DESC-REQ-9"))
    outsider.author_acceptance(llm, view, "DESC-REQ-9", log=lambda *_: None)
    user = llm.calls[0][1]["content"]
    assert "DESCRIPTION LEDGER" not in user and user.count("CLOSING PASS") == 1
    spec = outsider.AuthoredFile(path="backend/test-e2e/DESC-REQ-9.spec.js", content=_spec(_test("REQ-9: x")))
    assert "LEDGER" not in outsider.review_acceptance(_LLM("OK"), view, spec, "DESC-REQ-9", log=lambda *_: None)

    bare = _leaf("REQ-9", DRAFTS)
    assert outsider.coverage_block(bare, "DESC-REQ-9") == "" and outsider.coverage_gaps(bare, "x", "DESC-REQ-9") == []

@case
def the_review_sends_uncovered_statements_back_once_after_the_cap():
    from agent import outsider
    node = _leaf("REQ-9", DRAFTS)
    bare = outsider.AuthoredFile(path="backend/test-e2e/REQ-9.spec.js", content=_spec(_test("REQ-9: save", "// covers S1, S2")))
    lines: list[str] = []
    problems = outsider.review_acceptance(_LLM("OK"), node, bare, "REQ-9", log=lines.append)
    assert problems.startswith("PROBLEM: MISSING") and "S3, S4, S5" in problems and "S1," not in problems, problems
    assert "// covers S<n>" in problems and "not testable" in problems
    assert any("ledger: 3 description statement(s)" in line for line in lines), lines
    complete = outsider.AuthoredFile(path=bare.path, content=_spec(_test("REQ-9: save", "// covers S1-S5")))
    assert outsider.review_acceptance(_LLM("OK"), node, complete, "REQ-9", log=lambda *_: None) == ""

    quote = "Saving shows the message “Draft saved”"
    claims = "\n".join(f'PROBLEM: DATA | requirement: "{quote}" | spec: "" | fix: n{i}' for i in range(9))
    problems = outsider.review_acceptance(_LLM(claims), node, bare, "REQ-9", log=lambda *_: None).splitlines()
    assert len(problems) == 9 and problems[-1].startswith("PROBLEM: MISSING | these statements of the DESCRIPTION LEDGER"), problems

    lines = []
    assert outsider.deterministic_review(node, bare, "REQ-9", log=lines.append) == []
    assert any("hint only (not sent back)" in line and "S3, S4, S5" in line for line in lines), lines

@case
def a_batch_rewrites_at_most_once_for_the_ledger():
    import main as factory_module
    from agent import outsider
    node = _leaf("REQ-9", DRAFTS)
    short = _file(_spec(_test("REQ-9: save", "// covers S1")))
    full = _file(_spec(_test("REQ-9: save", "// covers S1-S5")))

    class Model:
        def __init__(self, drafts):
            self.drafts, self.writes, self.last_finish_reason, self.model = list(drafts), 0, "stop", "glm-test"

        def chat(self, messages, **kwargs):
            if messages[0]["content"] == outsider.REVIEW_PROMPT:
                return {"content": "OK"}
            self.writes += 1
            return {"content": self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]}

    names = ("author_acceptance", "review_acceptance", "ensure_helper", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    lines: list[str] = []
    try:
        for name in names[:3]:
            setattr(factory_module, name, getattr(outsider, name))
        factory_module.log = lambda *parts: lines.append(" ".join(str(p) for p in parts))
        for drafts, writes, covered in (([short, short, short], 2, False), ([short, full], 2, True), ([full], 1, True)):
            with tempfile.TemporaryDirectory() as tmp:
                factory = factory_module.Factory.__new__(factory_module.Factory)
                factory.llm, factory.project_dir, factory.revised_specs = Model(drafts), Path(tmp), set()
                factory.author = SimpleNamespace(author=lambda *a, **k: [])
                lines.clear()
                files = factory.author_tests(node, "REQ-9", contract="")
                assert factory.llm.writes == writes, (drafts, factory.llm.writes, lines)
                assert len(files) == 1 and ("S1-S5" in files[0].content) == covered, files
                if not covered:
                    assert any("hint only (not sent back)" in line for line in lines), lines
    finally:
        for name, value in saved.items():
            setattr(factory_module, name, value)

@census(GITHUB, SHEET, TICKET, *PLAIN)
def sample_counts():
    from agent import coverage
    counts = {}
    for name in (GITHUB, SHEET, TICKET) + PLAIN:
        work = _batches(name)
        leaves = [leaf for batch in work for leaf, _n in coverage.ledger(batch)]
        statements = [len(n) for batch in work for _leaf, n in coverage.ledger(batch)]
        blocks = [len(coverage.ledger_block(batch)) for batch in work]
        counts[name] = (len(leaves), sum(statements), max(statements, default=0), max(blocks))

    assert counts[GITHUB][0] >= 40 and counts[SHEET][0] == 24 and counts[TICKET][0] == 2, counts
    assert all(counts[name][2] <= coverage.CAP for name in counts), counts
    assert all(counts[name][3] < 2700 for name in counts), counts
    for name in PLAIN:
        assert counts[name] == (0, 0, 0, 0), (name, counts[name])

@census(GITHUB, SHEET)
def the_statements_are_behaviours_not_boilerplate():
    from agent import coverage
    github = {leaf: [t for _n, t in numbered] for batch in _batches(GITHUB) for leaf, numbered in coverage.ledger(batch)}
    every = [t for texts in github.values() for t in texts]
    assert not any("builds on the persisted objects" in t or "must reuse that context" in t for t in every)

    assert any("relative timestamp" in t for t in github["REQ-4-2-1"]), github["REQ-4-2-1"]
    assert any("reloading that file page retains" in t for t in github["REQ-4-1"]), github["REQ-4-1"]
    assert any("owner/name metadata" in t for t in github["REQ-3-1"]), github["REQ-3-1"]
    assert any("after reload exactly one row" in t for t in github["REQ-2-3"]), github["REQ-2-3"]
    sheet = {leaf: [t for _n, t in numbered] for batch in _batches(SHEET) for leaf, numbered in coverage.ledger(batch)}
    assert any("cannot be preserved" in t for t in sheet["REQ-2-2-1"]), sheet["REQ-2-2-1"]

    assert len(sheet["REQ-4-2-1"]) == 4, sheet["REQ-4-2-1"]
    assert any('"Invalid CSV file format. Import failed."' in t for t in sheet["REQ-1-3-1"]), sheet["REQ-1-3-1"]

@census(GITHUB, *PLAIN)
def a_ledger_batch_prompt_has_it_and_plain_samples_are_unchanged():
    from agent import outsider
    batch = _batch_of(GITHUB, "REQ-4-2-3")
    llm = _LLM(_file(_spec(_test("REQ-4-2-3: x")), "REQ-4-2-3"))
    outsider.author_acceptance(llm, batch, "REQ-4-2-3", log=lambda *_: None)
    user = llm.calls[0][1]["content"]
    assert "DESCRIPTION LEDGER" in user and "  REQ-4-2-3:" in user and "GENERATED SCENARIOS" in user
    assert user.index("DESCRIPTION LEDGER") > user.index("GENERATED SCENARIOS") and user.rstrip().endswith("Write the spec now.")
    for name in PLAIN:
        for batch in _batches(name):
            assert outsider.coverage_block(batch) == "", (name, batch["members"])
    keep = _batches("keep.yaml")[0]
    llm = _LLM(_file(_spec(_test("K: x")), "K"))
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
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed" + (f"; {len(SKIPPED)} skipped (no samples)" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
