from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

LIFECYCLE_TEXT = (
    "EVOLUTION DATA LIFECYCLE: The delivered package starts without database.db; the same startup code must also run on the "
    "database an older version of this application created, and run again on its own database. (1) Create every table with "
    "CREATE TABLE IF NOT EXISTS before any index, trigger, migration or seed that uses it. (2) A column added to an existing "
    "table: read PRAGMA table_info and only when the column is missing run ALTER TABLE ... ADD COLUMN ... DEFAULT ..., then "
    "backfill existing rows, in one transaction; before a case-insensitive UNIQUE index, look for rows that differ only by "
    "letter case and resolve them without deleting user data. (3) This round's pre-provisioned records go in as one "
    "versioned data migration run once inside one transaction and recorded as applied under a stable key; records several "
    "scenarios share (an account, an organization) are created once and referenced; never re-detect a seeded record by a "
    "display name a user can rename; a restart never re-creates or resets a record a user renamed, changed or deleted.")

@case
def the_switches_are_registered_and_on_by_default():
    from agent import settings

    for key in ("edit_blocks", "evolution_health_rules", "evolution_single_source", "evolution_health_check"):
        assert settings._DEFAULTS.get(key) is True, key

    import os
    saved = os.environ.get("AGENT_EVOLUTION_SINGLE_SOURCE")
    try:
        os.environ["AGENT_EVOLUTION_SINGLE_SOURCE"] = "false"
        assert settings._load()["evolution_single_source"] is False
    finally:
        if saved is None:
            os.environ.pop("AGENT_EVOLUTION_SINGLE_SOURCE", None)
        else:
            os.environ["AGENT_EVOLUTION_SINGLE_SOURCE"] = saved

@case
def the_data_lifecycle_convention_is_evolution_only_and_word_for_word():
    from agent import codegen, conventions

    assert conventions.EVOLUTION_DATA_LIFECYCLE == LIFECYCLE_TEXT
    assert "EVOLUTION DATA LIFECYCLE" not in conventions.PROJECT_CONVENTIONS
    assert conventions.evolution_round() is False and conventions.evolution_conventions() == ""
    import os
    saved_env = os.environ.get("AGENT_EDIT_BLOCKS")
    try:
        for edit_env, plain in (("true", codegen.SYSTEM_PROMPT), ("false", codegen.SYSTEM_PROMPT_WHOLE_FILES)):
            os.environ["AGENT_EDIT_BLOCKS"] = edit_env
            assert codegen.system_prompt() == plain
            conventions.set_evolution_round(True)
            try:
                text = codegen.system_prompt()
            finally:
                conventions.set_evolution_round(False)
            assert text == plain.replace(conventions.PROJECT_CONVENTIONS,
                                         conventions.PROJECT_CONVENTIONS + "- " + LIFECYCLE_TEXT + NL, 1)
            at = text.index(LIFECYCLE_TEXT)
            assert text.index(conventions.PROJECT_CONVENTIONS) + len(conventions.PROJECT_CONVENTIONS) + 2 == at
            assert at < text.index("Rules:" + NL) and text.count(LIFECYCLE_TEXT) == 1
            assert codegen.system_prompt() == plain
    finally:
        if saved_env is None:
            os.environ.pop("AGENT_EDIT_BLOCKS", None)
        else:
            os.environ["AGENT_EDIT_BLOCKS"] = saved_env

@case
def run_switches_the_convention_on_only_in_an_evolution_round():
    import inspect

    import main as m

    run = inspect.getsource(m.Factory.run)

    at = run.index('project_conventions.set_evolution_round(bool(evo) and setting_on("evolution_data_lifecycle"))')
    assert run.index("evo = self.evolution_enter(tree, found)") < at
    assert run.index("evo = self.evolution_state()") < at
    assert at < run.index("work = build_batches(")

RULES_A = ("CURRENTLY EFFECTIVE RULES of REQ-2-2 \"Star a repository\" (evolution round). Precedence: scenarios decide." + NL
           + "Modified Feature Description (the change):" + NL + "  [M1; change] The Star button now toggles.")
RULES_B = ("CURRENTLY EFFECTIVE RULES of REQ-2-3 \"Watch\" (evolution round). Precedence: scenarios decide." + NL
           + "Modified Feature Description (the change):" + NL + "  [M1; change] Watching is per branch.")

@case
def the_implementer_reads_the_effective_rules_once_after_the_requirement():
    from agent.codegen import EFFECTIVE_RULES_HEAD, describe_for_implementer, strip_effective_rules
    from agent.test_author import describe_node

    plain = {"id": "REQ-1", "name": "Plain", "description": "A plain leaf.", "ancestor_context": "Product: code hosting.",
             "scenarios": [{"name": "s", "steps": [{"keyword": "GIVEN", "content": "a user"}]}]}
    assert describe_for_implementer(plain) == describe_node(plain)
    context = "Product: code hosting." + NL + NL + RULES_A + NL + NL + "Inherited context of REQ-2-2." + NL + NL + RULES_B
    batch = dict(plain, id="REQ-2-2", members=["REQ-2-2", "REQ-2-3"], ancestor_context=context,
                 effective_rules=RULES_A + NL + NL + RULES_B)
    text = describe_for_implementer(batch)
    for rules in (RULES_A, RULES_B):
        assert text.count(rules) == 1, text
    assert text.index("Requirement REQ-2-2") < text.index(EFFECTIVE_RULES_HEAD.strip()) < text.index(RULES_A)
    assert "Product: code hosting." in text and "Inherited context of REQ-2-2." in text
    assert strip_effective_rules(context, RULES_A + NL + NL + RULES_B) == "Product: code hosting." + NL + NL + "Inherited context of REQ-2-2."

    assert strip_effective_rules("other", RULES_A) == "other"

    health = "The application you extend was delivered in an earlier round. RULES"
    text = describe_for_implementer(dict(plain, evolution_health=health))
    assert text == describe_node(plain) + NL + NL + health
    text = describe_for_implementer(dict(batch, evolution_health=health))
    assert text.index(RULES_B) < text.index(health)

@case
def every_implementer_and_diagnosis_prompt_goes_through_describe_for_implementer():
    import inspect

    from agent import codegen

    user = inspect.getsource(codegen.CodegenImplementer._user)
    assert "parts.append(describe_for_implementer(node))" in user and "parts.append(describe_node(node))" not in user

    assert "self._user(node, test_files, outcome, written, extra, True, ask=ask)" in inspect.getsource(codegen.CodegenImplementer._diagnose)

ORIGINAL = ("Each repository shows a `Star` button that stars it. The star count is shown next to the button. "
            "Starred repositories are listed on the profile page.")
MODIFIED = "The `Star` button now toggles: a second press removes the star and lowers the star count by one."
DESCRIPTION = ("Original Feature Description" + NL + NL + ORIGINAL + NL + NL + "Modified Feature Description" + NL + NL + MODIFIED)
HEAD = 'const { test } = require("@playwright/test");' + NL + NL

def block(title: str, source: str = "") -> str:
    above = f'// source: "{source}"{NL}' if source else ""
    return f'{above}test("{title}", async ({{ page }}) => {{{NL}  await page.goto("/");{NL}}});{NL}{NL}'

def modified_leaf() -> dict:
    from agent import evolution

    leaf = {"id": "REQ-2-2", "name": "Star a repository", "description": DESCRIPTION,
            "scenarios": [{"name": "Toggle the star", "steps": []}]}
    text, struct = evolution.effective_rules(leaf, "")
    assert text, "agent/evolution.py did not read the Modified head"
    leaf["effective_rules"] = text
    leaf["evolution"] = {"class": "modified", "rules": struct}
    return leaf

@case
def a_test_quoting_only_a_replaced_original_sentence_is_dropped():
    import agent.description_pass as dp

    leaf = modified_leaf()
    superseded = dp.superseded_sentences(leaf)
    overlapped = [o["text"] for o in leaf["evolution"]["rules"]["original"] if o["overlaps"]]
    assert superseded == overlapped and superseded, leaf["evolution"]["rules"]["original"]
    in_force = [o["text"] for o in leaf["evolution"]["rules"]["original"] if not o["overlaps"]]
    pieces, found = dp.description_pieces(DESCRIPTION, superseded)
    assert found == len(superseded)
    assert all(s not in piece for s in superseded for piece in pieces) and any(MODIFIED in p for p in pieces)
    assert dp.description_pieces(DESCRIPTION, []) == ([DESCRIPTION], 0)

    twice = DESCRIPTION.replace(ORIGINAL, ORIGINAL + " " + superseded[0])
    pieces2, found2 = dp.description_pieces(twice, superseded[:1])
    assert found2 == 2 and not any(superseded[0] in piece for piece in pieces2)
    assert dp.description_pieces("Original Feature Description\n\nIt starts. The restart works.", ["It starts."])[1] == 1
    assert dp.description_pieces(DESCRIPTION, ["A sentence that is nowhere."]) == ([DESCRIPTION], 0)

    assert dp.superseded_sentences({"id": "REQ-9", "description": "x"}) == []

    p = dp.DescriptionPass.__new__(dp.DescriptionPass)
    lines: list[str] = []
    p.log = lines.append
    first = superseded[0]
    old_title = "REQ-2-2: " + " ".join(first.split()[:6])
    new_title = "REQ-2-2: a second press removes the star"
    content = HEAD + block(old_title) + block(new_title)
    if in_force:
        kept_title = "REQ-2-2: " + " ".join(in_force[0].rstrip(".").split()[:5])
        content += block(kept_title)

    tests: dict = {}
    assert old_title in p.quote_gate(content, DESCRIPTION, tests)
    p.superseded_pieces = pieces
    tests = {}
    out = p.quote_gate(content, DESCRIPTION, tests)
    assert old_title not in out and new_title in out, out
    assert tests[old_title]["final"] == "dropped" and tests[old_title]["reason"] == "superseded-original", tests
    if in_force:
        assert kept_title in out, (kept_title, out)
    assert any("Original sentence(s) this round's Modified" in line for line in lines), lines

    tests = {}
    sourced = HEAD + block("REQ-2-2: counter", source=" ".join(first.split()[:6])) + block(new_title)
    assert "REQ-2-2: counter" not in p.quote_gate(sourced, DESCRIPTION, tests)

@case
def the_pass_wires_the_superseded_sentences_per_leaf():
    import inspect

    import agent.description_pass as dp

    leaf_source = inspect.getsource(dp.DescriptionPass.process_leaf)
    at = leaf_source.index("superseded = superseded_sentences(leaf)")
    assert at < leaf_source.index("spec = self.author(view, prefix)")
    assert "self.superseded_pieces, found = description_pieces(description, superseded)" in leaf_source

@case
def a_leaf_view_carries_its_own_effective_rules():
    import agent.description_pass as dp

    p = dp.DescriptionPass.__new__(dp.DescriptionPass)
    p.work, p.whole = [], "WHOLE"
    p.tools = type("T", (), {"note_channel": "pass_note"})()
    batch = {"id": "REQ-2-2", "members": ["REQ-2-2", "REQ-2-3"], "effective_rules": RULES_A + NL + NL + RULES_B,
             "ancestor_context": "Product." + NL + NL + RULES_A + NL + NL + "Context of REQ-2-3." + NL + NL + RULES_B}
    own = {"id": "REQ-2-3", "name": "Watch", "description": "d", "effective_rules": RULES_B}
    view = p.leaf_view(own, batch)
    assert view["effective_rules"] == RULES_B

    assert RULES_A not in view["ancestor_context"] and RULES_B in view["ancestor_context"], view["ancestor_context"]
    assert "Product." in view["ancestor_context"] and "Context of REQ-2-3." in view["ancestor_context"]
    from agent.codegen import describe_for_implementer
    text = describe_for_implementer(view)
    assert text.count(RULES_B) == 1 and RULES_A not in text
    new = {"id": "REQ-2-4", "name": "New", "description": "d"}
    assert "effective_rules" not in p.leaf_view(new, batch)
    assert "effective_rules" not in p.leaf_view(new, {"id": "REQ-2-4", "members": ["REQ-2-4"]})

def finding(kind: str, files: list[str], message: str, lines: list[int] | None = None) -> dict:
    return {"kind": kind, "files": files, "lines": lines or [1] * len(files), "message": message}

@case
def the_start_up_findings_are_grouped_by_certainty_and_place():
    import main as m

    touched = {"backend/src/routes/repo_helpers.js"}
    group = m.Factory.single_source_group
    assert group(finding("double-mount", ["backend/src/app.js"], "mounted 2 times"), set()) == "hard"
    assert group(finding("shadowed-route", ["a.js", "b.js"], "GET /x at a.js:3 never runs: ..."), set()) == "hard"
    probably = finding("shadowed-route", ["a.js", "b.js"], "GET /x at a.js:3 probably never runs (the earlier ...")
    assert group(probably, set()) == "" and group(probably, {"a.js"}) == "touched"
    for kind in ("name-only-lookup", "lookup-owners", "duplicate-helper"):
        assert group(finding(kind, ["backend/src/routes/repo_helpers.js"], "x"), touched) == "touched"
        assert group(finding(kind, ["backend/src/routes/other.js"], "x"), touched) == ""
    assert group(finding("duplicate-engine", ["backend/src/lib/formula.js", "frontend/src/lib/formula.ts"], "Advisory"), set()) == "advisory"
    assert group(finding("some-future-kind", ["x"], "x"), {"x"}) == ""

@case
def the_debt_note_is_bounded_and_says_each_fix_once():
    import main as m
    from agent import single_source

    f = m.Factory.__new__(m.Factory)
    assert f.inherited_debt_note({"hard": [], "touched": [], "advisory": []}) == ""
    long_tail = " filler" * 80
    hard = [finding("shadowed-route", ["backend/src/routes/repos.js"], f"GET /api/x{n} at repos.js:{n} never runs:{long_tail} "
                    + single_source.FIX["shadowed-route"]) for n in range(30)]
    touched = [finding("name-only-lookup", ["backend/src/routes/repo_helpers.js"], "repo_helpers.js:29 selects by name")]
    note = f.inherited_debt_note({"hard": hard, "touched": touched, "advisory": []})
    assert len(note) <= m.Factory.INHERITED_DEBT_CAP, len(note)

    many = {"hard": hard, "touched": [finding("lookup-owners", ["a.js"], "x" * 300)] * 20,
            "advisory": [finding("duplicate-engine", ["b.js"], "y" * 300)] * 20}
    assert len(f.inherited_debt_note(many)) <= m.Factory.INHERITED_DEBT_CAP
    assert note.startswith("INHERITED DEBT") and "more finding(s) of this group not shown" in note
    assert note.count(single_source.FIX["shadowed-route"]) == 1
    assert "[name-only-lookup]" not in note or note.count("fix [name-only-lookup]") == 1
    small = f.inherited_debt_note({"hard": hard[:1], "touched": touched, "advisory": []})
    assert "REPAIR in this batch" in small and "ON FILES this round's modified features were built in" in small
    assert "[name-only-lookup] repo_helpers.js:29 selects by name" in small and "fix [name-only-lookup]" in small
    assert all(len(line) <= m.Factory.DEBT_LINE_CHARS + 30 for line in small.splitlines() if line.startswith("- ["))

DOUBLE_MOUNT_APP = ("const express = require('express');" + NL + "const app = express();" + NL
                    + "const repos = require('./routes/repos');" + NL + "app.use('/api/repos', repos);" + NL
                    + "app.use('/api', repos);" + NL + "module.exports = app;" + NL)
REPOS_ROUTER = ("const express = require('express');" + NL + "const router = express.Router();" + NL
                + "router.get('/', (req, res) => res.json([]));" + NL + "module.exports = router;" + NL)

def health_factory(project: Path, implement: list[str], classes: dict[str, str]):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir = project
    f.report = {}
    f.open_findings = {}
    f.evolution = {"active": True, "implement": list(implement),
                   "classes": {"classes": dict(classes), "modified": [k for k, v in classes.items() if v == "modified"],
                               "new": [k for k, v in classes.items() if v == "new"]}}
    return f

def health_tree() -> dict:
    return {"id": "ROOT", "children": [
        {"id": "REQ-1", "name": "Sessions", "description": "Original Feature Description\n\nA user signs in.\n\n"
         "Modified Feature Description\n\nA user can revoke a session from the sessions page.",
         "scenarios": [{"name": "Revoke", "steps": [{"keyword": "WHEN", "content": "the user revokes the other session"}]}]},
        {"id": "REQ-2", "name": "Formula", "description": "A formula recalculates when a referenced cell is cleared.",
         "scenarios": []}]}

@case
def the_start_up_health_check_runs_in_order_and_never_says_passed():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        write(project, "backend/src/app.js", DOUBLE_MOUNT_APP)
        write(project, "backend/src/routes/repos.js", REPOS_ROUTER)

        f = health_factory(project, ["REQ-1", "REQ-2"], {"REQ-1": "modified", "REQ-2": "new"})
        f._earlier_handoff = {"leaves": [{"id": "REQ-1", "batch": "B1"}]}
        write(project, ".arc/agent-summary.json", json.dumps({"nodes": {"B1": {"written": ["backend/src/routes/repos.js"]}}}))
        assert f.evolution_touched_files() == {"backend/src/routes/repos.js"}
        lines: list[str] = []
        saved = m.log
        m.log = lines.append
        try:
            f.evolution_startup_health(health_tree())
        finally:
            m.log = saved
        book = f.evolution["health"]
        start = book["single_source"]["start"]
        assert [x["kind"] for x in start] == ["double-mount"], start
        assert book["single_source"]["handed"] == {"hard": 1, "touched": 0, "advisory": 0}
        assert "[double-mount]" in book["single_source"]["note"] and "REPAIR in this batch" in book["single_source"]["note"]
        assert any(line.startswith("[evolution] start-up single-source audit of the inherited application: 1 finding(s)")
                   for line in lines), lines
        items = [(r["item"], r["status"]) for r in book["health_check"]]

        from agent import inherited_health
        text = NL.join(m.Factory.leaf_requirement_text(leaf) for leaf in health_tree()["children"])
        assert [k for k, _ in items] == [k for k, _ in inherited_health.health_check_items(text)]
        assert ("owners", "findings") in items and {"session", "recalculation", "modified"} <= {k for k, _ in items}
        assert all(status == "not run" for key, status in items if key != "owners")
        assert not any(status == "passed" for _, status in items)
        assert f.report["evolution_health"]["single_source_start"]["findings"] == 1
        assert f.report["evolution_health"]["health_check"] == book["health_check"]

        work = [{"id": "REQ-1", "members": ["REQ-1"]}, {"id": "REQ-2", "members": ["REQ-2"]}]
        f.evolution_health_attach(work, health_tree())
        assert "Session chain" in work[0]["evolution_health"] and "Recalculation" in work[1]["evolution_health"]
        assert "Changed rules" in work[0]["evolution_health"] and "Changed rules" not in work[1]["evolution_health"]
        assert work[0]["evolution_inherited_debt"] == book["single_source"]["note"]
        assert "evolution_inherited_debt" not in work[1] and book["single_source"]["handed_to"] == "REQ-1"

@case
def the_switches_turn_each_part_off():
    import main as m
    from agent import settings

    saved = dict(settings.SETTINGS)
    try:
        settings.SETTINGS.update(evolution_single_source=False, evolution_health_check=False, evolution_health_rules=False)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write(project, "backend/src/app.js", DOUBLE_MOUNT_APP)
            write(project, "backend/src/routes/repos.js", REPOS_ROUTER)
            f = health_factory(project, ["REQ-1"], {"REQ-1": "modified"})
            saved_log = m.log
            m.log = lambda line: None
            try:
                f.evolution_startup_health(health_tree())
                work = [{"id": "REQ-1", "members": ["REQ-1"]}]
                f.evolution_health_attach(work, health_tree())
                f.evolution_single_source_closing()
            finally:
                m.log = saved_log
            assert f.evolution.get("health") == {} and work == [{"id": "REQ-1", "members": ["REQ-1"]}]
            assert f.open_findings == {} and "single_source_closing" not in f.report.get("evolution_health", {})

        settings.SETTINGS.update(evolution_health_check=True)
        with tempfile.TemporaryDirectory() as tmp:
            f = health_factory(Path(tmp), ["REQ-1"], {"REQ-1": "modified"})
            saved_log = m.log
            m.log = lambda line: None
            try:
                f.evolution_startup_health(health_tree())
            finally:
                m.log = saved_log
            owners = [r for r in f.evolution["health"]["health_check"] if r["item"] == "owners"]
            assert owners and owners[0]["status"] == "not run" and "off" in owners[0]["result"]
    finally:
        settings.SETTINGS.clear()
        settings.SETTINGS.update(saved)

@case
def nothing_happens_outside_an_evolution_round_and_nothing_raises():
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.report, f.open_findings = {}, {}
    saved = m.log
    lines: list[str] = []
    m.log = lines.append
    try:
        f.evolution_startup_health({})
        f.evolution_health_attach([{"id": "X"}], {})
        f.evolution_single_source_closing()

        g = health_factory(Path("Z:/no/such/dir/at/all"), ["REQ-1"], {"REQ-1": "modified"})
        g.report = None
        g.evolution_startup_health(health_tree())
        g.evolution_single_source_closing()
    finally:
        m.log = saved
    assert f.report == {} and f.open_findings == {}
    assert any("crashed" in line for line in lines), lines

@case
def a_dry_evolution_start_audits_and_hands_the_debt_to_the_first_batch():
    import cases_v160a_factory as a

    from agent import conventions

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        a.inherited_app(project)
        write(project, a.APP_JS, DOUBLE_MOUNT_APP)
        write(project, a.ROUTES, REPOS_ROUTER)
        try:
            lines, seen = a.dry_start(project, a.requirement_dir(root, a.NEW_YAML), set())
            switched = conventions.evolution_round()
        finally:
            conventions.set_evolution_round(False)
    queue = seen["queue"]
    assert queue is not None, lines[-12:]
    assert switched is True
    assert any(line.startswith("[evolution] start-up single-source audit of the inherited application: 1 finding(s)")
               for line in lines), [x for x in lines if "single-source" in x]
    nodes = queue["nodes"]
    assert all(n.get("evolution_health") for n in nodes), [sorted(n) for n in nodes]
    star = a.batch_of(nodes, "REQ-2-2")
    assert "Changed rules" in star["evolution_health"]
    assert "[double-mount]" in str(nodes[0].get("evolution_inherited_debt") or ""), nodes[0].get("evolution_inherited_debt")
    assert not any(n.get("evolution_inherited_debt") for n in nodes[1:])
    f = seen["factory"]

    order = [line for line in lines if (line.startswith("[evolution] start-up") and "health findings handed" not in line)
             or line.startswith("[continuation] baseline") or "shared-prerequisite rules for" in line]
    assert order and "shared-prerequisite rules for" in order[-1], order
    assert f.report["evolution_health"]["single_source_start"]["handed"]["hard"] == 1

@case
def the_closing_audit_is_diffed_against_the_start_and_reaches_the_handoff():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        write(project, "backend/src/app.js", DOUBLE_MOUNT_APP)
        write(project, "backend/src/routes/repos.js", REPOS_ROUTER)
        write(project, "backend/src/routes/teams.js", REPOS_ROUTER)
        f = health_factory(project, ["REQ-1"], {"REQ-1": "modified"})
        saved = m.log
        lines: list[str] = []
        m.log = lines.append
        try:
            f.evolution_startup_health(health_tree())

            write(project, "backend/src/app.js", "// a comment of this round" + NL + DOUBLE_MOUNT_APP.replace(
                "module.exports = app;", "const teams = require('./routes/teams');" + NL + "app.use('/api/teams', teams);"
                + NL + "app.use('/api/t', teams);" + NL + "module.exports = app;"))
            f.evolution_single_source_closing()
        finally:
            m.log = saved
        closing = f.report["evolution_health"]["single_source_closing"]
        assert (closing["findings"], len(closing["new"]), closing["still"], closing["gone"]) == (2, 1, 1, 0), closing
        assert "teams.js" in closing["new"][0]["message"]
        classes = sorted(v["class"] for v in f.open_findings.values())
        assert classes == ["single-source double-mount (inherited)", "single-source double-mount (new in this round)"], classes
        assert all(v["batch"] == "closing" and v["where"].startswith("backend/src/app.js:") for v in f.open_findings.values())
        assert f.evolution["health"]["single_source"]["closing"] == closing
        assert any("1 new in this round, 1 still there from the start-up, 0 gone" in line for line in lines), lines

        from agent import handoff
        f.contract, f.batch_members, f.failed_members, f.unattempted_members, f.description_specs = "", {}, [], [], []
        f.continuation = None
        built = handoff.build_handoff(f, {"id": "ROOT", "children": []}, "sha", "", project)
        assert sorted(x["class"] for x in built["debt"]["open_findings"]) == classes

        g = health_factory(project, ["REQ-1"], {"REQ-1": "modified"})
        m.log = lambda line: None
        try:
            g.evolution_single_source_closing()
        finally:
            m.log = saved
        assert g.report["evolution_health"]["single_source_closing"]["findings"] == 2 and len(g.open_findings) == 2

@case
def a_finding_whose_lines_moved_is_the_same_finding():
    import main as m

    key = m.Factory.single_source_key
    a = finding("name-only-lookup", ["backend/src/routes/pulls.js"], "backend/src/routes/pulls.js:19,23 selects from `repos` by `name`")
    b = finding("name-only-lookup", ["backend/src/routes/pulls.js"], "backend/src/routes/pulls.js:31, 40 selects from `repos` by `name`")
    c = finding("name-only-lookup", ["backend/src/routes/issues.js"], "backend/src/routes/issues.js:19,23 selects from `repos` by `name`")
    assert key(a) == key(b) and key(a) != key(c)

@case
def the_owners_item_is_not_clean_without_anything_to_read():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        f = health_factory(Path(tmp), ["REQ-1"], {"REQ-1": "modified"})
        saved = m.log
        m.log = lambda line: None
        try:
            f.evolution_startup_health(health_tree())
        finally:
            m.log = saved
        owners = [r for r in f.evolution["health"]["health_check"] if r["item"] == "owners"]
        assert owners and owners[0]["status"] == "not run", owners

@case
def the_handoff_share_is_capped():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        app = ["const express = require('express');", "const app = express();"]
        for n in range(m.Factory.SINGLE_SOURCE_HANDOFF_MAX + 5):
            write(project, f"backend/src/routes/r{n}.js", REPOS_ROUTER)
            app += [f"const r{n} = require('./routes/r{n}');", f"app.use('/api/a{n}', r{n});", f"app.use('/api/b{n}', r{n});"]
        write(project, "backend/src/app.js", NL.join(app + ["module.exports = app;"]) + NL)
        f = health_factory(project, ["REQ-1"], {"REQ-1": "modified"})
        saved = m.log
        m.log = lambda line: None
        try:
            f.evolution_single_source_closing()
        finally:
            m.log = saved
        assert f.report["evolution_health"]["single_source_closing"]["findings"] == m.Factory.SINGLE_SOURCE_HANDOFF_MAX + 5
        assert len(f.open_findings) == m.Factory.SINGLE_SOURCE_HANDOFF_MAX

@case
def the_wiring_sits_where_the_change_list_puts_it():
    import inspect

    import main as m

    run = inspect.getsource(m.Factory.run)
    baseline = run.index('self.evolution_after_baseline("the start")')
    startup = run.index("self.evolution_startup_health(tree)")
    assert baseline < startup < run.index("work = build_batches(evolution.implementation_tree(")
    assert run.index("self.evolution_batches(work, tree)") < run.index("self.evolution_health_attach(work, tree)") \
        < run.index("work = build_batches(tree)")
    closing = run.index("self.evolution_single_source_closing()")
    assert run.index("self.verify()") < closing < run.index("self.clean_runtime_state()")
    node = inspect.getsource(m.Factory.process_node)
    debt = node.index('inherited_debt = str(node.get("evolution_inherited_debt") or "")')
    assert node.index('debt_note = str(node.get("handoff_debt_note") or "")') < debt < node.index("initial_first = first")
    assert "retry_note = self.with_prerequisite_note(retry_note, inherited_debt) if retry else retry_note" in node
    assert 'prerequisite = (prerequisite + "\\n\\n" + inherited_debt).strip()' in node

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
