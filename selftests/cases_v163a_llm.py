from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NL = chr(10)
CASES = []

from agent import settings as _settings

for _store in (_settings._DEFAULTS, _settings.SETTINGS):
    _store.update(reasoning_cap=True, reasoning_cap_gen=True, cutoff_reask=True)

def case(function):
    CASES.append(function)
    return function

class Settings:

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, KeyError) for key in self.values}
        SETTINGS.update(self.values)
        return self

    def __exit__(self, *args):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is KeyError:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value
        return False

def runaway_stream(sent: dict, chunks: int = 2000, piece: str = "x" * 1000, content_after: int | None = None):
    def answer(handler, body, index):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.flush()
        sent["chunks"] = 0
        try:
            for number in range(chunks):
                event = {"choices": [{"index": 0, "delta": {"reasoning_content": piece}}]}
                if content_after is not None and number >= content_after:

                    event = {"choices": [{"index": 0, "delta": {"content": "=== FILE: a.js ===" + NL if number == content_after else "y",
                                                                "reasoning_content": piece}}]}
                handler.wfile.write(("data: " + json.dumps(event) + NL + NL).encode("utf-8"))
                handler.wfile.flush()
                sent["chunks"] += 1
            tail = [{"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]},
                    {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 16000}}]
            for event in tail:
                handler.wfile.write(("data: " + json.dumps(event) + NL + NL).encode("utf-8"))
            handler.wfile.write(("data: [DONE]" + NL + NL).encode("utf-8"))
            handler.wfile.flush()
            sent["finished"] = True
        except OSError:
            sent["closed"] = True
            raise
        handler.close_connection = True
    return answer

@case
def a_runaway_reasoning_is_closed_at_the_cap_and_returned_with_finish_reasoning_cap():
    import cases_v148a as h

    h.fresh()
    sent: dict = {}
    log: list = []
    with h.Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=120.0):
        with h.Server(runaway_stream(sent)) as server:
            client = h.client_for(server, log)
            box, took, alive = h.run_call(client, cap=20.0, thinking=True, max_tokens=16000, reasoning_cap=40000)
            time.sleep(0.3)
    assert not alive and "error" not in box, box.get("error")
    message = box["result"]
    reasoning = message.get("reasoning_content") or ""
    assert 40000 < len(reasoning) <= 41000, len(reasoning)
    assert (message.get("content") or "") == "", message.get("content")
    assert client.last_finish_reason == "reasoning_cap", client.last_finish_reason
    assert client.last_reasoning_chars == len(reasoning)
    assert not sent.get("finished") and sent.get("chunks", 0) < 2000, sent
    assert any("t: reasoning cut at 4" in line and "stream closed" in line for line in log), log
    assert not any("output truncated" in line for line in log), log
    assert any("ESTIMATED" in line for line in log), log
    assert not client._reasoning_caps
    assert len(server.bodies) == 1 and server.bodies[0].get("stream") is True and "reasoning_cap" not in json.dumps(server.bodies[0])

@case
def an_answer_under_the_cap_is_unchanged_and_the_request_is_the_same_as_without_a_cap():
    import cases_v148a as h

    h.fresh()
    got = []
    with h.Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=120.0):
        for options in ({}, {"reasoning_cap": 40000}, {"reasoning_cap": 5}):
            with h.Server(lambda handler, body, i: h.send_stream(handler)) as server:
                log: list = []
                client = h.client_for(server, log)
                box, took, alive = h.run_call(client, cap=15.0, thinking=True, **options)
                assert not alive and "error" not in box, box.get("error")
                got.append((server.bodies, box["result"], client.last_finish_reason, h.usage_of(client),
                            [line for line in log if "reasoning cut" in line]))

    assert got[0] == got[1], (got[0], got[1])
    assert got[0][2] == "stop" and got[0][1]["content"].startswith("=== FILE: a.js ===")

    assert got[2][2] == "reasoning_cap" and got[2][1]["content"] == "" and got[2][4], got[2]

@case
def reasoning_past_the_cap_after_the_content_started_is_not_cut():
    import cases_v148a as h

    h.fresh()
    sent: dict = {}
    with h.Switches(THINKING_STREAM=True, STREAM_IDLE_SEC=120.0):
        with h.Server(runaway_stream(sent, chunks=60, content_after=10)) as server:
            client = h.client_for(server)

            box, took, alive = h.run_call(client, cap=15.0, thinking=True, reasoning_cap=12000)
    assert not alive and "error" not in box, box.get("error")
    assert client.last_finish_reason == "length" and sent.get("finished"), (client.last_finish_reason, sent)
    assert box["result"]["content"].startswith("=== FILE: a.js ===")
    assert len(box["result"].get("reasoning_content") or "") == 60000

@case
def chat_refuses_unknown_keywords_as_before():
    from agent.llm import LLM

    client = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lambda line: None)
    try:
        client.chat([{"role": "user", "content": "x"}], no_such_option=1)
    except TypeError as exc:
        assert "no_such_option" in str(exc)
    else:
        raise AssertionError("an unknown keyword was accepted")

def block(path: str, body: str) -> str:
    return f"=== FILE: {path} ===" + NL + body + NL + "=== END FILE ==="

PAGE = "frontend/src/pages/HomePage.tsx"
FIRST = block(PAGE, "export default function HomePage() { return null; }")
FIX = block(PAGE, "export default function HomePage() { return <h1>Books</h1>; }")
TAIL = "TAIL-MARK: the heading is rendered by HomePage.tsx but the route never mounts it"

class Model:

    def __init__(self, diagnose, decide, writes):
        self.diagnose, self.decide, self.writes = diagnose, decide, list(writes)
        self.calls: list[tuple[str, dict, str]] = []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        tag = options.get("tag", "")
        self.calls.append((tag, dict(options), messages[-1]["content"]))
        assert len(self.calls) <= 30, [t for t, _, _ in self.calls]
        if ":diagnose" in tag:
            message, self.last_finish_reason = self.diagnose
            return dict(message)
        if tag.endswith(":decide"):
            self.last_finish_reason = "stop"
            return {"content": self.decide}
        content, self.last_finish_reason = self.writes.pop(0) if len(self.writes) > 1 else self.writes[0]
        return {"content": content}

    def kinds(self) -> list[str]:
        return [tag.split(":", 2)[2] for tag, _, _ in self.calls]

def run_scripted(model, outcomes):
    from agent.codegen import CodegenImplementer
    from cases_nofiles import SPEC, project, red

    CodegenImplementer.repair_mode_broken = False
    CodegenImplementer.all_thinking_batches.clear()
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        results = iter(outcomes)
        implementer = CodegenImplementer(model, root, lambda: next(results), log=lines.append)
        node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue through the link named `Books`."}
        result = implementer.implement(node, {SPEC: "await click(page, { role: 'link', name: 'Books' });"}, red())
    return result, lines

def reasoning_text() -> str:
    return ("Let me think. The route may be wrong. " * 1200) + TAIL

@case
def a_capped_diagnosis_without_a_decision_hands_the_reasoning_tail_to_the_writing_call():
    from cases_nofiles import green, red

    model = Model(({"content": "", "reasoning_content": reasoning_text()}, "reasoning_cap"), "", [(FIRST, "stop"), (FIX, "stop")])
    result, lines = run_scripted(model, [red(), green()])
    assert result.status == "passed", (result.status, result.detail)
    assert model.kinds() == ["gen0", "diagnose1", "decide", "repair1"], model.kinds()
    diagnose_options = model.calls[1][1]
    assert diagnose_options.get("reasoning_cap") == 40000 and diagnose_options.get("thinking") is True, diagnose_options
    assert model.calls[0][1].get("reasoning_cap") == 40000
    assert "reasoning_cap" not in model.calls[3][1]
    repair_prompt = model.calls[3][2]
    assert "(model's unfinished analysis, cut)" in repair_prompt and TAIL in repair_prompt, repair_prompt[-3000:]
    assert any(line.startswith("[llm] codegen:REQ-9:diagnose1: reasoning cut at ") and "its tail used as the diagnosis" in line
               and "marked unfinished" in line for line in lines), [l for l in lines if "reasoning" in l]

@case
def a_capped_diagnosis_with_a_decision_keeps_the_decision_as_v162_does():
    from cases_nofiles import green, red

    decision = "frontend/src/pages/HomePage.tsx -- render the heading named Books"
    model = Model(({"content": "", "reasoning_content": reasoning_text()}, "reasoning_cap"), decision, [(FIRST, "stop"), (FIX, "stop")])
    result, lines = run_scripted(model, [red(), green()])
    assert result.status == "passed" and model.kinds() == ["gen0", "diagnose1", "decide", "repair1"], model.kinds()
    repair_prompt = model.calls[3][2]
    assert decision in repair_prompt and "(model's unfinished analysis, cut)" not in repair_prompt
    assert any("its tail used as the diagnosis (decided by the concluding call)" in line for line in lines), lines

@case
def a_provider_truncation_with_empty_content_gets_the_same_tail_fallback():
    from cases_nofiles import green, red

    model = Model(({"content": "", "reasoning_content": reasoning_text()}, "length"), "", [(FIRST, "stop"), (FIX, "stop")])
    result, lines = run_scripted(model, [red(), green()])
    assert result.status == "passed", result.detail
    assert TAIL in model.calls[3][2] and "(model's unfinished analysis, cut)" in model.calls[3][2]
    assert any("diagnose1: reasoning cut at " in line for line in lines)

@case
def with_the_cap_off_the_diagnosis_is_v162():
    from cases_nofiles import green, red

    with Settings(reasoning_cap=False):
        model = Model(({"content": "", "reasoning_content": reasoning_text()}, "length"), "", [(FIRST, "stop"), (FIX, "stop")])
        result, lines = run_scripted(model, [red(), green()])
    assert result.status == "passed" and model.kinds() == ["gen0", "diagnose1", "decide", "repair1"], model.kinds()
    assert all("reasoning_cap" not in options for _, options, _ in model.calls), [o for _, o, _ in model.calls]
    assert TAIL not in model.calls[3][2] and "(model's unfinished analysis" not in model.calls[3][2]
    assert any("REQ-9 round 1: diagnosis of 0 line(s) (none)" in line for line in lines), lines
    assert not any("reasoning cut" in line for line in lines)

@case
def a_short_diagnosis_with_content_is_untouched_by_the_cap():
    from cases_nofiles import green, red

    model = Model(({"content": "frontend/src/pages/HomePage.tsx -- mount the heading", "reasoning_content": "short"}, "stop"), "",
                  [(FIRST, "stop"), (FIX, "stop")])
    result, lines = run_scripted(model, [red(), green()])
    assert model.kinds() == ["gen0", "diagnose1", "repair1"], model.kinds()
    assert "mount the heading" in model.calls[2][2] and not any("reasoning cut" in line for line in lines)

@case
def a_capped_first_answer_takes_the_all_thinking_path():
    from cases_nofiles import green, red

    model = Model(({"content": "plan"}, "stop"), "", [("", "reasoning_cap"), (FIX, "stop")])
    result, lines = run_scripted(model, [green()])
    assert result.status == "passed", result.detail
    assert model.kinds() == ["gen0", "gen0"], model.kinds()
    assert model.calls[0][1].get("thinking") is True and model.calls[1][1].get("thinking") is False
    assert "reasoning_cap" not in model.calls[1][1]
    assert any("cut at the cap with no content; handled as an all-thinking answer" in line for line in lines)
    assert any("the first answer was all thinking and no content" in line for line in lines)
    with Settings(reasoning_cap_gen=False):
        model = Model(({"content": "plan"}, "stop"), "", [(FIX, "stop")])
        run_scripted(model, [green()])
        assert "reasoning_cap" not in model.calls[0][1]

def run_gateway(streamed, outcomes, deadline_in: float | None = None):
    import urllib.request

    import cases_nofiles as nf
    from agent import clock
    from agent.codegen import CodegenImplementer
    from agent.llm import LLM

    server = nf.Server(streamed)
    original = urllib.request.urlopen
    saved = (LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled)
    LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = 0, 0, False
    CodegenImplementer.repair_mode_broken = False
    urllib.request.urlopen = server.urlopen
    lines: list[str] = []
    try:
        client = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lines.append)
        with tempfile.TemporaryDirectory() as tmp:
            root = nf.project(tmp)
            results = iter(outcomes)
            implementer = CodegenImplementer(client, root, lambda: next(results), log=lines.append)
            if deadline_in is not None:
                implementer.deadline = clock.now() + deadline_in
            node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue through the link named `Books`."}
            result = implementer.implement(node, {nf.SPEC: "await click(page, { role: 'link', name: 'Books' });"}, nf.red())
    finally:
        FUSE_SEEN.append((LLM.streamed, LLM.cutoffs))
        urllib.request.urlopen = original
        LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = saved
    return result, server, lines

REASK_WORDS = "wrote no files, and it was stopped"
FUSE_SEEN: list = []

@case
def a_cut_off_answer_is_asked_once_more_and_the_re_asks_files_are_the_rounds_answer():
    import cases_nofiles as nf
    from agent.codegen import PROSE_LIMIT_CHARS

    result, server, lines = run_gateway([nf.analysis(PROSE_LIMIT_CHARS + 5000), nf.FIX[0]], [nf.red(), nf.green()])
    kinds = [kind for kind, _ in server.requests]
    assert kinds == ["write", "diagnose", "stream", "stream"], kinds
    assert result.status == "passed" and result.test_runs == 2, (result.status, result.test_runs)
    assert REASK_WORDS in server.requests[3][1] and "At most 10" in server.requests[3][1], server.requests[3][1]
    assert any("REQ-9 round 1: cut-off answer had no files; asked once more for files only" in line for line in lines), lines
    assert any("the re-ask answered with blocks" in line for line in lines)

@case
def a_re_ask_that_fails_too_goes_the_v162_cut_off_path_and_is_not_repeated_in_the_round():
    import cases_nofiles as nf
    from agent.codegen import PROSE_LIMIT_CHARS

    streamed = [nf.analysis(PROSE_LIMIT_CHARS + 5000), nf.analysis(9000), nf.analysis(PROSE_LIMIT_CHARS + 5000), nf.FIX[0]]
    result, server, lines = run_gateway(streamed, [nf.red(), nf.green()])
    kinds = [kind for kind, _ in server.requests]

    assert kinds == ["write", "diagnose", "stream", "stream", "decide", "stream", "decide", "stream"], kinds
    assert sum(REASK_WORDS in user for _, user in server.requests) == 1
    assert result.status == "passed", (result.status, result.detail)
    assert server.streams[1].read_lines < len(server.streams[1].lines)
    assert any("the re-ask brought no block either (finish=aborted)" in line for line in lines), lines

    assert sum("stream cut off after" in line for line in lines) == 3
    assert FUSE_SEEN[-1] == (3, 2), FUSE_SEEN

@case
def a_re_ask_whose_header_is_not_at_a_line_start_is_no_answer():
    import cases_nofiles as nf
    from agent.codegen import PROSE_LIMIT_CHARS

    mention = "I will now write it as === FILE: frontend/src/pages/HomePage.tsx === below."
    result, server, lines = run_gateway([nf.analysis(PROSE_LIMIT_CHARS + 5000), mention, nf.FIX[0]], [nf.red(), nf.green()])
    kinds = [kind for kind, _ in server.requests]
    assert kinds == ["write", "diagnose", "stream", "stream", "decide", "stream"], kinds
    assert any("the re-ask brought no block either" in line for line in lines), lines
    assert result.status == "passed"

@case
def the_cap_is_per_thread_and_cleared_after_a_failing_call():
    import threading

    from agent.llm import LLM

    client = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lambda line: None)
    seen = {}

    def fake_chat_until(end, messages, **options):
        seen[threading.get_ident()] = client._cap_now()
        other = threading.Thread(target=lambda: seen.setdefault("other", client._cap_now()))
        other.start()
        other.join()
        raise OSError("gateway down")

    client._chat_until = fake_chat_until
    try:
        client.chat([{"role": "user", "content": "x"}], reasoning_cap=40000)
    except OSError:
        pass
    assert seen[threading.get_ident()] == 40000 and seen["other"] is None, seen
    assert not client._reasoning_caps and client._cap_now() is None

@case
def no_re_ask_starts_when_the_batch_deadline_is_too_close():
    import cases_nofiles as nf
    from agent.codegen import FOLLOW_UP_MIN_SEC, PROSE_LIMIT_CHARS

    result, server, lines = run_gateway([nf.analysis(PROSE_LIMIT_CHARS + 5000), nf.FIX[0]], [nf.red(), nf.green()],
                                        deadline_in=FOLLOW_UP_MIN_SEC - 60)
    kinds = [kind for kind, _ in server.requests]
    assert kinds == ["write", "diagnose", "stream", "decide", "stream"], kinds
    assert any("no reask call: less than" in line for line in lines), lines
    assert result.status == "passed"

@case
def with_the_re_ask_off_the_cut_off_path_is_v162_call_for_call():
    import cases_nofiles as nf
    from agent.codegen import PROSE_LIMIT_CHARS

    with Settings(cutoff_reask=False):
        result, server, lines = run_gateway([nf.analysis(PROSE_LIMIT_CHARS + 5000, mention="big.js"), nf.FIX[0]], [nf.red(), nf.green()])
    kinds = [kind for kind, _ in server.requests]

    assert kinds == ["write", "diagnose", "stream", "decide", "stream"], kinds
    assert not any("asked once more" in line or "re-ask" in line for line in lines)
    assert result.status == "passed" and result.test_runs == 2

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
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} v163a cases passed, {skipped} skipped, in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
