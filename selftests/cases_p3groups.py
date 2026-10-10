from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import interaction_probes as ip

CASES = []

def case(function):
    CASES.append(function)
    return function

RULE = ("- id: R name: Edit description: Only Editor may edit the note using the button named “Edit note”. scenarios: "
        "- keyword: GIVEN content: 'Seed values: account `rita`, password `Pw-123456789!`; `rita` has Reader role on "
        "the note. ")

def p3(extra: str = "") -> dict:
    jobs = ip.build_jobs(RULE + extra + "'")["p3"]
    assert len(jobs) == 1 and jobs[0]["allowed"] == ["Editor"], jobs
    return jobs[0]

def users(job: dict) -> list[str]:
    return [r["user"] for r in job["restricted"]]

@case
def a_direct_role_alone_stays_a_negative_sample():
    job = p3()
    assert job["restricted"] == [{"user": "rita", "password": "Pw-123456789!", "roles": ["Reader"]}], job
    assert "skipped" not in job

@case
def a_team_granted_an_allowed_role_is_another_grant_path():
    job = p3("`rita` is a member of team `writers`, which is granted the Editor role on the note.")
    assert users(job) == [] and job["skipped"][0]["user"] == "rita", job
    assert "`writers`" in job["skipped"][0]["why"]

    job = p3("`rita` belongs to the `writers` group. The `writers` group has Editor permission on every note.")
    assert users(job) == [] and job["skipped"], job

@case
def a_group_holding_no_allowed_role_keeps_the_sample():
    job = p3("`rita` is a member of team `viewers`. Team `viewers` has Reader role on the note.")
    assert users(job) == ["rita"] and "skipped" not in job, job
    job = p3("`rita` is a member of team `core`.")
    assert users(job) == ["rita"], job

@case
def an_unresolved_membership_or_grant_skips_the_account():
    job = p3("`rita` is a member of the writers team.")
    assert users(job) == [] and "names no group" in job["skipped"][0]["why"], job
    job = p3("`rita` is a member of team `ops`. Team `ops` is granted access to the note.")
    assert users(job) == [] and "no role names" in job["skipped"][0]["why"], job

@case
def a_stated_role_implication_is_another_grant_path():
    text = ("- id: R name: Edit description: Only Admin may delete the repository using the button named “Delete "
            "repository”. Organization Owners act with Admin permission. scenarios: - keyword: GIVEN content: 'Seed "
            "values: account `olga`, password `Pw-123456789!`; `olga` has Owner role in the organization.'")
    job = ip.build_jobs(text)["p3"][0]
    assert users(job) == [] and job["skipped"][0]["user"] == "olga", job
    job = ip.build_jobs(text.replace(" Organization Owners act with Admin permission.", ""))["p3"][0]
    assert users(job) == ["olga"], job
    member = text.replace(" Organization Owners act with Admin permission.", "").replace(
        "in the organization.", "in the organization. `olga` is a member of organization `acme`. Organization `acme` has "
        "Admin role on the repository.")
    job = ip.build_jobs(member)["p3"][0]
    assert users(job) == [] and "`acme`" in job["skipped"][0]["why"], job

@case
def a_skipped_account_is_logged_as_a_diagnostic():
    job = dict(p3("`rita` is a member of team `writers`, which is granted the Editor role on the note."), id="p3-0")
    lines = []
    found = ip.p3_findings([job], [{"id": "p3-0", "visitor": [], "accounts": []}], log=lines.append)
    assert found == [] and any("`rita` not probed as restricted" in line and "diagnostic" in line for line in lines), lines

@case
def a_restricted_account_seeing_the_control_is_still_reported():
    job = dict(p3("`rita` is a member of team `viewers`. Team `viewers` has Reader role on the note."), id="p3-0")
    res = {"id": "p3-0", "visitor": [], "accounts": [{"user": "rita", "roles": ["Reader"], "signedIn": True,
                                                     "seen": [{"route": "/notes/1", "role": "button", "name": "Edit note"}]}]}
    found = ip.p3_findings([job], [res], log=lambda m: None)
    assert len(found) == 1 and "Signed in as `rita`" in found[0]["problem"], found

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} p3groups cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
