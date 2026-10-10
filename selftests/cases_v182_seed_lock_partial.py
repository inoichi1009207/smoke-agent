from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from cases_v145b import PAGE, SEED, block, green, own_red
from cases_v156a import ORIGINAL, REWRITE, Lock, lock_lines, noted, project, run, same

NL = chr(10)
KEY = "seed_lock_partial_restore"
EXTRA = "backend/src/database/seed_q4.js"
INDEPENDENT = "module.exports = [['Q4 Plan', 'Budget'], ['West', 900]];"
NAMES_SEED = "const base = require('./init_db');" + NL + "module.exports = [['Q4 Plan', 'Budget'], ['West', 900]];"
USES_NEW_TOKEN = "module.exports = [['Q4 Plan', 'Budget'], ['Item', 900]];"
WHOLE_LINE = (f"[seed-lock] REQ-9 round 1: reverted ['{SEED}', '{EXTRA}']: they removed stated seed values "
              "['Region', 'Amount', 'East', '1200']")
CASES: list = []

def case(function):
    CASES.append(function)
    return function

class Switch:

    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        from agent.settings import SETTINGS

        for key, value in self.values.items():
            self.saved[key] = (key in SETTINGS, SETTINGS.get(key))
            SETTINGS[key] = value
        return self

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, (had, value) in self.saved.items():
            if had:
                SETTINGS[key] = value
            else:
                SETTINGS.pop(key, None)
        return False

class Patch:

    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self.saved = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.saved)
        return False

def seed_answer(extra: str) -> str:
    return block(SEED, REWRITE) + NL + block(EXTRA, extra)

def whole_rollback(extra: str, on: bool = True, patches=()):
    with Lock(), Switch(**{KEY: on}), tempfile.TemporaryDirectory() as tmp:
        for p in patches:
            p.__enter__()
        try:
            root = project(tmp)
            result, logs, model, seen = run(root, [block(PAGE, "page 1"), seed_answer(extra), block(PAGE, "page 2")],
                                            [own_red("Error: one"), green()])
        finally:
            for p in reversed(patches):
                p.__exit__()
        state = ((root / SEED).read_text(encoding="utf-8"), (root / EXTRA).is_file())
    return result, logs, model, seen, state

def assert_as_v181(result, logs, model, seen, state, partial_logged: bool):
    assert result.status == "passed", (result, logs[-6:])
    assert len(seen) == 2 and all(same(s, ORIGINAL) for s in seen), seen
    assert same(state[0], ORIGINAL) and state[1] is False, state
    lines = lock_lines(logs)
    assert lines[-1] == WHOLE_LINE, lines
    assert any("reverted whole by the seed lock; not checked, this round not counted" in line for line in logs), logs
    assert noted(model, "SEED LOCKED:") == ["diagnose1", "repair1"], [c[0] for c in model.calls]
    assert not noted(model, "was kept and checked")
    assert EXTRA not in result.written and SEED not in result.written, result.written
    has_partial = any("partial put-back not used" in line for line in lines)
    assert has_partial is partial_logged, lines

@case
def independent_files_put_back_only_the_one_that_lost_values():
    with Lock(), Switch(**{KEY: True}), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, model, seen = run(root, [block(PAGE, "page 1"), seed_answer(INDEPENDENT), block(PAGE, "page 2")],
                                        [own_red("Error: one"), own_red("Error: two"), green()])
        assert result.status == "passed", (result, logs[-6:])

        assert len(seen) == 3 and all(same(s, ORIGINAL) for s in seen), seen
        assert same((root / SEED).read_text(encoding="utf-8"), ORIGINAL)
        assert (root / EXTRA).read_text(encoding="utf-8").strip() == INDEPENDENT
        text = (root / SEED).read_text(encoding="utf-8")
        for value in ("Q3 Sales", "Region", "Amount", "East", "1200"):
            assert value in text, value
        lines = lock_lines(logs)
        assert lines == [f"[seed-lock] REQ-9 round 1: put back only ['{SEED}']: they removed stated seed values "
                         f"['Region', 'Amount', 'East', '1200']; kept ['{EXTRA}'] (independent of them), checked as usual"], lines
        assert not any("reverted whole by the seed lock" in line for line in logs), logs
        assert EXTRA in result.written and SEED not in result.written, result.written

        prompt = model.prompts("repair1")[-1] if model.prompts("repair1") else ""
        assert prompt.count("SEED LOCKED:") == 1 and "was kept and checked" in prompt and EXTRA in prompt, prompt[-800:]

@case
def the_partial_put_back_counts_against_the_same_revert_cap():
    from agent.codegen import CodegenImplementer

    with Lock(), Switch(**{KEY: True}), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        run(root, [block(PAGE, "page 1"), seed_answer(INDEPENDENT), block(PAGE, "page 2")],
            [own_red("Error: one"), own_red("Error: two"), green()])
        assert list(CodegenImplementer.seed_lock_reverts.values()) == [1], CodegenImplementer.seed_lock_reverts

@case
def the_partial_put_back_costs_no_round_like_the_whole_one():

    with Lock(), Switch(**{KEY: True}), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, _model, seen = run(root, [block(PAGE, "page 1"), seed_answer(INDEPENDENT), block(PAGE, "page 2")],
                                         [own_red("Error: one"), own_red("Error: two"), green()], max_rounds=2)
        assert result.status == "passed", (result, logs[-6:])
        assert len(seen) == 3, seen
        assert any("put back in part by the seed lock; the rest is checked, this round not counted (1 such left)" in line
                   for line in logs), logs

SHORT_SEEDS = "Evaluation seed:" + NL + "- Workbook `Q3 Sales`: the row `East/42`." + NL
SHORT_A = "insert('Q3 Sales', [['East']]);" + NL
SHORT_B = "module.exports = [[42]];" + NL

@case
def codex_east_42_a_short_value_lost_in_the_kept_file_puts_back_whole():

    from cases_v156a import SEED_NODE

    node = dict(SEED_NODE, seed_sentences=SHORT_SEEDS)
    with Lock(), Switch(**{KEY: True}), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp, seed=SHORT_A)
        (root / EXTRA).write_text(SHORT_B, encoding="utf-8")
        answer = block(SEED, "insert('Q3 Sales', [['West']]);") + NL + block(EXTRA, "module.exports = [[99]];")
        result, logs, _model, seen = run(root, [block(PAGE, "page 1"), answer, block(PAGE, "page 2")],
                                         [own_red("Error: one"), green()], node=node)
        lines = lock_lines(logs)
        assert any("stated literals not present afterwards ['42']" in line for line in lines), lines
        assert lines[-1].startswith("[seed-lock] REQ-9 round 1: reverted ") and "put back only" not in "".join(lines), lines
        assert (root / SEED).read_text(encoding="utf-8") == SHORT_A
        assert (root / EXTRA).read_text(encoding="utf-8") == SHORT_B
        assert result.status == "passed" and len(seen) == 2, (result, seen)

@case
def the_raw_literals_are_every_quoted_part_unfiltered():
    from agent.seed_values import stated_literals, stated_seed_values

    assert stated_seed_values(SHORT_SEEDS) == ["Q3 Sales", "East"], stated_seed_values(SHORT_SEEDS)
    assert stated_literals(SHORT_SEEDS) == ["Q3 Sales", "East", "42"], stated_literals(SHORT_SEEDS)

@case
def files_that_name_each_other_are_put_back_whole():
    assert_as_v181(*whole_rollback(NAMES_SEED), partial_logged=True)

@case
def a_kept_file_using_a_token_the_put_back_change_introduced_is_put_back_whole():
    assert_as_v181(*whole_rollback(USES_NEW_TOKEN), partial_logged=True)

@case
def a_kept_file_holding_a_stated_seed_value_is_put_back_whole():

    result = whole_rollback("module.exports = [['Q3 Sales', 'Budget'], ['West', 900]];")
    assert any("holds stated seed values" in line for line in result[1]), lock_lines(result[1])
    assert_as_v181(*result, partial_logged=True)

@case
def an_incomplete_snapshot_after_the_partial_put_back_puts_back_whole():
    import agent.codegen as codegen

    real, calls = codegen.app_snapshot, []

    def snapshot(project_dir):
        calls.append(1)
        files, problem = real(project_dir)
        return (files, "read backend/x.js: PermissionError") if len(calls) >= 2 else (files, problem)

    assert_as_v181(*whole_rollback(INDEPENDENT, patches=[Patch(codegen, "app_snapshot", snapshot)]), partial_logged=True)

@case
def a_value_still_missing_after_the_partial_put_back_puts_back_whole():
    import agent.seed_values as seed_values

    real, calls = seed_values.lost_values, []

    def lost(seeds, before, after):
        calls.append(1)
        found = real(seeds, before, after)
        return ["East"] if len(calls) == 3 else found

    result = whole_rollback(INDEPENDENT, patches=[Patch(seed_values, "lost_values", lost)])
    assert any("still missing ['East']" in line for line in result[1]), lock_lines(result[1])
    assert_as_v181(*result, partial_logged=True)

@case
def a_seed_only_answer_of_one_file_is_put_back_as_today():
    with Lock(), Switch(**{KEY: True}), tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        result, logs, _model, seen = run(root, [block(PAGE, "page 1"), block(SEED, REWRITE), block(PAGE, "page 2")],
                                         [own_red("Error: one"), green()])
        assert result.status == "passed" and len(seen) == 2, (result, seen)
        assert lock_lines(logs) == [f"[seed-lock] REQ-9 round 1: reverted ['{SEED}']: they removed stated seed values "
                                    "['Region', 'Amount', 'East', '1200']"], lock_lines(logs)

@case
def the_switch_off_puts_back_the_whole_answer_as_v181():
    assert_as_v181(*whole_rollback(INDEPENDENT, on=False), partial_logged=False)

@case
def the_switch_is_on_when_not_set():
    from agent.settings import SETTINGS, setting_on

    saved = (KEY in SETTINGS, SETTINGS.get(KEY))
    SETTINGS.pop(KEY, None)
    try:
        assert setting_on(KEY, True)
    finally:
        if saved[0]:
            SETTINGS[KEY] = saved[1]

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} v182 seed-lock partial cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
