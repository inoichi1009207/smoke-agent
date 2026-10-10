from __future__ import annotations

import json
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

def case(function):
    CASES.append(function)
    return function

USERNAME = "Error: The requirement says Username format is invalid is shown, but nothing visible matches."
SIGNED_IN = "Error: The requirement says Signed in as alice is shown, but nothing visible matches."

def brk2(title: str) -> str:
    return f"BREAK2<{title}>"

class SigWorld(rb.World):

    def __init__(self, root: Path):
        super().__init__(root)
        self.port = 50000

    def errors(self, spec: str, text: str) -> dict[str, str]:
        if spec == rb.OWN:
            return {} if "OWN-OK" in text else {rb.OWN_TITLE: "Error: own spec"}
        prefix = Path(spec).name.replace(".spec.js", "")
        found = {}
        for title in (rb.S1, rb.S2):
            if not title.startswith(prefix):
                continue
            if rb.brk(title) in text:
                found[title] = USERNAME
            elif brk2(title) in text:
                found[title] = SIGNED_IN
        return found

    def failing(self, spec: str, text: str) -> list[str]:
        return list(self.errors(spec, text))

    def output(self, name: str, errors: dict[str, str]) -> str:
        self.port += 1
        lines = [f"FAILED SPEC FILES: {name}"]
        for index, (title, error) in enumerate(errors.items(), 1):
            lines += [f"  {index}) test-e2e\\{name}:{10 * index}:1 › {title} ───────",
                      "",
                      f"    {error}",
                      f"    at http://127.0.0.1:{self.port}/login after {self.port % 97 + 1000}ms (user-{self.port}a9f3)",

                      f"        at D:\\w\\backend\\test-e2e\\{name}:{10 * index + 3}:5",
                      ""]
        lines += [f"  {len(errors)} failed"] + [f"    test-e2e\\{name}:{10 * i}:1 › {t} ────────" for i, t in enumerate(errors, 1)]
        return NL.join(lines + ["  1 passed"])

    def run_e2e(self, project_dir, specs, log=print):
        from agent.verify import StepResult

        text = self.sources()
        self.runs.append((list(specs), text))
        red = [(spec, self.errors(spec, text)) for spec in specs]
        red = [(spec, errors) for spec, errors in red if errors]
        if len(specs) == 1:
            return StepResult(not red, self.output(Path(specs[0]).name, red[0][1]) if red else "1 passed")
        if not red:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed, each run against fresh data")
        head = "FAILED SPEC FILES: " + ", ".join(Path(s).name for s, _ in red)
        parts = [f"## {Path(s).name}{NL}{self.output(Path(s).name, e)}" for s, e in red]
        return StepResult(False, NL.join([head, f"{len(specs) - len(red)} of {len(specs)} spec files passed"] + parts))

def run_batch_in(world_class, app_before: str, script: list[dict]):
    import main as factory_module
    from agent.settings import SETTINGS
    from agent.testrun import TestOutcome

    lines: list[str] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output", "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_audit = SETTINGS.get("audit_enabled", True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = world_class(root)
        f = rb.make_factory(root, app_before)
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = rb.implementer_class(script)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            SETTINGS["audit_enabled"] = False
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, 1)
            after = (root / rb.APP).read_text(encoding="utf-8")
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            SETTINGS["audit_enabled"] = saved_audit
    assert not script, f"scripted steps left over: {script}"
    return f, world, lines, after, passed

@case
def a_signature_masks_run_noise_and_keeps_the_words():
    import main as factory_module

    sig = factory_module.error_signature
    one = ("    TimeoutError: page.click: Timeout 5000ms exceeded at http://127.0.0.1:53211/login (id 3f2a9c1e77b0, user \"bob-1695\") "
           "after 12.5s, row 17 of 250")
    two = ("    TimeoutError: page.click: Timeout 30000ms exceeded at http://localhost:40017/login (id 9e8d7c6b5a41, user \"bob-2203\") "
           "after 3s, row 4 of 251")
    assert sig(one) == sig(two) and sig(one), (sig(one), sig(two))
    assert sig(USERNAME) != sig(SIGNED_IN)
    assert "Username format is invalid" in sig(USERNAME)

    world = SigWorld(Path("."))
    out = world.output("REQ-1-1-1.spec.js", {rb.S1: USERNAME, rb.S2: SIGNED_IN})
    found = factory_module.failed_test_signatures(out)
    assert found == {"REQ-1-1-1.spec.js": {rb.S1: sig(USERNAME) + " @ REQ-1-1-1.spec.js:13:5",
                                           rb.S2: sig(SIGNED_IN) + " @ REQ-1-1-1.spec.js:23:5"}}, found
    bare = NL.join(["  1) test-e2e\\REQ-1-1-1.spec.js:10:1 › " + rb.S1 + " ───", "", "  1 failed",
                    "    test-e2e\\REQ-1-1-1.spec.js:10:1 › " + rb.S1])
    assert factory_module.failed_test_signatures(bare) == {"REQ-1-1-1.spec.js": {rb.S1: ""}}

    assert factory_module.failed_test_signatures(out + NL + "... [middle of the output omitted] ...") == {}

@case
def a_new_break_under_an_already_failing_title_is_blamed():
    before = "base " + rb.brk(rb.S1)

    f, world, lines, app, passed = run_batch_in(SigWorld, before, [{"write": {rb.APP: "base OWN-OK " + brk2(rb.S1)}, "check": True}])
    assert app == before and rb.put_back(lines) and not passed, lines
    assert not rb.pre_existing(lines)

    f, world, lines, app, passed = run_batch_in(SigWorld, before, [{"write": {rb.APP: before + " OWN-OK"}, "check": True}])
    assert app == before + " OWN-OK" and not rb.put_back(lines) and rb.pre_existing(lines) and passed, lines

    f, world, lines, app, passed = run_batch_in(SigWorld, before, [{"write": {rb.APP: "base feature " + brk2(rb.S1)}, "check": True}])
    assert app == before and rb.put_back(lines), lines

@case
def an_unreadable_signature_counts_as_new():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "base")
        f.regression_specs = lambda before, own: ["x"]
        name = "REQ-1-1-1.spec.js"
        world = SigWorld(Path(tmp))
        f.foundation_baseline = lambda req_id, before: {name: {rb.S1: factory_module.error_signature(USERNAME) + f" @ {name}:13:5"}}
        saved = factory_module.log
        factory_module.log = lambda line: None
        try:
            same = world.output(name, {rb.S1: USERNAME})
            assert f.new_foundation_failures("REQ-9", {"a": b"1"}, {name: same}) == []
            bare = NL.join([f"  1) test-e2e\\{name}:10:1 › {rb.S1} ───", "", "  1 failed", f"    test-e2e\\{name}:10:1 › {rb.S1}"])
            assert f.new_foundation_failures("REQ-9", {"a": b"1"}, {name: bare}) == [name]

            f.foundation_baseline = lambda req_id, before: {name: {rb.S1: ""}}
            assert f.new_foundation_failures("REQ-9", {"a": b"1"}, {name: same}) == [name]
        finally:
            factory_module.log = saved

HEAD = 'const { test } = require("@playwright/test");' + NL + 'const { see } = require("../e2e-support/outsider");' + NL + NL

def block(title: str, modifier: str = "", body: str = "") -> str:
    return (f'test{modifier}("{title}", async ({{ page }}) => {{{NL}{body}  await page.goto("/");{NL}'
            f'  await see(page, "x");{NL}}});{NL}{NL}')

@case
def modifiers_and_runtime_skips_are_refused_at_the_quote_gate():
    import agent.description_pass as dp

    tmp, root, world, f = cl.with_world()
    with tmp:
        tools = dp.default_tools(f, lambda line: None)
        p = dp.DescriptionPass(f, {}, [], "", [], "d", "", lambda line: None, tools)
        variants = [block(cl.T3A, ".skip"), block(cl.T3A, ".fixme"), block(cl.T3A, ".fail"), block(cl.T3A, ".only"),
                    block(cl.T3A, body="  test.skip();" + NL), block(cl.T3A, body="  test.skip(true, 'later');" + NL),
                    block(cl.T3A, body="  test.fixme();" + NL), block(cl.T3A, body="  test.fail();" + NL)]
        for variant in variants:
            tests: dict = {}
            out = p.quote_gate(HEAD + variant + block(cl.T3B), cl.DESC3, tests)
            assert cl.T3A not in out and cl.T3B in out, (variant, out)
            assert tests[cl.T3A]["final"] == "dropped" and tests[cl.T3A]["reason"] == "not-run-modifier", tests

        tests = {}
        assert p.quote_gate(HEAD + "test.skip(true);" + NL + block(cl.T3A) + block(cl.T3B), cl.DESC3, tests) == ""

        tests = {}
        assert cl.T3A in p.quote_gate(HEAD + block(cl.T3A, ".slow"), cl.DESC3, tests)

@case
def a_skipped_test_is_not_admitted_in_the_pass():
    import agent.test_author as ta

    tmp, root, world, f = cl.with_world()
    with tmp:
        world.green = {cl.T3A, cl.T3B}

        def author(llm, node, prefix, problem="", current="", log=print):
            world.author_calls.append((str(node.get("id")), bool(problem)))
            return ta.AuthoredFile(path=f"backend/test-e2e/{prefix}.spec.js", content=HEAD + block(cl.T3A, ".skip") + block(cl.T3B))
        p, summary, lines = cl.run_pass(world, f, patch={"author": author})
        content = (root / cl.D3).read_text(encoding="utf-8")
        assert cl.T3A not in content and cl.T3B in content and f.description_specs == [cl.D3], content
        tests = {t["title"]: t for t in cl.ledger(f)[0]["tests"]}
        assert tests[cl.T3A]["final"] == "dropped" and tests[cl.T3B]["final"] == "admitted", tests

@case
def admission_needs_every_candidate_test_to_have_run():
    from agent.verify import StepResult

    tmp, root, world, f = cl.with_world()
    with tmp:
        world.green = {cl.T3A, cl.T3B}
        real = world.run_e2e

        def run_e2e(project_dir, specs, log=print, shared_data=False):
            result = real(project_dir, specs, log=log)
            if specs == [cl.D3] and result.ok:
                return StepResult(True, result.output, ran=1)
            return result
        p, summary, lines = cl.run_pass(world, f, patch={"run_e2e": run_e2e})
        assert cl.D3 not in f.e2e_specs and f.description_specs == [] and not (root / cl.D3).exists(), cl.ledger(f)
        entry = cl.ledger(f)[0]
        assert entry["outcome"].startswith("dropped"), entry
        assert all(t["final"] == "dropped" and t["reason"] == "not-run" for t in entry["tests"]), entry["tests"]

    tmp, root, world, f = cl.with_world()
    with tmp:
        world.green = {cl.T3A, cl.T3B}
        real = world.run_e2e

        def unknown(project_dir, specs, log=print, shared_data=False):
            result = real(project_dir, specs, log=log)
            return StepResult(result.ok, result.output, skipped=result.skipped) if specs == [cl.D3] else result
        p, summary, lines = cl.run_pass(world, f, patch={"run_e2e": unknown})
        assert f.description_specs == [] and cl.ledger(f)[0]["outcome"].startswith("dropped"), cl.ledger(f)

@case
def a_new_red_that_is_green_on_a_rerun_still_puts_the_repair_back():
    tmp, root, world, f = cl.with_world()
    with tmp:
        world.titles["REQ-3"] = [cl.T3A, cl.T3B]
        world.flaky = {"REQ-2.spec.js": (cl.ok(cl.T3A), 1)}
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A) + " " + cl.ok(cl.T3B)}, "check": True}]
        p, summary, lines = cl.run_pass(world, f)
        assert cl.app_text(root) == "base" and cl.D3 not in f.e2e_specs, (cl.app_text(root), cl.ledger(f))
        entry = cl.ledger(f)[0]
        assert entry["rollback"]["done"] and entry["rollback"]["reason"].startswith("new-red: REQ-2.spec.js"), entry
        assert summary["repairs_kept"] == 0 and summary["repairs_rolled_back"] == 1

@case
def every_admitted_description_spec_guards_the_closing_repair():
    import main as factory_module
    from agent.llm import LLM
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "base")
        target = "backend/test-e2e/REQ-5.spec.js"
        described = [f"backend/test-e2e/DESC-REQ-{i}.spec.js" for i in range(1, 15)]
        f.e2e_specs, f.green_specs, f.description_specs = [rb.F1, rb.F2, target] + described, [rb.F1, rb.F2], list(described)
        f.spec_context = {target: {"node": {"id": "REQ-5"}, "contract": "", "relevant": [], "tests": {}, "written": []}}
        last = Path(described[-1]).name
        runs: list[list[str]] = []

        def run_e2e(project_dir, specs, log=print, shared_data=False):
            text = (root / rb.APP).read_text(encoding="utf-8")
            runs.append(list(specs))
            red = [Path(s).name for s in specs
                   if (s == target and "FIXED" not in text) or (s != target and f"BREAK<{Path(s).name}>" in text)]
            if not red:
                return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed")
            return StepResult(False, "FAILED SPEC FILES: " + ", ".join(red) + NL
                              + NL.join(f"  1) test-e2e\\{n}:3:1 › {n} ───" for n in red))
        script = [{"write": {rb.APP: f"base FIXED BREAK<{last}>"}, "check": True}]
        lines: list[str] = []
        names = ("run_e2e", "build_frontend", "Implementer", "log")
        saved = {name: getattr(factory_module, name) for name in names}
        saved_quota = LLM.quota_exhausted
        try:
            factory_module.run_e2e = run_e2e
            factory_module.build_frontend = lambda project_dir, log=print: StepResult(True, "")
            factory_module.Implementer = rb.implementer_class(script)
            factory_module.log = lines.append
            LLM.quota_exhausted = False
            repaired = f.repair_failing_specs("FAILED SPEC FILES: REQ-5.spec.js" + NL)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            LLM.quota_exhausted = saved_quota
        assert not script
        assert repaired == 0 and (root / rb.APP).read_text(encoding="utf-8") == "base", (repaired, lines)
        guard_runs = [r for r in runs if target not in r and len(r) > 1]
        assert guard_runs and set(described) <= set(guard_runs[0]), guard_runs

@case
def without_description_specs_the_guard_keeps_its_cap_of_eight():
    import inspect

    import main as factory_module

    source = inspect.getsource(factory_module.Factory.repair_failing_specs)
    assert "[:8]" in source and "[:12 if described else 8]" not in source

class FakeLLM:
    def __init__(self, total: int):
        self.total = total

    def summary(self) -> dict:
        return {"total_tokens": self.total}

def resumed_factory(root: Path, world):
    import main as factory_module

    second = factory_module.Factory.__new__(factory_module.Factory)
    second.project_dir, second.revised_specs, second.resumed_description, second.restore_failures = root, set(), "", 0
    second.resumed_sources = {}
    done = second.load_checkpoint("digest-1")
    second.trace, second.started = cl._Trace(), world.now
    second.time_budget, second.large_tree, second.verify_repairs = 10800.0, True, 0
    return second, done

@case
def a_restart_before_the_pass_keeps_the_token_count_of_the_batches():
    import agent.description_pass as dp

    tmp, root, world, f = cl.with_world()
    with tmp:
        f.llm = FakeLLM(8000)
        f.save_checkpoint("digest-1", ["REQ-1", "REQ-2", "REQ-3", "REQ-4"], "root")
        second, done = resumed_factory(root, world)
        assert done == ["REQ-1", "REQ-2", "REQ-3", "REQ-4"]
        second.llm = FakeLLM(0)
        tokens = dp.default_tools(second, lambda line: None).tokens
        assert tokens() == 8000, tokens()
        second.llm.total = 2000
        assert tokens() == 10000, tokens()
        second.llm.total = 0
        world.green = {cl.T3A, cl.T3B}
        p, summary, lines = cl.run_pass(world, second, patch={"tokens": tokens})
        assert summary["status"] == "done" and summary["token_budget"] == 4000, (summary, lines)
        assert second.description_specs == [cl.D3]

@case
def the_pass_writes_a_checkpoint_with_its_baseline_when_it_starts():
    import agent.description_pass as dp

    tmp, root, world, f = cl.with_world()
    with tmp:
        f.llm = FakeLLM(6000)
        world.author_raise = {"REQ-3": cl.Killed()}
        try:
            cl.run_pass(world, f, patch={"tokens": dp.default_tools(f, lambda line: None).tokens})
            raise AssertionError("the kill did not reach the caller")
        except cl.Killed:
            pass
        state = json.loads((root / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert state["description_pass"]["tokens_before"] == 6000, state.get("description_pass")
        assert "started_elapsed" in state["description_pass"] and state.get("llm_tokens_total") == 6000

        second, _done = resumed_factory(root, world)
        second.llm = FakeLLM(0)
        world.author_raise = {}
        world.green = {cl.T3A, cl.T3B}
        p, summary, lines = cl.run_pass(world, second, patch={"tokens": dp.default_tools(second, lambda line: None).tokens})
        assert summary["status"] == "done" and summary["token_budget"] == 3000, (summary, lines)

@case
def a_foundation_retry_that_stays_red_leaves_the_first_attempts_sources():
    for failure in (False, RuntimeError("the retry crashed")):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frontend" / "src").mkdir(parents=True)
            (root / "backend" / "src").mkdir(parents=True)
            (root / rb.APP).write_text("template", encoding="utf-8")
            f, calls = cl._engine({"A": [False, failure], "B": [True]})
            f.project_dir, f.restore_failures = root, 0
            inner = f.process_node

            def process_node(node, nodes_left, retry=False, inner=inner, f=f):
                if node["id"] == "A":
                    (root / rb.APP).write_text("worse" if retry else "first attempt", encoding="utf-8")
                    if retry:
                        (root / "frontend" / "src" / "Extra.tsx").write_text("x", encoding="utf-8")
                result = inner(node, nodes_left, retry=retry)
                if node["id"] == "A":
                    f.report["nodes"]["A"]["written"] = [rb.APP] + (["frontend/src/Extra.tsx"] if retry else [])
                return result
            f.process_node = process_node
            result, lines = cl._queue(f, "A", "B")
            assert calls == ["A", "A+retry", "B"] and result is False, calls
            assert (root / rb.APP).read_text(encoding="utf-8") == "first attempt", failure
            assert not (root / "frontend" / "src" / "Extra.tsx").exists(), failure
            assert f.report["nodes"]["A"]["written"] == [rb.APP], f.report["nodes"]["A"]
            assert any("put back to the state the first attempt left" in line for line in lines), lines

@case
def the_closing_pass_gate_compares_first_error_signatures_too():

    from agent.description_pass import WHOLE, new_red

    base, now = {"F.spec.js": ["sign in works"]}, {"F.spec.js": ["sign in works"]}
    same = {"F.spec.js": {"sign in works": "Error: Username format is invalid @ F.spec.js:13:5"}}
    other = {"F.spec.js": {"sign in works": "Error: Signed in as alice is not visible @ F.spec.js:13:5"}}

    assert new_red(now, base) == {}

    assert new_red(now, base, same, same) == {}

    assert new_red(now, base, other, same) == {"F.spec.js": ["sign in works"]}
    assert new_red(now, base, {"F.spec.js": {}}, same) == {"F.spec.js": ["sign in works"]}
    assert new_red(now, base, same, {}) == {"F.spec.js": ["sign in works"]}

    assert new_red({"G.spec.js": WHOLE}, base, {}, {}) == {"G.spec.js": WHOLE}

import cases_audit43b

CASES += [function for function in cases_audit43b.CASES if function not in CASES]

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
    print(f"{len(CASES) - failed}/{len(CASES)} audit-42b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
