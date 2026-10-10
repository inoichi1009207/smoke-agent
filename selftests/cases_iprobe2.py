from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import interaction_probes as ip

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

class env:

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.values}
        os.environ.update(self.values)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

PRIVATE = ("- id: REQ-3-1 name: Search description: The seed organization has a public repository and a distinct private "
           "repository inaccessible to visitors. scenarios: - keyword: GIVEN content: 'The seeded data is public repository "
           "`acme-docs`, private repository `secret-research`, owner `alice-dev`.' - keyword: WHEN content: 'The user signs "
           "in as `alice-dev` with `Valid-password-123!` and follows the visible controls.'")
ISSUE = ("- id: REQ-5-1-1 name: Create an Issue type: ATOMIC description: “New issue” is a link on the Issues page. The "
         "creation form has fields labeled “Title” and “Description” and a “Submit new issue” button. A successful "
         "submission displays a heading named exactly after the entered title.")
ACCOUNT = (" - id: REQ-9 name: Accounts description: x scenarios: - keyword: WHEN content: 'The user signs in as `alice-dev` "
           "with `Valid-password-123!`.'")
REGISTER = ("- id: REQ-1-1-1 name: Register description: The form contains exactly one textbox labeled “Username”, one "
            "textbox labeled “Email”, and one enabled button named “Create account”.")
DIRECT = ("- id: REQ-3-2 name: Repositories description: The visitor submits the registered email; in each directly opened "
          "next step, the visitor enters the code. Users open a repository overview from global search results, a "
          "personal/organization Repositories list, or a direct link; signed-in users create repositories.")
SHEET_DIRECT = ("- id: REQ-1-1 name: Workbooks description: The current editor page entry in the browser must be directly "
                "accessible and continue to identify the same workbook after refresh.")

@case
def private_record_with_owner_password_switches_p7_on():
    p7 = ip.build_jobs(PRIVATE)["p7"]
    assert len(p7) == 1 and p7[0]["id"] == "p7-0", p7
    assert p7[0]["name"] == "secret-research" and p7[0]["owner"] == "alice-dev"
    assert ip.build_jobs(PRIVATE.replace("with `Valid-password-123!` ", ""))["p7"] == []
    assert ip.build_jobs(PRIVATE.replace("inaccessible to visitors", "for the team"))["p7"] == []

@case
def p7_alone_still_signs_the_owner_in():
    with env(ARC_IPROBE_P3="0"):
        jobs = ip.build_jobs(PRIVATE)
        assert jobs["p3b"] == [] and jobs["p7"] and jobs["owner"]["user"] == "alice-dev"

@case
def create_form_needs_a_creating_submit_a_title_field_and_an_account():
    p8 = ip.build_jobs(ISSUE + ACCOUNT)["p8"]
    assert len(p8) == 1, p8
    assert p8[0]["submit"] == "Submit new issue" and p8[0]["field"] == "Title" and p8[0]["openers"] == ["New issue"]
    assert "Submit new issue" in p8[0]["sentence"]
    assert ip.build_jobs(ISSUE)["p8"] == []
    assert ip.build_jobs(ISSUE.replace("Submit new issue", "Save") + ACCOUNT)["p8"] == []

@case
def credential_forms_are_not_create_forms():
    assert ip.build_jobs(REGISTER + ACCOUNT)["p8"] == []

@case
def direct_link_sentence_switches_p9_on_and_is_the_one_quoted():
    p9 = ip.build_jobs(DIRECT)["p9"]
    assert len(p9) == 1 and "a direct link" in p9[0]["sentence"], p9
    assert "directly accessible" in ip.build_jobs(SHEET_DIRECT)["p9"][0]["sentence"]
    assert ip.build_jobs("- id: X name: y description: Direct access by the account to private resources is denied.")["p9"] == []
    assert ip.build_jobs(ISSUE)["p9"] == []

@case
def switches_turn_p7_p8_p9_off():
    with env(ARC_IPROBE_P7="0", ARC_IPROBE_P8="0", ARC_IPROBE_P9="0"):
        jobs = ip.build_jobs(PRIVATE + " " + ISSUE + " " + DIRECT)
        assert jobs["p7"] == [] and jobs["p8"] == [] and jobs["p9"] == [] and jobs["p3b"]
    spec = ip.spec_of(ip.build_jobs(PRIVATE + " " + ISSUE + " " + DIRECT), None)
    assert spec["p7"] and spec["p8"] and spec["p9"]

def p7_result(**over):
    res = {"id": "p7-0", "resource": "secret-research", "root": "/alice-dev/secret-research",
           "ownerRoot": {"denied": False, "hasName": True},
           "visitorRoot": {"denied": True, "text": "Access denied You do not have permission to view this repository."},
           "pages": [{"path": "/", "links": [{"name": "secret-research", "path": "/alice-dev/secret-research"}],
                      "named": ['link "secret-research"'], "snippet": ""}],
           "apis": [{"name": "secret-research", "api": "/api/repositories/browse", "page": "/",
                     "items": [{"owner": "alice-dev", "name": "secret-research", "visibility": "private"}]}], "twins": []}
    res.update(over)
    return res

@case
def private_record_listed_to_the_refused_visitor_is_reported():
    jobs = ip.build_jobs(PRIVATE)["p7"]
    found = ip.p7_findings(jobs, [p7_result()], log=QUIET)
    assert len(found) == 1, found
    problem = found[0]["problem"]
    assert found[0]["where"] == "route /" and "/alice-dev/secret-research" in problem and "/api/repositories/browse" in problem
    assert "secret-research" in problem and "refused" in problem

@case
def p7_needs_the_refusal_the_owner_view_and_a_hit():
    jobs = ip.build_jobs(PRIVATE)["p7"]
    for res in (p7_result(visitorRoot={"denied": False, "text": "secret-research"}),
                p7_result(ownerRoot={"denied": True, "hasName": True}),
                p7_result(pages=[], apis=[]),
                p7_result(error="boom"), p7_result(skip="the owner sees no link to secret-research", ownerRoot={})):
        logs = []
        assert ip.p7_findings(jobs, [res], log=logs.append) == [], res
        assert any("(diagnostic, not reported) interaction P7" in m for m in logs)

@case
def p7_api_hit_alone_is_reported():
    found = ip.p7_findings(ip.build_jobs(PRIVATE)["p7"], [p7_result(pages=[])], log=QUIET)
    assert len(found) == 1 and "GET /api/repositories/browse" in found[0]["problem"]

def p8_result(**over):
    res = {"id": "p8-0", "submit": "Submit new issue", "field": "Title", "signedInAs": "alice-dev",
           "route": "/alice-dev/acme-docs/issues/new", "from": None, "value": "zq79783p8", "writes": [201, 201],
           "after": "/alice-dev/acme-docs/issues/3",
           "lists": [{"path": "/alice-dev/acme-docs/issues/new", "links": 0, "texts": 0, "json": []},
                     {"path": "/alice-dev/acme-docs/issues", "links": 2, "texts": 2,
                      "json": [{"api": "/api/repos/alice-dev/acme-docs/issues", "items": 2}]}]}
    res.update(over)
    return res

@case
def double_click_that_made_two_records_is_reported():
    jobs = ip.build_jobs(ISSUE + ACCOUNT)["p8"]
    found = ip.p8_findings(jobs, [p8_result()], log=QUIET)
    assert len(found) == 1, found
    assert "Submit new issue" in found[0]["problem"] and "(201, 201)" in found[0]["problem"] and "alice-dev" in found[0]["problem"]
    assert "disable" in found[0]["fix"]

@case
def p8_is_quiet_without_two_accepted_writes_and_two_records():
    jobs = ip.build_jobs(ISSUE + ACCOUNT)["p8"]
    one_list = [{"path": "/x/issues", "links": 1, "texts": 1, "json": [{"api": "/api/x", "items": 1}]}]
    for res in (p8_result(writes=[201], lists=one_list),
                p8_result(writes=[201]),
                p8_result(writes=[201, 409], lists=one_list),
                p8_result(lists=[{"path": "/x/issues", "links": 2, "texts": 2, "json": [{"api": "/api/x", "items": 1}]}]),
                p8_result(writes=[422]), p8_result(skip="no page shows the text box and the submit button"), p8_result(error="x")):
        logs = []
        assert ip.p8_findings(jobs, [res], log=logs.append) == [], res
        assert any("(diagnostic, not reported) interaction P8" in m for m in logs)
    assert ip.p8_findings(jobs, [p8_result(lists=[{"path": "/x", "links": 2, "texts": 2, "json": []}])], log=QUIET)

def p9_result(**over):
    res = {"id": "p9-0", "signedInAs": "alice-dev",
           "top": {"path": "/zq-no-such-page-zq1p9", "status": 200, "blank": False, "finalPath": "/zq-no-such-page-zq1p9", "text": "Page not found"},
           "records": []}
    res.update(over)
    return res

def record(**over):
    rec = {"real": "/orgs/acme-demo", "path": "/orgs/zq-no-such-zq2p9", "status": 200, "finalPath": "/orgs/zq-no-such-zq2p9",
           "blank": False, "errorText": False, "realHasErrorWords": False, "own": 9, "ownControls": 4,
           "shared": ["textbox|Filter repositories", "button|Public", "button|Private", "link|People"], "sharedControls": 3,
           "failed": ["GET /api/orgs/zq-no-such-zq2p9 404"], "text": "zq-no-such-zq2p9 Repositories People Teams"}
    rec.update(over)
    return rec

@case
def blank_unknown_address_is_reported():
    jobs = ip.build_jobs(DIRECT)["p9"]
    found = ip.p9_findings(jobs, [p9_result(top={"path": "/zq-no-such-page-zq1p9", "status": 200, "blank": True,
                                                  "finalPath": "/zq-no-such-page-zq1p9", "text": ""})], log=QUIET)
    assert len(found) == 1 and "completely blank" in found[0]["problem"] and "catch-all" in found[0]["fix"]

@case
def unknown_record_rendering_the_record_controls_is_reported():
    jobs = ip.build_jobs(DIRECT)["p9"]
    found = ip.p9_findings(jobs, [p9_result(records=[record()])], log=QUIET)
    assert len(found) == 1, found
    assert "Filter repositories" in found[0]["problem"] and "“Public”" in found[0]["problem"]
    assert "“People”" not in found[0]["problem"]

@case
def p9_is_quiet_on_a_stated_not_found_a_redirect_or_a_partial_match():
    jobs = ip.build_jobs(DIRECT)["p9"]
    for rec in (record(errorText=True),
                record(finalPath="/login"),
                record(realHasErrorWords=True),
                record(sharedControls=2, ownControls=5),
                record(sharedControls=1, ownControls=1),
                record(sharedControls=0, ownControls=9, shared=["link|Code", "link|Issues"])):
        logs = []
        assert ip.p9_findings(jobs, [p9_result(records=[rec])], log=logs.append) == [], rec
        assert any("(diagnostic, not reported) interaction P9" in m for m in logs)
    assert ip.p9_findings(jobs, [p9_result(records=[record(blank=True, sharedControls=0)])], log=QUIET)

@case
def the_new_probes_are_wired_and_data_writers_run_last():

    script = ip.PROBE_SCRIPT
    for name in ("async function p7(", "async function p8(", "async function p9(", "async function p10(",
                 "out.p7.push", "out.p8.push", "out.p9.push", "out.p10.push"):
        assert name in script, name
    reads = max(script.index(k) for k in ("out.p1.push", "out.p2.push", "out.p3.push", "out.p3b.push", "out.p4.push",
                                          "out.p5.push", "out.p6.push", "out.p7.push", "out.p9.push"))
    assert reads < script.index("out.p8.push") < script.index("out.p10.push"), "data writers P8, P10 must run last"

    assert ip.KINDS[-3:] == ("p7", "p8", "p9") and ip.ALL_KINDS[-1] == "p10"
    assert ip.triggered({"p9": [{"id": "p9-0"}]}) and ip.triggered({"p10": [{"id": "p10-0"}]})
    found = ip.findings_from({"p7": ip.build_jobs(PRIVATE)["p7"]}, {"p7": [p7_result()]}, log=QUIET)
    assert len(found) == 1

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
