from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

CASES = []
NL = "\n"
ESC = chr(27)

def case(function):
    CASES.append(function)
    return function

SNAPSHOT = NL.join([
    "- main [ref=e3]:",
    "  - generic [ref=e4]:",
    "    - heading \"Q3 Sales\" [level=1] [ref=e5]",
    "    - button \"Undo\" [ref=e6]",
    "    - button \"Row actions\" [expanded] [ref=e7]",
    "    - menu \"Row actions\" [ref=e8]:",
    "      - menuitem \"Insert row above\" [ref=e9]",
    "      - menuitem \"Insert row below\" [ref=e10]",
    "      - menuitem \"Delete row\" [ref=e11]",
    "  - generic [ref=e12]:",
    "    - textbox \"Password\" [ref=e13]: hunter2-secret",
    "    - textbox \"Formula bar\" [ref=e14]: =A2+A3",
    "    - alert [ref=e15]: \"Error: could not save\"",
] + [f"    - gridcell \"A{n}\" [ref=e{100 + n}]: value {n}" for n in range(1, 60)])
MARKDOWN = "# Page snapshot" + NL + NL + "```yaml" + NL + SNAPSHOT + NL + "```"
HELPER_ERROR = "Error: An outsider who only read the requirement could not find anything to click: menuitem named Insert 1 row above."

def failing_output(entries) -> str:
    lines = ["FAILED SPEC FILES: REQ-9.spec.js"]
    for number, (title, error, folder) in enumerate(entries, 1):
        lines += [f"  {number}) test-e2e/REQ-9.spec.js:{number * 10}:1 › {title} ───", "    " + error,
                  "      url: http://127.0.0.1:3100/workbooks/1", "",
                  f"    Error Context: test-results/{folder}/error-context.md", ""]
    lines.append(f"  {len(entries)} failed")
    lines += [f"    test-e2e/REQ-9.spec.js:{number * 10}:1 › {title} ───" for number, (title, _e, _f) in enumerate(entries, 1)]
    return NL.join(lines) + NL

def fake_run(backend: Path, entries, markdown: str = MARKDOWN, paths=None, color: bool = False) -> Path:
    specs = []
    for number, (title, error, folder) in enumerate(entries, 1):
        target = backend / "test-results" / folder / "error-context.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        path = (paths or {}).get(folder, str(target))
        specs.append({"title": title, "file": "REQ-9.spec.js", "line": number * 10, "column": 1, "tests": [{
            "status": "unexpected", "results": [{"status": "failed", "errors": [{"message": (
                error.replace("Error: ", "Error: " + ESC + "[2m", 1) + ESC + "[22m" if color else error) + NL + "  url: x"}],
                                                 "attachments": [{"name": "error-context", "contentType": "text/markdown",
                                                                  "path": path}]}]}]})
    report = backend / "report.json"
    report.write_text(json.dumps({"suites": [{"title": "REQ-9.spec.js", "file": "REQ-9.spec.js", "specs": specs}]}),
                      encoding="utf-8")
    return report

def fresh():
    from agent import testrun

    with testrun._ERROR_CONTEXTS_LOCK:
        testrun._ERROR_CONTEXTS.clear()

        testrun._BOUND_NOTES.clear()
        testrun._FRESH_RUN.clear()

def v147_summary(outcome) -> str:
    head = "ALL TESTS PASSED" if outcome.passed else "TESTS FAILING"
    parts = [head] + (["ran: " + ", ".join(outcome.ran)] if outcome.ran else []) \
        + (["skipped: " + ", ".join(outcome.skipped)] if outcome.skipped else [])
    return NL.join(parts) + NL + NL + outcome.output

@case
def looked_for_reads_the_target_of_helper_and_playwright_errors():
    from agent.testrun import looked_for

    assert looked_for(HELPER_ERROR) == ("menuitem", "Insert 1 row above")
    assert looked_for("Error: An outsider who only read the requirement could not find anything to click: Insert 1 row above.") \
        == ("", "Insert 1 row above")
    assert looked_for("Error: The requirement says button named Save / link named Back is shown, but nothing visible matches.") \
        == ("button", "Save")
    assert looked_for("Error: An outsider who only read the requirement could not find a field to type into: textbox named Title.") \
        == ("textbox", "Title")

    chain = ("Error: expect(locator).toContainText(expected) failed\n\nLocator: getByRole('grid', { name: /^\\s*Worksheet\\s+grid\\s*$/i })"
             ".first().getByRole('gridcell', { name: /^\\s*A1\\s*$/i }).first()\nTimeout: 5000ms")
    assert looked_for(chain) == ("gridcell", "A1"), looked_for(chain)
    assert looked_for("Error: locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for getByRole('dialog').getByText('Save changes')") \
        == ("", "Save changes")
    assert looked_for("Error: locator.click: Timeout\n  - waiting for getByRole('button', { name: \"Add sheet\" })") == ("button", "Add sheet")
    assert looked_for("Error: expect(received).toBe(expected)") == ("", "")

@case
def the_excerpt_marks_the_found_line_with_its_enclosing_lines_and_keeps_the_limit():
    from agent import testrun

    text = testrun.error_context_excerpt(MARKDOWN, "Error: x\n  - waiting for getByRole('menuitem', { name: 'Delete row' })",
                                         "REQ-9.spec.js › t", limit=700)
    assert "looked for menuitem \"Delete row\"; it IS in the snapshot (line 9 of" in text, text
    assert "- menuitem \"Delete row\"   <--" in text and "- main:" in text and "[ref=" not in text, text
    assert len(text) <= 700, len(text)
    tight = testrun.error_context_excerpt(MARKDOWN, "Error: x" + NL + "  - waiting for getByRole('menuitem', { name: 'Delete row' })",
                                          "t", limit=360)
    assert len(tight) <= 360 and "- menuitem \"Delete row\"   <--" in tight, (len(tight), tight)
    other_role = testrun.error_context_excerpt(MARKDOWN, "Error: x" + NL + "  - waiting for getByRole('link', { name: 'Undo' })", "t")
    assert "no line has role link with this name; line 4 of" in other_role and "- button \"Undo\"   <--" in other_role, other_role
    big = MARKDOWN.replace("```" + NL + "```", "") + NL.join(f"    - gridcell \"Z{n}\": {n}" for n in range(3000))
    huge = testrun.error_context_excerpt(big, "Error: x\n  - waiting for getByRole('gridcell', { name: 'A30' })", "t")
    assert len(huge) <= testrun.ERROR_CONTEXT_TEST_CHARS and "gridcell \"A30\": value 30   <--" in huge, huge

@case
def a_target_missing_from_the_snapshot_shows_the_closest_lines_and_says_so():
    from agent import testrun

    text = testrun.error_context_excerpt(MARKDOWN, HELPER_ERROR, "REQ-9.spec.js › t")
    assert "looked for menuitem \"Insert 1 row above\"; no line contains this name; line 7 of" in text, text
    assert "- menuitem \"Insert row above\"   <--" in text and "- menu \"Row actions\":" in text, text
    none = testrun.error_context_excerpt(MARKDOWN, "Error: expect(received).toBe(expected)", "t")
    assert "could not be read from the error; the snapshot's first lines:" in none and "- main:" in none, none
    assert "more lines)" in none and len(none) <= testrun.ERROR_CONTEXT_TEST_CHARS, none
    nothing = testrun.error_context_excerpt(MARKDOWN, "Error: could not find anything to click: Frobnicate.", "t")
    assert "no line of the snapshot shares a word with it; its first lines:" in nothing, nothing

@case
def a_secret_named_field_value_is_never_shown():
    from agent import testrun

    for message in ("Error: x\n  - waiting for getByRole('textbox', { name: 'Password' })", "Error: expect(received).toBe(expected)"):
        text = testrun.error_context_excerpt(MARKDOWN, message, "t")
        assert "hunter2" not in text and "textbox \"Password\": <hidden>" in text, text
        assert "textbox \"Formula bar\": =A2+A3" in text, text

@case
def a_failed_check_shows_the_snapshot_in_its_summary_and_leaves_its_output_alone():
    from agent import testrun

    fresh()
    entries = [("REQ-9: insert a row", HELPER_ERROR, "REQ-9-REQ-9-insert-a-row")]
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        report = fake_run(backend, entries, color=True)
        assert testrun.remember_error_contexts(report, backend) == 1
    output = "E2E FAILED against the real application" + NL + failing_output(entries)
    outcome = testrun.TestOutcome(False, output)
    summary = outcome.summary
    assert outcome.output == output
    assert summary.startswith(v147_summary(outcome).rstrip(NL) + NL + NL + testrun.ERROR_CONTEXT_HEAD), summary
    assert "Page snapshot at the failure (Playwright error-context)" in summary
    assert "-- REQ-9.spec.js › REQ-9: insert a row: looked for menuitem \"Insert 1 row above\"" in summary, summary
    assert "- menuitem \"Insert row above\"   <--" in summary, summary
    assert len(summary) - len(v147_summary(outcome)) <= 2 * testrun.ERROR_CONTEXT_TEST_CHARS + 400

@case
def at_most_two_tests_in_output_order():
    from agent import testrun

    fresh()
    entries = [(f"REQ-9: test {n}", HELPER_ERROR.replace("Insert 1 row above", f"Thing {n}"), f"REQ-9-REQ-9-test-{n}") for n in (1, 2, 3)]
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        assert testrun.remember_error_contexts(fake_run(backend, entries), backend) == 3
    summary = testrun.TestOutcome(False, failing_output([entries[2], entries[0], entries[1]])).summary
    shown = re.findall(r"^-- REQ-9\.spec\.js › REQ-9: test (\d)", summary, re.M)
    assert shown == ["3", "1"], shown

@case
def an_excerpt_of_another_failure_of_the_same_test_is_not_shown():
    from agent import testrun

    fresh()
    folder = "REQ-9-REQ-9-insert-a-row"
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        testrun.remember_error_contexts(fake_run(backend, [("REQ-9: insert a row", HELPER_ERROR, folder)]), backend)
    other = failing_output([("REQ-9: insert a row", "Error: expect(locator).toHaveText(expected) failed", folder)])
    assert testrun.ERROR_CONTEXT_HEAD not in testrun.TestOutcome(False, other).summary
    same = failing_output([("REQ-9: insert a row", HELPER_ERROR, folder)])
    assert testrun.ERROR_CONTEXT_HEAD in testrun.TestOutcome(False, same).summary

@case
def nothing_changes_without_a_remembered_file():
    from agent import testrun

    fresh()
    entries = [("REQ-9: insert a row", HELPER_ERROR, "REQ-9-REQ-9-insert-a-row")]
    plain = testrun.TestOutcome(False, "E2E FAILED against the real application" + NL + "  1) test-e2e/REQ-9.spec.js:3:1 › t" + NL
                                + "    " + HELPER_ERROR + NL, ran=["backend/x"])
    assert plain.summary == v147_summary(plain)
    unknown = testrun.TestOutcome(False, failing_output(entries))
    assert unknown.summary == v147_summary(unknown)
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        testrun.remember_error_contexts(fake_run(backend, entries), backend)
    green = testrun.TestOutcome(True, "E2E PASSED" + NL + failing_output(entries))
    assert green.summary == v147_summary(green)
    assert testrun.remember_error_contexts(None, None) == 0
    with tempfile.TemporaryDirectory() as tmp:
        assert testrun.remember_error_contexts(Path(tmp) / "missing.json", Path(tmp)) == 0
        (Path(tmp) / "broken.json").write_text("{not json", encoding="utf-8")
        assert testrun.remember_error_contexts(Path(tmp) / "broken.json", Path(tmp)) == 0

@case
def files_outside_test_results_hidden_or_linked_are_never_read():
    from agent import testrun

    fresh()
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        outside = Path(tmp) / "secrets" / "error-context.md"
        outside.parent.mkdir(parents=True)
        outside.write_text(MARKDOWN.replace("Insert row above", "OUTSIDE-LEAK"), encoding="utf-8")
        hidden = backend / "test-results" / ".cache" / "error-context.md"
        hidden.parent.mkdir(parents=True)
        hidden.write_text(MARKDOWN.replace("Insert row above", "HIDDEN-LEAK"), encoding="utf-8")
        env = backend / "test-results" / "t-env" / ".env"
        env.parent.mkdir(parents=True)
        env.write_text("SECRET=ENV-LEAK", encoding="utf-8")
        other = backend / "test-results" / "t-cred" / "credentials.json"
        other.parent.mkdir(parents=True)
        other.write_text('{"key": "CRED-LEAK"}', encoding="utf-8")
        entries = [("REQ-9: a", HELPER_ERROR, "t-a"), ("REQ-9: b", HELPER_ERROR, "t-b"), ("REQ-9: c", HELPER_ERROR, "t-env"),
                   ("REQ-9: e", HELPER_ERROR, "t-cred")]
        paths = {"t-a": str(outside), "t-b": str(hidden), "t-env": str(env), "t-cred": str(other)}
        assert testrun.remember_error_contexts(fake_run(backend, entries, paths=paths), backend) == 0
        relative = {"t-a": "test-results/../../secrets/error-context.md"}
        assert testrun.remember_error_contexts(fake_run(backend, entries[:1], paths=relative), backend) == 0
        link = backend / "test-results" / "t-link" / "error-context.md"
        link.parent.mkdir(parents=True)
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            link = None
        if link is not None:
            report = backend / "report.json"
            data = json.loads(fake_run(backend, [("REQ-9: d", HELPER_ERROR, "t-other")]).read_text(encoding="utf-8"))
            data["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"][0]["path"] = str(link)
            report.write_text(json.dumps(data), encoding="utf-8")
            assert testrun.remember_error_contexts(report, backend) == 0
    with testrun._ERROR_CONTEXTS_LOCK:
        kept = " ".join(text for _first, text in testrun._ERROR_CONTEXTS.values())
    assert not kept and "LEAK" not in kept

@case
def windows_paths_and_a_bounded_memory():
    from agent import testrun

    fresh()
    entries = [("REQ-9: insert a row", HELPER_ERROR, "REQ-9-REQ-9-insert-a-row")]
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        testrun.remember_error_contexts(fake_run(backend, entries), backend)
    windows = failing_output(entries).replace("test-results/REQ-9-REQ-9-insert-a-row/error-context.md",
                                              "test-results\\REQ-9-REQ-9-insert-a-row\\error-context.md")
    assert "Error Context: test-results\\REQ-9" in windows
    assert testrun.ERROR_CONTEXT_HEAD in testrun.TestOutcome(False, windows).summary
    many = [(f"REQ-9: t{n}", HELPER_ERROR, f"f{n}") for n in range(testrun.ERROR_CONTEXT_PER_RUN + 2)]
    for round_ in range(8):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Path(tmp) / "backend"
            renamed = [(t, e, f"{f}-{round_}") for t, e, f in many]
            assert testrun.remember_error_contexts(fake_run(backend, renamed), backend) == testrun.ERROR_CONTEXT_PER_RUN
    with testrun._ERROR_CONTEXTS_LOCK:
        assert len(testrun._ERROR_CONTEXTS) == testrun.ERROR_CONTEXT_KEEP and "f0-7" in testrun._ERROR_CONTEXTS
        assert f"f{testrun.ERROR_CONTEXT_PER_RUN}-7" not in testrun._ERROR_CONTEXTS

@case
def signatures_progress_and_blame_read_the_output_without_the_snapshot():
    import main as factory
    from agent import codegen, testrun

    fresh()
    entries = [("REQ-9: insert a row", HELPER_ERROR, "REQ-9-REQ-9-insert-a-row"), ("REQ-9: b", HELPER_ERROR, "REQ-9-REQ-9-b")]
    output = "E2E FAILED against the real application" + NL + failing_output(entries)
    tests = {"backend/test-e2e/REQ-9.spec.js": "test('x', async () => {})"}
    before = (codegen.red_signature(output), codegen.own_test_failures(codegen.blocking_output(output), tests),
              factory.failed_test_signatures(output), factory.failed_test_titles(output))
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        testrun.remember_error_contexts(fake_run(backend, entries), backend)
    outcome = testrun.TestOutcome(False, output)
    summary = outcome.summary
    assert testrun.ERROR_CONTEXT_HEAD in summary and outcome.output == output
    after = (codegen.red_signature(outcome.output), codegen.own_test_failures(codegen.blocking_output(outcome.output), tests),
             factory.failed_test_signatures(outcome.output), factory.failed_test_titles(outcome.output))
    assert after == before

    assert codegen.red_signature(summary) != before[0]
    progress = codegen.TestProgress()
    for _ in range(3):
        moved, why = progress.observe(codegen.blocking_output(testrun.TestOutcome(False, output).output), tests,
                                      codegen.red_signature(output))
    assert progress.last_stuck and not moved, why

@case
def the_implementer_prompt_carries_the_snapshot():
    import cases_v146b as b
    from agent import testrun

    fresh()
    entries = [("REQ-9: the grid shows the value", HELPER_ERROR, "REQ-9-REQ-9-the-grid-shows-the-value")]
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        testrun.remember_error_contexts(fake_run(backend, entries), backend)
    red = testrun.TestOutcome(False, "E2E FAILED against the real application" + NL + failing_output(entries))

    class Recording(b.Model):
        prompts: list[str] = []

        def chat(self, messages, **options):
            Recording.prompts.append(NL.join(str(m.get("content", "")) for m in messages))
            return super().chat(messages, **options)

    clock = b.Clock()
    with b.patched(clock), b.State(), tempfile.TemporaryDirectory() as tmp:
        model = Recording([b.block(b.PAGE, "export default function P() { return null; }")], clock=clock)
        implementer = b.make(model, Path(tmp), [red], max_rounds=1)
        implementer.implement(b.NODE, b.TESTS, red)
    assert any(testrun.ERROR_CONTEXT_HEAD in p and "- menuitem \"Insert row above\"   <--" in p for p in Recording.prompts), \
        [p[-600:] for p in Recording.prompts]

@case
def verify_hands_the_report_over_before_test_results_is_removed():
    import inspect

    from agent import verify

    source = inspect.getsource(verify._run_e2e_once)
    call = source.find("remember_error_contexts(report, backend)")
    removal = source.find('shutil.rmtree(backend / "test-results"')
    assert call >= 0, "verify._run_e2e_once does not call remember_error_contexts(report, backend) (v148b wiring)"
    assert 0 <= call < removal, (call, removal)
    assert re.search(r"if code != 0:\s*\n[^\n]*page_errors_note\(report\)[^\n]*\n\s*remember_error_contexts\(report, backend\)",
                     source), "the call belongs next to page_errors_note, for a red run only"

FOLDER = "REQ-9-REQ-9-insert-a-row"
ONE = [("REQ-9: insert a row", HELPER_ERROR, FOLDER)]

def marked(word: str) -> str:
    return MARKDOWN.replace("Q3 Sales", f"{word} marker")

def run_once(word: str, gone: bool = False, broken: bool = False) -> int:
    from agent import testrun

    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        report = fake_run(backend, ONE, markdown=marked(word))
        if gone:
            (backend / "test-results" / FOLDER / "error-context.md").unlink()
        if broken:
            report.write_text("{cut", encoding="utf-8")
        return testrun.remember_error_contexts(report, backend)

def markers(outcome) -> list[str]:
    return re.findall(r"(\w+) marker", outcome.summary)

@case
def an_earlier_outcome_keeps_the_snapshot_of_its_own_run():
    from agent import testrun

    fresh()
    output = "E2E FAILED against the real application" + NL + failing_output(ONE)
    assert run_once("Alpha") == 1
    first = testrun.TestOutcome(False, output)
    assert markers(first) == ["Alpha"], first.summary
    assert run_once("Beta") == 1
    second = testrun.TestOutcome(False, output)
    assert markers(second) == ["Beta"], second.summary
    assert markers(first) == ["Alpha"], first.summary
    assert first.output == second.output == output

@case
def a_run_whose_snapshot_cannot_be_read_shows_none_of_an_earlier_run():
    from agent import testrun

    fresh()
    output = "E2E FAILED against the real application" + NL + failing_output(ONE)
    assert run_once("Alpha") == 1
    first = testrun.TestOutcome(False, output)
    assert run_once("Gamma", gone=True) == 0
    gone = testrun.TestOutcome(False, output)
    assert testrun.ERROR_CONTEXT_HEAD not in gone.summary and gone.summary == v147_summary(gone), gone.summary
    assert run_once("Alpha") == 1 and markers(testrun.TestOutcome(False, output)) == ["Alpha"]
    assert run_once("Delta", broken=True) == 0
    broken = testrun.TestOutcome(False, output)
    assert broken.summary == v147_summary(broken), broken.summary
    assert markers(first) == ["Alpha"], first.summary

@case
def an_outcome_that_carries_an_earlier_outcomes_output_keeps_that_note():
    from agent import testrun

    fresh()
    output_a = "E2E FAILED against the real application" + NL + "1 of 2 spec files passed" + NL + failing_output(ONE)
    output_b = "E2E FAILED against the real application" + NL + "0 of 2 spec files passed" + NL + failing_output(ONE)
    run_once("Alpha")
    first = testrun.TestOutcome(False, output_a)
    run_once("Beta")
    second = testrun.TestOutcome(False, output_b)
    assert markers(first) == ["Alpha"] and markers(second) == ["Beta"], (first.summary, second.summary)
    wrapped = testrun.TestOutcome(False, testrun.LINT_HEADER + "x.tsx:1: finding" + NL + NL + first.output)
    assert markers(wrapped) == ["Alpha"], wrapped.summary
    again = testrun.TestOutcome(False, first.output)
    assert markers(again) == ["Alpha"], again.summary
    assert markers(testrun.TestOutcome(False, second.output)) == ["Beta"]
    once = testrun.TestOutcome(False, first.summary)
    assert once.summary.count(testrun.ERROR_CONTEXT_HEAD) == 1, once.summary

@case
def excerpts_handed_over_with_the_run_result_are_used_alone():
    from agent import testrun

    fresh()
    output = "E2E FAILED against the real application" + NL + failing_output(ONE)
    with tempfile.TemporaryDirectory() as tmp:
        backend = Path(tmp) / "backend"
        own = testrun.read_error_contexts(fake_run(backend, ONE, markdown=marked("Alpha")), backend)
    assert list(own) == [FOLDER] and not testrun._ERROR_CONTEXTS, own
    run_once("Beta")
    assert markers(testrun.TestOutcome(False, output, error_contexts=own)) == ["Alpha"]
    empty = testrun.TestOutcome(False, output, error_contexts={})
    assert empty.summary == v147_summary(empty), empty.summary
    assert testrun.read_error_contexts(None, None) == {}

    inner = testrun.TestOutcome(False, "E2E FAILED (inner)" + NL + failing_output(ONE), error_contexts=own)
    run_once("Gamma")
    testrun.TestOutcome(False, "FRONTEND BUILD FAILED")
    wrapped = testrun.TestOutcome(False, testrun.LINT_HEADER + "x.tsx:1: finding" + NL + NL + inner.output)
    assert markers(inner) == ["Alpha"] and markers(wrapped) == ["Alpha"], wrapped.summary

SECRET_SNAPSHOT = NL.join([
    "- main [ref=e1]:",
    "  - textbox \"API token\" [ref=e2]: |",
    "    sk-live-SECRETVALUE-1",
    "    second-line-SECRET",
    "  - textbox \"Password\" [ref=e3]:",
    "    - paragraph [ref=e4]: hunter2-child",
    "  - dialog \"Change password\" [ref=e5]:",
    "    - textbox \"Current password\" [ref=e6]: old-SECRET-pw",
    "    - button \"Save\" [ref=e7]",
    "  - note \"Access token\" [ref=e10]: |",
    "    tok-SECRET-line",
    "  - textbox \"Notes\" [ref=e8]: |",
    "    first note line",
    "    second note line",
    "  - button \"Insert row above\" [ref=e9]",
])

@case
def a_secret_named_nodes_multi_line_value_and_input_subtree_are_hidden():
    from agent import testrun

    markdown = "# Page snapshot" + NL + NL + "```yaml" + NL + SECRET_SNAPSHOT + NL + "```"
    for message in ("Error: x\n  - waiting for getByRole('textbox', { name: 'API token' })",
                    "Error: x\n  - waiting for getByRole('button', { name: 'Save' })",
                    HELPER_ERROR, "Error: expect(received).toBe(expected)"):
        text = testrun.error_context_excerpt(markdown, message, "t")
        assert "SECRET" not in text and "hunter2" not in text and "\"API token\": |" not in text, text
    whole = testrun.error_context_excerpt(markdown, "Error: expect(received).toBe(expected)", "t")
    assert "textbox \"API token\": <hidden>" + NL + "    <hidden>" + NL + "    <hidden>" in whole, whole
    assert "- textbox \"Password\":" + NL + "    <hidden>" in whole, whole
    assert "- dialog \"Change password\":" in whole and "- button \"Save\"" in whole, whole
    assert "first note line" in whole and "second note line" in whole, whole
    lines = testrun.snapshot_lines(markdown)
    shown = testrun.shown_lines(lines)
    assert len(shown) == len(lines)
    assert [index for index, line in enumerate(shown) if line.strip() == testrun.HIDDEN_VALUE] == [2, 3, 5, 10], shown

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    chosen = [f for f in CASES if not sys.argv[1:] or f.__name__ in sys.argv[1:]]
    failed = 0
    for function in chosen:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    fresh()
    print(f"{len(chosen) - failed}/{len(chosen)} v148b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
