from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import source_io
from .settings import SETTINGS, setting_on

def write_text(path: Path, content: str) -> None:
    source_io.put_back(Path(path), content.encode("utf-8"))

def remove_file(path: Path) -> None:
    source_io.put_back(Path(path), None)

DESC_PREFIX = "DESC-"
MIN_LEAF_SEC = 1200.0
LEAF_SHARE_MAX_SEC = 3600.0
DESC_OVERRUN_SLACK_SEC = 1800.0
REPAIR_MIN_SEC = 300.0

LEAF_AUTHOR_SEC = 600.0
REPAIR_WORK_SEC = 600.0
E2E_FILE_WORST_SEC = 1350.0
SUITE_EST_FACTOR = 1.2

LINT_SCAN_SEC = 30.0
SUITE_PRIOR_PER_FILE_SEC = 30.0

QUOTE_MIN_WORDS = 3
QUOTE_MIN_CJK = 12
QUOTE_STOP_WORDS = frozenset(
    "a an the and or nor of to in on at by for with from as is are be been was were it its this that these those into "
    "onto than then after before when if while but so do does can may must will their there here".split())
WHOLE = "*"

DESCRIPTION_PASS_NOTE = (
    "CLOSING PASS. The application has been built. You are an independent second reader of the DESCRIPTION of the "
    "requirement under === YOUR REQUIREMENT ===. Write 2 to 4 test()s, each checking ONE concrete, observable behaviour "
    "that description states. Title each test \"<requirement id>: <short name of the behaviour>\" where the name contains "
    "at least three consecutive words copied verbatim from the description; or put the line // source: \"<phrase>\" "
    "directly above the test(), the phrase copied verbatim from the description (three words or more). Prefer what a "
    "first reader tends to skip: rules with \"must not / only / without / keeps / restores / switches\", stated roles and "
    "exact names of controls, what survives a reload or a switch and back. No test per scenario; this count and this "
    "title form replace those of GENERATED SCENARIOS (a test with neither the copied words nor the source line is cut "
    "before the first run); every other rule still holds."
)

LATER_BUILT_NOTE = (
    "LATER REQUIREMENTS: every requirement has been worked on by now, so where a rule forbids a control of a LATER "
    "requirement (a sign-out control included), that means only a feature that was never built")

def closing_note(unbuilt: list[str] | tuple[str, ...]) -> str:
    names = [str(n).replace("`", "").replace('"', "'").strip() for n in unbuilt or []]
    names = [n for n in names if n]
    tail = (": " + ", ".join(f'"{n}"' for n in names[:20]) + " -- never use these." if names
            else "; there is none: any control the requirement text names may be used.")
    return DESCRIPTION_PASS_NOTE + " " + LATER_BUILT_NOTE + tail

REPAIR_CONTEXT = (
    "DESCRIPTION PASS: every requirement is implemented. An independent test written from this requirement's DESCRIPTION "
    "alone fails against the finished application. Repair the behaviour the description states without removing or "
    "renaming anything other requirements added. A test step that contradicts the description is a TEST DEFECT: quote both."
)

TEST_HEAD = re.compile(r"(?m)^[ \t]*test(?:\.(?:only|skip|fixme|fail|slow))?\(\s*([\"'`])((?:(?!\1)[^\n\\]|\\.)*)\1")
DESCRIBE = re.compile(r"(?<![\w$.])test\.describe(?:\.\w+)?\s*\(")

RUN_HEAD = re.compile(r"(?m)^[ \t]*test(?:\.(only|skip|fixme|fail|slow))?\(\s*[\"'`]")

NOT_RUN_CALL = re.compile(r"(?<![\w$.])test\s*\.\s*(?:\w+\s*\.\s*)*(?:skip|fixme|fail|only)\b")

FALSE_CONDITION_CALL = re.compile(r"(?<![\w$.])test\s*\.\s*(?:skip|fixme|fail)\s*\(\s*false\s*(?:,[^()]*)?\)\s*;?")

def not_run_call(code: str) -> bool:
    return NOT_RUN_CALL.search(FALSE_CONDITION_CALL.sub("", code)) is not None

def lint_worst_sec() -> float:
    from .testrun import HOOK_ORDER_TIMEOUT_SEC, UNDEFINED_NAMES_TIMEOUT_SEC
    return float(UNDEFINED_NAMES_TIMEOUT_SEC + HOOK_ORDER_TIMEOUT_SEC) + LINT_SCAN_SEC

def leaf_share(left: float, leaves_left: int, suite_est: float) -> float:
    equal = min(LEAF_SHARE_MAX_SEC, left / max(1, leaves_left))
    return max(MIN_LEAF_SEC, LEAF_AUTHOR_SEC + REPAIR_WORK_SEC + max(0.0, suite_est), equal)

SPEC_DEFECT_BACK = (
    "Your spec throws by itself when it runs: the test helper (or the JavaScript runtime) rejects how the spec calls it, "
    "and the first stack frame of each error below is in your spec file. Rewrite only the calls these errors name; keep "
    "every other test, each title with its words copied from the description, and each // source: line as they are.\n"
)

TEST_DEFECT_BACK = (
    "The implementer who repairs the application says a test of your spec is a TEST DEFECT -- it contradicts the "
    "requirement's DESCRIPTION -- for the reason below; without your answer that test is cut. Re-read the description. "
    "Where the reason is right, correct that test, or delete it when the description states nothing it can check. Where "
    "the description's own words support the test as you wrote it, keep it UNCHANGED: it then goes back to the "
    "implementer once more. Adopt nothing from the reason that the description does not state. Keep every other test, "
    "each title with its words copied from the description, and each // source: line as they are.\n\n"
    "The implementer's reason:\n"
)
TEST_DEFECT_AGAIN = (
    "The test author has seen your TEST DEFECT verdict below and answered with the spec you have now (a test it kept "
    "unchanged is its reading of the description). Repair the application for what is still red; where the verdict "
    "still holds, give it again with its quotes.\nYour earlier verdict:\n"
)
INVENTORY_LINE = re.compile(r"\s*(?:url:|on the page now|fields:|headings:|messages:|clickable:)", re.IGNORECASE)

def verdict_reason(detail: str) -> str:
    text = str(detail or "")
    cut = text.find("Latest check output:")
    if cut >= 0:
        text = text[:cut]
    return "\n".join(line for line in text.splitlines() if not INVENTORY_LINE.match(line)).strip()[:2000]

_STRING_LITERAL = re.compile(r"""(['"`])((?:\\.|(?!\1)[^\\\n])*)\1""")

def _literal_norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\\(.)", r"\1", str(text or ""))).strip().lower()

def toward_app_literals(old_block: str, new_block: str, reason: str, description: str) -> list[str]:
    old = {_literal_norm(m.group(2)) for m in _STRING_LITERAL.finditer(old_block or "")}
    said, wanted = _literal_norm(reason), _literal_norm(description)

    quoted = [_literal_norm(q) for q in re.findall(r"[\"`“”]([^\"`“”\n]{3,}?)[\"`“”]", str(reason or ""))]
    found: list[str] = []
    for match in _STRING_LITERAL.finditer(new_block or ""):
        literal = _literal_norm(match.group(2))
        if len(literal) < 3 or literal in old or literal not in said or literal in wanted or literal in found:
            continue
        if any(ch.isdigit() for ch in literal) or any(literal in q for q in quoted):
            found.append(literal)
    return found

def verdict_points_at_spec(reason: str, rel: str, content: str) -> bool:
    if not reason.strip():
        return False
    if Path(rel).name in reason:
        return True
    try:
        from .codegen import QUOTED, unescape_quotes
    except Exception:
        return False

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[`\"“”‘’']", "'", unescape_quotes(text))).strip().lower()

    spec = norm(content)
    quotes = {norm(q) for q in QUOTED.findall(reason) + QUOTED.findall(unescape_quotes(reason))}
    return any(len(q) >= 4 and q in spec for q in quotes)

QUOTE_MARKS = str.maketrans("", "", "`\"'“”‘’")
CJK = re.compile(r"[㐀-鿿豈-﫿]")
CJK_RUN = re.compile(r"[㐀-鿿豈-﫿]+")
WORD = re.compile(r"[^\W_]+")
TITLE_ID = re.compile(r"^\s*\[?(REQ-[0-9A-Za-z.\-]*[0-9A-Za-z])\]?\s*[:：\-–—]")
COMMENT_LINE = re.compile(r"^[ \t]*//.*$")
SOURCE_COMMENT = re.compile(r"^[ \t]*//[ \t]*source[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
ID_HEAD = re.compile(r"^\s*\[?REQ-[0-9A-Za-z.\-]*[0-9A-Za-z]\]?\s*[:：\-–—]\s*")

def _setting_float(key: str, default: float) -> float:
    try:
        return float(SETTINGS.get(key, default))
    except (TypeError, ValueError):
        return default

def _setting_bool(key: str, default: bool) -> bool:
    value = SETTINGS.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)

@dataclass
class Tools:
    run_e2e: Callable[..., Any]
    build_frontend: Callable[..., Any]
    Implementer: Any
    author: Callable[..., Any]
    review: Callable[..., str]
    deterministic_review: Callable[..., list]
    static_defects: Callable[..., str]
    now: Callable[[], float]
    tokens: Callable[[], int]
    quota_exhausted: Callable[[], bool]
    boilerplate_of_text: Callable[[str], set] | None
    failed_test_titles: Callable[[str], dict]
    lost_stated_names: Callable[..., list]
    lost_seed_values: Callable[..., list]
    final_judgement_reserve: Callable[[int], float]
    large_tree_cap_sec: float
    no_spec_ran: tuple = ()
    note_channel: str = "pass_note"
    llm_errors: tuple = (Exception,)
    extra: dict = field(default_factory=dict)
    failed_test_signatures: Callable[[str], dict] | None = None

    lint: Callable[..., str] | None = None

def outsider_reads_pass_note(author: Callable[..., Any]) -> bool:
    try:
        import inspect
        return "pass_note" in inspect.getsource(author)
    except (OSError, TypeError):
        return False

def default_tools(factory: Any, log: Callable[[str], None] = print) -> Tools:
    from . import clock
    from .codegen import CodegenImplementer
    from .llm import LLM, LLMError
    from .outsider import author_acceptance, deterministic_review, review_acceptance
    from .testrun import lint_application, static_test_defects
    from .verify import NO_SPEC_RAN, build_frontend, run_e2e

    module = sys.modules.get(type(factory).__module__)
    try:
        from agent.boilerplate import boilerplate_of_text
    except Exception as exc:
        log(f"[desc-pass] boilerplate detection unavailable ({exc!r}); only the red members of hub batches can be in scope")
        boilerplate_of_text = None
    llm = getattr(factory, "llm", None)

    def tokens() -> int:

        whole_run = getattr(factory, "llm_tokens_total", None)
        try:
            if callable(whole_run):
                return int(whole_run())
            return int(llm.summary().get("total_tokens") or 0) if llm is not None else 0
        except Exception:
            return 0

    return Tools(
        run_e2e=run_e2e, build_frontend=build_frontend, Implementer=getattr(module, "Implementer", CodegenImplementer),
        author=author_acceptance, review=review_acceptance, deterministic_review=deterministic_review,
        static_defects=static_test_defects, now=clock.now, tokens=tokens, quota_exhausted=lambda: bool(LLM.quota_exhausted),
        boilerplate_of_text=boilerplate_of_text,
        failed_test_titles=getattr(module, "failed_test_titles"), failed_test_signatures=getattr(module, "failed_test_signatures", None),
        lost_stated_names=getattr(module, "lost_stated_names"),
        lost_seed_values=getattr(module, "lost_seed_values"), final_judgement_reserve=getattr(module, "final_judgement_reserve"),
        large_tree_cap_sec=float(getattr(module, "LARGE_TREE_CAP_SEC", 129600)), no_spec_ran=tuple(NO_SPEC_RAN),
        note_channel="pass_note" if outsider_reads_pass_note(author_acceptance) else "seed_sentences",
        llm_errors=(LLMError,),
        lint=getattr(module, "lint_application", lint_application),
    )

def safe_prefix(req_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", req_id).strip("-") or "REQ"

def spec_path_for(leaf_id: str) -> str:
    return f"backend/test-e2e/{DESC_PREFIX}{safe_prefix(leaf_id)}.spec.js"

def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)

QUOTED_NAME = re.compile(r"\"([^\"\n]{1,60})\"|“([^”\n]{1,60})”|`([^`\n]{1,60})`")

def unbuilt_names(batch: dict[str, Any], work: list[dict[str, Any]] | None, unattempted: Any) -> list[str]:
    later = [str(n) for n in batch.get("later_names") or []]
    missing = {str(m) for m in unattempted or []}
    if not later or not missing:
        return []
    from .test_author import describe_node

    quoted: set[str] = set()
    for other in work or []:
        members = [str(m) for m in other.get("members") or [other.get("id")] if m is not None]
        if members and all(m in missing for m in members):
            quoted |= {next(g for g in m.groups() if g is not None).strip() for m in QUOTED_NAME.finditer(describe_node(other))}
    return sorted(n for n in later if n in quoted)

def executable_code(text: str) -> str:
    from .codegen import STRING_LITERAL
    from .detaudit import strip_js_comments

    return STRING_LITERAL.sub(lambda m: m.group(0) if m.group(0).startswith("`") and "${" in m.group(0) else '""',
                              strip_js_comments(text))

def _comment_start(content: str, head: int) -> int:
    start = head
    while start > 0 and content[start - 1] == "\n":
        line_start = content.rfind("\n", 0, start - 1) + 1
        if not COMMENT_LINE.match(content[line_start:start - 1]):
            break
        start = line_start
    return start

def split_tests(content: str) -> tuple[str, list[tuple[str, str]]] | None:
    if DESCRIBE.search(executable_code(content)):
        return None
    matches = list(TEST_HEAD.finditer(content))
    if not matches:
        return content, []
    starts = []
    for index, match in enumerate(matches):
        floor = matches[index - 1].end() if index else 0
        starts.append(max(floor, _comment_start(content, match.start())))
    blocks = []
    for index, match in enumerate(matches):
        end = starts[index + 1] if index + 1 < len(matches) else len(content)
        blocks.append((_unescape(match.group(2)), content[starts[index]:end]))
    return content[:starts[0]], blocks

def tests_to_run(content: str) -> int:
    try:
        return sum(1 for m in RUN_HEAD.finditer(executable_code(content)) if m.group(1) not in ("skip", "fixme"))
    except Exception:
        return -1

def by_file_name(red: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, titles in red.items():
        name = Path(str(key)).name
        before = merged.get(name)
        if before is None:
            merged[name] = titles
        elif before == WHOLE or titles == WHOLE:
            merged[name] = WHOLE
        else:
            merged[name] = sorted(set(before) | set(titles))
    return merged

def shown_names(keys: Any) -> dict[str, str]:
    keys = [str(k) for k in keys]
    counts: dict[str, int] = {}
    for key in keys:
        counts[Path(key).name] = counts.get(Path(key).name, 0) + 1
    return {key: (Path(key).name if counts[Path(key).name] == 1 else key) for key in keys}

def join_tests(header: str, blocks: list[tuple[str, str]]) -> str:
    text = header + "".join(block if block.endswith("\n") else block + "\n" for _title, block in blocks)
    return text if text.endswith("\n") else text + "\n"

def _quote_norm(text: str) -> str:
    return " ".join(str(text).translate(QUOTE_MARKS).split()).lower()

def title_quote(title: str) -> str:
    return ID_HEAD.sub("", title, count=1).strip()

def quote_words(text: str) -> list[str]:
    return WORD.findall(CJK.sub(" ", str(text).translate(QUOTE_MARKS).lower()))

def _meaningful(words: list[str] | tuple[str, ...]) -> bool:
    return any(word not in QUOTE_STOP_WORDS for word in words)

def _cjk_quote(text: str, description: str) -> bool:
    desc = "".join(_quote_norm(description).split())
    for run in CJK_RUN.findall(_quote_norm(text)):
        if any(run[i:i + QUOTE_MIN_CJK] in desc for i in range(len(run) - QUOTE_MIN_CJK + 1)):
            return True
    return False

def quote_ok(title: str, description: str) -> bool:
    rest = title_quote(title)
    words, desc = quote_words(rest), quote_words(description)
    n = QUOTE_MIN_WORDS
    grams = {tuple(desc[i:i + n]) for i in range(len(desc) - n + 1)}
    if any(tuple(words[i:i + n]) in grams and _meaningful(words[i:i + n]) for i in range(len(words) - n + 1)):
        return True
    return _cjk_quote(rest, description)

def source_phrase(block: str) -> str:
    head = TEST_HEAD.search(block)
    above = block[:head.start()] if head else ""
    match = SOURCE_COMMENT.search(above)
    return match.group(1).strip().strip("\"'“”‘’`").strip() if match else ""

def source_ok(phrase: str, description: str) -> bool:
    words, desc = quote_words(phrase), quote_words(description)
    if len(words) >= QUOTE_MIN_WORDS and _meaningful(words):
        return f" {' '.join(words)} " in f" {' '.join(desc)} "
    return not words and _cjk_quote(phrase, description) and "".join(_quote_norm(phrase).split()) in "".join(_quote_norm(description).split())

def quote_evidence(title: str, block: str, description: str) -> str:
    if quote_ok(title, description):
        return "title"
    phrase = source_phrase(block)
    return "source" if phrase and source_ok(phrase, description) else ""

SUPERSEDED_HEAD = "Modified Feature Description"

def superseded_sentences(leaf: dict[str, Any]) -> list[str]:
    info = leaf.get("evolution") if isinstance(leaf, dict) else None
    rules = info.get("rules") if isinstance(info, dict) else None
    originals = rules.get("original") if isinstance(rules, dict) else None
    return [str(item.get("text") or "") for item in originals or []
            if isinstance(item, dict) and item.get("overlaps") and str(item.get("text") or "").strip()]

def description_pieces(description: str, sentences: list[str]) -> tuple[list[str], int]:
    text = str(description or "")
    limit = text.find(SUPERSEDED_HEAD)
    limit = len(text) if limit < 0 else limit
    spans = []
    for sentence in sentences:
        words = WORD.findall(str(sentence))
        if not words:
            continue

        pattern = r"(?<![^\W_])" + r"[\W_]+".join(re.escape(w) for w in words) + r"(?![^\W_])"
        spans += [m.span() for m in re.compile(pattern, re.IGNORECASE).finditer(text, 0, limit)]
    if not spans:
        return [text], 0
    pieces, at = [], 0
    for start, end in sorted(spans):
        if start > at:
            pieces.append(text[at:start])
        at = max(at, end)
    pieces.append(text[at:])
    return [p for p in pieces if p.strip()], len(spans)

def quote_outside(title: str, block: str, pieces: list[str]) -> str:
    for piece in pieces:
        by = quote_evidence(title, block, piece)
        if by:
            return by
    return ""

def all_leaves(tree: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        children = [c for c in node.get("children") or [] if isinstance(c, dict)] if isinstance(node.get("children"), list) else []
        if children:
            for child in children:
                walk(child)
        elif str(node.get("id") or "").strip() not in ("", "ROOT"):
            found.append(node)

    walk(tree)
    return found

def order_leaves(leaves: list[dict[str, Any]], everything: list[dict[str, Any]], batch_index: dict[str, int],
                 rank: dict[str, int] | None = None) -> list[dict[str, Any]]:
    rank = rank or {}
    ids = [str(leaf.get("id")) for leaf in everything]

    def expand(dep: str) -> list[str]:
        dep = str(dep).strip()
        return [i for i in ids if i == dep or i.startswith(dep + "-") or i.startswith(dep + ".")]

    deps = {str(leaf.get("id")): [d for raw in (leaf.get("dependencies") or []) if isinstance(leaf.get("dependencies"), list)
                                  for d in expand(raw) if d != str(leaf.get("id"))] for leaf in everything}
    level: dict[str, int] = {}

    def depth(leaf_id: str, seen: frozenset[str]) -> int:
        if leaf_id in level:
            return level[leaf_id]
        if leaf_id in seen:
            return 0
        value = 1 + max((depth(d, seen | {leaf_id}) for d in deps.get(leaf_id, [])), default=-1)
        level[leaf_id] = value
        return value

    dependents: dict[str, set[str]] = {i: set() for i in ids}
    for leaf_id in ids:
        stack, seen = list(deps.get(leaf_id, [])), set()
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            dependents.setdefault(dep, set()).add(leaf_id)
            stack.extend(deps.get(dep, []))
    return sorted(leaves, key=lambda leaf: (rank.get(str(leaf.get("id")), 2), depth(str(leaf.get("id")), frozenset()),
                                            -len(dependents.get(str(leaf.get("id")), ())), -len(leaf.get("scenarios") or []),
                                            batch_index.get(str(leaf.get("id")), 10 ** 6)))

def leaf_ranks(work: list[dict[str, Any]], failed: list[str], green_specs: list[str], spec_context: dict[str, Any],
               foundation_batches: int = 3, hub_leaves: set[str] | frozenset[str] = frozenset()) -> dict[str, int]:
    foundation = {str(m) for batch in work[:foundation_batches] for m in batch.get("members") or [batch.get("id")]}
    for spec in green_specs[:foundation_batches]:
        foundation |= {str(m) for m in (spec_context.get(spec) or {}).get("members") or []}
    foundation |= {str(m) for m in hub_leaves}
    red = {str(m) for m in failed}
    return {leaf: (0 if leaf in foundation else 1) for leaf in red}

def red_hub_leaves(tree: dict[str, Any], work: list[dict[str, Any]], failed: list[str], minimum: int) -> dict[str, tuple[str, int]]:
    from .batching import transitive_dependents

    red = {str(m) for m in failed}
    if not red:
        return {}
    counts = {leaf: len(others) for leaf, others in transitive_dependents(all_leaves(tree)).items()}
    found: dict[str, tuple[str, int]] = {}
    for batch in work:
        members = [str(m) for m in batch.get("members") or [batch.get("id")]]
        hub = max(((m, counts.get(m, 0)) for m in members), key=lambda pair: pair[1], default=None)
        if hub is None or hub[1] < minimum:
            continue
        for member in members:
            if member in red:
                found[member] = hub
    return found

def _same_or_nested(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "-") or a.startswith(b + ".") or b.startswith(a + "-") or b.startswith(a + ".")

def hub_members_at_baseline(candidates: list[str], spec_context: dict[str, Any], baseline: dict[str, Any],
                            ran: set[str]) -> dict[str, str]:
    owners: dict[str, list[str]] = {}
    for spec, ctx in (spec_context or {}).items():
        if isinstance(ctx, dict):
            owners.setdefault(Path(str(spec)).name, [])
            owners[Path(str(spec)).name] += [str(m) for m in ctx.get("members") or []]
    verdicts: dict[str, str] = {}
    for member in candidates:
        names = sorted(name for name, members in owners.items() if member in members and name in ran)
        if not names:
            verdicts[member] = "unknown: no own spec ran at the baseline"
            continue
        verdict = ""
        for name in names:
            titles = baseline.get(name)
            if not titles:
                continue
            if titles == WHOLE:
                verdict = verdict or f"unknown: the failing titles of {name} cannot be read"
                continue
            for title in titles:
                head = TITLE_ID.match(str(title))
                named = [m for m in owners[name] if head and _same_or_nested(head.group(1), m)]
                if member in named:
                    verdict = f"red: {name}: {title}"
                    break
                if not named:
                    verdict = verdict or f"unknown: {name}: {title!r} names no member"
            if verdict.startswith("red"):
                break
        verdicts[member] = verdict or "green"
    return verdicts

def new_red(now: dict[str, Any], baseline: dict[str, Any], now_sigs: dict[str, dict] | None = None,
            base_sigs: dict[str, dict] | None = None) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, titles in now.items():
        before = baseline.get(name)
        if before is None:
            found[name] = titles
        elif titles == WHOLE or before == WHOLE:
            found[name] = titles
        else:
            extra = set(titles) - set(before)
            if now_sigs is not None and base_sigs is not None:
                sig_now, sig_before = now_sigs.get(name) or {}, base_sigs.get(name) or {}
                for title in set(titles) & set(before):
                    a, b = sig_now.get(title, ""), sig_before.get(title, "")
                    if not a or not b or a != b or " @ " not in a:
                        extra.add(title)
            if extra:
                found[name] = sorted(extra)
    return found

@dataclass
class Suite:
    red: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    sigs: dict[str, dict] = field(default_factory=dict)
    skipped: bool = False
    incomplete: str = ""

class DescriptionPass:
    def __init__(self, factory: Any, tree: dict[str, Any], work: list[dict[str, Any]], whole: str, done_ids: list[str],
                 digest: str, description: str, log: Callable[[str], None], tools: Tools) -> None:
        self.f, self.tree, self.work, self.whole = factory, tree, work, whole
        self.done_ids, self.digest, self.description = done_ids, digest, description
        self.log, self.tools = log, tools
        self.dir: Path = factory.project_dir
        state = getattr(factory, "description_pass", None)
        self.state: dict[str, Any] = state if isinstance(state, dict) else {}
        factory.description_pass = self.state
        if not isinstance(getattr(factory, "description_specs", None), list):
            factory.description_specs = []
        self.last_good: dict[str, bytes] | None = None
        self.baseline: dict[str, Any] = {}
        self.baseline_sigs: dict[str, dict] = {}
        self.suite_per_file = 0.0
        self.pass_end = 0.0
        self.token_budget = 0.0
        self.seg_tokens0 = 0
        self.max_leaf_tokens = 0
        self.max_repair_tokens = 0
        self.stop_after_leaf = ""
        self.red_hubs: set[str] = set()
        self.boilerplate_ids: set[str] = set()
        self.hub_green: list[str] = []
        self.last_verdict = ""

    def elapsed(self) -> float:
        return float(getattr(self.f, "spent_before", 0.0) or 0.0) + (self.tools.now() - float(self.f.started))

    def cap_end(self) -> float:
        if self.f.large_tree:
            return self.tools.large_tree_cap_sec
        return float(self.f.time_budget) + 3600.0 + self.tools.final_judgement_reserve(len(self.f.e2e_specs))

    def verify_reserve(self, n_after: int) -> float:
        cls = type(self.f)
        closing_floor = float(getattr(cls, "CLOSING_MAX_SEC", 14400.0)) if self.f.large_tree else 3600.0
        return (closing_floor + self.tools.final_judgement_reserve(n_after) + float(getattr(cls, "SHARED_SWEEP_CAP_SEC", 1200.0))
                + float(getattr(cls, "SHARED_SWEEP_MARGIN_SEC", 900.0)) + DESC_OVERRUN_SLACK_SEC)

    def window_end(self, started_elapsed: float, n_after: int) -> float:
        max_sec = _setting_float("description_pass_max_sec", 10800.0)

        if getattr(self.f, "continuation", None) and getattr(self.f, "stage_number", None):
            max_sec = min(max_sec, _setting_float("continuation_description_pass_sec", 3600.0))
        end = min(self.cap_end() - self.verify_reserve(n_after), started_elapsed + max_sec)
        hard = self.hard_window_end(n_after)
        if hard is not None and hard < end:
            if not getattr(self, "_hard_cut_logged", False):
                self._hard_cut_logged = True
                self.log(f"[hard-end] description pass: window cut from {int(end)} to {int(hard)} s elapsed -- the closing "
                         f"repair (worth more) keeps its chain before the hard end")
            end = hard
        return end

    def hard_window_end(self, n_after: int) -> float | None:
        hard_end = getattr(self.f, "hard_end", None)
        chain = self._closing_reserve_fn()
        end = hard_end() if callable(hard_end) else None
        if end is None or chain is None:
            return None
        at_end = float(getattr(self.f, "spent_before", 0.0) or 0.0) + (float(end) - float(self.f.started))
        return at_end - float(chain(n_after)) - DESC_OVERRUN_SLACK_SEC

    def _closing_reserve_fn(self):
        for name in ("closing_want_sec", "closing_chain_sec"):
            fn = getattr(self.f, name, None)
            if callable(fn):
                return fn
        return None

    def hard_reserve(self) -> float:
        chain = self._closing_reserve_fn()
        return float(chain(len(getattr(self.f, "e2e_specs", []) or []))) if chain is not None else 0.0

    def hard_stop(self, what: str, worst: float) -> bool:
        fits = getattr(self.f, "hard_fits", None)
        hard_end = getattr(self.f, "hard_end", None)
        if not callable(fits) or not callable(hard_end) or hard_end() is None:
            return False
        return not fits(what, worst, reserve=self.hard_reserve())

    @property
    def suite_est(self) -> float:
        return SUITE_EST_FACTOR * self.suite_per_file * max(1, len([s for s in self.f.e2e_specs if (self.dir / s).is_file()]))

    def spent_tokens(self) -> int:
        return int(self.state.get("tokens_spent_before", 0)) + max(0, self.tools.tokens() - self.seg_tokens0)

    def left(self) -> float:
        return self.pass_end - self.elapsed()

    def desc_files(self) -> list[Path]:
        folder = self.dir / "backend" / "test-e2e"
        return sorted(folder.glob(f"{DESC_PREFIX}*.spec.js")) if folder.is_dir() else []

    def remove_leftovers(self) -> list[str]:
        admitted = set(self.state.get("admitted") or [])

        continuation = getattr(getattr(self, "f", None), "continuation", None)
        earlier = set(continuation.get("earlier") or []) if isinstance(continuation, dict) else set()
        removed = []
        for path in self.desc_files():
            rel = path.relative_to(self.dir).as_posix()
            if rel not in admitted and rel not in earlier:
                try:
                    remove_file(path)
                    removed.append(rel)
                except OSError:
                    pass
        if removed:
            self.log(f"[desc-pass] removed {len(removed)} candidate spec file(s) not admitted: {removed}")
        return removed

    def keep_dropped(self, rel: str, content: str) -> None:
        try:
            write_text(self.dir / ".arc" / "description-pass" / "dropped" / Path(rel).name, content)
        except OSError:
            pass
        try:
            remove_file(self.dir / rel)
        except OSError:
            pass

    def write_ledger(self) -> None:
        try:
            write_text(self.dir / ".arc" / "description-pass.json",
                       json.dumps({"summary": self.f.report.get("description_pass"), "ledger": self.state.get("ledger") or []},
                                  ensure_ascii=False, indent=2))
        except (OSError, TypeError) as exc:
            self.log(f"[desc-pass] ledger not written: {exc!r}")

    def hub_scope(self) -> dict[str, tuple[str, int]]:
        if not _setting_bool("description_pass_hubs", True):
            return {}
        try:
            from .settings import hub_min_dependents

            return red_hub_leaves(self.tree, self.work, list(getattr(self.f, "failed_members", []) or []), hub_min_dependents())
        except Exception as exc:
            self.log(f"[desc-pass] red hub batches not determined ({exc!r}); boilerplate leaves only")
            return {}

    def in_scope(self) -> list[dict[str, Any]] | None:
        scope = str(SETTINGS.get("description_pass_scope", "boilerplate") or "boilerplate").strip().lower()
        leaves = all_leaves(self.tree)
        if scope == "all":
            return leaves
        hubs = self.hub_scope()
        self.red_hubs = set(hubs)
        if hubs:
            self.log("[desc-pass] leaves of red hub batches in scope (first): "
                     + ", ".join(f"{leaf} ({hub}: {n} dependents)" for leaf, (hub, n) in sorted(hubs.items())))
        detect = self.tools.boilerplate_of_text
        found_ids: set[str] | None = None
        if detect is None:
            self.log("[desc-pass] no boilerplate detection: " + ("only the leaves of red hub batches are in scope" if hubs else "no leaf is in scope"))
        else:
            try:
                found_ids = {str(leaf_id) for leaf_id, _scenario in detect(self.whole) or ()}
            except Exception as exc:
                self.log(f"[desc-pass] boilerplate detection failed ({exc!r}): "
                         + ("only the leaves of red hub batches are in scope" if hubs else "no leaf is in scope"))
        if found_ids is None and not hubs:
            return None
        self.boilerplate_ids = set(found_ids or set())
        ids = self.boilerplate_ids | set(hubs)
        return [leaf for leaf in leaves if str(leaf.get("id")) in ids]

    def narrow_hub_members(self, leaves: list[dict[str, Any]], specs: list[str]) -> list[dict[str, Any]]:
        if not self.red_hubs:
            return leaves
        verdicts = hub_members_at_baseline(sorted(self.red_hubs), dict(getattr(self.f, "spec_context", {}) or {}),
                                           by_file_name(self.baseline), {Path(s).name for s in specs})
        green = {member for member, verdict in verdicts.items() if verdict == "green"}
        self.red_hubs -= green
        self.hub_green = sorted(green)
        self.log("[desc-pass] members of red hub batches at the baseline: "
                 + "; ".join(f"{member} {verdict[:160]}" for member, verdict in sorted(verdicts.items())))
        if green:
            self.log(f"[desc-pass] {len(green)} member(s) of red hub batches green at the baseline leave the scope: {sorted(green)}")
        return [leaf for leaf in leaves if str(leaf.get("id")) not in green or str(leaf.get("id")) in self.boilerplate_ids]

    def order(self, leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
        f = self.f
        batch_index = {leaf_id: self.batch_of(leaf_id)[1] for leaf_id in (str(leaf.get("id")) for leaf in leaves)}
        ranks = leaf_ranks(self.work, list(getattr(f, "failed_members", []) or []), list(getattr(f, "green_specs", []) or []),
                           dict(getattr(f, "spec_context", {}) or {}), int(getattr(type(f), "FOUNDATION_BATCHES", 3)),
                           hub_leaves=frozenset(self.red_hubs))
        leaves = order_leaves(leaves, all_leaves(self.tree), batch_index, ranks)
        self.log("[desc-pass] leaf order (red foundation / hub leaves, other red leaves, the rest; then dependencies): "
                 + ", ".join(f"{leaf.get('id')}" + ("*" if ranks.get(str(leaf.get("id"))) == 0 else "") for leaf in leaves))
        return leaves

    def batch_of(self, leaf_id: str) -> tuple[dict[str, Any], int]:
        for index, batch in enumerate(self.work):
            if leaf_id in [str(m) for m in batch.get("members") or [batch.get("id")]]:
                return batch, index
        return {}, 10 ** 6

    def leaf_view(self, leaf: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
        leaf_id = str(leaf.get("id"))
        view = dict(batch)
        view.update(id=leaf_id, name=str(leaf.get("name") or ""), description=str(leaf.get("description") or ""),
                    scenarios=list(leaf.get("scenarios") or []), members=[leaf_id], trace_req_id=leaf_id, _leaves=[leaf],
                    pass_note=DESCRIPTION_PASS_NOTE)

        view["unbuilt_names"] = unbuilt_names(batch, getattr(self, "work", None),
                                              getattr(getattr(self, "f", None), "unattempted_members", None))

        view["pass_note"] = closing_note(view["unbuilt_names"])
        view.setdefault("requirement_text", self.whole)
        if not view.get("ancestor_context") and leaf.get("ancestor_context"):
            view["ancestor_context"] = leaf.get("ancestor_context")

        if view.get("effective_rules") or leaf.get("effective_rules"):

            own = str(leaf.get("effective_rules") or "").strip()
            siblings = [b for b in re.split(r"\n\n(?=CURRENTLY EFFECTIVE RULES of )", str(view.get("effective_rules") or ""))
                        if b.strip() and b.strip() != own]
            if siblings and view.get("ancestor_context"):
                from .codegen import strip_effective_rules
                view["ancestor_context"] = strip_effective_rules(str(view["ancestor_context"]), "\n\n".join(siblings))
            if leaf.get("effective_rules"):
                view["effective_rules"] = leaf.get("effective_rules")
            else:
                view.pop("effective_rules", None)
        if self.tools.note_channel == "seed_sentences":
            view["seed_sentences"] = (str(view.get("seed_sentences") or "") + "\n\n" + view["pass_note"]).strip()
        return view

    @property
    def path_ids(self) -> bool:
        return setting_on("description_pass_partial_keep", True)

    def spec_key(self, spec: Any) -> str:
        return Path(str(spec)).as_posix() if self.path_ids else Path(str(spec)).name

    def run_suite(self, specs: list[str], strict: bool = False) -> Suite:
        suite = Suite()
        if strict and not specs:
            suite.incomplete = "no spec file in the suite"
            return suite
        for spec in specs:
            name = Path(spec).name
            key = self.spec_key(spec)

            if self.hard_stop(f"description pass run of {name}", E2E_FILE_WORST_SEC):
                suite.incomplete = f"the hard end is near ({name} not run)"
                self.stop_after_leaf = self.stop_after_leaf or "hard end"
                return suite
            result = self.tools.run_e2e(self.dir, [spec], log=self.log)
            if result.skipped:
                suite.skipped = True
                return suite
            try:
                self.f.record_run([spec], result.ok, result.output)
            except Exception:
                pass
            if strict:
                try:
                    expected = tests_to_run((self.dir / spec).read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    expected = -1
                ran = getattr(result, "ran", -1)
                if expected < 1 or isinstance(ran, bool) or not isinstance(ran, int) or ran != expected:
                    suite.incomplete = f"not every test of {name} ran ({ran} of {expected})"
                    return suite
            if result.ok:
                continue
            if "FAILED SPEC FILES: " not in result.output and any(m in result.output for m in self.tools.no_spec_ran):
                suite.incomplete = f"no spec could run ({name})"
                return suite
            titles = self.tools.failed_test_titles(result.output).get(name)
            suite.red[key] = sorted(titles) if titles else WHOLE
            suite.outputs[key] = result.output
            if self.tools.failed_test_signatures is not None:
                try:
                    suite.sigs[key] = dict(self.tools.failed_test_signatures(result.output).get(name) or {})
                except Exception:
                    suite.sigs[key] = {}
        return suite

    def author(self, view: dict[str, Any], prefix: str, problem: str = "", current: str = ""):
        return self.tools.author(self.f.llm, view, prefix, problem=problem, current=current, log=self.log)

    def recheck(self, view: dict[str, Any], prefix: str, spec: Any) -> Any:
        try:
            problems = self.tools.deterministic_review(view, spec, prefix, log=self.log)
            if not problems:
                return spec
            again = self.author(view, prefix, "A reviewer compared your spec with the requirement and found these "
                                "contradictions:\n" + "\n".join(problems[:8]), spec.content)
            return again if again is not None else spec
        except self.tools.llm_errors:
            raise
        except Exception as exc:
            self.log(f"[desc-pass] {prefix}: re-check of the rewritten spec failed: {exc!r}")
            return spec

    def quote_gate(self, content: str, description: str, tests: dict[str, dict[str, Any]]) -> str:
        parts = split_tests(content)
        if parts is None:
            self.log("[desc-pass] the spec wraps its tests in test.describe; it cannot be gated test by test")
            return ""
        header, blocks = parts
        if not_run_call(executable_code(header)):

            self.log("[desc-pass] quote gate: the file skips / focuses its tests in the file scope; every test dropped")
            for title, block in blocks:
                by = quote_evidence(title, block, description)
                tests.setdefault(title, {"title": title}).update(quote_ok=bool(by), quote_by=by, final="dropped",
                                                                 reason="not-run-modifier")
            return ""
        kept = []
        not_run = 0
        invented = []
        superseded = []

        pieces = list(getattr(self, "superseded_pieces", None) or [])
        for title, block in blocks:
            by = quote_evidence(title, block, description)
            tests.setdefault(title, {"title": title}).update(quote_ok=bool(by), quote_by=by)
            if not_run_call(executable_code(block)):
                not_run += 1
                tests[title].update(final="dropped", reason="not-run-modifier")
            elif by and pieces and not quote_outside(title, block, pieces):
                superseded.append(title)
                tests[title].update(final="dropped", reason="superseded-original")
            elif by:
                kept.append((title, block))
            else:
                invented.append(title)
                tests[title].update(final="dropped", reason="invented-quote")
        if superseded:
            self.log(f"[desc-pass] quote gate: {len(superseded)} test(s) quote only Original sentence(s) this round's Modified "
                     "description replaces (evolution round); dropped: " + json.dumps([t[:160] for t in superseded[:6]], ensure_ascii=False))
        if len(kept) < len(blocks):

            self.log(f"[desc-pass] quote gate: {len(blocks) - len(kept)} of {len(blocks)} test(s) dropped ({len(invented)} with neither "
                     f"{QUOTE_MIN_WORDS} consecutive words of the description in the title nor a verbatim // source: line, "
                     f"{not_run} marked skip / fixme / fail / only): " + json.dumps([t[:160] for t in invented[:6]], ensure_ascii=False))
        return join_tests(header, kept) if kept else ""

    @staticmethod
    def all_ran(result: Any, content: str) -> bool:
        titles = [title for title, _block in (split_tests(content) or ("", []))[1]]
        ran = getattr(result, "ran", -1)
        return bool(result.ok) and not result.skipped and bool(titles) and isinstance(ran, int) and ran == len(titles)

    def process_leaf(self, leaf: dict[str, Any], leaves_left: int) -> dict[str, Any]:
        f, t = self.f, self.tools
        leaf_id = str(leaf.get("id"))
        prefix = DESC_PREFIX + safe_prefix(leaf_id)
        rel = spec_path_for(leaf_id)
        batch, _index = self.batch_of(leaf_id)
        view = self.leaf_view(leaf, batch)
        description = str(leaf.get("description") or "")

        superseded = superseded_sentences(leaf)
        self.superseded_pieces, found = description_pieces(description, superseded) if superseded else ([], 0)
        if not found:
            self.superseded_pieces = []
        if superseded:
            self.log(f"[desc-pass] {leaf_id}: {len(superseded)} Original sentence(s) replaced by this round's Modified "
                     f"description ({found} located in the description); a test quoting only them is dropped")
        started_at, tokens_at = t.now(), self.spent_tokens()
        share = leaf_share(self.left(), leaves_left, self.suite_est)
        leaf_end = min(self.elapsed() + share, self.pass_end)
        entry: dict[str, Any] = {"leaf": leaf_id, "spec": rel, "tests": [], "classification": "", "implementer": {},
                                 "rollback": {"done": False, "reason": ""}, "outcome": ""}
        tests: dict[str, dict[str, Any]] = {}
        revised = False
        self.log(f"[desc-pass] ===== {leaf_id}: {leaf.get('name', '')} =====")

        def finish(outcome: str, admitted: str = "") -> dict[str, Any]:
            entry["outcome"] = outcome
            entry["tests"] = list(tests.values())
            entry["seconds"] = round(t.now() - started_at)
            entry["tokens"] = self.spent_tokens() - tokens_at
            entry["admitted"] = admitted
            return entry

        spec = self.author(view, prefix)
        if spec is None:
            return finish("dropped: no spec")
        if SETTINGS.get("spec_review", True):
            problems = t.review(f.llm, view, spec, prefix, log=self.log)
            if problems:
                rewritten = self.author(view, prefix, "A reviewer compared your spec with the requirement and found these "
                                        "contradictions:\n" + problems, spec.content)
                if rewritten is not None:
                    spec, revised = self.recheck(view, prefix, rewritten), True
        content = self.quote_gate(spec.content, description, tests)
        if not content:
            return finish("dropped: no test quotes the description")
        write_text(self.dir / rel, content)
        defects = t.static_defects(self.dir, [rel])
        if defects:
            self.log(f"[desc-pass] {leaf_id}: the spec does not parse / load; one rewrite:\n{defects[:600]}")
            rewritten = self.author(view, prefix, "These test files do not parse / load:\n" + defects, content)
            content = ""
            if rewritten is not None:
                rewritten = self.recheck(view, prefix, rewritten)
                content = self.quote_gate(rewritten.content, description, tests)
                revised = True
            if content:
                write_text(self.dir / rel, content)
                defects = t.static_defects(self.dir, [rel])
            if not content or defects:
                for item in tests.values():
                    item.setdefault("final", "dropped")
                    item.setdefault("reason", "static-defect")
                self.keep_dropped(rel, content or spec.content)
                return finish("dropped: static defect")
        titles_now = [title for title, _block in (split_tests(content) or ("", []))[1]]
        for title in titles_now:
            tests.setdefault(title, {"title": title, "quote_ok": True})
        build = t.build_frontend(self.dir, log=self.log)
        if not build.ok:
            self.keep_dropped(rel, content)
            self.stop_after_leaf = "build"
            for title in titles_now:
                tests[title].update(final="dropped", reason="build-failed")
            return finish("dropped: the frontend does not build")
        first = t.run_e2e(self.dir, [rel], log=self.log)
        if not first.skipped and not first.ok:

            handed = self.hand_back_spec_defect(leaf_id, view, prefix, rel, content, description, tests, first, leaf_end, entry)
            if handed is not None:
                content, first, titles_now = handed
                revised = True
        if first.skipped:
            self.keep_dropped(rel, content)
            self.stop_after_leaf = "no browser"
            return finish("dropped: no browser")
        name = Path(rel).name
        first_red = set() if first.ok else set(t.failed_test_titles(first.output).get(name) or ())
        for title in titles_now:
            tests[title]["first"] = "red" if (not first.ok and (title in first_red or not first_red)) else "green"
        entry["first_red"] = sorted(first_red) if not first.ok else []
        if first.ok and not self.all_ran(first, content):

            self.log(f"[desc-pass] {leaf_id}: green, but the report counts {getattr(first, 'ran', -1)} test(s) run of "
                     f"{len(titles_now)}; nothing is admitted")
            for title in titles_now:
                tests[title].update(final="dropped", reason="not-run")
            self.keep_dropped(rel, content)
            return finish("dropped: not every test ran")
        if first.ok:
            return finish("admitted", self.admit(leaf_id, view, rel, content, batch, revised, [], first.output, tests))

        repair_reason, repair_written = self.repair(leaf_id, view, rel, content, batch, first, leaf_end, entry)
        if repair_reason == "rolled-back" and entry.get("classification") == "test_defect":

            verdict = self.last_verdict
            back = self.hand_back_test_defect(leaf_id, view, prefix, rel, content, description, tests, leaf_end, entry)
            if back is not None:
                content, first, titles_now = back
                revised = True
                if first.skipped:
                    self.keep_dropped(rel, content)
                    self.stop_after_leaf = "no browser"
                    return finish("dropped: no browser")
                first_red = set() if first.ok else set(t.failed_test_titles(first.output).get(name) or ())
                for title in titles_now:
                    tests[title]["after_hand_back"] = "red" if (not first.ok and (title in first_red or not first_red)) else "green"
                entry["hand_back_red"] = sorted(first_red) if not first.ok else []
                if first.ok and not self.all_ran(first, content):
                    for title in titles_now:
                        tests[title].update(final="dropped", reason="not-run")
                    self.keep_dropped(rel, content)
                    return finish("dropped: not every test ran")
                if first.ok:
                    return finish("admitted after a test-defect hand-back",
                                  self.admit(leaf_id, view, rel, content, batch, revised, [], first.output, tests))

                entry["first_repair"] = {"implementer": dict(entry.get("implementer") or {}), "rollback": dict(entry.get("rollback") or {})}
                repair_reason, repair_written = self.repair(leaf_id, view, rel, content, batch, first, leaf_end, entry,
                                                            note=TEST_DEFECT_AGAIN + verdict_reason(verdict)[:1500])
        if repair_reason == "accepted":
            for title in titles_now:
                tests[title]["final"] = "admitted"
            return finish("admitted after a kept repair", self.admit(leaf_id, view, rel, content, batch, revised,
                                                                   repair_written, "E2E PASSED", tests))
        if repair_reason == "restore-failed":
            self.keep_dropped(rel, content)
            return finish("dropped: restore failed")

        classification = str(entry.get("classification", ""))
        why = "test-defect" if classification == "test_defect" else (
            "not-repaired" if classification.startswith("not repaired") else "app-defect-unrepaired")
        partial = repair_reason == "partial"
        if partial:

            first_red = set((self.partial_kept or {}).get("own_red") or ())
            why = "app-defect-unrepaired"
        if not first_red:
            if partial:
                self.undo_partial(leaf_id, entry, "the failing titles cannot be read")
            for title in titles_now:
                tests[title].update(final="dropped", reason="unreadable-titles")
            self.keep_dropped(rel, content)
            return finish("dropped: the failing titles cannot be read")
        parts = split_tests(content)
        known = {title for title, _ in (parts or ("", []))[1]}
        if parts is None or not first_red <= known:
            if partial:
                self.undo_partial(leaf_id, entry, "red titles cannot be cut out")
            for title in titles_now:
                tests[title].update(final="dropped", reason=why)
            self.keep_dropped(rel, content)
            return finish("dropped: red titles cannot be cut out")
        header, blocks = parts
        kept = [(title, block) for title, block in blocks if title not in first_red]
        for title in first_red:
            tests[title].update(final="dropped", reason=why)
        if not kept:
            if partial:
                self.undo_partial(leaf_id, entry, "every test red")
            self.keep_dropped(rel, content)
            return finish(f"dropped: every test red ({why})")
        pruned = join_tests(header, kept)
        write_text(self.dir / rel, pruned)

        rebuilt = t.build_frontend(self.dir, log=self.log) if repair_reason == "rolled-back" else None
        again = t.run_e2e(self.dir, [rel], log=self.log) if rebuilt is None or rebuilt.ok else None
        if again is None:
            if partial:
                self.undo_partial(leaf_id, entry, "no confirming run")
            for title, _block in kept:
                tests[title].update(final="dropped", reason="build-failed")
            self.keep_dropped(rel, content)
            return finish("dropped: the frontend does not build after the rollback")
        if again.skipped or not again.ok or not self.all_ran(again, pruned):
            reason = "not-run" if (again.ok and not again.skipped) else "flaky"
            if partial:
                self.undo_partial(leaf_id, entry, "the pruned spec is not green" if reason == "flaky" else "not every test ran")
            for title, _block in kept:
                tests[title].update(final="dropped", reason=reason)
            self.keep_dropped(rel, content)
            return finish("dropped: the pruned spec is not green" if reason == "flaky" else "dropped: not every test ran")
        self.log(f"[desc-pass] {leaf_id}: {len(first_red)} red test(s) cut out ({why}): {sorted(first_red)}")
        if partial:
            self.partial_kept = None
            return finish(f"admitted after a partly kept repair ({why})",
                          self.admit(leaf_id, view, rel, pruned, batch, revised, repair_written, again.output, tests))
        return finish(f"admitted after pruning ({why})", self.admit(leaf_id, view, rel, pruned, batch, revised, [], again.output, tests))

    def hand_back_spec_defect(self, leaf_id: str, view: dict[str, Any], prefix: str, rel: str, content: str,
                              description: str, tests: dict[str, dict[str, Any]], first: Any, leaf_end: float,
                              entry: dict[str, Any]) -> tuple[str, Any, list[str]] | None:
        t = self.tools
        try:
            from .codegen import spec_thrown_errors

            thrown = spec_thrown_errors(str(first.output or ""), {rel: content})
        except Exception as exc:
            self.log(f"[desc-pass] {leaf_id}: spec errors not read ({exc!r})")
            thrown = []
        if not thrown:
            return None
        record: dict[str, Any] = {"thrown": [line[:200] for line in thrown[:5]], "result": ""}
        entry["spec_defect_hand_back"] = record
        if self.elapsed() >= leaf_end or self.spent_tokens() >= self.token_budget:
            record["result"] = "not handed back: time" if self.elapsed() >= leaf_end else "not handed back: token budget"
            self.log(f"[desc-pass] {leaf_id}: the spec throws by itself ({thrown[:2]}); {record['result']}")
            return None
        self.log(f"[desc-pass] {leaf_id}: the spec throws by itself; back to its author once: {thrown[:3]}")
        rewritten = self.author(view, prefix, SPEC_DEFECT_BACK + "\n".join(thrown[:10]), content)
        if rewritten is None:
            record["result"] = "no usable rewrite"
            return None
        rewritten = self.recheck(view, prefix, rewritten)
        fresh: dict[str, dict[str, Any]] = {}
        new_content = self.quote_gate(rewritten.content, description, fresh)
        if not new_content:
            record["result"] = "the rewrite keeps no test quoting the description"
            self.log(f"[desc-pass] {leaf_id}: {record['result']}; the first spec stands")
            return None
        write_text(self.dir / rel, new_content)
        defects = t.static_defects(self.dir, [rel])
        if defects:
            write_text(self.dir / rel, content)
            record["result"] = "the rewrite does not parse / load"
            self.log(f"[desc-pass] {leaf_id}: {record['result']}; the first spec stands:\n{defects[:400]}")
            return None
        new_titles = [title for title, _block in (split_tests(new_content) or ("", []))[1]]
        for title, _block in (split_tests(content) or ("", []))[1]:
            if title not in new_titles:
                tests.setdefault(title, {"title": title}).update(final="dropped", reason="spec-defect-rewritten")
        for title, item in fresh.items():
            tests[title] = dict(item)
        for title in new_titles:
            tests.setdefault(title, {"title": title, "quote_ok": True})
        again = t.run_e2e(self.dir, [rel], log=self.log)
        record["result"] = "rewritten: " + ("skipped" if again.skipped else "green" if again.ok else "red")
        self.log(f"[desc-pass] {leaf_id}: spec rewritten after its own error -> {record['result'][11:]}")
        return new_content, again, new_titles

    def hand_back_test_defect(self, leaf_id: str, view: dict[str, Any], prefix: str, rel: str, content: str,
                              description: str, tests: dict[str, dict[str, Any]], leaf_end: float,
                              entry: dict[str, Any]) -> tuple[str, Any, list[str]] | None:
        t = self.tools
        if "test_defect_hand_back" in entry:
            return None
        reason = verdict_reason(self.last_verdict)
        record: dict[str, Any] = {"reason": reason[:300], "result": ""}
        entry["test_defect_hand_back"] = record
        if not verdict_points_at_spec(reason, rel, content):
            record["result"] = "not handed back: the verdict quotes nothing of the spec"
            self.log(f"[desc-pass] {leaf_id}: test defect; {record['result']}")
            return None
        if self.elapsed() >= leaf_end or self.spent_tokens() >= self.token_budget:
            record["result"] = "not handed back: time" if self.elapsed() >= leaf_end else "not handed back: token budget"
            self.log(f"[desc-pass] {leaf_id}: test defect; {record['result']}")
            return None
        self.log(f"[desc-pass] {leaf_id}: the implementer calls the spec a test defect; back to its author once: {reason[:300]!r}")
        rewritten = self.author(view, prefix, TEST_DEFECT_BACK + reason, content)
        if rewritten is None:
            record["result"] = "no usable rewrite"
            return None
        rewritten = self.recheck(view, prefix, rewritten)
        fresh: dict[str, dict[str, Any]] = {}
        new_content = self.quote_gate(rewritten.content, description, fresh)
        if not new_content:
            record["result"] = "the rewrite keeps no test quoting the description"
            self.log(f"[desc-pass] {leaf_id}: {record['result']}; the red tests are cut as before")
            return None

        toward: dict[str, list[str]] = {}
        old_split, new_split = split_tests(content), split_tests(new_content)
        if old_split is None or new_split is None:
            whole = toward_app_literals(content, new_content, reason, description)
            if whole:
                record["result"] = f"the rewrite adopts values only the verdict names: {whole[:3]}"
                self.log(f"[desc-pass] {leaf_id}: {record['result']}; the red tests are cut as before")
                return None
        else:
            old_blocks = dict(old_split[1])
            for title, block in new_split[1]:
                if title in old_blocks and block != old_blocks[title]:
                    found = toward_app_literals(old_blocks[title], block, reason, description)
                    if found:
                        toward[title] = found
            if toward:
                kept = [(title, block) for title, block in new_split[1] if title not in toward]
                self.log(f"[desc-pass] {leaf_id}: rewritten toward the verdict's values, cut: "
                         f"{ {title: found[:3] for title, found in toward.items()} }")
                if not kept:
                    record["result"] = "the rewrite adopts values only the verdict names"
                    return None
                new_content = join_tests(new_split[0], kept)
                record["toward_app"] = sorted(toward)
        write_text(self.dir / rel, new_content)
        defects = t.static_defects(self.dir, [rel])
        if defects:
            write_text(self.dir / rel, content)
            record["result"] = "the rewrite does not parse / load"
            self.log(f"[desc-pass] {leaf_id}: {record['result']}; the red tests are cut as before:\n{defects[:400]}")
            return None
        if int((entry.get("implementer") or {}).get("checks") or 0) > 0:

            build = t.build_frontend(self.dir, log=self.log)
            if not build.ok:
                write_text(self.dir / rel, content)
                record["result"] = "the frontend does not build after the rollback"
                return None
        new_titles = [title for title, _block in (split_tests(new_content) or ("", []))[1]]
        for title, _block in (split_tests(content) or ("", []))[1]:
            if title not in new_titles:
                tests.setdefault(title, {"title": title}).update(
                    final="dropped", reason="test-defect-toward-app" if title in toward else "test-defect-author-dropped")
        for title, item in fresh.items():
            tests.setdefault(title, {"title": title}).update(item)
        for title in new_titles:
            tests.setdefault(title, {"title": title, "quote_ok": True})
        record["unchanged"] = new_content == content
        again = t.run_e2e(self.dir, [rel], log=self.log)
        record["result"] = "rewritten: " + ("skipped" if again.skipped else "green" if again.ok else "red")
        self.log(f"[desc-pass] {leaf_id}: spec answered after the test-defect verdict"
                 f"{' (unchanged)' if record['unchanged'] else ''} -> {record['result'][11:]}")
        return new_content, again, new_titles

    def with_lint(self, outcome: Any) -> Any:
        from .testrun import LINT_HEADER, TestOutcome

        lint = self.tools.lint
        if lint is None:
            return outcome
        worst = lint_worst_sec()
        left = self.left()
        if left < worst or self.hard_stop("the description pass's application lint", worst):
            self.log(f"[desc-pass] application lint skipped: {int(max(0.0, left))} s left in the pass window, the lint "
                     f"takes up to {int(worst)} s (or the hard end is near); the check's own verdict stands")
            return outcome
        try:
            findings = str(lint(self.dir, bool(getattr(self.f, "native_validation", False))) or "")
        except Exception as exc:
            self.log(f"[desc-pass] application lint failed ({exc!r}); no lint findings")
            return outcome
        if not findings:
            return outcome
        return TestOutcome(False, LINT_HEADER + findings + "\n\n" + outcome.output, ran=getattr(outcome, "ran", []),
                           infra=getattr(outcome, "infra", ""),
                           error_contexts=getattr(outcome, "error_contexts", None))

    def repair(self, leaf_id: str, view: dict[str, Any], rel: str, content: str, batch: dict[str, Any], first: Any,
               leaf_end: float, entry: dict[str, Any], note: str = "") -> tuple[str, list[str]]:
        f, t = self.f, self.tools
        self.last_verdict = ""
        from .testrun import TestOutcome

        budget_left = self.token_budget - self.spent_tokens()
        if budget_left < self.max_repair_tokens or budget_left <= 0:
            self.log(f"[desc-pass] {leaf_id}: no repair -- the token budget of the pass would be exceeded")
            entry["classification"] = "not repaired: token budget"
            self.stop_after_leaf = "token budget"
            return "skipped", []
        deadline_elapsed = min(leaf_end, self.pass_end) - self.suite_est
        seconds = deadline_elapsed - self.elapsed()
        if seconds < REPAIR_MIN_SEC:
            self.log(f"[desc-pass] {leaf_id}: no repair -- {int(max(0, seconds))} s left before the leaf's deadline")
            entry["classification"] = "not repaired: time"
            return "skipped", []
        deadline = t.now() + seconds
        clamp = getattr(f, "hard_deadline", None)
        if callable(clamp):
            deadline = clamp(deadline, f"description pass repair of {leaf_id}", reserve=self.hard_reserve())
            if deadline <= t.now():
                self.log(f"[desc-pass] {leaf_id}: no repair -- the hard end is near")
                entry["classification"] = "not repaired: time"
                return "skipped", []
        before = f.snapshot_sources()
        seeds = str(batch.get("seed_sentences") or "")
        specs = [s for s in f.e2e_specs if (self.dir / s).is_file()]
        gate: dict[str, Any] = {"accepted": None, "reason": "", "checks": 0}

        partial_on = setting_on("description_pass_partial_keep", True)
        first_red: set[str] = set()
        if partial_on and not first.ok:
            try:
                first_red = set(t.failed_test_titles(str(first.output or "")).get(Path(rel).name) or ())
            except Exception:
                first_red = set()
        self.partial_kept = None

        def check():
            return self.with_lint(gated())

        def gated():
            gate["accepted"], gate["checks"] = None, gate["checks"] + 1
            gate["partial"] = None
            gate.pop("partial_why_not", None)
            write_text(self.dir / rel, content)
            f.restore_tests()
            build = t.build_frontend(self.dir, log=self.log)
            if not build.ok:
                gate["reason"] = "build-failed"
                return TestOutcome(False, "FRONTEND BUILD FAILED\n" + build.output, infra=getattr(build, "infra", ""))
            own = t.run_e2e(self.dir, [rel], log=self.log)
            if own.skipped or not own.ok:
                gate["reason"] = "own-red"
                if partial_on and not own.skipped:

                    gate["partial"], why_not = self.partial_candidate(own, rel, content, first_red, before, seeds, specs)
                    if why_not:
                        gate["partial_why_not"] = why_not

                return TestOutcome(False, "E2E FAILED against the real application\n" + own.output,
                                   error_contexts=getattr(own, "error_contexts", None))
            if not self.all_ran(own, content):

                gate["reason"] = f"not-run ({getattr(own, 'ran', -1)} of the tests ran)"
                return TestOutcome(True, "E2E PASSED\n" + own.output)
            now = f.snapshot_sources()
            used_by = "\n".join((self.dir / s).read_text(encoding="utf-8", errors="replace") for s in specs + [rel]
                                if (self.dir / s).is_file())
            names = t.lost_stated_names(self.whole, before, now, used_by)
            values = t.lost_seed_values(seeds, before, now)
            if names or values:
                gate["reason"] = ("lost-names " + str(names[:8]) if names else "") + (" lost-seed " + str(values[:8]) if values else "")
                parts = []
                if values:
                    parts.append("SEED DATA CHECK FAILED (the e2e specs themselves passed)\nThis repair removed from the backend values "
                                 "that the requirement's seed sentences state and that were there before it: "
                                 + ", ".join(f'"{v}"' for v in values[:20]) + ". Put them back exactly as they were.")
                if names:
                    parts.append("STATED NAME CHECK FAILED (the e2e specs themselves passed)\nThis repair removed from the application "
                                 "names that the requirement states word for word and that were there before it: "
                                 + ", ".join(f'"{n}"' for n in names[:20]) + ". Put each one back exactly as written (same text, same role).")
                return TestOutcome(False, "\n\n".join(parts))
            suite = self.run_suite(specs)
            if suite.skipped or suite.incomplete:
                gate["reason"] = "suite-incomplete"
                return TestOutcome(False, "E2E FAILED against the real application\nThe test under repair PASSES now, but the "
                                   "whole suite could not be run to the end (" + (suite.incomplete or "skipped") + ").")

            confirmed = new_red(suite.red, self.baseline)
            if confirmed:
                shown = shown_names(confirmed)
                listed = "; ".join(f"{shown[name]}: " + (", ".join(v) if v != WHOLE else "(whole file)") for name, v in sorted(confirmed.items()))
                gate["reason"] = "new-red: " + listed[:400]
                details = "\n".join(suite.outputs.get(name, "")[-3000:] for name in sorted(confirmed))
                return TestOutcome(False, "E2E FAILED against the real application\nThe test under repair PASSES now, but these "
                                   "tests, which passed before this repair, fail: " + listed + ". Keep the repair AND restore "
                                   "what they check.\n" + details)
            gate["accepted"] = {"sources": now, "red": dict(suite.red), "sigs": dict(suite.sigs)}
            gate["reason"] = ""
            return TestOutcome(True, "E2E PASSED\n" + own.output)

        contract, relevant, written_before = self.leaf_contract(leaf_id)
        tokens_at = self.spent_tokens()
        implementer = t.Implementer(f.llm, self.dir, check, max_rounds=2, extra_rounds=1, deadline=deadline,
                                    contract=contract, relevant=relevant, log=self.log, show=written_before)
        status, written, detail = "gave_up", [], ""
        try:
            result = implementer.implement(view, {rel: content}, self.with_lint(TestOutcome(False, "E2E FAILED against the real application\n"
                                                                                            + first.output,
                                                                                            error_contexts=getattr(first, "error_contexts", None))),
                                           extra_context=REPAIR_CONTEXT + ("\n\n" + note if note else ""))
            status, written, detail = result.status, list(result.written or []), str(result.detail or "")
            if status == "test_defect":
                self.last_verdict = detail
        except t.llm_errors as exc:
            detail = f"gateway: {exc!r}"
        except Exception as exc:
            detail = f"crash: {exc!r}"
        self.max_repair_tokens = max(self.max_repair_tokens, self.spent_tokens() - tokens_at)
        entry["implementer"] = {"status": status, "checks": gate["checks"], "written": written}
        entry["classification"] = status
        accepted = gate["accepted"]
        if status == "passed" and accepted is not None and f.snapshot_sources() == accepted["sources"]:
            self.baseline = accepted["red"]
            self.baseline_sigs = accepted.get("sigs") or {}
            self.log(f"[desc-pass] {leaf_id}: repair kept -- own spec green, no new failing test title in the suite "
                     f"({gate['checks']} check(s))")
            return "accepted", written
        partial = gate.get("partial") if partial_on else None
        if (partial is not None and accepted is None and status not in ("passed", "test_defect")
                and f.snapshot_sources() == partial["sources"]):

            self.partial_kept = {"before": before, "own_red": list(partial["own_red"]),
                                 "baseline": dict(self.baseline), "baseline_sigs": dict(self.baseline_sigs)}
            self.baseline = partial["red"]
            self.baseline_sigs = partial.get("sigs") or {}
            entry["partial_keep"] = {"first_red": list(partial["first_red"]), "own_red": list(partial["own_red"]),
                                     "checks": gate["checks"]}
            self.log(f"[desc-pass] {leaf_id}: repair kept in part -- {len(partial['first_red']) - len(partial['own_red'])} "
                     f"more own test(s) green, still red: {partial['own_red']}; no new failing test title in the suite, "
                     f"no stated name / seed value lost ({gate['checks']} check(s))")
            return "partial", written
        reason = gate["reason"] or ("test-defect" if status == "test_defect" else detail[:200] or status)
        failures_before = f.restore_failures
        restored = f.restore_sources(before)
        entry["rollback"] = {"done": True, "reason": reason}
        if partial_on and gate.get("partial_why_not"):
            entry["rollback"]["partial_why_not"] = gate["partial_why_not"]
        self.log(f"[desc-pass] {leaf_id}: repair not kept ({status}; {reason}); {restored} file(s) put back")
        if f.restore_failures > failures_before:
            self.stop_after_leaf = "restore-failed"
            return "restore-failed", []
        return "rolled-back", []

    def partial_candidate(self, own: Any, rel: str, content: str, first_red: set[str], before: Any, seeds: str,
                          specs: list[str]) -> tuple[dict[str, Any] | None, str]:
        t = self.tools
        try:
            name = Path(rel).name
            titles = {title for title, _block in (split_tests(content) or ("", []))[1]}
            ran = getattr(own, "ran", -1)
            if not titles or isinstance(ran, bool) or not isinstance(ran, int) or ran != len(titles):
                return None, f"not every own test ran ({ran} of {len(titles)})"
            red_now = set(t.failed_test_titles(str(own.output or "")).get(name) or ())
            if not red_now or not red_now <= titles:
                return None, "own failing titles unreadable"
            if not first_red or not first_red <= titles:
                return None, "failing titles before the repair unreadable"
            if not red_now < first_red:
                return None, "no strict improvement (" + (f"{len(red_now - first_red)} own test(s) green before are red now"
                                                          if red_now - first_red else "as many own tests red") + ")"
            now = self.f.snapshot_sources()
            used_by = "\n".join((self.dir / s).read_text(encoding="utf-8", errors="replace") for s in specs + [rel]
                                if (self.dir / s).is_file())
            names = t.lost_stated_names(self.whole, before, now, used_by)
            values = t.lost_seed_values(seeds, before, now)
            if names or values:
                return None, ("lost-names " + str(names[:8]) if names else "") + (" lost-seed " + str(values[:8]) if values else "")
            suite = self.run_suite(specs, strict=True)
            if suite.skipped or suite.incomplete:
                return None, "suite-incomplete" + (f" ({suite.incomplete})" if suite.incomplete.startswith(("not every", "no spec file"))
                                                   else "")
            confirmed = new_red(suite.red, self.baseline)
            if confirmed:
                shown = shown_names(confirmed)
                return None, "new-red: " + "; ".join(sorted(shown[k] for k in confirmed))[:300]
            return {"sources": now, "red": dict(suite.red), "sigs": dict(suite.sigs), "own_red": sorted(red_now),
                    "first_red": sorted(first_red)}, ""
        except Exception as exc:
            return None, f"partial check failed: {exc!r}"[:200]

    def undo_partial(self, leaf_id: str, entry: dict[str, Any], why: str) -> None:
        kept, self.partial_kept = self.partial_kept, None
        if not kept:
            return
        self.baseline, self.baseline_sigs = kept["baseline"], kept["baseline_sigs"]
        failures_before = self.f.restore_failures
        restored = self.f.restore_sources(kept["before"])
        entry["rollback"] = {"done": True, "reason": "partial keep undone: " + why}
        self.log(f"[desc-pass] {leaf_id}: partly kept repair put back ({why}); {restored} file(s) put back")
        if self.f.restore_failures > failures_before:
            self.stop_after_leaf = "restore-failed"

    def leaf_contract(self, leaf_id: str) -> tuple[str, list[str], list[str]]:
        written: list[str] = []
        for ctx in (self.f.spec_context or {}).values():
            if leaf_id in [str(m) for m in ctx.get("members") or []]:
                written += [w for w in ctx.get("written") or [] if w not in written]
        try:
            from .contract import contract_slice, relevant_files
            return contract_slice(self.f.contract, [leaf_id]), relevant_files(self.f.contract, [leaf_id]), written
        except Exception:
            return str(getattr(self.f, "contract", "") or ""), [], written

    def admit(self, leaf_id: str, view: dict[str, Any], rel: str, content: str, batch: dict[str, Any], revised: bool,
              repair_written: list[str], output: str, tests: dict[str, dict[str, Any]]) -> str:
        from .test_author import AuthoredFile

        f = self.f
        f.current_members, f.current_trace_id = [leaf_id], leaf_id
        f.write_tests(f"{DESC_PREFIX}{leaf_id}", [AuthoredFile(path=rel, content=content)])
        for spec_list in (f.e2e_specs, f.all_test_files, f.description_specs):
            if rel not in spec_list:
                spec_list.append(rel)
        contract, relevant, written = self.leaf_contract(leaf_id)
        node = {k: v for k, v in view.items() if k != "pass_note"}
        f.spec_context[rel] = {"node": node, "contract": contract, "relevant": relevant, "tests": {rel: content},
                               "members": [leaf_id], "written": list(dict.fromkeys(written + repair_written))}
        if revised:
            f.revised_specs.add(rel)
        f.record_run([rel], True, output)
        f.set_file_tests_status(rel, True)
        admitted = list(self.state.get("admitted") or [])
        if rel not in admitted:
            admitted.append(rel)
        self.state["admitted"] = admitted
        self.baseline.pop(self.spec_key(rel), None)
        self.baseline_sigs.pop(self.spec_key(rel), None)
        for title, _block in (split_tests(content) or ("", []))[1]:
            tests.setdefault(title, {"title": title, "quote_ok": True}).update(final="admitted")
        return rel

    def stop(self, reason: str, **extra: Any) -> dict[str, Any]:
        summary = dict(self.f.report.get("description_pass") or {})
        summary.update(status=reason, **extra)
        self.f.report["description_pass"] = summary
        self.log(f"[desc-pass] {'skipped' if not summary.get('leaves_done') else 'stopped'}: {reason}")
        return summary

    def run(self) -> dict[str, Any]:
        f, t = self.f, self.tools
        self.f.report["description_pass"] = {}
        if not _setting_bool("description_pass", True):
            return self.stop("disabled")
        scoped = self.in_scope()
        if scoped is None:
            return self.stop("no boilerplate detection (no leaf in scope)")
        unattempted = {str(m) for m in getattr(f, "unattempted_members", []) or []}
        done = set(self.state.get("done") or [])
        leaves = [leaf for leaf in scoped if str(leaf.get("id")) not in unattempted and str(leaf.get("id")) not in done]
        if not scoped:
            return self.stop("no leaf in scope")
        if not leaves:
            self.remove_leftovers()
            return self.stop("every leaf in scope is done", leaves_done=len(done))
        if t.quota_exhausted():
            return self.stop("quota")
        self.state.setdefault("started_elapsed", round(self.elapsed(), 1))
        specs = [s for s in f.e2e_specs if (self.dir / s).is_file()]
        self.pass_end = self.window_end(float(self.state["started_elapsed"]), len(f.e2e_specs) + len(leaves))
        prior = SUITE_EST_FACTOR * SUITE_PRIOR_PER_FILE_SEC * max(1, len(specs))
        common = {"leaves_in_scope": len(scoped), "window_end_elapsed": round(self.pass_end)}
        if self.left() < MIN_LEAF_SEC + prior:
            return self.stop("time", **common, left_sec=round(max(0.0, self.left())))
        self.seg_tokens0 = t.tokens()
        if "tokens_before" not in self.state:
            self.state["tokens_before"] = self.seg_tokens0
            self.state["tokens_spent_before"] = 0
        share = _setting_float("description_pass_token_share", 0.5)
        self.token_budget = share * float(self.state.get("tokens_before") or 0)
        if self.token_budget <= 0:
            return self.stop("no token baseline", **common)

        try:
            f.save_checkpoint(self.digest, self.done_ids, self.description)
        except Exception as exc:
            self.log(f"[desc-pass] checkpoint at the start not written: {exc!r}")
        self.remove_leftovers()
        self.last_good = f.snapshot_sources()
        self.refresh_restore_point()
        stopped = "done"
        try:
            build = t.build_frontend(self.dir, log=self.log)
            if not build.ok:
                return self.stop("no baseline (the frontend does not build)", **common)
            began = t.now()
            base = self.run_suite(specs)
            if base.skipped:
                return self.stop("no browser", **common)
            if base.incomplete:
                return self.stop(f"no baseline ({base.incomplete})", **common)
            self.baseline = dict(base.red)
            self.baseline_sigs = dict(base.sigs)
            self.suite_per_file = max(0.0, t.now() - began) / max(1, len(specs))
            unreadable = sorted(name for name, titles in self.baseline.items() if titles == WHOLE)
            if unreadable:
                self.log(f"[desc-pass] failing titles unreadable at the baseline in {unreadable}: while they stay so, no repair "
                         "can show that it turned no test red, and every repair is put back")

            leaves = self.order(self.narrow_hub_members(leaves, specs))
            self.pass_end = self.window_end(float(self.state["started_elapsed"]), len(f.e2e_specs) + len(leaves))
            common.update(window_end_elapsed=round(self.pass_end), hub_members_green=list(self.hub_green))
            if not leaves:
                stopped = "no leaf left in scope after the baseline"
            self.log(f"[desc-pass] {len(leaves)} leaf/leaves in scope; baseline: {len(specs)} spec file(s), "
                     f"{len(self.baseline)} red; window ends at {int(self.pass_end)} s elapsed ({int(self.left())} s left); "
                     f"token budget {int(self.token_budget)}")
            for position, leaf in enumerate(leaves):
                leaf_id = str(leaf.get("id"))
                if t.quota_exhausted():
                    stopped = "quota"
                    break
                if self.left() < MIN_LEAF_SEC + self.suite_est:
                    stopped = "time"
                    break
                if self.spent_tokens() + self.max_leaf_tokens > self.token_budget:
                    stopped = "token budget"
                    break
                tokens_at = self.spent_tokens()
                try:
                    entry = self.process_leaf(leaf, len(leaves) - position)
                except t.llm_errors as exc:
                    self.log(f"[desc-pass] {leaf_id}: the model gateway failed ({exc!r}); the leaf is given up")
                    entry = {"leaf": leaf_id, "outcome": f"dropped: gateway ({type(exc).__name__})", "tests": []}
                    self.put_back_last_good()
                    candidate = self.dir / spec_path_for(leaf_id)
                    if candidate.is_file() and spec_path_for(leaf_id) not in (self.state.get("admitted") or []):
                        self.keep_dropped(spec_path_for(leaf_id), candidate.read_text(encoding="utf-8", errors="replace"))
                self.max_leaf_tokens = max(self.max_leaf_tokens, self.spent_tokens() - tokens_at)
                self.close_leaf(leaf_id, entry)
                if self.stop_after_leaf:
                    stopped = self.stop_after_leaf
                    break
        except Exception as exc:
            self.log(f"[desc-pass] crashed: {exc!r}")
            stopped = f"crash ({type(exc).__name__})"
        finally:
            self.put_back_last_good()
            self.remove_leftovers()
            self.state["tokens_spent_before"] = self.spent_tokens()
            self.seg_tokens0 = t.tokens()
            self.write_ledger()
        ledger = self.state.get("ledger") or []
        partly = sum(1 for e in ledger if str(e.get("outcome", "")).startswith("admitted after a partly kept"))
        if partly and setting_on("description_pass_partial_keep", True):
            common["repairs_partly_kept"] = partly
        summary = self.stop(stopped, **common, leaves_done=len(self.state.get("done") or []),
                            admitted=list(self.state.get("admitted") or []),
                            tests_admitted=sum(1 for e in ledger for x in e.get("tests") or [] if x.get("final") == "admitted"),
                            tests_dropped=sum(1 for e in ledger for x in e.get("tests") or [] if x.get("final") == "dropped"),
                            repairs_kept=sum(1 for e in ledger if str(e.get("outcome", "")).startswith("admitted after a kept")),
                            repairs_rolled_back=sum(1 for e in ledger if (e.get("rollback") or {}).get("done")),
                            tokens_spent=self.state.get("tokens_spent_before"), token_budget=round(self.token_budget))
        self.write_ledger()
        return summary

    def refresh_restore_point(self) -> None:
        refresh = getattr(self.f, "refresh_restore_point", None)
        if refresh is None or self.last_good is None:
            return
        try:
            refresh(self.last_good)
        except Exception as exc:
            self.log(f"[desc-pass] watchdog restore point not refreshed: {exc!r}")

    def put_back_last_good(self) -> None:
        if self.last_good is not None and self.f.snapshot_sources() != self.last_good:
            restored = self.f.restore_sources(self.last_good)
            self.log(f"[desc-pass] {restored} file(s) put back to the last verified state of the pass")

    def close_leaf(self, leaf_id: str, entry: dict[str, Any]) -> None:
        f = self.f
        tests = entry.get("tests") or []
        admitted = sum(1 for x in tests if x.get("final") == "admitted")
        dropped = sum(1 for x in tests if x.get("final") == "dropped")
        reasons = sorted({str(x.get("reason")) for x in tests if x.get("final") == "dropped" and x.get("reason")})
        impl = entry.get("implementer") or {}
        self.log(f"[desc-pass] {leaf_id}: {entry.get('outcome')} | admitted {admitted}/{len(tests)}"
                 + (f" ({dropped} dropped: {', '.join(reasons)})" if dropped else "")
                 + (f" | first run {len(entry.get('first_red') or [])}/{len(tests)} red" if "first_red" in entry else "")
                 + (f" | implementer {impl.get('status')} in {impl.get('checks')} check(s)" if impl else "")
                 + (f" | rolled back: {entry['rollback']['reason']}" if (entry.get("rollback") or {}).get("done") else "")
                 + f" | {entry.get('seconds', 0)} s | {entry.get('tokens', 0)} tokens")
        self.state["ledger"] = list(self.state.get("ledger") or []) + [entry]
        self.state["tokens_spent_before"] = self.spent_tokens()
        self.seg_tokens0 = self.tools.tokens()
        if self.stop_after_leaf == "restore-failed":

            self.write_ledger()
            return
        self.state["done"] = list(dict.fromkeys(list(self.state.get("done") or []) + [leaf_id]))
        self.last_good = f.snapshot_sources()
        self.refresh_restore_point()
        try:
            f.trace.git_commit(f"{leaf_id} (description pass): admitted {admitted} / dropped {dropped}")
        except Exception as exc:
            self.log(f"[desc-pass] commit failed: {exc!r}")
        self.write_ledger()
        f.save_checkpoint(self.digest, self.done_ids, self.description)

def run_description_pass(factory: Any, tree: dict[str, Any], work: list[dict[str, Any]], whole: str, done_ids: list[str],
                         digest: str, description: str, log: Callable[[str], None] = print, tools: Tools | None = None) -> dict[str, Any]:
    return DescriptionPass(factory, tree, work, whole, done_ids, digest, description, log, tools or default_tools(factory, log)).run()
