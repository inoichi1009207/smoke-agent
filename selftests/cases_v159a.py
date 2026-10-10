from __future__ import annotations

import inspect
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main

CASES = []

def case(function):
    CASES.append(function)
    return function

@case
def author_tests_turns_only_the_empty_fallback_into_an_empty_list():
    src = inspect.getsource(main.Factory.author_tests)
    assert "except RuntimeError as exc:" in src, src[-800:]
    assert 'if spec is not None or "produced no test files" not in str(exc):' in src
    i = src.index("except RuntimeError as exc:")
    assert "raise" in src[i:i + 400] and "return []" in src[i:i + 500]

@case
def process_node_reasks_once_and_fails_as_before_when_nothing_comes():
    src = inspect.getsource(main.Factory.process_node)
    i = src.index("files = self.author_tests(node, prefix, node_contract)")
    tail = src[i:i + 1400]
    assert "if not files:" in tail
    assert "author_acceptance(self.llm, node, prefix, log=log)" in tail
    assert 'raise RuntimeError("test author produced no test files")' in tail
    assert tail.index("if not files:") < tail.index("test_files = self.write_tests(req_id, files)")

def main_() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v159a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main_())
