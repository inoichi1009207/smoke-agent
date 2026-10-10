from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases_v145b import NODE, TESTS, State, green, own_red

from agent.edits import EDIT_MIN_CHARS, Hunk, apply_hunks, parse_answer, refusal_text, size_note

NL = chr(10)
CR = chr(13)
CASES = []
BIG = "frontend/src/components/Big.tsx"
SMALL = "frontend/src/pages/EditorPage.tsx"

def case(function):
    CASES.append(function)
    return function

def edit(path: str, *pairs: tuple[str, str], end: bool = True) -> str:
    lines = [f"=== EDIT: {path} ==="]
    for search, replace in pairs:
        lines += ["<<<<<<< SEARCH", search, "=======", replace, ">>>>>>> REPLACE"]
    if end:
        lines.append("=== END EDIT ===")
    return NL.join(lines)

def whole(path: str, body: str, end: bool = True) -> str:
    return NL.join([f"=== FILE: {path} ===", body] + (["=== END FILE ==="] if end else []))

def big_source(lines: int = 400) -> str:
    body = [f"  const value{i} = compute({i}, 'row {i} of the grid');" for i in range(lines)]
    return NL.join(["export function Big() {"] + body + ["  return null;", "}"]) + NL

@case
def an_exact_pair_replaces_its_lines_only():
    text = "a" + NL + "b" + NL + "c" + NL
    new, results = apply_hunks(text, [Hunk("b", "B1" + NL + "B2", 1)])
    assert new == "a" + NL + "B1" + NL + "B2" + NL + "c" + NL, repr(new)
    assert [r.status for r in results] == ["exact"] and results[0].line == 2

@case
def trailing_whitespace_and_indentation_are_tolerated_and_reindented():
    text = NL.join(["function f() {", "    if (x) {   ", "        go();", "    }", "}"]) + NL
    new, results = apply_hunks(text, [Hunk("if (x) {" + NL + "    go();" + NL + "}", "if (y) {" + NL + "    stop();" + NL + "}", 1)])
    assert results[0].status == "indent", results
    assert new == NL.join(["function f() {", "    if (y) {", "        stop();", "    }", "}"]) + NL, repr(new)
    new, results = apply_hunks("x = 1;   " + NL + "y = 2;" + NL, [Hunk("x = 1;" + NL + "y = 2;", "x = 3;" + NL + "y = 2;", 1)])
    assert results[0].status == "whitespace" and new == "x = 3;" + NL + "y = 2;" + NL, (results, new)

@case
def blank_lines_inside_the_search_are_not_guessed_and_surrounding_ones_do_not_leak():

    text = NL.join(["one();", "", "two();", "three();"]) + NL
    new, results = apply_hunks(text, [Hunk("one();" + NL + "two();", "ONE();" + NL + "TWO();", 1)])
    assert new == text and results[0].status == "refused" and "    2| " in results[0].snippet, results

    new, results = apply_hunks("a" + NL + "b" + NL, [Hunk(NL + "b", NL + "B", 1)])
    assert results[0].applied and new == "a" + NL + "B" + NL, (results, new)
    new, results = apply_hunks("a" + NL + NL + "b" + NL, [Hunk(NL + "b", NL + "B", 1)])
    assert results[0].status == "exact" and new == "a" + NL + NL + "B" + NL, (results, new)

@case
def codex_r1_counterexamples_never_land_in_the_wrong_place():

    new, results = apply_hunks("1| foo;" + NL + "foo;" + NL, [Hunk("1| foo;", "1| bar;", 1)])
    assert new == "1| bar;" + NL + "foo;" + NL and results[0].status == "exact", (results, new)

    text = NL.join(["go();", "function f() {", "    go();", "}"]) + NL
    new, results = apply_hunks(text, [Hunk("go();", "stop();", 1), Hunk("go();", "again();", 2)])
    assert [r.status for r in results] == ["exact", "refused"] and "overlaps pair 1" in results[1].reason, results
    assert new == NL.join(["stop();", "function f() {", "    go();", "}"]) + NL, repr(new)

    for original, part in (("const aaaaaaaa = 1;" + NL, "aaaaaaa"), ("const userIdentity = 1;" + NL, "Identity"),
                           ("x(userId);" + NL, "Id")):
        new, results = apply_hunks(original, [Hunk(part, "Z", 1)])
        assert new == original and results[0].status == "refused", (original, part, results)

    new, results = apply_hunks("  a" + NL + "      b" + NL, [Hunk("a" + NL + " b", "a" + NL + " c", 1)])
    assert results[0].status == "refused" and new == "  a" + NL + "      b" + NL, (results, new)

    mixed = "a" + CR + NL + "b" + NL
    new, results = apply_hunks(mixed, [Hunk("MISSING line", "X", 1)])
    assert new == mixed and results[0].status == "refused"

@case
def a_pair_that_matches_nowhere_is_refused_with_the_most_similar_current_lines():
    text = big_source(60)
    search = NL.join([f"  const value{i} = compute({i}, 'row {i} of a grid');" for i in (30, 31, 32)])
    new, results = apply_hunks(text, [Hunk(search, "X", 1)])
    assert new == text, "a refused pair changes nothing"
    r = results[0]
    assert r.status == "refused" and "not in the current file" in r.reason and "most similar lines" in r.reason, r
    assert "   32| " in r.snippet and "row 31 of the grid" in r.snippet, r.snippet
    report = refusal_text(BIG, results)
    assert "pair(s) 1 NOT applied" in report and "nothing of this block was applied" in report and r.snippet in report

@case
def a_pair_that_matches_several_places_is_refused_not_guessed():
    text = NL.join(["a();", "b();", "x", "a();", "b();"]) + NL
    new, results = apply_hunks(text, [Hunk("a();" + NL + "b();", "c();", 1)])
    assert new == text and results[0].status == "refused", results
    assert "match 2 places (lines 1, 4)" in results[0].reason, results[0].reason

@case
def several_pairs_apply_in_order_and_a_later_one_sees_the_earlier_result():
    text = NL.join(["import a;", "", "f();", "g();"]) + NL
    pairs = [Hunk("import a;", "import a;" + NL + "import b;", 1),
             Hunk("import b;", "import b2;", 2),
             Hunk("g();", "", 3)]
    new, results = apply_hunks(text, pairs)
    assert [r.status for r in results] == ["exact", "exact", "exact"], results
    assert new == NL.join(["import a;", "import b2;", "", "f();"]) + NL, repr(new)

@case
def an_overlapping_pair_is_refused_and_named_the_others_still_apply():
    text = NL.join(["l1", "l2", "l3", "l4", "l5"]) + NL
    pairs = [Hunk("l2" + NL + "l3", "L2" + NL + "L3", 1), Hunk("l3" + NL + "l4", "X", 2), Hunk("l5", "L5", 3)]
    new, results = apply_hunks(text, pairs)
    assert [r.status for r in results] == ["exact", "refused", "exact"], results
    assert "overlaps pair 1" in results[1].reason and "ONE pair" in results[1].reason, results[1].reason
    assert new == NL.join(["l1", "L2", "L3", "l4", "L5"]) + NL, repr(new)

@case
def a_crlf_file_is_matched_and_stays_crlf():
    text = "a" + CR + NL + "b" + CR + NL + "c" + CR + NL
    new, results = apply_hunks(text, [Hunk("b", "B", 1)])
    assert results[0].status == "exact" and new == "a" + CR + NL + "B" + CR + NL + "c" + CR + NL, repr(new)

    new, results = apply_hunks("a" + NL + "b" + NL, [Hunk("a" + CR + NL + "b", "x", 1)])
    assert results[0].applied and new == "x" + NL, (results, new)

@case
def empty_search_creates_only_a_missing_file_and_a_missing_file_is_not_edited():
    new, results = apply_hunks(None, [Hunk("", "export const x = 1;", 1)])
    assert new == "export const x = 1;" + NL and results[0].status == "created"
    new, results = apply_hunks(None, [Hunk("const a = 1;", "const a = 2;", 1)])
    assert new is None and results[0].status == "refused" and "does not exist" in results[0].reason
    new, results = apply_hunks("x" + NL, [Hunk("", "y", 1)])
    assert new == "x" + NL and results[0].status == "refused" and "SEARCH part is empty" in results[0].reason

@case
def a_unique_part_of_one_line_is_replaced_in_place():
    text = "const total = sum(a, b); // keep" + NL + "other();" + NL
    new, results = apply_hunks(text, [Hunk("sum(a, b)", "sum(a, b, c)", 1)])
    assert results[0].status == "substring" and new == "const total = sum(a, b, c); // keep" + NL + "other();" + NL, new

    new, results = apply_hunks("x(1); x(1);" + NL, [Hunk("x(1)", "y", 1)])
    assert results[0].status == "refused"

@case
def a_pair_copied_with_the_snippet_gutter_still_applies():

    text = NL.join(["a();", "b();", "c();"]) + NL
    new, results = apply_hunks(text, [Hunk("    2| b();" + NL + "    3| c();", "    2| B();" + NL + "    3| c();", 1)])
    assert results[0].status == "exact" and new == NL.join(["a();", "B();", "c();"]) + NL, (results, new)

    new, results = apply_hunks("x| y" + NL, [Hunk("1| q" + NL + "zz", "w", 1)])
    assert results[0].status == "refused"

@case
def an_identical_pair_is_a_no_op():
    new, results = apply_hunks("a" + NL, [Hunk("a", "a", 1)])
    assert new == "a" + NL and results[0].status == "noop" and results[0].applied

@case
def file_and_edit_blocks_parse_in_answer_order():
    text = whole("frontend/src/a.ts", "export const a = 1;") + NL + edit(BIG, ("x", "y")) + NL + whole("frontend/src/b.ts", "b")
    parsed = parse_answer(text, complete=True)
    assert [(b.kind, b.path) for b in parsed.blocks] == [("FILE", "frontend/src/a.ts"), ("EDIT", BIG), ("FILE", "frontend/src/b.ts")]
    assert parsed.blocks[1].hunks[0].search == "x" and parsed.blocks[1].hunks[0].replace == "y"
    assert not parsed.dropped and parsed.cut_path is None and parsed.complete_end == len(text)

@case
def a_cut_file_block_is_dropped_and_reported():
    text = whole("frontend/src/a.ts", "a") + NL + whole(SMALL, "export default function E() {", end=False)
    parsed = parse_answer(text, complete=False)
    assert [b.path for b in parsed.blocks] == ["frontend/src/a.ts"] and parsed.cut_path == SMALL
    assert parsed.dropped and parsed.dropped[0][0] == SMALL and "cut off by the output limit" in parsed.dropped[0][1]
    assert text[: parsed.complete_end].endswith("=== END FILE ===")

    assert [b.path for b in parse_answer(text, complete=True).blocks] == ["frontend/src/a.ts", SMALL]

@case
def a_cut_edit_block_keeps_its_complete_pairs_and_reports_the_lost_one():
    text = edit(BIG, ("a", "A"), ("b", "B"), end=False) + NL + "<<<<<<< SEARCH" + NL + "c" + NL + "======="
    parsed = parse_answer(text, complete=False)
    assert len(parsed.blocks) == 1 and [h.search for h in parsed.blocks[0].hunks] == ["a", "b"]
    assert parsed.blocks[0].cut and parsed.blocks[0].lost_hunk and parsed.cut_path == BIG
    assert "pair 3 of the EDIT block" in parsed.dropped[0][1] and "pairs before it are applied" in parsed.dropped[0][1]
    shown = text[: parsed.complete_end]
    assert shown.rstrip().endswith(">>>>>>> REPLACE") and shown.count(">>>>>>> REPLACE") == 2, shown

@case
def codex_r2_counterexamples_never_land_in_the_wrong_place():

    new, results = apply_hunks("x();" + NL + "foo();" + NL, [Hunk("    1| foo();", "    1| bar();", 1)])
    assert new == "x();" + NL + "foo();" + NL and results[0].status == "refused", (results, new)

    new, results = apply_hunks("a();" + NL + "b();" + NL, [Hunk("    2| b();", "    2| B();", 1)])
    assert new == "a();" + NL + "B();" + NL, (results, new)

    text = "go();" + NL + "next();" + NL + "  go();" + NL
    new, results = apply_hunks(text, [Hunk("go(); " + NL + "next();", "done();" + NL + "next();", 1), Hunk("go();", "wrong();", 2)])
    assert [r.status for r in results] == ["whitespace", "refused"] and "overlaps" in results[1].reason, results
    assert new == "done();" + NL + "next();" + NL + "  go();" + NL, repr(new)

    after_first, _r = apply_hunks(text, [Hunk("go();" + NL + "next();", "stop();" + NL + "next();", 1)])
    new, results = apply_hunks(after_first, [Hunk("go();", "again();", 1)], baseline=text)
    assert results[0].status == "refused" and new == after_first, (results, new)

    new, results = apply_hunks(NL + "foo();" + NL, [Hunk(NL + "foo(); " + NL, "bar();", 1)])
    assert new == "bar();" + NL, (results, repr(new))

    new, results = apply_hunks("const n = sum(alpha,beta);" + NL, [Hunk("sum(alpha,beta)", NL + "sum(alpha,gamma)" + NL, 1)])
    assert results[0].status == "refused", results
    new, results = apply_hunks("x = 'aaaaaaaaa';" + NL, [Hunk("aaaaaaaa", "Z", 1)])
    assert results[0].status == "refused", results

    new, results = apply_hunks("a" + CR + NL + "b" + NL + "c" + CR + NL, [Hunk("b", "B", 1)])
    assert new == "a" + CR + NL + "B" + NL + "c" + CR + NL, repr(new)

@case
def every_part_of_an_edit_block_that_is_not_a_pair_is_reported():

    text = NL.join(["=== EDIT: x.js ===", "<<<<<<< SEARCH", "a", "=======", "A", ">>>>>>> REPLACE",
                    "<<<<<<< SEARC", "b", "=======", "B", ">>>>>>> REPLACE", "=== END EDIT ==="])
    parsed = parse_answer(text, complete=True)
    assert len(parsed.blocks[0].hunks) == 1 and any("outside any SEARCH/REPLACE pair were ignored" in s for _p, s in parsed.dropped), parsed.dropped

    text = NL.join(["=== EDIT: x.js ===", "<<<<<<< SEARCH", "a", "=======", "A", "explanation that is not code",
                    "<<<<<<< SEARCH", "b", "=======", "B", ">>>>>>> REPLACE", "=== END EDIT ==="])
    parsed = parse_answer(text, complete=True)
    assert [(h.search, h.replace) for h in parsed.blocks[0].hunks] == [("b", "B")], parsed.blocks
    assert any("had no `>>>>>>> REPLACE` line before the next" in s and "NOT applied" in s for _p, s in parsed.dropped), parsed.dropped

    parsed = parse_answer(NL.join(["=== FILE: a.js ===", "a", "=== END OF FILE ===", "=== EDIT: b.js ===", "<<<<<<< SEARCH",
                                   "b", "=======", "c", ">>>>>>> REPLACE", "=== END EDIT ==="]), complete=True)
    assert parsed.blocks[0].body == "a" + NL and parsed.edit_headers == 1

@case
def a_long_report_names_every_item_it_cannot_show():
    from agent.edits import clip

    items = [f"EDIT f{i}.js: pair(s) 1 NOT applied" + NL + "x" * 500 for i in range(30)]
    text = clip(items, 2000)
    assert len(text) < 2000 + 30 * 200 and "... and" in text and all(f"EDIT f{i}.js" in text for i in range(30)), text[-400:]

@case
def a_forgotten_end_marker_ends_at_the_next_header_and_outside_paths_are_dropped():
    text = edit(BIG, ("a", "A"), end=False) + NL + whole("../../etc/passwd", "x") + NL + whole("frontend/src/c.ts", "c")
    parsed = parse_answer(text, complete=True)
    assert [b.path for b in parsed.blocks] == [BIG, "frontend/src/c.ts"], parsed.blocks
    assert any("outside the project" in sentence for _p, sentence in parsed.dropped)

@case
def the_size_note_marks_large_and_god_files():
    assert size_note("x" * EDIT_MIN_CHARS) == ""
    note = size_note(big_source(400))
    assert "change it with EDIT blocks" in note and "over 800 lines" not in note, note
    assert "over 800 lines" in size_note(big_source(900))

class Model:

    def __init__(self, answers, follow=()):
        self.answers = list(answers)
        self.follow = list(follow)
        self.calls: list[tuple[str, list]] = []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        tag = str(options.get("tag", ""))
        step = tag.split(":", 2)[2] if tag.count(":") >= 2 else tag
        self.calls.append((step, messages))
        assert len(self.calls) <= 40, [c[0] for c in self.calls]
        if ":diagnose" in tag or tag.endswith(":decide"):
            self.last_finish_reason = "stop"
            return {"content": SMALL + " -- render what the spec looks for"}
        source = self.follow if step.startswith(("cont", "redo")) else self.answers
        content, finish = source.pop(0) if len(source) > 1 else source[0]
        self.last_finish_reason = finish
        return {"content": content}

    def steps(self) -> list[str]:
        return [step for step, _m in self.calls]

def project(tmp: str, big_lines: int = 400) -> Path:
    root = Path(tmp)
    (root / "frontend/src/components").mkdir(parents=True)
    (root / "frontend/src/pages").mkdir(parents=True)
    (root / BIG).write_text(big_source(big_lines), encoding="utf-8")
    (root / SMALL).write_text("export default function E() { return null; }" + NL, encoding="utf-8")
    return root

def implementer(model, root: Path, outcomes, logs):
    from agent.codegen import CodegenImplementer

    pending = list(outcomes)
    made = []

    def check():
        made.append(1)
        return pending.pop(0) if len(pending) > 1 else pending[0]

    implementer = CodegenImplementer(model, root, check, log=logs.append, max_rounds=3, extra_rounds=0)
    implementer.checks = lambda: len(made)
    return implementer

OLD7 = "  const value7 = compute(7, 'row 7 of the grid');"
NEW7 = "  const value7 = compute(7, 'row seven');"

@case
def an_edit_of_a_large_file_is_applied_and_checked_once():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        (root / "frontend/src/api").mkdir(parents=True)
        (root / "frontend/src/api/store.ts").write_text(big_source(300), encoding="utf-8")
        model = Model([(edit(BIG, (OLD7, NEW7)), "stop")])
        logs: list[str] = []
        result = implementer(model, root, [green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed" and BIG in result.written, (result, logs[-6:])
        assert "--- frontend/src/api/store.ts ---" in model.calls[0][1][1]["content"]
        text = (root / BIG).read_text(encoding="utf-8")
        assert NEW7 in text and OLD7 not in text and text.count(NL) == big_source().count(NL)

        gen = model.calls[0][1]
        assert "=== EDIT: <project-relative path> ===" in gen[0]["content"] and "<<<<<<< SEARCH" in gen[0]["content"]
        assert f"--- {BIG} --- [" in gen[1]["content"] and "change it with EDIT blocks" in gen[1]["content"]

        assert all(f"--- {path} --- [" not in gen[1]["content"] for path in ("frontend/src/api/store.ts", "frontend/src/main.tsx"))
        assert any("EDIT " + BIG + ": 1 pair(s) exact 1" in line for line in logs), logs

@case
def an_edit_of_a_file_shown_only_in_part_is_not_a_blind_rewrite():

    from agent.codegen import MAX_FILE_CHARS

    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp, big_lines=900)
        assert len((root / BIG).read_text(encoding="utf-8")) > MAX_FILE_CHARS
        model = Model([(edit(BIG, (OLD7, NEW7)), "stop")])
        logs: list[str] = []
        result = implementer(model, root, [green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed", (result, logs[-8:])
        text = (root / BIG).read_text(encoding="utf-8")
        assert NEW7 in text and "row 899 of the grid" in text, "the unseen tail stays"
        assert not any("refused blind rewrite" in line for line in logs), logs
        assert "over 800 lines: move a self-contained part" in model.calls[0][1][1]["content"]

@case
def a_refused_pair_is_asked_again_with_the_current_lines_before_the_check():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        wrong = "  const value7 = compute(7, 'row 7 of a grid');"
        model = Model([(edit(BIG, (wrong, NEW7)) + NL + whole(SMALL, "export default function E() { return 1; }"), "stop")],
                      follow=[(edit(BIG, (OLD7, NEW7)), "stop")])
        logs: list[str] = []
        imp = implementer(model, root, [green()], logs)
        result = imp.implement(NODE, TESTS, own_red())
        assert result.status == "passed", (result, logs[-8:])
        assert model.steps() == ["gen0", "redo0"], model.steps()
        redo = model.calls[1][1]
        assert [m["role"] for m in redo] == ["system", "user", "assistant", "user"]
        assert redo[1]["content"] == model.calls[0][1][1]["content"], "the follow-up repeats the prompt (cache prefix)"
        assert "    9| " + OLD7 in redo[3]["content"] and "1 change(s) NOT applied" in redo[3]["content"], redo[3]["content"]
        assert "one SEARCH/REPLACE pair per change, in the order listed" in redo[3]["content"]
        assert NEW7 in (root / BIG).read_text(encoding="utf-8")
        assert "return 1;" in (root / SMALL).read_text(encoding="utf-8")

@case
def a_pair_still_refused_after_the_redo_reaches_the_next_prompt():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        wrong = "  const nothing = here();"
        model = Model([(edit(BIG, (OLD7, NEW7), (wrong, "x")), "stop"), (whole(SMALL, "export default function E() { return 2; }"), "stop")],
                      follow=[(edit(BIG, (wrong, "x")), "stop")])
        logs: list[str] = []
        result = implementer(model, root, [own_red(), green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed", (result, logs[-8:])
        assert NEW7 in (root / BIG).read_text(encoding="utf-8"), "the pair that matched was applied"
        repair = [m for step, m in model.calls if step.startswith("repair")][0][1]["content"]
        assert "NOT applied from your previous answer" in repair and "1 change(s) NOT applied" in repair, repair[-2500:]

@case
def an_answer_whose_pairs_all_fail_is_not_checked_and_costs_no_round():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        wrong = "  const nothing = here();"
        model = Model([(edit(BIG, (wrong, "x")), "stop"), (edit(BIG, (OLD7, NEW7)), "stop")], follow=[(edit(BIG, (wrong, "x")), "stop")])
        logs: list[str] = []
        imp = implementer(model, root, [green()], logs)
        result = imp.implement(NODE, TESTS, own_red())
        assert result.status == "passed" and result.test_runs == 1, (result, logs[-8:])
        assert imp.checks() == 1, "the answer that changed nothing was not checked"
        second = [m for step, m in model.calls if step == "gen0"][1][1]["content"]
        assert "none of its blocks could be applied" in second and "not in the current file" in second, second[-2000:]

@case
def an_answer_cut_after_complete_blocks_is_continued_before_its_check():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        first = whole("frontend/src/components/New.tsx", "export const N = 1;") + NL + whole(SMALL, "export default function E() {", end=False)
        model = Model([(first, "length")], follow=[(edit(BIG, (OLD7, NEW7)) + NL + whole(SMALL, "export default function E() { return 3; }"), "stop")])
        logs: list[str] = []
        imp = implementer(model, root, [green()], logs)
        result = imp.implement(NODE, TESTS, own_red())
        assert result.status == "passed" and imp.checks() == 1, (result, logs[-8:])
        assert model.steps() == ["gen0", "cont0"], model.steps()
        cont = model.calls[1][1]
        assert cont[2]["content"].endswith("=== END FILE ===") and "return 3" not in cont[2]["content"]
        assert f"while writing {SMALL}" in cont[3]["content"] and "REMAINING changes only" in cont[3]["content"]
        assert (root / "frontend/src/components/New.tsx").is_file() and "return 3;" in (root / SMALL).read_text(encoding="utf-8")
        assert NEW7 in (root / BIG).read_text(encoding="utf-8")

@case
def without_continuations_left_the_dropped_block_is_reported_to_the_next_round():
    from agent import codegen

    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        first = whole("frontend/src/components/New.tsx", "export const N = 1;") + NL + whole(SMALL, "export default function E() {", end=False)
        model = Model([(first, "length"), (whole(SMALL, "export default function E() { return 4; }"), "stop")])
        logs: list[str] = []
        saved = codegen.CONTINUE_FREE
        codegen.CONTINUE_FREE = 0
        try:
            result = implementer(model, root, [own_red(), green()], logs).implement(NODE, TESTS, own_red())
        finally:
            codegen.CONTINUE_FREE = saved
        assert result.status == "passed", (result, logs[-8:])
        repair = [m for step, m in model.calls if step.startswith("repair")][0][1]["content"]
        assert f"the FILE block of {SMALL} (cut off by the output limit" in repair, repair[-2000:]

@case
def an_edit_header_without_a_pair_never_ends_up_inside_the_file_before_it():

    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answer = NL.join([whole(SMALL, "export default function E() { return 8; }"), "=== EDIT: " + BIG + " ===",
                          "<<<<<<< SEARCH", OLD7, "=======", NEW7, "=== END EDIT ==="])
        model = Model([(answer, "stop")])
        logs: list[str] = []
        result = implementer(model, root, [green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed"
        assert (root / SMALL).read_text(encoding="utf-8") == "export default function E() { return 8; }" + NL
        assert NEW7 not in (root / BIG).read_text(encoding="utf-8")
        assert any("not applied from this answer" in line and "has no `>>>>>>> REPLACE` line" in line for line in logs), logs[-6:]

@case
def a_file_block_without_its_end_marker_never_swallows_a_following_edit_block():

    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answer = NL.join(["=== FILE: " + SMALL + " ===", "export default function E() { return 11; }",
                          "=== EDIT: " + BIG + " ===", "<<<<<<< SEARCH", OLD7, "=======", NEW7, ">>>>>>> REPLACE", "=== END EDIT ==="])
        model = Model([(answer, "stop")])
        result = implementer(model, root, [green()], []).implement(NODE, TESTS, own_red())
        assert result.status == "passed"
        assert (root / SMALL).read_text(encoding="utf-8") == "export default function E() { return 11; }" + NL
        assert NEW7 in (root / BIG).read_text(encoding="utf-8")

@case
def a_redo_of_no_op_pairs_does_not_settle_the_refused_changes():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        wrong_a, wrong_b = "  const missingA = 1;", "  const missingB = 2;"
        model = Model([(edit(BIG, (wrong_a, "A"), (wrong_b, "B")), "stop"), (whole(SMALL, "export default function E() { return 12; }"), "stop")],
                      follow=[(edit(BIG, (OLD7, OLD7), (OLD7, OLD7)), "stop")])
        logs: list[str] = []
        result = implementer(model, root, [own_red(), green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed", (result, logs[-6:])
        later = [m for step, m in model.calls if step.startswith(("gen", "repair"))][1][1]["content"]
        assert "2 change(s) NOT applied" in later and "the redo pair for it changed nothing" in later, later[-2500:]

@case
def an_edit_through_a_differently_cased_folder_is_refused():

    import os as _os

    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        (root / "backend/src/database").mkdir(parents=True)
        (root / "backend/src/database/index.js").write_text("module.exports = 1;" + NL, encoding="utf-8")
        alias = "Backend/src/database/index.js"
        case_insensitive = _os.path.exists(str(root / alias))
        model = Model([(edit(alias, ("module.exports = 1;", "module.exports = 2;")), "stop"),
                       (whole(SMALL, "export default function E() { return 13; }"), "stop")])
        logs: list[str] = []
        result = implementer(model, root, [green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed"
        assert (root / "backend/src/database/index.js").read_text(encoding="utf-8") == "module.exports = 1;" + NL
        if case_insensitive:
            assert any("spelt backend" in line for line in logs), logs[-6:]

@case
def an_edit_of_a_fixed_hidden_or_unreadable_file_is_refused_before_it_is_read():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        (root / "backend/src/database").mkdir(parents=True)
        (root / "backend/src/database/index.js").write_text("module.exports = require('./db_runtime');" + NL, encoding="utf-8")
        (root / ".env").write_text("A=1" + NL + "SECRET-LINE=x" + NL, encoding="utf-8")
        (root / "frontend/src/blob.ts").write_bytes(b"\xff\xfe not utf-8" + NL.encode())
        answer = NL.join([edit("backend/src/database/index.js", ("nothing", "x")), edit("frontend/src/blob.ts", ("", "export const b = 1;")),
                          edit(".env", ("A=1", "A=2")), whole(SMALL, "export default function E() { return 9; }")])
        model = Model([(answer, "stop"), (whole(SMALL, "export default function E() { return 10; }"), "stop")])
        logs: list[str] = []
        result = implementer(model, root, [own_red(), green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed"
        assert "redo0" not in model.steps(), "nothing to redo: those blocks are refused whole"
        assert (root / "frontend/src/blob.ts").read_bytes().startswith(b"\xff\xfe"), "an unreadable file is not overwritten"
        repair = [m for step, m in model.calls if step.startswith("repair")][0][1]["content"]
        assert "EDIT backend/src/database/index.js: not applied at all: a fixed or hidden file" in repair, repair[-1500:]
        assert "EDIT frontend/src/blob.ts: not applied at all: the file cannot be read as UTF-8" in repair
        assert "EDIT .env: not applied at all: a fixed or hidden file" in repair
        assert "SECRET-LINE" not in repair and not any("SECRET-LINE" in line for line in logs), "a hidden file is never read"

@case
def an_answer_of_no_op_pairs_on_a_green_check_is_a_pass_not_a_wasted_round():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        model = Model([(edit(BIG, (OLD7, OLD7)), "stop")])
        logs: list[str] = []
        imp = implementer(model, root, [green()], logs)
        result = imp.implement(NODE, TESTS, green())
        assert result.status == "passed" and imp.checks() == 0, (result, logs[-5:])

@case
def a_redo_that_asks_for_files_gets_them_shown():
    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        (root / "frontend/src/components/Other.tsx").write_text("export const O = 1;" + NL, encoding="utf-8")
        wrong = "  const nothing = here();"
        model = Model([(edit(BIG, (wrong, "x")), "stop"), (edit(BIG, (OLD7, NEW7)), "stop")],
                      follow=[("=== NEED FILES ===" + NL + "frontend/src/components/Other.tsx" + NL + "=== END NEED FILES ===", "stop")])
        logs: list[str] = []
        imp = implementer(model, root, [green()], logs)
        result = imp.implement(NODE, TESTS, own_red())
        assert result.status == "passed", (result, logs[-6:])
        second = [m for step, m in model.calls if step == "gen0"][1][1]["content"]
        assert "your redo answer asked for frontend/src/components/Other.tsx; they are shown in full now" in second, second[-1500:]

@case
def a_plain_whole_file_answer_is_handled_exactly_as_before():
    from agent.codegen import parse_output

    with State(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answer = whole(SMALL, "export default function E() { return 5; }") + NL + whole(SMALL, "export default function E() { return 6; }")
        model = Model([(answer, "stop")])
        logs: list[str] = []
        result = implementer(model, root, [green()], logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed" and result.written == [SMALL]
        assert (root / SMALL).read_text(encoding="utf-8") == parse_output(answer, True)[0][0][1]
        assert model.steps() == ["gen0"] and not any("not applied" in line for line in logs)

@case
def the_switch_off_restores_the_whole_file_prompts():
    from agent import codegen

    saved = os.environ.get("AGENT_EDIT_BLOCKS")
    os.environ["AGENT_EDIT_BLOCKS"] = "false"
    try:
        assert codegen.system_prompt() == codegen.SYSTEM_PROMPT_WHOLE_FILES and "=== EDIT:" not in codegen.SYSTEM_PROMPT_WHOLE_FILES
        assert "`=== EDIT:`" not in codegen.files_only_demand("") and "exactly ONE file" in codegen.files_only_demand("")
        with State(), tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            model = Model([(whole(SMALL, "export default function E() { return 7; }"), "stop")])
            result = implementer(model, root, [green()], []).implement(NODE, TESTS, own_red())
            assert result.status == "passed"
            prompt = model.calls[0][1][1]["content"]
            assert "change it with EDIT blocks" not in prompt and prompt.endswith(codegen.USER_TEXT["ask_first"])
    finally:
        if saved is None:
            os.environ.pop("AGENT_EDIT_BLOCKS", None)
        else:
            os.environ["AGENT_EDIT_BLOCKS"] = saved
    from agent import codegen as again
    assert again.system_prompt() == again.SYSTEM_PROMPT and "=== EDIT:" in again.files_only_demand("")

@case
def every_rule_of_the_whole_file_prompt_survives_in_the_edit_prompt():
    from agent import codegen

    def rules(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.startswith("- ")]

    changed = {"- Output every file you create or change IN FULL, using exactly this format and nothing else (no prose, no fences):",
               "- In a repair round, output only the files that must change (in full)."}
    kept = [line for line in rules(codegen.SYSTEM_PROMPT_WHOLE_FILES) if line not in changed]
    assert all(line in codegen.SYSTEM_PROMPT for line in kept), [line for line in kept if line not in codegen.SYSTEM_PROMPT]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v160c edit cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
