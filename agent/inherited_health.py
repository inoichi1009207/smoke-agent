from __future__ import annotations

import re

INTRO = (
    "The application you extend was delivered in an earlier round. Its earlier tests passing does not prove that what the "
    "new requirements build on works: before (and while) adding the new behaviour, make sure every SHARED PREREQUISITE it "
    "depends on works in the inherited code -- repairing it is part of this change, and it comes first."
)

PREREQUISITES: tuple[tuple[str, str | None, str], ...] = (
    ("recalculation", r"\bformula|recalculat|dependent (?:cell|result)|\bcells?\b.*\b(?:clear|delete)",
     "Recalculation: a change of any cell a formula references -- typing, pasting, Delete / clear, undo -- recalculates every "
     "dependent formula at once, in the module that owns evaluation (the page shows its results), with the empty-cell rule "
     "the requirement states (in plain arithmetic an emptied cell counts as a spreadsheet's empty cell, unless the "
     "requirement says otherwise); a cleared cell is empty, never a broken reference -- the reference error is only for a "
     "cell removed by a structural edit."),
    ("extent", r"\b(?:cell|cells|column|columns|row|rows|grid|worksheet|spreadsheet|selection|range)\b",
     "Extent: the grid's rendering (or scrolling / virtualising), coordinates, selection (click, Shift+click, Shift+arrows, "
     "drag), keyboard moves, paste and Delete work for every column and row the requirement or the stored data uses -- not "
     "only the first screenful an earlier round tested. Delete clears EVERY cell of the selected range and keeps the "
     "selection; the cleared state is saved."),
    ("session", r"\bsessions?\b|\brevok|sign(?:s|ed)? out (?:of )?(?:other|every|all)|log(?:s|ged)? out (?:of )?(?:other|every|all)",
     "Session chain: when a requirement lets a session end other than by its own sign-out (revoked from another browser, "
     "expired, ended by a password change), the stored session carries its state, the ONE authentication middleware rejects "
     "an inactive session on every request (that browser lands on sign-in at its next request or reload), and the current "
     "session keeps working. A sessions page alone ends nothing; check it with two separate browser contexts."),
    ("entry", None,
     "Entry paths: every scenario starts where its GIVEN puts the user (signed out on the start page, or right after "
     "signing in) and finds the control its first step names there, with the role and name the requirement words for it "
     "(a link where it says link, a button where it says button) -- not only behind a page, menu or tab the scenario does "
     "not mention. An inherited control keeps its kind unless the requirement names another."),
    ("owners", None,
     "One owner per rule (see the conventions): an inherited second engine, second lookup helper, double-mounted router or "
     "shadowed handler on the path of the new rule is merged into its one owner BEFORE the new rule is added, so the change "
     "lands where every caller sees it; a record unique per owner is looked up by owner and name, never by the name alone."),
    ("modified", r"Modified Feature Description|Original Feature Description",
     "Changed rules: where the requirement marks a feature as modified (an original and a modified description), the "
     "modified sentences replace the original ones they contradict; everything else the original states stays as it is, "
     "and the code that enforced the replaced rule is changed where it lives (middleware, validation, seed), not bypassed."),
)

def prerequisites_for(requirement_text: str) -> list[str]:
    text = requirement_text or ""
    keys = []
    for key, trigger, _rule in PREREQUISITES:
        if trigger is None or re.search(trigger, text, re.IGNORECASE):
            keys.append(key)
    return keys

def health_rules(requirement_text: str = "", keys: list[str] | None = None) -> str:
    wanted = keys if keys is not None else (prerequisites_for(requirement_text) if requirement_text else [k for k, _t, _r in PREREQUISITES])
    bullets = [f"- {rule}" for key, _trigger, rule in PREREQUISITES if key in wanted]
    return INTRO + "\n" + "\n".join(bullets) if bullets else ""

HEALTH_RULES = health_rules()

HEALTH_CHECK_ITEMS: tuple[tuple[str, str], ...] = (
    ("owners", "Static: agent/single_source.audit() on the inherited tree -- duplicate engines / lookup helpers, routers "
               "mounted twice, shadowed handlers, name-only lookups of owned records. Findings on the path of a modified or "
               "new leaf are repaired first; the rest are listed in the handoff."),
    ("entry", "Browser: for every scenario of a modified or new leaf, from `/` as its GIVEN's user (anonymous, or right after "
              "signing in), the first control its WHEN (or a GIVEN-stated click) names is visible, actionable and unique in "
              "its stated scope, with the stated role; an unresolved step is reported as unverified, never as passed."),
    ("recalculation", "API or browser: a formula referencing a cell, the cell cleared, the dependent value recalculated with the "
                      "requirement's empty-cell rule (on the server and on the page), and the same after a reload."),
    ("extent", "Browser: a cell beyond the first screenful addressed by coordinate (typed, selected, Shift-extended, Delete over "
               "a range) behaves like a cell of the first screenful, and the result survives a reload."),
    ("session", "Two browser contexts of one account: one ends the other's session the way the requirement states; the ended "
                "one is refused on its next request or reload, the other keeps working."),
    ("modified", "Text: every modified feature's original sentences that the modification replaces are listed, and no "
                 "inherited check (spec assertion, seed check, message check) still asserts one of them."),
)

def health_check_items(requirement_text: str = "") -> list[tuple[str, str]]:
    keys = prerequisites_for(requirement_text) if requirement_text else [k for k, _t, _r in PREREQUISITES]
    return [(key, item) for key, item in HEALTH_CHECK_ITEMS if key in keys]
