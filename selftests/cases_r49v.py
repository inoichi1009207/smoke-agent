from __future__ import annotations

import json
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
QUIET = lambda m: None

def case(function):
    CASES.append(function)
    return function

ROW = ("- id: R1 name: Issues description: The Issues page is the list page in repository navigation; each row displays "
       "the issue number, title, Open/Closed status, author, and labels, and clicking the number or title opens the issue "
       "detail page. At the top of the detail page are the title and status. ")
ACCOUNT = ("- id: R0 name: Sign in description: The user signs in as `alice` with `Valid-password-123!` and sees the "
           "workspace. ")
RULE = ("- id: R4 name: Rules description: The repository provides a Settings link followed by a Branches link. The rule "
        "form opens from the Add branch protection rule button and contains a field labeled Branch name pattern (an exact "
        "branch name, with no wildcard semantics), checkboxes named Require 1 approval and Require status check test, and "
        "a Create button; an existing rule uses Save changes. After saving and reloading, the branch name is visible. ")

def _source(name: str) -> str:
    script = ip.PROBE_SCRIPT
    if name.isupper() or name.startswith("P1"):
        at = script.index(f"const {name} =")
        return script[at: script.index("\n", at)]
    at = script.index(f"function {name}(")
    return script[at: script.index("\n}\n", at) + 2]

def js(names: list[str], body: str):
    node = shutil.which("node")
    if not node:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="r49v-js-"))
    try:
        src = "\n".join(_source(n) for n in names) + "\nconst out = (() => {\n" + body + "\n})();\nconsole.log(JSON.stringify(out));\n"
        (tmp / "t.cjs").write_text(src, encoding="utf-8")
        run = subprocess.run([node, str(tmp / "t.cjs")], capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert run.returncode == 0, run.stderr[-600:]
        return json.loads(run.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@case
def p11_job_carries_the_stated_record_stem_and_needs_one():
    jobs = ip.build_jobs(ROW)["p11"]
    assert len(jobs) == 1 and jobs[0]["stems"] == ["issue"], jobs

    alt = ROW.replace("clicking the number or title opens the issue detail page", "clicking the ticket number or title opens its page")
    assert ip.build_jobs(alt)["p11"][0]["stems"] == ["ticket"], ip.build_jobs(alt)["p11"]

    bare = ROW.replace("opens the issue detail page", "opens the detail page")
    assert ip.build_jobs(bare)["p11"] == [], ip.build_jobs(bare)["p11"]

P11_FUNCS = ["p11Segs", "p11Bind"]

@case
def p11_never_takes_the_longest_link_of_an_unrelated_numbered_row():

    out = js(P11_FUNCS, """
      const act = [{ j: 0, path: '/users/bob', text: 'bob-the-long-author-name' }, { j: 1, path: '/o/r/issues/3', text: 'Fix' }];
      return [
        p11Bind('#7', act, '/activity', ['Activity'], ['issue']),
        p11Bind('#7', act, '/o/r/issues', ['Issues'], ['issue']),
      ];""")
    if out is None:
        return
    assert out[0].get("skip") == "the page is not the stated list", out
    assert "skip" in out[1] and "link" not in out[1], out

@case
def p11_binds_the_rows_own_record_link_not_the_longest():
    out = js(P11_FUNCS, """
      const row = [{ j: 0, path: '/users/alice-with-a-much-longer-name', text: 'alice-with-a-much-longer-name' },
                   { j: 1, path: '/o/r/issues/3', text: 'Original issue title' }];
      const two = [{ j: 0, path: '/o/r/issues/3', text: 'Title' }, { j: 1, path: '/o/r/issues/3/edit/3', text: 'Edit' }];
      const same = [{ j: 0, path: '/o/r/issues/3', text: 'Title' }, { j: 1, path: '/o/r/issues/3?tab=comments', text: '2 comments' }];
      return [
        p11Bind('#3', row, '/o/r/issues', [], ['issue']),
        p11Bind('#3', row, '/dashboard', ['Open issues'], ['issue']),
        p11Bind('#3', two, '/o/r/issues', [], ['issue']),
        p11Bind('#3', same, '/o/r/issues', [], ['issue']),
        p11Bind('#3', row, '/o/r/issues', [], []),
      ];""")
    if out is None:
        return
    assert out[0] == {"link": 1} and out[1] == {"link": 1}, out
    assert out[2].get("skip") == "more than one record address in the row", out
    assert out[3] == {"link": 0}, out
    assert out[4].get("skip") == "no stated record kind", out

def p11_row(**kw):
    row = {"route": "/o/r/issues", "num": "#3", "activatable": False, "title": "Original issue title",
           "href": "/o/r/issues/3", "rowText": "Original issue title #3 Open alice", "numberPath": "/o/r/issues",
           "numberDialog": False, "titlePath": "/o/r/issues/3", "titleDenied": False, "titleLanded": True,
           "targetShowsTitle": True}
    row.update(kw)
    return row

@case
def p11_reports_only_when_the_title_opened_its_own_record_page():
    jobs = ip.build_jobs(ROW)["p11"]
    run = lambda **kw: ip.p11_findings(jobs, [{"id": jobs[0]["id"], "route": "/o/r/issues", "rows": [p11_row(**kw)]}], log=QUIET)
    assert len(run()) == 1
    assert run(titleLanded=False) == [] and run(targetShowsTitle=False) == []

@case
def p11_script_uses_the_binding():
    script = ip.PROBE_SCRIPT
    assert "p11Bind(r.num, r.links, pathOf(page), got.headings, stems)" in script
    assert "own(b).length - own(a).length" not in script
    assert "row.titleLanded = keyOf(row.titlePath) === keyOf(m.href)" in script
    assert "if (!row.titleLanded || !row.targetShowsTitle) row.skip" in script

P12_FUNCS = ["P12_NOT_TEXT", "p12Own", "p12Value"]

@case
def p12_a_token_elsewhere_on_the_page_does_not_make_the_form_its_own():
    out = js(P12_FUNCS, """
      const tok = 'zq1p12a';
      return [
        // audit #7: the seed row's inline form, the probe's value only in ANOTHER row
        p12Own({ prefill: 'Seed record', scope: 'row', textHasTok: false, inputHasTok: false, rowHasTok: false }, tok, true),
        p12Own({ prefill: 'Seed record', scope: '', }, tok, true),
        // one big form around the list: the value sits in another row of it
        p12Own({ prefill: 'seed@x.io', scope: 'form', textHasTok: true, inputHasTok: true, rowHasTok: true }, tok, false),
        // the same field: only its prefill binds it, never the form's other text
        p12Own({ prefill: 'Seed record', scope: 'form', textHasTok: true, inputHasTok: true, rowHasTok: false }, tok, true),
        // bound
        p12Own({ prefill: tok, scope: '' }, tok, true),
        p12Own({ prefill: '', scope: 'form', textHasTok: false, inputHasTok: true, rowHasTok: false }, tok, false),
        p12Own({ prefill: '', scope: 'row', textHasTok: true, inputHasTok: false, rowHasTok: false }, tok, false),
      ];""")
    if out is None:
        return
    assert out[:4] == ["", "", "", ""], out
    assert out[4:] == ["prefill", "form-input", "form-text"], out

@case
def p12_the_new_value_is_made_for_the_edited_field():
    out = js(P12_FUNCS, """
      const t = 'zq2p12b';
      return [
        p12Value({ type: 'text' }, 'Title', t, true),
        p12Value({ type: 'text' }, 'Email', t, false),
        p12Value({ type: 'email' }, 'Contact', t, false),
        p12Value({ type: 'url' }, 'Homepage', t, false),
        p12Value({ type: 'textarea' }, 'Description', t, false),
        p12Value({ type: 'text' }, 'Phone', t, false),
        p12Value({ type: 'number' }, 'Priority', t, false),
        p12Value({ type: 'date' }, 'Due', t, false),
        p12Value({ type: 'text', maxLength: 4 }, 'Code', t, false),
      ];""")
    if out is None:
        return
    assert out[:5] == ["zq2p12b", "zq2p12b@example.com", "zq2p12b@example.com", "https://example.com/zq2p12b", "zq2p12b"], out
    assert out[5:] == [None, None, None, None], out

@case
def p12_script_binds_the_form_and_checks_the_value():
    script = ip.PROBE_SCRIPT
    assert "res.ownBy = p12Own(info, tok1, sameField);" in script
    assert "(await mainText(page)).includes(tok1)" not in script
    assert "const value2 = p12Value(info, job.field, tok2, sameField);" in script
    assert "if (res.typed && !res.valueValid) { res.skip" in script

    assert "if (es.length === 1) { es[0].setAttribute('data-arc-edit', '1'); return true; }" in script
    assert "return shown.length === 1 ? shown[0] : null;" in script

def p12_res(**kw):
    res = {"shape": "B", "signedInAs": "alice", "createRoute": "/o/r/settings", "value1": "zq1p12a", "createStatus": [201],
           "created": True, "route": "/o/r/settings", "prefill": "zq1p12a", "own": True, "ownBy": "prefill",
           "sameField": True, "value2": "zq2p12b", "typed": True, "valueValid": True, "saveStatus": [404],
           "errorLines": ["Saving the rule failed"], "shownAfterSave": False,
           "reloaded": [{"path": "/o/r/settings", "hasNew": False, "hasOld": True}], "persisted": False}
    res.update(kw)
    return res

@case
def p12_findings_need_a_valid_value_and_say_how_it_was_made():
    jobs = ip.build_jobs(ACCOUNT + RULE)["p12"]
    run = lambda **kw: ip.p12_findings(jobs, [dict(p12_res(**kw), id=jobs[0]["id"])], log=QUIET)
    found = run()
    assert len(found) == 1 and "same form as the one the create accepted" in found[0]["problem"], found
    assert run(valueValid=False) == []
    other = run(sameField=False, value2="zq2p12b@example.com")
    assert len(other) == 1 and "made for that field" in other[0]["problem"] and "same form" not in other[0]["problem"], other

P10_FUNCS = ["P10_PLAIN", "P10_TRANSPORT", "p10ReplayHeaders"]

@case
def p10_replay_skips_unless_it_carries_the_restricted_accounts_own_credentials():
    out = js(P10_FUNCS, """
      const tpl = (headers, data) => ({ url: 'http://x/api/items', method: 'POST', headers, data: data || '{"title":"zq1p0cap"}' });
      const capC = { sid: 'cap-session-0123456789' };
      const ownC = { sid: 'rest-session-9876543210' };
      return [
        // audit #12: a custom auth header of the editor, never sent by the reader's session
        p10ReplayHeaders(tpl({ 'content-type': 'application/json', 'x-auth-user': 'editor-token-123' }), {}, capC, ownC),
        // the same header name from the reader's own requests: its own value is used
        p10ReplayHeaders(tpl({ 'content-type': 'application/json', 'x-auth-user': 'editor-token-123' }), { 'x-auth-user': 'reader-token-456' }, {}, {}),
        // bearer auth: the reader's own
        p10ReplayHeaders(tpl({ authorization: 'Bearer cap-abcdefgh' }), { authorization: 'Bearer rest-ijklmnop' }, {}, {}),
        // cookie session only, the reader's cookie differs: fine
        p10ReplayHeaders(tpl({ 'content-type': 'application/json', 'user-agent': 'x' }), {}, capC, ownC),
        // no own credential at all (the reader holds the same cookie / none)
        p10ReplayHeaders(tpl({ 'content-type': 'application/json' }), {}, capC, capC),
        p10ReplayHeaders(tpl({ 'content-type': 'application/json' }), {}, {}, {}),
        // the capture's credential in the body
        p10ReplayHeaders(tpl({ 'content-type': 'application/json' }, '{"title":"zq1p0cap","session":"cap-session-0123456789"}'), {}, capC, ownC),
      ];""")
    if out is None:
        return
    assert "never sends" in out[0].get("skip", ""), out
    assert out[1] == {"headers": {"content-type": "application/json", "x-auth-user": "reader-token-456"}}, out
    assert out[2] == {"headers": {"authorization": "Bearer rest-ijklmnop"}}, out
    assert out[3] == {"headers": {"content-type": "application/json"}}, out
    assert "no credential of the restricted account" in out[4].get("skip", "") and "skip" in out[5], out
    assert "in its address or body" in out[6].get("skip", ""), out

@case
def p10_script_replays_only_through_the_proven_headers():
    script = ip.PROBE_SCRIPT
    assert "const plan = p10ReplayHeaders(template, ownHeaders, capCookies, await cookieMap(s.context));" in script
    assert "headers: plan.headers, data" in script
    assert "entry.tookEffect = entry.replaySkip ? false :" in script
    assert "['cookie', 'authorization', 'host', 'content-length', 'origin', 'referer'].includes" not in script

@case
def p10_findings_never_report_a_skipped_or_unproven_replay():
    whole = ("- id: A description: Seed values: account `ed`, password `Pw-123456789!`; `ed` has Write role. account `rd`, "
             "password `Pw-123456789!`; `rd` has Read role. Only Write or Admin may add a comment: type into the "
             "“Comment” textbox and click “Add comment”. ")
    old = ip.os.environ.get("ARC_IPROBE_P10")
    ip.os.environ["ARC_IPROBE_P10"] = "1"
    try:
        jobs = ip.build_jobs(whole)["p10"]
    finally:
        if old is None:
            ip.os.environ.pop("ARC_IPROBE_P10", None)
        else:
            ip.os.environ["ARC_IPROBE_P10"] = old
    assert jobs, jobs
    acc = {"user": "rd", "roles": ["Read"], "signedIn": True, "controlHidden": True, "via": "replay", "status": 201,
           "value": "zq1p0r0", "tookEffect": True}
    run = lambda **kw: ip.p10_findings(jobs, [{"id": jobs[0]["id"], "route": "/r", "accounts": [dict(acc, **kw)]}], log=QUIET)
    assert len(run(authOwn=True)) == 1
    assert run(authOwn=False) == [] and run(replaySkip="no credential of the restricted account itself") == []

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
    print(f"{len(CASES) - failed}/{len(CASES)} r49v cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
