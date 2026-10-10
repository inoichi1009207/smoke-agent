from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import codegen
from agent.codegen import CodegenImplementer
from agent.testrun import TestOutcome

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def pad(head: str, size: int) -> str:
    text = head + NL
    line = "// filler line of the fixture" + NL
    while len(text) < size:
        text += line
    return text[:size]

def write(root: Path, files: dict[str, str]) -> None:
    for step, (rel, text) in enumerate(files.items()):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        stamp = 1.0e9 + step
        os.utime(target, (stamp, stamp))

def implementer(root: Path, contract: dict, members: list[str]) -> CodegenImplementer:
    from agent.contract import relevant_files

    text = json.dumps(contract)
    impl = CodegenImplementer(None, root, lambda: None, contract=text, relevant=relevant_files(text, members), log=lambda message: None)
    impl._history = []
    return impl

APP_JS = "const express = require('express');" + NL + "const app = express();" + NL \
    + "const sheets = require('./routes/sheets');" + NL + "app.use('/api/sheets', sheets);" + NL \
    + "const users = require('./routes/users');" + NL + "app.use('/api/users', users);" + NL + "module.exports = app;" + NL
SHEETS_HEAD = "const router = require('express').Router();" + NL + "const evaluate = require('../lib/evaluate');" + NL \
    + "router.get('/:id', (req, res) => res.json({}));" + NL + "module.exports = router;"
USERS_HEAD = "const router = require('express').Router();" + NL + "router.get('/:id', (req, res) => res.json({}));" + NL + "module.exports = router;"
EDITOR_CALLS = "import { get } from '../api';" + NL + "export function load(id: string) { return get(`/sheets/${id}`); }"
SPEC_API = "const { test, expect } = require('@playwright/test');" + NL \
    + "test('REQ-3-1-1: loads', async ({ request }) => { const r = await request.get('/api/sheets/1'); expect(r.ok()).toBeTruthy(); });"

@case
def generation_keeps_the_shared_frontend_ahead_of_the_batchs_backend():

    contract = {"pages": [{"route": "/sheet/:id", "file": "frontend/src/pages/EditorPage.tsx", "requirements": ["REQ-3-1-1"]}],
                "shared": [{"file": "frontend/src/components/SheetGrid.tsx"}],
                "api": [{"method": "GET", "path": "/api/sheets/:id", "requirements": ["REQ-3-1-1"]}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, {
            "frontend/src/App.tsx": "export default function App() { return null; }" + NL,
            "backend/src/app.js": APP_JS,
            "frontend/src/pages/EditorPage.tsx": pad(EDITOR_CALLS, 20000),
            "frontend/src/components/SheetGrid.tsx": pad("import GridCell from './GridCell';", 20000),
            "frontend/src/components/GridCell.tsx": pad("export default function GridCell() { return null; }", 20000),
            "backend/src/routes/sheets.js": pad(SHEETS_HEAD, 28000),
            "backend/src/lib/evaluate.js": pad("module.exports = function evaluate() {};", 5000),
            "backend/src/routes/users.js": pad(USERS_HEAD, 1000),
        })
        impl = implementer(root, contract, ["REQ-3-1-1"])
        spec = {"backend/test-e2e/REQ-3-1-1.spec.js": SPEC_API.replace("request.get('/api/sheets/1')", "page.goto('/sheet/1')")}
        impl.project_files([], repair=False, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]}, test_files=spec, output="")
        assert "backend/src/routes/sheets.js" in impl._tiers.get("own_backend", []), impl._tiers
        shown = impl._shown
        for rel in ("frontend/src/pages/EditorPage.tsx", "frontend/src/components/SheetGrid.tsx", "frontend/src/components/GridCell.tsx"):
            assert rel in shown, (rel, sorted(shown), sorted(impl._omitted))
        assert "backend/src/routes/sheets.js" in impl._omitted, sorted(shown)

@case
def generation_does_not_reorder_the_relevant_files_by_rank():

    contract = {"pages": [{"route": "/sheet/:id", "file": "frontend/src/pages/EditorPage.tsx", "requirements": ["REQ-3-1-1"]}],
                "shared": [{"file": "frontend/src/components/SheetGrid.tsx"}, {"file": "backend/src/routes/sheets.js"}],
                "api": [{"method": "GET", "path": "/api/sheets/:id", "requirements": ["REQ-3-1-1"]}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, {
            "frontend/src/App.tsx": "export default function App() { return null; }" + NL,
            "backend/src/app.js": APP_JS,
            "frontend/src/pages/EditorPage.tsx": pad(EDITOR_CALLS, 25000),
            "frontend/src/components/SheetGrid.tsx": pad("export default function SheetGrid() { return null; }", 25000),
            "backend/src/routes/sheets.js": pad(SHEETS_HEAD, 28000),
            "backend/src/lib/evaluate.js": pad("module.exports = function evaluate() {};", 20000),
            "backend/src/routes/users.js": pad(USERS_HEAD, 1000),
        })
        impl = implementer(root, contract, ["REQ-3-1-1"])
        impl.project_files([], repair=False, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]},
                           test_files={"backend/test-e2e/REQ-3-1-1.spec.js": SPEC_API}, output="")
        assert "backend/src/lib/evaluate.js" in impl._tiers.get("own_backend", []), impl._tiers
        assert "frontend/src/components/SheetGrid.tsx" in impl._shown, (sorted(impl._shown), sorted(impl._omitted))
        assert "backend/src/lib/evaluate.js" in impl._omitted, sorted(impl._shown)

def repair_project(root: Path) -> None:
    write(root, {
        "frontend/src/App.tsx": "export default function App() { return null; }" + NL,
        "backend/src/app.js": APP_JS,
        "frontend/src/pages/EditorPage.tsx": pad(EDITOR_CALLS, 3000),
        "frontend/src/pages/HomePage.tsx": pad("export default function HomePage() { return null; }", 3000),
        "frontend/src/pages/AboutPage.tsx": pad("export default function AboutPage() { return null; }", 3000),
        "frontend/src/pages/HelpPage.tsx": pad("export default function HelpPage() { return null; }", 3000),
    })

REPAIR_CONTRACT = {"pages": [{"route": "/sheet/:id", "file": "frontend/src/pages/EditorPage.tsx", "requirements": ["REQ-3-1-1"]},
                             {"route": "/start", "file": "frontend/src/pages/HomePage.tsx", "requirements": ["REQ-1-1-1"]},
                             {"route": "/about", "file": "frontend/src/pages/AboutPage.tsx", "requirements": ["REQ-9-1-1"]},
                             {"route": "/help", "file": "frontend/src/pages/HelpPage.tsx", "requirements": ["REQ-9-1-2"]}]}
REPAIR_SPECS = {"backend/test-e2e/REQ-3-1-1.spec.js": "test('REQ-3-1-1: back home', async ({ page }) => { await page.goto('/'); await page.goto('/start'); });",
                "backend/test-e2e/REQ-3-1-1b.spec.js": "test('REQ-3-1-1: help', async ({ page }) => { await page.goto('/help'); });"}
REPAIR_OUTPUT = "  1) backend/test-e2e/REQ-3-1-1.spec.js:1:1 > REQ-3-1-1: back home" + NL + "Error: expected heading"

@case
def repair_takes_the_failing_specs_page_into_the_background_allowance():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repair_project(root)
        impl = implementer(root, REPAIR_CONTRACT, ["REQ-3-1-1"])
        impl.project_files([], repair=True, focus_text=REPAIR_OUTPUT, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]},
                           test_files=REPAIR_SPECS, output=REPAIR_OUTPUT)

        assert impl._tiers.get("failing") == ["frontend/src/pages/HomePage.tsx"], impl._tiers
        assert "frontend/src/pages/HomePage.tsx" in impl._shown, (sorted(impl._shown), sorted(impl._omitted))

        assert "frontend/src/pages/HelpPage.tsx" in impl._omitted and "frontend/src/pages/AboutPage.tsx" in impl._omitted, sorted(impl._shown)
        assert "frontend/src/pages/EditorPage.tsx" in impl._shown

@case
def generation_keeps_background_under_the_old_small_allowance():

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repair_project(root)
        (root / "frontend/src/pages/EditorPage.tsx").write_text(pad(EDITOR_CALLS, 16000), encoding="utf-8")
        impl = implementer(root, REPAIR_CONTRACT, ["REQ-3-1-1"])
        impl.project_files([], repair=False, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]}, test_files=REPAIR_SPECS, output="")
        assert "frontend/src/pages/HomePage.tsx" in impl._tiers.get("failing", []), impl._tiers
        assert "frontend/src/pages/HomePage.tsx" in impl._omitted, sorted(impl._shown)

@case
def the_tiers_name_the_right_files_and_only_those():
    contract = {"pages": [{"route": "/sheet/:id", "file": "frontend/src/pages/EditorPage.tsx", "requirements": ["REQ-3-1-1"]},
                          {"route": "/users/:id", "file": "frontend/src/pages/UserPage.tsx", "requirements": ["REQ-2-1-1"]}],
                "api": [{"method": "GET", "path": "/api/sheets/:id", "requirements": ["REQ-3-1-1"]},
                        {"method": "GET", "path": "/api/users/:id", "requirements": ["REQ-2-1-1"]}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, {
            "frontend/src/App.tsx": "export default function App() { return null; }" + NL,
            "backend/src/app.js": APP_JS,
            "frontend/src/pages/EditorPage.tsx": pad(EDITOR_CALLS, 500),
            "frontend/src/pages/UserPage.tsx": pad("export default function UserPage() { return null; }", 500),
            "frontend/src/lib/format.ts": pad("export const f = 1;", 200),
            "frontend/src/lib/other.ts": pad("export const o = 1;", 200),
            "backend/src/routes/sheets.js": pad(SHEETS_HEAD, 500),
            "backend/src/lib/evaluate.js": pad("module.exports = function evaluate() {};", 200),
            "backend/src/routes/users.js": pad(USERS_HEAD, 500),
        })
        impl = implementer(root, contract, ["REQ-3-1-1"])
        impl._history = [("REQ-3-1-2", ["frontend/src/lib/format.ts"]),
                         ("REQ-2-1-1", ["frontend/src/lib/other.ts"])]
        spec = {"backend/test-e2e/REQ-3-1-1.spec.js": "test('REQ-3-1-1', async ({ page }) => { await page.goto('/users/7'); "
                                                      "await page.request.get('/api/sheets/1'); });"}
        impl.project_files([], repair=False, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]}, test_files=spec, output="")
        tiers = impl._tiers
        assert set(tiers["failing"]) == {"frontend/src/pages/UserPage.tsx", "backend/src/routes/sheets.js"}, tiers
        assert tiers["earlier"] == ["frontend/src/lib/format.ts"], tiers
        assert set(tiers["own_backend"]) == {"backend/src/routes/sheets.js", "backend/src/lib/evaluate.js"}, tiers

        assert not any(p in codegen.CORE_FILES or "test-e2e" in p for tier in tiers.values() for p in tier), tiers

@case
def the_bare_root_is_no_failing_page_signal():

    assert not codegen.route_matches("/about", "/") and codegen.route_matches("/", "/")
    assert codegen.route_matches("/users/:id", "/users/") and codegen.route_matches("/users/:id", "/users/7")
    assert not codegen.route_matches("/users/:id/edit", "/about/")
    contract = {"pages": [{"route": "/", "file": "frontend/src/pages/HomePage.tsx", "requirements": ["REQ-1-1-1"]},
                          {"route": "/about", "file": "frontend/src/pages/AboutPage.tsx", "requirements": ["REQ-9-1-1"]},
                          {"route": "/help", "file": "frontend/src/pages/HelpPage.tsx", "requirements": ["REQ-9-1-2"]}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repair_project(root)
        impl = implementer(root, contract, ["REQ-3-1-1"])
        output = "  1) backend/test-e2e/REQ-3-1-1.spec.js:1:1 > REQ-3-1-1: x" + NL + "  navigated to http://localhost:3000/"
        spec = {"backend/test-e2e/REQ-3-1-1.spec.js": "test('REQ-3-1-1: x', async ({ page }) => { await page.goto('/'); });"}
        impl.project_files([], repair=True, focus_text=output, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]}, test_files=spec, output=output)
        assert impl._tiers.get("failing") == [], impl._tiers

        spec = {"backend/test-e2e/REQ-3-1-1.spec.js": "test('REQ-3-1-1: x', async ({ page }) => { await page.goto('/'); await page.goto('/help'); });"}
        impl.project_files([], repair=True, focus_text=output, node={"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]}, test_files=spec, output=output)
        assert impl._tiers.get("failing") == ["frontend/src/pages/HelpPage.tsx"], impl._tiers

class AskingModel:
    last_finish_reason, model = "stop", "glm-test"

    def __init__(self, wanted: list[str]):
        self.wanted = list(wanted)
        self.tags: list[str] = []

    def chat(self, messages, **options):
        self.tags.append(options.get("tag", ""))
        if self.wanted:
            return {"content": "=== NEED FILES ===" + NL + self.wanted.pop(0) + NL + "=== END NEED FILES ==="}
        return {"content": "=== FILE: frontend/src/pages/EditorPage.tsx ===" + NL + "export default 1;" + NL + "=== END FILE ==="}

def asking_project(root: Path) -> None:
    files = {"frontend/src/App.tsx": "export default function App() { return null; }" + NL,
             "frontend/src/pages/EditorPage.tsx": "export default 0;" + NL}
    for n in range(3):
        files[f"backend/src/routes/big{n}.js"] = pad("module.exports = {};", codegen.MAX_FILE_CHARS + 2000)
    write(root, files)

@case
def a_generation_answer_that_only_asks_for_files_does_not_spend_the_round():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asking_project(root)
        model = AskingModel(["backend/src/routes/big0.js"])
        impl = CodegenImplementer(model, root, lambda: TestOutcome(True, "E2E PASSED"), max_rounds=0, extra_rounds=0, log=lambda message: None)
        result = impl.implement({"id": "REQ-3-1-1", "name": "n"}, {}, TestOutcome(False, "red"))
        assert result.status == "passed", (result.status, model.tags)
        assert [t for t in model.tags if t.startswith("codegen:")] == ["codegen:REQ-3-1-1:gen0", "codegen:REQ-3-1-1:gen0"], model.tags
        assert "backend/src/routes/big0.js" in impl._shown

@case
def free_generation_asks_are_bounded():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asking_project(root)
        model = AskingModel([f"backend/src/routes/big{n}.js" for n in range(codegen.GEN_ASK_FREE + 1)])
        impl = CodegenImplementer(model, root, lambda: TestOutcome(True, "E2E PASSED"), max_rounds=0, extra_rounds=0, log=lambda message: None)
        result = impl.implement({"id": "REQ-3-1-1", "name": "n"}, {}, TestOutcome(False, "red"))
        gen = [t for t in model.tags if t.startswith("codegen:")]

        assert len(gen) == codegen.GEN_ASK_FREE + 1, gen
        assert result.status != "passed", result.status

@case
def a_repair_answer_that_only_asks_for_files_still_spends_its_round():
    class Model(AskingModel):
        def __init__(self):
            super().__init__([])
            self.writes = 0

        def chat(self, messages, **options):
            tag = options.get("tag", "")
            if ":diagnose" in tag:
                return {"content": "frontend/src/pages/EditorPage.tsx -- render the sheet"}
            self.tags.append(tag)
            if tag.endswith("repair1") and not self.wanted and self.writes == 1:
                self.wanted.append("used")
                return {"content": "=== NEED FILES ===" + NL + "backend/src/routes/big0.js" + NL + "=== END NEED FILES ==="}
            self.writes += 1
            return {"content": "=== FILE: frontend/src/pages/EditorPage.tsx ===" + NL + f"export default {self.writes};" + NL + "=== END FILE ==="}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asking_project(root)
        model = Model()
        outcomes = iter([TestOutcome(False, "E2E FAILED" + NL + "  1) backend/test-e2e/a.spec.js:3 > REQ-3-1-1: x"), TestOutcome(True, "E2E PASSED")])
        impl = CodegenImplementer(model, root, lambda: next(outcomes), max_rounds=1, extra_rounds=0, log=lambda message: None)
        result = impl.implement({"id": "REQ-3-1-1", "name": "n"}, {}, TestOutcome(False, "red"))
        writing = [t for t in model.tags if t.startswith("codegen:")]

        assert writing == ["codegen:REQ-3-1-1:gen0", "codegen:REQ-3-1-1:repair1"], writing
        assert result.status != "passed", result.status

@case
def without_a_node_the_old_selection_stands():

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repair_project(root)
        impl = implementer(root, REPAIR_CONTRACT, ["REQ-3-1-1"])
        saved = codegen.REPAIR_BACKGROUND_CHARS
        codegen.REPAIR_BACKGROUND_CHARS = 0
        try:
            impl.project_files([], repair=True, focus_text=REPAIR_OUTPUT)
        finally:
            codegen.REPAIR_BACKGROUND_CHARS = saved
        assert impl._tiers == {}
        assert impl._omitted == {"frontend/src/pages/HomePage.tsx", "frontend/src/pages/AboutPage.tsx", "frontend/src/pages/HelpPage.tsx",
                                 "backend/src/app.js"} - {p for p in impl._shown}, (sorted(impl._shown), sorted(impl._omitted))

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
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} context142 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
