from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []
SPEC = "backend/test-e2e/REQ-9.spec.js"
BIG = "backend/src/routes/big.js"
PAGE = "frontend/src/pages/HomePage.tsx"
PLAN = "frontend/src/pages/HomePage.tsx -- render the heading the spec looks for"
DECISION = "frontend/src/pages/HomePage.tsx -- render the heading named Books"

def case(function):
    CASES.append(function)
    return function

def block(path: str, body: str) -> str:
    return f"=== FILE: {path} ===" + NL + body + NL + "=== END FILE ==="

def analysis(chars: int, mention: str = "") -> str:
    text = "The failure says the heading is missing. Maybe the route is wrong, or the store. " + (f"Check {mention} too. " if mention else "")
    return (text * (chars // len(text) + 1))[:chars]

def red(text: str = "heading not found"):
    from agent.testrun import TestOutcome

    return TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: " + text)

def green():
    from agent.testrun import TestOutcome

    return TestOutcome(True, "E2E PASSED")

def project(tmp: str) -> Path:
    root = Path(tmp)
    (root / "frontend/src").mkdir(parents=True)
    (root / "frontend/src/App.tsx").write_text("export default function App() { return null; }" + NL, encoding="utf-8")
    (root / "backend/src/routes").mkdir(parents=True)
    (root / BIG).write_text(("// route helper line" + NL) * 1600 + "// END-OF-BIG" + NL, encoding="utf-8")
    return root

class ScriptedModel:

    def __init__(self, writes):
        self.writes = list(writes)
        self.calls: list[tuple[str, str]] = []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        tag = options.get("tag", "")
        self.calls.append((tag, messages[-1]["content"]))
        assert len(self.calls) <= 40, "unbounded retries: " + str([t for t, _ in self.calls][-12:])
        self.last_finish_reason = "stop"
        if ":diagnose" in tag:
            return {"content": PLAN}
        if tag.endswith(":decide"):
            return {"content": DECISION}
        content, finish = self.writes.pop(0) if len(self.writes) > 1 else self.writes[0]
        self.last_finish_reason = finish
        return {"content": content}

    def tags(self) -> list[str]:
        return [tag.split(":", 2)[2] for tag, _ in self.calls]

    def prompts(self, tag: str) -> list[str]:
        return [prompt for t, prompt in self.calls if t.endswith(":" + tag)]

def implement(model, outcomes, max_rounds=4, extra_rounds=0):
    from agent.codegen import CodegenImplementer

    CodegenImplementer.repair_mode_broken = False
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        results = iter(outcomes)
        implementer = CodegenImplementer(model, root, lambda: next(results), max_rounds=max_rounds, extra_rounds=extra_rounds, log=lines.append)
        node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue through the link named `Books`."}
        result = implementer.implement(node, {SPEC: "await click(page, { role: 'link', name: 'Books' });"}, red())
    return result, lines

FIRST = (block(PAGE, "export default function HomePage() { return null; }"), "stop")
FIX = (block(PAGE, "export default function HomePage() { return <h1>Books</h1>; }"), "stop")

class _Stream:
    def __init__(self, text: str, finish: str = "stop"):
        pieces = [text[i:i + 400] for i in range(0, len(text), 400)]
        events = [{"choices": [{"delta": {"content": piece}}]} for piece in pieces]
        events.append({"choices": [{"delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": max(1, len(text) // 3)}})
        self.lines = [("data: " + json.dumps(event) + NL).encode("utf-8") for event in events] + [b"data: [DONE]\n"]
        self.read_lines = 0

    def __iter__(self):
        for line in self.lines:
            self.read_lines += 1
            yield line

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

class _Response:
    def __init__(self, content: str):
        self.body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}).encode("utf-8")

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

class Server:

    def __init__(self, streamed: list[str]):
        self.streamed = list(streamed)
        self.requests: list[tuple[str, str]] = []
        self.streams: list[_Stream] = []

    def urlopen(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        user = body["messages"][-1]["content"]
        system = body["messages"][0]["content"]
        assert len(self.requests) < 40, "unbounded retries"
        if body.get("stream"):
            self.requests.append(("stream", user))
            stream = _Stream(self.streamed.pop(0))
            self.streams.append(stream)
            return stream
        if "into a decision" in system:
            self.requests.append(("decide", user))
            return _Response(DECISION)
        if "DIAGNOSIS ROUND" in user:
            self.requests.append(("diagnose", user))
            return _Response(PLAN)
        self.requests.append(("write", user))
        return _Response(FIRST[0])

def run_with_server(streamed: list[str], outcomes):
    import urllib.request

    from agent.codegen import CodegenImplementer
    from agent.llm import LLM

    server = Server(streamed)
    original = urllib.request.urlopen
    saved = (LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled)
    LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = 0, 0, False
    CodegenImplementer.repair_mode_broken = False
    urllib.request.urlopen = server.urlopen
    lines: list[str] = []
    try:
        client = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lines.append)
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            results = iter(outcomes)
            implementer = CodegenImplementer(client, root, lambda: next(results), log=lines.append)
            node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue through the link named `Books`."}
            result = implementer.implement(node, {SPEC: "await click(page, { role: 'link', name: 'Books' });"}, red())
    finally:
        urllib.request.urlopen = original
        LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = saved
    return result, server, lines

@case
def a_cut_off_analysis_that_names_an_unshown_file_costs_no_round():
    from agent.codegen import PROSE_LIMIT_CHARS

    result, server, lines = run_with_server([analysis(PROSE_LIMIT_CHARS + 5000, mention="big.js"), FIX[0]], [red(), green()])
    kinds = [kind for kind, _ in server.requests]
    assert kinds == ["write", "diagnose", "stream", "decide", "stream"], kinds
    assert result.status == "passed" and result.test_runs == 2, (result.status, result.test_runs)

    assert server.streams[0].read_lines < len(server.streams[0].lines) and any("stream cut off" in line for line in lines)
    retry = server.requests[-1][1]

    assert "Fix plan (decided from your own analysis" in retry and DECISION in retry, retry[-1500:]
    assert "// END-OF-BIG" in retry and "// END-OF-BIG" not in server.requests[2][1]
    assert any("names files it was not shown ['backend/src/routes/big.js']" in line for line in lines), lines

@case
def a_real_request_for_an_unshown_file_still_costs_its_round():
    need = ("=== NEED FILES ===" + NL + BIG + NL + "=== END NEED FILES ===", "stop")
    model = ScriptedModel([FIRST, need, FIX])
    result, lines = implement(model, [red(), green()])
    assert model.tags() == ["gen0", "diagnose1", "repair1", "diagnose2", "repair2"], model.tags()
    assert result.status == "passed" and result.test_runs == 3
    assert "// END-OF-BIG" in model.prompts("repair2")[0] and not any("names files it was not shown" in line for line in lines)

@case
def cut_offs_are_retried_free_at_most_free_cutoffs_times():
    from agent.codegen import FREE_CUTOFFS

    assert FREE_CUTOFFS == 3
    prose = (analysis(4000), "aborted")
    model = ScriptedModel([FIRST, prose])
    result, _ = implement(model, [red()] * 10, max_rounds=1)
    writes = [t for t in model.tags() if t.startswith(("gen", "repair"))]
    assert writes == ["gen0"] + ["repair1"] * (FREE_CUTOFFS + 1), writes
    assert model.tags().count("decide") == FREE_CUTOFFS and result.status == "gave_up", (model.tags(), result.status)
    model = ScriptedModel([FIRST, prose])
    result, _ = implement(model, [red()] * 20, max_rounds=4)
    writes = [t for t in model.tags() if t.startswith(("gen", "repair"))]
    assert len(writes) == 1 + 4 + FREE_CUTOFFS and result.status == "gave_up", (writes, result.status)

@case
def a_cut_off_that_names_files_uses_the_same_bounded_allowance():
    from agent.codegen import FREE_CUTOFFS

    prose = (analysis(4000, mention="big.js"), "aborted")
    model = ScriptedModel([FIRST, prose])
    result, _ = implement(model, [red()] * 20, max_rounds=2)
    writes = [t for t in model.tags() if t.startswith(("gen", "repair"))]
    assert len(writes) == 1 + 2 + FREE_CUTOFFS, writes
    assert result.status == "gave_up"

@case
def a_need_files_request_for_fixed_files_is_answered_once_without_a_round():
    need = ("=== NEED FILES ===" + NL + "backend/src/database/seed_db.js" + NL + "backend/tests/REQ-1.test.js" + NL
            + "frontend/src/App.tsx" + NL + "frontend/src/pages/Missing.tsx" + NL + "=== END NEED FILES ===", "stop")
    model = ScriptedModel([FIRST, need, FIX])
    result, lines = implement(model, [red(), green()])
    assert model.tags() == ["gen0", "diagnose1", "repair1", "repair1"], model.tags()
    assert result.status == "passed" and result.test_runs == 2
    retry = model.prompts("repair1")[1]
    assert "backend/src/database/seed_db.js, backend/tests/REQ-1.test.js are fixed parts" in retry, retry[-1200:]
    assert "frontend/src/App.tsx are already shown in full" in retry and "frontend/src/pages/Missing.tsx do not exist" in retry
    assert PLAN in retry
    assert "got cut off" not in retry

@case
def a_second_unshowable_request_costs_a_round_as_before():
    from agent.codegen import NEED_UNSHOWN_FREE

    assert NEED_UNSHOWN_FREE == 1
    need = ("=== NEED FILES ===" + NL + "backend/src/database/seed_db.js" + NL + "=== END NEED FILES ===", "stop")
    model = ScriptedModel([FIRST, need, need, FIX])
    result, _ = implement(model, [red(), green()])
    assert model.tags() == ["gen0", "diagnose1", "repair1", "repair1", "diagnose2", "repair2"], model.tags()
    assert result.status == "passed" and result.test_runs == 3

    model = ScriptedModel([FIRST, need])
    result, _ = implement(model, [red()] * 10, max_rounds=2)
    assert len([t for t in model.tags() if t.startswith(("gen", "repair"))]) == 1 + 2 + NEED_UNSHOWN_FREE, model.tags()

@case
def a_long_analysis_before_the_first_file_is_still_read_and_written():
    from agent.codegen import PROSE_LIMIT_CHARS

    assert PROSE_LIMIT_CHARS == 30000
    late = analysis(PROSE_LIMIT_CHARS - 2000) + NL + FIX[0]
    result, server, lines = run_with_server([late], [red(), green()])
    kinds = [kind for kind, _ in server.requests]
    assert kinds == ["write", "diagnose", "stream"], kinds
    assert result.status == "passed" and result.test_runs == 2 and not any("stream cut off" in line for line in lines)
    assert server.streams[0].read_lines == len(server.streams[0].lines)

@case
def an_answer_with_files_after_a_cut_off_ends_the_retries():
    prose = (analysis(4000), "aborted")
    model = ScriptedModel([FIRST, prose, FIX])
    result, _ = implement(model, [red(), green()])
    assert model.tags() == ["gen0", "diagnose1", "repair1", "decide", "repair1"], model.tags()
    assert result.status == "passed" and result.test_runs == 2

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
    print(f"{len(CASES) - failed}/{len(CASES)} no-files cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
