from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES: list = []
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

GITHUB_WHOLE = NL.join([
    "Visitors enter the account-access page from “Sign up”, “Sign in”, or “Forgot password” on the home page; signed-in"
    " users enter security settings or sign out from the account menu.",
    "The registration page is the form opened by the unique link named “Create an account” from the sign-in page.",
    "The visitor clicks “Sign in” on the home page to enter the account-access page, then clicks “Create an account”.",
    "The sign-in page is the form on the account-access page containing “Username or email”, “Password”, and “Sign in”.",
])
SIGN_IN_BATCH = ("The sign-in page is the form on the account-access page containing “Username or email”, “Password”, and"
                 " “Sign in”. The visitor enters a username or email and a password and submits the form.")

SHEET_WHOLE = "The evaluation seed contains the seeded workbook `Q3 Sales` with `Sheet1` and `Sheet2`, rows `East/1200` and `North/800`."

def _run(batch_text: str, whole: str, snapshot: list) -> tuple[list, list]:
    from agent import detaudit
    node = {"id": "REQ-9-9", "name": "REQ-9-9", "description": batch_text, "scenarios": [], "requirement_text": whole}
    logs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        found = detaudit.deterministic_findings(Path(tmp), node, snapshot, log=logs.append)
    return found, logs

def _aria(*links: tuple[str, str]) -> str:
    rows = ["- banner:"]
    for name, href in links:
        rows += [f'  - link "{name}":', f"    - /url: {href}"]
    return NL.join(rows)

def _same_href_findings(found: list, href: str, names: tuple[str, ...]) -> list:
    hits = []
    for finding in found:
        text = " ".join(str(finding.get(k) or "") for k in ("where", "requirement", "problem", "fix"))
        if href in text and all(name in text for name in names):
            hits.append(finding)
    return hits

def _logged(logs: list, href: str, names: tuple[str, ...]) -> list:
    return [line for line in logs if "(diagnostic, not reported)" in line and f"all lead to {href}" in line
            and all(repr(name) in line for name in names)]

@case
def sign_in_and_sign_up_to_one_page_is_only_logged():
    snapshot = [{"route": "/", "aria": _aria(("Home", "/"), ("Sign in", "/signin"), ("Sign up", "/signin"))},
                {"route": "/search", "aria": _aria(("Sign in", "/signin"), ("Sign up", "/signin"))}]
    found, logs = _run(SIGN_IN_BATCH, GITHUB_WHOLE, snapshot)
    assert _same_href_findings(found, "/signin", ("Sign in", "Sign up")) == [], found
    lines = _logged(logs, "/signin", ("Sign in", "Sign up"))
    assert len(lines) == 2 and "route /:" in lines[0] and "route /search:" in lines[1], logs
    assert any("same_href=2" in line for line in logs if "deterministic layer:" in line), logs

@case
def sign_up_and_create_an_account_to_the_registration_page_is_only_logged():
    snapshot = [{"route": "/signin", "links": [{"name": "Sign up", "href": "/register"},
                                              {"name": "Create an account", "href": "/register"},
                                              {"name": "Sign in", "href": "/signin"}]}]
    found, logs = _run(SIGN_IN_BATCH, GITHUB_WHOLE, snapshot)
    assert _same_href_findings(found, "/register", ("Sign up", "Create an account")) == [], found
    assert len(_logged(logs, "/register", ("Sign up", "Create an account"))) == 1, logs
    assert not _logged(logs, "/signin", ("Sign in",)), logs

@case
def worksheet_names_on_a_workbook_card_are_only_logged():
    href = "/workbook/wb-q3-sales"
    snapshot = [{"route": "/", "aria": _aria(("Q3 Sales", href), ("Sheet1", href), ("Sheet2", href))}]
    found, logs = _run("Open a workbook from the home page.", SHEET_WHOLE, snapshot)
    assert _same_href_findings(found, href, ("Sheet1", "Sheet2")) == [], found
    assert len(_logged(logs, href, ("Q3 Sales", "Sheet1", "Sheet2"))) == 1, logs

@case
def one_name_twice_the_root_and_external_links_are_not_logged():
    snapshot = [{"route": "/", "aria": _aria(("Home", "/"), ("Logo", "/"), ("Sign in", "/signin"), ("SIGN IN", "/signin"),
                                             ("Docs", "https://example.test/docs"), ("Help", "https://example.test/docs"))}]
    found, logs = _run(SIGN_IN_BATCH, GITHUB_WHOLE, snapshot)
    assert not [line for line in logs if "all lead to" in line], logs
    assert any("same_href=0" in line for line in logs if "deterministic layer:" in line), logs

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} v145c cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
