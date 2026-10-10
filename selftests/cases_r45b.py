from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import contract as contract_module
from agent.contract import build_contract, relevant_files
from agent.json_tolerant import tolerant_loads

CASES: list = []
KEYS = contract_module.CONTRACT_KEYS
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

class _LLM:

    def __init__(self, *answers: str):
        self.answers, self.tags, self.model = list(answers), [], "glm-test"

    def chat(self, messages, **kwargs):
        self.tags.append(kwargs.get("tag"))
        return {"content": self.answers.pop(0) if self.answers else ""}

def _tree() -> dict:
    return {"id": "ROOT", "name": "Root", "children": [
        {"id": "REQ-1", "name": "Leaf 1", "description": "Visitors open the page at `/a`.", "scenarios": []}]}

def _project(tmp: str) -> Path:
    root = Path(tmp)
    for rel in ("frontend/src/pages/HomePage.tsx", "frontend/src/api/index.ts", "backend/src/app.js"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("// x" + NL, encoding="utf-8")
    return root

EARLY = '{"pages":[{"route":"/a","file":"A.tsx"}],"shared":[]},"api":[{"path":"/api/a"}]}'

@case
def n2_an_early_closed_document_is_not_a_lossless_reading():
    value, notes = tolerant_loads(EARLY, KEYS)
    assert value == {"pages": [{"route": "/a", "file": "A.tsx"}], "shared": []}, value
    assert notes and not contract_module._lossless(notes), notes

    lines = '{\n  "pages": [{"route": "/a", "file": "A.tsx"}],\n  "shared": []\n},\n  "api": [{"path": "/api/a"}]\n}'
    value, notes = tolerant_loads(lines, KEYS)
    assert "api" not in value and not contract_module._lossless(notes), (value, notes)

@case
def n2_build_contract_asks_the_repair_call_for_an_early_closed_answer():
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        llm = _LLM(EARLY, EARLY.replace('[]},"api"', '[],"api"'), "not json")
        text = build_contract(llm, _tree(), _project(tmp), log=lines.append)
    assert "architect:contract-json" in llm.tags, llm.tags
    assert json.loads(text)["api"] == [{"path": "/api/a"}], text
    assert not any("repaired by script without loss" in line for line in lines), lines

@case
def n2_prose_after_the_document_stays_lossless_json_after_it_does_not():
    value, notes = tolerant_loads('Here is the contract:\n{"a": 1}\nDone.')
    assert value == {"a": 1} and notes == ["ignored prose after the document"], (value, notes)
    assert contract_module._lossless(notes)
    value, notes = tolerant_loads('{"a": 1}\nNote: "b" is 2.')
    assert value == {"a": 1} and not contract_module._lossless(notes), (value, notes)
    value, notes = tolerant_loads('{"a": 1}, "b": 2}')
    assert value == {"a": 1} and not contract_module._lossless(notes), (value, notes)
    assert tolerant_loads('{"a": 1}\n') == ({"a": 1}, [])

NESTED = ('{\n "pages":[{"route":"/a","file":"A.tsx","requirements":["REQ-1"]}],\n "api":[{"response":{\n'
          ' "shared":[{"file":"WRONG.tsx"}],\n "body": @@@\n }}],\n "shared":[{"file":"RIGHT.tsx"}]\n}')

@case
def n6_a_nested_key_is_never_promoted_over_the_top_level_one():
    value, notes = tolerant_loads(NESTED, KEYS)
    assert value["shared"] == [{"file": "RIGHT.tsx"}], (value, notes)
    assert not contract_module._lossless(notes), notes
    files = relevant_files(NESTED, ["REQ-1"])
    assert "RIGHT.tsx" in files and "WRONG.tsx" not in files, files

@case
def n6_nothing_after_an_unsure_depth_counts_as_top_level():

    broken = ('{\n "pages":[{"route":"/a","file":"A.tsx","requirements":["REQ-1"]}],\n "api":[{"response":{"body": "{x\n'
              ' "shared":[{"file":"WRONG.tsx"}] }}]\n}')
    value, notes = tolerant_loads(broken, KEYS)
    assert value is None or "WRONG.tsx" not in json.dumps(value.get("shared")), (value, notes)
    assert value is None or not contract_module._lossless(notes), notes

    assert value is not None and value.get("pages") == [{"route": "/a", "file": "A.tsx", "requirements": ["REQ-1"]}], value

def _repair_files(root: Path, second_route: bool):
    from cases_context142 import implementer, pad, write

    app = ("const express = require('express');" + NL + "const app = express();" + NL
           + "const orders = require('./routes/orders');" + NL + "app.use('/api/orders', orders);" + NL
           + "const items = require('./routes/items');" + NL + "app.use('/api/items', items);" + NL + "module.exports = app;" + NL)
    route = "const router = require('express').Router();" + NL + "router.get('/:id', (req, res) => res.status(500).json({}));" + NL \
        + "module.exports = router;"
    write(root, {"frontend/src/App.tsx": "export default function App() { return null; }" + NL,
                 "backend/src/app.js": app,
                 "frontend/src/pages/Orders.tsx": "export default function Orders() { return null; }" + NL,
                 "backend/src/routes/items.js": pad(route, 6000),
                 "backend/src/routes/orders.js": pad(route, 6169)})
    contract = {"pages": [{"route": "/orders", "file": "frontend/src/pages/Orders.tsx", "requirements": ["REQ-1"]}]}
    calls = "await request.get('/api/orders/1');" + (" await request.get('/api/items/1');" if second_route else "")
    specs = {"backend/test-e2e/REQ-1.spec.js": "test('REQ-1: order', async ({ request }) => { " + calls + " });"}
    output = ("  1) backend/test-e2e/REQ-1.spec.js:1:1 > REQ-1: order" + NL + "Error: GET http://localhost:3000/api/orders/1 -> 500"
              + (NL + "GET http://localhost:3000/api/items/1 -> 500" if second_route else ""))
    impl = implementer(root, contract, ["REQ-1"])
    impl.project_files([], repair=True, focus_text=output, node={"id": "REQ-1", "members": ["REQ-1"]}, test_files=specs, output=output)
    return impl

@case
def n7_the_failing_endpoints_route_file_is_shown_in_a_repair_round():
    with tempfile.TemporaryDirectory() as tmp:
        impl = _repair_files(Path(tmp), second_route=False)
        assert impl._tiers.get("failing") == ["backend/src/routes/orders.js"], impl._tiers
        assert "backend/src/routes/orders.js" in impl._shown, (sorted(impl._shown), sorted(impl._omitted))
        assert "backend/src/routes/items.js" in impl._omitted

@case
def n7_only_one_failing_file_passes_the_allowance():
    with tempfile.TemporaryDirectory() as tmp:
        impl = _repair_files(Path(tmp), second_route=True)
        failing = impl._tiers.get("failing") or []
        assert set(failing) == {"backend/src/routes/orders.js", "backend/src/routes/items.js"}, impl._tiers
        shown = [p for p in failing if p in impl._shown]
        assert len(shown) == 1 and shown[0] == failing[0], (failing, sorted(impl._shown))
        assert failing[1] in impl._omitted, sorted(impl._omitted)

@case
def n7_generation_rounds_are_unchanged():
    from cases_context142 import implementer

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        impl = _repair_files(root, second_route=False)
        (root / "frontend/src/pages/Orders.tsx").write_text("// x" * 4000, encoding="utf-8")
        impl = implementer(root, {"pages": [{"route": "/orders", "file": "frontend/src/pages/Orders.tsx", "requirements": ["REQ-1"]}]}, ["REQ-1"])
        specs = {"backend/test-e2e/REQ-1.spec.js": "test('REQ-1: order', async ({ request }) => { await request.get('/api/orders/1'); });"}
        impl.project_files([], repair=False, node={"id": "REQ-1", "members": ["REQ-1"]}, test_files=specs, output="")
        assert "backend/src/routes/orders.js" in impl._tiers.get("failing", []), impl._tiers
        assert "backend/src/routes/orders.js" in impl._omitted, sorted(impl._shown)

def _subtract(typed_twice: bool, asserted: str) -> list[str]:
    from cases_gate142 import HEAD, REL, batch, leaf, requirement
    from agent.testrun import unsupported_assertions

    text = requirement(leaf("REQ-3-1-1", "The calculator shows the first operand minus the second operand.",
                            ["Both operands are 5.", "The user enters the operands and presses Subtract.", "The result shows."]))
    fills = '  await fill(page, "First operand", "5");\n' + ('  await fill(page, "Second operand", "5");\n' if typed_twice else "")
    spec = HEAD + ('test("REQ-3-1-1: subtract", async ({ page }) => {\n' + fills
                   + '  await click(page, "Subtract");\n' + f'  await see(page, "{asserted}");\n' + '});\n')
    return unsupported_assertions(REL, spec, batch(text, "REQ-3-1-1"))

@case
def n8_equal_operands_keep_their_multiplicity():
    assert _subtract(True, "0") == [], _subtract(True, "0")
    assert _subtract(True, "10") == []
    assert _subtract(True, "25") == []

@case
def n8_one_number_is_not_counted_twice():

    problems = _subtract(False, "0")
    assert len(problems) == 1 and "a number the requirement never states" in problems[0], problems
    assert len(_subtract(True, "7")) == 1

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
    print(f"{len(CASES) - failed}/{len(CASES)} r45b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
