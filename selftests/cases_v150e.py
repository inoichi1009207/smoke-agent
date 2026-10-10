from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.router_links import router_link_findings

def _app(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="v150e-"))
    for rel, text in files.items():
        p = root / "frontend" / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root

def href_link_is_reported():
    root = _app({"pages/Editor.tsx": "import { Link } from 'react-router-dom';\nexport default function E() {\n"
                 "  return (<div><Link href=\"/\" className=\"x\">\n    Home\n  </Link></div>);\n}\n"})
    try:
        f = router_link_findings(root, log=lambda m: None)
        assert len(f) == 1, f
        assert "pages/Editor.tsx:3" in f[0]["where"] and "Home" in f[0]["problem"] and "href" in f[0]["problem"], f
    finally:
        shutil.rmtree(root, ignore_errors=True)

def correct_and_foreign_links_are_quiet():
    root = _app({
        "a.tsx": "import { Link, NavLink as N } from 'react-router';\nconst A = () => (<><Link to=\"/\">Home</Link>"
                 "<N to={p} end>Teams</N><Link {...rest}>X</Link><a href=\"/\">Plain</a></>);\n",
        "b.tsx": "import { Link } from './ui/Link';\nconst B = () => <Link href=\"/\">Home</Link>;\n",
        "c.tsx": "import { Link } from 'react-router-dom';\nconst C = () => <Link\n  to={`/w/${id}`}\n  title=\"x\">Open</Link>;\n",
    })
    try:
        assert router_link_findings(root, log=lambda m: None) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)

def to_inside_a_value_does_not_count():
    root = _app({"d.tsx": "import { Link } from 'react-router-dom';\nconst D = () => <Link title=\"go to home\" "
                 "aria-label=\"to=x\">Home</Link>;\n"})
    try:
        f = router_link_findings(root, log=lambda m: None)
        assert len(f) == 1, f
    finally:
        shutil.rmtree(root, ignore_errors=True)

CASES = [href_link_is_reported, correct_and_foreign_links_are_quiet, to_inside_a_value_does_not_count]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v150e cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
