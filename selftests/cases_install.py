from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

from agent import verify

CASES = []
E404 = ("npm error code E404\nnpm error 404 Not Found - GET "
        "https://repo.huaweicloud.com/repository/npm/std-env/-/std-env-4.3.0.tgz\n")
VITE_127 = "> frontend@0.0.0 build\n> vite build\n\nsh: 1: vite: not found\n"

def case(function):
    CASES.append(function)
    return function

class Fake:

    def __init__(self, project: Path, installs: list[bool], builds: list[tuple[int, str]] | None = None, leave: bool = False) -> None:

        self.project, self.installs, self.builds, self.leave = project, list(installs), list(builds or []), leave
        self.calls: list[tuple[str, list[str]]] = []
        self.now = 1000.0
        self.slept: list[float] = []

    def run_cmd(self, args, *, cwd, timeout=600, env=None):
        cwd = Path(cwd)
        self.calls.append((cwd.name, list(args[1:])))
        if args[1] == "install":
            ok = self.installs.pop(0) if self.installs else False
            if ok or self.leave:
                manifest = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
                for name in [*manifest.get("dependencies", {}), *manifest.get("devDependencies", {})]:
                    (cwd / "node_modules" / name).mkdir(parents=True, exist_ok=True)
                    (cwd / "node_modules" / name / "package.json").write_text("{}", encoding="utf-8")
            if ok:
                return 0, "added 365 packages"
            return 1, E404
        if args[1:3] == ["run", "build"]:
            return self.builds.pop(0) if self.builds else (0, "built")
        raise AssertionError(f"unexpected command {args}")

    def installs_made(self, package: str) -> list[list[str]]:
        return [args for where, args in self.calls if where == package and args[0] == "install"]

    def __enter__(self):
        self.saved = (verify.run_cmd, verify._now)
        verify.run_cmd = self.run_cmd
        verify._now = lambda: self.now
        verify._INSTALL_STATE.clear()
        return self

    def __exit__(self, *exc):
        verify.run_cmd, verify._now = self.saved
        verify._INSTALL_STATE.clear()
        return False

def project(tmp: str) -> Path:
    root = Path(tmp)
    for package, deps in (("frontend", {"vite": "^7.2.4", "vitest": "^4.0.16"}), ("backend", {"express": "^5.2.1"})):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "package.json").write_text(json.dumps({"name": package, "devDependencies": deps}), encoding="utf-8")
    return root

@case
def start_install_tries_three_times_then_reports():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False, False, False]) as fake:
        lines: list[str] = []
        step = verify.install_with_retries(fake.project, "frontend", log=lines.append, sleep=fake.slept.append)
        assert not step.ok and "E404" in step.output
        assert len(fake.installs_made("frontend")) == 3 and fake.slept == [30.0, 30.0]
        assert any("FAILED three times" in line for line in lines)

@case
def start_install_stops_at_first_success():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False, True]) as fake:
        step = verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)
        assert step.ok and len(fake.installs_made("frontend")) == 2
        assert verify.missing_dependencies(fake.project, "frontend") == []

@case
def a_failed_start_is_retried_before_a_later_build_paced_at_ten_minutes():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False, False, False, False, True]) as fake:
        verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)
        assert verify.missing_dependencies(fake.project, "frontend") == ["vite", "vitest"]
        fake.now += 120
        note = verify.ensure_installed(fake.project, "frontend", log=lambda m: None)
        assert len(fake.installs_made("frontend")) == 3 and "INSTALL PROBLEM, NOT A CODE PROBLEM" in note
        fake.now += 600
        verify.ensure_installed(fake.project, "frontend", log=lambda m: None)
        assert len(fake.installs_made("frontend")) == 4
        fake.now += 300
        verify.ensure_installed(fake.project, "frontend", log=lambda m: None)
        assert len(fake.installs_made("frontend")) == 4
        fake.now += 400
        assert verify.ensure_installed(fake.project, "frontend", log=lambda m: None) == ""
        made = fake.installs_made("frontend")
        assert len(made) == 5 and "--prefer-offline" in made[-1] and "--prefer-offline" not in made[-2]
        fake.now += 3600
        assert verify.ensure_installed(fake.project, "frontend", log=lambda m: None) == ""
        assert len(fake.installs_made("frontend")) == 5

@case
def later_installs_are_bounded():

    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False] * 50) as fake:
        lines: list[str] = []
        for _ in range(20):
            fake.now += 10_000
            verify.ensure_installed(fake.project, "backend", log=lines.append)
            if len(fake.installs_made("backend")) == verify.INSTALL_RETRY_LIMIT:
                break
        assert len(fake.installs_made("backend")) == verify.INSTALL_RETRY_LIMIT
        for _ in range(18):
            fake.now += 600
            verify.ensure_installed(fake.project, "backend", log=lines.append)
        assert len(fake.installs_made("backend")) == verify.INSTALL_RETRY_LIMIT + 18 * 600 // int(verify.INSTALL_SLOW_INTERVAL)
        assert sum("used up" in line for line in lines) == 1

@case
def the_registry_is_never_switched():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False] * 50) as fake:
        verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)
        for _ in range(10):
            fake.now += 10_000
            verify.ensure_installed(fake.project, "frontend", log=lambda m: None)
        assert fake.calls and not any(arg.startswith("--registry") or "registry" in arg for _, args in fake.calls for arg in args)

@case
def installed_project_never_installs_again():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [True]) as fake:
        verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)
        fake.now += 10_000
        step = verify.build_frontend(fake.project, log=lambda m: None)
        assert step.ok and len(fake.installs_made("frontend")) == 1

@case
def build_exit_127_installs_once_and_builds_again():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False, False, False, True], [(127, VITE_127), (0, "built")]) as fake:
        verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)

        for name in ("vite", "vitest"):
            (fake.project / "frontend" / "node_modules" / name).mkdir(parents=True)
            (fake.project / "frontend" / "node_modules" / name / "package.json").write_text("{}", encoding="utf-8")
        fake.now += 150
        step = verify.build_frontend(fake.project, log=lambda m: None)
        assert step.ok and len(fake.installs_made("frontend")) == 4 and step.infra == ""
        assert [args for _, args in fake.calls if args[0] == "run"] == [["run", "build"], ["run", "build"]]

@case
def build_that_still_misses_its_program_says_it_is_not_the_code():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False] * 10, [(127, VITE_127)] * 5) as fake:
        verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)
        fake.now += 30
        step = verify.build_frontend(fake.project, log=lambda m: None)
        assert not step.ok and "vite: not found" in step.output
        assert step.output.rstrip().endswith("the factory retries the install at most every 10 minutes.")
        assert len(fake.installs_made("frontend")) == 3
        assert step.infra == "frontend: a program the command runs was not found"

@case
def start_retries_really_run_after_a_failure_that_left_node_modules():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False, False, True], leave=True) as fake:
        lines: list[str] = []
        step = verify.install_with_retries(fake.project, "frontend", log=lines.append, sleep=fake.slept.append)
        assert step.ok and not step.skipped and len(fake.installs_made("frontend")) == 3
        assert verify.install_complete(fake.project / "frontend") and verify.missing_dependencies(fake.project, "frontend") == []
        assert not any("skipping npm install" in line for line in lines)

@case
def a_folder_left_by_failed_installs_stays_a_failed_install():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False] * 10, leave=True) as fake:
        step = verify.install_with_retries(fake.project, "frontend", log=lambda m: None, sleep=fake.slept.append)
        assert not step.ok and len(fake.installs_made("frontend")) == 3
        assert not verify.install_complete(fake.project / "frontend")
        assert verify.missing_dependencies(fake.project, "frontend") == [verify.NOT_COMPLETED]
        fake.now += 700
        note = verify.ensure_installed(fake.project, "frontend", log=lambda m: None)
        assert len(fake.installs_made("frontend")) == 4 and "INSTALL PROBLEM, NOT A CODE PROBLEM" in note
        assert verify.NOT_COMPLETED in note

        step = verify.npm_install(fake.project, "frontend", log=lambda m: None)
        assert not step.skipped and len(fake.installs_made("frontend")) == 5

@case
def the_marker_is_removed_before_an_install_and_written_only_on_exit_0():
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [True, False], leave=True) as fake:
        assert verify.npm_install(fake.project, "backend", log=lambda m: None).ok
        assert verify.install_complete(fake.project / "backend")
        assert verify.npm_install(fake.project, "backend", log=lambda m: None).skipped
        assert not verify.npm_install(fake.project, "backend", log=lambda m: None, force=True).ok
        assert not verify.install_complete(fake.project / "backend")

@case
def program_missing_reads_the_platform_lines():
    assert verify.program_missing(127, "")
    assert verify.program_missing(1, VITE_127)
    assert verify.program_missing(1, "'vite' is not recognized as an internal or external command,\n")
    assert verify.program_missing(1, "bash: vite: command not found\n")
    assert not verify.program_missing(1, "error during build:\n[vite]: Rollup failed to resolve import \"x\" from \"src/App.tsx\".\n")
    assert not verify.program_missing(1, "Error: Module not found\n")
    assert not verify.program_missing(1, "GET /api/items -> 404 Not Found\n")

@case
def missing_dependencies_claims_nothing_without_a_readable_package_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert verify.missing_dependencies(root, "frontend") == []
        (root / "frontend").mkdir()
        (root / "frontend" / "package.json").write_text("{not json", encoding="utf-8")
        assert verify.missing_dependencies(root, "frontend") == []

@case
def probe_without_backend_dependencies_gives_no_start_verdict():
    class Process:
        def poll(self):
            return 1

    class Server:
        def __init__(self, *args, **kwargs):
            self.process = Process()

        def stop(self):
            return "Error: Cannot find module 'express'\n"

    saved = (verify.BackgroundProcess, verify.wait_http)
    with tempfile.TemporaryDirectory() as tmp, Fake(project(tmp), [False] * 10) as fake:
        verify.BackgroundProcess = Server
        verify.wait_http = lambda *args, **kwargs: (False, "connection refused")
        try:
            verify.install_with_retries(fake.project, "backend", log=lambda m: None, sleep=fake.slept.append)
            step, _ = verify.probe_server(fake.project, log=lambda m: None, port=45123)
            assert not step.ok and step.started is None and "INSTALL PROBLEM" in step.output

            for name in ("express",):
                (fake.project / "backend" / "node_modules" / name).mkdir(parents=True)
                (fake.project / "backend" / "node_modules" / name / "package.json").write_text("{}", encoding="utf-8")
            verify.mark_install_complete(fake.project / "backend")
            step, _ = verify.probe_server(fake.project, log=lambda m: None, port=45123)
            assert step.started is False and "INSTALL PROBLEM" not in step.output
        finally:
            verify.BackgroundProcess, verify.wait_http = saved

@case
def factory_install_failed_clears_once_installed():
    import main

    with tempfile.TemporaryDirectory() as tmp:
        factory = main.Factory.__new__(main.Factory)
        factory.project_dir = project(tmp)
        factory.install_failed = False
        assert factory.install_failed is False
        factory.install_failed = True
        assert factory.install_failed is True
        for package, names in (("frontend", ("vite", "vitest")), ("backend", ("express",))):
            for name in names:
                (factory.project_dir / package / "node_modules" / name).mkdir(parents=True)
                (factory.project_dir / package / "node_modules" / name / "package.json").write_text("{}", encoding="utf-8")
        assert factory.install_failed is True
        for package in ("frontend", "backend"):
            verify.mark_install_complete(factory.project_dir / package)
        assert factory.install_failed is False

@case
def main_start_install_goes_through_the_retrying_helper():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "install_with_retries(self.project_dir, package, log=log)" in source
    assert "time.sleep(30)" not in source

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
