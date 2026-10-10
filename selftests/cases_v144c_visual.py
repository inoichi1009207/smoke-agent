from __future__ import annotations

import base64
import copy
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import visual

CASES: list = []

def case(function):
    CASES.append(function)
    return function

class FakeLLM:

    def __init__(self, fail: tuple[str, ...] = (), delay: float = 0.3) -> None:
        self.model = "fake-model"
        self.lines: list[str] = []
        self.log = self.lines.append
        self.fail = fail
        self.delay = delay
        self.lock = threading.Lock()
        self.state = {"active": 0, "max": 0, "chats": 0}
        self.read: dict[str, bytes] = {}
        self.usage_calls = 0
        self.usage_threads: set[str] = set()

    def chat(self, messages, **kw):
        tag = str(kw.get("tag") or "")
        name = tag.split(":", 1)[1]
        url = messages[0]["content"][1]["image_url"]["url"]
        with self.lock:
            self.state["active"] += 1
            self.state["chats"] += 1
            self.state["max"] = max(self.state["max"], self.state["active"])
            self.read[name] = base64.b64decode(url.split(",", 1)[1])
        try:
            time.sleep(self.delay)
            if name in self.fail:
                raise RuntimeError(f"vision refused {name}")
            self._record_usage({"prompt_tokens": 10, "completion_tokens": 5}, tag)
            return {"content": f"HEADINGS:\n- Title of {name}\n- Settings\nBUTTONS:\n- Save\nLAYOUT:\n- a form for {name}"}
        finally:
            with self.lock:
                self.state["active"] -= 1

    def _record_usage(self, usage, tag):

        value = self.usage_calls
        time.sleep(0.01)
        self.usage_calls = value + 1
        self.usage_threads.add(threading.current_thread().name)

def _workspace(images: dict[str, bytes]) -> tuple[Path, Path, Path]:
    base = Path(tempfile.mkdtemp(prefix="v144c_"))
    requirements = base / "requirements"
    project = base / "project"
    requirements.mkdir()
    project.mkdir()
    for rel, data in images.items():
        target = requirements / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return base, requirements, project

def _tree(refs_per_node: list[list[str]]) -> dict:
    children = [
        {"name": f"Page {i}", "description": f"The page {i} has a form with `Settings` and a `Save` button.\n" + "\n".join(f"![shot]({r})" for r in refs), "children": []}
        for i, refs in enumerate(refs_per_node)
    ]

    children[1]["children"] = children[2:]
    return {"name": "App", "description": "The app. `Settings` `Save`", "children": children[:2]}

def _descriptions(node: dict) -> list[str]:
    out = [node["description"]]
    for child in node.get("children") or []:
        out.extend(_descriptions(child))
    return out

def _run(tree, requirements, project, llm, workers=None):
    saved = visual.VISUAL_WORKERS
    if workers is not None:
        visual.VISUAL_WORKERS = workers
    try:
        return visual.attach_visual_notes(tree, requirements, project, llm, log=llm.log)
    finally:
        visual.VISUAL_WORKERS = saved

@case
def v144c_six_images_described_concurrently_same_as_serial():
    names = [f"{c}.png" for c in "abcdef"]
    images = {n: f"png-{n}".encode() for n in names}
    refs = [["a.png", "./b.png"], ["/c.png"], ["reference/d.png", "e.png"], ["f.png", "a.png"]]
    base, requirements, project = _workspace(images)
    try:

        serial_tree = _tree(refs)
        serial_llm = FakeLLM()
        serial_project = base / "serial"
        serial_project.mkdir()
        serial_count = _run(serial_tree, requirements, serial_project, serial_llm, workers=1)
        assert serial_llm.state["max"] == 1, serial_llm.state
        assert serial_count == 6, serial_count

        tree = _tree(refs)
        llm = FakeLLM()
        started = time.monotonic()
        count = _run(tree, requirements, project, llm)
        elapsed = time.monotonic() - started
        assert 1 < llm.state["max"] <= 4, f"max concurrency {llm.state['max']} (bound {visual.VISUAL_WORKERS})"
        assert count == 6, count
        assert llm.state["chats"] == 6, llm.state
        assert _descriptions(tree) == _descriptions(serial_tree)
        assert all("Visual reference notes for" in d for d in _descriptions(tree)[1:]), _descriptions(tree)
        for n in names:
            cache = project / ".arc" / "design" / "visual" / (Path(n).stem + ".md")
            assert cache.is_file(), cache
            assert f"Title of {n}" in cache.read_text(encoding="utf-8")

        assert llm.usage_calls == 6, llm.usage_calls
        assert elapsed < 6 * llm.delay, elapsed
    finally:
        shutil.rmtree(base, ignore_errors=True)

@case
def v144c_same_name_twice_described_once_first_path_wins():
    images = {"reference/x.png": b"from-reference", "x.png": b"from-root", "y.png": b"y", "z.png": b"z"}
    base, requirements, project = _workspace(images)
    try:
        tree = {"name": "App", "description": "App", "children": [
            {"name": "One", "description": "![one](reference/x.png)\n![y](y.png)", "children": []},
            {"name": "Two", "description": "![two](x.png)\n![z](z.png)", "children": []},
        ]}
        llm = FakeLLM()
        count = _run(tree, requirements, project, llm)
        assert count == 3, count
        assert llm.state["chats"] == 3, llm.state
        assert llm.read["x.png"] == b"from-reference", llm.read
        one, two = tree["children"][0]["description"], tree["children"][1]["description"]
        assert one.count("Visual reference notes for x.png") == 1 and two.count("Visual reference notes for x.png") == 1, (one, two)
        assert "Visual reference notes for y.png" in one and "Visual reference notes for z.png" in two
        assert one.index("notes for x.png") < one.index("notes for y.png")
    finally:
        shutil.rmtree(base, ignore_errors=True)

@case
def v144c_cached_image_makes_no_chat_call():
    base, requirements, project = _workspace({"a.png": b"a", "b.png": b"b"})
    try:
        cache_dir = project / ".arc" / "design" / "visual"
        cache_dir.mkdir(parents=True)
        (cache_dir / "a.md").write_text("HEADINGS:\n- Cached heading\nLAYOUT:\n- cached", encoding="utf-8")
        tree = {"name": "App", "description": "![a](a.png)", "children": [{"name": "B", "description": "![b](b.png) ![a](a.png)", "children": []}]}
        llm = FakeLLM()
        count = _run(tree, requirements, project, llm)
        assert count == 2, count
        assert set(llm.read) == {"b.png"}, llm.read
        assert llm.state["chats"] == 1, llm.state
        assert "Visual reference notes for a.png" in tree["description"]

        llm2 = FakeLLM()
        tree2 = {"name": "App", "description": "![a](a.png) ![b](b.png)", "children": []}
        assert _run(tree2, requirements, project, llm2) == 2
        assert llm2.state["chats"] == 0, llm2.state
    finally:
        shutil.rmtree(base, ignore_errors=True)

@case
def v144c_broken_worker_yields_no_notes_and_others_continue():
    names = ["ok1.png", "bad1.png", "bad2.png", "ok2.png", "ok3.png"]
    base, requirements, project = _workspace({n: n.encode() for n in names})
    original = visual.describe_image

    def raising(llm, image, cache_dir, log=print):
        if image.name == "bad2.png":
            raise OSError("disk full while writing the cache")
        return original(llm, image, cache_dir, log)

    try:
        visual.describe_image = raising
        tree = {"name": "App", "description": "", "children": [
            {"name": n, "description": f"![s]({n})", "children": []} for n in names
        ]}
        llm = FakeLLM(fail=("bad1.png",))
        count = _run(tree, requirements, project, llm)
        assert count == 3, count
        for child in tree["children"]:
            has = "Visual reference notes for" in child["description"]
            assert has == child["name"].startswith("ok"), child
        assert any("bad2.png: description failed" in line and "OSError" in line for line in llm.lines), llm.lines
        assert any("bad1.png" in line and "cannot read it" in line for line in llm.lines), llm.lines
        cache_dir = project / ".arc" / "design" / "visual"
        assert not (cache_dir / "bad1.md").exists() and not (cache_dir / "bad2.md").exists()
        assert all((cache_dir / f"ok{i}.md").is_file() for i in (1, 2, 3))
    finally:
        visual.describe_image = original
        shutil.rmtree(base, ignore_errors=True)

@case
def v144c_worker_llm_is_a_copy_reporting_to_the_shared_one():
    llm = FakeLLM(delay=0)
    worker = visual._worker_llm(llm)
    assert worker is not llm and worker.model == llm.model
    worker._record_usage({"prompt_tokens": 1}, "t")
    assert llm.usage_calls == 1
    assert copy.copy(llm).usage_calls == 1
