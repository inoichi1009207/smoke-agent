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
    at = script.index(f"function {name}(")
    return script[at: script.index("\n}\n", at) + 2]

def js(names: list[str], body: str):
    node = shutil.which("node")
    if not node:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="r50v-js-"))
    try:
        src = "\n".join(_source(n) for n in names) + "\nconst out = (() => {\n" + body + "\n})();\nconsole.log(JSON.stringify(out));\n"
        (tmp / "t.cjs").write_text(src, encoding="utf-8")
        run = subprocess.run([node, str(tmp / "t.cjs")], capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert run.returncode == 0, run.stderr[-600:]
        return json.loads(run.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

P11_FUNCS = ["p11Segs", "p11Bind"]

@case
def p11_stem_is_the_whole_singular_noun():
    assert ip.build_jobs(ROW)["p11"][0]["stems"] == ["issue"], ip.build_jobs(ROW)["p11"]
    cat = ROW.replace("the issue number", "the category number").replace("opens the issue detail page", "opens the category detail page")
    assert ip.build_jobs(cat)["p11"][0]["stems"] == ["category"], ip.build_jobs(cat)["p11"]

@case
def p11_a_prefix_or_an_ancestors_id_is_no_record_binding():

    out = js(P11_FUNCS, """
      const st = ['issue'];
      return [
        p11Bind('#7', [{ j: 0, path: '/issue-reporters/7', text: 'Reporter seven' }], '/issues', [], st),
        p11Bind('#7', [{ j: 0, path: '/projects/7/issues/23', text: 'Some issue' }], '/projects/7/issues', [], st),
        // the list page itself must carry the stem as a whole segment (or a heading names it)
        p11Bind('#7', [{ j: 0, path: '/issues/7', text: 'Seven' }], '/issue-reporters', [], st),
      ];""")
    if out is None:
        return
    assert out[0].get("skip") and "link" not in out[0], out
    assert out[1].get("skip") and "link" not in out[1], out
    assert out[2].get("skip") == "the page is not the stated list", out

@case
def p11_binds_the_id_right_after_the_stem_segment():
    out = js(P11_FUNCS, """
      return [
        p11Bind('#7', [{ j: 0, path: '/projects/2/issues/7', text: 'Own' }], '/projects/2/issues', [], ['issue']),
        p11Bind('#7', [{ j: 0, path: '/users/7', text: 'bob' }, { j: 1, path: '/projects/7/issues/7', text: 'Own' }], '/projects/7/issues', [], ['issue']),
        p11Bind('#4', [{ j: 0, path: '/o/r/issue/4', text: 'Singular' }], '/o/r/issue', [], ['issue']),
        p11Bind('#5', [{ j: 0, path: '/categories/5', text: 'Tools' }], '/categories', [], ['category']),
        p11Bind('#5', [{ j: 0, path: '/categories/5', text: 'Tools' }], '/browse', ['Categories'], ['category']),
        p11Bind('#6', [{ j: 0, path: '/boxes/6', text: 'Box' }], '/boxes', [], ['box']),
      ];""")
    if out is None:
        return
    assert out == [{"link": 0}, {"link": 1}, {"link": 0}, {"link": 0}, {"link": 0}, {"link": 0}], out

P12_FUNCS = ["p12Own"]

@case
def p12_form_text_binds_a_cross_field_edit_only_through_a_row():
    out = js(P12_FUNCS, """
      const tok = 'zq1p12a';
      // audit r50 #4: one form around a div list and the editor; the page's only Edit opened the seed's Email
      const big = { prefill: 'seed@company.invalid', scope: 'form', textHasTok: true, inputHasTok: false, rowHasTok: false };
      return [
        p12Own(Object.assign({ rowBound: false }, big), tok, false),
        p12Own(big, tok, false),
        p12Own(Object.assign({ rowBound: true }, big), tok, false),        // the Edit of the probe's own row opened it
        p12Own({ prefill: '', scope: 'row', textHasTok: true, inputHasTok: false, rowHasTok: false, rowBound: false }, tok, false),
        p12Own({ prefill: '', scope: 'form', textHasTok: true, inputHasTok: true, rowHasTok: false, rowBound: false }, tok, false),
        p12Own({ prefill: tok, scope: 'form', textHasTok: true, rowBound: false }, tok, false),
        p12Own(Object.assign({ rowBound: true }, big), tok, true),         // same field: only its prefill binds
      ];""")
    if out is None:
        return
    assert out == ["", "", "form-text", "form-text", "form-input", "prefill", ""], out

@case
def p12_script_passes_how_the_edit_control_was_bound():
    script = ip.PROBE_SCRIPT
    assert "info.rowBound = editBy === 'row';" in script
    assert "res.ownBy = p12Own(info, tok1, sameField);" in script
    assert "if (inRow) { editBy = 'row'; return page.locator('[data-arc-edit]').first(); }" in script
    assert "if (shown.length === 1) editBy = 'page';" in script
    assert "if (marked) editBy = 'row';" in script
    assert "res.editBy = editBy;" in script

def p12_res(**kw):
    res = {"shape": "B", "signedInAs": "alice", "createRoute": "/o/r/settings", "value1": "zq1p12a", "createStatus": [201],
           "created": True, "route": "/o/r/settings", "prefill": "", "own": True, "ownBy": "form-text", "editBy": "row",
           "sameField": False, "value2": "zq2p12b@example.com", "typed": True, "valueValid": True, "saveStatus": [422],
           "errorLines": [], "shownAfterSave": False,
           "reloaded": [{"path": "/o/r/settings", "hasNew": False, "hasOld": True}], "persisted": False}
    res.update(kw)
    return res

@case
def p12_a_validation_status_is_never_a_save_defect():
    jobs = ip.build_jobs(ACCOUNT + RULE)["p12"]
    logs = []
    run = lambda **kw: ip.p12_findings(jobs, [dict(p12_res(**kw), id=jobs[0]["id"])], log=logs.append)
    for status in (400, 409, 422):
        assert run(saveStatus=[status]) == [], status
        assert run(saveStatus=[status], errorLines=["Saving failed"]) == [], status
    assert run(saveStatus=[422, 500]) == []
    assert any("validation refusal" in m for m in logs), logs[-1:]

    assert len(run(saveStatus=[404])) == 1 and len(run(saveStatus=[500])) == 1
    assert len(run(saveStatus=[200], errorLines=["Saving the rule failed"])) == 1

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
    print(f"{len(CASES) - failed}/{len(CASES)} r50v cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
