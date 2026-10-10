from __future__ import annotations

import http.client
import io
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

def _call_limit_default() -> float:
    try:
        from agent.settings import setting_seconds

        return setting_seconds("llm_call_limit_sec", 1800.0)
    except Exception:
        return 1800.0

CALL_LIMIT_SEC = _call_limit_default()

def _clock() -> float:
    return time.monotonic()

class _Cut:

    def __init__(self, response: Any, end: float | None) -> None:
        self.response, self.end, self.fired, self.timer = response, end, False, None

    def __enter__(self) -> "_Cut":
        if self.end is None:
            return self
        sock = getattr(getattr(getattr(self.response, "fp", None), "raw", None), "_sock", None)
        if not isinstance(sock, socket.socket):
            return self
        self.timer = threading.Timer(max(0.0, self.end - _clock()) + 0.5, self._shut, args=(sock,))
        self.timer.daemon = True
        self.timer.start()
        return self

    def _shut(self, sock: socket.socket) -> None:
        self.fired = True
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def __exit__(self, kind, exc, tb) -> bool:
        if self.timer is not None:
            self.timer.cancel()
        if kind is not None and self.fired and not isinstance(exc, LLMError):
            raise TotalTimeLimit("the model call reached its total time limit (connection closed)") from exc
        return False

_URLOPEN = urllib.request.urlopen

class _Guard:

    def __init__(self, end: float | None) -> None:
        self.end, self.sock, self.fired, self.timer = end, None, False, None
        self.lock = threading.Lock()

    def open(self, request: Any, timeout: float) -> Any:
        if self.end is None or urllib.request.urlopen is not _URLOPEN:
            return urllib.request.urlopen(request, timeout=timeout)
        guard = self

        def connection(http_class: Any) -> Any:
            class Connection(http_class):
                def connect(self) -> None:
                    super().connect()
                    guard.attach(self.sock)

            return Connection

        class Http(urllib.request.HTTPHandler):
            def do_open(self, http_class, req, **kwargs):
                return super().do_open(connection(http_class), req, **kwargs)

        class Https(urllib.request.HTTPSHandler):
            def do_open(self, http_class, req, **kwargs):
                return super().do_open(connection(http_class), req, **kwargs)

        return urllib.request.build_opener(Http, Https).open(request, timeout=timeout)

    def attach(self, sock: Any) -> None:
        with self.lock:
            self.sock = sock
            late = self.fired
        if late:
            _shut_socket(sock)

    def _fire(self) -> None:
        with self.lock:
            self.fired = True
            sock = self.sock
        if sock is not None:
            _shut_socket(sock)

    def __enter__(self) -> "_Guard":
        if self.end is not None:
            self.timer = threading.Timer(max(0.0, self.end - _clock()), self._fire)
            self.timer.daemon = True
            self.timer.start()
        return self

    def __exit__(self, kind, exc, tb) -> bool:
        if self.timer is not None:
            self.timer.cancel()
        if kind is not None and self.fired and self.sock is not None and not isinstance(exc, LLMError):
            raise TotalTimeLimit("the model call reached its total time limit (connection closed while the response was awaited)") from exc
        return False

def _shut_socket(sock: Any) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

def _error_detail(exc: Any, end: float | None, limit: int) -> str:
    source = getattr(exc, "fp", None) or exc
    cut = _Cut(source, end)
    raw = b""
    try:
        with cut:
            raw = source.read(4 * limit) or b""
    except Exception:
        raw = b""
    finally:
        try:
            exc.close()
        except Exception:
            pass
    if cut.fired:
        raise TotalTimeLimit("the model call reached its total time limit (error body closed while it was read)")
    return raw.decode("utf-8", errors="replace")[:limit]

QUOTA_WAIT_SEC = 120
QUOTA_WAITS = 10
QUOTA_RUN_LIMIT_SEC = 2400

def _setting(kind: str, key: str, default: Any) -> Any:
    try:
        from agent.settings import setting_on, setting_seconds

        return setting_on(key, default) if kind == "on" else setting_seconds(key, default)
    except Exception:
        return default

QUOTA_WAIT_BY_TIME = bool(_setting("on", "llm_quota_wait_by_time", True))

THINKING_STREAM = bool(_setting("on", "llm_thinking_stream", True))
STREAM_IDLE_SEC = float(_setting("seconds", "llm_stream_idle_sec", 120.0))

class _StreamBroken(OSError):
    pass

def _stream_error(event: Any) -> Any:
    if isinstance(event, dict) and event.get("error") and not event.get("choices"):
        return event["error"]
    return None

def _stream_error_status(error: Any) -> int:
    if not isinstance(error, dict):
        return 500
    for key in ("status", "status_code", "code"):
        try:
            number = int(str(error.get(key)).strip())
        except (TypeError, ValueError):
            continue
        if 400 <= number <= 599:
            return number
    kind = f"{error.get('type') or ''} {error.get('code') or ''}".lower()
    for word, status in (("invalid_request", 400), ("authentication", 401), ("permission", 403), ("not_found", 404),
                         ("rate_limit", 429), ("quota", 429)):
        if word in kind:
            return status
    return 500

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

def is_transport_error(detail: str) -> bool:
    return bool(re.search(r"proxy_error|connection reset|broken pipe|timed? ?out|\bEOF\b", detail, re.IGNORECASE))

def strip_reasoning_markup(content: str) -> str:
    content = THINK_BLOCK.sub("", content)
    if "</think>" in content:

        content = content.rsplit("</think>", 1)[1]
    return content.replace("<think>", "").lstrip("\n")

READ_TIMEOUT_ATTEMPTS = 2

class LLMError(RuntimeError):
    pass

class QuotaExhausted(LLMError):
    pass

class TotalTimeLimit(LLMError):
    pass

class CallDeadlineReached(LLMError):
    pass

class LLM:
    quota_waited_sec = 0
    quota_exhausted = False
    streamed = 0
    cutoffs = 0
    cutoff_disabled = False
    global_end: float | None = None

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        usage_log: Path | None = None,
        timeout: int = 600,
        log=print,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.usage_log = usage_log
        self.log = log
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.cached_tokens = 0
        self.last_prompt_tokens = 0
        self.last_finish_reason = ""
        self.last_reasoning_chars = 0
        self.last_marker_offset = -1
        self.call_limit_sec = CALL_LIMIT_SEC
        self._reasoning_caps: dict[int, int | None] = {}

    def _cap_now(self) -> int | None:
        return (self.__dict__.get("_reasoning_caps") or {}).get(threading.get_ident())

    def call_end(self) -> float | None:
        ends = [LLM.global_end] if LLM.global_end is not None else []
        if self.call_limit_sec and self.call_limit_sec > 0:
            ends.append(_clock() + float(self.call_limit_sec))
        return min(ends) if ends else None

    @staticmethod
    def _check_end(end: float | None, where: str) -> None:
        if end is not None and _clock() >= end:
            raise TotalTimeLimit(f"the model call reached its total time limit ({where})")

    def _timeout_for(self, end: float | None, bound: bool = False) -> float:
        if end is None:
            return self.timeout
        if bound:
            return max(0.001, min(float(self.timeout), end - _clock()))
        return max(1.0, min(float(self.timeout), end - _clock()))

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        tag: str = "",
        thinking: bool = True,
        model: str | None = None,
        expect_marker: tuple[tuple[str, ...], int] | None = None,
        end: float | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        reasoning_cap = extra.pop("reasoning_cap", None)
        if extra:
            raise TypeError(f"chat() got unexpected keyword argument(s) {sorted(extra)}")
        if reasoning_cap is not None:

            caps = self.__dict__.setdefault("_reasoning_caps", {})
            ident = threading.get_ident()
            caps[ident] = int(reasoning_cap) if int(reasoning_cap) > 0 else None
            try:
                return self.chat(messages, tools=tools, max_tokens=max_tokens, temperature=temperature, tag=tag,
                                 thinking=thinking, model=model, expect_marker=expect_marker, end=end)
            finally:
                caps.pop(ident, None)
        if LLM.quota_exhausted:
            raise QuotaExhausted("the key is out of balance and the run's waiting allowance is spent")
        call_end = self.call_end()
        if end is not None and (call_end is None or float(end) < call_end):
            bound_end = float(end)
            try:
                message = self._chat_until(bound_end, messages, tools=tools, max_tokens=max_tokens, temperature=temperature, tag=tag,
                                           thinking=thinking, model=model, expect_marker=expect_marker, bound=True)
            except QuotaExhausted:
                raise
            except Exception as exc:

                if isinstance(exc, TotalTimeLimit) or _clock() >= bound_end:
                    raise CallDeadlineReached(f"the call deadline was reached: {exc}") from exc
                raise
            if _clock() >= bound_end:

                raise CallDeadlineReached("the call deadline was reached: the model call reached its total time limit "
                                          "(the answer arrived after it)")
            return message
        return self._chat_until(call_end, messages, tools=tools, max_tokens=max_tokens, temperature=temperature, tag=tag,
                                thinking=thinking, model=model, expect_marker=expect_marker)

    def _chat_until(
        self,
        end: float | None,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float,
        tag: str,
        thinking: bool,
        model: str | None,
        expect_marker: tuple[tuple[str, ...], int] | None,
        bound: bool = False,
    ) -> dict[str, Any]:
        self._check_end(end, "not started")
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
        if not thinking:

            body["thinking"] = {"type": "disabled"}
            if "deepseek" not in str(body["model"]).lower():

                body["chat_template_kwargs"] = {"enable_thinking": False}
        data = None
        if expect_marker and not thinking and LLM.cutoffs >= 8 and LLM.cutoffs * 5 > LLM.streamed * 3:

            if not LLM.cutoff_disabled:
                LLM.cutoff_disabled = True
                self.log(f"[llm] the cut-off fired on {LLM.cutoffs} of {LLM.streamed} streamed calls; that is not 'rare runaway answers' -- switched off for the rest of the run")
        if expect_marker and not thinking and not LLM.cutoff_disabled:
            try:
                data = self._post_stream(body, expect_marker[0], expect_marker[1], messages, end=end, bound=bound)
            except QuotaExhausted:
                raise
            except Exception as exc:
                self.log(f"[llm] {tag}: streamed request failed ({str(exc)[:160]}); asking again without streaming")
        if data is None:

            data = self._post_with_retry(body, end=end, bound=bound, stream=bool(thinking and not tools))
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:500]}") from exc
        if isinstance(message.get("content"), str):
            message["content"] = strip_reasoning_markup(message["content"])
        self.last_prompt_tokens = int((data.get("usage") or {}).get("prompt_tokens") or 0)
        self.last_finish_reason = str(data["choices"][0].get("finish_reason") or "")
        self.last_reasoning_chars = len(str(message.get("reasoning_content") or ""))
        if self.last_finish_reason == "reasoning_cap":

            self.log(f"[llm] {tag}: reasoning cut at {self.last_reasoning_chars} chars (cap {self._cap_now()}), "
                     "no content yet; stream closed")
        if self.last_finish_reason == "length":

            self.log(
                f"[llm] {tag}: output truncated at max_tokens={max_tokens} "
                f"(content={len(str(message.get('content') or ''))} chars, reasoning_content={self.last_reasoning_chars} chars)"
            )
        self._record_usage(data.get("usage") or {}, tag)
        return message

    def _post_stream(self, body: dict[str, Any], markers: tuple[str, ...], limit: int, messages: list[dict[str, Any]],
                     end: float | None = None, bound: bool = False) -> dict[str, Any]:
        payload = json.dumps({**body, "stream": True, "stream_options": {"include_usage": True}}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        parts: list[str] = []
        size = 0
        usage: dict[str, Any] = {}
        finish = ""
        seen_marker = False
        aborted = False
        next_look = 64
        self.last_marker_offset = -1
        self._check_end(end, "before the stream")
        try:

            with _Guard(end if bound else None) as guard:
                response = guard.open(request, self._timeout_for(end, bound))
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc, end, 300)
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        with response, _Cut(response, end) as cut:
            for raw in response:

                self._check_end(end, f"stream cut after {size} characters")
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                event = json.loads(chunk)
                if _stream_error(event) is not None:

                    raise LLMError(f"the stream sent an error after {size} characters: {chunk[:300]}")
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        size += len(piece)
                    finish = str(choice.get("finish_reason") or finish)
                if not seen_marker and size >= next_look:
                    next_look = size + 400
                    text = "".join(parts)

                    found = [m.start() for marker in markers for m in [re.search(r"(?:^|\n|</think>)[ \t]*" + re.escape(marker), text)] if m]
                    if found:
                        seen_marker = True
                        self.last_marker_offset = min(found)
                    elif size >= limit:
                        aborted = True
                        break
            if cut.fired:
                raise TotalTimeLimit(f"the model call reached its total time limit (stream closed after {size} characters)")
        content = "".join(parts)
        if not seen_marker and not aborted:
            found = [m.start() for marker in markers for m in [re.search(r"(?:^|\n|</think>)[ \t]*" + re.escape(marker), content)] if m]
            self.last_marker_offset = min(found) if found else -1
        if not content and not aborted:
            raise LLMError("the stream ended without any content")
        if aborted or not usage:

            prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)

            usage = {"prompt_tokens": prompt_chars // 3, "completion_tokens": max(1, len(content) // 3), "estimated": True}
        LLM.streamed += 1
        if aborted:
            LLM.cutoffs += 1
            self.log(f"[llm] stream cut off after {size} characters without any of {list(markers)}")
        else:

            self.log(f"[llm] answer of {size} characters; first file header at character {self.last_marker_offset} (-1 = none)")
        return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "aborted" if aborted else finish}], "usage": usage}

    def _read_thinking_stream(self, response: Any, end: float | None, timeout: float, reading: dict[str, bool],
                              body: dict[str, Any]) -> dict[str, Any]:
        idle = min(STREAM_IDLE_SEC, float(timeout)) if STREAM_IDLE_SEC > 0 else float(timeout)
        content: list[str] = []
        reasoning: list[str] = []
        plain: list[bytes] = []
        usage: dict[str, Any] = {}
        finish = ""
        events = 0
        done = False
        read1 = getattr(response, "read1", None)
        buffer = b""
        start = 0
        eof = False
        cap = self._cap_now()
        thought_size = 0
        capped = False
        while not done:
            newline = buffer.find(b"\n", start)
            if newline < 0 and not eof:
                try:
                    block = read1(65536) if read1 is not None else response.readline()
                except (TimeoutError, socket.timeout) as exc:
                    if not reading["first_byte"]:
                        raise
                    reading["idle"] = True
                    raise TimeoutError(f"The read operation timed out (the stream sent nothing for {idle:g} s)") from exc
                except http.client.IncompleteRead:

                    block = b""
                if not block:
                    eof = True
                    continue
                if not reading["first_byte"]:
                    reading["first_byte"] = True
                    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                    if isinstance(sock, socket.socket):
                        sock.settimeout(idle)
                self._check_end(end, f"thinking stream cut after {events} events")
                buffer, start = buffer[start:] + block, 0
                continue
            if newline < 0:
                if start >= len(buffer):
                    break
                raw, start = buffer[start:], len(buffer)
            else:
                raw, start = buffer[start:newline + 1], newline + 1
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                if not events:
                    plain.append(raw)
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                done = True
                break
            event = json.loads(chunk)
            events += 1
            error = _stream_error(event)
            if error is not None:
                status = _stream_error_status(error)
                reading["error"] = True
                self.log(f"[llm] the thinking stream sent an error event after {events - 1} events ({len(''.join(content))} "
                         f"content characters): {chunk[:200]} -- not an answer; handled as HTTP {status}")
                raise urllib.error.HTTPError(f"{self.base_url}/chat/completions", status, "error event in the stream",
                                             None, io.BytesIO(chunk.encode("utf-8")))
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning.append(str(delta["reasoning_content"]))
                    thought_size += len(reasoning[-1])
                if delta.get("content"):
                    content.append(str(delta["content"]))
                finish = str(choice.get("finish_reason") or finish)
            if cap and not finish and thought_size > cap and not any(piece.strip() for piece in content):

                capped = True
                break
        if capped:
            finish = "reasoning_cap"
        if not events:
            return json.loads(b"".join(plain).decode("utf-8"))
        if not done and not finish:
            raise _StreamBroken(f"the thinking stream ended before its end after {events} events")
        text, thought = "".join(content), "".join(reasoning)
        if not usage:
            prompt_chars = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
            usage = {"prompt_tokens": prompt_chars // 3, "completion_tokens": max(1, (len(text) + len(thought)) // 3), "estimated": True}
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if thought:
            message["reasoning_content"] = thought
        return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage}

    def _post_with_retry(self, body: dict[str, Any], end: float | None = None, bound: bool = False,
                         stream: bool = False) -> dict[str, Any]:
        if end is None:
            end = self.call_end()
        url = f"{self.base_url}/chat/completions"
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        streaming = bool(stream) and THINKING_STREAM and urllib.request.urlopen is _URLOPEN
        stream_payload = (json.dumps({**body, "stream": True, "stream_options": {"include_usage": True}}).encode("utf-8")
                          if streaming else payload)
        last_error: Exception | None = None
        attempt = 0
        quota_waits = 0
        read_timeouts = 0
        while attempt < 5:
            attempt += 1
            self._check_end(end, f"attempt {attempt}" + (f", last error: {str(last_error)[:120]}" if last_error else ""))
            request = urllib.request.Request(url, data=stream_payload if streaming else payload, headers=headers, method="POST")
            timeout = self._timeout_for(end, bound)
            clipped = bound and timeout < float(self.timeout)
            reading = {"first_byte": False, "idle": False, "error": False}
            try:
                with _Guard(end if bound else None) as guard:
                    with guard.open(request, timeout) as response:
                        with _Cut(response, end) as cut:
                            if streaming:
                                data = self._read_thinking_stream(response, end, timeout, reading, body)
                            else:
                                raw = response.read()
                        if cut.fired:
                            raise TotalTimeLimit("the model call reached its total time limit (answer closed while it was read)")
                        return data if streaming else json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = _error_detail(exc, end, 500)
                if streaming:
                    streaming = False
                    if reading["error"]:
                        self.log(f"[llm] streamed thinking request sent an error event (as HTTP {exc.code}); asking without streaming")
                    else:
                        self.log(f"[llm] streamed thinking request failed before its first byte (HTTP {exc.code}); asking without streaming")
                    if exc.code in (400, 404, 413, 422) and not is_transport_error(detail) and re.search(r"stream", detail, re.IGNORECASE):

                        attempt -= 1
                        continue

                if exc.code in (400, 401, 403, 404, 413, 422) and not is_transport_error(detail):
                    raise LLMError(f"HTTP {exc.code}: {detail}") from exc
                last_error = LLMError(f"HTTP {exc.code}: {detail}")

                if exc.code in (402, 429) and re.search(r"quota|balance|recharge|credit|billing", detail, re.IGNORECASE):

                    if end is not None and _clock() + QUOTA_WAIT_SEC >= end:
                        if QUOTA_WAIT_BY_TIME and not bound and quota_waits > QUOTA_WAITS:

                            LLM.quota_exhausted = LLM.quota_waited_sec + QUOTA_WAIT_SEC > QUOTA_RUN_LIMIT_SEC
                            raise QuotaExhausted(f"HTTP {exc.code}: {detail}") from exc
                        raise TotalTimeLimit(f"the model call reached its total time limit (key out of quota, HTTP {exc.code})") from exc
                    if (quota_waits < QUOTA_WAITS or QUOTA_WAIT_BY_TIME) and LLM.quota_waited_sec + QUOTA_WAIT_SEC <= QUOTA_RUN_LIMIT_SEC:
                        quota_waits += 1
                        attempt -= 1
                        LLM.quota_waited_sec += QUOTA_WAIT_SEC
                        count = f"{quota_waits}/{QUOTA_WAITS}" if quota_waits <= QUOTA_WAITS else f"{quota_waits}, past {QUOTA_WAITS}: until the call's end"
                        self.log(f"[llm] key out of quota (HTTP {exc.code}); waiting {QUOTA_WAIT_SEC}s for a top-up (call {count}, run {LLM.quota_waited_sec}/{QUOTA_RUN_LIMIT_SEC}s)")
                        time.sleep(QUOTA_WAIT_SEC)
                        continue

                    LLM.quota_exhausted = LLM.quota_waited_sec + QUOTA_WAIT_SEC > QUOTA_RUN_LIMIT_SEC
                    raise QuotaExhausted(f"HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
                if streaming and not reading["first_byte"]:
                    streaming = False
                    self.log(f"[llm] streamed thinking request failed before its first byte ({str(exc)[:160]}); asking without streaming")
                if reading["idle"]:
                    clipped = False
                if "read operation timed out" in str(exc).lower():
                    read_timeouts += 1
                    if read_timeouts >= READ_TIMEOUT_ATTEMPTS:

                        if bound and (clipped or _clock() >= end):

                            raise TotalTimeLimit(f"the model call reached its total time limit (read timeout {read_timeouts} "
                                                 f"used up the time left): {last_error}") from exc
                        raise LLMError(f"LLM request failed after {read_timeouts} read timeouts: {last_error}") from exc
            delay = min(60, 5 * attempt)
            if end is not None and _clock() + delay >= end:
                raise TotalTimeLimit(f"the model call reached its total time limit after {attempt} attempt(s): {last_error}")
            self.log(f"[llm] attempt {attempt} failed ({last_error}); retrying in {delay}s")
            time.sleep(delay)
        if bound:
            self._check_end(end, f"after {attempt} attempt(s), last error: {str(last_error)[:120]}")
        raise LLMError(f"LLM request failed after retries: {last_error}")

    def _record_usage(self, usage: dict[str, Any], tag: str) -> None:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)
        self.requests += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.reasoning_tokens += reasoning
        self.cached_tokens += cached
        self.log(f"[llm] {tag or 'call'}: prompt={prompt} (cached={cached}) completion={completion} (reasoning={reasoning})" + (" ESTIMATED: the stream was cut off, no usage record" if usage.get("estimated") else ""))
        if self.usage_log is not None:
            try:
                self.usage_log.parent.mkdir(parents=True, exist_ok=True)
                with self.usage_log.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                                "tag": tag,
                                "model": self.model,
                                "prompt_tokens": prompt,
                                "completion_tokens": completion,
                                "reasoning_tokens": reasoning,
                                "cached_tokens": cached,
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass

    def summary(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }

def assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    keep: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("reasoning_content"):
        keep["reasoning_content"] = message["reasoning_content"]
    if message.get("tool_calls"):
        keep["tool_calls"] = message["tool_calls"]
    return keep

def llm_from_env(usage_log: Path | None, log=print) -> LLM:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.arc-bench.com/v1"
    model = os.environ.get("MODEL", "").strip() or "deepseek-v4-flash"
    if not api_key:
        raise LLMError("OPENAI_API_KEY is not set")
    return LLM(base_url, api_key, model, usage_log=usage_log, log=log)
