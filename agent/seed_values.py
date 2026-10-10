from __future__ import annotations

import re

TEST_PATH = re.compile(r"(?:^|/)(?:tests?|test-e2e|e2e-support|__tests__)/|\.(?:test|spec)\.[cm]?[jt]sx?$", re.IGNORECASE)
CREDENTIAL_BEFORE = re.compile(r"(?:password|passcode)\W*(?:\w+\W+){0,2}$", re.IGNORECASE)
NOT_A_SEED_VALUE = re.compile(r"^=|^[A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+|=)")

def app_text(files: dict[str, bytes]) -> str:
    return "\n".join(content.decode("utf-8", errors="replace").replace("\\", "")
                     for rel, content in files.items()
                     if rel.startswith(("backend/", "frontend/")) and not TEST_PATH.search(rel))

def stated_seed_values(seeds: str) -> list[str]:
    values: list[str] = []
    for line in seeds.splitlines():
        if not line.lstrip().startswith("- "):
            continue
        for quoted in re.finditer(r"`([^`\n]{1,80})`", line):
            if "@" in quoted.group(1) or CREDENTIAL_BEFORE.search(line[:quoted.start()]):
                continue
            for part in re.split(r"[/,]", quoted.group(1)):
                part = part.strip()
                if len(part) >= 3 and not NOT_A_SEED_VALUE.search(part) and part not in values:
                    values.append(part)
    return values

def stated_literals(seeds: str) -> list[str]:
    values: list[str] = []
    for line in seeds.splitlines():
        if not line.lstrip().startswith("- "):
            continue
        for quoted in re.finditer(r"`([^`\n]{1,80})`", line):
            for part in re.split(r"[/,]", quoted.group(1)):
                part = part.strip()
                if part and part not in values:
                    values.append(part)
    return values

def holds(text: str, value: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", text) is not None

def lost_values(seeds: str, before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    old, new = app_text(before), app_text(after)
    return [value for value in stated_seed_values(seeds) if holds(old, value) and not holds(new, value)]
