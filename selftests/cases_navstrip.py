from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import nav_edges

NL = chr(10)
BS = chr(92)
CASES = []

def case(function):
    CASES.append(function)
    return function

def strip(t: str) -> str:
    out = nav_edges._strip_comments(t)
    assert len(out) == len(t), (len(out), len(t))
    return out

@case
def strings_holding_comment_openers_survive():
    src = NL.join([
        'const a = "/files/*";',
        "const b = '//cdn.example/x.js';",
        "const c = `/*${a}//`;",
        'const d = "a // b";',
        "next();",
    ])
    assert strip(src) == src, strip(src)

@case
def real_block_comment_is_blanked():
    src = "a();" + NL + "/* one" + NL + " two */ b();"
    out = strip(src)
    assert "one" not in out and "two" not in out and "*/" not in out, out
    assert out.startswith("a();") and out.endswith(" b();"), out
    jsx = "<div>{/* hidden <Route path=" + '"/x"' + " /> */}</div>"
    assert "<Route" not in strip(jsx) and strip(jsx).startswith("<div>{") and strip(jsx).endswith("}</div>")

@case
def real_line_comment_is_blanked():
    src = "x = 1; // note" + NL + "// whole line" + NL + "y = 2;"
    out = strip(src)
    assert "note" not in out and "whole" not in out, out
    assert out.splitlines()[0].rstrip() == "x = 1;" and out.splitlines()[2] == "y = 2;", out

@case
def url_inside_string_survives_and_comment_after_it_goes():
    src = 'fetch("http://x/api"); // call it' + NL + "const u = 'https://y//z';"
    out = strip(src)
    assert out.splitlines()[0].rstrip() == 'fetch("http://x/api");', out
    assert out.splitlines()[1] == "const u = 'https://y//z';", out

    jsx = "<p>see http://x</p>"
    assert strip(jsx) == jsx

@case
def escaped_quote_does_not_end_the_string():
    src = 'const s = "say ' + BS + '"/*' + BS + '" ok"; /* gone */ z();'
    out = strip(src)
    assert out.startswith('const s = "say ' + BS + '"/*' + BS + '" ok";'), out
    assert "gone" not in out and out.endswith(" z();"), out
    tpl = "const t = `a" + BS + "`/* b`; /* gone */"
    assert strip(tpl).startswith("const t = `a" + BS + "`/* b`;") and "gone" not in strip(tpl)

@case
def quotes_inside_comments_and_stray_apostrophes_stay_local():
    src = NL.join([
        "// don't stop here",
        "/* it's fine */ a();",
        "<p>Don't have an account?</p>",
        "{/* gone */}",
        "b(); // also gone",
    ])
    out = strip(src).splitlines()
    assert out[0].strip() == "" and out[1].strip() == "a();", out
    assert out[2] == "<p>Don't have an account?</p>", out
    assert "gone" not in out[3] and "gone" not in out[4] and out[4].rstrip() == "b();", out

@case
def unterminated_block_comment_is_left_alone():
    src = 'a(); /* never closed' + NL + 'b();'
    assert strip(src) == src

@case
def router_keeps_routes_after_a_splat_path():
    app_tsx = NL.join([
        "import { Routes, Route } from 'react-router-dom';",
        "import Home from './Home';",
        "import Tree from './Tree';",
        "import Blob from './Blob';",
        "import Repo from './Repo';",
        "export default function App() {",
        "  return (",
        "    <Routes>",
        '      <Route path="/" element={<Home />} />',
        '      <Route path="/:owner/:repo/tree/:branch/*" element={<Tree />} />',
        '      <Route path="/:owner/:repo/blob/:branch/*" element={<Blob />} />',
        '      <Route path="/:owner/:repo" element={<Repo />} />',
        "      {/* settings */}",
        '      <Route path="/settings" element={<Home />} />',
        "    </Routes>",
        "  );",
        "}",
        "",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "frontend" / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(app_tsx, encoding="utf-8")
        for n in ("Home", "Tree", "Blob", "Repo"):
            (src / (n + ".tsx")).write_text("export default function " + n + "() { return <div>" + n + "</div>; }" + NL,
                                            encoding="utf-8")
        app = nav_edges.App(Path(tmp))
        paths = [r["path"] for r in app.routes]
        assert paths == ["/", "/:owner/:repo/tree/:branch/*", "/:owner/:repo/blob/:branch/*", "/:owner/:repo",
                         "/settings"], paths
        comps = {r["path"]: r["comp"] for r in app.routes}
        assert comps["/:owner/:repo"] == ("Repo.tsx", "Repo"), comps

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
    print(f"{len(CASES) - failed}/{len(CASES)} navstrip cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
