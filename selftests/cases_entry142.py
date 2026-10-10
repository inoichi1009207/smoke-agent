from __future__ import annotations

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

QUIET = lambda message: None

WHOLE = ("Users view available workbooks on the workbook home page. Each record displays the workbook name and provides "
         "a link whose accessible name is the workbook name.\nthe workbook `Q3 Sales`")
BATCH = "The user clicks the visible `Q3 Sales` workbook entry."

def page(aria: str, route: str = "/") -> dict:
    return {"route": route, "url": "http://127.0.0.1:3300" + route, "aria": aria, "links": []}

def audit(aria: str, whole: str = WHOLE, batch: str = BATCH, route: str = "/") -> list[dict[str, str]]:
    from agent.detaudit import deterministic_findings
    node = {"id": "REQ-2-1", "name": "Later batch", "description": batch, "requirement_text": whole}
    with tempfile.TemporaryDirectory() as tmp:
        return deterministic_findings(Path(tmp), node, [page(aria, route)], log=QUIET)

def item_links(found: list[dict[str, str]]) -> list[dict[str, str]]:
    return [f for f in found if "through a LINK" in f["problem"]]

def asks_button(found: list[dict[str, str]]) -> list[dict[str, str]]:
    return [f for f in found if "button" in f["fix"].lower()]

def asks_link(found: list[dict[str, str]]) -> list[dict[str, str]]:
    return [f for f in found if "<a" in f["fix"] or "role link" in f["fix"]]

@case
def conflict_1_title_carrying_ui_text_gets_one_direction_only():
    for name in ("Q3 SalesEdited today", "Open Q3 Sales"):
        aria = f'- list:\n  - listitem:\n    - link "{name}":\n      - /url: /workbooks/1\n'
        found = audit(aria)
        assert item_links(found) == [], (name, found)
        exact = [f for f in found if "accessible name is the workbook name" in f["requirement"]]
        assert len(exact) == 1 and name in exact[0]["problem"], (name, found)
        assert asks_link(found) and asks_button(found) == [], (name, found)

@case
def conflict_2_other_records_of_the_same_list_stay_links():
    aria = ('- list:\n  - listitem:\n    - link "Q3 Sales":\n      - /url: /workbooks/1\n'
            '  - listitem:\n    - link "Sample budget":\n      - /url: /workbooks/2\n')
    found = audit(aria)
    assert item_links(found) == [], found
    assert asks_button(found) == [], found

    nested = ('- list:\n  - listitem:\n    - heading "Q3 Sales" [level=3]:\n      - link "Q3 Sales":\n'
              '        - /url: /workbooks/1\n  - listitem:\n    - heading "Sample budget" [level=3]:\n'
              '      - link "Sample budget":\n        - /url: /workbooks/2\n')
    assert audit(nested) == [], audit(nested)

@case
def the_record_kind_of_the_statement_covers_a_list_without_the_seeded_name():
    aria = ('- list:\n  - listitem:\n    - heading "Sample budget" [level=3]:\n      - link "Sample budget":\n'
            '        - /url: /workbooks/2\n')
    assert item_links(audit(aria)) == []

    other = aria.replace("/workbooks/2", "/reports/2")
    found = item_links(audit(other))
    assert len(found) == 1 and "ONE button" in found[0]["fix"], found

@case
def item_links_still_fires_without_any_link_statement():
    whole = "Users browse books on the home page. GIVEN the book `Q3 Sales`."
    aria = ('- list:\n  - listitem:\n    - heading "Q3 Sales" [level=3]:\n      - link "Q3 Sales":\n'
            '        - /url: /books/1\n  - listitem:\n    - heading "Budget" [level=3]:\n      - link "Budget":\n'
            '        - /url: /books/2\n')
    found = item_links(audit(aria, whole=whole, batch="The user opens a book."))
    assert len(found) == 1 and "'Q3 Sales'" in found[0]["problem"] and "'Budget'" in found[0]["problem"], found

    open_kind = ("Users browse books on the home page. Each record provides a link or button whose accessible name is "
                 "the book name. GIVEN the book `Q3 Sales`.")
    from agent.entry_checks import statements
    assert statements(open_kind)["exact"] == []
    assert len(item_links(audit(aria, whole=open_kind, batch="The user opens a book."))) == 1

@case
def lists_are_grouped_by_their_parent_element():
    from agent.detaudit import item_title_link_groups, item_title_links
    aria = ('- list:\n  - listitem:\n    - link "A":\n      - /url: /books/1\n  - listitem:\n    - link "B":\n'
            '      - /url: /books/2\n- table:\n  - rowgroup:\n    - row:\n      - link "C":\n        - /url: /tags/3\n'
            '    - row:\n      - link "D":\n        - /url: /tags/4\n')
    assert item_title_link_groups(aria) == [[("A", "/books/1"), ("B", "/books/2")], [("C", "/tags/3"), ("D", "/tags/4")]]
    assert item_title_links(aria) == ["A", "B", "C", "D"]

@case
def cover_is_positive_only():
    from agent.entry_checks import covers_item_list, link_list_cover, statements
    cover = link_list_cover(statements(WHOLE))
    assert cover["seeds"] == {"q3 sales"} and cover["things"] == {"workbook"}, cover
    assert covers_item_list(cover, [("Q3 SalesEdited today", "/x/1")])
    assert covers_item_list(cover, [("Budget", "/workbooks/9")])
    assert not covers_item_list(cover, [("Budget", "/workbook-templates/9"), ("Q3", "/sales/1")])

    cover = link_list_cover(statements("On the home page each record has a button whose accessible name is the book "
                                       "name. GIVEN the book `Q3 Sales`."))
    assert not covers_item_list(cover, [("Q3 Sales", "/books/1")]), cover

KEEP = ("Display the notes list on the home page. Each note is exposed as a unique article named by its title.\n"
        "Edit the content of an existing note and persist it after closing the editor. Activating the note article opens "
        "a dialog named `Note editor` containing a textbox named `Note content` and a button named `Close`.")
ARTICLES = ('- main:\n  - article "Sprint goals":\n    - heading "Sprint goals" [level=3]:\n      - button "Sprint goals"\n'
            '  - article "Project ideas":\n    - heading "Project ideas" [level=3]:\n      - button "Project ideas"\n')

@case
def an_activation_statement_is_recognised_positively_only():
    from agent.activation_probe import activation_statements
    said = activation_statements(KEEP)
    assert [(s["role"], s["thing"], s["dialog"]) for s in said] == [("article", "note", "Note editor")], said
    assert activation_statements("Clicking a row opens the `Order details` dialog.")[0]["role"] == "row"
    for text in ("Activating the note article does not open a dialog named `Note editor`.",
                 "Activating the note article selects it; the `Note editor` dialog opens from the Edit button.",
                 "Activating the `Edit` button opens a dialog named `Note editor`.",
                 "Open the `Project ideas` note article and edit it."):
        assert activation_statements(text) == [], text

@case
def the_probe_goes_only_where_the_snapshot_shows_the_container():
    from agent.activation_probe import activation_statements, probe_routes
    statement = activation_statements(KEEP)[0]
    home = page(ARTICLES)
    other = page(ARTICLES, route="/archive")
    assert probe_routes(statement, [other, home]) == ["/"]
    assert probe_routes(statement, [page('- main:\n  - heading "Notes" [level=1]\n')]) == []

    redirected = dict(home, url="http://127.0.0.1:3300/login")
    assert probe_routes(statement, [redirected]) == []

@case
def a_finding_needs_the_contrast_container_silent_title_opens():
    from agent.activation_probe import activation_findings, activation_statements
    said = activation_statements(KEEP)
    v140 = [{"route": "/", "role": "article", "skipped": [], "item": "Sprint goals", "container": None,
             "title": {"dialog": "Note editor"}}]
    found = activation_findings(said, v140, log=QUIET)
    assert len(found) == 1 and "“Sprint goals”" in found[0]["problem"] and "closest(" in found[0]["fix"], found
    assert "Keep the title control" in found[0]["fix"]
    silent = [
        [dict(v140[0], container={"dialog": "Note editor"})],
        [dict(v140[0], container={"navigated": "http://x/notes/1"})],
        [dict(v140[0], title=None)],
        [dict(v140[0], title={"navigated": "http://x/notes/1"})],
        [dict(v140[0], item=None, skipped=["Sprint goals (middle on More options)"])],
        [dict(v140[0], error="Timeout")],
        None,
    ]
    for results in silent:
        assert activation_findings(said, results, log=QUIET) == [], results

@case
def the_audit_runs_the_probe_only_with_a_statement_and_a_browser():
    import agent.detaudit as detaudit
    calls = []

    def fake_probe(project_dir, jobs, log=print):
        calls.append(jobs)
        return [{"route": "/", "role": "article", "skipped": [], "item": "Sprint goals", "container": None,
                 "title": {"dialog": "Note editor"}}]

    real = detaudit.activation_probe
    detaudit.activation_probe = fake_probe
    try:
        found = audit(ARTICLES, whole=KEEP, batch="Edit the content of an existing note.")
        assert calls == [[{"route": "/", "role": "article"}]], calls
        assert [f for f in found if "opens nothing" in f["problem"]], found
        calls.clear()
        audit(ARTICLES, whole=KEEP.split("Edit the content")[0], batch="Show notes.")
        assert calls == []
    finally:
        detaudit.activation_probe = real

    assert [f for f in audit(ARTICLES, whole=KEEP, batch="Edit a note.") if "opens nothing" in f["problem"]] == []

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
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} entry142 self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
