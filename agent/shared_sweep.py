from __future__ import annotations

import re
from pathlib import Path

from .batching import _covers
from .verify import NOT_RUN_HEAD, named_failures

def sweep_order(specs: list[str], members_of: dict[str, list[str]], deps_of: dict[str, list[str]]) -> list[str]:
    ordered_input = list(dict.fromkeys(specs))
    needs: list[set[int]] = []
    for index, spec in enumerate(ordered_input):
        wanted: set[int] = set()
        deps = [str(d) for member in members_of.get(spec) or [] for d in deps_of.get(str(member)) or []]
        for dependency in deps:
            for other, candidate in enumerate(ordered_input):
                if other != index and any(_covers(dependency, str(m)) for m in members_of.get(candidate) or []):
                    wanted.add(other)
        needs.append(wanted)
    placed: list[int] = []
    left = list(range(len(ordered_input)))
    while left:
        ready = next((i for i in left if needs[i] <= set(placed)), None)
        if ready is None:
            ready = left[0]
        placed.append(ready)
        left.remove(ready)
    return [ordered_input[i] for i in placed]

def fresh_green_names(specs: list[str], suite_ok: bool, suite_output: str) -> set[str] | None:
    names = [Path(s).name for s in specs]
    if suite_ok:
        return set(names)
    failed = set(named_failures(suite_output or ""))
    if not failed:
        return None
    not_run: set[str] = set()
    match = re.search("^" + re.escape(NOT_RUN_HEAD) + r"(.+)$", suite_output or "", re.MULTILINE)
    if match:
        not_run = {n.strip() for n in match.group(1).split(",") if n.strip()}
    return {n for n in names if n not in failed and n not in not_run}

def red_only_in_shared(order: list[str], green: set[str], statuses: dict[str, str]) -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    ran: list[str] = []
    for spec in order:
        status = statuses.get(spec, "not_run")
        if status == "failed" and Path(spec).name in green:
            found.append((spec, list(ran)))
        if status in ("passed", "failed"):
            ran.append(spec)
    return found

QUOTED = re.compile(r"\"([^\"\n]{3,60})\"|'([^'\n]{3,60})'|`([^`\n$]{3,60})`")
CODE_LIKE = re.compile(r"^[./@]|^[\w-]+\.(?:js|ts|tsx|json)$|^(?:test-e2e|e2e-support)|^[a-z]+$")

def quoted_literals(text: str) -> set[str]:
    found: set[str] = set()
    for groups in QUOTED.findall(text or ""):
        value = next((g for g in groups if g), "").strip()
        if len(value) >= 3 and not CODE_LIKE.search(value):
            found.add(value)
    return found

def shared_literals(red_text: str, earlier: dict[str, str], every_spec: dict[str, str]) -> dict[str, list[str]]:
    counts: dict[str, int] = {}
    for text in every_spec.values():
        for value in quoted_literals(text):
            counts[value] = counts.get(value, 0) + 1
    common = {v for v, n in counts.items() if n >= 3 and n * 2 >= len(every_spec)}
    mine = quoted_literals(red_text) - common
    shared: dict[str, list[str]] = {}
    for spec, text in earlier.items():
        both = sorted(mine & quoted_literals(text))
        if both:
            shared[spec] = both[:6]
    return shared

def sweep_summary(total: int, statuses: dict[str, str], red_only: list[tuple[str, list[str]]], note: str = "") -> str:
    passed = sum(1 for s in statuses.values() if s == "passed")
    failed = sum(1 for s in statuses.values() if s == "failed")
    unknown = sum(1 for s in statuses.values() if s == "unknown")
    not_run = sum(1 for s in statuses.values() if s == "not_run")
    line = (f"advisory, not in the verdict: {total} spec file(s) in one shared database: {passed} passed, {failed} failed "
            f"({len(red_only)} red only after earlier specs), {unknown} without a result, {not_run} not run")
    if red_only:
        line += "; red only after earlier specs: " + ", ".join(Path(s).name for s, _ in red_only)
    if note:
        line += "; " + note.strip().splitlines()[0][:200]
    return line
