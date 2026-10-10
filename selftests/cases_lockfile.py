from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"
DEP_MAPS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
CASES = []

def case(function):
    CASES.append(function)
    return function

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_PARTIAL = re.compile(r"^v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:\+[0-9A-Za-z.-]+)?$")
_COMPARATOR = re.compile(r"^(\^|~>?|>=|<=|>|<|=)?\s*(\S+)$")

def _parse_version(text: str):
    found = _VERSION.match(text.strip())
    if not found or found.group(4):
        return None
    return tuple(int(part) for part in found.group(1, 2, 3))

def _partial(text: str):
    found = _PARTIAL.match(text)
    if not found:
        return False
    parts = [None if part is None or part in "xX*" else int(part) for part in found.groups()]
    for index in range(1, 3):
        if parts[index - 1] is None:
            parts[index] = None
    return parts

def _bump(parts, index):
    head = list(parts[: index + 1])
    head[index] += 1
    return tuple(head + [0] * (2 - index))

def _desugar(op: str, text: str):
    parts = _partial(text)
    if parts is False:
        return None
    major, minor, patch = parts
    known = sum(part is not None for part in parts)
    low = (major or 0, minor or 0, patch or 0)
    if op in ("", "="):
        if known == 0:
            return []
        if known == 3:
            return [("=", low)]
        return [(">=", low), ("<", _bump(parts, known - 1))]
    if op in ("~", "~>"):
        if known == 0:
            return []
        return [(">=", low), ("<", _bump(parts, 0 if known == 1 else 1))]
    if op == "^":
        if known == 0:
            return []
        if major != 0 or known == 1:
            return [(">=", low), ("<", _bump(parts, 0))]
        if minor != 0 or known == 2:
            return [(">=", low), ("<", _bump(parts, 1))]
        return [(">=", low), ("<", _bump(parts, 2))]
    if known == 0:
        return [("<", (0, 0, 0))] if op in ("<", ">") else []
    if op == ">=":
        return [(">=", low)]
    if op == "<":
        return [("<", low)]
    if op == ">":
        return [(">", low)] if known == 3 else [(">=", _bump(parts, known - 1))]
    if op == "<=":
        return [("<=", low)] if known == 3 else [("<", _bump(parts, known - 1))]
    return None

def _comparator_set(text: str):
    text = text.strip()
    hyphen = re.match(r"^(\S+)\s+-\s+(\S+)$", text)
    if hyphen:
        low, high = _partial(hyphen.group(1)), _partial(hyphen.group(2))
        if low is False or high is False:
            return None
        result = [(">=", tuple(part or 0 for part in low))]
        known = sum(part is not None for part in high)
        if known == 3:
            result.append(("<=", tuple(high)))
        elif known:
            result.append(("<", _bump(high, known - 1)))
        return result
    text = re.sub(r"(\^|~>?|>=|<=|>|<|=)\s+", r"\1", text)
    result = []
    for token in text.split():
        found = _COMPARATOR.match(token)
        if not found:
            return None
        piece = _desugar(found.group(1) or "", found.group(2))
        if piece is None:
            return None
        result += piece
    return result

def satisfies(version: str, spec: str):
    have = _parse_version(version)
    if have is None or not isinstance(spec, str):
        return None
    tests = {"=": lambda a, b: a == b, ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
             "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}
    sets = []
    for alternative in spec.split("||"):
        comparators = _comparator_set(alternative)
        if comparators is None:
            return None
        sets.append(comparators)
    return any(all(tests[op](have, bound) for op, bound in comparators) for comparators in sets)

def _parent(location: str):
    if location == "":
        return None
    index = location.rfind("node_modules/")
    if index < 0:
        return ""
    return location[: index].rstrip("/")

def resolve(packages: dict, location: str, name: str):
    looked = []
    here = location
    while here is not None:
        key = f"{here}/node_modules/{name}" if here else f"node_modules/{name}"
        looked.append(key)
        if key in packages:
            return key, looked
        here = _parent(here)
    return None, looked

def _version_of(packages: dict, key: str):
    entry = packages.get(key) or {}
    if entry.get("link"):
        entry = packages.get(entry.get("resolved", ""), {})
    return entry.get("version", "")

def lockfile_problems(package_dir: Path, manifest_name: str = "package.json", lock_name: str = "package-lock.json") -> list[str]:
    package_dir = Path(package_dir)
    try:
        manifest = json.loads((package_dir / manifest_name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{manifest_name}: cannot be read ({exc})"]
    try:
        lock = json.loads((package_dir / lock_name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{lock_name}: cannot be read ({exc})"]
    packages = lock.get("packages")
    if lock.get("lockfileVersion") not in (2, 3) or not isinstance(packages, dict):
        return [f"{lock_name}: lockfileVersion {lock.get('lockfileVersion')!r} without a packages map; npm 7+ rebuilds it from the registry"]
    problems = []
    root = packages.get("", {})

    for field in DEP_MAPS:
        declared, locked = manifest.get(field) or {}, root.get(field) or {}
        if declared != locked:
            extra = sorted(set(locked) - set(declared))
            missing = sorted(set(declared) - set(locked))
            changed = sorted(name for name in set(declared) & set(locked) if declared[name] != locked[name])
            problems.append(f'root "" {field} differs from package.json: not in package.json {extra}, not in the lock root '
                            f"{missing}, other range {changed}")

    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (manifest.get(field) or {}).items():
            key = f"node_modules/{name}"
            if key not in packages:
                problems.append(f"{field} {name}@{spec}: no {key} entry in the lock")
                continue
            version = _version_of(packages, key)
            if satisfies(version, spec) is False:
                problems.append(f"{field} {name}@{spec}: the lock has {version}")

    check_ranges = not manifest.get("overrides")
    for location, entry in packages.items():
        if not isinstance(entry, dict) or entry.get("link"):
            continue
        if "node_modules/" in location:
            if not entry.get("inBundle") and not (entry.get("resolved") and entry.get("integrity")):
                problems.append(f"{location}: no resolved/integrity; npm would ask the registry for it")
        if location == "":
            continue
        bundled = entry.get("bundleDependencies") or entry.get("bundledDependencies") or []
        optional_peers = {name for name, meta in (entry.get("peerDependenciesMeta") or {}).items() if (meta or {}).get("optional")}
        wanted = [(name, spec, "dependency", False) for name, spec in (entry.get("dependencies") or {}).items()]
        wanted += [(name, spec, "optional dependency", True) for name, spec in (entry.get("optionalDependencies") or {}).items()]
        wanted += [(name, spec, "peer", name in optional_peers) for name, spec in (entry.get("peerDependencies") or {}).items()]
        seen = set()
        for name, spec, kind, optional in wanted:
            if name in seen or bundled is True or name in bundled:
                continue
            seen.add(name)
            key, looked = resolve(packages, location, name)
            if key is None:
                if not optional:
                    problems.append(f"{location}: {kind} {name}@{spec} is not in the lock (looked at {', '.join(looked)})")
                continue
            if kind == "dependency" and check_ranges:
                version = _version_of(packages, key)
                if satisfies(version, spec) is False:
                    problems.append(f"{location}: dependency {name}@{spec} resolves to {key} at {version}")
    return problems

def _entry(version: str, **fields) -> dict:
    return {"version": version, "resolved": f"https://registry.npmjs.org/x/-/x-{version}.tgz",
            "integrity": "sha512-" + "A" * 86 + "==", **fields}

def _project(tmp: str, manifest: dict, packages: dict) -> Path:
    folder = Path(tmp)
    root = {"name": "app", "version": "1.0.0"}
    root.update({field: manifest[field] for field in DEP_MAPS if field in manifest})
    (folder / "package.json").write_text(json.dumps({"name": "app", "version": "1.0.0", **manifest}), encoding="utf-8")
    lock = {"name": "app", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": {"": root, **packages}}
    (folder / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return folder

def _set_lock_root(folder: Path, **fields) -> None:
    lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
    lock["packages"][""].update(fields)
    (folder / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")

@case
def the_template_frontend_lock_is_complete():
    problems = lockfile_problems(TEMPLATE / "frontend")
    assert problems == [], f"{len(problems)} problems, first: {problems[:5]}"

@case
def the_template_backend_lock_is_complete():
    problems = lockfile_problems(TEMPLATE / "backend")
    assert problems == [], f"{len(problems)} problems, first: {problems[:5]}"

@case
def a_complete_lock_has_no_problems():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"dependencies": {"a": "^1.2.0"}, "devDependencies": {"b": "~2.0.1"}},
                          {"node_modules/a": _entry("1.4.0", dependencies={"c": ">=3 <4"}),
                           "node_modules/b": _entry("2.0.9", dev=True), "node_modules/c": _entry("3.1.0")})
        assert lockfile_problems(folder) == []

@case
def the_v142_frontend_is_reproduced_and_caught():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"devDependencies": {"vite": "^7.2.4"}}, {"node_modules/vite": _entry("7.3.0", dev=True)})
        manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        manifest["devDependencies"].update({"vitest": "^4.0.16", "@testing-library/react": "^16.3.0"})
        (folder / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        problems = lockfile_problems(folder)
        assert any(p.startswith("devDependencies vitest@^4.0.16: no node_modules/vitest") for p in problems), problems
        assert any("@testing-library/react" in p and "no node_modules/@testing-library/react" in p for p in problems), problems
        assert any(p.startswith('root "" devDependencies differs') and "'vitest'" in p for p in problems), problems

@case
def a_declared_dependency_missing_from_the_lock_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"dependencies": {"a": "^1.0.0", "b": "^1.0.0"}}, {"node_modules/a": _entry("1.0.0")})
        assert lockfile_problems(folder) == ["dependencies b@^1.0.0: no node_modules/b entry in the lock"]

@case
def a_declared_dependency_at_a_version_outside_its_range_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"dependencies": {"a": "^2.0.0"}}, {"node_modules/a": _entry("1.9.0")})
        assert lockfile_problems(folder) == ["dependencies a@^2.0.0: the lock has 1.9.0"]

@case
def an_unrecognised_range_is_checked_for_presence_only():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"dependencies": {"a": "npm:other@^1.0.0", "b": "github:owner/b#main", "c": "latest"}},
                          {"node_modules/a": _entry("7.0.0"), "node_modules/b": _entry("0.0.1"), "node_modules/c": _entry("3.0.0")})
        assert lockfile_problems(folder) == []

@case
def a_transitive_dependency_missing_from_the_lock_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"devDependencies": {"vitest": "^4.0.16"}},
                          {"node_modules/vitest": _entry("4.0.16", dev=True, dependencies={"std-env": "^3.10.0", "tinyspy": "^4"}),
                           "node_modules/tinyspy": _entry("4.0.3", dev=True)})
        problems = lockfile_problems(folder)
        assert problems == ["node_modules/vitest: dependency std-env@^3.10.0 is not in the lock "
                            "(looked at node_modules/vitest/node_modules/std-env, node_modules/std-env)"], problems

@case
def nested_node_modules_resolve_as_npm_resolves_them():
    with tempfile.TemporaryDirectory() as tmp:
        packages = {
            "node_modules/a": _entry("1.0.0", dependencies={"b": "^2.0.0"}),
            "node_modules/a/node_modules/b": _entry("2.1.0", dependencies={"d": "^1.0.0"}),
            "node_modules/a/node_modules/d": _entry("1.0.0"),
            "node_modules/b": _entry("1.5.0"),
            "node_modules/@s/c": _entry("1.0.0", dependencies={"b": "^1.0.0"}),
        }
        folder = _project(tmp, {"dependencies": {"a": "^1.0.0", "b": "^1.0.0", "@s/c": "^1.0.0"}}, packages)
        assert lockfile_problems(folder) == []

        packages["node_modules/@s/c"] = _entry("1.0.0", dependencies={"b": "^2.0.0"})
        folder = _project(tmp, {"dependencies": {"a": "^1.0.0", "b": "^1.0.0", "@s/c": "^1.0.0"}}, packages)
        assert lockfile_problems(folder) == ["node_modules/@s/c: dependency b@^2.0.0 resolves to node_modules/b at 1.5.0"]

        packages["node_modules/@s/c"] = _entry("1.0.0", dependencies={"d": "^1.0.0"})
        folder = _project(tmp, {"dependencies": {"a": "^1.0.0", "b": "^1.0.0", "@s/c": "^1.0.0"}}, packages)
        assert lockfile_problems(folder) == ["node_modules/@s/c: dependency d@^1.0.0 is not in the lock "
                                             "(looked at node_modules/@s/c/node_modules/d, node_modules/d)"]

@case
def a_root_entry_that_differs_from_package_json_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _project(tmp, {"devDependencies": {"playwright": "^1.28.0"}}, {"node_modules/playwright": _entry("1.57.0", dev=True),
                                                                               "node_modules/playwright-chromium": _entry("1.57.0", dev=True)})
        _set_lock_root(folder, devDependencies={"playwright": "^1.28.0", "playwright-chromium": "^1.28.0"})
        assert lockfile_problems(folder) == ['root "" devDependencies differs from package.json: not in package.json '
                                             "['playwright-chromium'], not in the lock root [], other range []"]
        _set_lock_root(folder, devDependencies={"playwright": "^1.27.0"})
        assert lockfile_problems(folder) == ['root "" devDependencies differs from package.json: not in package.json [], '
                                             "not in the lock root [], other range ['playwright']"]

@case
def missing_optional_packages_and_optional_peers_are_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        packages = {
            "node_modules/esbuild": _entry("0.27.2", optionalDependencies={"@esbuild/win32-x64": "0.27.2", "@esbuild/aix-ppc64": "0.27.2"}),
            "node_modules/@esbuild/win32-x64": _entry("0.27.2", optional=True, os=["win32"], cpu=["x64"]),
            "node_modules/bytes": _entry("1.0.0", peerDependencies={"crypto": "^1.0.0-rc.4", "core": "^2.0.0"},
                                         peerDependenciesMeta={"crypto": {"optional": True}}),
            "node_modules/core": _entry("2.0.0"),
            "node_modules/oxide-wasm": _entry("4.1.18", optional=True, bundleDependencies=["tslib"], dependencies={"tslib": "^2.4.0"}),
        }
        folder = _project(tmp, {"dependencies": {"esbuild": "^0.27.0", "bytes": "^1.0.0", "core": "^2.0.0"},
                                "optionalDependencies": {"oxide-wasm": "4.1.18"}}, packages)
        assert lockfile_problems(folder) == []

        del packages["node_modules/core"]
        folder = _project(tmp, {"dependencies": {"esbuild": "^0.27.0", "bytes": "^1.0.0"},
                                "optionalDependencies": {"oxide-wasm": "4.1.18"}}, packages)
        assert lockfile_problems(folder) == ["node_modules/bytes: peer core@^2.0.0 is not in the lock "
                                             "(looked at node_modules/bytes/node_modules/core, node_modules/core)"]

@case
def every_fetched_entry_is_pinned_and_links_are_not():
    with tempfile.TemporaryDirectory() as tmp:
        unpinned = _entry("1.0.0")
        del unpinned["integrity"]
        folder = _project(tmp, {"dependencies": {"a": "^1.0.0", "local": "file:packages/local"}},
                          {"node_modules/a": unpinned,
                           "node_modules/local": {"resolved": "packages/local", "link": True},
                           "packages/local": {"name": "local", "version": "0.1.0", "dependencies": {"a": "^1.0.0"}}})
        assert lockfile_problems(folder) == ["node_modules/a: no resolved/integrity; npm would ask the registry for it"]

@case
def a_lock_without_a_packages_map_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "package.json").write_text(json.dumps({"dependencies": {"a": "^1.0.0"}}), encoding="utf-8")
        (folder / "package-lock.json").write_text(json.dumps({"lockfileVersion": 1, "dependencies": {}}), encoding="utf-8")
        assert len(lockfile_problems(folder)) == 1 and "lockfileVersion 1" in lockfile_problems(folder)[0]
        (folder / "package-lock.json").unlink()
        assert "cannot be read" in lockfile_problems(folder)[0]

@case
def the_semver_ranges_npm_writes_are_read_as_npm_reads_them():
    table = [
        ("1.2.3", "^1.0.0", True), ("2.0.0", "^1.0.0", False), ("0.2.9", "^0.2.3", True), ("0.3.0", "^0.2.3", False),
        ("0.0.3", "^0.0.3", True), ("0.0.4", "^0.0.3", False), ("0.9.0", "^0.x", True), ("1.0.0", "^0.x", False),
        ("1.2.9", "~1.2.3", True), ("1.3.0", "~1.2.3", False), ("1.9.0", "~1", True), ("2.0.0", "~1", False),
        ("3.1.0", ">=3 <4", True), ("4.0.0", ">=3 <4", False), ("4.0.0", ">= 3.0.0", True), ("1.2.3", "1.2.3", True),
        ("1.2.4", "=1.2.3", False), ("1.2.7", "1.2.x", True), ("1.3.0", "1.2", False), ("5.0.0", "*", True),
        ("5.0.0", "", True), ("2.5.0", "^1.0.0 || ^2.0.0", True), ("3.0.0", "^1.0.0 || ^2.0.0", False),
        ("20.1.0", "20 || >=22", True), ("21.0.0", "20 || >=22", False), ("1.5.0", "1.2.3 - 2", True),
        ("3.0.0", "1.2.3 - 2", False), ("2.3.4", "1 - 2.3.4", True), ("2.3.5", "1 - 2.3.4", False),
        ("1.2.4", ">1.2.3", True), ("1.2.3", ">1.2.3", False), ("1.3.0", ">1.2", True), ("1.2.9", ">1.2", False),
        ("1.2.9", "<=1.2", True), ("1.3.0", "<=1.2", False), ("0.9.9", "<1", True), ("1.0.0", "<1", False),
    ]
    wrong = [(version, spec, satisfies(version, spec)) for version, spec, expected in table if satisfies(version, spec) is not expected]
    assert not wrong, wrong
    for version, spec in (("1.0.0-rc.4", "^1.0.0"), ("1.0.0", "^1.0.0-rc.4"), ("1.0.0", "npm:x@1"), ("1.0.0", "latest"),
                          ("1.0.0", "git+https://x/y.git"), ("1.0.0", "file:../x"), ("", "^1.0.0")):
        assert satisfies(version, spec) is None, (version, spec)

def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        problems = lockfile_problems(Path(sys.argv[2]))
        for problem in problems:
            print(problem)
        print(f"{len(problems)} lockfile problems in {sys.argv[2]}")
        return 1 if problems else 0
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    started = time.time()
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
