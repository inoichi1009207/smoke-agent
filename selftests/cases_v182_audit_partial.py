from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb
import cases_v146a as v146a

NL = chr(10)
CASES = []
OWN_TITLES = ["REQ-9: alpha", "REQ-9: beta", "REQ-9: gamma"]
NEW_TITLE = "REQ-9: delta"
SEED = "backend/src/database/seed.js"

def case(function):
    CASES.append(function)
    return function

def fix(title: str) -> str:
    return f"FIX<{title}>"

class World(rb.World):

    def sources(self) -> str:
        seed = self.root / SEED
        return super().sources() + (NL + seed.read_text(encoding="utf-8") if seed.is_file() else "")

    def run_e2e(self, project_dir, specs, log=print):
        result = super().run_e2e(project_dir, specs, log=log)
        text = self.sources()
        counts = [self.ran_of(spec, text) for spec in specs]
        result.ran = 0 if any(n == 0 for n in counts) else sum(counts)
        return result

    @staticmethod
    def ran_of(spec, text):
        name = Path(spec).name
        if f"SKIPALL<{name}>" in text:
            return 0
        return (4 if spec == rb.OWN else 2) - text.count(f"SKIPONE<{name}>")

    def failing(self, spec, text):
        if f"SKIPALL<{Path(spec).name}>" in text:
            return []
        if spec == rb.OWN:
            red = [t for t in OWN_TITLES if fix(t) not in text]
            return red + ([NEW_TITLE] if rb.brk(NEW_TITLE) in text else [])
        return super().failing(spec, text)

def small_tree(f):
    f.large_tree = False

def run(audit_writes: dict, settings: dict | None = None, prep=small_tree, audit_status: str | None = None):
    unit = {"do": [("write", {rb.APP: "base"}), ("check",)]}
    audit = {"do": [("write", dict(audit_writes)), ("check",)]}
    if audit_status:
        audit["status"] = audit_status
    after: dict = {}

    def prep_all(f, offset, runs):
        prep(f)
        real = f.process_node

        def wrapped(*a, **k):
            try:
                return real(*a, **k)
            finally:
                after["app"] = (f.project_dir / rb.APP).read_text(encoding="utf-8")
                seed = f.project_dir / SEED
                after["seed"] = seed.read_text(encoding="utf-8") if seed.is_file() else None
        f.process_node = wrapped

    saved = rb.World
    rb.World = World
    try:
        f, lines, seen, runs, passed = v146a.run_world([unit, audit], nodes_left=1, audit=True, settings=settings, prep=prep_all)
    finally:
        rb.World = saved
    return after["app"], after["seed"], lines

def kept(lines):
    return any("[audit]" in l and "kept red:" in l for l in lines)

def put_back(lines):
    return any("[audit]" in l and "file(s) put back" in l for l in lines)

ON = {"audit_partial_keep": True, "partial_repair_evidence": True}
OFF = {"audit_partial_keep": False, "partial_repair_evidence": False}

@case
def a_repair_that_fixes_two_of_three_and_breaks_nothing_is_kept():
    better = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1])
    app, _, lines = run({rb.APP: better}, settings=ON)
    assert app == better and kept(lines) and not put_back(lines), [l for l in lines if "[audit]" in l]
    assert any("2 failure(s) gone, none new (1 left)" in l for l in lines), [l for l in lines if "[audit]" in l]

@case
def a_repair_that_fixes_two_but_breaks_a_test_that_passed_goes_back():
    worse = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1]) + rb.brk(NEW_TITLE)
    app, _, lines = run({rb.APP: worse}, settings=ON)
    assert app == "base" and put_back(lines) and not kept(lines), [l for l in lines if "[audit]" in l]
    assert any("not kept (new failure(s): REQ-9.spec.js > " in l and NEW_TITLE in l for l in lines), lines

@case
def a_repair_that_breaks_an_earlier_spec_goes_back():
    worse = "base " + fix(OWN_TITLES[0]) + rb.brk(rb.S1)
    app, _, lines = run({rb.APP: worse}, settings=ON)
    assert app == "base" and put_back(lines) and not kept(lines), [l for l in lines if "[audit]" in l]

@case
def a_repair_that_changes_no_failure_goes_back():
    app, _, lines = run({rb.APP: "base reworded"}, settings=ON)
    assert app == "base" and put_back(lines) and any("not kept (no failure is gone)" in l for l in lines), lines

@case
def a_repair_that_rewrote_a_seed_file_goes_back():
    better = "base " + fix(OWN_TITLES[0])
    app, seed, lines = run({rb.APP: better, SEED: "// seed " + fix(OWN_TITLES[1])}, settings=ON)
    assert app == "base" and seed is None and put_back(lines) and not kept(lines), lines

@case
def the_seed_guard_branch_keeps_the_rest_without_the_seed_change():

    better = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1])

    def with_seed(f):
        small_tree(f)
        (f.project_dir / SEED).parent.mkdir(parents=True, exist_ok=True)
        (f.project_dir / SEED).write_text("// seed", encoding="utf-8")

    app, seed, lines = run({rb.APP: better, SEED: "// seed " + fix(OWN_TITLES[2])}, settings=ON, prep=with_seed)
    assert any("without the seed change the check is red" in l for l in lines), lines
    assert app == better and seed == "// seed" and kept(lines), [l for l in lines if "[audit]" in l]

    app, seed, lines = run({rb.APP: better, SEED: "// seed " + fix(OWN_TITLES[2])}, settings=OFF, prep=with_seed)
    assert app == "base" and seed == "// seed" and put_back(lines) and not kept(lines), lines

@case
def a_large_tree_goes_back():
    better = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1])
    app, _, lines = run({rb.APP: better}, settings=ON, prep=lambda f: None)
    assert app == "base" and put_back(lines) and any("not kept (a check does not name every failed test)" in l for l in lines), lines

@case
def a_test_defect_end_goes_back_as_before():
    better = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1])
    calls: list = []

    def with_author(f):
        small_tree(f)
        v146a.recording_author(calls)(f, [0.0], [])

    app, _, lines = run({rb.APP: better}, settings=ON, audit_status="test_defect", prep=with_author)
    assert not kept(lines) and any("file(s) put back" in l for l in lines), lines
    assert len(calls) == 1, calls

@case
def off_the_same_better_repair_goes_back_as_v181():
    better = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1])
    for settings in (OFF, dict(OFF, partial_repair_evidence=True)):
        app, _, lines = run({rb.APP: better}, settings=settings)
        assert app == "base" and put_back(lines), lines
        assert not any("kept red" in l or "not kept (" in l for l in lines), lines

EARLIER = "REQ-1-1-1.spec.js"

@case
def codex_v182_r1_1_a_green_spec_dynamically_skipped_after_the_repair_blocks_the_keep():

    worse = "base " + fix(OWN_TITLES[0]) + rb.brk(rb.S1) + f"SKIPALL<{EARLIER}>"
    app, _, lines = run({rb.APP: worse}, settings=ON)
    assert app == "base" and put_back(lines) and not kept(lines), [l for l in lines if "[audit]" in l]
    assert any("not kept (" in l and ("fewer tests ran" in l or "not known" in l) for l in lines), [l for l in lines if "[audit]" in l]

@case
def one_test_that_ran_before_and_is_skipped_now_blocks_the_keep():
    worse = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1]) + f"SKIPONE<{EARLIER}>"
    app, _, lines = run({rb.APP: worse}, settings=ON)
    assert app == "base" and put_back(lines) and any("not kept (fewer tests ran" in l for l in lines), \
        [l for l in lines if "[audit]" in l]

@case
def a_crash_in_the_keep_decision_rolls_back_as_v181():
    import main

    saved = main.partial_repair_keepable

    def boom(*a, **k):
        raise RuntimeError("boom")
    main.partial_repair_keepable = boom
    try:
        better = "base " + fix(OWN_TITLES[0]) + fix(OWN_TITLES[1])
        app, _, lines = run({rb.APP: better}, settings=ON)
    finally:
        main.partial_repair_keepable = saved
    assert app == "base" and put_back(lines) and not kept(lines), [l for l in lines if "[audit]" in l]
    assert any("partial-keep decision crashed" in l for l in lines), [l for l in lines if "[audit]" in l]

@case
def failure_ids_need_complete_evidence():
    import main

    out = rb.spec_output("REQ-9.spec.js", ["REQ-9: alpha", "REQ-9: beta"])
    ev = {"output": "E2E FAILED against the real application\n" + "FAILED SPEC FILES: REQ-9.spec.js\n## REQ-9.spec.js\n" + out,
          "passed": False, "specs": ["x"], "large": False, "ran": 3}

    one = dict(ev, output=ev["output"].replace("REQ-9: beta", "REQ-9: alpha").replace("  2 failed", "  1 failed"))
    one = dict(one, output=rb.spec_output("REQ-9.spec.js", ["REQ-9: alpha"]))
    one = dict(ev, output="E2E FAILED against the real application\nFAILED SPEC FILES: REQ-9.spec.js\n## REQ-9.spec.js\n" + one["output"])
    assert main.check_failure_ids(one) == {("REQ-9.spec.js", "REQ-9: alpha")}
    assert main.partial_repair_keepable(dict(ev, digest="a"), dict(one, digest="b"), "a", "b")[0]
    for bad in ({"ran": None}, {"ran": -1}, {"ran": 0}, {"ran": 2}, {"ran": True}):
        keep, why = main.partial_repair_keepable(dict(ev, digest="a"), dict(one, digest="b", **bad), "a", "b")
        assert not keep and ("not known" in why or "fewer tests ran" in why), (bad, why)
    keep, why = main.partial_repair_keepable(dict(ev, digest="a", ran=None), dict(one, digest="b"), "a", "b")
    assert not keep and "not known" in why, why
    skipped = dict(one, digest="b", output=one["output"] + "\n  1 skipped")
    keep, why = main.partial_repair_keepable(dict(ev, digest="a"), skipped, "a", "b")
    assert not keep and "more tests skipped" in why, why
    assert main.spec_skipped_counts("## A.spec.js\n  2 skipped\n## B.spec.js\n  1 did not run\n") == {"A.spec.js": 2, "B.spec.js": 1}
    assert main.check_failure_ids(ev) == {("REQ-9.spec.js", "REQ-9: alpha"), ("REQ-9.spec.js", "REQ-9: beta")}
    assert main.check_failure_ids(dict(ev, large=True)) is None
    assert main.check_failure_ids(dict(ev, output=ev["output"] + "\n[middle of the output omitted]")) is None
    assert main.check_failure_ids(dict(ev, output=ev["output"].replace("  2 failed", "  3 failed"))) is None
    assert main.check_failure_ids(dict(ev, output="FRONTEND BUILD FAILED\n" + out)) is None
    assert main.check_failure_ids(dict(ev, output=ev["output"] + "\nNOT RUN SPEC FILES: REQ-1.spec.js")) is None
    assert main.check_failure_ids(dict(ev, output=ev["output"] + "\n\nNOT AN APPLICATION FAILURE (left out above): x")) is None
    assert main.check_failure_ids({"output": "E2E PASSED\nok", "passed": True, "specs": ["x"]}) == set()
    keep, why = main.partial_repair_keepable(dict(ev, digest="a"), dict(ev, digest="b"), "a", "b")
    assert not keep and why == "no failure is gone", why
    keep, _ = main.partial_repair_keepable(dict(ev, digest="a"), dict(ev, digest="b", lint="new lint"), "a", "b")
    assert not keep
    keep, why = main.partial_repair_keepable(dict(ev, digest="a"), dict(ev, digest="b"), "a", "c")
    assert not keep and "not about the states" in why

@case
def a_candidate_that_loses_stated_seed_values_is_never_kept():
    import main

    seeds = "SEED SENTENCES ..." + NL + "- The evaluation seed contains rows `East/1200/Open`, `North/800/Closed`."
    before = {"backend/src/routes/data.js": b"['East','1200','Open'],['North','800','Closed']"}
    after = {"backend/src/routes/data.js": b"['East','1200','Open']"}
    node = {"id": "REQ-9", "seed_sentences": seeds}
    assert "stated seed values gone" in main.Factory.seed_text_regressed(node, before, after)
    assert main.Factory.seed_text_regressed(node, before, before) == ""

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
