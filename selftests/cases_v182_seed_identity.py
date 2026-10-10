from __future__ import annotations

import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES: list = []

def case(function):
    CASES.append(function)
    return function

class Switch:

    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        from agent.settings import SETTINGS

        for key, value in self.values.items():
            self.saved[key] = (key in SETTINGS, SETTINGS.get(key))
            SETTINGS[key] = value
        return self

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, (had, value) in self.saved.items():
            if had:
                SETTINGS[key] = value
            else:
                SETTINGS.pop(key, None)
        return False

class StubCheck:

    def __init__(self, result):
        self.result, self.saved = result, None

    def __enter__(self):
        import agent.seed_identity as si

        self.saved = si.check

        def fake(*_a, **_k):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        si.check = fake
        return self

    def __exit__(self, *exc):
        import agent.seed_identity as si

        si.check = self.saved
        return False

ROLE_REQ = ("The system pre-provisions the account `alice` with email `alice@example.com` and password `secret1`, organization `Acme`. "
            "Given an organization Owner is signed in, the settings page shows the billing tab.")

ROLE_PROBLEM = {'kind': 'credential_role', 'account': 'alice', 'container_kind': 'organization', 'containers': ['Acme'],
                'required_role': 'owner', 'data_roles': ['memberships:member'], 'phase': 'boot'}
FREE_PROBLEM = {'kind': 'vacancy_occupied', 'req': 'REQ-2-1', 'vacancy': 'free', 'values': ['Acme', 'alice'],
                'thing': 'open invitation', 'qualifiers': ['open'], 'phase': 'lazy',
                'where': ['invitations rowid=1 `inv` status=open (organization Acme)']}
ABSENT_PROBLEM = {'kind': 'vacancy_occupied', 'req': 'REQ-3-1', 'vacancy': 'absent', 'values': ['Roadmap'],
                  'thing': 'project', 'qualifiers': [], 'phase': 'boot', 'where': ['projects rowid=4']}
DUP_PROBLEM = {'kind': 'seed_duplicate', 'record_kind': 'repository', 'name': 'web', 'rows': 2, 'table': 'repositories',
               'scope': 'global', 'phase': 'boot', 'where': ['rowid=1 (owner alice)', 'rowid=2 (owner bob)']}

def db(statements: list[str]) -> str:
    path = Path(tempfile.mkdtemp(prefix="v182si-")) / "seed.db"
    con = sqlite3.connect(path)
    for s in statements:
        con.execute(s)
    con.commit()
    con.close()
    return str(path)

def role_db(role: str | None) -> str:
    stmts = ["create table users (id integer primary key, username text, email text)",
             "create table organizations (id integer primary key, name text)",
             "create table memberships (id integer primary key, organization_id integer, user_id integer, role text)",
             "insert into users values (1, 'alice', 'alice@example.com')",
             "insert into organizations values (1, 'Acme')"]
    if role is not None:
        stmts.append(f"insert into memberships values (1, 1, 1, '{role}')")
    return db(stmts)

@case
def wrong_role_on_real_data_fires():
    from agent.seed_identity import check_roles, repair_findings

    probs, _ = check_roles(role_db("member"), ROLE_REQ)
    assert [p['kind'] for p in probs] == ['credential_role'], probs
    with Switch(seed_identity_findings=True):
        found = repair_findings([dict(p, phase='boot') for p in probs])
    assert len(found) == 1 and found[0]['check'] == 'seed-identity/credential_role', found
    f = found[0]
    assert all(f.get(k) for k in ('where', 'requirement', 'problem', 'fix')), f
    assert f['requirement'].startswith('('), f
    assert '`alice`' in f['problem'] and 'owner' in f['fix'] and 'Acme' in f['fix'], f

@case
def occupied_vacancy_on_real_data_fires():
    from agent.seed_identity import check_vacancies, repair_findings

    path = db(["create table projects (id integer primary key, name text)",
               "insert into projects values (1, 'Roadmap')"])
    vac = {'req': 'REQ-3-1', 'kind': 'absent', 'values': ['Roadmap'], 'thing': 'project', 'qualifiers': []}
    probs, _ = check_vacancies(path, [vac])
    assert len(probs) == 1 and probs[0]['where'][0].startswith('projects rowid='), probs
    with Switch(seed_identity_findings=True):
        found = repair_findings(probs)
    assert [f['check'] for f in found] == ['seed-identity/vacancy_occupied'], found
    assert 'REQ-3-1' in found[0]['requirement'] and 'Roadmap' in found[0]['problem'], found

@case
def free_vacancy_and_role_fire_together():
    from agent.seed_identity import repair_findings

    with Switch(seed_identity_findings=True):
        found = repair_findings([DUP_PROBLEM, ROLE_PROBLEM, FREE_PROBLEM, ABSENT_PROBLEM])
    assert [f['check'] for f in found] == ['seed-identity/credential_role', 'seed-identity/vacancy_occupied',
                                           'seed-identity/vacancy_occupied'], found

@case
def default_is_on():
    from agent.settings import SETTINGS
    from agent.seed_identity import repair_findings

    saved = (('seed_identity_findings' in SETTINGS), SETTINGS.pop('seed_identity_findings', None))
    try:
        assert len(repair_findings([ROLE_PROBLEM])) == 1
    finally:
        if saved[0]:
            SETTINGS['seed_identity_findings'] = saved[1]

@case
def same_name_duplicate_stays_silent():
    from agent.seed_identity import repair_findings

    with Switch(seed_identity_findings=True):
        assert repair_findings([DUP_PROBLEM, dict(DUP_PROBLEM, scope='repo_id', name='main')]) == []

@case
def right_role_and_unlinked_account_stay_silent():
    from agent.seed_identity import check_roles, repair_findings

    for role in ("owner", None):
        probs, _ = check_roles(role_db(role), ROLE_REQ)
        assert probs == [], (role, probs)
        with Switch(seed_identity_findings=True):
            assert repair_findings(probs) == []

@case
def absent_value_in_an_unrelated_table_stays_silent():
    from agent.seed_identity import check_vacancies, repair_findings

    path = db(["create table tasks (id integer primary key, title text)", "insert into tasks values (1, 'Roadmap')"])
    vac = {'req': 'REQ-3-1', 'kind': 'absent', 'values': ['Roadmap'], 'thing': 'project', 'qualifiers': []}
    probs, _ = check_vacancies(path, [vac])
    assert len(probs) == 1 and probs[0]['where'][0].startswith('tasks rowid='), probs
    with Switch(seed_identity_findings=True):
        assert repair_findings(probs) == []

@case
def incomplete_evidence_stays_silent():
    from agent.seed_identity import repair_findings

    broken = [dict(ROLE_PROBLEM, data_roles=[]), dict(ROLE_PROBLEM, account=''), {k: v for k, v in ROLE_PROBLEM.items() if k != 'containers'},
              dict(FREE_PROBLEM, where=[]), dict(FREE_PROBLEM, values=[]), dict(ABSENT_PROBLEM, where=['no table here']),
              {'kind': 'credential_role_unlinked', 'account': 'alice'}, 'not a dict', None]
    with Switch(seed_identity_findings=True):
        assert repair_findings(broken) == [], repair_findings(broken)

@case
def off_returns_nothing():
    from agent.seed_identity import repair_findings

    with Switch(seed_identity_findings=False):
        assert repair_findings([ROLE_PROBLEM, FREE_PROBLEM, ABSENT_PROBLEM]) == []
    with Switch(seed_identity_findings="false"):
        assert repair_findings([ROLE_PROBLEM]) == []

def both_logs(result, key):
    from agent.seed_identity import log_seed_identity, log_seed_identity_findings

    old, new = [], []
    with Switch(seed_identity_findings=key), StubCheck(result):
        old_ret = log_seed_identity("req", "proj", log=old.append)
        new_ret, findings = log_seed_identity_findings("req", "proj", log=new.append)
    return old, old_ret, new, new_ret, findings

RESULT = {'problems': [DUP_PROBLEM, ROLE_PROBLEM, FREE_PROBLEM],
          'info': [{'kind': 'coverage', 'names_found': 3, 'names_checked': 4}],
          'meta': {'seconds': 4.2, 'vacancies': [{'req': 'REQ-2-1', 'kind': 'free', 'values': ['Acme'], 'thing': 'invitation',
                                                  'qualifiers': ['open']}]}}

@case
def wrapper_off_logs_exactly_v181():
    for result in (RESULT, {'problems': [], 'info': [], 'meta': {'skipped': 'no seed sentences', 'vacancies': []}},
                   {'problems': [], 'info': [], 'meta': {'skipped': 'seed run produced no database', 'rc': 2, 'seconds': 9}},
                   RuntimeError("boom")):
        old, old_ret, new, new_ret, findings = both_logs(result, False)
        assert old == new and old_ret == new_ret and findings == [], (old, new)

@case
def wrapper_on_adds_one_line_and_the_findings():
    old, old_ret, new, new_ret, findings = both_logs(RESULT, True)
    assert new_ret == old_ret and len(old_ret) == 3
    assert new[:-1] == old and 'handed to the repair' in new[-1] and new[-1].startswith('[seed-identity] 2 '), new[-1]
    assert [f['check'] for f in findings] == ['seed-identity/credential_role', 'seed-identity/vacancy_occupied']

    only_dup = dict(RESULT, problems=[DUP_PROBLEM])
    old, _, new, _, findings = both_logs(only_dup, True)
    assert old == new and findings == []

@case
def findings_fit_the_existing_brief():
    from agent.audit import findings_brief
    from agent.seed_identity import repair_findings

    with Switch(seed_identity_findings=True):
        text = findings_brief(repair_findings([ROLE_PROBLEM, FREE_PROBLEM]))
    assert '1. [seed data: membership of `alice` in Acme] requirement: "(a GIVEN says' in text, text
    assert '2. [seed data: invitations rowid=1' in text and 'vacancy of REQ-2-1 occupied' in text, text

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"PASS {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
