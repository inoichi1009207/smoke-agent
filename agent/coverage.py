from __future__ import annotations

import functools
import re
from typing import Any, NamedTuple

from .boilerplate import boilerplate_scenarios, leaves_of, scenarios_of, threshold

CAP = 16
MIN_STATEMENTS = 5
MIN_WITH_GENERATED = 1
MIN_WORDS = 5
MIN_CJK = 10
ANCHOR_CHARS = 110

TAIL = re.compile(r"(?m)^[ \t]*(?:Screenshot references?|Page references?|Visual reference notes for|可选视觉参考|视觉参考|参考图)"
                  r"[\s\S]*$", re.IGNORECASE)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

CLAUSE_END = re.compile("(?<=[.;])\\s+(?=\\S)|(?<=[a-z][!?])\\s+(?=\\S)|(?<=[\ue000\ue001\ue002][\"\u201d\u300d])\\s+(?=[A-Z\u3400-\u9fff])"
                        "|(?<=[\u3002\uff01\uff1f\uff1b;])|\\n[ \\t]*\\n\\s*")
ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|etc|vs)\.", re.IGNORECASE)
QUOTE = re.compile(r"“[^”\n]{1,80}”|\"[^\"\n]{1,80}\"|`[^`\n]{1,80}`|「[^」\n]{1,80}」|『[^』\n]{1,80}』")
QUOTED_SPAN = re.compile(r"“[^”\n]{1,200}”|\"[^\"\n]{1,200}\"|`[^`\n]{1,200}`|「[^」\n]{1,200}」")
_ENDS = ".!?;。！？；"
PROTECT = str.maketrans({c: chr(0xE000 + i) for i, c in enumerate(_ENDS)})
RESTORE = str.maketrans({chr(0xE000 + i): c for i, c in enumerate(_ENDS)})
CJK = re.compile("[㐀-鿿]")
SEED = re.compile(r"\bseed(?:s|ed)?\b|\bevaluation data\b|\bthe system contains\b|\bpre-?existing\b|\bfixtures?\b|种子|预置数据",
                  re.IGNORECASE)
SHOW = re.compile(r"\b(?:show(?:s|n|ing)?|display(?:s|ed|ing)?|visible|visibly|appear(?:s|ed)?|lists?|listed|render(?:s|ed)?|"
                  r"highlight(?:s|ed)?|messages?|errors?|label(?:l)?ed|named|titled|placeholder|badge|count(?:s|er)?|"
                  r"empty state|toast|banner|explain(?:s)?|read(?:s)? (?:as|exactly)|absent|unavailable|summary)\b"
                  r"|显示|展示|提示|可见|出现|列出|标题|错误|消息", re.IGNORECASE)

CONTROL = re.compile(r"\b(?:links?|buttons?|tabs?|textbox(?:es)?|checkbox(?:es)?|combobox(?:es)?|radios?|dialogs?|"
                     r"menu(?:item)?s?|options?|searchbox|headings?|rows?|gridcells?|accessible names?|aria-[a-z]+)\b"
                     r"|按钮|链接|输入框|复选框|下拉|选项|对话框"
                     r"|(?-i:\b(?:Enter|Escape|Esc|Tab|Ctrl\+\w+|Shift\+\w+|Cmd\+\w+)\b)", re.IGNORECASE)

SEEN = re.compile(r"\b(?:show(?:s|n)?|display(?:s|ed)?|visible|appear(?:s)?|render(?:s|ed)?|remain(?:s)?|retain(?:s|ed)?|"
                  r"preserv(?:e|es|ed)|unchanged|after (?:a )?(?:refresh|reload)|ago)\b|显示|可见|展示|刷新后", re.IGNORECASE)
PERSIST = re.compile(r"\b(?:refresh(?:es|ed|ing)?|reload(?:s|ed|ing)?|reopen(?:s|ed|ing)?|restart(?:s|ed)?|remain(?:s|ed)?|"
                     r"retain(?:s|ed)?|keeps?|kept|preserv(?:e|es|ed|ing)|persist(?:s|ed|ing)?|restor(?:e|es|ed)|stays?|still|"
                     r"no longer|survives?)\b|刷新|保留|保持|仍|依然|恢复|持久", re.IGNORECASE)
CONSTRAINT = re.compile(r"\b(?:must|only|never|cannot|can't|not|no|exact(?:ly)?|unique(?:ly)?|at (?:least|most)|required|"
                        r"reject(?:s|ed)?|refus(?:e|es|ed)|prevent(?:s|ed)?|disabled|enabled|invalid|forbid(?:s|den)?|"
                        r"den(?:y|ies|ied)|without)\b|必须|不得|不能|不可|只能|仅|禁止|拒绝|唯一|不会|不应", re.IGNORECASE)
OUTCOME = re.compile(r"\b(?:open(?:s|ed)?|close(?:s|d)?|navigat\w*|redirect\w*|returns?|switch(?:es|ed)?|sort(?:s|ed)?|"
                     r"order(?:s|ed)?|filter(?:s|ed)?|select(?:s|ed)?|updat(?:e|es|ed)|recomput\w*|recalculat\w*|shift(?:s|ed)?|"
                     r"mov(?:e|es|ed)|enter(?:s|ed)?|creat(?:e|es|ed)|delet(?:e|es|ed)|remov(?:e|es|ed)|add(?:s|ed)?|copies|"
                     r"download(?:s|ed)?|upload(?:s|ed)?|cancel(?:s|led|ed)?|commit(?:s|ted)?|export(?:s|ed)?|clear(?:s|ed)?|"
                     r"paste(?:s|d)?|cop(?:y|ied)|undo(?:es|ne)?|redo(?:es|ne)?|toggle(?:s|d)?|expand(?:s|ed)?|collapse(?:s|d)?|"
                     r"combined|case-(?:in)?sensitive)\b|跳转|返回|打开|关闭|排序|筛选|更新|创建|删除|进入|取消|导出|复制|粘贴", re.IGNORECASE)
VALUE = re.compile(r"\d|\bago\b|\byesterday\b|\brelative\b")

BEHIND = re.compile(r"\batomic(?:ally)?\b|\bstor(?:e|es|ed|age)\b|\bdatabase\b|\bbackend\b|\bserver(?:-side)?\b|"
                    r"\bpersistence fails\b|\bidentifiers?\b|\bnot required\b|\bmay be\b|\bis optional\b|\bare optional\b"
                    r"|后端|服务端|接口|数据库", re.IGNORECASE)
REQ_ID = re.compile(r"\bREQ[-_.]?\d+(?:[.-]\d+)*\b", re.IGNORECASE)
PARENS = re.compile(r"\([^()]*\)")
NON_WORD = re.compile(r"[\W_]+")

class Statement(NamedTuple):
    position: int
    score: int
    text: str

def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())

def _protect(match: re.Match) -> str:
    return match.group(0).translate(PROTECT)

ACTION_GERUND = (r"(?:(?:double|right|left|shift|ctrl)-)?(?:press|click|tapp|select|choos|typ|enter|submitt|dragg|dropp|leav|"
                 r"clos|open|reopen|refresh|reload|return|navigat|switch|tabb|mov|hover|scroll|us|activat|past|sav|cancell|"
                 r"cancel|blurr|focus|edit|delet|remov|add|creat|renam|dismiss|confirm|visit|revisit|restart|sign|logg|apply|"
                 r"clear|sort|filter|undo|redo|upload|download|import|export|toggl|expand|collaps|check|uncheck|go)ing")
ALT_START = re.compile(rf"(?i:{ACTION_GERUND})\b")
ALT_PREFIX = re.compile(r"^(?:(?:If|When|Whenever|After|Once|While|Before|On)\b[^,;:]{1,80},\s*)?", re.IGNORECASE)
ALT_RESULT = re.compile(
    r"\s(?:(?:still|also|always|immediately|again|then|only)\s+)?"
    r"(?:(?:must|should|will|shall|can|cannot|does|do)\s+\w+"
    r"|(?:commits|saves|submits|cancels|discards|reverts|closes|opens|reopens|confirms|applies|dismisses|clears|deletes|"
    r"removes|creates|adds|selects|shows|displays|returns|navigates|leads|takes|brings|moves|updates|restores|keeps|"
    r"retains|preserves|produces|hides|expands|collapses|toggles|triggers|starts|stops|exits|ends|finishes|completes|"
    r"copies|pastes|sorts|filters|sends|marks|resets|undoes|redoes|activates|focuses|leaves|yields|gives|renders|reveals|"
    r"loads|reloads|refreshes|persists|remains))\b", re.IGNORECASE)
ALT_SPLIT = re.compile(r"\s*,\s*(?:or\s+)?|\s+or\s+", re.IGNORECASE)
MAX_ALTERNATIVES = 4

def alternatives(clause: str) -> list[str]:
    prefix = ALT_PREFIX.match(clause).group(0)
    rest = clause[len(prefix):]
    if not ALT_START.match(rest):
        return [clause]
    result = ALT_RESULT.search(rest, 1)
    if not result:
        return [clause]
    head, tail = rest[: result.start()], rest[result.start():].strip()
    if not re.search(r"\bor\b", head, re.IGNORECASE):
        return [clause]
    parts = [p.strip() for p in ALT_SPLIT.split(head) if p.strip()]
    if not 2 <= len(parts) <= MAX_ALTERNATIVES or not all(ALT_START.match(p) for p in parts):
        return [clause]
    out = []
    for part in parts:
        text = f"{prefix}{part} {tail}"
        out.append(text[:1].upper() + text[1:] if not prefix else text)
    return out

def _clauses(description: str) -> list[str]:
    text = IMAGE.sub(" ", TAIL.sub("", str(description or "")))

    text = QUOTED_SPAN.sub(_protect, text)
    text = ABBREVIATION.sub(_protect, text)
    out = []
    for part in CLAUSE_END.split(text):
        clause = _flat(part).strip(" ,:").translate(RESTORE)
        if not clause:
            continue
        cjk = len(CJK.findall(clause))
        if cjk >= MIN_CJK or len(clause.split()) >= MIN_WORDS:
            out.append(clause)
    return out

def _key(clause: str) -> str:
    flat = REQ_ID.sub(" ", clause.lower())
    flat = PARENS.sub(" ", flat)
    flat = QUOTE.sub(" value ", flat)
    return " ".join(NON_WORD.sub(" ", flat).split())

def score(clause: str) -> int:
    quotes = len(QUOTE.findall(clause))
    signs = [bool(quotes), bool(SHOW.search(clause)), bool(PERSIST.search(clause)), bool(CONSTRAINT.search(clause)),
             bool(OUTCOME.search(clause)), bool(CONTROL.search(clause))]
    if not any(signs):
        return 0
    rank = (3 * min(quotes, 3) + 3 * signs[2] + 2 * signs[3] + 2 * signs[5] + signs[1] + signs[4]
            + (1 if VALUE.search(clause) else 0) - (2 if BEHIND.search(clause) else 0))
    return max(rank, 1)

def statements_of(description: str, repeated: frozenset[str] = frozenset(), cap: int = CAP) -> list[Statement]:
    found: list[Statement] = []
    seen: set[str] = set()
    for position, clause in enumerate(_clauses(description)):
        same = clause.lower()
        if same in seen or _key(clause) in repeated or (SEED.search(clause) and not SEEN.search(clause)):
            continue
        rank = score(clause)
        if rank <= 0:
            continue
        seen.add(same)
        found.append(Statement(position, rank, clause))
    best = sorted(found, key=lambda s: (-s.score, s.position))[:cap]

    room = cap - len(best)
    expanded: dict[int, list[str]] = {}
    for statement in best:
        parts = alternatives(statement.text)
        if len(parts) > 1 and len(parts) - 1 <= room:
            expanded[statement.position] = parts
            room -= len(parts) - 1
    out = []
    for statement in sorted(best, key=lambda s: s.position):
        for text in expanded.get(statement.position, [statement.text]):
            if text.lower() != statement.text.lower() and text.lower() in seen:
                continue
            out.append(Statement(statement.position, statement.score, text))
    return out

@functools.lru_cache(maxsize=4)
def _analysis(text: str) -> dict[str, tuple[tuple[str, ...], int]]:
    try:
        import yaml
        tree = yaml.safe_load(text)
    except Exception:
        return {}
    if not isinstance(tree, dict):
        return {}
    try:
        leaves = leaves_of(tree)
        spread: dict[str, set[str]] = {}
        for leaf in leaves:
            for clause in _clauses(str(leaf.get("description") or "")):
                spread.setdefault(_key(clause), set()).add(str(leaf.get("id")))
        k = threshold(len(leaves))
        repeated = frozenset(key for key, ids in spread.items() if len(ids) >= k)

        try:
            generated = {leaf_id for leaf_id, _index in boilerplate_scenarios(tree)}
        except Exception:
            generated = set()
        return {str(leaf.get("id")): (tuple(s.text for s in statements_of(str(leaf.get("description") or ""), repeated)),
                                      MIN_WITH_GENERATED if str(leaf.get("id")) in generated else MIN_STATEMENTS)
                for leaf in leaves}
    except Exception:
        return {}

MAX_REFUSALS = 3
MAX_QUALIFIERS = 2
MAX_CLAUSES = 10
MAX_FOLDER = 4
FOLDER_MIN_OVERLAP = 2
ROLE_NOUN = re.compile(r"\b(?:author|owner|creator|administrator|admin|maintainer|moderator|editor|viewer|member|manager|"
                       r"reviewer|assignee|collaborator)s?\b", re.IGNORECASE)

ONLY_ROLE = re.compile(r"(?<!not )\bonly\s+(?P<who>[^.;:()]{2,90}?)\s+(?:may|can|is allowed to|are allowed to)\s+"
                       r"(?!be\b)(?P<act>[^.;]{3,200})", re.IGNORECASE)
LEAD_ROLE = re.compile(r"^(?:(?:an?|the)\s+)?(?P<who>[^.;:()]{2,90}?\b(?:or|and)\b[^.;:()]{1,60}?)\s+(?:may|can)\s+"
                       r"(?!only\b|not\b|also\b)(?P<act>[^.;]{3,200})", re.IGNORECASE)
WHO_SPLIT = re.compile(r"\s*,\s*(?:or\s+|and\s+)?|\s+or\s+|\s+and\s+|\s*/\s*", re.IGNORECASE)
NOT_WHO = re.compile(r"\b(?:is|are|be|been|may|can|have|has|buttons?|links?|fields?|pages?|tabs?|menus?|textbox(?:es)?|"
                     r"options?|one|once|when|if|after|before|then|that|which|who|whose|this|these|those)\b", re.IGNORECASE)
CAP_WORD = re.compile(r"\b[A-Z][a-z]+\b")
VIEW_ACT = re.compile(r"(?:only\s+)?(?:see|view|browse|read|look|access)\b", re.IGNORECASE)
READ_ONLY_ACT = re.compile(r"^(?:only\s+)?(?:view|browse|see|read|look)\b|\b(?:cannot|can't|not)\b", re.IGNORECASE)
ROLE_RUN = re.compile(r"\b[A-Z][a-z]+(?:\s*,\s*[A-Z][a-z]+)+\s*,?\s+(?:or|and)\s+[A-Z][a-z]+")

EXCLUDED_LIST = re.compile(r"(?P<who>[^.;:()]{2,90}?)\s+(?:users?\s+|accounts?\s+)?(?P<modal>may\s+only|can\s+only|cannot|can't|may\s+not)\b",
                           re.IGNORECASE)

ACT_STOP = frozenset("the a an any this that these those their its his her to of in on for from with into it them own".split())
ACT_ANAPHOR = re.compile(r"^(?:do|perform)\s+(?:so|this|that|it)$")
ACT_READ_ONLY = re.compile(r"^(?:view|browse|see|read|look)\b(?!.*(?:\b(?:and|or)\b|,))")
OTHER_ROLES = re.compile(r"\b(?:other|all other|remaining)\s+roles\b|\bnon-[A-Z][a-z]+\b|\beveryone else\b")

_WORD = r"\s+(?!or\b|and\b|may\b|can\b|be\b|is\b|are\b)[\w-]+"
QUALIFIED = (r"(?P<q>(?:current|open|unmerged|existing|active|valid|same|assignable|eligible|published|unpublished|public|"
             r"private|protected|archived|unarchived|verified|draft|pending)(?:" + _WORD + r"){1,3}(?:\s+or\s+[\w-]+)?)"
             r"|(?P<m>(?:current\s+|direct\s+)?members?\s+of\s+(?:the|an?|this|that|its)\s+[\w-]+(?:" + _WORD + r")?)"
             r"|(?P<e>[\w-]+\s+that\s+already\s+exists?\s+in\s+(?:the|an?|this|its)\s+[\w-]+(?:" + _WORD + r")?)")
ADMIT_AFTER = re.compile(r"\b(?:may|can|is allowed to|are allowed to)\s[^.;]*?\bonly\s+(?:(?:to|from|into|for|with)\s+)?"
                         r"(?:(?:an?|the|any)\s+)?(?:" + QUALIFIED + r")", re.IGNORECASE)
ADMIT_PASSIVE = re.compile(r"(?<!not )\bonly\s+(?:(?:an?|the)\s+)?(?:" + QUALIFIED + r")\s+(?:may|can)\s+be\s+\w+", re.IGNORECASE)
NEGATIVE = re.compile(r"(?<![\w$.])notSee\(|\.not\.|toHaveCount\(\s*0\s*\)|toBeHidden\(|toBeDisabled\(|\.status\)?\.toBe\(\s*4\d\d|"
                      r"\b40[0134]\b|\bforbidden\b|\bunauthori[sz]ed\b|\brefused\b|\brejected\b"

                      r"|\.to(?:Be|Equal|StrictEqual)\(\s*(?:0|false|null|undefined|\[\s*\]|''|\"\")\s*\)|toHaveLength\(\s*0\s*\)|"
                      r"toBe(?:Falsy|Null|Undefined)\(|toBeLessThan(?:OrEqual)?\(|toBeGreaterThanOrEqual\(\s*4\d\d|\b4\d\d\b",
                      re.IGNORECASE)

KNOWN_CALLS = frozenset(
    "test expect click doubleClick rightClick drag upload download allowClipboard clipboardText putClipboard locate locateAll "
    "freshSession tagged fill check choose hover press typeText see seeOnce item region inventory require if for while "
    "switch catch function return async await typeof new String Number Boolean Date JSON Math Array Object Promise RegExp "
    "Error parseInt parseFloat isNaN encodeURIComponent decodeURIComponent setTimeout Set Map".split())
BARE_CALL = re.compile(r"(?<![\w$.])(?P<name>[A-Za-z_$][\w$]*)\s*\(")
TAG = re.compile(r"^\[(?P<kind>refusal|counter-example|WHEN|THEN|folder)\b[^\]]*\]\s*")

WHEN_VERB = (r"(?:clicks?|chooses?|selects?|opens?|presses?|enters?|types?|uses?|activates?|submits?|picks?|follows?|"
             r"visits?|navigates?|drags?|checks?|unchecks?|toggles?|expands?|hovers?|double-clicks?|right-clicks?)")
WHEN_ALT = re.compile(rf"\s+or\s+(?:(?:the\s+)?(?:user|visitor|administrator|owner|author)\s+)?(?={WHEN_VERB}\b)", re.IGNORECASE)
WHEN_PARTS = re.compile(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|;\s*|\s+and\s+then\s+|\s+then\s+)(?=(?:(?:the\s+)?\w+\s+)?"
                        + WHEN_VERB + r"\b|confirms?\b)", re.IGNORECASE)
CONFIRM = re.compile(r"\bconfirm(?:s|ing)?\b|\bverif(?:y|ies)\b|\breview(?:s)? the displayed\b", re.IGNORECASE)
THEN_PARTS = re.compile(r"(?<=[.;])\s+(?=\S)|,\s+and\s+(?=(?:the|a|an|its|it|no|nothing|every|each|[A-Z])\b)|;\s*",
                        re.IGNORECASE)
CONTENT_WORD = re.compile(r"[a-z][a-z-]{3,}")
SCENARIO_WORD = re.compile(r"\bscenarios?\b|\bsample\b|\bexamples?\b", re.IGNORECASE)
STOP_WORDS = frozenset("that this with from into must have when then they their there these those which while where after "
                       "before page pages user users shows show displays display only also each every other same does "
                       "cannot should would will shall been being some such than them what your".split())

def _roles_in(who: str) -> list[str]:
    who = re.sub(r"^(?:an?|the)\s+|\b(?:signed-in|signed in|logged-in)\b|\busers?\s+with\b|\baccounts?\s+with\b|\bstatus\b",
                 " ", who, flags=re.IGNORECASE)
    if NOT_WHO.search(who):
        return []
    roles: list[str] = []
    for item in [i.strip() for i in WHO_SPLIT.split(who) if i.strip()]:
        caps = [w for w in CAP_WORD.findall(item) if w.lower() not in ("only", "an", "a", "the")]
        noun = ROLE_NOUN.search(item)
        if noun and (not caps or noun.start() <= item.find(caps[0])):
            word = noun.group(0)
            roles.append(word[:-1] if word.lower().endswith("s") and word.lower() != "access" else word)
        elif caps:
            roles.append(caps[-1])
        elif item.lower() in ("users", "user", "accounts", "account"):
            continue
        else:
            return []
    return list(dict.fromkeys(roles))

def _ladder(text: str) -> list[str]:
    role_words: set[str] = set()
    for pattern in (ONLY_ROLE, EXCLUDED_LIST):
        for m in pattern.finditer(text):
            role_words.update(r for r in _roles_in(m.group("who").split(";")[-1]) if r[:1].isupper())
    best: list[str] = []
    for run in ROLE_RUN.finditer(text):
        items = [w for w in CAP_WORD.findall(run.group(0))]
        if len(set(items)) < len(items):
            continue

        named = bool(ROLE_INTRO.search(text[max(0, run.start() - 30): run.start()])) and any(i in role_words for i in items)
        if len(items) >= 3 and (named or all(i in role_words for i in items)) and len(items) > len(best):
            best = items
    return best

ROLE_INTRO = re.compile(r"\b(?:roles?|permissions?|permission levels?)\b(?:\s+of)?\s*:?\s*$", re.IGNORECASE)

def _restrictions(clause: str) -> list[tuple[list[str], str, bool]]:
    found = []
    for pattern in (ONLY_ROLE, LEAD_ROLE):
        for m in pattern.finditer(clause):
            roles = _roles_in(m.group("who"))
            if (pattern is LEAD_ROLE and READ_ONLY_ACT.search(m.group("act"))) or VIEW_ACT.match(m.group("act")):
                continue
            if roles and (pattern is ONLY_ROLE or len(roles) >= 2):
                found.append((roles, " ".join(m.group("act").split()), pattern is ONLY_ROLE))
        if found:
            break
    return found

def _act_words(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words if w not in ACT_STOP]

def _same_act(act: str, modal: str, refused: str) -> bool:
    refused = " ".join(re.split(r"[.;]", refused, maxsplit=1)[0].split()).lower()
    if "only" in modal.lower():
        return bool(ACT_READ_ONLY.match(refused))
    if ACT_ANAPHOR.match(refused):
        return True
    mine, theirs = _act_words(act), _act_words(refused)
    return bool(mine and theirs) and theirs[0] == mine[0] and set(theirs) <= set(mine)

def _excluded(roles: list[str], after: str, ladder: list[str], exclusive: bool = True, act: str = "") -> list[str]:
    listed = {r.lower() for r in roles}
    explicit: list[str] = []
    head = re.split(r"(?<=[.])\s", after, maxsplit=1)[0][:240]
    m = EXCLUDED_LIST.search(head)
    bound = exclusive or bool(m and _same_act(act, m.group("modal"), head[m.end():]))
    if m and bound:
        explicit = [r for r in _roles_in(m.group("who").split(";")[-1]) if r.lower() not in listed]
    if bound and OTHER_ROLES.search(head[:160]):
        explicit += [r for r in ladder if r.lower() not in listed]
    positions = [ladder.index(r) for r in roles if r in ladder]
    below = ladder[: min(positions)] if positions and exclusive else []
    out = [r for r in dict.fromkeys(explicit + below) if r.lower() not in listed]
    rank = {r: i for i, r in enumerate(ladder)}
    return sorted(out, key=lambda r: -rank.get(r, -1))

def _short(text: str, limit: int = 90) -> str:
    return anchor(text, limit)

def role_obligations(description: str, ladder: list[str]) -> list[str]:
    flat = _flat(IMAGE.sub(" ", TAIL.sub("", str(description or ""))))
    refusals: list[list[str]] = []
    qualifiers: list[str] = []
    for m in re.finditer(r"[^.;]*(?:[.;]|$)", flat):
        clause = m.group(0).strip()
        if not clause:
            continue
        for roles, act, exclusive in _restrictions(clause):
            if ladder:
                roles = [r for r in roles if r in ladder or ROLE_NOUN.fullmatch(r)]
                if not roles:
                    continue
            who = ", ".join(roles)
            excluded = _excluded(roles, flat[m.end():], ladder, exclusive, act)

            lead = f"only {who} may" if exclusive else f"{who} may"
            if excluded:
                nouns = [x for x in roles if not x[:1].isupper()]
                other = f" (not the {' or '.join(nouns)})" if nouns else ""
                refusals.append([f"[refusal: {r}{other}] {lead} {_short(act, 80)}" for r in excluded])
            elif exclusive:
                refusals.append([f"[refusal: not {' or '.join(roles)}] {lead} {_short(act, 80)}"])

        admit = ADMIT_AFTER.search(clause) or ADMIT_PASSIVE.search(clause)
        if admit and len(qualifiers) < MAX_QUALIFIERS:
            phrase = " ".join((admit.group("q") or admit.group("m") or admit.group("e")).split())
            qualifiers.append(f"[counter-example: not {_short(phrase, 50)}] {_short(clause.rstrip('.;'), 70)}")
    out: list[str] = []
    depth = 0
    while len(out) < MAX_REFUSALS and any(depth < len(r) for r in refusals):
        for group in refusals:
            if depth < len(group) and len(out) < MAX_REFUSALS:
                out.append(group[depth])
        depth += 1
    return list(dict.fromkeys(out + qualifiers))

def _scenario_steps(scenario: dict[str, Any]) -> list[tuple[str, str]]:
    steps = scenario.get("steps") if isinstance(scenario, dict) else None
    out = []
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict):
            out.append((str(step.get("keyword") or "").strip().upper(), _flat(step.get("content"))))
    return out

def clause_obligations(leaf: dict[str, Any], generated: set[int], named: str = "") -> list[str]:
    base, controls = _clause_parts(leaf, generated, named)
    return base + controls[:max(0, min(MAX_CONTROLS, MAX_CLAUSES - len(base)))]

def _clause_parts(leaf: dict[str, Any], generated: set[int], named: str = "") -> tuple[list[str], list[str]]:
    from .boilerplate import scenarios_of
    firsts: list[str] = []
    controls: list[tuple[str, str, str]] = []
    thens: list[str] = []
    seeded: set[str] = _seeded_names("", str(leaf.get("description") or ""))
    for index, scenario in scenarios_of(leaf):
        if index in generated:
            continue
        label = f"Scenario {index + 1}"
        for kind, content in _scenario_steps(scenario):
            seeded |= _seeded_names(kind, content)
            text = QUOTED_SPAN.sub(_protect, content)
            if kind == "WHEN":
                parts = [p.strip(" ,").translate(RESTORE) for p in WHEN_PARTS.split(text) if p.strip(" ,")]
                for part in parts:
                    alts = [a.strip() for a in WHEN_ALT.split(part) if a.strip()]
                    if len(alts) >= 2:
                        firsts += [f"[WHEN {label}, alternative {i}] {_short(a)}" for i, a in enumerate(alts, 1)]
                    elif CONFIRM.search(part) and len(parts) >= 2:
                        firsts.append(f"[WHEN {label}, confirmation step] {_short(part)}")
                controls += [(label, name, quoted) for name, quoted in step_controls(content)]
            elif kind == "THEN":
                parts = [" ".join(p.split()).strip(" ,").translate(RESTORE) for p in THEN_PARTS.split(text)]
                parts = [p for p in parts if len(p.split()) >= 4 and score(p) > 0 and not SEED.search(p)]
                if len(parts) >= 2:
                    thens += [f"[THEN {label}] {_short(p)}" for p in parts]

    stated = " ".join([named] + firsts + thens)
    done: set[str] = set()
    lines: list[str] = []
    for label, name, quoted in controls:
        if name in done or name.lower() in seeded or _names(stated, name):
            continue
        done.add(name)
        lines.append(f"[WHEN {label}, control] the scenario step uses control {quoted}: a test must operate it")
    return list(dict.fromkeys(firsts + thens))[:MAX_CLAUSES], lines

STEP_NAME = r"(?P<quoted>“(?P<a>[^”\n]{1,60})”|\"(?P<b>[^\"\n]{1,60})\"|`(?P<c>[^`\n]{1,60})`|「(?P<d>[^」\n]{1,60})」)"
STEP_OPERATE = re.compile(r"\b(?:clicks?|selects?|chooses?|picks?|taps?|toggles?|unchecks?|activates?)\s+"
                          r"(?:(?:the|a|an|its|their)\s+)?" + STEP_NAME)
STEP_OR = re.compile(r"\s*(?:,\s*)?\bor\s+(?:(?:the|a|an)\s+)?[“\"`「]")
STEP_OR_BEFORE = re.compile(r"[”\"`」]\s*(?:,\s*)?\bor\s+(?:(?:the|a|an)\s+)?$")
STEP_VALUE_AFTER = re.compile(r"\s+(?:(?:from|in)\s+the\s+(?:[\w-]+\s+){0,3}(?:dropdown|drop-down|list|menu|select|combobox)\b"
                              r"|labels?\b)", re.IGNORECASE)
STEP_VALUE_BEFORE = re.compile(r"\b(?:dropdown|drop-down|combobox|list|menu)\W+(?:then\s+)?(?:selects?|chooses?|picks?)\s+"
                               r"(?:the\s+)?$", re.IGNORECASE)
STEP_EITHER = re.compile(rf"\bor\s+(?:(?:the\s+)?(?:user|visitor|administrator|owner|author)\s+)?{WHEN_VERB}\b",
                         re.IGNORECASE)
SEED_SENTENCE = re.compile(r"\bseed(?:s|ed)?\b|\bseed values?\b", re.IGNORECASE)
ANY_QUOTED = re.compile(STEP_NAME)

STEP_HEDGE = re.compile(r"\b(?:not|never|no|none|nothing|without|neither|nor|cannot|optional(?:ly)?|may(?:be)?|might|can|"
                        r"could|if|unless|whether|when(?:ever)?|instead|rather|except|avoid(?:s|ed|ing)?|"
                        r"skip(?:s|ped|ping)?|attempt(?:s|ed|ing)?|tr(?:y|ies|ied|ying)|fail(?:s|ed|ing)?\s+to)\b"
                        r"|n[’']t\b", re.IGNORECASE)
MAX_CONTROLS = 2
CONTROL_NAME = re.compile(r"^.*? uses control (.*): a test must operate it$", re.DOTALL)

def _step_name(m: re.Match) -> str:
    return next(g for g in (m.group("a"), m.group("b"), m.group("c"), m.group("d")) if g is not None).strip()

def _sentences(content: str) -> list[str]:
    return re.split(r"(?<=[.;])\s+", QUOTED_SPAN.sub(_protect, content))

def step_controls(content: str) -> list[tuple[str, str]]:
    out = []
    if STEP_HEDGE.search(QUOTED_SPAN.sub(" ", content)):
        return out
    for sentence in _sentences(content):
        sentence = sentence.translate(RESTORE)
        if STEP_EITHER.search(sentence):
            continue
        for m in STEP_OPERATE.finditer(sentence):
            if STEP_OR.match(sentence, m.end()) or STEP_OR_BEFORE.search(sentence[: m.start("quoted")]):
                continue
            if STEP_VALUE_AFTER.match(sentence, m.end()) or STEP_VALUE_BEFORE.search(sentence[: m.start("quoted")]):
                continue
            name = _step_name(m)
            if name and len(name.split()) <= 6:
                out.append((name, m.group("quoted")))
    return out

def _seeded_names(kind: str, content: str) -> set[str]:
    found: set[str] = set()
    for sentence in _sentences(content):
        sentence = sentence.translate(RESTORE)
        if kind == "GIVEN" or SEED.search(sentence) or SEED_SENTENCE.search(sentence):
            found |= {_step_name(m).lower() for m in ANY_QUOTED.finditer(sentence)}
    return found

def _names(text: str, name: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text))

def _content_words(text: str) -> set[str]:
    return {w for w in CONTENT_WORD.findall(str(text or "").lower()) if w not in STOP_WORDS}

def folder_statements(tree: dict[str, Any], repeated: frozenset[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        children = [c for c in node.get("children") or [] if isinstance(c, dict)] if isinstance(node.get("children"), list) else []
        if not children:
            return
        folder_id = str(node.get("id") or "").strip()
        if folder_id and folder_id != "ROOT" and str(node.get("description") or "").strip():
            leaves = leaves_of(node)
            words = [(leaf, _content_words(f"{leaf.get('name', '')} {leaf.get('description', '')}")) for leaf in leaves]
            for statement in statements_of(str(node.get("description") or ""), repeated):
                text = statement.text
                if SEED.search(text) or SCENARIO_WORD.search(text) or not (CONSTRAINT.search(text) or QUOTE.search(text)
                                                                            or re.search(r"\d", text)):
                    continue
                mine = _content_words(text)
                best, best_overlap = None, FOLDER_MIN_OVERLAP - 1
                for leaf, leaf_words in words:
                    overlap = len(mine & leaf_words)
                    if overlap > best_overlap:
                        best, best_overlap = leaf, overlap
                if best is None:
                    continue
                bucket = out.setdefault(str(best.get("id")), [])
                own = _flat(best.get("description")).lower()
                if text.lower() not in own and len(bucket) < MAX_FOLDER:
                    bucket.append(f"[folder {folder_id}] {text}")
        for child in children:
            walk(child)

    walk(tree)
    return out

def derived_for_leaf(leaf: dict[str, Any], ladder: list[str], generated: set[int], folder: list[str],
                     with_clauses: bool, statements: tuple[str, ...] = ()) -> tuple[str, ...]:
    return _derived_parts(leaf, ladder, generated, folder, with_clauses, statements)[0]

def _derived_parts(leaf: dict[str, Any], ladder: list[str], generated: set[int], folder: list[str],
                   with_clauses: bool, statements: tuple[str, ...] = ()) -> tuple[tuple[str, ...], list[str]]:
    description = str(leaf.get("description") or "")
    folder_text = " ".join(TAG.sub("", f).rstrip(".") + "." for f in folder)
    if not with_clauses:
        return (), []
    roles = role_obligations(description + " " + folder_text, ladder)
    clauses, controls = _clause_parts(leaf, generated, " ".join(tuple(statements) + tuple(folder)))
    whens = [c for c in clauses if _kind(c) == "WHEN"]
    thens = [c for c in clauses if _kind(c) == "THEN"]

    kept = list(dict.fromkeys(roles + whens + list(folder) + thens))
    room = min(MAX_CONTROLS, MAX_CLAUSES - len(clauses), CAP - len(statements) - len(kept))
    controls = [c for c in controls if c not in kept]
    return tuple(kept + controls[:max(0, room)]), controls[max(0, room):]

def dropped_controls(node: dict[str, Any]) -> list[tuple[str, str]]:
    try:
        if not isinstance(node, dict) or str(node.get("pass_note") or "").strip():
            return []
        text = str(node.get("requirement_text") or "")
        if text.strip() and _analysis(text):
            return [(leaf_id, line) for leaf_id in dict.fromkeys(_member_ids(node))
                    for line in _derived_full(text).get(leaf_id, ((), []))[1]]
        leaves = [leaf for leaf in node.get("_leaves") or [] if isinstance(leaf, dict)]
        ladder = _ladder(" ".join(_flat(leaf.get("description")) for leaf in leaves))
        out = []
        for leaf in leaves:
            texts = tuple(s.text for s in statements_of(str(leaf.get("description") or "")))
            _kept, dropped = _derived_parts(leaf, ladder, set(), [], with_clauses=bool(texts) and len(texts) >= MIN_STATEMENTS,
                                            statements=texts)
            out += [(str(leaf.get("id")), line) for line in dropped]
        return out
    except Exception:
        return []

def control_note(node: dict[str, Any]) -> str:
    dropped = dropped_controls(node)
    if not dropped:
        return ""
    shown = [(leaf_id, CONTROL_NAME.sub(r"\1", line)) for leaf_id, line in dropped[:6]]
    names = ", ".join(f"{leaf_id} {name}" for leaf_id, name in shown)
    return f"{len(dropped)} step-control obligation(s) left out for room ({names}{' ...' if len(dropped) > 6 else ''})"

def _derived_analysis(text: str) -> dict[str, tuple[str, ...]]:
    return {leaf_id: kept for leaf_id, (kept, _dropped) in _derived_full(text).items()}

@functools.lru_cache(maxsize=4)
def _derived_full(text: str) -> dict[str, tuple[tuple[str, ...], list[str]]]:
    try:
        import yaml
        tree = yaml.safe_load(text)
        if not isinstance(tree, dict):
            return {}
        table = _analysis(text)
        leaves = leaves_of(tree)
        spread: dict[str, set[str]] = {}
        for leaf in leaves:
            for clause in _clauses(str(leaf.get("description") or "")):
                spread.setdefault(_key(clause), set()).add(str(leaf.get("id")))
        k = threshold(len(leaves))
        repeated = frozenset(key for key, ids in spread.items() if len(ids) >= k)
        try:
            generated_pairs = boilerplate_scenarios(tree)
        except Exception:
            generated_pairs = set()
        folders = folder_statements(tree, repeated)
        ladder = _ladder(_flat(text))
        out = {}
        for leaf in leaves:
            leaf_id = str(leaf.get("id"))
            texts, minimum = table.get(leaf_id, ((), MIN_STATEMENTS))
            generated = {index for lid, index in generated_pairs if lid == leaf_id}
            own = {index for index, _s in scenarios_of(leaf)}

            out[leaf_id] = _derived_parts(leaf, ladder, generated, folders.get(leaf_id, []),
                                          with_clauses=bool(texts) and len(texts) >= MIN_STATEMENTS
                                          and not (own and own <= generated), statements=texts)
        return out
    except Exception:
        return {}

def _member_ids(node: dict[str, Any]) -> list[str]:
    ids = [str(m) for m in node.get("members") or []]
    return ids or [str(node.get("id"))]

def ledger(node: dict[str, Any]) -> list[tuple[str, list[tuple[int, str]]]]:
    if not isinstance(node, dict) or str(node.get("pass_note") or "").strip():
        return []
    per_leaf: list[tuple[str, tuple[tuple[str, ...], int]]] = []
    text = str(node.get("requirement_text") or "")
    table = _analysis(text) if text.strip() else {}
    if table:
        per_leaf = [(i, table.get(i, ((), MIN_STATEMENTS))) for i in dict.fromkeys(_member_ids(node))]
    else:
        leaves = [leaf for leaf in node.get("_leaves") or [] if isinstance(leaf, dict)]
        per_leaf = [(str(leaf.get("id")), (tuple(s.text for s in statements_of(str(leaf.get("description") or ""))),
                                           MIN_STATEMENTS)) for leaf in leaves]
    out: list[tuple[str, list[tuple[int, str]]]] = []
    number = 0
    for leaf_id, (texts, minimum) in per_leaf:
        if not texts or len(texts) < minimum:
            continue
        numbered = []
        for statement in texts:
            number += 1
            numbered.append((number, statement))
        out.append((leaf_id, numbered))

    try:
        derived = _derived_of(node, per_leaf, text if table else "")
    except Exception:
        derived = []
    positions = {leaf_id: index for index, (leaf_id, _n) in enumerate(out)}
    for leaf_id, texts in derived:
        if not texts or leaf_id not in positions:
            continue
        numbered = out[positions[leaf_id]][1]
        for statement in texts[: max(0, CAP - len(numbered))]:
            number += 1
            numbered.append((number, statement))
    return out

def _derived_of(node: dict[str, Any], per_leaf: list, text: str) -> list[tuple[str, tuple[str, ...]]]:
    if text:
        table = _derived_analysis(text)
        return [(leaf_id, table.get(leaf_id, ())) for leaf_id, _t in per_leaf]
    leaves = [leaf for leaf in node.get("_leaves") or [] if isinstance(leaf, dict)]
    ladder = _ladder(" ".join(_flat(leaf.get("description")) for leaf in leaves))
    counts = dict(per_leaf)
    out = []
    for leaf in leaves:
        leaf_id = str(leaf.get("id"))
        texts, minimum = counts.get(leaf_id, ((), MIN_STATEMENTS))
        out.append((leaf_id, derived_for_leaf(leaf, ladder, set(), [], with_clauses=bool(texts) and len(texts) >= MIN_STATEMENTS,
                                              statements=texts)))
    return out

def anchor(statement: str, limit: int = ANCHOR_CHARS) -> str:
    if len(statement) <= limit:
        return statement
    if CJK.search(statement[:limit]):
        return statement[: limit // 2 + 10] + "…"
    cut = statement[:limit].rsplit(" ", 1)[0]
    return cut + " …"

LEDGER_HEAD = ("DESCRIPTION LEDGER. The observable statements of your requirement's description, numbered (each is a "
               "sentence of the description above; only its start is shown):\n")
LEDGER_RULES = (
    "Every statement is checked by at least one test. Right above each test(), one comment line names the statements "
    "it checks: // covers S2, S5. A statement no scenario test checks gets a test of its own, titled \"<requirement "
    "id>: <short behaviour>\". A statement no browser test can check is listed once: // not testable: S7 -- <reason> "
    "(never one with a quoted name, a message or what persists, unless it persists only for a time or a look; at most "
    "a quarter of them). "
    "These comment lines are the only comments allowed.")

DERIVED_ANCHOR = 80
DERIVED_RULES = (
    " Tagged: [refusal: R] signed in as a stated R account, the control is absent (notSee) or refused and the data "
    "unchanged after reload; [counter-example: not Q] a target lacking Q is refused; none stated: not testable, outside "
    "the quarter; [WHEN] / [THEN] that step or sub-clause; [folder] a group rule.")

def role_refusals(node: dict[str, Any]) -> list[tuple[str, str]]:
    try:
        return [(leaf_id, text) for leaf_id, numbered in ledger(node) for _n, text in numbered
                if _kind(text) in ("refusal", "counter-example")]
    except Exception:
        return []

REFUSAL_API_HEAD = ("ROLE RULES of this requirement (derived from its sentences that reserve an action to some roles or refuse "
                    "it to others, or admit only some targets). The browser spec "
                    "checks that the control is absent; YOUR backend test checks the direct write:\n")
REFUSAL_API_RULES = ("For each line: one backend test that signs in as an account of that role the requirement states (skip "
                     "the line when it states none), sends the write request directly, expects it refused (a 4xx status) "
                     "and then reads the data back unchanged. A counter-example uses a target that exists but lacks the "
                     "stated quality.")

def refusal_api_block(node: dict[str, Any]) -> str:
    found = role_refusals(node)
    if not found:
        return ""
    return REFUSAL_API_HEAD + "\n".join(f"  {leaf_id}: {text}" for leaf_id, text in found) + "\n" + REFUSAL_API_RULES

def ledger_block(node: dict[str, Any]) -> str:
    found = ledger(node)
    if not found:
        return ""
    lines = []
    for leaf_id, numbered in found:
        lines.append(f"  {leaf_id}:")
        lines.extend(f"    S{number}: {anchor(text, DERIVED_ANCHOR if TAG.match(text) else ANCHOR_CHARS)}"
                     for number, text in numbered)
    derived = any(TAG.match(text) for _leaf, numbered in found for _n, text in numbered)
    return LEDGER_HEAD + "\n".join(lines) + "\n" + LEDGER_RULES + (DERIVED_RULES if derived else "")

TEST_LINE = re.compile(r"^[ \t]*test(?:\.(?P<mod>\w+))?\(\s*(?:(?P<q>[\"'`])(?P<title>(?:(?!(?P=q))[^\n\\]|\\.)*)(?P=q))?")
COMMENT_LINE = re.compile(r"^[ \t]*(?://(?P<line>.*)|/\*(?P<block>.*?)\*/[ \t]*)$")
REF = re.compile(r"\bS(\d{1,3})(?:\s*(?:-|–|—|\.\.|to)\s*S?(\d{1,3}))?\b")
NOT_TESTABLE = re.compile(r"not[\s-]+testable", re.IGNORECASE)
NOT_RUN = {"skip", "fixme", "describe"}

def _refs(text: str) -> set[int]:
    found: set[int] = set()
    for m in REF.finditer(text):
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first

        if first <= last:
            found.update(range(first, last + 1))
        else:
            found.add(first)
    return found

def _ledger_marks(spec_text: str) -> tuple[set[int], set[int]]:
    lines = spec_text.splitlines()
    covered: set[int] = set()
    marked: set[int] = set()
    for index, line in enumerate(lines):
        comment = COMMENT_LINE.match(line)
        if comment:
            body = comment.group("line") if comment.group("line") is not None else comment.group("block") or ""
            if NOT_TESTABLE.search(body):
                marked |= _refs(NOT_TESTABLE.split(body, 1)[1])
            continue
        test = TEST_LINE.match(line)
        if not test or (test.group("mod") or "") in NOT_RUN:
            continue
        refs = _refs(test.group("title") or "")
        above = index - 1
        while above >= 0 and (not lines[above].strip() or COMMENT_LINE.match(lines[above])):
            match = COMMENT_LINE.match(lines[above])
            if match:
                body = match.group("line") if match.group("line") is not None else match.group("block") or ""
                if not NOT_TESTABLE.search(body):
                    refs |= _refs(body)
            above -= 1
        covered |= refs
    return covered, marked

STATED_MESSAGE = re.compile(r"\bmessages?\b|提示|消息", re.IGNORECASE)
NOT_TESTABLE_SHARE = 4

DURABLE = re.compile(r"\b(?:refresh(?:es|ed|ing)?|reload(?:s|ed|ing)?|reopen(?:s|ed|ing)?|restart(?:s|ed)?|"
                     r"retain(?:s|ed)?|preserv(?:e|es|ed|ing)|persist(?:s|ed|ing)?|restor(?:e|es|ed)|survives?)\b"
                     r"|刷新|保留|恢复|持久", re.IGNORECASE)
DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|secs?|seconds?|minutes?|mins?)\b|"
                      r"\b(?:two|three|four|five|ten|few|several)\s+(?:seconds|minutes)\b|\bone (?:second|minute)\b|"
                      r"\b(?:briefly|momentarily|temporarily|for a moment)\b|\d+\s*(?:毫秒|秒|分钟)|几秒|片刻", re.IGNORECASE)

LOOK = re.compile(r"\b(?:highlight(?:s|ed|ing)?|bold|italic|underlin(?:e|es|ed)|colou?r(?:s|ed)?|fonts?|borders?|"
                  r"shad(?:ed|ing)|gr[ae]yed(?: out)?|dimmed|faded|opacity|animat(?:ed|ion|es)|styl(?:e|ed|es|ing)|css)\b"
                  r"|高亮|颜色|加粗|粗体|样式|字体|边框", re.IGNORECASE)

def testable_by_construction(statement: str) -> bool:
    tag = TAG.match(statement)
    if tag:

        if tag.group("kind") in ("refusal", "counter-example"):
            return False
        statement = statement[tag.end():]
    persists = bool(DURABLE.search(statement)) or (bool(PERSIST.search(statement))
                                                   and not (DURATION.search(statement) or LOOK.search(statement)))
    return bool(QUOTE.search(statement) or persists or STATED_MESSAGE.search(statement))

PREREQUISITE_KINDS = ("refusal", "counter-example")

def _sentence_key(statement: str) -> str:
    prefix = ALT_PREFIX.match(statement).group(0)
    rest = statement[len(prefix):]
    result = ALT_RESULT.search(rest, 1) if ALT_START.match(rest) else None
    return (prefix + "|" + rest[result.start():].strip()).lower() if result else statement.lower()

def refused_marks(node: dict[str, Any], spec_text: str) -> list[tuple[int, str]]:
    found = ledger(node)
    if not found:
        return []
    covered, marked = _ledger_marks(spec_text or "")
    statements = [(number, text) for _leaf, numbered in found for number, text in numbered]

    exempt = {number for number, text in statements if _kind(text) in PREREQUISITE_KINDS}
    sentence: dict[int, int] = {}
    for _leaf, numbered in found:
        previous = None
        for number, text in numbered:
            if number in exempt:
                previous = None
                continue
            key = _sentence_key(text)
            sentence[number] = sentence[number - 1] if key == previous and number - 1 in sentence else len(set(sentence.values()))
            previous = key
    marks = [(number, text) for number, text in statements
             if number in marked and number not in covered and number not in exempt]
    sentences, spent = len(set(sentence.values())), len({sentence[number] for number, _t in marks})
    allowed = max(1, -(-sentences // NOT_TESTABLE_SHARE))
    if spent > allowed:
        return [(number, f"{spent} of {sentences} marked, at most {allowed}") for number, _t in marks]
    return [(number, "it quotes a name, states a message or says what persists") for number, text in marks
            if testable_by_construction(text)]

def uncovered(node: dict[str, Any], spec_text: str) -> list[tuple[str, int, str]]:
    found = ledger(node)
    if not found:
        return []
    covered, marked = _ledger_marks(spec_text or "")
    marked = marked - {number for number, _reason in refused_marks(node, spec_text)}

    refusals = {number for _leaf, numbered in found for number, text in numbered if _kind(text) == "refusal"}
    if refusals & covered:
        negative = {n for refs, body in _test_bodies(spec_text or "") if _may_assert_absence(body) for n in refs & refusals}
        covered = (covered - refusals) | negative
    return [(leaf_id, number, text) for leaf_id, numbered in found for number, text in numbered
            if number not in covered and number not in marked]

def _kind(statement: str) -> str:
    tag = TAG.match(statement)
    return tag.group("kind") if tag else ""

def _may_assert_absence(body: str) -> bool:
    if NEGATIVE.search(body):
        return True
    code = COMMENT_OR_STRING.sub(" 0 ", body)
    return any(m.group("name") not in KNOWN_CALLS for m in BARE_CALL.finditer(code))

COMMENT_OR_STRING = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`")

def _test_bodies(spec_text: str) -> list[tuple[set[int], str]]:
    lines = spec_text.splitlines()
    starts = []
    for index, line in enumerate(lines):
        test = TEST_LINE.match(line)
        if test:
            starts.append((index, test if (test.group("mod") or "") not in NOT_RUN else None))
    out = []
    for position, (index, test) in enumerate(starts):
        if test is None:
            continue
        refs = _refs(test.group("title") or "")
        above = index - 1
        while above >= 0 and (not lines[above].strip() or COMMENT_LINE.match(lines[above])):
            match = COMMENT_LINE.match(lines[above])
            if match:
                body = match.group("line") if match.group("line") is not None else match.group("block") or ""
                if not NOT_TESTABLE.search(body):
                    refs |= _refs(body)
            above -= 1
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        out.append((refs, "\n".join(lines[index:end])))
    return out

def not_testable(node: dict[str, Any], spec_text: str) -> list[int]:
    numbers = {number for _leaf, numbered in ledger(node) for number, _t in numbered}
    refused = {number for number, _reason in refused_marks(node, spec_text)}
    return sorted((numbers & _ledger_marks(spec_text or "")[1]) - refused)
