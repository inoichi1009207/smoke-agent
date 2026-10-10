from __future__ import annotations

import inspect
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []
PAGE = "frontend/src/pages/EditorPage.tsx"
SPEC = "backend/test-e2e/REQ-9.spec.js"

def case(function):
    CASES.append(function)
    return function

def block(path: str, body: str) -> str:
    return "=== FILE: " + path + " ===" + NL + body + NL + "=== END FILE ==="

def outcome(passed: bool, text: str):
    from agent.testrun import TestOutcome

    return TestOutcome(passed, text)

def own_red():
    return outcome(False, "E2E FAILED against the real application" + NL + "FAILED SPEC FILES: REQ-9.spec.js" + NL
                   + "  1) test-e2e/REQ-9.spec.js:3:1 › REQ-9: the grid shows the value" + NL
                   + "    Error: expect(locator).toContainText(expected) failed" + NL)

NODE = {"id": "REQ-9", "name": "Grid", "description": "The grid shows every stored value in its cell."}
TESTS = {SPEC: 'const { test } = require("@playwright/test");' + NL + 'test("REQ-9: the grid shows the value", async ({ page }) => {'
         + NL + '  await expect(cell("A1")).toContainText("stored value");' + NL + "});" + NL}

class Clock:

    def __init__(self):
        self.mono, self.run = 500.0, 1_700_000_000.0

    def monotonic(self) -> float:
        return self.mono

    def now(self) -> float:
        return self.run

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.run += seconds

class patched:

    def __init__(self, clock: Clock, urlopen=None):
        self.clock, self.urlopen = clock, urlopen

    def __enter__(self):
        import time
        import urllib.request
        from agent import clock as run_clock
        from agent import llm
        from agent.llm import LLM

        self.saved = (llm._clock, run_clock.now, urllib.request.urlopen, time.sleep, LLM.global_end)
        llm._clock = self.clock.monotonic
        run_clock.now = self.clock.now
        if self.urlopen is not None:
            urllib.request.urlopen = self.urlopen
        time.sleep = self.clock.advance
        LLM.global_end = None
        return self

    def __exit__(self, *exc):
        import time
        import urllib.request
        from agent import clock as run_clock
        from agent import llm
        from agent.llm import LLM

        llm._clock, run_clock.now, urllib.request.urlopen, time.sleep, LLM.global_end = self.saved
        return False

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

class Model:

    def __init__(self, answers, cut=(), clock: Clock | None = None, step_sec: float = 0.0):
        self.answers, self.cut, self.clock, self.step_sec = list(answers), set(cut), clock, step_sec
        self.writes = 0
        self.calls: list[tuple[str, dict]] = []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        from agent.llm import CallDeadlineReached

        tag = str(options.get("tag", ""))
        step = tag.split(":", 2)[2] if tag.count(":") >= 2 else tag
        self.calls.append((step, dict(options)))
        assert len(self.calls) <= 40, [c[0] for c in self.calls]
        if self.clock is not None:
            self.clock.advance(self.step_sec)
        if step in self.cut:
            raise CallDeadlineReached(f"the call deadline was reached: the model call reached its total time limit ({step})")
        self.last_finish_reason = "stop"
        if ":diagnose" in tag or tag.endswith(":decide"):
            return {"content": PAGE + " -- render the stored value"}
        answer = self.answers[min(self.writes, len(self.answers) - 1)]
        self.writes += 1
        return {"content": answer}

    def steps(self) -> list[str]:
        return [step for step, _options in self.calls]

def make(model, project: Path, outcomes, logs=None, **kw):
    from agent.codegen import CodegenImplementer

    pending = list(outcomes)

    def check():
        return pending.pop(0) if len(pending) > 1 else pending[0]

    kw.setdefault("max_rounds", 4)
    kw.setdefault("extra_rounds", 0)
    return CodegenImplementer(model, project, check, log=(logs.append if logs is not None else (lambda m: None)), **kw)

def client():
    from agent.llm import LLM

    c = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lambda line: None)
    c.call_limit_sec = 1800
    return c

class HeartbeatStream:

    def __init__(self, clock: Clock, step: float):
        self.clock, self.step, self.lines = clock, step, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        while self.lines < 100000:
            self.clock.advance(self.step)
            self.lines += 1
            yield b": keep-alive\n"

@case
def an_end_earlier_than_the_global_end_cuts_the_stream_there_and_nothing_follows():
    from agent.llm import LLM, CallDeadlineReached

    clock = Clock()
    opened: list = []

    def urlopen(request, timeout=None):
        opened.append((bool(json.loads(request.data.decode("utf-8")).get("stream")), timeout))
        return HeartbeatStream(clock, 7.0)

    with patched(clock, urlopen):
        LLM.global_end = clock.mono + 1000.0
        began = clock.mono
        try:
            client().chat([{"role": "user", "content": "fix"}], thinking=False, expect_marker=(("=== FILE:",), 30000), end=clock.mono + 100.0)
            raise AssertionError("cut expected")
        except CallDeadlineReached as exc:
            assert "total time limit" in str(exc), exc
        assert clock.mono - began <= 100.0 + 7.0, clock.mono - began
        assert opened == [(True, 100.0)], opened

@case
def an_end_later_than_the_global_end_changes_nothing():
    from agent.llm import LLM, CallDeadlineReached, LLMError

    clock = Clock()
    with patched(clock, lambda request, timeout=None: HeartbeatStream(clock, 7.0)):
        LLM.global_end = clock.mono + 100.0
        began = clock.mono
        try:
            client().chat([{"role": "user", "content": "fix"}], thinking=False, expect_marker=(("=== FILE:",), 30000), end=clock.mono + 500.0)
            raise AssertionError("cut expected")
        except LLMError as exc:

            assert "total time limit" in str(exc) and not isinstance(exc, CallDeadlineReached), repr(exc)
        assert clock.mono - began <= 100.0 + 7.0, clock.mono - began

@case
def retries_stop_at_the_end_and_raise_the_call_deadline():
    from agent.llm import CallDeadlineReached

    clock = Clock()
    timeouts: list = []

    def refused(request, timeout=None):
        timeouts.append(timeout)
        clock.advance(min(10.0, timeout))
        raise ConnectionResetError("connection reset by peer")

    with patched(clock, refused):
        began = clock.mono
        try:
            client().chat([{"role": "user", "content": "x"}], end=clock.mono + 40.0)
            raise AssertionError("expected to fail")
        except CallDeadlineReached as exc:
            assert "total time limit" in str(exc), exc
        assert clock.mono - began <= 40.0, clock.mono - began
        assert 1 <= len(timeouts) < 5 and all(t <= 40.0 for t in timeouts), timeouts

@case
def a_slow_answer_read_times_out_at_the_end_without_a_second_wait():
    from agent.llm import CallDeadlineReached

    clock = Clock()
    timeouts: list = []

    def slow(request, timeout=None):
        timeouts.append(timeout)
        clock.advance(timeout)
        raise TimeoutError("The read operation timed out")

    with patched(clock, slow):
        try:
            client().chat([{"role": "user", "content": "x"}], end=clock.mono + 250.0)
            raise AssertionError("expected to fail")
        except CallDeadlineReached:
            pass
        assert timeouts == [250.0], timeouts

@case
def a_bad_request_under_an_own_end_stays_an_ordinary_error():
    import io
    import urllib.error
    from agent.llm import CallDeadlineReached, LLMError

    clock = Clock()

    def bad(request, timeout=None):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": "max_tokens too large"}'))

    with patched(clock, bad):
        try:
            client().chat([{"role": "user", "content": "x"}], end=clock.mono + 100.0)
            raise AssertionError("expected to fail")
        except LLMError as exc:
            assert "HTTP 400" in str(exc) and not isinstance(exc, CallDeadlineReached), repr(exc)

@case
def without_an_end_the_call_is_as_before():
    from agent.llm import LLM

    clock = Clock()
    timeouts: list = []

    class Answer:
        def __init__(self):
            self.raw = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.raw

    def answer(request, timeout=None):
        timeouts.append(timeout)
        return Answer()

    with patched(clock, answer):
        assert client().chat([{"role": "user", "content": "x"}])["content"] == "ok"
        assert client().chat([{"role": "user", "content": "x"}], end=None)["content"] == "ok"
        LLM.global_end = clock.mono + 50.0
        assert client().chat([{"role": "user", "content": "x"}], end=clock.mono + 500.0)["content"] == "ok"
    assert timeouts == [600, 600, 50.0], timeouts

@case
def without_a_call_deadline_no_call_gets_an_end():
    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")])
        implementer = make(model, Path(tmp), [own_red()], max_rounds=2)
        assert implementer.call_deadline is None
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps()[:3] == ["gen0", "diagnose1", "repair1"], model.steps()
        assert all("end" not in options for _step, options in model.calls), model.calls
        assert result.status == "budget" and result.stop_reason in ("rounds", "stuck"), result

@case
def with_a_call_deadline_every_call_gets_it_on_the_clients_clock():
    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")], clock=clock, step_sec=10.0)
        implementer = make(model, Path(tmp), [own_red()], max_rounds=2)
        implementer.call_deadline = clock.run + 3600.0
        mono_deadline = clock.mono + 3600.0
        implementer.implement(NODE, TESTS, own_red())
        assert {"gen0", "diagnose1", "repair1"} <= set(model.steps()), model.steps()
        for step, options in model.calls:

            assert abs(options["end"] - mono_deadline) < 1e-6, (step, options.get("end"), mono_deadline)

@case
def no_round_starts_past_the_call_deadline():
    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")])
        implementer = make(model, Path(tmp), [own_red()])
        implementer.call_deadline = clock.run - 1.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.calls == [], model.steps()
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result

    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")], clock=clock, step_sec=100.0)
        implementer = make(model, Path(tmp), [own_red()], max_rounds=6)
        implementer.call_deadline = clock.run + 250.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps() == ["gen0", "diagnose1", "repair1"], model.steps()
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result

@case
def a_writing_call_cut_by_the_call_deadline_ends_the_implementer_without_a_crash():
    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")], cut={"repair1"})
        implementer = make(model, Path(tmp), [own_red()], max_rounds=6)
        implementer.call_deadline = clock.run + 3600.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps() == ["gen0", "diagnose1", "repair1"], model.steps()
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result
        assert result.written == [PAGE], result.written

@case
def a_diagnosis_cut_by_the_call_deadline_starts_no_writing_call():
    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        model = Model([block(PAGE, "x")], cut={"diagnose1"})
        implementer = make(model, Path(tmp), [own_red()], logs=logs, max_rounds=6)
        implementer.call_deadline = clock.run + 3600.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps() == ["gen0", "diagnose1"], model.steps()
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result

@case
def a_generation_cut_ends_at_once_too():
    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")], cut={"gen0"})
        implementer = make(model, Path(tmp), [own_red()], max_rounds=6)
        implementer.call_deadline = clock.run + 3600.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps() == ["gen0"], model.steps()
        assert (result.status, result.stop_reason, result.written) == ("budget", "deadline", []), result

@case
def the_real_client_under_a_call_deadline_ends_the_implementer_at_it():
    from agent.llm import LLM

    clock = Clock()
    opened: list = []

    def slow(request, timeout=None):
        opened.append(timeout)
        clock.advance(timeout)
        raise TimeoutError("The read operation timed out")

    with patched(clock, slow), State(), tempfile.TemporaryDirectory() as tmp:
        LLM.global_end = clock.mono + 80000.0
        implementer = make(client(), Path(tmp), [own_red()], max_rounds=6)
        implementer.call_deadline = clock.run + 300.0
        began = clock.run
        result = implementer.implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result
        assert clock.run - began <= 300.0 + 1.0, clock.run - began
        assert opened == [300.0], opened

UNIT_CLAIM_HEAD = ("SPEC: test-e2e/REQ-2-2-1.spec.js:44 (test 3) — expectation `D4 contains \"800\"` contradicts the requirement: "
                   "the typed formula `=B3` references empty B3 (800 is ")
AUDIT_CLAIM = ('"await expect(locate(page, { role: "gridcell", name: "D4" })).toContainText("800");" — the requirement states '
               '"On insertion, the target row and all subsequent complete records, validation rules, and formula references '
               'shift downward together".')
V144_SPEC = {"backend/test-e2e/REQ-2-2-1.spec.js": (
    'test("REQ-2-2-1: formula inside inserted region", async ({ page }) => {' + NL
    + '  await expect(locate(page, { role: "gridcell", name: "D4" })).toContainText("800");' + NL + "});" + NL)}
V144_REQUIREMENT = ("On insertion, the target row and all subsequent complete records, validation rules, and formula references "
                    "shift downward together.")

V144_OUTPUT = ("  1) test-e2e/REQ-2-2-1.spec.js:63:1 › REQ-2-2-1: formula above the insertion stays but its reference adjusts" + NL
               + "    Error: expect(locator).toContainText(expected) failed" + NL
               + '    > 72 |   await expect(locate(page, { role: "gridcell", name: "D1" })).toContainText("800");' + NL)

@case
def the_v144_claims_through_the_one_gate_the_paraphrase_is_refused_the_verbatim_step_accepted():
    from agent.codegen import claim_quotes, spec_evidence

    assert not spec_evidence(UNIT_CLAIM_HEAD, V144_REQUIREMENT, V144_SPEC, V144_OUTPUT)
    assert spec_evidence(AUDIT_CLAIM, V144_REQUIREMENT, V144_SPEC, V144_OUTPUT)

    quotes = claim_quotes(AUDIT_CLAIM)
    assert not any("on insertion" in q for q in quotes), quotes
    assert not spec_evidence(AUDIT_CLAIM, V144_REQUIREMENT, V144_SPEC, "")

    assert not any(q in "await expect(locate(page, { role: 'gridcell', name: 'd4' })).tocontaintext('800');"
                   for q in claim_quotes(UNIT_CLAIM_HEAD)), claim_quotes(UNIT_CLAIM_HEAD)

@case
def the_diagnosis_gate_and_the_test_defect_gate_call_the_same_check_with_the_same_arguments():
    from agent import codegen

    source = inspect.getsource(codegen.CodegenImplementer.implement)
    calls = re.findall(r"spec_evidence\(([^\n]*)\)", source)
    args = [call.split(",", 1)[1].strip() for call in calls]
    assert len(args) == 2, calls
    assert args[0] == args[1] == "describe_node(node), test_files, own_output(outcome.output)", args

@case
def the_unit_phase_refuses_the_paraphrase_and_accepts_the_verbatim_block():
    node = {"id": "REQ-2-2-1", "name": "Rows", "description": V144_REQUIREMENT}
    red = outcome(False, "E2E FAILED against the real application" + NL + "FAILED SPEC FILES: REQ-2-2-1.spec.js" + NL + V144_OUTPUT)

    class Blames(Model):
        def chat(self, messages, **options):
            if ":diagnose" in str(options.get("tag", "")):
                self.calls.append(("diagnose", dict(options)))
                return {"content": UNIT_CLAIM_HEAD}
            return super().chat(messages, **options)

    defect = "=== TEST DEFECT ===" + NL + AUDIT_CLAIM + NL + "=== END TEST DEFECT ==="
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        result = make(Blames([block(PAGE, "x"), defect]), Path(tmp), [red], logs=logs).implement(node, V144_SPEC, red)
        assert any("a SPEC verdict without verifiable quotes is not accepted" in line for line in logs), logs
        assert result.status == "test_defect" and result.detail == AUDIT_CLAIM, result

ANSWER_JSON = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode("utf-8")

class Answer:

    def __init__(self, clock: Clock | None = None, late: float = 0.0):
        self.clock, self.late = clock, late

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if self.clock is not None:
            self.clock.advance(self.late)
        return ANSWER_JSON

class TrickleServer:

    def __init__(self, trickle_headers: bool = True):
        import http.server
        import threading
        import time as real_time

        sleep = real_time.sleep
        headers_slow = trickle_headers

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK" + b"\r\n")
                    self.wfile.flush()
                    for index in range(12 if headers_slow else 0):
                        sleep(0.1)
                        self.wfile.write(("X-Pad-" + str(index) + ": x").encode("ascii") + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"Content-Type: application/json" + b"\r\n" + b"Connection: close" + b"\r\n"
                                     + b"Content-Length: " + str(len(ANSWER_JSON)).encode("ascii") + b"\r\n" + b"\r\n" + ANSWER_JSON)
                    self.wfile.flush()
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return "http://127.0.0.1:" + str(self.server.server_address[1]) + "/v1"

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        return False

@case
def r52_1_a_bound_call_is_cut_while_the_headers_trickle_and_an_unbound_one_is_as_before():
    import time as real_time
    from agent.llm import LLM, CallDeadlineReached

    saved = LLM.global_end
    LLM.global_end = None
    try:
        with TrickleServer() as base:
            gateway = LLM(base, "key", "glm-test", timeout=10, log=lambda line: None)
            gateway.call_limit_sec = 1800
            from agent import llm

            began = real_time.monotonic()
            try:
                gateway.chat([{"role": "user", "content": "x"}], end=llm._clock() + 0.3)
                raise AssertionError("the trickling headers were waited for past the end")
            except CallDeadlineReached as exc:
                assert "total time limit" in str(exc), exc
            elapsed = real_time.monotonic() - began
            assert elapsed < 0.3 + 0.6, elapsed

            began = real_time.monotonic()
            assert gateway.chat([{"role": "user", "content": "x"}])["content"] == "ok"
            assert real_time.monotonic() - began >= 1.0
    finally:
        LLM.global_end = saved

@case
def r52_1_a_bound_call_whose_answer_arrives_after_the_end_is_refused():
    from agent.llm import CallDeadlineReached

    clock = Clock()
    with patched(clock, lambda request, timeout=None: Answer(clock, late=30.0)):
        try:
            client().chat([{"role": "user", "content": "x"}], end=clock.mono + 20.0)
            raise AssertionError("a late answer was returned")
        except CallDeadlineReached as exc:
            assert "arrived after it" in str(exc), exc

    clock = Clock()
    with patched(clock, lambda request, timeout=None: Answer(clock, late=10.0)):
        assert client().chat([{"role": "user", "content": "x"}], end=clock.mono + 20.0)["content"] == "ok"

@case
def r52_1_a_bound_attempt_never_waits_longer_than_the_time_left():
    clock = Clock()
    timeouts: list = []

    def answer(request, timeout=None):
        timeouts.append(timeout)
        return Answer()

    with patched(clock, answer):
        client().chat([{"role": "user", "content": "x"}], end=clock.mono + 0.3)
        client().chat([{"role": "user", "content": "x"}], thinking=False, expect_marker=(("=== FILE:",), 30000), end=clock.mono + 0.25)

    assert [round(t, 6) for t in timeouts[:2]] == [0.3, 0.25], timeouts

@case
def r52_6_a_second_read_timeout_that_used_up_the_time_left_is_the_call_deadline():
    from agent.llm import CallDeadlineReached

    clock = Clock()
    timeouts: list = []

    def slow(request, timeout=None):
        timeouts.append(timeout)
        clock.advance(timeout)
        raise TimeoutError("The read operation timed out")

    with patched(clock, slow):
        gateway = client()
        gateway.timeout = 10
        try:
            gateway.chat([{"role": "user", "content": "x"}], end=clock.mono + 22.0)
            raise AssertionError("expected to fail")
        except CallDeadlineReached as exc:
            assert "read timeout" in str(exc), exc
    assert timeouts == [10.0, 7.0], timeouts

    clock = Clock()
    timeouts = []

    def early(request, timeout=None):
        timeouts.append(timeout)
        clock.advance(timeout - 0.01)
        raise TimeoutError("The read operation timed out")

    with patched(clock, early):
        gateway = client()
        gateway.timeout = 10
        try:
            gateway.chat([{"role": "user", "content": "x"}], end=clock.mono + 22.0)
            raise AssertionError("expected to fail")
        except CallDeadlineReached as exc:
            assert "read timeout" in str(exc), exc
    assert len(timeouts) == 2 and timeouts[1] < 10.0, timeouts

@case
def r52_6_a_retry_that_would_start_past_the_end_is_the_call_deadline_by_its_type():
    from agent.llm import CallDeadlineReached

    clock = Clock()
    with patched(clock, lambda request, timeout=None: (_ for _ in ()).throw(ConnectionResetError("connection reset by peer"))):
        began = clock.mono
        try:
            client().chat([{"role": "user", "content": "x"}], end=clock.mono + 60.0)
            raise AssertionError("expected to fail")
        except CallDeadlineReached as exc:
            assert "after 5 attempt(s)" in str(exc), exc
        assert clock.mono - began < 60.0, clock.mono - began

@case
def r52_6_a_gateway_error_that_merely_mentions_the_words_stays_an_ordinary_error():
    import io
    import urllib.error
    from agent.llm import CallDeadlineReached, LLMError

    clock = Clock()

    def bad(request, timeout=None):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": "the model call reached its total time limit"}'))

    with patched(clock, bad):
        try:
            client().chat([{"role": "user", "content": "x"}], end=clock.mono + 100.0)
            raise AssertionError("expected to fail")
        except LLMError as exc:
            assert "HTTP 400" in str(exc) and not isinstance(exc, CallDeadlineReached), repr(exc)

def recording_check(results, calls):
    def check():
        calls.append(1)
        return results[min(len(calls) - 1, len(results) - 1)]
    return check

@case
def r52_2_a_generation_answer_after_the_call_deadline_is_neither_written_nor_checked():
    from agent.codegen import CodegenImplementer

    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        checks: list = []
        model = Model([block(PAGE, "x")], clock=clock, step_sec=200.0)
        implementer = CodegenImplementer(model, Path(tmp), recording_check([own_red()], checks), log=lambda m: None,
                                         max_rounds=4, extra_rounds=0)
        implementer.call_deadline = clock.run + 100.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps() == ["gen0"], model.steps()
        assert checks == [], checks
        assert not (Path(tmp) / PAGE).exists()
        assert (result.status, result.stop_reason, result.written) == ("budget", "deadline", []), result

@case
def r52_2_a_late_repair_answer_leaves_the_last_checked_state_on_disk():
    from agent.codegen import CodegenImplementer

    clock = Clock()
    with patched(clock), State(), tempfile.TemporaryDirectory() as tmp:
        checks: list = []
        model = Model([block(PAGE, "first"), block(PAGE, "late")], clock=clock, step_sec=100.0)
        implementer = CodegenImplementer(model, Path(tmp), recording_check([own_red()], checks), log=lambda m: None,
                                         max_rounds=4, extra_rounds=0)
        implementer.call_deadline = clock.run + 250.0
        result = implementer.implement(NODE, TESTS, own_red())
        assert model.steps() == ["gen0", "diagnose1", "repair1"], model.steps()
        assert len(checks) == 1, checks
        assert (Path(tmp) / PAGE).read_text(encoding="utf-8").strip() == "first"
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result

    with State(), tempfile.TemporaryDirectory() as tmp:
        checks = []
        model = Model([block(PAGE, "first"), block(PAGE, "late")])
        implementer = CodegenImplementer(model, Path(tmp), recording_check([own_red()], checks), log=lambda m: None,
                                         max_rounds=1, extra_rounds=0)
        implementer.implement(NODE, TESTS, own_red())
        assert len(checks) == 2 and (Path(tmp) / PAGE).read_text(encoding="utf-8").strip() == "late", checks

V145_LLM = ROOT.parent / "smoke-agent-v145-shipped" / "agent" / "llm.py"

def gateway_scenarios():
    import io
    import urllib.error

    def ok(clock, log, request, timeout, state):
        return Answer()

    def read_timeouts(clock, log, request, timeout, state):
        clock.advance(timeout)
        raise TimeoutError("The read operation timed out")

    def resets(clock, log, request, timeout, state):
        clock.advance(3.0)
        raise ConnectionResetError("connection reset by peer")

    def bad400(clock, log, request, timeout, state):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": "max_tokens too large"}'))

    def quota_then_ok(clock, log, request, timeout, state):
        state["n"] = state.get("n", 0) + 1
        if state["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "quota", {}, io.BytesIO(b'{"error": "Free quota exhausted and balance too low"}'))
        return Answer()

    def heartbeat(clock, log, request, timeout, state):
        if json.loads(request.data.decode("utf-8")).get("stream"):
            return HeartbeatStream(clock, 7.0)
        return Answer()

    return [("ok", ok, None, False), ("read_timeouts", read_timeouts, None, False), ("resets", resets, None, False),
            ("bad400", bad400, None, False), ("quota_then_ok", quota_then_ok, None, False),
            ("stream_heartbeat", heartbeat, 100.0, True), ("near_global_end", ok, 0.5, False),
            ("global_end_cuts_retries", resets, 20.0, False)]

def drive(module, gateway, global_after, streamed, end_kw):
    import time
    import urllib.request

    clock = Clock()
    log: list = []
    state: dict = {}
    klass = module.LLM
    saved = (module._clock, urllib.request.urlopen, time.sleep, klass.global_end, klass.quota_waited_sec,
             klass.quota_exhausted, klass.streamed, klass.cutoffs, klass.cutoff_disabled)

    def urlopen(request, timeout=None):
        log.append(("open", timeout, request.full_url, request.data, dict(request.header_items())))
        return gateway(clock, log, request, timeout, state)

    def sleep(seconds):
        log.append(("sleep", seconds))
        clock.advance(seconds)

    module._clock, urllib.request.urlopen, time.sleep = clock.monotonic, urlopen, sleep
    klass.global_end = None if global_after is None else clock.mono + global_after
    klass.quota_waited_sec, klass.quota_exhausted, klass.streamed, klass.cutoffs, klass.cutoff_disabled = 0, False, 0, 0, False
    try:
        gateway_client = klass("http://gateway.invalid/v1", "key", "glm-test", log=lambda line: log.append(("log", line)))
        gateway_client.call_limit_sec = 1000
        options = dict(thinking=False, expect_marker=(("=== FILE:",), 30000)) if streamed else {}
        try:
            out = ("answer", gateway_client.chat([{"role": "user", "content": "x"}], tag="t", **options, **end_kw)["content"])
        except module.LLMError as exc:
            out = ("error", str(exc))
        return log, out, clock.mono
    finally:
        (module._clock, urllib.request.urlopen, time.sleep, klass.global_end, klass.quota_waited_sec,
         klass.quota_exhausted, klass.streamed, klass.cutoffs, klass.cutoff_disabled) = saved

@case
def r52_none_deadline_makes_the_same_calls_with_the_same_arguments_as_v145():
    import importlib.util

    if not V145_LLM.is_file():
        print("      (v145 client not found at " + str(V145_LLM) + "; differential skipped)")
        return
    spec = importlib.util.spec_from_file_location("llm_v145_for_r52", V145_LLM)
    v145 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v145)
    from agent import llm as v146

    for name, gateway, global_after, streamed in gateway_scenarios():
        want = drive(v145, gateway, global_after, streamed, {})
        for end_kw in ({}, {"end": None}, {"end": 10.0 ** 9}):
            got = drive(v146, gateway, global_after, streamed, end_kw)
            assert got == want, (name, end_kw, got, want)
        assert any(entry[0] == "open" for entry in want[0]), name

    outs = {name: drive(v145, gateway, g, s, {})[1] for name, gateway, g, s in gateway_scenarios()}
    assert outs["ok"] == ("answer", "ok") and outs["quota_then_ok"] == ("answer", "ok"), outs
    assert "2 read timeouts" in outs["read_timeouts"][1] and "HTTP 400" in outs["bad400"][1], outs
    assert "total time limit" in outs["stream_heartbeat"][1] and "total time limit" in outs["global_end_cuts_retries"][1], outs

@case
def r52_source_every_time_limit_exit_raises_the_type_and_chat_never_reads_the_words():
    from agent import llm

    source = inspect.getsource(llm)
    assert not re.search(r"raise LLMError\([^)]*total time limit", source), "a time-limit exit without its type"
    chat = inspect.getsource(llm.LLM.chat)
    assert "in str(exc)" not in chat, "chat() classifies by the message's words"

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
    print(f"{len(CASES) - failed}/{len(CASES)} v146b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
