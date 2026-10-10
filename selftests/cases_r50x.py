from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.testrun import static_test_defects, vacuous_absence_checks, vacuous_absence_hints
from cases_v144l import BATCH, REL, spec

CASES: list = []

def case(function):
    CASES.append(function)
    return function

CREATE_DELETE = "  await fill(page, 'Title', name);\n  await click(page, 'Create');\n  await click(page, 'Delete');\n"
OPAQUE = [
    ("  const name = String.fromCharCode(67, 101, 100, 97, 114);\n", "Cedar"),
    ("  const name = String.fromCodePoint(67, 101, 100, 97, 114);\n", "Cedar"),
    ("  const name = atob('Q2VkYXI=');\n", "Cedar"),
    ("  const name = Buffer.from('Q2VkYXI=', 'base64').toString();\n", "Cedar"),
    ("  const name = 'xCedarx'.slice(1, 6);\n", "Cedar"),
    ("  const name = 'xxCedar Parkxx'.substring(2, 12);\n", "Cedar Park"),
    ("  const name = 'C-e-d-a-r'.replace(/-/g, '');\n", "Cedar"),
    ("  const name = 'radeC'.split('').reverse().join('');\n", "Cedar"),
    ("  const code = String.fromCharCode(67, 101, 100, 97, 114);\n  const name = `${code} lodge`;\n", "Cedar lodge"),
    ("  const name = new Date(2024, 8, 1).toLocaleDateString('en-US', { month: 'long' });\n", "September"),
    ("  const name = (20825811).toString(36);\n", "Cedar"),
    ("  const name = `${new Date(2024, 8, 1)}`;\n", "Sun Sep"),
]

@case
def r50x_unevaluable_construction_is_advice_not_a_defect():
    for made, shown in OPAQUE:
        text = spec(made + CREATE_DELETE + f"  await notSee(page, '{shown}');")
        assert vacuous_absence_checks(REL, text, BATCH) == [], made
        hints = vacuous_absence_hints(REL, text, BATCH)
        assert len(hints) == 1 and "is NOT shown" in hints[0] and "reading cannot evaluate" in hints[0], (made, hints)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        body = OPAQUE[0][0] + CREATE_DELETE + "  await notSee(page, 'Cedar');"
        (folder / "REQ-5-1-2.spec.js").write_text(spec(body), encoding="utf-8")
        assert static_test_defects(Path(tmp), [REL], batch=BATCH) == ""

@case
def r50x_evaluated_constructions_keep_their_verdict():

    upper = "  const name = 'cedar'.toUpperCase();\n" + CREATE_DELETE + "  await notSee(page, 'CEDAR');"
    assert vacuous_absence_checks(REL, spec(upper), BATCH) == [] and vacuous_absence_hints(REL, spec(upper), BATCH) == []

    for made, shown in (("  const name = 'ab'.repeat(3);\n", "ababab"), ("  const name = 'ar'.padStart(5, 'Ced');\n", "Cedar")):
        text = spec(made + CREATE_DELETE + f"  await notSee(page, '{shown}');")
        hints = vacuous_absence_hints(REL, text, BATCH)
        assert vacuous_absence_checks(REL, text, BATCH) == [] and len(hints) == 1, (made, hints)
        assert "join of this file's own strings" in hints[0], hints

    for made in ("  const name = 'maple'.toLowerCase().toUpperCase();\n",
                 "  const name = 'note ' + Date.now() + ' ' + Math.random();\n",
                 "  const name = `note ${Date.now()}`.padEnd(20);\n"):
        text = spec(made + CREATE_DELETE + "  await notSee(page, 'Cedar');")
        problems = vacuous_absence_checks(REL, text, BATCH)
        assert len(problems) == 1 and "proves nothing" in problems[0], (made, problems)
        assert vacuous_absence_hints(REL, text, BATCH) == [], made

RANDOM = [

    "  const title = 'outsider-issue-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);\n",
    "  const title = (Date.now() * 7).toString(36);\n",
    "  const title = Math.floor(Math.random() * 1e6).toString(36).padStart(8, 'x');\n",
    "  const title = crypto.randomUUID().slice(0, 8);\n",
    "  const title = performance.now().toString(36).replace('.', '');\n",
    "  const title = `note ${Math.random().toString(36).slice(2)}`;\n",
]

@case
def r50x_random_or_clock_chains_are_no_source():
    for made in RANDOM:
        text = spec(made + "  await fill(page, 'Title', title);\n  await click(page, 'Create');\n"
                    "  await fill(page, 'Title', '   ');\n  await notSee(page, 'outsider-issue-blank');")
        problems = vacuous_absence_checks(REL, text, BATCH)
        assert len(problems) == 1 and "proves nothing" in problems[0], (made, problems)
        assert vacuous_absence_hints(REL, text, BATCH) == [], made

    for made in ("  await fill(page, 'Title', String.fromCharCode(67, 101, 100, 97, 114) + Math.random().toString(36));\n",
                 "  const name = String.fromCharCode(67, 101, 100, 97, 114);\n  const tag = Math.random().toString(36).slice(2);\n"):
        text = spec(made + "  await notSee(page, 'Cedar');")
        hints = vacuous_absence_hints(REL, text, BATCH)
        assert vacuous_absence_checks(REL, text, BATCH) == [] and len(hints) == 1, (made, hints)
        assert "(`String.fromCharCode(67" in hints[0], hints

@case
def r50x_constructions_named_in_strings_or_comments_do_not_count():
    body = ("  // String.fromCharCode(67) or atob() or .slice( would make it\n"
            "  await fill(page, 'Title', 'use .slice(1) and new Date() here');\n"
            "  await notSee(page, 'Cedar');")
    problems = vacuous_absence_checks(REL, spec(body), BATCH)
    assert len(problems) == 1 and "proves nothing" in problems[0], problems
    assert vacuous_absence_hints(REL, spec(body), BATCH) == []
