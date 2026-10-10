from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"
IS_WINDOWS = os.name == "nt"
NPM = "npm.cmd" if IS_WINDOWS else "npm"
PORT = 3000

def template_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(TEMPLATE.rglob("*")):
        if path.is_file() and "node_modules" not in path.parts and "dist" not in path.parts:
            digest.update(path.relative_to(TEMPLATE).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()

def run(args: list[str], cwd: Path, timeout: int) -> None:
    started = time.time()
    done = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=IS_WINDOWS)
    print(f"  {' '.join(args)} in {cwd.name}: exit {done.returncode} after {time.time() - started:.0f}s")
    if done.returncode != 0:
        raise SystemExit("SMOKE FAILED\n" + (done.stdout or "")[-1500:] + (done.stderr or "")[-1500:])

def port_is_free() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
    except urllib.error.HTTPError:
        return False
    except Exception:
        return True
    return False

def main() -> int:
    stamp = Path(tempfile.gettempdir()) / "arc-agent-template-smoke.ok"
    current = template_hash()
    if "--force" not in sys.argv and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == current:
        print("template smoke: unchanged template, already verified (use --force to run again)")
        return 0
    if not port_is_free():
        raise SystemExit(f"SMOKE FAILED: something already answers on 127.0.0.1:{PORT}; the platform's start contract cannot be tested")
    work = Path(tempfile.mkdtemp(prefix="arc-template-smoke-"))
    server = None
    try:
        shutil.copytree(TEMPLATE, work / "template", ignore=shutil.ignore_patterns("node_modules", "dist"))
        app = work / "template"
        for folder in ("frontend", "backend"):
            if not (app / folder / "package.json").is_file():
                raise SystemExit(f"SMOKE FAILED: {folder}/package.json is missing from the template")
        install = [NPM, "install", "--include=optional", "--no-audit", "--no-fund"]
        run(install, app / "frontend", 900)
        run([NPM, "run", "build"], app / "frontend", 600)
        run(install, app / "backend", 900)
        env = {**os.environ, "HOST": "0.0.0.0", "PORT": str(PORT)}
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
        server = subprocess.Popen([NPM, "run", "start"], cwd=str(app / "backend"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=IS_WINDOWS, **kwargs)
        started = time.time()
        status = None
        while time.time() - started < 120:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as response:
                    status = response.status
            except urllib.error.HTTPError as exc:
                status = exc.code
            except Exception:
                status = None
            if status is not None:
                break
            time.sleep(1)
        print(f"  HOST=0.0.0.0 PORT={PORT} npm run start: first answer {status} after {time.time() - started:.0f}s")
        if status is None or status >= 500:
            raise SystemExit("SMOKE FAILED: the started template does not answer below 500 within 120 seconds")
        stamp.write_text(current, encoding="utf-8")
        print("template smoke: install, build, start and first answer all as the platform does them -- OK")
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
