from __future__ import annotations

import re

from .shared_sweep import quoted_literals

POLLUTION = "spec pollution"
LEAK = "app state leak"
UNKNOWN = "unknown"
LEAK_ERROR = re.compile(r"(?i)toHaveCount|strict mode violation|resolved to \d+ elements|Expected:\s*\d+\s+Received:\s*\d+|"
                        r"already exists|duplicate")
MUTATING = re.compile(r"(?i)\b(?:delete|remove|rename|clear|archive|restore|close|reopen|merge|revoke|edit|update|save|sort|"
                      r"filter|apply|replace|move|undo|redo|cut|paste|assign|unassign|change|transfer|lock|unlock|add|create|"
                      r"new|insert|import|duplicate|copy|freeze|hide|resize)\b")
ACTION_LINE = re.compile(r"\b(?:click|press|fill|check|uncheck|selectOption|dblclick)\s*\(")
ERROR_KEYS = ("Error", "expect(", "Expected", "Received", "Timeout", "could not", "is shown", "no longer shown")

def first_error(output: str, name: str) -> str:
    lines = (output or "").splitlines()
    picked: list[str] = []
    inside = False
    for line in lines:
        if name in line and ("›" in line or ") [" in line or line.strip().startswith(name)):
            inside = True
        if inside and any(k in line for k in ERROR_KEYS):
            picked.append(" ".join(line.split()))
            if len(picked) >= 4:
                break
    return " | ".join(picked)[:600]

def mutated_literals(text: str, literals: set[str]) -> list[str]:
    hit: list[str] = []

    for block in re.split(r"(?m)^\s*test(?:\.\w+)?\(", text or ""):
        lines = block.splitlines()
        for literal in literals:
            if literal in hit:
                continue
            first = next((i for i, line in enumerate(lines) if literal in line), None)
            if first is None:
                continue
            for line in lines[first:]:
                if ACTION_LINE.search(line) and (MUTATING.search(line) or re.search(r"\bfill\s*\(", line)):
                    hit.append(literal)
                    break
    return hit

def classify(error: str, red_text: str, culprit_text: str, within: bool = False) -> tuple[str, str]:
    if not error:
        return UNKNOWN, "the replay names no readable error"
    red = quoted_literals(red_text)
    other = quoted_literals(culprit_text)
    shared = {s for s in red & other if len(s) >= 3 and not s.isdigit()}
    if within:
        shared = {s for s in red if len(s) >= 3 and not s.isdigit()}
    changed = mutated_literals(culprit_text, shared)
    in_error = [s for s in changed if s in error]
    if changed and (in_error or LEAK_ERROR.search(error)):

        what = in_error or changed
        return POLLUTION, (f"{'an earlier test of the same file' if within else 'the predecessor'} changes "
                           f"{', '.join(repr(s) for s in what[:4])}, which this spec also uses")
    if LEAK_ERROR.search(error) and not changed:
        return LEAK, "a count / duplicate failure although the specs change no record they share by name"
    return UNKNOWN, ("the specs share " + ", ".join(repr(s) for s in sorted(shared)[:4]) + " but nothing changes them"
                     if shared else "no shared record and no count / duplicate failure")

def spec_problem(name: str, culprit: str, evidence: str, error: str) -> str:
    return (f"SPEC: {name} passes on fresh data but fails when {culprit} ran before it against the same database "
            f"({evidence}). The graders run every test against ONE database without a reset, and every scenario of the "
            "requirement uses records of its own. Make this spec independent of what other specs change: use the records "
            "its own scenario names (or create its own), never one another spec renames, deletes or edits; keep every "
            f"assertion the requirement states. First error in the shared run: {error[:400]}")

def app_problem(name: str, culprit: str, evidence: str, error: str) -> str:
    return (f"{name} passes on fresh data but fails right after {culprit} ran against the same server and database "
            f"({evidence}). The graders run all tests in one database without a reset: the application must show each "
            "page's own records only -- no state another flow left behind (a global variable, a cached list, a session "
            "or filter that leaks across users or pages, a count that includes another scope's records). Fix the "
            f"application, not the specs. First error in the shared run: {error[:400]}")
