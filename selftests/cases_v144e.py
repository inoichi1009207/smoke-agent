from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["ARC_IPROBE_P10"] = "1"

from agent import interaction_probes as ip

CASES = []

def case(function):
    CASES.append(function)
    return function

HEAD = ("- id: R name: Comment description: Only Write may comment on the pull request using the text box named "
        "“Comment” and the button named “Add comment”. scenarios: - keyword: GIVEN content: "
        "'Seed values: account `bob`, password `Pw-123456789!`; `bob` has Write role on the repository. account "
        "`dave`, password `Pw-123456789!`; `dave` has Read role on the repository. ")

def build(extra: str = "") -> list[dict]:
    return ip.build_jobs(HEAD + extra + "'")["p10"]

def job_one(extra: str = "") -> dict:
    jobs = build(extra)
    assert len(jobs) == 1, jobs
    return jobs[0]

@case
def off_by_default():
    os.environ.pop("ARC_IPROBE_P10", None)
    try:
        assert ip.p10_enabled() is False
        assert ip.build_jobs(HEAD + "'")["p10"] == []
    finally:
        os.environ["ARC_IPROBE_P10"] = "1"
    assert ip.p10_enabled() is True

@case
def a_free_text_act_builds_one_job():
    job = job_one()
    assert job["field"] == "Comment" and job["submit"] == "Add comment", job
    assert job["allowed"] == ["Write"], job
    assert job["capture"] == {"user": "bob", "password": "Pw-123456789!", "roles": ["Write"]}, job
    assert job["restricted"] == [{"user": "dave", "password": "Pw-123456789!", "roles": ["Read"]}], job
    assert "skipped" not in job

@case
def an_act_with_no_free_text_box_builds_nothing():

    text = ("- id: R name: Approve description: Only Maintain may approve the pull request using the button named "
            "“Submit review”. scenarios: - keyword: GIVEN content: 'Seed values: account `carol`, password "
            "`Pw-123456789!`; `carol` has Maintain role on the repository. account `dave`, password `Pw-123456789!`; "
            "`dave` has Read role on the repository.'")
    assert ip.build_jobs(text)["p10"] == []

@case
def no_allowed_capture_account_builds_nothing():

    text = ("- id: R name: Comment description: Only Write may comment using the text box named “Comment” "
            "and the button named “Add comment”. scenarios: - keyword: GIVEN content: 'Seed values: account "
            "`dave`, password `Pw-123456789!`; `dave` has Read role on the repository.'")
    assert ip.build_jobs(text)["p10"] == []

@case
def no_restricted_account_builds_nothing():

    text = ("- id: R name: Comment description: Only Write may comment using the text box named “Comment” "
            "and the button named “Add comment”. scenarios: - keyword: GIVEN content: 'Seed values: account "
            "`bob`, password `Pw-123456789!`; `bob` has Write role on the repository.'")
    assert ip.build_jobs(text)["p10"] == []

@case
def a_group_granted_allowed_role_is_not_a_restricted_account():

    job = job_one("account `erin`, password `Pw-123456789!`; `erin` has Read role on the repository. `erin` is a "
                  "member of team `writers`, which is granted the Write role on the repository. ")
    assert [r["user"] for r in job["restricted"]] == ["dave"], job
    assert job["skipped"][0]["user"] == "erin" and "`writers`" in job["skipped"][0]["why"], job

def _job() -> dict:
    return dict(job_one(), id="p10-0")

@case
def a_hidden_control_whose_replay_took_effect_is_reported():
    res = {"id": "p10-0", "route": "/pr/1", "capture": {"user": "bob", "signedIn": True, "tookEffect": True},
           "accounts": [{"user": "dave", "roles": ["Read"], "signedIn": True, "controlHidden": True,
                         "via": "replay", "status": 201, "value": "zq1p0r0", "tookEffect": True}]}
    found = ip.p10_findings([_job()], [res], log=lambda m: None)
    assert len(found) == 1, found
    assert "correctly not shown" in found[0]["problem"] and "does not check the role" in found[0]["problem"], found
    assert "server" in found[0]["fix"].lower()

@case
def a_shown_control_whose_write_took_effect_is_reported():
    res = {"id": "p10-0", "route": "/pr/1", "capture": {"user": "bob", "signedIn": True, "tookEffect": True},
           "accounts": [{"user": "dave", "roles": ["Read"], "signedIn": True, "controlHidden": False,
                         "via": "ui", "status": 200, "value": "zq1p0r0", "tookEffect": True}]}
    found = ip.p10_findings([_job()], [res], log=lambda m: None)
    assert len(found) == 1 and "still shows" in found[0]["problem"], found

@case
def a_2xx_without_a_visible_effect_is_not_reported():

    res = {"id": "p10-0", "route": "/pr/1", "capture": {"user": "bob", "signedIn": True, "tookEffect": True},
           "accounts": [{"user": "dave", "roles": ["Read"], "signedIn": True, "controlHidden": True,
                         "via": "replay", "status": 200, "value": "zq1p0r0", "tookEffect": False}]}
    lines = []
    found = ip.p10_findings([_job()], [res], log=lines.append)
    assert found == [] and any("not reported" in ln for ln in lines), (found, lines)

@case
def a_failed_sign_in_or_skip_is_not_reported():
    res = {"id": "p10-0", "route": "/pr/1", "skip": "capture account sign-in failed",
           "accounts": [{"user": "dave", "roles": ["Read"], "signedIn": False, "why": "no password field"}]}
    assert ip.p10_findings([_job()], [res], log=lambda m: None) == []

@case
def the_skipped_account_is_logged_as_a_diagnostic():
    job = dict(job_one("account `erin`, password `Pw-123456789!`; `erin` has Read role on the repository. `erin` is a "
                       "member of team `writers`, which is granted the Write role on the repository. "), id="p10-0")
    lines = []
    ip.p10_findings([job], [{"id": "p10-0", "accounts": []}], log=lines.append)
    assert any("`erin` not probed as restricted" in ln and "diagnostic" in ln for ln in lines), lines

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144e cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
