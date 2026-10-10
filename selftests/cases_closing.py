from __future__ import annotations

import inspect
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES = ROOT.parent / "codex-audit" / "requirements-samples"
NL = chr(10)
CASES = []
MISSING = object()

APP = "frontend/src/App.tsx"
F1 = "backend/test-e2e/REQ-1.spec.js"
F2 = "backend/test-e2e/REQ-2.spec.js"
T1A, T1B, T2A = "REQ-1: shows the list", "REQ-1: saves the entry", "REQ-2: filters the rows"
DESC3 = "The filter bar keeps the chosen region after a reload of the page. Each row shows its total in bold type."
DESC4 = "The export button downloads the visible rows as a file named sales.csv right away."
T3A = "REQ-3: keeps the chosen region after a reload"
T3B = "REQ-3: each row shows its total in bold"
T3X = "REQ-3: shows a friendly toast after saving"
T4A = "REQ-4: downloads the visible rows as a file named sales.csv"
D3 = "backend/test-e2e/DESC-REQ-3.spec.js"
D4 = "backend/test-e2e/DESC-REQ-4.spec.js"

def case(function):
    CASES.append(function)
    return function

def brk(title: str) -> str:
    return f"BREAK<{title}>"

def ok(title: str) -> str:
    return f"OK<{title}>"

def spec_text(titles: list[str]) -> str:
    head = 'const { test } = require("@playwright/test");' + NL + 'const { see } = require("../e2e-support/outsider");' + NL + NL
    body = "".join(f'test("{t}", async ({{ page }}) => {{{NL}  await page.goto("/");{NL}  await see(page, "x");{NL}}});{NL}{NL}' for t in titles)
    return head + body

def spec_output(name: str, titles: list[str]) -> str:
    lines = [f"FAILED SPEC FILES: {name}"]
    for index, title in enumerate(titles, 1):
        lines += [f"[{index}/3] test-e2e\\{name}:{10 * index}:1 › {title}",
                  f"  {index}) test-e2e\\{name}:{10 * index}:1 › {title} ───────",
                  "    Error: The requirement says X is shown, but nothing visible matches.",

                  f"        at D:\\w\\backend\\test-e2e\\{name}:{10 * index + 3}:5"]
    lines += [f"  {len(titles)} failed"] + [f"    test-e2e\\{name}:{10 * i}:1 › {t} ────────" for i, t in enumerate(titles, 1)]
    return NL.join(lines + ["  1 passed"])

class GatewayError(Exception):
    pass

class Killed(BaseException):
    pass

class _Trace:
    def __init__(self):
        self.commits: list[str] = []

    def git_commit(self, message):
        self.commits.append(message)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

class World:
    def __init__(self, root: Path):
        from agent import clock

        self.root = root
        self.now = clock.now()
        self.run_sec = 10.0
        self.runs: list[tuple[list[str], str]] = []
        self.green: set[str] = set()
        self.flaky: dict[str, tuple[str, int]] = {}
        self.no_browser = False
        self.titles: dict[str, list[str]] = {"REQ-3": [T3A, T3B], "REQ-4": [T4A]}
        self.author_calls: list[tuple[str, bool]] = []
        self.author_raise: dict[str, BaseException] = {}
        self.tokens = 100000
        self.steps: list[dict] = []
        self.implementers: list[dict] = []
        self.check_outputs: list[str] = []
        self.builds = 0

    def sources(self) -> str:
        base = self.root / "frontend" / "src"
        return NL.join(p.read_text(encoding="utf-8") for p in sorted(base.rglob("*")) if p.is_file())

    def spec_titles(self, rel: str) -> list[str]:
        from agent.description_pass import split_tests

        path = self.root / rel
        parts = split_tests(path.read_text(encoding="utf-8")) if path.is_file() else None
        return [title for title, _ in parts[1]] if parts else []

    def failing(self, rel: str, text: str) -> list[str]:
        titles = self.spec_titles(rel)
        if Path(rel).name.startswith("DESC-"):
            return [t for t in titles if t not in self.green and ok(t) not in text]
        return [t for t in titles if brk(t) in text]

    def run_e2e(self, project_dir, specs, log=print, shared_data=False):
        from agent.verify import StepResult

        self.now += self.run_sec
        if self.no_browser:
            return StepResult(True, "playwright browsers unavailable here", skipped=True)
        text = self.sources()
        self.runs.append((list(specs), text))
        red = []
        for spec in specs:
            titles = self.failing(spec, text)
            name = Path(spec).name
            marker, times = self.flaky.get(name, ("", 0))
            if times > 0 and marker in text and not titles:
                self.flaky[name] = (marker, times - 1)
                titles = self.spec_titles(spec)[:1]
            if titles:
                red.append((spec, titles))
        if len(specs) == 1:

            return StepResult(not red, spec_output(Path(specs[0]).name, red[0][1]) if red else "1 passed",
                              ran=len(self.spec_titles(specs[0])))
        if not red:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed")
        head = "FAILED SPEC FILES: " + ", ".join(Path(s).name for s, _ in red)
        return StepResult(False, NL.join([head] + [spec_output(Path(s).name, t) for s, t in red]))

    def build_frontend(self, project_dir, log=print):
        from agent.verify import StepResult

        self.builds += 1
        return StepResult(True, "")

    def author(self, llm, node, prefix, problem="", current="", log=print):
        from agent.test_author import AuthoredFile

        leaf = str(node.get("id"))
        self.author_calls.append((leaf, bool(problem)))
        self.tokens += 1000
        if leaf in self.author_raise:
            raise self.author_raise[leaf]
        return AuthoredFile(path=f"backend/test-e2e/{prefix}.spec.js", content=spec_text(self.titles.get(leaf, [])))

    def desc_runs(self, rel: str) -> int:
        return sum(1 for specs, _ in self.runs if rel in specs)

def implementer_class(world: World):
    from agent.codegen import ImplementResult

    class ScriptedImplementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check
            world.implementers.append({"deadline": options.get("deadline"), "now": world.now, "options": options})

        def implement(self, node, tests, outcome, extra_context=""):
            step = world.steps.pop(0)
            world.tokens += step.get("tokens", 5000)
            for rel, content in step.get("write", {}).items():
                path = self.project_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            if step.get("raise") is not None:
                raise step["raise"]
            written = list(step.get("write", {}))
            if not step.get("check"):
                return ImplementResult(step["status"], written, "scripted", 0)
            result = self.check()
            world.check_outputs.append(result.output)
            status = step.get("status") or ("passed" if result.passed else "budget")
            return ImplementResult(status, written, "scripted", 1, last_check=result.output if result.passed else "")

    return ScriptedImplementer

def make_tree() -> dict:
    def leaf(i, description, deps=()):
        return {"id": f"REQ-{i}", "name": f"Feature {i}", "description": description, "dependencies": list(deps),
                "scenarios": [{"name": f"s-REQ-{i}", "steps": [{"keyword": "GIVEN", "content": "the user is on the home page"},
                                                               {"keyword": "WHEN", "content": "the user acts"},
                                                               {"keyword": "THEN", "content": "something is shown"}]}]}
    return {"id": "ROOT", "name": "R", "description": "root", "children": [
        leaf(1, "The home page shows the list."), leaf(2, "The rows can be filtered."),
        leaf(3, DESC3), leaf(4, DESC4, deps=["REQ-3"])]}

def make_factory(root: Path, world: World, app: str = "base"):
    import main as factory_module

    (root / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "src").mkdir(parents=True, exist_ok=True)
    (root / APP).write_text(app, encoding="utf-8")
    (root / "backend" / "src" / "server.js").write_text("// server", encoding="utf-8")
    contents = {F1: spec_text([T1A, T1B]), F2: spec_text([T2A])}
    for rel, content in contents.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")
    f = factory_module.Factory.__new__(factory_module.Factory)
    f.project_dir, f.requirements_dir, f.llm, f.contract, f.native_validation = root, root, None, "", False
    f.started, f.spent_before, f.time_budget, f.large_tree = world.now, 0.0, 10800.0, True
    f.trace = _Trace()
    f.report = {"nodes": {}, "verification": {}}
    f.all_test_files, f.e2e_specs, f.green_specs = [F1, F2], [F1, F2], [F1, F2]
    f.file_specs, f.foundation_checks, f.foundation_baselines, f.regression_debts = {}, {}, {}, []
    f.revised_specs, f.seed_checks = set(), {}
    f.spec_context = {F1: {"node": {"id": "REQ-1"}, "contract": "", "relevant": [], "tests": {}, "members": ["REQ-1"], "written": [APP]},
                      F2: {"node": {"id": "REQ-2"}, "contract": "", "relevant": [], "tests": {}, "members": ["REQ-2"], "written": []}}
    f.test_snapshot, f.test_ids_by_file, f.test_titles = dict(contents), {}, {}
    f.current_trace_id, f.current_members = "", []
    f.failed_members, f.no_evidence_members, f.unattempted_members = [], [], []
    f.restore_failures, f.verify_repairs = 0, 0
    f.audit_sent_fingerprints, f.audit_rolled_back_fingerprints = [], []
    f.description_pass, f.description_specs = {}, []
    f.resumed_sources, f.resumed_description = {}, ""
    return f

def run_pass(world: World, f, scope=("REQ-3",), settings=None, tree=None, whole=None, real_boilerplate=False,
             done_ids=("REQ-1", "REQ-2", "REQ-3", "REQ-4"), patch=None, seeds="", batches=None):
    import yaml

    import main as factory_module
    import agent.description_pass as dp
    from agent.settings import SETTINGS

    tree = tree if tree is not None else make_tree()
    whole = whole if whole is not None else yaml.safe_dump(tree)
    factory_module.attach_ancestor_context(tree)
    by_id = {str(leaf.get("id")): leaf for leaf in dp.all_leaves(tree)}
    batches = batches if batches is not None else [(leaf_id, [leaf_id]) for leaf_id in by_id]
    work = [{"id": batch_id, "members": list(members), "_leaves": [by_id[m] for m in members if m in by_id],
             "seed_sentences": seeds, "requirement_text": whole, "reference_requirements": ""} for batch_id, members in batches]
    lines: list[str] = []
    settings = dict(settings or {})
    saved_settings = {key: SETTINGS.get(key, MISSING) for key in settings}
    saved_log = factory_module.log
    try:
        SETTINGS.update(settings)
        factory_module.log = lines.append
        tools = dp.default_tools(f, lines.append)
        tools.run_e2e, tools.build_frontend = world.run_e2e, world.build_frontend
        tools.Implementer = implementer_class(world)
        tools.author, tools.review = world.author, (lambda llm, node, spec, prefix, log=print: "")
        tools.deterministic_review = lambda node, spec, prefix, log=print: []
        tools.static_defects = lambda project_dir, files: ""
        tools.now, tools.tokens, tools.quota_exhausted = (lambda: world.now), (lambda: world.tokens), (lambda: False)
        tools.llm_errors = (GatewayError,)
        if not real_boilerplate:
            tools.boilerplate_of_text = lambda text: {(leaf_id, f"s-{leaf_id}") for leaf_id in scope}
        for name, value in (patch or {}).items():
            setattr(tools, name, value)
        p = dp.DescriptionPass(f, tree, work, whole, list(done_ids), "digest-1", "root", lines.append, tools)
        summary = p.run()
    finally:
        factory_module.log = saved_log
        for key, value in saved_settings.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value
    return p, summary, lines

def with_world(app: str = "base"):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    world = World(root)
    f = make_factory(root, world, app)
    return tmp, root, world, f

def app_text(root: Path) -> str:
    return (root / APP).read_text(encoding="utf-8")

def ledger(f) -> list[dict]:
    return list((f.description_pass or {}).get("ledger") or [])

@case
def in_scope_are_exactly_the_leaves_with_generated_scenarios():
    import yaml

    import agent.description_pass as dp
    from agent.boilerplate import boilerplate_of_text

    children = []
    for i in range(6):
        children.append({"id": f"REQ-{i + 1}", "name": f"Feature {i}", "description": f"Users do thing number {i} here.",
                         "scenarios": [{"name": f"REQ-{i + 1} - the requested workflow {i}", "steps": [
                             {"keyword": "GIVEN", "content": f"The evaluation seed contains the seeded record `Record {i}` with `v{i}`."},
                             {"keyword": "WHEN", "content": f"The user opens the home page, clicks the visible `Record {i}` entry, and the "
                                                            f"requested workflow with concrete values `a{i}`, `b{i}`. Every value is entered "
                                                            "through a visible, labelled control; nothing internal is assumed."},
                             {"keyword": "THEN", "content": "The application exposes the observable result for \"the requested workflow\" "
                                                            f"using the same seeded names and values (the seeded record `Record {i}` with `c{i}`); "
                                                            "failures are shown beside the named control."}]}]})
    generated = {"id": "ROOT", "name": "R", "children": children}
    tmp, root, world, f = with_world()
    with tmp:
        p, summary, lines = run_pass(world, f, tree=generated, whole=yaml.safe_dump(generated), real_boilerplate=True,
                                     settings={"description_pass_max_sec": 0})
        assert len(p.in_scope() or []) == 6, p.in_scope()
        assert summary["status"] == "time" and world.runs == [] and world.author_calls == []
    sheet = SAMPLES / "hackathon-sheet-platform.yaml"
    if sheet.is_file():
        text = sheet.read_text(encoding="utf-8")
        ids = {leaf_id for leaf_id, _ in boilerplate_of_text(text)}
        assert len(ids) >= 20, len(ids)
        assert {str(leaf.get("id")) for leaf in dp.all_leaves(yaml.safe_load(text))} >= ids

@case
def keep_ticket_booking_and_bookstack_are_untouched():
    import yaml

    concrete = make_tree()
    samples = [("synthetic", concrete, yaml.safe_dump(concrete))]
    for name in ("keep.yaml", "ticket-booking.yaml", "bookstack.yaml", "keep-lite.yaml", "bookstack-lite.yaml"):
        path = SAMPLES / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            samples.append((name, yaml.safe_load(text), text))
        else:
            print(f"      (skipped: no {name} next to the agent)")
    for name, tree, text in samples:
        tmp, root, world, f = with_world()
        with tmp:
            before = f.snapshot_sources()
            specs = list(f.e2e_specs)
            p, summary, lines = run_pass(world, f, tree=tree, whole=text, real_boilerplate=True)
            assert summary["status"] == "no leaf in scope", (name, summary, lines)
            assert world.runs == [] and world.author_calls == [] and world.builds == 0 and world.implementers == [], name
            assert f.snapshot_sources() == before and f.e2e_specs == specs and f.description_specs == [], name
            assert not (root / ".arc" / "resume.json").exists() and f.trace.commits == [], name

@case
def without_the_boilerplate_module_no_leaf_is_in_scope():
    import agent.description_pass as dp

    saved = sys.modules.get("agent.boilerplate", MISSING)
    lines: list[str] = []
    tmp, root, world, f = with_world()
    with tmp:
        try:
            sys.modules["agent.boilerplate"] = None
            tools = dp.default_tools(f, lines.append)
        finally:
            if saved is MISSING:
                sys.modules.pop("agent.boilerplate", None)
            else:
                sys.modules["agent.boilerplate"] = saved
        assert tools.boilerplate_of_text is None and any("boilerplate detection unavailable" in line for line in lines), lines
        p, summary, lines = run_pass(world, f, patch={"boilerplate_of_text": None})
        assert summary["status"].startswith("no boilerplate detection"), summary
        assert world.runs == [] and world.author_calls == [] and any("no leaf is in scope" in line for line in lines)

@case
def the_note_reaches_the_spec_writer_through_one_channel_only():
    import agent.description_pass as dp
    from agent.outsider import author_acceptance

    class Model:
        last_finish_reason = "stop"

        def chat(self, messages, **options):
            self.user = messages[1]["content"]
            return {"content": ""}

    def patched(llm, node, prefix, problem="", current="", log=print):
        return node.get("pass_note")

    tmp, root, world, f = with_world()
    with tmp:
        leaf = {"id": "REQ-3", "name": "Filter", "description": DESC3, "scenarios": []}
        batch = {"id": "B", "members": ["REQ-3"], "seed_sentences": "SEEDS", "reference_requirements": "DIGEST"}
        tools = dp.default_tools(f, lambda line: None)
        p = dp.DescriptionPass(f, {}, [], "", [], "d", "", lambda line: None, tools)
        expected = "pass_note" if dp.outsider_reads_pass_note(author_acceptance) else "seed_sentences"
        assert tools.note_channel == expected
        view = p.leaf_view(leaf, batch)
        model = Model()
        author_acceptance(model, view, "DESC-REQ-3", log=lambda line: None)
        assert model.user.count("CLOSING PASS") == 1, model.user
        assert "`" not in dp.DESCRIPTION_PASS_NOTE
        assert batch["seed_sentences"] == "SEEDS"

        assert dp.outsider_reads_pass_note(patched)
        tools.note_channel = "pass_note"
        assert p.leaf_view(leaf, batch)["seed_sentences"] == "SEEDS"

@case
def a_green_new_test_joins_the_suite_without_a_repair():
    tmp, root, world, f = with_world()
    with tmp:
        world.green = {T3A, T3B}
        p, summary, lines = run_pass(world, f)
        assert summary["status"] == "done" and summary["admitted"] == [D3], (summary, lines)
        assert D3 in f.e2e_specs and D3 in f.all_test_files and f.description_specs == [D3]
        assert f.test_snapshot[D3] == (root / D3).read_text(encoding="utf-8")
        assert f.spec_context[D3]["members"] == ["REQ-3"] and "pass_note" not in f.spec_context[D3]["node"]
        assert D3 not in f.green_specs and world.implementers == []
        assert ledger(f)[0]["outcome"] == "admitted" and f.description_pass["done"] == ["REQ-3"]
        assert any("(description pass): admitted 2 / dropped 0" in c for c in f.trace.commits)
        assert (root / ".arc" / "resume.json").is_file() and (root / ".arc" / "description-pass.json").is_file()

@case
def a_title_that_is_no_phrase_of_the_description_is_cut_before_the_first_run():
    import agent.description_pass as dp

    assert dp.quote_ok(T3A, DESC3) and dp.quote_ok(T3B, DESC3) and not dp.quote_ok(T3X, DESC3)
    assert not dp.quote_ok("REQ-3: keeps the region", DESC3)
    assert dp.quote_ok("REQ-3: “Keeps the  chosen region after a reload.”", DESC3)
    assert dp.quote_ok("REQ-9: 用户在列表页可以按名称筛选全部记录", "说明:用户在列表页可以按名称筛选全部记录。")
    tmp, root, world, f = with_world()
    with tmp:
        world.green = {T3A, T3X}
        world.titles["REQ-3"] = [T3A, T3X]
        p, summary, lines = run_pass(world, f)
        content = (root / D3).read_text(encoding="utf-8")
        assert T3A in content and T3X not in content, content
        tests = {t["title"]: t for t in ledger(f)[0]["tests"]}
        assert tests[T3X]["reason"] == "invented-quote" and tests[T3A]["final"] == "admitted"
    tmp, root, world, f = with_world()
    with tmp:
        world.titles["REQ-3"] = [T3X]
        p, summary, lines = run_pass(world, f)
        assert world.desc_runs(D3) == 0 and not (root / D3).exists() and D3 not in f.e2e_specs
        assert ledger(f)[0]["outcome"].startswith("dropped"), ledger(f)

@case
def a_repair_that_fixes_the_new_test_but_reddens_an_old_title_is_put_back():
    tmp, root, world, f = with_world()
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + ok(T3A) + " " + brk(T1B)}, "check": True}]
        p, summary, lines = run_pass(world, f)
        assert app_text(root) == "base", app_text(root)
        assert D3 not in f.e2e_specs and not (root / D3).exists() and f.description_specs == []
        assert (root / ".arc" / "description-pass" / "dropped" / "DESC-REQ-3.spec.js").is_file()
        entry = ledger(f)[0]
        assert entry["rollback"]["done"] and entry["rollback"]["reason"].startswith("new-red: REQ-1.spec.js: " + T1B), entry
        assert entry["tests"][0]["reason"] == "app-defect-unrepaired"
        assert any("which passed before this repair, fail" in out for out in world.check_outputs)

@case
def a_new_title_in_an_already_red_file_counts_and_an_old_one_does_not():

    tmp, root, world, f = with_world(app="base " + brk(T1A))
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + brk(T1A) + " " + ok(T3A)}, "check": True}]
        p, summary, lines = run_pass(world, f)
        assert ledger(f)[0]["outcome"] == "admitted after a kept repair", ledger(f)
        assert app_text(root) == "base " + brk(T1A) + " " + ok(T3A) and D3 in f.e2e_specs

    tmp, root, world, f = with_world(app="base " + brk(T1A))
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + brk(T1A) + " " + brk(T1B) + " " + ok(T3A)}, "check": True}]
        p, summary, lines = run_pass(world, f)
        assert app_text(root) == "base " + brk(T1A) and D3 not in f.e2e_specs, ledger(f)
        assert "REQ-1.spec.js: " + T1B in ledger(f)[0]["rollback"]["reason"]

@case
def a_repair_that_removes_stated_seed_values_is_put_back():
    seeds = "SEED SENTENCES OF THE WHOLE REQUIREMENT:" + NL + "- The evaluation seed contains the row `East/1200`."
    tmp, root, world, f = with_world(app="base East 1200")
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + ok(T3A)}, "check": True}]
        p, summary, lines = run_pass(world, f, seeds=seeds)
        assert app_text(root) == "base East 1200" and D3 not in f.e2e_specs, ledger(f)
        assert "lost-seed" in ledger(f)[0]["rollback"]["reason"] and any("SEED DATA CHECK FAILED" in o for o in world.check_outputs)

@case
def a_kept_repair_moves_the_baseline():

    tmp, root, world, f = with_world(app="base " + brk(T2A))
    with tmp:
        world.titles["REQ-3"] = [T3A, T3B]
        world.steps = [{"write": {APP: "base " + ok(T3A) + " " + ok(T3B)}, "check": True}]
        p, summary, lines = run_pass(world, f)
        assert ledger(f)[0]["outcome"] == "admitted after a kept repair", (ledger(f), lines)
        assert app_text(root) == "base " + ok(T3A) + " " + ok(T3B) and f.description_specs == [D3]
        assert p.baseline == {}
        assert summary["repairs_kept"] == 1 and summary["repairs_rolled_back"] == 0

@case
def an_implementer_that_claims_green_without_a_green_last_check_is_put_back():
    tmp, root, world, f = with_world()
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + ok(T3A) + " " + brk(T2A)}, "check": True, "status": "passed"}]
        p, summary, lines = run_pass(world, f)
        assert app_text(root) == "base" and D3 not in f.e2e_specs, (app_text(root), ledger(f))
        assert summary["status"] == "done" and ledger(f)[0]["rollback"]["done"], (summary, ledger(f))

@case
def red_and_unclear_tests_are_cut_out_never_rewritten_toward_the_app():

    tmp, root, world, f = with_world()
    with tmp:
        world.green = {T3B}
        world.steps = [{"write": {APP: "base half-done"}, "status": "test_defect"}]
        p, summary, lines = run_pass(world, f)
        assert app_text(root) == "base" and f.description_specs == [D3], ledger(f)
        content = (root / D3).read_text(encoding="utf-8")
        assert T3B in content and T3A not in content
        assert world.author_calls == [("REQ-3", False)], world.author_calls
        tests = {t["title"]: t for t in ledger(f)[0]["tests"]}
        assert tests[T3A]["reason"] == "test-defect" and tests[T3B]["final"] == "admitted"

    for step in ({"write": {APP: "base x"}, "status": "gave_up"}, {"write": {APP: "base y"}, "raise": GatewayError("read timeout")}):
        tmp, root, world, f = with_world()
        with tmp:
            world.green = {T3B}
            world.steps = [step]
            p, summary, lines = run_pass(world, f)
            assert app_text(root) == "base" and f.description_specs == [D3], (step, ledger(f))
            tests = {t["title"]: t for t in ledger(f)[0]["tests"]}
            assert tests[T3A]["reason"] == "app-defect-unrepaired", tests

    tmp, root, world, f = with_world()
    with tmp:
        world.steps = [{"write": {APP: "base z"}, "check": True}]
        p, summary, lines = run_pass(world, f)
        assert app_text(root) == "base" and f.description_specs == [] and not (root / D3).exists()
        assert D3 not in f.test_snapshot and D3 not in f.spec_context and D3 not in f.all_test_files

@case
def the_window_is_the_earlier_of_cap_minus_reserve_and_start_plus_three_hours():
    import main as factory_module
    from agent.settings import _DEFAULTS

    tmp, root, world, f = with_world()
    with tmp:
        import agent.description_pass as dp

        tools = dp.default_tools(f, lambda line: None)
        p = dp.DescriptionPass(f, {}, [], "", [], "d", "", lambda line: None, tools)

        assert _DEFAULTS["description_pass_max_sec"] == 10800

        assert factory_module.final_judgement_reserve(45) == 5995
        assert p.verify_reserve(45) == 24295 and p.window_end(25900, 45) == 36700, p.window_end(25900, 45)

        assert p.window_end(96000, 45) == 105305

        f.large_tree, f.time_budget, f.e2e_specs = False, 10800.0, [F1, F2]
        assert p.window_end(0, 4) == 10800 + 3600 + factory_module.final_judgement_reserve(2) - (
            3600 + factory_module.final_judgement_reserve(4) + 1200 + 900 + 1800)

@case
def the_pass_never_runs_when_time_is_short():
    tmp, root, world, f = with_world()
    with tmp:
        f.spent_before = 129600 - 20000
        before = f.snapshot_sources()
        p, summary, lines = run_pass(world, f)
        assert summary["status"] == "time", summary
        assert world.runs == [] and world.author_calls == [] and world.builds == 0 and f.snapshot_sources() == before

@case
def the_window_stops_the_next_leaf_and_the_repair_deadline_keeps_a_suite_run_free():
    tmp, root, world, f = with_world()
    with tmp:
        world.run_sec, world.green = 150.0, {T3A, T3B, T4A}
        p, summary, lines = run_pass(world, f, scope=("REQ-3", "REQ-4"), settings={"description_pass_max_sec": 2000})

        assert summary["status"] == "time" and f.description_pass["done"] == ["REQ-3"], (summary, lines)
        assert D4 not in f.e2e_specs and ("REQ-4", False) not in world.author_calls
    tmp, root, world, f = with_world()
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + ok(T3A)}, "check": True}]
        p, summary, lines = run_pass(world, f)
        made = world.implementers[0]
        elapsed = f.spent_before + (made["now"] - f.started)
        assert made["options"]["max_rounds"] == 2 and made["options"]["extra_rounds"] == 1
        suite_then = 1.2 * p.suite_per_file * 2
        assert suite_then > 0 and made["deadline"] - made["now"] <= (p.pass_end - elapsed) - suite_then + 1e-6
        assert made["deadline"] - made["now"] <= 3600 - suite_then + 1e-6

@case
def the_token_budget_stops_the_next_leaf():
    tmp, root, world, f = with_world()
    with tmp:
        world.tokens = 10000
        world.titles["REQ-3"] = [T3A]
        world.green = {T4A}
        world.steps = [{"write": {APP: "base " + ok(T3A)}, "check": True, "tokens": 5000}]
        p, summary, lines = run_pass(world, f, scope=("REQ-3", "REQ-4"))
        assert summary["status"] == "token budget" and f.description_pass["done"] == ["REQ-3"], (summary, lines)
        assert summary["token_budget"] == 5000 and ("REQ-4", False) not in world.author_calls

    tmp, root, world, f = with_world()
    with tmp:
        world.tokens = 1000
        p, summary, lines = run_pass(world, f)
        assert world.implementers == [] and app_text(root) == "base" and summary["status"] == "token budget", summary

@case
def resume_after_an_interruption_skips_done_leaves_and_removes_the_half_written_candidate():
    import main as factory_module
    import agent.description_pass as dp

    tmp, root, world, f = with_world()
    with tmp:
        world.green = {T3A, T3B}
        world.titles["REQ-4"] = [T4A]
        world.steps = [{"write": {APP: "base half of a repair"}, "raise": Killed()}]
        saved = (dp.DescriptionPass.put_back_last_good, dp.DescriptionPass.remove_leftovers)
        dp.DescriptionPass.put_back_last_good = lambda self: None
        dp.DescriptionPass.remove_leftovers = lambda self: []
        try:
            run_pass(world, f, scope=("REQ-3", "REQ-4"))
            raise AssertionError("the kill did not reach the caller")
        except Killed:
            pass
        finally:
            dp.DescriptionPass.put_back_last_good, dp.DescriptionPass.remove_leftovers = saved
        assert (root / D4).is_file() and app_text(root) == "base half of a repair"

        second = factory_module.Factory.__new__(factory_module.Factory)
        second.project_dir, second.revised_specs, second.resumed_description, second.restore_failures = root, set(), "", 0
        second.resumed_sources, second.llm = {}, None
        assert second.load_checkpoint("digest-1") == ["REQ-1", "REQ-2", "REQ-3", "REQ-4"]
        second.restore_sources(second.resumed_sources)
        second.restore_tests()
        assert app_text(root) == "base" and second.description_pass["done"] == ["REQ-3"]
        assert second.description_specs == [D3] and second.e2e_specs.count(D3) == 1
        second.trace, second.started, second.spent_before = _Trace(), world.now, second.spent_before
        second.time_budget, second.large_tree, second.verify_repairs = 10800.0, True, 0
        world.titles["REQ-4"] = ["REQ-4: an invented export title here"]
        p, summary, lines = run_pass(world, second, scope=("REQ-3", "REQ-4"))
        assert any("removed 1 candidate spec file(s) not admitted" in line for line in lines), lines
        assert [c for c in world.author_calls if c[0] == "REQ-3"] == [("REQ-3", False)]
        assert second.description_pass["done"] == ["REQ-3", "REQ-4"] and not (root / D4).exists()
        assert second.e2e_specs.count(D3) == 1 and (root / D3).is_file()

@case
def a_crash_inside_the_pass_leaves_the_last_verified_tree():
    tmp, root, world, f = with_world()
    with tmp:
        world.titles["REQ-3"] = [T3A]
        world.steps = [{"write": {APP: "base " + ok(T3A)}, "check": True}]

        def broken_write_tests(req_id, files):
            raise RuntimeError("disk full")
        f.write_tests = broken_write_tests
        p, summary, lines = run_pass(world, f)
        assert summary["status"].startswith("crash"), summary
        assert app_text(root) == "base" and not (root / D3).exists() and D3 not in f.e2e_specs

@case
def the_leaves_go_in_dependency_order():
    import agent.description_pass as dp

    tree = make_tree()
    leaves = dp.all_leaves(tree)
    ordered = dp.order_leaves([leaves[3], leaves[2]], leaves, {"REQ-3": 5, "REQ-4": 1})
    assert [leaf["id"] for leaf in ordered] == ["REQ-3", "REQ-4"]
    tmp, root, world, f = with_world()
    with tmp:
        f.unattempted_members = ["REQ-4"]
        world.green = {T3A, T3B, T4A}
        p, summary, lines = run_pass(world, f, scope=("REQ-3", "REQ-4"))
        assert [c[0] for c in world.author_calls] == ["REQ-3"]

@case
def red_foundation_leaves_come_first_then_other_red_leaves_then_the_rest():
    import agent.description_pass as dp

    leaves = [{"id": f"REQ-{i}", "scenarios": [{}] * i, "dependencies": ["REQ-4"] if i == 6 else []} for i in range(1, 7)]
    work = [{"id": leaf["id"], "members": [leaf["id"]]} for leaf in leaves]
    spec = {f"S{i}": {"members": [f"REQ-{i}"]} for i in range(1, 7)}

    ranks = dp.leaf_ranks(work, ["REQ-5", "REQ-2"], ["S1", "S3", "S4"], spec, 3)
    assert ranks == {"REQ-2": 0, "REQ-5": 1}, ranks
    index = {leaf["id"]: i for i, leaf in enumerate(leaves)}
    ordered = dp.order_leaves([leaves[5], leaves[4], leaves[3], leaves[1]], leaves, index, ranks)

    assert [leaf["id"] for leaf in ordered] == ["REQ-2", "REQ-5", "REQ-4", "REQ-6"], [leaf["id"] for leaf in ordered]

    for failed, expected in ((["REQ-4"], ["REQ-4", "REQ-3"]), (["REQ-4", "REQ-3"], ["REQ-3", "REQ-4"]), ([], ["REQ-3", "REQ-4"])):
        tmp, root, world, f = with_world()
        with tmp:
            f.failed_members = list(failed)
            world.green = {T3A, T3B, T4A}
            run_pass(world, f, scope=("REQ-3", "REQ-4"))
            assert [c[0] for c in world.author_calls] == expected, (failed, world.author_calls)

def _engine(outcomes: dict, time_left: float = 10 ** 6):
    import main as factory_module

    f = factory_module.Factory.__new__(factory_module.Factory)
    f.trace = _Trace()

    f._tmp = tempfile.TemporaryDirectory()
    f.project_dir, f.restore_failures = Path(f._tmp.name), 0
    f.failed_members, f.unattempted_members = [], []
    f.report = {"nodes": {}, "verification": {}}
    f.GATEWAY_PAUSE_SEC = 0.0
    f.over_budget = lambda: False
    f.time_left = lambda: time_left
    calls: list[str] = []

    def process_node(node, nodes_left, retry=False):
        calls.append(node["id"] + ("+retry" if retry else ""))
        outcome = outcomes[node["id"]].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        f.report["nodes"][node["id"]] = {"status": "passed" if outcome else "budget", "tests": [f"backend/test-e2e/{node['id']}.spec.js"]}
        return outcome

    f.process_node = process_node
    f.save_checkpoint = lambda digest, done, description: None
    return f, calls

def _queue(f, *ids):
    import main as factory_module
    from agent.llm import LLM

    lines: list[str] = []
    saved = (LLM.quota_exhausted, factory_module.log)
    try:
        LLM.quota_exhausted, factory_module.log = False, lines.append
        result = f.run_queue([({"id": i, "members": [i]}, []) for i in ids], [], "d", "")
    finally:
        LLM.quota_exhausted, factory_module.log = saved
    return result, lines

@case
def a_red_foundation_batch_gets_exactly_one_more_attempt():
    from agent.settings import SETTINGS

    f, calls = _engine({"A": [False, False], "B": [True]})
    result, lines = _queue(f, "A", "B")
    assert calls == ["A", "A+retry", "B"] and result is False and f.failed_members == ["A"], calls
    assert any("foundation retry ended red" in line for line in lines)
    f, calls = _engine({"A": [False, True], "B": [True]})
    result, lines = _queue(f, "A", "B")
    assert calls == ["A", "A+retry", "B"] and result is True and f.failed_members == [], (calls, f.failed_members)

    f, calls = _engine({"A": [True], "B": [True], "C": [True], "D": [False, True]})
    assert _queue(f, "A", "B", "C", "D")[0] is False and calls == ["A", "B", "C", "D"], calls
    f, calls = _engine({"A": [RuntimeError("a bug in the batch"), True], "B": [True]})

    f.report["nodes"]["A"] = {"status": "budget", "tests": ["backend/test-e2e/A.spec.js"]}
    assert _queue(f, "A", "B")[0] is False and calls == ["A", "B"], calls
    saved = SETTINGS.get("foundation_retry", MISSING)
    try:
        SETTINGS["foundation_retry"] = False
        f, calls = _engine({"A": [False, True], "B": [True]})
        assert _queue(f, "A", "B")[0] is False and calls == ["A", "B"], calls
    finally:
        if saved is MISSING:
            SETTINGS.pop("foundation_retry", None)
        else:
            SETTINGS["foundation_retry"] = saved

@case
def no_foundation_retry_when_time_is_short():
    import main as factory_module

    need = factory_module.MIN_BATCH_SEC * 4
    f, calls = _engine({"A": [False, True], "B": [True], "C": [True]}, time_left=need - 1)
    result, lines = _queue(f, "A", "B", "C")
    assert calls == ["A", "B", "C"] and any("no foundation retry" in line for line in lines), (calls, lines)
    f, calls = _engine({"A": [False, True], "B": [True], "C": [True]}, time_left=need)
    assert _queue(f, "A", "B", "C")[0] is True and calls == ["A", "A+retry", "B", "C"], calls

@case
def a_green_foundation_retry_keeps_its_sources_and_the_same_tests():
    import cases_rollback as rb
    import main as factory_module
    from agent.llm import LLM
    from agent.settings import SETTINGS
    from agent.testrun import TestOutcome

    lines: list[str] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output", "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_audit, saved_quota = SETTINGS.get("audit_enabled", True), LLM.quota_exhausted
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        f.save_checkpoint = lambda digest, done, description: None
        authored: list[str] = []
        first_author = f.author_tests
        f.author_tests = lambda node, prefix, contract: (authored.append(prefix), first_author(node, prefix, contract))[1]
        script = [{"write": {rb.APP: "base feature"}, "check": True},
                  {"write": {"frontend/src/Retry.tsx": "OWN-OK"}, "check": True}]
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = rb.implementer_class(script)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            SETTINGS["audit_enabled"], LLM.quota_exhausted = False, False
            ok_run = f.run_queue([({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, [])], [], "d", "")
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            SETTINGS["audit_enabled"], LLM.quota_exhausted = saved_audit, saved_quota
        assert not script and ok_run is True and f.failed_members == [], (script, lines)
        assert authored == ["REQ-9"]

        assert (root / rb.APP).read_text(encoding="utf-8") == "base feature"
        assert (root / "frontend" / "src" / "Retry.tsx").read_text(encoding="utf-8") == "OWN-OK"
        assert f.e2e_specs.count(rb.OWN) == 1 and rb.OWN in f.green_specs and f.report["nodes"]["REQ-9"]["status"] == "passed"
        assert any("foundation retry ended GREEN" in line for line in lines), lines

@case
def the_hook_sits_between_the_batches_and_verify_and_keep_keeps_its_guard():
    import main as factory_module

    source = inspect.getsource(factory_module.Factory.run)
    batches = source.index("self.run_queue(")
    seed = source.index("log_seed_identity(whole, self.project_dir")
    hook = source.index("run_description_pass(self, tree, work, whole, done_ids, resume_digest")
    assert batches < seed < hook < source.index("self.verify()\n", hook)
    block = source[source.rindex("try:", 0, hook):hook]
    assert "from agent.description_pass import run_description_pass" in block
    assert "description_pass" in factory_module.Factory.CHECKPOINT_FIELDS and "description_specs" in factory_module.Factory.CHECKPOINT_FIELDS
    repair = inspect.getsource(factory_module.Factory.repair_failing_specs)
    assert "[:8]" in repair and "guard += " in repair

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
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} closing-pass cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
