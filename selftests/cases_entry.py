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

GH_REQ4 = ("Write scenarios provide separate existing contributor accounts with Write or higher permission on their target "
           "repositories. These accounts sign in through the home-page “Sign in” link, the “Username or "
           "email” and “Password” fields, and the “Sign in” button; the signed-in username is "
           "visible before opening the target repository entry. Unless a narrower scope is explicitly given below, each "
           "named control is unique on the active page, and field and option names refer to accessible names.")
GH_REQ6 = ("Signed-in scenarios begin with the unique Sign in link on the home page, labeled Username or email and Password "
           "fields, a Sign in button, and the account username visible after authentication; each role account has a "
           "username, verified email, and password.")

SHEET = ("Users view available workbooks on the workbook home page. Each record displays \"Last updated: <last updated "
         "value>\" and provides a link whose accessible name is the workbook name. After the user clicks the link, the "
         "editor displays the same \"Last updated: <last updated value>\".\n"
         "GIVEN the seeded workbook `Q3 Sales` exists.")

def page(aria: str, route: str = "/", url: str | None = None, links: list[dict] | None = None, **extra) -> dict:
    entry = {"route": route, "url": "http://127.0.0.1:3300" + (url if url is not None else route), "aria": aria}
    if links is not None:
        entry["links"] = links
    entry.update(extra)
    return entry

def link(name: str, href: str = "/login") -> dict:
    return {"name": name, "href": href, "inItem": False}

def run(whole: str, snapshot: list[dict], earlier: list | None = None):
    from agent.entry_checks import entry_findings, statements
    found, counts = entry_findings(statements(whole), snapshot, earlier or [], log=QUIET)
    return found, counts

V103_ARIA = ('- banner:\n  - link "GitHub":\n    - /url: /\n  - link "Sign in":\n    - /url: /login\n'
             '- main:\n  - heading "Welcome" [level=1]\n  - paragraph:\n    - link "Sign in":\n      - /url: /login\n')

@case
def github_v103_two_visible_sign_in_links_on_the_home_page_are_reported():
    for whole in (GH_REQ4, GH_REQ6):
        found, counts = run(whole, [page(V103_ARIA, links=[link("GitHub", "/"), link("Sign in"), link("Sign in")])])
        assert counts["entry_unique"] == 1, (whole[:40], found)
        assert found[0]["where"] == "route /" and "2 link elements with the accessible name `Sign in`" in found[0]["problem"]

    one = V103_ARIA.replace('  - paragraph:\n    - link "Sign in":\n      - /url: /login\n', "")
    assert run(GH_REQ6, [page(one, links=[link("GitHub", "/"), link("Sign in")])])[0] == []

@case
def a_copy_hidden_for_another_screen_size_or_of_zero_size_is_not_counted():

    one = '- banner:\n  - link "Sign in":\n    - /url: /login\n- main:\n  - heading "Welcome" [level=1]\n'
    assert run(GH_REQ6, [page(one, links=[link("Sign in")])])[0] == []

    found, _ = run(GH_REQ6, [page(V103_ARIA, links=[link("GitHub", "/"), link("Sign in")])])
    assert found == [], found

    assert run(GH_REQ6, [page(V103_ARIA)])[0] == []

@case
def only_pages_the_snapshot_reached_are_read():

    assert run(GH_REQ6, [page(V103_ARIA, url="/login", links=[link("Sign in"), link("Sign in")])])[0] == []

    assert run(GH_REQ6, [page(V103_ARIA, links=[link("Sign in"), link("Sign in")], crawled=True)])[0] == []
    assert run(GH_REQ6, [page(V103_ARIA, links=[link("Sign in"), link("Sign in")], error="Timeout")])[0] == []

    assert run(GH_REQ6, [page(V103_ARIA, route="/about", links=[link("Sign in"), link("Sign in")])])[0] == []

@case
def uniqueness_inside_a_stated_container_is_counted_per_container():
    text = "The user activates the unique link or button named `Login` in the homepage navigation bar."
    two_navs = ('- navigation "Main":\n  - link "Login":\n    - /url: /login\n'
                '- navigation "Footer":\n  - button "Login"\n')
    assert run(text, [page(two_navs, links=[link("Login")])])[0] == []
    outside = '- navigation:\n  - link "Login":\n    - /url: /login\n- main:\n  - button "Login"\n'
    assert run(text, [page(outside, links=[link("Login")])])[0] == []

    inside = '- navigation:\n  - link "Login":\n    - /url: /login\n  - button "Login"\n'
    found, counts = run(text, [page(inside, links=[link("Login")])])
    assert counts["entry_unique"] == 1 and "inside one navigation" in found[0]["problem"], found

@case
def the_accessible_name_is_the_computed_one():

    aria = '- list:\n  - listitem:\n    - link "Q3 Sales":\n      - /url: /workbooks/1\n    - text: "Last updated: 2026-09-29"\n'
    assert run(SHEET, [page(aria, links=[link("Q3 Sales", "/workbooks/1")])])[0] == []

@case
def sheet_v124_link_named_with_the_last_updated_text_is_reported():
    aria = ("- list:\n  - listitem:\n    - 'link \"Q3 SalesLast updated: 2026-09-29T00:17:28.403Z\"':\n"
            "      - /url: /workbooks/1\n")
    found, counts = run(SHEET, [page(aria, links=[])])
    assert counts["entry_exact_name"] == 1, found
    assert "Q3 SalesLast updated" in found[0]["problem"] and "outside the element" in found[0]["fix"]

    for name in ("Q3 Sales Last updated: 2026-09-29", "Open Q3 Sales", "Q3 Sales · 9/29/2026"):
        aria2 = f'- list:\n  - listitem:\n    - \'link "{name}"\':\n      - /url: /workbooks/1\n'
        assert run(SHEET, [page(aria2, links=[])])[1]["entry_exact_name"] == 1, name

    for name in ("Q3 Sales Copy", "Q3 Sales 2", "Q3 Salesforce"):
        aria3 = f'- list:\n  - listitem:\n    - link "{name}":\n      - /url: /workbooks/2\n'
        assert run(SHEET, [page(aria3, links=[])])[0] == [], name

@case
def sheet_v112_workbook_entry_rendered_as_a_button_is_reported():
    aria = '- list:\n  - listitem:\n    - heading "Q3 Sales" [level=3]:\n      - button "Q3 Sales"\n'
    found, counts = run(SHEET, [page(aria, links=[])])
    assert counts["entry_role"] == 1, found
    assert "rendered here as a button" in found[0]["problem"] and "<a href>" in found[0]["fix"]

    both = aria + '    - link "Q3 Sales":\n      - /url: /workbooks/1\n'
    assert run(SHEET, [page(both, links=[link("Q3 Sales", "/workbooks/1")])])[0] == []

    assert run(SHEET, [page('- main:\n  - heading "Workbooks" [level=1]\n', links=[])])[0] == []

@case
def a_statement_needs_its_page_and_positive_wording():
    from agent.entry_checks import statements

    parsed = statements("The visitor activates the unique “Sign in” link on the home page and then the unique "
                        "“Create an account” link.")
    pages = {s["name"]: s["page"] for s in parsed["unique"]}
    assert pages == {"Sign in": ("home", ""), "Create an account": ("", "")}, pages
    parsed = statements("The registration page is the form opened by the unique link named “Create an account” "
                        "from the sign-in page.")
    assert [s["page"] for s in parsed["unique"]] == [("", "")]

    assert statements("Each row on the home page has the unique `Edit` link.")["unique"] == []
    assert statements("The `Save` button on the home page is not unique.")["unique"] == []
    assert statements("Visitors need not activate the unique `Help` link on the home page.")["unique"] == []
    no_page = statements("Activate the unique `Settings` button.")["unique"]
    assert [s["page"] for s in no_page] == [("", "")]
    aria = '- banner:\n  - button "Settings"\n- main:\n  - button "Settings"\n'
    assert run("Activate the unique `Settings` button.", [page(aria, links=[])])[0] == []

    blanket = statements(GH_REQ4)["unique"]
    assert [(s["name"], s["page"], s["how"]) for s in blanket] == [("Sign in", ("home", ""), "blanket")], blanket

    parsed = statements("Users view workbooks on the workbook home page. The search page shows a link whose accessible "
                        "name is the workbook name. Seed: workbook `Q3 Sales`.")
    assert [e["page"] for e in parsed["exact"]] == [("slug", "search")]
    parsed = statements("Users view workbooks on the workbook home page. A link whose accessible name is the workbook "
                        "name opens the search page. Seed: workbook `Q3 Sales`.")
    assert [e["page"] for e in parsed["exact"]] == [("", "")]

@case
def findings_already_made_by_detaudit_are_not_repeated():
    from agent.detaudit import deterministic_findings
    snapshot = [page(V103_ARIA, links=[link("GitHub", "/"), link("Sign in"), link("Sign in")])]
    with tempfile.TemporaryDirectory() as tmp:

        node = {"id": "REQ-6", "name": "Pull Requests", "description": GH_REQ6, "requirement_text": GH_REQ6}
        found = deterministic_findings(Path(tmp), node, snapshot, log=QUIET)
        said = [f for f in found if "Sign in" in f["problem"] and "unique" in (f["requirement"] + f["problem"])]
        assert len(said) == 1, found

        node = {"id": "REQ-1-1", "name": "Browse", "description": "Visitors browse public repositories.",
                "requirement_text": GH_REQ4 + "\n" + GH_REQ6}
        found = deterministic_findings(Path(tmp), node, snapshot, log=QUIET)
        said = [f for f in found if "`Sign in`" in f["problem"]]
        assert len(said) == 1 and "accessible name `Sign in`" in said[0]["problem"], found

    aria = ("- list:\n  - listitem:\n    - 'link \"Q3 SalesLast updated: 2026-09-29T00:17:28.403Z\"':\n"
            "      - /url: /workbooks/1\n")
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-1-1-1", "name": "View", "description": SHEET, "requirement_text": SHEET}
        found = deterministic_findings(Path(tmp), node, [page(aria, links=[])], log=QUIET)
        said = [f for f in found if "Q3 SalesLast" in f["problem"]]
        assert len(said) == 1, found

@case
def a_title_the_requirement_states_as_a_link_is_not_turned_into_a_button():

    from agent.detaudit import deterministic_findings
    aria = ('- list:\n  - listitem:\n    - heading "Q3 Sales" [level=3]:\n      - link "Q3 Sales":\n'
            '        - /url: /workbooks/1\n  - listitem:\n    - heading "Budget" [level=3]:\n      - link "Budget":\n'
            '        - /url: /workbooks/2\n')
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-2-1", "name": "Edit cells", "description": "The user edits a cell of the grid.",
                "requirement_text": SHEET}
        found = deterministic_findings(Path(tmp), node, [page(aria, links=[link("Q3 Sales", "/workbooks/1")])], log=QUIET)

        items = [f for f in found if "through a LINK" in f["problem"]]
        assert items == [], found

        other = aria + ('- list:\n  - listitem:\n    - heading "Urgent" [level=3]:\n      - link "Urgent":\n'
                        '        - /url: /tags/7\n')
        found = deterministic_findings(Path(tmp), node, [page(other, links=[link("Q3 Sales", "/workbooks/1")])], log=QUIET)
        items = [f for f in found if "through a LINK" in f["problem"]]
        assert len(items) == 1 and "'Urgent'" in items[0]["problem"] and "Budget" not in items[0]["problem"], found

@case
def census_of_the_sample_requirements():
    samples = ROOT.parent / "codex-audit" / "requirements-samples"
    if not (samples / "hackathon-github-platform.yaml").is_file():
        print("      (skipped: no requirement samples next to the agent)")
        return
    from agent.entry_checks import statements
    expected = {
        "keep.yaml": set(), "keep-lite.yaml": set(), "ticket-booking.yaml": set(), "bookstack.yaml": set(),
        "ctrip.yaml": set(),
        "hackathon-github-platform.yaml": {("U", "link", "Sign in", "home"), ("U", "button", "Add file", "writable-code"),
                                           ("E", "link", "file", "active-search")},
        "hackathon-sheet-platform.yaml": {("E", "link", "workbook", "home")},
        "bookstack-lite.yaml": {("U", "button", "BookStack", "home"), ("U", "button", "BookStack", "every"),
                                ("U", "heading", "BookStack", "home"), ("U", "heading", "Login", "login"),
                                ("U", "button/link", "Login", "home"), ("U", "heading", "Login", "login-form"),
                                ("U", "button", "Confirm Delete", "dialog-or-confirmation"),
                                ("U", "button", "Cancel", "dialog-or-confirmation"),
                                ("U", "button/link", "Book 5.2.2", "shelf-details"),
                                ("U", "button", "Favorite", "current-content")},
    }
    for name, wanted in expected.items():
        path = samples / name
        if not path.is_file():
            continue
        parsed = statements(path.read_text(encoding="utf-8", errors="replace"))
        got = {("U", "/".join(sorted(s["roles"])), s["name"], s["page"][1] or s["page"][0])
               for s in parsed["unique"] if s["page"][0]}
        got |= {("E", "/".join(sorted(s["roles"])), s["thing"], s["page"][1] or s["page"][0])
                for s in parsed["exact"] if s["page"][0]}
        assert got == wanted, (name, sorted(got ^ wanted))
    sheet = statements((samples / "hackathon-sheet-platform.yaml").read_text(encoding="utf-8", errors="replace"))
    assert [e["seeds"] for e in sheet["exact"]] == [["Q3 Sales"]]

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
    print(f"{len(CASES) - failed}/{len(CASES)} entry self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
