from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import interaction_probes as ip

CASES = []
QUIET = lambda m: None

def case(function):
    CASES.append(function)
    return function

ROW = ("- id: R1 name: Issues description: The Issues page is the list page in repository navigation; each row displays "
       "the issue number, title, Open/Closed status, author, and labels, and clicking the number or title opens the issue "
       "detail page. At the top of the detail page are the title and status. ")
ACCOUNT = ("- id: R0 name: Sign in description: The user signs in as `alice` with `Valid-password-123!` and sees the "
           "workspace. ")
EDIT_A = ("- id: R2 name: Edit description: Only a signed-in user with Write, Maintain, or Admin may use the unique buttons "
          "named “Edit issue title” on an issue detail page. “Edit issue title” opens a form with a textbox labeled "
          "“Issue title” and a button named “Save issue title”; “Edit issue description” opens a form with a textbox "
          "labeled “Issue description” and a button named “Save issue description”. ")
CREATE = ("- id: R3 name: New issue description: The user clicks “New issue”, fills the textbox named “Title” and clicks "
          "the button named “Submit new issue”. ")
RULE = ("- id: R4 name: Rules description: The repository provides a Settings link followed by a Branches link. The rule "
        "form opens from the Add branch protection rule button and contains a field labeled Branch name pattern (an exact "
        "branch name, with no wildcard semantics), checkboxes named Require 1 approval and Require status check test, and "
        "a Create button; an existing rule uses Save changes. After saving and reloading, the branch name is visible. ")

def with_env(name: str, value: str | None):
    class _Env:
        def __enter__(self):
            self.old = os.environ.get(name)
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

        def __exit__(self, *exc):
            if self.old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = self.old
    return _Env()

@case
def p11_takes_the_stated_number_or_title_row_click():
    jobs = ip.build_jobs(ROW)["p11"]
    assert len(jobs) == 1, jobs
    assert jobs[0]["fields"] == ["number", "title"] and jobs[0]["target"] == "the issue detail page", jobs

@case
def p11_needs_two_ways_a_number_and_a_title():
    single = ROW.replace("clicking the number or title opens", "clicking the title opens")
    assert ip.build_jobs(single)["p11"] == []
    two_titles = ROW.replace("clicking the number or title opens", "clicking the title or name opens")
    assert ip.build_jobs(two_titles)["p11"] == []

@case
def p11_skips_a_dialog_target_and_a_sentence_without_a_list():
    assert ip.build_jobs(ROW.replace("opens the issue detail page", "opens a preview dialog"))["p11"] == []
    bare = "- id: X description: Clicking the number or title opens the issue detail page. "
    assert ip.build_jobs(bare)["p11"] == []

@case
def p11_has_its_own_switch():
    with with_env("ARC_IPROBE_P11", "0"):
        assert ip.build_jobs(ROW)["p11"] == []

def p11_row(**kw):
    row = {"route": "/o/r/issues", "num": "#3", "activatable": False, "title": "Original issue title",
           "href": "/o/r/issues/3", "rowText": "Original issue title #3 Open alice", "numberPath": "/o/r/issues",
           "numberDialog": False, "titlePath": "/o/r/issues/3", "titleDenied": False}
    row.update(kw)
    return row

def p11_run(**kw):
    jobs = ip.build_jobs(ROW)["p11"]
    return ip.p11_findings(jobs, [{"id": jobs[0]["id"], "route": "/o/r/issues", "rows": [p11_row(**kw)]}], log=QUIET)

@case
def p11_reports_a_plain_text_number_while_the_title_opens_the_page():
    found = p11_run()
    assert len(found) == 1, found
    assert "“#3” as plain text" in found[0]["problem"] and "/o/r/issues/3" in found[0]["problem"], found
    assert "link to the record's page" in found[0]["fix"]

@case
def p11_is_silent_without_the_contrast():
    for kw in ({"activatable": True}, {"numberPath": "/o/r/issues/3"}, {"numberDialog": True},
               {"titlePath": "/o/r/issues"}, {"titlePath": None}, {"titleDenied": True}, {"skip": "the row changed"}):
        assert p11_run(**kw) == [], kw

@case
def p12_shape_a_needs_a_stated_create_flow():
    jobs = ip.build_jobs(ACCOUNT + EDIT_A + CREATE)["p12"]
    assert len(jobs) == 1 and jobs[0]["shape"] == "A", jobs
    job = jobs[0]
    assert (job["opener"], job["field"], job["save"]) == ("Edit issue title", "Issue title", "Save issue title"), job
    assert job["creates"][0]["submit"] == "Submit new issue" and job["creates"][0]["field"] == "Title", job
    assert job["account"]["user"] == "alice"

    assert ip.build_jobs(ACCOUNT + EDIT_A)["p12"] == []

@case
def p12_shape_b_reads_the_existing_record_save_sentence():
    jobs = ip.build_jobs(ACCOUNT + RULE)["p12"]
    assert len(jobs) == 1 and jobs[0]["shape"] == "B", jobs
    job = jobs[0]
    assert job["save"] == "Save changes" and job["field"] == "Branch name pattern" and job["thing"] == "rule", job
    assert job["creates"] == [{"submit": "Create", "field": "Branch name pattern", "openers": ["Add branch protection rule"]}], job
    assert "Branches" in job["hints"] and "Settings" in job["hints"], job["hints"]
    assert ip.build_jobs(ACCOUNT + RULE.replace("; an existing rule uses Save changes", ""))["p12"] == []

@case
def p12_one_job_per_shape_and_needs_an_account():
    jobs = ip.build_jobs(ACCOUNT + EDIT_A + CREATE + RULE)["p12"]
    assert [j["shape"] for j in jobs] == ["A", "B"], jobs
    assert ip.build_jobs(EDIT_A + CREATE + RULE)["p12"] == []
    with with_env("ARC_IPROBE_P12", "0"):
        assert ip.build_jobs(ACCOUNT + EDIT_A + CREATE)["p12"] == []

@case
def p12_never_edits_as_a_stated_role_that_may_not():
    reader = ("- id: S description: Seed values: account `dave`, password `Pw-123456789!`; `dave` has Read role on the "
              "repository. ")
    assert ip.build_jobs(reader + EDIT_A + CREATE)["p12"] == []
    writer = reader + ("account `bob`, password `Pw-123456789!`; `bob` has Write role on the repository. ")
    jobs = ip.build_jobs(writer + EDIT_A + CREATE)["p12"]
    assert jobs and jobs[0]["account"]["user"] == "bob" and jobs[0]["roleProven"] is True, jobs

    grouped = reader + "`dave` is a member of team `core`; team `core` has Admin role on the repository. "
    jobs = ip.build_jobs(grouped + EDIT_A + CREATE)["p12"]
    assert jobs and jobs[0]["account"]["user"] == "dave" and jobs[0]["roleProven"] is False, jobs

def p12_res(**kw):
    res = {"shape": "B", "signedInAs": "alice", "createRoute": "/o/r/settings", "value1": "zq1p12a", "createStatus": [201],
           "created": True, "route": "/o/r/settings", "prefill": "zq1p12a", "own": True, "value2": "zq2p12b", "typed": True,
           "saveStatus": [404], "errorLines": ["Saving the rule failed"], "shownAfterSave": False,
           "reloaded": [{"path": "/o/r/settings", "hasNew": False, "hasOld": True}], "persisted": False}
    res.update(kw)
    return res

def p12_run(text=ACCOUNT + RULE, **kw):
    jobs = ip.build_jobs(text)["p12"]
    return ip.p12_findings(jobs, [dict(p12_res(**kw), id=jobs[0]["id"])], log=QUIET)

@case
def p12_reports_a_failed_save_of_its_own_record():
    found = p12_run()
    assert len(found) == 1, found
    assert "HTTP 404" in found[0]["problem"] and "Saving the rule failed" in found[0]["problem"], found
    assert "the edit was not saved" in found[0]["problem"] and "update route" in found[0]["fix"]

    assert len(p12_run(saveStatus=[200], errorLines=[], shownAfterSave=True)) == 1

@case
def p12_is_silent_unless_the_reload_shows_the_old_value_and_not_the_new():
    assert p12_run(persisted=True, reloaded=[{"path": "/o/r/settings", "hasNew": True, "hasOld": False}]) == []

    assert p12_run(reloaded=[{"path": "/o/r/settings", "hasNew": False, "hasOld": False}]) == []

@case
def p12_is_silent_without_its_own_record_or_a_sent_write():
    for kw in ({"own": False}, {"created": False}, {"typed": False}, {"saveStatus": []}, {"skip": "no edit control"},
               {"saveStatus": [200], "errorLines": [], "shownAfterSave": False}):
        assert p12_run(**kw) == [], kw

@case
def p12_a_refusal_counts_only_for_a_proven_role():
    assert p12_run(saveStatus=[403], errorLines=[]) == []
    writer = ("- id: S description: Seed values: account `bob`, password `Pw-123456789!`; `bob` has Write role on the "
              "repository. ")
    found = p12_run(text=writer + EDIT_A + CREATE, saveStatus=[403], errorLines=[])
    assert len(found) == 1 and "HTTP 403" in found[0]["problem"], found

@case
def p11_reads_before_the_writers_and_p12_writes_last():
    script = ip.PROBE_SCRIPT
    for name in ("async function p11(", "async function p12(", "out.p11.push", "out.p12.push", "p11: [], p12: []"):
        assert name in script, name
    assert script.index("out.p11.push") < script.index("out.p8.push") < script.index("out.p10.push") < script.index("out.p12.push")
    assert ip.ALL_KINDS[-1] == "p10" and ip.PROBE_KINDS[-2:] == ("p11", "p12")
    assert ip.triggered({"p11": [{"id": "p11-0"}]}) and ip.triggered({"p12": [{"id": "p12-0"}]})
    spec = ip.spec_of(ip.build_jobs(ACCOUNT + ROW + RULE), None)
    assert spec["p11"] and spec["p12"], spec
    assert not ip._complete({"p12": [{"id": "p12-0", "budget": True}]})
    found = ip.findings_from({"p11": ip.build_jobs(ROW)["p11"]},
                             {"p11": [{"id": "p11-0", "route": "/o/r/issues", "rows": [p11_row()]}]}, log=QUIET)
    assert len(found) == 1

@case
def sign_in_accepts_a_page_that_names_the_account_beside_a_static_sign_in_link():
    script = ip.PROBE_SCRIPT
    at = script.index("async function signIn(")
    body = script[at: script.index("\n}\n", at)]
    assert "n.name === user || n.text === user || SIGN_OUT.test(n.name)" in body, body
    assert "res.ok = !stillPw && (named ||" in body, body

    assert "['textbox', 'searchbox', 'combobox'].includes(n.role)" in body

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok   {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
