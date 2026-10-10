from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

import yaml

from agent import clock

PROCESS_STARTED = clock.now()
MONO_STARTED = time.monotonic()

from agent import watchdog
from agent import source_io
from agent import continuation as stage_continuation
from agent import handoff as stage_handoff
from agent import evolution
from agent import seed_foundation
from agent.fsutil import log, safe_path, strip_terminal_codes, write_text
from agent.codegen import FOUNDATION_MARK, SEED_LOCK_FILE, SHARED_DATA_FILE, CodegenImplementer as Implementer, ImplementResult, own_output
from agent.batching import build_batches, transitive_dependents
from agent.contract import build_contract, contract_slice, relevant_files
from agent.visual import attach_visual_notes
from agent.audit import audit_node, findings_brief
from agent.outsider import author_acceptance, ensure_helper, review_acceptance
from agent.settings import SETTINGS, hub_min_dependents, retry_temperature, setting_on, setting_seconds
from agent.llm import LLM, LLMError, llm_from_env
from agent.test_author import AuthoredFile, TestAuthor, describe_node, lost_requirement_facts
from agent.testrun import NATIVE_VALIDATION, TestOutcome, lint_application, parse_defects_in_output, run_node_tests, static_test_defects, unrouted_page_note
from agent.trace import Trace, interface_kind_for
from agent.verify import APP_DOWN_MARKS, BUILD_FRONTEND_WORST_SEC, StepResult, build_frontend, probe_server, run_e2e, set_global_deadline

def e1_takes_batch() -> bool:
    import inspect

    try:
        params = inspect.signature(static_test_defects).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "batch" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)

TIME_BUDGET_SEC = int(os.environ.get("AGENT_TIME_BUDGET_SEC", "10800"))
LARGE_TREE_BASE_SEC = 1800
LARGE_TREE_PER_BATCH_SEC = 2700
LARGE_TREE_CAP_SEC = 129600

FINAL_JUDGEMENT_RESERVE_SEC = 1800

def final_judgement_reserve(spec_files: int) -> float:
    return max(float(FINAL_JUDGEMENT_RESERVE_SEC), 600 + 85 + 600 + 90 * spec_files + 660)
MIN_BATCH_SEC = 1200.0
LARGE_TREE_BATCHES = 4

BUILD_WORST_SEC = BUILD_FRONTEND_WORST_SEC
PROBE_WORST_SEC = 85.0
HARD_END_TAIL_SEC = 300.0
HARD_END_SLACK_SEC = 120.0
HARD_END_FINISH_SEC = BUILD_WORST_SEC + HARD_END_TAIL_SEC + HARD_END_SLACK_SEC

HARD_END_ROUND_SEC = 1800.0

HARD_END_BATCH_AFTER_SEC = 900.0
HARD_END_BATCH_TAIL_SEC = HARD_END_FINISH_SEC + HARD_END_BATCH_AFTER_SEC + HARD_END_ROUND_SEC + MIN_BATCH_SEC
SEED_IDENTITY_SEC = 60.0

def e2e_worst(files: int) -> float:
    return 600.0 + 90.0 * max(1, int(files)) + 660.0

def staged_stage_number(requirements_dir: Path) -> int | None:
    try:
        import yaml as _yaml
        root = _yaml.safe_load((Path(requirements_dir) / "requirements.yaml").read_text(encoding="utf-8", errors="replace"))
        if not isinstance(root, dict):
            return None
        m = (re.search(r"(?:^|\s[-–:]\s*)Stage\s+(\d+)\s*$", str(root.get("name") or "").strip())
             or re.match(r"\s*Stage\s+(\d+)\s+of\b", str(root.get("description") or "")))
        return int(m.group(1)) if m else None
    except Exception:
        return None

def staged_run_target(requirements_dir: Path) -> float | None:
    target = setting_seconds("stage_run_target_sec", 14400.0)
    number = staged_stage_number(requirements_dir)
    if target <= 0 or number is None:
        return None
    final = int(setting_seconds("stage_final_number", 3.0))
    return None if final > 0 and number >= final else float(target)

def platform_hard_end(process_started: float) -> float | None:
    limit = setting_seconds("platform_time_limit_sec", 86400.0)
    if limit <= 0:
        return None
    return float(process_started) + limit - max(0.0, setting_seconds("platform_time_margin_sec", 5400.0))

def wall_now() -> float:
    return time.time()

def contest_deadline_end(now_wall: float | None = None) -> tuple[float | None, str]:
    import datetime as _dt

    raw = str(SETTINGS.get("contest_deadline_utc") or "").strip()
    if not raw or raw.lower() in ("off", "none", "0", "false"):
        return None, "contest deadline: off"
    try:
        stamp = _dt.datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_dt.timezone.utc)
        deadline = stamp.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None, f"contest deadline: {raw!r} unreadable -- ignored"
    margin = max(0.0, setting_seconds("contest_deadline_margin_sec", 3600.0))
    now = time.time() if now_wall is None else float(now_wall)
    return deadline - margin, (f"contest deadline {stamp.astimezone(_dt.timezone.utc).isoformat()} minus {int(margin)} s; "
                               f"{(deadline - margin - now) / 3600.0:.2f} h from now")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement an ARC-Bench task with a two-agent TDD factory.")
    parser.add_argument(
        "requirement_path",
        nargs="?",
        default=os.environ.get("ARCBENCH_TASK_DIR", "requirements"),
        help="Directory containing requirements.yaml.",
    )
    parser.add_argument("--output-dir", default=os.environ.get("ARCBENCH_OUTPUT_DIR", "."))
    parser.add_argument("--type", dest="task_type", default=os.environ.get("ARCBENCH_TASK_TYPE", "web"))
    return parser.parse_args()

INFRA_TEMPLATE_FILES = ("backend/src/database/prepare_e2e.js", "backend/src/database/test_harness.js",
                        "backend/src/database/db_runtime.js")

def infra_template_file(relative: str) -> bool:
    rel = str(relative).replace("\\", "/")
    return rel in INFRA_TEMPLATE_FILES or not rel.startswith(("frontend/src/", "backend/src/"))

def copy_template(template_dir: Path, output_dir: Path, overwrite: bool = True, only=None) -> None:
    if not template_dir.is_dir():
        raise FileNotFoundError(f"template directory missing: {template_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(template_dir.rglob("*")):
        relative = source.relative_to(template_dir)
        if relative.as_posix() == "template.yaml" or not source.is_file():
            continue
        if only is not None and not only(relative.as_posix()):
            continue
        destination = output_dir / relative
        if not overwrite and destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

def copy_requirement_assets(requirements_dir: Path, output_dir: Path) -> str:
    source = requirements_dir / "assets"
    if not source.is_dir():
        return ""
    target = output_dir / "frontend" / "public" / "assets"
    target.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for item in sorted(source.iterdir()):
        if item.is_file():
            shutil.copy2(item, target / item.name)
            names.append(item.name)
    if not names:
        return ""
    log(f"[assets] copied {len(names)} requirement asset(s) to frontend/public/assets")
    return (
        "Static assets shipped with the requirement are already in frontend/public/assets and are served at "
        "/assets/<file> (use <img src=\"/assets/NAME\" alt=\"...\">): " + ", ".join(names)
    )

def load_tree(requirements_dir: Path) -> dict[str, Any]:
    path = requirements_dir / "requirements.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"requirements.yaml not found in {requirements_dir}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or str(payload.get("id") or "").strip() != "ROOT":
        raise ValueError("requirements.yaml must contain a ROOT mapping")
    return payload

def atomic_nodes(tree: dict[str, Any]) -> list[tuple[dict[str, Any], list[str]]]:
    found: list[tuple[dict[str, Any], list[str]]] = []

    def walk(node: dict[str, Any], ancestors: list[str]) -> None:
        children = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        node_id = str(node.get("id") or "").strip()
        if children:
            for child in children:
                walk(child, ancestors + [node_id])
        elif node_id and node_id != "ROOT":
            found.append((node, ancestors))

    walk(tree, [])
    return found

def attach_ancestor_context(tree: dict[str, Any], per_ancestor_chars: int = 3000) -> None:

    def walk(node: dict[str, Any], inherited: list[str]) -> None:
        if inherited:
            node["ancestor_context"] = "\n\n".join(inherited)
        description = str(node.get("description") or "").strip()
        mine = list(inherited)
        if description:
            text = description if len(description) <= per_ancestor_chars else description[:per_ancestor_chars] + " ..."
            mine.append(f"[{node.get('id')}] {node.get('name', '')}: {text}")
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child, mine)

    walk(tree, [])

RULE_SENTENCE = re.compile(r"\baria-|\bARIA\b|\brole\b|accessible name|\bURL\b|\bmust\b|\bexactly one\b|\bunique\b|"
                           r"\bpersist|\batomic|\beither\b|\bindependent|\bunaffected\b", re.IGNORECASE)

def shared_module_rules(tree: dict[str, Any], per_module: int = 1200, total: int = 3000) -> dict[str, str]:
    rules: dict[str, str] = {}
    used = 0
    for module in [c for c in tree.get("children") or [] if isinstance(c, dict) and c.get("children")]:
        description = " ".join(str(module.get("description") or "").split())
        sentences = re.split(r"(?<=[.!?])\s+", description)

        picked = description if len(description) <= per_module * 1.2 else " ".join(s.strip() for s in sentences if RULE_SENTENCE.search(s))[:per_module]
        if picked and used < total:
            picked = picked[: total - used]
            rules[str(module.get("id"))] = f"[{module.get('id')}] {module.get('name', '')} (rules for the whole product): {picked}"
            used += len(picked)
    return rules

def attach_shared_rules(work: list[dict[str, Any]], rules: dict[str, str]) -> None:
    for batch in work:
        context = str(batch.get("ancestor_context") or "")
        extra = [text for module, text in rules.items() if f"[{module}]" not in context]
        if extra:
            batch["ancestor_context"] = (context + "\n\n" + "\n\n".join(extra)).strip()

def requirement_digest(tree: dict[str, Any], max_chars: int = 30000) -> str:
    parts: list[str] = []
    for node, _ancestors in atomic_nodes(tree):
        lines = [f"[{node.get('id')}] {node.get('name', '')}: {' '.join(str(node.get('description') or '').split())}"]
        for scenario in node.get("scenarios") or []:
            steps = "; ".join(f"{s.get('keyword', '')} {' '.join(str(s.get('content', '')).split())}" for s in scenario.get("steps") or [])
            if steps:
                lines.append(f"  - {steps}")
        parts.append("\n".join(lines))
    text = "\n".join(parts)
    if len(text) <= max_chars:
        return text

    share = max_chars // max(1, len(parts)) - 1
    if share >= 160:
        return "\n".join(part if len(part) <= share else part[:share - 4] + " ..." for part in parts)

    index = [part.split(":", 1)[0][:share] for part in parts]
    return "\n".join(index)[:max_chars]

SEED_SENTENCE_WHOLE = re.compile(r"(?:The\s+)?(?:Seed data:|evaluation seed contains|Seed values:|seeded data (?:is|includes))"
                                 r"(?:[^.`\"“]|`[^`]*`|\"[^\"]*\"|“[^”]*”|\.(?=\S))*\.?", re.IGNORECASE)

def seed_digest(tree: dict[str, Any], max_chars: int = 6000) -> str:
    seen: list[str] = []
    for node, _ancestors in atomic_nodes(tree):
        texts = [str(node.get("description") or "")]
        texts += [str(s.get("content") or "") for sc in node.get("scenarios") or [] for s in sc.get("steps") or []]
        for text in texts:
            flat = " ".join(text.split())
            for match in SEED_SENTENCE_WHOLE.finditer(flat):
                sentence = match.group(0).strip()
                if sentence and sentence not in seen:
                    seen.append(sentence)
    if not seen:
        return ""
    block = "\n".join(f"- {s}" for s in seen)
    return "SEED SENTENCES OF THE WHOLE REQUIREMENT (records that exist before any test; use them by these names):\n" + block[:max_chars]

SEED_CONTENTS = re.compile(r"\b(?:cells?|ranges?|rows?|headers?|formulas?|values?)\s+`|`[A-Z]{1,3}[0-9]{1,5}(?::[A-Z]{1,3}[0-9]{1,5}|=)")

def seed_scenarios(tree: dict[str, Any] | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for node, _ancestors in atomic_nodes(tree) if isinstance(tree, dict) else []:

        scenarios = node.get("scenarios") if isinstance(node, dict) else None
        for scenario in scenarios if isinstance(scenarios, list) else []:
            if not isinstance(scenario, dict):
                continue
            raw = scenario.get("steps")
            steps = [s for s in (raw if isinstance(raw, list) else []) if isinstance(s, dict)]

            def text(*keywords: str) -> str:
                return " ".join(seed_match_form(str(s.get("content") or ""))
                                for s in steps if str(s.get("keyword") or "").strip().upper() in keywords)
            found.append((text("GIVEN"), text("GIVEN", "WHEN")))
    return found

_QUOTES = str.maketrans({c: "'" for c in "“”‘’\"`"})

def seed_match_form(text: str) -> str:
    return " ".join(str(text).translate(_QUOTES).split()).lower().rstrip(" .;:")

def seed_content_differences(seeds: str, tree: dict[str, Any] | None = None) -> str:
    scenarios = seed_scenarios(tree)
    groups: dict[str, list[str]] = {}
    for line in seeds.splitlines()[1:]:
        sentence = line[2:].strip() if line.startswith("- ") else line.strip()
        if not SEED_CONTENTS.search(sentence):
            continue
        for group in SEED_ENTITY.findall(sentence):
            for name in SEED_VALUE.findall(group):
                groups.setdefault(name, [])
                if sentence not in groups[name]:
                    groups[name].append(sentence)

    unnamed = []
    for line in seeds.splitlines()[1:]:
        sentence = line[2:].strip() if line.startswith("- ") else line.strip()
        if SEED_CONTENTS.search(sentence) and not any(SEED_VALUE.findall(g) for g in SEED_ENTITY.findall(sentence)):
            unnamed.append(sentence)
    differing = [name for name, sentences in groups.items() if len(sentences) >= 2]
    if len(differing) == 1:
        name = differing[0]
        for sentence in unnamed:
            stating = [given_when for given, given_when in scenarios if seed_match_form(sentence) in given]
            if stating and all(seed_match_form(name) in given_when for given_when in stating) and sentence not in groups[name]:
                groups[name].append(sentence)

    notes = []
    for name, sentences in groups.items():
        if len(sentences) < 2:
            continue
        counted = sorted(((sum(1 for given, _ in scenarios if seed_match_form(s) in given), s) for s in sentences[:8]),
                         key=lambda x: -x[0])
        listed = "\n".join(f"  - ({n} scenario(s)) {s}" if scenarios else f"  - {s}" for n, s in counted)
        notes.append(f"`{name}` is given different contents by different requirements:\n{listed}\n"
                     f"The application holds `{name}` once. Its seed is the UNION of these contents, cell by cell: every stated "
                     f"value that no other sentence contradicts for the SAME cell (same workbook, same worksheet, same cell) is "
                     f"seeded exactly as written. Only where two sentences give one cell different values does the sentence "
                     f"stated by more scenarios (the counts above) win. A spec whose own seed sentence was seeded completely relies "
                     f"on those contents, exactly as its GIVEN says; a spec whose sentence lost a cell never relies on the cell "
                     f"contents of `{name}` -- it creates its own record through the interface an earlier requirement provides "
                     f"and enters the starting values its scenario states there; `{name}` itself is only opened or looked for. "
                     f"A spec that relies on `{name}` checks the cells its own GIVEN states and nothing about the rest: it never "
                     f"asserts that a row, an exported line, a list or a range holds ONLY those values (an equality on a whole "
                     f"CSV line, a row's full text, a count of columns) -- the other sentences above add cells to the same "
                     f"record, and such an assertion turns red on a correct application (a spec that expects the exported "
                     f"line `<a>,<b>` fails once the union seed makes it `<a>,<b>,<c>`). Check each stated value on its own: "
                     f"the line contains it, the cell shows it. Only where the scenario itself states the WHOLE output exactly "
                     f"(\"the exported line is exactly ...\") does that statement win, and the spec checks it as written.")

    return ("SEED RECORDS WITH DIFFERENT CONTENTS (decided from the requirement text):\n" + "\n".join(notes)) if notes else ""

SEED_CONSTRAINTS_HEAD = "SEED CONSTRAINTS STATED OUTSIDE THE SEED SENTENCES (decided from the requirement text):"

def seed_constraints_note(tree: dict[str, Any] | None, log=None) -> str:
    from agent.seed_presence import note_lines

    held, unbound, named = note_lines(tree if isinstance(tree, dict) else None)

    seed_text = seed_digest(tree, max_chars=10 ** 9) if isinstance(tree, dict) else ""
    kept_held: list[str] = []
    for line in held:
        head = line.split("is stated as not existing", 1)[0] if "is stated as not existing" in line else ""

        clash = [value for value in re.findall(r"[`\"“”]([^`\"“”\n]+)[`\"“”]", head)
                 if re.search(r"[`\"“”]" + re.escape(value) + r"[`\"“”]", seed_text)]
        if clash:
            if log:
                log(f"[factory] seed constraint left out (also named as seed data, decide by hand): {', '.join(clash)}")
            continue
        kept_held.append(line)
    held = kept_held
    parts: list[str] = []
    if held:
        parts.append(
            "Records a scenario says do NOT exist when it starts (the scenario creates one there; a seed record in that "
            "place makes the creation fail):\n" + "\n".join(held) + "\n"
            "The seed leaves each of these places empty: no record with the named value, and no record of the named "
            "kind on the named combination of values. A seed record that another requirement needs (a seeded <thing> of "
            "the same kind) goes on OTHER values -- another pair of <a> / <b>, another name -- never on a place listed "
            "here. In a combination line (\"no <thing> on `<a>` + `<b>`\") the records `<a>` and `<b>` themselves still "
            "exist as the seed sentences say; only the <thing> on them is absent. A spec for such a scenario uses exactly these values and expects the creation to succeed.")
    if unbound:
        parts.append("Stated without naming values (keep the records they describe free when the scenario starts):\n"
                     + "\n".join(unbound))
    if named:
        parts.append(
            "Records and relations that must exist in the seed (named outside the seed sentences; the role in "
            "brackets):\n" + "\n".join(named) + "\n"
            "Seed each one exactly as written, in the state the sentence gives it, with the relation stored as data: a "
            "parent is the stored parent of its child, a descendant sits below the record it descends from, an owner "
            "is the stored owner. A spec that needs one of them opens it by this name; it does not create it.")
    return (SEED_CONSTRAINTS_HEAD + "\n" + "\n".join(parts)) if parts else ""

def contract_with_seed_constraints(contract: str, note: str, head: str = "") -> str:
    if not note or not contract:
        return contract
    head = head or SEED_CONSTRAINTS_HEAD
    try:
        data = json.loads(contract)
    except (json.JSONDecodeError, TypeError, ValueError):
        return contract if head in contract else contract + "\n\n" + note
    if not isinstance(data, dict):
        return contract if head in contract else contract + "\n\n" + note
    notes = data.get("notes")
    notes = [n for n in notes if not (isinstance(n, str) and n.startswith(head))] if isinstance(notes, list) else ([notes] if notes else [])
    data["notes"] = notes + [note]
    return json.dumps(data, ensure_ascii=False, indent=2)

def folder_ids(tree: dict[str, Any]) -> list[str]:
    ids: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        children = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        node_id = str(node.get("id") or "").strip()
        if children and node_id:
            ids.append(node_id)
            for child in children:
                walk(child)

    walk(tree)
    return ids

SEED_SENTENCE = re.compile(r"Seed data:([^\n]*)", re.IGNORECASE)
QUOTED = re.compile(r"\"([^\"\n]{1,80})\"|“([^”\n]{1,80})”")

PLATFORM_SEED = re.compile(r"(?:evaluation seed contains|seed values:|seeded data (?:is|includes))(.*?)(?:\.\s|\.$|$)", re.IGNORECASE)

SEED_ENTITY = re.compile(r"\b(?:account|user(?:name)?|workbook|worksheet|organi[sz]ation|repositor(?:y|ies)|issue|branch(?:es)?|"
                         r"pull requests?|label|milestone|team|shelf|shelves|book|chapter)s?\s+(?:named\s+|called\s+)?"
                         r"((?:[`\"“][^`\"”\n]{1,80}[`\"”](?:\s*(?:,|and|,\s*and)\s*)?)+)", re.IGNORECASE)
SEED_VALUE = re.compile(r"[`\"“]([^`\"”\n]{1,80})[`\"”]")

def seed_names(text: str) -> list[str]:
    names: list[str] = []
    for sentence in SEED_SENTENCE.findall(text):
        names += [a or b for a, b in QUOTED.findall(sentence)]
    for sentence in PLATFORM_SEED.findall(" ".join(text.split())):
        names += [value for group in SEED_ENTITY.findall(sentence) for value in SEED_VALUE.findall(group)]
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))

def missing_seed_names(text: str, sources: dict[str, bytes]) -> list[str]:
    backend = "\n".join(content.decode("utf-8", errors="replace").replace("\\", "")
                        for rel, content in sources.items() if rel.startswith("backend/"))
    backend = backend.lower()
    return [name for name in seed_names(text) if name.lower() not in backend]

QUOTED_NAME = re.compile(r"\"([^\"\n]{1,60})\"|“([^”\n]{1,60})”|`([^`\n]{1,60})`")

def quoted_names(text: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None).strip() for m in QUOTED_NAME.finditer(text)]

def batch_unquoted_names(text: str) -> set[str]:
    try:
        from agent.stated_controls import unquoted_statements, uq_normalise
        return {name.strip() for _role, name, _s, _e, _n in unquoted_statements(uq_normalise(text)) if name.strip()}
    except Exception:
        return set()

def stamp_batch_names(work: list[dict[str, Any]]) -> None:

    quoted_per_batch = [set(quoted_names(describe_node(b))) for b in work]
    own_per_batch = [quoted_per_batch[i] | batch_unquoted_names(describe_node(b)) for i, b in enumerate(work)]
    for index, batch in enumerate(work):
        earlier = set().union(*own_per_batch[:index]) if index else set()
        later = set().union(*quoted_per_batch[index + 1:]) if index + 1 < len(work) else set()
        batch["earlier_names"] = sorted(earlier | own_per_batch[index])
        batch["later_names"] = sorted(later - earlier - own_per_batch[index])

def failed_spec_names(playwright_output: str) -> set[str]:
    text = strip_terminal_codes(playwright_output)
    head = re.search(r"^FAILED SPEC FILES: (.+)$", text, re.MULTILINE)
    if head:
        return {name.strip() for name in head.group(1).split(",") if name.strip()}
    lines = [line for line in text.splitlines() if re.match(r"\s*(?:\d+\)|✘|×)", line)]
    summary = re.search(r"^\s*\d+ failed\s*$(.*?)(?=^\s*\d+ (?:flaky|skipped|passed|did not run)|\Z)", text, re.MULTILINE | re.DOTALL)
    if summary:
        lines += [line for line in summary.group(1).splitlines() if re.search(r"test-e2e[\\/][\w.+-]+\.spec\.js:\d+", line)]
    return set(re.findall(r"([\w.+-]+\.spec\.js)", "\n".join(lines)))

OUTPUT_CUT_MARK = "[middle of the output omitted]"
FAILED_TEST_LINE = re.compile(r"([\w.+-]+\.spec\.js):\d+:\d+\s+›\s+(.+)$")

def failed_test_titles(playwright_output: str) -> dict[str, set[str]]:
    text = strip_terminal_codes(playwright_output)
    if OUTPUT_CUT_MARK in text:
        return {}
    lines = [line for line in text.splitlines() if re.match(r"\s*(?:\d+\)|✘|×)\s", line)]
    for summary in re.finditer(r"^\s*\d+ failed\s*$(.*?)(?=^\s*\d+ (?:flaky|skipped|passed|did not run)|^## |\Z)", text, re.MULTILINE | re.DOTALL):
        lines += summary.group(1).splitlines()
    titles: dict[str, set[str]] = {}
    for line in lines:
        match = FAILED_TEST_LINE.search(line.rstrip().rstrip("─").rstrip())
        if match:
            title = re.sub(r"\s*\(\d+(?:\.\d+)?m?s\)$", "", match.group(2)).strip()
            if title:
                titles.setdefault(match.group(1), set()).add(title)
    return titles

ERROR_LINE = re.compile(r"\b\w*Error:\s")
FAILURE_HEAD = re.compile(r"^\s*\d+\)\s")
BLOCK_END = re.compile(r"^\s*(?:\d+\)\s|\d+ (?:failed|flaky|skipped|passed|did not run)\s*$|\[\d+/\d+\]|## |FAILED SPEC FILES: )")

NOTSEE_ERROR = re.compile(r"Error: The requirement says (.+) is no longer shown, but it is still visible\.")
NOTSEE_SIGNATURE_HEAD, NOTSEE_SIGNATURE_TAIL = "Error: The requirement says ", " is no longer shown, but it is still visible."

SEED_CONFLICT_PROBLEM = re.compile(r"error|exception|timeout|timed out|sqlite|constraint|failed to load|console\.|page error|server|ECONN|refused|crash", re.IGNORECASE)

PLAYWRIGHT_STRUCTURE_LINE = re.compile(
    r"\s*(?:Error Context: \S+"
    r"|\d+ failed"
    r"|(?:\d+\)\s+|\[\d+/\d+\]\s+|[✘×]\s+)?(?:\[[\w-]+\]\s+›\s+)?(?:test-e2e[\\/])?[\w.+-]+\.spec\.js:\d+:\d+\s+›\s+[^\n]+?(?:\s+[─]+)?"
    r"|at (?:[\w$.<>\[\] ]+ \()?[^()\s][^()]*:\d+:\d+\)?"

    r"|>?\s*\d+\s*\|\s*throw new Error\(`\$\{what\}[^\n]*)\s*")
PLAYWRIGHT_FORMAT_LINE = re.compile(
    r"\s*(?:Error Context: \S+"
    r"|\d+ failed"
    r"|(?:\d+\)\s+|\[\d+/\d+\]\s+|[✘×]\s+)?(?:\[[\w-]+\]\s+›\s+)?(?:test-e2e[\\/])?[\w.+-]+\.spec\.js:\d+:\d+\s+›\s+[^\n]+?(?:\s+[─]+)?"
    r"|at (?:[\w$.<>\[\] ]+ \()?[^()\s][^()]*:\d+:\d+\)?"
    r"|>?\s*\d+\s*\|[^\n]*"
    r"|\|?\s*\^+"
    r"|[─-]+"
    r"|attachment #\d+: [\w ]+\([\w/.+-]+\)(?:\s+[─]+)?"
    r"|test-results[\\/]\S+"
    r"|Usage:"
    r"|npx playwright show-trace \S+"
    r"|Retry #\d+(?:\s+[─]+)?"
    r"|(?:url|on the page now -> clickable|fields|headings): [^\n]*"
    r"|messages: \[\])\s*")

def seed_conflict_output_clean(text: str) -> bool:
    for line in strip_terminal_codes(text).splitlines():
        if not SEED_CONFLICT_PROBLEM.search(line):
            continue
        found = ERROR_LINE.search(line)
        if found and line[:found.start()].strip() == "" and NOTSEE_ERROR.fullmatch(line[found.start():].strip()):
            continue
        if PLAYWRIGHT_STRUCTURE_LINE.fullmatch(line):
            continue
        return False

    lines = strip_terminal_codes(text).splitlines()
    index = 0
    while index < len(lines):
        head = FAILURE_HEAD.match(lines[index])
        index += 1
        if not head:
            continue
        while index < len(lines) and not BLOCK_END.match(lines[index]):
            line = lines[index]
            index += 1
            if not line.strip() or PLAYWRIGHT_FORMAT_LINE.fullmatch(line):
                continue
            found = ERROR_LINE.search(line)
            if found and line[:found.start()].strip() == "" and NOTSEE_ERROR.fullmatch(line[found.start():].strip()):
                continue
            return False
    return True

def notsee_values_by_test(text: str) -> dict[tuple[str, str], list[str]]:
    lines = strip_terminal_codes(text).splitlines()
    found: dict[tuple[str, str], list[str]] = {}
    index = 0
    while index < len(lines):
        match = FAILED_TEST_LINE.search(lines[index].rstrip().rstrip("─").rstrip()) if FAILURE_HEAD.match(lines[index]) else None
        index += 1
        if not match:
            continue
        key = (match.group(1), re.sub(r"\s*\(\d+(?:\.\d+)?m?s\)$", "", match.group(2)).strip())
        while index < len(lines) and not BLOCK_END.match(lines[index]):
            error = ERROR_LINE.search(lines[index])
            notsee = NOTSEE_ERROR.fullmatch(lines[index][error.start():].strip()) if error else None
            if notsee:
                found.setdefault(key, []).append(notsee.group(1))
            index += 1
    return found

def error_signature(line: str) -> str:
    text = " ".join(strip_terminal_codes(line).split())
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s'\"`)]+", "<url>", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:localhost|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?", "<host>", text)

    kept: list[str] = []

    def quoted(match: re.Match[str]) -> str:
        value = match.group(2)
        if GENERATED_VALUE.search(value):
            return match.group(1) + "<v>" + match.group(1)
        kept.append(match.group(0))
        return "\0" + "".join(chr(ord("a") + int(d)) for d in str(len(kept) - 1)) + "\0"
    text = re.sub(r"([\"'`])((?:(?!\1)[^\n])*?)\1", quoted, text)

    text = GENERATED_TOKEN.sub(lambda m: m.group(1) + "<g>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", "<id>", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|s|m|min)\b", "<t>", text)
    text = re.sub(r"\d+", "<n>", text)
    return re.sub(r"\0([a-j]+)\0", lambda m: kept[int("".join(str(ord(c) - ord("a")) for c in m.group(1)))], text)

GENERATED_VALUE = re.compile(r"\d{4,}|[0-9a-f]{8}-[0-9a-f]{4}-|(?=[0-9a-f]*\d)[0-9a-f]{8,}|\b\d{1,2}:\d{2}\b", re.IGNORECASE)

GENERATED_TOKEN = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z][A-Za-z0-9]*)*[-_.])\d{10,}(?:[-_.][A-Za-z0-9]{1,12})?\b")

def spec_location(name: str, block: list[str]) -> str:
    pattern = re.compile(r"(?:^|[\s\\/(])" + re.escape(name) + r":(\d+):(\d+)\b")
    for line in block:
        match = pattern.search(line)
        if match:
            return f"{name}:{match.group(1)}:{match.group(2)}"
    return ""

def failed_test_signatures(playwright_output: str) -> dict[str, dict[str, str]]:
    titles = failed_test_titles(playwright_output)
    if not titles:
        return {}
    lines = strip_terminal_codes(playwright_output).splitlines()
    first: dict[tuple[str, str], str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        match = FAILED_TEST_LINE.search(line.rstrip().rstrip("─").rstrip()) if FAILURE_HEAD.match(line) else None
        index += 1
        if not match:
            continue
        key = (match.group(1), re.sub(r"\s*\(\d+(?:\.\d+)?m?s\)$", "", match.group(2)).strip())
        error = locator = ""
        start = index
        while index < len(lines) and not BLOCK_END.match(lines[index]):
            if not error and ERROR_LINE.search(lines[index]):
                error = error_signature(lines[index][ERROR_LINE.search(lines[index]).start():])
            elif error and not locator and lines[index].strip().startswith("Locator:"):
                locator = error_signature(lines[index])
            index += 1
        where = spec_location(key[0], lines[start:index])
        if error and key not in first:
            first[key] = error + (" | " + locator if locator else "") + (" @ " + where if where else "")
    return {name: {title: first.get((name, title), "") for title in found} for name, found in titles.items()}

EVIDENCE_CAP = 3500
EVIDENCE_FILE_CAP = 1400
EVIDENCE_LINE_CAP = 200
LOCK_FILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json")

SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der", ".jks", ".keystore")

def evidence_visibility(rel: str) -> str:
    from agent.codegen import CodegenImplementer

    path = Path(rel)
    if any(part.startswith(".") for part in path.parts) or path.name.lower().endswith(SENSITIVE_SUFFIXES):
        return "hidden"
    if path.name in LOCK_FILES or not CodegenImplementer._writable(rel):
        return "named"
    return "shown"

def first_error_lines(output: str, limit: int = 6, width: int = EVIDENCE_LINE_CAP) -> list[str]:
    lines = strip_terminal_codes(str(output or "")).splitlines()
    found: list[str] = []
    index = 0
    while index < len(lines) and len(found) < limit:
        line = lines[index]
        match = FAILED_TEST_LINE.search(line.rstrip().rstrip("─").rstrip()) if FAILURE_HEAD.match(line) else None
        index += 1
        if not match:
            continue
        title = re.sub(r"\s*\(\d+(?:\.\d+)?m?s\)$", "", match.group(2)).strip()
        while index < len(lines) and not BLOCK_END.match(lines[index]):
            hit = ERROR_LINE.search(lines[index])
            if hit:
                item = f"{match.group(1)} › {title}: {lines[index][hit.start():].strip()}"
                if item not in found:
                    found.append(item[:width])
                break
            index += 1
    if found:
        return found
    errors = list(dict.fromkeys(l[ERROR_LINE.search(l).start():].strip()[:width] for l in lines if ERROR_LINE.search(l)))
    if errors:
        return errors[:limit]
    return [l.strip()[:width] for l in lines if l.strip()][:min(limit, 3)]

def digest_order(reference: dict[str, bytes], now: dict[str, bytes], first: list[str] | tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    changed = sorted(rel for rel in set(reference) | set(now) if reference.get(rel) != now.get(rel))
    hidden = [rel for rel in changed if evidence_visibility(rel) == "hidden"]
    changed = [rel for rel in changed if rel not in hidden]
    front = [rel for rel in first if rel in changed]
    order = front + [rel for rel in changed if rel not in front and rel in reference and rel in now] \
        + [rel for rel in changed if rel not in front and rel not in reference] \
        + [rel for rel in changed if rel not in front and rel not in now]
    return order, hidden

def change_digest(reference: dict[str, bytes], now: dict[str, bytes], first: list[str] | tuple[str, ...] = (),
                  cap: int = EVIDENCE_CAP, per_file: int = EVIDENCE_FILE_CAP) -> tuple[list[str], str]:
    import difflib

    order, hidden = digest_order(reference, now, first)
    changed = sorted(order)
    if not changed:
        return [], (f"({len(hidden)} hidden or key file(s) also changed; not shown)" if hidden else "")
    parts: list[str] = []
    used = 0
    left_out: list[str] = []
    for rel in order:
        if used >= cap:
            left_out.append(rel)
            continue
        if rel in reference and rel in now and Path(rel).name in LOCK_FILES:
            block = f"--- {rel} (a lock file: diff not shown)"
        elif evidence_visibility(rel) == "named":
            block = f"--- {rel} (" + ("new; " if rel not in reference else "deleted; " if rel not in now else "") + "content not shown)"
        elif rel not in reference:
            text = (now.get(rel) or b"").decode("utf-8", "replace")
            block = f"+++ {rel} (new file, {len(text.splitlines())} lines)"
        elif rel not in now:
            block = f"--- {rel} (deleted)"
        else:
            old = (reference.get(rel) or b"").decode("utf-8", "replace").splitlines()
            new = (now.get(rel) or b"").decode("utf-8", "replace").splitlines()
            body = [l[:EVIDENCE_LINE_CAP] for l in difflib.unified_diff(old, new, n=1, lineterm="")][2:]
            room = min(per_file, cap - used)
            if len("\n".join(body)) > room:
                minus = [l for l in body if l.startswith("-")]
                plus = [l for l in body if l.startswith("+")]
                kept: list[str] = []
                size = 0
                for l in minus + plus:
                    if size + len(l) + 1 > room:
                        break
                    kept.append(l)
                    size += len(l) + 1
                rest = len(minus) + len(plus) - len(kept)
                body = kept + ([f"... ({rest} more changed line(s) not shown)"] if rest else [])
            block = f"--- {rel}\n" + "\n".join(body)
        parts.append(block)
        used += len(block) + 1
    if left_out:
        parts.append("(also changed, not shown: " + ", ".join(left_out[:20]) + (" ..." if len(left_out) > 20 else "") + ")")
    if hidden:
        parts.append(f"({len(hidden)} hidden or key file(s) also changed; not shown)")
    return changed, "\n".join(parts)

REJECTED_CLAIM_LOG = (
    (re.compile(r"a test defect without verifiable quotes is not accepted: (.*)$", re.DOTALL),
     "no verifiable quote: the claim did not quote the requirement sentence and the spec step it contradicts, nor an "
     "error thrown by the spec itself"),
    (re.compile(r"test defect.*not accepted[^:]*: (.*)$", re.DOTALL | re.IGNORECASE), "not accepted by the evidence gate"),
    (re.compile(r"the answer only rewrote tests (\[.*?\])", re.DOTALL),
     "the answer rewrote test files instead of stating the defect with quotes (tests are frozen)"),
)

def defect_whitelist(result: Any) -> set[str] | None:
    if str(getattr(result, "defect_kind", "") or "") != "api":
        return None
    return {Path(str(p)).as_posix() for p in (getattr(result, "defect_files", None) or []) if str(p)}

def rejected_claim_of(line: str) -> tuple[str, str] | None:
    for pattern, reason in REJECTED_CLAIM_LOG:
        match = pattern.search(str(line or ""))
        if match:
            claim = match.group(1).strip()
            if len(claim) >= 2 and claim[0] == claim[-1] and claim[0] in "'\"":
                claim = claim[1:-1]
            return claim[:300], reason
    return None

def not_run_spec_names(output: str) -> set[str]:
    unrun = re.search(r"(?m)^NOT RUN SPEC FILES: (.+)$", str(output or ""))
    return {name.strip() for name in unrun.group(1).split(",") if name.strip()} if unrun else set()

PARTLY_RUN_NOTE = re.compile(r"(?m)^\s*\d+ test\(s\) of (\S+?) not run: ")

def partly_run_spec_names(output: str) -> set[str]:
    return set(PARTLY_RUN_NOTE.findall(str(output or "")))

def unrun_kw(result: Any, function: Any) -> dict[str, list]:
    import inspect

    tests = getattr(result, "not_run_tests", None)
    if not tests:
        return {}
    try:
        params = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return {}
    if any(p.name == "not_run_tests" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return {"not_run_tests": list(tests)}
    return {}

def takes_until(function: Any) -> bool:
    import inspect

    try:
        params = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "until" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)

def until_kw(end: Any, function: Any = None) -> dict[str, float]:
    if end is None or not takes_until(run_e2e if function is None else function):
        return {}
    return {"until": float(end)}

def stopped_unrun(result: Any) -> bool:
    if getattr(result, "ok", False) or getattr(result, "skipped", False):
        return False
    output = str(getattr(result, "output", "") or "")
    return bool(not_run_spec_names(output)) and not failed_spec_names(output)

CROSSDEP_FAILURE_HEAD = re.compile(r"^\s*\d+\)\s")
CROSSDEP_COUNT_LINE = re.compile(r"^\s*\d+ (?:failed|passed|flaky|skipped|did not run|interrupted)\b")
CROSSDEP_SECTION = re.compile(r"^## (\S+) (line \d+(?:, line \d+)*)( \.\.\.)?: alone")

def crossdep_settings_on() -> bool:
    return setting_on("v162b_crossdep", True) or setting_on("v162b_dependency_probe", True)

def drop_red_tests(output: str, verdicts: list[dict]) -> str:
    marks = {(str(v.get("name") or ""), int(v.get("line") or 0)) for v in verdicts}
    located = [re.compile(re.escape(name) + ":" + str(line) + r":\d+\b") for name, line in marks if name and line]

    def names_one(line: str) -> bool:
        return any(pattern.search(line) for pattern in located)

    lines = str(output or "").split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        section = CROSSDEP_SECTION.match(line)
        if section and not section.group(3):
            numbers = [int(n) for n in re.findall(r"\d+", section.group(2))]
            if numbers and all((section.group(1), n) in marks for n in numbers):
                end = next((j for j in range(index + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
                counts = [j for j in range(index + 1, end) if re.match(r"^\s*\d+ failed\s*$", lines[j])]

                index = counts[-1] if (counts and end == len(lines)) else end
                continue
        if CROSSDEP_FAILURE_HEAD.match(line) and names_one(line):
            index += 1
            while index < len(lines) and not (CROSSDEP_FAILURE_HEAD.match(lines[index]) or CROSSDEP_COUNT_LINE.match(lines[index])
                                              or lines[index].startswith("## ")):
                index += 1
            continue
        if not names_one(line):
            kept.append(line)
        index += 1
    return "\n".join(kept)

def crossdep_note(verdicts: list[dict], routed: bool) -> str:
    tests = "; ".join(f'"{v.get("title")}" ({v.get("name")} line {v.get("line")})' for v in verdicts[:6])
    return ("\n\nNOT AN APPLICATION FAILURE (left out above): " + tests + (" fails" if len(verdicts) == 1 else " fail")
            + " only because the test relies on data ANOTHER test of its file created; every test runs alone on a fresh "
            "server with fresh data. That is a defect of the acceptance spec and goes to the test author"
            + (" now" if routed else "") + ". Do not change the application or its seed data for it: every stated record "
            "keeps exactly the name the requirement gives it.")

def crossdep_problem(verdicts: list[dict], output: str) -> str:
    rows = []
    for v in verdicts[:6]:
        message = " ".join(str(v.get("message") or "").split())
        rows.append(f'SPEC: {v.get("name")} line {v.get("line")}, test "{v.get("title")}" ({v.get("kind")} check): {message}')
    return ("\n".join(rows) + "\nEach test runs ALONE on a fresh server with fresh data. Rewrite only the test(s) named "
            "above so that each starts from its scenario's own GIVEN state (the records the requirement states) or creates "
            "/ types what it checks inside itself; change nothing else.\nLatest check output:\n" + str(output or "")[-2500:])

def run_unfinished(result: Any) -> bool:
    if getattr(result, "skipped", False):
        return True
    if not getattr(result, "ok", False):
        return False
    return re.search(r"(?m)^NOT RUN SPEC FILES: ", str(getattr(result, "output", "") or "")) is not None or getattr(result, "ran", -1) == 0

def sources_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha1()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8") + b"\0" + files[rel] + b"\0")
    return digest.hexdigest()

def check_failure_ids(evidence: Any) -> set[tuple[str, str]] | None:
    if not isinstance(evidence, dict) or evidence.get("large") or not evidence.get("specs"):
        return None
    output = strip_terminal_codes(str(evidence.get("output") or ""))
    if OUTPUT_CUT_MARK in output or re.search(r"(?m)^NOT RUN SPEC FILES: ", output) or partly_run_spec_names(output):
        return None
    if evidence.get("passed"):
        return set() if output.startswith("E2E PASSED\n") else None
    if not output.startswith("E2E FAILED against the real application\n") or "NOT AN APPLICATION FAILURE (left out above)" in output:
        return None
    titles = failed_test_titles(output)
    files = failed_spec_names(output)
    if not files or set(titles) != files:
        return None

    last_count: dict[str, int] = {}
    current = ""
    for line in output.splitlines():
        head = re.match(r"^## ([\w.+-]+\.spec\.js)\b", line)
        if head:
            current = head.group(1)
            continue
        count = re.match(r"^\s*(\d+) failed\s*$", line)
        if count and current:
            last_count[current] = int(count.group(1))
    if any(last_count.get(name) != len(group) for name, group in titles.items()):
        return None
    return {(name, title) for name, group in titles.items() for title in group}

def spec_skipped_counts(output: str) -> dict[str, int] | None:
    counts: dict[str, dict[str, int]] = {}
    current = ""
    for line in strip_terminal_codes(str(output or "")).splitlines():
        head = re.match(r"^## ([\w.+-]+\.spec\.js)\b", line)
        if head:
            current = head.group(1)
            continue
        found = re.match(r"^\s*(\S+) (skipped|did not run|interrupted)\s*$", line)
        if found:
            if not found.group(1).isdigit():
                return None
            counts.setdefault(current, {})[found.group(2)] = int(found.group(1))
    return {name: sum(kinds.values()) for name, kinds in counts.items()}

def partial_repair_keepable(before: Any, after: Any, before_digest: str, now_digest: str) -> tuple[bool, str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False, "no check evidence"
    if before.get("digest") != before_digest or after.get("digest") != now_digest:
        return False, "the checks are not about the states compared"
    if list(before.get("specs") or []) != list(after.get("specs") or []):
        return False, "the checks ran different specs"
    if str(after.get("lint") or "") not in ("", str(before.get("lint") or "")):
        return False, "the lint changed"

    ran_before, ran_after = before.get("ran"), after.get("ran")
    if (not isinstance(ran_before, int) or not isinstance(ran_after, int) or isinstance(ran_before, bool)
            or isinstance(ran_after, bool) or ran_before <= 0 or ran_after <= 0):
        return False, "the test counts of the checks are not known"
    if ran_after < ran_before:
        return False, f"fewer tests ran ({ran_after} after, {ran_before} before)"
    skipped_before = spec_skipped_counts(str(before.get("output") or ""))
    skipped_after = spec_skipped_counts(str(after.get("output") or ""))
    if skipped_before is None or skipped_after is None:
        return False, "the skipped tests of a check cannot be read"
    more = sorted(name for name, n in skipped_after.items() if n > skipped_before.get(name, 0))
    if more:
        return False, "more tests skipped / not run in: " + ", ".join(more[:4])
    old, new = check_failure_ids(before), check_failure_ids(after)
    if old is None or new is None:
        return False, "a check does not name every failed test"
    if not new <= old:
        return False, "new failure(s): " + ", ".join(f"{n} > {t}" for n, t in sorted(new - old)[:4])
    if new == old:
        return False, "no failure is gone"
    return True, f"{len(old) - len(new)} failure(s) gone, none new ({len(new)} left)"

class _EndBoundLLM:

    def __init__(self, llm: Any, end: float):
        self._llm, self._end = llm, float(end)

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        from agent import llm as llm_module
        kwargs.setdefault("end", llm_module._clock() + (self._end - clock.now()))
        return self._llm.chat(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)

class _CallsEndBy:

    def __init__(self, factory: Any, end: float | None):
        self.factory, self.end, self.saved = factory, end, None

    def __enter__(self) -> "_CallsEndBy":
        llm = self.factory.__dict__.get("llm")
        if self.end is None or llm is None:
            return self
        try:
            import inspect
            if "end" not in inspect.signature(llm.chat).parameters:
                log("[audit] the model client takes no call end; the test author's calls keep their own limits")
                return self
        except (TypeError, ValueError):
            return self
        author = self.factory.__dict__.get("author")
        self.saved = (llm, author, getattr(author, "llm", None))
        bound = _EndBoundLLM(llm, float(self.end))
        self.factory.llm = bound
        if author is not None and getattr(author, "llm", None) is llm:
            author.llm = bound
        return self

    def __exit__(self, *exc: Any) -> bool:
        if self.saved is not None:
            llm, author, author_llm = self.saved
            self.factory.llm = llm
            if author is not None and author_llm is not None:
                author.llm = author_llm
        return False

TEST_PATH =re.compile(r"(?:^|/)(?:tests?|test-e2e|e2e-support|__tests__)/|\.(?:test|spec)\.[cm]?[jt]sx?$", re.IGNORECASE)
CREDENTIAL_BEFORE = re.compile(r"(?:password|passcode)\W*(?:\w+\W+){0,2}$", re.IGNORECASE)
NOT_A_SEED_VALUE = re.compile(r"^=|^[A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+|=)")

STATED_NAME = re.compile(r"[“\"]([^“”\"\n<>]{3,60})[”\"]")

def app_text(files: dict[str, bytes]) -> str:
    return "\n".join(content.decode("utf-8", errors="replace").replace("\\", "")
                     for rel, content in files.items()
                     if rel.startswith(("backend/", "frontend/")) and not TEST_PATH.search(rel))

def lost_stated_names(requirement: str, before: dict[str, bytes], after: dict[str, bytes], used_by: str) -> list[str]:

    def literal(name: str) -> re.Pattern[str]:
        return re.compile(r"(?:[\"'`>]|^)[ \t]*\n?[ \t]*" + re.escape(name) + r"[ \t]*\n?[ \t]*(?:[\"'`<]|$)", re.MULTILINE)
    names = dict.fromkeys(match.group(1).strip() for match in STATED_NAME.finditer(requirement))
    old, new = app_text(before), app_text(after)
    lost = []
    for name in names:
        if len(name) < 3 or name not in used_by or name not in old:
            continue
        pattern = literal(name)
        if pattern.search(used_by) and pattern.search(old) and not pattern.search(new):
            lost.append(name)
    return lost

def lost_seed_values(seeds: str, before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    from agent.seed_values import lost_values
    return lost_values(seeds, before, after)

def backend_sources(project_dir: Path, limit: int = 40000) -> str:
    root = project_dir / "backend" / "src"
    order = sorted(root.glob("routes/**/*.js")) + [root / "app.js", root / "database" / "init_db.js"]
    parts, used = [], 0
    for path in order:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        block = f"=== {path.relative_to(project_dir).as_posix()} ===\n{text}\n"
        if used + len(block) > limit:
            parts.append(f"=== {path.relative_to(project_dir).as_posix()} === (not shown: over the limit)\n")
            continue
        parts.append(block)
        used += len(block)
    return "".join(parts)

def safe_prefix(req_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", req_id).strip("-") or "REQ"

def test_kind(path: str) -> str:
    if path.startswith("backend/test-e2e/"):
        return "E2E"
    if path.startswith("backend/tests/"):
        return "INTEGRATION"
    return "UNIT"

class PutBack(int):

    complete = True
    INCOMPLETE = " [INCOMPLETE: the tree still differs from the snapshot]"

    def __new__(cls, changed: int, complete: bool = True) -> "PutBack":
        value = super().__new__(cls, changed)
        value.complete = bool(complete)
        return value

    def __str__(self) -> str:
        return int.__repr__(self) + ("" if self.complete else self.INCOMPLETE)

def lifecycle_new_problems(others: list[dict[str, Any]], others0: list[dict[str, Any]], report: dict[str, Any],
                           dups_base: dict[str, Any] | None = None) -> list[str]:
    def dup(f: dict[str, Any]) -> bool:
        return str(f.get("check") or "").endswith("/duplicate_fixture")

    known = {str(o.get("check")) for o in others0 if not dup(o)}
    out = [f"{f.get('check')}: {str(f.get('problem') or '')[:400]}" for f in others
           if not dup(f) and str(f.get("check")) not in known]
    base = {str(n): _count(c) for n, c in dict(dups_base or {}).items()}
    now = {str(n): _count(c) for n, c in dict((report or {}).get("duplicate_counts") or {}).items()}
    worse = [(k, c, base.get(k, 1)) for k, c in now.items() if c > base.get(k, 1)]
    if worse:

        log("[seed-foundation]   stated records now counted more often than before (advisory): "
            + "; ".join(_count_label(k, c, b) for k, c, b in worse[:20]))
    return out

def _count_label(key: str, rows: int, was: int) -> str:
    parts = str(key).split("|", 3)
    if len(parts) == 4:
        return f"`{parts[3]}` ({parts[1]}.{parts[2]}: {rows} rows, was {was})"
    return f"`{key}` ({rows} rows, was {was})"

def _count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 2

class Factory:
    def __init__(self, requirements_dir: Path, output_dir: Path) -> None:
        self.requirements_dir = requirements_dir
        self.project_dir = output_dir
        self.native_validation = False
        self.started = clock.now()
        self.hard_end_at: float | None = platform_hard_end(PROCESS_STARTED)
        self.hard_end_mono: float | None = platform_hard_end(MONO_STARTED)
        self.llm: LLM = llm_from_env(output_dir / ".arc" / "llm-usage.jsonl", log=log)
        self.trace = Trace(output_dir, log=log)
        self.author = TestAuthor(self.llm, log=log)
        self.report: dict[str, Any] = {"nodes": {}, "verification": {}}
        self.all_test_files: list[str] = []
        self.e2e_specs: list[str] = []
        self.foundation_checks: dict[str, int] = {}
        self.foundation_baselines: dict[str, dict[str, set[str]] | None] = {}
        self.regression_debts: list[str] = []
        self.install_failed = False
        self.revised_specs: set[str] = set()

        self.spec_defects: dict[str, dict[str, Any]] = {}
        self.spec_context: dict[str, dict[str, Any]] = {}
        self.green_specs: list[str] = []
        self.file_specs: dict[str, list[str]] = {}
        self.contract: str = ""
        self.resumed_description: str = ""
        self.spent_before: float = 0.0
        self.resumed_sources: dict[str, bytes] = {}
        self.seed_checks: dict[str, int] = {}
        self.test_snapshot: dict[str, str] = {}
        self.test_ids_by_file: dict[str, list[str]] = {}
        self.test_titles: dict[str, str] = {}
        self.time_budget: float = float(TIME_BUDGET_SEC)
        self.large_tree = False
        self.current_trace_id = ""
        self.current_members: list[str] = []
        self.failed_members: list[str] = []
        self.no_evidence_members: list[str] = []
        self.unattempted_members: list[str] = []
        self.restore_failures = 0
        self.verify_repairs = 0
        self.audit_sent_fingerprints: list[str] = []
        self.audit_rolled_back_fingerprints: list[str] = []
        self.description_pass: dict[str, Any] = {}
        self.description_specs: list[str] = []
        self.foundation_retried: list[str] = []
        self.hub_retried: list[str] = []
        self.partial_green: dict[str, dict[str, Any]] = {}
        self.leaf_dependents: dict[str, int] = {}
        self.llm_tokens_before: int = 0

        self.continuation: dict[str, Any] | None = None
        self.requirement_sha: str = ""

        self.open_findings: dict[str, dict[str, str]] = {}
        self.batch_members: dict[str, list[str]] = {}
        self.handoff_tree: dict[str, Any] | None = None

    CHECKPOINT_FIELDS = ("all_test_files", "e2e_specs", "regression_debts", "spec_context", "green_specs",
                         "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "failed_members",
                         "no_evidence_members", "unattempted_members", "contract", "report", "seed_checks",
                         "audit_sent_fingerprints", "audit_rolled_back_fingerprints", "description_pass", "description_specs")

    AUDIT_ROLLBACKS_MAX = 2

    GATEWAY_REQUEUES = 2
    GATEWAY_PAUSE_SEC = 60.0

    FOUNDATION_BATCHES = 3

    def hub_leaf(self, node: dict[str, Any]) -> tuple[str, int] | None:
        counts = getattr(self, "leaf_dependents", None) or {}
        members = [str(m) for m in (node.get("members") or [node.get("id")])]
        best = max(((m, int(counts.get(m, 0) or 0)) for m in members), key=lambda pair: pair[1], default=None)
        return best if best is not None and best[1] >= hub_min_dependents() else None

    def index_leaves(self, leaves: list[dict[str, Any]]) -> None:
        try:
            dependents = transitive_dependents(leaves)
            self.leaf_dependents = {leaf: len(others) for leaf, others in dependents.items()}
        except Exception as exc:
            log(f"[factory] leaf dependents not counted: {type(exc).__name__}: {exc}")
            self.leaf_dependents = {}
            dependents = {}
        prerequisites: dict[str, set[str]] = {}
        for base, others in dependents.items():
            for other in others:
                prerequisites.setdefault(other, set()).add(base)
        self.leaf_prerequisites = {leaf: sorted(bases) for leaf, bases in prerequisites.items()}
        self.leaf_names = {str(leaf.get("id") or "").strip(): " ".join(str(leaf.get("name") or "").split())
                           for leaf in leaves if isinstance(leaf, dict)}

    PREREQUISITES_NAMED = 3
    PREREQUISITE_FILES_NAMED = 8
    PREREQUISITE_NOTE_CAP = 1200

    def undelivered_prerequisites(self, node: dict[str, Any]) -> list[str]:
        prerequisites = getattr(self, "leaf_prerequisites", None) or {}
        members = [str(m) for m in (node.get("members") or [node.get("id")])]
        wanted = {str(d) for m in members for d in (prerequisites.get(m) or []) if str(d) not in members}
        if not wanted:
            return []
        failed = {str(m) for m in (getattr(self, "failed_members", None) or [])}
        missing: list[str] = []
        for entry in ((getattr(self, "report", None) or {}).get("nodes") or {}).values():
            if not isinstance(entry, dict) or entry.get("status") == "passed" or list(entry.get("written") or []):
                continue
            for leaf in entry.get("put_back_members") or []:
                if str(leaf) in wanted and str(leaf) in failed and str(leaf) not in missing:
                    missing.append(str(leaf))
        return missing

    def prerequisite_note(self, node: dict[str, Any]) -> str:
        try:
            missing = self.undelivered_prerequisites(node)
            if not missing:
                return ""
            names = getattr(self, "leaf_names", None) or {}
            one = len(missing) == 1
            foundation = list(getattr(self, "green_specs", None) or [])[:3]
            files = sorted(rel for rel, specs in (getattr(self, "file_specs", None) or {}).items()
                           if any(spec in foundation for spec in specs or []))

            def build(named: int, listed: int) -> str:
                shown = [f"{leaf} ({names[leaf][:80]})" if names.get(leaf) else leaf for leaf in missing[:named]]
                more = len(missing) - len(shown)
                text = (f"PREREQUISITE{'' if one else 'S'} NOT DELIVERED: this batch's requirement depends on "
                        + ", ".join(shown) + (f" and {more} more" if more > 0 else "") + ". "
                        + ("The batch that implemented it ended red and was put back to the state before it; none of its changes "
                           "were kept. " if one else "The batches that implemented them ended red and were put back to the state "
                           "before them; none of their changes were kept. ")
                        + "Do not assume what " + ("it describes" if one else "they describe") + " exists in the sources: look. "
                        "Build now, minimally, only the parts this batch's tests need and the sources lack")
                if files:
                    text += (", without changing what the foundation specs (those of the first requirements) rely on in the files "
                             "their batches wrote" + (" (" + ", ".join(files[:listed]) + (", ..." if len(files) > listed else "") + ")"
                                                      if listed else "")
                             + ": add to those files; do not rewrite or remove what is there.")
                else:
                    text += ", without changing what the specs of earlier requirements rely on."
                return text

            text = ""
            for named in range(min(len(missing), self.PREREQUISITES_NAMED), 0, -1):
                for listed in range(min(len(files), self.PREREQUISITE_FILES_NAMED), -1, -1):
                    text = build(named, listed)
                    if len(text) <= self.PREREQUISITE_NOTE_CAP:
                        return text
            return text
        except Exception as exc:
            log(f"[tdd] prerequisite note not built: {exc!r}")
            return ""

    @staticmethod
    def with_prerequisite_note(note: str, prerequisite: str) -> str:
        if not prerequisite:
            return note
        if not note:
            return prerequisite
        from agent.codegen import ATTEMPT_NOTE_CHARS

        room = max(0, ATTEMPT_NOTE_CHARS - len(prerequisite) - 2)
        return note[:room].rstrip() + "\n\n" + prerequisite

    def round_caps(self, node: dict[str, Any], test_files: dict[str, str]) -> dict[str, int]:
        from agent.codegen import count_spec_tests, round_caps

        if not setting_on("round_bonus"):
            return {}
        try:
            counts = getattr(self, "leaf_dependents", None) or {}
            members = [str(m) for m in (node.get("members") or [node.get("id")])]
            dependents = max((int(counts.get(m, 0) or 0) for m in members), default=0)
            tests = count_spec_tests(test_files or {})
            max_rounds, extra_rounds, points = round_caps(dependents, tests, hub_min_dependents())
        except Exception as exc:
            log(f"[codegen] {node.get('id')}: round caps not computed ({exc!r}); the defaults stand")
            return {}
        if not points:
            return {}
        log(f"[codegen] {node.get('id')}: round cap {max_rounds} + {extra_rounds} extra (dependents {dependents}, tests {tests})")
        return {"max_rounds": max_rounds, "extra_rounds": extra_rounds}

    def unit_round_options(self, nodes_left: int) -> dict[str, Any]:
        from agent.codegen import PROGRESS_ROUNDS_MAX, START_FAILURE_FREE

        options: dict[str, Any] = {}
        if setting_on("progress_rounds"):
            options.update(progress_rounds=PROGRESS_ROUNDS_MAX, round_room=self.progress_round_room(nodes_left))
        if setting_on("start_failure_free"):
            options["start_failure_free"] = START_FAILURE_FREE
        return options

    def progress_round_room(self, nodes_left: int):
        def room() -> str:
            if LLM.quota_exhausted:
                return "the key is out of balance"
            if self.over_budget():
                return "the run's batch budget is used up"
            share = self.time_left() * 0.8 / max(1, nodes_left)
            if share < MIN_BATCH_SEC:
                return f"the run is short of time (a fair share of what is left is {int(share)} s, under {int(MIN_BATCH_SEC)} s)"
            return ""
        return room

    def retry_kind(self, node: dict[str, Any], foundation_ids: set[str]) -> str:
        if str(node.get("id")) in foundation_ids and setting_on("foundation_retry"):
            return "foundation"
        if self.hub_leaf(node) is not None and setting_on("hub_retry"):
            return "hub"
        return ""

    def foundation_retry_due(self, node: dict[str, Any], foundation_ids: set[str], batches_left: int, fresh: bool) -> bool:
        req_id = str(node.get("id"))
        retried = list(self.__dict__.setdefault("foundation_retried", [])) + list(self.__dict__.setdefault("hub_retried", []))
        kind = self.retry_kind(node, foundation_ids)
        if not kind or req_id in retried or not fresh:
            return False
        tests = [p for p in ((getattr(self, "report", None) or {}).get("nodes", {}).get(req_id) or {}).get("tests") or []
                 if str(p).startswith("backend/test-e2e/")]
        if not tests or LLM.quota_exhausted or self.over_budget():
            return False
        need = MIN_BATCH_SEC * (batches_left + 2)
        if self.time_left() < need:
            log(f"[factory] {req_id}: no {kind} retry -- {int(max(0.0, self.time_left()))} s left, {int(need)} s needed")
            return False
        return True

    def attempt_node(self, node: dict[str, Any], nodes_left: int, pause_sec: float = 60.0, requeue: bool = False, retry: bool = False) -> bool | None:
        held = self.evolution_hold(node)
        try:
            return self._attempt_node(node, nodes_left, pause_sec, requeue, retry)
        finally:
            self.evolution_release(held)

    def _attempt_node(self, node: dict[str, Any], nodes_left: int, pause_sec: float, requeue: bool, retry: bool) -> bool | None:
        for attempt in (1, 2):
            try:

                return self.process_node(node, nodes_left=nodes_left, **({"retry": True} if retry else {}))
            except Exception as exc:
                log(f"[factory] node {node.get('id')} crashed: {exc!r}")
                gateway = isinstance(exc, LLMError) and not LLM.quota_exhausted and not self.over_budget()
                if attempt == 1 and gateway:
                    log(f"[factory] node {node.get('id')}: the model gateway failed, not the batch; trying it once more in {int(pause_sec)} s")
                    time.sleep(pause_sec)
                    if not self.over_budget():
                        continue
                    log(f"[factory] node {node.get('id')}: the time budget ran out during the pause; not tried again now")
                if requeue and gateway:
                    return None
                for member in node.get("members") or [node.get("id")]:
                    self.trace.node_state(str(member), "FAILED", "implement")
                return False
        return False

    def run_queue(self, nodes: list[tuple[dict[str, Any], list[Any]]], done_ids: list[str], digest: str, description: str) -> bool:
        all_passed = True

        queue: list[tuple[dict[str, Any], int]] = [(node, 0) for node, _ in nodes]
        foundation_ids = {str(node.get("id")) for node, _ in nodes[: self.FOUNDATION_BATCHES]}
        self.foundation_ids = set(foundation_ids)
        while queue:
            node, requeued = queue.pop(0)
            if str(node.get("id")) in done_ids:
                log(f"[resume] {node.get('id')} was done before this start; skipping it")
                continue
            if requeued and not self.over_budget() and not LLM.quota_exhausted:
                log(f"[factory] {node.get('id')}: back from the end of the queue (gateway re-queue {requeued} of {self.GATEWAY_REQUEUES}); pausing {int(self.GATEWAY_PAUSE_SEC)} s first")
                time.sleep(self.GATEWAY_PAUSE_SEC)

            stop = ""
            if self.over_budget():
                stop = f"time budget exhausted before {node.get('id')}"
                if self.hard_end() is not None and self.time_budget - (clock.now() - self.started) - self.spent_before > 0:
                    self.hard_note(f"batches stop before {node.get('id')}: no batch starts within {int(HARD_END_BATCH_TAIL_SEC)} s of the hard end")
            elif LLM.quota_exhausted:

                stop = f"key out of balance; {1 + len(queue)} batch(es) from {node.get('id')} on are not attempted"
            if stop:
                log(f"[factory] {stop}")
                for rest, times in [(node, requeued)] + queue:
                    if str(rest.get("id")) in done_ids:
                        continue
                    members = [str(m) for m in (rest.get("members") or [rest.get("id")])]
                    if times:
                        self.failed_members.extend(members)
                        for member in members:
                            self.trace.node_state(member, "FAILED", "implement")
                    else:
                        self.unattempted_members.extend(members)
                all_passed = False
                break
            self.held_queue_mark(node, queue, done_ids)
            reported = (getattr(self, "report", None) or {}).get("nodes", {})
            entry_before = reported.get(str(node.get("id")))
            if not requeued and not done_ids and setting_on("evolution_seed_foundation") and self.__dict__.get("seed_inventory"):
                self.evolution_seed_foundation_slot()
            if not requeued and node.get("evolution_entry_debt"):
                self.evolution_entry_debt_slot(node)
            in_flight = watchdog.begin_snapshot(self, f"batch {node.get('id')}")
            ok = self.attempt_node(node, nodes_left=len(queue) + 1, pause_sec=self.GATEWAY_PAUSE_SEC, requeue=requeued < self.GATEWAY_REQUEUES)
            watchdog.end(in_flight)
            if ok is None:
                log(f"[factory] {node.get('id')}: the model gateway failed on both runs; the batch goes to the end of the queue, after {len(queue)} other batch(es)")
                queue.append((node, requeued + 1))
                continue
            fresh = reported.get(str(node.get("id"))) is not None and reported.get(str(node.get("id"))) is not entry_before
            if ok is False and self.foundation_retry_due(node, foundation_ids, len(queue), fresh):

                kind = self.retry_kind(node, foundation_ids)
                status = str((reported.get(str(node.get("id"))) or {}).get("status") or "red")
                if kind == "hub":
                    self.__dict__.setdefault("hub_retried", []).append(str(node.get("id")))
                    hub, dependents = self.hub_leaf(node) or ("?", 0)
                    log(f"[factory] {node.get('id')}: hub batch ended {status} ({hub}: {dependents} leaves depend on it); "
                        "one more attempt with a fresh implementer and the same tests (hub retry)")
                else:
                    self.foundation_retried.append(str(node.get("id")))
                    log(f"[factory] {node.get('id')}: foundation batch ended {status}; one more attempt with a fresh implementer and the same tests (foundation retry)")

                req = str(node.get("id"))
                first_sources = self.snapshot_sources()

                first_evidence = self.__dict__.get("last_check_evidence") if setting_on("retry_partial_keep", True) else None
                first_entry = self.report["nodes"].get(req)
                contexts = getattr(self, "spec_context", None)
                first_contexts = {spec: contexts[spec] for spec in (first_entry or {}).get("tests") or []
                                  if isinstance(contexts, dict) and spec in contexts}

                partial_store = self.__dict__.setdefault("partial_green", {})
                first_partial = {spec: partial_store[spec] for spec in (first_entry or {}).get("tests") or [] if spec in partial_store}
                in_flight = watchdog.begin(f"{kind or 'foundation'} retry of {req}", lambda: self.restore_sources(first_sources))
                ok = bool(self.attempt_node(node, nodes_left=len(queue) + 1, pause_sec=self.GATEWAY_PAUSE_SEC, requeue=False, retry=True))
                watchdog.end(in_flight)
                log(f"[factory] {node.get('id')}: {kind or 'foundation'} retry ended {'GREEN' if ok else 'red'}")
                keep_retry = False
                if not ok and first_evidence is not None:

                    try:
                        retry_sources = self.snapshot_sources()
                        keep_retry, why = partial_repair_keepable(first_evidence, self.__dict__.get("last_check_evidence"),
                                                                  sources_digest(first_sources), sources_digest(retry_sources))
                        seeds = self.seed_text_regressed(node, first_sources, retry_sources) if keep_retry else ""
                        if seeds:
                            keep_retry, why = False, seeds
                    except Exception as exc:
                        keep_retry, why = False, f"the keep decision crashed: {exc!r}"
                    log(f"[factory] {req}: the red retry " + (f"is kept: {why}" if keep_retry else f"is not better per test ({why})"))
                if not ok and not keep_retry:
                    failures_before = self.restore_failures
                    restored = self.restore_sources(first_sources)
                    retry_entry = self.report["nodes"].get(req)
                    retry_contexts = {spec: contexts.get(spec) for spec in first_contexts} if isinstance(contexts, dict) else {}

                    frozen = getattr(self, "test_snapshot", None) or {}
                    retry_tests = [p for p in ((retry_entry or {}).get("tests") or []) if retry_entry is not first_entry and p in frozen]
                    if first_entry is not None:
                        self.report["nodes"][req] = {**first_entry, "tests": list(dict.fromkeys(list(first_entry.get("tests") or []) + retry_tests))}
                    for spec, context in first_contexts.items():
                        tests = dict((context or {}).get("tests") or {})
                        tests.update({p: c for p, c in ((retry_contexts.get(spec) or {}).get("tests") or {}).items() if p in frozen})
                        contexts[spec] = {**context, "tests": {p: frozen.get(p, c) for p, c in tests.items()}}
                    for spec in retry_tests:
                        if isinstance(contexts, dict) and spec not in first_contexts and isinstance(contexts.get(spec), dict):
                            contexts[spec] = {**contexts[spec], "tests": {p: frozen.get(p, c) for p, c in (contexts[spec].get("tests") or {}).items()},
                                              "written": list((first_entry or {}).get("written") or [])}
                    log(f"[factory] {req}: the retry is not kept; {restored} file(s) put back to the state the first attempt left"
                        " (tests stay as the retry left them)")
                    if self.restore_failures > failures_before:
                        log(f"[factory] {req}: WARNING -- putting back the first attempt's state did not reproduce it")

                        entry = self.report["nodes"].get(req)
                        if isinstance(entry, dict) and entry.get("put_back_members"):
                            now_state = self.snapshot_sources()
                            left = sorted(rel for rel in set(first_sources) | set(now_state) if first_sources.get(rel) != now_state.get(rel))
                            if left:
                                entry.pop("put_back_members", None)
                                entry["put_back_incomplete"] = left
                                entry["written"] = list(dict.fromkeys(list(entry.get("written") or []) + left))

                    for spec in list((first_entry or {}).get("tests") or []) + list((retry_entry or {}).get("tests") or []):
                        partial_store.pop(spec, None)
                    if self.restore_failures == failures_before:
                        partial_store.update(first_partial)
            if not ok:
                self.failed_members.extend(str(m) for m in (node.get("members") or [node.get("id")]))
            all_passed = all_passed and ok
            done_ids.append(str(node.get("id")))
            self.held_after_batch(node, bool(ok))
            try:
                self.evolution_after_batch(node, bool(ok))
            except Exception as exc:
                log(f"[evolution] after {node.get('id')}: {exc!r}")
            self.save_checkpoint(digest, done_ids, description)
        self.held_hand_to_closing()
        return all_passed

    def llm_tokens_total(self) -> int:
        total = 0
        llm = getattr(self, "llm", None)
        try:
            total = int((llm.summary() or {}).get("total_tokens") or 0) if llm is not None else 0
        except Exception:
            total = 0
        return max(0, int(getattr(self, "llm_tokens_before", 0) or 0)) + total

    def save_checkpoint(self, digest: str, done: list[str], description: str) -> None:
        state: dict[str, Any] = {"digest": digest, "done": done, "description": description,
                                 "revised_specs": sorted(self.revised_specs),
                                 "spec_defects": dict(self.__dict__.get("spec_defects") or {}),
                                 "spent_sec": round(self.spent_before + (clock.now() - self.started))}
        for field in self.CHECKPOINT_FIELDS:
            state[field] = getattr(self, field)

        state["sources"] = {rel: base64.b64encode(content).decode("ascii") for rel, content in self.snapshot_sources().items()}
        state["llm_tokens_total"] = self.llm_tokens_total()
        state["partial_green"] = dict(getattr(self, "partial_green", None) or {})
        state["debt_specs"] = dict(getattr(self, "debt_specs", None) or {})
        state["unknown_debts"] = dict(getattr(self, "unknown_debts", None) or {})
        state["open_findings"] = dict(self.__dict__.get("open_findings") or {})
        state["audit_routed"] = dict(self.__dict__.get("audit_routed") or {})
        state["audit_rollback_evidence"] = dict(self.__dict__.get("audit_rollback_evidence") or {})
        state["seed_quarantine"] = dict(self.__dict__.get("seed_quarantine") or {})
        state["seed_quarantine_switch"] = setting_on("seed_conflict_quarantine")
        if setting_on("findings_owner_batch_v2", True):
            state["held_findings"] = dict(self.__dict__.get("held_findings") or {})
            state["held_released"] = list(self.__dict__.get("held_released") or [])
        if self.__dict__.get("evolution"):
            state["evolution"] = self.evolution
            state["evolution_frozen"] = dict(self.__dict__.get("_frozen_inherited_specs") or {})
        state.update(watchdog.checkpoint_ends(self))

        state["req_sha"] = str(self.__dict__.get("requirement_sha") or "")
        if self.__dict__.get("continuation"):
            state["continuation"] = self.continuation
        try:
            write_text(self.project_dir / ".arc" / "resume.json", json.dumps(state, ensure_ascii=False))
        except (OSError, TypeError) as exc:
            log(f"[resume] checkpoint not written: {exc!r}")

    def load_checkpoint(self, digest: str) -> list[str]:
        path = self.project_dir / ".arc" / "resume.json"
        if os.environ.get("AGENT_RESUME", "1") == "0" or not path.is_file():
            return []
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"[resume] checkpoint unreadable, starting from zero: {exc!r}")
            return []
        if state.get("digest") != digest:
            log("[resume] checkpoint is for other requirements; starting from zero")
            return []

        sha, saved_sha = str(self.__dict__.get("requirement_sha") or ""), str(state.get("req_sha") or "")
        if sha and saved_sha and saved_sha != sha:
            log("[resume] checkpoint is for another requirements.yaml (an earlier stage's run); starting from zero")
            return []
        if sha and not saved_sha and (self.__dict__.get("continuation_found") or (self.__dict__.get("inherited_app") or {}).get("preserve")):
            log("[resume] checkpoint without a requirement identity in a folder an earlier stage delivered; starting from zero")
            return []
        try:
            sources = {rel: base64.b64decode(text) for rel, text in (state.get("sources") or {}).items()}
        except (TypeError, ValueError, AttributeError) as exc:
            log(f"[resume] the saved sources are unreadable, starting from zero: {exc!r}")
            return []
        for field in self.CHECKPOINT_FIELDS:
            if field in state:
                setattr(self, field, state[field])
        self.resumed_sources = sources
        if isinstance(state.get("partial_green"), dict):
            self.partial_green = dict(state["partial_green"])
        if isinstance(state.get("debt_specs"), dict):
            self.debt_specs = dict(state["debt_specs"])
        if isinstance(state.get("unknown_debts"), dict):
            self.unknown_debts = dict(state["unknown_debts"])
        if isinstance(state.get("open_findings"), dict):
            self.open_findings = dict(state["open_findings"])
        for key in ("audit_routed", "audit_rollback_evidence"):
            if isinstance(state.get(key), dict):
                setattr(self, key, dict(state[key]))
        if isinstance(state.get("held_findings"), dict) and setting_on("findings_owner_batch_v2", True):
            self.held_findings = {str(leaf): [r for r in rows if isinstance(r, dict)]
                                  for leaf, rows in state["held_findings"].items() if isinstance(rows, list)}
            log(f"[resume] {sum(len(r) for r in self.held_findings.values())} held audit finding row(s) restored "
                f"for {len(self.held_findings)} leaf(s)")
            if isinstance(state.get("held_released"), list):
                self.held_released = [r for r in state["held_released"] if isinstance(r, dict)]
        if isinstance(state.get("seed_quarantine"), dict):
            if setting_on("seed_conflict_quarantine"):
                self.seed_quarantine = dict(state["seed_quarantine"])
            elif state["seed_quarantine"]:

                log(f"[resume] seed_conflict_quarantine is off: the saved seed quarantine {sorted(state['seed_quarantine'])} "
                    "is ignored")
        self.revised_specs = set(state.get("revised_specs") or [])
        if isinstance(state.get("spec_defects"), dict):
            self.spec_defects = dict(state["spec_defects"])
        if isinstance(state.get("continuation"), dict):
            self.continuation = dict(state["continuation"])
        if isinstance(state.get("evolution"), dict):
            self.evolution = dict(state["evolution"])
            self._frozen_inherited_specs = dict(state.get("evolution_frozen") or {})
        self.resumed_description = str(state.get("description") or "")
        self.spent_before = float(state.get("spent_sec") or 0.0)
        watchdog.resume_ends(self, state, log=log)
        try:
            self.llm_tokens_before = max(0, int(state.get("llm_tokens_total") or 0))
        except (TypeError, ValueError):
            self.llm_tokens_before = 0
        done = [str(i) for i in (state.get("done") or [])]
        log(f"[resume] {len(done)} batch(es) already done: {done}")
        return done

    def author_tests(self, node: dict[str, Any], prefix: str, contract: str) -> list[AuthoredFile]:
        spec = None
        if SETTINGS.get("outsider_enabled", True):
            try:
                ensure_helper(self.project_dir)
                spec = author_acceptance(self.llm, node, prefix, log=log)
            except Exception as exc:
                log(f"[outsider] {prefix}: failed: {exc!r}")
            if spec is not None and SETTINGS.get("spec_review", True):

                try:
                    problems = review_acceptance(self.llm, node, spec, prefix, log=log)
                    if problems:
                        revised = author_acceptance(self.llm, node, prefix, problem="A reviewer compared your spec with the requirement "
                                                    "and found these contradictions:\n" + problems, current=spec.content, log=log)
                        if revised is not None:
                            spec = self.recheck_rewritten_spec(node, prefix, revised)

                            self.revised_specs.add(spec.path)
                except Exception as exc:
                    log(f"[outsider] {prefix}: review failed: {exc!r}")
        try:
            others = self.author.author(node, prefix, contract=contract, with_e2e=spec is None)
        except RuntimeError as exc:

            if spec is not None or "produced no test files" not in str(exc):
                raise
            log(f"[author] {prefix}: {exc}")
            return []
        if spec is None:
            return others
        return [f for f in others if not f.path.startswith("backend/test-e2e/")] + [spec]

    def repair_tests(self, node: dict[str, Any], prefix: str, files: list[AuthoredFile], problem: str, contract: str = "") -> list[AuthoredFile]:
        spec = next((f for f in files if f.path.startswith("backend/test-e2e/") and "e2e-support/outsider" in f.content), None)
        others = [f for f in files if f is not spec]
        if spec is None:
            return self.author.repair(node, prefix, files, problem, contract=contract)
        names_other = [Path(f.path).name for f in others if Path(f.path).name in problem]

        spec_verdict = re.search(r"(?m)^(?:The acceptance spec contradicts the requirement text:|\s*SPEC:)", problem) is not None
        about_spec = Path(spec.path).name in problem or not names_other or spec_verdict
        if names_other:
            others = self.author.repair(node, prefix, others, problem, contract=contract)
        if about_spec:
            repaired = author_acceptance(self.llm, node, prefix, problem=problem, current=spec.content, log=log)
            spec = self.recheck_rewritten_spec(node, prefix, repaired) if repaired is not None else spec
        return others + [spec]

    def repair_tests_within(self, req_id: str, node: dict[str, Any], prefix: str, files: list[AuthoredFile], test_files: dict[str, str],
                            problem: str, contract: str, allowed: set[str] | None) -> tuple[list[AuthoredFile], dict[str, str]]:
        if allowed is None:
            files = self.repair_tests(node, prefix, files, problem, contract=contract)
            return files, self.write_tests(req_id, files)
        given = [f for f in files if f.path in allowed]
        if not given:
            log(f"[tdd] {req_id}: the API test defect names no test file of this batch ({sorted(allowed)}); no test repair")
            return files, test_files
        answer = {f.path: f for f in self.repair_tests(node, prefix, given, problem, contract=contract)}
        outside = sorted(path for path in answer if path not in allowed)
        if outside:
            log(f"[tdd] {req_id}: the API test repair returned {outside}, outside the defect's files; ignored")
        merged = [answer.get(f.path, f) if f.path in allowed else f for f in files]
        changed = [f for f in merged if f.path in allowed and (f.path not in test_files or test_files[f.path] != f.content)]
        written = dict(test_files)
        written.update(self.write_tests(req_id, changed) if changed else {})
        return merged, written

    def recheck_rewritten_spec(self, node: dict[str, Any], prefix: str, spec: AuthoredFile) -> AuthoredFile:
        from agent.outsider import deterministic_review
        try:
            problems = deterministic_review(node, spec, prefix, log=log)
            if not problems:
                self.settle_spec_defect(node, spec.path, [])
                return spec
            log(f"[outsider] {prefix}: the rewritten spec breaks {len(problems)} scripted rule(s); one more rewrite")
            again = author_acceptance(self.llm, node, prefix, problem="A reviewer compared your spec with the requirement "
                                      "and found these contradictions:\n" + "\n".join(problems[:8]), current=spec.content, log=log)
            if again is None:
                self.settle_spec_defect(node, spec.path, problems)
                return spec
            left = deterministic_review(node, again, prefix, log=log)
            if left:
                log(f"[outsider] {prefix}: {len(left)} scripted problem(s) remain after the second rewrite; kept as it is")
            self.settle_spec_defect(node, again.path, left)
            return again
        except Exception as exc:
            log(f"[outsider] {prefix}: re-check of the rewritten spec failed: {exc!r}")
            return spec

    SPEC_DEFECT_PROBLEMS = 4

    def settle_spec_defect(self, node: dict[str, Any], path: str, problems: list[str]) -> None:
        try:
            if not setting_on("spec_defect_outcome"):
                return
            from agent.outsider import certain_spec_defects

            book = self.__dict__.setdefault("spec_defects", {})
            findings = self.__dict__.setdefault("open_findings", {})
            certain = certain_spec_defects(list(problems or []))
            key = "spec-defect:" + str(path)
            if not certain:
                if book.pop(path, None) is not None:
                    findings.pop(key, None)
                    log(f"[outsider] {Path(path).name}: no certain spec defect left")
                return
            entry = book.setdefault(path, {"batch": str(node.get("id") or ""), "repair_done": False})
            entry["problems"] = [" ".join(str(p).split())[:400] for p in certain[:self.SPEC_DEFECT_PROBLEMS]]
            self.revised_specs.add(path)
            findings[key] = {"class": "spec defect", "where": f"{path}: {entry['problems'][0]}"[:160],
                             "batch": entry["batch"]}
            log(f"[outsider] {Path(path).name}: {len(certain)} certain spec defect(s) remain"
                + (" after its SPEC repair -- booked open (spec defect), never counted as clearing evidence"
                   if entry.get("repair_done") else " -- one SPEC repair in the batch's test-defect budget"))
        except Exception as exc:
            log(f"[outsider] spec-defect booking failed: {exc!r}")

    def repair_spec_defects(self, req_id: str, node: dict[str, Any], prefix: str, files: list[AuthoredFile],
                            test_files: dict[str, str], contract: str) -> tuple[list[AuthoredFile], dict[str, str], int]:
        if not setting_on("spec_defect_outcome"):
            return files, test_files, 0
        book = self.__dict__.get("spec_defects") or {}
        due = [path for path in test_files if isinstance(book.get(path), dict) and not book[path].get("repair_done")]
        if not due:
            return files, test_files, 0
        try:
            lines = [f"- {Path(path).name}: {problem}" for path in due for problem in book[path].get("problems") or []]
            for path in due:
                book[path]["repair_done"] = True
            problem = ("The acceptance spec contradicts the requirement text:\n" + "\n".join(lines[:8]) + "\nThese are certain "
                       "(a script compared the spec's clicks with the scenario's own steps and stated roles): follow the "
                       "scenario's steps exactly, with the role the requirement states for each, and change nothing else.")
            log(f"[tdd] {req_id}: {len(due)} spec(s) with certain scripted defects left after their rewrites; ONE SPEC "
                "repair (counted as a test-defect round)")
            files, test_files = self.repair_tests_within(req_id, node, prefix, files, test_files, problem, contract, None)
            self.revised_specs.update(due)

            broken = static_test_defects(self.project_dir, list(test_files))
            if broken:
                log(f"[gate-E1] {req_id}: the SPEC-repaired tests do not parse / load; one more repair:\n{broken[:600]}")
                files, test_files = self.repair_tests_within(req_id, node, prefix, files, test_files, "These test files do not "
                                                             "parse / load:\n" + broken, contract, None)
            return files, test_files, 1
        except Exception as exc:
            log(f"[tdd] {req_id}: SPEC repair of the certain defects failed: {exc!r}")
            return files, test_files, 0

    CROSSDEP_PROBES_PER_CHECK = 3
    CROSSDEP_PROBE_SEC = 120.0
    CROSSDEP_CHECK_SEC = 240.0

    def crossdep_verdicts(self, node: dict[str, Any], e2e: Any, own_specs: list[str], until: float | None) -> list[dict]:
        try:
            static_on = setting_on("v162b_crossdep", True)
            probe_on = setting_on("v162b_dependency_probe", True)
            if not (static_on or probe_on) or getattr(e2e, "ok", False) or getattr(e2e, "skipped", False):
                return []
            by_name = {Path(spec).name: spec for spec in own_specs}
            entries = [e for e in (getattr(e2e, "failed_tests", None) or [])
                       if isinstance(e, (tuple, list)) and len(e) >= 4 and Path(str(e[0])).name in by_name]
            if not entries:
                return []
            from agent import testrun as _testrun
            from agent import verify as _verify

            ran = int(getattr(e2e, "ran", -1) if getattr(e2e, "ran", -1) is not None else -1)
            all_red = 0 <= ran <= len(getattr(e2e, "failed_tests", None) or [])
            cache = self.__dict__.setdefault("crossdep_probed", {})
            digest = ""
            found: list[dict] = []
            probes = 0
            began = clock.now()
            for entry in entries:
                spec = by_name[Path(str(entry[0])).name]
                line, title = int(entry[1] or 0), str(entry[3])
                message, kind = "", ""
                if static_on:
                    message = str(_testrun.cross_test_dependency_defects(self.project_dir, [spec], node, [title]) or "")
                    kind = "static" if message else ""
                if not message and probe_on and not all_red:
                    path = self.project_dir / spec
                    text = path.read_bytes() if path.is_file() else b""
                    if not digest:
                        digest = sources_digest(self.snapshot_sources())
                    key = (spec, line, title, hashlib.sha1(text).hexdigest(), digest)
                    if key in cache:
                        verdict = cache[key]
                    else:
                        left = min(self.CROSSDEP_PROBE_SEC, self.CROSSDEP_CHECK_SEC - (clock.now() - began))
                        if until is not None:
                            left = min(left, float(until) - clock.now())
                        if probes >= self.CROSSDEP_PROBES_PER_CHECK or left < _verify.DEPENDENCY_PROBE_MIN_RUN_SEC:
                            continue
                        probes += 1
                        verdict = _verify.dependency_probe(self.project_dir, spec, tuple(entry), log=log, time_limit=left, until=until)
                        cache[key] = verdict
                    if isinstance(verdict, dict) and verdict.get("message"):
                        message, kind = str(verdict["message"]), "probe"
                if message:
                    found.append({"spec": spec, "name": Path(spec).name, "line": line, "title": title, "kind": kind,
                                  "message": message})
            return found
        except Exception as exc:
            log(f"[crossdep] the cross-test dependency check failed: {exc!r}; the red tests stay the implementer's")
            return []

    SNAPSHOT_SKIP_DIRS = {"node_modules", "dist", "tests", "test", "test-e2e", "e2e-support", "test-results", ".arc-test-db", "playwright-report", ".vite"}
    SNAPSHOT_SKIP_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".log", ".sqlite", ".sqlite3", ".arc-restore")

    def snapshot_sources(self) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for path in self.source_paths():
            try:
                files[path.relative_to(self.project_dir).as_posix()] = path.read_bytes()
            except OSError:
                pass
        return files

    def source_names(self) -> set[str]:
        return {path.relative_to(self.project_dir).as_posix() for path in self.source_paths()}

    def source_paths(self) -> list[Path]:
        paths: list[Path] = []
        for top in ("frontend", "backend"):
            base = self.project_dir / top
            for path in sorted(base.rglob("*")) if base.is_dir() else []:
                relative = path.relative_to(base)
                folders = relative.parts[:-1]

                if (folders and folders[0] in self.SNAPSHOT_SKIP_DIRS) or "node_modules" in folders or not path.is_file():
                    continue
                if path.suffix in self.SNAPSHOT_SKIP_SUFFIXES:
                    continue
                try:
                    if path.stat().st_size <= 2_000_000:
                        paths.append(path)
                except OSError:
                    pass
        return paths

    SEED_FILE = re.compile(r"^backend/src/database/(?:[^/]*/)*(?:init_db|seed[^/]*)\.[cm]?[jt]s$", re.IGNORECASE)
    SEED_WORDS = re.compile(r"seed|种子", re.IGNORECASE)

    SCHEMA_LINE = re.compile(r"(?i:\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|UNIQUE\s+INDEX|VIEW|TRIGGER)\b|\bADD\s+COLUMN\b)|"
                             r"\b\w+\s+(?:INTEGER|TEXT|REAL|BLOB|BOOLEAN|DATETIME|VARCHAR|NUMERIC)\b")

    SEED_PENDING_HEAD = "STILL OPEN from an earlier check of this batch (not blocking; repair them in the same answer if they are real): "

    def seed_pending_note(self, req_id: str, node: dict[str, Any], batch_before: dict[str, bytes], own_specs: list[str]) -> str:
        try:
            checks = getattr(self, "seed_checks", None) or {}
            if not any(checks.get(key, 0) >= 1 for key in (f"{req_id}:values", f"{req_id}:names", req_id)):
                return ""
            now = self.snapshot_sources()
            parts: list[str] = []
            if batch_before and checks.get(f"{req_id}:values", 0) >= 1:
                lost = lost_seed_values(str(node.get("seed_sentences") or ""), batch_before, now)
                if lost:
                    parts.append("stated seed values this batch removed: " + ", ".join(f'"{v}"' for v in lost[:20]))
            if batch_before and checks.get(f"{req_id}:names", 0) >= 1:
                earlier = "\n".join((self.project_dir / spec).read_text(encoding="utf-8", errors="replace")
                                    for spec in self.e2e_specs if spec not in own_specs and (self.project_dir / spec).is_file())
                gone = lost_stated_names(str(node.get("requirement_text") or ""), batch_before, now, earlier)
                if gone:
                    parts.append("stated names this batch removed: " + ", ".join(f'"{n}"' for n in gone[:20]))
            if checks.get(req_id, 0) >= 1:
                missing = missing_seed_names(describe_node({k: v for k, v in node.items() if k != "ancestor_context"}), now)
                if missing:
                    parts.append("stated seed records missing: " + ", ".join(f'"{n}"' for n in list(missing)[:20]))
            return ("\n\n" + self.SEED_PENDING_HEAD + "; ".join(parts) + ".") if parts else ""
        except Exception as exc:
            log(f"[tdd] {req_id}: the still-open seed note could not be built: {exc!r}")
            return ""

    def audit_carry_check(self, req_id: str, node: dict[str, Any], check, findings: list[dict[str, Any]], carry: dict[str, Any],
                          end: float):
        def audit_check():
            outcome = check()
            if not getattr(outcome, "passed", False) or carry.get("state") is not None or carry.get("tried"):
                return outcome
            carry["tried"] = True
            if self.over_budget() or LLM.quota_exhausted or clock.now() >= end:
                return outcome
            from agent.audit import finding_fingerprint, is_session_finding, recheck_repaired

            selected = [f for f in findings if not is_session_finding(f)]
            if not selected:
                return outcome
            try:
                rechecked = recheck_repaired(self.project_dir, node, self.contract, selected, log=log)
            except Exception as exc:
                log(f"[audit] {req_id}: re-check inside the audited repair crashed: {exc!r}")
                return outcome
            state = self.snapshot_sources()
            carry.update(rechecked=rechecked, digest=sources_digest(state), checked=[finding_fingerprint(f) for f in selected])
            still = ([f for f in rechecked.get("still") or [] if isinstance(f, dict)]
                     if isinstance(rechecked, dict) and rechecked.get("ran") else [])
            if not still or clock.now() >= end:
                return outcome
            carry.update(state=state, green=outcome)
            log(f"[audit] {req_id}: the batch's checks are green but {len(still)} audit finding(s) are still there; "
                "the repair's next round gets them (the green state is kept if that round does not end green)")
            return TestOutcome(False, "AUDIT FINDINGS STILL THERE (every test of the batch PASSES; keep it so)" + chr(10)
                               + "The independent audit's re-check still reports these findings on the changed code; repair "
                               "them without breaking anything that passes now:" + chr(10) + findings_brief(still)
                               + chr(10) + chr(10) + str(outcome.output or ""))

        return audit_check

    def audit_carry_settle(self, req_id: str, fix: Any, carry: dict[str, Any]) -> Any:
        if getattr(fix, "status", "") == "passed":
            return fix
        state = carry["state"]
        if sources_digest(self.snapshot_sources()) != carry.get("digest"):
            failures_before = self.restore_failures
            self.restore_sources(state)
            if self.restore_failures > failures_before or sources_digest(self.snapshot_sources()) != carry.get("digest"):
                log(f"[audit] {req_id}: the green state of the audited repair could not be put back; it ends {fix.status}")
                return fix
        log(f"[audit] {req_id}: the round on the remaining findings ended {fix.status}; the earlier green state is kept")
        green = carry.get("green")
        return ImplementResult("passed", list(getattr(fix, "written", []) or []), "green at the audited repair's earlier check",
                               getattr(fix, "test_runs", 0) or 0, last_check=str(getattr(green, "output", "") or ""))

    @staticmethod
    def seed_text_regressed(node: dict[str, Any], before: dict[str, bytes], now: dict[str, bytes]) -> str:
        try:
            lost = lost_seed_values(str(node.get("seed_sentences") or ""), before, now)
            if lost:
                return f"stated seed values gone: {lost[:6]}"
            text = describe_node({k: v for k, v in node.items() if k != "ancestor_context"})
            new = [n for n in missing_seed_names(text, now) if n not in set(missing_seed_names(text, before))]
            return f"stated seed records missing now: {new[:6]}" if new else ""
        except Exception as exc:
            return f"the seed text check failed: {exc!r}"

    def audit_partial_keep(self, req_id: str, before: dict[str, bytes], before_evidence: Any, after_evidence: Any,
                           findings: list[dict[str, Any]], fix: Any, node: dict[str, Any] | None = None) -> bool:
        try:
            return self._audit_partial_keep(req_id, before, before_evidence, after_evidence, findings, fix, node)
        except Exception as exc:
            log(f"[audit] {req_id}: the partial-keep decision crashed ({exc!r}); the repair goes back as before")
            return False

    def _audit_partial_keep(self, req_id: str, before: dict[str, bytes], before_evidence: Any, after_evidence: Any,
                            findings: list[dict[str, Any]], fix: Any, node: dict[str, Any] | None = None) -> bool:
        now = self.snapshot_sources()
        if any(self.SEED_FILE.match(rel) and now.get(rel) != before.get(rel) for rel in set(now) | set(before)):
            return False
        keep, why = partial_repair_keepable(before_evidence, after_evidence, sources_digest(before), sources_digest(now))
        if keep:
            seeds = self.seed_text_regressed(node or {}, before, now)
            if seeds:
                keep, why = False, seeds
        log(f"[audit] {req_id}: the audited repair ended as {getattr(fix, 'status', '?')}; "
            + (f"kept red: {why}" if keep else f"not kept ({why})"))
        if keep:
            from agent.audit import finding_fingerprint

            self.audit_rolled_back_fingerprints = list(getattr(self, "audit_rolled_back_fingerprints", None) or []) + [
                finding_fingerprint(f) for f in findings]
        return keep

    def audit_seed_guard(self, req_id: str, before: dict[str, bytes], findings: list[dict[str, Any]], fix: Any, check) -> Any:
        now = self.snapshot_sources()
        seeds = sorted(rel for rel in set(now) | set(before) if self.SEED_FILE.match(rel) and now.get(rel) != before.get(rel))
        if not seeds:
            return fix
        if any(str(f.get("where") or "").strip().lower() == "seed data"
               or self.SEED_WORDS.search(" ".join(str(f.get(k) or "") for k in ("requirement", "problem"))) for f in findings):
            return fix

        import difflib

        for rel in seeds:
            old = (before.get(rel) or b"").decode("utf-8", "replace").splitlines()
            new = (now.get(rel) or b"").decode("utf-8", "replace").splitlines()
            changed = [line[2:] for line in difflib.ndiff(old, new) if line[:2] in ("+ ", "- ")]
            if any(self.SCHEMA_LINE.search(line) for line in changed):
                log(f"[audit] {req_id}: the audited repair changed the schema in {rel}; its seed change is not put back")
                return fix
        for rel in seeds:
            source_io.put_back(self.project_dir / rel, before.get(rel))
        log(f"[audit] {req_id}: the audited repair rewrote seed file(s) {seeds} although no finding is about seed data; "
            "put back, checking the repair once more without them")
        again = check()
        if not getattr(again, "passed", False):
            log(f"[audit] {req_id}: without the seed change the check is red; the whole repair goes back")
            return ImplementResult("gave_up", list(getattr(fix, "written", []) or []), "red once its unrelated seed change was put back")
        fix.written = [rel for rel in (getattr(fix, "written", []) or []) if rel not in seeds]
        if getattr(fix, "last_check", None) is not None:
            fix.last_check = again.output
        log(f"[audit] {req_id}: green without the seed change; the rest of the repair is kept")
        return fix

    def begin_restore_point(self, what: str) -> int | None:
        self.restore_point = None
        if watchdog.active() is None:
            return None
        point = {"sources": self.snapshot_sources()}
        self.restore_point = point
        return watchdog.begin(what, lambda: self.restore_sources(point["sources"]))

    def refresh_restore_point(self, snapshot: dict[str, bytes] | None = None) -> bool:
        point = self.__dict__.get("restore_point")
        if point is None:
            return False
        point["sources"] = dict(snapshot) if snapshot is not None else self.snapshot_sources()
        return True

    def restore_sources(self, snapshot: dict[str, bytes]) -> PutBack:

        watchdog.park()
        in_flight = watchdog.begin("putting back the sources", lambda: self.restore_sources(snapshot))

        added = sorted(set(self.snapshot_sources()) - set(snapshot))
        items = [(self.project_dir / rel, content) for rel, content in snapshot.items()]
        items += [(self.project_dir / rel, None) for rel in added]

        try:
            changed, failed = source_io.put_back_all(
                items, late_deletes=lambda: [self.project_dir / rel for rel in self.source_names() - set(snapshot)])
        except OSError:
            changed, failed = 0, []
        watchdog.end(in_flight)
        if failed:

            log(f"[factory] putting back the sources: {len(failed)} file(s) could not be put back (left as they were): "
                f"{[Path(p).as_posix() for p in failed][:10]}")
        after = self.snapshot_sources()
        if after != snapshot:
            self.restore_failures += 1
            missing = sorted(set(snapshot) - set(after))
            extra = sorted(set(after) - set(snapshot))
            different = sorted(rel for rel in set(after) & set(snapshot) if after[rel] != snapshot[rel])
            log("[factory] WARNING: restoring the sources did not reproduce the snapshot; the affected batch is booked as failed "
                f"(missing: {missing[:10]}; extra: {extra[:10]}; different: {different[:10]})")
            return PutBack(changed, complete=False)
        return PutBack(changed)

    def restore_tests(self) -> None:
        for rel, content in self.test_snapshot.items():
            path = self.project_dir / rel
            try:
                if not path.is_file() or path.read_text(encoding="utf-8", errors="replace") != content:
                    write_text(path, content)
                    log(f"[freeze] restored {rel}")
            except OSError:
                pass

    REGRESSION_SPECS_MAX = 1000

    FOUNDATION_CHECKS_MAX = 4

    CONTINUATION_SAMPLE_MIN = 2
    CONTINUATION_NOTE_RUN_SEC = 300.0
    CONTINUATION_END_RUN_SEC = 1200.0

    def protected_specs(self) -> list[str]:
        protected = set(((self.__dict__.get("continuation") or {}).get("protected")) or [])
        if not protected:
            return []
        return [spec for spec in self.__dict__.get("green_specs") or [] if spec in protected]

    def foundation_specs(self) -> list[str]:
        first = list(self.green_specs[:3])
        protected = self.protected_specs()
        return first + [spec for spec in protected if spec not in first] if protected else first

    def foundation_run_list(self, own: list[str] | tuple = (), budget_sec: float = CONTINUATION_NOTE_RUN_SEC,
                            what: str = "foundation run") -> list[str]:
        base = [spec for spec in self.green_specs[:3] if spec not in own]
        extra = [spec for spec in self.protected_specs() if spec not in base and spec not in own]
        if not extra:
            return base
        size = len(extra)
        while size > self.CONTINUATION_SAMPLE_MIN and self.suite_est(len(base) + size) > budget_sec:
            size -= 1
        if size >= len(extra):
            return base + extra
        cursor = int(self.__dict__.get("protected_cursor", 0) or 0) % len(extra)
        window = (extra[cursor:] + extra[:cursor])[:size]
        self.protected_cursor = (cursor + size) % len(extra)
        log(f"[continuation] {what}: {size} of {len(extra)} protected earlier-stage spec(s) in this run (a rotating "
            f"window sized to ~{int(budget_sec)} s; the next runs take the others): {[Path(s).name for s in window]}")
        return base + window

    CONTINUATION_BASELINE_SEC = 3600.0

    def continuation_baseline(self) -> dict[str, Any]:
        cont = self.continuation_run_baseline()
        self.report["continuation"] = {key: cont.get(key) for key in ("earlier", "protected", "unchecked", "beyond", "baseline", "baseline_sec")}
        self.report["continuation"]["baseline_red"] = sorted(cont.get("baseline_red") or {})
        return cont

    def continuation_run_baseline(self) -> dict[str, Any]:
        cont = self.continuation if isinstance(self.continuation, dict) else {}
        specs = [spec for spec in cont.get("earlier") or [] if (self.project_dir / spec).is_file()]
        cont.update(protected=[], baseline_red={}, unchecked=list(specs), baseline="unknown")
        if not specs:
            cont["baseline"] = "no spec files"
            return cont
        bound = min(setting_seconds("continuation_baseline_sec", self.CONTINUATION_BASELINE_SEC), e2e_worst(len(specs)))
        if not self.hard_fits("the continuation baseline", bound):
            log("[continuation] baseline not run: the hard end is near; no earlier-stage spec is protected")
            return cont

        try:
            ensure_helper(self.project_dir)
        except Exception as exc:
            log(f"[continuation] the e2e helper could not be written before the baseline: {exc!r}")
        began = clock.now()
        build = build_frontend(self.project_dir, log=log)
        self.note_duration("build", clock.now() - began)
        if not build.ok:
            infra = getattr(build, "infra", "")
            log(f"[continuation] baseline: the existing application does not build ({'install: ' + infra if infra else 'build error'}); "
                "its specs are not judged and none is protected")
            cont["baseline"] = "build failed"
            return cont
        log(f"[continuation] baseline: running the {len(specs)} earlier-stage spec file(s) on the application as it was "
            f"delivered (at most {int(bound)} s; files not reached by then are not judged)")
        began = clock.now()
        run = run_e2e(self.project_dir, specs, log=log, **until_kw(began + bound))
        if run.skipped:
            log("[continuation] baseline: Playwright did not run here; no earlier-stage spec is protected")
            cont["baseline"] = "skipped"
            return cont
        self.note_duration("e2e", clock.now() - began, len(specs))
        output = str(run.output or "")
        failed = failed_spec_names(output) if not run.ok else set()
        unrun = not_run_spec_names(output) | partly_run_spec_names(output)
        if not run.ok and not failed and not unrun:
            log("[continuation] baseline: the run failed without naming a spec (the app did not start?); none is protected")
            cont["baseline"] = "unnamed failure"
            return cont
        signatures = failed_test_signatures(output) if failed else {}

        if failed:
            again_specs = [spec for spec in specs if Path(spec).name in failed]
            again = run_e2e(self.project_dir, again_specs, log=log, **until_kw(began + bound))
            again_out = str(getattr(again, "output", "") or "")
            again_failed = failed_spec_names(again_out) if not again.ok else set()
            again_unrun = not_run_spec_names(again_out) | partly_run_spec_names(again_out)
            if again.skipped or (not again.ok and not again_failed and not again_unrun):
                again_unrun = set(failed)
            green_again = {name for name in failed if name not in again_failed and name not in again_unrun}
            if green_again or again_unrun:
                log(f"[continuation] baseline: run once more, {sorted(green_again)} passed (protected), "
                    f"{sorted(set(failed) & again_failed)} failed again (baseline red), {sorted(set(failed) & again_unrun)} not run again (not judged)")
            unrun = unrun | (set(failed) & again_unrun)
            failed = set(failed) & again_failed
            signatures = {name: sigs for name, sigs in signatures.items() if name in failed}
        protected = [spec for spec in specs if Path(spec).name not in failed and Path(spec).name not in unrun]
        red = {Path(spec).name: dict(signatures.get(Path(spec).name, {})) for spec in specs if Path(spec).name in failed}
        unchecked = [spec for spec in specs if Path(spec).name in unrun and Path(spec).name not in failed]
        cont.update(protected=protected, baseline_red=red, unchecked=unchecked, baseline="taken",
                    baseline_sec=round(clock.now() - began))

        self.green_specs = list(dict.fromkeys(protected + list(self.green_specs)))
        self.e2e_specs = list(dict.fromkeys(protected + list(self.e2e_specs)))
        log(f"[continuation] baseline ({int(clock.now() - began)} s): {len(protected)} green -> protected (regression set "
            f"and foundation of every batch): {[Path(s).name for s in protected]}; {len(red)} red at the start (a baseline, "
            f"never blamed on a batch): {sorted(red)}; {len(unchecked)} not judged: {[Path(s).name for s in unchecked]}")
        return cont

    def red_batch_broke_foundation(self, req_id: str, status: str, batch_before: dict[str, bytes]) -> bool:
        if not batch_before or not self.green_specs or self.snapshot_sources() == batch_before:
            return False

        foundation_specs = self.foundation_run_list((), self.CONTINUATION_END_RUN_SEC, f"{req_id}: the end check of a red batch")
        build = build_frontend(self.project_dir, log=log)
        if not build.ok and getattr(build, "infra", ""):

            log(f"[tdd] foundation check not judged: the build failed for an infrastructure reason ({build.infra})")
            return False
        check = run_e2e(self.project_dir, foundation_specs, log=log) if build.ok else StepResult(False, "FRONTEND BUILD FAILED\n" + build.output)
        if check.skipped:
            return False
        self.record_run(foundation_specs, check.ok, check.output, **unrun_kw(check, self.record_run))
        if check.ok:
            return False

        named = failed_spec_names(check.output) if build.ok else set()
        if named:
            confirmed = self.failing_alone(req_id, foundation_specs, named)
            if not confirmed:
                return False

            if not self.new_foundation_failures(req_id, batch_before, dict(confirmed)):
                return False
        failures_before = self.restore_failures
        restored = self.restore_sources(batch_before)
        log(f"[tdd] {req_id}: ends {status} AND the foundation specs fail now; {restored} file(s) put back to the state before this batch")
        if self.restore_failures > failures_before:

            log(f"[tdd] {req_id}: WARNING -- the rollback did not reproduce the state before this batch; the foundation may still be broken")
        return True

    def salvage_foundation_break(self, req_id: str, batch_before: dict[str, bytes], own_specs: list[str],
                                 regression_state: dict[str, Any], deadline: float) -> tuple[list[str], str, Any]:
        if not setting_on("foundation_salvage") or not batch_before or not own_specs:
            return [], "", ""
        failed = set(regression_state.get("failed") or [])
        failing = [spec for spec in self.foundation_specs() if Path(spec).name in failed]
        if not failing:
            return [], "", ""
        end_state = self.snapshot_sources()
        covered = sorted({rel for spec in failing for rel in self.foundation_covered(spec)})
        shared = [rel for rel in sorted(set(batch_before) | set(end_state))
                  if batch_before.get(rel) != end_state.get(rel) and SHARED_DATA_FILE.search(rel)]
        order, hidden = digest_order(batch_before, end_state, shared + [rel for rel in covered if rel not in shared])
        order += hidden

        from agent import verify

        rest = [spec for spec in self.regression_specs(batch_before, own_specs) if spec not in failing]
        partial = self.partial_green_groups(batch_before, own_specs)
        rest_need = (self.suite_est(len(rest)) if rest else 0.0) + sum(
            max(self.suite_est(1), len(groups) * verify.ISOLATED_RUN_EST_SEC) for _, groups in partial)
        foundation_names = {Path(spec).name for spec in self.foundation_specs()}
        def late(rel: str, what: str, force: bool = False) -> bool:
            if not force and clock.now() < deadline and self.hard_left(HARD_END_FINISH_SEC) > 0:
                return False
            log(f"[tdd] {req_id}: salvage stopped: the batch's deadline passed during the try with {rel} put back ({what})")
            return True

        def salvage_end() -> float:
            return min(deadline, clock.now() + self.hard_left(HARD_END_FINISH_SEC))

        built = end_state
        tried: list[str] = []
        for rel in order:
            state = dict(end_state)
            if rel in batch_before:
                state[rel] = batch_before[rel]
            else:
                state.pop(rel, None)
            rebuild = any(name.startswith("frontend/") and built.get(name) != state.get(name) for name in set(built) | set(state))
            need = (self.build_est() if rebuild else 0.0) + self.suite_est(len(failing)) + self.suite_est(len(own_specs)) + rest_need
            if clock.now() + need > deadline or self.hard_left(HARD_END_FINISH_SEC) < need:
                if tried:
                    log(f"[tdd] {req_id}: salvage stopped: no time for the next try ({int(need)} s needed)")
                break
            if not tried:
                log(f"[tdd] {req_id}: salvage before the foundation put-back: the batch's end state with one file put back "
                    f"at a time ({len(order)} file(s), shared data first), foundation spec(s) {[Path(s).name for s in failing]}")
            tried.append(rel)
            failures = self.restore_failures
            self.restore_sources(state)
            if self.restore_failures > failures:
                break
            if rebuild:
                if late(rel, "its build does not start"):
                    break
                began = clock.now()
                build = build_frontend(self.project_dir, log=log)
                self.note_duration("build", clock.now() - began)
                built = state if build.ok else {}
                if not build.ok:
                    log(f"[tdd] {req_id}: salvage: with {rel} put back the frontend does not build")
                    continue
            if late(rel, "the run of the failing foundation spec(s) does not start"):
                break
            began = clock.now()
            base = run_e2e(self.project_dir, failing, log=log, **until_kw(salvage_end()))
            if base.skipped or getattr(base, "infra", ""):
                break
            self.note_duration("e2e", clock.now() - began, len(failing))
            if not base.ok:
                log(f"[tdd] {req_id}: salvage: with {rel} put back the foundation spec(s) still fail")
                continue
            if late(rel, "the run of the batch's own spec(s) does not start"):
                break
            began = clock.now()
            own = run_e2e(self.project_dir, list(own_specs), log=log, **until_kw(salvage_end()))
            if own.skipped or getattr(own, "infra", ""):
                break
            self.note_duration("e2e", clock.now() - began, len(own_specs))
            if not own.ok:
                log(f"[tdd] {req_id}: salvage: with {rel} put back the foundation spec(s) pass but the batch's own spec fails")
                continue

            again = StepResult(True, "(no earlier green spec)")
            if rest:
                if late(rel, "the regression run of the earlier specs does not start"):
                    break
                began = clock.now()
                again = run_e2e(self.project_dir, rest, log=log, **until_kw(salvage_end()))
                if again.skipped or getattr(again, "infra", ""):
                    break
                self.note_duration("e2e", clock.now() - began, len(rest))
            if partial:

                with_partial = self.with_partial_green(again, partial, phase_end={"end": min(deadline, clock.now() + self.hard_left(HARD_END_FINISH_SEC))})
                if with_partial is None:
                    late(rel, "the partial-green tests' runs do not start", force=True)
                    break
                again = with_partial
            if late(rel, "not kept: its checks ended past it"):
                break
            names = failed_spec_names(again.output) if not again.ok else set()
            if not again.ok and (not names or names & foundation_names):
                log(f"[tdd] {req_id}: salvage: with {rel} put back the regression run of the earlier specs fails "
                    + (f"foundation spec(s) {sorted(names & foundation_names)}" if names else "without naming a spec"))
                continue

            unrun = not_run_spec_names(again.output) if not again.ok else set()
            unrun_foundation = [spec for spec in rest if Path(spec).name in unrun and Path(spec).name in foundation_names]
            if unrun_foundation:
                need = self.suite_est(len(unrun_foundation))
                if clock.now() + need > deadline or self.hard_left(HARD_END_FINISH_SEC) < need:
                    log(f"[tdd] {req_id}: salvage stopped: with {rel} put back the regression run did not run foundation spec(s) "
                        f"{[Path(s).name for s in unrun_foundation]} and there is no time to run them ({int(need)} s needed); not kept")
                    break
                began = clock.now()
                unrun_run = run_e2e(self.project_dir, unrun_foundation, log=log, **until_kw(salvage_end()))
                if unrun_run.skipped or getattr(unrun_run, "infra", ""):
                    break
                self.note_duration("e2e", clock.now() - began, len(unrun_foundation))
                if not unrun_run.ok or run_unfinished(unrun_run):
                    log(f"[tdd] {req_id}: salvage: with {rel} put back the foundation spec(s) the regression run did not run "
                        f"{[Path(s).name for s in unrun_foundation]} do not pass on their own run; not kept")
                    continue
                if late(rel, "not kept: its checks ended past it"):
                    break
                self.record_run(unrun_foundation, True, unrun_run.output)
            unrun_other = [spec for spec in rest if Path(spec).name in unrun and spec not in unrun_foundation]
            self.record_run(failing, True, base.output)
            self.record_run(list(own_specs), True, own.output)
            if rest:
                self.record_run(rest, again.ok, again.output, **unrun_kw(again, self.record_run))
            log(f"[tdd] {req_id}: salvage KEPT: with {rel} put back to the state before this batch the foundation spec(s) "
                f"{[Path(s).name for s in failing]} and the batch's own spec(s) pass; the batch's other files stay; the "
                f"regression run of {len(rest)} earlier spec(s)" + (f" and {len(partial)} partial-green spec(s)" if partial else "")
                + (" is green" if again.ok else f" names non-foundation spec(s) red {sorted(names)} (booked as regression debt)")
                + (f"; {len(unrun_foundation)} foundation spec(s) it did not run passed on their own run" if unrun_foundation else "")
                + (f"; {len(unrun_other)} spec(s) it did not run are owed as an unknown debt" if unrun_other else ""))
            return [Path(spec).name for spec in failing], rel, {"failed": sorted(names), "output": again.output, "own": own,
                                                                "unknown": unrun_other}
        if tried:
            self.restore_sources(end_state)
            log(f"[tdd] {req_id}: salvage found no state to keep ({len(tried)} tried); the batch's end state is back for the put-back")
        return [], "", ""

    def remember_best_state(self, req_id: str, best: dict[str, Any], failed: list[str], test_files: dict[str, str],
                            check_state: dict[str, Any]) -> None:
        if not setting_on("best_batch_state") or not self.large_tree:
            return
        if best and len(failed) > len(best.get("failed") or []):
            return
        best.clear()
        best.update(sources=self.snapshot_sources(), failed=list(failed), tests=dict(test_files), check=int(check_state.get("n", 0)),
                    output=str(check_state.get("regression_output") or ""),
                    own_ran=int(getattr((check_state.get("own") or {}).get("run"), "ran", -1) or -1))
        log(f"[tdd] {req_id}: check {best['check']} is the batch's best state so far (own spec green, no foundation spec red, "
            f"{len(failed)} earlier spec(s) red: {failed[:6]}); kept in case the batch is put back")

    def put_best_state_back(self, req_id: str, best: dict[str, Any], test_files: dict[str, str], batch_before: dict[str, bytes],
                            where: str) -> bool:
        if not best or not setting_on("best_batch_state"):
            return False
        if best.get("tests") != dict(test_files):
            log(f"[tdd] {req_id}: its best state (check {best.get('check')}) was judged by tests that were repaired since; not used")
            return False
        failures_before = self.restore_failures
        restored = self.restore_sources(best["sources"])
        if self.restore_failures > failures_before:
            log(f"[tdd] {req_id}: WARNING -- its best state could not be reproduced; back to the state before this batch")
            self.restore_sources(batch_before)
            return False
        log(f"[tdd] {req_id}: {where} is put back to its best state instead of the state before it ({restored} file(s)): check "
            f"{best.get('check')} had its own spec green, no foundation spec red and {len(best.get('failed') or [])} earlier spec(s) red "
            f"{list(best.get('failed') or [])[:6]}; the batch is kept like one that ends there (regression debt)")
        return True

    REGRESSION_REPAIR_SEC = 900.0

    def repair_regression_debt(self, req_id: str, node: dict[str, Any], test_files: dict[str, str], check, check_state: dict[str, Any],
                               green_at_start: list[str], partial_at_start: dict[str, Any], batch_before: dict[str, bytes],
                               own: list[str], contract: str, relevant: list[str], result: ImplementResult,
                               nodes_left: int) -> ImplementResult | None:
        if not setting_on("regression_repair") or not self.large_tree or not batch_before:
            return None
        store = self.__dict__.setdefault("partial_green", {})
        broken_green = [spec for spec in green_at_start if spec not in self.green_specs]
        broken_partial = {spec: entry for spec, entry in partial_at_start.items() if spec not in store}
        names = sorted({Path(spec).name for spec in broken_green} | {Path(spec).name for spec in broken_partial})
        if not names:
            log(f"[tdd] {req_id}: owes a regression, but no spec left the guard for it; no targeted regression repair")
            return None
        if LLM.quota_exhausted or self.over_budget():
            log(f"[tdd] {req_id}: owes a regression {names}; no targeted repair (budget or key)")
            return None

        need = 2 * self.REGRESSION_REPAIR_SEC + MIN_BATCH_SEC * max(0, nodes_left - 1)
        if self.time_left() < need:
            log(f"[tdd] {req_id}: owes a regression {names}; no targeted repair -- {int(max(0.0, self.time_left()))} s left, {int(need)} s needed")
            return None
        before_repair = self.snapshot_sources()

        before_ev = check_state.get("evidence") if setting_on("regression_partial_keep", True) else None
        before_ev_output = (str(before_ev.get("output") or "") if isinstance(before_ev, dict)
                            and before_ev.get("digest") == sources_digest(before_repair) else "")

        before_counts = ({"ran": before_ev.get("ran"), "regression_ran_count": check_state.get("regression_ran_count"),
                          "regression_ran": list(check_state.get("regression_ran") or [])} if before_ev_output else None)
        own_names = [Path(spec).name for spec in own or []]
        kept_green, kept_partial = list(self.green_specs), dict(store)

        seed_checks = self.__dict__.setdefault("seed_checks", {})
        kept_seed_checks = dict(seed_checks)

        self.green_specs = list(dict.fromkeys([spec for spec in green_at_start if spec in kept_green or spec in broken_green] + kept_green))
        store.update(broken_partial)
        shown = dict(test_files)
        for spec in broken_green + list(broken_partial):
            try:
                shown.setdefault(spec, (self.project_dir / spec).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass

        last = "" if check_state.get("kept_at_best") else str(check_state.get("last") or "")
        if "This batch's own spec PASSES" in last and all(name in last for name in names):
            first_output = last
        else:
            first_output = ("E2E FAILED against the real application\nThis batch's own spec PASSES. What fails are specs of EARLIER "
                            "requirements, which passed before this batch changed the files they depend on: " + ", ".join(names) + ".")
        brief = ("TARGETED REGRESSION REPAIR. This batch's own acceptance spec is GREEN. It broke these earlier, frozen specs: "
                 + ", ".join(names) + " (shown with the tests). Change ONLY what restores their behaviour; keep every behaviour this "
                 "batch's own spec checks. Both are checked together; if anything is red afterwards, this repair is undone.")
        log(f"[tdd] {req_id}: owes a regression {names}; one targeted repair of it ({int(self.REGRESSION_REPAIR_SEC)} s, "
            "kept only when its own spec and the regression run are green)")
        fixer = Implementer(self.llm, self.project_dir, check, max_rounds=1, extra_rounds=0,
                            deadline=self.hard_deadline(clock.now() + self.REGRESSION_REPAIR_SEC, f"{req_id} regression repair"),
                            contract=contract, relevant=relevant, show=result.written, log=log)
        try:
            fix = fixer.implement(node, shown, TestOutcome(False, first_output), extra_context=brief)
        except Exception as exc:
            log(f"[tdd] {req_id}: the targeted regression repair crashed: {exc!r}")
            fix = ImplementResult("gave_up", [], repr(exc))

        covered = set(check_state.get("regression_ran") or []) if fix.status == "passed" else set()
        if fix.status == "passed" and set(names) <= covered:
            if req_id in self.regression_debts:
                self.regression_debts.reverse()
                self.regression_debts.remove(req_id)
                self.regression_debts.reverse()
            log(f"[tdd] {req_id}: the targeted regression repair is GREEN (own spec and {names}); kept, the batch owes nothing")
            return ImplementResult("passed", list(dict.fromkeys(list(result.written) + list(fix.written))),
                                   "own spec green; the regression it caused was repaired by the targeted repair",
                                   result.test_runs + fix.test_runs, last_check=fix.last_check or "E2E PASSED\n(targeted regression repair)")
        if setting_on("regression_partial_keep", True):
            try:
                still = self.regression_partial_still(req_id, names, before_ev_output, check_state, node=node,
                                                      before=before_repair, before_counts=before_counts, own=own_names)
                still_evidence = self.debt_evidence(str(check_state.get("regression_output") or "")) if still is not None else {}
            except Exception as exc:
                log(f"[tdd] {req_id}: the partial-keep decision of the targeted regression repair crashed: {exc!r}")
                still = None
            if still is not None:

                self.green_specs = [spec for spec in self.green_specs if Path(spec).name not in still]
                for spec in broken_partial:
                    if Path(spec).name in still:
                        store.pop(spec, None)
                self.__dict__["regression_partial_kept"] = {
                    "req": req_id, "written": list(fix.written or []), "still": set(still),
                    "evidence": still_evidence}
                log(f"[tdd] {req_id}: the targeted regression repair ended {fix.status} but repaired "
                    f"{sorted(set(names) - still)} with no new failure; kept, the batch still owes {sorted(still)}")
                return None
        failures_before = self.restore_failures
        restored = self.restore_sources(before_repair)
        self.green_specs = kept_green
        store.clear()
        store.update(kept_partial)
        seed_checks.clear()
        seed_checks.update(kept_seed_checks)
        why = "its check did not run " + ", ".join(sorted(set(names) - covered)) if fix.status == "passed" else f"it ended {fix.status}"
        log(f"[tdd] {req_id}: the targeted regression repair is not kept ({why}); {restored} file(s) put back, the batch still owes {names}")
        if self.restore_failures > failures_before:
            log(f"[tdd] {req_id}: WARNING -- putting back the kept state did not reproduce it")
            return ImplementResult("gave_up", list(result.written), "the state kept before the targeted regression repair could not be reproduced",
                                   result.test_runs + fix.test_runs)
        return None

    def regression_partial_still(self, req_id: str, names: list[str], before_output: str, check_state: dict[str, Any],
                                 node: dict[str, Any] | None = None, before: dict[str, bytes] | None = None,
                                 before_counts: dict[str, Any] | None = None, own: list[str] | None = None) -> set[str] | None:
        still = self._regression_partial_still(req_id, names, before_output, check_state)
        if still is None:
            return None
        if not isinstance(before_counts, dict) or not isinstance(before, dict) or not isinstance(node, dict):
            log(f"[tdd] {req_id}: the targeted regression repair is not kept partly: the starting check's counts are not at hand")
            return None
        after = check_state.get("evidence") or {}

        def known(value: Any) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and value > 0

        reg_before, reg_after = before_counts.get("regression_ran_count"), check_state.get("regression_ran_count")
        own_before, own_after = before_counts.get("ran"), after.get("ran")
        why = ""
        if not (known(reg_before) and known(reg_after) and known(own_before) and known(own_after)):
            why = "test counts not known or 0"
        elif reg_after < reg_before or own_after < own_before:
            why = f"fewer tests ran (regression {reg_after} < {reg_before} or own {own_after} < {own_before})"
        else:
            ran_before = set(before_counts.get("regression_ran") or [])
            ran_after = set(check_state.get("regression_ran") or [])
            foundation = {Path(spec).name for spec in self.foundation_specs()}
            if not ran_before or not ran_before <= ran_after:
                why = "specs the starting check ran did not run: " + ", ".join(sorted(ran_before - ran_after)[:4])
            elif (foundation & ran_before) - ran_after:
                why = "foundation specs did not run"
            else:
                skipped_before = spec_skipped_counts(before_output)
                skipped_after = spec_skipped_counts(str(after.get("output") or ""))
                if skipped_before is None or skipped_after is None or any(
                        n > skipped_before.get(name, 0) for name, n in skipped_after.items()):
                    why = "more tests skipped / not run"
        if not why:
            now = self.snapshot_sources()
            why = self.seed_text_regressed(node, before, now)
            if not why:
                earlier = "\n".join((self.project_dir / spec).read_text(encoding="utf-8", errors="replace")
                                    for spec in self.e2e_specs if Path(spec).name not in set(own or [])
                                    and (self.project_dir / spec).is_file())
                gone = lost_stated_names(str(node.get("requirement_text") or ""), before, now, earlier)
                why = f"stated names gone: {gone[:6]}" if gone else ""
        if why:
            log(f"[tdd] {req_id}: the targeted regression repair is not kept partly ({why})")
            return None
        return still

    def _regression_partial_still(self, req_id: str, names: list[str], before_output: str, check_state: dict[str, Any]) -> set[str] | None:
        after = check_state.get("evidence")
        if not isinstance(after, dict) or after.get("digest") != sources_digest(self.snapshot_sources()) or after.get("lint"):
            return None
        marker = "This batch's own spec PASSES"
        if marker not in str(after.get("output") or "") or marker not in str(before_output or ""):
            return None
        if not set(names) <= set(check_state.get("regression_ran") or []) or not check_state.get("regression_output"):
            return None
        old = check_failure_ids({"output": before_output, "passed": False, "specs": list(names)})
        new = check_failure_ids({"output": after.get("output"), "passed": False, "specs": list(names)})
        if old is None or new is None or not new <= old:
            return None
        still = {name for name, _ in new}
        foundation = {Path(spec).name for spec in self.foundation_specs()}
        if not still < set(names) or still & foundation:
            return None
        return still

    def debt_evidence(self, output: str) -> dict[str, Any]:
        return {"output": str(output or ""), "sources": sources_digest(self.snapshot_sources())}

    def debt_net_loss(self, own_ran: int, broken: set[str], evidence: dict[str, Any],
                      preexisting: dict[str, Any] | None = None) -> tuple[int, int] | None:
        if not evidence or not broken or own_ran <= 0 or evidence.get("sources") != sources_digest(self.snapshot_sources()):
            return None
        titles = failed_test_titles(str(evidence.get("output") or ""))
        if any(not titles.get(name) for name in broken):
            return None
        old = preexisting or {}
        return sum(len(set(titles[name]) - set(old.get(name) or ())) for name in broken), int(own_ran)

    def settle_regression_debt(self, req_id: str, own: list[str], seen: dict[str, Any], batch_before: dict[str, bytes],
                               green_at_start: list[str], partial_at_start: dict[str, Any]) -> str | None:
        broken = set(seen.get("broken") or [])
        evidence = dict(seen.get("evidence") or {})
        loss = None
        if setting_on("regression_net_rollback") and self.large_tree and batch_before:
            try:
                loss = self.debt_net_loss(int(seen.get("own_ran", -1)), broken, evidence, dict(seen.get("preexisting") or {}))
            except Exception as exc:
                log(f"[tdd] {req_id}: the regression's cost could not be counted: {exc!r}")
        if loss is not None and loss[0] > loss[1]:
            kept_state = self.snapshot_sources()
            failures_before = self.restore_failures
            restored = self.restore_sources(batch_before)
            if self.restore_failures > failures_before:
                log(f"[tdd] {req_id}: WARNING -- the state before this batch could not be reproduced; the batch stays kept, owing {sorted(broken)}")
                self.restore_sources(kept_state)
            else:
                self.green_specs = list(dict.fromkeys(list(green_at_start) + list(self.green_specs)))
                store = self.__dict__.setdefault("partial_green", {})
                for spec, entry in partial_at_start.items():
                    store.setdefault(spec, entry)
                if req_id in self.regression_debts:
                    self.regression_debts.reverse()
                    self.regression_debts.remove(req_id)
                    self.regression_debts.reverse()
                why = (f"put back: its own spec(s) pass {loss[1]} test(s) but it broke {loss[0]} test(s) of earlier specs "
                       f"{sorted(broken)} that the targeted repair did not restore")
                log(f"[tdd] {req_id}: {why}; {restored} file(s) put back to the state before this batch")
                return why
        self.track_debt_specs(req_id, broken, evidence, partial_at_start)

        unknown = [spec for spec in dict.fromkeys(seen.get("unknown") or []) if spec not in own and Path(spec).name not in broken]
        if unknown:

            self.__dict__.setdefault("unknown_debts", {})[req_id] = unknown
            log(f"[tdd] {req_id}: owes an UNKNOWN regression ("
                + ("its run named no failing spec, twice" if not broken else "specs its regression run did not run")
                + f"); {len(unknown)} candidate spec(s) booked for a confirming run at the closing's first job")
        return None

    def track_debt_specs(self, req_id: str, broken: set[str], evidence: dict[str, Any],
                         partial_before: dict[str, Any] | None = None) -> None:
        ledger = self.__dict__.setdefault("debt_specs", {})
        store = self.__dict__.setdefault("partial_green", {})
        same = bool(evidence) and evidence.get("sources") == sources_digest(self.snapshot_sources())
        titles = failed_test_titles(str(evidence.get("output") or "")) if same else {}
        for spec in dict.fromkeys(list(self.e2e_specs) + list(store)):
            name = Path(spec).name
            if name not in broken:
                continue
            bad = set(titles.get(name) or ())
            ledger[spec] = {"by": req_id, "failed": sorted(bad)}
            earlier = store.get(spec) or (partial_before or {}).get(spec) or {}
            if not bad or not self.large_tree or not setting_on("partial_green_guard"):
                log(f"[tdd] {req_id}: owes {name}, booked for the closing repair (none of its tests stays in the regression checks: "
                    + ("its failing tests cannot be read)" if not bad else "the guard is off)"))
                continue
            try:
                digest = self.spec_digest((self.project_dir / spec).read_bytes())
            except OSError:
                continue
            green = [t for t in (earlier.get("titles") or []) if t not in bad] if earlier.get("titles") else None

            if earlier.get("sha1") == digest:
                bad = bad | set(earlier.get("red") or ())
            if green == []:
                log(f"[tdd] {req_id}: owes {name}, booked for the closing repair (none of its green tests passed the regression run)")
                continue
            written = [rel for rel, specs in (getattr(self, "file_specs", None) or {}).items() if spec in specs]
            store[spec] = {"batch": str(earlier.get("batch") or req_id), "titles": green, "red": sorted(bad), "sha1": digest,
                           "written": list(dict.fromkeys(list(earlier.get("written") or []) + written)), "debt": req_id}
            log(f"[tdd] {req_id}: owes {name} ({len(bad)} test(s) red), booked for the closing repair; its other tests stay in "
                "the regression checks of later batches")

    DEAD_APP_MIN_LEFT_SEC = 300.0

    def dead_app_put_back(self, req_id: str, status: str, before: dict[str, bytes], last_check: str) -> bool:
        if status == "passed" or not before or not any(mark in (last_check or "") for mark in APP_DOWN_MARKS):
            return False
        current = self.snapshot_sources()
        if current == before:
            return False

        short = (self.time_left() < self.DEAD_APP_MIN_LEFT_SEC if self.hard_end() is None
                 else not self.hard_fits(f"{req_id} dead-app probes", 3 * self.build_est() + 2 * PROBE_WORST_SEC))
        if short:
            log(f"[tdd] {req_id}: the last check found the application down; too little time left to probe it, nothing put back")
            return False
        try:
            if not build_frontend(self.project_dir, log=log).ok:
                log(f"[tdd] {req_id}: the last check found the application down, but the sources do not build now; nothing put back")
                return False
            now = probe_server(self.project_dir, log=log)[0].started
        except Exception as exc:
            log(f"[tdd] {req_id}: probing the application failed ({exc!r}); nothing put back")
            return False
        if now is not False:
            log(f"[tdd] {req_id}: the last check found the application down, but " + ("it starts now" if now else "whether it starts is unknown (a slow start, or another process on the port)") + "; nothing put back")
            return False
        digest = hashlib.sha1()
        for rel in sorted(before):
            digest.update(rel.encode("utf-8") + b"\0" + before[rel] + b"\0")
        cache = self.__dict__.setdefault("start_probes", {})
        key = digest.hexdigest()
        if key in cache and cache[key] is not True:
            log(f"[tdd] {req_id}: the application does not start, and it did not start before this batch either (probed before); nothing put back")
            return False
        failures_before = self.restore_failures
        keep = False
        restored = 0
        try:
            restored = self.restore_sources(before)
            if self.restore_failures > failures_before:
                log(f"[tdd] {req_id}: the state before this batch could not be reproduced; nothing put back")
                return False
            if key not in cache:
                cache[key] = None
                log(f"[tdd] {req_id}: the application does not start; probing the sources as they were before this batch")
                cache[key] = probe_server(self.project_dir, log=log)[0].started if build_frontend(self.project_dir, log=log).ok else None
            elif not build_frontend(self.project_dir, log=log).ok:

                log(f"[tdd] {req_id}: the state before this batch started when probed earlier, but it does not build now; nothing put back")
                return False
            keep = cache[key] is True
        except Exception as exc:
            log(f"[tdd] {req_id}: probing the state before this batch failed ({exc!r}); nothing put back")
            keep = False
        finally:
            if not keep:
                back = self.restore_failures
                self.restore_sources(current)
                try:
                    build_frontend(self.project_dir, log=log)
                except Exception as exc:
                    log(f"[tdd] {req_id}: rebuilding the batch's own state failed: {exc!r}")
                if self.restore_failures > back:
                    log(f"[tdd] {req_id}: WARNING -- the batch's own state could not be reproduced after the probe")
        if not keep:
            log(f"[tdd] {req_id}: the application does not start, and it did not start before this batch either; nothing put back" if cache.get(key) is False
                else f"[tdd] {req_id}: whether the application started before this batch is unknown; nothing put back")
            return False
        log(f"[tdd] {req_id}: ends {status} with an application that does not start (it started before this batch); "
            f"{restored} file(s) put back to the state before this batch")
        return True

    BASELINE_MIN_LEFT_SEC = 900.0

    def foundation_baseline(self, req_id: str, batch_before: dict[str, bytes],
                            phase_end: dict[str, Any] | None = None,
                            names: set[str] | None = None) -> dict[str, dict[str, str]] | None:
        cache = self.__dict__.setdefault("foundation_baselines", {})
        digest = hashlib.sha1()
        for rel in sorted(batch_before):
            digest.update(rel.encode("utf-8") + b"\0" + batch_before[rel] + b"\0")
        key = f"{req_id}:{digest.hexdigest()}"
        extra = ([spec for spec in self.foundation_specs() if spec not in self.green_specs[:3] and Path(spec).name in names]
                 if names else [])
        if extra:
            key += "|" + ",".join(sorted(Path(spec).name for spec in extra))
        if key in cache:
            log(f"[tdd] {req_id}: foundation baseline of this batch reused (no second run)")
            return cache[key]
        cache[key] = None
        if self.time_left() < self.BASELINE_MIN_LEFT_SEC:
            log(f"[tdd] {req_id}: too little time left for a foundation baseline; every foundation failure counts")
            return None
        foundation = self.green_specs[:3] + extra
        current = self.snapshot_sources()
        failures_before = self.restore_failures
        baseline: dict[str, dict[str, str]] | None = None
        cut = False
        log(f"[tdd] {req_id}: foundation baseline: running {[Path(s).name for s in foundation]} on the sources as they were before this batch")
        try:
            self.restore_sources(batch_before)
            if self.restore_failures == failures_before and build_frontend(self.project_dir, log=log).ok:
                found: dict[str, dict[str, str]] | None = {}
                for spec in foundation:
                    if self.foundation_run_past_end(req_id, phase_end, f"the baseline run of {Path(spec).name}"):
                        found, cut = None, True
                        break
                    end = (phase_end or {}).get("end")
                    run = run_e2e(self.project_dir, [spec], log=log, **until_kw(end))
                    if end is not None and stopped_unrun(run):
                        found, cut = None, True
                        break
                    if run.skipped:
                        found = None
                        break
                    if not run.ok:

                        found[Path(spec).name] = failed_test_signatures(run.output).get(Path(spec).name, {})
                baseline = found
        finally:
            self.restore_sources(current)
            if self.restore_failures > failures_before:
                baseline = None
            build_frontend(self.project_dir, log=log)
        cache[key] = baseline
        if cut:
            cache.pop(key, None)
        if baseline is None:
            log(f"[tdd] {req_id}: foundation baseline unknown; every foundation failure counts")
        else:
            log(f"[tdd] {req_id}: foundation baseline: " + ("; ".join(f"{name} red ({len(titles)} test(s))" for name, titles in sorted(baseline.items())) or "all green"))
        return baseline

    def new_foundation_failures(self, req_id: str, batch_before: dict[str, bytes], failing: dict[str, str],
                                let_off: dict[str, list[str]] | None = None,
                                phase_end: dict[str, Any] | None = None) -> list[str]:
        now = {name: failed_test_signatures(output).get(name, {}) for name, output in failing.items()}
        if not any(sig for sigs in now.values() for sig in sigs.values()):
            return sorted(now)
        if not self.regression_specs(batch_before, []):
            log(f"[tdd] {req_id}: the batch changed no existing file; no foundation baseline, every foundation failure counts")
            return sorted(now)

        first = {Path(spec).name for spec in list(self.__dict__.get("green_specs") or [])[:3]}
        protected = {Path(spec).name for spec in self.protected_specs()} - first
        extra_names = {name for name in now if name in protected}
        if extra_names:
            baseline = self.foundation_baseline(req_id, batch_before, names=extra_names,
                                                **({"phase_end": phase_end} if phase_end is not None else {}))
        else:
            baseline = (self.foundation_baseline(req_id, batch_before, phase_end=phase_end) if phase_end is not None
                        else self.foundation_baseline(req_id, batch_before))
        if baseline is None:
            return sorted(now)
        new = []
        for name, sigs in now.items():
            before = baseline.get(name) or {}

            changed = sorted(title for title, sig in sigs.items()
                             if title in before and (not sig or " @ " not in sig or before.get(title) != sig))
            if let_off is not None:
                same = sorted(title for title, sig in sigs.items() if sig and " @ " in sig and before.get(title) == sig)
                if same:
                    let_off[name] = same
            if not sigs or changed or not set(sigs) <= set(before):
                new.append(name)
            if changed:
                log(f"[tdd] {req_id}: {name}: {changed[:4]} failed before this batch too, but now with another (or an unreadable) "
                    "first error: counted as a new failure")
        old = sorted(set(now) - set(new))
        if old:
            log(f"[tdd] {req_id}: pre-existing foundation failure in {', '.join(old)}: the same tests failed before this batch began; not blamed on it")
        return sorted(new)

    def foundation_regression_is_new(self, req_id: str, batch_before: dict[str, bytes], regression_state: dict[str, Any]) -> bool:
        failed = set(regression_state.get("failed") or [])
        regression_state["preexisting"] = {}
        if not failed:
            return True
        foundation = {Path(spec).name for spec in self.foundation_specs()} & failed
        if not foundation:
            return True

        protected = {Path(spec).name for spec in self.protected_specs()}
        extra = sorted(foundation & protected)
        if extra:
            end = self.__dict__.get("confirm_window_end")
            passed_alone: set[str] = set()
            unconfirmed: set[str] = set()
            for spec in [spec for spec in self.foundation_specs() if Path(spec).name in extra]:
                name = Path(spec).name
                need = self.suite_est(1)
                if (end is not None and clock.now() + need > float(end)) or self.hard_left(HARD_END_FINISH_SEC) < need:
                    unconfirmed.add(name)
                    continue

                got = self.failing_alone(req_id, [spec], {name}, phase_end={"end": float(end), "name": "batch's window"}
                                         if end is not None else None)
                if got is None:
                    unconfirmed.add(name)
                elif not got:
                    passed_alone.add(name)
            if passed_alone:
                log(f"[continuation] {req_id}: earlier-stage spec(s) {sorted(passed_alone)} failed in the regression run but "
                    "pass alone on this state; not this batch's break (they stay in the guard)")
                regression_state["failed"] = sorted(failed - passed_alone)
                foundation -= passed_alone
            if unconfirmed:
                log(f"[continuation] {req_id}: earlier-stage spec(s) {sorted(unconfirmed)} failed in the regression run and could "
                    "not be run alone inside the batch's window: unconfirmed -- not blamed on this batch, and an ordinary "
                    "earlier spec for it (not foundation)")
                foundation -= unconfirmed
            if not foundation:
                return False
        output = str(regression_state.get("output") or "")

        let_off: dict[str, list[str]] = {}
        regression_state["preexisting"] = let_off
        return bool(self.new_foundation_failures(req_id, batch_before, {name: output for name in sorted(foundation)}, let_off))

    def failing_alone(self, req_id: str, foundation: list[str], named: set[str],
                      phase_end: dict[str, Any] | None = None) -> list[tuple[str, str]] | None:
        confirmed: list[tuple[str, str]] = []
        for spec in (s for s in foundation if Path(s).name in named):
            if self.foundation_run_past_end(req_id, phase_end, f"the run alone of {Path(spec).name}"):
                return None
            end = (phase_end or {}).get("end")
            alone = run_e2e(self.project_dir, [spec], log=log, **until_kw(end))
            if end is not None and stopped_unrun(alone):

                log(f"[tdd] {req_id}: the run alone of {Path(spec).name} was stopped at the phase's end; nothing is blamed")
                return None
            if alone.skipped:
                continue
            self.record_run([spec], alone.ok, alone.output, **unrun_kw(alone, self.record_run))
            if not alone.ok:
                confirmed.append((Path(spec).name, alone.output))
        if not confirmed:
            log(f"[tdd] {req_id}: foundation failures ({', '.join(sorted(named))}) do not repeat alone; not blamed on this batch")
        return confirmed

    def foundation_note_while_red(self, req_id: str, own: list[str], before: dict[str, bytes],
                                  check_state: dict[str, Any] | None = None, own_output: str = "",
                                  phase_end: dict[str, Any] | None = None) -> str:

        foundation = self.foundation_run_list(own, self.CONTINUATION_NOTE_RUN_SEC, f"{req_id}: the foundation run of a red check")
        if not foundation or not self.regression_specs(before, own):
            return ""
        if own_output and setting_on("timesave_down_no_foundation") and any(mark in own_output for mark in APP_DOWN_MARKS):

            log(f"[tdd] {req_id}: the application did not start in this check; no foundation run (it could not blame anything)")
            return ""
        if check_state is not None:
            unchanged = self.foundation_unchanged(req_id, foundation, before, check_state)
            foundation = [spec for spec in foundation if spec not in unchanged]
            if not foundation:
                return ""

        done = self.foundation_checks.get(req_id, 0)
        if done >= self.FOUNDATION_CHECKS_MAX:
            return ""
        if self.foundation_run_past_end(req_id, phase_end, "its foundation run"):
            return ""
        self.foundation_checks[req_id] = done + 1
        group = run_e2e(self.project_dir, foundation, log=log, **until_kw((phase_end or {}).get("end")))
        if group.skipped:
            return ""
        if check_state is not None:
            self.note_foundation_run(check_state, foundation, group)
            self.note_green_reference(check_state, foundation, group)
        if group.ok:
            return ""
        named = failed_spec_names(group.output)
        if not named:
            log(f"[tdd] {req_id}: the foundation run failed without naming a spec; not blamed on this batch")
            return ""
        if self.foundation_run_past_end(req_id, phase_end, "the runs alone of its failing foundation specs"):
            return ""
        confirmed = (self.failing_alone(req_id, foundation, named, phase_end=phase_end) if phase_end is not None
                     else self.failing_alone(req_id, foundation, named))
        if not confirmed:
            return ""
        if self.foundation_run_past_end(req_id, phase_end, "the foundation baseline"):
            return ""

        new = set(self.new_foundation_failures(req_id, before, dict(confirmed), phase_end=phase_end) if phase_end is not None
                  else self.new_foundation_failures(req_id, before, dict(confirmed)))
        if self.foundation_run_past_end(req_id, phase_end, "the rest of the foundation baseline"):
            return ""
        confirmed = [(name, output) for name, output in confirmed if name in new]
        if not confirmed:
            return ""
        names = [name for name, _ in confirmed]
        log(f"[tdd] {req_id}: own spec red AND the foundation specs fail now ({', '.join(names)}); shown to the implementer")
        details = "\n".join(output[-1500:] for _, output in confirmed)

        evidence = self.break_evidence([spec for spec in foundation if Path(spec).name in names], before, check_state,
                                       [output for _, output in confirmed])
        return (FOUNDATION_MARK + " the FOUNDATION specs of EARLIER requirements (" + ", ".join(names) + ") passed before "
                "this batch and fail now, each on its own with fresh data -- your changes broke them. They are correct and "
                "frozen. Fix this batch AND restore their behaviour: a batch that ends red while the foundation is broken is "
                "rolled back entirely, and everything it wrote is lost.\n" + evidence + details)

    def note_green_reference(self, check_state: dict[str, Any] | None, specs: list[str], run: Any) -> None:
        if check_state is None or not specs or getattr(run, "skipped", False) or not setting_on("break_evidence"):
            return
        ok = bool(getattr(run, "ok", False))
        output = str(getattr(run, "output", "") or "")
        named = set() if ok else failed_spec_names(output)
        if not ok and not named:
            return
        unrun = re.search(r"(?m)^NOT RUN SPEC FILES: (.+)$", output)
        not_run = {name.strip() for name in unrun.group(1).split(",")} if unrun else set()
        unfinished = ok and run_unfinished(run)
        green = [spec for spec in specs if ((ok and not unfinished) or (named and Path(spec).name not in named))
                 and Path(spec).name not in not_run]
        if not green:
            return
        snapshot = self.snapshot_sources()
        book = check_state.setdefault("green_ref", {})
        for spec in green:
            book[spec] = (int(check_state.get("n", 0) or 0), snapshot)

    def break_evidence(self, specs: list[str], before: dict[str, bytes], check_state: dict[str, Any] | None,
                       outputs: list[str]) -> str:
        if not setting_on("break_evidence") or not specs:
            return ""
        try:
            book = (check_state or {}).get("green_ref") or {}
            refs = [book.get(spec) for spec in specs]
            if before and any(ref is None for ref in refs):
                label, reference = "when this batch began (they passed then)", before
            elif refs and all(ref is not None for ref in refs):
                n, reference = min(refs, key=lambda ref: ref[0])
                label = f"at check {n} of this batch, where they last passed"
            else:
                label, reference = "", {}
            errors: list[str] = []
            for output in outputs:
                errors += [line for line in first_error_lines(output, limit=4) if line not in errors]
            text = ""
            if errors:
                text += "FIRST ERRORS OF THE BROKEN SPECS:\n" + "\n".join("  " + line for line in errors[:8]) + "\n"
            if reference:
                covered = sorted({rel for spec in specs for rel in self.foundation_covered(spec)})
                now = self.snapshot_sources()

                shared = [rel for rel in sorted(set(reference) | set(now))
                          if reference.get(rel) != now.get(rel) and SHARED_DATA_FILE.search(rel)]
                changed, diff = change_digest(reference, now, first=shared + [rel for rel in covered if rel not in shared])
                shared = [rel for rel in shared if rel in changed]
                changed = shared + [rel for rel in changed if rel not in shared]
                if changed:
                    text += (f"FILES THIS BATCH CHANGED SINCE THOSE SPECS LAST PASSED ({label}): {', '.join(changed[:20])}"
                             + (" ..." if len(changed) > 20 else "") + "\n"
                             + (f"SHARED DATA: {', '.join(shared)} hold{'s' if len(shared) == 1 else ''} data that the tests "
                                "of earlier requirements rely on (seed / init data / fixtures): check that the rows / "
                                "accounts / values those tests use are unchanged before changing other code.\n" if shared else "")
                             + "Their diff (" + ("shared data files first, then " if shared else "")
                             + ("files the broken specs' own batches wrote come first" if not shared else
                                "the files the broken specs' own batches wrote")
                             + "; '-' = a line removed or replaced, '+' = a line added):\n" + diff + "\n")
            if text and check_state is not None:
                check_state["break_evidence"] = text
            return text
        except Exception as exc:
            log(f"[tdd] break evidence not built: {exc!r}")
            return ""

    ATTEMPT_NOTE_CAP = 5000
    RESTART_REASONS = ("stuck", "seed-conflict", "gave_up")

    AUDIT_LATE_WINDOW_SEC = 480.0

    AUDIT_BOUND_SHARE_SEC = MIN_BATCH_SEC

    def calls_bounded(self) -> bool:
        return hasattr(Implementer, "call_deadline") or hasattr(Implementer, "_past_call_deadline")

    def set_call_deadline(self, implementer: Any, when: float) -> None:
        try:
            setattr(implementer, "call_deadline", float(when))
        except Exception as exc:
            log(f"[tdd] call_deadline not set: {exc!r}")

    def later_share(self, nodes_left: int) -> float | None:
        later = int(nodes_left) - 1
        if later < 1:
            return None
        return self.time_left() * 0.8 / later

    def audit_repair_bound(self, req_id: str, status: str, deadline: float, repair_end: float, nodes_left: int) -> float | None:
        if not setting_on("audit_repair_bound"):
            return None
        share = self.later_share(nodes_left)
        if share is None or share >= self.AUDIT_BOUND_SHARE_SEC:
            return None
        now = clock.now()
        if now + self.AUDIT_LATE_WINDOW_SEC <= deadline:
            bound = repair_end
        else:
            window = max(0.0, min(self.AUDIT_LATE_WINDOW_SEC, share))
            bound = min(repair_end, max(deadline, now + window))
        log(f"[audit] {req_id}: the run is short of time (a fair share of {int(max(0.0, share))} s for each of the "
            f"{int(nodes_left) - 1} batch(es) still to start, under {int(self.AUDIT_BOUND_SHARE_SEC)} s); the batch ended "
            f"{status or 'red'}; the audited repair gets {int(max(0.0, bound - now))} s -- every round and every model call "
            "of it (and a test author's hand-over after it) ends by then")
        return bound

    UNFINISHED_MARK = "CHECK UNFINISHED"

    @staticmethod
    def phase_passed(phase_end: dict[str, Any] | None) -> bool:
        end = (phase_end or {}).get("end")
        return end is not None and clock.now() >= float(end)

    def phase_unfinished(self, req_id: str, phase_end: dict[str, Any], count: int) -> TestOutcome | None:
        end = phase_end.get("end")
        if end is None or clock.now() < float(end):
            return None
        name = str(phase_end.get("name") or "phase")
        log(f"[tdd] {req_id}: the {name}'s end has passed; no regression run starts past it ({count} earlier spec(s) not run) "
            "-- this check is UNFINISHED: no regression is booked, the state is unverified")
        outcome = TestOutcome(False, f"{self.UNFINISHED_MARK}: this batch's own specs ran, but the {name}'s time ended before the "
                              "regression run of earlier specs could start. The state is unverified; it is no evidence of a "
                              "regression and no evidence of a green batch.")
        outcome.stop = f"check unfinished: the {name}'s end passed before its regression run"
        return outcome

    def handover_past_end(self, req_id: str, end: float | None, what: str) -> bool:
        if end is None or clock.now() < float(end):
            return False
        log(f"[audit] {req_id}: the audited repair's phase ended {int(clock.now() - float(end))} s ago; {what} is not done "
            "(booked as not done: the batch keeps its state, nothing is booked as a regression)")
        return True

    def calls_end_by(self, end: float | None) -> "_CallsEndBy":
        return _CallsEndBy(self, end)

    def attempt_note(self, result: Any, check_state: dict[str, Any], files: list[str], put_back: bool) -> str:
        if not setting_on("attempt_note"):
            return ""
        try:
            status = str(getattr(result, "status", "") or "red")
            reason = str(getattr(result, "stop_reason", "") or "")
            detail = " ".join(str(getattr(result, "detail", "") or "").split())[:300]
            lines = ["PREVIOUS ATTEMPT AT THIS BATCH: it ended red, and you are a fresh attempt on the same frozen tests. "
                     "Do not repeat what it did where it failed; take a different approach there.",
                     f"How it ended: {status}" + (f" ({reason})" if reason and reason != status else "") + (f" -- {detail}" if detail else "")
                     + (". Its changes were put back: you start from the sources as they were "
                     "before it." if put_back else ". Its changes are still on disk: you continue from the sources as it left them.")]
            last = own_output(str(check_state.get("last_red") or ""))
            errors = first_error_lines(last, limit=6) if last.strip() else []
            if errors:
                lines.append("Its last failing check, first errors:\n" + "\n".join("  " + line for line in errors))
            if files:
                lines.append("Files it changed: " + ", ".join(list(dict.fromkeys(files))[:30]))
            evidence = str(check_state.get("break_evidence") or "")
            if evidence:
                lines.append("It broke the specs of EARLIER requirements; the evidence shown to it then:\n" + evidence[:2200])
            claims = list(check_state.get("rejected_claims") or [])
            claims = list(dict.fromkeys((str(c), str(w)) for c, w in claims))[-3:]
            if claims:
                lines.append("TEST DEFECT claims it made that were REJECTED (a claim like these is rejected again unless it "
                             "brings what the reason names):\n" + "\n".join(f"  - {claim!r} -- rejected: {why}" for claim, why in claims))
            restart = str(check_state.get("restart_summary") or "")
            if restart:
                lines.append(restart)
            return "\n".join(lines)[: self.ATTEMPT_NOTE_CAP]
        except Exception as exc:
            log(f"[tdd] attempt note not built: {exc!r}")
            return ""

    def arm_retry(self, implementer: Any, note: str) -> None:
        try:
            if note:
                setattr(implementer, "attempt_note", note)
            temperature = retry_temperature()
            if temperature > 0:
                setattr(implementer, "temperature", temperature)
        except Exception as exc:
            log(f"[tdd] retry attributes not set: {exc!r}")

    def restart_reason(self, req_id: str, result: Any, retry: bool, share_start: float, deadline: float,
                       node: dict[str, Any] | None = None) -> str:
        if retry or not setting_on("in_batch_restart") or str(getattr(result, "status", "")) == "passed":
            return ""
        if req_id in list(self.__dict__.get("foundation_retried") or []) + list(self.__dict__.get("hub_retried") or []):
            return ""

        if node is not None and self.retry_kind(node, set(self.__dict__.get("foundation_ids") or ())):
            return ""
        reason = str(getattr(result, "stop_reason", "") or "")
        if reason not in self.RESTART_REASONS:
            reason = "gave_up" if str(getattr(result, "status", "")) == "gave_up" else ""
        if not reason or LLM.quota_exhausted or self.over_budget():
            return ""
        share = max(0.0, deadline - share_start)
        left = deadline - clock.now()
        if self.calls_bounded():

            need = max(MIN_BATCH_SEC / 2, 0.4 * share)
            if left < need:
                log(f"[tdd] {req_id}: the implementer stopped red early ({reason}); no in-batch restart -- {int(max(0.0, left))} s "
                    f"of the batch's {int(share)} s left, {int(need)} s needed")
                return ""
            return reason

        from agent import llm as llm_module
        call_limit = float(getattr(llm_module, "CALL_LIMIT_SEC", 0.0) or 0.0)
        if call_limit <= 0:
            log(f"[tdd] {req_id}: the implementer stopped red early ({reason}); no in-batch restart -- a model call has no "
                "time limit of its own (llm_call_limit_sec), the restart could not be kept inside the batch's share")
            return ""
        need = max(MIN_BATCH_SEC / 2, 0.4 * share) + call_limit
        if left < need:
            log(f"[tdd] {req_id}: the implementer stopped red early ({reason}); no in-batch restart -- {int(max(0.0, left))} s "
                f"of the batch's {int(share)} s left, {int(need)} s needed")
            return ""
        return reason

    CHASE_REDS_MAX = 2
    API_MUTE_REDS = 2

    def foundation_covered(self, spec: str) -> list[str]:
        return sorted(rel for rel, specs in self.file_specs.items() if spec in specs)

    def covered_state(self, files: list[str]) -> dict[str, bytes | None]:
        state: dict[str, bytes | None] = {}
        for rel in files:
            try:
                state[rel] = (self.project_dir / rel).read_bytes()
            except OSError:
                state[rel] = None
        return state

    def foundation_unchanged(self, req_id: str, specs: list[str], before: dict[str, bytes], check_state: dict[str, Any]) -> list[str]:
        if not setting_on("timesave_foundation_filter") or not self.large_tree or not before:
            return []
        book = check_state.setdefault("foundation_seen", {})
        unchanged: list[str] = []
        for spec in specs:
            files = self.foundation_covered(spec)
            entry = book.get(spec)
            if not files or (entry is not None and entry.get("red")):
                continue
            seen = entry["files"] if entry is not None else {rel: before.get(rel) for rel in files}
            if set(seen) == set(files) and self.covered_state(files) == seen:
                unchanged.append(spec)
        if unchanged:
            log(f"[tdd] {req_id}: foundation spec(s) {[Path(s).name for s in unchanged]} not run: none of the files they cover "
                "changed since they last ran green")
        return unchanged

    def note_foundation_run(self, check_state: dict[str, Any], specs: list[str], run: Any) -> None:
        book = check_state.setdefault("foundation_seen", {})
        named = set() if run.ok else failed_spec_names(run.output)

        unrun = re.search(r"(?m)^NOT RUN SPEC FILES: (.+)$", str(getattr(run, "output", "") or ""))
        not_run = {name.strip() for name in unrun.group(1).split(",")} if unrun else set()
        unfinished = bool(run.ok) and run_unfinished(run)
        for spec in specs:
            green = bool((run.ok and not unfinished) or (named and Path(spec).name not in named)) and Path(spec).name not in not_run
            book[spec] = {"red": not green, "files": self.covered_state(self.foundation_covered(spec))}

    def foundation_run_past_end(self, req_id: str, phase_end: dict[str, Any] | None, what: str) -> bool:
        if phase_end is None or not self.phase_passed(phase_end):
            return False
        log(f"[tdd] {req_id}: the {phase_end.get('name') or 'phase'}'s end has passed; {what} does not start past it "
            "(this red check shows no foundation break)")
        return True

    def settle_unnamed_regression(self, req_id: str, regression: list[str], run: Any,
                                  phase_end: dict[str, Any] | None = None) -> tuple[Any, bool | None]:
        from agent.verify import NOT_RUN_HEAD

        if run.ok or run.skipped or failed_spec_names(run.output) or not setting_on("timesave_unnamed_inconclusive"):
            return run, False
        unrun = re.search("^" + re.escape(NOT_RUN_HEAD) + r"(.+)$", run.output, re.MULTILINE)
        names = {name.strip() for name in unrun.group(1).split(",")} if unrun else set()
        again = [spec for spec in regression if Path(spec).name in names] or list(regression)
        if phase_end is not None and self.phase_passed(phase_end):
            return run, None
        log(f"[tdd] {req_id}: the regression run failed without naming a spec ({run.output.strip().splitlines()[0][:120] if run.output.strip() else 'no output'}); "
            f"running {len(again)} of its spec(s) once more")
        second = run_e2e(self.project_dir, again, log=log, **until_kw((phase_end or {}).get("end")))
        if not second.skipped:
            self.record_run(again, second.ok, second.output, **unrun_kw(second, self.record_run))
        if not second.skipped and (second.ok or failed_spec_names(second.output)):
            return second, False

        log(f"[tdd] {req_id}: the regression run named no failing spec twice; inconclusive -- not a foundation break (no "
            "rollback for it), but not green either: the batch owes it")
        tail = str(getattr(second, "output", "") or "")[-3000:] if not second.skipped else "(the second run was skipped)"
        return StepResult(False, "(regression run inconclusive: it named no failing spec, twice -- the earlier specs "
                                 "could not be shown to pass)\n" + tail), True

    def chase_streak(self, req_id: str, check_state: dict[str, Any], ran: list[str], failed: set[str], output: str = "") -> str:
        streaks = check_state.setdefault("chase_streaks", {})
        marks = check_state.setdefault("chase_marks", {})
        signatures = failed_test_signatures(output) if output and failed else {}
        for name in ran:
            if name not in failed:
                streaks[name] = 0
                marks.pop(name, None)
        for name in failed:
            found = signatures.get(name) or {}
            mark = "\n".join(f"{title}\t{sign}" for title, sign in sorted(found.items())) if found and all(found.values()) else ""
            if not mark:
                streaks[name] = 0
            elif marks.get(name) == mark:
                streaks[name] = streaks.get(name, 0) + 1
            else:
                streaks[name] = 1
            marks[name] = mark
        due = sorted(name for name, count in streaks.items() if count >= self.CHASE_REDS_MAX)
        if not due or not check_state.get("chase_armed") or not setting_on("timesave_chase_stop"):
            return ""
        log(f"[tdd] {req_id}: {due} red in {self.CHASE_REDS_MAX} regression runs in a row with the own spec green; "
            "no further round chases it: no progress between them (the same failing tests with the same errors) (the batch "
            "ends as its rules say: kept owing it, or put back for the foundation)")
        return f"the same earlier spec(s) {due} failed {self.CHASE_REDS_MAX} regression runs in a row with the same tests and errors"

    def chase_unknown(self, check_state: dict[str, Any], names: list[str]) -> None:
        streaks = check_state.setdefault("chase_streaks", {})
        marks = check_state.setdefault("chase_marks", {})
        for name in names:
            streaks[name] = 0
            marks.pop(name, None)

    def api_files_red(self, output: str, files: list[str]) -> set[str] | None:
        lines = [line for line in strip_terminal_codes(output).splitlines() if re.match(r"\s*(?:FAIL\b|❯|×)", line)]
        red = {rel for rel in files if any(rel.split("/", 1)[-1] in line for line in lines)}
        return red or None

    def advisory_api_files(self, req_id: str, advisory: list[str], own: list[str]) -> list[str]:
        if not setting_on("timesave_api_mute") or not self.large_tree:
            return list(advisory)
        streaks = self.__dict__.setdefault("api_red_streaks", {})

        once_green = self.__dict__.setdefault("api_once_green", set())
        muted_at = self.__dict__.setdefault("api_muted_at", {})
        muted = [p for p in advisory if p not in own and p not in once_green and streaks.get(p, 0) >= self.API_MUTE_REDS]
        if muted:
            lifted = []

            cache: dict[str, Any] = {}
            budget = {"reads": self.API_RECHECK_READS_MAX, "until": time.monotonic() + self.API_RECHECK_SEC, "out": False}
            unchecked: list[str] = []
            for p in muted:
                if budget["out"]:
                    unchecked.append(p)
                    continue
                related = self.api_related_digest(p, cache=cache, budget=budget)
                if budget["out"]:
                    unchecked.append(p)
                    continue
                if not muted_at.get(p):
                    muted_at[p] = related
                elif related and muted_at[p] != related:
                    lifted.append(p)
            for p in lifted:
                streaks[p] = self.API_MUTE_REDS - 1
                muted_at.pop(p, None)
                self.__dict__.setdefault("api_muted_told", set()).discard((req_id, p))
            if lifted:
                log(f"[tdd] {req_id}: advisory API test file(s) {lifted} were muted; sources they import changed since -- run again")
            if unchecked:
                log(f"[tdd] {req_id}: re-checking the imports of muted advisory API test files stopped at its budget "
                    f"({self.API_RECHECK_READS_MAX} file reads / {int(self.API_RECHECK_SEC)} s per check); {len(unchecked)} stay "
                    "muted unchecked this time")
            muted = [p for p in muted if p not in lifted]
        told = self.__dict__.setdefault("api_muted_told", set())
        new = [p for p in muted if (req_id, p) not in told]
        if new:
            told.update((req_id, p) for p in new)
            log(f"[tdd] {req_id}: advisory API test file(s) {new} of earlier batches were red in their last {self.API_MUTE_REDS} runs; "
                "not run (nor shown) any more")
        return [p for p in advisory if p not in muted]

    API_RELATED_MAX = 400
    API_RECHECK_READS_MAX = 2000
    API_RECHECK_SEC = 15.0

    def api_related_digest(self, rel: str, cache: dict[str, Any] | None = None, budget: dict[str, Any] | None = None) -> str:
        pattern = re.compile(r"""(?:require\s*\(\s*|import\s*\(\s*|\bfrom\s+|^\s*import\s+)['"](\.{1,2}/[^'"]+)['"]""", re.MULTILINE)
        root = self.project_dir.resolve()
        start = (self.project_dir / rel).resolve()
        cache = {} if cache is None else cache

        class OutOfBudget(Exception):
            pass

        def node(path: Path) -> tuple[bytes, list[Path]] | None:
            key = path.as_posix()
            if key not in cache:
                if budget is not None:
                    if budget["reads"] <= 0 or time.monotonic() >= budget["until"]:
                        budget["out"] = True
                        raise OutOfBudget(key)
                    budget["reads"] -= 1
                try:
                    content = path.read_bytes()
                    cache[key] = (content, self.api_imports(path, pattern, content.decode("utf-8", errors="replace")))
                except OSError:
                    cache[key] = None
            return cache[key]

        try:
            first = node(start)
            if first is None:
                return ""
            queue = list(first[1])
            seen: dict[str, bytes] = {}
            while queue and len(seen) < self.API_RELATED_MAX:
                path = queue.pop(0)
                key = path.as_posix()
                if key in seen or root not in path.parents:
                    continue
                found = node(path)
                if found is None:
                    continue
                seen[key] = found[0]
                queue += found[1]
        except OutOfBudget:
            return ""
        return sources_digest({str(Path(k).relative_to(root).as_posix()): v for k, v in seen.items()}) if seen else ""

    @staticmethod
    def api_imports(path: Path, pattern: re.Pattern[str], text: str | None = None) -> list[Path]:
        found: list[Path] = []
        for spec in pattern.findall(path.read_text(encoding="utf-8", errors="replace") if text is None else text):
            base = (path.parent / spec).resolve()
            for candidate in [base] + [base.with_name(base.name + ext) for ext in (".js", ".ts", ".mjs", ".cjs", ".json")] \
                    + [base / ("index" + ext) for ext in (".js", ".ts", ".mjs", ".cjs")]:
                if candidate.is_file():
                    found.append(candidate)
                    break
        return found

    def api_partly_green(self, output: str, files: list[str]) -> set[str]:
        found: set[str] = set()
        for line in strip_terminal_codes(output).splitlines():
            counts = re.search(r"\((\d+) tests? \| (\d+) failed", line)
            if counts and int(counts.group(2)) < int(counts.group(1)):
                found.update(rel for rel in files if rel.split("/", 1)[-1] in line)
        return found

    def note_api_run(self, files: list[str], own: list[str], outcome: Any) -> None:
        streaks = self.__dict__.setdefault("api_red_streaks", {})
        once_green = self.__dict__.setdefault("api_once_green", set())
        if outcome.passed:
            for rel in files:
                streaks[rel] = 0
            once_green.update(files)
            return
        red = self.api_files_red(outcome.output, files)
        if red is None:
            return
        once_green.update(rel for rel in files if rel not in red)
        once_green.update(self.api_partly_green(outcome.output, files))
        for rel in files:
            if rel in own:
                continue
            streaks[rel] = streaks.get(rel, 0) + 1 if rel in red else 0

        muted_at = self.__dict__.setdefault("api_muted_at", {})
        newly = [rel for rel in files if rel not in own and streaks.get(rel, 0) >= self.API_MUTE_REDS and rel not in muted_at]
        if newly and self.large_tree and setting_on("timesave_api_mute"):
            for rel in newly:
                muted_at[rel] = self.api_related_digest(rel)

    def regression_specs(self, before: dict[str, bytes], own: list[str], pool: list[str] | None = None) -> list[str]:
        changed: list[str] = []
        for rel, content in before.items():
            try:
                if (self.project_dir / rel).read_bytes() != content:
                    changed.append(rel)
            except OSError:
                changed.append(rel)
        if not changed:
            return []
        direct = {spec for rel in changed for spec in self.file_specs.get(rel, [])}

        green = list(self.green_specs) if pool is None else list(pool)
        ordered = [spec for spec in green if spec in direct] + [spec for spec in self.green_specs[:3] if spec in green] + green
        return [spec for spec in dict.fromkeys(ordered) if spec not in own][: self.REGRESSION_SPECS_MAX]

    REGRESSION_WINDOW_SEC = CONTINUATION_NOTE_RUN_SEC

    def inherited_spec_files(self) -> dict[str, set[str]]:
        cached = self.__dict__.get("_inherited_spec_files")
        if isinstance(cached, dict):
            return cached
        out: dict[str, set[str]] = {}
        try:
            handoff = self.__dict__.get("_earlier_handoff") or {}
            nodes = (self.__dict__.get("_inherited_summary_nodes") if isinstance(self.__dict__.get("_inherited_summary_nodes"), dict)
                     else (json.loads((self.project_dir / ".arc" / "agent-summary.json").read_text(encoding="utf-8")) or {}).get("nodes"))
            if isinstance(handoff, dict) and isinstance(nodes, dict):
                batch_of = {str(r.get("id")): str(r.get("batch") or r.get("id")) for r in handoff.get("leaves") or [] if isinstance(r, dict)}
                for name, leaves in evolution._handoff_spec_leaves(handoff).items():
                    for leaf in leaves:
                        entry = nodes.get(batch_of.get(leaf, leaf))
                        if isinstance(entry, dict):
                            out.setdefault(name, set()).update(str(p) for p in entry.get("written") or [] if isinstance(p, str))
        except (OSError, ValueError, AttributeError):
            out = {}
        self._inherited_spec_files = out
        return out

    def regression_split(self, req_id: str, regression: list[str], before: dict[str, bytes]) -> tuple[list[str], list[str]] | None:
        if not setting_on("regression_window") or not regression:
            return None
        protected = set(self.protected_specs())
        if not protected or not any(spec in protected for spec in regression):
            return None
        changed = set()
        for rel, content in (before or {}).items():
            try:
                if (self.project_dir / rel).read_bytes() != content:
                    changed.add(rel)
            except OSError:
                changed.add(rel)
        touched = {name for name, files in self.inherited_spec_files().items() if files & changed}
        direct = {spec for rel in changed for spec in self.file_specs.get(rel, [])}
        must = [spec for spec in regression if spec not in protected or spec in direct or Path(spec).name in touched
                or spec in self.green_specs[:3]]
        others = [spec for spec in regression if spec not in must]
        size = len(others)
        while size > self.CONTINUATION_SAMPLE_MIN and self.suite_est(len(must) + size) > self.REGRESSION_WINDOW_SEC:
            size -= 1
        if size >= len(others):
            return None
        cursor = int(self.__dict__.get("regression_cursor", 0) or 0) % len(others)
        window = (others[cursor:] + others[:cursor])[:size]
        self.regression_cursor = (cursor + size) % len(others)
        first = [spec for spec in regression if spec in must or spec in window]
        rest = [spec for spec in regression if spec not in first]
        log(f"[tdd] {req_id}: regression in two steps -- first {len(first)} spec(s) ({len(must)} this round touched / its "
            f"own / the first flows, {len(window)} in a rotating window of the protected ones), then the other {len(rest)} "
            "only when those are green (the full set before any green verdict)")
        return first, rest

    def run_regression(self, req_id: str, regression: list[str], before: dict[str, bytes], **until: Any) -> StepResult:
        split = self.regression_split(req_id, regression, before)
        if split is None:
            return run_e2e(self.project_dir, regression, log=log, **until)
        first, rest = split
        head = run_e2e(self.project_dir, first, log=log, **until)
        rest_names = [Path(spec).name for spec in rest]

        def with_unrun(result: Any, extra: list[str]) -> StepResult:
            output = str(getattr(result, "output", "") or "")
            contexts = getattr(result, "error_contexts", None)
            names = sorted(not_run_spec_names(output) | set(extra))
            body = re.sub(r"(?m)^NOT RUN SPEC FILES: .*\n?", "", output)
            return StepResult(False, f"NOT RUN SPEC FILES: {', '.join(names)}\n{body}", skipped=bool(getattr(result, "skipped", False)),
                              ran=getattr(result, "ran", -1), failed_tests=getattr(result, "failed_tests", None),
                              infra=str(getattr(result, "infra", "") or ""), not_run_tests=getattr(result, "not_run_tests", None),
                              error_contexts=contexts)
        if head.skipped:
            return head
        if not head.ok or run_unfinished(head):
            log(f"[tdd] {req_id}: the first step of the regression run is not green; the other {len(rest)} spec(s) are not "
                "run this round (no verdict -- never green)")
            return with_unrun(head, rest_names)
        log(f"[tdd] {req_id}: the first step of the regression run is green; running the other {len(rest)} protected spec(s)")
        tail_run = run_e2e(self.project_dir, rest, log=log, **until)
        if tail_run.skipped:
            return with_unrun(tail_run, rest_names)
        ran = -1 if getattr(head, "ran", -1) < 0 or getattr(tail_run, "ran", -1) < 0 else head.ran + tail_run.ran
        failed = (list(head.failed_tests or []) + list(tail_run.failed_tests or [])) if (head.failed_tests is not None or tail_run.failed_tests is not None) else None
        return StepResult(bool(tail_run.ok), str(head.output or "") + "\n" + str(tail_run.output or ""), ran=ran, failed_tests=failed,
                          infra=str(tail_run.infra or ""), not_run_tests=tail_run.not_run_tests, error_contexts=tail_run.error_contexts)

    def remember_green_batch(self, written: list[str], specs: list[str]) -> None:
        for spec in specs:
            if spec not in self.green_specs:
                self.green_specs.append(spec)
        for rel in written:
            known = self.file_specs.setdefault(rel, [])
            known.extend(spec for spec in specs if spec not in known)

    PARTIAL_GROUPS_MAX = 16

    @staticmethod
    def spec_digest(content: bytes) -> str:
        return hashlib.sha1(content).hexdigest()

    def own_run_state(self, run: Any, specs: list[str]) -> dict[str, Any]:
        state: dict[str, Any] = {"run": run, "sources": sources_digest(self.snapshot_sources()), "specs": {}}
        for spec in specs:
            try:
                state["specs"][spec] = self.spec_digest((self.project_dir / spec).read_bytes())
            except OSError:
                state["specs"][spec] = ""
        return state

    def partial_green_titles(self, own: dict[str, Any] | None, specs: list[str]) -> tuple[dict[str, list[str]], str]:
        from agent import verify

        if not own or own.get("run") is None:
            return {}, "its last check did not run its own spec"
        run = own["run"]
        if run_unfinished(run) or getattr(run, "skipped", False):
            return {}, "its last run of its own spec did not finish"
        if own.get("sources") != sources_digest(self.snapshot_sources()):
            return {}, "the sources are no longer those of its last check"
        listed: dict[str, list] = {}
        for spec in specs:
            try:
                same = self.spec_digest((self.project_dir / spec).read_bytes()) == (own.get("specs") or {}).get(spec)
            except OSError:
                same = False
            if not same:
                return {}, f"{Path(spec).name} is no longer the file its last check ran"
            groups = verify.test_groups(self.project_dir, spec, log=log)
            if not groups:
                return {}, f"the tests of {Path(spec).name} cannot be listed one by one (serial mode, .only, or no listing)"
            listed[spec] = groups
        total = sum(len(tests) for groups in listed.values() for _, tests in groups)
        if int(getattr(run, "ran", -1)) != total:
            return {}, f"its last run ran {getattr(run, 'ran', -1)} test(s), {total} are listed"
        failed: dict[str, set[str]] = {}
        if not run.ok:
            entries = getattr(run, "failed_tests", None)
            if entries is not None:
                for entry in entries:
                    failed.setdefault(str(entry[0]), set()).add(str(entry[3]))
            else:
                failed = failed_test_titles(run.output)
        found: dict[str, list[str]] = {}
        for spec, groups in listed.items():
            titles = {title for _, tests in groups for _, _, title in tests}
            bad = failed.get(Path(spec).name, set())
            if not bad <= titles:
                return {}, f"a failing test of {Path(spec).name} is not among its listed tests"
            green = sorted({title for _, tests in groups if not any(t in bad for _, _, t in tests) for _, _, title in tests})
            if green:
                found[spec] = green
        if not run.ok and not any(failed.get(Path(spec).name) for spec in specs):

            return {}, "the failing tests of its last run cannot be read"
        return found, ("" if found else "no test of its own spec passed at its last check")

    def book_partial_green(self, req_id: str, specs: list[str], status: str, rolled_back: bool, own: dict[str, Any] | None,
                           written: list[str]) -> None:
        store = self.__dict__.setdefault("partial_green", {})
        for spec in specs:
            store.pop(spec, None)
        if status == "passed" or not specs or not self.large_tree or not setting_on("partial_green_guard"):
            return
        if rolled_back:
            log(f"[tdd] {req_id}: put back; no test of its own spec joins the regression checks of later batches")
            return
        try:
            found, why = self.partial_green_titles(own, specs)
        except Exception as exc:
            found, why = {}, f"reading its last check failed: {exc!r}"
        if not found:
            log(f"[tdd] {req_id}: ends {status}; no test of its own spec joins the regression checks of later batches ({why})")
            return
        for spec, titles in found.items():
            try:
                digest = self.spec_digest((self.project_dir / spec).read_bytes())
            except OSError:
                continue
            store[spec] = {"batch": req_id, "titles": titles, "sha1": digest, "written": list(written)}
            log(f"[tdd] {req_id}: ends {status}; {len(titles)} test(s) of {Path(spec).name} were green at its last check and "
                f"join the regression checks of later batches: {titles[:6]}")

    def partial_green_groups(self, before: dict[str, bytes], own: list[str]) -> list[tuple[str, list]]:
        from agent import verify

        store = getattr(self, "partial_green", None) or {}
        if not store or not self.large_tree or not setting_on("partial_green_guard"):
            return []
        changed: set[str] = set()
        for rel, content in before.items():
            try:
                if (self.project_dir / rel).read_bytes() != content:
                    changed.add(rel)
            except OSError:
                changed.add(rel)
        if not changed:
            return []
        direct = [spec for spec, entry in store.items() if changed & set(entry.get("written") or [])]
        order = list(dict.fromkeys(direct + list(store)))
        if setting_on("seed_quarantine_per_test", True):

            order = ([spec for spec in order if not store[spec].get("seed_conflict")]
                     + [spec for spec in order if store[spec].get("seed_conflict")])
        chosen: list[tuple[str, list]] = []
        dropped: list[str] = []
        total = 0
        for spec in order:
            if spec in own:
                continue
            entry = store[spec]
            try:
                same = self.spec_digest((self.project_dir / spec).read_bytes()) == entry.get("sha1")
            except OSError:
                same = False
            if not same:
                continue

            green = None if entry.get("titles") is None and entry.get("red") else set(entry.get("titles") or [])
            red = set(entry.get("red") or [])
            groups = verify.test_groups(self.project_dir, spec, log=log) or []
            keep = [(line, tests) for line, tests in groups if tests and all((green is None or title in green) and title not in red
                                                                              for _, _, title in tests)]
            if not keep:
                continue
            room = self.PARTIAL_GROUPS_MAX - total
            if room <= 0:
                dropped.append(Path(spec).name)
                continue
            chosen.append((spec, keep[:room]))
            total += len(keep[:room])
        if dropped:
            log(f"[tdd] partial-green tests over the cap of {self.PARTIAL_GROUPS_MAX} test groups not run in this check: {dropped}")
        return chosen

    def run_partial_tests(self, spec: str, groups: list, until: float | None = None) -> StepResult:
        from agent import verify

        if verify.isolation_wanted() and len(groups) * verify.ISOLATED_RUN_EST_SEC <= verify.PER_FILE_ISOLATION_SEC:
            return verify.run_tests_isolated(self.project_dir, spec, groups, log=log, **until_kw(until, verify.run_tests_isolated))
        rel = spec.split("/", 1)[1]

        return verify._run_e2e_once(self.project_dir, [spec], log=log, only=[f"{rel}:{line}" for line, _ in groups],
                                    **until_kw(until, verify._run_e2e_once))

    def with_partial_green(self, base: StepResult, partial: list[tuple[str, list]], ran: list[str] | None = None,
                           phase_end: dict[str, Any] | None = None) -> StepResult | None:
        from agent.verify import FAILED_FILES_HEAD

        if not base.ok and not failed_spec_names(base.output):
            return base
        failing = sorted(failed_spec_names(base.output)) if not base.ok else []
        parts: list[str] = []
        unrun = list(getattr(base, "not_run_tests", None) or [])
        end = (phase_end or {}).get("end")
        for spec, groups in partial:
            name = Path(spec).name
            if phase_end is not None and self.phase_passed(phase_end):
                return None

            run = self.run_partial_tests(spec, groups, **until_kw(end, self.run_partial_tests))
            if end is not None and stopped_unrun(run):

                log(f"[tdd] the partial-green tests of {name} were stopped at the phase's end before a test failed; unfinished")
                return None
            if run.skipped:
                log(f"[tdd] the partial-green tests of {name} could not run here: {run.output.strip()[:160]}")
                continue
            if ran is not None:
                ran.append(name)
            if run.ok:
                continue
            unrun += getattr(run, "not_run_tests", None) or []
            if name not in failing:
                failing.append(name)
            text = run.output
            if text.startswith(FAILED_FILES_HEAD):
                text = text.split("\n", 1)[1] if "\n" in text else ""
            parts.append(f"## {name}: only its {sum(len(t) for _, t in groups)} test(s) that were green when its own batch ended\n{text.strip()}")
        if not parts:
            return base
        body = base.output
        if body.startswith(FAILED_FILES_HEAD):
            body = body.split("\n", 1)[1] if "\n" in body else ""
        return StepResult(False, FAILED_FILES_HEAD + ", ".join(failing) + "\n" + body.rstrip() + "\n\n" + "\n\n".join(parts) + "\n",
                          ran=getattr(base, "ran", -1), not_run_tests=unrun or None)

    def drop_partial_green(self, names: set[str], req_id: str) -> None:
        store = getattr(self, "partial_green", None) or {}
        gone = [spec for spec in store if Path(spec).name in names]
        for spec in gone:
            store.pop(spec, None)
        if gone:
            log(f"[tdd] {req_id}: partial-green tests of {[Path(s).name for s in gone]} fail now; they leave the regression checks")

    def seed_conflict_verdict(self, node: dict[str, Any], batch_before: dict[str, bytes], regression_state: dict[str, Any],
                              check_state: dict[str, Any]) -> tuple[list[str], list[str]]:
        none: tuple[list[str], list[str]] = ([], [])
        try:
            if not setting_on("seed_conflict_quarantine") or not batch_before or not regression_state.get("red"):
                return none
            if check_state.get("kept_at_best") or regression_state.get("unknown"):
                return none
            own = check_state.get("own") or {}
            now = self.snapshot_sources()
            if not own or not getattr(own.get("run"), "ok", False) or own.get("sources") != sources_digest(now):
                return none
            changed = [rel for rel in sorted(set(batch_before) | set(now)) if batch_before.get(rel) != now.get(rel)]
            if not changed or not all(SEED_LOCK_FILE.search(rel) for rel in changed):
                return none
            failed = set(regression_state.get("failed") or [])
            output = strip_terminal_codes(str(regression_state.get("output") or ""))
            if not failed or not output or OUTPUT_CUT_MARK in output:
                return none
            signatures = failed_test_signatures(output)
            if set(signatures) != failed or not all(signatures.values()):
                return none
            for tests in signatures.values():
                for signature in tests.values():
                    if not signature.startswith(NOTSEE_SIGNATURE_HEAD) or NOTSEE_SIGNATURE_TAIL not in signature:
                        return none
            if check_state.get("last_lint"):
                return none
            if not seed_conflict_output_clean(output):
                return none
            by_test = notsee_values_by_test(output)
            wanted = {(name, title) for name, tests in signatures.items() for title in tests}
            if set(by_test) != wanted or not all(by_test.values()):
                return none
            values = [value for found in by_test.values() for value in found]
            from agent.seed_values import stated_seed_values
            stated = set(stated_seed_values(str(node.get("seed_sentences") or "")))
            if not all(value in stated for value in values):
                return none

            from agent.outsider import seed_conflict_bindings
            paths = {Path(spec).name: spec for spec in list(self.green_specs) + list(self.e2e_specs)}
            for name in failed:
                if name not in paths:
                    return none
                flagged = seed_conflict_bindings((self.project_dir / paths[name]).read_text(encoding="utf-8"), node)
                for (spec_name, title), found in by_test.items():
                    if spec_name != name:
                        continue
                    for value in found:
                        if not any(value == v and (title == t or title.endswith(" › " + t)) for t, v in flagged if t):
                            return none
            return sorted(failed), list(dict.fromkeys(values))
        except Exception as error:
            log(f"[tdd] seed-conflict check skipped: {type(error).__name__}: {error}")
            return none

    seed_conflict_excuse = seed_conflict_verdict

    def without_seed_quarantined(self, specs: list[str], what: str) -> list[str]:
        book = (self.__dict__.get("seed_quarantine") or {}) if setting_on("seed_conflict_quarantine") else {}
        skipped = [spec for spec in specs if Path(spec).name in book]
        if skipped:
            said = self.__dict__.setdefault("seed_quarantine_said", set())
            if what not in said:
                said.add(what)
                log(f"[verify] {what}: skips seed-quarantined spec(s) {[Path(s).name for s in skipped]}")
        return [spec for spec in specs if Path(spec).name not in book]

    def quarantine_seed_conflict(self, req_id: str, names: list[str], values: list[str], output: str = "") -> None:
        log(f"[tdd] {req_id}: earlier spec(s) {names} fail only on notSee of stated seed values {values} after a seed-only "
            "change: the spec contradicts the requirement's seed (union); quarantined, the batch is kept")
        gone = set(names)
        self.green_specs = [spec for spec in self.green_specs if Path(spec).name not in gone]
        self.drop_partial_green(gone, req_id)
        book = self.__dict__.setdefault("seed_quarantine", {})
        for name in names:
            book[name] = list(values)
        if setting_on("seed_quarantine_per_test", True):
            self.keep_unconflicted_tests(req_id, names, output)

    def keep_unconflicted_tests(self, req_id: str, names: list[str], output: str) -> None:
        if not self.large_tree or not setting_on("partial_green_guard"):
            return
        try:
            titles = failed_test_titles(str(output or ""))
            if not titles or any(not titles.get(name) for name in names):
                log(f"[tdd] {req_id}: the quarantined specs' failing tests cannot be read; the whole file(s) stay out of the regression checks")
                return
            store = self.__dict__.setdefault("partial_green", {})
            for spec in dict.fromkeys(list(self.e2e_specs)):
                name = Path(spec).name
                if name not in names:
                    continue
                try:
                    digest = self.spec_digest((self.project_dir / spec).read_bytes())
                except OSError:
                    continue
                written = [rel for rel, specs in (getattr(self, "file_specs", None) or {}).items() if spec in specs]
                red = sorted(titles[name])
                store[spec] = {"batch": req_id, "titles": None, "red": red, "sha1": digest, "written": written,
                               "seed_conflict": True}
                log(f"[tdd] {req_id}: {name}: only its conflicting test(s) {red[:6]} leave the regression checks; its other "
                    "tests stay in them")
        except Exception as exc:
            log(f"[tdd] {req_id}: keeping the quarantined specs' other tests failed: {exc!r}")

    def time_left(self) -> float:

        left = self.time_budget - (clock.now() - self.started) - self.spent_before
        end = self.hard_end()
        if end is not None:

            left = min(left, end - HARD_END_BATCH_TAIL_SEC - clock.now())
        return left

    def over_budget(self) -> bool:
        return self.time_left() <= 0

    def apply_contest_deadline(self) -> bool:
        try:
            end, note = contest_deadline_end()
            own = self.__dict__.get("hard_end_at")
            if end is None:
                log(f"[hard-end] {note}; the hard end stays "
                    + (f"{(own - PROCESS_STARTED) / 3600.0:.2f} h after the process start" if own is not None else "off"))
                return False
            wins = own is None or end < own
            log(f"[hard-end] {note}; own hard end "
                + (f"{(own - PROCESS_STARTED) / 3600.0:.2f} h after the process start" if own is not None else "off")
                + f"; the {'contest deadline' if wins else 'platform limit'} wins")
            if wins:
                self.hard_end_at = end
                self.hard_end_mono = min([e for e in (self.__dict__.get("hard_end_mono"), MONO_STARTED + (end - PROCESS_STARTED))
                                          if e is not None])
                watchdog.set_llm_end(self)
                dog = getattr(watchdog, "_ACTIVE", None)
                if dog is not None:
                    dog.move_end(end + max(0.0, setting_seconds("contest_deadline_margin_sec", 3600.0)) / 2.0)
            left = end - wall_now()
            if left < setting_seconds("contest_min_run_sec", 7200.0):
                log(f"[hard-end] only {max(0, int(left))} s to the contest end (< contest_min_run_sec): nothing is done -- "
                    "the output folder stays exactly as the platform gave it")
                self.report["contest_deadline"] = {"skipped": True, "left_sec": round(left)}
                return True
            return False
        except Exception as exc:
            log(f"[hard-end] contest deadline not applied: {exc!r}")
            return False

    def apply_evolution_run_target(self) -> None:
        try:
            target = setting_seconds("evolution_run_target_sec", 54000.0)
            platform = platform_hard_end(PROCESS_STARTED)
            contest, _note = contest_deadline_end()
            if target <= 0:
                log("[hard-end] evolution run target: off")
                return
            if getattr(self, "stage_target", None) and float(self.stage_target) <= target:
                log(f"[hard-end] evolution run target {int(target)} s: the staged target {int(self.stage_target)} s is shorter; kept")
                return
            end = PROCESS_STARTED + target
            ends = {"run target": end, "platform limit": platform, "contest deadline": contest}
            current = self.__dict__.get("hard_end_at")
            if current is None or end < current:
                self.hard_end_at = end
                self.hard_end_mono = min([e for e in (self.__dict__.get("hard_end_mono"), MONO_STARTED + target) if e is not None])
                watchdog.set_llm_end(self)
                dog = getattr(watchdog, "_ACTIVE", None)
                if dog is not None:
                    dog.move_end(end + setting_seconds("stage_watchdog_slack_sec", 2700.0))
            self.stage_target = target
            final = self.hard_end_at
            wins = min((k for k, v in ends.items() if v is not None), key=lambda k: ends[k])
            log("[hard-end] evolution round: " + "; ".join(
                f"{k} {(v - PROCESS_STARTED) / 3600.0:.2f} h" if v is not None else f"{k} off" for k, v in ends.items())
                + f" after the process start -- the {wins} wins (hard end {(final - PROCESS_STARTED) / 3600.0:.2f} h)")
        except Exception as exc:
            log(f"[hard-end] evolution run target not applied: {exc!r}")

    def hard_end(self) -> float | None:
        end = getattr(self, "hard_end_at", None)

        if end is None:
            return None
        wall = float(end) - clock.paused_sec()
        mono = self.__dict__.get("hard_end_mono")
        if mono is None:
            return wall

        return min(wall, clock.now() + (float(mono) - time.monotonic()))

    def hard_left(self, reserve: float = 0.0) -> float:
        end = self.hard_end()
        return float("inf") if end is None else end - reserve - clock.now()

    def hard_note(self, message: str) -> None:
        log(f"[hard-end] {message}")
        self.__dict__.setdefault("hard_end_cuts", []).append(message[:300])

    def hard_fits(self, what: str, worst: float, reserve: float = HARD_END_FINISH_SEC) -> bool:
        left = self.hard_left(reserve)
        if left >= worst:
            return True
        self.hard_note(f"{what} not started: {int(max(0.0, left + reserve))} s to the hard end; it may take {int(worst)} s "
                       f"and {int(reserve)} s stay free after it")
        return False

    def hard_deadline(self, deadline: float, what: str, reserve: float = HARD_END_FINISH_SEC,
                      round_sec: float = HARD_END_ROUND_SEC) -> float:
        end = self.hard_end()
        if end is None:
            return deadline
        cap = end - reserve - round_sec
        if deadline > cap:
            self.hard_note(f"{what}: deadline cut by {int(deadline - cap)} s -- rounds start until {int(cap - clock.now())} s from now")
            return cap
        return deadline

    def seed_identity_due(self) -> bool:
        return self.hard_fits("seed identity (a diagnostic, it only reports)", SEED_IDENTITY_SEC,
                              reserve=self.closing_chain_sec(len(getattr(self, "e2e_specs", []) or [])))

    EST_FACTOR = 1.5
    EST_KEEP = 12
    BUILD_EST_FLOOR_SEC = 60.0
    E2E_EST_FLOOR_PER_FILE_SEC = 10.0
    E2E_EST_SERVER_SEC = 60.0

    def note_duration(self, kind: str, seconds: float, files: int = 0) -> None:
        ledger = self.__dict__.setdefault("durations", {})
        runs = ledger.setdefault(kind, [])
        runs.append((max(0.0, float(seconds)), int(files)))
        del runs[:-self.EST_KEEP]

    def build_est(self) -> float:
        builds = [s for s, _ in (self.__dict__.get("durations") or {}).get("build", [])]
        if not builds:
            return BUILD_WORST_SEC
        return min(BUILD_WORST_SEC, max(self.BUILD_EST_FLOOR_SEC, self.EST_FACTOR * max(builds)))

    def suite_est(self, files: int) -> float:
        runs = [(s, f) for s, f in (self.__dict__.get("durations") or {}).get("e2e", []) if f > 0]
        if not runs:
            return e2e_worst(files)
        per_file = max(self.E2E_EST_FLOOR_PER_FILE_SEC, sum(s for s, _ in runs) / sum(f for _, f in runs))
        return min(e2e_worst(files), self.EST_FACTOR * per_file * max(1, int(files)) + self.E2E_EST_SERVER_SEC)

    def closing_repair_min_sec(self) -> float:
        return 2 * self.suite_est(1) + float(self.CLOSING_PER_SPEC_SEC[0]) + HARD_END_ROUND_SEC

    def closing_want_sec(self, files: int) -> float:
        failed = set(str(m) for m in getattr(self, "failed_members", []) or [])
        red = sum(1 for ctx in (getattr(self, "spec_context", None) or {}).values()
                  if failed & {str(m) for m in (ctx or {}).get("members") or []})
        per_spec = 2 * self.suite_est(1) + float(self.CLOSING_PER_SPEC_SEC[0])
        repair = max(self.closing_repair_min_sec(), min(self.CLOSING_MAX_SEC, max(1, red) * per_spec) + HARD_END_ROUND_SEC)
        return self.build_est() + PROBE_WORST_SEC + self.suite_est(files) + repair + HARD_END_FINISH_SEC

    def closing_chain_sec(self, files: int) -> float:
        return self.build_est() + PROBE_WORST_SEC + self.suite_est(files) + self.closing_repair_min_sec() + HARD_END_FINISH_SEC

    def staged_batch_budget(self, own: float, batches: int, evolution_round: bool) -> float:
        reserve = setting_seconds("stage_closing_reserve_sec", 2700.0)
        share = float(self.stage_target) - reserve
        if evolution_round and setting_on("evolution_full_batch_window"):
            budget = max(600.0 * batches, share)
            log(f"[factory] evolution: batch window {int(budget)} s (run target {int(self.stage_target)} s - closing reserve {int(reserve)} s)")
            return budget
        return max(600.0 * batches, min(own, share))

    def hard_plan_line(self, batches: int) -> str:
        end = self.hard_end()
        if end is None:
            return "no hard end (platform_time_limit_sec is 0): every limit is as before hardend"
        base = PROCESS_STARTED

        def at(t: float) -> str:
            return f"{(t - base) / 3600.0:.2f} h"

        batch_end = min(self.started + self.time_budget - self.spent_before, end - HARD_END_BATCH_TAIL_SEC)
        desc_end = end - self.closing_want_sec(batches) - 1800.0
        return (f"hard end at {at(end)} after the process start (limit {int(setting_seconds('platform_time_limit_sec', 86400))} s - "
                f"margin {int(setting_seconds('platform_time_margin_sec', 5400))} s); batches start until {at(batch_end)}, their "
                f"rounds until {at(end - HARD_END_FINISH_SEC - HARD_END_BATCH_AFTER_SEC - HARD_END_ROUND_SEC)}; description pass ends by about {at(desc_end)} "
                f"(so that the closing chain keeps {int(self.closing_want_sec(batches))} s); closing repair rounds until "
                f"{at(end - HARD_END_FINISH_SEC - HARD_END_ROUND_SEC)}; final build + finish in the last {int(HARD_END_FINISH_SEC)} s; "
                "final judgement, shared sweep and seed identity only in time left over")

    def write_tests(self, req_id: str, files: list[AuthoredFile]) -> dict[str, str]:
        written: dict[str, str] = {}
        for item in files:
            path = safe_path(self.project_dir, item.path)
            write_text(path, item.content)
            written[item.path] = item.content
            self.test_snapshot[item.path] = item.content
            log(f"[author] wrote {item.path} ({len(item.content)} chars)")
            for test_id, line, owner in self.test_case_ids(req_id, item.path, item.content):
                self.trace.register_test(test_id, owner, test_kind(item.path), item.path, None, first_line=line)
        return written

    def reconcile_api_tests(self, node: dict[str, Any], req_id: str, test_files: list[str]) -> None:
        api = [p for p in test_files if p.startswith("backend/tests/") and (self.project_dir / p).is_file()]
        if not api or self.over_budget():
            return
        first = run_node_tests(self.project_dir, api)
        self.record_run(api, first.passed, first.output)
        if first.passed:
            return
        files = [AuthoredFile(path=p, content=(self.project_dir / p).read_text(encoding="utf-8", errors="replace")) for p in api]
        sources = backend_sources(self.project_dir)
        try:
            aligned = TestAuthor(self.llm, log=log).reconcile(node, self.spec_prefix_of(node), files, strip_terminal_codes(first.output)[-5000:], sources)
        except Exception as exc:
            log(f"[tdd] {req_id}: API test alignment failed: {exc!r}")
            return
        requirement = describe_node(node)
        before = {f.path: f.content for f in files}
        accepted = []
        for item in aligned:
            lost = lost_requirement_facts(before[item.path], item.content, requirement)
            if lost:
                log(f"[tdd] {req_id}: alignment of {item.path} refused -- it drops what the requirement states: {lost[:4]}")
            else:
                accepted.append(item)
        if not accepted:
            return
        self.write_tests(req_id, accepted)
        defects = static_test_defects(self.project_dir, [f.path for f in accepted], **({"batch": node} if e1_takes_batch() else {}))
        if defects:
            self.write_tests(req_id, [AuthoredFile(path=f.path, content=before[f.path]) for f in accepted])
            log(f"[tdd] {req_id}: alignment refused by the E1 gate; the API tests stay as they were")
            return
        again = run_node_tests(self.project_dir, api)
        self.record_run(api, again.passed, again.output)
        log(f"[tdd] {req_id}: API tests aligned with the application ({len(accepted)} file(s)): {'green' if again.passed else 'still red'}")

    def set_file_tests_status(self, path: str, passed: bool | None) -> None:
        for test_id in self.test_ids_by_file.get(path, []):
            self.trace.set_test_status(test_id, passed)

    def record_run(self, files: list[str], ok: bool, output: str, not_run_tests: list | None = None) -> None:
        failure_lines = [l for l in strip_terminal_codes(output).splitlines() if re.match(r"\s*(?:\d+\)|✘|×|FAIL\b)", l)] if not ok else []

        listed = failed_spec_names(output) if not ok and "FAILED SPEC FILES: " in output else set()
        unrun = re.search(r"^NOT RUN SPEC FILES: (.+)$", output, re.MULTILINE)
        not_run = {name.strip() for name in unrun.group(1).split(",")} if unrun else set()

        nothing_named = not ok and not listed and not any(Path(p).name in l for p in files for l in failure_lines)
        unrun_titles: dict[str, list[str]] = {}
        for entry in not_run_tests or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 4:
                unrun_titles.setdefault(str(entry[0]), []).append(str(entry[3]))
        partly_run = partly_run_spec_names(output) if not ok else set()
        for path in files:
            name = Path(path).name
            if name in not_run:
                continue
            ids = self.test_ids_by_file.get(path, [])
            mine = [l for l in failure_lines if name in l]
            matched = {t for t in ids if self.test_titles.get(t) and any(self.test_titles[t] in l for l in mine)}
            gone = unrun_titles.get(name, [])
            unrun = {t for t in ids if self.test_titles.get(t) and any(self.test_titles[t] in g for g in gone)}

            unsure = (name in partly_run and not gone) or any(
                not any(self.test_titles.get(t) and self.test_titles[t] in g for t in ids) for g in gone)

            titles = [self.test_titles[t] for t in ids if self.test_titles.get(t)]
            whole_file_red = any(not any(title in l for title in titles) for l in mine) or (name in listed and not matched)
            for test_id in ids:
                failed = test_id in matched or whole_file_red
                if nothing_named:

                    self.trace.set_test_status(test_id, None)
                    continue
                if (test_id in unrun and test_id not in matched) or (unsure and not failed):
                    continue
                self.trace.set_test_status(test_id, not failed)

    @staticmethod
    def test_layer(path: str) -> str:
        if path.startswith("backend/test-e2e/"):
            return "e2e"
        if path.startswith("backend/"):
            return "api"
        return "ui"

    def test_case_ids(self, req_id: str, path: str, content: str) -> list[tuple[str, int | None, str]]:
        layer = self.test_layer(path)
        members = set(self.current_members)
        fallback = self.current_trace_id or req_id
        ids: list[tuple[str, int | None, str]] = []
        for match in re.finditer(r"^\s*(?:it|test)\(\s*(['\"`])(.+?)\1", content, re.MULTILINE):
            title = match.group(2)
            name = re.sub(r"[^\w-]+", "-", title).strip("-")[:60]
            owner_match = re.match(r"\s*\[?(REQ-[0-9][0-9.\-]*[0-9]|REQ-[0-9]+)", title)
            owner = owner_match.group(1) if owner_match and owner_match.group(1) in members else fallback
            ids.append((f"{req_id}.TEST.{layer}.{name}", content.count("\n", 0, match.start()) + 1, owner))
            self.test_titles[f"{req_id}.TEST.{layer}.{name}"] = title
        if not ids:
            ids.append((f"{req_id}.TEST.{layer}.{Path(path).stem}", None, fallback))

        self.test_ids_by_file[path] = list(dict.fromkeys(t for t, _, _ in ids))
        return ids

    def unit_files(self, files: dict[str, str]) -> list[str]:
        return [p for p in files if not p.startswith("backend/test-e2e/")]

    def node_deadline(self, nodes_left: int) -> float:
        share = max(MIN_BATCH_SEC, self.time_left() * 0.8 / max(1, nodes_left))

        return self.hard_deadline(clock.now() + share, f"batch {self.__dict__.get('current_trace_id') or ''}".strip(),
                                  reserve=HARD_END_FINISH_SEC + HARD_END_BATCH_AFTER_SEC)

    def process_node(self, node: dict[str, Any], nodes_left: int = 1, retry: bool = False) -> bool:
        req_id = str(node.get("id"))
        prefix = self.spec_prefix_of(node)

        retry_label = " (hub retry)" if req_id in (self.__dict__.get("hub_retried") or []) else " (foundation retry)"
        log(f"\n===== {req_id}: {node.get('name', '')} =====" + (retry_label if retry else ""))
        members = [str(m) for m in (node.get("members") or [req_id])]
        trace_id = str(node.get("trace_req_id") or req_id)
        self.current_trace_id = trace_id
        self.current_members = members

        node_contract = contract_slice(self.contract, members)
        node_relevant = relevant_files(self.contract, members)
        if len(node_contract) < len(self.contract):
            log(f"[contract] {req_id}: slice {len(node_contract)} of {len(self.contract)} chars")
        self.trace.design_started(trace_id)
        for member in members:
            self.trace.node_state(member, "DESIGNING", "design")

        if retry:

            self.foundation_checks.pop(req_id, None)
            files = [AuthoredFile(path=p, content=self.test_snapshot.get(p) or (self.project_dir / p).read_text(encoding="utf-8", errors="replace"))
                     for p in (self.report["nodes"].get(req_id) or {}).get("tests") or []
                     if p in self.test_snapshot or (self.project_dir / p).is_file()]
        else:
            files = self.author_tests(node, prefix, node_contract)
            if not files:

                again = None
                if SETTINGS.get("outsider_enabled", True) and not LLM.quota_exhausted:
                    log(f"[gate-E1] {req_id}: no test at all; asking the outsider once more")
                    try:
                        again = author_acceptance(self.llm, node, prefix, log=log)
                    except Exception as exc:
                        log(f"[outsider] {prefix}: failed again: {exc!r}")
                if again is None:
                    raise RuntimeError("test author produced no test files")
                files = [again]
        test_files = self.write_tests(req_id, files)
        unit = self.unit_files(test_files)
        first = run_node_tests(self.project_dir, unit)

        for attempt in range(1, 4):
            defects = "\n\n".join(x for x in (static_test_defects(self.project_dir, list(test_files), **({"batch": node} if e1_takes_batch() else {})),
                                               parse_defects_in_output(first.output, unit)) if x)
            if not defects:
                break
            if attempt == 3:

                log(f"[gate-E1] {req_id}: tests still defective after two repairs; going on with them:\n{defects[:600]}")
                break
            log(f"[gate-E1] {req_id}: authored tests do not parse (attempt {attempt}); sending back to the author:\n{defects[:600]}")
            before = {path: (self.project_dir / path).read_text(encoding="utf-8", errors="replace") for path in test_files
                      if path.startswith("backend/test-e2e/") and (self.project_dir / path).is_file()}
            files = self.repair_tests(node, prefix, files, "These test files do not parse / load:\n" + defects, contract=node_contract)
            test_files = self.write_tests(req_id, files)

            for path in test_files:
                if path in before and (self.project_dir / path).read_text(encoding="utf-8", errors="replace") != before[path]:
                    self.revised_specs.add(path)
            unit = self.unit_files(test_files)
            first = run_node_tests(self.project_dir, unit)

        files, test_files, spec_defect_rounds = self.repair_spec_defects(req_id, node, prefix, files, test_files, node_contract)
        log(f"[tdd] {req_id} initial run: {'GREEN (unexpected)' if first.passed else 'RED as expected'}")
        self.trace.design_done(trace_id)
        for member in members:
            self.trace.node_state(member, "DESIGNED", "design")
        self.trace.git_commit(f"{req_id} (design): executable tests")

        self.trace.implement_started(trace_id)
        for member in members:
            self.trace.node_state(member, "IMPLEMENTING", "implement")
        share_start = clock.now()
        deadline = self.node_deadline(nodes_left)

        self.confirm_window_end = deadline + HARD_END_BATCH_AFTER_SEC

        phase_end: dict[str, Any] = {"end": None, "name": ""}
        e2e_specs = [p for p in test_files if p.startswith("backend/test-e2e/")]
        if not e2e_specs and SETTINGS.get("outsider_enabled", True) and not LLM.quota_exhausted:

            log(f"[gate-E1] {req_id}: no acceptance spec; asking the outsider once more")
            try:
                again = author_acceptance(self.llm, node, prefix, log=log)
            except Exception as exc:
                log(f"[outsider] {prefix}: failed again: {exc!r}")
                again = None
            if again is not None:
                files = files + [again]
                test_files.update(self.write_tests(req_id, [again]))
                e2e_specs = [again.path]

        def node_check() -> TestOutcome:
            self.restore_tests()
            check_state["n"] = int(check_state.get("n", 0)) + 1
            outcome = run_checks()
            if phase_end["end"] is not None:

                check_state["checked_digest"] = sources_digest(self.snapshot_sources())
            check_state["last"] = outcome.output
            if not outcome.passed:
                check_state["last_red"] = outcome.output
            lint = lint_application(self.project_dir, self.native_validation)
            check_state["last_lint"] = str(lint or "")
            if setting_on("partial_repair_evidence", True):

                check_state["evidence"] = {
                    "digest": sources_digest(self.snapshot_sources()), "output": outcome.output, "passed": bool(outcome.passed),
                    "lint": str(lint or ""), "large": bool(self.large_tree), "ran": check_state.get("e2e_ran"),
                    "specs": list(e2e_specs) if self.large_tree else list(dict.fromkeys(self.e2e_specs + e2e_specs))}
                self.__dict__["last_check_evidence"] = check_state["evidence"]
            if lint:

                linted = TestOutcome(False, "APPLICATION LINT FAILED (fix these too, in the same answer):\n" + lint + "\n\n" + outcome.output, ran=outcome.ran,
                                     infra=getattr(outcome, "infra", ""), error_contexts=getattr(outcome, "error_contexts", None))
                if getattr(outcome, "stop", ""):
                    linted.stop = outcome.stop
                return linted
            return outcome

        def run_checks() -> TestOutcome:
            check_state.pop("crossdep_pending", None)
            all_unit = list(dict.fromkeys([p for p in self.all_test_files if not p.startswith("backend/test-e2e/")] + unit))

            blocking = [p for p in all_unit if p.startswith("frontend/")]
            advisory = [p for p in all_unit if not p.startswith("frontend/")]
            unit_outcome = run_node_tests(self.project_dir, blocking)
            self.record_run(blocking, unit_outcome.passed, unit_outcome.output)
            api_note = ""

            own_api = [p for p in unit if not p.startswith("frontend/")]
            advisory = self.advisory_api_files(req_id, advisory, own_api)
            if advisory:
                api_outcome = run_node_tests(self.project_dir, advisory)

                self.record_run(advisory, api_outcome.passed, api_outcome.output)
                self.note_api_run(advisory, own_api, api_outcome)
                if not api_outcome.passed:
                    api_note = "\n\nADVISORY backend API test failures (fix them if they point at a real bug; they do not block):\n" + api_outcome.output[-2500:]
                    log("[tdd] advisory API tests failing (non-blocking)")

            regression_state["red"] = regression_state["foundation"] = False
            check_state["own"] = None
            check_state["regression_ran"] = []
            check_state["e2e_ran"] = None
            check_state["regression_ran_count"] = None
            all_e2e = list(e2e_specs) if self.large_tree else list(dict.fromkeys(self.e2e_specs + e2e_specs))
            if not all_e2e:
                return TestOutcome(unit_outcome.passed, unit_outcome.output + api_note, ran=unit_outcome.ran)
            began = clock.now()
            build = build_frontend(self.project_dir, log=log)
            self.note_duration("build", clock.now() - began)
            if not build.ok:
                return TestOutcome(False, "FRONTEND BUILD FAILED\n" + build.output, infra=getattr(build, "infra", ""))
            began = clock.now()
            e2e = run_e2e(self.project_dir, all_e2e, log=log)
            if not e2e.skipped:
                check_state["e2e_ran"] = int(getattr(e2e, "ran", -1))
            if not e2e.skipped:
                self.note_duration("e2e", clock.now() - began, len(all_e2e))
            if getattr(e2e, "infra", ""):

                return TestOutcome(False, "E2E NOT RUN (install)\n" + e2e.output, infra=e2e.infra)
            if e2e.skipped:

                return TestOutcome(unit_outcome.passed, unit_outcome.output + "\n(Playwright unavailable here, skipped)\n" + api_note, ran=unit_outcome.ran)
            self.record_run(all_e2e, e2e.ok, e2e.output, **unrun_kw(e2e, self.record_run))
            if self.large_tree and setting_on("partial_green_guard"):
                check_state["own"] = self.own_run_state(e2e, list(e2e_specs))

            unit_note = ""
            if not unit_outcome.passed:
                unit_note = "\n\nADVISORY component test failures (jsdom, no server; fix them only if they show a real client-side bug):\n" + unit_outcome.output[-2000:]
                log("[tdd] advisory component tests failing (non-blocking)")
            if e2e.ok and batch_before:

                lost = lost_seed_values(str(node.get("seed_sentences") or ""), batch_before, self.snapshot_sources())
                key = f"{req_id}:values"

                if lost and self.seed_checks.get(key, 0) >= 1:
                    log(f"[tdd] {req_id}: stated seed values still look removed {lost[:8]}; not blocking a second time")
                if lost and self.seed_checks.get(key, 0) < 1:
                    self.seed_checks[key] = self.seed_checks.get(key, 0) + 1
                    log(f"[tdd] {req_id}: e2e green but the batch removed stated seed values {lost[:8]}")
                    return TestOutcome(False, "SEED DATA CHECK FAILED (the e2e specs themselves passed)\nThis batch removed from the "
                                       "backend values that the requirement's seed sentences state and that were there before it: "
                                       + ", ".join(f'"{v}"' for v in lost[:20]) + ". Put them back exactly as they were. Data this "
                                       "batch needs goes into NEW records or rows; stated seed values are never replaced." + unit_note + api_note)
            if e2e.ok and batch_before:

                earlier = "\n".join((self.project_dir / spec).read_text(encoding="utf-8", errors="replace")
                                    for spec in self.e2e_specs if spec not in e2e_specs and (self.project_dir / spec).is_file())
                gone = lost_stated_names(str(node.get("requirement_text") or ""), batch_before, self.snapshot_sources(), earlier)
                key = f"{req_id}:names"
                if gone and self.seed_checks.get(key, 0) >= 1:
                    log(f"[tdd] {req_id}: stated names still look removed {gone[:8]}; not blocking a second time")
                if gone and self.seed_checks.get(key, 0) < 1:
                    self.seed_checks[key] = self.seed_checks.get(key, 0) + 1
                    log(f"[tdd] {req_id}: e2e green but the batch removed names the requirement states {gone[:8]}")
                    return TestOutcome(False, "STATED NAME CHECK FAILED (the e2e specs themselves passed)\nThis batch removed from the "
                                       "application names that the requirement states word for word and that earlier batches had "
                                       "built: " + ", ".join(f'"{n}"' for n in gone[:20]) + ". The specs of those earlier requirements "
                                       "use them. Put each one back exactly as written (same text, same role). A control this batch "
                                       "needs gets its own name from its own requirement; stated names are never replaced by "
                                       "familiar wording." + unit_note + api_note)
            if e2e.ok:

                missing = missing_seed_names(describe_node({k: v for k, v in node.items() if k != "ancestor_context"}), self.snapshot_sources())
                if missing and self.seed_checks.get(req_id, 0) >= 1:

                    log(f"[tdd] {req_id}: the seed still looks to lack {missing}; not blocking a second time")
                    missing = []
                if missing:
                    self.seed_checks[req_id] = self.seed_checks.get(req_id, 0) + 1
                    log(f"[tdd] {req_id}: e2e green but the seed lacks {missing}")
                    return TestOutcome(False, "SEED DATA CHECK FAILED (the e2e specs themselves passed)\nThe requirement says these records already exist "
                                       "(its seed sentence), but no file under backend/ contains them: "
                                       + ", ".join(f'"{n}"' for n in missing) + ". Add each one to the seed data exactly as named, "
                                       "in the state the requirement gives it, without removing any other seed record." + unit_note + api_note)
            if e2e.ok and self.large_tree:

                regression = self.regression_specs(batch_before, e2e_specs)

                foundation_run = [spec for spec in regression if spec in self.foundation_specs()]
                unchanged = self.foundation_unchanged(req_id, foundation_run, batch_before, check_state)
                if unchanged:
                    regression = [spec for spec in regression if spec not in unchanged]
                    foundation_run = [spec for spec in foundation_run if spec not in unchanged]

                partial = self.partial_green_groups(batch_before, e2e_specs)
                if regression or partial:
                    unfinished = self.phase_unfinished(req_id, phase_end, len(regression) + len(partial))
                    if unfinished is not None:
                        return unfinished
                    if regression:
                        log(f"[tdd] {req_id}: existing files were changed; running {len(regression)} earlier green spec(s) again: {regression}")
                    if partial:
                        log(f"[tdd] {req_id}: existing files were changed; running the tests of red batches that were green at their end again: "
                            + ", ".join(f"{Path(s).name} ({sum(len(t) for _, t in g)})" for s, g in partial))
                    began = clock.now()

                    again = (self.run_regression(req_id, regression, batch_before, **until_kw(phase_end.get("end"))) if regression
                             else StepResult(True, "(no earlier green spec)"))
                    if regression and not again.skipped:
                        self.note_duration("e2e", clock.now() - began, len(regression))
                    if not again.skipped:
                        inconclusive = False
                        regression_state["unknown"] = []
                        regression_ran_specs = list(regression)
                        check_state["regression_ran_count"] = int(getattr(again, "ran", -1)) if regression else None
                        if regression:
                            self.record_run(regression, again.ok, again.output, **unrun_kw(again, self.record_run))
                            self.note_green_reference(check_state, list(regression), again)
                            if stopped_unrun(again) and self.phase_passed(phase_end):

                                return self.phase_unfinished(req_id, phase_end, len(regression))

                            again, inconclusive = self.settle_unnamed_regression(req_id, regression, again, phase_end=phase_end)
                            if inconclusive is None:
                                return self.phase_unfinished(req_id, phase_end, len(regression))
                            check_state["regression_ran"] = [] if inconclusive else [Path(spec).name for spec in regression]
                            check_state["regression_ran_count"] = -1 if inconclusive else int(getattr(again, "ran", -1))

                            regression_state["unknown"] = list(regression) if inconclusive else []
                            if not inconclusive and not again.ok and failed_spec_names(again.output):

                                unrun = not_run_spec_names(again.output)
                                regression_state["unknown"] = [spec for spec in regression if Path(spec).name in unrun]
                            if foundation_run and not inconclusive:
                                self.note_foundation_run(check_state, foundation_run, again)
                        if partial:
                            with_partial = self.with_partial_green(again, partial, ran=check_state.setdefault("regression_ran", []), phase_end=phase_end)
                            if with_partial is None:
                                return self.phase_unfinished(req_id, phase_end, len(partial))
                            again = with_partial
                            regression = regression + [f"{s} (only its tests that were green when its batch ended)" for s, _ in partial]
                        ran_names = list(check_state.get("regression_ran") or [])
                        if again.ok:
                            self.chase_streak(req_id, check_state, ran_names, set())
                        if not again.ok:
                            regression_state["red"] = True
                            failed_names = failed_spec_names(again.output)
                            foundation = {Path(spec).name for spec in self.foundation_specs()}

                            regression_state["foundation"] = bool(failed_names & foundation) or (not failed_names and not inconclusive)
                            regression_state["failed"] = sorted(failed_names)
                            regression_state["output"] = again.output
                            check_state["regression_output"] = again.output

                            unrun_foundation = not_run_spec_names(again.output) & foundation
                            if not regression_state["foundation"] and not inconclusive and not unrun_foundation:

                                self.remember_best_state(req_id, best_state, sorted(failed_names), test_files, check_state)

                            stop = self.chase_streak(req_id, check_state, ran_names, failed_names, again.output) if failed_names else ""
                            if not failed_names:

                                self.chase_unknown(check_state, [Path(spec).name for spec in regression_state.get("unknown") or regression_ran_specs])

                            evidence = self.break_evidence([spec for spec in regression_ran_specs if Path(spec).name in failed_names],
                                                           batch_before, check_state, [again.output]) if failed_names and not inconclusive else ""
                            red_regression = TestOutcome(
                                False,
                                "E2E FAILED against the real application\n"
                                "This batch's own spec PASSES. What fails are specs of EARLIER requirements, which passed before this batch "
                                "changed the files they depend on: " + ", ".join(regression) + ". This batch broke that behaviour. Those specs "
                                "are correct and frozen: restore the earlier behaviour in the application AND keep this batch's own.\n" + again.output
                                + (FOUNDATION_MARK + " the specs above -- the evidence of that break (facts, not failures):\n" + evidence if evidence else ""),
                            error_contexts=getattr(again, "error_contexts", None),
                            )
                            if stop:
                                red_regression.stop = stop
                            return red_regression

            shown, crossdep_stop = e2e.output, ""
            crossdep = (self.crossdep_verdicts(node, e2e, list(e2e_specs), phase_end["end"] if phase_end["end"] is not None else deadline)
                        if not e2e.ok and crossdep_settings_on() else [])
            if crossdep:
                routed = bool(check_state.get("crossdep_route")) and clock.now() < deadline
                shown = drop_red_tests(e2e.output, crossdep) + crossdep_note(crossdep, routed)
                for verdict in crossdep:
                    first_line = (str(verdict["message"]).strip().splitlines() or [""])[0][:300]
                    log(f"[crossdep] {verdict['name']}:{verdict['line']} {verdict['kind']}: "
                        + ("-> test author: " if routed else "no test repair left; kept from the implementer: ") + first_line)
                if routed:
                    check_state["crossdep_pending"] = crossdep_problem(crossdep, e2e.output)
                    crossdep_stop = f"{len(crossdep)} red test(s) rely on another test's data: a spec defect, handed to the test author"
            foundation_note = self.foundation_note_while_red(req_id, e2e_specs, batch_before, check_state, e2e.output, phase_end=phase_end) if not e2e.ok and self.large_tree else ""

            route_note = "" if e2e.ok else unrouted_page_note(self.project_dir, e2e.output)

            seed_note = (self.seed_pending_note(req_id, node, batch_before, list(e2e_specs))
                         if not e2e.ok and setting_on("seed_pending_carry", True) else "")
            final = TestOutcome(e2e.ok, ("E2E PASSED\n" if e2e.ok else "E2E FAILED against the real application\n") + shown + ("" if e2e.ok else ("\n\n" + route_note if route_note else "") + unit_note + api_note + seed_note + foundation_note), error_contexts=getattr(e2e, "error_contexts", None))
            if crossdep_stop:
                final.stop = crossdep_stop
            return final

        regression_state = {"red": False, "foundation": False}
        regression_debt = False
        batch_before = self.snapshot_sources() if self.large_tree else {}

        green_at_start = list(self.green_specs)
        partial_at_start = dict(self.__dict__.get("partial_green") or {})

        start_state = batch_before or self.snapshot_sources()
        check_state: dict[str, Any] = {"last": "", "own": None, "n": 0}

        check_state["chase_armed"] = bool(self.large_tree)
        rolled_back = False

        best_state: dict[str, Any] = {}

        debt_seen: dict[str, Any] = {}

        def take_best_state(where: str) -> bool:
            nonlocal result, regression_debt, rolled_back
            written_before = list(result.written)
            if not self.put_best_state_back(req_id, best_state, test_files, batch_before, where):
                return False
            broken = set(best_state.get("failed") or [])

            debt_seen.clear()
            debt_seen.update(broken=set(broken), own_ran=int(best_state.get("own_ran", -1)),
                             evidence={"output": str(best_state.get("output") or ""), "sources": sources_digest(best_state["sources"])})
            result = ImplementResult("passed", [p for p in written_before if p in best_state["sources"]],
                                     "own spec green at the batch's best check; regressions in earlier specs left to the closing verification",
                                     result.test_runs, last_check="E2E PASSED\n(own spec at the batch's best check; regression specs red)")
            regression_debt, rolled_back = True, False
            check_state["kept_at_best"] = True
            self.regression_debts.append(req_id)
            self.green_specs = [spec for spec in self.green_specs if Path(spec).name not in broken]
            self.drop_partial_green(broken, req_id)
            return True

        def keep_owing() -> None:
            nonlocal result, regression_debt

            log(f"[tdd] {req_id}: its own spec is green, earlier non-foundation specs fail; the batch is kept, the regressions are left to the closing verification")
            result = ImplementResult("passed", result.written, "own spec green; regressions in earlier specs left to the closing verification", result.test_runs, last_check="E2E PASSED\n(own spec; regression specs red)")

            regression_debt = True
            self.regression_debts.append(req_id)

            broken = set(regression_state.get("failed") or [])
            debt_seen.clear()
            debt_seen.update(broken=set(broken), evidence=self.debt_evidence(str(regression_state.get("output") or "")),
                             own_ran=int(getattr((check_state.get("own") or {}).get("run"), "ran", -1) or -1),

                             preexisting=dict(regression_state.get("preexisting") or {}) if regression_state["foundation"] else {},

                             unknown=list(regression_state.get("unknown") or []))
            self.green_specs = [spec for spec in self.green_specs if Path(spec).name not in broken]
            self.drop_partial_green(broken, req_id)

        def excuse_seed_conflict() -> bool:
            nonlocal result
            names, values = self.seed_conflict_verdict(node, batch_before, regression_state, check_state)
            if not names:
                return False
            self.quarantine_seed_conflict(req_id, names, values, output=str(regression_state.get("output") or ""))
            result = ImplementResult("passed", result.written, "own spec green; earlier spec(s) quarantined (seed conflict)",
                                     result.test_runs, last_check="E2E PASSED\n(own spec; earlier specs quarantined: seed conflict)")
            regression_state.update(red=False, foundation=False)
            return True

        def salvage_first(where_deadline: float) -> tuple[list[str], str, Any]:
            if best_state and setting_on("best_batch_state") and best_state.get("tests") == dict(test_files):
                return [], "", ""
            salvaged = self.salvage_foundation_break(req_id, batch_before, list(e2e_specs), regression_state, where_deadline)
            if salvaged[0]:
                verdict = salvaged[2]
                regression_state.update(failed=list(verdict["failed"]), output=str(verdict["output"]), unknown=list(verdict.get("unknown") or []),
                                        foundation=False, preexisting={})
                result.written = [rel for rel in result.written if rel != salvaged[1]]
                check_state["kept_at_best"] = True
                if self.large_tree and setting_on("partial_green_guard"):
                    check_state["own"] = self.own_run_state(verdict["own"], list(e2e_specs))
            return salvaged

        def implementer_log(line: Any) -> None:
            log(line)
            claim = rejected_claim_of(str(line))
            if claim is not None:
                claims = check_state.setdefault("rejected_claims", [])
                if claim not in claims:
                    claims.append(claim)

        retry_note = str((self.__dict__.get("attempt_notes") or {}).get(req_id) or "") if retry else ""

        prerequisite = self.prerequisite_note(node)
        if prerequisite:
            log(f"[tdd] {req_id}: prerequisite(s) put back with nothing kept: {self.undelivered_prerequisites(node)}; "
                "its implementers are told")
            retry_note = self.with_prerequisite_note(retry_note, prerequisite) if retry else retry_note

        debt_note = str(node.get("handoff_debt_note") or "")
        if debt_note:
            log(f"[handoff] {req_id}: depends on earlier-stage leaves that did not end green; its implementers are told")
            retry_note = self.with_prerequisite_note(retry_note, debt_note) if retry else retry_note
            prerequisite = (prerequisite + "\n\n" + debt_note).strip()

        inherited_debt = str(node.get("evolution_inherited_debt") or "")
        if inherited_debt:
            log(f"[evolution] {req_id}: the start-up single-source findings go to its implementers as inherited debt")
            retry_note = self.with_prerequisite_note(retry_note, inherited_debt) if retry else retry_note
            prerequisite = (prerequisite + "\n\n" + inherited_debt).strip()

        startup_debt = str(node.get("evolution_startup_debt") or "")
        if startup_debt:
            log(f"[evolution] {req_id}: start-up health findings go to its implementers as inherited debt")
            retry_note = self.with_prerequisite_note(retry_note, startup_debt) if retry else retry_note
            prerequisite = (prerequisite + "\n\n" + startup_debt).strip()

        held_note = self.held_delivery_note(req_id, node) if setting_on("findings_owner_batch_v2", True) else ""
        if held_note:
            retry_note = self.with_prerequisite_note(retry_note, held_note) if retry else retry_note
            prerequisite = (prerequisite + "\n\n" + held_note).strip()
        initial_first = first
        attempt_files: list[str] = []
        implementer = Implementer(self.llm, self.project_dir, node_check, deadline=deadline, contract=node_contract, relevant=node_relevant, log=implementer_log,
                                  route_raw_waits=True, **self.round_caps(node, test_files), **self.unit_round_options(nodes_left))
        if retry:
            self.arm_retry(implementer, retry_note)

        def crossdep_handover(ended: ImplementResult) -> ImplementResult:
            detail = check_state.pop("crossdep_pending", None)
            if not detail or ended.status in ("passed", "test_defect"):
                return ended
            log(f"[tdd] {req_id}: the implementer stopped on a cross-test dependency of the spec; it goes to the test author")
            return ImplementResult("test_defect", ended.written, str(detail), ended.test_runs)

        if crossdep_settings_on():
            check_state["crossdep_route"] = spec_defect_rounds < 2
        if prerequisite and not retry:
            result = implementer.implement(node, test_files, first, extra_context=prerequisite)
        else:
            result = implementer.implement(node, test_files, first)
        result = crossdep_handover(result)
        defect_rounds = spec_defect_rounds
        while result.status == "test_defect" and defect_rounds < 2 and clock.now() < deadline:
            if regression_state["red"] and str(getattr(result, "defect_kind", "") or "") != "seed-conflict":

                log(f"[tdd] {req_id}: the unchanged failures are regressions in earlier specs; no test repair")
                check_state.setdefault("rejected_claims", []).append(
                    (" ".join(str(result.detail or "").split())[:300],
                     "the failing specs belong to EARLIER requirements; they passed before this batch and are evidence, not suspects"))
                result = ImplementResult("budget", result.written, "could not turn green without breaking earlier requirements", result.test_runs)
                break
            defect_rounds += 1
            if "crossdep_route" in check_state:
                check_state["crossdep_route"] = defect_rounds < 2
            log(f"[tdd] {req_id}: test defect #{defect_rounds} reported: {result.detail[:400]}")

            api_only = defect_whitelist(result)
            if api_only is None:
                self.revised_specs.update(p for p in test_files if p.startswith("backend/test-e2e/"))
            files, test_files = self.repair_tests_within(req_id, node, prefix, files, test_files, result.detail, node_contract, api_only)

            from agent.codegen import CodegenImplementer as _Codegen
            _Codegen.raw_wait_routed.update(tuple(key) for key in (getattr(result, "routed", None) or []))
            app_plan = str(getattr(result, "app_plan", "") or "")

            try:

                rewritten = static_test_defects(self.project_dir, list(test_files)) if not e1_takes_batch() else static_test_defects(self.project_dir, list(test_files), batch=node)
                if rewritten and clock.now() >= deadline:
                    log(f"[gate-E1] {req_id}: the repaired tests do not parse / load, and the batch deadline has passed; no further repair")
                elif rewritten:
                    log(f"[gate-E1] {req_id}: the repaired tests do not parse / load; one more repair:\n{rewritten[:600]}")
                    files, test_files = self.repair_tests_within(req_id, node, prefix, files, test_files, "These test files do not parse / load:\n"
                                                                 + rewritten, node_contract, api_only)
            except Exception as exc:
                log(f"[gate-E1] {req_id}: re-check after the test repair failed: {exc!r}")
            unit = self.unit_files(test_files)
            first = node_check()
            if first.passed:
                result = ImplementResult("passed", result.written, "green after test repair", result.test_runs, last_check=first.output)
                break

            implementer = Implementer(self.llm, self.project_dir, node_check, deadline=deadline, contract=node_contract, relevant=node_relevant, log=implementer_log, show=result.written,
                                      route_raw_waits=defect_rounds < 2, **self.round_caps(node, test_files), **self.unit_round_options(nodes_left))
            if retry:
                self.arm_retry(implementer, retry_note)
            previous_written = result.written
            context = "(The test author just repaired the tests; the application files you wrote earlier are still in place.)"
            if app_plan:
                context += ("\nThe diagnosis that sent the spec to its author also named these changes to the application; they "
                            "are not made yet. Make those the repaired tests still need:\n" + app_plan[:2500])
                log(f"[tdd] {req_id}: the application part of that diagnosis goes to the next implementer ({len(app_plan.splitlines())} line(s))")
            if prerequisite and not retry:
                context += "\n\n" + prerequisite
            result = crossdep_handover(implementer.implement(node, test_files, first, extra_context=context))
            result.written = list(dict.fromkeys(previous_written + result.written))
        if "crossdep_route" in check_state:
            check_state["crossdep_route"] = False
            check_state.pop("crossdep_pending", None)
        log(f"[tdd] {req_id} unit phase: {result.status} after {result.test_runs} test runs; files: {result.written}")
        attempt_files.extend(p for p in result.written if p not in attempt_files)
        if result.status != "passed" and regression_state["red"] and batch_before:
            excuse_seed_conflict()

        restart_why = self.restart_reason(req_id, result, retry, share_start, deadline, node=node)
        if restart_why:
            first_result = result
            first_end = self.snapshot_sources()
            changed_first, _ = change_digest(start_state, first_end)
            restart_note = self.attempt_note(first_result, check_state, list(dict.fromkeys(changed_first + attempt_files)), put_back=True)
            restart_note = self.with_prerequisite_note(restart_note, prerequisite)
            saved_checks = dict(check_state)
            saved_regression = dict(regression_state)
            saved_best = dict(best_state)
            saved_foundation_checks = self.foundation_checks.get(req_id)
            seed_keys = (req_id, f"{req_id}:values", f"{req_id}:names")
            saved_seed_checks = {key: self.seed_checks[key] for key in seed_keys if key in self.seed_checks}
            first_debt_state = bool(regression_state.get("red")) and not regression_state.get("foundation")
            left_sec, share_sec = int(max(0.0, deadline - clock.now())), int(max(0.0, deadline - share_start))
            failures_before = self.restore_failures
            restored = self.restore_sources(start_state)
            log(f"[tdd] {req_id}: the implementer stopped red early ({restart_why}) with {left_sec} s of the batch's {share_sec} s "
                f"left; {restored} file(s) put back to the state before this batch, ONE fresh implementer on the same tests "
                "(in-batch restart)")
            again = None
            if self.restore_failures == failures_before:
                check_state.clear()
                check_state.update({"last": "", "own": None, "n": int(saved_checks.get("n", 0) or 0),
                                    "chase_armed": saved_checks.get("chase_armed")})
                regression_state.clear()
                regression_state.update({"red": False, "foundation": False})
                best_state.clear()
                self.foundation_checks.pop(req_id, None)
                for key in seed_keys:
                    self.seed_checks.pop(key, None)
                caps = self.round_caps(node, test_files)
                restarter = Implementer(self.llm, self.project_dir, node_check, deadline=deadline, contract=node_contract, relevant=node_relevant,
                                        log=implementer_log, route_raw_waits=False, **caps, **self.unit_round_options(nodes_left))
                self.arm_retry(restarter, restart_note)
                self.set_call_deadline(restarter, deadline)
                if self.calls_bounded():
                    phase_end.update(end=deadline, name="in-batch restart")
                try:
                    start = initial_first if not defect_rounds else TestOutcome(False, "(no check has run yet on this "
                        "state: the sources are as they were before this batch, and the tests were repaired by their author)")
                    again = restarter.implement(node, test_files, start)
                except Exception as exc:
                    log(f"[tdd] {req_id}: the in-batch restart crashed: {exc!r}")
                    again = None
                finally:
                    phase_end.update(end=None, name="")
            restart_debt_state = (again is not None and bool(regression_state.get("red")) and not regression_state.get("foundation")
                                  and bool(batch_before))
            if restart_debt_state and check_state.get("checked_digest") and check_state.get("checked_digest") != sources_digest(self.snapshot_sources()):

                log(f"[tdd] {req_id}: the in-batch restart's last files were never checked (its phase ended first); its end "
                    "is unverified and no candidate of the choice")
                restart_debt_state = False

            restart_verdict: tuple[list[str], list[str]] = ([], [])
            if again is not None and again.status != "passed" and regression_state.get("red") and batch_before:
                restart_verdict = self.seed_conflict_verdict(node, batch_before, regression_state, check_state)

            first_best = saved_best if (saved_best and saved_best.get("tests") == dict(test_files)) else {}
            restart_best = (dict(best_state) if (again is not None and again.status != "passed" and best_state
                                                 and best_state.get("tests") == dict(test_files)) else {})

            def reds(state: dict[str, Any]) -> int:
                return len(set(state.get("failed") or []) | set(state.get("unknown") or []))

            choice = "first end"
            if again is not None and (again.status == "passed" or restart_verdict[0]):
                choice = "restart end"
            elif first_debt_state or restart_debt_state:
                candidates = [(reds(state), tie, name) for name, state, eligible, tie in (
                    ("first best", first_best, bool(first_best), 0), ("first end", saved_regression, first_debt_state, 3),
                    ("restart best", restart_best, bool(restart_best), 1), ("restart end", regression_state, restart_debt_state, 2))
                    if eligible]
                choice = min(candidates, key=lambda c: (c[0], -c[1]))[2]
                log(f"[tdd] {req_id}: in-batch restart choice (fewest earlier specs red; equals: first end > restart end > "
                    "restart best > first best): " + ", ".join(f"{name} {count} red" for count, _, name in candidates) + f" -> {choice}")
            first_best_wins = choice == "first best"
            restart_end_state = self.snapshot_sources() if choice == "restart best" else {}
            if again is not None and choice in ("restart end", "restart best"):
                again.test_runs = int(getattr(again, "test_runs", 0) or 0) + int(getattr(first_result, "test_runs", 0) or 0)
                result = again
                attempt_files[:] = list(result.written)
                if choice == "restart best":
                    log(f"[tdd] {req_id}: the in-batch restart ended {again.status}; its best check ({len(restart_best.get('failed') or [])} "
                        "earlier spec(s) red) wins the choice; it is kept at that check")
                    if not take_best_state("the in-batch restart's choice"):
                        self.restore_sources(restart_end_state)
                elif restart_verdict[0] and again.status != "passed":
                    log(f"[tdd] {req_id}: the in-batch restart ended with its own spec green and earlier spec(s) {restart_verdict[0]} "
                        "red only on a seed conflict; it is kept")
                    excuse_seed_conflict()
                else:
                    log(f"[tdd] {req_id}: the in-batch restart ended " + ("GREEN" if again.status == "passed" else
                        "with its own spec green and only earlier non-foundation specs red (a batch kept owing them)") + "; it is kept")
            else:
                ended = str(getattr(again, "status", "") or "without a result")
                restart_failed = list(regression_state.get("failed") or [])
                restart_claims = list(check_state.get("rejected_claims") or [])
                failures_mid = self.restore_failures
                put_back = self.restore_sources(first_end)
                check_state.clear()
                check_state.update(saved_checks)
                claims = check_state.setdefault("rejected_claims", [])
                claims.extend(claim for claim in restart_claims if claim not in claims)
                check_state["restart_summary"] = (
                    f"An in-batch restart from the state before the batch was tried as well and ended {ended}"
                    + (f" -- {' '.join(str(getattr(again, 'detail', '') or '').split())[:200]}" if again is not None else "")
                    + "; the first attempt's state was kept.")
                regression_state.clear()
                regression_state.update(saved_regression)
                best_state.clear()
                best_state.update(saved_best)
                for key in seed_keys:
                    self.seed_checks.pop(key, None)
                self.seed_checks.update(saved_seed_checks)
                if saved_foundation_checks is None:
                    self.foundation_checks.pop(req_id, None)
                else:
                    self.foundation_checks[req_id] = saved_foundation_checks
                result = first_result
                log(f"[tdd] {req_id}: the in-batch restart ended {ended}; not kept -- {put_back} file(s) put back to the state "
                    "the first attempt left")
                if self.restore_failures > failures_mid:
                    log(f"[tdd] {req_id}: WARNING -- putting back the first attempt's state did not reproduce it")
                elif first_best_wins:
                    log(f"[tdd] {req_id}: the first attempt's best check ({len(first_best.get('failed') or [])} earlier spec(s) red) "
                        f"beats the restart's end ({len(restart_failed)} red); it is put back")
                    if not take_best_state("the in-batch restart's choice"):
                        self.restore_sources(first_end)

        if result.status != "passed" and not regression_state["red"] and self.red_batch_broke_foundation(req_id, result.status, batch_before):
            if not take_best_state("the unit phase"):
                result.written = []
                rolled_back = True
        if result.status != "passed" and regression_state["red"] and batch_before:

            foundation_break = regression_state["foundation"] and self.foundation_regression_is_new(req_id, batch_before, regression_state)

            salvaged: tuple[list[str], str, Any] = salvage_first(deadline) if foundation_break else ([], "", "")
            if foundation_break and not salvaged[0]:

                restored = self.restore_sources(batch_before)
                log(f"[tdd] {req_id}: ends red on regressions in the FOUNDATION specs; {restored} file(s) put back to the state before this batch")
                if not take_best_state("the unit phase"):
                    result.written = []
                    rolled_back = True
            else:
                keep_owing()

        e2e_status = "skipped"
        if result.status == "passed" and e2e_specs:
            written_so_far = list(result.written)

            full_check = node_check

            gate = TestOutcome(True, result.last_check) if result.last_check else full_check()
            if gate.passed:
                e2e_status = "passed" if "E2E PASSED" in gate.output else "skipped"
                log(f"[tdd] {req_id} e2e gate: {e2e_status}" + (" (from the implementer's last check)" if result.last_check else ""))
            else:
                log(f"[tdd] {req_id} e2e gate FAILED; starting repair round")
                repairer = Implementer(self.llm, self.project_dir, full_check, max_rounds=3, deadline=deadline, contract=node_contract, relevant=node_relevant, log=log, show=result.written)
                repairer.recall_all_thinking = False
                result = repairer.implement(
                    node,
                    test_files,
                    gate,
                    extra_context="The unit tests are green but the Playwright spec fails against the REAL running application. "
                    "Fix the real integration (routing, API client, session cookies, server routes); do not adapt to mocks.",
                )
                for path in result.written:
                    if path not in written_so_far:
                        written_so_far.append(path)
                result.written = written_so_far
                attempt_files.extend(p for p in result.written if p not in attempt_files)
                e2e_status = "passed" if result.status == "passed" else "failed"
                log(f"[tdd] {req_id} e2e repair: {result.status} after {result.test_runs} checks")
                if result.status != "passed" and batch_before:

                    if regression_state["red"] and excuse_seed_conflict():
                        e2e_status = "passed"
                    elif regression_state["red"]:
                        if regression_state["foundation"] and self.foundation_regression_is_new(req_id, batch_before, regression_state):

                            if salvage_first(deadline)[0]:
                                keep_owing()
                                e2e_status = "passed"
                            else:
                                restored = self.restore_sources(batch_before)
                                log(f"[tdd] {req_id}: e2e repair ends red on regressions in the FOUNDATION specs; {restored} file(s) put back to the state before this batch")
                                if take_best_state("the e2e repair"):
                                    e2e_status = "passed"
                                else:
                                    result.written = []
                                    rolled_back = True
                    elif self.red_batch_broke_foundation(req_id, result.status, batch_before):
                        if take_best_state("the e2e repair"):
                            e2e_status = "passed"
                        else:
                            result.written = []
                            rolled_back = True

        check_state["chase_armed"] = False

        note_books = {key: (list(value) if isinstance(value, list) else value) for key, value in check_state.items()
                      if key in ("last_red", "break_evidence", "rejected_claims", "restart_summary")}
        if regression_debt and result.status == "passed" and e2e_specs:
            repaired = self.repair_regression_debt(req_id, node, test_files, node_check, check_state, green_at_start, partial_at_start,
                                                   batch_before, list(e2e_specs), node_contract, node_relevant, result, nodes_left)
            if repaired is not None:
                result = repaired
                regression_debt = repaired.status != "passed"
            elif setting_on("regression_partial_keep", True):
                partial_kept = self.__dict__.pop("regression_partial_kept", None)
                if isinstance(partial_kept, dict) and partial_kept.get("req") == req_id:

                    result.written = list(dict.fromkeys(list(result.written) + list(partial_kept["written"])))
                    if debt_seen:
                        debt_seen["broken"] = set(partial_kept["still"])
                        debt_seen["evidence"] = partial_kept["evidence"]

        if regression_debt and result.status == "passed" and e2e_specs and debt_seen:
            put_back = self.settle_regression_debt(req_id, list(e2e_specs), debt_seen, batch_before, green_at_start, partial_at_start)
            if put_back is not None:
                result = ImplementResult("budget", [], put_back, result.test_runs)
                e2e_status = "failed"
                regression_debt = False
                rolled_back = True

        if result.status != "passed" and self.dead_app_put_back(req_id, result.status, start_state, check_state["last"]):
            result.written = []
            rolled_back = True

        put_back_left: list[str] = []
        if rolled_back:
            now_state = self.snapshot_sources()
            put_back_left = sorted(rel for rel in set(start_state) | set(now_state) if start_state.get(rel) != now_state.get(rel))
            if put_back_left:
                result.written = list(put_back_left)
                log(f"[tdd] {req_id}: the put-back was INCOMPLETE -- {len(put_back_left)} file(s) still differ from the state "
                    f"before this batch and stay booked as its files: {put_back_left[:10]}")

        own_last_run = check_state.get("own")

        audit_findings: list[dict[str, str]] = []
        if SETTINGS.get("audit_enabled", True) and not self.over_budget() and not LLM.quota_exhausted:
            try:
                if not build_frontend(self.project_dir, log=log).ok:

                    raise RuntimeError("frontend does not build; audit skipped")

                mode = str(SETTINGS.get("audit_llm", "small")).lower()
                use_llm = mode == "always" or (mode == "small" and not self.large_tree)

                from agent.batch_ui_checks import any_check_on
                if any_check_on():
                    node["audit_written"] = [str(rel).replace("\\", "/") for rel in (result.written or [])]
                    node["audit_written_before"] = {rel: start_state[rel].decode("utf-8", "replace") for rel in node["audit_written"]
                                                    if rel.startswith("frontend/") and isinstance(start_state.get(rel), bytes)}
                audit_findings = audit_node(self.llm, self.project_dir, node, self.contract, log=log, use_llm=use_llm)

                audit_findings = list(audit_findings or []) + self.evolution_lifecycle_findings(node, list(result.written or []))

                if setting_on("session_revoke_after_batch") and self.evolution_state() and self.hard_fits("the session revoke probe", 180.0):
                    from agent import session_chain
                    from agent.audit import SESSION_SOURCE
                    probe_findings = session_chain.batch_findings(self.project_dir, (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace"), [str(m) for m in (node.get("members") or [node.get("id")])], log=log)
                    audit_findings += [dict(f, source=SESSION_SOURCE) for f in probe_findings or [] if isinstance(f, dict)]
            except Exception as exc:
                log(f"[audit] {req_id} crashed: {exc!r}")

                self.__dict__.setdefault("held_audit_crashed", set()).add(str(req_id))
            self.book_open_findings(req_id, audit_findings)
            if setting_on("findings_owner_batch_v2", True):
                audit_findings = self.held_release_into(req_id, audit_findings)
            if audit_findings:

                from agent.audit import MAX_REPAIR_FINDINGS, finding_fingerprint, select_for_repair
                if setting_on("findings_owner_batch_v2", True):
                    audit_findings = self.hold_for_owner_batches(req_id, node, audit_findings)
                sent_before = list(getattr(self, "audit_sent_fingerprints", None) or [])

                rolled = list(getattr(self, "audit_rolled_back_fingerprints", None) or [])
                given_up = [fp for fp in dict.fromkeys(rolled) if rolled.count(fp) >= self.AUDIT_ROLLBACKS_MAX]

                given_up = self.route_given_up_findings(req_id, audit_findings, given_up)
                audit_findings, repeated, over_cap = select_for_repair(audit_findings, sent_before + given_up)
                repeat_logged = self.__dict__.setdefault("audit_repeat_logged", set())
                for finding in repeated:
                    if finding_fingerprint(finding) not in repeat_logged:
                        repeat_logged.add(finding_fingerprint(finding))
                        log(f"[audit] {req_id}: kept in an earlier batch or rolled back {self.AUDIT_ROLLBACKS_MAX} times, not sent again: {str(finding.get('where', ''))[:40]}: {str(finding.get('problem', ''))[:140]}")
                if over_cap:
                    log(f"[audit] {req_id}: {len(over_cap)} finding(s) over the cap of {MAX_REPAIR_FINDINGS} left out of this repair (a later batch may send them)")
            if audit_findings:
                brief = findings_brief(audit_findings)
                was_green = result.status == "passed"
                before_audit = self.snapshot_sources()

                before_evidence = dict(check_state.get("evidence") or {}) if setting_on("audit_partial_keep", True) else {}
                repair_end = self.hard_deadline(max(deadline, clock.now() + 480), f"{req_id} audit repair")
                repair_bound = self.audit_repair_bound(req_id, str(result.status), deadline, repair_end, nodes_left)
                carry: dict[str, Any] = {}
                repair_check = (self.audit_carry_check(req_id, node, node_check, audit_findings, carry,
                                                       repair_end if repair_bound is None else repair_bound)
                                if setting_on("audit_carry_still", True) else node_check)
                fixer = Implementer(self.llm, self.project_dir, repair_check, max_rounds=2, extra_rounds=0, deadline=repair_end if repair_bound is None else repair_bound, contract=node_contract, relevant=node_relevant, show=result.written, log=log)
                if repair_bound is not None:
                    self.set_call_deadline(fixer, repair_bound)
                    phase_end.update(end=repair_bound, name="audited repair")
                try:

                    first_check = ("(no test failed: this repair is driven by the independent audit's findings listed below; "
                                   "the batch's checks decide afterwards whether the change is kept)")
                    fix = fixer.implement(node, test_files, TestOutcome(False, first_check), extra_context=brief)
                except Exception as exc:
                    log(f"[audit] {req_id}: the audited repair crashed: {exc!r}")
                    fix = ImplementResult("gave_up", [], repr(exc))
                if carry.get("state") is not None:
                    try:
                        fix = self.audit_carry_settle(req_id, fix, carry)
                    except Exception as exc:
                        log(f"[audit] {req_id}: putting back the carried green state crashed: {exc!r}")

                if fix.status == "passed":

                    fix = self.audit_seed_guard(req_id, before_audit, audit_findings, fix, node_check)
                if fix.status == "passed":
                    result.status = "passed"
                    result.written = list(dict.fromkeys(result.written + fix.written))
                    if e2e_specs:
                        e2e_status = "passed"

                    from agent.audit import is_session_finding
                    probed = [f for f in audit_findings if is_session_finding(f)]
                    settled = [f for f in audit_findings if not is_session_finding(f)] if probed else audit_findings

                    self.audit_sent_fingerprints = list(dict.fromkeys(
                        list(getattr(self, "audit_sent_fingerprints", None) or []) + [finding_fingerprint(f) for f in settled]))
                    self.close_open_findings(settled)

                    rechecked = None
                    if not self.over_budget() and (settled or not probed):
                        try:
                            from agent.audit import recheck_repaired
                            if ("rechecked" in carry and carry.get("digest") == sources_digest(self.snapshot_sources())
                                    and carry.get("checked") == [finding_fingerprint(f) for f in settled]):
                                rechecked = carry["rechecked"]
                            else:
                                rechecked = recheck_repaired(self.project_dir, node, self.contract, settled, log=log)
                            self.reopen_still_there(req_id, rechecked)
                        except Exception as exc:
                            log(f"[audit] {req_id}: re-check of the audited repair crashed: {exc!r}")
                    if setting_on("audit_settle_rechecked"):
                        self.reopen_unrechecked(req_id, settled, rechecked)
                    if probed:
                        self.audit_recheck_session(req_id, node, probed)
                elif (setting_on("audit_partial_keep", True) and fix.status != "test_defect"
                      and self.audit_partial_keep(req_id, before_audit, before_evidence, check_state.get("evidence"), audit_findings, fix, node)):

                    result.written = list(dict.fromkeys(result.written + list(getattr(fix, "written", []) or [])))
                    self.note_rollback_evidence(req_id, audit_findings, str(check_state.get("last") or ""))
                else:
                    failures_before = self.restore_failures
                    restored = self.restore_sources(before_audit)
                    log(f"[audit] {req_id}: the audited repair ended as {fix.status}; {restored} file(s) put back")
                    self.audit_rolled_back_fingerprints = (list(getattr(self, "audit_rolled_back_fingerprints", None) or [])
                                                           + [finding_fingerprint(f) for f in audit_findings])

                    self.note_rollback_evidence(req_id, audit_findings, str(check_state.get("last") or ""))
                    if self.restore_failures > failures_before:
                        result.status = "gave_up"
                    elif (fix.status == "test_defect" and not was_green and not rolled_back and e2e_specs and defect_rounds < 2
                          and setting_on("audit_spec_to_author") and not self.over_budget() and not LLM.quota_exhausted
                          and not self.handover_past_end(req_id, repair_bound, "the test author's hand-over")):

                        defect_rounds += 1
                        log(f"[audit] {req_id}: the audited repair proved a spec defect; the test author gets it (test defect #{defect_rounds}): {fix.detail[:300]}")
                        self.revised_specs.update(p for p in test_files if p.startswith("backend/test-e2e/"))
                        from agent.llm import CallDeadlineReached
                        tests_before, handover_cut = dict(test_files), False
                        with self.calls_end_by(repair_bound):
                            try:
                                files = self.repair_tests(node, prefix, files, fix.detail, contract=node_contract)
                                test_files = self.write_tests(req_id, files)
                                try:
                                    rewritten = static_test_defects(self.project_dir, list(test_files), **({"batch": node} if e1_takes_batch() else {}))
                                    if rewritten and not self.handover_past_end(req_id, repair_bound, "a second test repair"):
                                        log(f"[gate-E1] {req_id}: the repaired tests do not parse / load; one more repair:\n{rewritten[:600]}")
                                        files = self.repair_tests(node, prefix, files, "These test files do not parse / load:\n" + rewritten, contract=node_contract)
                                        test_files = self.write_tests(req_id, files)
                                except Exception as exc:
                                    if isinstance(exc, CallDeadlineReached):
                                        raise
                                    log(f"[gate-E1] {req_id}: re-check after the test repair failed: {exc!r}")
                            except CallDeadlineReached as exc:
                                handover_cut = True
                                log(f"[audit] {req_id}: the test author's repair was cut at the audited repair's phase end ({exc}); "
                                    "not done -- the tests stay as they were written last, the batch keeps its state")
                        from agent.codegen import CodegenImplementer as _Codegen
                        _Codegen.raw_wait_routed.update(tuple(key) for key in (getattr(fix, "routed", None) or []))
                        unit = self.unit_files(test_files)
                        if handover_cut or self.handover_past_end(req_id, repair_bound, "the check after the test author's repair"):
                            if dict(test_files) != tests_before:
                                own_last_run = None
                        else:
                            again = node_check()
                            own_last_run = check_state.get("own")
                            if again.passed:
                                result = ImplementResult("passed", result.written, "green after the audited repair's spec defect went to the test author",
                                                         result.test_runs, last_check=again.output)
                                e2e_status = "passed" if "E2E PASSED" in again.output else e2e_status
                            log(f"[audit] {req_id}: after the test author's repair the batch's check is {'GREEN' if again.passed else 'still red'}")
                log(f"[audit] {req_id}: repair after audit -> {fix.status}; node status {result.status} (was green: {was_green})")
                phase_end.update(end=None, name="")

        if result.status == "passed" and not e2e_specs:

            log(f"[tdd] {req_id}: green without an acceptance spec is not evidence; booked as failed")
            result.status = "gave_up"
            result.detail = "no blocking acceptance spec for this batch"
        if not e2e_specs:
            self.no_evidence_members.extend([m for m in members if m not in self.no_evidence_members])
        passed = result.status == "passed"
        log(f"[tdd] {req_id} implementer finished: {result.status} (e2e={e2e_status}); files: {result.written}")
        if passed:
            self.reconcile_api_tests(node, req_id, list(test_files))
        for spec in e2e_specs:
            if e2e_status in ("passed", "failed"):
                self.set_file_tests_status(spec, e2e_status == "passed")
        for path in result.written:
            self.trace.register_interface(f"{req_id}.{interface_kind_for(path).upper()}.{Path(path).stem}", trace_id, interface_kind_for(path), f"implements {req_id}", path, passed)

        self.trace.implement_done(trace_id)
        self.trace.tests_result(trace_id, passed, result.detail[:200])
        for member in members:
            self.trace.node_state(member, "PASSED" if passed else "FAILED", "test")
        self.trace.git_commit(f"{req_id} (implement): {result.status}")

        self.all_test_files.extend([p for p in test_files if p not in self.all_test_files])
        self.e2e_specs.extend([p for p in test_files if p.startswith("backend/test-e2e/") and p not in self.e2e_specs])

        if passed and e2e_status == "passed":
            self.remember_green_batch(result.written, e2e_specs)

        self.book_partial_green(req_id, list(e2e_specs), result.status, rolled_back, own_last_run, list(result.written))

        node.pop("audit_written", None)
        node.pop("audit_written_before", None)
        for spec in e2e_specs:

            self.spec_context[spec] = {"node": node, "contract": node_contract, "relevant": node_relevant, "tests": dict(test_files), "members": list(members), "written": list(result.written)}
        self.report["nodes"][req_id] = {
            "status": result.status,
            "e2e": e2e_status,
            "tests": list(test_files),
            "written": result.written,
            "test_runs": result.test_runs,

            **({"put_back_members": list(members)} if rolled_back and not put_back_left else {}),

            **({"put_back_incomplete": list(put_back_left)} if put_back_left else {}),
        }
        if not passed and not retry:

            notes = self.__dict__.setdefault("attempt_notes", {})
            notes[req_id] = self.attempt_note(result, note_books, attempt_files + [p for p in result.written if p not in attempt_files],
                                              put_back=rolled_back)
        return passed

    def all_tests_on_disk(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for rel in self.all_test_files:
            path = self.project_dir / rel
            if path.is_file():
                files[rel] = path.read_text(encoding="utf-8", errors="replace")
        return files

    def full_check(self) -> TestOutcome:
        self.restore_tests()
        unit = [p for p in self.all_test_files if p.startswith("frontend/")]
        unit_outcome = run_node_tests(self.project_dir, unit)
        self.record_run(unit, unit_outcome.passed, unit_outcome.output)
        if not self.e2e_specs:
            return unit_outcome
        build = build_frontend(self.project_dir, log=log)
        if not build.ok:
            return TestOutcome(False, "FRONTEND BUILD FAILED\n" + build.output, infra=getattr(build, "infra", ""))
        live = self.without_seed_quarantined(list(self.e2e_specs), "full check")
        if not live:
            return unit_outcome
        e2e = run_e2e(self.project_dir, live, log=log)
        if getattr(e2e, "infra", ""):
            return TestOutcome(False, "E2E NOT RUN (install)\n" + e2e.output, infra=e2e.infra)
        if e2e.skipped:
            return TestOutcome(unit_outcome.passed, unit_outcome.output + "\n(Playwright unavailable here, skipped)\n" + e2e.output)
        self.record_run(list(live), e2e.ok, e2e.output, **unrun_kw(e2e, self.record_run))
        lint = lint_application(self.project_dir, self.native_validation)
        if lint and not e2e.ok:
            return TestOutcome(False, "APPLICATION LINT FAILED (fix these too, in the same answer):\n" + lint + "\n\n" + e2e.output,
                               error_contexts=getattr(e2e, "error_contexts", None))
        if lint:

            log("[verify] lint findings on a green suite (advisory): " + lint[:300].replace("\n", " | "))
        return TestOutcome(e2e.ok, ("E2E PASSED\n" if e2e.ok else "E2E FAILED against the real application\n") + e2e.output,
                           error_contexts=getattr(e2e, "error_contexts", None))

    def repair_round(self, label: str, check, failure_output: str, test_files: dict[str, str] | None = None,
                     deadline: float | None = None, max_rounds: int = 2, keep_if=None) -> bool:
        if self.over_budget():
            log(f"[verify] {label}: over time budget, no repair round")
            return False
        if LLM.quota_exhausted:
            log(f"[verify] {label}: key out of balance, no repair round")
            return False
        pseudo_node = {
            "id": f"VERIFY-{label}",
            "name": f"{label} must succeed",
            "description": f"The deterministic `{label}` check failed. Fix the application so it succeeds without weakening tests.",
        }

        def as_outcome() -> TestOutcome:
            step = check()
            if isinstance(step, TestOutcome):
                return step
            return TestOutcome(step.ok, step.output, infra=getattr(step, "infra", ""))

        round_sec = HARD_END_ROUND_SEC + (self.build_est() + self.suite_est(len(getattr(self, 'e2e_specs', None) or [])) if label == "playwright-e2e" else 0.0)
        slot_end = deadline
        deadline = self.hard_deadline(self.started + self.time_budget + 300, f"{label} repair round", round_sec=round_sec)
        if slot_end is not None:
            deadline = min(deadline, slot_end)
            if deadline - clock.now() < 120:
                log(f"[verify] {label}: {max(0, int(deadline - clock.now()))} s of its slot left, no repair round")
                return False
        if deadline <= clock.now() and self.hard_end() is not None and deadline < self.started + self.time_budget + 300:
            self.hard_note(f"{label} repair round not started: no round fits before the hard end")
            return False
        implementer = Implementer(self.llm, self.project_dir, as_outcome, max_rounds=max_rounds, extra_rounds=0, deadline=deadline, contract=self.contract, log=log)
        before = self.snapshot_sources()
        self.verify_repairs += 1
        in_flight = watchdog.begin(f"{label} repair round", lambda: self.restore_sources(before))
        try:
            result = implementer.implement(pseudo_node, test_files or {}, TestOutcome(False, failure_output))
        except Exception as exc:
            result = ImplementResult("gave_up", [], repr(exc))
        log(f"[verify] {label} repair round: {result.status}")

        keep_anyway = result.status != "passed" and keep_if is not None and bool(keep_if())
        if keep_anyway:
            log(f"[verify] {label}: only a kept-for-later problem is left; the round's work stays")
        if result.status != "passed" and not keep_anyway:

            restored = self.restore_sources(before)
            log(f"[verify] {label}: repair did not reach green; {restored} file(s) put back")
        watchdog.end(in_flight)
        return result.status == "passed" or keep_anyway

    CLOSING_SWEEPS = 2
    CLOSING_MAX_SEC = 14400.0
    CLOSING_PER_SPEC_SEC = (900.0, 2700.0)

    def closing_budget(self) -> float:
        reserve = final_judgement_reserve(len(self.e2e_specs))
        end = self.started + (LARGE_TREE_CAP_SEC if self.large_tree else self.time_budget + 3600 + reserve)
        budget = max(3600.0, min(self.CLOSING_MAX_SEC, end - clock.now() - reserve))
        if self.hard_end() is not None:

            budget = min(budget, max(0.0, self.hard_left(HARD_END_FINISH_SEC + HARD_END_ROUND_SEC)))
        return budget

    def repair_failing_specs(self, suite_output: str, only: list[str] | None = None, until: float | None = None,
                             pool: list[str] | None = None, notes: dict[str, str] | None = None,
                             tried: list[str] | None = None) -> int:
        from agent.shared_sweep import fresh_green_names

        repaired = 0
        already_tried: set[str] = set()
        if only is None and self.__dict__.get("debt_tried"):

            already_tried = set(self.__dict__["debt_tried"])
            log(f"[verify] closing repair: {len(already_tried)} owed spec(s) the first job could not repair are not tried again: "
                f"{sorted(Path(s).name for s in already_tried)}")
        fixed_all: list[str] = []
        closing_end = clock.now() + self.closing_budget()
        if until is not None:
            closing_end = min(closing_end, until)
        if self.hard_end() is not None:
            log(f"[hard-end] closing repair: specs start until {int(closing_end - clock.now())} s from now "
                f"({int(self.hard_left())} s to the hard end)")
        sweeps = 1 if only is not None else self.CLOSING_SWEEPS
        for sweep in range(1, sweeps + 1):
            names = failed_spec_names(suite_output)
            failing = [spec for spec in self.e2e_specs if Path(spec).name in names and spec not in already_tried
                       and (only is None or spec in only)]
            failing = self.without_seed_quarantined(failing, "closing repair")
            if not failing:
                break
            log(f"[verify] closing repair, sweep {sweep}: {len(failing)} failing spec(s): {[Path(s).name for s in failing]}")
            fixed_now: list[str] = []

            known_green = fresh_green_names(self.e2e_specs, False, suite_output) if setting_on("closing_regression_guard") else None
            green_pool = [s for s in self.e2e_specs if Path(s).name in (known_green or set())]
            if pool is not None:
                green_pool = [s for s in dict.fromkeys(pool) if s in self.e2e_specs]
            green_pool = self.without_seed_quarantined(green_pool, "closing repair guard")
            for position, spec in enumerate(failing):
                if LLM.quota_exhausted or clock.now() >= closing_end:
                    log("[verify] closing repair stops: " + ("key out of balance" if LLM.quota_exhausted else "its time is used up"))
                    return repaired

                if not self.hard_fits(f"closing repair of {Path(spec).name}", self.closing_repair_min_sec() - HARD_END_ROUND_SEC,
                                      reserve=HARD_END_FINISH_SEC + HARD_END_ROUND_SEC):
                    log("[verify] closing repair stops: the hard end is near")
                    return repaired
                if until is not None and clock.now() + self.closing_repair_min_sec() > until:
                    log("[verify] closing repair stops: its window for these specs is used up")
                    return repaired
                already_tried.add(spec)
                if tried is not None:
                    tried.append(spec)
                context = self.spec_context.get(spec)
                if not context:
                    continue
                target = [spec]
                began = clock.now()
                alone = run_e2e(self.project_dir, target, log=log)
                if not alone.skipped:
                    self.note_duration("e2e", clock.now() - began, 1)
                if alone.skipped:
                    continue
                self.record_run(target, alone.ok, alone.output, **unrun_kw(alone, self.record_run))
                if alone.ok:

                    live = self.without_seed_quarantined(list(self.e2e_specs), "closing repair, predecessor replay")
                    index = live.index(spec)
                    if index == 0:
                        continue
                    target = [live[index - 1], spec]

                    alone = run_e2e(self.project_dir, target, log=log, shared_data=True)
                    if alone.skipped or alone.ok or Path(spec).name not in failed_spec_names(alone.output):
                        log(f"[verify] {Path(spec).name} passes on its own and behind its predecessor: order-dependent in the suite, not reproduced, left as it is")
                        continue
                    log(f"[verify] {Path(spec).name} fails behind {Path(target[0]).name}: the application does not cope with the state that spec leaves")

                described = list(getattr(self, "description_specs", None) or [])
                guard = [s for s in dict.fromkeys(self.green_specs[:3] + fixed_all) if s not in target][:8]
                guard += [s for s in dict.fromkeys(described) if s not in target and s not in guard]

                red_guard = [s for s in guard if Path(s).name in names and s not in fixed_now]
                if red_guard:
                    log(f"[verify] closing repair of {Path(spec).name}: {len(red_guard)} guard spec(s) red in this sweep left out: {[Path(s).name for s in red_guard]}")
                    guard = [s for s in guard if s not in red_guard]

                exempt = set(evolution.exempt_files((self.evolution_state() or {}).get("registry") or {},
                                                    [str(m) for m in context.get("members") or []])) if self.evolution_state() else set()
                if exempt:
                    log(f"[evolution] closing repair of {Path(spec).name}: not guarded by the unverified inherited tests in "
                        f"{sorted(Path(s).name for s in exempt)}")
                    guard = [s for s in guard if s not in exempt]
                spec_pool = [s for s in green_pool if s not in exempt]
                before = self.snapshot_sources()

                def check(spec: str = spec, guard: list[str] = guard, target: list[str] = target, before: dict[str, bytes] = before,
                          fixed_now: list[str] = fixed_now, green_pool: list[str] = spec_pool, names: set[str] = names) -> TestOutcome:

                    pool = [s for s in dict.fromkeys(green_pool + fixed_now) if Path(s).name not in names or s in fixed_now]
                    more = [s for s in self.regression_specs(before, target, pool=pool) if s not in guard] if pool else []
                    if more:
                        log(f"[verify] closing repair of {Path(spec).name}: existing files changed; {len(more)} other green spec(s) guard it too")
                    self.restore_tests()
                    build = build_frontend(self.project_dir, log=log)
                    if not build.ok:
                        return TestOutcome(False, "FRONTEND BUILD FAILED\n" + build.output, infra=getattr(build, "infra", ""))
                    own = run_e2e(self.project_dir, target, log=log, shared_data=True)

                    if run_unfinished(own):
                        return TestOutcome(False, "E2E NOT RUN to completion (the spec under repair; nothing is kept on it)\n" + own.output, infra=getattr(own, "infra", ""))
                    self.record_run(target, own.ok, own.output, **unrun_kw(own, self.record_run))
                    if not own.ok:
                        return TestOutcome(False, "E2E FAILED against the real application\n" + own.output, infra=getattr(own, "infra", ""), error_contexts=getattr(own, "error_contexts", None))
                    if guard:
                        others = run_e2e(self.project_dir, guard, log=log)
                        if run_unfinished(others):
                            return TestOutcome(False, "E2E NOT RUN to completion (the specs that guard this repair: " + ", ".join(guard)
                                               + "; nothing is kept on it)\n" + others.output, infra=getattr(others, "infra", ""))
                        self.record_run(guard, others.ok, others.output, **unrun_kw(others, self.record_run))
                        if not others.ok:
                            return TestOutcome(False, "E2E FAILED against the real application\nThe spec under repair PASSES now, but these specs, which passed "
                                               "before this repair, fail: " + ", ".join(guard) + ". Keep the repair AND restore what they check.\n" + others.output, infra=getattr(others, "infra", ""), error_contexts=getattr(others, "error_contexts", None))
                    if more:

                        again = run_e2e(self.project_dir, more, log=log)
                        named = sorted(failed_spec_names(again.output) & {Path(s).name for s in more}) if not again.skipped and not again.ok else []
                        if not again.skipped:
                            self.record_run(more, again.ok, again.output, **unrun_kw(again, self.record_run))
                        if named:
                            return TestOutcome(False, "E2E FAILED against the real application\nThe spec under repair PASSES now, but these specs, which passed "
                                               "before this repair, fail: " + ", ".join(named) + ". Keep the repair AND restore what they check.\n" + again.output, infra=getattr(again, "infra", ""), error_contexts=getattr(again, "error_contexts", None))
                        if run_unfinished(again) or not again.ok:
                            log(f"[verify] closing repair of {Path(spec).name}: the run of the other green specs did not finish or named no file; not held on it")
                    return TestOutcome(True, "E2E PASSED\n" + own.output)

                share = (closing_end - clock.now()) / max(1, len(failing) - position)
                deadline = clock.now() + max(self.CLOSING_PER_SPEC_SEC[0], min(self.CLOSING_PER_SPEC_SEC[1], share))
                deadline = self.hard_deadline(deadline, f"closing repair of {Path(spec).name}")
                if until is not None:
                    deadline = min(deadline, until - HARD_END_ROUND_SEC)
                self.verify_repairs += 1
                in_flight = watchdog.begin(f"closing repair of {Path(spec).name}", lambda before=before: self.restore_sources(before))
                implementer = Implementer(self.llm, self.project_dir, check, deadline=deadline, contract=context["contract"], relevant=context["relevant"], log=log, show=context.get("written", []))
                try:
                    result = implementer.implement(
                        context["node"], context["tests"], TestOutcome(False, "E2E FAILED against the real application\n" + alone.output, error_contexts=getattr(alone, "error_contexts", None)),
                        extra_context="CLOSING REPAIR: every requirement has been implemented; this acceptance spec of this requirement fails against "
                        "the finished application. The files of later requirements are part of the project now: repair the behaviour "
                        "this spec checks without removing or renaming anything those later requirements added."
                        + str((notes or {}).get(spec) or ""),
                    )
                except Exception as exc:
                    result = ImplementResult("gave_up", [], repr(exc))
                if result.status == "passed":
                    repaired += 1
                    fixed_now.append(spec)
                    fixed_all.append(spec)
                    self.__dict__.setdefault("closing_fixed", []).append(spec)
                    log(f"[verify] closing repair: {Path(spec).name} is green now ({result.test_runs} check(s))")
                else:
                    restored = self.restore_sources(before)
                    log(f"[verify] closing repair: {Path(spec).name} stays red ({result.status}); {restored} file(s) put back")
                watchdog.end(in_flight)
            if not fixed_now or sweep == sweeps:
                break

            if not self.hard_fits("closing repair, second sweep (whole suite)", self.suite_est(len(self.e2e_specs)) + self.closing_repair_min_sec(),
                                  reserve=HARD_END_FINISH_SEC):
                break

            again = run_e2e(self.project_dir, self.e2e_specs, log=log)
            if again.skipped or again.ok:
                break
            self.record_run(list(self.e2e_specs), False, again.output, **unrun_kw(again, self.record_run))
            suite_output = again.output
        return repaired

    def repair_debt_specs(self) -> int:
        ledger = dict(self.__dict__.get("debt_specs") or {})

        unknown_by: dict[str, str] = {}
        for req_id, candidates in dict(self.__dict__.get("unknown_debts") or {}).items():
            for spec in candidates or []:
                if spec not in ledger:
                    unknown_by.setdefault(spec, str(req_id))
        specs = [s for s in self.e2e_specs if (s in ledger or s in unknown_by) and s in (self.spec_context or {}) and (self.project_dir / s).is_file()]
        specs = self.without_seed_quarantined(specs, "closing debt repair")
        if not specs or not setting_on("closing_debt_first"):
            return 0
        if LLM.quota_exhausted:
            log("[verify] closing debt repair: key out of balance, not started")
            return 0
        per_spec = float(self.CLOSING_PER_SPEC_SEC[0]) + 2 * self.suite_est(1)
        owed_only = [s for s in specs if s in ledger]
        for attempt in ([specs, owed_only] if owed_only and len(owed_only) < len(specs) else [specs]):

            specs = attempt

            need = self.build_est() + self.suite_est(len(specs)) + self.closing_repair_min_sec()
            window = need + (len(specs) - 1) * per_spec
            start = clock.now()
            left = min(window, self.closing_budget())
            if self.hard_end() is not None:

                left = min(left, self.hard_left(self.closing_chain_sec(len(self.e2e_specs))))
            until = start + left
            if left >= need:
                break
        else:
            log(f"[verify] closing debt repair not started: {int(max(0.0, until - clock.now()))} s for it, {int(need)} s needed")
            return 0
        owed = [s for s in specs if s in ledger]
        log(f"[verify] closing, first job: {len(owed)} spec(s) that passed at their own batch's end and were broken by a later "
            f"batch: {[Path(s).name + ' (by ' + str(ledger[s].get('by')) + ')' for s in owed]}"
            + (f"; {len(specs) - len(owed)} candidate(s) of unknown debts, confirming run: "
               f"{[Path(s).name + ' (by ' + unknown_by[s] + ')' for s in specs if s not in ledger]}" if len(owed) < len(specs) else ""))
        build = build_frontend(self.project_dir, log=log)
        if not build.ok:
            log("[verify] closing debt repair: the frontend does not build; left to the closing verification")
            return 0
        began = clock.now()
        run = run_e2e(self.project_dir, specs, log=log)
        if run.skipped or run_unfinished(run):
            log("[verify] closing debt repair: the owed specs' run did not finish; left to the closing verification")
            return 0
        self.note_duration("e2e", clock.now() - began, len(specs))
        self.record_run(specs, run.ok, run.output, **unrun_kw(run, self.record_run))
        red = failed_spec_names(run.output) & {Path(s).name for s in specs} if not run.ok else set()
        if not red:
            log("[verify] closing debt repair: " + ("every owed spec passes now" if run.ok else "the run names no owed spec; nothing repaired"))
            return 0
        failing = [s for s in specs if Path(s).name in red]
        notes = {s: (f" This spec passed when its own batch ended; the later requirement {ledger[s].get('by')} changed files it "
                     "depends on and broke it -- restore its behaviour together with that requirement's.") if s in ledger else
                 (f" This spec passed when its own batch ended; the regression run of the later requirement {unknown_by[s]} "
                  "named no failing spec (the run did not complete), and this run names it red -- restore its behaviour "
                  "together with that requirement's.") for s in failing}

        tried: list[str] = []
        repaired = self.repair_failing_specs(run.output, only=failing, until=until, pool=list(self.green_specs), notes=notes,
                                             tried=tried)
        fixed = set(self.__dict__.get("closing_fixed") or [])
        self.debt_tried = [s for s in failing if s in tried and s not in fixed]
        log(f"[verify] closing debt repair finished: {repaired} of {len(failing)} owed spec(s) repaired")
        self.report["debt_repair"] = {"owed": [Path(s).name for s in specs], "red": sorted(red), "repaired": repaired}
        return repaired

    SHARED_SWEEP_BASE_SEC = 120.0
    SHARED_SWEEP_PER_FILE_SEC = 30.0
    SHARED_SWEEP_MARGIN_SEC = 900.0
    SHARED_SWEEP_CAP_SEC = 1200.0

    def leaf_dependencies(self) -> dict[str, list[str]]:
        try:
            leaves = atomic_nodes(load_tree(self.requirements_dir))
            return {str(leaf.get("id")): [str(d) for d in (leaf.get("dependencies") or [])]
                    for leaf, _ in leaves if isinstance(leaf.get("dependencies") or [], list)}
        except Exception as exc:
            log(f"[shared-sweep] dependencies not read, batch order kept: {exc!r}")
            return {}

    def shared_sweep_line(self) -> str:
        sweep = self.report.get("shared_sweep") or {}
        return (sweep.get("summary") or f"skipped: {sweep.get('skipped')}") if sweep else "not run"

    def planned_end(self) -> float:
        return self.started + (LARGE_TREE_CAP_SEC if self.large_tree else self.time_budget + 3600 + final_judgement_reserve(len(self.e2e_specs)))

    def shared_context_sweep(self, last_suite: StepResult) -> str:
        from agent.shared_sweep import fresh_green_names, red_only_in_shared, shared_literals, sweep_order, sweep_summary
        from agent.verify import run_e2e_in_order

        if not SETTINGS.get("shared_sweep", True):
            return ""
        specs = [s for s in self.e2e_specs if (self.project_dir / s).is_file()]
        results = self.report["verification"]
        why = ""
        if len(specs) < 2:
            why = "fewer than two spec files"
        elif self.install_failed or not results.get("frontend_build") or last_suite.skipped:
            why = "the delivered sources were not judged by a browser run"
        green = None if why else fresh_green_names(specs, last_suite.ok, last_suite.output)
        if not why and green is None:
            why = "the last fresh-data run names no spec file (a global fault)"
        estimate = self.SHARED_SWEEP_BASE_SEC + self.SHARED_SWEEP_PER_FILE_SEC * len(specs)
        left = self.planned_end() - clock.now() - self.SHARED_SWEEP_MARGIN_SEC
        if self.hard_end() is not None:

            left = min(left, self.hard_left(HARD_END_TAIL_SEC) - 60.0 - 300.0)
        if not why and left < estimate:
            why = f"the closing budget is short ({int(max(0, left))} s left, the sweep needs about {int(estimate)} s)"
        if why:
            log(f"[shared-sweep] skipped: {why}")
            self.report["shared_sweep"] = {"skipped": why}
            return ""
        members_of = {spec: [str(m) for m in (self.spec_context.get(spec) or {}).get("members") or []] for spec in specs}
        order = sweep_order(specs, members_of, self.leaf_dependencies())
        log(f"[shared-sweep] {len(order)} spec file(s) in dependency order against one server and one database (advisory)")
        swept = run_e2e_in_order(self.project_dir, order, log=log, time_limit=min(left, self.SHARED_SWEEP_CAP_SEC, 600.0 + 90.0 * len(order)))
        if swept.skipped:
            log(f"[shared-sweep] skipped: {swept.note}")
            self.report["shared_sweep"] = {"skipped": swept.note}
            return ""
        red_only = red_only_in_shared(order, green, swept.statuses)
        texts: dict[str, str] = {}
        for spec in order:
            try:
                texts[spec] = (self.project_dir / spec).read_text(encoding="utf-8", errors="replace")
            except OSError:
                texts[spec] = ""
        details = []
        for spec, earlier in red_only:
            shared = shared_literals(texts.get(spec, ""), {e: texts.get(e, "") for e in earlier}, texts)
            nearest = list(shared.items())[-4:]
            hint = "; ".join(f"{Path(e).name} also uses {', '.join(repr(v) for v in values)}" for e, values in nearest)
            if len(shared) > len(nearest):
                hint += f"; and {len(shared) - len(nearest)} more earlier spec(s)"
            log(f"[shared-sweep] {Path(spec).name} is green on fresh data but red only after earlier specs "
                f"({', '.join(Path(e).name for e in earlier[-8:])}{' and more' if len(earlier) > 8 else ''})"
                + (f"; shared quoted texts: {hint}" if hint else "; it shares no quoted text with them"))
            first_lines = [line for line in (swept.outputs.get(spec) or "").splitlines() if any(k in line for k in ("Error", "expect(", "Expected", "Received", "Timeout", "could not"))]
            if first_lines:
                log("[shared-sweep]   " + " | ".join(first_lines[:4])[:600])
            details.append({"spec": Path(spec).name, "earlier": [Path(e).name for e in earlier],
                            "shared_texts": {Path(e).name: v for e, v in shared.items()}})
        if swept.server_errors and red_only:
            log("[shared-sweep] server errors during the sweep:" + swept.server_errors[:1500])
        summary = sweep_summary(len(order), swept.statuses, red_only, swept.note)
        self.report["shared_sweep"] = {"summary": summary, "order": [Path(s).name for s in order], "red_only_after_earlier": details}
        log(f"[shared-sweep] {summary}")
        return summary

    def verify(self) -> None:
        results = self.report["verification"]

        if self.hard_fits("closing build check", self.build_est()):
            began = clock.now()
            build = build_frontend(self.project_dir, log=log)
            self.note_duration("build", clock.now() - began)
            if not build.ok and getattr(build, "infra", ""):

                log(f"[verify] frontend build failed for an infrastructure reason ({build.infra}); no repair round")
                build_ok = False
            elif not build.ok:
                build_ok = self.repair_round("frontend-build", lambda: build_frontend(self.project_dir, log=log), build.output)
            else:
                build_ok = True
        else:
            build_ok = False
        results["frontend_build"] = build_ok

        if self.hard_fits("closing probe check", PROBE_WORST_SEC):
            probe, _ = probe_server(self.project_dir, log=log)
            if not probe.ok:
                probe_ok = self.repair_round("server-probe", lambda: probe_server(self.project_dir, log=log)[0], probe.output)
            else:
                probe_ok = True
        else:
            probe_ok = False
        results["server_probe"] = probe_ok

        suite_cut = not self.hard_fits("closing suite run and closing repair", self.suite_est(len(self.e2e_specs)) + self.closing_repair_min_sec())
        if suite_cut:
            results["e2e"] = False
            e2e = StepResult(True, "not run: the hard end is near", skipped=True)
        else:
            began = clock.now()
            e2e = run_e2e(self.project_dir, self.e2e_specs, log=log)
            if not e2e.skipped:
                self.note_duration("e2e", clock.now() - began, len(self.e2e_specs))
        if suite_cut:
            pass
        elif e2e.skipped:
            results["e2e"] = "skipped"
        elif e2e.ok:
            results["e2e"] = True
            self.record_run(list(self.e2e_specs), True, e2e.output)
        else:

            self.record_run(list(self.e2e_specs), False, e2e.output, **unrun_kw(e2e, self.record_run))
            results["e2e"] = False
            if failed_spec_names(e2e.output) & {Path(s).name for s in self.spec_context}:
                repaired = self.repair_failing_specs(e2e.output)
                log(f"[verify] closing repair finished: {repaired} spec(s) repaired")
            elif self.evolution_unverified_only(e2e.output):

                log("[verify] the only failing tests are unverified inherited tests of modified leaves; no whole-suite repair round")
            else:

                results["e2e"] = self.repair_round("playwright-e2e", self.full_check, e2e.output, test_files=self.all_tests_on_disk())
        last_suite = e2e

        if setting_on("shared_sweep_handle") and SETTINGS.get("shared_sweep", True) and not suite_cut and not e2e.skipped:
            try:
                self.shared_sweep_handle(e2e)
            except Exception as exc:
                log(f"[shared-sweep] handling crashed: {exc!r}")

        if self.hard_fits("final build", BUILD_WORST_SEC, reserve=HARD_END_TAIL_SEC):
            final_build = build_frontend(self.project_dir, log=log)
        else:
            final_build = StepResult(False, "not run: the hard end is near")
        results["frontend_build"] = bool(final_build.ok)

        wanted = bool(self.verify_repairs) or suite_cut
        judge = wanted and bool(final_build.ok) and self.hard_fits(
            "final judgement (probe + whole suite; it only judges)", PROBE_WORST_SEC + e2e_worst(len(self.e2e_specs)), reserve=HARD_END_TAIL_SEC)
        if wanted and final_build.ok and not judge:
            results["server_probe"] = False
            results["e2e"] = False
        elif self.verify_repairs and not final_build.ok:

            log("[verify] the delivered sources do not build; probe and E2E are not judged against a stale dist")
            results["server_probe"] = False
            results["e2e"] = False
        elif judge:
            log(f"[verify] {self.verify_repairs} repair round(s) ran; judging the delivered state once more")

            results["server_probe"] = False
            results["e2e"] = False
            final_probe, _ = probe_server(self.project_dir, log=log)
            results["server_probe"] = bool(final_probe.ok)
            final_e2e = run_e2e(self.project_dir, self.e2e_specs, log=log)
            if final_e2e.skipped:
                results["e2e"] = "skipped"
            else:
                results["e2e"] = bool(final_e2e.ok)
                self.record_run(list(self.e2e_specs), final_e2e.ok, final_e2e.output, **unrun_kw(final_e2e, self.record_run))
                last_suite = final_e2e
        if results.get("e2e") is False and results.get("frontend_build") and results.get("server_probe") and self.failed_members and not last_suite.skipped:

            red = failed_spec_names(last_suite.output)
            if red:
                passing_members = {m for spec, ctx in self.spec_context.items() if Path(spec).name not in red and spec not in self.revised_specs for m in ctx["members"]}
                failing_members = {m for spec, ctx in self.spec_context.items() if Path(spec).name in red for m in ctx["members"]}
                cleared = [m for m in self.failed_members if m in passing_members and m not in failing_members and m not in self.no_evidence_members]
                if cleared:
                    log(f"[verify] {len(cleared)} leaf/leaves recorded as failed earlier pass in the final suite")
                    for member in cleared:
                        self.trace.node_state(member, "PASSED", "test")
                    self.failed_members = [m for m in self.failed_members if m not in cleared]
        if results.get("e2e") is True and results.get("frontend_build") and results.get("server_probe") and self.failed_members:

            weak = {m for spec, ctx in self.spec_context.items() if spec in self.revised_specs for m in ctx["members"]}
            cleared = [m for m in self.failed_members if m not in self.no_evidence_members and m not in weak]
            log(f"[verify] final suite green: {len(cleared)} leaf/leaves recorded as failed earlier are passing now"
                + (f"; {len(self.failed_members) - len(cleared)} without an untouched acceptance spec stay failed" if len(cleared) != len(self.failed_members) else ""))
            for member in cleared:
                self.trace.node_state(member, "PASSED", "test")
            self.failed_members = [m for m in self.failed_members if m not in cleared]

        self.final_suite = self.final_suite_books(last_suite)

        handled = self.shared_sweep_handled()
        try:
            self.shared_context_sweep(last_suite) if self.shared_sweep_final_due(handled) else None
        except Exception as exc:
            log(f"[shared-sweep] failed, nothing booked: {exc!r}")
        if handled is not None:
            self.report.setdefault("shared_sweep", {})["handled"] = handled
        self.trace.git_commit("verification: build/server/e2e gates")

    def book_open_findings(self, req_id: str, findings: list[dict[str, Any]]) -> None:
        try:
            from agent.audit import finding_fingerprint

            book = self.__dict__.setdefault("open_findings", {})
            for finding in findings or []:
                if isinstance(finding, dict) and finding.get("layer") != "model":
                    book[finding_fingerprint(finding)] = {"class": stage_handoff.finding_class(finding),
                                                          "where": str(finding.get("where") or "")[:160], "batch": req_id}
        except Exception as exc:
            log(f"[handoff] findings not booked: {exc!r}")

    def close_open_findings(self, findings: list[dict[str, Any]]) -> None:
        try:
            from agent.audit import finding_fingerprint

            book = self.__dict__.setdefault("open_findings", {})
            for finding in findings or []:
                if isinstance(finding, dict):
                    book.pop(finding_fingerprint(finding), None)
        except Exception as exc:
            log(f"[handoff] findings not closed: {exc!r}")

    def held_queue_mark(self, node: dict[str, Any], queue: list[tuple[dict[str, Any], int]], done_ids: list[str]) -> None:
        try:
            from agent import finding_owner

            if not finding_owner.enabled():
                return
            current = {str(m) for m in (node.get("members") or [node.get("id")])}
            ahead: dict[str, str] = {}
            for rest, _times in queue:
                if str(rest.get("id")) in done_ids:
                    continue
                for member in rest.get("members") or [rest.get("id")]:
                    if str(member) not in current:
                        ahead.setdefault(str(member), str(rest.get("id")))
            self.held_ahead = ahead
        except Exception as exc:
            self.held_ahead = {}
            log(f"[audit] batches ahead not determined, nothing is held: {exc!r}")

    def hold_for_owner_batches(self, req_id: str, node: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            from agent import finding_owner

            ahead = dict(self.__dict__.get("held_ahead") or {})
            current = {str(m) for m in (node.get("members") or [node.get("id")])}
            future = set(ahead) - current
            if not future or not findings:
                return findings
            send, held = finding_owner.split_for_owner_batches(findings, future)
            if not held:
                return findings
            book = self.__dict__.setdefault("held_findings", {})
            finding_owner.book_held(book, held)
            counts: dict[str, int] = {}
            for finding, owners in held:
                for leaf in owners:
                    counts[leaf] = counts.get(leaf, 0) + 1
                log(f"[audit] {req_id}: held (owners {', '.join(owners)} not built yet): "
                    f"{str(finding.get('where', ''))[:40]}: {str(finding.get('problem', ''))[:140]}")
            for leaf, n in sorted(counts.items()):
                log(f"[audit] held for {leaf}: {n} finding(s) (found by {req_id}'s audit; batch {ahead.get(leaf)} has not run yet)")
            return send
        except Exception as exc:
            log(f"[audit] {req_id}: holding later batches' findings crashed, all sent now: {exc!r}")
            return findings

    def held_delivery_note(self, req_id: str, node: dict[str, Any]) -> str:
        try:
            from agent import finding_owner
            from agent.audit import finding_fingerprint

            if not finding_owner.enabled():
                return ""
            rows = finding_owner.rows_for(self.__dict__.get("held_findings") or {},
                                          [str(m) for m in (node.get("members") or [node.get("id")])])
            if not rows:
                return ""
            note = finding_owner.held_note(rows)
            delivered = self.__dict__.setdefault("held_delivered", set())
            delivered.update(finding_fingerprint(r) for r in rows)
            log(f"[audit] delivered {len(rows)} held finding(s) to {req_id}")
            return note
        except Exception as exc:
            log(f"[audit] {req_id}: held findings not delivered: {exc!r}")
            return ""

    def held_to_closing(self, pairs: list[tuple[dict[str, Any], str]], why: str) -> None:
        from agent.audit import finding_fingerprint

        book = self.__dict__.setdefault("open_findings", {})
        unrun = self.report.setdefault("held_unrun", []) if isinstance(getattr(self, "report", None), dict) else []
        for row, leaf in pairs:
            self.book_open_findings(f"held for {leaf}", [row])
            full = {key: str(row.get(key) or "") for key in ("where", "requirement", "problem", "fix")}
            entry = book.get(finding_fingerprint(row))
            if isinstance(entry, dict):
                entry.update(full)
            unrun.append({**full, "owner_leaf": list(row.get("owner_leaf") or []), "held_for": str(leaf)})
        if pairs:
            log(f"[audit] {len(pairs)} held finding(s) handed to closing ({why})")

    def held_release_into(self, req_id: str, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            from agent.audit import finding_fingerprint

            own = [f for f in findings or [] if isinstance(f, dict)]
            if str(req_id) in (self.__dict__.get("held_audit_crashed") or set()):
                self.__dict__.setdefault("held_audit_seen", {}).pop(str(req_id), None)
            else:
                self.__dict__.setdefault("held_audit_seen", {})[str(req_id)] = sorted({finding_fingerprint(f) for f in own})
            released = list(self.__dict__.get("held_released") or [])
            if not released:
                return findings
            self.held_released = []
            seen = {finding_fingerprint(f) for f in own}
            extra = [r for r in released if isinstance(r, dict) and finding_fingerprint(r) not in seen]
            if extra:
                log(f"[audit] {req_id}: {len(extra)} released held finding(s) join this audit's findings (sent now, as before v168)")
            return list(findings or []) + extra
        except Exception as exc:
            log(f"[audit] {req_id}: released held findings not joined: {exc!r}")
            return findings

    def held_after_batch(self, node: dict[str, Any], ok: bool = False) -> None:
        try:
            from agent import finding_owner
            from agent.audit import finding_fingerprint

            if not finding_owner.enabled():
                return
            self.__dict__.pop("held_delivered", None)
            book = self.__dict__.get("held_findings") or {}
            members = [str(m) for m in (node.get("members") or [node.get("id")])]
            rows = finding_owner.rows_for(book, members)
            if not rows:
                return
            seen = self.__dict__.get("held_audit_seen") or {}
            reported = set(seen.get(str(node.get("id"))) or []) if str(node.get("id")) in seen else None
            repaired = set(getattr(self, "audit_sent_fingerprints", None) or [])
            gone, still = [], []
            for row in rows:
                fp = finding_fingerprint(row)
                if ok and reported is not None and (fp not in reported or fp in repaired):
                    gone.append(row)
                else:
                    still.append(row)
            finding_owner.drop_rows(book, gone)
            for leaf in members:
                book.pop(leaf, None)
            ahead = set(self.__dict__.get("held_ahead") or {}) - set(members)
            released = []
            for row in still:
                if not (set(finding_owner.owner_leaves(row)) & ahead & set(book)):
                    finding_owner.drop_rows(book, [row])
                    released.append(row)
            if released:
                self.held_released = list(self.__dict__.get("held_released") or []) + released
            if gone or released:
                log(f"[audit] held findings after {node.get('id')}: {len(gone)} settled, {len(released)} released to the next "
                    f"audit, {len(still) - len(released)} still held for a later owner batch")
        except Exception as exc:
            log(f"[audit] held findings after {node.get('id')}: {exc!r}")

    def held_hand_to_closing(self) -> None:
        try:
            from agent import finding_owner

            if not finding_owner.enabled():
                return
            self.held_ahead = {}
            book = self.__dict__.get("held_findings") or {}
            released = [r for r in self.__dict__.get("held_released") or [] if isinstance(r, dict)]
            if not book and not released:
                return
            pairs = finding_owner.all_rows(book) + [(r, "a later audit") for r in released]
            book.clear()
            self.held_released = []
            self.held_to_closing(pairs, "their batches did not run in this process, or no later audit took them")
        except Exception as exc:
            log(f"[audit] held findings not handed to the closing: {exc!r}")

    SHARED_REPLAY_PREDECESSORS = 3

    def shared_sweep_handle(self, last_suite: StepResult) -> int:
        from agent import shared_handle

        self.shared_sweep_ran = True
        self.shared_context_sweep(last_suite)
        sweep = self.report.get("shared_sweep") or {}
        details = [d for d in sweep.get("red_only_after_earlier") or [] if isinstance(d, dict)]
        if not details or not setting_on("shared_sweep_handle"):
            return 0
        end = clock.now() + setting_seconds("shared_sweep_handle_sec", 2400.0)
        if self.hard_end() is not None:
            end = min(end, clock.now() + self.hard_left(HARD_END_FINISH_SEC + HARD_END_ROUND_SEC))
        by_name = {Path(s).name: s for s in self.e2e_specs}
        books: list[dict[str, Any]] = []
        kept = 0
        cap = int(setting_seconds("shared_sweep_handle_max_specs", 6))
        for index, detail in enumerate(details):
            name = str(detail.get("spec"))
            spec = by_name.get(name)
            entry: dict[str, Any] = {"spec": name, "kind": shared_handle.UNKNOWN, "culprit": None, "disposition": "booked"}
            books.append(entry)
            if spec is None or index >= cap or clock.now() + 2 * self.suite_est(2) > end:
                entry["why"] = "over the cap or the time" if spec is not None else "not in the suite"
                continue
            try:
                self.shared_handle_one(spec, [by_name[e] for e in detail.get("earlier") or [] if e in by_name], entry, end)
                kept += 1 if entry.get("disposition", "").startswith("repaired") else 0
            except Exception as exc:
                entry["why"] = f"crashed: {exc!r}"[:200]
                log(f"[shared-sweep] handling {name} crashed: {exc!r}")
        counts: dict[str, int] = {}
        for entry in books:
            counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
        sweep["handled"] = books
        self.report["shared_sweep"] = sweep
        self.verify_repairs += kept
        log(f"[shared-sweep] handled {len(books)} red-only spec(s): {counts}; repaired and kept: {kept}; booked: "
            f"{[e['spec'] + ' (' + e['kind'] + ')' for e in books if not e.get('disposition', '').startswith('repaired')]}")
        return kept

    def shared_sweep_handled(self) -> list[dict[str, Any]] | None:
        return (self.report.get("shared_sweep") or {}).get("handled") if self.__dict__.get("shared_sweep_ran") else None

    def shared_sweep_final_due(self, handled: list[dict[str, Any]] | None) -> bool:
        if handled is None and not self.__dict__.get("shared_sweep_ran"):
            return True
        if any(str(h.get("disposition", "")).startswith("repaired") for h in handled or []):
            return True
        log("[shared-sweep] the sweep before the final judgement stands for the delivered state (no repair of it kept)")
        return False

    def shared_handle_one(self, spec: str, earlier: list[str], entry: dict[str, Any], end: float) -> None:
        from agent import shared_handle

        name = Path(spec).name
        texts = {s: (self.project_dir / s).read_text(encoding="utf-8", errors="replace") for s in [spec] + earlier
                 if (self.project_dir / s).is_file()}
        culprit, error = None, ""
        if not earlier:
            run = run_e2e(self.project_dir, [spec], log=log, shared_data=True)
            if run.skipped or run.ok:
                entry["why"] = "not reproduced alone in one database"
                return
            error = shared_handle.first_error(str(run.output or ""), name)
        else:
            for candidate in list(reversed(earlier))[: self.SHARED_REPLAY_PREDECESSORS]:
                if clock.now() + self.suite_est(2) > end:
                    break
                run = run_e2e(self.project_dir, [candidate, spec], log=log, shared_data=True)
                if run.skipped:
                    break
                if not run.ok and name in failed_spec_names(str(run.output or "")):
                    culprit, error = candidate, shared_handle.first_error(str(run.output or ""), name)
                    break
            if culprit is None:
                entry["why"] = f"not reproduced behind any of its {self.SHARED_REPLAY_PREDECESSORS} nearest predecessors"
                return
        kind, evidence = shared_handle.classify(error, texts.get(spec, ""), texts.get(culprit, "") if culprit else texts.get(spec, ""),
                                                within=culprit is None)
        entry.update(kind=kind, culprit=Path(culprit).name if culprit else "(the same file)", evidence=evidence, error=error[:300])
        log(f"[shared-sweep] {name}: {kind} -- {evidence}" + (f" (behind {Path(culprit).name})" if culprit else " (within the file)"))
        context = self.spec_context.get(spec)
        inherited = spec in (self.__dict__.get("_frozen_inherited_specs") or {})
        if kind == shared_handle.UNKNOWN or not context or inherited:
            entry["why"] = ("an inherited spec: kept as it is" if inherited else "not a spec of this run" if not context else
                            "the evidence does not tell spec from application")
            return
        target = [culprit, spec] if culprit else [spec]
        if kind == shared_handle.POLLUTION:
            entry["disposition"] = self.shared_spec_repair(spec, target, context, shared_handle.spec_problem(
                name, Path(culprit).name if culprit else "its own earlier tests", evidence, error), end)
        else:
            entry["disposition"] = self.shared_app_repair(spec, target, context, shared_handle.app_problem(
                name, Path(culprit).name if culprit else "its own earlier tests", evidence, error), end)

    def shared_spec_repair(self, spec: str, target: list[str], context: dict[str, Any], problem: str, end: float) -> str:
        if clock.now() + 2 * self.suite_est(2) + 300 > end or LLM.quota_exhausted:
            return "booked (no time for a spec repair)"
        node = context.get("node") or {}
        tests = dict(context.get("tests") or {})
        old = (self.project_dir / spec).read_text(encoding="utf-8", errors="replace")
        files = [AuthoredFile(path=spec, content=old)]
        try:
            with self.calls_end_by(end):
                repaired = self.repair_tests(node, self.spec_prefix_of(node), files, problem, contract=str(context.get("contract") or ""))
        except Exception as exc:
            return f"booked (spec repair failed: {type(exc).__name__})"
        new = next((f.content for f in repaired if f.path == spec), old)
        if new == old:
            return "booked (the spec came back unchanged)"
        write_text(self.project_dir / spec, new)
        alone = run_e2e(self.project_dir, [spec], log=log)
        behind = run_e2e(self.project_dir, target, log=log, shared_data=True) if alone.ok and not alone.skipped else alone
        if alone.ok and not alone.skipped and behind.ok and not behind.skipped:
            snapshot = self.__dict__.get("test_snapshot")
            if isinstance(snapshot, dict):
                snapshot[spec] = new
            self.revised_specs.add(spec)
            tests[spec] = new
            context["tests"] = tests
            log(f"[shared-sweep] {Path(spec).name}: spec repaired -- green alone and behind its culprit; kept")
            return "repaired (spec)"
        write_text(self.project_dir / spec, old)
        log(f"[shared-sweep] {Path(spec).name}: the rewritten spec is not green alone and in the replay; put back")
        return "booked (spec repair not green)"

    def shared_app_repair(self, spec: str, target: list[str], context: dict[str, Any], problem: str, end: float) -> str:
        if clock.now() + 3 * self.suite_est(2) + 300 > end or LLM.quota_exhausted:
            return "booked (no time for an application repair)"
        guard = [s for s in self.green_specs[:3] if s not in target]
        before = self.snapshot_sources()

        def check() -> TestOutcome:
            self.restore_tests()
            build = build_frontend(self.project_dir, log=log)
            if not build.ok:
                return TestOutcome(False, "FRONTEND BUILD FAILED\n" + build.output, infra=getattr(build, "infra", ""))
            replay = run_e2e(self.project_dir, target, log=log, shared_data=True)
            if replay.skipped or not replay.ok:
                return TestOutcome(False, "E2E FAILED in one shared database (the graders' setting)\n" + str(replay.output or ""))
            for group in ([spec], guard):
                if not group:
                    continue
                run = run_e2e(self.project_dir, group, log=log)
                if run.skipped or not run.ok:
                    return TestOutcome(False, "E2E FAILED on fresh data\n" + str(run.output or ""))
            return TestOutcome(True, "E2E PASSED in the shared replay and alone")

        fixer = Implementer(self.llm, self.project_dir, check, max_rounds=2, extra_rounds=0, deadline=end,
                            contract=str(context.get("contract") or self.contract), relevant=context.get("relevant"), log=log)
        self.set_call_deadline(fixer, end)
        in_flight = watchdog.begin(f"shared-sweep repair of {Path(spec).name}", lambda: self.restore_sources(before))
        try:
            result = fixer.implement(context.get("node") or {"id": "SHARED", "name": Path(spec).name}, dict(context.get("tests") or {}),
                                     TestOutcome(False, problem), extra_context=problem)
        except Exception as exc:
            result = ImplementResult("gave_up", [], repr(exc))
        finally:
            watchdog.end(in_flight)
        if result.status == "passed":
            log(f"[shared-sweep] {Path(spec).name}: application repaired -- green in the shared replay and alone; kept")
            return "repaired (application)"
        restored = self.restore_sources(before)
        log(f"[shared-sweep] {Path(spec).name}: the application repair ended {result.status}; {restored} file(s) put back")
        return f"booked (application repair {result.status})"

    ROLLBACK_EVIDENCE_CAP = 12000

    def note_rollback_evidence(self, req_id: str, findings: list[dict[str, Any]], output: str) -> None:
        try:
            from agent.audit import finding_fingerprint

            book = self.__dict__.setdefault("audit_rollback_evidence", {})
            for finding in findings or []:
                entries = book.setdefault(finding_fingerprint(finding), [])
                entries.append({"batch": req_id, "output": strip_terminal_codes(output)[-self.ROLLBACK_EVIDENCE_CAP:]})
                del entries[:-2]
        except Exception as exc:
            log(f"[audit] rollback evidence not kept: {exc!r}")

    def route_given_up_findings(self, req_id: str, findings: list[dict[str, Any]], given_up: list[str]) -> list[str]:
        if not setting_on("audit_route_stated"):
            return given_up
        try:
            from agent import stated_route
            from agent.audit import finding_fingerprint
        except Exception:
            return given_up
        routed = self.__dict__.setdefault("audit_routed", {})
        keep = list(given_up)
        for finding in findings or []:
            fp = finding_fingerprint(finding)
            if fp not in given_up:
                continue
            entry = routed.get(fp)
            if entry is None:
                control = stated_route.stated_control(finding)
                same = next((dict(e) for e in routed.values() if control and e.get("control") == list(control)), None)
                if same is not None:

                    entry = {**same, "sends_left": 1 if same.get("spec_fixes") else 0}
                elif control:
                    entry = self.route_stated_control(req_id, finding, fp, control)
                elif stated_route.quotes_requirement(finding):
                    entry = {"kind": "stated text", "sends_left": 0, "status": "open: needs a spec or precondition fix",
                             "specs": self.rollback_specs(fp)}
                    log(f"[audit] {req_id}: a finding that quotes the requirement reached the rollback cap; it stays OPEN "
                        f"(handoff), the specs that undid its repairs: {entry['specs'] or 'not readable'}: "
                        f"{str(finding.get('problem', ''))[:140]}")
                else:
                    entry = {"kind": "other", "sends_left": 0, "status": "given up after rollbacks"}
                entry["where"] = str(finding.get("where") or "")[:160]
                entry["problem"] = str(finding.get("problem") or "")[:200]
                routed[fp] = entry
                self.mark_open_finding_route(fp, entry)
            if int(entry.get("sends_left") or 0) > 0:
                entry["sends_left"] = int(entry["sends_left"]) - 1
                keep = [g for g in keep if g != fp]
                log(f"[audit] {req_id}: the routed finding goes to one more repair after its spec fix: {entry.get('problem', '')[:120]}")
        return keep

    def rollback_specs(self, fp: str) -> list[str]:
        names: list[str] = []
        for entry in (self.__dict__.get("audit_rollback_evidence") or {}).get(fp) or []:
            output = str(entry.get("output") or "")
            names += sorted(failed_spec_names(output)) if output else []
        return list(dict.fromkeys(names))

    def route_stated_control(self, req_id: str, finding: dict[str, Any], fp: str, control: tuple[str, str, str]) -> dict[str, Any]:
        from agent import stated_route

        name, to_role, from_role = control
        evidence = (self.__dict__.get("audit_rollback_evidence") or {}).get(fp) or []
        outputs = [str(e.get("output") or "") for e in evidence]
        folder = self.project_dir / "backend" / "test-e2e"
        changed: dict[str, list[int]] = {}
        refused: list[str] = []
        names = list(dict.fromkeys(n for out in outputs for n in sorted(failed_spec_names(out))))
        for spec_name in names:
            path = folder / spec_name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            failing: set[int] = set()
            for out in outputs:
                failing |= stated_route.failing_lines(out, spec_name)
            lines = stated_route.route_lines(text, name, from_role, failing)

            named = any(stated_route.names_control(out, name, from_role) for out in outputs)
            if not lines or not (named or any(abs(n - f) <= 3 for n in lines for f in failing)):
                continue
            new = stated_route.rewrite_role(text, lines, name, from_role, to_role)
            if new == text:
                continue
            rel = path.relative_to(self.project_dir).as_posix()
            why = f"the requirement states {to_role} '{name}' (an audit finding at the rollback cap)"
            if not self.write_routed_spec(rel, text, new, why):
                refused.append(spec_name)
                continue
            changed[spec_name] = lines
        if changed:
            log(f"[audit] {req_id}: the requirement states {to_role} '{name}'; spec line(s) asserting a {from_role} "
                f"rewritten (the spec was the defect): {changed}")
            return {"kind": "stated control type", "control": [name, to_role, from_role], "sends_left": 1,
                    "status": "spec fixed, sent once more", "spec_fixes": changed}
        why = (f"the rewrite of {refused} did not parse and was put back" if refused else
               f"no spec line that undid its repairs addresses it as a {from_role}")
        log(f"[audit] {req_id}: the requirement states {to_role} '{name}', but {why}; it stays OPEN (handoff), specs that "
            f"undid the repairs: {names or 'not readable'}")
        return {"kind": "stated control type", "control": [name, to_role, from_role], "sends_left": 0,
                "status": "open: spec fix put back (does not parse)" if refused else "open: no contradicting spec line found",
                "specs": names}

    def write_routed_spec(self, rel: str, old: str, new: str, why: str) -> bool:
        frozen = self.__dict__.get("_frozen_inherited_specs") or {}
        snapshot = self.__dict__.get("test_snapshot")

        def put(content: str, reason: str) -> None:
            if rel in frozen:
                self.evolution_update_inherited_spec(rel, content, reason)
            else:
                write_text(self.project_dir / rel, content)
                if isinstance(snapshot, dict) and rel in snapshot:
                    snapshot[rel] = content
        try:
            put(new, why)
            defects = static_test_defects(self.project_dir, [rel])
            if defects:
                log(f"[audit] the routed rewrite of {Path(rel).name} does not parse; put back: {str(defects)[:200]}")
                put(old, "routed rewrite put back (does not parse)")
                return False
            if isinstance(snapshot, dict) and rel in snapshot:
                self.revised_specs.add(rel)
            self.__dict__.setdefault("spec_fixes", []).append({"file": Path(rel).name, "why": why[:200]})
            return True
        except Exception as exc:
            log(f"[audit] routed rewrite of {rel} failed: {exc!r}")
            return False

    def mark_open_finding_route(self, fp: str, entry: dict[str, Any]) -> None:
        book = self.__dict__.setdefault("open_findings", {})
        if fp in book:
            book[fp]["route"] = str(entry.get("status") or "")[:120]

    def audit_recheck_session(self, req_id: str, node: dict[str, Any], probed: list[dict[str, Any]]) -> dict[str, Any]:
        rechecked: dict[str, Any] = {"ran": False, "gone": [], "still": [], "unchecked": list(probed)}
        try:
            if self.over_budget() or not self.hard_fits("the session revoke re-check", 180.0):
                log(f"[audit] {req_id}: {len(probed)} session-revoke finding(s) not re-checked (no time): they stay open")
                return rechecked
            from agent.audit import finding_fingerprint, recheck_session_findings

            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
            rechecked = recheck_session_findings(self.project_dir, whole, [str(m) for m in (node.get("members") or [node.get("id")])],
                                                 probed, log=log)
            gone = [f for f in rechecked.get("gone") or [] if isinstance(f, dict)]
            if gone:
                self.audit_sent_fingerprints = list(dict.fromkeys(
                    list(getattr(self, "audit_sent_fingerprints", None) or []) + [finding_fingerprint(f) for f in gone]))
                self.close_open_findings(gone)
            self.reopen_still_there(req_id, rechecked)
        except Exception as exc:
            log(f"[audit] {req_id}: re-check of the session-revoke finding(s) crashed: {exc!r}; they stay open")
        return rechecked

    def reopen_unrechecked(self, req_id: str, settled: list[dict[str, Any]], rechecked: Any) -> None:
        try:
            from agent.audit import finding_fingerprint, is_session_finding

            decided = set()
            if isinstance(rechecked, dict):
                decided = {finding_fingerprint(f) for kind in ("gone", "still") for f in rechecked.get(kind) or []
                           if isinstance(f, dict)}
            open_now = [f for f in settled or [] if isinstance(f, dict) and f.get("layer") != "model"
                        and not is_session_finding(f) and finding_fingerprint(f) not in decided]
            if not open_now:
                return
            before = set(getattr(self, "audit_unrechecked_fingerprints", None) or [])
            again = [f for f in open_now if finding_fingerprint(f) not in before]
            spent = [f for f in open_now if finding_fingerprint(f) in before]
            prints = {finding_fingerprint(f) for f in again}
            if prints:
                self.audit_sent_fingerprints = [fp for fp in list(getattr(self, "audit_sent_fingerprints", None) or [])
                                                if fp not in prints]
                self.audit_unrechecked_fingerprints = sorted(before | prints)
            self.book_open_findings(req_id, open_now)
            why = ("no re-check ran" if not isinstance(rechecked, dict) else
                   "the re-check could not run" if not rechecked.get("ran") else "their check did not run completely")
            if again:
                log(f"[audit] {req_id}: {len(again)} finding(s) of the kept repair not re-checked ({why}): not settled, "
                    "open again, may be sent once more")
            if spent:
                log(f"[audit] {req_id}: {len(spent)} finding(s) not re-checked a second time ({why}): not sent again, "
                    "left open in the handoff")
        except Exception as exc:
            log(f"[audit] {req_id}: re-opening the not re-checked findings failed: {exc!r}")

    def reopen_still_there(self, req_id: str, rechecked: Any) -> None:
        if not isinstance(rechecked, dict) or not rechecked.get("ran") or not (
                setting_on("audit_route_stated") or setting_on("audit_settle_rechecked")):
            return
        still = [f for f in rechecked.get("still") or [] if isinstance(f, dict)]
        if not still:
            return
        from agent.audit import finding_fingerprint

        prints = {finding_fingerprint(f) for f in still}
        self.audit_sent_fingerprints = [fp for fp in list(getattr(self, "audit_sent_fingerprints", None) or []) if fp not in prints]
        self.audit_rolled_back_fingerprints = list(getattr(self, "audit_rolled_back_fingerprints", None) or []) + sorted(prints)
        self.book_open_findings(req_id, still)
        log(f"[audit] {req_id}: {len(still)} finding(s) still there after the kept repair: open again, may be sent again "
            f"(counted as a failed attempt toward the cap of {self.AUDIT_ROLLBACKS_MAX})")

    INHERITED_BACKEND_DIR = ".arc/evolution/inherited-backend"
    INHERITED_BACKEND_SKIP = ("node_modules", "test-e2e", "tests", "test", "__tests__", "test-results", ".arc-test-db",
                              "playwright-report", "dist", "e2e-support", "coverage")
    INHERITED_BACKEND_MAX_FILES = 3000
    DB_SOURCE = re.compile(r"(?i)^backend/src/(?:.*/)?(?:database|db|models?|migrations?|seeds?|schema)/|"
                           r"^backend/src/(?:.*/)?(?:init_db|seed[^/]*|schema|migrat[^/]*|db)\.[cm]?[jt]s$")

    def evolution_state(self) -> dict[str, Any] | None:
        state = self.__dict__.get("evolution")
        return state if isinstance(state, dict) and state.get("active") else None

    def evolution_enter(self, tree: dict[str, Any], found: dict[str, Any] | None) -> dict[str, Any] | None:
        project = self.project_dir
        handoff = stage_handoff.load_handoff(project, self.requirement_sha, log=log) if setting_on("stage_handoff") else None
        self._earlier_handoff = handoff
        old_tree, why = evolution.inherited_requirement(project, self.requirement_sha)
        self._old_tree = old_tree
        specs = evolution.inherited_spec_files(project)
        inventory = evolution.historical_inventory(tree, handoff, old_tree, project, specs)
        classes = evolution.classify(tree, inventory, old_tree)
        log(f"[evolution] history of the inherited application: {len(inventory['leaves'])} leaf/leaves (handoff: "
            f"{'read' if handoff else 'none'}; {why}; spec-title ids: {inventory['sources'].get('spec_titles', 0)}); "
            f"complete={inventory['complete']}")
        log(f"[evolution] classes: inherited {len(classes['inherited'])}, modified {len(classes['modified'])} "
            f"{classes['modified']}, new {len(classes['new'])} {classes['new']}; inherited leaves outside this requirement: "
            f"{len(classes['out_of_tree'])}" + (f"; unverified: {classes['unverified']}" if classes["unverified"] else "")
            + (f"; changed without the Modified marker: {classes['unmarked_changes']}" if classes["unmarked_changes"] else ""))
        if not evolution.evolution_round(classes):
            log("[evolution] no leaf of this requirement is inherited or modified: no evolution round (a later stage of a "
                "staged task keeps its v150-v155 path)")
            return None
        registry = (evolution.register_specs(project, specs, classes, handoff) if setting_on("evolution_test_baseline")
                    else {"files": {}, "counts": {}})
        state: dict[str, Any] = {
            "active": True, "classes": classes, "implement": evolution.to_implement(classes), "registry": registry,
            "specs": list(specs), "inherited_requirement": why, "inventory_sources": inventory["sources"],
            "spec_only_ids": inventory["spec_only_ids"], "tests": {}, "spec_prefix": {}, "inherited_debt": {},
            "lifecycle": {}, "settled": [], "preservation": dict(self.__dict__.get("inherited_app") or {}),
        }
        self.evolution = state
        self._frozen_inherited_specs = {}
        if setting_on("evolution_freeze_inherited_specs"):
            for rel in specs:
                try:
                    self._frozen_inherited_specs[rel] = (project / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        signals = dict((self.__dict__.get("inherited_app") or {}).get("signals") or {})
        cont = dict(found or {"beyond": signals.get("beyond"), "beyond_sample": list(signals.get("beyond_sample") or [])})
        cont["earlier"] = list(specs)
        self.continuation = {**cont, "protected": [], "baseline_red": {}, "unchecked": [], "baseline": "not taken", "evolution": True}
        counts = registry.get("counts") or {}
        unmapped = [Path(rel).name + ": " + e.get("why", "") for rel, e in (registry.get("files") or {}).items() if not e.get("mapped")]
        log(f"[evolution] inherited specs registered per test: {counts.get('files', 0)} file(s), {counts.get('tests', 0)} test(s) -- "
            f"valid {counts.get('valid', 0)}, unverified {counts.get('unverified', 0)} (tests of modified leaves: settled after "
            f"their leaf's own batch); unmapped files: {unmapped or 'none'}")
        self.evolution_snapshot_inherited_backend()
        return state

    def evolution_snapshot_inherited_backend(self) -> None:
        if not setting_on("evolution_data_lifecycle"):
            return
        source = self.project_dir / "backend"
        target = self.project_dir / self.INHERITED_BACKEND_DIR
        if not source.is_dir() or target.exists():
            return
        copied = 0
        try:
            for path in sorted(source.rglob("*")):
                rel = path.relative_to(source)
                if not path.is_file() or set(rel.parts) & set(self.INHERITED_BACKEND_SKIP) or rel.parts[0].startswith(".arc"):
                    continue
                if re.search(r"\.(?:db|sqlite3?)(?:-journal|-wal|-shm)?$", path.name) or path.name.startswith(".arc-e2e-report"):
                    continue
                copied += 1
                if copied > self.INHERITED_BACKEND_MAX_FILES:
                    raise OSError("too many files")
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target / rel)
            log(f"[evolution] the inherited backend's code kept for the upgrade check ({copied} file(s), {self.INHERITED_BACKEND_DIR})")
        except OSError as exc:
            shutil.rmtree(target, ignore_errors=True)
            log(f"[evolution] the inherited backend could not be kept ({exc!r}); the data-lifecycle check skips the upgrade path")

    def evolution_leads(self, specs: int) -> dict[str, str]:
        state = self.evolution_state() or {}
        classes = dict(state.get("classes") or {})
        handoff = self.__dict__.get("_earlier_handoff")
        head = f"{stage_continuation.CONTRACT_HEAD} (inherited from the original round of this task; evolution round)"
        modified, new = list(classes.get("modified") or []), list(classes.get("new") or [])
        return {
            "contract": evolution.contract_lead(classes, specs, handoff, head),
            "architect": (f"{head}. The project folder holds the application the original round of this task delivered; its "
                          f"{specs} acceptance spec file(s) stay and keep guarding it. Write the contract for THIS round as a "
                          "change of it: reuse its routes, pages, endpoints, tables and shared files; list only what the "
                          f"modified leaves ({', '.join(modified) or 'none'}) change and the new leaves ({', '.join(new) or 'none'}) "
                          "add -- including the existing files a stated change must edit -- and never rename, re-create or "
                          "drop what exists. The inherited leaves are not built again."),
            "outsider": (f"{head}: this round changes an application the original round of the same task delivered; its "
                         "sign-up, sign-in and navigation already exist. A step of yours that passes through them uses exactly "
                         "the names the inherited acceptance specs use, unless this round's requirement states another name."),
        }

    def evolution_attach(self, tree: dict[str, Any], whole_text: str = "") -> None:
        state = self.evolution_state()
        if not state:
            return
        classes = dict((state.get("classes") or {}).get("classes") or {})
        unverified = dict((state.get("classes") or {}).get("unverified") or {})
        implement = set(state.get("implement") or [])
        old_tree = self.__dict__.get("_old_tree")
        if old_tree is None and implement:
            old_tree, _ = evolution.inherited_requirement(self.project_dir, self.requirement_sha)
            self._old_tree = old_tree
        by_leaf: dict[str, list[dict[str, str]]] = {}
        for rel, entry in ((state.get("registry") or {}).get("files") or {}).items():
            for test in entry.get("tests") or []:
                by_leaf.setdefault(str(test.get("leaf")), []).append({"file": Path(rel).name, "title": test.get("title", ""),
                                                                       "class": test.get("class", "")})
        leaves, _, _ = evolution.tree_index(tree)
        rules = 0
        for leaf_id, leaf in leaves.items():
            if leaf_id not in classes:
                continue
            info: dict[str, Any] = {"class": classes[leaf_id], "inherited_tests": by_leaf.get(leaf_id, [])[:40]}
            if leaf_id in unverified:
                info["unverified"] = unverified[leaf_id]
            extras: list[str] = []
            if leaf_id in implement:
                if classes[leaf_id] == "modified" and setting_on("evolution_effective_rules"):
                    text, struct = evolution.effective_rules(leaf, whole_text)
                    if text:
                        leaf[evolution.EFFECTIVE_RULES_KEY] = text
                        info["rules"] = struct
                        extras.append(text)
                        rules += 1
                if setting_on("evolution_inherited_context"):
                    context = evolution.inherited_context(leaf_id, leaf, tree, old_tree)
                    if context:
                        info["inherited_context"] = context
                        extras.append(context)
            if extras:
                known = str(leaf.get("ancestor_context") or "")
                leaf["ancestor_context"] = "\n\n".join([known] + [e for e in extras if e not in known]).strip()
            leaf[evolution.EVOLUTION_KEY] = info
        log(f"[evolution] node keys attached: {len(classes)} classified leaf/leaves, effective rules of {rules} modified leaf/leaves")

    def evolution_batches(self, work: list[dict[str, Any]], tree: dict[str, Any] | None = None) -> None:
        state = self.evolution_state()
        if not state:
            return
        classes = dict(state.get("classes") or {})
        existing = {Path(rel).name for rel in state.get("specs") or []}
        prefixes = {}

        by_id = evolution.tree_index(tree)[0] if isinstance(tree, dict) else {}
        for batch in work:
            members = [str(m) for m in (batch.get("members") or [batch.get("id")])]
            leaves = [by_id[m] for m in members if isinstance(by_id.get(m), dict)]
            texts = [str(leaf.get(evolution.EFFECTIVE_RULES_KEY)) for leaf in leaves if leaf.get(evolution.EFFECTIVE_RULES_KEY)]
            if texts:
                batch[evolution.EFFECTIVE_RULES_KEY] = "\n\n".join(texts)
            batch[evolution.EVOLUTION_KEY] = {"members": {m: (classes.get("classes") or {}).get(m, "") for m in members}}
            context = str(batch.get("ancestor_context") or "")
            for leaf in leaves[1:]:
                info = leaf.get(evolution.EVOLUTION_KEY) or {}
                for extra in (leaf.get(evolution.EFFECTIVE_RULES_KEY), info.get("inherited_context")):
                    if extra and str(extra) not in context:
                        context = (context + "\n\n" + str(extra)).strip()
            batch["ancestor_context"] = context
            if setting_on("evolution_evo_spec_names"):
                prefix = evolution.batch_spec_prefix(str(batch.get("id")), members, classes, existing, safe=safe_prefix)
                batch["spec_prefix"] = prefix
                prefixes[str(batch.get("id"))] = prefix
                existing.add(f"{prefix}.spec.js")
        state["spec_prefix"] = prefixes
        renamed = {k: v for k, v in prefixes.items() if v != safe_prefix(k)}
        log(f"[evolution] {len(work)} batch(es) of this round for {len(state.get('implement') or [])} leaf/leaves"
            + (f"; spec names of this round: {', '.join(f'{v}.spec.js' for v in renamed.values())}" if renamed else ""))

    def spec_prefix_of(self, node: dict[str, Any]) -> str:
        return str(node.get("spec_prefix") or "") or safe_prefix(str(node.get("id")))

    def evolution_guards(self) -> bool:
        return any(isinstance(e, dict) and e.get("inherited") for e in (self.__dict__.get("partial_green") or {}).values())

    def evolution_after_baseline(self, when: str) -> None:
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else None
        if cont is None:
            return
        state = self.evolution_state()
        if cont.get("baseline") != "taken":
            if setting_on("evolution_deferred_baseline"):
                cont["baseline_open"] = True
            log(f"[continuation] baseline at {when}: {cont.get('baseline')}; {len(cont.get('unchecked') or cont.get('earlier') or [])} "
                "earlier spec file(s) NOT judged -- not counted as no regression"
                + ("; it is taken again after the next green batch" if setting_on("evolution_deferred_baseline") else ""))
            self.report.setdefault("continuation", {})["baseline_open"] = True
            return
        cont["baseline_open"] = False
        cont["baseline_when"] = when
        if not state or not setting_on("evolution_test_baseline"):
            return
        files = (state.get("registry") or {}).get("files") or {}
        red = dict(cont.get("baseline_red") or {})
        protected = set(cont.get("protected") or [])
        store = self.__dict__.setdefault("partial_green", {})
        books: dict[str, dict[str, str]] = {}
        guarded: dict[str, int] = {}
        unreadable: list[str] = []
        for rel, entry in files.items():
            titles = [str(t.get("title")) for t in entry.get("tests") or []]
            name = Path(rel).name
            if rel in protected:
                books[rel] = {t: "passed" for t in titles}
            elif name in red:
                failing = set((red.get(name) or {}).keys())
                if not failing or not failing <= set(titles):
                    books[rel] = {t: "red file, failing tests unreadable" for t in titles}
                    unreadable.append(name)
                    continue
                books[rel] = {t: ("failed" if t in failing else "passed") for t in titles}
                passing = [t for t in titles if t not in failing]
                if passing:
                    try:
                        digest = self.spec_digest((self.project_dir / rel).read_bytes())
                    except OSError:
                        continue
                    store[rel] = {"batch": f"inherited baseline ({when})", "titles": passing, "sha1": digest, "written": [],
                                  "inherited": True}
                    guarded[name] = len(passing)
            else:
                books[rel] = {t: "not judged" for t in titles}
        state["tests"] = books
        flat = [v for b in books.values() for v in b.values()]
        state["baseline_counts"] = {"tests_passed": flat.count("passed"), "tests_failed": flat.count("failed"),
                                    "tests_not_judged": len(flat) - flat.count("passed") - flat.count("failed"),
                                    "files_protected": len(protected), "files_red": len(red),
                                    "red_files_with_guarded_tests": len(guarded), "guarded_tests_in_red_files": sum(guarded.values())}
        log(f"[evolution] baseline per test ({when}): {state['baseline_counts']}; passing tests of red files guard one by one: "
            f"{guarded or 'none'}" + (f"; red files whose failing tests cannot be read (not guarded): {unreadable}" if unreadable else ""))

    def evolution_hold(self, node: dict[str, Any]) -> dict[str, Any] | None:
        state = self.evolution_state()
        if not state or not setting_on("evolution_test_baseline"):
            return None
        members = [str(m) for m in (node.get("members") or [node.get("id")])]
        files = evolution.exempt_files(state.get("registry") or {}, members)
        if not files:
            return None
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
        held: dict[str, Any] = {"protected": [], "green": [], "partial": {}, "members": members}
        order = list(cont.get("protected") or [])
        green = self.__dict__.setdefault("green_specs", [])
        store = self.__dict__.setdefault("partial_green", {})
        for rel in files:
            if rel in (cont.get("protected") or []):
                cont["protected"] = [s for s in cont["protected"] if s != rel]
                held["protected"].append((order.index(rel), rel))
            if rel in green:
                held["green"].append((green.index(rel), rel))
                green.remove(rel)
            entry = store.get(rel)
            if isinstance(entry, dict) and entry.get("inherited"):
                leaf_of = evolution.leaf_of_title(state.get("registry") or {}, rel)
                keep = [t for t in entry.get("titles") or [] if not (leaf_of.get(t, {}).get("leaf") in members
                                                                      and leaf_of.get(t, {}).get("class") == "unverified")]
                held["partial"][rel] = dict(entry)
                if keep:
                    store[rel] = {**entry, "titles": keep}
                else:
                    store.pop(rel, None)
        log(f"[evolution] {node.get('id')}: while it runs, free of the unverified inherited tests of {members} in "
            f"{[Path(f).name for f in files]} (every other batch stays guarded by them; settled after it)")
        return held

    def evolution_release(self, held: dict[str, Any] | None) -> None:
        if not held:
            return
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
        protected = cont.setdefault("protected", [])
        for index, rel in sorted(held.get("protected") or []):
            if rel not in protected:
                protected.insert(min(index, len(protected)), rel)
        green = self.__dict__.setdefault("green_specs", [])
        for index, rel in sorted(held.get("green") or []):
            if rel not in green:
                green.insert(min(index, len(green)), rel)
        store = self.__dict__.setdefault("partial_green", {})
        for rel, entry in (held.get("partial") or {}).items():
            store[rel] = entry

    def evolution_after_batch(self, node: dict[str, Any], ok: bool) -> None:
        self.evolution_restore_inherited_specs(f"batch {node.get('id')}")
        if ok:
            self.evolution_settle(node)
        self.evolution_deferred_baseline(node, ok)

    def evolution_restore_inherited_specs(self, what: str) -> list[str]:
        frozen = self.__dict__.get("_frozen_inherited_specs") or {}
        if not frozen or not setting_on("evolution_freeze_inherited_specs"):
            return []
        restored = []
        for rel, text in frozen.items():
            path = self.project_dir / rel
            try:
                now = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
                if now != text:
                    write_text(path, text)
                    restored.append(rel)
            except OSError:
                pass
        if restored:
            log(f"[evolution] after {what}: inherited spec file(s) put back as the original round left them: "
                f"{[Path(r).name for r in restored]}")
        return restored

    def evolution_update_inherited_spec(self, rel: str, content: str, why: str) -> bool:
        frozen = self.__dict__.get("_frozen_inherited_specs")
        if not isinstance(frozen, dict) or rel not in frozen:
            return False
        write_text(self.project_dir / rel, content)
        frozen[rel] = content
        state = self.evolution_state()
        if state is not None:
            state.setdefault("spec_updates", []).append({"file": Path(rel).name, "why": str(why)[:300]})
        log(f"[evolution] inherited spec {Path(rel).name} updated: {str(why)[:200]}")
        return True

    def evolution_unprotect(self, rel: str, keep: list[str], why: str) -> None:
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
        cont["protected"] = [s for s in cont.get("protected") or [] if s != rel]
        self.green_specs = [s for s in self.__dict__.get("green_specs") or [] if s != rel]
        self.e2e_specs = [s for s in self.__dict__.get("e2e_specs") or [] if s != rel]
        store = self.__dict__.setdefault("partial_green", {})
        try:
            digest = self.spec_digest((self.project_dir / rel).read_bytes())
        except OSError:
            digest = ""
        if keep and digest:
            store[rel] = {"batch": f"inherited ({why})", "titles": list(keep), "sha1": digest, "written": [], "inherited": True}
        else:
            store.pop(rel, None)

    def evolution_settle(self, node: dict[str, Any]) -> None:
        state = self.evolution_state()
        if not state or not setting_on("evolution_test_baseline"):
            return
        members = [str(m) for m in (node.get("members") or [node.get("id")])]
        registry = state.get("registry") or {}
        pending = [rel for rel in evolution.exempt_files(registry, members) if (self.project_dir / rel).is_file()]
        if not pending:
            return
        need = self.build_est() + self.suite_est(len(pending))
        if not self.hard_fits("settling the unverified inherited tests", need):
            log(f"[evolution] {node.get('id')}: no time to settle the unverified inherited tests; they stay unverified (guarded)")
            return
        if not build_frontend(self.project_dir, log=log).ok:
            log(f"[evolution] {node.get('id')}: the application does not build; the unverified inherited tests stay unverified")
            return
        run = run_e2e(self.project_dir, pending, log=log)
        if run.skipped:
            log(f"[evolution] {node.get('id')}: the inherited files could not run here; their tests stay unverified")
            return
        output = str(run.output or "")
        failed = failed_test_titles(output) if not run.ok else {}
        if not run.ok and not any(failed.get(Path(rel).name) for rel in pending):
            log(f"[evolution] {node.get('id')}: the run of {[Path(r).name for r in pending]} failed without readable tests; "
                "their tests stay unverified (guarded)")
            return
        modified = set(state.get("implement") or [])
        summary = []
        for rel in pending:
            name = Path(rel).name
            entry = (registry.get("files") or {}).get(rel) or {}
            bad = set(failed.get(name) or ())
            titles = {str(t.get("title")) for t in entry.get("tests") or []}
            if bad and not bad <= titles:
                summary.append(f"{name}: failing tests unreadable, unchanged")
                continue
            superseded, broken, confirmed = [], [], []
            for test in entry.get("tests") or []:
                mine = test.get("leaf") in members and test.get("class") == "unverified"
                if test.get("title") in bad:
                    if mine:
                        test["class"] = "superseded"
                        test["why"] = f"fails after {node.get('id')}'s stated change while its own spec of this round is green"
                        superseded.append(test["title"])
                    else:
                        broken.append(test["title"])
                elif mine:
                    test["class"] = "valid"
                    test["why"] = f"passes after {node.get('id')}'s change"
                    confirmed.append(test["title"])
            entry["modified_leaves"] = sorted({str(t.get("leaf")) for t in entry.get("tests") or []
                                               if t.get("class") == "unverified" and t.get("leaf") in modified})
            books = state.setdefault("tests", {}).setdefault(rel, {})
            for test in entry.get("tests") or []:
                books[test["title"]] = "failed" if test["title"] in bad else "passed"
            if broken:
                state.setdefault("inherited_debt", {})[rel] = {"by": str(node.get("id")), "failed": broken}
            if bad:
                self.evolution_unprotect(rel, [t["title"] for t in entry.get("tests") or [] if t["title"] not in bad
                                               and t.get("class") != "superseded"], f"settled after {node.get('id')}")
            summary.append(f"{name}: {len(confirmed)} valid, {len(superseded)} superseded, {len(broken)} broken")
        counts = registry.setdefault("counts", {})
        flat = [t.get("class") for e in (registry.get("files") or {}).values() for t in e.get("tests") or []]
        counts.update(valid=flat.count("valid"), unverified=flat.count("unverified"), superseded=flat.count("superseded"))
        state.setdefault("settled", []).append({"batch": str(node.get("id")), "files": summary})
        log(f"[evolution] {node.get('id')}: unverified inherited tests settled -- " + "; ".join(summary))

    def evolution_deferred_baseline(self, node: dict[str, Any], ok: bool) -> None:
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else None
        if not cont or not cont.get("baseline_open") or not ok or not setting_on("evolution_deferred_baseline"):
            return
        attempts = int(cont.get("baseline_deferred_attempts") or 0)
        if attempts >= 2:
            return
        cont["baseline_deferred_attempts"] = attempts + 1
        log(f"[continuation] the baseline not taken at the start ({cont.get('baseline')}) is taken now, after {node.get('id')} "
            "ended green (a test red now may be that batch's: booked 'red after', not 'red at the start')")
        try:
            self.continuation_baseline()
        except Exception as exc:
            cont.update(baseline=f"crashed: {type(exc).__name__}")
            log(f"[continuation] the deferred baseline crashed: {exc!r}")
        self.evolution_after_baseline(f"after {node.get('id')}")

    def evolution_lifecycle_run(self, what: str, leaf_ids: list[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        state = self.evolution_state()
        if not state or not setting_on("evolution_data_lifecycle"):
            return [], {}
        try:
            from agent.data_lifecycle import lifecycle_check
        except Exception as exc:
            log(f"[evolution] data-lifecycle check unavailable: {exc!r}")
            return [], {}
        limit = setting_seconds("evolution_data_lifecycle_sec", 150.0)
        if not self.hard_fits(f"the data-lifecycle check ({what})", limit + 30.0):
            return [], {}
        tree = self.__dict__.get("handoff_tree") or {}
        ids = list(state.get("implement") or []) if leaf_ids is None else list(leaf_ids)
        names = evolution.scenario_seed_names(tree, ids)

        inv = self.seed_foundation_inventory(tree) if isinstance(tree, dict) and tree else None
        strict = bool(inv)
        if inv:
            names = seed_foundation.scenario_names(inv, ids)
        try:
            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            whole = ""
        old = self.project_dir / self.INHERITED_BACKEND_DIR
        kinds = (seed_foundation.global_names(inv, [n for ns in names.values() for n in ns])
                 if inv and setting_on("fixture_idempotent") else {})
        findings, report = lifecycle_check(self.project_dir, whole, scenario_names=names,
                                           inherited_backend=old if old.is_dir() else None, time_limit=limit, log=log,
                                           **({"strict": True} if strict else {}), **({"name_kinds": kinds} if kinds else {}))
        findings = [f for f in findings or [] if isinstance(f, dict)]
        state.setdefault("lifecycle", {})[what] = {"findings": [str(f.get("check") or f.get("problem", ""))[:80] for f in findings],
                                                   "paths": (report or {}).get("paths"), "completed": (report or {}).get("completed")}
        log(f"[evolution] data lifecycle ({what}): {len(findings)} finding(s); paths {(report or {}).get('paths')}")
        return findings, report or {}

    def evolution_lifecycle_findings(self, node: dict[str, Any], written: list[str]) -> list[dict[str, Any]]:
        if not self.evolution_state() or not setting_on("evolution_data_lifecycle"):
            return []
        members = [str(m) for m in (node.get("members") or [node.get("id")])]
        seeds = evolution.scenario_seed_names(self.__dict__.get("handoff_tree") or {}, members)
        inv = self.__dict__.get("seed_inventory") if setting_on("evolution_seed_foundation") else None
        if isinstance(inv, dict) and inv.get("scenarios"):
            seeds = seed_foundation.scenario_names(inv, members)
        if not seeds and not any(self.DB_SOURCE.search(str(rel)) for rel in written or []):
            return []
        try:
            return self.evolution_lifecycle_run(f"batch {node.get('id')}", members)[0]
        except Exception as exc:
            log(f"[evolution] data-lifecycle check of {node.get('id')} crashed: {exc!r}")
            return []

    def evolution_lifecycle_closing(self) -> None:
        findings, _ = self.evolution_lifecycle_run("closing")
        if not findings:
            return

        def check() -> StepResult:
            again, _ = self.evolution_lifecycle_run("closing re-check")

            blocking = [f for f in again if not str(f.get("check") or "").endswith("/duplicate_fixture")]
            return StepResult(not blocking, findings_brief(again) if again else "data lifecycle clean")

        text = ("The delivered application must start on an EMPTY database, on the database the inherited application "
                "built (upgrade), and again on the same database, with this round's scenario seed records present and "
                "never resurrected after a user renamed or deleted them. Findings:\n" + findings_brief(findings))
        kept = self.repair_round("data-lifecycle", check, text)
        log(f"[evolution] closing data-lifecycle repair: {'kept' if kept else 'not kept'}")

    SINGLE_SOURCE_HARD = ("double-mount",)
    SINGLE_SOURCE_TOUCHED = ("name-only-lookup", "lookup-owners", "duplicate-helper")
    SINGLE_SOURCE_ADVISORY = ("duplicate-engine",)
    INHERITED_DEBT_CAP = 2500
    DEBT_LINE_CHARS = 170
    SINGLE_SOURCE_HANDOFF_MAX = 20

    @staticmethod
    def single_source_dict(finding: Any) -> dict[str, Any]:
        return {"kind": str(getattr(finding, "kind", "")), "files": [str(f) for f in getattr(finding, "files", []) or []],
                "lines": [int(n) for n in getattr(finding, "lines", []) or [] if isinstance(n, int)],
                "message": str(getattr(finding, "message", ""))}

    @staticmethod
    def single_source_key(finding: dict[str, Any]) -> str:
        message = " ".join(str(finding.get("message") or "").split())

        return str(finding.get("kind") or "") + "|" + re.sub(r":\d+(?:\s*,\s*\d+)*\b", ":#", message)

    @classmethod
    def single_source_group(cls, finding: dict[str, Any], touched: set[str]) -> str:
        kind = str(finding.get("kind") or "")
        on_touched = bool(touched & set(finding.get("files") or []))
        if kind in cls.SINGLE_SOURCE_HARD:
            return "hard"
        if kind == "shadowed-route":

            return "hard" if "probably never runs" not in str(finding.get("message") or "") else ("touched" if on_touched else "")
        if kind in cls.SINGLE_SOURCE_TOUCHED:
            return "touched" if on_touched else ""
        if kind in cls.SINGLE_SOURCE_ADVISORY:
            return "advisory"
        return ""

    def evolution_touched_files(self) -> set[str]:
        state = self.evolution_state() or {}
        classes = dict((state.get("classes") or {}).get("classes") or {})
        wanted = [leaf for leaf in state.get("implement") or [] if classes.get(leaf) == "modified"]
        handoff = self.__dict__.get("_earlier_handoff") or {}
        batch_of = {str(r.get("id")): str(r.get("batch") or r.get("id")) for r in (handoff.get("leaves") or [])
                    if isinstance(r, dict) and r.get("id")} if isinstance(handoff, dict) else {}
        try:
            summary = json.loads((self.project_dir / ".arc" / "agent-summary.json").read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return set()
        nodes = summary.get("nodes") if isinstance(summary, dict) else None
        if not isinstance(nodes, dict):
            return set()
        touched: set[str] = set()
        for leaf in wanted:
            entry = nodes.get(batch_of.get(leaf, leaf))
            if isinstance(entry, dict):
                touched.update(str(p) for p in entry.get("written") or [] if isinstance(p, str))
        return touched

    def inherited_debt_note(self, groups: dict[str, list[dict[str, Any]]]) -> str:
        from agent import single_source

        heads = {
            "hard": ("REPAIR in this batch (each is a defect wherever it sits); keep the behaviour of the handler that runs "
                     "today, so the inherited acceptance specs stay green:"),
            "touched": ("ON FILES this round's modified features were built in -- merge into the one owner where a change of "
                        "this round passes through them:"),
            "advisory": "ADVISORY (change it only where this batch's change needs it):",
        }
        kinds = list(dict.fromkeys(f["kind"] for group in heads for f in groups.get(group) or []))
        if not kinds:
            return ""
        lines = ["INHERITED DEBT (start-up single-source audit of the application the original round delivered): one rule "
                 "has two owners at the places below."]
        fixes = [f"  fix [{k}]: {single_source.FIX[k]}" for k in kinds if k in single_source.FIX]
        used = len(lines[0]) + sum(len(x) + 1 for x in fixes)
        shown: set[str] = set()
        shown_items: list[dict[str, Any]] = []
        mark = 60
        for group, head in heads.items():
            items = groups.get(group) or []
            if not items:
                continue
            if used + len(head) + 1 + mark > self.INHERITED_DEBT_CAP:
                lines.append(f"- ... {sum(len(groups.get(g) or []) for g in heads) - len(shown_items)} more finding(s) not shown")
                break
            lines.append(head)
            used += len(head) + 1
            for n, finding in enumerate(items):
                text = " ".join(str(finding.get("message") or "").split())
                fix = single_source.FIX.get(str(finding.get("kind")), "")
                if fix and text.endswith(fix):
                    text = text[:-len(fix)].rstrip()
                if len(text) > self.DEBT_LINE_CHARS:
                    text = text[:self.DEBT_LINE_CHARS - 3].rstrip() + "..."
                line = f"- [{finding.get('kind')}] {text}"
                if used + len(line) + 1 + mark > self.INHERITED_DEBT_CAP:
                    lines.append(f"- ... {len(items) - n} more finding(s) of this group not shown")
                    used += mark
                    break
                lines.append(line)
                used += len(line) + 1
                shown.add(str(finding.get("kind")))
                shown_items.append(finding)

        return "\n".join(lines + [fix for kind, fix in zip([k for k in kinds if k in single_source.FIX], fixes) if kind in shown])

    @staticmethod
    def leaf_requirement_text(leaf: dict[str, Any]) -> str:
        lines = [str(leaf.get("name") or ""), str(leaf.get("description") or "")]
        for scenario in leaf.get("scenarios") or []:
            if isinstance(scenario, dict):
                lines.append(str(scenario.get("name") or ""))
                lines += [str(step.get("content") or "") for step in scenario.get("steps") or [] if isinstance(step, dict)]
        return "\n".join(line for line in lines if line.strip())

    def evolution_startup_health(self, tree: dict[str, Any]) -> None:
        try:
            state = self.evolution_state()
            if not state:
                return
            from agent import single_source

            book = state.setdefault("health", {})
            report = self.report.setdefault("evolution_health", {})
            findings: list[dict[str, Any]] | None = None
            if setting_on("evolution_single_source"):
                found = single_source.audit(self.project_dir)
                findings = [self.single_source_dict(f) for f in found]
                text = single_source.render(found, 8000)
                log(f"[evolution] start-up single-source audit of the inherited application: {len(findings)} finding(s)"
                    + (":\n" + text if text else ""))
                touched = self.evolution_touched_files()
                groups: dict[str, list[dict[str, Any]]] = {"hard": [], "touched": [], "advisory": []}
                for finding in findings:
                    group = self.single_source_group(finding, touched)
                    if group:
                        groups[group].append(finding)
                note = self.inherited_debt_note(groups)
                handed = {group: len(items) for group, items in groups.items()}
                book["single_source"] = {"start": findings, "touched_files": len(touched), "handed": handed, "note": note}
                report["single_source_start"] = {"findings": len(findings), "kinds": sorted({f["kind"] for f in findings}),
                                                 "handed": handed, "touched_files": len(touched)}
                log(f"[evolution] inherited debt for the first batch: {handed['hard']} to repair, {handed['touched']} on the "
                    f"{len(touched)} file(s) the modified leaves were built in, {handed['advisory']} advisory; "
                    f"{len(findings) - sum(handed.values())} left to the handoff")
            if setting_on("evolution_health_check"):
                results = self.evolution_health_items(tree, state, findings)
                book["health_check"] = results
                report["health_check"] = results
                log("[evolution] start-up health check: " + ("; ".join(
                    f"{r['item']} {r['status']}" + (f" ({r['why']})" if r.get("why") else "") for r in results) or "no item applies"))
        except Exception as exc:
            log(f"[evolution] start-up health check crashed: {exc!r}")

    HEALTH_STATUS = {"finding": "findings", "findings": "findings", "clean": "clean", "not run": "not run"}

    def evolution_health_items(self, tree: dict[str, Any], state: dict[str, Any], findings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        from agent import inherited_health

        began = clock.now()
        cap = setting_seconds("evolution_health_cap_sec", 900.0)
        end = began + cap
        leaves = evolution.tree_index(tree)[0]
        implement = [str(leaf) for leaf in state.get("implement") or []]
        text = "\n\n".join(self.leaf_requirement_text(leaves[leaf]) for leaf in implement if isinstance(leaves.get(leaf), dict))
        keys = [key for key, _item in inherited_health.health_check_items(text)] if text.strip() else []
        try:
            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
        except (OSError, AttributeError):
            whole = ""
        node_modules = str(self.project_dir / "backend" / "node_modules")
        results: list[dict[str, Any]] = []
        build: dict[str, Any] | None = None
        probes_on = setting_on("evolution_health_probes")
        grid_done: dict[str, dict[str, Any]] = {}

        def left() -> float:
            return end - clock.now()

        for key in keys:
            started = clock.now()
            if key == "owners":
                if findings is None:
                    row = {"item": key, "status": "not run", "why": "evolution_single_source is off"}
                elif not findings and not any((self.project_dir / part / "src").is_dir() for part in ("backend", "frontend")):

                    row = {"item": key, "status": "not run", "why": "no backend/src or frontend/src to read"}
                else:
                    row = {"item": key, "status": "clean" if not findings else "findings",
                           "result": f"single_source.audit: {len(findings)} finding(s)"}
            elif key == "modified":
                row = self.evolution_modified_item(state)
            elif key == "entry" and not setting_on("det_plan_walk"):
                row = {"item": key, "status": "not run", "why": "det_plan_walk is off"}
            elif key in ("entry", "recalculation", "extent") and (key == "entry" or probes_on):
                if build is None:
                    build = self.evolution_health_build(end)
                    log(f"[evolution] start-up build of the inherited frontend: {build['status']} -- {build['detail']}")
                if not build.get("dist"):
                    row = {"item": key, "status": "not run", "why": f"no built frontend ({build['status']}: {build['detail'][:160]})"}
                elif left() < 30:
                    row = {"item": key, "status": "not run", "why": f"the health check's {int(cap)} s were used up"}
                elif key == "entry":
                    row = self.evolution_entry_walk(tree, implement, time_limit=min(setting_seconds("det_plan_walk_sec", 300.0), left() - 10))
                else:
                    if key not in grid_done:
                        wanted = tuple(k for k in ("recalculation", "extent") if k in keys)
                        grid_done.update(self.evolution_probe("grid", whole, node_modules, min(300.0, left() - 10), wanted))
                    row = grid_done.get(key) or {"item": key, "status": "not run", "why": "the grid probe returned nothing"}
            elif key == "session" and probes_on:
                if left() < 30:
                    row = {"item": key, "status": "not run", "why": f"the health check's {int(cap)} s were used up"}
                else:
                    row = self.evolution_probe("session", whole, node_modules, min(180.0, left() - 10), ("session",)).get("session") \
                        or {"item": key, "status": "not run", "why": "the session probe returned nothing"}
            else:
                row = {"item": key, "status": "not run", "why": "evolution_health_probes is off" if not probes_on else "no runner for this item"}
            row["status"] = self.HEALTH_STATUS.get(str(row.get("status")), "not run")
            if row["status"] == "not run" and not row.get("why"):
                row["why"] = str(row.get("result") or "not executed")[:300]
            row.setdefault("result", f"not run: {row.get('why')}")
            row.setdefault("seconds", round(clock.now() - started, 1))
            if build is not None and key in ("entry", "recalculation", "extent"):
                row.setdefault("build", build.get("status"))
            results.append(row)
        state.setdefault("health", {})["seconds"] = round(clock.now() - began, 1)
        log(f"[evolution] start-up health check took {int(clock.now() - began)} s (cap {int(cap)} s)")
        return results

    def evolution_health_build(self, end: float) -> dict[str, Any]:
        dist = self.project_dir / "frontend" / "dist" / "index.html"
        try:
            if not (self.project_dir / "frontend" / "package.json").is_file():
                return {"status": "no frontend", "detail": "frontend/package.json is missing", "dist": dist.is_file()}
            if dist.is_file():
                built = dist.stat().st_mtime
                newer = [p for p in (self.project_dir / "frontend" / "src").rglob("*") if p.is_file() and p.stat().st_mtime > built]
                if not newer:
                    return {"status": "present", "detail": "frontend/dist is newer than frontend/src", "dist": True}
            if not setting_on("evolution_health_build"):
                return {"status": "not built", "detail": "evolution_health_build is off", "dist": dist.is_file()}
            if end - clock.now() < 60:
                return {"status": "not built", "detail": "too little of the health check's time left", "dist": dist.is_file()}
            began = clock.now()
            step = build_frontend(self.project_dir, log=log, deadline=end - 30)
            self.note_duration("build", clock.now() - began)
            if step.ok and dist.is_file():
                return {"status": "built", "detail": f"npm run build in {int(clock.now() - began)} s", "dist": True}
            if step.ok:
                return {"status": "build reported ok", "detail": "but frontend/dist/index.html is missing", "dist": False}
            return {"status": "build failed", "detail": " ".join(str(step.output or "").split())[-200:] or "no output", "dist": False}
        except Exception as exc:
            return {"status": "build crashed", "detail": f"{type(exc).__name__}: {exc}"[:200], "dist": dist.is_file()}

    def evolution_probe(self, which: str, whole: str, node_modules: str, time_limit: float, items: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        began = clock.now()
        try:
            if time_limit < 20:
                raise TimeoutError(f"only {int(time_limit)} s left")
            if not whole.strip():
                raise ValueError("the requirement text could not be read")
            if which == "grid":
                from agent.grid_probes import grid_probes
                rows = grid_probes(whole, self.project_dir, time_limit=time_limit, log=log, node_modules=node_modules, items=items)
            else:
                from agent.session_chain import session_chain_probe
                rows = [session_chain_probe(whole, self.project_dir, time_limit=time_limit, log=log, node_modules=node_modules)]
            for row in rows or []:
                if isinstance(row, dict) and row.get("item") in items:
                    findings = [str(f)[:500] for f in row.get("findings") or []][:5]
                    out[str(row["item"])] = {"item": str(row["item"]), "status": str(row.get("status") or "not run"),
                                             "result": str(row.get("result") or "")[:300], "findings": findings,
                                             **({"parts": row["parts"]} if isinstance(row.get("parts"), dict) else {})}
        except Exception as exc:
            for item in items:
                out[item] = {"item": item, "status": "not run", "why": f"the {which} probe did not run: {type(exc).__name__}: {exc}"[:300]}
        spent = round(clock.now() - began, 1)
        for row in out.values():
            row["seconds"] = spent
            log(f"[evolution] health probe {row['item']}: {row['status']} -- {row.get('result') or row.get('why') or ''}")
        return out

    def evolution_modified_item(self, state: dict[str, Any]) -> dict[str, Any]:
        classes = dict((state.get("classes") or {}).get("classes") or {})
        modified = [leaf for leaf in state.get("implement") or [] if classes.get(leaf) == "modified"]
        files = (state.get("registry") or {}).get("files") or {}
        books = state.get("tests")
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
        if not modified:
            return {"item": "modified", "status": "not run", "why": "no modified leaf"}
        if not isinstance(books, dict) or (cont and cont.get("baseline") != "taken"):
            return {"item": "modified", "status": "not run",
                    "why": f"the start-up baseline was not taken ({cont.get('baseline') or 'unknown'}); no inherited test of a modified leaf has a verdict"}
        per: dict[str, dict[str, list[str]]] = {}
        for rel, entry in files.items():
            for test in entry.get("tests") or []:
                leaf = str(test.get("leaf") or "")
                if test.get("class") != "unverified" or leaf not in modified:
                    continue
                verdict = str((books.get(rel) or {}).get(str(test.get("title")), "not judged"))
                bucket = "passed" if verdict == "passed" else "failed" if verdict == "failed" else "not judged"
                per.setdefault(leaf, {"passed": [], "failed": [], "not judged": []})[bucket].append(f"{Path(rel).name}: {test.get('title')}"[:160])
        if not per:
            return {"item": "modified", "status": "not run", "why": f"none of the {len(modified)} modified leaves has an inherited test"}
        judged = sum(len(v["passed"]) + len(v["failed"]) for v in per.values())
        failed = sum(len(v["failed"]) for v in per.values())
        summary = "; ".join(f"{leaf} {len(v['passed'])} passed / {len(v['failed'])} failed / {len(v['not judged'])} not judged"
                            for leaf, v in sorted(per.items()))
        if not judged:
            return {"item": "modified", "status": "not run", "why": f"the baseline judged none of their tests: {summary}"[:300]}
        return {"item": "modified", "status": "findings" if failed else "clean",
                "result": f"inherited tests of the modified leaves in the baseline (information, not debt): {summary}"[:600],
                "findings": [f"{leaf}: failing at the start {v['failed'][:4]}" for leaf, v in sorted(per.items()) if v["failed"]][:5]}

    def evolution_entry_walk(self, tree: dict[str, Any], implement: list[str], time_limit: float | None = None) -> dict[str, Any]:
        try:
            from agent.outsider_walk import plan_walk_report

            leaves = evolution.tree_index(tree)[0]
            wanted = {str(leaf) for leaf in implement}
            for base, dependents in transitive_dependents(list(leaves.values())).items():
                if wanted & {str(d) for d in dependents}:
                    wanted.add(str(base))
            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
            limit = setting_seconds("det_plan_walk_sec", 300.0) if time_limit is None else max(10.0, time_limit)
            walk = plan_walk_report(whole, self.project_dir, time_limit=limit, log=log, leaf_ids=sorted(wanted))
            classed = self.entry_findings_classed(walk, tree, set(implement))
            rows = walk.get("scenarios") or []
            counts = ", ".join(f"{k} {sum(1 for r in rows if r.get('status') == k)}" for k in ("clean", "finding", "unrun", "no-navigation"))
            inherited = [c for c in classed if c["class"] == "inherited"]
            own = [c for c in classed if c["class"] == "own"]
            completed = bool(walk.get("completed"))
            if not completed and not classed:
                notes = "; ".join(str(n) for n in (walk.get("notes") or [])[-2:])[:200]
                return {"item": "entry", "status": "not run", "why": f"the plan walk did not complete ({notes or 'no note'})"}
            return {"item": "entry", "status": "findings" if classed else "clean",
                    "result": (f"plan walk of {len(wanted)} leaf/leaves ({len(rows)} scenarios: {counts}): {len(inherited)} "
                               f"finding(s) on inherited entry paths (debt), {len(own)} on this round's own controls (not debt)"
                               + ("" if completed else "; the walk did not complete -- the rest is not run")),
                    "findings": [c["problem"][:300] for c in inherited + own][:10], "debt": inherited[:12],
                    "own": [c["problem"][:200] for c in own][:10]}
        except Exception as exc:
            return {"item": "entry", "status": "not run", "why": f"plan walk crashed: {type(exc).__name__}"}

    def entry_findings_classed(self, walk: dict[str, Any], tree: dict[str, Any], implement: set[str]) -> list[dict[str, Any]]:
        from agent.outsider_walk import _finding_text

        names = {n.casefold() for n in self.inherited_leaf_names(tree)}
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in walk.get("scenarios") or []:
            for f in row.get("findings") or []:
                if f.get("outcome") == "ambiguous":
                    continue
                key = (str(f.get("outcome")), str(f.get("route")), str(f.get("name") or ""))
                entry = groups.get(key)
                if entry is None:
                    problem, fix = _finding_text(row, f)
                    entry = groups[key] = {"key": list(key), "problem": problem, "fix": fix, "leaves": [], "scenarios": 0}
                if str(row.get("leaf")) not in entry["leaves"]:
                    entry["leaves"].append(str(row.get("leaf")))
                entry["scenarios"] += 1
        out = []
        for (outcome, _route, name), entry in groups.items():
            prerequisite = any(leaf not in implement for leaf in entry["leaves"])
            inherited = prerequisite or outcome == "sign-in-failed" or (bool(name) and name.casefold() in names)
            entry["class"] = "inherited" if inherited else "own"
            entry["why"] = ("seen in a prerequisite leaf's scenario" if prerequisite else "a sign-in failure" if outcome == "sign-in-failed"
                            else "the inherited leaves state this control" if inherited else "only this round's leaves state this control")
            out.append(entry)
        out.sort(key=lambda e: (e["class"] != "inherited", -e["scenarios"]))
        return out

    STARTUP_DEBT_CAP = 2500
    PROBE_KEYS = ("recalculation", "extent", "session")

    def evolution_debt_attach(self, work: list[dict[str, Any]], tree: dict[str, Any]) -> None:
        state = self.evolution_state()
        if not state or not work or not setting_on("evolution_startup_debt"):
            return
        try:
            from agent import inherited_health

            rows = {str(r.get("item")): r for r in (state.get("health") or {}).get("health_check") or [] if isinstance(r, dict)}
            leaves = evolution.tree_index(tree)[0]
            order = {str(b.get("id")): n for n, b in enumerate(work)}
            members = {str(b.get("id")): [str(m) for m in (b.get("members") or [b.get("id")])] for b in work}
            batch_of = {m: bid for bid, ms in members.items() for m in ms}
            dependents = {str(k): {str(d) for d in v} for k, v in transitive_dependents(list(leaves.values())).items()}
            notes: dict[str, list[str]] = {}
            entry_debt: dict[str, list[dict[str, Any]]] = {}

            def first_batch(leaf_ids: list[str]) -> str | None:
                found = []
                for leaf in leaf_ids:
                    if leaf in batch_of:
                        found.append(batch_of[leaf])
                    else:
                        found += [batch_of[d] for d in dependents.get(leaf, set()) if d in batch_of]
                return min(found, key=lambda b: order[b]) if found else None

            for debt in (rows.get("entry") or {}).get("debt") or []:
                target = first_batch([str(x) for x in debt.get("leaves") or []]) or str(work[0].get("id"))
                entry_debt.setdefault(target, []).append(debt)
                notes.setdefault(target, []).append(f"- [entry] {debt.get('problem')} Fix: {debt.get('fix')} "
                                                    f"(seen in {', '.join(debt.get('leaves') or [])})")
            keys_of: dict[str, list[str]] = {}
            for bid, ms in members.items():
                text = "\n\n".join(self.leaf_requirement_text(leaves[m]) for m in ms if isinstance(leaves.get(m), dict))
                keys_of[bid] = inherited_health.prerequisites_for(text) if text.strip() else []
            for key in self.PROBE_KEYS:
                row = rows.get(key) or {}
                if row.get("status") != "findings" or not row.get("findings"):
                    continue
                target = next((str(b.get("id")) for b in work if key in keys_of.get(str(b.get("id")), [])), str(work[0].get("id")))
                for finding in row.get("findings") or []:
                    notes.setdefault(target, []).append(f"- [{key}] {finding}")
            handed = {}
            for bid, lines in notes.items():
                head = ("INHERITED DEFECTS FOUND AT START-UP (the health check ran them on the application the original round "
                        "delivered, before this round changed anything): repair each where it lives, FIRST, keeping every "
                        "inherited behaviour the requirement does not change -- the new behaviour of this batch builds on it.")
                text, shown = head, 0
                for line in lines:
                    if len(text) + len(line) + 60 > self.STARTUP_DEBT_CAP:
                        text += f"\n- ... {len(lines) - shown} more not shown"
                        break
                    text += "\n" + line
                    shown += 1
                batch = work[order[bid]]
                batch["evolution_startup_debt"] = text
                if bid in entry_debt and setting_on("evolution_entry_debt_slot"):
                    batch["evolution_entry_debt"] = entry_debt[bid]
                handed[bid] = len(lines)
            state.setdefault("health", {})["startup_debt"] = {bid: {"findings": n, "entry": len(entry_debt.get(bid) or [])} for bid, n in handed.items()}
            log("[evolution] start-up health findings handed as inherited debt: "
                + (", ".join(f"{bid} {n} ({len(entry_debt.get(bid) or [])} entry)" for bid, n in handed.items()) or "none"))
        except Exception as exc:
            log(f"[evolution] start-up debt not attached: {exc!r}")

    def evolution_entry_debt_slot(self, node: dict[str, Any]) -> None:
        debt = node.get("evolution_entry_debt") or []
        state = self.evolution_state()
        if not debt or not state:
            return
        book = state.setdefault("health", {}).setdefault("entry_slots", {})
        bid = str(node.get("id"))
        if bid in book:
            return
        began = clock.now()
        slot = setting_seconds("evolution_entry_debt_sec", 900.0)
        book[bid] = {"status": "started"}
        try:
            from agent.outsider_walk import plan_walk_report

            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
            leaf_ids = sorted({str(x) for d in debt for x in d.get("leaves") or []})
            wanted = {tuple(d.get("key") or ()) for d in debt}

            def left_over() -> list[str]:
                walk = plan_walk_report(whole, self.project_dir, time_limit=min(setting_seconds("det_plan_walk_sec", 300.0), 240.0),
                                        log=log, leaf_ids=leaf_ids)
                if not walk.get("completed"):
                    return ["the walk did not complete: " + "; ".join(str(n) for n in (walk.get("notes") or [])[-2:])]
                seen = []
                for row in walk.get("scenarios") or []:
                    for f in row.get("findings") or []:
                        if (str(f.get("outcome")), str(f.get("route")), str(f.get("name") or "")) in wanted:
                            seen.append(f"{f.get('outcome')} '{f.get('name')}' on {f.get('route')} ({row.get('leaf')})")
                return list(dict.fromkeys(seen))

            def check() -> StepResult:
                build = build_frontend(self.project_dir, log=log, deadline=began + slot)
                if not build.ok:
                    return StepResult(False, "FRONTEND BUILD FAILED\n" + str(build.output or "")[-3000:], infra=getattr(build, "infra", ""))
                rest = left_over()
                return StepResult(not rest, ("entry paths still failing:\n" + "\n".join(rest)) if rest else "entry paths clean")

            text = ("START-UP ENTRY-PATH DEBT: the scenarios below start where their GIVEN puts the user and need these "
                    "controls of the INHERITED application on the way; the walk from `/` on the application as delivered "
                    "found them missing or wrong. Repair the inherited pages / navigation only -- keep every other inherited "
                    "behaviour and control kind; do not build this round's new features here.\n"
                    + "\n".join(f"- {d.get('problem')} Fix: {d.get('fix')}" for d in debt))
            before = self.snapshot_sources()
            kept = self.repair_round("inherited-entry", check, text, deadline=began + slot)
            status = "kept" if kept else "not kept"
            if kept and self.protected_specs():
                specs = self.foundation_run_list((), self.CONTINUATION_END_RUN_SEC, f"{bid}: after the entry-debt repair")
                run = run_e2e(self.project_dir, specs, log=log)
                cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
                newly = sorted(failed_spec_names(str(run.output or "")) - set(cont.get("baseline_red") or {})) if not run.ok and not run.skipped else []
                newly = self.protected_red_after_slot(newly, specs, "evolution")
                if newly or (not run.ok and not run.skipped and not failed_spec_names(str(run.output or ""))):
                    restored = self.restore_sources(before)
                    status = "put back"
                    log(f"[evolution] {bid}: the entry-debt repair broke protected specs {newly or '(a run naming none)'}; "
                        f"{restored} file(s) put back")
            if status == "kept":
                node["evolution_startup_debt"] = (str(node.get("evolution_startup_debt") or "") +
                                                  "\n(The entry-path items above were repaired in a slot before this batch; "
                                                  "keep them working.)").strip()
            book[bid] = {"status": status, "seconds": round(clock.now() - began, 1), "findings": len(debt)}
            log(f"[evolution] {bid}: entry-debt repair slot {status} in {int(clock.now() - began)} s (at most {int(slot)} s)")
        except Exception as exc:
            book[bid] = {"status": f"crashed: {type(exc).__name__}", "seconds": round(clock.now() - began, 1)}
            log(f"[evolution] {bid}: entry-debt repair slot crashed: {exc!r}")

    FIRST_LOOK_LATE = re.compile(r"was not on the page when it opened: it was found \d+ ms after")

    def protected_red_after_slot(self, newly: list[str], specs: list[str], what: str) -> list[str]:
        if not newly or not setting_on("slot_protected_recheck"):
            return newly
        try:
            paths = [spec for spec in specs if Path(spec).name in set(newly)]
            if len(paths) != len(set(newly)):
                return newly
            again = run_e2e(self.project_dir, paths, log=log)
            out = str(again.output or "")
            if again.skipped:
                return newly
            red = failed_spec_names(out) if not again.ok else set()
            if not again.ok and not red:
                return newly
            unrun = not_run_spec_names(out) | partly_run_spec_names(out)
            if unrun & set(newly):
                return newly
            flaky = [n for n in newly if n not in red]
            errors = [line.strip()[len("Error:"):].strip() for line in out.splitlines()
                      if line.strip().startswith("Error:")]
            timing_only = bool(red) and bool(errors) and all(self.FIRST_LOOK_LATE.search(e) for e in errors)
            if red and not timing_only:
                log(f"[{what}] protected specs red again, not only by the first-look timing rule: {sorted(red)}")
                return sorted(red)
            cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else None
            if red and cont is not None:
                base = dict(cont.get("baseline_red") or {})
                for name in red:
                    base.setdefault(name, {})
                cont["baseline_red"] = base
                cont["protected"] = [spec for spec in cont.get("protected") or [] if Path(spec).name not in red]
            log(f"[{what}] protected run once more: {flaky} green (a flake), {sorted(red)} red only by the first-look "
                f"timing rule (baseline red from now on); the slot's work stays")
            return []
        except Exception as exc:
            log(f"[{what}] protected re-check failed ({exc!r}); as before")
            return newly

    def seed_foundation_names(self, inv: dict[str, Any]) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
        from agent.data_lifecycle import lifecycle_check

        names = seed_foundation.scenario_names(inv)
        try:
            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            whole = ""
        old = self.project_dir / self.INHERITED_BACKEND_DIR
        limit = setting_seconds("evolution_data_lifecycle_sec", 150.0) + 60.0
        kinds = seed_foundation.global_names(inv) if setting_on("fixture_idempotent") else {}
        findings, report = lifecycle_check(self.project_dir, whole, scenario_names=names, strict=True, log=log,
                                           inherited_backend=old if old.is_dir() else None, time_limit=limit,
                                           **({"name_kinds": kinds} if kinds else {}))
        missing = list(dict.fromkeys(n for bad in (report.get("missing") or {}).values() for n in bad))
        others = [f for f in findings or [] if isinstance(f, dict) and not str(f.get("check") or "").endswith("/scenario_seed_missing")]
        return missing, report or {}, others

    def seed_foundation_extent(self, end: float) -> dict[str, Any]:
        try:
            whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
            build = build_frontend(self.project_dir, log=log, deadline=end)
            if not build.ok:
                return {"item": "extent", "status": "not run", "result": "the frontend did not build"}
            row = self.evolution_probe("grid", whole, str(self.project_dir / "backend" / "node_modules"),
                                       min(300.0, max(0.0, end - clock.now() - 10)), ("extent",)).get("extent") or {}
            row["status"] = self.HEALTH_STATUS.get(str(row.get("status")), "not run")
            return row
        except Exception as exc:
            return {"item": "extent", "status": "not run", "result": f"crashed: {type(exc).__name__}"}

    SEED_B_MIN_SEC = 300.0
    SEED_B_HEAD = ("SEED FOUNDATION, STAGE B -- the grid's extent only. The data migration of this round's pre-provisioned "
                   "records is already in place: do not change it, the seed or any data, and do not build any feature of "
                   "this round.\n")
    SEED_B_RULE = ("GRID WIDENING RULE: widen the grid by changing its dimension constants (the column count, the row count, "
                   "any list of column letters) and EVERY place that uses them -- the clamps for selection, keyboard "
                   "movement, paste, row / column insert and delete, and the rendered range; the rendered range is "
                   "max(the minimum range, the used range). Never patch individual cells or rows. Keep every inherited "
                   "control, its role and its accessible name (worksheet tabs, cells, headers, menus) exactly as it is. "
                   "Before the edits, list the references you changed in at most 5 lines.")
    SEED_B_LIST = " Before the edits, list the references you changed in at most 5 lines."

    def seed_b_rule(self) -> str:
        if setting_on("fixture_idempotent") and self.SEED_B_RULE.endswith(self.SEED_B_LIST):
            return self.SEED_B_RULE[:-len(self.SEED_B_LIST)]
        return self.SEED_B_RULE

    SEED_B_PRE_HEAD = ("Your grid edit broke these protected inherited specs (they passed on the application as delivered); "
                       "their first error lines follow. Repair the grid change so they pass again -- keep the widened "
                       "extent and every inherited control, role and name they use:\n")
    SEED_GRID_MARKS = (("gridcell", re.compile(r"gridcell")),
                       ("worksheet tabs", re.compile(r"""['"]tab['"]|\bSheet\d+\b|(?i:worksheet tabs?)""")),
                       ("insert row", re.compile(r"""(?i)\binsert\b[^\n'"`]{0,30}\brows?\b""")))
    SEED_GRID_NAME = re.compile(r"(?i)grid|cell|sheet|row|column")

    def seed_foundation_plan(self, inv: dict[str, Any] | None) -> tuple[float, bool, dict[str, Any]]:
        state = self.evolution_state() or {}
        rows = (state.get("health") or {}).get("health_check") or []
        row = next((r for r in rows if isinstance(r, dict) and r.get("item") == "extent"), {})
        short = bool(((inv or {}).get("extent") or {}).get("grid")) and str(row.get("status")) == "findings"
        if short and setting_on("seed_foundation_staged"):
            return setting_seconds("seed_foundation_staged_sec", 2400.0), True, row
        return setting_seconds("evolution_seed_foundation_sec", 1500.0), False, row

    def seed_protected_est(self) -> float:
        n = len(self.protected_specs())
        if not n:
            return 0.0
        return min(self.CONTINUATION_END_RUN_SEC, self.suite_est(n + 3) / self.EST_FACTOR)

    @staticmethod
    def seed_stage_files(before: dict[str, bytes], after: dict[str, bytes]) -> str:
        import difflib

        rows = []
        for rel in sorted(set(before) | set(after)):
            old, new = before.get(rel), after.get(rel)
            if old == new:
                continue
            a = old.decode("utf-8", "replace").splitlines() if old is not None else []
            b = new.decode("utf-8", "replace").splitlines() if new is not None else []
            plus = minus = 0
            for line in list(difflib.unified_diff(a, b, n=0, lineterm=""))[2:]:
                if line.startswith("@@"):
                    continue
                plus += line.startswith("+")
                minus += line.startswith("-")
            kind = "new, " if old is None else "deleted, " if new is None else ""
            rows.append(f"{rel} ({kind}+{plus} -{minus})")
        if not rows:
            return "(no file changed)"
        return "; ".join(rows[:20]) + (f"; ... {len(rows) - 20} more" if len(rows) > 20 else "")

    def seed_grid_precheck_specs(self, limit: int = 3) -> list[str]:
        protected = self.protected_specs()
        texts: dict[str, str] = {}
        for spec in protected:
            try:
                texts[spec] = (self.project_dir / spec).read_text(encoding="utf-8", errors="replace")
            except OSError:
                texts[spec] = ""
        picked: list[str] = []
        for _, mark in self.SEED_GRID_MARKS:
            hit = next((s for s in protected if s not in picked and mark.search(texts[s])), None)
            if hit and len(picked) < limit:
                picked.append(hit)
        for spec in protected:
            if len(picked) >= limit:
                break
            if spec not in picked and any(mark.search(texts[spec]) for _, mark in self.SEED_GRID_MARKS):
                picked.append(spec)
        if not picked:
            picked = [s for s in protected if self.SEED_GRID_NAME.search(Path(s).name)][:limit]
        return picked

    def seed_newly_red(self, run: Any) -> list[str] | None:
        if run.ok or run.skipped:
            return None
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
        names = failed_spec_names(str(run.output or ""))
        newly = sorted(names - set(cont.get("baseline_red") or {}))
        if newly:
            return newly
        return None if names else ["(a run naming none)"]

    def seed_run_verdict(self, run: Any, files: list[str] | tuple) -> tuple[list[str], str, list[str]]:
        out = str(getattr(run, "output", "") or "")
        infra = str(getattr(run, "infra", "") or "")
        if run.skipped:
            return [], "skipped (Playwright did not run)", []
        names = failed_spec_names(out) if not run.ok else set()
        unrun = not_run_spec_names(out) | partly_run_spec_names(out)
        cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
        newly = sorted(names - set(cont.get("baseline_red") or {}))
        why = ""
        if infra:
            why = f"an infrastructure fault ({infra[:160]})"
        elif unrun:
            why = f"not completed: {sorted(unrun)[:10]} not run or run only in part"
        elif not run.ok and not names:
            return ["(a run naming none)"], "", []
        passed = [] if infra else [s for s in files if Path(s).name not in names and Path(s).name not in unrun]
        return newly, why, passed

    def seed_restore_b(self, snap: dict[str, bytes], files: str, why: str) -> str:
        try:
            restored = self.restore_sources(snap)
        except Exception as exc:
            log(f"[seed-foundation] stage B restore INCOMPLETE: the put-back crashed ({exc!r}); {files}")
            return "restore incomplete"
        if getattr(restored, "complete", True):
            log(f"[seed-foundation] stage B restored: {files} ({restored} file(s) put back; the fixtures stay){why}")
            return "restored"
        log(f"[seed-foundation] stage B restore INCOMPLETE: {self.seed_unrestored(snap)}")
        return "restore incomplete"

    def seed_unrestored(self, snap: dict[str, bytes]) -> list[str]:
        try:
            now = self.snapshot_sources()
            return sorted(rel for rel in set(snap) | set(now) if snap.get(rel) != now.get(rel))[:20]
        except Exception as exc:
            return [f"(unknown: {exc!r})"]

    def seed_foundation_stage_b(self, inv: dict[str, Any], extent_row: dict[str, Any], missing_a: list[str],
                                others0: list[dict[str, Any]], began: float, slot: float,
                                dups_a: dict[str, Any] | None = None) -> str:
        end_impl = began + slot - self.seed_protected_est()
        if end_impl - clock.now() < self.SEED_B_MIN_SEC:
            log(f"[seed-foundation] stage B not run: {int(max(0.0, end_impl - clock.now()))} s of the slot left before its "
                "protected run (the grid extent stays the batches' inherited debt)")
            return "not run: no time"
        grid_line = next((line[2:] for line in seed_foundation.foundation_text(inv).splitlines()
                          if line.startswith("- The worksheet grid must render")), "")
        text = (self.SEED_B_HEAD + grid_line + "\n" + self.seed_b_rule()
                + "\n\nThe start-up extent probe: " + str(extent_row.get("result") or "")[:400])
        pre = self.seed_grid_precheck_specs()
        snap = self.snapshot_sources()
        seen: dict[str, Any] = {}

        def check(with_pre: bool) -> StepResult:
            problems: list[str] = []
            missing, report, others = self.seed_foundation_names(inv)
            if not report.get("completed"):

                problems.append("the data-lifecycle check did not complete (B is unverified): "
                                + "; ".join(str(n) for n in (report.get("notes") or [])[-2:]))
            lost = [n for n in missing if n not in missing_a]
            if lost:
                problems.append("stated records that were present are now missing (do not touch the data): "
                                + ", ".join(f"`{n}`" for n in lost[:20]))
            problems += lifecycle_new_problems(others, others0, report, dups_a)
            if not problems:
                row = self.seed_foundation_extent(began + slot)
                if row.get("status") != "clean":
                    problems.append(f"grid extent ({row.get('status')}): " + str(row.get("result") or "")[:400])
                else:
                    log(f"[seed-foundation] grid extent after stage B: clean -- {str(row.get('result') or '')[:200]}")
            if with_pre and pre and not problems:

                run = run_e2e(self.project_dir, pre, log=log, **until_kw(end_impl))
                bad, why, passed = self.seed_run_verdict(run, pre)
                if bad:
                    problems.append(f"protected inherited specs now failing {bad}:\n" + "\n".join(first_error_lines(str(run.output or ""), limit=8)))
                elif why:
                    problems.append(f"the protected pre-check could not establish safety: {why}")
                else:
                    seen["pre_passed"] = passed
            seen["after"] = self.snapshot_sources()
            return StepResult(not problems, "\n".join(problems) if problems else "grid extent reached; fixtures intact")

        kept = False
        unverified = ""
        pre_passed: list[str] = []
        try:
            kept = self.repair_round("seed-foundation-grid", lambda: check(False), text, deadline=end_impl)
            if kept and pre:
                run = run_e2e(self.project_dir, pre, log=log, **until_kw(end_impl))
                bad, why, passed = self.seed_run_verdict(run, pre)
                if bad:
                    errors = first_error_lines(str(run.output or ""), limit=8)
                    log(f"[seed-foundation] stage B pre-check red {bad} ({[Path(s).name for s in pre]}); one more round")
                    seen.pop("pre_passed", None)
                    kept = self.repair_round("seed-foundation-grid", lambda: check(True),
                                             text + "\n\n" + self.SEED_B_PRE_HEAD + "\n".join(errors), deadline=end_impl, max_rounds=0)
                    pre_passed = list(seen.get("pre_passed") or []) if kept else []
                elif why:
                    kept, unverified = False, f"the pre-check {[Path(s).name for s in pre]}: {why}"
                    log(f"[seed-foundation] stage B pre-check did not establish safety ({why}); stage B goes back")
                else:
                    pre_passed = passed
                    log(f"[seed-foundation] stage B pre-check green: {[Path(s).name for s in pre]}")
            if kept and self.protected_specs():
                specs = self.foundation_run_list(tuple(pre_passed), self.CONTINUATION_END_RUN_SEC, "after the seed foundation's grid stage")
                if specs:
                    run = run_e2e(self.project_dir, specs, log=log, **until_kw(began + slot))
                    bad, why, _ = self.seed_run_verdict(run, specs)
                    if bad:
                        kept = False
                        log(f"[seed-foundation] the grid stage broke protected specs {bad}")
                    elif why:
                        kept, unverified = False, f"the protected run: {why}"
                        log(f"[seed-foundation] stage B protected run did not establish safety ({why}); stage B goes back")
                else:
                    log("[seed-foundation] stage B protected run: every protected spec it would run passed in the pre-check")
        except Exception as exc:
            kept = False
            log(f"[seed-foundation] stage B crashed: {exc!r}")
        files = self.seed_stage_files(snap, seen.get("after", snap))
        if not kept:
            return self.seed_restore_b(snap, files, f" -- unverified: {unverified}" if unverified else "")
        log(f"[seed-foundation] stage B kept: {files}")
        return "kept"

    def evolution_seed_foundation_slot(self) -> None:
        state = self.evolution_state()
        if not state or not setting_on("evolution_seed_foundation"):
            return
        inv = self.__dict__.get("seed_inventory")
        if not isinstance(inv, dict) or not inv.get("names"):
            return
        book = state.setdefault("health", {})
        if book.get("seed_foundation"):
            return
        began = clock.now()
        slot, plan_b, extent_row = self.seed_foundation_plan(inv)
        staged = setting_on("seed_foundation_staged")
        book["seed_foundation"] = {"status": "started"}
        before: dict[str, bytes] | None = None
        try:
            if not self.hard_fits("the seed foundation slot", slot):
                book["seed_foundation"] = {"status": "not run: no time"}
                log("[seed-foundation] not run: the slot does not fit before the hard end")
                return
            missing0, report0, others0 = self.seed_foundation_names(inv)
            dups0 = dict(report0.get("duplicates") or {})
            counts0 = dict(report0.get("duplicate_counts") or {})
            ext = inv.get("extent") or {}
            extent_short = bool(ext.get("grid")) and str(extent_row.get("status")) == "findings"
            if not report0.get("completed") and not missing0:
                book["seed_foundation"] = {"status": "not run: the fixture check did not complete",
                                           "notes": (report0.get("notes") or [])[-3:]}
                log(f"[seed-foundation] not run: the fixture check did not complete ({(report0.get('notes') or ['?'])[-1]})")
                return
            log(f"[seed-foundation] before: {len(missing0)} of {len(inv.get('names') or [])} stated name(s) missing, "
                f"{len(report0.get('misplaced') or {})} under another display name, {len(others0)} other lifecycle finding(s); "
                f"grid extent {ext.get('cell') or 'n/a'}: {extent_row.get('status') or 'n/a'}")
            for scenario, bad in list((report0.get("missing") or {}).items())[:40]:
                log(f"[seed-foundation]   missing in {scenario}: {bad}")
            if not missing0 and not extent_short and not dups0:
                book["seed_foundation"] = {"status": "clean", "seconds": round(clock.now() - began, 1)}
                log("[seed-foundation] kept: nothing to add (every stated name is present under its display name)")
                return
            extent_a = extent_short and not plan_b
            misplaced0 = dict(report0.get("misplaced") or {})
            text = seed_foundation.foundation_text(inv, visible=setting_on("fixture_visible_name_prompt"))
            if not extent_a:
                text = "\n".join(line for line in text.splitlines() if not line.startswith("- The worksheet grid must render"))
            text += ("\n\nThe deterministic fixture check (empty database, then the app's own initialisation) does not "
                     f"find these {len(missing0)} stated name(s) as a record's own display name: "
                     + ", ".join(f"`{n}`" for n in missing0[:120])
                     + ("".join(f"\n- `{n}`: {why}" for n, why in list(misplaced0.items())[:20]) if misplaced0 else ""))
            if extent_a:
                text += "\n\nThe start-up extent probe: " + str(extent_row.get("result") or "")[:400]
            if dups0:
                from agent.data_lifecycle import FIX_DUPLICATE
                text += ("\n\nThe deterministic fixture check finds these stated name(s) held by more than one record of "
                         "their kind in a table the check takes for top-level: " + "; ".join(f"`{n}` ({w})" for n, w in list(dups0.items())[:40])
                         + ". " + FIX_DUPLICATE)
                log(f"[seed-foundation]   duplicated before: {sorted(dups0)[:40]}")

            seed_state: dict[str, bool] = {}

            def check() -> StepResult:
                missing, report, others = self.seed_foundation_names(inv)
                problems: list[str] = []
                if not report.get("completed") and not missing:
                    problems.append("the fixture check did not complete: " + "; ".join(str(n) for n in (report.get("notes") or [])[-2:]))
                problems += lifecycle_new_problems(others, others0, report, counts0)
                dups = dict(report.get("duplicate_counts") or {})
                if dups0 and not any(_count(dups.get(k, 1)) < _count(c) for k, c in counts0.items()):

                    log("[seed-foundation]   still reported more than once (the implementer may have found them "
                        "separate records): " + ", ".join(f"`{n}`" for n in list(dups0)[:20]))
                if dups0 or dups:
                    book["seed_foundation"]["dups_after"] = dups
                new_missing = [n for n in missing if n not in missing0]
                if new_missing:
                    problems.append("names that were present are now missing: " + ", ".join(f"`{n}`" for n in new_missing[:20]))

                if not setting_on("seed_slot_keep_leftovers"):

                    seed_state["only_soft"] = False
                    if report.get("misplaced"):
                        problems.append("under another display name: " + "; ".join(f"`{n}` ({w})" for n, w in list(report["misplaced"].items())[:10]))
                    if missing and len(missing) >= len(missing0):
                        problems.append(f"still missing ({len(missing)}): " + ", ".join(f"`{n}`" for n in missing[:60]))
                    if extent_a and not problems:
                        row = self.seed_foundation_extent(began + slot)
                        if row.get("status") == "findings":
                            problems.append("grid extent: " + str(row.get("result") or "")[:400])
                        else:
                            log(f"[seed-foundation] grid extent after the slot: {row.get('status')} -- {str(row.get('result') or '')[:200]}")
                else:
                    from agent.data_lifecycle import DISPLAY_NAME_EVIDENCE
                    soft = {n for n, w in (report.get("misplaced") or {}).items() if DISPLAY_NAME_EVIDENCE in str(w)}
                    soft0 = {n for n, w in misplaced0.items() if DISPLAY_NAME_EVIDENCE in str(w)}
                    hard_misplaced = {n: w for n, w in (report.get("misplaced") or {}).items() if n not in soft}
                    hard_misplaced0 = {n for n in misplaced0 if n not in soft0}

                    new_hard = {n: w for n, w in hard_misplaced.items() if n not in hard_misplaced0}
                    if new_hard:
                        problems.append("under another display name: " + "; ".join(f"`{n}` ({w})" for n, w in list(new_hard.items())[:10]))
                    damage = bool(problems)
                    leftovers: list[str] = []
                    old_hard = {n: w for n, w in hard_misplaced.items() if n in hard_misplaced0}
                    if old_hard:
                        leftovers.append("under another display name: " + "; ".join(f"`{n}` ({w})" for n, w in list(old_hard.items())[:10]))
                    missing_hard = [n for n in missing if n not in soft]
                    missing0_hard = [n for n in missing0 if n not in soft0]
                    if missing_hard:
                        leftovers.append(f"still missing ({len(missing_hard)}): " + ", ".join(f"`{n}`" for n in missing_hard[:60]))
                    if extent_a and not damage:
                        row = self.seed_foundation_extent(began + slot)
                        if row.get("status") == "findings":
                            leftovers.append("grid extent: " + str(row.get("result") or "")[:400])
                        else:
                            log(f"[seed-foundation] grid extent after the slot: {row.get('status')} -- {str(row.get('result') or '')[:200]}")
                    progress = len(missing_hard) < len(missing0_hard) or len(hard_misplaced) < len(hard_misplaced0)
                    seed_state["only_soft"] = (not damage and (bool(leftovers) or bool(soft))
                                               and (progress or (bool(soft) and not leftovers)))
                    problems += leftovers
                    if soft:
                        problems.append("under another display name (the row's display_name shows another value; set it to "
                                        "the stated name or leave it empty): "
                                        + "; ".join(f"`{n}` ({(report.get('misplaced') or {}).get(n, '')})" for n in sorted(soft)[:10]))
                book["seed_foundation"]["missing_after"] = missing
                return StepResult(not problems, "\n".join(problems) if problems else
                                  f"fixtures: {len(missing0) - len(missing)} of {len(missing0)} missing name(s) added")

            seen: dict[str, Any] = {}

            def check_seen() -> StepResult:

                out = check()
                seen["after"] = self.snapshot_sources()
                return out

            status = "clean"
            if missing0 or dups0 or not plan_b:
                before = self.snapshot_sources()
                deadline_a = began + slot - (self.seed_protected_est() if plan_b else 0.0)
                kept = self.repair_round("seed-foundation", check_seen if staged else check, text, deadline=deadline_a,
                                         keep_if=lambda: seed_state.get("only_soft", False))
                status = "kept" if kept else "rolled back"
                if kept and self.protected_specs():
                    specs = self.foundation_run_list((), self.CONTINUATION_END_RUN_SEC, "after the seed foundation")
                    run = run_e2e(self.project_dir, specs, log=log)
                    cont = self.continuation if isinstance(self.__dict__.get("continuation"), dict) else {}
                    newly = sorted(failed_spec_names(str(run.output or "")) - set(cont.get("baseline_red") or {})) if not run.ok and not run.skipped else []
                    newly = self.protected_red_after_slot(newly, specs, "seed-foundation")
                    if newly or (not run.ok and not run.skipped and not failed_spec_names(str(run.output or ""))):
                        restored = self.restore_sources(before)
                        status = "rolled back"
                        log(f"[seed-foundation] the migration broke protected specs {newly or '(a run naming none)'}; {restored} file(s) put back")
                        if not getattr(restored, "complete", True):
                            book["seed_foundation"]["restore"] = "incomplete"
                            log(f"[seed-foundation] stage A restore INCOMPLETE: {self.seed_unrestored(before)}")
                if staged:
                    log(f"[seed-foundation] stage A {'kept' if status == 'kept' else 'restored'}: "
                        + self.seed_stage_files(before, seen.get("after", before)))
            status_a, stage_b = status, ""
            if plan_b:
                if status in ("kept", "clean"):
                    after_a = list(book["seed_foundation"].get("missing_after") or []) if status == "kept" else list(missing0)
                    dups_a = dict(book["seed_foundation"].get("dups_after", counts0) or {}) if status == "kept" else counts0
                    stage_b = self.seed_foundation_stage_b(inv, extent_row, after_a, others0, began, slot,
                                                           **({"dups_a": dups_a} if dups_a else {}))
                else:
                    stage_b = "not run: stage A was not kept"
                    log("[seed-foundation] stage B not run: stage A was not kept (the grid extent stays the batches' inherited debt)")
                if status == "clean":
                    status = "kept" if stage_b == "kept" else "rolled back"
            left = list(book["seed_foundation"].get("missing_after") or []) if status == "kept" else missing0
            book["seed_foundation"] = {"status": status, "seconds": round(clock.now() - began, 1), "missing_before": len(missing0),
                                       "missing_after": left}
            if plan_b:
                book["seed_foundation"].update(stage_a=status_a, stage_b=stage_b)
            log(f"[seed-foundation] {status}: {len(missing0) - len(left)} of {len(missing0)} missing name(s) added in "
                f"{int(clock.now() - began)} s (at most {int(slot)} s); still missing: {left[:30]}"
                + (f"; stage A {status_a}, stage B {stage_b}" if plan_b else ""))
        except Exception as exc:
            book["seed_foundation"] = {"status": f"crashed: {type(exc).__name__}", "seconds": round(clock.now() - began, 1)}

            if before is None:
                log(f"[seed-foundation] rolled back: the slot crashed before stage A changed anything: {exc!r}")
                return
            try:
                restored = self.restore_sources(before)
            except Exception as again:
                log(f"[seed-foundation] stage A restore INCOMPLETE: the slot crashed ({exc!r}) and the put-back crashed ({again!r})")
                return
            if getattr(restored, "complete", True):
                log(f"[seed-foundation] rolled back: the slot crashed: {exc!r}; {restored} file(s) put back")
            else:
                book["seed_foundation"]["restore"] = "incomplete"
                log(f"[seed-foundation] stage A restore INCOMPLETE: {self.seed_unrestored(before)} (the slot crashed: {exc!r})")

    def seed_foundation_inventory(self, tree: dict[str, Any]) -> dict[str, Any] | None:
        if not setting_on("evolution_seed_foundation"):
            return None
        state = self.evolution_state()
        if not state:
            return None
        cached = self.__dict__.get("seed_inventory")
        if isinstance(cached, dict):
            return cached if cached.get("scenarios") else None
        inv = seed_foundation.inventory(tree, [str(m) for m in state.get("implement") or []])
        self.seed_inventory = inv
        log(seed_foundation.summary(inv))
        for row in inv.get("scenarios") or []:
            log(f"[seed-foundation]   {row.get('leaf')} / {str(row.get('scenario'))[:90]}: names {row.get('names')}"
                + (f"; advisory {row.get('advisory')[:8]}" if row.get("advisory") else ""))
        return inv if inv.get("scenarios") else None

    def evolution_extra_budget(self, work: list[dict[str, Any]]) -> float:
        state = self.evolution_state()
        if not state:
            return 0.0
        slots = sum(1 for b in work if b.get("evolution_entry_debt")) if setting_on("evolution_entry_debt_slot") else 0
        inv = self.__dict__.get("seed_inventory")
        foundation = (self.seed_foundation_plan(inv)[0]
                      if setting_on("evolution_seed_foundation") and isinstance(inv, dict) and inv.get("names") else 0.0)
        return float((state.get("health") or {}).get("seconds") or 0.0) + slots * setting_seconds("evolution_entry_debt_sec", 900.0) + foundation

    def inherited_leaf_names(self, tree: dict[str, Any]) -> set[str]:
        try:
            state = self.evolution_state() or {}
            classes = dict((state.get("classes") or {}).get("classes") or {})
            implement = {str(m) for m in state.get("implement") or []}
            leaves = list(evolution.tree_index(tree)[0].items())
            old_tree = self.__dict__.get("_old_tree")
            if isinstance(old_tree, dict):
                mine = {leaf_id for leaf_id, _ in leaves}
                leaves += [(leaf_id, leaf) for leaf_id, leaf in evolution.tree_index(old_tree)[0].items() if leaf_id not in mine]
            names: set[str] = set()
            for leaf_id, leaf in leaves:
                if leaf_id in implement or (leaf_id in classes and classes[leaf_id] != "inherited"):
                    continue
                text = describe_node({k: v for k, v in leaf.items() if k != "ancestor_context"})
                names |= set(quoted_names(text)) | batch_unquoted_names(text)
            return {n for n in names if n.strip()}
        except Exception:
            return set()

    def evolution_health_attach(self, work: list[dict[str, Any]], tree: dict[str, Any]) -> None:
        state = self.evolution_state()
        if not state or not work:
            return
        try:
            from agent import inherited_health

            if setting_on("evolution_health_rules"):
                leaves = evolution.tree_index(tree)[0]
                shown: dict[str, list[str]] = {}
                for batch in work:
                    members = [str(m) for m in (batch.get("members") or [batch.get("id")])]
                    text = "\n\n".join(self.leaf_requirement_text(leaves[m]) for m in members if isinstance(leaves.get(m), dict))
                    if not text.strip():
                        continue
                    rules = inherited_health.health_rules(text)
                    if rules:
                        batch["evolution_health"] = rules
                        shown[str(batch.get("id"))] = inherited_health.prerequisites_for(text)
                log(f"[evolution] shared-prerequisite rules for {len(shown)} of {len(work)} batch(es): "
                    + ", ".join(f"{b} {keys}" for b, keys in list(shown.items())[:12]))
            note = str(((state.get("health") or {}).get("single_source") or {}).get("note") or "")
            if note and setting_on("evolution_single_source"):
                work[0]["evolution_inherited_debt"] = note
                state["health"]["single_source"]["handed_to"] = str(work[0].get("id"))
                log(f"[evolution] inherited debt ({len(note)} characters) handed to the first batch {work[0].get('id')}")
        except Exception as exc:
            log(f"[evolution] shared-prerequisite rules not attached: {exc!r}")

    def evolution_single_source_closing(self) -> None:
        try:
            state = self.evolution_state()
            if not state or not setting_on("evolution_single_source"):
                return
            from agent import single_source

            found = single_source.audit(self.project_dir)
            now = [self.single_source_dict(f) for f in found]
            text = single_source.render(found, 8000)
            book = (state.get("health") or {}).get("single_source")
            start = book.get("start") if isinstance(book, dict) else None
            report = self.report.setdefault("evolution_health", {})
            if not isinstance(start, list):
                log(f"[evolution] closing single-source audit: {len(now)} finding(s); no start-up audit to compare with"
                    + (":\n" + text if text else ""))
                new, still, gone = list(now), [], 0
            else:
                before = {self.single_source_key(f) for f in start if isinstance(f, dict)}
                after = {self.single_source_key(f) for f in now}
                new = [f for f in now if self.single_source_key(f) not in before]
                still = [f for f in now if self.single_source_key(f) in before]
                gone = len(before - after)
                log(f"[evolution] closing single-source audit: {len(now)} finding(s) -- {len(new)} new in this round, "
                    f"{len(still)} still there from the start-up, {gone} gone" + (":\n" + text if text else ""))
            closing = {"findings": len(now), "new": [{"kind": f["kind"], "files": f["files"][:6], "lines": f["lines"][:6],
                                                      "message": f["message"][:300]} for f in new[:20]],
                       "still": len(still), "gone": gone}
            report["single_source_closing"] = closing
            if isinstance(book, dict):
                book["closing"] = closing
            open_book = self.__dict__.setdefault("open_findings", {})
            entries = [(f, "new in this round") for f in new] + [(f, "inherited") for f in still]
            for finding, origin in entries[:self.SINGLE_SOURCE_HANDOFF_MAX]:
                key = "single-source:" + hashlib.sha256(self.single_source_key(finding).encode("utf-8")).hexdigest()[:16]
                where = ", ".join(f"{rel}:{line}" for rel, line in zip(finding["files"], finding["lines"])) or ", ".join(finding["files"])
                open_book[key] = {"class": f"single-source {finding['kind']} ({origin})", "where": where[:160], "batch": "closing"}
        except Exception as exc:
            log(f"[evolution] closing single-source audit crashed: {exc!r}")

    INHERITED_NAMES_MAX = 400

    def stamp_inherited_names(self, work: list[dict[str, Any]], tree: dict[str, Any]) -> None:
        try:
            state = self.evolution_state()
            if not state or not work:
                return
            classes = dict((state.get("classes") or {}).get("classes") or {})
            implement = {str(m) for m in state.get("implement") or []}
            leaves = list(evolution.tree_index(tree)[0].items())
            old_tree = self.__dict__.get("_old_tree")
            if isinstance(old_tree, dict):
                mine = {leaf_id for leaf_id, _ in leaves}
                leaves += [(leaf_id, leaf) for leaf_id, leaf in evolution.tree_index(old_tree)[0].items() if leaf_id not in mine]
            names: set[str] = set()
            for leaf_id, leaf in leaves:
                if leaf_id in implement or (leaf_id in classes and classes[leaf_id] != "inherited"):
                    continue
                text = describe_node({k: v for k, v in leaf.items() if k != "ancestor_context"})
                names |= set(quoted_names(text)) | batch_unquoted_names(text)
            stamped = sorted(n for n in names if n.strip())[:self.INHERITED_NAMES_MAX]
            full = sorted(n for n in names if n.strip()) if setting_on("findings_owner_batch_v2", True) else None
            for batch in work:
                batch["inherited_names"] = list(stamped)
                if full is not None:
                    batch["inherited_names_all"] = list(full)
                    batch["inherited_names_complete"] = True
            log(f"[evolution] {len(stamped)} name(s) of inherited leaves stamped on the batches (never a later control)")
        except Exception as exc:
            log(f"[evolution] inherited names not stamped: {exc!r}")

    def evolution_unverified_only(self, output: str) -> bool:
        state = self.evolution_state()
        if not state:
            return False
        titles = failed_test_titles(output)
        if not titles or not any(titles.values()):
            return False
        files = {Path(rel).name: entry for rel, entry in ((state.get("registry") or {}).get("files") or {}).items()}
        modified = set(state.get("implement") or [])
        for name, failing in titles.items():
            entry = files.get(name)
            if not entry or not failing:
                return False
            classes = {str(t.get("title")): t for t in entry.get("tests") or []}
            for title in failing:
                test = classes.get(title)
                if not test or test.get("class") != "unverified" or test.get("leaf") not in modified:
                    return False
        return True

    def final_suite_books(self, last_suite: Any) -> dict[str, Any]:
        if last_suite is None or getattr(last_suite, "skipped", False):
            return {"judged": False, "why": "the suite did not run"}
        output = str(getattr(last_suite, "output", "") or "")
        ok = bool(getattr(last_suite, "ok", False))
        red = failed_spec_names(output) if not ok else set()
        unrun = (not_run_spec_names(output) | partly_run_spec_names(output)) if not ok else set()
        if not ok and not red and not unrun:
            return {"judged": False, "why": "the last suite run failed without naming a spec"}
        specs = {Path(s).name: ("red" if Path(s).name in red else "not run" if Path(s).name in unrun else "green")
                 for s in self.__dict__.get("e2e_specs") or []}
        titles = {name: sorted(t) for name, t in (failed_test_titles(output) if not ok else {}).items()}
        return {"judged": True, "ok": ok, "specs": specs, "failed_titles": titles}

    def write_stage_handoff(self) -> bool:
        tree = self.__dict__.get("handoff_tree")
        if not isinstance(tree, dict) or not setting_on("stage_handoff"):
            return False
        try:
            text = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return stage_handoff.write_handoff(self, tree, str(self.__dict__.get("requirement_sha") or ""), text, self.project_dir, log=log)

    def clean_runtime_state(self) -> None:
        backend = self.project_dir / "backend"
        shutil.rmtree(backend / ".arc-test-db", ignore_errors=True)
        for report in backend.glob(".arc-e2e-report-*.json"):
            report.unlink(missing_ok=True)
        removed = []
        for pattern in ("database.db", "database.db-journal", "database.db-wal", "database.db-shm"):
            for path in (backend / pattern, backend / "src" / "database" / pattern, backend / "data" / pattern):
                if path.is_file():
                    try:
                        path.unlink()
                        removed.append(path.relative_to(self.project_dir).as_posix())
                    except OSError:
                        pass
        if removed:
            log(f"[factory] removed stale runtime database file(s): {removed}")

        self.write_stage_handoff()

    def _set_e2e_status(self, spec: str, passed: bool) -> None:
        self.set_file_tests_status(spec, passed)

    @property
    def install_failed(self) -> bool:
        if not getattr(self, "_install_failed", False):
            return False
        from agent.verify import missing_dependencies
        return any(missing_dependencies(self.project_dir, package) for package in ("frontend", "backend"))

    @install_failed.setter
    def install_failed(self, value: bool) -> None:
        self._install_failed = bool(value)

    def run(self) -> int:

        watchdog.start_for(self, started_mono=MONO_STARTED, started_wall=PROCESS_STARTED, log=log)

        self.stage_number = staged_stage_number(self.requirements_dir)
        self.stage_target = staged_run_target(self.requirements_dir)
        if self.stage_target:
            ends = [e for e in (self.hard_end_at, PROCESS_STARTED + self.stage_target) if e is not None]
            self.hard_end_at = min(ends)
            monos = [e for e in (self.__dict__.get("hard_end_mono"), MONO_STARTED + self.stage_target) if e is not None]
            self.hard_end_mono = min(monos)
            watchdog.set_llm_end(self)
            dog = getattr(watchdog, "_ACTIVE", None)
            if dog is not None:
                dog.move_end(PROCESS_STARTED + self.stage_target + setting_seconds("stage_watchdog_slack_sec", 2700.0))
            log(f"[factory] staged task: run target {self.stage_target / 3600.0:.2f} h (hard end and watchdog pulled in)")
        if self.apply_contest_deadline():
            return 0
        clock.start(log=log)
        set_global_deadline(self.hard_end())
        tree = load_tree(self.requirements_dir)
        self.handoff_tree = tree

        self.requirement_sha = stage_continuation.requirement_sha(self.requirements_dir)
        found = (stage_continuation.detect(self.project_dir, tree, AGENT_ROOT / "template")
                 if setting_on("stage_continuation") else None)
        self.continuation_found = found

        self.inherited_app = (evolution.preservation(self.project_dir, AGENT_ROOT / "template") if setting_on("evolution_preserve")
                              else {"preserve": False, "signals": {}, "reason": "evolution_preserve is off"})
        if self.inherited_app.get("preserve"):
            log(f"[evolution] the output folder holds an application an earlier run delivered ({self.inherited_app.get('reason')}); "
                "it is never overwritten by the starter template")
        if not found and not self.inherited_app.get("preserve"):

            from agent.stage_controls import clear_baseline
            clear_baseline(self.project_dir)
        resume_digest = requirement_digest(tree)
        done_ids = self.load_checkpoint(resume_digest)

        set_global_deadline(self.hard_end())

        evo = None
        if self.inherited_app.get("preserve") and not done_ids and setting_on("evolution_mode"):
            try:
                evo = self.evolution_enter(tree, found)
            except Exception as exc:
                log(f"[evolution] classification crashed: {exc!r}; the run goes on without the evolution round")
                evo = None
        elif done_ids and self.evolution_state():
            evo = self.evolution_state()
            log(f"[evolution] resumed: {len(evo.get('implement') or [])} leaf/leaves of this round, the rest inherited")
        if evo:
            self.apply_evolution_run_target()

        from agent import conventions as project_conventions
        project_conventions.set_evolution_round(bool(evo) and setting_on("evolution_data_lifecycle"))
        if (found or evo) and not done_ids:
            if not evo:
                self.continuation = {**found, "protected": [], "baseline_red": {}, "unchecked": [], "baseline": "not taken"}
            if setting_on("det_stage_controls"):
                from agent.stage_controls import record_baseline
                log(f"[continuation] recorded {record_baseline(self.project_dir)} link/button label(s) of the earlier stage")
            earlier_now = list(self.continuation.get("earlier") or [])
            log(f"[continuation] the output folder holds an application an earlier run delivered: {len(earlier_now)} spec "
                f"file(s) {'of it' if evo else 'of other requirements'} ({', '.join(Path(s).name for s in earlier_now[:8])}"
                f"{' ...' if len(earlier_now) > 8 else ''}), {self.continuation.get('beyond')} source file(s) beyond the template; it is "
                "extended, never reset (template files are only added where missing)")
        elif (found or evo) and self.continuation:
            log(f"[continuation] resumed: {len(self.continuation.get('protected') or [])} protected earlier-stage spec(s) "
                "from this stage's checkpoint (the baseline is not taken again)")

        if self.inherited_app.get("preserve") and not found and (not done_ids or evo):
            copy_template(AGENT_ROOT / "template", self.project_dir, overwrite=False, only=infra_template_file)
        elif done_ids or found:
            copy_template(AGENT_ROOT / "template", self.project_dir, overwrite=False)
        else:
            copy_template(AGENT_ROOT / "template", self.project_dir, overwrite=not done_ids and not found)
        if done_ids and self.resumed_sources:
            restored = self.restore_sources(self.resumed_sources)
            log(f"[resume] {restored} application file(s) put back to the state the batches done left")
        if done_ids:

            self.restore_tests()
            ensure_helper(self.project_dir)
        self.asset_note = copy_requirement_assets(self.requirements_dir, self.project_dir)
        self.trace.start(tree)
        self.trace.git_init()
        if getattr(self, "asset_note", ""):
            tree["description"] = (str(tree.get("description") or "") + "\n\n" + self.asset_note).strip()

        try:
            images = attach_visual_notes(tree, self.requirements_dir, self.project_dir, self.llm, log=log)
            log(f"[visual] {images} reference image(s) described")
        except Exception as exc:
            log(f"[visual] failed: {exc!r}")

        if self.continuation and not done_ids:
            try:
                earlier = list(self.continuation.get("earlier") or [])
                app = stage_continuation.existing_app(self.project_dir, earlier)
                leads = self.evolution_leads(len(earlier)) if evo else {}
                self.continuation["contract_note"] = (stage_continuation.contract_note(app, earlier, limit=9000, lead=leads["contract"])
                                                      if leads else stage_continuation.contract_note(app, earlier))
                self.continuation["architect_block"] = (stage_continuation.architect_block(app, earlier, lead=leads["architect"])
                                                        if leads else stage_continuation.architect_block(app, earlier))
                self.continuation["outsider_note"] = (stage_continuation.outsider_note(self.project_dir, app, earlier, lead=leads["outsider"])
                                                      if leads else stage_continuation.outsider_note(self.project_dir, app, earlier))
                log(f"[continuation] existing application: {len(app['routes'])} frontend route(s), {len(app['api'])} API "
                    f"endpoint(s), {len(app['tables'])} table(s), {len(app['seeds'])} seed literal(s), {len(app['names'])} "
                    "accessible name(s) of the earlier specs -- to the architect, the contract notes and the outsider")
            except Exception as exc:
                log(f"[continuation] reading the existing application failed: {exc!r}")

            try:

                handoff = (self.__dict__.get("_earlier_handoff") if evo else
                           stage_handoff.load_handoff(self.project_dir, self.requirement_sha, log=log) if setting_on("stage_handoff") else None)
                if handoff:
                    self.continuation["architect_block"] = (str(self.continuation.get("architect_block") or "") + "\n\n"
                                                            + stage_handoff.architect_text(handoff)).strip()
                    notes = stage_handoff.notes_text(handoff)
                    if notes:
                        self.continuation["contract_note"] = (str(self.continuation.get("contract_note") or stage_continuation.CONTRACT_HEAD)
                                                              + "\n" + notes)
                    self.continuation["handoff_debt"] = stage_handoff.debt_leaves(handoff)
                    self.continuation["handoff_conventions"] = list(dict.fromkeys(
                        [str(c) for c in handoff.get("conventions") or []]
                        + [str(c) for c in ((handoff.get("earlier") or {}).get("conventions") or [])]))[:24]
                    self.continuation["handoff"] = {"requirement_sha256": str(handoff.get("requirement_sha256") or ""),
                                                    "leaves": len(handoff.get("leaves") or []),
                                                    "debt": len(self.continuation["handoff_debt"])}
                    log(f"[handoff] the earlier stage's handoff read: {len(handoff.get('leaves') or [])} leaves, "
                        f"{len(self.continuation['handoff_debt'])} not green, {len(handoff.get('conventions') or [])} convention(s) "
                        "-- to the architect and the contract notes")
            except Exception as exc:
                log(f"[handoff] the earlier stage's handoff not used: {exc!r}")
        if done_ids and self.contract:
            log(f"[architect] contract of {len(self.contract)} chars restored from the checkpoint")
        else:
            existing = str((self.continuation or {}).get("architect_block") or "")
            try:

                self.contract = (build_contract(self.llm, tree, self.project_dir, log=log, existing=existing) if existing
                                 else build_contract(self.llm, tree, self.project_dir, log=log))
            except Exception as exc:
                log(f"[architect] contract failed: {exc!r}")
                self.contract = ""
        note = str((self.continuation or {}).get("contract_note") or "")
        if note and self.contract:

            self.contract = contract_with_seed_constraints(self.contract, note, head=stage_continuation.CONTRACT_HEAD)
        if evo and self.contract and setting_on("evolution_data_lifecycle"):

            self.contract = contract_with_seed_constraints(self.contract, evolution.data_lifecycle_note(), head="DATA LIFECYCLE")

        from agent.verify import install_with_retries
        for package in ("frontend", "backend"):
            if not install_with_retries(self.project_dir, package, log=log).ok:
                self.install_failed = True

        if self.continuation and not done_ids:
            try:
                self.continuation_baseline()
            except Exception as exc:

                cont = self.continuation if isinstance(self.continuation, dict) else {}
                cont.update(protected=[], baseline_red={}, unchecked=list(cont.get("earlier") or []),
                            baseline=f"crashed: {type(exc).__name__}")
                log(f"[continuation] baseline crashed: {exc!r}; no earlier-stage spec is protected yet -- "
                    f"{len(cont.get('earlier') or [])} spec file(s) not judged (not counted as no regression)")
            self.evolution_after_baseline("the start")
        if evo and not done_ids:

            self.evolution_startup_health(tree)

        folders = folder_ids(tree)
        self.folders = folders

        leaves = atomic_nodes(tree)
        attach_ancestor_context(tree)

        if evo:
            self.evolution_attach(tree, whole_text=(self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace"))
            work = build_batches(evolution.implementation_tree(tree, list(evo.get("implement") or [])))
            self.evolution_batches(work, tree)
            self.evolution_health_attach(work, tree)
        else:
            work = build_batches(tree)
        attach_shared_rules(work, shared_module_rules(tree))

        self.batch_members = {str(batch.get("id")): [str(m) for m in (batch.get("members") or [batch.get("id")])] for batch in work}
        debts = dict((self.continuation or {}).get("handoff_debt") or {})
        if debts:
            for batch in work:
                note = stage_handoff.debt_note(tree, self.batch_members[str(batch.get("id"))], debts)
                if note:
                    batch["handoff_debt_note"] = note
            noted = [str(b.get("id")) for b in work if b.get("handoff_debt_note")]
            log(f"[handoff] {len(noted)} batch(es) depend on earlier-stage leaves that did not end green: {noted}")

        self.index_leaves([leaf for leaf, _ in leaves])
        hub_batches = [(str(batch.get("id")), self.hub_leaf(batch)) for batch in work]
        hub_batches = [(batch_id, hub) for batch_id, hub in hub_batches if hub is not None]
        log(f"[factory] hub batches (a leaf with >= {hub_min_dependents()} transitive dependents): "
            + (", ".join(f"{batch_id} ({hub[0]}: {hub[1]})" for batch_id, hub in hub_batches) or "none"))

        digest = requirement_digest(tree)
        self.native_validation = bool(NATIVE_VALIDATION.search(digest))

        seeds = seed_digest(tree)
        try:
            differences = seed_content_differences(seeds, tree)
        except Exception as exc:
            log(f"[factory] seed-difference note skipped: {type(exc).__name__}: {exc}")
            differences = ""
        if differences:
            seeds = (seeds + "\n\n" + differences).strip()

            self.contract = contract_with_seed_constraints(self.contract, differences,
                                                           head="SEED RECORDS WITH DIFFERENT CONTENTS")
            log(f"[factory] seed records with different contents across requirements: {differences.count('is given different contents')} (also in the contract notes)")
        try:
            constraints = seed_constraints_note(tree, log=log)
        except Exception as exc:
            log(f"[factory] seed-constraint note skipped: {type(exc).__name__}: {exc}")
            constraints = ""
        if constraints:
            seeds = (seeds + "\n\n" + constraints).strip()
            self.contract = contract_with_seed_constraints(self.contract, constraints)
            log(f"[factory] seed constraints outside the seed sentences: {constraints.count(chr(10) + '  * ')} line(s), also in the contract notes")

        existing_note = str((self.continuation or {}).get("outsider_note") or "")
        if existing_note:
            seeds = (seeds + "\n\n" + existing_note).strip()

        whole = (self.requirements_dir / "requirements.yaml").read_text(encoding="utf-8", errors="replace")
        evo_inventory = self.seed_foundation_inventory(tree) if evo else None
        for batch in work:
            batch["reference_requirements"] = digest
            batch["requirement_text"] = whole
            batch["seed_sentences"] = seeds
            if evo_inventory:

                block = seed_foundation.batch_block(evo_inventory, [str(m) for m in (batch.get("members") or [batch.get("id")])])
                if block:
                    batch["seed_sentences"] = (seeds + "\n\n" + block).strip()

        stamp_batch_names(work)
        if evo:
            self.stamp_inherited_names(work, tree)
            self.evolution_debt_attach(work, tree)
        self.large_tree = len(work) > LARGE_TREE_BATCHES
        if self.large_tree and "AGENT_TIME_BUDGET_SEC" not in os.environ:

            per_batch = (setting_seconds("continuation_per_batch_sec", LARGE_TREE_PER_BATCH_SEC)
                         if self.continuation and getattr(self, "stage_number", None) else LARGE_TREE_PER_BATCH_SEC)

            extra = self.evolution_extra_budget(work) if evo else 0.0
            self.time_budget = float(min(LARGE_TREE_CAP_SEC - final_judgement_reserve(len(work)), LARGE_TREE_BASE_SEC + per_batch * len(work) + extra))
            if extra:
                log(f"[factory] evolution: +{int(extra)} s for the start-up health check and its entry-debt slot(s)")
            if self.continuation:
                log(f"[factory] continuation: {int(per_batch)} s per batch (continuation_per_batch_sec)")
            if getattr(self, "stage_target", None):

                self.time_budget = self.staged_batch_budget(self.time_budget, len(work), bool(evo))
                log(f"[factory] staged task: {int(self.time_budget)} s for {len(work)} batch(es) "
                    f"(~{int(self.time_budget / max(1, len(work)))} s each)")

        reserved = self.large_tree and "AGENT_TIME_BUDGET_SEC" not in os.environ
        log(f"[factory] {len(leaves)} leaves -> {len(work)} batch(es); large_tree={self.large_tree}; time budget {int(self.time_budget)}s for the batches; "
            + (f"{int(final_judgement_reserve(len(work)))}s of the {LARGE_TREE_CAP_SEC}s cap kept for the closing verification" if reserved else "the closing verification runs on top of it"))
        if not self.large_tree and (self.protected_specs() or self.evolution_guards()):

            self.large_tree = True
            log(f"[continuation] {len(work)} batch(es) on top of {len(self.protected_specs())} protected earlier-stage spec(s): "
                "the batches run with the large-tree guards; the time budget stays the small tree's")
        nodes = [(node, []) for node in work]
        log(f"[factory] {len(nodes)} work item(s): {[n.get('id') for n, _ in nodes]}; model={self.llm.model}")
        log(f"[hard-end] {self.hard_plan_line(len(work))}")
        all_passed = self.run_queue(nodes, done_ids, resume_digest, str(tree.get("description") or ""))

        try:
            from agent.seed_identity import log_seed_identity

            if self.seed_identity_due():
                log_seed_identity(whole, self.project_dir, log=log, time_limit=60.0)
        except Exception as exc:
            log(f"[seed-identity] not run: {exc!r}")

        try:
            self.repair_debt_specs()
        except Exception as exc:
            log(f"[verify] closing debt repair crashed: {exc!r}")
        in_flight = self.begin_restore_point("description pass")
        try:
            from agent.description_pass import run_description_pass

            run_description_pass(self, tree, work, whole, done_ids, resume_digest, str(tree.get("description") or ""), log=log)
        except Exception as exc:
            log(f"[desc-pass] crashed: {exc!r}")
        self.evolution_restore_inherited_specs("the description pass")
        watchdog.end(in_flight)
        self.restore_point = None

        try:
            self.evolution_lifecycle_closing()
        except Exception as exc:
            log(f"[evolution] closing data-lifecycle check crashed: {exc!r}")
        try:
            self.verify()
        except Exception as exc:
            log(f"[verify] crashed: {exc!r}")
        self.evolution_single_source_closing()

        gates = self.report["verification"]
        gates_ok = bool(gates.get("frontend_build")) and bool(gates.get("server_probe")) and gates.get("e2e") in (True, "skipped") and not self.install_failed
        all_passed = gates_ok and not self.failed_members and not self.unattempted_members
        for folder in folders:
            self.trace.node_state(folder, "CONVERGED" if all_passed else "CONVERGED_WITH_FAILED_CHILDREN", "test")
        self.clean_runtime_state()

        (self.project_dir / ".arc" / "resume.json").unlink(missing_ok=True)

        usage = self.llm.summary()
        self.report["usage"] = usage
        self.report["elapsed_sec"] = round(self.spent_before + (clock.now() - self.started))
        self.report["paused_sec"] = round(clock.paused_sec())
        if self.hard_end() is not None:
            self.report["hard_end"] = {"left_sec": round(self.hard_left()), "cuts": list(self.__dict__.get("hard_end_cuts") or [])}
        try:
            write_text(self.project_dir / ".arc" / "agent-summary.json", json.dumps(self.report, indent=2, ensure_ascii=False))
        except OSError:
            pass
        log(f"[factory] done in {self.report['elapsed_sec']}s; usage={usage}; verification={self.report['verification']}")
        log(f"[factory] shared-database sweep: {self.shared_sweep_line()}")
        self.trace.finish(all_passed, f"nodes passed={all_passed}; usage={usage}")
        return 0

def main() -> int:

    try:
        watchdog.arm_early(started_mono=MONO_STARTED, started_wall=PROCESS_STARTED)
    except Exception:
        pass
    args = parse_args()
    requirements_dir = Path(args.requirement_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not requirements_dir.is_dir():
        raise FileNotFoundError(f"requirement directory not found: {requirements_dir}")

    try:
        early_target = staged_run_target(requirements_dir)
        dog = getattr(watchdog, "_ACTIVE", None)
        if early_target and dog is not None:
            dog.move_end(PROCESS_STARTED + early_target + setting_seconds("stage_watchdog_slack_sec", 2700.0))

        contest_end, _note = contest_deadline_end()
        if contest_end is not None and dog is not None:
            dog.move_end(contest_end + max(0.0, setting_seconds("contest_deadline_margin_sec", 3600.0)) / 2.0)
    except Exception:
        pass
    log(f"[env] MODEL={os.environ.get('MODEL', '')} BASE_URL={os.environ.get('OPENAI_BASE_URL', '')} KEY={'set' if os.environ.get('OPENAI_API_KEY') else 'MISSING'}")
    log(f"[env] requirements={requirements_dir} output={output_dir} type={args.task_type}")

    try:
        from agent.inert_controls import install_parser_in_background

        install_parser_in_background(log=log)
    except Exception as exc:
        log(f"[deps] TSX parser install not started: {exc!r}")
    try:
        return Factory(requirements_dir, output_dir).run()
    finally:
        watchdog.shutdown(log=log)

if __name__ == "__main__":
    raise SystemExit(main())
