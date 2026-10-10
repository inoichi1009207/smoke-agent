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

SHEET_2_2_2 = ("Data outside the deleted column is preserved; affected formulas display the adjusted original formulas and "
               "correct results, while direct references that cannot be preserved display #REF!; filters continue to apply "
               "to the adjusted region. If a selected header is deleted, refreshing or opening the pivot table editor "
               "displays a visible error requiring the field to be reselected and preserves the last successful result. "
               "The column-header menu provides “Insert 1 column left”, “Insert 1 column right”, and “Delete column”.")

V141_CUT = ["REQ-2-2-2: filters continue to apply to the adjusted region after inserting a column",
            "REQ-2-2-2: if a selected header is deleted the pivot editor requires the field to be reselected and preserves "
            "the last successful result"]
V141_KEPT = ["REQ-2-2-2: data outside the deleted column is preserved"]

def case(function):
    CASES.append(function)
    return function

def old_quote_ok(title: str, description: str) -> bool:
    import agent.description_pass as dp

    rest = dp._quote_norm(dp.title_quote(title)).rstrip(" .;:,!?。，；：")
    return bool(rest) and len(rest.split()) >= 5 and rest in dp._quote_norm(description)

@case
def three_consecutive_words_of_the_description_in_the_title_pass():
    import agent.description_pass as dp

    for title in V141_CUT:
        assert not old_quote_ok(title, SHEET_2_2_2) and dp.quote_ok(title, SHEET_2_2_2), title
    for title in V141_KEPT:
        assert old_quote_ok(title, SHEET_2_2_2) and dp.quote_ok(title, SHEET_2_2_2), title

    assert dp.quote_ok("REQ-2-2-2: the “Insert 1 column left” entry works", SHEET_2_2_2)
    assert dp.quote_ok("REQ-2-2-2: Deleted refreshing or opening the editor", SHEET_2_2_2)

    assert not dp.quote_ok("REQ-2-2-2: shows a friendly toast after saving", SHEET_2_2_2)
    assert not dp.quote_ok("REQ-3: chosen region stays", cl.DESC3)
    assert not dp.quote_ok("REQ-3: keeps the region", cl.DESC3)

    assert not dp.quote_ok("REQ-9: to be in trouble", "It has to be in the box.")
    assert dp.quote_ok("REQ-9: stays in the box", "It has to be in the box.")

    assert not dp.quote_ok("REQ-2-2-2: nothing", "REQ-2-2-2: nothing at all")
    assert dp.quote_ok("REQ-9: 列表页可以按名称筛选全部记录之后", "说明:用户在列表页可以按名称筛选全部记录。")
    assert not dp.quote_ok("REQ-9: 列表页可以按名称", "说明:用户在列表页可以按名称筛选全部记录。")

@case
def a_source_line_directly_above_the_test_quotes_for_it():
    import agent.description_pass as dp

    assert dp.source_ok("filters continue to apply to the adjusted region", SHEET_2_2_2)
    assert dp.source_ok("“Delete column”. Data outside", "“Delete column”. Data outside the column")
    assert not dp.source_ok("filters continue to apply to every region", SHEET_2_2_2)
    assert not dp.source_ok("adjusted region", SHEET_2_2_2)
    assert not dp.source_ok("to be in", "It has to be in the box.")
    block = ('// checks the filter' + NL + '// source: "filters continue to apply to the adjusted region"' + NL
             + 'test("REQ-2-2-2: filter after insert", async ({ page }) => {' + NL + '});' + NL)
    assert dp.source_phrase(block) == "filters continue to apply to the adjusted region"
    assert dp.quote_evidence("REQ-2-2-2: filter after insert", block, SHEET_2_2_2) == "source"
    assert dp.quote_evidence(V141_KEPT[0], block, SHEET_2_2_2) == "title"
    assert dp.quote_evidence("REQ-2-2-2: filter after insert", block.replace("adjusted", "whole"), SHEET_2_2_2) == ""

    inside = 'test("REQ-2-2-2: filter after insert", async () => {' + NL + '  // source: "filters continue to apply"' + NL + '});' + NL
    assert dp.source_phrase(inside) == ""

@case
def a_source_line_travels_with_its_test_when_another_one_is_cut():
    import agent.description_pass as dp

    head = 'const { test } = require("@playwright/test");' + NL + NL
    first = 'test("REQ-3: invented one", async () => {' + NL + '});' + NL + NL
    second = ('// source: "keeps the chosen region after a reload"' + NL
              + 'test("REQ-3: region after reload", async () => {' + NL + '});' + NL)
    header, blocks = dp.split_tests(head + first + second)
    assert header == head and [t for t, _ in blocks] == ["REQ-3: invented one", "REQ-3: region after reload"]
    assert blocks[1][1].startswith("// source:") and "source" not in blocks[0][1]
    assert "// source:" in dp.join_tests(header, blocks[1:])

    loose = head + first + '// source: "keeps the chosen region after a reload"' + NL + NL + 'test("REQ-3: x", async () => {});' + NL
    _header, blocks = dp.split_tests(loose)
    assert "source" in blocks[0][1] and dp.source_phrase(blocks[1][1]) == ""

    header, blocks = dp.split_tests(head + second)
    assert header == head and blocks[0][1].startswith("// source:")

def _authored(titles_and_sources: list[tuple[str, str, bool]]):
    from agent.test_author import AuthoredFile

    def author(llm, node, prefix, problem="", current="", log=print):
        text = 'const { test } = require("@playwright/test");' + NL + 'const { see } = require("../e2e-support/outsider");' + NL + NL
        for title, source, loose in titles_and_sources:
            if source:
                text += f'// source: "{source}"' + NL + (NL if loose else "")
            text += f'test("{title}", async ({{ page }}) => {{{NL}  await page.goto("/");{NL}  await see(page, "x");{NL}}});{NL}{NL}'
        return AuthoredFile(path=f"backend/test-e2e/{prefix}.spec.js", content=text)
    return author

@case
def the_gate_admits_by_source_line_drops_invented_quotes_and_logs_the_dropped_titles():
    a, b, c = "REQ-3: region survives a reload", "REQ-3: totals look bold", "REQ-3: the filter remembers"
    tmp, root, world, f = cl.with_world()
    with tmp:
        world.green = {a, b, c}
        author = _authored([(a, "keeps the chosen region after a reload", False),
                            (b, "each row shows its total in heavy type", False),
                            (c, "keeps the chosen region", True)])
        p, summary, lines = cl.run_pass(world, f, patch={"author": author})
        content = (root / cl.D3).read_text(encoding="utf-8")
        assert a in content and b not in content and c not in content, content
        assert '// source: "keeps the chosen region after a reload"' in content
        tests = {t["title"]: t for t in cl.ledger(f)[0]["tests"]}
        assert tests[a]["quote_by"] == "source" and tests[a]["final"] == "admitted", tests
        assert tests[b]["reason"] == "invented-quote" and tests[c]["reason"] == "invented-quote", tests
        gate = [line for line in lines if "quote gate:" in line]
        assert gate and b in gate[0] and c in gate[0] and a not in gate[0], gate

@case
def the_window_default_is_three_hours_and_the_formula_stays():
    import agent.description_pass as dp
    from agent.settings import SETTINGS, _DEFAULTS

    assert _DEFAULTS["description_pass_max_sec"] == 10800
    tmp, root, world, f = cl.with_world()
    with tmp:
        p = dp.DescriptionPass(f, {}, [], "", [], "d", "", lambda line: None, dp.default_tools(f, lambda line: None))
        saved = SETTINGS.pop("description_pass_max_sec", cl.MISSING)
        try:
            assert p.window_end(25900, 45) == 25900 + 10800
            assert p.window_end(120000, 45) == 129600 - p.verify_reserve(45)
        finally:
            if saved is not cl.MISSING:
                SETTINGS["description_pass_max_sec"] = saved

HB = "backend/test-e2e/REQ-4+5.spec.js"
H4, H5 = "REQ-4: four shows", "REQ-5: five shows"
D5 = "REQ-5: shows its own page with its own list"
D4 = "REQ-4: shows its own page with its own list"

def hub_world(app: str):
    tmp, root, world, f = cl.with_world(app)
    (root / HB).write_text(cl.spec_text([H4, H5]), encoding="utf-8")
    f.e2e_specs.append(HB)
    f.all_test_files.append(HB)
    f.test_snapshot[HB] = (root / HB).read_text(encoding="utf-8")
    f.spec_context[HB] = {"node": {"id": "REQ-4+5"}, "contract": "", "relevant": [], "tests": {}, "members": ["REQ-4", "REQ-5"], "written": []}
    f.failed_members = ["REQ-4", "REQ-5"]
    world.titles.update({"REQ-4": [D4], "REQ-5": [D5]})
    world.green |= {D4, D5}
    return tmp, root, world, f

def hub_tree() -> dict:
    def leaf(i, deps=()):
        return {"id": f"REQ-{i}", "name": f"Feature {i}", "description": f"Feature number {i} shows its own page with its own list.",
                "dependencies": list(deps), "scenarios": [{"name": f"s-REQ-{i}", "steps": [{"keyword": "THEN", "content": "shown"}]}]}
    return {"id": "ROOT", "name": "R", "description": "root", "children": [
        leaf(1), leaf(2), leaf(3), leaf(4), leaf(5, ["REQ-1"]), leaf(6, ["REQ-5"]), leaf(7, ["REQ-5"]), leaf(8, ["REQ-5"]),
        leaf(9, ["REQ-5"]), leaf(10, ["REQ-6"])]}

BATCHES = [("REQ-1", ["REQ-1"]), ("REQ-2", ["REQ-2"]), ("REQ-3", ["REQ-3"]), ("REQ-4+5", ["REQ-4", "REQ-5"])] + [
    (f"REQ-{i}", [f"REQ-{i}"]) for i in range(6, 11)]
IDS = tuple(f"REQ-{i}" for i in (1, 2, 3)) + ("REQ-4+5",) + tuple(f"REQ-{i}" for i in range(6, 11))

def _hub_pass(app: str, scope=(), patch=None):
    tmp, root, world, f = hub_world(app)
    with tmp:
        p, summary, lines = cl.run_pass(world, f, scope=scope, tree=hub_tree(), done_ids=IDS, batches=BATCHES, patch=patch)
        return p, summary, lines, [c[0] for c in world.author_calls if not c[1]], world

@case
def hub_members_at_baseline_reads_the_members_titles():
    import agent.description_pass as dp

    ctx = {HB: {"members": ["REQ-4", "REQ-5"]}, "backend/test-e2e/REQ-6.spec.js": {"members": ["REQ-6"]}}
    ran = {"REQ-4+5.spec.js", "REQ-6.spec.js"}
    v = dp.hub_members_at_baseline(["REQ-4", "REQ-5"], ctx, {"REQ-4+5.spec.js": [H5]}, ran)
    assert v["REQ-5"].startswith("red: REQ-4+5.spec.js: " + H5) and v["REQ-4"] == "green", v
    v = dp.hub_members_at_baseline(["REQ-4", "REQ-5"], ctx, {}, ran)
    assert v == {"REQ-4": "green", "REQ-5": "green"}, v

    v = dp.hub_members_at_baseline(["REQ-4", "REQ-5"], ctx, {"REQ-4+5.spec.js": dp.WHOLE}, ran)
    assert all(x.startswith("unknown") for x in v.values()), v
    v = dp.hub_members_at_baseline(["REQ-4"], ctx, {"REQ-4+5.spec.js": ["Scenario 1 without an id"]}, ran)
    assert v["REQ-4"].startswith("unknown"), v
    assert dp.hub_members_at_baseline(["REQ-4"], ctx, {}, {"REQ-6.spec.js"})["REQ-4"].startswith("unknown")
    assert dp.hub_members_at_baseline(["REQ-7"], ctx, {}, ran)["REQ-7"].startswith("unknown")

    v = dp.hub_members_at_baseline(["REQ-4-1", "REQ-5"], {HB: {"members": ["REQ-4-1", "REQ-5"]}}, {"REQ-4+5.spec.js": ["REQ-4: x"]}, ran)
    assert v["REQ-4-1"].startswith("red") and v["REQ-5"] == "green", v

@case
def only_the_red_members_of_a_red_hub_batch_are_taken():

    p, summary, lines, calls, world = _hub_pass("base " + cl.brk(H5))
    assert calls == ["REQ-5"], (calls, lines)
    assert summary["hub_members_green"] == ["REQ-4"] and summary["admitted"] == ["backend/test-e2e/DESC-REQ-5.spec.js"], summary
    assert any("leaf order" in line and "REQ-5*" in line and "REQ-4" not in line for line in lines), lines

    p, summary, lines, calls, world = _hub_pass("base " + cl.brk(H4))
    assert calls == ["REQ-4"] and summary["hub_members_green"] == ["REQ-5"], (calls, summary)

    p, summary, lines, calls, world = _hub_pass("base " + cl.brk(H4) + " " + cl.brk(H5))
    assert sorted(calls) == ["REQ-4", "REQ-5"] and summary["hub_members_green"] == [], (calls, summary)

@case
def a_hub_batch_green_at_the_baseline_leaves_nothing_to_do():
    p, summary, lines, calls, world = _hub_pass("base")
    assert calls == [] and world.implementers == [], calls
    assert summary["status"] == "no leaf left in scope after the baseline" and summary["hub_members_green"] == ["REQ-4", "REQ-5"], summary
    assert world.builds == 1

@case
def unreadable_titles_keep_every_member_and_boilerplate_keeps_a_green_one_not_first():

    p, summary, lines, calls, world = _hub_pass("base " + cl.brk(H5), patch={"failed_test_titles": lambda output: {}})
    assert sorted(calls) == ["REQ-4", "REQ-5"] and summary["hub_members_green"] == [], (calls, summary)

    p, summary, lines, calls, world = _hub_pass("base " + cl.brk(H5), scope=("REQ-4",))
    assert calls == ["REQ-5", "REQ-4"], (calls, lines)
    order = [line for line in lines if "leaf order" in line][-1]
    assert "REQ-5*" in order and "REQ-4*" not in order, order

@case
def the_note_asks_for_three_words_or_a_source_line():
    from agent.description_pass import DESCRIPTION_PASS_NOTE

    note = " ".join(DESCRIPTION_PASS_NOTE.split())
    assert "at least three consecutive words copied verbatim from the description" in note
    assert '// source: "<phrase>"' in note and "directly above the test()" in note
    assert "replace those of GENERATED SCENARIOS" in note
    assert "five" not in note and "`" not in note

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
    print(f"{len(CASES) - failed}/{len(CASES)} pass-142 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
