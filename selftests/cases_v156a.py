from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases_v145b import NODE, PAGE, SEED, TESTS, Model, State, block, earlier_red, green, make, own_red

NL = chr(10)
CASES = []
SEEDS = ("Evaluation seed:" + NL
         + "- Workbook `Q3 Sales`: sheet `Sheet1` with header `Region/Amount` and the row `East/1200`." + NL)
SEED_NODE = dict(NODE, seed_sentences=SEEDS)
ORIGINAL = ("// seed" + NL + "db.exec(\"CREATE TABLE workbooks (id INTEGER, name TEXT)\");" + NL
            + "insert('Q3 Sales', 'Sheet1', [['Region', 'Amount'], ['East', 1200]]);" + NL)
REWRITE = ("// seed, this batch's variant" + NL + "db.exec(\"CREATE TABLE workbooks (id INTEGER, name TEXT)\");" + NL
           + "insert('Q3 Sales', 'Sheet1', [['Item', 'Qty'], ['Pen', 4]]);" + NL)
ADDED = ORIGINAL + "insert('Q4 Plan', 'Sheet1', [['Item', 'Qty'], ['Pen', 4]]);" + NL
LOCK_LINE = "[seed-lock] REQ-9 round "
STAGNATION = "Two repair rounds have left exactly these failures unchanged"

def case(function):
    CASES.append(function)
    return function

class Lock(State):

    def __init__(self, on: bool | None = True):
        self.on = on

    def __enter__(self):
        from agent.codegen import CodegenImplementer
        from agent.settings import SETTINGS

        super().__enter__()
        self.reverts = dict(CodegenImplementer.seed_lock_reverts)
        self.switch = SETTINGS.get("seed_lock")
        CodegenImplementer.seed_lock_reverts.clear()
        if self.on is None:
            SETTINGS.pop("seed_lock", None)
        else:
            SETTINGS["seed_lock"] = self.on
        return self

    def __exit__(self, *exc):
        from agent.codegen import CodegenImplementer
        from agent.settings import SETTINGS

        CodegenImplementer.seed_lock_reverts.clear()
        CodegenImplementer.seed_lock_reverts.update(self.reverts)
        if self.switch is None:
            SETTINGS.pop("seed_lock", None)
        else:
            SETTINGS["seed_lock"] = self.switch
        return super().__exit__(*exc)

def project(tmp: str, seed: str | None = ORIGINAL, page: str | None = None) -> Path:
    root = Path(tmp)
    if seed is not None:
        (root / SEED).parent.mkdir(parents=True, exist_ok=True)
        (root / SEED).write_text(seed, encoding="utf-8")
    if page is not None:
        (root / PAGE).parent.mkdir(parents=True, exist_ok=True)
        (root / PAGE).write_text(page, encoding="utf-8")
    return root

def run(root: Path, answers, checks, node=SEED_NODE, log_hook=None, **kw):
    logs: list[str] = []
    model = Model(answers)
    seen: list[str | None] = []
    implementer = make(model, root, checks, logs=logs, **kw)
    if log_hook is not None:
        implementer.log = log_hook(logs)
    inner = implementer.check

    def check():
        seen.append((root / SEED).read_text(encoding="utf-8") if (root / SEED).is_file() else None)
        return inner()

    implementer.check = check
    return implementer.implement(node, TESTS, own_red()), logs, model, seen

def noted(model, mark: str) -> list[str]:
    return [step for step, _o, prompt in model.calls if mark in prompt]

def lock_lines(logs) -> list[str]:
    return [line for line in logs if line.startswith("[seed-lock]")]

def same(a, b) -> bool:
    return (a or "").rstrip(NL) == (b or "").rstrip(NL)

@case
def an_answer_of_seed_files_only_that_removes_stated_values_is_put_back_and_not_checked():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, model, seen = run(root, [block(PAGE, "page 1"), block(SEED, REWRITE), block(PAGE, "page 2")],
                                        [own_red("Error: one"), green()])
        assert result.status == "passed", (result, logs[-6:])
        assert len(seen) == 2 and all(same(s, ORIGINAL) for s in seen), seen
        assert same((root / SEED).read_text(encoding="utf-8"), ORIGINAL)
        lines = lock_lines(logs)
        assert lines == [f"[seed-lock] REQ-9 round 1: reverted ['{SEED}']: they removed stated seed values ['Region', 'Amount', 'East', '1200']"], lines
        assert any("reverted whole by the seed lock; not checked, this round not counted" in line for line in logs), logs
        assert noted(model, "SEED LOCKED:") == ["diagnose1", "repair1"], [c[0] for c in model.calls]
        prompt = model.prompts("repair1")[-1]
        assert prompt.count("SEED LOCKED:") == 1 and SEED in prompt and '"Region"' in prompt and "you may change the schema" in prompt
        assert SEED not in result.written and PAGE in result.written, result.written

@case
def reverted_answers_feed_no_stagnation():

    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answers = [block(PAGE, "p0"), block(SEED, REWRITE), block(SEED, REWRITE), block(PAGE, "p1"), block(PAGE, "p2")]
        result, logs, model, seen = run(root, answers, [own_red(), own_red(), green()])
        assert result.status == "passed" and len(seen) == 3, (result, seen, logs[-6:])
        assert not noted(model, STAGNATION), noted(model, STAGNATION)

@case
def a_targeted_repair_with_one_round_still_gets_an_answer_after_two_reverts():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answers = [block(PAGE, "p0"), block(SEED, REWRITE), block(SEED, REWRITE), block(PAGE, "p1")]
        result, logs, model, seen = run(root, answers, [own_red(), green()], max_rounds=1)
        assert result.status == "passed", (result, logs[-8:])
        assert model.writes == 4 and len(seen) == 2, (model.writes, seen)

@case
def the_note_is_shown_until_the_next_writing_answer_only():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answers = [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1"), block(PAGE, "p2")]
        result, logs, model, _seen = run(root, answers, [own_red(), own_red("Error: another"), green()])
        assert result.status == "passed", (result, logs[-6:])
        assert noted(model, "SEED LOCKED:") == ["diagnose1", "repair1"], [c[0] for c in model.calls]

@case
def a_new_seed_file_that_takes_the_values_away_is_deleted_again():
    other = "backend/src/database/seed_workbooks.js"
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp, seed=None)
        (root / other).parent.mkdir(parents=True, exist_ok=True)
        (root / other).write_text(ORIGINAL, encoding="utf-8")
        answers = [block(PAGE, "p0"), block(other, "// moved") + NL + block(SEED, REWRITE), block(PAGE, "p1")]
        result, logs, _model, seen = run(root, answers, [own_red(), green()])
        assert result.status == "passed" and seen == [None, None], (result, seen, logs[-4:])
        assert not (root / SEED).exists() and (root / other).read_text(encoding="utf-8") == ORIGINAL
        assert any(line.startswith(LOCK_LINE + "1: reverted") for line in logs), logs

@case
def a_failed_put_back_deletes_nothing_and_the_answer_is_checked_as_written():
    from agent import fsutil

    helper = "backend/src/database/seed_helper.js"
    rewrite = REWRITE + "require('./seed_helper');" + NL
    original = fsutil.replace_or_leave

    def refuse(temporary, path):
        if str(temporary).endswith(".arc-restore"):
            raise OSError("held open")
        return original(temporary, path)

    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        fsutil.replace_or_leave = refuse
        try:
            answers = [block(PAGE, "p0"), block(SEED, rewrite) + NL + block(helper, "module.exports = {};"), block(PAGE, "p1")]
            result, logs, _model, seen = run(root, answers, [own_red(), own_red("Error: two"), green()])
        finally:
            fsutil.replace_or_leave = original
        assert result.status == "passed", (result, logs[-6:])
        assert (root / helper).is_file() and same((root / SEED).read_text(encoding="utf-8"), rewrite)
        assert len(seen) == 3 and same(seen[1], rewrite), seen
        assert any("the revert failed" in line for line in lock_lines(logs)), lock_lines(logs)
        assert SEED in result.written and helper in result.written, result.written
        assert not list(root.rglob("*.arc-restore"))

@case
def an_exception_after_the_put_back_keeps_the_books_true_to_the_disk():
    def hook(logs):
        def log(message):
            if message.startswith("[seed-lock]") and ": reverted [" in message:
                raise RuntimeError("log broke")
            logs.append(message)
        return log

    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, _model, seen = run(root, [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1")],
                                         [own_red(), own_red("Error: two"), green()], log_hook=hook)
        assert result.status == "passed", (result, logs[-6:])
        assert same(seen[1], ORIGINAL), seen
        assert SEED not in result.written, result.written
        assert any("not checked (RuntimeError" in line for line in logs), logs

@case
def an_incomplete_snapshot_does_nothing():
    original = Path.read_bytes

    def read_bytes(self):
        if self.name == "locked.js":
            raise PermissionError("locked")
        return original(self)

    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        (root / "frontend/src").mkdir(parents=True, exist_ok=True)
        (root / "frontend/src/locked.js").write_text("export const ROW = ['East', 1200];", encoding="utf-8")
        Path.read_bytes = read_bytes
        try:
            result, logs, model, seen = run(root, [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1")],
                                            [own_red(), own_red("Error: two"), green()])
        finally:
            Path.read_bytes = original
        assert result.status == "passed" and same(seen[1], REWRITE), (result, seen)
        assert lock_lines(logs) and all("not checked (incomplete snapshot: read frontend/src/locked.js" in line for line in lock_lines(logs)), lock_lines(logs)
        assert not noted(model, "SEED LOCK")

@case
def an_answer_that_also_writes_other_files_is_never_put_back_but_noted():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, model, seen = run(root, [block(SEED, REWRITE) + NL + block(PAGE, "page 1"), block(PAGE, "page 2")],
                                        [own_red(), green()])
        assert result.status == "passed", (result, logs[-6:])
        assert same(seen[0], REWRITE), seen
        lines = lock_lines(logs)
        assert len(lines) == 1 and "not reverted (the answer also wrote" in lines[0], lines
        assert noted(model, "SEED LOCK:") == ["diagnose1", "repair1"] and not noted(model, "SEED LOCKED:"), [c[0] for c in model.calls]
        prompt = model.prompts("repair1")[0]
        assert "Put every stated value back in this answer" in prompt and '"East"' in prompt
        assert SEED in result.written

@case
def the_soft_note_is_bounded_too():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)

        answers = [block(SEED, ORIGINAL if n % 2 else REWRITE) + NL + block(PAGE, f"p{n}") for n in range(4)]
        answers += [block(SEED, REWRITE) + NL + block(PAGE, "p9"), block(PAGE, "p10")]
        checks = [own_red(f"Error: {n}") for n in range(6)] + [green()]
        _result, logs, _model, _seen = run(root, answers, checks)
        lines = lock_lines(logs)
        assert sum("not reverted" in line for line in lines) == 2 and "not noting again" in lines[-1], lines

@case
def a_seed_rewrite_that_only_adds_rows_is_never_touched():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, model, seen = run(root, [block(PAGE, "p0"), block(SEED, ADDED), block(PAGE, "p1")], [own_red(), own_red("Error: b"), green()])
        assert result.status == "passed" and same(seen[1], ADDED) and not lock_lines(logs), (seen, logs)
        assert not noted(model, "SEED LOCK") and SEED in result.written

@case
def a_value_moved_to_another_seed_file_of_the_answer_is_no_loss():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        moved = block(SEED, REWRITE) + NL + block("backend/src/database/seed_q3.js", "module.exports = [['Region', 'Amount'], ['East', 1200]];")
        result, logs, _model, seen = run(root, [block(PAGE, "p0"), moved, block(PAGE, "p1")], [own_red(), own_red("Error: b"), green()])
        assert result.status == "passed" and same(seen[1], REWRITE) and not lock_lines(logs), (result, seen, logs)

@case
def a_non_seed_file_is_never_put_back():
    page = "export const HEADER = ['Region', 'Amount'];" + NL
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp, page=page)
        result, logs, _model, _seen = run(root, [block(PAGE, "export const HEADER = [];")], [green()])
        assert result.status == "passed" and not lock_lines(logs), logs
        assert "Region" not in (root / PAGE).read_text(encoding="utf-8")

@case
def without_seed_sentences_or_stated_values_the_lock_does_nothing():
    for node in (NODE, dict(NODE, seed_sentences="The workbook Q3 Sales is seeded (no list line, no quoted value).")):
        with Lock(), tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            result, logs, model, seen = run(root, [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1")],
                                            [own_red(), own_red("Error: b"), green()], node=node)
            assert result.status == "passed" and same(seen[1], REWRITE), (result, seen)
            assert not lock_lines(logs) and not noted(model, "SEED LOCK"), logs

@case
def the_revert_is_bounded_per_batch_for_the_same_values_then_only_logged():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        for _attempt in range(2):
            run(root, [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1")], [own_red(), green()])
        _result, logs, _model, seen = run(root, [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1")],
                                          [own_red(), own_red("Error: b"), green()])
        assert same(seen[1], REWRITE), seen
        assert [line for line in lock_lines(logs) if "reverted 2 times already, not reverting again" in line], lock_lines(logs)
    with Lock(), tempfile.TemporaryDirectory() as tmp:

        root = project(tmp)
        answers = [block(PAGE, "p0")] + [block(SEED, REWRITE)] * 3 + [block(PAGE, "p1")]
        result, logs, _model, seen = run(root, answers, [own_red(), own_red("Error: b"), green()])
        assert result.status == "passed" and len(seen) == 3 and same(seen[1], REWRITE), (result, seen)
        assert sum(": reverted [" in line for line in lock_lines(logs)) == 2, lock_lines(logs)

@case
def one_implementer_reverts_at_most_twice_whatever_the_values():

    drops_row = ORIGINAL.replace("['East', 1200]", "['West', 7]")
    drops_header = ORIGINAL.replace("['Region', 'Amount']", "['Zone', 'Sum']")
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answers = [block(PAGE, "p0"), block(SEED, drops_row), block(SEED, drops_row), block(SEED, drops_header), block(PAGE, "p1")]
        result, logs, _model, seen = run(root, answers, [own_red(), own_red("Error: b"), green()])
        assert result.status == "passed" and len(seen) == 3 and same(seen[1], drops_header), (result, seen, lock_lines(logs))
        assert "no free revert left in this implementer" in lock_lines(logs)[-1], lock_lines(logs)

@case
def the_switch_off_leaves_the_rewrite_on_disk():
    with Lock(on=False), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, model, seen = run(root, [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1")],
                                        [own_red(), own_red("Error: b"), green()])
        assert result.status == "passed" and same(seen[1], REWRITE), (result, seen)
        assert not lock_lines(logs) and not noted(model, "SEED LOCK")

@case
def the_switch_is_on_by_default():
    from agent.settings import setting_on

    with Lock(on=None):
        assert setting_on("seed_lock")

@case
def a_reverted_seed_answer_is_no_shared_data_flip():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answers = [block(PAGE, "p0"), block(SEED, REWRITE), block(PAGE, "p1"), block(PAGE, "p2")]
        result, logs, _model, _seen = run(root, answers, [own_red(), earlier_red(), green()])
        assert result.status == "passed", (result, logs[-6:])
        assert not any("(shared-data flip " in line for line in logs), [line for line in logs if "flip" in line]

@case
def a_noted_answer_still_counts_as_a_flip():
    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        answers = [block(SEED, REWRITE) + NL + block(PAGE, "page 1"), block(PAGE, "page 2")]
        result, logs, _model, _seen = run(root, answers, [earlier_red(), green()])
        assert result.status == "passed", result
        assert sum("(shared-data flip " in line for line in logs) == 1, logs

@case
def the_call_deadline_is_checked_after_the_lock_and_no_check_starts_past_it():

    from agent.codegen import CodegenImplementer

    state = {"locked": False}
    lock, past = CodegenImplementer._seed_lock, CodegenImplementer._past_call_deadline

    def locked(self, node, round_index, undo, undo_paths, free):
        out = lock(self, node, round_index, undo, undo_paths, free)
        if SEED in undo_paths.values():
            state["locked"] = True
        return out

    def passed(self):
        return state["locked"] or past(self)

    with Lock(), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        CodegenImplementer._seed_lock, CodegenImplementer._past_call_deadline = locked, passed
        try:
            answers = [block(PAGE, "p0"), block(SEED, REWRITE) + NL + block(PAGE, "p1")]
            result, logs, _model, seen = run(root, answers, [own_red(), green()])
        finally:
            CodegenImplementer._seed_lock, CodegenImplementer._past_call_deadline = lock, past
        assert state["locked"], logs[-6:]
        assert getattr(result, "stop_reason", "") == "deadline", (result, logs[-6:])
        assert len(seen) == 1, seen
        assert same((root / SEED).read_text(encoding="utf-8"), ORIGINAL)

@case
def a_roll_back_that_cannot_restore_an_original_deletes_no_created_file():

    from agent import source_io

    helper = "backend/src/database/seed_helper.js"
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        (root / SEED).write_text(REWRITE + "require('./seed_helper');" + NL, encoding="utf-8")
        (root / helper).write_text("module.exports = {};", encoding="utf-8")
        logs: list[str] = []
        implementer = make(Model([]), root, [green()], logs=logs)
        original = source_io.put_back

        def refuse(target, content):
            if content is not None:
                raise OSError("held open")
            return original(target, content)

        source_io.put_back = refuse
        try:
            failed = implementer._roll_back({root / SEED: ORIGINAL.encode("utf-8"), root / helper: None}, "REQ-9", 1)
        finally:
            source_io.put_back = original
        assert (root / helper).is_file(), logs
        assert set(failed) == {root / SEED, root / helper}, failed

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
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v156a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
