from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import session_probe as sp

REQ = """
- id: REQ-1
  description: >-
    The system pre-provisions the verified account `org-owner` with email `org-owner@example.test` and password
    `Valid-password-123!`; the account can create organizations. The system pre-provisions the verified account
    `team-maintainer` with email `team-maintainer@example.test` and password `Other-password-456!`.
    Seed values: account `alice-dev`, email `alice.dev@example.test`, password `Alice-password-1!`.
"""

class _A:
    def __init__(self, status):
        self.status, self.cookies, self.body = status, ({"sid": "x"} if 200 <= status < 300 else {}), ""

def _with_server(accounts: dict[str, str], other_status=401):
    calls = []

    def fake(base, method, path, body, cookies, deadline):
        calls.append(body)
        who = body.get("login") or body.get("username")
        if accounts.get(who) == body.get("password"):
            return _A(200)
        return _A(other_status)
    return fake, calls

def wording_is_read():
    creds = sp.credentials(REQ)
    names = {c["account"] for c in creds}
    assert {"org-owner", "team-maintainer", "alice-dev"} <= names, names
    pw = {c["account"]: c["password"] for c in creds}
    assert pw["team-maintainer"] == "Other-password-456!", pw

def missing_accounts_make_one_finding():
    creds = sp.credentials(REQ)
    used = next(c for c in creds if c["account"] == "org-owner")
    body = {"login": used["account"], "password": used["password"]}
    fake, calls = _with_server({"org-owner": "Valid-password-123!"})
    saved = sp._call
    sp._call = fake
    try:
        found = sp._provisioned_accounts("http://x", {"path": "/api/login"}, body, creds, REQ, 1e12, lambda m: None)
    finally:
        sp._call = saved
    assert len(found) == 1, found
    text = found[0]["problem"]
    assert "team-maintainer" in text and "alice-dev" in text and "org-owner` ->" not in text, text
    tried = [c for c in calls]
    assert {"login": "team-maintainer", "password": "Other-password-456!"} in tried, tried

def all_present_no_finding():
    creds = sp.credentials(REQ)
    body = {"login": "org-owner", "password": "Valid-password-123!"}
    fake, _ = _with_server({c["account"]: c["password"] for c in creds})
    saved = sp._call
    sp._call = fake
    try:
        found = sp._provisioned_accounts("http://x", {"path": "/api/login"}, body, creds, REQ, 1e12, lambda m: None)
    finally:
        sp._call = saved
    assert found == [], found

def rate_limit_is_not_a_finding():
    creds = sp.credentials(REQ)
    body = {"login": "org-owner", "password": "Valid-password-123!"}
    fake, _ = _with_server({"org-owner": "Valid-password-123!"}, other_status=429)
    saved = sp._call
    sp._call = fake
    try:
        found = sp._provisioned_accounts("http://x", {"path": "/api/login"}, body, creds, REQ, 1e12, lambda m: None)
    finally:
        sp._call = saved
    assert found == [], found

CASES = [wording_is_read, missing_accounts_make_one_finding, all_present_no_finding, rate_limit_is_not_a_finding]

def main() -> int:
    failed = 0
    for case in CASES:
        try:
            case()
            print(f"ok    {case.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {case.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} v150d cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
