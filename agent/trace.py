from __future__ import annotations

from pathlib import Path
from typing import Any

from arcbench_agent_runtime import AgentRuntime

class Trace:
    def __init__(self, project_dir: Path, log=print) -> None:
        self.log = log
        self.runtime: AgentRuntime | None = None
        try:
            self.runtime = AgentRuntime.from_env(project_dir=str(project_dir))
        except Exception as exc:
            self.log(f"[trace] runtime unavailable: {exc}")

    def _call(self, label: str, func, *args, **kwargs) -> Any:
        if self.runtime is None:
            return None
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            self.log(f"[trace] {label} failed: {exc}")
            return None

    def start(self, tree: dict[str, Any]) -> None:
        if self.runtime is None:
            return
        self._call("init_store", self.runtime.traceability.init_store)
        self._call("store_requirement_tree", self.runtime.traceability.store_requirement_tree, tree)
        self._call("run_started", self.runtime.events.mark_run_started, "smoke-agent started")

    def finish(self, ok: bool, message: str) -> None:
        if self.runtime is None:
            return
        if ok:
            self._call("run_completed", self.runtime.events.mark_run_completed, message)
        else:
            self._call("run_failed", self.runtime.events.mark_run_failed, message)

    def design_started(self, req_id: str) -> None:
        self._call("design_started", self.runtime.events.mark_design_started, req_id, "test author writing tests") if self.runtime else None

    def design_done(self, req_id: str) -> None:
        self._call("design_done", self.runtime.events.mark_design_done, req_id, "tests written (red)") if self.runtime else None

    def implement_started(self, req_id: str) -> None:
        self._call("implement_started", self.runtime.events.mark_implementation_started, req_id, "implementer working") if self.runtime else None

    def implement_done(self, req_id: str) -> None:
        self._call("implement_done", self.runtime.events.mark_implementation_done, req_id, "implementation written") if self.runtime else None

    def tests_result(self, req_id: str, passed: bool, message: str) -> None:
        if self.runtime is None:
            return
        if passed:
            self._call("test_passed", self.runtime.events.mark_test_passed, req_id, message)
        else:
            self._call("test_failed", self.runtime.events.mark_test_failed, req_id, message)

    def node_state(self, req_id: str, state: str, phase: str | None = None) -> None:
        self._call("node_state", self.runtime.traceability.upsert_node_state, req_id, state, phase) if self.runtime else None

    def register_test(self, test_id: str, req_id: str, kind: str, file_path: str, passed: bool | None, first_line: int | None = None) -> None:
        if self.runtime is None:
            return
        self._call(
            "upsert_test",
            self.runtime.traceability.upsert_test,
            test_id=test_id,
            req_id=req_id,
            type=kind,
            file_path=file_path,
            first_line=str(first_line) if first_line else None,
            passed=passed,
        )

    def set_test_status(self, test_id: str, passed: bool | None) -> None:
        self._call("set_test_pass_status", self.runtime.traceability.set_test_pass_status, test_id, passed) if self.runtime else None

    def register_interface(self, interface_id: str, req_id: str, kind: str, content: str, file_path: str, implemented: bool) -> None:
        if self.runtime is None:
            return
        self._call(
            "upsert_interface",
            self.runtime.traceability.upsert_interface,
            interface_id=interface_id,
            req_ids=[req_id],
            type=kind,
            content=content,
            file_path=file_path,
            implemented=implemented,
        )

    def git_init(self) -> None:
        self._call("git ensure_repo", self.runtime.git.ensure_repo, create_initial_commit=True) if self.runtime else None

    def git_commit(self, message: str) -> None:
        self._call("git commit", self.runtime.git.commit, message) if self.runtime else None

def interface_kind_for(path: str) -> str:
    if path.startswith("backend/src/database"):
        return "db"
    if path.startswith("backend/"):
        return "api"
    if path.startswith("frontend/"):
        return "ui"
    return "func"
