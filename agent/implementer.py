from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .conventions import PROJECT_CONVENTIONS
from .fsutil import PathError, list_tree, read_text, safe_path, write_text
from .llm import LLM, assistant_message_for_history
from .settings import IMPLEMENTER_THINKING
from .test_author import describe_node
from .testrun import TestOutcome

RESET_PROMPT_TOKENS = 30000
WRITE_PLACEHOLDER = "<content written; call read_file if you need it again>"
STALE_PLACEHOLDER = "<earlier output omitted; superseded by a newer call>"

SYSTEM_PROMPT = f"""You are the IMPLEMENTER agent of a test-driven software factory.
Executable tests for one requirement already exist and currently fail. Change the application code until they pass.

{PROJECT_CONVENTIONS}
Working rules:
- Work in small steps: read what you need, write files, then call run_tests. Repeat until run_tests reports ALL TESTS PASSED.
- run_tests may also build the frontend and run the Playwright spec against the real server; treat its output as the truth.
- You cannot edit test files. If a test is genuinely broken (syntax/import error, contradicts the requirement), call
  report_test_defect with a precise explanation instead of working around it.
- Never write code whose only purpose is to satisfy a mock or a test double; use the real modules (axios client, fetch, DB).
- Keep the existing project structure and tooling; do not add dependencies or change configs unless unavoidable.
- Keep the code simple and readable; no styling work beyond what the requirement asks.
- Write complete files (write_file replaces the whole file).
- Be economical: read only the source files you will change or call (usually 2-4 files); do not read package.json,
  configs, node_modules or docs unless an error message points there; do not re-read files you already have; do not narrate.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a project file (path relative to the project root).",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or fully replace a project file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files under a project directory (node_modules etc. hidden).",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "directory, default project root"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the checks of the current requirement (unit tests, and when applicable build + Playwright) and return the result.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_test_defect",
            "description": "Stop and report that a test file itself is defective (not the implementation).",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Declare the work finished (only after run_tests passed).",
            "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
        },
    },
]

@dataclass
class ImplementResult:
    status: str
    written: list[str]
    detail: str = ""
    test_runs: int = 0

class Implementer:
    def __init__(
        self,
        llm: LLM,
        project_dir: Path,
        run_tests: Callable[[], TestOutcome],
        *,
        max_steps: int = 40,
        max_test_runs: int = 8,
        deadline: float | None = None,
        log=print,
    ) -> None:
        self.llm = llm
        self.project_dir = project_dir
        self.run_tests = run_tests
        self.max_steps = max_steps
        self.max_test_runs = max_test_runs
        self.deadline = deadline
        self.log = log

    def _initial_user(self, node: dict[str, Any], test_files: dict[str, str], outcome: TestOutcome, written: list[str], extra: str) -> str:
        tests_text = "\n\n".join(f"### {path}\n{content}" for path, content in test_files.items())
        tree = list_tree(self.project_dir, "frontend/src") + "\n" + list_tree(self.project_dir, "backend/src")
        parts = [describe_node(node)]
        if tests_text:
            parts.append(f"Tests to satisfy (read-only):\n{tests_text}")
        parts.append(f"Current source files:\n{tree}")
        if written:
            parts.append("Files you already wrote in this session (read_file to see them): " + ", ".join(written))
        parts.append(f"Current check result:\n{outcome.summary}")
        if extra:
            parts.append(extra)
        return "\n\n".join(parts).strip()

    def implement(
        self,
        node: dict[str, Any],
        test_files: dict[str, str],
        first_outcome: TestOutcome,
        extra_context: str = "",
    ) -> ImplementResult:
        protected = set(test_files)
        written: list[str] = []
        test_runs = 0
        last_outcome = first_outcome
        node_id = str(node.get("id"))

        def fresh_messages() -> list[dict[str, Any]]:
            return [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._initial_user(node, test_files, last_outcome, written, extra_context)},
            ]

        messages = fresh_messages()
        nudges = 0
        steps_without_progress = 0
        for step in range(1, self.max_steps + 1):
            if self.deadline is not None and time.time() > self.deadline:
                return ImplementResult("budget", written, "node deadline reached", test_runs)
            message = self.llm.chat(messages, tools=TOOLS, max_tokens=8192, tag=f"implementer:{node_id}:{step}", thinking=IMPLEMENTER_THINKING)
            messages.append(assistant_message_for_history(message))
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                if last_outcome.passed:
                    return ImplementResult("passed", written, str(message.get("content") or ""), test_runs)
                nudges += 1
                if nudges > 2:
                    return ImplementResult("gave_up", written, str(message.get("content") or ""), test_runs)
                messages.append({"role": "user", "content": "Checks are not green yet. Continue with tool calls (edit files, then run_tests)."})
                continue
            progressed = any(c.get("function", {}).get("name") in ("write_file", "run_tests") for c in tool_calls)
            steps_without_progress = 0 if progressed else steps_without_progress + 1
            for call in tool_calls:
                name = call.get("function", {}).get("name", "")
                try:
                    arguments = json.loads(call.get("function", {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                self.log(f"[implementer] step {step}: {name}({self._brief(arguments)})")
                result_text = ""
                if name == "read_file":
                    result_text = self._read(arguments.get("path", ""))
                elif name == "list_files":
                    result_text = self._list(arguments.get("path", ""))
                elif name == "write_file":
                    result_text = self._write(arguments.get("path", ""), arguments.get("content", ""), protected, written)
                elif name == "run_tests":
                    test_runs += 1
                    if test_runs > self.max_test_runs:
                        return ImplementResult("budget", written, "test run budget exhausted", test_runs)
                    last_outcome = self.run_tests()
                    self.log(f"[implementer] run_tests #{test_runs}: {'PASSED' if last_outcome.passed else 'failing'}")
                    if last_outcome.passed:
                        return ImplementResult("passed", written, "checks green", test_runs)
                    result_text = last_outcome.summary
                elif name == "report_test_defect":
                    return ImplementResult("test_defect", written, str(arguments.get("reason", "")), test_runs)
                elif name == "finish":
                    if last_outcome.passed:
                        return ImplementResult("passed", written, str(arguments.get("summary", "")), test_runs)
                    result_text = "Cannot finish: the last run_tests result was not green. Fix the code and call run_tests."
                else:
                    result_text = f"unknown tool {name}"
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result_text})

            if steps_without_progress >= 3:
                messages.append({"role": "user", "content": "You have explored enough. Stop reading; write the implementation files now, then call run_tests."})
                steps_without_progress = 0

            self._compact(messages, keep_last_assistant=True)
            if self.llm.last_prompt_tokens > RESET_PROMPT_TOKENS:
                self.log(f"[implementer] prompt reached {self.llm.last_prompt_tokens} tokens; restarting conversation from a state summary")
                messages = fresh_messages()
        return ImplementResult("budget", written, "step budget exhausted", test_runs)

    @staticmethod
    def _compact(messages: list[dict[str, Any]], *, keep_last_assistant: bool) -> None:
        last_assistant = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        call_info: dict[str, tuple[str, str]] = {}
        latest_for_path: dict[str, str] = {}
        latest_run_tests: str | None = None
        for m in messages:
            if m.get("role") != "assistant":
                continue
            for call in m.get("tool_calls") or []:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                path = str(args.get("path", "")).replace("\\", "/").strip().lstrip("./")
                call_id = call.get("id", "")
                call_info[call_id] = (name, path)
                if name in ("read_file", "write_file"):
                    latest_for_path[path] = call_id
                elif name == "run_tests":
                    latest_run_tests = call_id
        for i, m in enumerate(messages):
            if keep_last_assistant and i >= last_assistant:
                continue
            if m.get("role") == "assistant":
                for call in m.get("tool_calls") or []:
                    fn = call.get("function", {})
                    if fn.get("name") == "write_file":
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            continue
                        if args.get("content") and args["content"] != WRITE_PLACEHOLDER:
                            fn["arguments"] = json.dumps({"path": args.get("path", ""), "content": WRITE_PLACEHOLDER})
            elif m.get("role") == "tool":
                name, path = call_info.get(m.get("tool_call_id", ""), ("", ""))
                content = str(m.get("content") or "")
                if content == STALE_PLACEHOLDER or len(content) <= 400:
                    continue
                if name == "run_tests" and latest_run_tests != m.get("tool_call_id"):
                    m["content"] = STALE_PLACEHOLDER
                elif name == "read_file" and latest_for_path.get(path) != m.get("tool_call_id"):
                    m["content"] = STALE_PLACEHOLDER

    def _read(self, rel: str) -> str:
        try:
            path = safe_path(self.project_dir, rel)
        except PathError as exc:
            return f"error: {exc}"
        if not path.is_file():
            return f"error: no such file: {rel}"
        return read_text(path)

    def _list(self, rel: str) -> str:
        try:
            return list_tree(self.project_dir, rel)
        except PathError as exc:
            return f"error: {exc}"

    def _write(self, rel: str, content: str, protected: set[str], written: list[str]) -> str:
        normalized = rel.replace("\\", "/").strip().lstrip("./")
        if normalized in protected or "/tests/" in f"/{normalized}" or "/test-e2e/" in f"/{normalized}":
            return "error: test files are read-only for the implementer (use report_test_defect if a test is wrong)"
        try:
            path = safe_path(self.project_dir, normalized)
        except PathError as exc:
            return f"error: {exc}"
        write_text(path, content)
        if normalized not in written:
            written.append(normalized)
        return f"wrote {normalized} ({len(content)} chars)"

    @staticmethod
    def _brief(arguments: dict[str, Any]) -> str:
        parts = []
        for key, value in arguments.items():
            text = str(value)
            parts.append(f"{key}={text[:60]!r}" + ("..." if len(text) > 60 else ""))
        return ", ".join(parts)
