from __future__ import annotations

import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

NL = chr(10)
CASES = []
MISSING = object()

def case(function):
    CASES.append(function)
    return function

class Settings:

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, MISSING) for key in self.values}
        SETTINGS.update(self.values)

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

SPEC_FILE = "backend/test-e2e/REQ-9.spec.js"
SPECS = {SPEC_FILE: "test('REQ-9: totals are shown', async ({ page }) => {" + NL + "  await expect(page.getByText('Total 3')).toBeVisible();" + NL + "});" + NL}
NODE = {"id": "REQ-9", "name": "Totals", "description": "The totals page shows the total of all items."}
BIG = NL.join(f"// big helper line {i:05d} keeps the arithmetic of the totals" for i in range(700)) + NL

def pad(n: int) -> str:
    return NL.join(f"// pad{n} line {i:05d} of a helper module the totals page imports" for i in range(420)) + NL

FILES = {
    "frontend/src/App.jsx": "export default function App() {" + NL + "  return null;" + NL + "}" + NL,
    "backend/src/routes/items.js": "module.exports = function items(router) {" + NL + "  return router;" + NL + "};" + NL,
    "backend/src/lib/small.js": "module.exports = { add: (a, b) => a + b };" + NL,
    **{f"backend/src/lib/pad{n}.js": pad(n) for n in range(1, 5)},
}
NOTE = re.compile(r"^--- (\S+) --- \(exists, content not shown", re.M)
CONTEXT = "Context note from the caller: the totals page is new in this batch."

def check(text: str) -> str:
    return NL.join(["E2E FAILED against the real application", "FAILED SPEC FILES: REQ-9.spec.js",
                    "  1) test-e2e/REQ-9.spec.js:2:1 > REQ-9: totals are shown", f"    Error: {text}"])

CHECKS = [check("expected Total 3, got Total 0"), check("expected Total 3, got nothing"), check("expected Total 3, got nothing")]

def scenario(on, mutate=None, diagnosis=True, spy=None, big=False):
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    named: list[str] = []

    class Model:
        def __init__(self):
            self.calls, self.last_finish_reason, self.model = [], "stop", "glm-test"

        def chat(self, messages, **opts):
            tag = opts.get("tag", "")
            self.calls.append((tag, messages[-1]["content"]))
            if ":diagnose" in tag:
                if mutate:
                    mutate(tag)
                if not named:
                    named.extend(NOTE.findall(messages[-1]["content"])[:1])
                plan = [f"{rel} -- export the total helper the route needs" for rel in named]
                plan += ["backend/src/lib/small.js -- add the missing sum over all items"]
                plan += ["backend/src/lib/big.js -- keep the arithmetic in one place"] if big else []
                return {"content": NL.join(plan)}
            return {"content": NL.join(["=== FILE: backend/src/lib/formula.js ===", f"module.exports = {len(self.calls)};",
                                        "=== END FILE ==="])}

    lines = []
    outcomes = iter([TestOutcome(False, text) for text in CHECKS] + [TestOutcome(False, CHECKS[-1])] * 4)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index, (rel, text) in enumerate(list(FILES.items()) + ([("backend/src/lib/big.js", BIG)] if big else [])):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            os.utime(path, (1_700_000_000 + index * 60, 1_700_000_000 + index * 60))
        if mutate:
            mutate.root = root
        model = Model()
        implementer = CodegenImplementer(model, root, lambda: next(outcomes), max_rounds=2, extra_rounds=0, log=lines.append)
        if spy is not None:
            original = implementer._shared_writer_prompt

            def spied(*args, **kwargs):
                spy.append(args)
                return original(*args, **kwargs)

            implementer._shared_writer_prompt = spied
        with Settings(repair_prefix_shared=on, repair_diagnosis=diagnosis):
            implementer.implement(NODE, dict(SPECS), TestOutcome(False, "RED as expected"), extra_context=CONTEXT)
    return model.calls, lines, implementer

def prompts(calls, kind: str) -> dict[str, str]:
    out = {}
    for tag, user in calls:
        name = tag.rsplit(":", 1)[-1]
        if name.startswith(kind) and name[len(kind):].isdigit():
            out[name[len(kind):]] = user
    return out

def body_of(diagnosis_prompt: str) -> str:
    from agent.codegen import USER_TEXT

    return diagnosis_prompt[:diagnosis_prompt.rindex(NL + NL + USER_TEXT["ask_diagnose"])]

@case
def off_leaves_the_prompts_as_they_were():
    from agent.codegen import USER_TEXT

    spy = []
    calls, lines, implementer = scenario(False, spy=spy)
    assert not spy, "the shared builder was asked with the switch off"
    assert implementer._diag_prefix is None
    assert not any("repair prefix" in line for line in lines), [line for line in lines if "repair prefix" in line]
    writers = prompts(calls, "repair")
    assert set(writers) == {"1", "2"} and set(prompts(calls, "diagnose")) == {"1", "2"}, [tag for tag, _ in calls]
    assert not any(USER_TEXT["prefix_added"] in user for user in writers.values())

    named = NOTE.findall(prompts(calls, "diagnose")["1"])
    assert named and "Fix plan (your own diagnosis" in writers["1"] and FILES[named[0]].strip() in writers["1"], named

@case
def on_the_writer_starts_with_the_diagnosis_body():
    from agent.codegen import USER_TEXT

    calls, lines, _ = scenario(True)
    diagnoses, writers = prompts(calls, "diagnose"), prompts(calls, "repair")
    assert set(writers) == {"1", "2"}, [tag for tag, _ in calls]
    for number, writer in writers.items():
        body = body_of(diagnoses[number])
        assert writer.startswith(body + NL + NL), number
        assert USER_TEXT["ask_diagnose"] not in writer[len(body):], number
    shared = [line for line in lines if "repair prefix shared" in line]
    assert len(shared) == 2, lines

    named = NOTE.findall(diagnoses["1"])[0]
    assert "files_added=1" in shared[0], shared
    assert USER_TEXT["prefix_added"] in writers["1"] and writers["1"].index(FILES[named].strip()) > len(body_of(diagnoses["1"]))
    assert writers["1"].count(FILES["backend/src/lib/small.js"].strip()) == 1
    assert writers["1"].index("add the missing sum over all items") > len(body_of(diagnoses["1"]))

    assert "files_added=0" in shared[1], shared

def line_set(text: str) -> set[str]:
    return {line.rstrip() for line in text.splitlines() if line.strip()}

@case
def on_the_writer_sees_what_off_sees():
    from agent.codegen import USER_TEXT

    off_calls, _, _ = scenario(False)
    on_calls, _, _ = scenario(True)
    off, on = prompts(off_calls, "repair"), prompts(on_calls, "repair")

    assert prompts(off_calls, "diagnose") == prompts(on_calls, "diagnose")
    named = NOTE.findall(prompts(on_calls, "diagnose")["1"])[0]
    superseded = {line for line in line_set(on["1"]) if NOTE.match(line) and NOTE.match(line).group(1) == named}
    assert superseded, named
    allowed = line_set(USER_TEXT["prefix_added"]) | superseded
    for number in ("1", "2"):
        missing = line_set(off[number]) - line_set(on[number])
        assert not missing, (number, sorted(missing)[:5])
        extra = line_set(on[number]) - line_set(off[number]) - allowed
        assert not extra, (number, sorted(extra)[:5])
        plan = [line for line in line_set(prompts(on_calls, "diagnose")[number]) if " -- " in line and line.startswith("backend/")]
        for text in list(FILES.values()) + [SPECS[SPEC_FILE], CONTEXT] + plan:
            assert (text.strip() in off[number]) == (text.strip() in on[number]), (number, text[:60])
        assert FILES[named].strip() in on[number] and CONTEXT in on[number]
        assert "add the missing sum over all items" in on[number]

    assert len(on["1"]) - len(off["1"]) < 600, (len(on["1"]), len(off["1"]))

@case
def a_truncated_file_shown_whole_builds_fresh():

    on_calls, lines, _ = scenario(True, big=True)
    off_calls, _, _ = scenario(False, big=True)
    refused = [line for line in lines if "repair prefix not shared" in line]
    assert len(refused) == 1 and "big.js was shown truncated" in refused[0], lines
    assert prompts(on_calls, "repair")["1"] == prompts(off_calls, "repair")["1"]
    assert BIG.strip() in prompts(on_calls, "repair")["1"]
    shared = [line for line in lines if "repair prefix shared" in line]
    assert len(shared) == 1, lines

@case
def a_file_changed_between_the_calls_builds_fresh():
    def mutate(tag):
        if tag.endswith(":diagnose1"):
            (mutate.root / "backend/src/lib/small.js").write_text("module.exports = { add: (a, b) => b + a };" + NL, encoding="utf-8")

    on_calls, lines, _ = scenario(True, mutate=mutate)

    def mutate_off(tag):
        if tag.endswith(":diagnose1"):
            (mutate_off.root / "backend/src/lib/small.js").write_text("module.exports = { add: (a, b) => b + a };" + NL, encoding="utf-8")

    off_calls, _, _ = scenario(False, mutate=mutate_off)
    refused = [line for line in lines if "repair prefix not shared" in line]
    assert len(refused) == 1 and "backend/src/lib/small.js" in refused[0], lines
    assert prompts(on_calls, "repair")["1"] == prompts(off_calls, "repair")["1"]
    assert "b + a" in prompts(on_calls, "repair")["1"]
    assert any("repair prefix shared" in line for line in lines)

@case
def a_round_without_a_diagnosis_shares_nothing():
    on_calls, lines, implementer = scenario(True, diagnosis=False)
    off_calls, _, _ = scenario(False, diagnosis=False)
    assert not prompts(on_calls, "diagnose") and on_calls == off_calls
    assert not any("repair prefix" in line for line in lines) and implementer._diag_prefix is None

@case
def other_evidence_builds_fresh():
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    def attempt(change: str):
        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in FILES.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            implementer = CodegenImplementer(None, root, lambda: None, log=lines.append)
            red = TestOutcome(False, CHECKS[0])
            diagnosis = implementer._user(NODE, SPECS, red, [], CONTEXT, True, ask="DIAGNOSE")
            implementer._diag_prefix = implementer._last_user
            node = dict(NODE, id="REQ-10") if change == "leaf" else NODE
            outcome = TestOutcome(False, CHECKS[1]) if change == "check" else red
            written = ["backend/src/lib/small.js"] if change == "written" else []
            context = CONTEXT + " (changed)" if change == "notes" else CONTEXT
            implementer._user(node, SPECS, outcome, written, (context + "\nFix plan: x").strip(), True)
            result = implementer._shared_writer_prompt(node, outcome, written, context, "\nFix plan: x", "")
            assert implementer._diag_prefix is None
            if result is not None:
                assert diagnosis.endswith(NL + NL + "DIAGNOSE")
                assert result.startswith(diagnosis[:-len("DIAGNOSE")]) and result.endswith(implementer._last_user["ask"])
                assert "Fix plan: x" in result[len(diagnosis) - len("DIAGNOSE"):]
        return result, lines

    result, lines = attempt("")
    assert result is not None, lines
    for change, why in (("leaf", "another leaf"), ("check", "a new check result"), ("written", "the files written"),
                        ("notes", "the notes changed")):
        result, lines = attempt(change)
        assert result is None and any(why in line for line in lines), (change, lines)

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok   {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
