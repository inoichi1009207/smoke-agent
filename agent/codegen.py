from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .conventions import PROJECT_CONVENTIONS, evolution_conventions, v166_conventions
from .edits import (EDIT_MIN_CHARS, GOD_FILE_LINES, Hunk, HunkResult, apply_hunks, clip, parse_answer, pending_text, size_note,
                    TRAILING_MARKER_CODE_SUFFIXES, strip_trailing_markers)
from .fsutil import IGNORED_DIRS, PathError, safe_path, write_text
from .llm import LLM, CallDeadlineReached, QuotaExhausted
from .settings import SETTINGS, setting_on
from .test_author import describe_node, split_file_blocks
from . import clock
from .testrun import TestOutcome, lint_only_failure
from .verify import FAILED_FILES_HEAD, NO_SPEC_RAN, settle_install_failure

FILE_BLOCK = re.compile(r"=== FILE: (?P<path>[^\n=]+?) ===\n(?P<body>.*?)\n=== END FILE ===", re.DOTALL)
DEFECT_BLOCK = re.compile(r"=== TEST DEFECT ===\n(?P<body>.*?)\n=== END TEST DEFECT ===", re.DOTALL)
NEED_BLOCK = re.compile(r"=== NEED FILES ===\n(?P<body>.*?)(?:\n=== END NEED FILES ===|\Z)", re.DOTALL)
FILE_REQUEST_WORDS = re.compile(r"\b(?:show|shown|provide|send|see the (?:full|whole|complete)|need (?:to see|the))\b|看不到|没(?:有)?(?:给|展示)|未展示|请(?:给|提供|展示)", re.I)

MAX_FILE_CHARS = 30000
MAX_CONTEXT_CHARS = 90000
FORCED_CHARS = 120000

PROSE_LIMIT_CHARS = 30000
ANALYSIS_NOTE_CHARS = 6000

FREE_CUTOFFS = 3

CUT_FREE = 2

NEED_UNSHOWN_FREE = 1

ATTEMPT_NOTE_CHARS = 4000
EXTRA_ROUNDS = 3
BATCH_MAX_ROUNDS = 4

ROUND_BONUS_BIG_HUB = 15
ROUND_BONUS_TESTS = 7

PROGRESS_ROUNDS_MAX = 3

START_FAILURE_FREE = 2
APP_NOT_STARTED = NO_SPEC_RAN[0]
EARLIER_FILES_FAILED = "Spec files that failed before this"

def app_did_not_start(output: str) -> bool:
    own = blocking_output(output or "")
    return (APP_NOT_STARTED in own and FAILED_FILES_HEAD not in own and EARLIER_FILES_FAILED not in own
            and not any(mark in own for mark in OWN_SPECS_PASSED))
ROUND_CAPS = {0: (BATCH_MAX_ROUNDS, EXTRA_ROUNDS), 1: (BATCH_MAX_ROUNDS + 1, EXTRA_ROUNDS), 2: (BATCH_MAX_ROUNDS + 2, EXTRA_ROUNDS + 1)}
SPEC_TEST_HEAD = re.compile(r"(?m)^[ \t]*test(?:\.(?:only|skip|fixme|fail|slow))?\(\s*[\"'`]")

def count_spec_tests(test_files: dict[str, str]) -> int:
    return sum(len(SPEC_TEST_HEAD.findall(str(text or ""))) for path, text in test_files.items() if path.startswith("backend/test-e2e/"))

def round_caps(dependents: int, tests: int, hub_min: int = 5) -> tuple[int, int, int]:
    points = (1 if dependents >= hub_min else 0) + (1 if dependents >= max(hub_min, ROUND_BONUS_BIG_HUB) else 0) \
        + (1 if tests >= ROUND_BONUS_TESTS else 0)
    points = min(2, points)
    max_rounds, extra_rounds = ROUND_CAPS[points]
    return max_rounds, extra_rounds, points

ANSWER_MARKERS = ("=== FILE:", "=== EDIT:", "=== TEST DEFECT", "=== NEED FILES")

CONTINUE_FREE = 3
EDIT_RETRY_FREE = 3
FOLLOW_UP_MIN_SEC = 300
BACKGROUND_CHARS = 15000
IMPORT_SPEC = re.compile(r"""(?:from\s+|import\s+|require\(\s*)['"](\.{1,2}/[^'"]+)['"]""")
CORE_FILES = (
    "frontend/src/App.tsx",
    "frontend/src/api/index.ts",
    "frontend/src/api/store.ts",
    "frontend/src/main.tsx",
    "backend/src/app.js",
    "backend/src/database/init_db.js",

    "backend/src/database/db_runtime.js",
    "backend/src/database/index.js",
)
READ_ONLY_FILES = ("backend/src/database/db_runtime.js", "backend/src/database/index.js", "frontend/src/api/store.ts", "frontend/src/main.tsx")
TEMPLATE_PLUMBING = ("backend/src/database/test_harness.js", "backend/src/database/prepare_e2e.js", "backend/src/database/seed_db.js")

BUILD_CONTRACT = (
    "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.js", "frontend/eslint.config.js",
    "backend/package.json", "backend/package-lock.json", "backend/playwright.config.js", "backend/vitest.config.js",
)

SYSTEM_PROMPT_WHOLE_FILES = f"""You are the IMPLEMENTER agent of a test-driven software factory.
Executable tests for one requirement exist and currently fail. You output complete source files that make them pass
against the REAL application (the Playwright spec runs against the built app served by the backend).

{PROJECT_CONVENTIONS}
Rules:
- Output every file you create or change IN FULL, using exactly this format and nothing else (no prose, no fences):
=== FILE: <project-relative path> ===
<complete file content>
=== END FILE ===
- Never output test files. If a test is genuinely defective (see TEST DEFECT below), output instead:
=== TEST DEFECT ===
<the spec step, in double quotes exactly as the spec writes it, and the requirement sentence or error it contradicts, in double quotes>
=== END TEST DEFECT ===
- To see a file whose content was not shown, do not guess it and do not call it a test defect; output instead:
=== NEED FILES ===
<one project-relative path per line>
=== END NEED FILES ===
- backend/src/database/db_runtime.js, backend/src/database/index.js, frontend/src/api/store.ts and frontend/src/main.tsx are READ-ONLY:
  anything you output for them is discarded. Build on their API as shown.
- Keep the project structure and tooling; do not add dependencies. Package manifests and build/test configs (Vite,
  ESLint, Playwright, Vitest) are FIXED: anything you output for an existing one is discarded, even when a failure
  seems to point there -- fix the failure in the application files that use them.
- Use the real modules (axios client, Express routes, SQLite helpers); never adapt code to mocks or test doubles.
- Implement every explicit statement of the requirement, not only what the visible tests assert; do not add features
  the requirement does not state. Output each file path at most once.
- Keep code simple; minimal styling; forms use <label htmlFor> + id so labels are accessible names.
- Small files: one component per file, a page file only composes components (aim for under 150 lines). A feature of
  its own (editor, picker, menu, dialog, list item) gets its own file under frontend/src/components -- its own FILE, not
  its own dialog: a panel the requirement places inside a named dialog is rendered inside that dialog; backend routes
  are split per resource under backend/src/routes. Every repair re-sends whole files, so a 600-line page costs a
  fortune to fix and gets cut off.
- Earlier requirements are already implemented in the project files you see. NEVER remove or rename fields, labels,
  links, buttons or routes they need; add to the page instead. Earlier functionality has only partial regression
  coverage in the check you see, so preserve existing interfaces even when nothing exercises them right now.
- The Playwright spec (backend/test-e2e/*.spec.js) is written by an OUTSIDER who has read only the requirement and
  finds everything by the requirement's own words: by the role and name the requirement STATES ("the button named
  X": only a button counts), or by the word alone -- any clickable element, any labelled field -- where it states no
  role. When it reports that it cannot find a name, cannot type into a field, or cannot see a text, the APPLICATION
  is wrong: rename / relabel the control to the requirement's wording, give it the stated role, make the field
  editable, show the text. Its report lists what is on the page. That is never a test defect -- unless the missing
  text is a value the test itself typed that breaks a format the requirement states (see below).
- A TEST DEFECT is exactly one of: (a) a test that cannot run as written (a syntax error, a function it never
  imported); (b) a step that contradicts the requirement text itself; (c) a locator that finds several elements where
  the requirement itself names several (where it names one, the duplicate is the application's to remove). Output a
  TEST DEFECT block for those, never bend the application to them. A value the test itself types that breaks a format
  the requirement states for that field (its characters, case, length, first or last character) is kind (b) ONLY when
  the test then treats it as ACCEPTED -- it expects the record to be created, opens it or looks it up by that value:
  quote the step and the requirement's format sentence. A test that types an invalid value and expects it to be
  refused (a field error, a stated message, no record created) is correct: when it fails, the application's check or
  message is what is missing.
- A repair never weakens what the requirement states to get a check green: never loosen a validation, a format or the
  condition under which a stated message appears, and never turn a refusal into a navigation to another existing record
  so the attempt looks successful. A refused create or submit stays on the same page and shows the server's reason
  for refusing; a successful one ends where the requirement says it goes (a create it says opens the new record ends
  on that record's page) and otherwise shows the values just entered.
- Do not add loading gates that hide navigation or forms while a session request is pending: render links and forms
  immediately. Server data (lists, details, the signed-in user) comes from the store (see the conventions), so a page
  shows it in the same render as the navigation.
- In a repair round, output only the files that must change (in full).
"""

_WHOLE_FORMAT = """- Output every file you create or change IN FULL, using exactly this format and nothing else (no prose, no fences):
=== FILE: <project-relative path> ===
<complete file content>
=== END FILE ===
"""
_EDIT_FORMAT = f"""- Output every NEW file, and every existing file of at most {EDIT_MIN_CHARS} characters that you change, IN FULL, using
exactly this format and nothing else (no prose, no fences):
=== FILE: <project-relative path> ===
<complete file content>
=== END FILE ===
- Change an existing file of more than {EDIT_MIN_CHARS} characters (its header in the listing says "change it with EDIT
  blocks") with ONE EDIT block that holds only the lines that change -- never re-send such a file whole:
=== EDIT: <project-relative path> ===
<<<<<<< SEARCH
<lines copied EXACTLY from the current file: enough consecutive lines (3 or more) to match ONE place only>
=======
<the lines that replace them>
>>>>>>> REPLACE
=== END EDIT ===
  Several SEARCH/REPLACE pairs in one block go top to bottom and never overlap. To insert, quote the neighbouring
  lines in SEARCH and repeat them in REPLACE next to the new ones; to delete, leave REPLACE empty. Never abbreviate
  SEARCH ("...", "// rest unchanged"), and write plain lines -- not a unified diff (no `+` / `-` in front) and no line
  numbers: a pair whose SEARCH lines are not found exactly once is NOT applied, and you are shown the current lines.
  A file with a line that is exactly `=======` or starts with `<<<<<<<` / `>>>>>>>` is written whole (FILE block).
- One answer holds at most 12000-16000 tokens of output (about 45000 characters), your thinking included. Put the most important change FIRST (the
  file the first failure points at); output only the files this change needs; when a large file must change, its EDIT
  block comes before everything else. An answer cut off by that limit loses its last, unfinished block.
- A file of more than {GOD_FILE_LINES} lines (its header says so) is not made longer: first move a self-contained part of
  it (a component, a hook, a route group, a helper module) into a new file -- a FILE block for the new file plus an EDIT
  block that removes that part from the big file and imports it -- then add the new code to the smaller file.
"""
_WHOLE_SMALL = "Every repair re-sends whole files, so a 600-line page costs a" + chr(10) + "  fortune to fix and gets cut off."
_EDIT_SMALL = ("A big file is in every prompt and every whole rewrite of it risks the output limit, so a 600-line page" + chr(10)
               + "  costs a fortune to fix.")
_WHOLE_REPAIR = "- In a repair round, output only the files that must change (in full)." + chr(10)
_EDIT_REPAIR = ("- In a repair round, output only what must change: EDIT blocks for large existing files, FILE blocks (in full) "
                "for new and small ones." + chr(10))
for _old in (_WHOLE_FORMAT, _WHOLE_SMALL, _WHOLE_REPAIR):
    if SYSTEM_PROMPT_WHOLE_FILES.count(_old) != 1:
        raise RuntimeError(f"codegen: the whole-file prompt no longer holds {_old[:60]!r}")
SYSTEM_PROMPT = (SYSTEM_PROMPT_WHOLE_FILES.replace(_WHOLE_FORMAT, _EDIT_FORMAT).replace(_WHOLE_SMALL, _EDIT_SMALL)
                 .replace(_WHOLE_REPAIR, _EDIT_REPAIR))

def edit_blocks_on() -> bool:
    env = os.environ.get("AGENT_EDIT_BLOCKS")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return setting_on("edit_blocks", True)

def system_prompt() -> str:
    base = SYSTEM_PROMPT if edit_blocks_on() else SYSTEM_PROMPT_WHOLE_FILES

    extra = evolution_conventions() + v166_conventions()
    if extra and base.count(PROJECT_CONVENTIONS) == 1:
        return base.replace(PROJECT_CONVENTIONS, PROJECT_CONVENTIONS + extra, 1)
    return base

EFFECTIVE_RULES_HEAD = ("This batch CHANGES behaviour the inherited application already has. The currently effective rules "
                        "below are binding for it: they decide over the inherited code and over the context above.\n")
_RULES_BLOCK_START = re.compile(r"\n\n(?=CURRENTLY EFFECTIVE RULES of )")

def strip_effective_rules(context: str, rules: str) -> str:
    out = str(context or "")
    for block in _RULES_BLOCK_START.split(str(rules or "")):
        block = block.strip()
        if block and block in out:
            out = out.replace(block, "")
    return re.sub(r"\n{3,}", "\n\n", out).strip()

def describe_for_implementer(node: dict[str, Any]) -> str:
    rules = str(node.get("effective_rules") or "").strip()
    health = str(node.get("evolution_health") or "").strip()
    if not rules and not health:
        return describe_node(node)
    view = dict(node)
    if rules:
        view["ancestor_context"] = strip_effective_rules(str(node.get("ancestor_context") or ""), rules)
    parts = [describe_node(view)]
    if rules:
        parts.append(EFFECTIVE_RULES_HEAD + rules)
    if health:
        parts.append(health)
    return "\n\n".join(parts)

USER_TEXT = {
    "contract": "Interface contract (fixed by the architect; routes, endpoints, file names and accessible names are binding; the KIND of an element -- link, button, heading -- is not: it follows the requirement text and the conventions, whatever the contract calls it; a control the existing application already renders keeps its current kind unless the requirement names another):\n",
    "tests": "Tests to satisfy (read-only):\n",
    "hidden": "\n\n(not shown, currently passing: {names})",
    "sources": "Current project source files (untouched in this batch):\n",
    "working": "Project files written or requested in this batch (current content):\n",
    "check_repair": "Latest check result:\n",

    "prefix_added": ("More project files for this answer (current content; where a file above was marked as not shown, "
                     "it is shown here in full and that notice no longer applies):\n"),
    "check_first": "Initial check result (tests are red, nothing implemented yet):\n",
    "ask_repair": "Repair round: output ONLY the files that must change, each in full. Do not explain; start directly with `=== FILE:`.",
    "ask_first": "Output all files needed to implement this requirement, each in full.",
    "ask_diagnose": (
        "DIAGNOSIS ROUND: do NOT output any file. Work out why the check above fails and answer with a FIX PLAN only: at most "
        "12 lines, one per change, in the form `<file path> -- <what exactly to change, and why that fixes which failure>`. "
        "Use the server's own error output when there is one. When the failing step of the acceptance spec contradicts the "
        "requirement text itself (a value the spec types that breaks a format the requirement states for that field counts "
        "when the spec then expects it to be ACCEPTED -- never loosen the application's check to accept it; a spec that "
        "expects such a value to be REFUSED is correct), or the spec is broken in itself (it calls a function its helper does not have, a syntax or reference "
        "error inside the spec file), say so in one line starting with `SPEC:`. No code, no alternatives, no preamble."
    ),
    "plan_failed": "\nYour previous fix plan was carried out and the failures did not change, so its diagnosis was wrong or incomplete:\n{plan}\nLook for a different cause.",
    "plan": "\nFix plan (your own diagnosis of this failure; do not reopen it):\n{plan}\nImplement it now.",
    "spec_unproven": ("\nThe diagnosis blamed the acceptance spec without two verifiable quotes (the spec's step as written, and the "
                      "requirement sentence or error it contradicts), so it does not count. The application is what has to change: "
                      "find what in it makes the check above fail, and fix that."),
    "defect_unproven": ("\nYour TEST DEFECT was not accepted: it must quote, in double quotes, the spec's own step exactly as the spec file "
                        "writes it AND the requirement sentence (or the error message) it contradicts. Without both, the application is "
                        "what has to change: fix it now."),
    "refused_part": "\nPart of your previous answer was NOT written: {files} (fixed files, or files you were not shown). The rest is on disk; the check above ran without those changes.",
    "plan_fixed": "\nThe plan names {files}: these are fixed (acceptance tests, starter plumbing, package manifests, build configuration) and cannot be changed. Carry the plan out in the application files that use them instead.",
    "stagnation": (
        "\nTwo repair rounds have left exactly these failures unchanged:\n{failures}\nEither the acceptance spec is at fault -- then "
        "prove it: answer with one `SPEC:` line that QUOTES the requirement sentence and the spec step that contradict each other "
        "(or the spec step and the error the spec raises by itself) -- or it is the application: then give a fix plan that takes "
        "a DIFFERENT direction from the earlier ones. An error of the server, a missing field, name or element is the application."
    ),

    "attempt": "A previous attempt at this batch ended red; what it did and how it failed is below. Do not repeat what failed:\n",

    "seed_conflict": (
        "\nSHARED DATA: {files} is shared with earlier requirements. Changing it turned this batch's spec green while specs of "
        "earlier requirements turned red, and changing it back turned this batch's spec red again ({flips} such changes so "
        "far). Do not change {files} again. If your spec cannot pass without changing it, answer with a TEST DEFECT block "
        "instead of files: quote, in double quotes, the assertion of your spec exactly as the spec writes it AND the "
        "assertion of the earlier requirement's spec (as the check output shows it) that it contradicts."
    ),

    "seed_hint": (
        "\nSHARED DATA: {files} is shared with earlier requirements, whose specs are frozen. {flips} rounds that changed it "
        "turned this batch's spec green with specs of earlier requirements red, or the other way round. Keep what the "
        "earlier specs rely on when you change it: add what this batch needs next to it instead of replacing it."
    ),

    "seed_lock": (
        "\nSEED LOCKED: your change to {files} removed values that the requirement's seed sentences state ({values}); that "
        "answer changed nothing else, so it was reverted and not checked (the check above is still the one before it). "
        "Stated seed values are never replaced or removed. Keep every stated seed value; you may change the schema or add "
        "rows. Data this requirement needs goes into NEW records or rows next to the existing ones, or the spec creates its "
        "own record through the interface."
    ),

    "seed_lock_part": (
        "\nSEED LOCKED: your change to {files} removed values that the requirement's seed sentences state ({values}); "
        "{files} was put back as it was before that answer. The rest of that answer ({kept}) was kept and checked. Stated "
        "seed values are never replaced or removed. Keep every stated seed value; you may change the schema or add rows. "
        "Data this requirement needs goes into NEW records or rows next to the existing ones, or the spec creates its own "
        "record through the interface."
    ),
    "seed_lock_soft": (
        "\nSEED LOCK: your change to {files} removed values that the requirement's seed sentences state ({values}). Put "
        "every stated value back in this answer; you may change the schema or add rows. Stated seed values are never "
        "replaced or removed: data this requirement needs goes into NEW records or rows next to the existing ones, or the "
        "spec creates its own record through the interface."
    ),
    "spec_challenge": (
        "\nYou answered that the acceptance spec is wrong:\n{claim}\nThat spec was written from the requirement text alone, and a "
        "wrong verdict here gets a correct test weakened. Check again. The spec is wrong ONLY if you can quote (1) the exact "
        "sentence of the requirement and (2) the exact step of the spec that contradict each other -- or (2) alone plus the error "
        "the spec raises BY ITSELF -- then answer with one `SPEC:` line containing the quotes. A name, role, text or element the application does not have yet is NOT a spec "
        "defect: in that case answer with the fix plan for the application."
    ),

    "ask_repair_edits": ("Repair round: output ONLY what must change -- EDIT blocks for existing files over {limit} characters, FILE "
                         "blocks in full for new or small files -- the most important change first. Do not explain; start directly "
                         "with `=== EDIT:` or `=== FILE:`."),
    "ask_first_edits": ("Output what this requirement needs: new files in full (FILE blocks), changes to existing files over {limit} "
                        "characters as EDIT blocks (only the changed lines), the most important change first."),
    "edit_retry": ("Part of your answer could not be applied; everything else of it was applied:\n{report}\nAnswer ONLY with EDIT "
                   "blocks that redo exactly these changes -- one SEARCH/REPLACE pair per change, in the order listed --, each SEARCH copied line for line from the current lines shown above "
                   "(the numbers and `|` in front are not part of the file). Do not repeat what was applied. Start directly with "
                   "`=== EDIT:`."),
    "continue_cut": ("Your answer was cut off by the output limit{where}. Everything before that point was received and will be "
                     "applied{lost}. Continue with the REMAINING changes only: {first}every other file this change still needs. Change "
                     "existing files over {limit} characters with EDIT blocks (only the changed lines), new or small files with "
                     "FILE blocks, the most important first; do not repeat a block you already wrote. If nothing is left, answer "
                     "exactly `=== DONE ===`. Start directly with `===`."),
    "edit_note": "\nNOT applied from your previous answer (the rest of it is on disk and the check above ran without these):\n{report}\n",
    "edit_none": ("\nYour previous answer changed nothing on disk: none of its blocks could be applied.\n{report}\nAnswer again: "
                  "EDIT blocks whose SEARCH lines are copied exactly from the current file, FILE blocks for new or small files."),

    "cutoff_reask": ("Your previous answer analysed the failure but wrote no files, and it was stopped. Write the files now, in "
                     "the required format (FILE blocks, or EDIT blocks for existing files over {limit} characters). At most 10 "
                     "lines of analysis before the first block; better none: start directly with {start}."),
}

REASONING_TAIL_CHARS = 4000
REASONING_TAIL_HEAD = "(model's unfinished analysis, cut)\n"
HEADER_AT_LINE_START = re.compile(r"(?:^|\n)[ \t]*(?:" + "|".join(re.escape(m) for m in ("=== FILE:", "=== EDIT:", "=== TEST DEFECT", "=== NEED FILES")) + ")")

def reasoning_cap_chars() -> int | None:
    if not setting_on("reasoning_cap", True):
        return None
    try:
        value = int(float(SETTINGS.get("reasoning_cap_chars", 40000)))
    except (TypeError, ValueError):
        value = 40000
    return value if value > 0 else None

def cutoff_reask_chars() -> int:
    if not setting_on("cutoff_reask", True):
        return 0
    try:
        return max(500, int(float(SETTINGS.get("cutoff_reask_chars", 6000))))
    except (TypeError, ValueError):
        return 6000

@dataclass
class ImplementResult:
    status: str
    written: list[str] = field(default_factory=list)
    detail: str = ""
    test_runs: int = 0
    last_check: str = ""

    app_plan: str = ""
    routed: list = field(default_factory=list)

    stop_reason: str = ""

    defect_files: list = field(default_factory=list)
    defect_kind: str = ""

VOLATILE_BITS = (
    (re.compile(r"Outsider \d+"), "Outsider <n>"),
    (re.compile(r"\b\d{10,}\b"), "<n>"),

    (re.compile(r"<n>-[a-z0-9]{2,6}\b"), "<n>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE), "<uuid>"),

    (re.compile(r"\(\d+(?:\.\d+)? ?m?s\)|\b\d+(?:\.\d+)? ?m?s\b"), "<t>"),
    (re.compile(r"\b\d+ × "), "<k> × "),
    (re.compile(r"(?:127\.0\.0\.1|localhost):\d+"), "<host>"),

    (re.compile(r"(?:[A-Za-z]:[\\/]|/(?:tmp|home|var|workspace|mnt|Users|private|root)/)[^\s:'\"]*[\\/]"), "<dir>/"),
)
SIGNATURE_MARKS = ("×", "✘", "FAIL", "Error", "Locator:", "Timeout", "Expected", "Received")

FOUNDATION_MARK = "\n\nALSO BROKEN:"

def own_output(output: str) -> str:
    return output.split(FOUNDATION_MARK, 1)[0]

ADVISORY_MARK = "\n\nADVISORY "

def blocking_output(output: str) -> str:
    return own_output(output).split(ADVISORY_MARK, 1)[0]

SPEC_RUNTIME_ERROR = re.compile(
    r"^\s*(?:ReferenceError: [\w$]+ is not defined"
    r"|TypeError: (?:(?:page|expect\(\.\.\.\))\.)?[\w$]+ is not a function"
    r"|Error: \w+ can be only used with \w+ object)\s*$")
HELPER_SPEC_DEFECT = re.compile(r"^\s*Error: SPEC DEFECT: ")

SPEC_CHAINED_CALL_ERROR = re.compile(
    r"^\s*TypeError: (?:page|expect\(\.\.\.\))(?:\.[\w$]+(?:\(\.\.\.\))?)*?\.[\w$]+\(\.\.\.\)"
    r"(?:\.[\w$]+(?:\(\.\.\.\))?)*\.[\w$]+ is not a function\s*$")
CHAINED_CALL_SETTING = "spec_chained_call_defect"

def spec_runtime_error(line: str) -> bool:
    if SPEC_RUNTIME_ERROR.match(line):
        return True
    return bool(SPEC_CHAINED_CALL_ERROR.match(line)) and setting_on(CHAINED_CALL_SETTING, True)

STACK_FRAME = re.compile(r"^\s*at (?:.*?[\\/(\s])?(?P<file>[^\\/()\s:]+):\d+(?::\d+)?\)?\s*$")
FAILURE_HEADER = re.compile(r"^\s*\d+\) ")

NOT_A_FUNCTION = re.compile(r"([\w$]+) is not a function\s*$")
STRING_LITERAL = re.compile(r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`""")

def binds_name(spec_text: str, name: str) -> bool:
    from .detaudit import strip_js_comments

    code = STRING_LITERAL.sub('""', strip_js_comments(spec_text))
    word = re.escape(name)

    imported = (
        rf"\bimport\s*\{{[^}}]*(?<![\w$.]){word}(?![\w$])[^}}]*\}}\s*from\b",
        rf"\bimport\s+{word}\s*(?:,|\bfrom\b)",
        rf"\b(?:const|let|var)\s*\{{[^}}]*(?<![\w$.]){word}(?![\w$])[^}}]*\}}\s*=\s*require\s*\(",
        rf"\b(?:const|let|var)\s+{word}\s*=\s*require\s*\(",
    )
    if any(re.search(pattern, code) for pattern in imported):
        return False

    for source in re.findall(rf"\b(?:const|let|var)\s*\{{[^}}]*(?<![\w$.]){word}(?![\w$])[^}}]*\}}\s*=\s*([A-Za-z_$][\w$]*)\s*;?", code):
        if re.search(rf"\b(?:const|let|var)\s+{re.escape(source)}\s*=\s*require\s*\(|\bimport\s+(?:\*\s+as\s+)?{re.escape(source)}\s+from\b", code):
            return False
    spec_text = code
    patterns = (
        rf"\b(?:const|let|var)\s+{word}\b",
        rf"\b(?:const|let|var)\s*[{{\[][^;=]*?(?<![\w$.]){word}(?![\w$])[^;=]*?[}}\]]\s*=",
        rf"\bfunction\b[^(]*\(([^)]*?(?<![\w$.]){word}(?![\w$])[^)]*)\)",
        rf"\(([^()]*?(?<![\w$.]){word}(?![\w$])[^()]*)\)\s*=>",
        rf"(?<![\w$.]){word}\s*=>",
        rf"(?<![\w$.]){word}\s*=(?![=>])",
    )
    return any(re.search(pattern, spec_text) for pattern in patterns)

def page_from_application(spec_text: str) -> bool:
    from .detaudit import strip_js_comments

    code = STRING_LITERAL.sub('""', strip_js_comments(spec_text))
    return any(re.search(pattern, code) for pattern in (
        r"\bimport\s+page\s*(?:,|\bfrom\b)",
        r"\bimport\s*\{[^}]*(?<![\w$.])page(?![\w$])[^}]*\}\s*from\b",
        r"\b(?:const|let|var)\s*\{[^}]*(?<![\w$.])page(?![\w$])[^}]*\}\s*=\s*require\s*\(",
        r"\b(?:const|let|var)\s+page\s*=\s*(?:require\s*\(|new\b)",
    ))

def spec_thrown_errors(output: str, test_files: dict[str, str]) -> list[str]:
    own_specs = {Path(path).name: str(text or "") for path, text in test_files.items() if path.startswith("backend/test-e2e/")}
    lines = output.splitlines()
    found: list[str] = []
    for index, line in enumerate(lines):
        if HELPER_SPEC_DEFECT.match(line):

            for later in lines[index + 1:index + 40]:
                if FAILURE_HEADER.match(later):
                    break
                frame = STACK_FRAME.match(later)
                if frame and frame.group("file") in own_specs:
                    item = f"{frame.group('file')}: {line.strip()}"
                    if item not in found:
                        found.append(item)
                    break
            continue
        if not spec_runtime_error(line):
            continue
        for later in lines[index + 1:index + 40]:
            if FAILURE_HEADER.match(later):
                break
            frame = STACK_FRAME.match(later)
            if frame:
                if (frame.group("file") in own_specs and not SPEC_RUNTIME_ERROR.match(line)
                        and line.strip().startswith("TypeError: page.") and page_from_application(own_specs[frame.group("file")])):
                    break
                called = NOT_A_FUNCTION.search(line.strip())
                if called and frame.group("file") in own_specs and binds_name(own_specs[frame.group("file")], called.group(1)):
                    break
                if frame.group("file") in own_specs:
                    item = f"{frame.group('file')}: {line.strip()}"
                    if item not in found:
                        found.append(item)
                break
    return found

RAW_WAIT_TIMEOUT = r"(?:Test timeout of \d+\s*ms exceeded|Timeout \d+\s*ms exceeded)"
PAGE_WAIT_METHODS = (r"waitFor[\w$]*|click|dblclick|tap|fill|type|press|check|uncheck|setChecked|selectOption|hover|focus|"
                     r"textContent|innerText|innerHTML|inputValue|getAttribute|isChecked|isEnabled|isDisabled|isEditable|"
                     r"setInputFiles|dragAndDrop|dispatchEvent")
RAW_WAIT_ERROR = re.compile(
    r"^\s*(?:Timeout)?Error: (?:(?:locator|frameLocator|elementHandle)\.[\w$]+|(?:page|frame)\.(?:" + PAGE_WAIT_METHODS + r"))"
    r": " + RAW_WAIT_TIMEOUT)
EXPECT_WAIT_ERROR = re.compile(r"^\s*Error: (?:Timed out \d+\s*ms waiting for )?expect\((?:locator|page)\)\.(?:not\.)?[\w$]+\(")
NAMES_EXPECTATION = re.compile(r"^\s*(?:Expected|Received)\b")
BARE_TEST_TIMEOUT = re.compile(r"^\s*Test timeout of \d+\s*ms exceeded")
ANY_ERROR_LINE = re.compile(r"^\s*[A-Za-z$]*Error: ")
TERMINAL_CODES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RAW_WAIT_CHECKS = 2
RAW_WAIT_DEFECT = (
    "The acceptance spec waits on a raw Playwright call that timed out: the first error of each test below is a Playwright "
    "wait raised from a line of the SPEC file itself, not from the helper, and it came back unchanged in {checks} checks in a "
    "row. A raw locator / page call waits until the test times out and says nothing about the application -- the failure "
    "never names what the requirement expects. Rewrite each such step with the helper's see() / seeOnce() / notSee() / "
    "click() / fill() / choose() / check() / press() ..., keeping what the step checks, so that a failure names what the "
    "requirement expects.\n")

def spec_unexplained_waits(output: str, test_files: dict[str, str]) -> list[tuple[tuple[str, str, str], str]]:
    own_specs = {Path(path).name for path in test_files if path.startswith("backend/test-e2e/")}
    lines = [TERMINAL_CODES.sub("", line) for line in output.splitlines()]
    found: list[tuple[tuple[str, str, str], str]] = []
    starts = [i for i, line in enumerate(lines) if FAILURE_HEADER.match(line)]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        title = re.sub(r"[\s─]+$", "", block[0].split("›")[-1]).strip() if "›" in block[0] else block[0].strip()
        first_error = next((i for i, line in enumerate(block) if ANY_ERROR_LINE.match(line)), None)
        if first_error is not None:
            error = block[first_error]
            if RAW_WAIT_ERROR.match(error):
                pass
            elif EXPECT_WAIT_ERROR.match(error) and not any(NAMES_EXPECTATION.match(line) for line in block[first_error + 1:]):
                pass
            else:
                continue
            after = block[first_error + 1:]
        else:
            bare = next((i for i, line in enumerate(block) if BARE_TEST_TIMEOUT.match(line)), None)
            if bare is None:
                continue
            error, after = block[bare], block[bare + 1:]
        frame = next((STACK_FRAME.match(line) for line in after if STACK_FRAME.match(line)), None)
        if frame is None or frame.group("file") not in own_specs:
            continue
        key = (frame.group("file"), title, error.strip())
        if key not in [k for k, _ in found]:
            found.append((key, f"{frame.group('file')}: {title}: {error.strip()}"))
    return found

def check_excerpt(output: str, limit: int = 3000) -> str:
    own = own_output(output)
    rest = output[len(own):]
    if not rest:
        return own[-limit:]
    return own[-(limit * 3 // 4):] + rest[:limit // 4]

def files_only_demand(extra_context: str) -> str:
    note = "\nYour previous answer contained no complete file block (it was analysis and got cut off). "
    if note in extra_context:
        return extra_context
    if edit_blocks_on():

        return (extra_context + note
                + "Answer with file blocks ONLY: the very first characters of your answer must be `=== FILE:` or `=== EDIT:`. "
                "No analysis. Output exactly ONE file this time: the single change most likely to fix the first failure -- as an "
                "EDIT block (only the changed lines) when it is an existing file over " + str(EDIT_MIN_CHARS) + " characters.").strip()
    return (extra_context + note
            + "Answer with file blocks ONLY: the very first characters of your answer must be `=== FILE:`. No analysis. "
            "Output exactly ONE file this time: the single change most likely to fix the first failure.").strip()

def answer_start() -> str:
    return "`=== FILE:` or `=== EDIT:`" if edit_blocks_on() else "`=== FILE:`"

def plan_paths(text: str) -> list[str]:
    found: list[str] = []
    for raw in re.findall(r"(?<![\w:./\\-])[\w./\\-]+\.(?:tsx?|jsx?|mjs|cjs|css|scss|json|html|svg)\b", text):
        path = posixpath.normpath(raw.replace("\\", "/"))
        if path.startswith(("/", "../")) or path in (".", "..") or ":" in path:
            continue
        if path not in found:
            found.append(path)
    return found

QUOTED = re.compile(r"[\"“`「]([^\"”`」\n]{4,})[\"”`」]")

BACKSLASH_ESCAPE = re.compile(r"\\([\"'`\\nt])")

def unescape_quotes(text: str) -> str:
    return BACKSLASH_ESCAPE.sub(lambda m: " " if m.group(1) in "nt" else m.group(1), text)

def spec_evidence(claim: str, requirement: str, test_files: dict[str, str], output: str) -> bool:
    norm = evidence_norm
    specs = norm("\n".join(text for path, text in test_files.items() if path.startswith("backend/test-e2e/")) or "\n".join(test_files.values()))
    other = norm(requirement + "\n" + output)
    quotes = claim_quotes(claim)
    in_spec = [q for q in quotes if q in specs]
    return any(q in other for q in quotes if any(s != q for s in in_spec))

def evidence_norm(text: str) -> str:

    return re.sub(r"\s+", " ", re.sub(r"[`\"“”‘’']", "'", unescape_quotes(text))).strip().lower()

def claim_quotes(claim: str) -> list[str]:
    return list(dict.fromkeys(evidence_norm(q) for q in QUOTED.findall(claim) + QUOTED.findall(unescape_quotes(claim))))

API_TEST_ROOT = "backend/tests/"

def api_test_evidence(claim: str, requirement: str, test_files: dict[str, str], output: str) -> list[str]:
    other = evidence_norm(requirement + "\n" + output)
    quotes = claim_quotes(claim)
    found: list[str] = []
    for path, text in test_files.items():
        if not path.startswith(API_TEST_ROOT):
            continue
        body = evidence_norm(str(text or ""))
        in_file = [q for q in quotes if q in body]
        if any(q in other for q in quotes if any(s != q for s in in_file)):
            found.append(path)
    return found

API_DEFECT_HEAD = ("TEST DEFECT in the backend API test file(s) {files} of this batch (advisory tests); the quoted step is "
                   "in {names}. Only {names} is to be repaired; the acceptance spec is not part of this report.\n")

def api_defect_detail(defect: str, files: list[str], test_files: dict[str, str]) -> str:
    text = defect
    for path in test_files:
        if path.startswith("backend/test-e2e/"):
            text = text.replace(Path(path).name, "(the acceptance spec)")
    text = re.sub(r"(?mi)^(\s*)(SPEC:|The acceptance spec contradicts the requirement text:)", r"\1(claim) \2", text)
    names = ", ".join(Path(path).name for path in files)
    return API_DEFECT_HEAD.format(files=", ".join(files), names=names) + text

SHARED_DATA_FILE = re.compile(r"(?i)(?:^|/)(?:database|data|seeds?|fixtures?)/|(?:^|/)[^/]*(?:seed|fixture|init_db)[^/]*$")
SEED_FLIPS_ARM = 3
SEED_DEFECT_HEAD = ("SHARED DATA: this batch's acceptance spec ({names}) and specs of earlier requirements need different "
                    "contents of {files}: rounds that changed it turned one side green and the other red ({flips} such "
                    "changes). The earlier specs are frozen. The implementer's claim about this batch's spec:\n")
SEED_RETURNS_ARM = 2

SEED_LOCK_FILE = re.compile(r"(?:^|/)(?:init_db|seed[^/]*)\.[cm]?[jt]s$", re.IGNORECASE)
SEED_LOCK_REVERTS = 2
SEED_LOCK_FREE = 2

SEED_LOCK_SKIP_TOP = {"node_modules", "dist", "tests", "test", "test-e2e", "e2e-support", "test-results", ".arc-test-db", "playwright-report", ".vite"}
SEED_LOCK_SKIP_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".log", ".sqlite", ".sqlite3", ".arc-restore")

def app_snapshot(project_dir: Path) -> tuple[dict[str, bytes], str]:
    root = Path(project_dir)
    files: dict[str, bytes] = {}
    problems: list[str] = []

    def shown(path: Any) -> str:
        try:
            return Path(path).relative_to(root).as_posix()
        except (TypeError, ValueError):
            return "(a folder)"

    for top in ("frontend", "backend"):
        base = root / top
        if not base.is_dir():
            continue
        for folder, dirnames, filenames in os.walk(base, onerror=lambda exc: problems.append(f"list {shown(getattr(exc, 'filename', ''))}: {type(exc).__name__}")):
            depth = Path(folder).relative_to(base).parts
            dirnames[:] = sorted(d for d in dirnames if d != "node_modules" and not (not depth and d in SEED_LOCK_SKIP_TOP))
            for name in sorted(filenames):
                if name.endswith(SEED_LOCK_SKIP_SUFFIXES):
                    continue
                path = Path(folder) / name
                rel = shown(path)
                try:
                    if path.is_file():
                        files[rel] = path.read_bytes()
                except OSError as exc:
                    problems.append(f"read {rel}: {type(exc).__name__}")
    return files, (problems[0] if problems else "")

REGRESSION_ONLY_MARK = "This batch's own spec PASSES"

def seed_hash(content: str | bytes | None) -> str:
    if content is None:
        return ""
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:16]

def red_signature(output: str) -> str:
    lines = [l for l in blocking_output(output or "").splitlines() if any(k in l for k in ("×", "FAIL", "Error", "expect(", "Locator:", "Timeout", "›"))
             and not l.startswith("[page] ")]
    return failure_signature(lines)

def own_or_earlier(output: str, test_files: dict[str, str]) -> str:
    blocking = blocking_output(output or "")
    if REGRESSION_ONLY_MARK in blocking:
        return "G"
    failures, _known = own_test_failures(blocking, test_files)
    return "R" if failures else ""

PLAN_HEADING = re.compile(r"^[#*\s]*(?:fix\s+plan|plan)\b[\s:*#.-]*$", re.IGNORECASE)

def split_diagnosis(text: str) -> tuple[list[str], list[str]]:
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    spec = [row for row in rows if row.upper().startswith("SPEC:")]
    app = [row for row in rows if not row.upper().startswith("SPEC:") and not PLAN_HEADING.match(row)]
    return spec, app

def failure_signature(failure_lines: list[str]) -> str:
    kept = []
    for line in failure_lines:
        if line.startswith(ADVISORY_MARK.strip()):
            break

        if any(k in line for k in SIGNATURE_MARKS) or re.match(r"\s*\d+\) ", line):
            text = line.strip()
            for pattern, placeholder in VOLATILE_BITS:
                text = pattern.sub(placeholder, text)
            kept.append(text)
    return "\n".join(sorted(kept))[:2500]

OWN_FAILURE_TITLE = re.compile(r"([\w.+-]+\.spec\.js):\d+:\d+\s+›\s+(.+)$")
OWN_FAILURE_END = re.compile(r"^\s*(?:\d+\)\s|\d+ (?:failed|flaky|skipped|passed|did not run)\s*$|\[\d+/\d+\]|## |FAILED SPEC FILES: )")

OWN_SPECS_PASSED = ("This batch's own spec PASSES", "(the e2e specs themselves passed)")

NOT_ALL_READ = ("[middle of the output omitted]", "did not run", "NOT RUN SPEC FILES")
GREEN_STATE = "<green>"
STUCK_WINDOW, STUCK_REPEATS = 4, 3
WHOLE_CHECK = ("", "(the check as a whole)")

def own_test_failures(output: str, test_files: dict[str, str]) -> tuple[dict[tuple[str, str], str], bool]:
    own = {Path(path).name for path in test_files if path.startswith("backend/test-e2e/")}
    lines = [TERMINAL_CODES.sub("", line) for line in output.splitlines()]
    failures: dict[tuple[str, str], str] = {}
    index = 0
    while index < len(lines):
        match = OWN_FAILURE_TITLE.search(lines[index].rstrip().rstrip("─").rstrip()) if FAILURE_HEADER.match(lines[index]) else None
        index += 1
        if not match or match.group(1) not in own:
            continue
        name = match.group(1)
        title = re.sub(r"\s*\(\d+(?:\.\d+)?m?s\)$", "", match.group(2)).strip()
        block: list[str] = []
        while index < len(lines) and not OWN_FAILURE_END.match(lines[index]):
            block.append(lines[index])
            index += 1
        error = next((line for line in block if ANY_ERROR_LINE.match(line)), None) \
            or next((line for line in block if "timeout" in line.lower() or "Timeout" in line), "")
        locator = next((line for line in block[block.index(error) + 1:] if line.strip().startswith("Locator:")), "") if error else ""
        where = next((m.group(0) for line in block if line.strip().startswith("at ")
                      for m in [re.search(re.escape(name) + r":\d+:\d+", line)] if m), "")
        text = " | ".join(part.strip() for part in (error, locator, where) if part.strip())
        for pattern, placeholder in VOLATILE_BITS:
            text = pattern.sub(placeholder, text)
        failures.setdefault((name, title), text or "(no error line)")
    whole = own_output(output)
    passed_marks = any(mark in whole for mark in OWN_SPECS_PASSED)
    greens_known = (bool(failures) or passed_marks) and not any(mark in whole for mark in NOT_ALL_READ)
    return failures, greens_known

class TestProgress:

    def __init__(self) -> None:
        self.history: dict[tuple[str, str], list[str]] = {}
        self.last_stuck = False

    @staticmethod
    def stuck(states: list[str]) -> bool:
        window = [s for s in states[-STUCK_WINDOW:] if s != GREEN_STATE]
        return any(window.count(s) >= STUCK_REPEATS for s in set(window))

    def observe(self, blocking: str, test_files: dict[str, str], signature: str) -> tuple[bool, str]:
        failures, greens_known = own_test_failures(blocking, test_files)
        current = dict(failures)
        if not failures and not greens_known:
            current[WHOLE_CHECK] = signature or "(no failure line)"
        if greens_known:
            for key in self.history:
                if key != WHOLE_CHECK and key not in failures:
                    current[key] = GREEN_STATE
        before = {key: list(states) for key, states in self.history.items()}
        for key, state in current.items():
            self.history.setdefault(key, []).append(state)
        stuck_before = [key for key, states in before.items() if self.stuck(states)]
        stuck_now = [key for key, states in self.history.items() if self.stuck(states)]
        self.last_stuck = False
        first_green = [key for key, state in current.items() if state == GREEN_STATE and GREEN_STATE not in before.get(key, [])]
        if first_green:
            return True, f"{first_green[0][1]} turned green"

        moved = [key for key in stuck_before if key in current and current[key] != before[key][-1]
                 and (current[key] == GREEN_STATE or current[key] not in before[key])]
        if moved:
            return True, f"the stuck test {moved[0][1]} moved on"
        if not stuck_before and not stuck_now:
            novel = [key for key, state in current.items() if state != GREEN_STATE and state not in before.get(key, [])]
            if novel:
                return True, f"{novel[0][1]} fails in a way it never did before"
            return False, "every failing test fails the way it did before"
        stuck = (stuck_now or stuck_before)[0]
        self.last_stuck = True
        return False, f"{stuck[1]} is stuck (the same first error in {STUCK_REPEATS} of its last {STUCK_WINDOW} checks)"

def parse_output(text: str, complete: bool = False) -> tuple[list[tuple[str, str]], str | None]:
    files: list[tuple[str, str]] = []
    defect = DEFECT_BLOCK.search(text)
    for raw_path, body in split_file_blocks(text, complete):

        path = posixpath.normpath(raw_path.replace("\\", "/").strip().lstrip("/"))

        if path.startswith("..") or path in ("", "."):
            continue
        body = re.sub(r"^```[a-zA-Z]*\n", "", body)
        body = re.sub(r"\n```\s*$", "", body)
        files.append((path, body.rstrip() + "\n"))

    last = {path: index for index, (path, _) in enumerate(files)}
    files = [item for index, item in enumerate(files) if last[item[0]] == index]
    return files, (defect.group("body").strip() if defect else None)

REPAIR_BACKGROUND_CHARS = 5000

GEN_ASK_FREE = 2
URL_CALL = re.compile(r"""\b(?:goto|toHaveURL|waitForURL)\(\s*(?:[`'"]([^`'"\n]*)[`'"]|/((?:\\.|[^/\\\n])+)/[a-z]*)""")
API_LITERAL = re.compile(r"""[`'"](?:https?://[^/\s`'"]+)?(/api(?:/[^`'"\s]*)?)[`'"]""")
OUTPUT_URL = re.compile(r"""https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?(/[^\s'"`)<>\]]*)""")
OUTPUT_API = re.compile(r"""(?<![\w/.])(/api/[\w\-./:%]*)""")
FRONT_CALL = re.compile(r"""\b(?:get|post|put|patch|delete|useData|preload|peek|postNow|loadNow|refresh|fetch)(?:<[^>()\n]*>)?\(\s*[`'"](/[^`'"\n]*)[`'"]""")
ROUTER_DEF = re.compile(r"""\b(router|app)\.(get|post|put|patch|delete|all)\(\s*[`'"]([^`'"]+)[`'"]""")
REQUIRE_VAR = re.compile(r"""(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*[`'"](\.[^`'"]+)[`'"]\s*\)""")
USE_MOUNT = re.compile(r"""\b(app|router)\.use\(\s*(?:[`'"]([^`'"]*)[`'"]\s*,\s*)?(?:(\w+)|require\(\s*[`'"](\.[^`'"]+)[`'"]\s*\))\s*\)""")

def api_path(raw: str) -> str:
    path = re.sub(r"\$\{[^}]*\}", ":p", raw.strip())
    path = re.sub(r"^https?://[^/]+", "", path).split("?")[0].split("#")[0]
    if not path.startswith("/api"):
        path = "/api" + (path if path.startswith("/") else "/" + path)
    return path

def route_matches(pattern: str, path: str) -> bool:
    a = [s for s in pattern.split("?")[0].split("/") if s]
    b = [s for s in path.split("?")[0].split("/") if s]
    if path.endswith("/") and b and len(b) < len(a):
        a = a[:len(b)]
    if len(a) != len(b):
        return False
    return all(x.startswith(":") or ":" in y or x == "*" or x == y for x, y in zip(a, b))

def page_files_for(paths: list[str], regexes: list[str], pages: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for path in paths:
        hits = [(sum(1 for s in route.split("/") if s and not s.startswith(":")), file) for route, file in pages
                if route and route != "*" and route_matches(route, path)]
        if hits:
            best = max(score for score, _ in hits)
            found += [file for score, file in hits if score == best]
    for source in regexes:
        try:
            rx = re.compile(source.replace("\\/", "/"))
        except re.error:
            continue
        hits = []
        for route, file in pages:
            if not route or route == "*":
                continue
            sample = "/" + "/".join("x1" if s.startswith(":") else s for s in route.split("/") if s)
            if rx.search(sample):
                hits.append((len([s for s in route.split("/") if s]), file))
        if hits:
            best = max(n for n, _ in hits)
            found += [file for n, file in hits if n == best]
    return list(dict.fromkeys(found))

def batch_base(batch_id: str) -> str:
    base = re.sub(r"^DESC-", "", str(batch_id).strip())
    base = re.split(r"\+|\.part", base)[0]
    return base.replace(".", "-")

def feature_of(batch_id: str) -> str:
    parts = batch_base(batch_id).split("-")
    return "-".join(parts[:-1]) if len(parts) > 2 else "-".join(parts)

def same_feature_or_leaf(batch_id: str, members: list[str]) -> bool:
    base = batch_base(batch_id)
    for member in members:
        other = batch_base(member)
        if base == other or feature_of(base) == feature_of(other) or other.startswith(base + "-") or base.startswith(other + "-"):
            return True
    return False

def implement_history(project_dir: Path, limit: int = 400) -> list[tuple[str, list[str]]]:
    import subprocess

    try:
        done = subprocess.run(["git", "-C", str(project_dir), "log", "--no-renames", f"-n{limit}", "--format=%x1e%s", "--name-only"],
                              capture_output=True, timeout=20)
    except Exception:
        return []
    if done.returncode != 0:
        return []
    history: list[tuple[str, list[str]]] = []
    for chunk in done.stdout.decode("utf-8", "replace").split("\x1e")[1:]:
        lines = [line.strip() for line in chunk.strip("\n").splitlines()]
        match = re.match(r"(\S+) \(implement\)", lines[0] if lines else "")
        if match:
            history.append((match.group(1), [line for line in lines[1:] if line]))
    return history

def contract_data(contract: str) -> dict[str, Any] | None:
    text = (contract or "").strip()
    if not text:
        return None
    try:
        start = text.index("{")
        value, _end = json.JSONDecoder().raw_decode(text[start:])
        if isinstance(value, dict):
            return value
    except (ValueError, json.JSONDecodeError):
        pass
    try:
        from .contract import parse_contract

        value = parse_contract(text)
    except Exception:
        return None
    return value if isinstance(value, dict) else None

def entry_related(requirements: Any, members: list[str]) -> bool:
    try:
        from .contract import related

        return bool(related(requirements, members))
    except Exception:
        return False

class CodegenImplementer:
    repair_mode_broken = False

    raw_wait_routed: set = set()

    all_thinking_batches: set = set()

    seed_lock_reverts: dict = {}
    call_deadline: float | None = None

    def __init__(
        self,
        llm: LLM,
        project_dir: Path,
        check: Callable[[], TestOutcome],
        *,
        max_rounds: int = 4,
        extra_rounds: int = EXTRA_ROUNDS,
        deadline: float | None = None,
        contract: str = "",
        relevant: list[str] | None = None,
        log=print,
        show: list[str] | None = None,
        route_raw_waits: bool = False,
        progress_rounds: int = 0,
        round_room: Callable[[], str] | None = None,
        start_failure_free: int = 0,
    ) -> None:

        self.progress_rounds = max(0, int(progress_rounds or 0))
        self.round_room = round_room
        self.start_failure_free = max(0, int(start_failure_free or 0))

        self.route_raw_waits = route_raw_waits

        self.extra_rounds = extra_rounds
        self.relevant = list(relevant or [])
        self.llm = llm
        self.project_dir = project_dir
        self.check = check
        self.max_rounds = max_rounds
        self.deadline = deadline
        self.contract = contract
        self.log = log
        self._omitted: set[str] = set()

        self._force_include: list[str] = []
        self._show_prior: list[str] = list(dict.fromkeys(show or []))
        self._shown: set[str] = set()
        self._volatile_sources = ""

        self._source_items: list[tuple[str, str, str]] = []
        self._source_truncated: set[str] = set()
        self._last_user: dict[str, Any] | None = None
        self._diag_prefix: dict[str, Any] | None = None

        self._history: list[tuple[str, list[str]]] | None = None
        self._contract_cache: tuple[str, dict[str, Any] | None] | None = None
        self._tiers: dict[str, list[str]] = {}
        self._tiers_logged: str = ""

        self.attempt_note: str = ""
        self.temperature: float | None = None

        self.call_deadline: float | None = None

    def _chat(self, messages: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        temperature = getattr(self, "temperature", None)
        if temperature is not None:
            options["temperature"] = float(temperature)
        call_deadline = getattr(self, "call_deadline", None)
        if call_deadline is None:
            return self.llm.chat(messages, **options)
        from . import llm as llm_module

        if self.__dict__.get("_call_cut"):

            raise CallDeadlineReached("the call deadline was reached by an earlier call of this implementer")
        options["end"] = llm_module._clock() + (float(call_deadline) - clock.now())
        try:
            return self.llm.chat(messages, **options)
        except CallDeadlineReached:
            self._call_cut = True
            raise

    def _past_call_deadline(self) -> bool:
        call_deadline = getattr(self, "call_deadline", None)
        return call_deadline is not None and (bool(self.__dict__.get("_call_cut")) or clock.now() >= float(call_deadline))

    def _attempt_part(self) -> str:
        note = str(getattr(self, "attempt_note", "") or "").strip()
        if not note:
            return ""
        if len(note) > ATTEMPT_NOTE_CHARS:
            note = note[:ATTEMPT_NOTE_CHARS].rstrip() + "\n[... rest of the note omitted]"
        return USER_TEXT["attempt"] + note

    def _progress_round_room(self, started: float, checks_done: int) -> str:
        if self.deadline is None:
            return "no batch deadline to measure a round against"
        now = clock.now()
        average = (now - started) / max(1, checks_done)
        left = self.deadline - now
        if left < average:
            return f"{int(max(0.0, left))} s left before the batch deadline, a round of this batch takes {int(average)} s"
        if self.round_room is not None:
            try:
                why = str(self.round_room() or "")
            except Exception as exc:
                return f"the run's budget could not be read ({exc!r})"
            if why:
                return why
        return ""

    def _decision_note(self, node_id: str, analysis: str, outcome: TestOutcome) -> str:
        plan = self._conclude(node_id, analysis, outcome)
        first = ""
        if plan:
            self.log(f"[codegen] {node_id}: analysis without files turned into a plan of {len(plan.splitlines())} line(s)")
            body = "\nFix plan (decided from your own analysis of this failure; do not reopen it):\n" + plan[:2500]

            paths = list(dict.fromkeys(m.group(1) for m in re.finditer(r"^\s*[-*]?\s*`?([\w./-]+\.[a-z]{1,4})`?\s+--", plan, re.MULTILINE)))
            if len(paths) > 2:
                first = (f"\nThis answer writes ONLY these files: {', '.join(paths[:2])}. The other lines of the plan come in the "
                         "next round -- do not write them now.")
                self.log(f"[codegen] {node_id}: the plan names {len(paths)} files; this answer is asked for the first two")
        else:
            body = "\nYour analysis so far (your previous answer was stopped because it contained no file):\n" + analysis[-ANALYSIS_NOTE_CHARS:].strip()
        return (
            body + first + "\nImplement it now. Answer with file blocks ONLY: the very first characters of your answer must be " + answer_start() + ". "
            "No analysis, no commentary."
        )

    def _need_note(self, body: str, test_files: dict[str, str]) -> str:
        fixed: list[str] = []
        shown: list[str] = []
        absent: list[str] = []
        for path in plan_paths(body):
            if path in self._shown:
                shown.append(path)
            elif self._is_test_path(path, test_files) or not self._writable(path):
                fixed.append(path)
            elif not (self.project_dir / path).is_file():
                absent.append(path)
        parts = ["\nYour NEED FILES request brought nothing new:"]
        if fixed:
            parts.append(f" {', '.join(fixed)} are fixed parts of the project (tests, starter plumbing, build configuration): they are "
                         "never shown and never change -- build on them as the files and tests you were shown use them.")
        if shown:
            parts.append(f" {', '.join(shown)} are already shown in full above.")
        if absent:
            parts.append(f" {', '.join(absent)} do not exist -- create them if the change needs them.")
        if not (fixed or shown or absent):
            parts.append(" it named no file that can be shown.")
        parts.append(" Do not ask again: answer now with the files that must change, starting directly with " + answer_start() + ".")
        return "".join(parts)

    def _conclude(self, node_id: str, analysis: str, outcome: TestOutcome) -> str:
        plan = ""
        try:
            message = self._chat(
                [
                    {"role": "system", "content": "You turn an unfinished debugging analysis into a decision. You never analyse further."},
                    {"role": "user", "content": (
                        "Failing check (excerpt):\n" + check_excerpt(outcome.output or "")
                        + "\n\nUnfinished analysis of that failure (it went in circles and was stopped):\n" + analysis[-14000:]
                        + "\n\nDecide now. Output at most 12 lines, one per change: `<file path> -- <what to change>`. Where the analysis "
                        "hesitates between causes, pick the most likely one. No code, no alternatives, no explanations."
                    )},
                ],
                max_tokens=1200,
                tag=f"codegen:{node_id}:decide",
                thinking=False,
            )
            plan = str(message.get("content") or "").strip()
        except QuotaExhausted:
            raise
        except Exception as exc:
            self.log(f"[codegen] {node_id}: no decision from the analysis ({str(exc)[:120]})")
        return plan

    def _diagnose(self, node: dict[str, Any], test_files: dict[str, str], outcome: TestOutcome, written: list[str], extra: str,
                  round_index: int, failed_plan: str, challenge: str = "", demand: str = "") -> str:
        node_id = str(node.get("id"))
        ask = USER_TEXT["ask_diagnose"] + (USER_TEXT["plan_failed"].format(plan=failed_plan[:1500]) if failed_plan else "")
        if challenge:
            ask += USER_TEXT["spec_challenge"].format(claim=challenge[:1200])
        if demand:
            ask += demand
        cap = reasoning_cap_chars()
        try:

            user = self._user(node, test_files, outcome, written, extra, True, ask=ask)
            record = self.__dict__.get("_last_user")
            message = self._chat(
                [
                    {"role": "system", "content": system_prompt()},
                    {"role": "user", "content": user},
                ],

                max_tokens=16000,
                tag=f"codegen:{node_id}:diagnose{round_index}",
                thinking=True,
                **({"reasoning_cap": cap} if cap else {}),
            )
        except QuotaExhausted:
            raise
        except Exception as exc:
            self.log(f"[codegen] {node_id}: diagnosis call failed ({str(exc)[:120]})")
            return ""
        if record is not None and setting_on("repair_prefix_shared", True):

            self._diag_prefix = record
        finish = str(getattr(self.llm, "last_finish_reason", "") or "")
        plan = str(message.get("content") or "").strip()
        for marker in ("=== FILE:", "=== EDIT:"):
            if marker in plan:
                plan = plan.split(marker, 1)[0].strip()
        tail_used = False
        if not plan:
            reasoning = str(message.get("reasoning_content") or "")

            if reasoning.strip():
                plan = self._conclude(node_id, reasoning, outcome)
                if cap and finish in ("reasoning_cap", "length"):

                    if not plan.strip():
                        plan = REASONING_TAIL_HEAD + reasoning[-REASONING_TAIL_CHARS:].strip()
                        tail_used = True
                    self.log(f"[llm] codegen:{node_id}:diagnose{round_index}: reasoning cut at {len(reasoning)} chars; its tail used "
                             "as the diagnosis" + (" (as it stands, marked unfinished)" if tail_used else " (decided by the concluding call)"))
        plan = plan if tail_used else plan[:2500]
        self.log(f"[codegen] {node_id} round {round_index}: diagnosis of {len(plan.splitlines())} line(s)" + (": " + plan.splitlines()[0][:160] if plan else " (none)"))
        return plan

    def _edit_target(self, path: str, test_files: dict[str, str]) -> tuple[str, str | None]:
        if self._is_test_path(path, test_files) or not self._writable(path):
            return ("a fixed or hidden file (tests, starter plumbing, package manifests, build configuration, dot files) that "
                    "is never changed", None)
        try:
            target = safe_path(self.project_dir, path)
        except PathError as exc:
            return (f"not a project path ({str(exc)[:80]})", None)

        folder = self.project_dir
        real: list[str] = []
        for part in Path(path).parts:
            try:
                names = os.listdir(folder)
            except OSError:
                names = []
            if part in names:
                real.append(part)
            else:
                other = next((n for n in names if n.lower() == part.lower()), None)
                if other is not None:
                    real.append(other)
                    shown = "/".join(real)
                    return f"the path on disk is spelt {shown}{'/...' if len(real) < len(Path(path).parts) else ''} (letter case): use that spelling", None
                break
            folder = folder / part
        if not target.exists():
            return "", None
        if not target.is_file():
            return "not a file", None
        try:
            return "", target.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"the file cannot be read as UTF-8 text ({type(exc).__name__}); write it whole in a FILE block", None

    def _follow_up(self, node_id: str, round_index: int, user_prompt: str, so_far: str, ask: str, max_tokens: int, kind: str,
                   marker: tuple[tuple[str, ...], int] | None = None) -> str | None:
        if self._past_call_deadline() or self.__dict__.get("_follow_quota"):
            return None
        if self.deadline is not None and clock.now() > self.deadline - FOLLOW_UP_MIN_SEC:
            self.log(f"[codegen] {node_id} round {round_index}: no {kind} call: less than {FOLLOW_UP_MIN_SEC} s left of the batch")
            return None

        saved_bound = getattr(self, "call_deadline", None)
        node_bound = self.deadline is not None and (saved_bound is None or float(self.deadline) < float(saved_bound))
        was_cut = bool(self.__dict__.get("_call_cut"))
        if node_bound:
            self.call_deadline = self.deadline
        try:
            message = self._chat(
                [
                    {"role": "system", "content": system_prompt()},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": so_far or "(nothing yet)"},
                    {"role": "user", "content": ask},
                ],
                max_tokens=max_tokens,
                tag=f"codegen:{node_id}:{kind}{round_index}",
                thinking=False,
                expect_marker=marker or (ANSWER_MARKERS + ("=== DONE",), PROSE_LIMIT_CHARS),
            )
        except QuotaExhausted as exc:
            self._follow_quota = True
            self.log(f"[codegen] {node_id} round {round_index}: {kind} call: key out of balance ({str(exc)[:120]}); the answer so far is used")
            return None
        except CallDeadlineReached:
            if node_bound and not was_cut:
                self._call_cut = False
                self.log(f"[codegen] {node_id} round {round_index}: the {kind} call reached the batch deadline; the answer so far is used")
            return None
        except Exception as exc:
            self.log(f"[codegen] {node_id} round {round_index}: {kind} call failed ({str(exc)[:160]})")
            return None
        finally:
            if node_bound:
                self.call_deadline = saved_bound
        return str(message.get("content") or "").replace("\r\n", "\n")

    def _reask_cutoff(self, node_id: str, round_index: int, user_prompt: str, content: str, max_tokens: int, limit: int) -> str:
        self.log(f"[codegen] {node_id} round {round_index}: cut-off answer had no files; asked once more for files only")

        counters = (LLM.streamed, LLM.cutoffs)
        try:
            more = self._follow_up(node_id, round_index, user_prompt, content,
                                   USER_TEXT["cutoff_reask"].format(limit=EDIT_MIN_CHARS, start=answer_start()),
                                   max_tokens, "reask", marker=(ANSWER_MARKERS, limit))
        finally:
            LLM.streamed, LLM.cutoffs = counters
        finish = str(getattr(self.llm, "last_finish_reason", "") or "")
        if more is not None and finish != "aborted" and HEADER_AT_LINE_START.search(more):
            self.log(f"[codegen] {node_id} round {round_index}: the re-ask answered with blocks (finish={finish}); it is this round's answer")
            return more
        self.log(f"[codegen] {node_id} round {round_index}: the re-ask brought no block either "
                 f"({'no call' if more is None else 'finish=' + (finish or 'none')}); the cut-off answer goes on as before")
        self.llm.last_finish_reason = "aborted"
        return content

    def _follow_controls(self, text: str, kind: str, test_files: dict[str, str]) -> list[str]:
        notes: list[str] = []
        need = NEED_BLOCK.search(text)
        if need:
            asked = [p for p in plan_paths(need.group("body")) if self._writable(p) and (self.project_dir / p).is_file()]
            self._promote(asked)
            notes.append(f"- your {kind} answer asked for {', '.join(asked) or 'files that cannot be shown'}"
                         + ("; they are shown in full now" if asked else ""))
        defect = DEFECT_BLOCK.search(text)
        if defect:
            notes.append(f"- your {kind} answer claimed a TEST DEFECT; such a claim counts only as the whole answer of a round "
                         f"(with its two quotes): {defect.group('body').strip()[:300]!r}")
        return notes

    def _settle_answer(self, node_id: str, round_index: int, user_prompt: str, content: str, max_tokens: int,
                       follow: dict[str, int], test_files: dict[str, str]) -> tuple[list[tuple[str, str]] | None, set[str], str, bool] | None:
        finish = self.llm.last_finish_reason
        parsed = parse_answer(content, finish == "stop")
        blocks = list(parsed.blocks)
        dropped = list(parsed.dropped)
        so_far = parsed.text[: parsed.complete_end]
        continued = False
        cut_path = parsed.cut_path
        notes: list[str] = []
        while finish == "length" and blocks and follow["continue"] > 0:
            where = f" while writing {cut_path}" if cut_path else ""
            lost = (f", except the unfinished block of {cut_path} (a cut FILE block is never written; of a cut EDIT block only "
                    "its complete pairs, shown above)") if cut_path else ""
            first = (f"{cut_path} first (as an EDIT block if it is an existing file over {EDIT_MIN_CHARS} characters), then "
                     if cut_path else "")
            more = self._follow_up(node_id, round_index, user_prompt, so_far,
                                   USER_TEXT["continue_cut"].format(where=where, lost=lost, first=first, limit=EDIT_MIN_CHARS),
                                   max_tokens, "cont")
            if more is None:
                break
            follow["continue"] -= 1
            continued = True
            finish = self.llm.last_finish_reason
            part = parse_answer(more, finish == "stop")
            notes += self._follow_controls(part.text, "continuation", test_files)
            later = {b.path for b in part.blocks}

            dropped = [(p, s + (" -- the continuation then sent a block for this file; check that it holds every change "
                                "you meant" if p in later else "")) for p, s in dropped] + list(part.dropped)
            blocks += part.blocks
            so_far = so_far + "\n" + part.text[: part.complete_end]
            cut_path = part.cut_path
            self.log(f"[codegen] {node_id} round {round_index}: continuation of the answer cut{where}: "
                     f"{len(part.blocks)} more block(s) {[b.kind + ' ' + b.path for b in part.blocks][:6]} (finish={finish})")
            if not part.blocks:
                break
        if not parsed.edit_headers and not continued and not any(b.kind == "EDIT" for b in blocks):
            if not dropped:
                return None
            note = clip(["- " + sentence for _path, sentence in dropped], 6000)
            self.log(f"[codegen] {node_id} round {round_index}: not applied from this answer:\n{note[:1500]}")
            return None, set(), note, False
        virtual: dict[str, str] = {}
        origin: dict[str, str] = {}
        before: dict[str, str | None] = {}
        order: list[str] = []
        refused_whole: dict[str, str] = {}
        pending: dict[str, list[tuple[Hunk, Any]]] = {}

        def settle(items: list, redo: bool = False) -> None:
            for b in items:
                if b.kind == "FILE":
                    if b.path not in order:
                        order.append(b.path)
                    body = b.body
                    if setting_on("strip_trailing_marker") and str(b.path).lower().endswith(TRAILING_MARKER_CODE_SUFFIXES):
                        body, removed = strip_trailing_markers(body)
                        for line in removed:
                            self.log(f"[codegen] {node_id}: removed trailing protocol line '{line}' from {b.path}")
                    virtual[b.path] = body
                    origin[b.path] = "FILE"
                    continue
                if b.path not in virtual and b.path not in before:
                    reason, text = self._edit_target(b.path, test_files)
                    if reason:
                        refused_whole[b.path] = reason
                        self.log(f"[codegen] {node_id} round {round_index}: EDIT {b.path} refused: {reason}")
                        continue
                    before[b.path] = text
                if b.path in refused_whole:
                    continue
                if b.path not in order:
                    order.append(b.path)
                base = virtual[b.path] if b.path in virtual else before[b.path]
                new, results = apply_hunks(base, b.hunks, baseline=before.get(b.path))
                if new is not None:
                    virtual[b.path] = new
                    origin.setdefault(b.path, "EDIT")
                counts: dict[str, int] = {}
                for r in results:
                    counts[r.status] = counts.get(r.status, 0) + 1
                self.log(f"[codegen] {node_id} round {round_index}: EDIT {b.path}{' (redo)' if redo else ''}: {len(results)} pair(s) "
                         + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
                bad = [(h, r) for h, r in zip(b.hunks, results) if not r.applied]
                if redo and pending.get(b.path):
                    asked = pending[b.path]
                    if len(b.hunks) == len(asked):

                        still = []
                        for (old, old_result), hunk, result in zip(asked, b.hunks, results):
                            meant = old.search.strip() != old.replace.strip()
                            if not result.applied or (result.status == "noop" and meant):
                                still.append((old, result if not result.applied else HunkResult(
                                    result.number, "refused", line=result.line,
                                    reason="the redo pair for it changed nothing; " + old_result.reason, snippet=old_result.snippet)))
                        pending[b.path] = still
                    else:
                        notes.append(f"- EDIT {b.path}: {len(asked)} change(s) were refused and the redo sent {len(b.hunks)} pair(s) "
                                     f"({sum(1 for r in results if r.applied and r.status != 'noop')} applied); the refused changes "
                                     "below are still open -- check which of them the file now holds")
                        pending[b.path] = asked + bad
                elif bad:
                    pending.setdefault(b.path, []).extend(bad)
                if not pending.get(b.path):
                    pending.pop(b.path, None)

        def report() -> list[str]:
            return [pending_text(path, [r for _h, r in pairs]) for path, pairs in pending.items()]

        settle(blocks)
        while pending and follow["retry"] > 0:
            more = self._follow_up(node_id, round_index, user_prompt, so_far, USER_TEXT["edit_retry"].format(report=clip(report(), 12000)),
                                   max_tokens, "redo")
            if more is None:
                break
            follow["retry"] -= 1
            part = parse_answer(more, self.llm.last_finish_reason == "stop")
            notes += self._follow_controls(part.text, "redo", test_files)
            self.log(f"[codegen] {node_id} round {round_index}: the refused pairs asked again with the current lines: "
                     f"{len(part.blocks)} block(s) back")
            dropped += part.dropped
            if not part.blocks:
                break
            settle(part.blocks, redo=True)
            so_far = so_far + "\n" + part.text[: part.complete_end]
        items = ["- " + sentence for _path, sentence in dropped]
        items += [f"- EDIT {path}: not applied at all: {reason}" for path, reason in refused_whole.items()]
        items += report() + notes
        files: list[tuple[str, str]] = []
        edited: set[str] = set()
        for path in order:
            if path not in virtual:
                continue
            if origin.get(path) == "EDIT":
                if virtual[path] == before.get(path):
                    continue
                edited.add(path)
            files.append((path, virtual[path]))
        note = clip(items, 6000)
        if note:
            self.log(f"[codegen] {node_id} round {round_index}: not applied from this answer:\n{clip(items, 3000)}")
        return files, edited, note, bool(items)

    def _promote(self, paths: list[str]) -> None:
        self._force_include = list(dict.fromkeys(list(paths) + self._force_include))

    @classmethod
    def _writable(cls, path: str) -> bool:
        return not (cls._is_test_path(path, {}) or path in READ_ONLY_FILES or path in TEMPLATE_PLUMBING or path in BUILD_CONTRACT
                    or any(part.startswith(".") for part in Path(path).parts))

    @staticmethod
    def _is_test_path(path: str, test_files: dict[str, str]) -> bool:

        return path.startswith(("frontend/tests/", "backend/tests/", "backend/test-e2e/", "backend/e2e-support/", "frontend/test/")) or path in test_files

    @staticmethod
    def _differs_from(target: Path, before: bytes | None) -> bool:
        try:
            now = target.read_bytes() if target.is_file() else None
        except OSError:
            return True
        return now != before

    @staticmethod
    def _shown_path(root: Path | None, target: Path) -> str:
        try:
            return Path(target).resolve().relative_to(Path(root).resolve()).as_posix()
        except (AttributeError, TypeError, ValueError, OSError):
            return str(target)

    def _roll_back(self, undo: dict[Path, bytes | None], node_id: str, round_index: int,
                   why: str = "an exception interrupted the round") -> list[Path]:
        from agent import source_io

        failed: list[Path] = []

        original_failed = False
        for target, before in sorted(undo.items(), key=lambda item: item[1] is None):
            if before is None and original_failed:
                failed.append(target)
                continue
            try:
                if before is None:
                    source_io.put_back(target, None)
                elif not target.is_file() or target.read_bytes() != before:
                    source_io.put_back(target, before)
            except OSError:
                failed.append(target)
                if before is not None:
                    original_failed = True
        self.log(f"[codegen] {node_id} round {round_index}: {why}; {len(undo)} file(s) put back"
                 + (f", NOT restored: {[self._shown_path(getattr(self, "project_dir", None), path) for path in failed]}" if failed else ""))
        return failed

    def _seed_lock(self, node: dict[str, Any], round_index: int, undo: dict[Path, bytes | None],
                   undo_paths: dict[Path, str], revert_free: bool) -> tuple[str, str]:
        from .seed_values import app_text, holds, lost_values, stated_seed_values

        if not setting_on("seed_lock"):
            return "", ""
        seeds = str(node.get("seed_sentences") or "")
        if not seeds.strip() or not stated_seed_values(seeds):
            return "", ""
        node_id = str(node.get("id"))

        names = {target: posixpath.normpath(str(undo_paths.get(target) or self._shown_path(self.project_dir, target)).replace("\\", "/"))
                 for target in undo}
        changed = {target: rel for target, rel in names.items() if self._differs_from(target, undo[target])}
        seed_targets = [target for target, rel in changed.items() if SEED_LOCK_FILE.search(rel)]
        if not seed_targets:
            return "", ""
        before_part = {rel: undo[target] for target, rel in changed.items() if undo[target] is not None}
        try:
            after_part = {rel: target.read_bytes() for target, rel in changed.items() if target.is_file()}
        except OSError as exc:
            self.log(f"[seed-lock] {node_id} round {round_index}: not checked (incomplete snapshot: a written file could not be read, {type(exc).__name__})")
            return "", ""

        if not lost_values(seeds, before_part, after_part):
            return "", ""
        after, problem = app_snapshot(self.project_dir)
        if problem:
            self.log(f"[seed-lock] {node_id} round {round_index}: not checked (incomplete snapshot: {problem})")
            return "", ""
        mine = set(changed.values())
        before = {rel: content for rel, content in after.items() if rel not in mine}
        before.update(before_part)
        lost = lost_values(seeds, before, after)

        seed_before = app_text({changed[target]: undo[target] for target in seed_targets if undo[target] is not None})
        lost = [value for value in lost if holds(seed_before, value)]
        if not lost:
            return "", ""
        files = [changed[target] for target in seed_targets]
        values = ", ".join(f'"{v}"' for v in lost[:20])
        others = [rel for rel in names.values() if not SEED_LOCK_FILE.search(rel)]
        key = (str(self.project_dir), node_id, "note" if others else "revert", tuple(sorted(lost)))
        used = CodegenImplementer.seed_lock_reverts.get(key, 0)
        head = f"[seed-lock] {node_id} round {round_index}: {files} removed stated seed values {lost[:8]}"
        if others:
            if used >= SEED_LOCK_REVERTS:
                self.log(f"{head}; noted {SEED_LOCK_REVERTS} times already, not noting again")
                return "", ""
            CodegenImplementer.seed_lock_reverts[key] = used + 1
            self.log(f"{head}; not reverted (the answer also wrote {others[:8]}); the next prompt asks for them back")
            return "noted", USER_TEXT["seed_lock_soft"].format(files=", ".join(files), values=values)
        if used >= SEED_LOCK_REVERTS or not revert_free:
            self.log(f"{head}; " + (f"reverted {SEED_LOCK_REVERTS} times already" if used >= SEED_LOCK_REVERTS
                                    else "no free revert left in this implementer") + ", not reverting again")
            return "", ""
        if setting_on("seed_lock_partial_restore", True):

            part = self._seed_lock_partial(node_id, round_index, seeds, undo, changed, lost, before)
            if part is not None:
                CodegenImplementer.seed_lock_reverts[key] = used + 1
                return part
        from agent import source_io

        _done, failed = source_io.put_back_all(list(undo.items()))
        still = [names[target] for target in undo if self._differs_from(target, undo[target])]
        if failed or still:
            self.log(f"{head}; the revert failed ({len(failed)} file(s) not put back, still changed: {still[:8]}); "
                     "the files stay as they are and are checked as usual")
            return "revert_failed", ""
        CodegenImplementer.seed_lock_reverts[key] = used + 1
        self.log(f"[seed-lock] {node_id} round {round_index}: reverted {files}: they removed stated seed values {lost[:8]}")
        return "reverted", USER_TEXT["seed_lock"].format(files=", ".join(files), values=values)

    def _seed_lock_partial(self, node_id: str, round_index: int, seeds: str, undo: dict[Path, bytes | None],
                           changed: dict[Path, str], lost: list[str],
                           before: dict[str, bytes]) -> tuple[str, str] | None:
        from agent import source_io
        from .seed_values import app_text, holds, lost_values, stated_literals, stated_seed_values

        def why_not(reason: str) -> None:
            self.log(f"[seed-lock] {node_id} round {round_index}: partial put-back not used ({reason}); the whole answer is put back")
            return None

        try:
            restore = [target for target, rel in changed.items() if undo[target] is not None
                       and any(holds(app_text({rel: undo[target]}), value) for value in lost)]
            kept = [target for target in changed if target not in restore]
            if not restore or not kept:
                return None
            after_of: dict[Path, bytes | None] = {}
            for target in changed:
                after_of[target] = target.read_bytes() if target.is_file() else None
            if any(after_of[target] is None for target in changed):
                return why_not("a file of the answer was deleted by it")

            def text(content: bytes | None) -> str:
                return "" if content is None else content.decode("utf-8", errors="replace")

            def tokens(content: bytes | None) -> set[str]:
                return set(re.findall(r"[A-Za-z_$][\w$]{2,}", text(content)))

            def stem(rel: str) -> str:
                return posixpath.splitext(posixpath.basename(rel))[0]

            introduced: set[str] = set()
            taken: set[str] = set()
            for target in restore:
                introduced |= tokens(after_of[target]) - tokens(undo[target])
                taken |= tokens(undo[target]) - tokens(after_of[target])
            stated = stated_seed_values(seeds)
            for k in kept:

                if any(holds(text(after_of[k]), value) for value in stated):
                    return why_not(f"{changed[k]} holds stated seed values")
                if tokens(after_of[k]) & taken:
                    return why_not(f"{changed[k]} uses what the put-back change took out")
                k_texts = (text(undo[k]), text(after_of[k]))
                for r in restore:
                    r_texts = (text(undo[r]), text(after_of[r]))
                    if (any(holds(t, stem(changed[k])) for t in r_texts) or any(holds(t, stem(changed[r])) for t in k_texts)):
                        return why_not(f"{changed[k]} and {changed[r]} name each other")
                    if undo[k] is not None and (tokens(undo[k]) - tokens(after_of[k])) & tokens(undo[r]):
                        return why_not(f"{changed[r]} uses what the change of {changed[k]} removed")
                if tokens(after_of[k]) & introduced:
                    return why_not(f"{changed[k]} uses what the put-back change introduced")
            _done, failed = source_io.put_back_all([(target, undo[target]) for target in restore])
            if failed or any(self._differs_from(target, undo[target]) for target in restore) \
                    or any(self._differs_from(target, after_of[target]) for target in kept):
                return why_not("the partial put-back did not land")
            now, problem = app_snapshot(self.project_dir)
            if problem:
                return why_not(f"incomplete snapshot: {problem}")
            missing = lost_values(seeds, before, now)
            if missing:
                return why_not(f"still missing {missing[:8]}")

            now_text = app_text(now)
            absent = [value for value in stated_literals(seeds) if not holds(now_text, value)]
            if absent:
                return why_not(f"stated literals not present afterwards {absent[:8]}")
        except Exception as exc:
            return why_not(f"{type(exc).__name__}")
        files = [changed[target] for target in restore]
        kept_names = [changed[target] for target in kept]
        values = ", ".join(f'"{v}"' for v in lost[:20])
        self.log(f"[seed-lock] {node_id} round {round_index}: put back only {files}: they removed stated seed values "
                 f"{lost[:8]}; kept {kept_names[:8]} (independent of them), checked as usual")
        return "restored_part", USER_TEXT["seed_lock_part"].format(files=", ".join(files), values=values,
                                                                  kept=", ".join(kept_names[:8]))

    def _books_from_disk(self, undo: dict[Path, bytes | None], undo_paths: dict[Path, str], written_before: list[str],
                         written: list[str], seed_now: dict[str, str], seed_changes: dict[str, str]) -> None:
        for target, path in undo_paths.items():
            if self._differs_from(target, undo.get(target)):
                if path not in written:
                    written.append(path)
            elif path not in written_before and path in written:
                written.remove(path)
            if path in seed_now:
                try:
                    now = target.read_bytes() if target.is_file() else None
                except OSError:
                    continue
                digest = seed_hash(now)
                if digest != seed_now[path]:
                    seed_changes[path] = digest
                else:
                    seed_changes.pop(path, None)

    def project_files(self, extra_paths: list[str], repair: bool = False, focus_text: str = "", node: dict[str, Any] | None = None,
                      test_files: dict[str, str] | None = None, output: str = "") -> str:
        candidates: list[str] = []
        for root in ("frontend/src", "backend/src"):
            base = self.project_dir / root
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and not any(part in IGNORED_DIRS for part in path.parts):
                    rel = path.relative_to(self.project_dir).as_posix()
                    if rel not in TEMPLATE_PLUMBING:
                        candidates.append(rel)

        for rel in dict.fromkeys(self._force_include + self._show_prior + list(extra_paths)):
            if rel not in candidates and self._writable(rel) and (self.project_dir / rel).is_file() \
                    and not any(part in IGNORED_DIRS for part in Path(rel).parts):
                candidates.append(rel)

        core = [p for p in CORE_FILES if p in candidates and p not in extra_paths]

        relevant = [p for p in self._with_imports([p for p in self.relevant if p in candidates], candidates) if p not in core and p not in extra_paths]
        rest = [p for p in candidates if p not in core and p not in relevant and p not in extra_paths]
        rest.sort(key=lambda rel: (self.project_dir / rel).stat().st_mtime, reverse=True)
        forced = [p for p in self._force_include if p in candidates]
        if repair and focus_text:

            forced += [p for p in candidates if p not in forced and Path(p).name in focus_text]
        forced += [p for p in self._show_prior if p in candidates and p not in forced]
        forced = [p for p in forced if p not in extra_paths]

        spent = 0
        over_budget: set[str] = set()
        for rel in forced:
            try:
                size = (self.project_dir / rel).stat().st_size
            except OSError:
                continue
            if spent + size > FORCED_CHARS and spent:
                over_budget.add(rel)
            else:
                spent += size
        forced = [p for p in forced if p not in over_budget]

        tiers = self._rank(candidates, node, test_files or {}, output, repair) if node is not None else {}
        self._tiers = tiers
        ranked = list(dict.fromkeys(p for tier in tiers.values() for p in tier if p not in core and p not in extra_paths))
        position = {p: index for index, p in enumerate(ranked)}

        def by_rank(group: list[str]) -> list[str]:
            return sorted(group, key=lambda p: position.get(p, len(position)))

        first = [p for p in relevant if p not in forced]
        if repair:
            first = by_rank(first)
        ordered = list(dict.fromkeys(core + [p for p in relevant if p not in forced] + [p for p in rest if p not in forced] + forced
                                     + [p for p in extra_paths if p in candidates]))
        background = (set(rest) - set(forced)) if relevant else set()

        anchor = next((p for p in by_rank([p for p in rest if p in background]) if p in set(tiers.get("failing") or [])), None) \
            if repair else None
        taking = list(dict.fromkeys(core + first + by_rank([p for p in rest if p not in forced]) + forced
                                    + [p for p in extra_paths if p in candidates]))
        self._omitted = set()
        self._shown = set()
        edits = edit_blocks_on()
        truncated: set[str] = set()
        blocks: dict[str, str] = {}
        notes_of: dict[str, str] = {}
        total = 0
        background_spent = 0
        for rel in taking:
            try:
                text = (self.project_dir / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            full = text
            if len(text) > MAX_FILE_CHARS and rel not in forced and rel not in extra_paths:

                text = text[:MAX_FILE_CHARS] + "\n... [truncated: list this file in a NEED FILES block and it will be shown in full next round; do NOT rewrite it now]"
                truncated.add(rel)

            block = f"--- {rel} ---{size_note(full) if edits and self._writable(rel) else ''}\n{text}"

            if rel in forced or rel in extra_paths:
                fits = True
            elif rel in background:
                fits = (background_spent + len(block) <= REPAIR_BACKGROUND_CHARS and total + len(block) <= MAX_CONTEXT_CHARS) if repair \
                    else total + len(block) <= BACKGROUND_CHARS
                if rel == anchor:
                    fits = total + len(block) <= MAX_CONTEXT_CHARS
            else:
                fits = total + len(block) <= MAX_CONTEXT_CHARS
            if not fits:
                notes_of[rel] = f"--- {rel} --- (exists, content not shown; {len(text)} chars. Do NOT rewrite it blind: list it in a NEED FILES block and it will be shown next round)"
                self._omitted.add(rel)
                continue
            blocks[rel] = block
            total += len(block)
            if rel in background:
                background_spent += len(block)
            if rel not in truncated:
                self._shown.add(rel)
        self._omitted |= truncated

        self._source_items = ([("chunk", p, blocks[p]) for p in ordered if p in blocks and p not in forced and p not in extra_paths]
                              + [("note", p, notes_of[p]) for p in ordered if p in notes_of]
                              + [("volatile", p, blocks[p]) for p in ordered if p in blocks and (p in forced or p in extra_paths)])
        self._source_truncated = set(truncated)
        chunks = [blocks[p] for p in ordered if p in blocks and p not in forced and p not in extra_paths]
        volatile = [blocks[p] for p in ordered if p in blocks and (p in forced or p in extra_paths)]
        notes = [notes_of[p] for p in ordered if p in notes_of]
        if node is not None and tiers:
            summary = "; ".join(f"{name} {files[:4]}" + (f" +{len(files) - 4}" if len(files) > 4 else "") for name, files in tiers.items() if files)
            if summary and summary != self._tiers_logged:
                self._tiers_logged = summary
                self.log(f"[codegen] {node.get('id')}: context ranking: {summary}")

        self._volatile_sources = "\n\n".join(notes + volatile)
        return "\n\n".join(chunks) if chunks else "(no untouched source files)"

    def _with_imports(self, files: list[str], candidates: list[str]) -> list[str]:
        known = set(candidates)
        result = list(files)
        for rel in files:
            try:
                text = (self.project_dir / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            base = (self.project_dir / rel).parent
            for spec in IMPORT_SPEC.findall(text):
                target = (base / spec).resolve()
                for candidate in (target, *(target.with_name(target.name + ext) for ext in (".tsx", ".ts", ".js", ".jsx")), target / "index.ts", target / "index.tsx", target / "index.js"):
                    try:
                        found = candidate.relative_to(self.project_dir.resolve()).as_posix()
                    except ValueError:
                        continue
                    if found in known and found not in result:
                        result.append(found)
                        break
        return result

    def _read(self, rel: str) -> str:
        try:
            return (self.project_dir / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _contract_value(self) -> dict[str, Any]:
        if self._contract_cache is None or self._contract_cache[0] != self.contract:
            self._contract_cache = (self.contract, contract_data(self.contract))
        return self._contract_cache[1] or {}

    def _route_table(self, known: set[str]) -> list[tuple[str, str, str]]:

        def resolve(base: str, spec: str) -> str | None:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(base), spec))
            return next((option for option in (target, target + ".js", target + "/index.js") if option in known), None)

        def join(prefix: str, path: str) -> str:
            return "/" + "/".join(s for s in (prefix + "/" + path).split("/") if s)

        table: list[tuple[str, str, str]] = []
        queue = [("", "backend/src/app.js")]
        seen: set[tuple[str, str]] = set()
        while queue and len(seen) < 200:
            prefix, rel = queue.pop(0)
            if (prefix, rel) in seen or rel not in known:
                continue
            seen.add((prefix, rel))
            text = self._read(rel)
            variables = {m.group(1): resolve(rel, m.group(2)) for m in REQUIRE_VAR.finditer(text)}
            for m in USE_MOUNT.finditer(text):
                target = variables.get(m.group(3)) if m.group(3) else resolve(rel, m.group(4))
                if target and target != rel:
                    queue.append((join(prefix, m.group(2) or ""), target))
            for m in ROUTER_DEF.finditer(text):
                table.append((m.group(2).upper(), join(prefix, m.group(3)), rel))
        return table

    @staticmethod
    def _route_files(requests: list[tuple[str | None, str]], table: list[tuple[str, str, str]], known: set[str]) -> list[str]:
        found: list[str] = []
        for method, path in requests:
            hits = [f for m, pattern, f in table if (method is None or m in (method, "ALL")) and route_matches(pattern, path)]
            if not hits and method is not None:
                hits = [f for _m, pattern, f in table if route_matches(pattern, path)]
            if not hits:
                segments = [s for s in path.split("/") if s]
                guess = f"backend/src/routes/{segments[1]}.js" if len(segments) > 1 and ":" not in segments[1] else ""
                hits = [guess] if guess in known else []
            found += hits
        return list(dict.fromkeys(found))

    def _rank(self, candidates: list[str], node: dict[str, Any], test_files: dict[str, str], output: str, repair: bool) -> dict[str, list[str]]:
        known = set(candidates)
        members = [str(m) for m in (node.get("members") or [node.get("id")]) if m]
        data = self._contract_value()
        page_rows = [p for p in (data.get("pages") or []) if isinstance(p, dict) and p.get("file")]
        pages = [(str(p.get("route") or ""), posixpath.normpath(str(p["file"]).replace("\\", "/").strip().lstrip("/"))) for p in page_rows]
        table_cache: list[list[tuple[str, str, str]]] = []

        def table() -> list[tuple[str, str, str]]:
            if not table_cache:
                table_cache.append(self._route_table(known))
            return table_cache[0]

        specs = self._failing_tests(test_files, output) if (repair and output) else dict(test_files)
        urls: list[str] = []
        regexes: list[str] = []
        requests: list[tuple[str | None, str]] = []
        imported: list[str] = []
        for spec, text in specs.items():
            text = str(text or "")
            for m in URL_CALL.finditer(text):
                if m.group(1) is None:
                    regexes.append(m.group(2))
                    continue
                url = re.sub(r"^https?://[^/]+", "", m.group(1))
                if url.startswith("/api"):
                    requests.append((None, api_path(url)))
                elif url.startswith("/"):
                    urls.append(re.sub(r"\$\{[^}]*\}", ":p", url).split("?")[0])
            requests += [(None, api_path(m.group(1))) for m in API_LITERAL.finditer(text)]
            base = posixpath.dirname(spec)
            for target_spec in IMPORT_SPEC.findall(text):
                target = posixpath.normpath(posixpath.join(base, target_spec))
                imported += [next((o for o in (target, *(target + e for e in (".tsx", ".ts", ".js", ".jsx"))) if o in known), "")]
        for m in OUTPUT_URL.finditer(output or ""):
            path = m.group(1).split("?")[0]
            if path.startswith("/api"):
                requests.append((None, api_path(path)))
            else:
                urls.append(path)
        requests += [(None, api_path(m.group(1))) for m in OUTPUT_API.finditer(output or "")]

        urls = [u for u in urls if u.strip("/")]
        failing = page_files_for(list(dict.fromkeys(urls)), list(dict.fromkeys(regexes)), pages) \
            + self._route_files(list(dict.fromkeys(requests)), table(), known) + imported

        if self._history is None:
            self._history = implement_history(self.project_dir)
        ids = members + [str(node.get("id") or "")]
        earlier = [f for batch, files in self._history if same_feature_or_leaf(batch, ids) for f in files]

        own_requests: list[tuple[str | None, str]] = []
        for entry in data.get("api") or []:
            if isinstance(entry, dict) and entry.get("path") and entry_related(entry.get("requirements"), members):
                methods = [x.upper() for x in re.split(r"[^A-Za-z]+", str(entry.get("method") or "")) if x] or [None]
                own_requests += [(method, api_path(str(entry["path"]))) for method in methods]
        own_pages = [file for (route, file), row in zip(pages, page_rows) if entry_related(row.get("requirements"), members)]
        for rel in self._with_imports([p for p in own_pages if p in known], candidates):
            if rel.startswith("frontend/") and rel not in CORE_FILES:
                own_requests += [(None, api_path(m.group(1))) for m in FRONT_CALL.finditer(self._read(rel))]
        routes = self._route_files(list(dict.fromkeys(own_requests)), table(), known)
        own_backend = [p for p in self._with_imports(routes, candidates) if p.startswith("backend/src/")]

        def clean(files: list[str]) -> list[str]:
            return [p for p in dict.fromkeys(files) if p and p in known and p not in CORE_FILES and self._writable(p)
                    and not self._is_test_path(p, test_files)]

        return {"failing": clean(failing), "earlier": clean(earlier), "own_backend": clean(own_backend)}

    @staticmethod
    def _failing_tests(test_files: dict[str, str], output: str) -> dict[str, str]:
        named = {p: c for p, c in test_files.items() if Path(p).name in output}
        return named or test_files

    def _user(self, node: dict[str, Any], test_files: dict[str, str], outcome: TestOutcome, written: list[str], extra: str, repair: bool, ask: str = "") -> str:
        parts = []
        sizes: dict[str, int] = {}
        if self.contract:
            parts.append(USER_TEXT["contract"] + self.contract)
            sizes["contract"] = len(parts[-1])
        parts.append(describe_for_implementer(node))
        sizes["requirement"] = len(parts[-1])

        stable_sources = self.project_files(written, repair=repair, focus_text=outcome.output if repair else "", node=node,
                                            test_files=test_files, output=outcome.output or "")
        parts.append(USER_TEXT["sources"] + stable_sources)
        sizes["sources"] = len(parts[-1])
        if test_files:
            shown = self._failing_tests(test_files, outcome.output) if repair else test_files
            hidden = [p for p in test_files if p not in shown]
            parts.append(
                USER_TEXT["tests"]
                + "\n\n".join(f"### {p}\n{c}" for p, c in shown.items())
                + (USER_TEXT["hidden"].format(names=", ".join(hidden)) if hidden else "")
            )
            sizes["tests"] = len(parts[-1])
        if self._volatile_sources:
            parts.append(USER_TEXT["working"] + self._volatile_sources)
            sizes["working"] = len(parts[-1])
        attempt = self._attempt_part()
        if attempt:

            parts.append(attempt)
            sizes["attempt"] = len(attempt)
        parts.append((USER_TEXT["check_repair"] if repair else USER_TEXT["check_first"]) + outcome.summary)
        sizes["check"] = len(parts[-1])
        if extra:
            parts.append(extra)
            sizes["extra"] = len(extra)

        self.log(f"[prompt] {node.get('id')} {'repair' if repair else 'gen'}: " + " ".join(f"{k}={v}" for k, v in sizes.items()) + f" omitted_files={len(self._omitted)}")

        if not ask and edit_blocks_on():
            ask = USER_TEXT["ask_repair_edits" if repair else "ask_first_edits"].format(limit=EDIT_MIN_CHARS)

        self._last_user = {"labels": list(sizes), "parts": list(parts), "node": str(node.get("id")), "output": outcome.output,
                           "summary": outcome.summary, "written": tuple(written), "extra": extra,
                           "items": list(self.__dict__.get("_source_items") or []),
                           "truncated": set(self.__dict__.get("_source_truncated") or set()),
                           "ask": ask or (USER_TEXT["ask_repair"] if repair else USER_TEXT["ask_first"])}
        parts.append(ask or (USER_TEXT["ask_repair"] if repair else USER_TEXT["ask_first"]))
        return "\n\n".join(parts)

    def _shared_writer_prompt(self, node: dict[str, Any], outcome: TestOutcome, written: list[str], extra_context: str,
                              added: str, tail: str) -> str | None:
        diag = self.__dict__.get("_diag_prefix")
        self._diag_prefix = None
        fresh = self.__dict__.get("_last_user")
        if diag is None or fresh is None:
            return None
        node_id = str(node.get("id"))
        why = ""
        if diag["node"] != node_id or fresh["node"] != node_id:
            why = "another leaf"
        elif diag["output"] != outcome.output or diag["summary"] != outcome.summary or fresh["summary"] != outcome.summary:
            why = "a new check result"
        elif diag["written"] != tuple(written):
            why = "the files written in this batch changed"
        elif diag["extra"] != extra_context + tail or fresh["extra"] != (extra_context + added + tail).strip():
            why = "the notes changed"
        else:
            d_parts, f_parts = dict(zip(diag["labels"], diag["parts"])), dict(zip(fresh["labels"], fresh["parts"]))
            for label in ("contract", "requirement", "tests", "attempt", "check"):
                if d_parts.get(label) != f_parts.get(label):
                    why = f"the {label} part changed"
                    break

        def states(record: dict[str, Any]) -> dict[str, tuple[str, str]]:
            return {rel: ("note" if kind == "note" else "cut" if rel in record["truncated"] else "full", text)
                    for kind, rel, text in record["items"]}

        appended: list[str] = []
        if not why:
            before, now = states(diag), states(fresh)
            replaced: set[str] = set()
            for rel, state in before.items():
                if now.get(rel) == state:
                    continue
                if now.get(rel, ("", ""))[0] == "full" and state[0] == "note":
                    replaced.add(rel)
                    continue

                why = (f"{rel} was shown truncated and is shown in full now" if state[0] == "cut" and now.get(rel, ("", ""))[0] == "full"
                       else f"{rel} changed on disk or in its view")
                break
            if not why:
                appended = [text for kind, rel, text in fresh["items"] if rel not in before or rel in replaced]
        if why:
            self.log(f"[prompt] {node_id} repair prefix not shared: {why}")
            return None
        pieces = ["\n\n".join(diag["parts"])]
        if appended:
            pieces.append(USER_TEXT["prefix_added"] + "\n\n".join(appended))
        if added.strip():
            pieces.append(added.strip())
        pieces.append(fresh["ask"])
        prompt = "\n\n".join(pieces)
        self.log(f"[prompt] {node_id} repair prefix shared: prefix={len(pieces[0])} files_added={len(appended)}"
                 f" ({sum(map(len, appended))} chars) notes_added={len(added.strip())} total={len(prompt)}")
        return prompt

    def implement(self, node: dict[str, Any], test_files: dict[str, str], first_outcome: TestOutcome, extra_context: str = "") -> ImplementResult:
        written: list[str] = []
        outcome = first_outcome
        node_id = str(node.get("id"))
        last_signature = ""
        empty_rounds = 0
        round_index = -1
        stagnant = 0
        aborted_rounds = 0
        cut_free = CUT_FREE
        untouched_rounds = 0
        refusal_note = ""
        unproven_free = 1
        need_free = NEED_UNSHOWN_FREE
        need_note = ""
        gen_ask_free = GEN_ASK_FREE
        challenged: set[tuple[str, str]] = set()
        stagnation_pending = ""
        last_plan = ""
        last_plan_signature = ""
        analysis_note = ""
        gen0_thinking_off = False
        repeats = 0
        stalled = False
        raw_waits: dict[tuple[str, str, str], int] = {}
        tests_progress = TestProgress()
        started = clock.now()
        checks_done = 0
        uncounted = 0
        progress_granted = 0
        last_stuck = False

        seed_state = own_or_earlier(outcome.output, test_files) if not outcome.passed else ""
        seed_flips = 0
        seed_returns = 0
        seed_files: list[str] = []
        seed_note = ""
        seed_stage = 0
        seed_strong = False
        seed_hinted = False
        lock_note = ""
        lock_free = SEED_LOCK_FREE
        edits_mode = edit_blocks_on()
        follow = {"continue": CONTINUE_FREE, "retry": EDIT_RETRY_FREE}
        seed_now: dict[str, str] = {}
        seed_seen: dict[str, dict[str, str]] = {}
        seed_sigs: set[str] = set()
        if not outcome.passed:
            seed_sigs.add(red_signature(outcome.output))
        if not outcome.passed and lint_only_failure(outcome.output) is not None:

            self.log(f"[codegen] {node_id}: the e2e specs are green; lint findings alone do not block (advisory):\n{lint_only_failure(outcome.output)[:600]}")
            return ImplementResult("passed", written, "e2e green; lint findings advisory", 0, last_check=outcome.output)
        if not outcome.passed and getattr(outcome, "stop", ""):

            self.log(f"[codegen] {node_id}: {outcome.stop}; no round")
            return ImplementResult("budget", written, str(outcome.stop), 0)
        if not outcome.passed and getattr(outcome, "infra", ""):

            self.log(f"[codegen] {node_id}: the first check failed for an infrastructure reason ({outcome.infra}); not shown to the model")
            outcome = TestOutcome(False, "(The checks could not run yet: the dependency install failed and the factory retries it. "
                                         "This is not about the application's code. Write the implementation from the requirement and the tests.)")

        if (self.extra_rounds > 0 and getattr(self, "recall_all_thinking", True)
                and (str(self.project_dir), node_id) in CodegenImplementer.all_thinking_batches):

            gen0_thinking_off = True
            self.log(f"[codegen] {node_id}: an earlier first answer of this batch was all thinking and no content; "
                     "the first answer is asked without thinking")
        limit = self.max_rounds
        reasked_rounds: set[int] = set()
        while round_index < limit:
            self._diag_prefix = None
            round_index += 1
            part_round = False
            if self.deadline is not None and clock.now() > self.deadline:
                return ImplementResult("budget", written, "node deadline reached", round_index, stop_reason="deadline")
            if self._past_call_deadline():
                return ImplementResult("budget", written, "call deadline reached", round_index, stop_reason="deadline")
            repair = round_index > 0
            thinking = bool(SETTINGS["repair_thinking"] if repair else SETTINGS["implementer_thinking"])
            if not repair and gen0_thinking_off:
                thinking = False
            if repair and CodegenImplementer.repair_mode_broken:
                thinking = bool(SETTINGS["implementer_thinking"])

            if repair and not CodegenImplementer.repair_mode_broken:
                max_tokens = 12000
            else:
                max_tokens = 16000 if "glm" in str(getattr(self.llm, "model", "")).lower() else 32000
            plan_note = ""
            if repair and SETTINGS.get("repair_diagnosis", True) and not analysis_note and not thinking:

                try:
                    failed_plan = last_plan if (last_plan and last_signature and last_plan_signature == last_signature) else ""

                    demanded = bool(stagnation_pending)
                    plan = self._diagnose(node, test_files, outcome, written, extra_context + seed_note + lock_note, round_index, failed_plan,
                                          demand=stagnation_pending)
                    stagnation_pending = ""
                except QuotaExhausted as exc:
                    return ImplementResult("budget", written, f"key out of balance: {exc}", round_index)

                def spec_part(text: str, logged: bool = True) -> list[str]:
                    blamed = split_diagnosis(text)[0]
                    if not blamed:
                        return []

                    if not spec_evidence("\n".join(blamed), describe_node(node), test_files, own_output(outcome.output)):
                        if logged:
                            self.log(f"[codegen] {node_id}: a SPEC verdict without verifiable quotes is not accepted: {blamed[0][:160]}")
                        return []
                    return blamed

                def claim_key(text: str) -> tuple[str, str]:
                    return last_signature, re.sub(r"\s+", " ", text).strip().lower()

                first_app = split_diagnosis(plan)[1]
                proven = spec_part(plan)
                rediagnosed = False
                if proven and not demanded and claim_key("\n".join(proven)) not in challenged:

                    challenged.add(claim_key("\n".join(proven)))
                    try:
                        plan = self._diagnose(node, test_files, outcome, written, extra_context + seed_note + lock_note, round_index, "", challenge="\n".join(proven))
                        rediagnosed = True
                    except QuotaExhausted as exc:
                        return ImplementResult("budget", written, f"key out of balance: {exc}", round_index)
                last_plan, last_plan_signature = plan, last_signature
                about_spec = spec_part(plan) if rediagnosed else proven
                blamed_rows, app_rows = split_diagnosis(plan)
                if about_spec:

                    app = app_rows or first_app
                    if app:
                        self.log(f"[codegen] {node_id}: the diagnosis blames the spec ({len(about_spec)} line(s)) and names "
                                 f"{len(app)} application change(s); the spec goes to its author, the changes to the next implementer")
                    return ImplementResult("test_defect", written, "The acceptance spec contradicts the requirement text:\n" + "\n".join(about_spec)
                                           + f"\nLatest check output:\n{outcome.output[-2500:]}", round_index, app_plan="\n".join(app),
                                           defect_kind="seed-conflict" if (seed_note and seed_strong) else "")
                if blamed_rows and not app_rows:

                    plan_note = USER_TEXT["spec_unproven"]
                elif plan:
                    if blamed_rows:

                        plan = "\n".join(app_rows)
                        last_plan = plan
                    plan_note = USER_TEXT["plan"].format(plan=plan) + (USER_TEXT["spec_unproven"] if blamed_rows else "")

                    fixed: list[str] = []
                    for path in plan_paths(plan):
                        if self._is_test_path(path, test_files) or not self._writable(path):
                            if path not in fixed:
                                fixed.append(path)
                        elif (self.project_dir / path).is_file() and path not in self._force_include:
                            self._force_include.append(path)
                    if fixed:
                        plan_note += USER_TEXT["plan_fixed"].format(files=", ".join(fixed))
            user_prompt = self._user(node, test_files, outcome, written, (extra_context + plan_note + analysis_note + refusal_note + seed_note + lock_note).strip(), repair)
            if repair and setting_on("repair_prefix_shared", True):

                shared = self._shared_writer_prompt(node, outcome, written, extra_context, plan_note + analysis_note + refusal_note,
                                                    seed_note + lock_note)
                if shared is not None:
                    user_prompt = shared
            self._diag_prefix = None

            write_cap = (reasoning_cap_chars() if (thinking and setting_on("reasoning_cap_gen", True)
                                                   and (not repair or CodegenImplementer.repair_mode_broken)) else None)
            try:
                message = self._chat(
                    [
                        {"role": "system", "content": system_prompt()},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    tag=f"codegen:{node_id}:{'repair' if repair else 'gen'}{round_index}",
                    thinking=thinking,

                    expect_marker=None if thinking else (ANSWER_MARKERS, PROSE_LIMIT_CHARS),
                    **({"reasoning_cap": write_cap} if write_cap else {}),
                )
            except QuotaExhausted as exc:

                return ImplementResult("budget", written, f"key out of balance: {exc}", round_index)
            except CallDeadlineReached as exc:

                return ImplementResult("budget", written, f"call deadline reached: {str(exc)[:160]}", round_index, stop_reason="deadline")
            if self._past_call_deadline():

                self.log(f"[codegen] {node_id} round {round_index}: the answer arrived after the call deadline; not written, not checked")
                return ImplementResult("budget", written, "call deadline reached (the answer arrived after it; not written, not checked)",
                                       round_index, stop_reason="deadline")
            content = str(message.get("content") or "").replace("\r\n", "\n")
            if write_cap and self.llm.last_finish_reason == "reasoning_cap":

                self.log(f"[codegen] {node_id} round {round_index}: the answer's reasoning was cut at the cap with no content; "
                         "handled as an all-thinking answer (finish=length)")
                self.llm.last_finish_reason = "length"
            reask_limit = cutoff_reask_chars()
            if (reask_limit and not thinking and self.llm.last_finish_reason == "aborted"
                    and round_index not in reasked_rounds):

                reasked_rounds.add(round_index)
                content = self._reask_cutoff(node_id, round_index, user_prompt, content, max_tokens, reask_limit)
                if self._past_call_deadline():
                    self.log(f"[codegen] {node_id} round {round_index}: the call deadline came during the re-ask; not written, not checked")
                    return ImplementResult("budget", written, "call deadline reached (during the re-ask of a cut-off answer; not written, not checked)",
                                           round_index, stop_reason="deadline")
            files, defect = parse_output(content, self.llm.last_finish_reason == "stop")
            settle_note, edited, had_blocks = "", set(), False
            if edits_mode:
                main_finish = self.llm.last_finish_reason
                try:
                    settled = self._settle_answer(node_id, round_index, user_prompt, content, max_tokens, follow, test_files)
                finally:

                    self.llm.last_finish_reason = main_finish
                if settled is not None:
                    settled_files, edited, settle_note, had_blocks = settled
                    if settled_files is not None:
                        files = settled_files
                if self._past_call_deadline():
                    self.log(f"[codegen] {node_id} round {round_index}: the call deadline came during the follow-up calls of the answer; not written, not checked")
                    return ImplementResult("budget", written, "call deadline reached (during the follow-up calls of an answer; not written, not checked)",
                                           round_index, stop_reason="deadline")
            if need_note and analysis_note == need_note:

                analysis_note = ""

            need = NEED_BLOCK.search(content)
            requested = [p for p in plan_paths(need.group("body")) if self._writable(p) and (self.project_dir / p).is_file()] if need else []
            mentioned = [p for p in sorted(self._omitted) if p in content or Path(p).name in content]

            asked = [p for p in dict.fromkeys(requested + mentioned) if p not in self._shown]
            self._promote(asked)

            if defect and asked and not (need or FILE_REQUEST_WORDS.search(defect)):
                asked = []

            ask_free = not repair and gen_ask_free > 0 and not files and bool(asked)
            if defect and not files and asked and (round_index < limit or ask_free):
                if ask_free:
                    gen_ask_free -= 1
                    round_index -= 1
                self.log(f"[codegen] {node_id} round {round_index + (1 if ask_free else 0)}: the 'test defect' asks for files that were not shown {asked}; showing them next round"
                         + (f" (generation round, not counted; {gen_ask_free} such left)" if ask_free else ""))
                continue
            cut_off = self.llm.last_finish_reason == "aborted"
            if asked and not files and not defect and cut_off:

                self.log(f"[codegen] {node_id} round {round_index}: the cut-off analysis names files it was not shown {asked}; they are shown with the next attempt")
            elif asked and not files and not defect and (round_index < limit or ask_free):
                if ask_free:
                    gen_ask_free -= 1
                    round_index -= 1
                self.log(f"[codegen] {node_id} round {round_index + (1 if ask_free else 0)}: asked for {asked}; showing them next round"
                         + (f" (generation round, not counted; {gen_ask_free} such left)" if ask_free else ""))
                continue
            if need and not asked and not files and not defect and not cut_off and need_free and round_index < limit:

                need_free -= 1
                round_index -= 1

                analysis_note = need_note = (USER_TEXT["plan"].format(plan=last_plan) if (repair and last_plan) else "") + self._need_note(need.group("body"), test_files)
                self.log(f"[codegen] {node_id} round {round_index + 1}: the NEED FILES request names nothing that can be shown; told why, asked again")
                continue
            if defect and not files:

                if not spec_evidence(defect, describe_node(node), test_files, own_output(outcome.output)):

                    api_files = api_test_evidence(defect, describe_node(node), test_files, own_output(outcome.output))
                    if api_files:
                        self.log(f"[codegen] {node_id} round {round_index}: a test defect in the API test file(s) {api_files} "
                                 "with verifiable quotes; handed to their author (the acceptance spec is not part of it)")
                        return ImplementResult("test_defect", written, api_defect_detail(defect, api_files, test_files), round_index,
                                               defect_files=list(api_files), defect_kind="api")
                    self.log(f"[codegen] {node_id} round {round_index}: a test defect without verifiable quotes is not accepted: {defect[:160]!r}")
                    if round_index >= limit:
                        return ImplementResult("budget", written, "repair rounds exhausted (last answer: an unproven test defect)", round_index + 1,
                                               stop_reason="rounds")
                    refusal_note = USER_TEXT["defect_unproven"]
                    if unproven_free:

                        unproven_free -= 1
                        round_index -= 1
                    continue
                if seed_note and seed_strong:

                    own_specs = ", ".join(Path(p).name for p in test_files if p.startswith("backend/test-e2e/"))
                    self.log(f"[codegen] {node_id} round {round_index}: a test defect about the shared data {seed_files} with "
                             "verifiable quotes; handed to the spec's author")
                    return ImplementResult("test_defect", written, SEED_DEFECT_HEAD.format(names=own_specs or "(none)", files=", ".join(seed_files),
                                                                                        flips=seed_flips) + defect,
                                           round_index, defect_kind="seed-conflict")
                return ImplementResult("test_defect", written, defect, round_index)
            if edits_mode and had_blocks and settle_note and not files and not defect:

                self.log(f"[codegen] {node_id} round {round_index}: none of the answer's blocks could be applied:\n{settle_note[:3000]}")
                if round_index < limit:
                    keep_plan = USER_TEXT["plan"].format(plan=last_plan) if last_plan else ""
                    analysis_note = keep_plan + USER_TEXT["edit_none"].format(report=settle_note)
                    if untouched_rounds < 2:
                        untouched_rounds += 1
                        round_index -= 1
                    continue
                return ImplementResult("budget", written, "repair rounds exhausted (the last answer's edits could not be applied)",
                                       round_index + 1, stop_reason="rounds")
            if not files:
                self.log(f"[codegen] {node_id} round {round_index}: no files in output (finish={self.llm.last_finish_reason}); head: {content[:300]!r}")
                if outcome.passed and content.strip():

                    self.log(f"[codegen] {node_id}: the check is green and the answer changes nothing; done")
                    return ImplementResult("passed", written, "already green: nothing to change", round_index + 1)
                empty_rounds += 1
                if self.llm.last_finish_reason == "length" and not content.strip() and not repair and thinking and not gen0_thinking_off:

                    gen0_thinking_off = True
                    CodegenImplementer.all_thinking_batches.add((str(self.project_dir), node_id))
                    empty_rounds -= 1
                    round_index -= 1
                    self.log(f"[codegen] {node_id}: the first answer was all thinking and no content; asking again without thinking")
                    continue
                if self.llm.last_finish_reason == "length" and not content.strip() and repair and not CodegenImplementer.repair_mode_broken:

                    CodegenImplementer.repair_mode_broken = True
                    empty_rounds -= 1
                    self.log(f"[codegen] {node_id}: repair mode yields empty answers on this model; repair rounds switch to generation mode for the rest of the run")
                    if round_index < limit:
                        continue
                if self.llm.last_finish_reason == "aborted" and aborted_rounds < FREE_CUTOFFS:

                    aborted_rounds += 1
                    empty_rounds -= 1
                    round_index -= 1
                    analysis_note = self._decision_note(node_id, content, outcome)

                    extra_context = files_only_demand(extra_context)
                    continue
                if self.llm.last_finish_reason in ("length", "") and cut_free > 0:

                    finish = self.llm.last_finish_reason or "none"
                    cut_free -= 1
                    empty_rounds -= 1
                    round_index -= 1
                    if not repair and thinking:

                        gen0_thinking_off = True
                    if content.strip():
                        analysis_note = self._decision_note(node_id, content, outcome)
                    extra_context = files_only_demand(extra_context)
                    self.log(f"[codegen] {node_id} round {round_index + 1}: the answer was cut off (finish={finish}) "
                             f"before any file was complete; asked again, this round not counted ({cut_free} such left)")
                    continue
                if round_index < limit:
                    if len(content) > 3000:

                        analysis_note = self._decision_note(node_id, content, outcome)
                        continue

                    extra_context = files_only_demand(extra_context)
                    continue
                return ImplementResult("gave_up", written, "model produced no files", round_index, stop_reason="gave_up")
            wrote = 0
            round_paths: list[str] = []
            seed_changes: dict[str, str] = {}
            analysis_note = ""
            lock_note = ""
            touched_tests: list[str] = []
            refused: list[str] = []
            blind: list[str] = []

            undo: dict[Path, bytes | None] = {}
            written_before = list(written)
            undo_paths: dict[Path, str] = {}
            for path, content in files:
                if self._is_test_path(path, test_files):
                    self.log(f"[codegen] refused to write test file {path}")
                    touched_tests.append(path)
                    continue
                try:
                    target = safe_path(self.project_dir, path)
                except PathError as exc:
                    self.log(f"[codegen] refused {path}: {exc}")
                    refused.append(path)
                    continue
                if path in READ_ONLY_FILES and target.is_file():

                    self.log(f"[codegen] refused rewrite of read-only template file {path}")
                    refused.append(path)
                    continue
                if path in TEMPLATE_PLUMBING and target.is_file():

                    self.log(f"[codegen] refused rewrite of template plumbing {path}")
                    refused.append(path)
                    continue
                if path in BUILD_CONTRACT and target.is_file():
                    self.log(f"[codegen] refused rewrite of {path}: the build and start contract of the deliverable is fixed")
                    refused.append(path)
                    continue
                if not self._writable(path):

                    self.log(f"[codegen] refused write of hidden file {path}")
                    refused.append(path)
                    continue
                if target.is_file() and path not in edited and (path in self._omitted or path not in self._shown):

                    self.log(f"[codegen] refused blind rewrite of {path}; it will be shown next round")
                    blind.append(path)
                    self._promote([path])
                    continue
                try:
                    if target not in undo:
                        undo[target] = target.read_bytes() if target.is_file() else None
                        undo_paths[target] = path
                    write_text(target, content)
                except Exception:
                    self._roll_back(undo, node_id, round_index)
                    raise
                wrote += 1
                round_paths.append(path)
                if SHARED_DATA_FILE.search(path):
                    if path not in seed_now:

                        seed_now[path] = seed_hash(undo[target])
                        if seed_state:
                            seed_seen.setdefault(path, {})[seed_now[path]] = seed_state
                    new_hash = seed_hash(content)
                    if new_hash != seed_now[path]:
                        seed_changes[path] = new_hash
                    else:
                        seed_changes.pop(path, None)
                if path not in written:
                    written.append(path)
            self.log(f"[codegen] {node_id} round {round_index}: wrote {wrote} file(s): {[p for p, _ in files][:8]}")

            refusal_note = (USER_TEXT["refused_part"].format(files=", ".join(refused + blind + touched_tests)) if (wrote and (refused or blind or touched_tests)) else "")
            if settle_note and wrote:
                refusal_note += USER_TEXT["edit_note"].format(report=settle_note)
            if touched_tests and wrote == 0:

                self.log(f"[codegen] {node_id} round {round_index}: the answer only rewrote tests {touched_tests}; not a hand-over without evidence")
                if round_index >= limit:
                    return ImplementResult("budget", written, "repair rounds exhausted (last answer only rewrote tests)", round_index + 1, stop_reason="rounds")
                refusal_note = USER_TEXT["defect_unproven"]
                if unproven_free:
                    unproven_free -= 1
                    round_index -= 1
                continue
            if wrote == 0:

                if round_index < limit:
                    why = (f" These are fixed parts of the starter (database plumbing, package manifests, build configuration) and cannot "
                           f"be rewritten: {', '.join(refused)}. Put seeds into backend/src/database/init_db.js, logic into your own files, "
                           "and work with the dependencies that are installed." if refused else "")

                    keep_plan = USER_TEXT["plan"].format(plan=last_plan) if last_plan else ""
                    analysis_note = (keep_plan + "\nYour previous answer changed nothing on disk: every file in it was refused." + why +
                                     " Files you were not shown are shown now. Answer again with the files that really have to change."
                                     + (USER_TEXT["edit_note"].format(report=settle_note) if settle_note else ""))
                    if untouched_rounds < 2:
                        untouched_rounds += 1
                        round_index -= 1
                    continue
                return ImplementResult("budget", written, "repair rounds exhausted (the last answer wrote nothing)", round_index + 1, stop_reason="rounds")
            if wrote:

                lock_action = ""
                try:
                    lock_action, lock_note = self._seed_lock(node, round_index, undo, undo_paths, lock_free > 0)
                except Exception as exc:
                    lock_action, lock_note = "raised", ""
                    try:
                        self.log(f"[seed-lock] {node_id} round {round_index}: not checked ({type(exc).__name__}: {str(exc)[:160]})")
                    except Exception:
                        pass
                if lock_action in ("reverted", "revert_failed", "raised", "restored_part"):
                    self._books_from_disk(undo, undo_paths, written_before, written, seed_now, seed_changes)
                if lock_action == "reverted":

                    lock_free -= 1
                    round_index -= 1
                    last_plan_signature = ""
                    self.log(f"[codegen] {node_id} round {round_index + 1}: the answer was reverted whole by the seed lock; "
                             f"not checked, this round not counted ({lock_free} such left)")
                    continue
                if lock_action == "restored_part":

                    lock_free -= 1
                    round_index -= 1
                    part_round = True
                    self.log(f"[codegen] {node_id} round {round_index + 1}: the answer was put back in part by the seed "
                             f"lock; the rest is checked, this round not counted ({lock_free} such left)")
            if self._past_call_deadline():

                not_restored = self._roll_back(undo, node_id, round_index, why="the call deadline passed while the answer was written")
                if not not_restored:
                    written[:] = written_before
                    return ImplementResult("budget", written, "call deadline reached while the answer was written; its files were put back",
                                           round_index, stop_reason="deadline")

                still_changed = [undo_paths[target] for target in undo if self._differs_from(target, undo[target])]
                written[:] = written_before + [path for path in still_changed if path not in written_before]
                return ImplementResult("budget", written, "call deadline reached while the answer was written; "
                                       f"{len(still_changed)} of its file(s) could not be put back and stay unchecked: {still_changed[:8]}",
                                       round_index, stop_reason="deadline")
            try:
                outcome = self.check()
                if not outcome.passed and getattr(outcome, "infra", ""):

                    outcome = settle_install_failure(self.check, outcome, self.project_dir, deadline=self.deadline, log=self.log)
            except Exception:
                self._roll_back(undo, node_id, round_index)
                raise
            if not outcome.passed and getattr(outcome, "infra", ""):

                self.log(f"[codegen] {node_id} round {round_index}: infrastructure failure ({outcome.infra}), not the code; "
                         "no further round")
                return ImplementResult("budget", written, f"infrastructure failure, not the code: {outcome.infra}", round_index + 1)
            advisory_lint = None if outcome.passed else lint_only_failure(outcome.output)
            if advisory_lint is not None:

                self.log(f"[codegen] {node_id} round {round_index}: the e2e specs are green; lint findings alone do not block (advisory):\n{advisory_lint[:600]}")
                outcome = TestOutcome(True, outcome.output, ran=outcome.ran, skipped=outcome.skipped)
            self.log(f"[codegen] {node_id} round {round_index}: check {'PASSED' if outcome.passed else 'failing'}")
            checks_done += 1
            if not outcome.passed:

                now_state = own_or_earlier(outcome.output, test_files)
                data_written = sorted(seed_changes)
                returned = [p for p in data_written if now_state and seed_seen.get(p, {}).get(seed_changes[p]) == now_state]
                flipped = bool(data_written and now_state and seed_state and now_state != seed_state)
                signature_now = red_signature(outcome.output)
                repeated = signature_now in seed_sigs
                seed_sigs.add(signature_now)
                if flipped:
                    seed_flips += 1
                    seed_returns += 1 if returned else 0
                    seed_files = list(dict.fromkeys(seed_files + data_written))
                    self.log(f"[codegen] {node_id} round {round_index}: the change of {data_written} turned "
                             + ("this batch's spec green and specs of earlier requirements red" if now_state == "G" else "this batch's spec red again")
                             + f" (shared-data flip {seed_flips}" + (f", back to an earlier content of {returned}: return {seed_returns})" if returned else ")"))
                for p in data_written:
                    seed_now[p] = seed_changes[p]
                    if now_state:
                        seed_seen.setdefault(p, {})[seed_changes[p]] = now_state
                if now_state:
                    seed_state = now_state
                if seed_stage and seed_strong and flipped and returned and repeated:
                    self.log(f"[codegen] {node_id} round {round_index}: seed-conflict: {returned} changed back again after the "
                             f"note ({seed_flips} shared-data flips, {seed_returns} back to an earlier content); no further round")
                    return ImplementResult("budget", written, f"seed-conflict: the shared data {', '.join(seed_files or data_written)} "
                                           f"was changed back and forth ({seed_flips} flips between this batch's spec and earlier specs)",
                                           round_index + 1, stop_reason="seed-conflict")
                if seed_stage == 1:
                    seed_stage, seed_note = 2, ""
                elif not seed_strong and seed_flips >= SEED_FLIPS_ARM and seed_returns >= SEED_RETURNS_ARM:
                    seed_stage, seed_strong = 1, True
                    seed_note = USER_TEXT["seed_conflict"].format(files=", ".join(seed_files), flips=seed_flips)
                    self.log(f"[codegen] {node_id}: {seed_flips} shared-data flips on {seed_files}, {seed_returns} back to an earlier "
                             "content; the next round is told once not to change it (or to report its spec)")
                elif seed_stage == 0 and not seed_hinted and seed_flips >= SEED_FLIPS_ARM:
                    seed_stage, seed_hinted = 1, True
                    seed_note = USER_TEXT["seed_hint"].format(files=", ".join(seed_files), flips=seed_flips)
                    self.log(f"[codegen] {node_id}: {seed_flips} shared-data flips on {seed_files} without a return to an earlier "
                             "content; the next round gets the shared-data hint once (no stop)")
            stop = "" if outcome.passed else str(getattr(outcome, "stop", "") or "")
            if stop:

                self.log(f"[codegen] {node_id} round {round_index}: {stop}; no further round")
                return ImplementResult("budget", written, stop, round_index + 1)
            if not outcome.passed and uncounted < self.start_failure_free and app_did_not_start(outcome.output):

                if not part_round:
                    uncounted += 1
                    limit += 1
                last_plan_signature = ""
                self.log(f"[codegen] {node_id} round {round_index}: the application did not start (no test ran); the start error "
                         f"goes back and this round is not counted ({self.start_failure_free - uncounted} such left)")
                continue
            if not outcome.passed:
                blocking = blocking_output(outcome.output)

                failure_lines = [l for l in blocking.splitlines() if any(k in l for k in ("×", "FAIL", "Error", "expect(", "Locator:", "Timeout", "›"))
                                 and not l.startswith("[page] ")]
                self.log("[codegen] failures:\n" + "\n".join(failure_lines[:20]))

                signature = failure_signature(failure_lines)
                stagnant = stagnant + 1 if (repair and signature and signature == last_signature) else 0
                repeats = repeats + 1 if (signature and signature == last_signature) else 1

                progress, progress_why = tests_progress.observe(blocking, test_files, signature)
                last_stuck = (not progress) and tests_progress.last_stuck
                if stalled and repeats == 1:

                    stalled = False
                    self.log(f"[codegen] {node_id}: the failures changed after the stall; extra rounds possible again when a stuck test moves or a test turns green")

                routing = self.route_raw_waits and setting_on("raw_wait_to_author") and (self.deadline is None or clock.now() < self.deadline)
                waits = spec_unexplained_waits(blocking, test_files) if routing else []
                raw_waits = {key: raw_waits.get(key, 0) + 1 for key, _text in waits}
                due = [(key, text) for key, text in waits if raw_waits[key] >= RAW_WAIT_CHECKS
                       and (str(self.project_dir), key[0]) not in CodegenImplementer.raw_wait_routed]
                if due:

                    thrown = [item for item in spec_thrown_errors(blocking, test_files) if item not in [text for _key, text in due]]
                    self.log(f"[codegen] {node_id}: a raw Playwright wait in the spec timed out {RAW_WAIT_CHECKS} checks in a row "
                             f"and names no expectation: {[text for _key, text in due][:3]}; handing it to the test author"
                             + (f" (with the spec's own errors {thrown[:3]})" if thrown else ""))
                    return ImplementResult(
                        "test_defect", written,
                        RAW_WAIT_DEFECT.format(checks=RAW_WAIT_CHECKS) + "\n".join(text for _key, text in due[:10])
                        + ("\nThe acceptance spec also throws by itself -- the first stack frame of each error below is in the spec "
                           "file:\n" + "\n".join(thrown[:10]) if thrown else "")
                        + f"\nLatest check output:\n{blocking[-2500:]}",
                        round_index + 1,
                        routed=sorted({(str(self.project_dir), key[0]) for key, _text in due}),
                    )
                if repeats >= 3:

                    thrown = spec_thrown_errors(blocking, test_files)
                    if thrown:
                        self.log(f"[codegen] {node_id}: the same failure {repeats} checks in a row, and the spec itself throws: {thrown[:3]}; handing it to the test author")
                        return ImplementResult(
                            "test_defect", written,
                            "The acceptance spec throws by itself -- the first stack frame of each error below is in the spec file, "
                            f"and the same failure came back in {repeats} checks in a row:\n" + "\n".join(thrown[:10])
                            + f"\nLatest check output:\n{blocking[-2500:]}",
                            round_index + 1,
                        )
                    if not stalled:
                        self.log(f"[codegen] {node_id}: the same failure {repeats} checks in a row; no extra rounds from here on")
                    stalled = True

                if stagnant >= 2 and SETTINGS.get("repair_diagnosis", True) and round_index < limit:

                    stagnation_pending = USER_TEXT["stagnation"].format(failures="\n".join(failure_lines[:8])[:1500])
                    stagnant = 0
                    self.log(f"[codegen] {node_id}: two repair rounds left the failures unchanged; the next diagnosis must prove a spec defect or change course")
                elif stagnant >= 2 and not SETTINGS.get("repair_diagnosis", True):

                    return ImplementResult(
                        "test_defect",
                        written,
                        "Two consecutive repair rounds left exactly the same failures, so the tests are suspected to be defective "
                        f"(syntax error, ambiguous locator, or contradiction with the requirement):\n{outcome.output[-2500:]}",
                        round_index + 1,
                    )
                if round_index == limit and limit < self.max_rounds + self.extra_rounds + uncounted and wrote:

                    if progress:
                        limit += 1
                        self.log(f"[codegen] {node_id}: failures are still changing ({progress_why}); granting extra round {limit}")
                    else:
                        self.log(f"[codegen] {node_id}: no extra round: {progress_why}")
                elif round_index == limit and wrote and progress_granted < self.progress_rounds:

                    room = self._progress_round_room(started, checks_done) if progress else ""
                    if progress and not room:
                        limit += 1
                        progress_granted += 1
                        self.log(f"[codegen] {node_id}: the round cap is reached and the tests still make progress ({progress_why}); "
                                 f"progress round {progress_granted} of {self.progress_rounds} (round {limit})")
                    else:
                        self.log(f"[codegen] {node_id}: no progress round: {room or progress_why}")
                last_signature = signature
            if outcome.passed:
                return ImplementResult("passed", written, "checks green", round_index + 1, last_check=outcome.output)
        return ImplementResult("budget", written, "repair rounds exhausted", round_index + 1,
                              stop_reason="stuck" if last_stuck else "rounds")
