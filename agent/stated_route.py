from __future__ import annotations

import re
from typing import Any

ROLES = ("link", "button", "tab", "checkbox", "menuitem", "option", "radio", "switch", "combobox", "textbox", "heading")
_ROLE = "|".join(ROLES)
QUOTED = re.compile(r"[“\"']([^“”\"'\n]{1,80})[”\"']")
RENDERED = re.compile(r"(?i)\b(?:rendered|renders|render|shown|is)\b[^.;]{0,40}?\bas an? <?(" + _ROLE + r")\b")
STATED = re.compile(r"(?i)(?:requirement\s+(?:states|names|says|asks for|calls|requires)(?:\s+it)?(?:\s+as)?|it is not|should be|must be|"
                    r"stated as|names it)\s+an?\s+<?(" + _ROLE + r")\b")
REQUIREMENT = re.compile(r"(?i)\brequirement\b|\bstated\b|\bstates\b")
SUBMIT_CONTEXT = re.compile(r"\b(?:fill|check|selectOption|press|type)\s*\(")

def _text(finding: dict[str, Any]) -> str:
    return " ".join(str(finding.get(k) or "") for k in ("problem", "requirement", "fix", "where"))

def stated_control(finding: dict[str, Any]) -> tuple[str, str, str] | None:
    text = _text(finding)
    stated = STATED.search(text)
    rendered = RENDERED.search(text)
    names = QUOTED.findall(str(finding.get("problem") or "")) or QUOTED.findall(text)
    if not stated or not rendered or not names:
        return None
    to_role, from_role = stated.group(1).lower(), rendered.group(1).lower()
    if to_role == from_role:
        return None
    name = next((n.strip() for n in names if n.strip() and n.strip().lower() not in ROLES and not n.strip().startswith("<")), "")
    return (name, to_role, from_role) if name else None

def quotes_requirement(finding: dict[str, Any]) -> bool:
    text = _text(finding)
    return bool(REQUIREMENT.search(text) and QUOTED.search(str(finding.get("problem") or "")))

def failing_lines(output: str, spec_name: str) -> set[int]:
    found: set[int] = set()
    for match in re.finditer(r"(?:^|[\s\\/(])" + re.escape(spec_name) + r":(\d+)(?::\d+)?\b", output or ""):
        found.add(int(match.group(1)))
    return found

def _addresses(line: str, name: str, role: str) -> bool:
    n = re.escape(name)
    return bool(re.search(r"role\s*:\s*[\"']" + role + r"[\"']\s*,\s*name\s*:\s*[\"'`]" + n + r"[\"'`]", line)
                or re.search(r"name\s*:\s*[\"'`]" + n + r"[\"'`]\s*,\s*role\s*:\s*[\"']" + role + r"[\"']", line)
                or re.search(r"getByRole\(\s*[\"']" + role + r"[\"']\s*,\s*\{\s*name\s*:\s*(?:[\"'`]" + n + r"[\"'`]|/[^/\n]*"
                             + r"[^/\n]*".join(re.escape(w) for w in name.split()) + r"[^/\n]*/)", line))

def names_control(output: str, name: str, role: str) -> bool:
    text = output or ""
    if re.search(r"\b" + role + r"\s+named\s+[\"'“]?" + re.escape(name) + r"\b", text):
        return True
    return any(_addresses(line, name, role) for line in text.splitlines())

def contradicting_lines(text: str, name: str, role: str) -> list[int]:
    return [i for i, line in enumerate((text or "").splitlines(), 1) if _addresses(line, name, role)]

def route_lines(text: str, name: str, from_role: str, failing: set[int]) -> list[int]:
    lines = (text or "").splitlines()
    chosen = []
    for number in contradicting_lines(text, name, from_role):
        before = lines[max(0, number - 4):number - 1]
        if number in failing or not any(SUBMIT_CONTEXT.search(b) for b in before):
            chosen.append(number)
    return chosen

def rewrite_role(text: str, numbers: list[int], name: str, from_role: str, to_role: str) -> str:
    lines = (text or "").split("\n")
    n = re.escape(name)
    for number in numbers:
        if not 1 <= number <= len(lines):
            continue
        line = lines[number - 1]
        line = re.sub(r"(role\s*:\s*[\"'])" + from_role + r"([\"']\s*,\s*name\s*:\s*[\"'`]" + n + r"[\"'`])", r"\g<1>" + to_role + r"\2", line)
        line = re.sub(r"(name\s*:\s*[\"'`]" + n + r"[\"'`]\s*,\s*role\s*:\s*[\"'])" + from_role + r"([\"'])", r"\g<1>" + to_role + r"\2", line)
        line = re.sub(r"(getByRole\(\s*[\"'])" + from_role + r"([\"']\s*,\s*\{\s*name\s*:)", r"\g<1>" + to_role + r"\2", line)
        lines[number - 1] = line
    return "\n".join(lines)
