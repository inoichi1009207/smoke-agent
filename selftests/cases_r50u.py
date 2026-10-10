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

def _node(description):
    return {"id": "REQ-1", "name": "x", "members": ["REQ-1"],
            "_leaves": [{"id": "REQ-1", "name": "Drafts", "description": description, "scenarios": []}]}

def _refusals(text):
    return [t for _l, numbered in coverage.ledger(_node(text)) for _n, t in numbered
            if coverage.TAG.match(t) and coverage._kind(t) == "refusal"]

@case
def a_permission_plus_a_refusal_of_another_act_derives_nothing():
    for extra in ("Editors and Reviewers may add comments. Viewers cannot delete projects.",
                  "Editors and Reviewers may add comments. Viewers cannot add projects.",
                  "Editors and Reviewers may add comments. Viewers cannot add comments or delete projects.",
                  "Editors and Reviewers may add comments. Viewers can only add comments.",
                  "Editors and Reviewers may add comments. Viewers may only view and comment on drafts."):
        for roles in ("", ROLES):
            assert not _refusals(roles + BASE + extra), (extra, roles, _refusals(roles + BASE + extra))

@case
def other_roles_refused_another_act_derives_nothing():
    lead = ROLES + BASE + "Viewers may only view drafts. Editors and Reviewers may add comments. "
    assert not _refusals(lead + "All other roles cannot delete projects."), _refusals(lead + "All other roles cannot delete projects.")
    got = {t.split("]")[0] for t in _refusals(lead + "All other roles cannot add comments.")}
    assert {"[refusal: Viewer", "[refusal: Commenter", "[refusal: Manager"} <= got, got

@case
def a_permission_plus_a_refusal_of_the_same_act_refuses_that_role():
    for extra in ("Editors and Reviewers may add comments. Viewers cannot add comments.",
                  "Editors and Reviewers may add comments to any draft. Viewers may not add comments.",
                  "Editors and Reviewers may add comments. Viewers cannot do so.",
                  "Editors and Reviewers may add comments. Viewers may only view drafts."):
        for roles in ("", ROLES):
            got = _refusals(roles + BASE + extra)
            assert [t.split("]")[0] for t in got] == ["[refusal: Viewer"], (extra, roles, got)
            assert "Editor, Reviewer may add comments" in got[0], got

@case
def an_exclusive_restriction_is_not_bound_to_the_next_act():
    got = _refusals(BASE + "Only Editors may add comments. Viewers cannot delete projects.")
    assert got == ["[refusal: Viewer] only Editor may add comments"], got
    got = _refusals(ROLES + BASE + "Only Editors may add comments. Viewers cannot delete projects.")
    assert {t.split("]")[0] for t in got} == {"[refusal: Reviewer", "[refusal: Commenter", "[refusal: Viewer"}, got

@case
def same_act_helper_is_conservative():
    same = coverage._same_act
    assert same("add comments", "cannot", "add comments.")
    assert same("add comments to a draft", "may not", "add comments")
    assert not same("add comments", "cannot", "add comments to published drafts")
    assert not same("add comments", "cannot", "delete projects")
    assert not same("add comments", "cannot", "be assigned")
    assert same("archive a draft", "cannot", "do that")
    assert same("archive a draft", "may only", "view drafts")
    assert not same("archive a draft", "may only", "view or archive drafts")
    assert not same("archive a draft", "can only", "archive their own drafts")
