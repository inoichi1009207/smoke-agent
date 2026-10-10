from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES: list = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

class _LLM:
    def __init__(self, answer: str):
        self.answer, self.calls, self.last_finish_reason, self.model = answer, [], "stop", "glm-test"

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return {"content": self.answer}

SPEC_ANSWER = """=== FILE: backend/test-e2e/X.spec.js ===
const { test } = require("@playwright/test");
const { see } = require("../e2e-support/outsider");
test("X: y", async ({ page }) => { await page.goto("/"); await see(page, "y"); });
=== END FILE ==="""

RELATION = "Moving an entry shifts every reference to it; references that cannot be preserved show Broken link."
PLAIN = "Users rename an entry; the new name is shown after a reload."

YAML = f"""id: ROOT
name: Root
children:
  - id: REQ-1
    name: References shift together and dependents update
    description: "Folder text: every reference is adjusted after a move."
    children:
      - id: REQ-1-1
        name: Move entries
        description: "{RELATION}"
        scenarios: []
      - id: REQ-1-2
        name: References stay in sync on rename
        description: "{PLAIN}"
        scenarios: []
"""

def _production_batch(member: str) -> dict:
    return {"id": member, "name": f"References shift together and dependents update ({member})",
            "description": f"This batch implements these sibling requirements together:\n\n### {member}\nreferences shift together "
                           "and every dependent is adjusted",
            "members": [member], "requirement_text": YAML, "scenarios": []}

def _closing_view(member: str) -> dict:
    from agent.boilerplate import leaves_of
    from agent.description_pass import DescriptionPass
    import yaml
    leaf = next(leaf for leaf in leaves_of(yaml.safe_load(YAML)) if leaf["id"] == member)
    from types import SimpleNamespace
    owner = SimpleNamespace(whole=YAML, tools=SimpleNamespace(note_channel="pass_note"))
    return DescriptionPass.leaf_view(owner, leaf, _production_batch(member))

@case
def the_relation_block_is_on_in_a_batch_and_off_in_the_closing_pass():
    from agent import outsider
    batch = _production_batch("REQ-1-1")
    assert outsider.relation_block(batch).startswith("RELATIONS.") and outsider.review_relation_note(batch)
    view = _closing_view("REQ-1-1")
    assert str(view.get("pass_note") or "").strip()
    assert outsider.relation_sentences(view), "the leaf itself states a relation: only the pass switches it off"
    assert outsider.relation_block(view) == "" and outsider.review_relation_note(view) == ""

    assert outsider.relation_block(batch, "DESC-REQ-1-1") == "" and outsider.review_relation_note(batch, "DESC-REQ-1-1") == ""
    assert outsider.relation_block(batch, "REQ-1-1").startswith("RELATIONS.")

@case
def the_closing_pass_messages_carry_no_relations_and_the_batch_messages_do():
    from agent import outsider
    view = _closing_view("REQ-1-1")
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, view, "DESC-REQ-1-1", log=lambda *_: None)
    user = llm.calls[0][1]["content"]
    assert "RELATIONS." not in user and "BOTH ends" not in user and str(view["pass_note"]).strip()[:40] in user
    llm = _LLM("OK")
    outsider.review_acceptance(llm, view, outsider.AuthoredFile(path="p", content="x"), "DESC-REQ-1-1", log=lambda *_: None)
    assert "Report MISSING, quoting that sentence" not in llm.calls[0][1]["content"]

    batch = _production_batch("REQ-1-1")
    llm = _LLM(SPEC_ANSWER)
    outsider.author_acceptance(llm, batch, "REQ-1-1", log=lambda *_: None)
    assert "RELATIONS." in llm.calls[0][1]["content"] and "shifts every reference" in llm.calls[0][1]["content"]
    llm = _LLM("OK")
    outsider.review_acceptance(llm, batch, outsider.AuthoredFile(path="p", content="x"), "REQ-1-1", log=lambda *_: None)
    assert "Report MISSING, quoting that sentence" in llm.calls[0][1]["content"]

@case
def a_production_batch_is_scanned_by_its_member_leaves_not_by_its_name_or_rendered_text():
    from agent import outsider
    plain = _production_batch("REQ-1-2")
    assert "_leaves" not in plain
    assert [leaf["id"] for leaf in outsider.member_leaves(plain)] == ["REQ-1-2"]
    assert outsider.relation_sentences(plain) == [], outsider.relation_sentences(plain)
    assert outsider.relation_block(plain) == "" and outsider.review_relation_note(plain) == ""
    related = _production_batch("REQ-1-1")
    assert outsider.relation_sentences(related)[0] == ("REQ-1-1", "Moving an entry shifts every reference to it;")
    both = dict(related, members=["REQ-1-1", "REQ-1-2"], id="REQ-1-1+1")
    assert sorted({leaf for leaf, _s in outsider.relation_sentences(both)}) == ["REQ-1-1"]

@case
def without_a_parsable_requirement_the_leaves_travel_along_and_nothing_else_is_read():
    from agent import outsider
    leaf = {"id": "REQ-9", "name": "References shift together", "description": PLAIN, "scenarios": []}
    node = {"id": "REQ-9", "name": "References shift together (REQ-9)", "description": "references shift together",
            "members": ["REQ-9"], "_leaves": [leaf], "requirement_text": "not: [yaml"}
    assert outsider.member_leaves(node) == [leaf] and outsider.relation_sentences(node) == []
    leaf["description"] = RELATION
    assert outsider.relation_sentences(node)
    bare = {"id": "REQ-9", "name": "x", "description": RELATION, "members": ["REQ-9"]}
    assert outsider.member_leaves(bare) == [] and outsider.relation_sentences(bare) == []

@case
def the_browser_checks_pin_the_v142_helper_and_run_in_the_agents_browser():
    spec = (ROOT / "selftests" / "browser" / "checks.spec.js").read_text(encoding="utf-8")
    for block in ("v142 first look", "v142 whole name first", "v142 stated roles with more in their name", "v142 item()",
                  "v142 notSee, see, hover, choose"):
        assert f"test.describe('{block}'" in spec, block
    runner = (ROOT / "selftests" / "browser_checks.py").read_text(encoding="utf-8")
    assert 'os.environ.get("BROWSER_CHECKS_CHANNEL", "")' in runner and 'BROWSER_CHECKS_CHANNEL", "chrome")' not in runner

@case
def the_prompt_example_of_a_row_is_one_the_helper_can_match():
    from agent import outsider
    helper = outsider.HELPER_JS
    assert '{ role: "row", name: "Invoice 1042" }' in outsider.SYSTEM_PROMPT
    roles = re.search(r"const CONTAINING_ROLES = new Set\(\[([^\]]*)\]\)", helper).group(1)
    assert {"'row'", "'option'", "'tab'", "'link'", "'heading'"} == {r.strip() for r in roles.split(",")}

    assert "'button'" not in roles and "'textbox'" not in roles

NODE_PROBE = r"""
const H = require('./outsider.js');
const want = ['test', 'freshSession', 'tagged', 'click', 'doubleClick', 'rightClick', 'drag', 'upload', 'download',
  'allowClipboard', 'clipboardText', 'putClipboard', 'locate', 'fill', 'check', 'choose', 'hover', 'press', 'see', 'notSee',
  'seeOnce', 'item', 'region', 'inventory', 'expect'];
const missing = want.filter((n) => !(n in H));
process.stdout.write(JSON.stringify({ missing }));
"""

@case
def the_helper_parses_in_node_and_exports_what_the_prompt_teaches():
    node = shutil.which("node")
    if not node:
        line = "SKIP  the_helper_parses_in_node_and_exports_what_the_prompt_teaches: node not found"
        SKIPPED.append(line)
        print(line)
        return
    from agent.outsider import HELPER_JS
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        stub = base / "node_modules" / "@playwright" / "test"
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name":"@playwright/test","main":"index.js"}', encoding="utf-8")
        (stub / "index.js").write_text("module.exports = { test: {}, expect: {} };\n", encoding="utf-8")
        (base / "outsider.js").write_text(HELPER_JS, encoding="utf-8")
        (base / "probe.cjs").write_text(NODE_PROBE, encoding="utf-8")
        done = subprocess.run([node, "probe.cjs"], cwd=str(base), capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stderr[-1500:]
        assert '"missing":[]' in done.stdout, done.stdout

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} helper142 cases passed" + (f"; {len(SKIPPED)} SKIPPED" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
