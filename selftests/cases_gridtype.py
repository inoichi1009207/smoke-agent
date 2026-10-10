from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl

NL = chr(10)
CASES = []
REL = "backend/test-e2e/REQ-2-2-2.spec.js"
HEAD = ('const { test } = require("@playwright/test");' + NL
        + 'const { click, fill, press, typeText, see } = require("../e2e-support/outsider");' + NL + NL)

def case(function):
    CASES.append(function)
    return function

def wrap(body: str) -> str:
    return HEAD + 'test("REQ-2-2-2: edit a cell", async ({ page }) => {' + NL + "  " + body + NL + "});" + NL

@case
def e1_cell_hint_names_the_typing_helper_in_every_form():
    from agent.testrun import spec_api_misuse

    fill_cell = wrap('await fill(page, { role: "gridcell", name: "B2" }, "7");')
    later = {"id": "REQ-2-2-1", "description": "Rows.", "earlier_names": [], "later_names": ["Formula bar"]}
    earlier = {"id": "REQ-3-1-1", "description": "Edit.", "earlier_names": ["Formula bar"], "later_names": []}
    two = {"id": "REQ-3-1-1", "description": "Edit.", "earlier_names": ["Formula bar", "Cell editor"], "later_names": []}
    for node in (None, later, earlier, two):
        text = spec_api_misuse(REL, fill_cell, node)
        assert len(text) == 1, text
        assert 'typeText(page, "<value>")' in text[0] and 'press(page, "Enter")' in text[0], text[0]
        assert "press the keys" not in text[0] and "type into the selected cell: typeText(page," in text[0]
    assert 'fill the "Formula bar" field' in spec_api_misuse(REL, fill_cell, earlier)[0]

@case
def e1_flags_every_fill_of_a_cell_and_no_real_field():
    from agent.testrun import spec_api_misuse

    flagged = [
        'await fill(page, { role: "gridcell", name: "B2" }, "7");',
        'await fill(page, { role: "cell", name: "B2" }, "7");',
        'await fill(page, "B2", "7");',
        'await locate(page, { role: "gridcell", name: "B2" }).fill("7");',
        'await region(page, "gridcell", "B2").fill("7");',
        'await page.getByRole("gridcell", { name: "B2" }).first().fill("7");',
        'const cell = locate(page, { role: "gridcell", name: coord });' + NL + '  await cell.fill("7");',
        'await click(page, { role: "gridcell", name: coord });' + NL + '  await fill(page, coord, "7");',
    ]
    for body in flagged:
        found = [p for p in spec_api_misuse(REL, wrap(body)) if "typeText" in p]
        assert found, body
    clean = [
        'await fill(page, { role: "textbox", name: "Formula bar" }, "7");',
        'await fill(page, "Q3 Sales", "x");',
        'await fill(page, "Value", "7", region(page, "gridcell", "B2"));',
        'await locate(page, { role: "row", name: "x" }).fill("7");',
        'const cell = locate(page, { role: "gridcell", name: coord });' + NL + '  await click(page, cell);',
        'await click(page, { role: "gridcell", name: "B2" }); await typeText(page, "7"); await press(page, "Enter");',
    ]
    for body in clean:
        assert not any("grid cell" in p for p in spec_api_misuse(REL, wrap(body))), body

@case
def e1_flags_press_of_text_and_typetext_misuse_as_the_helper_does():
    from agent.testrun import press_text_misuse

    for key in ("", "=A1+B1", "=D1*2", "acme-docs", "hello world", "Control+=D1", "Control + Z"):
        found = press_text_misuse(REL, wrap(f'await press(page, "{key}");'))
        assert len(found) == 1 and "typeText(page," in found[0], (key, found)
    for body in ('await press(page, "Enter");', 'await press(page, "Control+Z");', 'await press(page, "Control++");',
                 'await press(page, "Shift+=");', 'await press(page, "ArrowDown");', 'await press(page, "a");',
                 'await press(page, key);', 'await press(page, `${key}`);', 'await press(page, "Contr\\u006fl");',
                 '// await press(page, "=A1");', 'await typeText(page, "=A1+B1");', 'await typeText(page, value);'):
        assert press_text_misuse(REL, wrap(body)) == [], body
    assert "types nothing" in press_text_misuse(REL, wrap('await typeText(page, "");'))[0]
    assert "keys of their own" in press_text_misuse(REL, wrap('await typeText(page, "42\\n");'))[0]
    assert "keys of their own" in press_text_misuse(REL, wrap("await typeText(page, '42\\t');"))[0]
    assert "takes no target" in press_text_misuse(REL, wrap('await typeText(page, { role: "gridcell", name: "A1" }, "4");'))[0]
    assert press_text_misuse("backend/tests/x.test.js", 'await press(page, "=A1");') == []

@case
def e1_press_rule_is_the_helpers_own_rule():
    from agent.testrun import press_text_misuse

    result = _probe()
    if result is None:
        return
    for key, verdict in result["press"].items():
        flagged = bool(press_text_misuse(REL, wrap("await press(page, " + json.dumps(key) + ");")))
        assert flagged == verdict.startswith("SPEC DEFECT"), (key, verdict, flagged)

@case
def e1_runs_the_new_check_and_the_arity_check_knows_typetext():
    from agent.testrun import helper_arity_defects, outsider_undefined_calls, static_test_defects

    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / REL
        spec.parent.mkdir(parents=True)
        spec.write_text(wrap('await press(page, "=A1+B1");'), encoding="utf-8")
        assert 'press(page, "=A1+B1") is no key' in static_test_defects(Path(tmp), [REL])
    assert outsider_undefined_calls(REL, wrap('await typeText(page, "4");')) == []
    assert any("typeText(page, text)" in p for p in helper_arity_defects(REL, wrap("await typeText(page);")))

PROBE = r"""
const H = require('./outsider.js');
const KEYS = new Set(['Enter', 'Escape', 'Tab', 'Control', 'Shift', 'Z', 'a', '+', '=', 'ArrowDown']);
function fakePage() {
  const log = [];
  return { log, url: () => 'http://app.test/', on() {}, evaluate: async () => { throw new Error('no document'); },
    keyboard: {
      type: async (text, options) => { log.push(['type', text, options || null]); },
      press: async (k) => { log.push(['press', k]);
        const tokens = []; let b = '';
        for (const ch of k) { if (ch === '+' && b) { tokens.push(b); b = ''; } else b += ch; }
        tokens.push(b);
        for (const t of tokens) if (!KEYS.has(t)) throw new Error(`keyboard.press: Unknown key: "${t}"`); } } };
}
async function outcome(run) { try { await run(); return 'ok'; } catch (e) { return String(e && e.message).split('\n')[0]; } }
(async () => {
  const r = { exported: typeof H.typeText, press: {}, type: {} };
  for (const key of ['', '=A1+B1', '=D1*2', 'acme-docs', 'hello world', 'Control+=D1', 'Control + Z', 'Enter', 'Control+Z',
                     'Control++', 'Shift+=', 'ArrowDown', 'a']) {
    const p = fakePage(); r.press[key] = await outcome(() => H.press(p, key));
  }
  for (const [label, value] of [['text', '=A1*2'], ['number', 42], ['empty', ''], ['newline', '42\n'], ['tab', '4\t'],
                                ['escape', 'x\u001b'], ['target', { role: 'gridcell', name: 'A1' }], ['none', undefined]]) {
    const p = fakePage(); const o = await outcome(() => H.typeText(p, value)); r.type[label] = o + ' | ' + JSON.stringify(p.log);
  }
  process.stdout.write(JSON.stringify(r));
})();
"""
_RESULT: dict = {}

def _probe(helper_js: str | None = None) -> dict | None:
    key = helper_js or "current"
    if key in _RESULT:
        return _RESULT[key]
    node = shutil.which("node")
    if not node:
        return None
    if helper_js is None:
        from agent.outsider import HELPER_JS as helper_js
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        stub = base / "node_modules" / "@playwright" / "test"
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name":"@playwright/test","main":"index.js"}', encoding="utf-8")
        (stub / "index.js").write_text("module.exports = { test: {}, expect: {} };\n", encoding="utf-8")
        (base / "outsider.js").write_text(helper_js, encoding="utf-8")
        (base / "probe.cjs").write_text(PROBE, encoding="utf-8")
        done = subprocess.run([node, "probe.cjs"], cwd=str(base), capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stderr[-1500:]
        _RESULT[key] = json.loads(done.stdout)
    return _RESULT[key]

@case
def helper_typetext_types_keys_and_refuses_what_is_no_text_before_any_key():
    result = _probe()
    if result is None:
        return
    assert result["exported"] == "function"
    assert result["type"]["text"] == 'ok | [["type","=A1*2",{"delay":15}]]', result["type"]["text"]
    assert result["type"]["number"] == 'ok | [["type","42",{"delay":15}]]', result["type"]["number"]
    for label, why in (("empty", "empty text"), ("newline", "keys of their own"), ("tab", "keys of their own"),
                       ("escape", "keys of their own"), ("target", "takes no target"), ("none", "takes no target")):
        verdict = result["type"][label]
        assert verdict.startswith("SPEC DEFECT: typeText(page, text)") and why in verdict and verdict.endswith("| []"), (label, verdict)

@case
def helper_press_defect_names_the_typing_helper():
    result = _probe()
    if result is None:
        return
    for key in ("=A1+B1", "", "acme-docs"):
        verdict = result["press"][key]
        assert verdict.startswith('SPEC DEFECT: press() takes a key such as "Enter" or "Control+Z"; to type text use fill()/typeText()'), verdict
        assert "typeText(page, text) and then press(page, \"Enter\")" in verdict, verdict
    assert result["press"]["Enter"] == "ok" and result["press"]["Control++"] == "ok"

@case
def helper_texts_the_spec_writer_and_the_implementer_see():
    from agent import outsider

    prompt = outsider.SYSTEM_PROMPT
    assert "press, typeText, see" in prompt
    assert 'await click(page, { role: "gridcell", name: "B2" }); await typeText(page, "42"); await press(page, "Enter")' in prompt
    assert "never fill() it" in prompt
    js = outsider.HELPER_JS
    assert "typeText, see," in js.split("module.exports")[-1]

    assert "loadNow([...the paths that page reads]) inside its own click" in js

    assert "SELECTED_ROLES = ['option'" in js and "function nativeOptionHolders" in js and "CELL_FILL_DEFECT" in js

class _Reviewer:
    def __init__(self, answer):
        self.answer = answer

    def chat(self, messages, **options):
        return {"content": self.answer}

REVIEW_NODE = {"id": "REQ-7", "name": "Toolbar", "scenarios": [],
               "description": "The toolbar shows the `Save` button next to the `Undo` button at all times."}

@case
def review_gate_ignores_quote_style_and_logs_each_dropped_claim():
    from agent import outsider
    from agent.test_author import AuthoredFile

    spec = AuthoredFile(path="backend/test-e2e/REQ-7.spec.js", content='await click(page, { role: "button", name: "Save" });')
    retyped = _Reviewer('PROBLEM: ROLE / NAME | requirement: "shows the \\"Save\\" button next to the “Undo” button" | '
                        'spec: "await click(page, { role: \'button\', name: \'Save\' });" | fix: count it in the toolbar')
    lines: list[str] = []
    kept = outsider.review_acceptance(retyped, REVIEW_NODE, spec, "REQ-7", log=lines.append)
    assert kept.startswith("PROBLEM: ROLE / NAME"), (kept, lines)
    for answer, why in (('PROBLEM: NUMBER | requirement: "at all times" | spec: "x" | fix: y', "shorter than six words"),
                        ('PROBLEM: NAME | requirement: "the toolbar shows the Print button next to" | spec: "x" | fix: y',
                         "not in the requirement"),
                        ('PROBLEM: NAME | requirement: "shows the `Save` button next to the `Undo` button" | '
                         'spec: "await click(page, \'Print\');" | fix: y', "spec line not in the spec"),
                        ("PROBLEM: NAME | no quote at all | fix: y", "no quoted requirement")):
        lines = []
        assert outsider.review_acceptance(_Reviewer(answer), REVIEW_NODE, spec, "REQ-7", log=lines.append) == ""
        dropped = [line for line in lines if "review claim dropped" in line]
        assert len(dropped) == 1 and why in dropped[0] and answer[:30] in dropped[0], (why, lines)
    flat = " ".join(outsider.REVIEW_PROMPT.split())
    assert "at least SIX consecutive words" in flat and "six or more consecutive words of the requirement" in flat

T3A2 = "REQ-3: keeps the chosen region after a reload of the page"
VERDICT = ('The acceptance spec contradicts the requirement text:' + NL
           + 'SPEC: the test "' + cl.T3A + '" expects the region after a reload, the description says "keeps the chosen region"'
           + NL + '  url: http://127.0.0.1:3100/sheets' + NL + '  on the page now -> clickable: ["Secret menu"]' + NL
           + 'Latest check output:' + NL + 'E2E FAILED against the real application' + NL + 'Inner app detail 4711')

def world_with(authored, steps):
    from agent.codegen import ImplementResult
    from agent.test_author import AuthoredFile

    tmp, root, world, f = cl.with_world()
    world.titles["REQ-3"] = [cl.T3A, cl.T3B]
    world.green = {cl.T3B, T3A2}
    problems: list[str] = []
    contexts: list[str] = []

    def author(llm, node, prefix, problem="", current="", log=print):
        world.author_calls.append((str(node.get("id")), bool(problem)))
        problems.append(problem)
        world.tokens += 1000
        text = authored[min(len(problems) - 1, len(authored) - 1)]
        return None if text is None else AuthoredFile(path=f"backend/test-e2e/{prefix}.spec.js", content=text)

    class Implementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check
            world.implementers.append({"deadline": options.get("deadline"), "now": world.now})

        def implement(self, node, tests, outcome, extra_context=""):
            contexts.append(extra_context)
            step = steps.pop(0)
            world.tokens += step.get("tokens", 5000)
            world.now += step.get("seconds", 0.0)
            for rel, content in step.get("write", {}).items():
                (self.project_dir / rel).write_text(content, encoding="utf-8")
            runs = 0
            if step.get("check"):
                result = self.check()
                runs = 1
                status = step.get("status") or ("passed" if result.passed else "budget")
            else:
                status = step["status"]
            return ImplementResult(status, list(step.get("write", {})), step.get("detail", "scripted"), runs)

    return tmp, root, world, f, problems, contexts, {"author": author, "Implementer": Implementer}

FIRST = cl.spec_text([cl.T3A, cl.T3B])

@case
def a_test_defect_goes_back_to_its_author_once_and_a_corrected_test_is_admitted():
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, cl.spec_text([T3A2, cl.T3B])],
                                                                [{"status": "test_defect", "detail": VERDICT}])
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False), ("REQ-3", True)], world.author_calls
        back = problems[1]
        assert back.startswith("The implementer who repairs the application says a test of your spec is a TEST DEFECT")
        assert cl.T3A in back and "keeps the chosen region" in back

        assert "Latest check output" not in back and "4711" not in back and "Secret menu" not in back and "url:" not in back
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after a test-defect hand-back", (entry["outcome"], lines)
        assert entry["test_defect_hand_back"]["result"] == "rewritten: green" and not entry["test_defect_hand_back"]["unchanged"]
        assert len(world.implementers) == 1 and cl.app_text(root) == "base" and f.description_specs == [cl.D3]
        tests = {t["title"]: t for t in entry["tests"]}
        assert tests[cl.T3A]["reason"] == "test-defect-author-dropped" and tests[T3A2]["final"] == "admitted", tests
        assert cl.D3 in f.revised_specs
        assert world.builds == 2

@case
def a_kept_test_goes_to_the_implementer_once_more_with_the_earlier_verdict():
    steps = [{"status": "test_defect", "detail": VERDICT},
             {"write": {cl.APP: "base " + cl.ok(cl.T3A)}, "check": True}]
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST], steps)
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert entry["test_defect_hand_back"]["unchanged"] and entry["test_defect_hand_back"]["result"] == "rewritten: red"
        assert len(world.implementers) == 2 and entry["outcome"] == "admitted after a kept repair", (entry["outcome"], lines)
        assert "Your earlier verdict:" in contexts[1] and "keeps the chosen region" in contexts[1]
        assert "Latest check output" not in contexts[1] and "Your earlier verdict" not in contexts[0]
        assert cl.app_text(root) == "base " + cl.ok(cl.T3A)
        assert entry["first_repair"]["implementer"]["status"] == "test_defect"

@case
def a_second_test_defect_is_cut_without_a_second_hand_back():
    steps = [{"status": "test_defect", "detail": VERDICT}, {"status": "test_defect", "detail": VERDICT}]
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST], steps)
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False), ("REQ-3", True)], world.author_calls
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after pruning (test-defect)", entry["outcome"]
        tests = {t["title"]: t for t in entry["tests"]}
        assert tests[cl.T3A]["reason"] == "test-defect" and tests[cl.T3B]["final"] == "admitted"
        assert len(world.implementers) == 2 and cl.app_text(root) == "base"

@case
def a_verdict_that_names_nothing_of_the_spec_is_not_handed_back():
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST], [{"status": "test_defect", "detail": "scripted"}])
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False)]
        entry = cl.ledger(f)[0]
        assert entry["test_defect_hand_back"]["result"] == "not handed back: the verdict quotes nothing of the spec"
        assert entry["outcome"] == "admitted after pruning (test-defect)"

    import agent.description_pass as dp
    assert dp.verdict_points_at_spec("DESC-REQ-3.spec.js: Error: SPEC DEFECT ...", cl.D3, FIRST)
    assert not dp.verdict_points_at_spec('the "Delete row" button', cl.D3, FIRST)
    assert dp.verdict_points_at_spec("SPEC: `see(page, 'x')` checks nothing stated", cl.D3, FIRST)

@case
def no_hand_back_past_the_leaf_share_or_the_token_budget():
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, cl.spec_text([T3A2, cl.T3B])],
                                                                [{"status": "test_defect", "detail": VERDICT, "seconds": 5000.0}])
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False)]
        assert cl.ledger(f)[0]["test_defect_hand_back"]["result"] == "not handed back: time"
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, cl.spec_text([T3A2, cl.T3B])],
                                                                [{"status": "test_defect", "detail": VERDICT, "tokens": 60000}])
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.author_calls == [("REQ-3", False)]
        assert cl.ledger(f)[0]["test_defect_hand_back"]["result"] == "not handed back: token budget"

@case
def an_answer_that_fails_the_gate_or_e1_leaves_the_cut_as_before():

    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, cl.spec_text([cl.T3X])], [{"status": "test_defect", "detail": VERDICT}])
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert entry["test_defect_hand_back"]["result"] == "the rewrite keeps no test quoting the description"
        assert entry["outcome"] == "admitted after pruning (test-defect)" and len(world.implementers) == 1

    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, cl.spec_text([T3A2, cl.T3B]) + "SYNTAX("],
                                                                [{"status": "test_defect", "detail": VERDICT}])
    with tmp:
        patch["static_defects"] = lambda project_dir, files: (
            "does not parse" if "SYNTAX(" in (project_dir / files[0]).read_text(encoding="utf-8") else "")
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert entry["test_defect_hand_back"]["result"] == "the rewrite does not parse / load"
        content = (root / cl.D3).read_text(encoding="utf-8")
        assert cl.T3B in content and cl.T3A not in content and "SYNTAX(" not in content and T3A2 not in content

    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, None], [{"status": "test_defect", "detail": VERDICT}])
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert cl.ledger(f)[0]["test_defect_hand_back"]["result"] == "no usable rewrite"

@case
def after_a_repair_that_built_its_own_sources_the_answer_runs_on_a_fresh_build():
    steps = [{"write": {cl.APP: "base half"}, "check": True, "status": "test_defect", "detail": VERDICT}]
    tmp, root, world, f, problems, contexts, patch = world_with([FIRST, cl.spec_text([T3A2, cl.T3B])], steps)
    with tmp:
        seen: list[tuple[int, str]] = []
        run_e2e = world.run_e2e

        def watching(project_dir, specs, log=print, shared_data=False):
            seen.append((world.builds, cl.app_text(root)))
            return run_e2e(project_dir, specs, log, shared_data)

        patch["run_e2e"] = watching
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert entry["outcome"] == "admitted after a test-defect hand-back", (entry["outcome"], lines)

        assert seen[-1] == (4, "base"), seen

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} gridtype cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
