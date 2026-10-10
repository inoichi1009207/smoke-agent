from __future__ import annotations

import io
import json
import sys
import threading
import time
import traceback
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
REAL_SLEEP = time.sleep
CASES = []

def case(function):
    CASES.append(function)
    return function

def fresh():
    from agent.llm import LLM

    LLM.global_end = None
    LLM.quota_exhausted = False
    LLM.quota_waited_sec = 0

class Switches:

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent import llm

        self.saved = {name: getattr(llm, name) for name in self.values}
        for name, value in self.values.items():
            setattr(llm, name, value)
        return self

    def __exit__(self, *args):
        from agent import llm

        for name, value in self.saved.items():
            setattr(llm, name, value)
        return False

REASONING = ["Let me ", "think about ", "the grid."]
CONTENT = ["=== FILE: a.js ===", NL + "x", NL + "=== END FILE ==="]
USAGE = {"prompt_tokens": 40, "completion_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 20},
         "prompt_tokens_details": {"cached_tokens": 8}}
WHOLE = {"choices": [{"message": {"role": "assistant", "content": "".join(CONTENT), "reasoning_content": "".join(REASONING)},
                      "finish_reason": "stop"}], "usage": USAGE}

def sse_events(stall_after: int | None = None):
    events = [{"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": piece}}]} for piece in REASONING]
    events += [{"choices": [{"index": 0, "delta": {"content": piece}}]} for piece in CONTENT]
    events += [{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}, {"choices": [], "usage": USAGE}]
    return [("data: " + json.dumps(event) + NL + NL).encode("utf-8") for event in events] + [("data: [DONE]" + NL + NL).encode("utf-8")]

def send_stream(handler, stall_after: int | None = None, stall_sec: float = 4.0):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.flush()
    for index, line in enumerate(sse_events()):
        if stall_after is not None and index == stall_after:
            REAL_SLEEP(stall_sec)
        handler.wfile.write(line)
        handler.wfile.flush()
    handler.close_connection = True

def send_json(handler, status: int, payload: bytes):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(payload)
    handler.wfile.flush()
    handler.close_connection = True

class Server:

    def __init__(self, answer):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                with outer.lock:
                    outer.bodies.append(body)
                    index = len(outer.bodies) - 1
                try:
                    answer(self, body, index)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.lock = threading.Lock()
        self.bodies: list = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "Server":
        self.thread.start()
        return self

    def __exit__(self, *args) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def streamed(self) -> list:
        return [bool(body.get("stream")) for body in self.bodies]

def client_for(server, log: list | None = None):
    from agent.llm import LLM

    c = LLM(server.url, "key", "glm-test", timeout=20, log=(log.append if log is not None else (lambda line: None)))
    c.call_limit_sec = 1800
    return c

def run_call(client, cap: float, **kwargs):
    from agent import llm

    box: dict = {"sleeps": []}

    def target():
        try:
            box["result"] = client.chat([{"role": "user", "content": "hi"}], tag="t", **kwargs)
        except BaseException as exc:
            box["error"] = exc

    saved = llm.time.sleep
    llm.time.sleep = lambda seconds: box["sleeps"].append(seconds)
    started = time.monotonic()
    try:
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(cap)
    finally:
        llm.time.sleep = saved
    return box, time.monotonic() - started, worker.is_alive()

def usage_of(client):
    return (client.prompt_tokens, client.completion_tokens, client.reasoning_tokens, client.cached_tokens, client.requests)

@case
def a_normal_thinking_stream_gives_the_same_answer_as_the_non_streamed_call():
    fresh()
    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=120.0):
        with Server(lambda h, body, i: send_stream(h)) as server:
            streamed_client = client_for(server)
            box, took, alive = run_call(streamed_client, cap=15.0)
            assert not alive and "result" in box, box
            assert server.streamed() == [True], server.streamed()
            assert server.bodies[0].get("stream_options") == {"include_usage": True}, server.bodies[0]
    with Switches(THINKING_STREAM=False):
        with Server(lambda h, body, i: send_json(h, 200, json.dumps(WHOLE).encode("utf-8"))) as server:
            plain_client = client_for(server)
            plain, _, alive = run_call(plain_client, cap=15.0)
            assert not alive and "result" in plain and server.streamed() == [False], (plain, server.streamed())
    assert box["result"] == plain["result"], (box["result"], plain["result"])
    assert box["result"]["reasoning_content"] == "".join(REASONING) and box["result"]["content"].startswith("=== FILE: a.js")
    assert (streamed_client.last_finish_reason, streamed_client.last_reasoning_chars, streamed_client.last_prompt_tokens) == \
        (plain_client.last_finish_reason, plain_client.last_reasoning_chars, plain_client.last_prompt_tokens)
    assert usage_of(streamed_client) == usage_of(plain_client) == (40, 30, 20, 8, 1), (usage_of(streamed_client), usage_of(plain_client))

@case
def a_stream_that_stalls_after_some_chunks_is_a_read_timeout_after_the_idle_time_and_is_retried_streamed():
    fresh()
    log: list = []

    def answer(handler, body, index):
        send_stream(handler, stall_after=4 if index == 0 else None)

    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=0.5):
        with Server(answer) as server:
            gateway = client_for(server, log)
            box, took, alive = run_call(gateway, cap=15.0)
    assert not alive and "result" in box, box
    assert box["result"]["content"] == "".join(CONTENT), box["result"]
    assert took < 3.5, f"{took:.1f}s: the stall was waited out instead of the idle time"
    assert server.streamed() == [True, True], server.streamed()
    assert box["sleeps"] == [5], box["sleeps"]
    assert any("attempt 1 failed (The read operation timed out (the stream sent nothing for 0.5 s))" in line for line in log), log

@case
def two_stalled_streams_are_two_read_timeouts_like_v147():
    fresh()
    from agent.llm import LLMError, CallDeadlineReached

    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=0.5):
        with Server(lambda h, body, i: send_stream(h, stall_after=2)) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
            assert not alive and isinstance(box.get("error"), LLMError), box
            assert "LLM request failed after 2 read timeouts" in str(box["error"]), box["error"]
            assert len(server.bodies) == 2 and took < 5.0, (len(server.bodies), took)

        fresh()
        from agent import llm

        with Server(lambda h, body, i: send_stream(h, stall_after=2)) as server:
            box, took, alive = run_call(client_for(server), cap=15.0, end=llm._clock() + 12.0)
            assert not alive and isinstance(box.get("error"), LLMError), box
            assert not isinstance(box["error"], CallDeadlineReached), repr(box["error"])
            assert "2 read timeouts" in str(box["error"]), box["error"]

@case
def the_idle_time_never_exceeds_the_attempts_own_timeout():
    fresh()
    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=120.0):
        with Server(lambda h, body, i: send_stream(h, stall_after=3 if i == 0 else None, stall_sec=3.0)) as server:
            gateway = client_for(server)
            gateway.timeout = 1
            box, took, alive = run_call(gateway, cap=15.0)
    assert not alive and "result" in box, box
    assert took < 3.0 and len(server.bodies) == 2, (took, len(server.bodies))

@case
def a_refused_stream_falls_back_to_the_ordinary_request():
    fresh()
    log: list = []
    refusal = b'{"error": {"message": "stream mode is not supported for this model"}}'

    def answer(handler, body, index):
        if body.get("stream"):
            send_json(handler, 400, refusal)
        else:
            send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

    with Switches(THINKING_STREAM=True):
        with Server(answer) as server:
            box, took, alive = run_call(client_for(server, log), cap=15.0)
    assert not alive and "result" in box, box
    assert box["result"]["content"] == "".join(CONTENT)
    assert server.streamed() == [True, False], server.streamed()
    assert box["sleeps"] == [], box["sleeps"]
    assert any("streamed thinking request failed before its first byte (HTTP 400)" in line for line in log), log

@case
def a_stream_that_fails_before_its_first_byte_is_retried_without_streaming():
    fresh()

    def answer(handler, body, index):
        if body.get("stream"):
            send_json(handler, 502, b'{"error": "bad gateway"}')
        else:
            send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

    with Switches(THINKING_STREAM=True):
        with Server(answer) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and "result" in box, box
    assert server.streamed() == [True, False], server.streamed()
    assert box["sleeps"] == [5], box["sleeps"]

    def drop(handler, body, index):
        if body.get("stream"):
            handler.close_connection = True
            handler.connection.shutdown(2)
        else:
            send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

    fresh()
    with Switches(THINKING_STREAM=True):
        with Server(drop) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and "result" in box, box
    assert server.streamed() == [True, False], server.streamed()

@case
def an_ordinary_4xx_on_the_stream_is_final_as_in_v147():
    fresh()
    from agent.llm import LLMError

    with Switches(THINKING_STREAM=True):
        with Server(lambda h, body, i: send_json(h, 400, b'{"error": {"message": "max_tokens is too large"}}')) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and isinstance(box.get("error"), LLMError), box
    assert str(box["error"]).startswith("HTTP 400:") and len(server.bodies) == 1, (box["error"], len(server.bodies))

@case
def a_gateway_that_ignores_stream_true_is_read_as_the_ordinary_answer():
    fresh()
    with Switches(THINKING_STREAM=True):
        with Server(lambda h, body, i: send_json(h, 200, json.dumps(WHOLE).encode("utf-8"))) as server:
            gateway = client_for(server)
            box, took, alive = run_call(gateway, cap=15.0)
    assert not alive and "result" in box, box
    assert server.streamed() == [True] and box["result"]["reasoning_content"] == "".join(REASONING), (server.streamed(), box)
    assert usage_of(gateway) == (40, 30, 20, 8, 1)

@case
def a_stream_that_ends_before_its_end_is_retried():
    fresh()
    lines = sse_events()

    def answer(handler, body, index):
        if index == 0:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Connection", "close")
            handler.end_headers()
            for line in lines[:3]:
                handler.wfile.write(line)
            handler.wfile.flush()
            handler.close_connection = True
        else:
            send_stream(handler)

    with Switches(THINKING_STREAM=True):
        with Server(answer) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and "result" in box, box
    assert box["result"]["content"] == "".join(CONTENT) and server.streamed() == [True, True], (box, server.streamed())

@case
def only_thinking_calls_without_tools_are_streamed_and_the_switch_off_streams_none():
    fresh()
    with Switches(THINKING_STREAM=True):
        with Server(lambda h, body, i: send_json(h, 200, json.dumps(WHOLE).encode("utf-8"))) as server:
            gateway = client_for(server)
            for kwargs in ({"thinking": False}, {"tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}]}):
                box, _, alive = run_call(gateway, cap=15.0, **kwargs)
                assert not alive and "result" in box, box
            assert server.streamed() == [False, False], server.streamed()
    with Switches(THINKING_STREAM=False):
        with Server(lambda h, body, i: send_json(h, 200, json.dumps(WHOLE).encode("utf-8"))) as server:
            box, _, alive = run_call(client_for(server), cap=15.0)
            assert not alive and "result" in box and server.streamed() == [False], (box, server.streamed())

class Clock:
    def __init__(self):
        self.mono = 500.0

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.mono += seconds

ANSWER = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode("utf-8")

class Answer:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return ANSWER

def quota_gateway(dry_requests: int | None):
    state = {"n": 0}

    def gateway(request, timeout=None):
        state["n"] += 1
        if dry_requests is None or state["n"] <= dry_requests:
            raise urllib.error.HTTPError("u", 429, "quota", {}, io.BytesIO(b'{"error": "Free quota exhausted and balance too low"}'))
        return Answer()

    return gateway, state

def drive(module, gateway, *, call_limit: float = 1800.0, global_after: float | None = None, end_after: float | None = None,
          quota_waited: int = 0):
    clock = Clock()
    log: list = []
    klass = module.LLM
    saved = (module._clock, urllib.request.urlopen, time.sleep, klass.global_end, klass.quota_waited_sec, klass.quota_exhausted)

    def urlopen(request, timeout=None):
        log.append(("open", timeout, request.data))
        return gateway(request, timeout)

    def sleep(seconds):
        log.append(("sleep", seconds))
        clock.advance(seconds)

    module._clock, urllib.request.urlopen, time.sleep = clock.monotonic, urlopen, sleep
    klass.global_end = None if global_after is None else clock.mono + global_after
    klass.quota_waited_sec, klass.quota_exhausted = quota_waited, False
    began = clock.mono
    try:
        c = klass("http://gateway.invalid/v1", "key", "glm-test", log=lambda line: log.append(("log", line)))
        c.call_limit_sec = call_limit
        kwargs = {} if end_after is None else {"end": clock.mono + end_after}
        try:
            out = ("answer", c.chat([{"role": "user", "content": "x"}], tag="t", **kwargs)["content"])
        except module.LLMError as exc:
            out = (type(exc).__name__, str(exc))
        return out, log, clock.mono - began, klass.quota_waited_sec, klass.quota_exhausted
    finally:
        (module._clock, urllib.request.urlopen, time.sleep, klass.global_end, klass.quota_waited_sec,
         klass.quota_exhausted) = saved

def waits(log) -> int:
    return sum(1 for entry in log if entry[0] == "sleep" and entry[1] == 120)

@case
def a_quota_outage_longer_than_ten_waits_is_waited_out_within_the_call_and_the_run_allowance():
    from agent import llm

    with Switches(QUOTA_WAIT_BY_TIME=True):
        gateway, state = quota_gateway(13)
        out, log, took, waited, breaker = drive(llm, gateway)
    assert out == ("answer", "ok"), out
    assert waits(log) == 13 and waited == 13 * 120 and not breaker, (waits(log), waited, breaker)
    lines = [entry[1] for entry in log if entry[0] == "log"]
    assert lines[0].endswith("(call 1/10, run 120/2400s)") and "(call 11, past 10: until the call's end, run 1320/2400s)" in lines[10], lines
    with Switches(QUOTA_WAIT_BY_TIME=False):
        gateway, state = quota_gateway(13)
        out, log, took, waited, breaker = drive(llm, gateway)
    assert out[0] == "QuotaExhausted" and waits(log) == 10 and not breaker, (out, waits(log), breaker)

@case
def the_calls_own_end_stops_the_waiting_with_v147s_exit_and_the_run_allowance_still_opens_the_breaker():
    from agent import llm

    with Switches(QUOTA_WAIT_BY_TIME=True):
        gateway, state = quota_gateway(None)
        out, log, took, waited, breaker = drive(llm, gateway)

        assert out[0] == "QuotaExhausted" and waits(log) == 14 and took < 1800, (out, waits(log), took)
        assert waited == 1680 and not breaker, (waited, breaker)

        gateway, state = quota_gateway(None)
        out, log, took, waited, breaker = drive(llm, gateway, quota_waited=1680)
        assert out[0] == "QuotaExhausted" and waits(log) == 6 and waited == 2400 and breaker, (out, waits(log), waited, breaker)

        gateway, state = quota_gateway(None)
        out, log, took, waited, breaker = drive(llm, gateway, global_after=300.0)
        assert out[0] == "TotalTimeLimit" and waits(log) == 2 and not breaker, (out, waits(log))

        gateway, state = quota_gateway(None)
        out, log, took, waited, breaker = drive(llm, gateway, end_after=1500.0)
        assert out[0] == "CallDeadlineReached" and waits(log) == 12 and took < 1500, (out, waits(log), took)

def send_raw(handler, pieces, content_type: str = "text/event-stream"):
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.flush()
    for piece in pieces:
        if isinstance(piece, (int, float)):
            REAL_SLEEP(piece)
            continue
        handler.wfile.write(piece)
        handler.wfile.flush()
    handler.close_connection = True

def sse(event) -> bytes:
    return ("data: " + (event if isinstance(event, str) else json.dumps(event)) + NL + NL).encode("utf-8")

def half_answer_then(error: dict) -> list:
    events = sse_events()
    return events[:4] + [sse({"choices": [{"index": 0, "delta": {"content": NL + "=== FILE: b.js ==="}}]}),
                         sse({"error": error}), sse("[DONE]")]

@case
def an_in_stream_server_error_is_retried_like_a_5xx_and_the_half_answer_is_never_returned():
    for error in ({"type": "server_error", "message": "upstream connection lost"}, {"message": "internal error"},
                  {"code": 503, "message": "service unavailable"}):
        fresh()
        log: list = []

        def answer(handler, body, index, error=error):
            if body.get("stream"):
                send_raw(handler, half_answer_then(error))
            else:
                send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

        with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=120.0):
            with Server(answer) as server:
                gateway = client_for(server, log)
                box, took, alive = run_call(gateway, cap=15.0)
        assert not alive and "result" in box, (error, box)
        assert box["result"]["content"] == "".join(CONTENT), (error, box["result"])
        assert server.streamed() == [True, False], (error, server.streamed())
        assert box["sleeps"] == [5], (error, box["sleeps"])
        assert usage_of(gateway) == (40, 30, 20, 8, 1), (error, usage_of(gateway))
        assert any("streamed thinking request sent an error event" in line for line in log), log
        assert not any("before its first byte" in line for line in log), log
        assert any("attempt 1 failed (HTTP 5" in line for line in log), log

@case
def an_error_event_as_the_first_event_is_no_empty_success():
    fresh()
    with Switches(THINKING_STREAM=True):
        def answer(handler, body, index):
            if body.get("stream"):
                send_raw(handler, [sse({"error": {"type": "server_error", "message": "x"}}), sse("[DONE]")])
            else:
                send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

        with Server(answer) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and "result" in box and box["result"]["content"] == "".join(CONTENT), box
    assert server.streamed() == [True, False] and box["sleeps"] == [5], (server.streamed(), box["sleeps"])

@case
def an_in_stream_error_takes_the_path_of_its_http_class():
    from agent.llm import LLMError

    fresh()
    with Switches(THINKING_STREAM=True, QUOTA_WAIT_BY_TIME=True):
        def quota(handler, body, index):
            if body.get("stream"):
                send_raw(handler, half_answer_then({"code": 429, "message": "Free quota exhausted and balance too low"}))
            else:
                send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

        with Server(quota) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and "result" in box and box["result"]["content"] == "".join(CONTENT), box
    assert box["sleeps"] == [120] and server.streamed() == [True, False], (box["sleeps"], server.streamed())

    fresh()
    with Switches(THINKING_STREAM=True):
        with Server(lambda h, body, i: send_raw(h, half_answer_then({"type": "invalid_request_error", "message": "bad"}))) as server:
            box, took, alive = run_call(client_for(server), cap=15.0)
    assert not alive and isinstance(box.get("error"), LLMError), box
    assert str(box["error"]).startswith("HTTP 400:") and len(server.bodies) == 1, (box["error"], len(server.bodies))

@case
def the_marker_stream_with_an_error_event_falls_back_to_the_ordinary_request():
    fresh()
    log: list = []
    partial = [sse({"choices": [{"index": 0, "delta": {"content": "=== FILE: a.js ===" + NL + "x" + NL + "=== FILE: b"}}]}),
               sse({"error": {"type": "server_error", "message": "upstream failed"}}), sse("[DONE]")]

    def answer(handler, body, index):
        if body.get("stream"):
            send_raw(handler, partial)
        else:
            send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

    with Server(answer) as server:
        gateway = client_for(server, log)
        box, took, alive = run_call(gateway, cap=15.0, thinking=False, expect_marker=(("=== FILE:",), 30000))
    assert not alive and "result" in box and box["result"]["content"] == "".join(CONTENT), box
    assert server.streamed() == [True, False], server.streamed()
    assert any("streamed request failed (the stream sent an error" in line for line in log), log

@case
def a_stall_inside_the_first_line_hits_the_idle_timeout_and_the_stream_stays_on():
    fresh()
    log: list = []
    lines = sse_events()

    def answer(handler, body, index):
        if index == 0:
            send_raw(handler, [lines[0][:6], 3.0] + [lines[0][6:]] + lines[1:])
        else:
            send_stream(handler)

    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=0.4):
        with Server(answer) as server:
            gateway = client_for(server, log)
            gateway.timeout = 2
            box, took, alive = run_call(gateway, cap=15.0)
    assert not alive and "result" in box and box["result"]["content"] == "".join(CONTENT), box
    assert server.streamed() == [True, True], server.streamed()
    assert took < 1.9, f"{took:.2f}s: the pause ran under the attempt's timeout, not the idle one"
    assert any("the stream sent nothing for 0.4 s" in line for line in log), log
    assert not any("before its first byte" in line for line in log), log

@case
def before_the_first_byte_the_attempts_own_timeout_holds():
    fresh()
    log: list = []

    def answer(handler, body, index):
        if body.get("stream"):
            send_raw(handler, [2.5])
        else:
            send_json(handler, 200, json.dumps(WHOLE).encode("utf-8"))

    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=0.2):
        with Server(answer) as server:
            gateway = client_for(server, log)
            gateway.timeout = 1
            box, took, alive = run_call(gateway, cap=15.0)
    assert not alive and "result" in box, box
    assert server.streamed() == [True, False], server.streamed()
    assert took >= 0.9, f"{took:.2f}s: the idle timeout started before the first byte"
    assert any("before its first byte" in line for line in log), log

@case
def lines_split_across_byte_chunks_are_reassembled():
    fresh()
    pieces = []
    for line in sse_events():
        pieces += [line[:3], 0.02, line[3:11], line[11:]]
    crlf = [line.replace(NL.encode(), b"\r\n") for line in sse_events()]
    no_end = sse_events()[:-1] + [b"data: [DONE]"]
    body = json.dumps(WHOLE).encode("utf-8")
    with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=5.0):
        for script, kind in ((pieces, "text/event-stream"), (crlf, "text/event-stream"), (no_end, "text/event-stream"),
                             ([body[:7], 0.02, body[7:40], body[40:]], "application/json")):
            fresh()
            with Server(lambda h, b, i, script=script, kind=kind: send_raw(h, script, kind)) as server:
                gateway = client_for(server)
                box, took, alive = run_call(gateway, cap=15.0)
            assert not alive and "result" in box, (kind, box)
            assert box["result"]["content"] == "".join(CONTENT) and box["result"]["reasoning_content"] == "".join(REASONING), box
            assert usage_of(gateway) == (40, 30, 20, 8, 1) and len(server.bodies) == 1, (usage_of(gateway), len(server.bodies))

def send_chunked(handler, lines, terminate: bool):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    for line in lines:
        handler.wfile.write(f"{len(line):x}".encode() + b"\r\n" + line + b"\r\n")
        handler.wfile.flush()
    if terminate:
        handler.wfile.write(b"0\r\n\r\n")
    handler.wfile.flush()
    handler.close_connection = True

@case
def a_chunked_stream_cut_between_chunks_ends_as_before_the_byte_reads():
    from agent.llm import LLMError

    lines = sse_events()
    for cut, want_requests in ((lines[:3], 2), (lines[:-1], 1)):
        fresh()

        def answer(handler, body, index, cut=cut):
            if index == 0:
                send_chunked(handler, cut, terminate=False)
            else:
                send_chunked(handler, lines, terminate=True)

        with Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=5.0):
            with Server(answer) as server:
                gateway = client_for(server)
                box, took, alive = run_call(gateway, cap=15.0)
        assert not alive and "result" in box, (len(cut), box)
        assert not isinstance(box.get("error"), LLMError)
        assert box["result"]["content"] == "".join(CONTENT) and len(server.bodies) == want_requests, (len(cut), box, len(server.bodies))
        assert server.streamed() == [True] * want_requests, server.streamed()

V147_LLM = ROOT.parent / "smoke-agent-v147-shipped" / "agent" / "llm.py"

@case
def with_both_switches_off_the_client_is_v147_call_for_call():
    import importlib.util

    if not V147_LLM.is_file():
        raise unittest.SkipTest("the v147 client is not at " + str(V147_LLM) + ": differential not run")
    spec = importlib.util.spec_from_file_location("llm_v147_for_v148a", V147_LLM)
    v147 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v147)
    from agent import llm

    with Switches(QUOTA_WAIT_BY_TIME=False, THINKING_STREAM=False):
        for dry, kwargs in ((13, {}), (None, {}), (None, {"global_after": 300.0}), (None, {"end_after": 1500.0}), (3, {})):
            want = drive(v147, quota_gateway(dry)[0], **kwargs)
            got = drive(llm, quota_gateway(dry)[0], **kwargs)
            assert got == want, (dry, kwargs, got, want)
        bodies = []
        for module in (v147, llm):
            with Server(lambda h, body, i: send_json(h, 200, json.dumps(WHOLE).encode("utf-8"))) as server:
                c = module.LLM(server.url, "key", "glm-test", timeout=20, log=lambda line: None)
                message = c.chat([{"role": "user", "content": "hi"}], tag="t")
                bodies.append((server.bodies, message, c.summary()))
        assert bodies[0] == bodies[1], bodies

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    started = time.time()
    failed = skipped = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except unittest.SkipTest as why:
            skipped += 1
            print(f"SKIP  {function.__name__}: {why}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
        finally:
            fresh()
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} v148a cases passed, {skipped} skipped, in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
