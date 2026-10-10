from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"
BROWSER = Path(__file__).resolve().parent / "browser"
IS_WINDOWS = os.name == "nt"
NPM = "npm.cmd" if IS_WINDOWS else "npm"
NPX = "npx.cmd" if IS_WINDOWS else "npx"
sys.path.insert(0, str(ROOT))

def content_hash() -> str:
    from agent.outsider import HELPER_JS

    digest = hashlib.sha256(HELPER_JS.encode("utf-8"))
    for base in (TEMPLATE, BROWSER):
        for path in sorted(base.rglob("*")):
            if path.is_file() and "node_modules" not in path.parts and "dist" not in path.parts:
                digest.update(path.relative_to(base).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()

def run(args: list[str], cwd: Path, timeout: int, env: dict | None = None) -> str:
    started = time.time()
    done = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=IS_WINDOWS,
                          env=env, encoding="utf-8", errors="replace")
    print(f"  {' '.join(args[:3])} in {cwd.name}: exit {done.returncode} after {time.time() - started:.0f}s")
    if done.returncode != 0:
        raise SystemExit("BROWSER CHECKS FAILED\n" + (done.stdout or "")[-12000:] + (done.stderr or "")[-1500:])
    return done.stdout or ""

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def main() -> int:
    from agent.outsider import HELPER_JS

    stamp = Path(tempfile.gettempdir()) / "arc-agent-browser-checks.ok"
    current = content_hash()
    if "--force" not in sys.argv and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == current:
        print("browser checks: nothing changed since the last pass (use --force to run again)")
        return 0
    work = Path(tempfile.mkdtemp(prefix="arc-browser-checks-"))
    server = None
    try:
        shutil.copytree(TEMPLATE, work / "app", ignore=shutil.ignore_patterns("node_modules", "dist"))
        app = work / "app"
        shutil.copy2(BROWSER / "App.tsx", app / "frontend" / "src" / "App.tsx")
        shutil.copy2(BROWSER / "routes.js", app / "backend" / "src" / "browser_check_routes.js")
        app_js = app / "backend" / "src" / "app.js"
        source = app_js.read_text(encoding="utf-8")
        anchor = "app.get('/api/health'"
        if anchor not in source:
            raise SystemExit("BROWSER CHECKS FAILED: the template's app.js has no /api/health route to mount the test API before")
        app_js.write_text(source.replace(anchor, "require('./browser_check_routes')(app);\n" + anchor, 1), encoding="utf-8")
        install = [NPM, "install", "--include=optional", "--no-audit", "--no-fund"]
        run(install, app / "frontend", 900)
        run([NPM, "run", "build"], app / "frontend", 600)
        run(install, app / "backend", 900)
        port = free_port()
        env = {**os.environ, "PORT": str(port), "DATA_DIR": str(work / "data"), "ARC_DB_FILE": str(work / "data" / "db.sqlite")}
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
        server = subprocess.Popen([NPM, "run", "start"], cwd=str(app / "backend"), env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, shell=IS_WINDOWS, **kwargs)
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "/", timeout=3)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(1)
        checks = work / "checks"
        (checks / "tests").mkdir(parents=True)
        (checks / "e2e-support").mkdir()
        (checks / "e2e-support" / "outsider.js").write_text(HELPER_JS, encoding="utf-8")
        shutil.copy2(BROWSER / "checks.spec.js", checks / "tests" / "checks.spec.js")

        channel = os.environ.get("BROWSER_CHECKS_CHANNEL", "").strip()
        use = "{ channel: %r }" % channel if channel else "{}"
        (checks / "playwright.config.js").write_text(
            "module.exports = { testDir: './tests', timeout: 10000, workers: 1, use: %s };\n" % use, encoding="utf-8")
        (checks / "node_modules").mkdir()
        for name in ("@playwright", "playwright", "playwright-core"):
            source_dir = app / "backend" / "node_modules" / name
            if source_dir.exists():
                shutil.copytree(source_dir, checks / "node_modules" / name, symlinks=True)

        cli = checks / "node_modules" / "@playwright" / "test" / "cli.js"
        output = run(["node", str(cli), "test", "--reporter=line"], checks, 600, env={**os.environ, "APP_URL": url})
        print("  " + (output.strip().splitlines() or ["(no output)"])[-1])
        stamp.write_text(current, encoding="utf-8")
        print("browser checks: store and helper behave as ADR-082 says -- OK")
        return 0
    finally:
        if server is not None:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
            else:
                import signal

                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGTERM)
                except OSError:
                    pass
        shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    raise SystemExit(main())
