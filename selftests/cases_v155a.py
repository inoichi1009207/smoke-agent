from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("agent_main_v155a", ROOT / "main.py")
main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main)
from agent import settings as st
from agent import watchdog

def _req(text: str | None) -> Path:
    d = Path(tempfile.mkdtemp(prefix="v155a-"))
    if text is not None:
        (d / "requirements.yaml").write_text(text, encoding="utf-8")
    return d

def staged_detection():

    assert float(st._DEFAULTS["stage_run_target_sec"]) == 0
    on_saved = st.SETTINGS.get("stage_run_target_sec")
    st.SETTINGS["stage_run_target_sec"] = 14400
    try:
        _staged_detection_on()
    finally:
        st.SETTINGS["stage_run_target_sec"] = on_saved

def _staged_detection_on():
    cases = {
        "id: ROOT\nname: GitHub - Stage 1\ntype: FOLDER\ndescription: x\n": float(st.SETTINGS["stage_run_target_sec"]),
        "id: ROOT\nname: GitHub - Stage 2\ndescription: >-\n  Stage 2 of the progressive GitHub task covers x.\n": float(st.SETTINGS["stage_run_target_sec"]),
        "id: ROOT\nname: GitHub - Stage 3\ndescription: Stage 3 of the progressive task.\n": None,
        "id: ROOT\nname: Core Requirements for an Online Spreadsheet\ndescription: sheets\n": None,
        "id: ROOT\nname: GitHub Collaboration Platform Core Requirements\n": None,

        "id: ROOT\nname: Sheets\ndescription: An ordinary cell may contain Stage 1.\n": None,
        "id: ROOT\nname: GitHub\ndescription: All features of Stage 1, Stage 2 and Stage 3 are included.\n": None,
        "id: ROOT\nname: Stage 1 planning tool\ndescription: x\n": None,
        ": : not yaml [": None,
        None: None,
    }
    for text, want in cases.items():
        d = _req(text)
        try:
            got = main.staged_run_target(d)
            assert got == want, (text, got, want)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    saved = st.SETTINGS.get("stage_run_target_sec")
    st.SETTINGS["stage_run_target_sec"] = 0
    d = _req("id: ROOT\nname: GitHub - Stage 1\n")
    try:
        assert main.staged_run_target(d) is None
    finally:
        st.SETTINGS["stage_run_target_sec"] = saved
        shutil.rmtree(d, ignore_errors=True)

def move_end_only_pulls_in():
    import time
    dog = watchdog.Watchdog(83700.0, started_mono=time.monotonic(), started_wall=time.time(), log=lambda m: None)
    before = dog.end_wall
    assert dog.move_end(time.time() + 100.0) is True and dog.end_wall < before
    later = dog.end_wall
    assert dog.move_end(time.time() + 999999.0) is False and dog.end_wall == later

CASES = [staged_detection, move_end_only_pulls_in]

def main_() -> int:
    failed = 0
    for case in CASES:
        try:
            case()
            print(f"ok    {case.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {case.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} v155a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main_())
