from __future__ import annotations

import inspect
import json
import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
SAMPLES = Path("local-data/codex-audit/requirements-samples")
LOGS = Path("local-data/hackathon-logs")

def case(function):
    CASES.append(function)
    return function

GIVEN_6_2_3 = ("The visitor starts at the application home page in a fresh unauthenticated browser session. The seeded data is "
               "pull requests `Improve onboarding` and `Fix search`, branches `main` and `feature-search`, reviewer "
               "`bob-reviewer`, check `test`. A signed-in collaborator is on a valid comparison-results page for `main` and "
               "`feature-search`; the page displays at least one changed file, and no open PR currently exists for that "
               "comparison. Seed values: pull requests `Improve onboarding` and `Fix search`, branches `main` and "
               "`feature-search`, reviewer `bob-reviewer`, check `test`.")
DESC_6_2_2 = ("Seed data supplies a contributor with Write permission, a public repository PR-list page entry, a base branch, a "
              "distinct compare branch ahead by at least one commit, and a known changed file. Separate valid comparison page "
              "entrys open the same usable creation flow with the branches already selected; these pairs have no existing "
              "Open or Draft PR at the start of each creation scenario.")
GIVEN_2_2_4_S2 = ("The visitor starts at the application home page in a fresh unauthenticated browser session. The seeded data is "
                  "organization `Acme Demo`, repository `acme-docs`, member `bob-reviewer`, team `frontend-team`. The attempted "
                  "descendant is `frontend-child` and the original parent is `platform-team`. A signed-in user who is not an "
                  "organization Owner opens the organization's People page and can see a member. Seed values: organization "
                  "`Acme Demo`, repository `acme-docs`, member `bob-reviewer`, team `frontend-team`.")
DESC_2_2_2 = ("The original parent remains selected after a rejected change and reload. The cycle scenario supplies a team with "
              "an existing parent and a descendant selectable by name; submitting that descendant shows the cycle error rather "
              "than saving it. The seed record's original-parent value identifies the stored selection.")
GIVEN_2_1_2 = ("The visitor starts at the application home page in a fresh unauthenticated browser session. The seeded data is "
               "organization `Acme Demo`, repository `acme-docs`, member `bob-reviewer`, team `frontend-team`. A signed-in user "
               "enters the \u201cYour organizations\u201d page from the account menu and clicks \u201cNew organization\u201d; the "
               "candidate organization identifier is `mobile-guild` and does not exist in the system, and the creation form "
               "displays organization-identifier and display-name fields. Seed values: organization `Acme Demo`, repository "
               "`acme-docs`, member `bob-reviewer`, team `frontend-team`.")
GIVEN_4_3_2 = ("The visitor starts at the application home page in a fresh unauthenticated browser session. The seeded data is "
               "repository `acme-docs`, branches `main` and `feature-search`, file `README.md`, commit `Document search flow`. "
               "A signed-in user with write permission has opened the repository's `main` branch; the current branch-head "
               "commit is known, and the target name `feature/api-v2` does not exist. Seed values: repository `acme-docs`, "
               "branches `main` and `feature-search`, file `README.md`, commit `Document search flow`.")
DESC_5_1_2 = ("The seeded open issue title is `Improve onboarding` and its description is `Describe the onboarding improvement.` "
              "This feature builds on the persisted objects, opened pages, or authenticated-session context provided by "
              "REQ-5-1-1 (List and Filter Repository Issues); it must reuse that context and keep pages and data consistent "
              "after the context changes.")

def leaf(req_id: str, description: str = "", given: str = "", when: str = "", then: str = "") -> dict:
    steps = [{"keyword": k, "content": c} for k, c in (("GIVEN", given), ("WHEN", when), ("THEN", then)) if c]
    return {"id": req_id, "name": req_id, "type": "ATOMIC", "description": description,
            "scenarios": [{"name": f"{req_id} - Scenario 1", "steps": steps}] if steps else []}

def platform_tree() -> dict:
    return {"id": "ROOT", "name": "root", "children": [
        leaf("REQ-2-1-2", given=GIVEN_2_1_2),
        leaf("REQ-2-2-2", description=DESC_2_2_2),
        leaf("REQ-2-2-4", given=GIVEN_2_2_4_S2),
        leaf("REQ-4-3-2", given=GIVEN_4_3_2),
        leaf("REQ-5-1-2", description=DESC_5_1_2),
        leaf("REQ-6-2-2", description=DESC_6_2_2),
        leaf("REQ-6-2-3", given=GIVEN_6_2_3),
    ]}

@case
def the_platform_vacancy_binds_the_comparison_values():
    from agent.seed_presence import vacancies
    found = vacancies(platform_tree())
    free = [v for v in found if v["kind"] == "free"]
    assert len(free) == 1, free
    assert free[0]["req"] == "REQ-6-2-3" and free[0]["values"] == ["main", "feature-search"], free
    assert free[0]["qualifiers"] == ["open"] and free[0]["thing"] == "PR", free

    assert not any("Improve onboarding" in v["values"] for v in found)
    unbound = [v for v in found if v["kind"] == "unbound"]
    assert len(unbound) == 1 and unbound[0]["req"] == "REQ-6-2-2" and "these pairs have no existing" in unbound[0]["sentence"], unbound
    assert set(unbound[0]["qualifiers"]) == {"existing", "open", "draft"} and unbound[0]["thing"] == "PR", unbound

@case
def a_value_stated_as_not_existing_is_an_absent_vacancy_only_before_the_outcome():
    from agent.seed_presence import vacancies
    absent = {v["values"][0]: v["thing"] for v in vacancies(platform_tree()) if v["kind"] == "absent"}
    assert absent == {"mobile-guild": "candidate organization identifier", "feature/api-v2": "target name"}, absent

    outcome = {"id": "R", "children": [leaf("REQ-9", then="After deleting it, the note `n-1` does not exist and no open item exists for `a1`.")]}
    assert vacancies(outcome) == []

    rule = {"id": "R", "children": [leaf("REQ-9", description="Reopening the page shows that no new account corresponding to the failed input exists.")]}
    assert vacancies(rule) == []

    direct = {"id": "R", "children": [leaf("REQ-9", given="No existing folder named `f-1` is present. Also no draft entry exists for `k1` and `k2`.")]}
    got = sorted((v["kind"], tuple(v["values"])) for v in vacancies(direct))
    assert got == [("absent", ("f-1",)), ("free", ("k1", "k2"))], got

@case
def an_anaphor_without_a_preposition_run_stays_unbound():
    from agent.seed_presence import vacancies
    text = "The user signs in as `u-1` with password `p-1`; no open ticket currently exists for that queue."
    got = vacancies({"id": "R", "children": [leaf("REQ-9", given=text)]})
    assert [(v["kind"], v["values"]) for v in got] == [("unbound", [])], got

@case
def the_relation_sentence_names_both_records_and_the_requirement_that_relies_on_them():
    from agent.seed_presence import named_records, relied_on_by
    tree = platform_tree()
    records = named_records(tree)
    relation = [r for r in records if r["req"] == "REQ-2-2-4"]
    assert len(relation) == 1, records
    assert relation[0]["items"] == [("frontend-child", "attempted descendant"), ("platform-team", "original parent")], relation
    assert relied_on_by(tree, relation[0]) == ["REQ-2-2-2"]

    assert not any(v == "Acme Demo" for r in records for v, _ in r["items"])

    assert not any(v in ("mobile-guild", "feature/api-v2") for r in records for v, _ in r["items"])

@case
def a_quoted_value_that_ends_its_sentence_ends_it():
    from agent.seed_presence import named_records, split_sentences
    assert len(split_sentences(DESC_5_1_2)) == 2, split_sentences(DESC_5_1_2)
    records = [r for r in named_records(platform_tree()) if r["req"] == "REQ-5-1-2"]
    assert records and records[0]["items"] == [("Improve onboarding", "seeded open issue title")], records

    assert split_sentences("Open `Shelf 4.3.1` now. Then close it.") == ["Open `Shelf 4.3.1` now.", "Then close it."]

@case
def later_states_credentials_and_seed_data_sentences_name_no_record():
    from agent.seed_presence import named_records
    texts = [
        "After the move, the parent is `p-9`.",
        "The seeded data is `d-1`.",
        "The owner is `u-1` with password `the owner is secret`.",
        "The parent is `a@b.test`.",
    ]
    records = named_records({"id": "R", "children": [leaf("REQ-9", given=" ".join(texts))]})
    values = [v for r in records for v, _ in r["items"]]
    assert values == ["u-1"], records

    assert named_records({"id": "R", "children": [leaf("REQ-9", when="The parent is `p-1`.", then="The owner is `o-1`.")]}) == []

    clash = {"id": "R", "children": [leaf("REQ-8", given="The target name `n-new` does not exist."),
                                     leaf("REQ-9", description="The seeded draft title is `n-new`. The parent is `p-2`.")]}
    assert [v for r in named_records(clash) for v, _ in r["items"]] == ["p-2"], named_records(clash)

@case
def ownership_sentences_list_owner_and_owned():
    from agent.seed_presence import named_records
    text = ("The page lists `org-a`'s private repository `repo-x`. The account `u-1` owns the note `n-1`. "
            "The label `l-2` belongs to the organization `org-b`.")
    items = [i for r in named_records({"id": "R", "children": [leaf("REQ-9", description=text)]}) for i in r["items"]]
    assert ("org-a", "owner of the repository `repo-x`") in items and ("repo-x", "repository owned by `org-a`") in items, items
    assert ("n-1", "note owned by `u-1`") in items and ("l-2", "label owned by `org-b`") in items, items

@case
def the_note_lists_both_kinds_with_star_bullets_and_no_task_literal_in_its_rules():
    import main as factory_module
    note = factory_module.seed_constraints_note(platform_tree())
    assert note.startswith(factory_module.SEED_CONSTRAINTS_HEAD), note[:120]
    assert "no open PR on `main` + `feature-search`" in note
    assert "`mobile-guild` (candidate organization identifier) is stated as not existing" in note
    assert "`frontend-child` (attempted descendant), `platform-team` (original parent)" in note and "relied on by REQ-2-2-2" in note
    assert "these pairs have no existing Open or Draft PR" in note

    assert not any(line.lstrip().startswith("- ") for line in note.splitlines()), note
    assert factory_module.lost_seed_values(note, {"backend/s.js": b"'mobile-guild' 'frontend-child'"}, {"backend/s.js": b""}) == []

    rules = "\n".join(line for line in note.splitlines() if not line.startswith("  * "))
    for literal in ("main", "feature-search", "mobile-guild", "frontend-child", "platform-team", "Improve onboarding", "PR on"):
        assert f"`{literal}`" not in rules and literal not in rules.replace("<thing>", ""), (literal, rules)
    seeds_only = {"id": "R", "children": [leaf("REQ-1", given="Seed values: workbook `W1`, worksheet `S1`.")]}
    assert factory_module.seed_constraints_note(seeds_only) == ""
    assert factory_module.seed_constraints_note(None) == "" and factory_module.seed_constraints_note({"children": "x"}) == ""

@case
def the_note_reaches_the_contract_notes_once():
    import main as factory_module
    note = factory_module.seed_constraints_note(platform_tree())
    contract = json.dumps({"pages": [], "notes": ["keep the header"]})
    once = factory_module.contract_with_seed_constraints(contract, note)
    twice = factory_module.contract_with_seed_constraints(once, note)
    notes = json.loads(twice)["notes"]
    assert notes == ["keep the header", note], notes
    assert factory_module.contract_with_seed_constraints("", note) == ""
    assert factory_module.contract_with_seed_constraints(contract, "") == contract
    raw = "not json"
    assert factory_module.contract_with_seed_constraints(factory_module.contract_with_seed_constraints(raw, note), note).count(factory_module.SEED_CONSTRAINTS_HEAD) == 1

    from agent.contract import contract_slice
    big = json.dumps({"pages": [{"route": f"/p{i}", "file": f"f{i}.tsx", "requirements": [f"REQ-{i}"], "purpose": "x" * 200} for i in range(60)],
                      "notes": []})
    assert note in json.loads(contract_slice(factory_module.contract_with_seed_constraints(big, note), ["REQ-1"]))["notes"]
    source = inspect.getsource(factory_module.Factory)
    assert "constraints = seed_constraints_note(tree, log=log)" in source
    assert "self.contract = contract_with_seed_constraints(self.contract, constraints)" in source
    assert 'seeds = (seeds + "\\n\\n" + constraints).strip()' in source

@case
def real_requirements_give_the_expected_note_and_nothing_elsewhere():
    import yaml
    import main as factory_module
    github = SAMPLES / "hackathon-github-platform.yaml"
    if not github.is_file():
        print("      (skipped: platform requirement samples not on this machine)")
        return
    from agent.seed_presence import named_records, vacancies
    tree = yaml.safe_load(github.read_text(encoding="utf-8"))
    got = sorted((v["req"], v["kind"], tuple(v["values"])) for v in vacancies(tree))
    assert got == [("REQ-2-1-2", "absent", ("mobile-guild",)), ("REQ-2-2-1", "absent", ("mobile-team",)),
                   ("REQ-4-3-2", "absent", ("feature/api-v2",)), ("REQ-4-4", "absent", ("docs/guide.md",)),
                   ("REQ-6-2-2", "unbound", ()), ("REQ-6-2-3", "free", ("main", "feature-search"))], got
    named = sorted(v for r in named_records(tree) for v, _ in r["items"])
    assert named == ["Improve onboarding", "Legacy welcome text", "frontend-child", "platform-team"], named
    for other in ("hackathon-sheet-platform.yaml", "keep.yaml", "bookstack.yaml"):
        assert factory_module.seed_constraints_note(yaml.safe_load((SAMPLES / other).read_text(encoding="utf-8"))) == "", other

def _project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="arc-seed2-"))
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

WHOLE = None

def _whole() -> str:
    import yaml
    global WHOLE
    if WHOLE is None:
        WHOLE = yaml.safe_dump(platform_tree(), allow_unicode=True, sort_keys=False)
    return WHOLE

@case
def a_named_record_absent_from_the_backend_is_a_finding():
    from agent.detaudit import requirement_text
    from agent.seed_presence import seed_presence_findings
    own = requirement_text(leaf("REQ-2-2-4", given=GIVEN_2_2_4_S2))
    quiet = lambda _m: None
    cases = {

        "none": ({"backend/src/database/init_db.js": "seed('frontend-team')"}, ["frontend-child", "platform-team"]),

        "both": ({"backend/src/database/init_db.js": "seedTeam('platform-team'); seedTeam(\"frontend-child\", parent)"}, []),

        "tests": ({"backend/tests/t.test.js": "'frontend-child' 'platform-team'", "backend/test-e2e/a.spec.js": "'frontend-child'",
                   "backend/src/routes/teams.spec.js": "'frontend-child'", "backend/e2e-support/h.js": "'frontend-child'",
                   "backend/node_modules/x/i.js": "'platform-team'", "backend/src/database/init_db.js": "'platform-team'"}, ["frontend-child"]),

        "token": ({"backend/src/database/init_db.js": "'frontend-children' 'Frontend-Child' 'platform-team'"}, ["frontend-child"]),

        "escaped": ({"backend/src/database/init_db.js": "'frontend-child\\' 'platform-team'"}, []),
    }
    for name, (files, expected) in cases.items():
        project = _project(files)
        try:
            findings = seed_presence_findings(own, _whole(), project, log=quiet)
            got = sorted(v for f in findings for v in ("frontend-child", "platform-team") if f"`{v}`" in f["problem"])
            assert got == expected, (name, findings)
            if findings:
                assert set(findings[0]) == {"where", "requirement", "problem", "fix"} and "REQ-2-2-2" in findings[0]["problem"]
        finally:
            shutil.rmtree(project, ignore_errors=True)

    project = _project({"backend/src/database/init_db.js": "x"})
    try:
        unrelated = requirement_text(leaf("REQ-4-3-2", given=GIVEN_4_3_2))
        assert seed_presence_findings(unrelated, _whole(), project, log=quiet) == []

        user = requirement_text(leaf("REQ-2-2-2", description=DESC_2_2_2))
        assert len(seed_presence_findings(user, _whole(), project, log=quiet)) == 1

        assert seed_presence_findings(unrelated, "Requirement REQ-1: plain text", project, log=quiet) == []
    finally:
        shutil.rmtree(project, ignore_errors=True)

@case
def the_platform_deliverables_v124_reports_and_v116_stays_silent():
    import yaml
    github = SAMPLES / "hackathon-github-platform.yaml"
    v124, v116 = LOGS / "ws-739d616d5d5b" / "template", LOGS / "ws-60c39dec0076" / "template"
    if not (github.is_file() and v124.is_dir() and v116.is_dir()):
        print("      (skipped: platform deliverables not on this machine)")
        return
    from agent.detaudit import requirement_text
    from agent.seed_presence import seed_presence_findings
    whole = github.read_text(encoding="utf-8")
    tree = yaml.safe_load(whole)
    node = next(n for n in _leaves(tree) if n.get("id") == "REQ-2-2-4")
    own = requirement_text(node)
    reported = seed_presence_findings(own, whole, v124, log=lambda _m: None)
    assert len(reported) == 1 and "`frontend-child`" in reported[0]["problem"] and "`platform-team`" in reported[0]["problem"], reported
    assert seed_presence_findings(own, whole, v116, log=lambda _m: None) == []

def _leaves(node):
    kids = [c for c in node.get("children") or [] if isinstance(c, dict)]
    if not kids:
        yield node
    for kid in kids:
        yield from _leaves(kid)

@case
def detaudit_calls_the_check_once_with_the_batch_text_and_the_whole_requirement():
    from agent import detaudit
    source = inspect.getsource(detaudit.deterministic_findings)
    assert source.count("seed_presence_findings(requirement, whole, project_dir, log=log)") == 1

def _db(rows_sql: list[str]) -> tuple[Path, str]:
    folder = Path(tempfile.mkdtemp(prefix="arc-seed2-db-"))
    path = folder / "app.db"
    con = sqlite3.connect(path)
    con.executescript(
        "create table branches (id integer primary key, name text);"
        "create table pulls (id integer primary key, title text, status text, base_branch_id integer, head_branch_id integer);"
        "create table organizations (id integer primary key, name text);"
        "insert into branches values (1, 'main'), (2, 'feature-x'), (3, 'feature-y');"
        + "".join(rows_sql))
    con.commit()
    con.close()
    return folder, str(path)

@case
def seed_identity_reports_an_occupied_vacancy_in_the_data():
    from agent.seed_identity import check_vacancies, format_problem, format_vacancy
    free = {"req": "REQ-9", "kind": "free", "values": ["main", "feature-x"], "qualifiers": ["open"], "thing": "PR", "sentence": "s"}
    absent = {"req": "REQ-8", "kind": "absent", "values": ["org-new"], "qualifiers": [], "thing": "candidate organization identifier", "sentence": "s"}
    unbound = {"req": "REQ-7", "kind": "unbound", "values": [], "qualifiers": ["existing"], "thing": "PR", "sentence": "these pairs ..."}
    runs = {
        "occupied": (["insert into pulls values (1, 'Seed PR', 'Open', 1, 2);"], ["REQ-9"]),
        "other pair": (["insert into pulls values (1, 'Seed PR', 'Open', 1, 3);"], []),
        "closed": (["insert into pulls values (1, 'Seed PR', 'closed', 1, 2);"], []),
        "absent taken": (["insert into organizations values (1, 'org-new');"], ["REQ-8"]),
    }
    for name, (rows, expected) in runs.items():
        folder, path = _db(rows)
        try:
            problems, _info = check_vacancies(path, [free, absent, unbound])
            assert sorted(p["req"] for p in problems) == expected, (name, problems)
            for p in problems:
                p["phase"] = "boot"
                assert "occupied" in format_problem(p)
            if name == "occupied":
                assert "`Seed PR`" in problems[0]["where"][0], problems
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    assert "no open PR on `main` + `feature-x`" in format_vacancy(free) and "must not exist" in format_vacancy(absent)

@case
def seed_identity_lists_the_stated_vacancies_even_when_it_cannot_run_the_seed():
    import yaml
    from agent import seed_identity
    text = yaml.safe_dump(platform_tree(), allow_unicode=True, sort_keys=False)
    empty = Path(tempfile.mkdtemp(prefix="arc-seed2-empty-"))
    lines: list[str] = []
    try:
        result = seed_identity.check(text, str(empty), time_limit=5)
        assert result["meta"].get("skipped") == "no backend/src/database", result["meta"]
        assert len(result["meta"]["vacancies"]) == 4, result["meta"]["vacancies"]
        seed_identity.log_seed_identity(text, empty, log=lines.append, time_limit=5)
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    assert lines and lines[0].startswith("[seed-identity] 4 vacancy precondition(s) stated: ") and "`mobile-guild`" in lines[0], lines
    assert seed_identity.requirement_vacancies("plain text, no tree") == []

    only = yaml.safe_dump({"id": "R", "children": [leaf("REQ-8", given="The target name `n-new` does not exist.")]})
    empty = Path(tempfile.mkdtemp(prefix="arc-seed2-empty-"))
    try:
        meta = seed_identity.check(only, str(empty), time_limit=5)["meta"]
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    assert meta.get("skipped") == "no backend/src/database" and len(meta["vacancies"]) == 1, meta

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
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} seed2 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
