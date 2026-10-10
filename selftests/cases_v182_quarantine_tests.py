from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb
import cases_v145a as v145a

NL = chr(10)
CASES = []
SPEC = "backend/test-e2e/REQ-1-1-3.spec.js"
CONFLICT = "REQ-1-1-3: Scenario 1"

def case(function):
    CASES.append(function)
    return function

def quarantine(output: str, on: bool = True, large: bool = True):
    with tempfile.TemporaryDirectory() as tmp, v145a.Settings(seed_quarantine_per_test=on, partial_green_guard=True,
                                                              seed_conflict_quarantine=True):
        root = Path(tmp)
        f = rb.make_factory(root, "x")
        f.large_tree = large
        f.e2e_specs = [rb.F1, SPEC]
        f.green_specs = [rb.F1, SPEC]
        f.file_specs = {rb.APP: [SPEC]}
        (root / SPEC).parent.mkdir(parents=True, exist_ok=True)
        (root / SPEC).write_text("test('a', async () => {});" + NL, encoding="utf-8")
        f.quarantine_seed_conflict("REQ-9", ["REQ-1-1-3.spec.js"], ["East"], output=output)
        return f, dict(f.__dict__.get("partial_green") or {})

@case
def only_the_conflicting_test_leaves_the_regression_checks():
    f, store = quarantine(rb.spec_output("REQ-1-1-3.spec.js", [CONFLICT]))
    assert SPEC not in f.green_specs and "REQ-1-1-3.spec.js" in f.seed_quarantine, f.green_specs
    entry = store.get(SPEC)
    assert entry and entry["titles"] is None and entry["red"] == [CONFLICT] and entry["written"] == [rb.APP], store

@case
def unreadable_titles_a_small_tree_or_off_keep_the_whole_file_out():
    cut = rb.spec_output("REQ-1-1-3.spec.js", [CONFLICT]) + NL + "[middle of the output omitted]"
    for kwargs, output in (({}, cut), ({"large": False}, rb.spec_output("REQ-1-1-3.spec.js", [CONFLICT])),
                           ({"on": False}, rb.spec_output("REQ-1-1-3.spec.js", [CONFLICT]))):
        f, store = quarantine(output, **kwargs)
        assert SPEC not in f.green_specs and SPEC not in store, (kwargs, store)

@case
def codex_v182_r1_a_quarantine_entry_never_takes_the_slots_of_an_older_partial_entry():

    from agent import verify

    older = "backend/test-e2e/REQ-2-1-1.spec.js"
    other = "frontend/src/Other.tsx"
    with tempfile.TemporaryDirectory() as tmp, v145a.Settings(seed_quarantine_per_test=True, partial_green_guard=True):
        root = Path(tmp)
        f = rb.make_factory(root, "x")
        f.large_tree = True
        for spec in (older, SPEC):
            (root / spec).parent.mkdir(parents=True, exist_ok=True)
            (root / spec).write_text(f"test('{spec}', async () => {{}});" + NL, encoding="utf-8")
        (root / other).parent.mkdir(parents=True, exist_ok=True)
        (root / other).write_text("other", encoding="utf-8")
        (root / rb.APP).write_text("changed", encoding="utf-8")
        digest = {spec: f.spec_digest((root / spec).read_bytes()) for spec in (older, SPEC)}
        f.__dict__["partial_green"] = {
            older: {"batch": "REQ-2", "titles": None, "red": ["REQ-2-1-1: red one"], "sha1": digest[older], "written": [other]},
            SPEC: {"batch": "REQ-9", "titles": None, "red": [CONFLICT], "sha1": digest[SPEC], "written": [rb.APP],
                   "seed_conflict": True}}
        f.PARTIAL_GROUPS_MAX = 16
        saved = verify.test_groups

        def groups(project_dir, spec, log=print):
            name = Path(spec).name.replace(".spec.js", "")
            return [(line, [(line, 1, f"{name}: green {line}")]) for line in range(1, 17)]
        verify.test_groups = groups
        try:
            chosen = f.partial_green_groups({rb.APP: b"before", other: b"other"}, [])
        finally:
            verify.test_groups = saved
    assert chosen and chosen[0][0] == older and len(chosen[0][1]) == 16, [(s, len(g)) for s, g in chosen]
    assert all(spec != SPEC for spec, _ in chosen), [(s, len(g)) for s, g in chosen]

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok   {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
