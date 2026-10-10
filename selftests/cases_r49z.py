from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []
PAGE = "frontend/src/pages/HomePage.tsx"
FILES = "=== FILE: " + PAGE + " ===" + NL + "export default function HomePage() { return null; }" + NL + "=== END FILE ==="
PLAN = PAGE + " -- render the heading the spec looks for"

def case(function):
    CASES.append(function)
    return function

def red():
    from agent.testrun import TestOutcome

    return TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-7: opens" + NL + "Error: heading not found")

def green():
    from agent.testrun import TestOutcome

    return TestOutcome(True, "E2E PASSED")

class Model:

    def __init__(self, eat: bool = True):
        self.eat = eat
        self.calls: list[tuple[str, object]] = []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        tag = str(options.get("tag", ""))
        step = tag.split(":", 2)[2] if tag.count(":") >= 2 else tag
        self.calls.append((step, options.get("thinking")))
        assert len(self.calls) <= 30, self.calls
        self.last_finish_reason = "stop"
        if ":diagnose" in tag:
            return {"content": PLAN}
        if tag.endswith(":decide"):
            return {"content": PLAN}
        if step == "gen0" and options.get("thinking") and self.eat:
            self.last_finish_reason = "length"
            return {"content": "", "reasoning_content": "Let me think. " * 50}
        return {"content": FILES}

    def flags(self, step: str) -> list:
        return [flag for s, flag in self.calls if s == step]

class State:

    def __enter__(self):
        from agent.codegen import CodegenImplementer
        from agent.settings import SETTINGS

        self.batches = set(CodegenImplementer.all_thinking_batches)
        self.broken = CodegenImplementer.repair_mode_broken
        self.settings = {k: SETTINGS.get(k) for k in ("implementer_thinking", "repair_thinking", "repair_diagnosis")}
        CodegenImplementer.repair_mode_broken = False
        SETTINGS.update(implementer_thinking=True, repair_thinking=False, repair_diagnosis=True)
        return self

    def __exit__(self, *exc):
        from agent.codegen import CodegenImplementer
        from agent.settings import SETTINGS

        CodegenImplementer.all_thinking_batches.clear()
        CodegenImplementer.all_thinking_batches.update(self.batches)
        CodegenImplementer.repair_mode_broken = self.broken
        for k, v in self.settings.items():
            if v is None:
                SETTINGS.pop(k, None)
            else:
                SETTINGS[k] = v
        return False

def run(model, project: Path, node_id: str = "REQ-7", outcomes=None, logs=None, **kw):
    from agent.codegen import CodegenImplementer

    pending = list(outcomes or [green()])

    def check():
        return pending.pop(0) if len(pending) > 1 else pending[0]

    implementer = CodegenImplementer(model, project, check, log=(logs.append if logs is not None else (lambda m: None)), **kw)
    return implementer.implement({"id": node_id, "name": "n"}, {}, red())

@case
def a_batch_that_came_back_asks_its_first_answer_without_thinking():
    with State(), tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        first = Model()
        assert run(first, project).status == "passed"
        assert first.flags("gen0") == [True, False], first.calls
        again = Model()
        logs: list[str] = []
        assert run(again, project, logs=logs).status == "passed"
        assert again.flags("gen0") == [False], again.calls
        assert any("an earlier first answer of this batch was all thinking" in line for line in logs), logs

@case
def only_that_batch_of_that_project_is_remembered():
    with State(), tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
        assert run(Model(), Path(tmp)).status == "passed"
        neighbour = Model(eat=False)
        assert run(neighbour, Path(tmp), node_id="REQ-8").status == "passed"
        assert neighbour.flags("gen0") == [True], neighbour.calls
        elsewhere = Model(eat=False)
        assert run(elsewhere, Path(other)).status == "passed"
        assert elsewhere.flags("gen0") == [True], elsewhere.calls

@case
def a_batch_whose_first_answer_had_content_is_not_remembered():
    with State(), tempfile.TemporaryDirectory() as tmp:
        assert run(Model(eat=False), Path(tmp)).status == "passed"
        again = Model(eat=False)
        assert run(again, Path(tmp)).status == "passed"
        assert again.flags("gen0") == [True], again.calls

@case
def the_bounded_repairs_keep_their_first_answer_with_thinking():
    with State(), tempfile.TemporaryDirectory() as tmp:
        assert run(Model(), Path(tmp)).status == "passed"
        audit = Model(eat=False)
        assert run(audit, Path(tmp), max_rounds=2, extra_rounds=0).status == "passed"
        assert audit.flags("gen0") == [True], audit.calls

@case
def a_caller_that_opts_out_keeps_the_first_answer_with_thinking():
    from agent.codegen import CodegenImplementer

    with State(), tempfile.TemporaryDirectory() as tmp:
        assert run(Model(), Path(tmp)).status == "passed"
        gate = Model(eat=False)
        implementer = CodegenImplementer(gate, Path(tmp), lambda: green(), log=lambda m: None, max_rounds=3)
        implementer.recall_all_thinking = False
        assert implementer.implement({"id": "REQ-7", "name": "n"}, {}, red()).status == "passed"
        assert gate.flags("gen0") == [True], gate.calls
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "repairer.recall_all_thinking = False" in source, "the e2e gate repair must opt out"

@case
def the_diagnosis_of_a_remembered_batch_keeps_thinking():
    with State(), tempfile.TemporaryDirectory() as tmp:
        assert run(Model(), Path(tmp)).status == "passed"
        again = Model()
        result = run(again, Path(tmp), outcomes=[red(), green()])
        assert result.status == "passed", (result.status, again.calls)
        assert again.flags("gen0") == [False], again.calls
        assert again.flags("diagnose1") == [True], again.calls

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
    print(f"{len(CASES) - failed}/{len(CASES)} r49z cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
