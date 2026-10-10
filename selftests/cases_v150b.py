from __future__ import annotations

import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import stated_messages as sm

CASES = []

def case(function):
    CASES.append(function)
    return function

def yaml_requirement(leaves: list[tuple[str, str, list[tuple[str, str]]]]) -> str:
    import json
    lines = ["id: ROOT", "name: Fixture", "type: FOLDER", "children:"]
    for leaf_id, description, steps in leaves:
        lines += [f"- id: {leaf_id}", f"  name: {leaf_id}", "  type: ATOMIC",
                  f"  description: {json.dumps(description, ensure_ascii=False)}"]
        if steps:
            lines += ["  scenarios:", f"  - name: {leaf_id} - Scenario 1", "    steps:"]
            for keyword, content in steps:
                lines += [f"    - keyword: {keyword}", f"      content: {json.dumps(content, ensure_ascii=False)}"]
    return "\n".join(lines) + "\n"

def messages(description: str, steps=None) -> list[str]:
    return [i["message"] for i in sm.stated_messages(yaml_requirement([("REQ-9", description, steps or [])]))]

def run(requirement: str, files: dict[str, str], **kw) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text), encoding="utf-8")
        return sm.stated_message_findings(requirement, root, log=lambda *_: None, **kw)

@case
def c01_taken_forms():
    assert messages("A duplicate identifier displays “Organization name already exists” without opening it.") == ["Organization name already exists"]
    assert messages("Saving displays the exact message “Profile saved successfully”.") == ["Profile saved successfully"]
    assert messages("A cycle is rejected with “Cyclic team hierarchy is not allowed”, and the parent stays.") == ["Cyclic team hierarchy is not allowed"]
    assert messages("The page displays both exact messages “Name format is invalid” and “Display name is required”.") == [
        "Name format is invalid", "Display name is required"]
    assert messages("After the failed import the message “Import failed again” is displayed.") == ["Import failed again"]
    assert messages("A query with no matching repository displays “No results”, also after reload.") == ["No results"]
    assert messages("A visitor who lacks access sees “You don’t have access”.") == ["You don't have access"]
    then = [("GIVEN", "A user exists."), ("WHEN", "The user clicks “Save”."),
            ("THEN", "The page displays the exact message “Password updated”."), ("AND", "It shows “Session kept alive”.")]
    assert messages("Nothing here.", then) == ["Password updated", "Session kept alive"]

@case
def c02_never_taken():
    for text in [
        "The applicable message is “Current password is incorrect”, “Confirmation does not match”, or “Field is required”.",
        "The page does not display “Pending invitation text”.",
        "The list never shows “Archived repository name”.",
        "For example, an invalid name displays “Bad name given here”.",
        "The step runs with concrete values and displays “East region total”.",
        "The Comment button may either be disabled or display “Comment is required” when activated.",
        "The page displays a button named “Create account now”.",
        "The page displays the “Sign in here” link.",
        "The form displays visibly labeled “Organization name” and “Display name” fields.",
        "The workspace visibly displays the username `nora-demo`.",
        "The page displays “Saved”.",
        "The PR page displays `test: success`, the setter, and time.",
        "The menu displays “Member menu <username>” for every row.",
        "The heading displays the exact team name `Frontend Team` and the role “Write”.",
    ]:
        assert messages(text) == [], (text, messages(text))
    seeded = [("GIVEN", "The organization `Acme Demo` exists."), ("WHEN", "The visitor enters `Hello there world` in “Title”."),
              ("THEN", "The visitor sees `Acme Demo` and the page displays “Hello there world”.")]
    assert messages("Nothing.", seeded) == [], messages("Nothing.", seeded)

PASSWORD_REQ = yaml_requirement([("REQ-1-3", "The security form has a button “Update password”.", [
    ("GIVEN", "The account `pc-user` exists."),
    ("WHEN", "The user enters a wrong current password and clicks “Update password”."),
    ("THEN", "The password-settings page displays the exact message “Current password is incorrect”.")])])
PASSWORD_FRONT = """\
    import axios from 'axios';
    export function SecurityPage() {
      const submit = async () => {
        try { await axios.put('/api/me/password', {}); }
        catch (err) { setError(err.response?.data?.message || 'Password update failed'); }
      };
      return <form><button type="submit">Update password</button></form>;
    }
    """
PASSWORD_BACK = """\
    router.put('/me/password', (req, res) => {
      if (!ok) {
        return res.status(400).json({ message: 'Current password is incorrect.' });
      }
    });
    """

@case
def c03_punct():
    found = run(PASSWORD_REQ, {"frontend/src/pages/SecurityPage.tsx": PASSWORD_FRONT, "backend/src/routes/auth.js": PASSWORD_BACK})
    assert len(found) == 1, found
    f = found[0]
    assert f["where"] == "stated_messages: backend/src/routes/auth.js:3", f
    assert f["requirement"].startswith("REQ-1-3") and "Current password is incorrect" in f["requirement"], f
    assert "Current password is incorrect." in f["problem"] and set(f) == {"where", "requirement", "problem", "fix"}, f
    contains = PASSWORD_REQ.replace("displays the exact message “Current", "displays an error containing “Current")
    assert not run(contains, {"frontend/src/pages/SecurityPage.tsx": PASSWORD_FRONT, "backend/src/routes/auth.js": PASSWORD_BACK})
    exact_front = PASSWORD_FRONT.replace("'Password update failed'", "'Current password is incorrect'")
    assert not run(PASSWORD_REQ, {"frontend/src/pages/SecurityPage.tsx": exact_front, "backend/src/routes/auth.js": PASSWORD_BACK})
    stripped = PASSWORD_FRONT.replace("err.response?.data?.message", "(err.response?.data?.message || '').replace(/\\.$/, '')")
    assert not run(PASSWORD_REQ, {"frontend/src/pages/SecurityPage.tsx": stripped, "backend/src/routes/auth.js": PASSWORD_BACK})

MEMBER_REQ = yaml_requirement([("REQ-2-2-3", "The People page has an “Add member” button.", [
    ("WHEN", "The owner enters `existing-member` and clicks “Add member”."),
    ("THEN", "The first submission displays the exact message “Account is already a member”.")])])
MEMBER_BACK = """\
    router.post('/orgs/:name/members', (req, res) => {
      return res.status(409).json({
        message: 'User is already a member',
        accountMessage: 'Account is already a member',
      });
    });
    """
MEMBER_FRONT = """\
    export function People() {
      const add = () => postNow('/members', {}).catch((err) => setError(err.data?.message || 'Could not add'));
      return <button onClick={add}>Add member</button>;
    }
    """

@case
def c04_unread():
    found = run(MEMBER_REQ, {"backend/src/routes/orgs.js": MEMBER_BACK, "frontend/src/People.tsx": MEMBER_FRONT})
    assert len(found) == 1 and found[0]["where"].endswith("backend/src/routes/orgs.js:4"), found
    assert "`accountMessage`" in found[0]["problem"], found
    reads = MEMBER_FRONT.replace("err.data?.message", "err.data?.accountMessage")
    assert not run(MEMBER_REQ, {"backend/src/routes/orgs.js": MEMBER_BACK, "frontend/src/People.tsx": reads})
    unknown = "router.post('/m', () => { throw new Error('Account is already a member'); });\n"
    assert not run(MEMBER_REQ, {"backend/src/routes/orgs.js": unknown, "frontend/src/People.tsx": MEMBER_FRONT})
    generic = MEMBER_FRONT.replace("err.data?.message", "Object.values(err.data).join(' ')")
    assert not run(MEMBER_REQ, {"backend/src/routes/orgs.js": MEMBER_BACK, "frontend/src/People.tsx": generic})

TEAM_REQ = yaml_requirement([
    ("REQ-2-2-1", "The Teams page opens a form with a “Team name” field and a “Create team” button.", [
        ("WHEN", "The owner enters `-bad` and clicks “Create team”."),
        ("THEN", "The page displays the exact message “Team name is invalid”.")]),
    ("REQ-2-2-9", "Another leaf with a “Delete team” button.", [])])
TEAM_FRONT = """\
    export function NewTeam() {
      return (
        <form>
          <label htmlFor="t">Team name</label><input id="t" />
          <button type="submit">Create team</button>
        </form>
      );
    }
    """

@case
def c05_missing():
    found = run(TEAM_REQ, {"frontend/src/pages/NewTeam.tsx": TEAM_FRONT, "backend/src/app.js": "module.exports = {};\n"})
    assert len(found) == 1 and found[0]["where"] == "stated_messages: frontend/src/pages/NewTeam.tsx:5", found
    assert "nowhere" in found[0]["problem"] and found[0]["requirement"].startswith("REQ-2-2-1"), found
    not_built = TEAM_FRONT.replace("Create team", "Submit").replace("Team name", "Name")
    assert not run(TEAM_REQ, {"frontend/src/pages/NewTeam.tsx": not_built})
    for back in ["const msg = `${field} is invalid`;\n",
                 "const msg = label + ' is invalid';\n",
                 "const msg = 'Team name is invalid: use lower case';\n"]:
        assert not run(TEAM_REQ, {"frontend/src/pages/NewTeam.tsx": TEAM_FRONT, "backend/src/v.js": back}), back
    spread = TEAM_FRONT.replace("</form>", "<p role=\"alert\">Team name is\n           invalid</p></form>")
    assert not run(TEAM_REQ, {"frontend/src/pages/NewTeam.tsx": spread})

    desc_req = yaml_requirement([("REQ-7", "The form has a “Create team” button. A malformed name displays “Team name is "
                                           "invalid”, and a duplicate name displays “Team name already taken”.", [])])
    assert not run(desc_req, {"frontend/src/pages/NewTeam.tsx": TEAM_FRONT})
    sibling = TEAM_FRONT.replace("</form>", "<p>Team name already taken</p></form>")
    found = run(desc_req, {"frontend/src/pages/NewTeam.tsx": sibling})
    assert len(found) == 1 and "Team name is invalid" in found[0]["requirement"], found

@case
def c06_entry_point():
    assert sm.stated_message_findings("not: [yaml", Path("Z:/no/such/project"), log=lambda *_: None) == []
    assert sm.stated_message_findings(TEAM_REQ, Path("Z:/no/such/project"), log=lambda *_: None) == []
    assert sm.stated_message_findings(None, Path("."), log=lambda *_: None) == []
    assert not run(TEAM_REQ, {"frontend/src/pages/NewTeam.tsx": TEAM_FRONT}, leaf_ids=["REQ-2-2-9"])
    leaves = [(f"REQ-{i}", f"The form has a “Create thing {i}” button and displays “Thing number {i} failed”.", [])
              for i in range(12)]
    front = "export const P = () => <div>" + "".join(f"<button>Create thing {i}</button>" for i in range(12)) + "</div>;\n"
    back = "".join(f"res.status(400).json({{ message: 'Thing number {i} failed.' }});\n" for i in range(12))
    found = run(yaml_requirement(leaves), {"frontend/src/P.tsx": front, "backend/src/a.js": back})
    assert len(found) == 8, len(found)

def page(body: str, head: str = "", extra: str = "") -> str:
    return (f"import {{ useState }} from 'react';\n{head}\n{extra}\n"
            f"export default function Page() {{\n  const [open, setOpen] = useState(false);\n"
            f"  const rows = [{{ name: 'a' }}];\n  return (\n    <div>\n{body}\n    </div>\n  );\n}}\n")

ROW = ("function GrantRow({ grant }: { grant: { name: string } }) {\n"
       "  return (\n    <li>\n      <span>{grant.name}</span>\n"
       "      <select aria-label=\"Role\"><option>Read</option></select>\n    </li>\n  );\n}\n")

DUP_FIXTURES = [
    ("labelled input + aria-label input, both unconditional",
     {"pages/Page.tsx": page('<label htmlFor="n">Name</label><input id="n" />\n<input aria-label="Name" />')},
     1, '"Name"'),
    ("map rows with a literal aria-label vs the picker's labelled select (stage-1 shape)",
     {"pages/Page.tsx": page('{open && (<form><label htmlFor="r">Role</label><select id="r"><option>Read</option></select></form>)}\n'
                             '<ul>{rows.map((g) => (<GrantRow key={g.name} grant={g} />))}</ul>', extra=ROW)},
     1, '"Role"'),
    ("map only: the same control repeated per row is not reported by default",
     {"pages/Page.tsx": page('<ul>{rows.map((g) => (<GrantRow key={g.name} grant={g} />))}</ul>', extra=ROW)},
     0, None),
    ("map only with report_map_only=True",
     {"pages/Page.tsx": page('<ul>{rows.map((g) => (<GrantRow key={g.name} grant={g} />))}</ul>', extra=ROW)},
     1, "every item", {"report_map_only": True}),
    ("name built from the item is no literal name",
     {"pages/Page.tsx": page('<label htmlFor="r">Role</label><select id="r"></select>\n'
                             '{rows.map((g) => (<select key={g.name} aria-label={`Role for ${g.name}`}></select>))}')},
     0, None),
    ("ternary branches are mutually exclusive",
     {"pages/Page.tsx": page('{open ? <input aria-label="Title" /> : <input aria-label="Title" readOnly />}')},
     0, None),
    ("cond && vs !cond && are mutually exclusive",
     {"pages/Page.tsx": page('{open && <input aria-label="Title" />}\n{!open && (<input aria-label="Title" />)}')},
     0, None),
    ("cond && control vs an unconditional one coexist",
     {"pages/Page.tsx": page('{open && <input aria-label="Title" />}\n<input aria-label="Title" />')},
     1, '"Title"'),
    ("different early returns never meet",
     {"pages/Page.tsx": "export default function Page({ loading }: { loading: boolean }) {\n"
                        "  if (loading) {\n    return <input aria-label=\"Query\" />;\n  }\n"
                        "  return (<div><input aria-label=\"Query\" /></div>);\n}\n"},
     0, None),
    ("a control under a hidden class is skipped",
     {"pages/Page.tsx": page('<div className="hidden md:block"><input aria-label="Search" /></div>\n<input aria-label="Search" />')},
     0, None),
    ("a control under md:hidden is skipped",
     {"pages/Page.tsx": page('<div className="block md:hidden"><input aria-label="Search" /></div>\n<input aria-label="Search" />')},
     0, None),
    ("different roles with one name are no pair",
     {"pages/Page.tsx": page('<input aria-label="Status" />\n<select aria-label="Status"><option>Open</option></select>')},
     0, None),
    ("checkboxes with one name are a pair",
     {"pages/Page.tsx": page('<input type="checkbox" aria-label="Done" />\n<label><input type="checkbox" /> Done</label>')},
     1, "checkbox"),
    ("a dynamic label text is no literal name",
     {"pages/Page.tsx": page('<label htmlFor="n">{label}</label><input id="n" />\n<input aria-label="Name" />',
                             extra="const label = 'Name';")},
     0, None),
    ("aria-labelledby is ignored",
     {"pages/Page.tsx": page('<input aria-labelledby="h" aria-label="Name" />\n<input aria-label="Name" />')},
     0, None),
    ("a wrapping label vs a htmlFor label",
     {"pages/Page.tsx": page('<label>Email <input type="email" /></label>\n<label htmlFor="e">Email</label><input id="e" />')},
     1, '"Email"'),
    ("an imported child is expanded one level",
     {"pages/Page.tsx": page('<Filter />\n<input aria-label="Filter" />', head="import Filter from '../components/Filter';"),
      "components/Filter.tsx": "export default function Filter() {\n  return <div><input aria-label=\"Filter\" /></div>;\n}\n"},
     1, "rendered via <Filter>"),
    ("a grandchild (two levels) is not expanded",
     {"pages/Page.tsx": page('<Outer />\n<input aria-label="Filter" />', head="import Outer from '../components/Outer';"),
      "components/Outer.tsx": "import Filter from './Filter';\nexport default function Outer() {\n  return <section><Filter /></section>;\n}\n",
      "components/Filter.tsx": "export default function Filter() {\n  return <div><input aria-label=\"Filter\" /></div>;\n}\n"},
     0, None),
    ("unknown conditions a && X vs b && Y are not certain",
     {"pages/Page.tsx": page('{a && <input aria-label="Note" />}\n{b && <input aria-label="Note" />}',
                             extra="const a = true; const b = false;")},
     0, None),
    ("|| around a control is uncertain",
     {"pages/Page.tsx": page('{a || <input aria-label="Note" />}\n<input aria-label="Note" />', extra="const a = false;")},
     0, None),
    ("two instances of a child with an internal condition are not certain",
     {"pages/Page.tsx": page('<Row />\n<Row />', extra="function Row() {\n  const [e] = useState(false);\n"
                                                      "  return <div>{e && <input aria-label=\"Name\" />}</div>;\n}\n")},
     0, None),
    ("two instances of a child with an unconditional control are a pair",
     {"pages/Page.tsx": page('<Row />\n<Row />', extra="function Row() {\n  return <div><input aria-label=\"Name\" /></div>;\n}\n")},
     1, '"Name"'),
    ("buttons are never checked",
     {"pages/Page.tsx": page('<button>Save</button>\n<button aria-label="Save">S</button>')},
     0, None),
    ("JSX held in a variable is uncertain",
     {"pages/Page.tsx": "export default function Page() {\n  const extra = <input aria-label=\"Find\" />;\n"
                        "  return (<div>{extra}<input aria-label=\"Find\" /></div>);\n}\n"},
     0, None),
    ("a project wrapper's {children} slot is followed",
     {"pages/Page.tsx": page('<Card><input aria-label="Query" /></Card>\n<input aria-label="Query" />',
                             extra="function Card({ children }: { children: React.ReactNode }) {\n"
                                   "  return <section className=\"card\">{children}</section>;\n}\n")},
     1, '"Query"'),
    ("children of an unknown (library) component are uncertain",
     {"pages/Page.tsx": page('<Modal><input aria-label="Query" /></Modal>\n<input aria-label="Query" />',
                             head="import Modal from 'some-lib';")},
     0, None),
    ("an apostrophe in JSX text does not break the reader",
     {"pages/Page.tsx": page("<p>Don't panic, it's fine</p>\n<input aria-label=\"Name\" />\n<input aria-label=\"Name\" />")},
     1, '"Name"'),
    ("an input of type hidden is no control",
     {"pages/Page.tsx": page('<input type="hidden" aria-label="Token" />\n<input aria-label="Token" />')},
     0, None),
    ("two distinct controls inside one map item are a pair",
     {"pages/Page.tsx": page('{rows.map((r) => (<div key={r.name}><input aria-label="Value" /><input aria-label="Value" /></div>))}')},
     1, '"Value"'),
]

def dup_case(fixture):
    title, files, expected, needle = fixture[:4]
    kwargs = fixture[4] if len(fixture) > 4 else {}

    def check():
        from agent.duplicate_names import duplicate_name_source_findings
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                path = root / "frontend" / "src" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            logs: list[str] = []
            found = duplicate_name_source_findings(root, log=logs.append, **kwargs)
        assert len(found) == expected, (title, found, logs)
        assert needle is None or all(needle in f["problem"] for f in found), (title, found)
        assert all(f["where"].startswith("duplicate_names: frontend/src/") and set(f) == {"where", "requirement", "problem", "fix"}
                   for f in found), found

        assert not any("skipped" in line or "crashed" in line for line in logs), (title, logs)

    check.__name__ = "d" + "_".join(title.replace("'", "").replace("&", "and").replace("|", "or").split())[:70]
    return check

for _fixture in DUP_FIXTURES:
    CASES.append(dup_case(_fixture))

@case
def d_entry_point_never_raises():
    from agent.duplicate_names import duplicate_name_source_findings
    assert duplicate_name_source_findings(Path("Z:/no/such/project"), log=lambda *_: None) == []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "Broken.tsx").write_text("export default function B() { return (<div><input aria-label=\"X\" ", encoding="utf-8")
        body = "".join(f'<input aria-label="N{i}" />\n<input aria-label="N{i}" />\n' for i in range(12))
        (root / "frontend" / "src" / "Many.tsx").write_text(page(body), encoding="utf-8")
        found = duplicate_name_source_findings(root, log=lambda *_: None)
    assert len(found) == 8, len(found)

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
