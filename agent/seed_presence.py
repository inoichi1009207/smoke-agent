from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import Any

QUOTE_PAIRS = {"`": "`", "\"": "\"", "“": "”"}

QV = r"(?:`([^`\n]{1,80})`|\"([^\"\n]{1,80})\"|“([^”\n]{1,80})”)"

QRUN = QV + r"(?:\s*(?:,\s*and|,|and)\s*" + QV + r")*"
QV_RE = re.compile(QV)
SEED_MARK = re.compile(r"(?i)\b(?:seed values?|seed data|seeded data (?:is|are|includes)|evaluation seed contains|pre-?provisions)\b")

POST_STATE = re.compile(r"(?i)\b(?:after|once|then|becomes?|became|changes?|changed|moves?|moved|transfer\w*|renam\w*|"
                        r"no longer|is created|are created|updated|deleted|removed)\b")
CREDENTIAL = re.compile(r"(?i)\b(?:password|passcode|passphrase|credentials?|token|secret)\b")

KIND = (r"(?:account|user|organi[sz]ation|org|repository|repo|issue|branch|pull request|PR|label|milestone|team|shelf|"
        r"book|chapter|page|note|notebook|workbook|worksheet|project|board|folder|document|file|record|group)")

RELATION = re.compile(r"(?i)\b(?:the|its|their)\s+((?:[a-z][\w-]*\s+){0,2}?(?:parent|child|descendant|ancestor|owner)"
                      r"(?:\s+(?:team|record|item|folder|page|organi[sz]ation|account|user|group|repository|book|shelf|"
                      r"chapter|note|label))?)\s+(?:is|are)\s+(" + QRUN + ")")

SEEDED_IS = re.compile(r"(?i)\bthe\s+seeded\s+((?:[a-z][\w-]*\s+){0,4}?[a-z][\w-]*)\s+(?:is|are|named|called|titled)\s+(" + QRUN + ")")
OWNED = [
    re.compile(r"(?i)" + QV + r"'s\s+(?:(?:private|public|personal|internal|archived)\s+)?(" + KIND + r")\s+" + QV),
    re.compile(r"(?i)" + QV + r"\s+owns\s+(?:the\s+|a\s+|an\s+)?(?:(?:private|public|personal|internal)\s+)?(" + KIND + r")\s+" + QV),
]
OWNED_BY = re.compile(r"(?i)\b(" + KIND + r")\s+" + QV + r"\s+(?:is\s+)?(?:owned\s+by|belongs\s+to)\s+(?:the\s+)?(?:" + KIND + r"\s+)?" + QV)

ABSENT_SUBJECT = re.compile(r"(" + QRUN + r")\s*,?\s*(?:and\s+|which\s+)?(?:(?:does|do)\s+not\s+(?:yet\s+|currently\s+|already\s+)?exist|"
                            r"(?:is|are)\s+not\s+(?:yet\s+)?(?:used|taken|in\s+use))\b")
STOP = r"(?:currently|yet|exists?|for|on|named|called|in|between|with|at|before|after|when|and|or|to|of)"

NO_RECORD = re.compile(r"(?i)\bno\s+(?:currently\s+)?((?:open|existing|draft)(?:\s*(?:,|or|,\s*or)?\s+(?:open|existing|draft)\b)*)\s+"
                       r"((?!" + STOP + r"\b)[A-Za-z][\w-]*(?:\s+(?!" + STOP + r"\b)[A-Za-z][\w-]*){0,2})"
                       r"(?:\s+(?:currently|yet))?(?:\s+exists?)?"
                       r"(?:\s+(named|called|for|on|in|between|with)\s+(?:(" + QRUN + r")|(?:that|this|the|these|those)\s+[\w-]+))?")
PREP_RUN = re.compile(r"(?i)\b(?:for|between|of|on)\s+(" + QRUN + ")")

def quoted_values(text: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None).strip() for m in QV_RE.finditer(text)]

def split_sentences(text: str) -> list[str]:
    flat = " ".join(str(text).split())
    out, start, quote, i = [], 0, None, 0
    while i < len(flat):
        ch = flat[i]
        if quote:
            if ch == QUOTE_PAIRS[quote]:
                quote = None

                if flat[i - 1:i] in (".", "!", "?") and i + 2 < len(flat) and flat[i + 1] == " " and flat[i + 2].isupper():
                    out.append(flat[start:i + 1].strip())
                    start = i + 2
        elif ch in QUOTE_PAIRS:
            quote = ch
        elif ch in ".!?" and i + 2 < len(flat) and flat[i + 1] == " " and (flat[i + 2].isupper() or flat[i + 2] in "`\"“("):
            out.append(flat[start:i + 1].strip())
            start = i + 2
        i += 1
    tail = flat[start:].strip()
    if tail:
        out.append(tail)
    return out

def segments(tree: Any) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node_id = str(node.get("id") or "")
        description = node.get("description")
        if isinstance(description, str) and description.strip():
            found.append((node_id, "DESC", description))
        scenarios = node.get("scenarios")
        for scenario in scenarios if isinstance(scenarios, list) else []:
            steps = scenario.get("steps") if isinstance(scenario, dict) else None
            for step in steps if isinstance(steps, list) else []:
                if isinstance(step, dict) and isinstance(step.get("content"), str):
                    found.append((node_id, str(step.get("keyword") or "").strip().upper(), step["content"]))
        children = node.get("children")
        for child in children if isinstance(children, list) else []:
            walk(child)

    walk(tree)
    return found

def _descriptor(before: str) -> str:
    clause = re.split(r"[,;:]|\band\b", before)[-1]
    words = re.findall(r"[A-Za-z][\w-]*", clause)[-5:]
    while words and words[-1].lower() in ("is", "are", "named", "called"):
        words.pop()
    while words and words[0].lower() in ("the", "a", "an", "its", "their", "and"):
        words.pop(0)
    return " ".join(words)

def vacancies(tree: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    for req, part, text in segments(tree):
        if part not in ("DESC", "GIVEN", "WHEN", "AND", "BUT") and not part.startswith("GIVEN"):
            continue
        for sentence in split_sentences(text):
            if SEED_MARK.search(sentence):
                continue
            for match in ABSENT_SUBJECT.finditer(sentence):
                values = quoted_values(match.group(1))
                if not values or any("@" in v for v in values):
                    continue
                item = {"req": req, "part": part, "sentence": sentence, "kind": "absent", "values": values,
                        "qualifiers": [], "thing": _descriptor(sentence[:match.start()])}
                key = ("absent", tuple(values), item["thing"])
                if key not in seen:
                    seen.add(key)
                    found.append(item)
            for match in NO_RECORD.finditer(sentence):
                qualifiers = list(dict.fromkeys(q.lower() for q in re.findall(r"(?i)open|existing|draft", match.group(1))))
                thing = match.group(2)
                prep = (match.group(3) or "").lower()
                direct = quoted_values(match.group(4)) if match.group(4) else []
                if direct:
                    kind, values = ("absent" if prep in ("named", "called") else "free"), direct
                else:

                    runs = [quoted_values(m.group(1)) for m in PREP_RUN.finditer(sentence[:match.start()])]
                    values = runs[-1] if runs else []
                    kind = "free" if values else "unbound"
                if any("@" in v for v in values):
                    continue
                key = (kind, tuple(values), sentence if kind == "unbound" else thing.lower())
                if key in seen:
                    continue
                seen.add(key)
                found.append({"req": req, "part": part, "sentence": sentence, "kind": kind, "values": values,
                              "qualifiers": qualifiers, "thing": thing})
    return found

def named_records(tree: Any) -> list[dict[str, Any]]:
    absent = {v for item in vacancies(tree) if item["kind"] == "absent" for v in item["values"]}
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for req, part, text in segments(tree):
        if part not in ("DESC", "GIVEN") and not part.startswith("GIVEN"):
            continue
        for sentence in split_sentences(text):
            if SEED_MARK.search(sentence) or POST_STATE.search(sentence):
                continue
            items: list[tuple[str, str]] = []
            phrases: list[str] = []
            for match in RELATION.finditer(sentence):
                role = " ".join(match.group(1).lower().split())
                phrases.append(role)
                items += [(v, role) for v in quoted_values(match.group(2))]
            for match in SEEDED_IS.finditer(sentence):
                descriptor = " ".join(match.group(1).lower().split())
                if descriptor in ("data", "values", "value"):
                    continue
                items += [(v, "seeded " + descriptor) for v in quoted_values(match.group(2))]
            for pattern in OWNED:
                for match in pattern.finditer(sentence):
                    owner = next(g for g in match.groups()[0:3] if g is not None).strip()
                    kind = match.group(4).lower()
                    thing = next(g for g in match.groups()[4:7] if g is not None).strip()
                    items += [(owner, f"owner of the {kind} `{thing}`"), (thing, f"{kind} owned by `{owner}`")]
                    phrases.append("owner")
            for match in OWNED_BY.finditer(sentence):
                kind = match.group(1).lower()
                thing = next(g for g in match.groups()[1:4] if g is not None).strip()
                owner = next(g for g in match.groups()[4:7] if g is not None).strip()
                items += [(thing, f"{kind} owned by `{owner}`"), (owner, f"owner of the {kind} `{thing}`")]
                phrases.append("owner")
            kept = []
            for value, role in items:
                at = sentence.find(value)
                before = sentence[max(0, at - 40):at] if at >= 0 else ""
                if "@" in value or value in absent or CREDENTIAL.search(before) or (value, role) in kept:
                    continue
                kept.append((value, role))
            if not kept:
                continue
            key = tuple(v for v, _ in kept)
            if key in seen:
                continue
            seen.add(key)
            found.append({"req": req, "part": part, "sentence": sentence, "items": kept, "phrases": phrases})
    return found

def _norm_phrase(text: str) -> str:
    return " ".join(re.sub(r"[-_]", " ", str(text)).lower().split())

def relied_on_by(tree: Any, record: dict[str, Any]) -> list[str]:
    phrases = [_norm_phrase(p) for p in record.get("phrases") or [] if len(p.split()) >= 2]
    if not phrases:
        return []
    ids: list[str] = []
    for req, _part, text in segments(tree):
        if req and req != record["req"] and req not in ids and any(p in _norm_phrase(text) for p in phrases):
            ids.append(req)
    return ids

TEST_DIRS = re.compile(r"(?:^|/)(?:tests?|test-e2e|e2e-support|__tests__|node_modules|\.arc-test-db|test-results|"
                       r"playwright-report|\.git)/|\.(?:test|spec)\.[cm]?[jt]sx?$", re.IGNORECASE)
SOURCE_SUFFIXES = (".js", ".cjs", ".mjs", ".ts", ".json", ".sql")
MAX_FINDINGS = 8

def backend_text(project_dir: Path, limit: int = 3_000_000) -> str:
    root = Path(project_dir) / "backend"
    parts: list[str] = []
    used = 0
    if not root.is_dir():
        return ""
    files: list[Path] = []
    for folder, dirs, names in os.walk(root):

        dirs[:] = sorted(d for d in dirs if not TEST_DIRS.search((Path(folder, d).relative_to(root).as_posix()) + "/"))
        files += [Path(folder, name) for name in sorted(names)]
    for path in files:
        rel = path.relative_to(root).as_posix()
        if not rel.endswith(SOURCE_SUFFIXES) or TEST_DIRS.search(rel) or rel.startswith("package"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        used += len(text)
        if used > limit:
            break
        parts.append(text.replace("\\", ""))
    return "\n".join(parts)

def holds_token(text: str, value: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", text) is not None

@functools.lru_cache(maxsize=4)
def _tree_of(whole: str) -> Any:
    try:
        import yaml
        data = yaml.safe_load(whole)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None

def seed_presence_findings(own_text: str, whole: str, project_dir: Path, log=print) -> list[dict[str, str]]:
    try:
        tree = _tree_of(whole)
        if tree is None:
            return []
        own_norm = _norm_phrase(own_text)
        records = [r for r in named_records(tree)
                   if any(holds_token(own_text, v) for v, _ in r["items"])
                   or any(len(p.split()) >= 2 and _norm_phrase(p) in own_norm for p in r["phrases"])]
        if not records:
            return []
        backend = backend_text(Path(project_dir))
        findings: list[dict[str, str]] = []
        for record in records:
            missing = [(v, role) for v, role in record["items"] if not holds_token(backend, v)]
            if not missing or len(findings) >= MAX_FINDINGS:
                continue
            listed = ", ".join(f"`{v}` ({role})" for v, role in missing)
            users = relied_on_by(tree, record)
            findings.append({
                "where": "backend seed data",
                "requirement": f"({record['req']}: {record['sentence'][:220]})",
                "problem": (f"The requirement names these records ({record['req']}"
                            + (f"; relied on by {', '.join(users[:4])}" if users else "")
                            + f"), but no backend source file contains them: {listed}. The scenario that starts from them "
                            "finds nothing to open or select."),
                "fix": ("Add each named record to the seed data exactly as written, with the stated relation stored as data "
                        "(a parent is the stored parent, an owner the stored owner), in the state the sentence gives it. "
                        "Keep every other seed record as it is."),
            })
        log(f"[audit] seed-presence: {len(records)} named seed sentence(s) relevant to this batch, {len(findings)} with records absent from backend sources")
        return findings
    except Exception as exc:
        log(f"[audit] seed-presence check crashed: {exc!r}")
        return []

def note_lines(tree: Any) -> tuple[list[str], list[str], list[str]]:
    held: list[str] = []
    unbound: list[str] = []
    for item in vacancies(tree):
        where = f"[{item['req']}, {item['part'].lower() if item['part'] != 'DESC' else 'description'}]"
        values = " + ".join(f"`{v}`" for v in item["values"])
        if item["kind"] == "absent":
            thing = f" ({item['thing']})" if item["thing"] else ""
            held.append(f"  * {where} {values}{thing} is stated as not existing: \"{item['sentence']}\"")
        elif item["kind"] == "free":
            qualifier = " or ".join(item["qualifiers"])
            held.append(f"  * {where} no {qualifier} {item['thing']} on {values}: \"{item['sentence']}\"")
        else:
            unbound.append(f"  * {where} \"{item['sentence']}\"")
    named: list[str] = []
    for record in named_records(tree):
        where = f"[{record['req']}, {record['part'].lower() if record['part'] != 'DESC' else 'description'}]"
        listed = ", ".join(f"`{v}` ({role})" for v, role in record["items"])
        users = relied_on_by(tree, record)
        named.append(f"  * {where} {listed}: \"{record['sentence']}\"" + (f" (relied on by {', '.join(users[:4])})" if users else ""))
    return held, unbound[:8], named
