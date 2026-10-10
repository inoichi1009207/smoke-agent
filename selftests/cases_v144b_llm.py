from __future__ import annotations

import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

def fresh():
    from agent.llm import LLM

    LLM.global_end = None
    LLM.quota_exhausted = False

class Server:

    def __init__(self, answer):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                outer.requests += 1
                try:
                    answer(self)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.requests = 0
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

def run_call(client, cap: float, **kwargs):
    box: dict = {}

    def target():
        try:
            box["result"] = client.chat([{"role": "user", "content": "hi"}], tag="t", **kwargs)
        except BaseException as exc:
            box["error"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(cap)
    return box, time.monotonic() - started, worker.is_alive()

def trickle_500(handler):
    body_len = 150
    handler.send_response(500)
    handler.send_header("Content-Type", "text/plain")
    handler.send_header("Content-Length", str(body_len))
    handler.end_headers()
    handler.wfile.flush()
    for _ in range(body_len):
        handler.wfile.write(b"x")
        handler.wfile.flush()
        time.sleep(0.2)

def client_for(server, limit: float):
    from agent.llm import LLM

    c = LLM(server.url, "key", "glm-test", timeout=60, log=lambda line: None)
    c.call_limit_sec = limit
    return c

@case
def a_trickled_500_error_body_ends_at_the_call_limit():
    from agent.llm import LLMError

    with Server(trickle_500) as server:
        box, took, alive = run_call(client_for(server, 1.5), cap=12.0)
    assert not alive, f"the call was still running after {took:.1f}s (error body read past the call's end)"
    assert isinstance(box.get("error"), LLMError), box
    assert took < 5.0, f"the call took {took:.1f}s with a 1.5s limit"
    assert "total time limit" in str(box["error"]), box["error"]
    assert server.requests == 1, f"retried past the end: {server.requests} requests"

@case
def a_trickled_500_on_the_stream_ends_at_the_call_limit_too():
    from agent.llm import LLMError

    with Server(trickle_500) as server:
        box, took, alive = run_call(client_for(server, 1.5), cap=12.0, thinking=False, expect_marker=(("=== FILE:",), 30000))
    assert not alive, f"the call was still running after {took:.1f}s (streamed error body read past the call's end)"
    assert isinstance(box.get("error"), LLMError), box
    assert took < 5.0, f"the call took {took:.1f}s with a 1.5s limit"
    assert server.requests == 1, f"the fallback ran past the end: {server.requests} requests"

@case
def a_normal_400_error_body_is_still_in_the_message():
    from agent.llm import LLMError

    def bad_request(handler):
        body = b'{"error": {"message": "max_tokens is too large for this model"}}'
        handler.send_response(400)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    with Server(bad_request) as server:
        box, took, alive = run_call(client_for(server, 30.0), cap=12.0)
    assert not alive and isinstance(box.get("error"), LLMError), box
    message = str(box["error"])
    assert message.startswith("HTTP 400:") and "max_tokens is too large for this model" in message, message
    assert server.requests == 1 and took < 5.0, (server.requests, took)

@case
def a_long_error_body_is_cut_to_its_limit():
    from agent.llm import LLMError

    def long_body(handler):
        body = b"E" * 20000
        handler.send_response(400)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    with Server(long_body) as server:
        box, took, alive = run_call(client_for(server, 30.0), cap=12.0)
    assert not alive and isinstance(box.get("error"), LLMError), box
    assert str(box["error"]) == "HTTP 400: " + "E" * 500, str(box["error"])[:80]

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    started = time.time()
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
        finally:
            fresh()
    print(f"{len(CASES) - failed}/{len(CASES)} v144b llm cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
