from __future__ import annotations

from typing import Any

def _steps(node: dict[str, Any]) -> int:
    return sum(len(s.get("steps") or []) for s in node.get("scenarios") or [])

def _render_leaf(leaf: dict[str, Any]) -> str:
    lines = [f"### {leaf.get('id')} {leaf.get('name', '')}".strip()]
    description = str(leaf.get("description") or "").strip()
    if description:
        lines.append(description)
    deps = leaf.get("dependencies") or []
    if deps:
        lines.append("Depends on: " + ", ".join(str(d) for d in deps))
    for index, scenario in enumerate(leaf.get("scenarios") or [], start=1):
        lines.append(f"Scenario {index}: {scenario.get('name', '')}")
        for step in scenario.get("steps") or []:
            lines.append(f"  {step.get('keyword', '')}: {str(step.get('content', '')).strip()}")
    return "\n".join(lines)

BIG_LEAF_CHARS = 1500

def _size(node: dict[str, Any]) -> int:
    text = _render_leaf(node)
    marker = text.find("Visual reference notes for")
    return len(text if marker < 0 else text[:marker])

def build_batches(tree: dict[str, Any], *, max_steps: int = 12, max_leaves: int = 3, max_chars: int = 4000, solo: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []

    def flush(parent: dict[str, Any], chunk: list[dict[str, Any]], part: int, split: bool) -> None:
        if not chunk:
            return
        parent_id = str(parent.get("id") or "ROOT")
        batch_id = f"{parent_id}.part{part}" if split else (parent_id if parent_id != "ROOT" else str(chunk[0].get("id")))
        if len(chunk) == 1:
            batch_id = str(chunk[0].get("id"))
        batches.append(
            {
                "id": batch_id,
                "name": f"{parent.get('name', '')} ({', '.join(str(c.get('id')) for c in chunk)})",
                "description": "This batch implements these sibling requirements together:\n\n" + "\n\n".join(_render_leaf(c) for c in chunk),
                "scenarios": [],
                "members": [str(c.get("id")) for c in chunk],
                "trace_req_id": str(chunk[0].get("id")),
                "ancestor_context": chunk[0].get("ancestor_context", ""),
                "_leaves": list(chunk),
                "_parent_name": str(parent.get("name") or ""),
            }
        )

    def walk(node: dict[str, Any]) -> None:
        children = [c for c in node.get("children") or [] if isinstance(c, dict)]
        if not children:
            return
        leaf_run: list[dict[str, Any]] = []
        chunks: list[list[dict[str, Any]]] = []

        def close_run() -> None:
            nonlocal leaf_run
            current: list[dict[str, Any]] = []
            steps = 0
            chars = 0
            first_new = len(chunks)
            for leaf in leaf_run:

                if _size(leaf) > BIG_LEAF_CHARS or str(leaf.get("id")) in solo:

                    if current:
                        chunks.append(current)
                        current, steps, chars = [], 0, 0
                    chunks.append([leaf])
                    continue
                if current and (steps + _steps(leaf) > max_steps or len(current) >= max_leaves or chars + _size(leaf) > max_chars):
                    chunks.append(current)
                    current, steps, chars = [], 0, 0
                current.append(leaf)
                steps += _steps(leaf)
                chars += _size(leaf)
            if current:

                if (
                    len(current) == 1
                    and len(chunks) > first_new
                    and len(chunks[-1]) < max_leaves
                    and sum(_size(c) for c in chunks[-1]) + chars <= max_chars * 1.3
                    and sum(_steps(c) for c in chunks[-1]) + steps <= max_steps
                ):
                    chunks[-1].extend(current)
                else:
                    chunks.append(current)
            leaf_run = []

        pending: list[tuple[str, Any]] = []
        for child in children:
            if child.get("children"):
                close_run()
                pending.append(("chunks", list(chunks)))
                chunks.clear()
                pending.append(("folder", child))
            else:
                leaf_run.append(child)
        close_run()
        pending.append(("chunks", list(chunks)))

        total_chunks = sum(len(item) for kind, item in pending if kind == "chunks")
        part = 0
        for kind, item in pending:
            if kind == "folder":
                walk(item)
            else:
                for chunk in item:
                    part += 1
                    flush(node, chunk, part, split=total_chunks > 1)

    walk(tree)

    merged: list[dict[str, Any]] = []
    for batch in batches:
        previous = merged[-1] if merged else None
        leaves = batch["_leaves"]
        if (
            previous is not None
            and len(previous["_leaves"]) + len(leaves) <= max_leaves
            and all(_size(leaf) <= BIG_LEAF_CHARS and str(leaf.get("id")) not in solo for leaf in previous["_leaves"] + leaves)
            and sum(_size(leaf) for leaf in previous["_leaves"] + leaves) <= max_chars
            and sum(_steps(leaf) for leaf in previous["_leaves"] + leaves) <= max_steps
        ):
            previous["_leaves"] = previous["_leaves"] + leaves
            extra_context = str(batch.get("ancestor_context") or "")
            if extra_context and extra_context not in str(previous.get("ancestor_context") or ""):

                known = str(previous.get("ancestor_context") or "")
                fresh = [part for part in extra_context.split("\n\n") if part and part not in known]
                previous["ancestor_context"] = "\n\n".join([known] + fresh).strip()
            if batch["_parent_name"] not in previous["_parent_name"]:
                previous["_parent_name"] += " + " + batch["_parent_name"]
            continue
        merged.append(batch)
    for batch in merged:
        leaves = batch.pop("_leaves")
        batch["_deps"] = sorted({str(d) for leaf in leaves for d in (leaf.get("dependencies") or [])})
        parent_name = batch.pop("_parent_name")
        if len(leaves) != len(batch["members"]):
            ids = [str(leaf.get("id")) for leaf in leaves]
            batch.update(
                {
                    "id": f"{ids[0]}+{len(ids) - 1}",
                    "name": f"{parent_name} ({', '.join(ids)})",
                    "description": "This batch implements these neighbouring requirements together:\n\n" + "\n\n".join(_render_leaf(leaf) for leaf in leaves),
                    "members": ids,
                    "trace_req_id": ids[0],
                }
            )
    ordered, cyclic = _ordered(merged)

    split = {str(m) for batch in cyclic if len(batch.get("members") or []) > 1 for m in batch["members"]} - set(solo)
    if split:
        return build_batches(tree, max_steps=max_steps, max_leaves=max_leaves, max_chars=max_chars, solo=frozenset(solo) | split)
    return ordered

def _covers(dependency: str, member: str) -> bool:
    return member == dependency or any(member.startswith(dependency + s) for s in ".-")

def order_by_dependencies(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _ordered(batches)[0]

def transitive_dependents(leaves: list[dict[str, Any]]) -> dict[str, set[str]]:
    ids = [str(leaf.get("id") or "").strip() for leaf in leaves if isinstance(leaf, dict)]
    ids = [i for i in ids if i]
    direct: dict[str, set[str]] = {i: set() for i in ids}
    for leaf in leaves:
        if not isinstance(leaf, dict):
            continue
        leaf_id = str(leaf.get("id") or "").strip()
        wanted = leaf.get("dependencies") or []
        if not leaf_id or not isinstance(wanted, list):
            continue
        for dependency in (str(d).strip() for d in wanted):
            if dependency:
                direct[leaf_id].update(other for other in ids if other != leaf_id and _covers(dependency, other))
    dependents: dict[str, set[str]] = {i: set() for i in ids}
    for leaf_id in ids:
        stack, seen = list(direct.get(leaf_id, ())), set()
        while stack:
            base = stack.pop()
            if base in seen:
                continue
            seen.add(base)
            if base != leaf_id:
                dependents[base].add(leaf_id)
            stack.extend(direct.get(base, ()))
    return dependents

def _ordered(batches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    needs: list[set[int]] = []
    for index, batch in enumerate(batches):
        wanted = set()
        for dependency in batch.pop("_deps", []):
            for other, candidate in enumerate(batches):
                if other != index and any(_covers(dependency, str(m)) for m in candidate.get("members") or []):
                    wanted.add(other)
        needs.append(wanted)
    def on_cycle(start: int) -> bool:
        seen, todo = set(), list(needs[start])
        while todo:
            current = todo.pop()
            if current == start:
                return True
            if current not in seen:
                seen.add(current)
                todo.extend(needs[current])
        return False

    placed: list[int] = []
    left = list(range(len(batches)))
    while left:
        ready = next((i for i in left if needs[i] <= set(placed)), None)
        if ready is None:
            ready = left[0]
        placed.append(ready)
        left.remove(ready)
    return [batches[i] for i in placed], [batches[i] for i in range(len(batches)) if on_cycle(i)]
