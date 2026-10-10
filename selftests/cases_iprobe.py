from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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

BRANCH = ("- id: REQ-4-3-1 name: List and Switch Repository Branches type: ATOMIC description: The selector is a unique "
          "button named “Branch <current branch name>”; it opens a textbox named “Find branch” and selectable items with "
          "role option whose exact accessible names are the branch names. Selecting the target immediately updates the "
          "button's branch name. Escape closes the selector; for an unmatched search it preserves the original active branch.")
ADD_FILE = "- id: REQ-4-4 name: Add a File description: From the writable Code page, the unique “Add file” button opens the “Create new file” menuitem."
SHEET_MENU = ('- id: REQ-2-1 name: Worksheets description: Each worksheet tab provides a button with the accessible name '
              '"Worksheet options for <worksheet name>"; clicking it opens a menu whose commands use the ARIA menuitem role.')
PR_TABS = ("- id: REQ-6-3 name: PR detail description: On the PR detail page, Conversation displays the title; Commits "
           "displays commits on the compare branch; Files changed displays per-file differences; Checks displays check "
           "status. Conversation, Commits, Files changed, and Checks are navigation links, even when visually styled as tabs.")
ORG_TABS = ("- id: REQ-2-1 name: Org description: The organization overview page uses the organization name as a heading; its "
            "“Repositories”, “People”, and “Teams” navigation entries have link roles even when visually styled as tabs. The "
            "Repositories tab lists repositories owned by the organization, the People tab lists members and their roles, "
            "and the Teams tab lists teams. From the organization page, the user clicks the “Repositories” link and click a "
            "repository name in the list to open its overview.")
COMMENT = ("- id: REQ-5-2-3 name: Comment on an Issue Discussion type: ATOMIC description: Only a signed-in user with Write, "
           "Maintain, or Admin may enter a non-empty comment on the issue detail page of the current repository. The comment "
           "editor is labeled “Comment”, and its submit button is named exactly “Comment”. scenarios: - name: x")
MEMBERS = ("- id: REQ-2-2-2 name: Team members description: Only an organization Owner may maintain members or hierarchy on "
           "an organization team page. On Members, the button “Add member” opens a textbox labeled “Username” and the submit "
           "button “Add member”.")
PRIVATE = ("- id: REQ-3-1 name: Search description: The seed organization has a public repository and a distinct private "
           "repository inaccessible to visitors. scenarios: - keyword: GIVEN content: 'The seeded data is public repository "
           "`acme-docs`, private repository `secret-research`, owner `alice-dev`.' - keyword: WHEN content: 'The user signs "
           "in as `alice-dev` with `Valid-password-123!` and follows the visible controls.'")
SHEET_CELL = ("- id: REQ-1-1-1 name: View and Open a Workbook type: ATOMIC description: | Users view available workbooks on the "
              "workbook home page. Each record displays \"Last updated: <last updated value>\" and provides a link. "
              "- id: REQ-3-1-1 name: Edit a Cell Through the Grid or Formula Bar type: ATOMIC description: 'After selecting a "
              "cell in the current active worksheet, users can modify its content directly in the grid or formula bar. Pressing "
              "Enter or clicking another cell commits the change; pressing Escape cancels an uncommitted change. The text box "
              "labeled \"Formula bar\" shows the input.'")

def jobs(text: str) -> dict:
    return ip.build_jobs(text)

@case
def branch_selector_opens_a_named_textbox_and_escape_closes_it():
    p1 = jobs(BRANCH)["p1"]
    assert len(p1) == 1, p1
    assert p1[0]["opener"] == "^Branch .+$" and p1[0]["target"] == {"role": "textbox", "name": "Find branch"}
    assert p1[0]["escape"] is True and p1[0]["outside"] is False and p1[0]["enter"] is False

@case
def a_menu_opener_without_an_escape_sentence_is_probed_for_opening_only():
    p1 = jobs(ADD_FILE + " " + SHEET_MENU)["p1"]
    targets = sorted((j["opener"], j["target"]["role"], j["target"]["name"], j["escape"]) for j in p1)
    assert targets == [("^Add file$", "menuitem", "Create new file", False), ("^Worksheet options for .+$", "menuitem", "", False)], targets

@case
def negated_or_unnamed_openers_are_not_recognised():
    text = ("- id: X description: The “Save” button does not open a dialog. Clicking a file opens a read-only file page. "
            "The “Rename” menu item opens a dialog named “Rename worksheet”. Escape closes the dialog.")
    assert jobs(text)["p1"] == []

@case
def displays_opens_only_as_a_click_effect():
    said = ('- id: R description: Next to the editor title is a button with the accessible name "Rename workbook"; clicking '
            'it displays a text box labeled "Workbook name", prefilled with the last saved name, and a "Save" button.')
    assert [(j["opener"], j["target"]["name"]) for j in jobs(said)["p1"]] == [("^Rename workbook$", "Workbook name")]
    plain = '- id: R description: The toolbar has a button named "Filter" that shows a text box labeled "Filter value".'
    assert jobs(plain)["p1"] == []

@case
def escape_in_another_node_is_not_linked():
    p1 = jobs(ADD_FILE + " - id: REQ-9 name: y description: Escape closes the selector.")["p1"]
    assert p1 and not p1[0]["escape"]

@case
def stated_outside_click_and_enter_in_a_dialog_are_linked():
    text = ("- id: R description: The “Rename workbook” button opens a dialog named “Rename workbook” with a “Save” button. "
            "Pressing Enter in the dialog's text box saves it. Escape closes the dialog; clicking outside the dialog closes it.")
    p1 = jobs(text)["p1"]
    assert len(p1) == 1 and p1[0]["target"] == {"role": "dialog", "name": "Rename workbook"}, p1
    assert p1[0]["escape"] and p1[0]["outside"] and p1[0]["enter"] and p1[0]["submit"] == "Save", p1

@case
def tabs_named_as_navigation_with_stated_views_are_recognised():
    p2 = jobs(PR_TABS + " " + ORG_TABS)["p2"]
    names = sorted(tuple(j["names"]) for j in p2)
    assert names == [("Conversation", "Commits", "Files changed", "Checks"), ("Repositories", "People", "Teams")], names

@case
def tabs_without_stated_views_are_not_probed():
    assert jobs("- id: X description: Protocol controls are tabs “HTTPS” and “SSH”, and a button “Copy clone value”.")["p2"] == []
    no_views = PR_TABS.split("On the PR detail page")[0] + "Conversation, Commits, Files changed, and Checks are navigation links, even when visually styled as tabs."
    assert jobs(no_views)["p2"] == []

@case
def only_sentence_takes_the_control_named_for_its_act():
    p3 = jobs(COMMENT)["p3"]
    assert len(p3) == 1 and sorted((c["role"], c["name"]) for c in p3[0]["controls"]) == [("button", "Comment"), ("textbox", "Comment")], p3
    assert p3[0]["allowed"] == ["Admin", "Maintain", "Write"]

@case
def a_control_of_another_act_is_not_taken():

    p3 = jobs(MEMBERS)["p3"]
    assert [[(c["role"], c["name"]) for c in s["controls"]] for s in p3] == [[("button", "Add member")]], p3

@case
def only_visitors_or_everyone_is_no_restriction():
    assert jobs("- id: X description: Only visitors can use the button named “Preview”.")["p3"] == []

@case
def restricted_accounts_need_a_stated_role_and_password():
    text = COMMENT + " Seed values: account `rita-read`, password `Pw-123456789!`; `rita-read` has Read permission on the repository."
    restricted = jobs(text)["p3"][0]["restricted"]
    assert restricted == [{"user": "rita-read", "password": "Pw-123456789!", "roles": ["Read"]}], restricted
    assert jobs(COMMENT + " `rita-read` has Read permission.")["p3"][0]["restricted"] == []

@case
def private_resource_needs_the_access_rule_and_the_owner_password():
    p3b = jobs(PRIVATE)["p3b"]
    assert [(j["kind"], j["name"], j["owner"], j["password"]) for j in p3b] == [("repository", "secret-research", "alice-dev", "Valid-password-123!")]
    assert jobs(PRIVATE.replace("inaccessible to visitors", "listed first"))["p3b"] == []
    assert jobs(PRIVATE.replace("signs in as `alice-dev` with `Valid-password-123!`", "signs in"))["p3b"] == []
    assert jobs(PRIVATE)["owner"] == {"user": "alice-dev", "password": "Valid-password-123!"}

@case
def last_updated_label_and_enter_commit_switch_p4_on():
    p4 = jobs(SHEET_CELL)["p4"]
    assert len(p4) == 1 and p4[0]["label"] == "Last updated" and p4[0]["roles"] == ["gridcell", "cell"], p4
    assert jobs(SHEET_CELL.replace("Last updated", "Status"))["p4"] == []
    assert jobs(SHEET_CELL.replace("Pressing Enter or clicking", "Clicking"))["p4"] == []

@case
def formula_bar_is_an_alternative_commit_path():
    p5 = jobs(SHEET_CELL)["p5"]
    assert len(p5) == 1 and p5[0]["field"] == "Formula bar" and p5[0]["escape"] and p5[0]["thing"] == "cell", p5
    assert jobs(SHEET_CELL.replace("in the grid or formula bar", "in the grid"))["p5"] == []

@case
def owned_list_with_open_by_name_switches_p6_on():
    p6 = jobs(ORG_TABS)["p6"]
    assert [(j["noun"], j["scope"]) for j in p6] == [("repository", "organization")], p6
    assert jobs(ORG_TABS.replace("click a repository name in the list to open its overview", "filter"))["p6"] == []

@case
def switches_turn_probes_off():
    old = {k: os.environ.get(k) for k in ("ARC_IPROBE", "ARC_IPROBE_P2")}
    try:
        os.environ["ARC_IPROBE_P2"] = "0"
        j = jobs(PR_TABS + " " + BRANCH)
        assert j["p2"] == [] and j["p1"]
        os.environ["ARC_IPROBE"] = "0"
        assert not ip.triggered(jobs(PR_TABS + " " + BRANCH))
        assert ip.interaction_findings(PR_TABS, Path("."), [], log=QUIET) == []
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

@case
def name_pattern_reads_placeholders():
    assert ip.name_pattern("Branch <current branch name>") == "^Branch .+$"
    assert ip.name_pattern("Open (a.b)") == "^Open \\(a\\.b\\)$"

def p1_job():
    return dict(jobs(BRANCH)["p1"][0])

@case
def branch_selector_open_on_load_and_escape_ignored_is_one_finding():
    job = p1_job()
    res = {"id": job["id"], "route": "/alice-dev/acme-docs", "openerName": "Branch main", "onLoad": True, "escapeClosed": False, "escapeInside": True}
    found = ip.p1_findings([job], [res], log=QUIET)
    assert len(found) == 1 and "already shown when the page loads" in found[0]["problem"] and "Escape" in found[0]["problem"], found
    fixed = {"id": job["id"], "route": "/alice-dev/acme-docs", "openerName": "Branch main", "onLoad": False, "opened": True, "escapeClosed": True}
    assert ip.p1_findings([job], [fixed], log=QUIET) == []

@case
def unnamed_target_shown_on_load_is_not_reported():
    job = dict(jobs(SHEET_MENU)["p1"][0])
    assert ip.p1_findings([job], [{"id": job["id"], "route": "/w/1", "onLoad": True, "opened": True}], log=QUIET) == []

@case
def dialog_enter_needs_the_button_contrast():
    text = ("- id: R description: The “Rename workbook” button opens a dialog named “Rename workbook” with a “Save” button. "
            "Pressing Enter in the dialog's text box saves it.")
    job = dict(jobs(text)["p1"][0])
    base = {"id": job["id"], "route": "/w/1", "onLoad": False, "opened": True, "enterClosed": False, "enterWrites": 0}
    assert len(ip.p1_findings([job], [dict(base, buttonClosed=True)], log=QUIET)) == 1
    assert ip.p1_findings([job], [dict(base, buttonClosed=False)], log=QUIET) == []

def views(texts, anchors, same=True, heads=("Improve onboarding", "Conversation")):

    names = ["Conversation", "Commits", "Files changed", "Checks"]
    return [{"name": n, "hash": "#" + n.lower(), "samePage": same, "anchor": a, "text": t, "length": len(t), "headings": list(heads)}
            for n, t, a in zip(names, texts, anchors)]

@case
def fake_tabs_are_reported_real_tabs_and_sections_are_not():
    job = [j for j in jobs(PR_TABS)["p2"]][0]
    same = "Improve onboarding #1 Conversation Improves the onboarding."
    fake = {"id": job["id"], "route": "/alice-dev/acme-docs/pull/1", "views": views([same] * 4, [True, False, False, False])}
    found = ip.p2_findings([job], [fake], log=QUIET)
    assert len(found) == 1 and "changes only the address" in found[0]["problem"], found
    real = {"id": job["id"], "route": "/p/1", "views": views([same, "Commits Commit summary", "Files changed Changed files summary", "Checks"], [True, False, False, False])}
    assert ip.p2_findings([job], [real], log=QUIET) == []
    sections = {"id": job["id"], "route": "/p/1", "views": views([same] * 4, [True, True, True, True])}
    assert ip.p2_findings([job], [sections], log=QUIET) == []
    moved = {"id": job["id"], "route": "/p/1", "views": views([same] * 4, [False] * 4, same=False)}
    assert ip.p2_findings([job], [moved], log=QUIET) == []

@case
def visitor_control_is_reported_only_when_seen():
    job = jobs(COMMENT)["p3"][0]
    seen = {"id": job["id"], "visitor": [{"route": "/alice-dev/acme-docs/issues/3", "role": "textbox", "name": "Comment"}], "accounts": []}
    found = ip.p3_findings([job], [seen], log=QUIET)
    assert len(found) == 1 and "signed-out visitor" in found[0]["problem"], found
    assert ip.p3_findings([job], [{"id": job["id"], "visitor": [], "accounts": []}], log=QUIET) == []
    acc = {"id": job["id"], "visitor": [], "accounts": [{"user": "r", "roles": ["Read"], "signedIn": False, "seen": [{"route": "/x", "role": "button", "name": "Comment"}]}]}
    assert ip.p3_findings([job], [acc], log=QUIET) == []

def p3b_result(**over):
    res = {"id": "p3b-0", "resource": "secret-research", "root": "/alice-dev/secret-research",
           "ownerRoot": {"denied": False, "hasName": True},
           "visitorRoot": {"denied": True, "hasName": False, "text": "Access denied"},
           "subs": [{"path": "/alice-dev/secret-research/settings", "ownerOk": True, "visitorDenied": False, "visitorHasName": True,
                     "visitorText": "alice-dev/secret-research General Branches Manage access", "ownLines": 8, "shared": 4,
                     "sharedSample": ["General", "Branches", "Manage access", "Repository overview"]},
                    {"path": "/alice-dev/secret-research/file/README.md", "ownerOk": True, "visitorDenied": True, "visitorHasName": True,
                     "visitorText": "File not found", "ownLines": 3, "shared": 0, "sharedSample": []}]}
    res.update(over)
    return res

@case
def private_sub_route_leak_needs_the_refused_root():
    job = jobs(PRIVATE)["p3b"][0]
    found = ip.p3b_findings([job], [p3b_result()], log=QUIET)
    assert len(found) == 1 and "/settings" in found[0]["problem"] and "README" not in found[0]["problem"], found
    assert ip.p3b_findings([job], [p3b_result(visitorRoot={"denied": False, "hasName": True, "text": ""})], log=QUIET) == []
    assert ip.p3b_findings([job], [p3b_result(ownerRoot={"denied": True, "hasName": False})], log=QUIET) == []
    fixed = p3b_result(subs=[dict(s, visitorDenied=True) for s in p3b_result()["subs"]])
    assert ip.p3b_findings([job], [fixed], log=QUIET) == []

@case
def stale_label_is_reported_only_when_the_reload_shows_a_new_value():
    job = jobs(SHEET_CELL)["p4"][0]
    base = {"id": job["id"], "route": "/workbooks/1", "cell": "G5", "typed": True, "persisted": True, "v0": "T1"}
    assert len(ip.p4_findings([job], [dict(base, v1="T1", v2="T2")], log=QUIET)) == 1
    assert ip.p4_findings([job], [dict(base, v1="T2", v2="T2")], log=QUIET) == []
    assert ip.p4_findings([job], [dict(base, v1="T1", v2="T1")], log=QUIET) == []
    assert ip.p4_findings([job], [dict(base, v1="T1", v2="T2", persisted=False)], log=QUIET) == []

@case
def field_value_lost_on_click_needs_enter_to_keep_it():
    job = jobs(SHEET_CELL)["p5"][0]
    lost = {"id": job["id"], "route": "/workbooks/1", "click": {"cell": "E1", "other": "L8", "typed": True, "afterClick": False, "persisted": False},
            "enter": {"cell": "H1", "typed": True, "persisted": True}, "escape": {"cell": "K1", "typed": True, "persisted": False}}
    found = ip.p5_findings([job], [lost], log=QUIET)
    assert len(found) == 1 and "Formula bar" in found[0]["problem"], found
    assert ip.p5_findings([job], [dict(lost, enter={"cell": "H1", "typed": True, "persisted": False})], log=QUIET) == []
    kept = dict(lost, click={"cell": "E1", "other": "L8", "typed": True, "afterClick": True, "persisted": True})
    kept.pop("enter")
    assert ip.p5_findings([job], [kept], log=QUIET) == []
    saved = dict(kept, escape={"cell": "K1", "typed": True, "persisted": True})
    assert len(ip.p5_findings([job], [saved], log=QUIET)) == 1

@case
def list_link_to_a_same_named_record_elsewhere_is_reported():
    job = jobs(ORG_TABS)["p6"][0]
    item = {"route": "/orgs/acme-demo", "name": "acme-docs", "path": "/acme-demo/acme-docs", "facts": ["Public documentation repository"],
            "texts": ["Public", "Public documentation repository"],
            "target": {"denied": True, "first": "Access denied", "facts": 0},
            "partners": [{"path": "/alice-dev/acme-docs", "denied": False, "hasName": True, "facts": 1}]}
    found = ip.p6_findings([job], [{"id": job["id"], "items": [item]}], log=QUIET)
    assert len(found) == 1 and "/alice-dev/acme-docs" in found[0]["problem"], found
    own = dict(item, target={"denied": False, "first": "acme-demo/acme-docs", "facts": 1})
    assert ip.p6_findings([job], [{"id": job["id"], "items": [own]}], log=QUIET) == []
    lonely = dict(item, partners=[{"path": "/alice-dev/acme-docs", "denied": True, "hasName": False, "facts": 0}])
    assert ip.p6_findings([job], [{"id": job["id"], "items": [lonely]}], log=QUIET) == []
    other = dict(item, target={"denied": False, "first": "x", "facts": 0}, texts=["alice-dev", "Public documentation repository"])
    assert len(ip.p6_findings([job], [{"id": job["id"], "items": [other]}], log=QUIET)) == 1

@case
def a_crashed_probe_makes_no_finding():
    j = jobs(BRANCH + " " + PR_TABS)
    result = {"p1": [{"id": j["p1"][0]["id"], "error": "boom"}], "p2": [{"id": j["p2"][0]["id"], "error": "boom", "views": []}]}
    assert ip.findings_from(j, result, log=QUIET) == []
    assert ip.findings_from(j, None, log=QUIET) == []

@case
def no_statement_starts_no_app():
    original = ip.probe
    ip.probe = lambda *a, **k: (_ for _ in ()).throw(AssertionError("started"))
    try:
        assert ip.interaction_findings("- id: X description: Users create notes.", Path("."), [], log=QUIET) == []
    finally:
        ip.probe = original

@case
def an_unchanged_app_is_probed_once():
    calls = []
    job = p1_job()
    result = {"p1": [{"id": job["id"], "route": "/r", "openerName": "Branch main", "onLoad": True, "escapeClosed": False}]}
    original = ip.probe
    ip.probe = lambda project, spec, log=print: (calls.append(spec), result)[1]
    tmp = Path(tempfile.mkdtemp(prefix="iprobe-"))
    try:
        (tmp / "frontend" / "src").mkdir(parents=True)
        (tmp / "frontend" / "src" / "App.tsx").write_text("a", encoding="utf-8")
        first = ip.interaction_findings(BRANCH, tmp, [{"route": "/r", "aria": ""}], log=QUIET)
        second = ip.interaction_findings(BRANCH, tmp, [{"route": "/r", "aria": ""}], log=QUIET)
        assert len(first) == 1 and first == second and len(calls) == 1, (first, second, len(calls))
        assert calls[0]["hints"] == ["/r"] and calls[0]["p1"][0]["opener"] == "^Branch .+$"
        (tmp / "frontend" / "src" / "App.tsx").write_text("changed", encoding="utf-8")
        ip.interaction_findings(BRANCH, tmp, [], log=QUIET)
        assert len(calls) == 2
    finally:
        ip.probe = original
        shutil.rmtree(tmp, ignore_errors=True)

@case
def own_port_is_a_random_free_port_outside_the_shared_sequence():
    import socket

    ports = {ip.own_port() for _ in range(6)}
    assert len(ports) >= 2, ports
    for port in ports:
        assert ip.PORT_RANGE[0] <= port <= ip.PORT_RANGE[1], port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))

@case
def only_the_started_server_counts_as_the_answer():
    assert ip.own_server_answered(True, "> backend@1.0.0 start\nlistening on 3500\n")
    assert not ip.own_server_answered(False, "")
    assert not ip.own_server_answered(True, "Error: listen EADDRINUSE: address already in use 127.0.0.1:3500")

class _FakeServer:
    lives: list = [True]
    output = ""
    stopped = 0

    def __init__(self, *args, **kwargs):
        self._lives = list(type(self).lives)

    def alive(self):
        return self._lives.pop(0) if len(self._lives) > 1 else self._lives[0]

    def output_so_far(self):
        return type(self).output

    def stop(self):
        type(self).stopped += 1
        return ""

def _probe_with(lives, output=""):
    from agent import fsutil

    tmp = Path(tempfile.mkdtemp(prefix="iprobe-own-"))
    (tmp / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
    saved = (fsutil.BackgroundProcess, fsutil.wait_http, ip.run_probe)
    _FakeServer.lives, _FakeServer.output, _FakeServer.stopped = lives, output, 0
    logs = []
    try:
        fsutil.BackgroundProcess = _FakeServer
        fsutil.wait_http = lambda url, timeout=60: (True, 200)
        ip.run_probe = lambda backend, base, spec, log=print: {"crawl": {"visitor": ["/", "/w/1"]}, "p1": []}
        result = ip.probe(tmp, {}, log=logs.append)
    finally:
        fsutil.BackgroundProcess, fsutil.wait_http, ip.run_probe = saved
        shutil.rmtree(tmp, ignore_errors=True)
    assert _FakeServer.stopped == 1
    return result, logs

@case
def a_result_from_another_app_on_the_port_is_dropped():
    result, _ = _probe_with([True])
    assert result and result["crawl"]["visitor"] == ["/", "/w/1"]
    result, logs = _probe_with([False])
    assert result is None and "not run" in logs[-1], logs
    result, logs = _probe_with([True, False])
    assert result is None and "dropped" in logs[-1], logs
    result, logs = _probe_with([True], "Error: listen EADDRINUSE: address already in use")
    assert result is None, logs

@case
def owner_fallback_is_named_in_the_finding():
    job = p1_job()
    res = {"id": job["id"], "route": "/alice-dev/acme-docs", "openerName": "Branch main", "onLoad": True, "escapeClosed": True,
           "signedInAs": "alice-dev", "visitorSkip": "no crawled page shows the opener"}
    found = ip.p1_findings([job], [res], log=QUIET)
    assert len(found) == 1 and "(signed in as `alice-dev`)" in found[0]["problem"], found
    plain = dict(res)
    plain.pop("signedInAs")
    assert "signed in as" not in ip.p1_findings([job], [plain], log=QUIET)[0]["problem"]

@case
def owner_fallback_is_wired_for_the_page_probes_only():
    script = ip.PROBE_SCRIPT
    for kind in ("p1", "p2", "p4", "p5"):
        assert f"out.{kind}.push(await reach({kind}, job))" in script, kind

    assert "out.p6.push(await guard(() => p6(visitor" in script and "out.p3.push(await guard(() => p3(browser" in script
    assert "/^no crawled page/" in script

    assert "await guard(widen);" in script and "crawl(owner.context, more, 20, 12000, 0, false)" in script

@case
def a_small_crawl_logs_its_pages_and_the_owner_sign_in():
    job = p1_job()
    original = ip.probe
    ip.probe = lambda project, spec, log=print: {"crawl": {"visitor": ["/", "/workbooks/1"]}, "ms": 3000,
                                                 "owner": {"user": "alice-dev", "ok": False, "why": "no password field"}, "p1": []}
    logs = []
    tmp = Path(tempfile.mkdtemp(prefix="iprobe-log-"))
    try:
        ip.interaction_findings(BRANCH, tmp, [], log=logs.append)
    finally:
        ip.probe = original
        shutil.rmtree(tmp, ignore_errors=True)
    line = next(m for m in logs if "crawled 2 page(s)" in m)
    assert "'/workbooks/1'" in line and "no password field" in line, line
    assert job["id"] == "p1-0"

@case
def probe_script_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        return
    tmp = Path(tempfile.mkdtemp(prefix="iprobe-js-"))
    try:
        (tmp / "probe.cjs").write_text(ip.PROBE_SCRIPT, encoding="utf-8")
        run = subprocess.run([node, "--check", str(tmp / "probe.cjs")], capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr[-400:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
