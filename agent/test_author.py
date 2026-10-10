from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .conventions import PROJECT_CONVENTIONS
from .llm import LLM
from .settings import AUTHOR_THINKING

ALLOWED_TEST_PREFIXES = ("frontend/tests/", "backend/tests/", "backend/test-e2e/")

FILE_BLOCK = re.compile(r"=== FILE: (?P<path>[^\n=]+?) ===\n(?P<body>.*?)\n=== END FILE ===", re.DOTALL)

SYSTEM_PROMPT = f"""You are the TEST AUTHOR agent of a test-driven software factory.
You receive ONE requirement node and write executable tests for it. You never write application code.

{PROJECT_CONVENTIONS}
Rules:
- Cover every scenario step (GIVEN/WHEN/THEN) with concrete assertions. Do not test styling or layout.
- NEVER mock or stub application modules (no vi.mock / jest.mock of "../src/api", axios, fetch, or any app file).
  Component tests cover only client-side behaviour that needs no server: accessible names, labels, initial values,
  client validation messages, navigation links, pure UI state (counters, dice, toggles). Every behaviour that needs
  the server (saving, sessions, login, duplicates) is covered by backend/tests (supertest) and by the E2E spec,
  which runs against the real application. If a scenario cannot be tested without a server, leave it to E2E.
- Locate elements the way a user would: getByRole with the exact accessible name from the requirement,
  getByTestId only where the requirement says "test id" / data-testid (a plain HTML id is an id attribute, checked
  as such -- never through getByTestId), getByLabelText for form fields. Do not depend on CSS classes.
- Locators must be unambiguous on a page that may contain fields from OTHER requirements: use exact names
  (getByLabel("密码", {{ exact: true }}), getByRole("button", {{ name: "下一步" }})), never loose regexes such as
  /密码/ that also match "登录密码" and "确认密码".
- Repeated items (note cards, table rows, list entries) all carry the same action buttons. Pick the item by its text
  first, then the control inside it: `const card = page.getByRole("listitem").filter({{ hasText: "Invoice 1042" }});
  await card.getByRole("button", {{ name: "Approve" }}).click();` (component tests: `within(card)`). A page-wide
  getByRole for a control that every item has matches several elements and fails.
- Never wait for real time (auto-dismiss after seconds, expiry after days): assert the state right after the action.
- The application starts on an empty data directory for every test run, so using the requirement's pre-existing data
  directly (test-data rule 1) is what proves it is really provided.
- Start every test title with the id of the requirement it verifies, e.g. test("REQ-2.1.3: rejects a duplicate
  username", ...). When the input lists several sibling requirements, every one of them needs at least one test.
- Translate the requirement's predicates into the matching assertion (E2E unless noted):
  shown / displayed / visible (显示, 可见)      -> toBeVisible(), in the initial state too (attributes alone prove nothing)
  not shown / must not appear (不显示, 不得出现) -> toHaveCount(0) or toBeHidden()
  initial / default value is X (初始值, 默认值)  -> toHaveValue(X)
  options include A, B, C (选项包括)            -> each option is present in that combobox
  required (必填)                              -> submit it empty: an error appears and the URL does not change
  navigates to / returns to / enters (导航, 返回, 进入) -> what the requirement states that page shows is visible (the
                                                  sign-in page: its form's fields); toHaveURL(...) only for a URL the
                                                  requirement states
  checked / unchecked (勾选)                    -> toBeChecked() / not.toBeChecked()
  disabled / clickable (禁用, 可点击)            -> toBeDisabled() / toBeEnabled()
  changes dynamically with input (动态变化)     -> capture the state before and after typing and assert they differ
  still holds after refresh (刷新后仍)          -> page.reload(), then repeat the same assertions
  must not create a session                    -> afterwards still signed out: no sign-out entry on "/", GET session
                                                  endpoint is 401 (API test)
  must not create an account                   -> no new session (the checks above); a later registration of the same
                                                  data is asserted to succeed only when the requirement says nothing
                                                  else about that data (no existing account holds it, it meets the
                                                  stated rules)
  exactly the same message (完全相同)           -> read both texts and assert equality
  must not return X (不得返回, 禁止返回)         -> API test: the response body has no such key
  unique / duplicate (唯一, 重复)               -> second submission is rejected, the first record is unchanged
  persisted / survives restart (持久化, 重启后)  -> backend test: reload the app module on the same data dir and read it back
- A text that can legitimately appear twice on a page (e.g. a dropdown placeholder that is also an error message)
  must not be asserted with getByText; assert an error message through the role the requirement names for it (role="alert" only when it names none) or a scoped container instead.
- Two frameworks, two vocabularies; never mix them. Component tests (Testing Library + jest-dom): screen.getByLabelText,
  getByPlaceholderText, toBeInTheDocument, toHaveTextContent, toHaveDisplayValue. Playwright specs: page.getByLabel,
  getByPlaceholder, toBeVisible, toHaveText / toContainText, toHaveValue, toHaveCount, toHaveURL.
- Backend test files must use ESM syntax: `import {{ describe, it, expect }} from "vitest"` and
  `import request from "supertest"`; `require("vitest")` does not work. App modules may still be loaded with require.
  The database helpers of backend/src/database/db_runtime.js -- run, get, all -- are ASYNC: `const rows = await all(...)`,
  `const row = await get(...)`. Without await a test holds a Promise ("expected Promise to have property 'length'",
  "rows.map is not a function"): in the local runs of 09-24 nearly every API test failed that way (ADR-099).
- Tests must be deterministic and independent; use userEvent (frontend) or page actions (E2E) for interaction.
  E2E specs run one after another on ONE instance without a reset: a successful rename, delete or edit of a shared
  seed record (or its parts) is done on a record the test creates first, or undone through the UI before the test ends.
- Produce these files:
  1. frontend/tests/<REQ-ID>.test.tsx - Vitest component test rendering <App/> (see conventions), ONLY for behaviour
     that works with no server at all (static texts, client-side validation, pure UI state). jsdom has no backend, so
     a page that loads its content from the API shows nothing there: for such requirements OMIT this file entirely.
     Import {{ describe, it, expect }} from "vitest" explicitly.
  2. backend/test-e2e/<REQ-ID>.spec.js - Playwright spec (CommonJS: const {{ test, expect }} = require("@playwright/test")),
     unless the request says an independent acceptance tester covers the user flows: then there is no file 2.
  3. backend/tests/<REQ-ID>.test.js - ONLY if the node needs server-side behaviour (API, persistence). Otherwise omit it.
- Output format, nothing else (no prose, no markdown fences around the blocks):
=== FILE: <project-relative path> ===
<file content>
=== END FILE ===
"""

CHOICE_SPEC_RULE = (
    "- Choose-one controls in the Playwright spec: where a step chooses an option in a labelled control or a combo box\n"
    "  (and the requirement states no other interaction for it, such as a named button that opens a list), choose it the\n"
    "  first time by clicking that combobox (located by its label), then getByRole with role option and the exact\n"
    "  option text from the requirement (exact: true); a later choice in the same control uses selectOption({ label }).\n"
    "  When the scenario chooses in that control only once and the requirement does not say that choosing opens another\n"
    "  page, add right after that choice: read its inputValue(), choose the same option again with selectOption({ label })\n"
    "  and assert toHaveValue with the value read.\n"
)
_CHOICE_SPEC_ANCHOR = "- Never wait for real time (auto-dismiss"

def system_prompt() -> str:
    from .settings import setting_on

    if not (setting_on("spec_choice_both_ways") and setting_on("conventions_v166") and setting_on("conventions_v167")
            and setting_on("choice_select_hybrid")):
        return SYSTEM_PROMPT
    if SYSTEM_PROMPT.count(_CHOICE_SPEC_ANCHOR) != 1:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT.replace(_CHOICE_SPEC_ANCHOR, CHOICE_SPEC_RULE + _CHOICE_SPEC_ANCHOR, 1)

NO_E2E_NOTE = (
    "\n\nDo NOT write file 2 (the Playwright spec): an independent acceptance tester covers every user flow. "
    "Write file 3 (backend API test) ONLY for server-side facts a user interface cannot show: fields a response "
    "must not contain, status codes of rejected requests, data surviving a restart. Keep it to those cases. "
    "Write file 1 only if it applies. If neither applies, output exactly: === NO TESTS ==="
)

def refusal_note(node: dict[str, Any]) -> str:
    try:
        from .coverage import refusal_api_block
        return refusal_api_block(node)
    except Exception:
        return ""

@dataclass
class AuthoredFile:
    path: str
    content: str

def describe_node(node: dict[str, Any]) -> str:
    lines: list[str] = []
    ancestor_context = str(node.get("ancestor_context") or "").strip()
    if ancestor_context:
        lines.append("Context inherited from ancestor requirements (background for this node, not extra scope):")
        lines.append(ancestor_context)
        lines.append("")
    lines.append(f"Requirement {node.get('id')}: {node.get('name', '')}".strip())
    description = str(node.get("description") or "").strip()
    if description:
        lines.append(description)
    scenarios = node.get("scenarios") or []
    if scenarios:
        lines.append("Scenarios:")
        for index, scenario in enumerate(scenarios, start=1):
            lines.append(f"  {index}. {scenario.get('name', '')}")
            for step in scenario.get("steps") or []:
                lines.append(f"     {step.get('keyword', '')}: {str(step.get('content', '')).strip()}")
    return "\n".join(lines)

FILE_HEADER = re.compile(r"^[ \t]*=== FILE: (?P<path>[^\n=]+?) ===[ \t]*$", re.MULTILINE)
FILE_END = re.compile(r"^[ \t]*=== END(?: OF)? FILE\b[^\n]*$", re.MULTILINE)

def split_file_blocks(text: str, complete: bool = False) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = re.sub(r"</?think>", "\n", text)
    headers = list(FILE_HEADER.finditer(text))
    for index, header in enumerate(headers):
        limit = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        segment = text[header.end() : limit]
        end = FILE_END.search(segment)
        if end is None and index + 1 >= len(headers) and not complete:

            continue
        body = segment[: end.start()] if end else segment
        blocks.append((header.group("path").strip(), body.strip("\n")))
    return blocks

def parse_file_blocks(text: str, complete: bool = False) -> list[AuthoredFile]:
    files: list[AuthoredFile] = []
    for raw_path, body in split_file_blocks(text, complete):
        path = raw_path.replace("\\", "/")

        body = re.sub(r"^```[a-zA-Z]*\n", "", body)
        body = re.sub(r"\n```\s*$", "", body)
        if not path.startswith(ALLOWED_TEST_PREFIXES):
            continue
        if path.startswith("backend/test-e2e/"):
            from .testrun import normalize_spec_imports
            body = normalize_spec_imports(body)
        files.append(AuthoredFile(path=path, content=body.rstrip() + "\n"))
    return files

class TestAuthor:
    def __init__(self, llm: LLM, log=print) -> None:
        self.llm = llm
        self.log = log

    def author(self, node: dict[str, Any], node_prefix: str, contract: str = "", with_e2e: bool = True) -> list[AuthoredFile]:
        contract_part = f"Interface contract (fixed by the architect; use these routes, names and endpoints exactly):\n{contract}\n\n" if contract else ""
        user = (
            f"{contract_part}{describe_node(node)}\n\n"
            f"Name the files after the requirement id `{node_prefix}`. Write the tests now."
        )
        refusals = refusal_note(node)
        if refusals:
            user += "\n\n" + refusals
        if not with_e2e:
            user += NO_E2E_NOTE
        message = self.llm.chat(
            [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user}],
            max_tokens=16000,
            tag=f"test-author:{node_prefix}",
            thinking=AUTHOR_THINKING,
        )
        files = parse_file_blocks(str(message.get("content") or ""), self.llm.last_finish_reason == "stop")
        if not with_e2e and not files and self.llm.last_finish_reason != "length":
            return []
        if not files or self.llm.last_finish_reason == "length":
            self.log(f"[author] {node_prefix}: output incomplete ({len(files)} complete file(s), finish={self.llm.last_finish_reason}); retrying compactly")
            retry = self.llm.chat(
                [
                    {"role": "system", "content": system_prompt()},
                    {
                        "role": "user",
                        "content": user
                        + "\n\nIMPORTANT: your previous answer was cut off. Be compact: one test per scenario, at most ~120 lines per file, "
                        "shared helper functions instead of repeated form-filling code, no comments.",
                    },
                ],
                max_tokens=16000,
                tag=f"test-author-retry:{node_prefix}",
                thinking=AUTHOR_THINKING,
            )
            retried = parse_file_blocks(str(retry.get("content") or ""), self.llm.last_finish_reason == "stop")
            if len(retried) >= len(files):
                files = retried
        if not files and with_e2e:
            raise RuntimeError("test author produced no test files")
        return files

    def repair(
        self,
        node: dict[str, Any],
        node_prefix: str,
        files: list[AuthoredFile],
        problem: str,
        contract: str = "",
    ) -> list[AuthoredFile]:
        current = "\n\n".join(f"=== FILE: {f.path} ===\n{f.content}=== END FILE ===" for f in files)
        contract_part = f"Interface contract:\n{contract}\n\n" if contract else ""
        user = (
            f"{contract_part}{describe_node(node)}\n\nYour current tests:\n{current}\n\n"
            f"The implementer reports a defect in the tests themselves (not in the implementation):\n{problem}\n\n"
            "Fix ONLY what is wrong with the tests and keep the behavioural assertions. Output ONLY the files you change "
            "(each in full); files you do not output stay as they are."
        )
        message = self.llm.chat(
            [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user}],
            max_tokens=16000,
            tag=f"test-author-repair:{node_prefix}",
            thinking=AUTHOR_THINKING,
        )
        repaired = parse_file_blocks(str(message.get("content") or ""), self.llm.last_finish_reason == "stop")

        return merge_repaired(files, repaired, self.llm.log if hasattr(self.llm, "log") else print)

    def reconcile(self, node: dict[str, Any], node_prefix: str, files: list[AuthoredFile], failures: str, sources: str) -> list[AuthoredFile]:
        current = "\n\n".join(f"=== FILE: {f.path} ===\n{f.content}=== END FILE ===" for f in files)
        user = (
            f"{describe_node(node)}\n\nThe backend of the application as it is now (it passes its acceptance check):\n{sources}\n\n"
            f"Your API tests, written before the application existed:\n{current}\n\nThey fail against it:\n{failures}\n\n"
            "Align each test with the application's ACTUAL API wherever the requirement does not state the detail: paths, "
            "request payload fields, response shapes and field names, success status codes, how the data is set up (the "
            "real seed data; the db_runtime helpers run / get / all are async -- await them). Keep EVERY assertion of a fact "
            "the requirement states -- a quoted message, a status code or a field the requirement names, a value that must "
            "not be returned, data surviving a restart -- exactly as it is: when the application breaks such a fact, the "
            "test must stay red. Remove a test only when what it checks appears nowhere in the requirement and cannot be "
            "aligned. Output ONLY the files you change (each in full)."
        )
        message = self.llm.chat(
            [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user}],
            max_tokens=16000,
            tag=f"test-author-reconcile:{node_prefix}",
            thinking=AUTHOR_THINKING,
        )
        repaired = parse_file_blocks(str(message.get("content") or ""), self.llm.last_finish_reason == "stop")
        return [f for f in merge_repaired(files, repaired, self.log) if any(f is r for r in repaired)]

REQUIRED_LITERAL = re.compile(r"""["'`]([^"'`\n]{3,160})["'`]""")
STATUS_CODE = re.compile(r"(?<![\w.])([1-5]\d\d)(?!\w)")

def lost_requirement_facts(before: str, after: str, requirement: str) -> list[str]:
    lost = [text for text in dict.fromkeys(REQUIRED_LITERAL.findall(before))
            if text in requirement and text not in after]
    stated = set(STATUS_CODE.findall(requirement))
    kept_codes = set(STATUS_CODE.findall(after))
    lost += [code for code in dict.fromkeys(STATUS_CODE.findall(before)) if code in stated and code not in kept_codes]
    return list(dict.fromkeys(lost))

def merge_repaired(files: list[AuthoredFile], repaired: list[AuthoredFile], log=print) -> list[AuthoredFile]:
    merged = {f.path: f for f in files}
    for item in repaired:
        if item.path in merged:
            merged[item.path] = item
        else:
            log(f"[author] repair returned {item.path}, which is not one of this batch's test files; ignored")
    return list(merged.values())

def files_as_json(files: list[AuthoredFile]) -> str:
    return json.dumps([f.path for f in files])
