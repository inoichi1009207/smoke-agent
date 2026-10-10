from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

CASES = []
REL = "frontend/src/components/IssueReactions.tsx"
REQ = ("Activating \u201cAdd reaction\u201d opens an element with the `menu` role whose \u201c+1\u201d choice has the "
       "`menuitem` role. The issue detail displays the reaction type and count, and the user's own reaction is "
       "represented by a button. An unauthenticated visitor can view existing reaction counts on a public issue but "
       "cannot activate a reaction control.")

FORK = """function IssueReactions({ reactions, signedIn }: Props) {
  const plusOne = reactions?.['+1'] ?? { count: 0, mine: false };
  return (
    <ul className="list-none">
      <li className="inline-flex items-center gap-1 rounded-full border border-gray-300 px-3 py-0.5 text-sm">
        <span>+1</span>
        <span>{plusOne.count}</span>
        {plusOne.mine ? (
          <button type="button" aria-label="Remove +1 reaction" onClick={toggle}>
            Remove +1 reaction
          </button>
        ) : null}
      </li>
    </ul>
  );
}
"""
BARE_LINE = "<span>{plusOne.count}</span>"

OFFICIAL = """function IssueReactions({ detailPath }: { detailPath: string }) {
  const plus = data?.reactions?.['+1'];
  const count = plus?.count ?? 0;
  return (
    <span className="mt-2 flex items-center gap-2">
      {count > 0 ? (
        mine ? (
          <button type="button" onClick={toggle}>Remove +1 reaction</button>
        ) : (
          <span className="rounded border border-gray-400 px-2 py-0.5 text-sm">+1</span>
        )
      ) : null}
      {count > 0 ? <span className="text-sm text-gray-700">{count}</span> : null}
    </span>
  );
}
"""

def case(fn):
    CASES.append(fn)
    return fn

def _run(files: dict[str, str], requirement: str = REQ, before: dict[str, str] | None = None) -> list[dict]:
    from agent.count_text_check import count_text_findings

    return count_text_findings(files, before or {}, requirement)

@case
def nouns_are_read_from_the_count_phrases():
    from agent.count_text_check import stated_counted_nouns

    assert stated_counted_nouns(REQ) == ["reaction"], stated_counted_nouns(REQ)
    assert stated_counted_nouns("The repository shows the star counts.") == ["star"]
    assert stated_counted_nouns("The page shows the label type and count.") == ["label"]

    assert stated_counted_nouns("Then the issue shows the updated count. The count stays after reload.") == []
    assert stated_counted_nouns("The page exposes a link named Commits; a commit count may accompany the label.") == []

@case
def the_fork_snippet_fires():
    found = _run({REL: FORK})
    assert len(found) == 1, found
    f = found[0]
    assert f["check"] == "count_text" and f["where"].startswith("count_text " + REL + ":"), f
    assert "bare number" in f["problem"] and "'<n> reaction' / '<n> reactions'" in f["fix"] and "0 reactions" in f["fix"], f

@case
def the_official_hidden_at_zero_fires():
    found = _run({REL: OFFICIAL})
    assert len(found) == 1 and "hides the reaction count at zero" in found[0]["problem"], found
    for guard in ("{count && <span>{count} reactions</span>}", "{!!count && <span>{count} reactions</span>}",
                  "{count ? <span>{count} reactions</span> : null}", "{count !== 0 && <span>{count} reactions</span>}",
                  "{reactionCount >= 1 ? (<span>{reactionCount} reactions</span>) : ''}"):
        assert _run({REL: f"export const R = () => <div>{guard}</div>;\n"}), guard

@case
def other_bare_shapes_fire():
    for expr in ("{r.count}", "{reactionCount}", "{reactions.length}", "{reaction_count}", "{plus?.count ?? 0}",
                 "{item.reactions['+1'].count}"):
        assert _run({REL: FORK.replace(BARE_LINE, f"<strong>{expr}</strong>")}), expr

@case
def the_fixed_forms_are_silent():
    for fixed in ("<span>{`${plusOne.count} ${plusOne.count === 1 ? 'reaction' : 'reactions'}`}</span>",
                  "<span>{plusOne.count} reaction{plusOne.count === 1 ? '' : 's'}</span>",
                  "<span>{plusOne.count} reactions</span>",
                  "<span>{reactionLabel(plusOne.count)}</span>"):
        assert not _run({REL: FORK.replace(BARE_LINE, fixed)}), fixed

@case
def other_things_and_list_switches_are_silent():

    for body in ("<span>{starCount}</span>", "<span>{repo.stars.length}</span>",
                 "{reactions.length > 0 ? <ul>{rows}</ul> : <p>No reactions yet</p>}",
                 "<span>{issue.number}</span>", "{open ? <span>{reactions}</span> : null}"):
        text = FORK.replace(BARE_LINE, body)
        assert not _run({REL: text}), body

    assert not _run({"frontend/src/components/Badge.tsx": "export const B = ({ count }) => <span>{count}</span>;\n"})

@case
def no_count_phrase_in_the_requirement_is_silent():
    assert not _run({REL: FORK}, requirement="The issue detail shows a +1 button; activating it toggles the reaction.")
    assert not _run({REL: OFFICIAL}, requirement="The issue shows the updated count after reload.")
    assert not _run({REL: FORK}, requirement="A reaction count may accompany the type.")

@case
def a_spot_the_file_had_before_the_batch_is_silent():
    assert not _run({REL: FORK}, before={REL: FORK})
    assert _run({REL: FORK}, before={REL: FORK.replace(BARE_LINE, "<span>+1</span>")})

S = Path(os.environ.get("V184_SCRATCH", "local-data/scratchpad"))
DELIVERABLES = (("github", S / "off163/0e93fb7bf95c/template"), ("sheet", S / "off163/8333f603a045/template"))

def _frontend(base: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, dirs, names in os.walk(base / "frontend" / "src"):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "build")]
        for name in names:
            if name.endswith((".tsx", ".jsx", ".ts", ".js")):
                path = Path(root) / name
                out[path.relative_to(base).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return out

@case
def replay_every_leaf_on_the_official_v163_deliverables():
    import yaml

    from agent.batching import _render_leaf
    from agent.boilerplate import leaves_of

    fired: dict[str, int] = {}
    for task, base in DELIVERABLES:
        req = Path(f"local-data/evolution/req/hackathon-evolution--{task}/requirements.yaml")
        if not base.is_dir() or not req.is_file():
            print(f"      (replay {task}: inputs not on this machine -- skipped)")
            continue
        files = _frontend(base)
        for leaf in leaves_of(yaml.safe_load(req.read_text(encoding="utf-8"))):
            got = __import__("agent.count_text_check", fromlist=["x"]).count_text_findings(
                files, {}, _render_leaf(leaf), unbounded=True)
            if got:
                fired[f"{task}:{leaf.get('id')}"] = len(got)
    if DELIVERABLES[0][1].is_dir():
        assert fired == {"github:REQ-5-5": 1}, fired
    else:
        assert not fired, fired

@case
def the_fork_file_on_disk_fires_when_present():
    path = S / "rtcal/probe/fork-gh-2/template" / REL
    if not path.is_file():
        print("      (fork snapshot not on this machine -- skipped)")
        return
    found = _run({REL: path.read_text(encoding="utf-8")})
    assert len(found) == 1 and "bare number" in found[0]["problem"], found

@case
def wiring_when_registered():
    from agent import batch_ui_checks as b

    if "count_text_check" not in b.SETTING_KEYS:
        print("      (count_text_check not wired into batch_ui_findings yet -- wiring cases skipped)")
        return
    from agent.settings import _DEFAULTS

    assert _DEFAULTS.get("count_text_check") is True
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp())
    try:
        (base / REL).parent.mkdir(parents=True, exist_ok=True)
        (base / REL).write_text(FORK, encoding="utf-8")
        node = {"audit_written": [REL]}

        def run(settings):
            return [f for f in b.batch_ui_findings(base, node, REQ, log=lambda *_: None, settings=settings)
                    if f.get("check") == "count_text"]

        assert len(run({})) == 1
        assert not run({"count_text_check": False})
    finally:
        shutil.rmtree(base, ignore_errors=True)

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} v184 count_text cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
