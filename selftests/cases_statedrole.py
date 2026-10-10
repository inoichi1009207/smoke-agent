from __future__ import annotations

import inspect
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

REQ_6_2_2 = ("The system validates the role and rejects unauthorized requests. New pull request is a link. The "
             "comparison page has native select controls with combobox roles named base and compare; each option's "
             "visible label is its exact branch name. Compare changes is a button. For a valid selection it displays "
             "the known changed file path verbatim, a Commit summary with the comparable commit count, and an enabled "
             "Create pull request button.")

REQ_4_1 = ("The directory page displays the current branch, path breadcrumbs, and the file list for that directory; the "
           "file page displays the full path, current branch, and readable file content, and the breadcrumbs can return "
           "to the parent directory.")

def _requirement_yaml(items):
    import json
    lines = ["requirements:"]
    for rid, text in items:
        lines.append(f"  - id: {rid}")
        lines.append(f"    description: {json.dumps(text, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"

def _project(tmp, files):
    root = Path(tmp)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

COMPARE_H1 = """export default function ComparePage() {
  return (
    <main>
        <h1 className="mb-6 text-2xl font-bold">Compare changes</h1>
      <label htmlFor="compare">
              compare
      </label>
    </main>
  );
}
"""

def _problems(requirement, files):
    from agent.stated_controls import stated_control_problems
    with tempfile.TemporaryDirectory() as tmp:
        return [p for p in stated_control_problems(requirement, _project(tmp, files)) if p["name"] == "Compare changes"]

REQ = _requirement_yaml([("REQ-6-2-2", REQ_6_2_2)])

@case
def a_stated_button_shown_only_as_a_heading_is_a_role_finding():
    found = _problems(REQ, {"frontend/src/pages/ComparePage.tsx": COMPARE_H1})
    assert len(found) == 1 and found[0]["kind"] == "stated control not a control", found
    assert found[0]["rendered"] == "heading" and found[0]["role"] == "button", found
    assert found[0]["evidence"].startswith("frontend/src/pages/ComparePage.tsx:4 text inside <h1> -> role heading"), found

@case
def a_stated_button_shown_only_as_a_plain_text_node_is_a_role_finding():
    page = COMPARE_H1.replace('<h1 className="mb-6 text-2xl font-bold">Compare changes</h1>',
                              '<div className="tab"><span>Compare changes</span></div>')
    found = _problems(REQ, {"frontend/src/pages/ComparePage.tsx": page})
    assert len(found) == 1 and found[0]["rendered"] == "text", found
    assert "plain text, no role" in found[0]["evidence"], found

@case
def detaudit_never_tells_the_implementer_to_replace_the_heading():

    from agent import detaudit
    node = {"id": "REQ-6-2-2", "name": "REQ-6-2-2", "description": REQ_6_2_2, "scenarios": [], "requirement_text": REQ}
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(tmp, {"frontend/src/pages/ComparePage.tsx": COMPARE_H1})
        findings, _counts = detaudit.stated_control_findings(node, root)
    mine = [f for f in findings if "Compare changes" in f["problem"] and "another role" in f["problem"]]
    assert all("in place of the element" not in " ".join(f["fix"].split()) for f in mine), mine
    if "stated control not a control" in inspect.getsource(detaudit.stated_control_findings):
        assert len(mine) == 1 and mine[0]["where"] == "frontend/src/pages/ComparePage.tsx:4", findings
        assert "Keep that heading" in mine[0]["fix"] and "ADD" in mine[0]["fix"], mine
    else:
        assert mine == [], mine

@case
def a_real_button_beside_the_heading_silences_it():
    page = COMPARE_H1.replace("</main>", '  <button type="button">Compare changes</button>\n    </main>')
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": page}) == []

@case
def a_string_literal_that_may_feed_a_control_silences_it():
    page = COMPARE_H1.replace("</main>", "  <div>{['Compare changes'].map((l) => <Action key={l} label={l} />)}</div>\n    </main>")
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": page}) == []

    alone = COMPARE_H1.replace('<h1 className="mb-6 text-2xl font-bold">Compare changes</h1>',
                               "<h1>{busy ? 'Compare changes' : 'Comparing'}</h1>")
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": alone}) == []

@case
def a_name_composed_at_runtime_silences_it():
    page = COMPARE_H1.replace("</main>", '  <button type="button">{verb} changes</button>\n    </main>')
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": page}) == []

@case
def a_name_split_over_child_tags_silences_it():
    page = COMPARE_H1.replace("</main>", '  <button type="button">Compare <strong>changes</strong></button>\n    </main>')
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": page}) == []
    other = COMPARE_H1.replace("</main>", '  <button type="button"><b>Compare</b> changes</button>\n    </main>')
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": other}) == []

@case
def a_label_supplied_by_the_backend_silences_it():
    files = {"frontend/src/pages/ComparePage.tsx": COMPARE_H1,
             "backend/src/actions.js": "module.exports = [{ id: 'compare', label: 'Compare changes' }];\n"}
    assert _problems(REQ, files) == []

@case
def a_name_the_requirement_also_states_as_a_heading_is_left_alone():
    both = _requirement_yaml([("REQ-6-2-2", REQ_6_2_2), ("REQ-6-2-3", 'The page shows the heading "Compare changes".')])
    assert _problems(both, {"frontend/src/pages/ComparePage.tsx": COMPARE_H1}) == []

@case
def a_label_or_a_presentation_element_stays_silent():
    label = COMPARE_H1.replace('<h1 className="mb-6 text-2xl font-bold">Compare changes</h1>', "<label>Compare changes</label>")
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": label}) == []

    attr = COMPARE_H1.replace('<h1 className="mb-6 text-2xl font-bold">Compare changes</h1>',
                              '<div role="none" title="Compare changes"><Icon /></div>')
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": attr}) == []

    pres = COMPARE_H1.replace('<h1 className="mb-6 text-2xl font-bold">Compare changes</h1>',
                              '<div role="presentation">Compare changes</div>')
    found = _problems(REQ, {"frontend/src/pages/ComparePage.tsx": pres})
    assert len(found) == 1 and found[0]["rendered"] == "text", found

@case
def a_heading_beside_a_control_of_another_role_keeps_the_old_rule():

    page = COMPARE_H1.replace("</main>", '  <a href="/compare">Compare changes</a>\n    </main>')
    assert _problems(REQ, {"frontend/src/pages/ComparePage.tsx": page}) == []

    only = COMPARE_H1.replace('<h1 className="mb-6 text-2xl font-bold">Compare changes</h1>', '<a href="/c">Compare changes</a>')
    found = _problems(REQ, {"frontend/src/pages/ComparePage.tsx": only})
    assert len(found) == 1 and "rendered" not in found[0] and "-> role link" in found[0]["evidence"], found

APP = """import { Routes, Route } from 'react-router-dom';
export default function App() {
  return (
    <Routes>
      <Route path="/:owner/:repo" element={<RepositoryPage />} />
      <Route path="/:owner/:repo/tree/:branch/*" element={<DirectoryPage />} />
      <Route path="/:owner/:repo/blob/:branch/*" element={<FilePage />} />
    </Routes>
  );
}
"""

V141_FILE = """import { Link } from 'react-router-dom';
function FilePage() {
  return (
        <nav className="mb-4 flex items-center gap-1 text-sm" aria-label="Breadcrumb">
          {segments.map((segment, index) => {
            const isLast = index === segments.length - 1;
            const partial = segments.slice(0, index + 1).join('/');
            return (
              <span key={partial} className="flex items-center gap-1">
                <span>/</span>
                {isLast ? (
                  <Link
                    to={`/${owner}/${repo}/blob/${branch}/${partial}`}
                    className="font-semibold text-blue-700 hover:underline"
                  >
                    {segment}
                  </Link>
                ) : (
                  <Link
                    to={`/${owner}/${repo}/blob/${branch}/${partial}`}
                    className="text-blue-700 hover:underline"
                  >
                    {segment}
                  </Link>
                )}
              </span>
            );
          })}
        </nav>
  );
}
export default FilePage;
"""

V126_FILE = """import { Link } from 'react-router-dom';
function FilePage() {
  return (<nav>
          {segments.map((segment, index) => {
            const prefix = segments.slice(0, index + 1).join('/');
            const isLast = index === segments.length - 1;
            return (
              <span key={prefix}>
                <Link
                  to={`/${owner}/${repo}/blob/${currentBranch}/${prefix}`}
                  className={isLast ? 'font-medium text-blue-700' : 'text-blue-700'}
                >
                  {segment}
                </Link>
              </span>
            );
          })}
  </nav>);
}
export default FilePage;
"""
CRUMB_REQ = _requirement_yaml([("REQ-4-1", REQ_4_1)])

def _crumbs(files, requirement=CRUMB_REQ):
    from agent.breadcrumbs import breadcrumb_findings
    with tempfile.TemporaryDirectory() as tmp:
        return breadcrumb_findings(requirement, _project(tmp, files))

@case
def the_v141_and_v126_file_page_crumbs_are_found():
    for page, line in ((V141_FILE, 5), (V126_FILE, 4)):
        found = _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": page})
        assert len(found) == 1 and found[0]["where"] == f"frontend/src/pages/FilePage.tsx:{line}", found
        assert "/:owner/:repo/blob/:branch/*" in found[0]["problem"] and "directory view" in found[0]["fix"], found
        assert set(found[0]) == {"where", "requirement", "problem", "fix"}

@case
def crumbs_to_the_directory_route_are_fine():
    tree = V141_FILE.replace("/blob/${branch}/${partial}", "/tree/${branch}/${partial}")
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": tree}) == []

    mixed = V141_FILE.replace("/blob/${branch}/${partial}`}\n                    className=\"text-blue-700",
                              "/tree/${branch}/${partial}`}\n                    className=\"text-blue-700")
    assert mixed != V141_FILE
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": mixed}) == []

@case
def one_target_under_an_element_choice_may_be_the_last_crumb_only():
    last_only = V141_FILE.replace(""") : (
                  <Link
                    to={`/${owner}/${repo}/blob/${branch}/${partial}`}
                    className="text-blue-700 hover:underline"
                  >
                    {segment}
                  </Link>
                )}""", """) : (
                  <span>{segment}</span>
                )}""")
    assert last_only != V141_FILE
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": last_only}) == []

@case
def no_breadcrumb_in_the_requirement_no_finding():
    other = _requirement_yaml([("REQ-4-1", "The file page displays the full path and readable file content.")])
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": V141_FILE}, other) == []

@case
def a_page_that_can_show_a_directory_is_left_alone():
    aware = V141_FILE.replace("function FilePage() {", "function FilePage() {\n  if (data.type === 'dir') return <DirectoryListing />;")
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": aware}) == []

@case
def a_directory_page_linking_to_itself_is_fine():
    app = APP.replace('/blob/:branch/*" element={<FilePage />}', '/blob/:branch/*" element={<CodePage />}')
    page = V141_FILE.replace("FilePage", "CodePage")
    assert _crumbs({"frontend/src/App.tsx": app, "frontend/src/pages/CodePage.tsx": page}) == []

    view = APP.replace("/blob/:branch/*", "/view/:branch/*")
    moved = V141_FILE.replace("/blob/${branch}", "/view/${branch}")
    assert _crumbs({"frontend/src/App.tsx": view, "frontend/src/pages/FilePage.tsx": moved}) == []

@case
def a_crumb_with_a_handler_or_an_unresolved_target_is_left_alone():
    handler = V126_FILE.replace("<Link\n", "<Link onClick={() => open(prefix)}\n")
    assert handler != V126_FILE
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": handler}) == []
    helper = V126_FILE.replace("to={`/${owner}/${repo}/blob/${currentBranch}/${prefix}`}", "to={dirTarget(prefix)}")
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/FilePage.tsx": helper}) == []

@case
def a_crumb_on_another_page_linking_to_the_file_route_is_not_this_check():

    page = V141_FILE.replace("FilePage", "DirectoryPage")
    assert _crumbs({"frontend/src/App.tsx": APP, "frontend/src/pages/DirectoryPage.tsx": page}) == []

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} statedrole cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
