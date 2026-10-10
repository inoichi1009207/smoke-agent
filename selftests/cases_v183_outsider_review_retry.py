from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import outsider
from agent.settings import SETTINGS
from agent.test_author import AuthoredFile

KEY = "outsider_review_retry"
_MISSING = object()

def _node() -> dict:
    leaf = {"id": "REQ-9", "name": "Rename", "description": "Users rename an entry; the new name is shown after a reload.",
            "scenarios": []}
    return {"id": "REQ-9", "name": "Rename", "description": leaf["description"], "scenarios": [], "members": ["REQ-9"],
            "_leaves": [leaf]}

class _LLM:

    def __init__(self, *replies: tuple[str, str]):
        self.replies, self.calls, self.last_finish_reason, self.model = list(replies), [], "", "glm-test"

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        content, finish = self.replies[min(len(self.calls), len(self.replies)) - 1]
        self.last_finish_reason = finish
        return {"content": content}

def _review(llm: _LLM, setting=_MISSING) -> list[str]:
    lines: list[str] = []
    saved = SETTINGS.get(KEY, _MISSING)
    try:
        if setting is _MISSING:
            SETTINGS.pop(KEY, None)
        else:
            SETTINGS[KEY] = setting
        outsider.review_acceptance(llm, _node(), AuthoredFile(path="p", content="x"), "REQ-9", log=lines.append)
    finally:
        if saved is _MISSING:
            SETTINGS.pop(KEY, None)
        else:
            SETTINGS[KEY] = saved
    return lines

def _inconclusive(lines: list[str]) -> bool:
    return any("review inconclusive (empty answer)" in line for line in lines)

def _retried(lines: list[str]) -> bool:
    return any("retrying once with max_tokens=12000" in line for line in lines)

def empty_and_length_retries_with_12000_and_uses_the_answer():
    llm = _LLM(("", "length"), ("OK", "stop"))
    lines = _review(llm)
    assert len(llm.calls) == 2, llm.calls
    assert llm.calls[0][1]["max_tokens"] == 4000 and llm.calls[1][1]["max_tokens"] == 12000, [c[1] for c in llm.calls]
    assert llm.calls[1][1]["thinking"] is True
    assert llm.calls[1][0] == llm.calls[0][0], "the retry must send the same messages"
    assert _retried(lines) and not _inconclusive(lines), lines
    assert any("review -> OK" in line for line in lines), lines

def empty_and_stop_does_not_retry():
    llm = _LLM(("", "stop"), ("OK", "stop"))
    lines = _review(llm)
    assert len(llm.calls) == 1, llm.calls
    assert not _retried(lines) and _inconclusive(lines), lines

def answer_at_length_does_not_retry():
    llm = _LLM(("OK", "length"), ("PROBLEM: never asked", "stop"))
    lines = _review(llm)
    assert len(llm.calls) == 1, llm.calls
    assert not _retried(lines) and any("review -> OK" in line for line in lines), lines

def whitespace_only_answer_counts_as_empty():
    llm = _LLM(("  \n ", "length"), ("OK", "stop"))
    lines = _review(llm)
    assert len(llm.calls) == 2 and _retried(lines), lines

def key_off_does_not_retry():
    for off in (False, "false", "0"):
        llm = _LLM(("", "length"), ("OK", "stop"))
        lines = _review(llm, setting=off)
        assert len(llm.calls) == 1, (off, llm.calls)
        assert not _retried(lines) and _inconclusive(lines), (off, lines)

def retry_also_empty_is_inconclusive_after_two_calls():
    llm = _LLM(("", "length"), ("", "length"), ("OK", "stop"))
    lines = _review(llm)
    assert len(llm.calls) == 2, llm.calls
    assert _retried(lines) and _inconclusive(lines), lines

def setting_on_accepts_the_unknown_key_with_a_default():
    from agent.settings import setting_on
    saved = SETTINGS.pop(KEY, _MISSING)
    try:
        assert setting_on(KEY, True) is True and setting_on(KEY, False) is False
    finally:
        if saved is not _MISSING:
            SETTINGS[KEY] = saved

CASES = [empty_and_length_retries_with_12000_and_uses_the_answer, empty_and_stop_does_not_retry,
         answer_at_length_does_not_retry, whitespace_only_answer_counts_as_empty, key_off_does_not_retry,
         retry_also_empty_is_inconclusive_after_two_calls, setting_on_accepts_the_unknown_key_with_a_default]

def main() -> int:
    failed = 0
    for case in CASES:
        try:
            case()
            print(f"ok    {case.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {case.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} v183 outsider review retry cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
