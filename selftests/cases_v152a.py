from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.outsider import unstated_navigation

def _leaf(when: str, name: str = "REQ-9: Change Password - Scenario 1") -> dict:
    return {"id": "REQ-9", "description": "", "scenarios": [{"name": name, "steps": [
        {"keyword": "GIVEN", "content": "The visitor starts in a fresh session. The system pre-provisions the account `u1`."},
        {"keyword": "WHEN", "content": when},
        {"keyword": "THEN", "content": "The page displays “Password updated”."}]}]}

SPEC = '''const { test } = require("@playwright/test");
const { click, fill } = require("../e2e-support/outsider");
async function signIn(page) {
  await page.goto("/");
  await click(page, "Sign in");
  await fill(page, "Username or email", "u1");
  await click(page, { role: "button", name: "Sign in" });
}
async function openSecurity(page) {
  await click(page, { role: "button", name: "%s" });
  await click(page, "Settings");
  await click(page, "Password and authentication");
}
test("REQ-9: Change Password - Scenario 1", async ({ page }) => {
  await signIn(page);
  await openSecurity(page);
  await click(page, { role: "button", name: "Update password" });
});
'''

def menu_bridge_is_reported():
    p = unstated_navigation(SPEC % "Account menu", _leaf("The visitor signs in as `u1`, opens Settings and then Password and authentication, and clicks “Update password”."))
    assert len(p) == 1 and '"Account menu" before "Settings"' in p[0], p

def quiet_cases():
    when = "The visitor signs in as `u1`, opens Settings and then Password and authentication, and clicks “Update password”."

    hop = unstated_navigation(SPEC % "Your organizations", _leaf(when))
    assert len(hop) == 1 and '"Your organizations" before "Settings"' in hop[0], hop
    assert unstated_navigation(SPEC % "Account menu", _leaf(
        "The visitor signs in as `u1`, opens “Account menu”, clicks Settings and then Password and authentication.")) == []
    assert unstated_navigation(SPEC % "Account menu", _leaf(
        "The user signs in and follows the visible controls for the settings and password workflow.")) == []
    assert unstated_navigation(SPEC % "Account menu", _leaf(when, name="REQ-9: Something else - Scenario 7")) == []

def codex_r57_false_positives_stay_quiet():

    leaf = {"id": "REQ-9", "description": "", "scenarios": [
        {"name": "REQ-9: Open - Scenario 1", "steps": [{"keyword": "WHEN", "content": "The visitor signs in as `u1` and opens Settings."}]},
        {"name": "REQ-9: Open - Scenario 10", "steps": [{"keyword": "WHEN", "content": "The visitor signs in as `u1`, opens “Account menu” and clicks Settings."}]}]}
    spec = '''const { click } = require("x");
test("REQ-9: Open - Scenario 10", async ({ page }) => {
  await click(page, { role: "button", name: "Account menu" });
  await click(page, "Settings");
});
'''
    assert unstated_navigation(spec, leaf) == [], unstated_navigation(spec, leaf)

    spec2 = '''const { click } = require("x");
test("REQ-9: Change Password - Scenario 1", async ({ page }) => {
  await click(page, "Settings");
});
async function unused(page) {
  await click(page, { role: "button", name: "Account menu" });
  await click(page, "Settings");
}
'''
    assert unstated_navigation(spec2, _leaf("The visitor signs in as `u1`, opens Settings.")) == []

CASES = [menu_bridge_is_reported, quiet_cases, codex_r57_false_positives_stay_quiet]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v152a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
