from __future__ import annotations

import bisect
import functools
import json
import os
import re
from pathlib import Path

CHECK = "stage_control_role"
MAX_FINDINGS = 8
BASELINE = Path(".arc") / "stage-controls.json"
MAX_FILES = 600
MAX_BYTES = 300_000
MAX_TAG = 3000
SKIP_DIRS = {"node_modules", "dist", "build", "test", "tests", "__tests__", "test-e2e", "coverage"}
TAG = re.compile(r"<(a|Link|NavLink|button)\b")
FIX = ("Keep the earlier stage's control as it was: the earlier stage delivered {label!r} as a {was} and its tests -- "
       "and this stage's hidden tests -- reach it as a {was}. Render it again as {how} (also when it moved into a shared "
       "header or component); never swap a link for a button-styled control or the reverse.")
HOW = {"link": "a real <a href> or router <Link to=...> (ARIA role link)", "button": "a <button>"}

def _files(project_dir: Path) -> list[Path]:
    src = Path(project_dir) / "frontend" / "src"
    if not src.is_dir():
        return []
    out: list[Path] = []
    for root, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if name.endswith((".tsx", ".jsx")) and not re.search(r"\.(test|spec)\.", name):
                path = Path(root) / name
                try:
                    if path.stat().st_size <= MAX_BYTES:
                        out.append(path)
                except OSError:
                    continue
                if len(out) >= MAX_FILES:
                    return out
    return out

def _opening_end(text: str, start: int) -> int:
    i, depth, quote, n = start + 1, 0, None, min(len(text), start + MAX_TAG)
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
            return -1 if text[i - 1] == "/" else i + 1
        i += 1
    return -1

def controls(project_dir: Path) -> dict[str, dict[str, list[str]]]:
    kinds: dict[str, dict[str, list[str]]] = {"link": {}, "button": {}, "nav_button": {}}
    src = Path(project_dir) / "frontend" / "src"
    for path in _files(project_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in TAG.finditer(text):
            name = m.group(1)
            end = _opening_end(text, m.start())
            if end < 0:
                continue
            close = text.find(f"</{name}>", end)
            if close < 0 or close - end > 300:
                continue
            inner = text[end:close]
            if "<" in inner or "{" in inner:
                continue
            label = " ".join(inner.split())
            if not 1 <= len(label) <= 60:
                continue
            rel = path.relative_to(src).as_posix()
            for kind in (("button", "nav_button") if name == "button" and only_navigates(text, m.start(), text[m.start():end])
                         else ("button",) if name == "button" else ("link",)):
                files = kinds[kind].setdefault(label, [])
                if rel not in files:
                    files.append(rel)
    return kinds

def clear_baseline(project_dir: Path) -> None:
    try:
        (Path(project_dir) / BASELINE).unlink(missing_ok=True)
    except OSError:
        pass

def record_baseline(project_dir: Path) -> int:
    clear_baseline(project_dir)
    try:
        kinds = controls(project_dir)
        target = Path(project_dir) / BASELINE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(kinds, indent=1, sort_keys=True), encoding="utf-8")
        return sum(len(v) for v in kinds.values())
    except Exception:
        return 0

def stage_control_findings(project_dir: Path, log=print) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    try:
        path = Path(project_dir) / BASELINE
        if not path.is_file():
            return []
        before = json.loads(path.read_text(encoding="utf-8"))
        before = {k: {lbl: list(fs) for lbl, fs in (before.get(k) or {}).items()} for k in ("link", "button", "nav_button")}
        now = controls(project_dir)
        for label in sorted(before["link"]):
            lost = [f for f in before["link"][label] if f not in now["link"].get(label, [])]
            new = [f for f in now["nav_button"].get(label, []) if f not in before["nav_button"].get(label, [])]
            if not lost or not new:
                continue
            files = ", ".join(new[:3])
            found.append({
                "where": f"{CHECK}: {files}",
                "requirement": "(the earlier stage's controls are this stage's contract)",
                "problem": (f"The earlier stage delivered {label!r} as a link ({', '.join(lost[:3])}); there it is no "
                            f"longer a link, and a new button {label!r} whose click navigates appeared ({files}). A test "
                            f"that looks for the link named {label!r} finds nothing there."),
                "fix": FIX.format(label=label, was="link", how=HOW["link"]),
            })
            if len(found) >= MAX_FINDINGS:
                break
        turned = [lbl for lbl in sorted(before["button"]) if before["button"][lbl]
                  and not any(f in now["button"].get(lbl, []) for f in before["button"][lbl])
                  and any(f not in before["link"].get(lbl, []) for f in now["link"].get(lbl, []))]
        if turned:
            log(f"[audit] (diagnostic, not reported) earlier-stage button label(s) now only links in their files: {turned[:8]}")
    except Exception as exc:
        log(f"[audit] stage-control check crashed: {exc!r}")
        return []
    if found:
        log(f"[audit] stage-control check: {len(found)} control(s) changed kind since the earlier stage")
    return found

ENTRY_LABEL = re.compile(r"(?i)^(?:sign|log)[ -]?(?:in|up)$")
ENTRY_ROUTE = re.compile(r"(?i)^/(?:[\w-]+/)*(?:sign[-_]?in|log[-_]?in|sign[-_]?up|register)/?$")
NAVIGATES = re.compile(r"\bnavigate\s*\(|\b(?:window\.)?location(?:\.href)?\s*=|\blocation\.(?:assign|replace)\s*\(|\bhistory\.push\s*\(")
TARGET = re.compile(r"navigate\s*\(\s*[\"'`]([^\"'`$]+)[\"'`]")
LITERAL_EXPR = re.compile(r"^\s*\{\s*([\"'])([^\"'{}<>]{1,60})\1\s*\}\s*$")
ARIA = re.compile(r"\baria-label=(?:\"([^\"]+)\"|'([^']+)'|\{\s*[\"']([^\"']+)[\"']\s*\})")

ONLY_NAVIGATE = re.compile(r"\bonClick=\{\s*\(\s*\)\s*=>\s*(?:\{\s*)?navigate\(\s*([\"'])(/[^\"'$`{}]*)\1\s*\)\s*;?\s*(?:\}\s*)?\}")
CLASS_ATTR = re.compile(r"\bclassName=(\"[^\"]*\"|'[^']*')")
TYPE_ATTR = re.compile(r"\btype=(?:\"button\"|'button')")
ROUTER_IMPORT = re.compile(r"import\s*\{([^}]*)\}\s*from\s*(['\"])react-router-dom\2")
ENTRY_FIX = ("Render the visitor's entry {label!r} as a link: <Link to=\"{target}\">{label}</Link> (ARIA role link), styled as "
             "you like -- a button that only navigates is a different control to a test that follows the link {label!r}.")

@functools.lru_cache(maxsize=64)
def _file_facts(text: str) -> tuple[bool, tuple[int, ...], tuple[int, ...]]:
    hook = bool(re.search(r"\b(?:const|let)\s+navigate\s*=\s*useNavigate\s*\(", text))
    opens = tuple(m.start() for m in re.finditer(r"<form\b", text))
    closes = tuple(m.start() for m in re.finditer(r"</form", text))
    return hook, opens, closes

def only_navigates(text: str, start: int, tag: str) -> bool:
    if not ONLY_NAVIGATE.search(tag) or re.search(r"\btype\s*=\s*(?:\"submit\"|'submit'|\{)", tag):
        return False
    hook, opens, closes = _file_facts(text)
    if not hook or re.search(r"\bform\s*=", tag):
        return False
    if bisect.bisect_left(opens, start) > bisect.bisect_left(closes, start) and not TYPE_ATTR.search(tag):
        return False
    return True

def stated_kinds(whole: str, label: str) -> set[str]:
    kinds: set[str] = set()
    name = re.escape(label)
    q_open, q_close = "[“\"'‘]?", "[”\"'’]?"
    for m in re.finditer(r"(?<![\w-])" + q_open + name + q_close + r"\s+(link|button)\b", whole or "", re.IGNORECASE):
        kinds.add(m.group(1).lower())
    for m in re.finditer(r"\b(link|button)\s+(?:named|called|labell?ed)\s+[“\"'‘]" + name + r"[”\"'’]", whole or "", re.IGNORECASE):
        kinds.add(m.group(1).lower())
    return kinds

def _entry_buttons(text: str, whole: str):
    for m in re.finditer(r"<button\b", text):
        end = _opening_end(text, m.start())
        if end < 0:
            continue
        close = text.find("</button>", end)
        if close < 0 or close - end > 300:
            continue
        tag, inner = text[m.start():end], text[end:close]
        if not only_navigates(text, m.start(), tag):
            continue
        label = None
        if "<" not in inner and "{" not in inner:
            label = " ".join(inner.split()) or None
        elif LITERAL_EXPR.match(inner):
            label = LITERAL_EXPR.match(inner).group(2).strip()
        if not label:
            aria = ARIA.search(tag)
            label = next((g for g in aria.groups() if g), None) if aria else None
        target = TARGET.search(tag)
        target = target.group(1) if target else None
        if not ((label and ENTRY_LABEL.match(label)) or (target and ENTRY_ROUTE.match(target))):
            continue
        if label and stated_kinds(whole, label) == {"button"}:
            continue
        if label and re.search(r"<(?:a|Link|NavLink)\b[^<]*>\s*" + re.escape(label) + r"\s*</", text):
            continue
        if not label and target and re.search(r"<(?:a|Link|NavLink)\b[^>]*\b(?:to|href)=\{?\s*[\"']" + re.escape(target) + r"[\"']", text):
            continue
        yield m.start(), close + len("</button>"), label, tag, target

def _rewrite(text: str, start: int, stop: int, label: str | None, tag: str) -> str | None:
    nav = ONLY_NAVIGATE.search(tag)
    if not nav or not label or not ENTRY_LABEL.match(label) or "<" in text[start + len(tag):stop - len("</button>")]:
        return None
    rest = ONLY_NAVIGATE.sub("", tag[len("<button"):-1], count=1)
    cls = CLASS_ATTR.search(rest)
    rest = CLASS_ATTR.sub("", TYPE_ATTR.sub("", rest, count=1), count=1)
    if rest.strip():
        return None
    imp = ROUTER_IMPORT.search(text)
    if not imp or imp.end() > start:
        return None
    link = f'<Link to="{nav.group(2)}"' + (f" className={cls.group(1)}" if cls else "") + f">{label}</Link>"
    new = text[:start] + link + text[stop:]
    names = [n.strip() for n in imp.group(1).split(",") if n.strip()]
    if not any(n == "Link" or n.endswith(" as Link") for n in names):
        new = new[:imp.start(1)] + " Link," + new[imp.start(1):]
    return new

def fix_entry_buttons(project_dir: Path, whole: str = "", log=print) -> list[str]:
    fixed: list[str] = []
    try:
        src = Path(project_dir) / "frontend" / "src"
        for path in _files(project_dir):
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            changed = False
            for start, stop, label, tag, _target in reversed(list(_entry_buttons(text, whole))):
                new = _rewrite(text, start, stop, label, tag)
                if new is None:
                    continue
                text, changed = new, True
                fixed.append(f"{path.relative_to(src).as_posix()}:{label}")
            if changed:
                path.write_bytes(text.encode("utf-8"))
    except Exception as exc:
        log(f"[audit] entry-link rewrite crashed: {exc!r}")
    if fixed:
        log(f"[audit] entry-link rewrite: {', '.join(fixed)} rendered as router <Link> (was a navigating <button>)")
    return fixed

def entry_link_findings(project_dir: Path, whole: str = "", log=print) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    try:
        src = Path(project_dir) / "frontend" / "src"
        for path in _files(project_dir):
            text = path.read_text(encoding="utf-8", errors="replace")
            for start, _stop, label, _tag, target in _entry_buttons(text, whole):
                rel = path.relative_to(src).as_posix()
                line = text.count("\n", 0, start) + 1
                shown = label or f"the entry to {target}"
                found.append({
                    "where": f"{CHECK}: {rel}:{line}",
                    "requirement": "(the entry a signed-out visitor follows to sign in / sign up is a link)",
                    "problem": (f"{rel}:{line} renders {shown!r} as a <button> whose click only navigates; it is not a "
                                f"link, so following the link {shown!r} finds nothing on this page."),
                    "fix": ENTRY_FIX.format(label=label or "Sign in", target=target or "/signin"),
                })
                if len(found) >= MAX_FINDINGS:
                    return found
    except Exception as exc:
        log(f"[audit] entry-link check crashed: {exc!r}")
        return []
    return found
