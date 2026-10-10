from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "template" / "backend" / "src" / "database"
CASES = []

def case(function):
    CASES.append(function)
    return function

def _node_modules() -> Path | None:
    for candidate in (os.environ.get("ARC_TX_NODE_MODULES", ""), str(ROOT / "template" / "backend" / "node_modules")):
        if candidate and (Path(candidate) / "sqlite3").is_dir():
            return Path(candidate)
    return None

@case
def transactions_queue_and_nested_ones_join_the_open_one():
    text = (DATABASE / "db_runtime.js").read_text(encoding="utf-8")
    assert "AsyncLocalStorage" in text and "transactionQueue" in text
    assert "if (open) {\n    return work(open.tx);" in text
    assert "store.open = false;" in text
    assert text.count("await afterOpenTransaction();") == 4

@case
def every_initialize_call_returns_the_handle():
    text = (DATABASE / "init_db.js").read_text(encoding="utf-8")
    assert "if (initPromise) {" in text and "await initPromise;\n    return getDb();" in text

@case
def overlapping_transactions_all_commit_and_rollbacks_stay_their_own():
    modules = _node_modules()
    if modules is None or shutil.which("node") is None:
        raise unittest.SkipTest("no node or no sqlite3 (set ARC_TX_NODE_MODULES)")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "src" / "database"
        work.mkdir(parents=True)
        for path in DATABASE.glob("*.js"):
            shutil.copy2(path, work / path.name)
        env = dict(os.environ, NODE_PATH=str(modules), ARC_DB_FILE=str(Path(tmp) / "db.sqlite"))
        out = subprocess.run(["node", str(ROOT / "selftests" / "node" / "tx_serial.js"), tmp], env=env,
                             capture_output=True, text=True, timeout=120).stdout
    lines = out.strip().splitlines()
    assert "parallel fulfilled,fulfilled,fulfilled,fulfilled,fulfilled" in lines, out
    assert "nested ok" in lines and "rollback boom rejected fulfilled" in lines, out
    counts = {row["k"]: row["n"] for row in json.loads(lines[-1])}
    assert counts == {"a-in": 1, "a-late": 1, "inner": 1, "outer": 1, "par": 5, "plain-in-tx": 1, "plain-outside": 1}, counts
