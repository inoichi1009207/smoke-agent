from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import coverage

CASES: list = []

def case(function):
    CASES.append(function)
    return function

ROLES = "Every account holds one of the roles Viewer, Commenter, Reviewer, Editor, or Manager. "
BASE = ("The draft page has a button named “Publish”. Publishing shows the message “Draft published”. The new draft remains "
        "listed after a reload of the page. An empty title is rejected with “Title is required”. The list shows the "
        "owner of each draft. ")
SHORT = ("The draft page has a button named “Publish”. Publishing shows the message “Draft published”. The new draft remains "
         "listed after a reload of the page. ")

def _node(description):
    return {"id": "REQ-1", "name": "x", "members": ["REQ-1"],
            "_leaves": [{"id": "REQ-1", "name": "Drafts", "description": description, "scenarios": []}]}

def _derived(node, kind=None):
    return [t for _l, numbered in coverage.ledger(node) for _n, t in numbered
            if coverage.TAG.match(t) and (kind is None or coverage._kind(t) == kind)]

@case
def a_permission_refuses_nobody_by_itself():
    for extra in ("Editors and Reviewers may add comments.", "An Editor or Manager may archive a draft."):
        for roles in ("", ROLES):
            node = _node(roles + BASE + extra)
            assert not _derived(node, "refusal"), (extra, roles, _derived(node))

@case
def a_permission_refuses_only_the_roles_a_following_sentence_refuses():
    node = _node(ROLES + BASE + "Editors and Managers may add comments; Viewer may only view drafts.")
    refusals = _derived(node, "refusal")

    assert [t.split("]")[0] for t in refusals] == ["[refusal: Viewer"], refusals
    assert refusals[0].startswith("[refusal: Viewer] Editor, Manager may add comments"), refusals

@case
def an_exclusive_restriction_keeps_its_refusals_but_no_qualifier_counter_example():
    node = _node(ROLES + BASE + "Only Editors may add comments to the current draft.")
    refusals = [t.split("]")[0] for t in _derived(node, "refusal")]
    assert refusals == ["[refusal: Reviewer", "[refusal: Commenter", "[refusal: Viewer"], refusals
    assert not _derived(node, "counter-example"), _derived(node)
    assert not _derived(_node(ROLES + BASE + "Not only Editors may add comments."), "refusal")

@case
def a_counter_example_only_from_a_sentence_admitting_only_the_qualified_target():
    after = _derived(_node(ROLES + BASE + "An Editor may add comments only to a current draft."), "counter-example")
    assert len(after) == 1 and after[0].startswith("[counter-example: not current draft]"), after
    passive = _node(ROLES + BASE + "Only existing labels can be applied to a draft.")
    assert [t.split("]")[0] for t in _derived(passive, "counter-example")] == ["[counter-example: not existing labels"], \
        _derived(passive)
    members = _node(ROLES + BASE + "Only current team members can be assigned to a draft.")
    assert not _derived(members, "refusal"), _derived(members)
    assert _derived(members, "counter-example"), _derived(members)
    for plain in ("An Editor may add comments to a current draft.", "Only an Editor may move a draft to an archived folder."):
        assert not _derived(_node(ROLES + BASE + plain), "counter-example"), (plain, _derived(_node(ROLES + BASE + plain)))

REFUSAL_DESC = ROLES + BASE + "Only Editors may add comments; Viewer and Commenter may only view drafts."

def _spec(body: str, before: str = "") -> str:
    node = _node(REFUSAL_DESC)
    every = ", ".join(f"S{n}" for _l, numbered in coverage.ledger(node) for n, _t in numbered)
    return (f'const {{ test, expect, see, notSee, click }} = require("../e2e-support/outsider");\n{before}'
            f'// covers {every}\ntest("REQ-1: comments", async ({{ page }}) => {{\n  await page.goto("/");\n{body}\n}});\n')

@case
def equivalent_absence_assertions_cover_a_refusal():
    node = _node(REFUSAL_DESC)
    refusals = [n for _l, numbered in coverage.ledger(node) for n, t in numbered if coverage._kind(t) == "refusal"]
    assert len(refusals) == 3, refusals
    for body in ('  await expect.poll(() => page.getByRole("button", { name: "Add comment" }).count()).toBe(0);',
                 '  const rows = await page.getByRole("button", { name: "Add comment" }).all();\n  expect(rows).toHaveLength(0);',
                 '  const res = await page.request.post("/api/comments", { data: { text: "x" } });\n  expect(res.ok()).toBe(false);'):
        assert coverage.uncovered(node, _spec(body)) == [], (body, coverage.uncovered(node, _spec(body)))

    positive = _spec('  await see(page, "Add (comment)");\n  await click(page, { role: "button", name: "Publish" });')
    assert [n for _l, n, _t in coverage.uncovered(node, positive)] == refusals, coverage.uncovered(node, positive)

@case
def a_call_the_check_cannot_see_into_keeps_the_mark():
    node = _node(REFUSAL_DESC)
    helper = "async function commentControlGone(page) {\n  await notSee(page, \"Add comment\");\n}\n"
    spec = _spec("  await commentControlGone(page);", before=helper)
    assert coverage.uncovered(node, spec) == [], coverage.uncovered(node, spec)
    imported = _spec("  await signInAs(page, \"viewer\");\n  await see(page, \"Drafts\");")
    assert coverage.uncovered(node, imported) == [], coverage.uncovered(node, imported)

QUOTA_DESC = (ROLES + SHORT + "Only Manager may move a draft, and only to an archived folder. "
              "Only Manager may copy a draft, and only into an existing folder.")

@case
def prerequisite_marks_neither_spend_nor_widen_the_quarter():
    node = _node(QUOTA_DESC)
    found = coverage.ledger(node)
    tagged = [n for _l, numbered in found for n, t in numbered if coverage._kind(t) in ("refusal", "counter-example")]
    plain = [n for _l, numbered in found for n, t in numbered if n not in tagged]
    kinds = sorted({coverage._kind(t) for _l, numbered in found for _n, t in numbered if coverage.TAG.match(t)})
    assert len(plain) == 5 and len(tagged) == 5 and kinds == ["counter-example", "refusal"], (plain, tagged, found)
    spec = (f"// not testable: {', '.join(f'S{n}' for n in tagged)} -- no stated account or target\n"
            f"// covers {', '.join(f'S{n}' for n in plain)}\n"
            'test("REQ-1: x", async ({ page }) => {\n  await page.goto("/");\n});\n')
    assert coverage.refused_marks(node, spec) == [] and coverage.uncovered(node, spec) == [], coverage.refused_marks(node, spec)
    assert sorted(coverage.not_testable(node, spec)) == tagged

    texts = dict((n, t) for _l, numbered in found for n, t in numbered)
    loose = [n for n in plain if not coverage.testable_by_construction(texts[n])]
    three = plain[:3]
    spec3 = (f"// not testable: {', '.join(f'S{n}' for n in three + tagged)} -- x\n"
             f"// covers {', '.join(f'S{n}' for n in plain if n not in three)}\n"
             'test("REQ-1: x", async ({ page }) => {\n  await page.goto("/");\n});\n')
    refused = coverage.refused_marks(node, spec3)
    assert sorted(n for n, _r in refused) == three and all("3 of 5 marked, at most 2" in r for _n, r in refused), (refused, loose)

@case
def the_api_note_names_the_sources():
    from agent import test_author
    note = test_author.refusal_note(_node(REFUSAL_DESC))
    assert note.startswith(coverage.REFUSAL_API_HEAD) and "[refusal: Viewer]" in note, note
    assert "not testable, outside the quarter" in coverage.DERIVED_RULES
