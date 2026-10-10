from __future__ import annotations

import re
from pathlib import Path

CHECK = "router_link"
MAX_FINDINGS = 8
SKIP_DIRS = {"node_modules", "dist", "build", "test", "tests", "__tests__", "test-e2e", "coverage"}
IMPORT = re.compile(r"""import\s*\{([^}]*)\}\s*from\s*['"]react-router(?:-dom)?['"]""")
FIX = ("A react-router <Link>/<NavLink> takes its target from `to`: write `<Link to=\"/\">` (the path the requirement "
       "says the control opens). `href` on a router Link is ignored and the link points at the current page.")

def _finding(where: str, problem: str) -> dict[str, str]:
    return {"where": f"{CHECK}: {where}", "requirement": "(a link the requirement names must open its page)",
            "problem": problem, "fix": FIX}

def _router_names(text: str) -> set[str]:
    names: set[str] = set()
    for m in IMPORT.finditer(text):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part or part.startswith("type "):
                continue
            orig, _, alias = part.partition(" as ")
            if orig.strip() in ("Link", "NavLink"):
                names.add((alias or orig).strip())
    return names

def _opening_tag(text: str, start: int) -> str | None:
    i, depth, quote, n = start + 1, 0, None, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == ">" and depth == 0:
            return text[start:i + 1]
        i += 1
    return None

def _label(text: str, end: int, name: str) -> str:
    close = text.find(f"</{name}>", end)
    if close < 0 or close - end > 400:
        return ""
    inner = re.sub(r"<[^>]*>|\{[^}]*\}", " ", text[end:close])
    return " ".join(inner.split())[:60]

def router_link_findings(project_dir: Path, log=print) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    try:
        src = Path(project_dir) / "frontend" / "src"
        files = [p for p in src.rglob("*.tsx")] + [p for p in src.rglob("*.jsx")] if src.is_dir() else []
        for path in sorted(files):
            if any(part in SKIP_DIRS for part in path.relative_to(src).parts) or re.search(r"\.(test|spec)\.", path.name):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            names = _router_names(text)
            for name in names:
                for m in re.finditer(r"<" + re.escape(name) + r"(?=[\s/>])", text):
                    tag = _opening_tag(text, m.start())
                    if tag is None:
                        continue
                    body = re.sub(r"""(["'`]).*?\1""", '""', tag, flags=re.S)
                    if re.search(r"\{\s*\.\.\.", body):
                        continue
                    if re.search(r"(?<![\w-])to\s*=", body):
                        continue
                    line = text.count("\n", 0, m.start()) + 1
                    rel = path.relative_to(Path(project_dir)).as_posix()
                    has_href = re.search(r"(?<![\w-])href\s*=", body) is not None
                    label = _label(text, m.start() + len(tag), name) if not tag.endswith("/>") else ""
                    found.append(_finding(
                        f"{rel}:{line}",
                        f"<{name}{' href=...' if has_href else ''}>{(' ' + label) if label else ''} (imported from react-router) "
                        f"has no `to`: it points at the page it is on, so clicking it never leaves the page"
                        + (" (the `href` attribute is ignored by a router Link)" if has_href else "") + "."))
                    if len(found) >= MAX_FINDINGS:
                        return found
    except Exception as exc:
        log(f"[audit] router-link check crashed: {exc!r}")
        return []
    return found
