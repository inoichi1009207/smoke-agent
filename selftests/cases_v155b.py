from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import stage_controls as sc
from agent import continuation

HOME_LINK = 'import { Link } from "react-router-dom";\nexport default function Home() {\n  return <main><h1>Welcome</h1>\n    <Link to="/signin" className="underline">\n      Sign in\n    </Link></main>;\n}\n'
SIGN_IN_FORM = 'export default function SignIn() {\n  return <form><button type="submit" className="x">Sign in</button></form>;\n}\n'
HOME_BARE = 'export default function Home() {\n  return <main><h1>Welcome</h1></main>;\n}\n'
HEADER_BUTTON = ('export function AppHeader() {\n  const navigate = useNavigate();\n  return <nav><button type="button" '
                 'onClick={() => navigate("/signin")} className="rounded border">\n    Sign in\n  </button></nav>;\n}\n')
HEADER_LINK = 'export function AppHeader() {\n  return <nav><Link to="/signin">Sign in</Link></nav>;\n}\n'

def _app(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="v155b-"))
    for rel, text in files.items():
        p = d / "frontend" / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d

def _stage2(d: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = d / "frontend" / "src" / rel
        if text is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

STAGE1 = {"pages/HomePage.tsx": HOME_LINK, "pages/SignInPage.tsx": SIGN_IN_FORM}

def link_made_button_is_reported():
    d = _app(STAGE1)
    try:
        assert sc.record_baseline(d) == 2
        _stage2(d, {"pages/HomePage.tsx": HOME_BARE, "components/AppHeader.tsx": HEADER_BUTTON})
        f = sc.stage_control_findings(d, log=lambda m: None)
        assert len(f) == 1, f
        assert "'Sign in' as a link (pages/HomePage.tsx)" in f[0]["problem"] and "components/AppHeader.tsx" in f[0]["where"]
        assert "<Link to=" in f[0]["fix"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

def quiet_cases():
    for change in ({}, {"pages/HomePage.tsx": HOME_BARE, "components/AppHeader.tsx": HEADER_LINK},
                   {"pages/HomePage.tsx": HOME_BARE}):
        d = _app(STAGE1)
        try:
            sc.record_baseline(d)
            _stage2(d, change)
            assert sc.stage_control_findings(d, log=lambda m: None) == [], change
        finally:
            shutil.rmtree(d, ignore_errors=True)
    d = _app({"pages/HomePage.tsx": HOME_BARE, "components/AppHeader.tsx": HEADER_BUTTON})
    try:
        assert sc.stage_control_findings(d) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)

def contract_note_lists_links():
    d = _app(STAGE1)
    try:
        app = continuation.existing_app(d, [])
        assert app.get("links") == ["Sign in"], app.get("links")
        note = continuation.contract_note(app, [])
        assert 'Links of the existing pages (they stay links): "Sign in"' in note and "keeps its KIND" in note
    finally:
        shutil.rmtree(d, ignore_errors=True)

def entry_button_without_baseline():

    d = _app({"pages/HomePage.tsx": HOME_BARE, "components/AppHeader.tsx": HEADER_BUTTON, "pages/SignInPage.tsx": SIGN_IN_FORM})
    try:
        f = sc.entry_link_findings(d, log=lambda m: None)
        assert len(f) == 1 and "components/AppHeader.tsx:3" in f[0]["where"], f
        assert '<Link to="/signin">Sign in</Link>' in f[0]["fix"]
    finally:
        shutil.rmtree(d, ignore_errors=True)

    both = HEADER_BUTTON.replace("</nav>", '<Link to="/signin">Sign in</Link></nav>')
    for files in ({"pages/SignInPage.tsx": SIGN_IN_FORM, "components/AppHeader.tsx": HEADER_LINK},
                  {"components/AppHeader.tsx": both},
                  {"pages/X.tsx": 'export const X = () => <button onClick={() => navigate("/new")}>New</button>;\n'}):
        d = _app(files)
        try:
            assert sc.entry_link_findings(d, log=lambda m: None) == [], files
        finally:
            shutil.rmtree(d, ignore_errors=True)

ROUTED_HEADER = ('import { Link, useNavigate } from "react-router-dom";\r\nexport function AppHeader() {\r\n'
                 '  const navigate = useNavigate();\r\n  return <nav><Link to="/">Home</Link>\r\n    <button\r\n'
                 '      type="button"\r\n      onClick={() => navigate(\'/signin\')}\r\n      className="rounded border"\r\n'
                 '    >\r\n      Sign in\r\n    </button></nav>;\r\n}\r\n')

def entry_rewrite_and_gates():

    d = _app({"components/AppHeader.tsx": ROUTED_HEADER})
    try:
        path = d / "frontend" / "src" / "components" / "AppHeader.tsx"
        path.write_bytes(ROUTED_HEADER.encode("utf-8"))
        assert sc.fix_entry_buttons(d, "", log=lambda m: None) == ["components/AppHeader.tsx:Sign in"]
        out = path.read_bytes().decode("utf-8")
        assert '<Link to="/signin" className="rounded border">Sign in</Link>' in out and "<button" not in out, out
        assert out.count("\r\n") == ROUTED_HEADER.count("\r\n") - 6 and "\n" not in out.replace("\r\n", ""), repr(out)
        assert sc.entry_link_findings(d, "", log=lambda m: None) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)

    for text in (HEADER_BUTTON, ROUTED_HEADER.replace('type="button"', 'type="button" disabled={busy}')):
        d = _app({"components/AppHeader.tsx": text})
        try:
            assert sc.fix_entry_buttons(d, "", log=lambda m: None) == []
            assert len(sc.entry_link_findings(d, "", log=lambda m: None)) == 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    d = _app({"components/AppHeader.tsx": 'const navigate = useNavigate();\nexport const H = () => <button onClick={() => navigate("/login")}>{t("signIn")}</button>;\n'})
    try:
        f = sc.entry_link_findings(d, "", log=lambda m: None)
        assert len(f) == 1 and "the entry to /login" in f[0]["problem"], f
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = _app({"components/AppHeader.tsx": HEADER_BUTTON})
    try:
        whole = "The visitor clicks the “Sign in” button in the header."
        assert sc.entry_link_findings(d, whole, log=lambda m: None) == []
        assert len(sc.entry_link_findings(d, whole + " Later the “Sign in” link is shown.", log=lambda m: None)) == 1
        assert sc.stated_kinds("the “Sign in” link and the “Sign in” button", "Sign in") == {"link", "button"}
    finally:
        shutil.rmtree(d, ignore_errors=True)

def codex_r58_counterexamples():
    import time

    d = _app({"Home.tsx": '<Link to="/signin">Sign in</Link>\n', "SignIn.tsx": '<button type="submit">Sign in</button>\n'})
    try:
        sc.record_baseline(d)
        _stage2(d, {"Home.tsx": "<Header />\n", "SignIn.tsx": "<CredentialsForm />\n",
                    "Header.tsx": '<Link to="/signin">Sign in</Link>\n', "CredentialsForm.tsx": '<button type="submit">Sign in</button>\n'})
        assert sc.stage_control_findings(d, log=lambda m: None) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = _app({"App.tsx": '<Link to="/signin">Sign in</Link>\n<button type="submit">Sign in</button>\n'})
    try:
        sc.record_baseline(d)
        _stage2(d, {"App.tsx": 'const navigate = useNavigate();\n<button onClick={() => navigate("/signin")}>Sign in</button>\n<button type="submit">Sign in</button>\n'})
        assert len(sc.stage_control_findings(d, log=lambda m: None)) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    labels = [f"Go {i}" for i in range(12)]
    d = _app({f"P{i}.tsx": f'<Link to="/p{i}">{lbl}</Link>\n' for i, lbl in enumerate(labels)})
    try:
        sc.record_baseline(d)
        _stage2(d, {f"P{i}.tsx": f'const navigate = useNavigate();\n<button onClick={{() => navigate("/p{i}")}}>{lbl}</button>\n' for i, lbl in enumerate(labels)})
        assert len(sc.stage_control_findings(d, log=lambda m: None)) == sc.MAX_FINDINGS
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = _app({"Big.tsx": "/* " + "<button " * 4000 + "*/\n"})
    try:
        sc.record_baseline(d)
        assert (d / sc.BASELINE).is_file()
        t0 = time.monotonic()
        sc.controls(d)
        sc.entry_link_findings(d, "", log=lambda m: None)
        assert time.monotonic() - t0 < 3.0, time.monotonic() - t0
        sc.clear_baseline(d)
        assert not (d / sc.BASELINE).exists() and sc.stage_control_findings(d) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)

def codex_r59_counterexamples():
    from agent.settings import SETTINGS
    assert SETTINGS.get("det_entry_link_rewrite") is False
    nav = 'const navigate = useNavigate();\n'
    quiet = [
        nav + '<button type="submit" onClick={() => navigate("/signin")}>Sign in</button>\n',
        nav + '<button onClick={() => { save(); navigate("/signin"); }}>Sign in</button>\n',
        nav + '<form onSubmit={save}>\n  <button onClick={() => navigate("/signin")}>Sign in</button>\n</form>\n',
        'const navigate = (p) => { save(); location.assign(p); };\n<button onClick={() => navigate("/signin")}>Sign in</button>\n',
    ]
    for text in quiet:
        d = _app({"X.tsx": text})
        try:
            assert sc.entry_link_findings(d, "", log=lambda m: None) == [], text
        finally:
            shutil.rmtree(d, ignore_errors=True)
    for whole in ("Click the Sign in button.", "Click the 'Sign in' button.", 'Click the button named "Sign in".'):
        assert sc.stated_kinds(whole, "Sign in") == {"button"}, whole
    assert sc.stated_kinds("Design input button", "Sign in") == set()

CASES = [link_made_button_is_reported, quiet_cases, contract_note_lists_links, entry_button_without_baseline,
         entry_rewrite_and_gates, codex_r58_counterexamples, codex_r59_counterexamples]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v155b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
