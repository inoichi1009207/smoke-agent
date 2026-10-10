from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import contract as contract_module
from agent.contract import build_contract, contract_slice, dedupe_alternatives, relevant_files
from agent.json_tolerant import tolerant_loads

CASES: list = []
FIXTURE = ROOT / "selftests" / "fixtures" / "contract142-github-v141-invalid.json"
KEYS = contract_module.CONTRACT_KEYS

def case(function):
    CASES.append(function)
    return function

class _LLM:

    def __init__(self, *answers: str):
        self.answers, self.tags, self.model = list(answers), [], "glm-test"

    def chat(self, messages, **kwargs):
        self.tags.append(kwargs.get("tag"))
        return {"content": self.answers.pop(0) if self.answers else ""}

def _tree(leaves: int = 3) -> dict:
    children = []
    for index in range(1, leaves + 1):
        children.append({"id": f"REQ-{index}", "name": f"Leaf {index}", "description": "", "scenarios": []})
    children[0]["description"] = "Visitors open the sign-in page at `/login` and see the Sign in button."
    children[1]["description"] = "After saving, the issue detail page shows the new title."
    return {"id": "ROOT", "name": "Root", "children": children}

def _project(tmp: str) -> Path:
    root = Path(tmp)
    for rel in ("frontend/src/App.tsx", "frontend/src/api/index.ts", "frontend/src/pages/HomePage.tsx",
                "backend/src/app.js", "backend/src/database/seed_db.js", "backend/src/database/init_db.js"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("// template\n", encoding="utf-8")
    return root

@case
def the_real_v141_contract_is_read_completely():
    text = FIXTURE.read_text(encoding="utf-8")
    try:
        json.loads(text)
        raise AssertionError("the fixture must stay the invalid contract")
    except json.JSONDecodeError as error:
        assert "Invalid control character" in error.msg
    value, notes = tolerant_loads(text, KEYS)
    assert isinstance(value, dict), notes
    assert (len(value["pages"]), len(value["api"]), len(value["shared"])) == (24, 39, 10), notes
    assert [n for n in notes if n.startswith("closed an unterminated string")] == notes and len(notes) == 5, notes
    reviews = [a for a in value["api"] if a["path"].endswith("/pulls/:number/reviews")][0]
    assert reviews["response"] == {"status": 201, "body": "{review:{reviewer,state,summary}}"}, reviews

    after = [a for a in value["api"] if a["path"].endswith("/pulls/:number/reviewers")][0]
    assert after["method"] == "POST/DELETE" and after["requirements"] == ["REQ-6-4"], after

@case
def relevant_files_live_on_the_real_invalid_contract():
    text = FIXTURE.read_text(encoding="utf-8")
    files = relevant_files(text, ["REQ-6-3-4"])
    assert "frontend/src/api/index.ts" in files, files
    own = [f for f in files if f.startswith("frontend/src/pages/")]
    assert own, files
    assert len(files) == len(set(files))

@case
def the_slice_works_on_the_real_invalid_contract_and_keeps_an_appended_note():
    text = FIXTURE.read_text(encoding="utf-8")
    sliced = contract_slice(text, ["REQ-6-3-4"])
    assert len(sliced) < len(text)
    json.loads(sliced)
    appended = text + "\n\nSeed constraints: keep the union."
    assert contract_slice(appended, ["REQ-6-3-4"]) == appended

@case
def build_contract_repairs_the_real_answer_by_script_without_a_repair_call():
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        llm = _LLM(FIXTURE.read_text(encoding="utf-8"), "not json")
        text = build_contract(llm, _tree(), project, log=lines.append)
        data = json.loads(text)
        assert len(data["api"]) == 39 and len(data["pages"]) == 24
        assert "architect:contract-json" not in llm.tags, llm.tags
        written = json.loads((project / ".arc" / "design" / "contract.json").read_text(encoding="utf-8"))
        assert len(written["pages"]) == 24
    assert any("repaired by script without loss" in line for line in lines), lines

@case
def a_truncated_answer_keeps_its_complete_entries_when_the_repair_call_fails():
    pages = [{"route": f"/p{i}", "file": f"frontend/src/pages/P{i}Page.tsx", "requirements": [f"REQ-{i}"]} for i in range(1, 4)]
    full = json.dumps({"pages": pages, "api": [{"method": "GET", "path": "/api/x", "requirements": ["REQ-1"]}] * 3,
                       "shared": [{"file": "frontend/src/api/index.ts"}]}, indent=1)
    truncated = full[: full.index('"shared"') - 30]
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        llm = _LLM(truncated, "still {broken", "not json")
        text = build_contract(llm, _tree(), project, log=lines.append)
    data = json.loads(text)
    assert "architect:contract-json" in llm.tags
    assert [p["route"] for p in data["pages"]] == ["/p1", "/p2", "/p3"]
    assert 1 <= len(data["api"]) < 3 and all(a == {"method": "GET", "path": "/api/x", "requirements": ["REQ-1"]} for a in data["api"])
    shared = [s["file"] for s in data["shared"]]
    assert "frontend/src/api/index.ts" in shared and "backend/src/database/seed_db.js" not in shared, shared
    assert any("recovered by the tolerant parser" in line for line in lines), lines
    assert any("FALLBACK" in line and "shared" in line for line in lines), lines

@case
def an_unreadable_answer_becomes_the_logged_fallback_contract():
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        llm = _LLM("I cannot produce the contract.", "Sorry.", "not json")
        text = build_contract(llm, _tree(), project, log=lines.append)
    data = json.loads(text)
    by_file = {p["file"]: p for p in data["pages"]}
    assert by_file["frontend/src/pages/LoginPage.tsx"]["route"] == "/login"
    assert by_file["frontend/src/pages/LoginPage.tsx"]["requirements"] == ["REQ-1"]
    assert by_file["frontend/src/pages/IssueDetailPage.tsx"]["requirements"] == ["REQ-2"]
    assert "frontend/src/pages/HomePage.tsx" in by_file
    assert data["architect_draft"] == "I cannot produce the contract."
    assert any("FALLBACK contract in use" in line for line in lines), lines
    assert relevant_files(text, ["REQ-1"])[:1] == ["frontend/src/pages/LoginPage.tsx"]
    assert "frontend/src/api/index.ts" in relevant_files(text, ["REQ-3"])

@case
def the_fallback_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        first = contract_module.fallback_contract(_tree(), project)
        assert first == contract_module.fallback_contract(_tree(), project)

@case
def a_valid_answer_is_untouched():
    valid = json.dumps({"pages": [{"route": "/", "file": "frontend/src/pages/HomePage.tsx", "requirements": ["REQ-1"]}], "shared": []})
    value, notes = tolerant_loads(valid)
    assert notes == [] and value == json.loads(valid)
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        llm = _LLM(valid, "not json")
        text = build_contract(llm, _tree(), _project(tmp), log=lines.append)
    assert json.loads(text) == json.loads(valid)
    assert not any("FALLBACK" in line or "tolerant" in line or "script" in line for line in lines), lines
    assert dedupe_alternatives('{"pages":[]}', "") == '{"pages":[]}'

def _reads(text: str, expected) -> list[str]:
    value, notes = tolerant_loads(text)
    assert value == expected, (value, notes)
    return notes

@case
def the_historical_defect_shapes_are_repaired_without_loss():

    notes = _reads('{"api": [{"a": 1, "r": {"body": "{w:{id}}}"},\n {"a": 2}]}', {"api": [{"a": 1, "r": {"body": "{w:{id}}"}}, {"a": 2}]})
    assert notes[0].startswith("moved a closing quote"), notes

    notes = _reads('{"api": [{"r": {"body": "{fav": "boolean"}}, "errors": []}, {"a": 2}]}',
                   {"api": [{"r": {"body": {"fav": "boolean"}}, "errors": []}, {"a": 2}]})
    assert notes[0].startswith("swapped an opening quote"), notes

    notes = _reads('{"api": [{"a": 1}, {"a": 2, "r": {"s": 1}\n  ],\n  "shared": []}', {"api": [{"a": 1}, {"a": 2, "r": {"s": 1}}], "shared": []})
    assert notes[0].startswith("inserted a missing '}'"), notes

    notes = _reads('{"api": [{"body": {"user": {"id": "string"} | null}}, {"a": 2}]}', {"api": [{"body": {"user": {"id": "string"}}}, {"a": 2}]})
    assert notes[0].startswith("dropped a non-JSON token"), notes

    notes = _reads('{"api": [{"r": {"body": "{x:{y}}}},\n {"a": 2}]}', {"api": [{"r": {"body": "{x:{y}}"}}, {"a": 2}]})
    assert notes[0].startswith("closed an unterminated string"), notes

@case
def the_generic_defects_are_repaired():
    assert tolerant_loads('```json\n{"a": [1, 2,], "b": {"c": 1,},}\n```')[0] == {"a": [1, 2], "b": {"c": 1}}
    assert tolerant_loads('{"a": 1, // the id\n "b": /* note */ 2}')[0] == {"a": 1, "b": 2}
    assert tolerant_loads('{"url": "http://x/y", "b": "a // b"}')[0] == {"url": "http://x/y", "b": "a // b"}
    assert tolerant_loads('{"note": "line one\nline two", "b": 1}')[0] == {"note": "line one\nline two", "b": 1}
    assert tolerant_loads('Here is the contract:\n{"a": 1}\nDone.')[0] == {"a": 1}
    assert tolerant_loads("{a: 1, 'b': 2}")[0] == {"a": 1, "b": 2}

@case
def a_truncated_tail_keeps_the_complete_top_level_entries():
    text = '{\n  "pages": [{"route": "/a", "file": "A.tsx"}, {"route": "/b", "file": "B.tsx"}],\n  "api": [{"method": "GET", "path": "/api/a"}, {"method": "POST", "pa'
    value, notes = tolerant_loads(text, ("pages", "api"))
    assert value == {"pages": [{"route": "/a", "file": "A.tsx"}, {"route": "/b", "file": "B.tsx"}], "api": [{"method": "GET", "path": "/api/a"}]}, (value, notes)
    assert any(n.startswith("cut after") for n in notes)
    assert not contract_module._lossless(notes)

@case
def keys_after_an_unrecoverable_defect_are_read_one_by_one():
    text = ('{\n  "pages": [{"route": "/a", "file": "A.tsx", "requirements": ["REQ-1"]}],\n  "api": [{"method": @@@ }],\n'
            '  "shared": [{"file": "frontend/src/api/index.ts"}],\n  "notes": ["n"]\n}')
    value, _notes = tolerant_loads(text, KEYS)
    assert value["pages"] == [{"route": "/a", "file": "A.tsx", "requirements": ["REQ-1"]}]
    assert value["shared"] == [{"file": "frontend/src/api/index.ts"}] and value["notes"] == ["n"], value
    assert relevant_files(text, ["REQ-1"]) == ["A.tsx", "frontend/src/api/index.ts"]

@case
def nothing_readable_gives_none_not_an_invented_value():
    assert tolerant_loads("")[0] is None
    assert tolerant_loads("no json here at all")[0] is None
    assert relevant_files("no json here at all", ["REQ-1"]) == []

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
    print(f"{len(CASES) - failed}/{len(CASES)} contract142 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
