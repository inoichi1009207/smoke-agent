from __future__ import annotations

import collections
import functools
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import fresh_data_env, npm_bin, run_cmd, strip_terminal_codes, tail

@dataclass
class TestOutcome:
    passed: bool
    output: str
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    infra: str = ""

    error_contexts: dict | None = field(default=None, repr=False, compare=False)
    _note: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:

        self._note = bind_error_context_note(self.output, self.error_contexts, passed=self.passed)

    @property
    def summary(self) -> str:
        head = "ALL TESTS PASSED" if self.passed else "TESTS FAILING"
        parts = [head]
        if self.ran:
            parts.append("ran: " + ", ".join(self.ran))
        if self.skipped:
            parts.append("skipped: " + ", ".join(self.skipped))
        text = "\n".join(parts) + "\n\n" + self.output
        if not self.passed:

            note = self._note
            if note:
                text = text.rstrip("\n") + "\n\n" + note
        return text

VITEST_NOISE = re.compile(r"^\s*(RUN|Duration|Start at|Transform|Setup|Collect|Environment|Prepare)\b.*$", re.MULTILINE)

def _clean(output: str) -> str:
    output = VITEST_NOISE.sub("", output)
    output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output.strip()

def run_vitest(project_dir: Path, package: str, files: list[str], timeout: int = 300) -> TestOutcome:
    package_dir = project_dir / package
    if not files:
        return TestOutcome(True, "(no tests)")
    code, output = run_cmd(
        [npm_bin("npx"), "vitest", "run", *files, "--reporter=default", "--no-color"],
        cwd=package_dir,
        timeout=timeout,
        env=fresh_data_env(project_dir) if package == "backend" else None,
    )
    cleaned = tail(_clean(output), 7000)
    return TestOutcome(code == 0, cleaned, ran=[f"{package}/{f}" for f in files])

def run_node_tests(project_dir: Path, test_files: list[str]) -> TestOutcome:
    by_package: dict[str, list[str]] = {"frontend": [], "backend": []}
    skipped: list[str] = []
    for rel in test_files:
        parts = rel.replace("\\", "/").split("/", 1)
        if len(parts) == 2 and parts[0] in by_package and not parts[1].startswith("test-e2e/"):
            by_package[parts[0]].append(parts[1])
        else:
            skipped.append(rel)
    outcomes: list[TestOutcome] = []
    for package, files in by_package.items():
        if files:
            outcomes.append(run_vitest(project_dir, package, files))
    if not outcomes:
        return TestOutcome(True, "(no unit tests registered)", skipped=skipped)
    passed = all(o.passed for o in outcomes)
    output = "\n\n".join(f"## {', '.join(o.ran)}\n{o.output}" for o in outcomes)
    ran = [f for o in outcomes for f in o.ran]
    return TestOutcome(passed, output, ran=ran, skipped=skipped)

PARSE_ERROR = re.compile(r"SyntaxError|Transform failed|Unexpected token|Unterminated|missing \) after|Expected .{1,40} but found|Unexpected end of", re.IGNORECASE)

JEST_DOM_ONLY = {
    "toHaveTextContent": "toHaveText / toContainText",
    "toBeInTheDocument": "toBeVisible / toHaveCount(1)",
    "toHaveDisplayValue": "toHaveValue",
    "toBeRequired": "toHaveAttribute(\"required\", \"\")",
    "toBeInvalid": "toHaveAttribute(\"aria-invalid\", \"true\")",
    "toHaveFormValues": "toHaveValue on each field",
    "toBeEmptyDOMElement": "toBeEmpty",
    "toContainElement": "locator(...).locator(...) + toHaveCount",
    "toHaveErrorMessage": "toHaveAccessibleErrorMessage",
    "toBePartiallyChecked": "toBeChecked({ indeterminate: true })",
}
PLAYWRIGHT_ONLY = {
    "toHaveText": "toHaveTextContent",
    "toContainText": "toHaveTextContent",
    "toHaveURL": "(no URL in a component test; assert what the new route renders)",
    "toHaveCount": "expect(screen.queryAllByRole(...)).toHaveLength(n)",
    "toBeHidden": "not.toBeVisible() / not.toBeInTheDocument()",
    "toBeEditable": "not.toBeDisabled()",
}
TESTING_LIBRARY_QUERIES = r"(?:get|query|find)(?:All)?By(?:LabelText|DisplayValue|PlaceholderText|AltText)"

def framework_api_misuse(rel: str, text: str) -> list[str]:
    problems: list[str] = []

    def line_of(match: re.Match) -> int:
        return text.count("\n", 0, match.start()) + 1

    if rel.startswith("backend/test-e2e/"):
        for name, instead in JEST_DOM_ONLY.items():
            match = re.search(r"\.\s*" + name + r"\s*\(", text)
            if match:
                problems.append(f"{rel}:{line_of(match)}: `{name}` is a jest-dom matcher and does not exist in Playwright; use {instead}.")
        match = re.search(r"\b(?:page|\w+)\s*\.\s*(" + TESTING_LIBRARY_QUERIES + r")\s*\(", text)
        if match:
            problems.append(f"{rel}:{line_of(match)}: `{match.group(1)}` is a Testing Library query; Playwright has getByLabel / getByPlaceholder / getByAltText.")
        match = re.search(r"\bscreen\s*\.|\brender\s*\(\s*<|@testing-library/", text)
        if match:
            problems.append(f"{rel}:{line_of(match)}: a Playwright spec drives the real app through `page`; it cannot use Testing Library (`screen`, `render`).")
    if rel.startswith(("backend/tests/", "frontend/tests/")):

        match = re.search(r"\bjest\s*(?:\?\.|\.)\s*\w+", text)
        if match:
            problems.append(f"{rel}:{line_of(match)}: `{match.group(0)}` -- these tests run under Vitest, which has no `jest` global; use `vi` (vi.fn(), vi.mock(), vi.resetModules()).")
    if rel.startswith("backend/tests/") and "db_runtime" in text:

        helpers = set()
        for group in re.findall(r"\{([^}]*)\}\s*=\s*(?:require\([^)]*db_runtime[^)]*\)|await\s+import\([^)]*db_runtime[^)]*\))", text):
            helpers |= {part.split(":")[-1].strip() for part in group.split(",") if part.strip()}
        for group in re.findall(r"import\s*\{([^}]*)\}\s*from\s*[\"'][^\"']*db_runtime[^\"']*[\"']", text):
            helpers |= {part.split(" as ")[-1].strip() for part in group.split(",") if part.strip()}
        for name in sorted(helpers & {"run", "get", "all"}):
            match = re.search(r"(?<![\w$.])(?<!await )(?<!await  )" + name + r"\s*\(", text)
            if match and not re.match(r"\s*(?:async\s+)?function\b", text[max(0, match.start() - 20):match.start()]):
                problems.append(f"{rel}:{line_of(match)}: `{name}(...)` of db_runtime returns a Promise -- write `await {name}(...)`.")
    if rel.startswith("frontend/tests/"):
        for name, instead in PLAYWRIGHT_ONLY.items():
            match = re.search(r"\.\s*" + name + r"\s*\(", text)
            if match:
                problems.append(f"{rel}:{line_of(match)}: `{name}` is a Playwright assertion and does not exist in Vitest + jest-dom; use {instead}.")
        match = re.search(r"\b(?:screen|within\([^)]*\))\s*\.\s*((?:get|query|find)(?:All)?By(?:Label|Placeholder))\s*\(", text)
        if match:
            problems.append(f"{rel}:{line_of(match)}: `{match.group(1)}` is a Playwright locator; Testing Library has getByLabelText / getByPlaceholderText / getByAltText.")
        match = re.search(r"@playwright/test|\bpage\s*\.\s*(?:goto|getBy\w+|locator)\s*\(", text)
        if match:
            problems.append(f"{rel}:{line_of(match)}: a component test renders <App/> with Testing Library; it has no Playwright `page`.")
    return problems

JS_BUILTIN_CALLS = {"test", "expect", "require", "Promise", "setTimeout", "String", "Number", "Date", "JSON", "Math", "Array", "Object", "Boolean", "RegExp", "encodeURIComponent"}

SPEC_REQUIRE = re.compile(r"^[ \t]*(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['\"](@playwright/test|(?:\.\./|\./)*e2e-support/outsider)['\"]\s*\);?[ \t]*\n?", re.M)

def normalize_spec_imports(text: str) -> str:
    matches = list(SPEC_REQUIRE.finditer(text))
    if not matches:
        return text
    from .outsider import HELPER_JS

    exported = set(re.search(r"module\.exports\s*=\s*\{([^}]*)\}", HELPER_JS).group(1).replace(" ", "").replace("\n", "").split(","))
    names: list[str] = []
    for match in matches:
        for item in (part.strip() for part in match.group(1).split(",")):
            if ":" in item:
                return text
            if item and item not in names:
                names.append(item)
    playwright = ["test"] if "test" in names else []
    playwright += [n for n in names if n not in exported and n != "test"]
    helper = [n for n in names if n in exported and n != "test"]
    lines = []
    if playwright:
        lines.append(f'const {{ {", ".join(playwright)} }} = require("@playwright/test");\n')
    if helper:
        lines.append(f'const {{ {", ".join(helper)} }} = require("../e2e-support/outsider");\n')
    first = matches[0].start()
    rest = SPEC_REQUIRE.sub("", text)
    offset = len(SPEC_REQUIRE.sub("", text[:first]))
    return rest[:offset] + "".join(lines) + rest[offset:]

PLAYWRIGHT_TEST_EXPORTS = frozenset(("test", "expect", "devices", "chromium", "firefox", "webkit", "request", "selectors",
                                     "errors", "defineConfig", "mergeTests", "mergeExpects", "_electron", "_android"))

def outsider_undefined_calls(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/") or "e2e-support/outsider" not in text:
        return []
    from .outsider import HELPER_JS

    exported = set(re.search(r"module\.exports\s*=\s*\{([^}]*)\}", HELPER_JS).group(1).replace(" ", "").split(","))

    code = re.sub(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/",
                  lambda m: '""' if m.group(0)[0] in "\"'`" else " ", text, flags=re.S)

    uncommented = re.sub(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/",
                         lambda m: m.group(0) if m.group(0)[0] in "\"'`" else " ", text, flags=re.S)

    def bindings(source: str) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for group in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['\"]" + source + r"['\"]\s*\)", uncommented):
            for item in (part.strip() for part in group.split(",")):
                if item:
                    name, _, local = item.partition(":")
                    pairs[name.strip()] = (local or name).strip()
        return pairs

    helper = bindings(r"[./]*e2e-support/outsider")
    other = bindings(r"[^'\"]+")
    local = set(helper.values()) | set(other.values())
    defined = (set(re.findall(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)", code)) | set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)", code))
               | {name for group in re.findall(r"(?:const|let|var)\s*[{\[]([^}\]]*)[}\]]\s*=", code)
                  for name in re.findall(r"(?:^|[,:]\s*|\.\.\.)\s*([A-Za-z_$][\w$]*)\s*(?==|,|$)", group.strip())}

               | {name for group in re.findall(r"\bfunction\s*[\w$]*\s*\(([^)]*)\)|\(([^()]*)\)\s*=>", code) for part in group
                  for name in re.findall(r"[A-Za-z_$][\w$]*", part)}
               | set(re.findall(r"([A-Za-z_$][\w$]*)\s*=>", code)))
    problems: list[str] = []
    missing_exports = sorted(name for name in helper if name and name not in exported)
    if missing_exports:
        problems.append(f"{rel}: imports {', '.join(missing_exports)} from the helper, which has no such function "
                        f"(it provides: {', '.join(sorted(n for n in exported if n != 'inventory'))}).")

    playwright = bindings(r"@playwright/test")

    for group in re.findall(r"\bimport\s*\{([^}]*)\}\s*from\s*['\"]@playwright/test['\"]", uncommented):
        for item in (part.strip() for part in group.split(",")):
            if item:
                name, _, local = item.partition(" as ")
                playwright[name.strip()] = (local or name).strip()
    not_exported = sorted(name for name in playwright if name and name not in PLAYWRIGHT_TEST_EXPORTS)
    if not_exported:
        problems.append(f"{rel}: takes {', '.join(not_exported)} from @playwright/test, which exports no such name "
                        f"(it exports: {', '.join(sorted(PLAYWRIGHT_TEST_EXPORTS))}); page actions are methods of `page` "
                        "or functions of the helper.")

    called = {name for name in re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code) if name in exported}
    called |= set(re.findall(r"\bawait\s+([A-Za-z_$][\w$]*)\s*\(", code))
    unknown = sorted(name for name in called if name not in local and name not in defined and name not in JS_BUILTIN_CALLS)
    if unknown:
        problems.append(f"{rel}: calls {', '.join(unknown)}, which is neither imported nor defined in the file"
                        + (f" (the helper provides {', '.join(n for n in unknown if n in exported)}: import it)" if any(n in exported for n in unknown) else "") + ".")
    return problems

CLOCK = r"(Date\.now\(\)|Math\.random\(\)[^;,)`}]*|crypto\.randomUUID\(\))"
UNIQUE_NAME = re.compile(r"""(["'])([^"'`\n]{1,60})\1\s*\+\s*""" + CLOCK)
UNIQUE_TEMPLATE = re.compile(r"`([^`$\n]{0,60})\$\{\s*" + CLOCK + r"[^}]*\}[^`\n]*`")
ASSIGNED = re.compile(r"(?:const|let|var)\s+\w+\s*(?::\s*[\w.<>\[\]| ]+)?=\s*\(*\s*$")

BLANK_LOOKUP = re.compile(r"\b(see|notSee|seeOnce)\s*\(\s*page\s*,\s*([\"'`])(\s*)\2")

def blank_lookups(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    return [f"{rel}: {m.group(1)}(page, {m.group(2)}{m.group(3)}{m.group(2)}) looks for an empty text, which matches everything. "
            "Check what the requirement says is shown instead (the error message, the unchanged old value)."
            for m in BLANK_LOOKUP.finditer(text)]

ARIA_ROLES = {"alert", "alertdialog", "application", "article", "banner", "button", "cell", "checkbox", "columnheader", "combobox", "complementary", "contentinfo", "dialog", "document", "feed", "figure", "form", "grid", "gridcell", "group", "heading", "img", "link", "list", "listbox", "listitem", "main", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "navigation", "none", "note", "option", "presentation", "progressbar", "radio", "radiogroup", "region", "row", "rowgroup", "rowheader", "scrollbar", "search", "searchbox", "separator", "slider", "spinbutton", "status", "switch", "tab", "table", "tablist", "tabpanel", "term", "textbox", "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem"}
PAGE_METHODS = {"accessibility", "addInitScript", "addScriptTag", "addStyleTag", "ariaSnapshot", "bringToFront", "check", "click", "clock", "close", "content", "context", "coverage", "dblclick", "dispatchEvent", "dragAndDrop", "emulateMedia", "evaluate", "evaluateHandle", "exposeFunction", "fill", "focus", "frameLocator", "frames", "getAttribute", "getByAltText", "getByLabel", "getByPlaceholder", "getByRole", "getByTestId", "getByText", "getByTitle", "goBack", "goForward", "goto", "hover", "innerHTML", "innerText", "inputValue", "isChecked", "isClosed", "isDisabled", "isEditable", "isEnabled", "isHidden", "isVisible", "keyboard", "locator", "mainFrame", "mouse", "off", "on", "once", "opener", "pause", "pdf", "press", "reload", "request", "requestGC", "route", "routeFromHAR", "screenshot", "selectOption", "setContent", "setDefaultNavigationTimeout", "setDefaultTimeout", "setExtraHTTPHeaders", "setInputFiles", "setViewportSize", "tap", "textContent", "title", "touchscreen", "type", "uncheck", "unroute", "url", "video", "viewportSize", "waitForEvent", "waitForFunction", "waitForLoadState", "waitForNavigation", "waitForRequest", "waitForResponse", "waitForSelector", "waitForTimeout", "waitForURL", "workers"}

CELL_EDIT_FIELD = re.compile(r"^(?:formula|cell|value|content)s?\b.{0,20}\b(?:bar|editor|input|field|box|textbox)$", re.IGNORECASE)

def stated_cell_editors(batch: dict | None) -> list[str]:
    if not isinstance(batch, dict):
        return []
    try:
        from .test_author import describe_node

        later = [str(n) for n in batch.get("later_names") or []]
        later_folded = {" ".join(n.split()).lower() for n in later}
        if "earlier_names" in batch:
            earlier = [str(n) for n in batch.get("earlier_names") or []]
        else:
            earlier = [a or b or c for a, b, c in re.findall(r"\"([^\"\n]{1,60})\"|“([^”\n]{1,60})”|`([^`\n]{1,60})`",
                                                             describe_node(batch))]
        found = [n.strip() for n in earlier if CELL_EDIT_FIELD.match(n.strip()) and " ".join(n.split()).lower() not in later_folded]
        if later:
            from .outsider import named_by_own_requirement

            found += [n.strip() for n in later if CELL_EDIT_FIELD.match(n.strip()) and named_by_own_requirement(n, batch)]
    except Exception:
        return []
    seen: dict[str, str] = {}
    for name in found:
        seen.setdefault(" ".join(name.split()).lower(), name)
    return sorted(seen.values())

CELL_TYPING = ('type into the selected cell: typeText(page, "<value>") types the value there as a person does, then '
               'press(page, "Enter") commits it (Tab / Escape: a press() of their own)')

def _cell_input_hint(batch: dict | None) -> str:
    fields = stated_cell_editors(batch)
    if len(fields) == 1:
        return f"fill the \"{fields[0]}\" field (this requirement or an earlier one names it) and press Enter -- or {CELL_TYPING}."
    if fields:
        return ("fill the field this requirement or an earlier one names for editing it (" + " / ".join(f"\"{f}\"" for f in fields)
                + f") and press Enter -- or {CELL_TYPING}.")
    return ("type the value into a text field for editing the cell ONLY if this requirement or an earlier one names one "
            f"(a field only a later requirement names is not built yet) and press Enter -- otherwise {CELL_TYPING}.")

CELL_TARGET = r"""\{[^}]*role\s*:\s*["'](?:grid)?cell["'][^}]*\}"""

CELL_LOCATOR = (r"""(?:\b(?:locate|region)\(\s*page\s*,\s*(?:""" + CELL_TARGET + r"""|["'](?:grid)?cell["'][^()]*)\)"""
                r"""|\bgetByRole\(\s*["'](?:grid)?cell["'][^()]*\))(?:\s*\.\s*(?:first|last|nth)\([^()]*\))*""")
CELL_LOCATOR_FILL = re.compile(CELL_LOCATOR + r"\s*\.\s*fill\(")
CELL_LOCATOR_VAR = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?" + CELL_LOCATOR + r"\s*;?")

PRESS_LITERAL = re.compile(r"""\bpress\(\s*page\s*,\s*(["'`])((?:(?!\1)[^\\\n$])*)\1\s*\)""")
TYPE_LITERAL = re.compile(r"""\btypeText\(\s*page\s*,\s*(["'`])((?:\\.|(?!\1)[^\\\n])*)\1\s*\)""")
TYPE_TARGET = re.compile(r"""\btypeText\(\s*page\s*,\s*[{\[]""")

def _key_tokens(key: str) -> list[str]:
    tokens: list[str] = []
    building = ""
    for char in key:
        if char == "+" and building:
            tokens.append(building)
            building = ""
        else:
            building += char
    tokens.append(building)
    return tokens

def press_text_misuse(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    code = re.sub(r"//[^\n]*", "", text)
    problems: list[str] = []
    for m in PRESS_LITERAL.finditer(code):
        key = m.group(2)
        if key == "":
            problems.append(f'{rel}: press(page, "") presses no key. To enter a value on the selected cell or the focused field: '
                            'typeText(page, "<value>"), then press(page, "Enter") (or "Tab" / "Escape") as a step of its own.')
        elif any(len(token) > 1 and not re.fullmatch(r"[A-Za-z0-9]+", token) for token in _key_tokens(key)):
            problems.append(f'{rel}: press(page, "{key}") is no key -- press() takes ONE key ("Enter", "Tab", "Escape", '
                            f'"Control+Z"). To type it on the selected cell or the focused field: typeText(page, "{key}"), then '
                            'press(page, "Enter") (or "Tab" / "Escape") as a step of its own; into a named field: fill().')
    for m in TYPE_LITERAL.finditer(code):
        value = m.group(2)
        if value == "":
            problems.append(f'{rel}: typeText(page, "") types nothing -- give it the text the step enters.')
        elif re.search(r"\\[nrt]|\\u001[bB]|\\x1[bB]", value):
            problems.append(f'{rel}: typeText(page, "{value}") types characters only; Enter, Tab and Escape are keys of their own: '
                            'typeText(page, "<text>"), then press(page, "Enter").')
    if TYPE_TARGET.search(code):
        problems.append(f"{rel}: typeText(page, text) takes no target -- it types into what has the focus. Select the cell or "
                        'field first (click(page, { role: "gridcell", name: "B2" })), then typeText(page, "<text>").')
    return list(dict.fromkeys(problems))

def spec_api_misuse(rel: str, text: str, batch: dict | None = None) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    hint = _cell_input_hint(batch)
    problems: list[str] = []
    for m in re.finditer(r"\bregion\(\s*page\s*,\s*[\"']([^\"']+)[\"']", text):
        if m.group(1) not in ARIA_ROLES:
            problems.append(f"{rel}: region(page, \"{m.group(1)}\") -- the second argument is a ROLE (dialog, navigation, complementary, "
                            f"region, form, list ...) and \"{m.group(1)}\" is none; a named part is region(page, \"<role>\", \"{m.group(1)}\").")
    for m in re.finditer(r"\bpage\s*\.\s*([A-Za-z_$][\w$]*)\s*\(", text):
        if m.group(1) not in PAGE_METHODS:
            problems.append(f"{rel}: page.{m.group(1)}() does not exist in Playwright" + (" (back: page.goBack())" if m.group(1) == "back" else "") + ".")

    for m in re.finditer(r"""\bfill\(\s*page\s*,\s*(""" + CELL_TARGET + r"""|["'`][A-Z]{1,3}[0-9]{1,5}["'`])""", text):
        problems.append(f"{rel}: fill(page, {m.group(1)}, ...) types into a grid cell, which is no text field. Select the cell "
                        f"(click it), then {hint}")
    for m in CELL_LOCATOR_FILL.finditer(text):
        problems.append(f"{rel}: {' '.join(m.group(0).split())[:120]} fills a grid cell, which is no text field. Select the cell "
                        f"(click it), then {hint}")
    for m in CELL_LOCATOR_VAR.finditer(text):
        if re.search(r"(?<![\w$.])" + re.escape(m.group(1)) + r"\s*\.\s*fill\(", text[m.end():]):
            problems.append(f"{rel}: {m.group(1)}.fill(...) fills a grid cell (`{m.group(1)}` is the cell's locator), which is no "
                            f"text field. Select the cell (click it), then {hint}")

    for m in re.finditer(r"""\{\s*role\s*:\s*["']gridcell["']\s*,\s*name\s*:\s*(\w+)\s*\}\s*\)\s*;?\s*\n(?:[^\n]*\n){0,2}?[^\n]*\bfill\(\s*page\s*,\s*\1\b""", text):
        problems.append(f"{rel}: fill(page, {m.group(1)}, ...) right after clicking the gridcell named {m.group(1)} types into the "
                        f"grid cell, which is no text field. With the cell selected, {hint}")
    for m in re.finditer(r"\blocate\(\s*page\s*,\s*\{\s*role\s*:\s*[\"'](\w+)[\"']\s*\}\s*\)\s*\.\s*(first|last|nth)\b", text):
        problems.append(f"{rel}: locate(page, {{ role: \"{m.group(1)}\" }}).{m.group(2)}() takes whichever {m.group(1)} comes {m.group(2)} on the whole page "
                        "(a header or menu entry as often as the record meant). Name the element the requirement names, or look inside the part "
                        "of the page it names (a list, a region) with that part as scope.")
    return list(dict.fromkeys(problems))

SIGN_IN_SUBMIT = re.compile(r"""\bclick\(\s*page\s*,\s*\{\s*role\s*:\s*["']button["']\s*,\s*name\s*:\s*["'](?:sign\s*in|log\s*in|login|登录)["']\s*\}""", re.IGNORECASE)
NEW_SESSION = re.compile(r"\bfreshSession\s*\(|clearCookies\s*\(|\bnewContext\s*\(|\bbrowser\.newPage\s*\("

                         r"|\bclick\(\s*page\s*,[^\n]*?(?:sign|log)\s*out", re.IGNORECASE)

HTML_TAGS = {"mark", "table", "thead", "tbody", "tr", "td", "th", "li", "ul", "ol", "img", "svg", "canvas", "video",
             "audio", "iframe", "code", "pre", "strong", "em", "kbd", "abbr", "time", "progress", "meter", "details",
             "summary", "figure", "figcaption", "blockquote", "hr", "span", "div", "p", "del", "ins", "sup", "sub"}
LOCATE_TAG = re.compile(r"""\blocate\(\s*page\s*,\s*["'`]([a-z][a-z0-9]*)["'`]""")

def tags_as_names(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    return [f'{rel}: locate(page, "{tag}") looks for a name or text "{tag}", not for <{tag}> elements -- use tagged(page, "{tag}").'
            for tag in dict.fromkeys(LOCATE_TAG.findall(text)) if tag in HTML_TAGS]

CELL_AS_ITEM = re.compile(r"""\bitem\(\s*page\s*,\s*(?:["'`]([A-Z]{1,3}[0-9]{1,5})["'`]|\{[^}]*\brole\s*:\s*["'`](gridcell|cell|columnheader|rowheader)["'`])""")

def cells_as_items(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []

    coordinates = {m.group(1) for m in CELL_AS_ITEM.finditer(text) if m.group(1)}
    grid = "gridcell" in text or len(coordinates) >= 2
    found = []
    for match in CELL_AS_ITEM.finditer(text):
        if match.group(1) and not grid:
            continue
        what = match.group(1) or match.group(2)
        found.append(f'{rel}: item(page, ...) with the cell "{what}" looks for a list ROW whose text contains it, and no '
                     'row holds a cell reference or a role as text -- a cell is region(page, "gridcell", "<its name>").')
    return list(dict.fromkeys(found))

TOP_FUNCTION = re.compile(r"^(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)\s*\{|^(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
                          re.MULTILINE)

def unbound_page(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    if re.search(r"^(?:const|let|var)\s+page\b|^(?:const|let|var)\s*\{[^}]*\bpage\b[^}]*\}\s*=", text, re.MULTILINE):
        return []
    problems: list[str] = []
    starts = [m for m in TOP_FUNCTION.finditer(text)]
    for index, m in enumerate(starts):
        name, params = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if re.search(r"\bpage\b", params or ""):
            continue

        end = len(text)
        nxt = re.search(r"^(?:test(?:\.\w+)?\(|(?:async\s+)?function\s|(?:const|let|var)\s)", text[m.end():], re.MULTILINE)
        if nxt:
            end = m.end() + nxt.start()
        body = re.sub(r"//[^\n]*|([\"'`])(?:\\.|(?!\1).)*\1", "", text[m.end():end])
        if re.search(r"(?<![\w.$])page\b", body):
            problems.append(f"{rel}: the helper `{name}` uses `page` but does not receive it -- `page` exists only inside each "
                            f"test's callback, so every test that calls `{name}` fails with \"page is not defined\". "
                            f"Give it a parameter: `{name}(page, ...)` and pass `page` from the test.")
    return problems

def repeated_sign_ins(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    problems: list[str] = []
    for block in re.split(r"\n\s*test(?:\.\w+)?\(", text)[1:]:
        title = block.split(",", 1)[0].strip()[:80]
        submits = [m.start() for m in SIGN_IN_SUBMIT.finditer(block)]
        for earlier, later in zip(submits, submits[1:]):
            if not NEW_SESSION.search(block[earlier:later]):
                problems.append(f"{rel}: test {title} signs in again while the first sign-in is still active -- start that "
                                "attempt with `await freshSession(page)` (cookies and storage cleared, back on the home page).")
                break
    return problems

def rebuilt_unique_names(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    problems: list[str] = []
    for block in re.split(r"\n\s*test(?:\.\w+)?\(", text)[1:]:
        title = block.split(",", 1)[0].strip()[:80]
        builds: dict[str, list[bool]] = {}
        found = [(m.start(), m.group(2), m.group(3)) for m in UNIQUE_NAME.finditer(block)]
        found += [(m.start(), m.group(1), m.group(2)) for m in UNIQUE_TEMPLATE.finditer(block)]
        for start, prefix, clock in found:
            key = prefix.strip() + " + " + clock.split("(")[0]
            builds.setdefault(key, []).append(not ASSIGNED.search(block[:start]))
        for key, inline in builds.items():
            if len(inline) > 1 and any(inline):
                prefix, clock = key.split(" + ", 1)
                problems.append(
                    f"{rel}: test {title} builds the name `\"{prefix} \" + {clock}()` {len(inline)} times. "
                    "Each call gives a different value, so a later lookup searches for a record that was never created. "
                    "Build the name ONCE (const name = \"...\" + Date.now()) and use that constant everywhere in the test.")
    return problems

ARITY_CHECKED_HELPERS = ("fill", "type", "choose", "select", "press", "typeText")
OPTIONAL_HELPER_PARAMS = {"within", "how"}
_JS_BLANKS = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/", re.S)

def helper_signatures() -> dict[str, tuple[int, str]]:
    from .outsider import HELPER_JS

    exported = set(re.search(r"module\.exports\s*=\s*\{([^}]*)\}", HELPER_JS).group(1).replace(" ", "").replace("\n", "").split(","))
    found: dict[str, tuple[int, str]] = {}
    for name in ARITY_CHECKED_HELPERS:
        match = re.search(r"^(?:async\s+)?function\s+" + name + r"\s*\(([^)]*)\)", HELPER_JS, re.MULTILINE)
        if not match or name not in exported:
            continue
        params = [part.strip() for part in match.group(1).split(",") if part.strip()]
        required = 0
        for param in params:
            if "=" in param or param.startswith("...") or param in OPTIONAL_HELPER_PARAMS:
                break
            required += 1
        found[name] = (required, f"{name}({', '.join(params)})")
    return found

def _call_arguments(code: str, open_paren: int) -> list[str] | None:
    depth = 0
    start = open_paren + 1
    args: list[str] = []
    for index in range(open_paren, len(code)):
        char = code[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                args.append(code[start:index])
                parts = [a.strip() for a in args]
                if parts and not parts[-1]:
                    parts.pop()
                return None if any("/" in part for part in parts) else parts
            if depth < 0:
                return None
        elif char == "," and depth == 1:
            args.append(code[start:index])
            start = index + 1
    return None

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")

def _bracket_text(code: str, open_at: int, step: int) -> str | None:
    pairs = {"(": ")", "[": "]", "{": "}"} if step > 0 else {")": "(", "]": "[", "}": "{"}
    depth = 0
    index = open_at
    while 0 <= index < len(code):
        char = code[index]
        if char in pairs:
            depth += 1
        elif char in pairs.values():
            depth -= 1
            if depth == 0:
                return code[open_at + 1:index] if step > 0 else code[index + 1:open_at]
        index += step
    return None

def _pattern_names(text: str) -> set[str]:
    return set(_IDENT.findall(re.sub(r"[A-Za-z_$][\w$]*\s*:(?!:)", " ", text)))

def _local_bindings(code: str) -> set[str]:
    names: set[str] = set()
    for match in re.finditer(r"\bfunction\b\s*\*?\s*[\w$]*\s*\(", code):
        inner = _bracket_text(code, match.end() - 1, 1)
        names |= _pattern_names(inner if inner is not None else code[match.end():match.end() + 200])
    for match in re.finditer(r"=>", code):
        index = match.start() - 1
        while index >= 0 and code[index] in " \t\r\n":
            index -= 1
        if index >= 0 and code[index] == ")":
            inner = _bracket_text(code, index, -1)
            names |= _pattern_names(inner if inner is not None else code[max(0, index - 200):index])
        else:
            single = re.search(r"[A-Za-z_$][\w$]*$", code[:index + 1])
            if single:
                names.add(single.group(0))

    for match in re.finditer(r"(?<![\w$.])[A-Za-z_$][\w$]*\s*\(([^()]*)\)\s*\{", code):
        if not re.match(r"(?:if|for|while|switch|catch|function|with)\b", match.group(0)):
            names |= _pattern_names(match.group(1)) | {_IDENT.match(match.group(0)).group(0)}
    for match in re.finditer(r"\bcatch\s*\(([^)]*)\)", code):
        names |= _pattern_names(match.group(1))
    for match in re.finditer(r"\b(?:const|let|var)\s*([{\[])", code):
        inner = _bracket_text(code, match.end() - 1, 1)
        if inner is None:
            continue
        after = code[match.end() + len(inner) + 1:]
        if re.match(r"\s*[}\]]?\s*=\s*require\s*\(", after):
            continue
        names |= _pattern_names(inner)
    return names

def helper_arity_defects(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/") or "e2e-support/outsider" not in text:
        return []
    signatures = helper_signatures()

    code = _JS_BLANKS.sub(lambda m: ('""' if m.group(0)[0] in "\"'`" else " ") + "\n" * m.group(0).count("\n"), text)
    uncommented = _JS_BLANKS.sub(lambda m: m.group(0) if m.group(0)[0] in "\"'`" else " " + "\n" * m.group(0).count("\n"), text)
    imported: set[str] = set()
    for group in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['\"][./]*e2e-support/outsider['\"]\s*\)", uncommented):
        for item in (part.strip() for part in group.split(",")):
            name, colon, local = item.partition(":")
            if item and not colon:
                imported.add(name.strip())
    source_lines = text.splitlines()
    problems: list[str] = []

    shadowed = _local_bindings(code)
    for name, (required, signature) in signatures.items():
        if name not in imported or name in shadowed or re.search(r"\b(?:function|class)\s*\*?\s*" + name + r"\b|(?:const|let|var)\s+" + name + r"\b", code):
            continue
        for match in re.finditer(r"(?<![\w$.])" + name + r"\s*\(", code):
            args = _call_arguments(code, match.end() - 1)
            if args is None or not args or args[0] != "page" or any(a.startswith("...") for a in args) or len(args) >= required:
                continue
            line = code.count("\n", 0, match.start()) + 1
            shown = source_lines[line - 1].strip() if line <= len(source_lines) else f"{name}(...)"
            hint = {"fill": " The FIELD comes second (its label or { role, name }) and the text to type third: "
                            "fill(page, <field>, <text>). With the text in the field's place, the helper looks for a field "
                            "named after the text and types nothing.",
                    "choose": " The DROPDOWN comes second and the option third: choose(page, <dropdown>, <option>).",
                    "press": " The key comes second: press(page, \"Enter\")."}.get(name, "")
            problems.append(f"{rel}:{line}: `{shown}` passes {len(args)} argument(s) to the helper's {signature}, which needs "
                            f"at least {required}.{hint}")
    return problems

ASSERT_TEXT_CALL = re.compile(r"(?<![\w$.])(see|seeOnce)\s*\(")
ASSERT_MATCHER = re.compile(r"(?<!\.not)\.(toHaveText|toContainText|toHaveValue)\s*\(")
TEST_CALL = re.compile(r"^[ \t]*test(?:\.(only|skip|fixme|fail))?\s*\(", re.MULTILINE)
JS_STRING = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`")
JS_LITERAL = re.compile(r"\"((?:\\.|[^\"\\\n])*)\"|'((?:\\.|[^'\\\n])*)'|`((?:\\.|[^`\\])*)`", re.S)
CELL_REFERENCE = re.compile(r"\$?[A-Z]{1,3}\$?[0-9]{1,5}(?::\$?[A-Z]{1,3}\$?[0-9]{1,5})?")
NUMBER_LITERAL = re.compile(r"[-+]?[$€£¥]?\d[\d,]*(?:\.\d+)?%?")
NUMBER_TOKEN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
PLACEHOLDER_QUOTE = re.compile(r"'([^'\n]{0,80}<[^<>'\n]{1,60}>[^'\n]{0,80})'")
_QUOTES = str.maketrans({"“": "'", "”": "'", "„": "'", "″": "'", "‘": "'", "’": "'", "`": "'",
                         '"': "'", "「": "'", "」": "'", "『": "'", "』": "'"})
AGGREGATION = re.compile(r"\b(?:sums?|summ\w*|totals?|subtotals?|averages?|mean|counts?|pivot|aggregat\w*|minimum|maximum)\b",
                         re.IGNORECASE)
FORMULA = re.compile(r"(?<![\w=<>!])=\s*(?:[A-Z]{2,}\s*\(|\$?[A-Z]{1,3}\$?\d|\()")
SCENARIO_NUMBER = re.compile(r"\bscenario\s*#?\s*(\d{1,2})\b", re.IGNORECASE)

def _fold(text: str) -> str:
    return " ".join(str(text or "").translate(_QUOTES).lower().split())

def _blank_in_place(text: str) -> str:
    def blank(match: re.Match) -> str:
        found = match.group(0)
        if found[0] in "\"'`":
            return found[0] + re.sub(r"[^\n]", " ", found[1:-1]) + found[-1]
        return re.sub(r"[^\n]", " ", found)
    return _JS_BLANKS.sub(blank, text)

def _argument_spans(code: str, open_paren: int) -> tuple[list[tuple[int, int]], int] | None:
    depth = 0
    start = open_paren + 1
    spans: list[tuple[int, int]] = []
    for index in range(open_paren, len(code)):
        char = code[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                spans.append((start, index))
                return [s for s in spans if code[s[0]:s[1]].strip()], index
            if depth < 0:
                return None
        elif char == "," and depth == 1:
            spans.append((start, index))
            start = index + 1
    return None

def _js_literal(source: str) -> str | None:
    match = JS_LITERAL.fullmatch(source.strip())
    if not match or (match.group(3) is not None and "${" in match.group(3)):
        return None
    body = next(group for group in match.groups() if group is not None)
    return re.sub(r"\\(.)", lambda e: {"n": "\n", "t": "\t"}.get(e.group(1), e.group(1)), body, flags=re.S)

def _asserted_values(source: str) -> list[str]:
    source = source.strip()
    if source.startswith("["):
        return [value for part in JS_STRING.findall(source) if (value := _js_literal(part)) is not None]
    if source.startswith("{"):
        match = re.search(r"\bname\s*:\s*(" + JS_STRING.pattern + r")\s*[,}]", source)
        value = _js_literal(match.group(1)) if match else None
        return [value] if value is not None else []
    value = _js_literal(source)
    return [value] if value is not None else []

class _Requirement:

    def __init__(self, tree: dict) -> None:
        from .boilerplate import leaves_of

        self.leaves = {str(leaf.get("id")): leaf for leaf in leaves_of(tree)}
        self.texts: list[tuple[str, str]] = []
        stack = [tree]
        while stack:
            node = stack.pop()
            text = f"{node.get('name') or ''}\n{node.get('description') or ''}"
            if str(node.get("id")) in self.leaves:
                text += "\n" + "\n".join(_scenario_text(s) for s in node.get("scenarios") or [])
            self.texts.append((str(node.get("id")), text))
            stack.extend(child for child in node.get("children") or [] if isinstance(child, dict))

    def elsewhere(self, members: tuple[str, ...]) -> str:
        return _fold("\n".join(text for node_id, text in self.texts if node_id not in members))

@functools.lru_cache(maxsize=4)
def _requirement(requirement_text: str) -> _Requirement | None:
    try:
        import yaml

        tree = yaml.safe_load(requirement_text)
        return _Requirement(tree) if isinstance(tree, dict) else None
    except Exception:
        return None

def _scenario_text(scenario: dict) -> str:
    steps = scenario.get("steps") if isinstance(scenario, dict) else None
    return " ".join(str(step.get("content") or "") for step in steps or [] if isinstance(step, dict))

@functools.lru_cache(maxsize=16)
def _elsewhere(requirement_text: str, members: tuple[str, ...]) -> str:
    parsed = _requirement(requirement_text)
    return parsed.elsewhere(members) if parsed else ""

def _batch_requirement(batch: dict) -> tuple[list[tuple[str, str, list[tuple[str, str]]]], str, str]:
    ids = [str(m) for m in batch.get("members") or []] or [str(batch.get("id") or "")]
    whole = str(batch.get("requirement_text") or "")
    parsed = _requirement(whole) if whole.strip() else None
    leaves = [parsed.leaves[i] for i in dict.fromkeys(ids) if i in parsed.leaves] if parsed else []
    if not leaves:
        return [], "", ""

    background = str(batch.get("ancestor_context") or "")
    return ([(str(leaf.get("id")), f"{leaf.get('name') or ''}\n{leaf.get('description') or ''}",
              [(str(s.get("name") or ""), _scenario_text(s)) for s in leaf.get("scenarios") or [] if isinstance(s, dict)])
             for leaf in leaves], background, _elsewhere(whole, tuple(str(leaf.get("id")) for leaf in leaves)))

def _own_scenario(title: str, leaves: list[tuple[str, str, list[tuple[str, str]]]]) -> str | None:
    number = SCENARIO_NUMBER.search(title or "")
    if not number:
        return None
    named = [leaf for leaf in leaves if re.search(r"(?<![\w-])" + re.escape(leaf[0]) + r"(?![\w-])", title)]
    if len(named) != 1 and len(leaves) == 1:
        named = leaves
    if len(named) != 1:
        return None
    scenarios = named[0][2]
    wanted = int(number.group(1))
    by_name = [text for name, text in scenarios if (m := SCENARIO_NUMBER.search(name)) and int(m.group(1)) == wanted]
    if len(by_name) == 1:
        return by_name[0]
    return scenarios[wanted - 1][1] if 1 <= wanted <= len(scenarios) and not by_name else None

def _occurs(value: str, text: str) -> bool:
    if not value:
        return True
    head = r"(?<!\w)" if value[0].isalnum() or value[0] == "_" else ""
    tail = r"(?!\w)" if value[-1].isalnum() or value[-1] == "_" else ""
    return re.search(head + re.escape(value) + tail, text) is not None

@functools.lru_cache(maxsize=64)
def _templates(text: str) -> list[re.Pattern]:
    patterns = []
    for quoted in PLACEHOLDER_QUOTE.findall(text):
        parts = re.split(r"<[^<>]{1,60}>", quoted)
        patterns.append(re.compile(".{1,80}?".join(re.escape(part) for part in parts), re.S))
    return patterns

def _numbers(text: str) -> list[float]:
    text = CELL_REFERENCE.sub(" ", re.sub(r"\bREQ[-_.\w]*", " ", text, flags=re.IGNORECASE)).replace(",", "")
    return [float(n) for n in NUMBER_TOKEN.findall(text)]

def _derivable(value: float, pool: list[float]) -> bool:
    close = lambda a, b: abs(a - b) <= 1e-9 * max(1.0, abs(b))
    if any(close(n, value) for n in pool):
        return True
    for i, a in enumerate(pool):
        for j, b in enumerate(pool):
            if i == j:
                continue
            if close(a + b, value) or close(a - b, value) or close(a * b, value) or (b and close(a / b, value)):
                return True
    return len(pool) >= 3 and close(sum(pool), value)

def unsupported_assertions(rel: str, text: str, batch: dict | None) -> list[str]:
    if not rel.startswith("backend/test-e2e/") or not isinstance(batch, dict):
        return []
    try:
        leaves, background, elsewhere = _batch_requirement(batch)
    except Exception:
        return []
    if not leaves:
        return []
    descriptions = _fold("\n".join(leaf[1] for leaf in leaves) + "\n" + background)
    every_scenario = _fold("\n".join(s[1] for leaf in leaves for s in leaf[2]))
    code = _blank_in_place(text)
    tests: list[tuple[int, int, str, bool]] = []
    for match in TEST_CALL.finditer(code):
        call = _argument_spans(code, match.end() - 1)
        if not call or not call[0]:
            continue
        title = _js_literal(text[call[0][0][0]:call[0][0][1]])
        if title is None:
            continue
        tests.append((match.start(), call[1], title, match.group(1) in ("skip", "fixme")))
    assertions: list[tuple[int, tuple[int, int]]] = []
    for match in ASSERT_TEXT_CALL.finditer(code):
        call = _argument_spans(code, match.end() - 1)
        if call and len(call[0]) >= 2 and code[call[0][0][0]:call[0][0][1]].strip() == "page":
            assertions.append((match.start(), call[0][1]))
    for match in ASSERT_MATCHER.finditer(code):
        call = _argument_spans(code, match.end() - 1)
        if call and call[0]:
            assertions.append((match.start(), call[0][0]))
    if not assertions:
        return []
    asserted_spans = [span for _position, span in assertions]
    title_spans = []
    for start, _end, _title, _skip in tests:
        call = _argument_spans(code, TEST_CALL.match(code, start).end() - 1)
        title_spans.append(call[0][0])
    strings: list[tuple[int, str]] = []
    for match in JS_STRING.finditer(code):
        inside = any(a <= match.start() < b for a, b in asserted_spans + title_spans)
        value = _js_literal(text[match.start():match.end()])
        if not inside and value is not None and value.strip():
            strings.append((match.start(), value))
    outside = [value for position, value in strings if not any(s <= position <= e for s, e, _t, _k in tests)]
    problems: list[str] = []
    reported: set[tuple[int, str]] = set()
    lines = text.splitlines()
    for position, (start, end) in sorted(assertions):
        test = next((t for t in tests if t[0] <= position <= t[1]), None)
        if test and test[3]:
            continue
        own = _own_scenario(test[2], leaves) if test else None
        stated = descriptions + " \n " + (_fold(own) if own is not None else every_scenario)
        typed = outside + ([value for p, value in strings if test[0] <= p < position] if test else [value for _p, value in strings])

        typed_folded = [_fold(value.replace('""', '"')) for value in typed]
        for value in _asserted_values(text[start:end]):
            folded = _fold(value).strip(" :.,;")
            if (not folded or folded.startswith("=") or CELL_REFERENCE.fullmatch(value.strip())
                    or re.fullmatch(r"[A-Z]{1,3}", value.strip())):
                continue
            numeric = NUMBER_LITERAL.fullmatch(value.strip()) is not None

            def supported(part: str) -> bool:
                if (_occurs(part, stated) or any(pattern.fullmatch(part) for pattern in _templates(stated))
                        or any(part == t or _occurs(part, t) for t in typed_folded)):
                    return True

                return not numeric and (_occurs(part, elsewhere) or any(pattern.fullmatch(part) for pattern in _templates(elsewhere)))

            if supported(folded):
                continue

            parts = [p.strip(" :.,;") for p in re.split(r"\s*[/:·|>]\s*", folded)]
            if len(parts) > 1 and all(not p or supported(p) for p in parts):
                continue

            if re.search(r"[^\W\d_]", folded) and re.search(r"\d", folded):
                variant = re.sub(r"\d+", lambda _m: r"\d+", re.escape(folded))
                if re.search(r"(?<!\w)" + variant + r"(?!\w)", stated + " \n " + elsewhere):
                    continue
            if numeric:
                number = float(re.sub(r"[^\d.\-]", "", value.strip().replace(",", "")) or "nan")

                own_text = " ".join(leaf[1] for leaf in leaves) + " " + (
                    own if own is not None else " ".join(s[1] for leaf in leaves for s in leaf[2]))

                if any(t.lstrip().startswith("=") for t in typed) or FORMULA.search(own_text):
                    continue

                if AGGREGATION.search(own_text):
                    continue

                pool = collections.Counter(_numbers(own_text)) | collections.Counter(n for t in typed for n in _numbers(t))
                if _derivable(number, sorted(pool.elements())):
                    continue
            key = (test[0] if test else -1, folded)
            if key in reported:
                continue
            reported.add(key)
            line = text.count("\n", 0, position) + 1
            shown = lines[line - 1].strip() if line <= len(lines) else value
            if numeric:
                why = "a number the requirement never states and the test neither types nor computes from stated values"
            elif own is not None:
                why = ("which the requirement never states for this test (neither in this batch's descriptions nor in "
                       "the scenario the test implements, and no other requirement states it)")
            else:
                why = "which the requirement never states (not in this batch's descriptions or scenarios, nor anywhere else)"
            problems.append(f"{rel}:{line}: `{shown[:160]}` asserts \"{value}\", {why}; the test does not type or create "
                            "it before either. A check of a text nobody stated fails a correct application: assert what "
                            "the requirement states (its quoted texts, the values of the scenario this test implements), "
                            "or leave that check out.")
    return problems

PLAYWRIGHT_ROLES = ARIA_ROLES | {"blockquote", "caption", "code", "definition", "deletion", "directory", "emphasis", "generic",
                                 "insertion", "log", "mark", "marquee", "math", "meter", "paragraph", "strong", "subscript",
                                 "superscript", "time"}
NOT_TARGET_HELPERS = {"test", "expect", "inventory", "freshSession"}

def helper_role_words() -> tuple[frozenset[str], frozenset[str], frozenset[str]] | None:
    try:
        from .outsider import HELPER_JS

        roles = re.search(r"const ARIA_ROLES = new Set\(\((.*?)\)\.split\(' '\)\)", HELPER_JS, re.S)
        fields = re.search(r"const FIELD_WORDS = new Set\(\[(.*?)\]\)", HELPER_JS, re.S)
        aliases = re.search(r"const ROLE_ALIASES = \{(.*?)\}", HELPER_JS, re.S)
        if not (roles and fields and aliases):
            return None
        role_set = frozenset("".join(re.findall(r"'([^']*)'", roles.group(1))).split())
        field_set = frozenset(re.findall(r"'([^']*)'", fields.group(1)))
        alias_set = frozenset(re.findall(r"([A-Za-z]+)\s*:", aliases.group(1)))
        return (role_set, field_set, alias_set) if len(role_set) > 40 and field_set and alias_set else None
    except Exception:
        return None

def _helper_accepts_role(role: str, words: tuple[frozenset[str], frozenset[str], frozenset[str]]) -> bool:
    roles, fields, aliases = words
    word = role.strip()
    lower = word.lower()
    return word in roles or lower in roles or lower in aliases or lower in fields

def unknown_roles(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    code = _blank_in_place(text)
    lines = text.splitlines()
    problems: list[str] = []

    def line_of(pos: int) -> tuple[int, str]:
        number = code.count("\n", 0, pos) + 1
        return number, (lines[number - 1].strip()[:160] if number <= len(lines) else "")

    def literal_at(quote_at: int) -> str | None:
        quote = code[quote_at]
        end = code.find(quote, quote_at + 1)
        return None if end < 0 else _js_literal(text[quote_at:end + 1])

    for match in re.finditer(r"\.\s*getByRole\s*\(\s*([\"'`])", code):
        value = literal_at(match.end() - 1)
        if value is None or not value.strip() or value.strip().lower() in PLAYWRIGHT_ROLES:
            continue
        number, shown = line_of(match.start())
        problems.append(f"{rel}:{number}: `{shown}` -- getByRole(\"{value}\"): \"{value}\" is no ARIA role, so it matches "
                        "nothing (a password field has no role at all). Use a role the element has (button, link, textbox, "
                        "checkbox, combobox, heading ...) or find it by the name / label the requirement quotes.")
    words = helper_role_words()
    if words is None or "e2e-support/outsider" not in text:
        return list(dict.fromkeys(problems))
    try:
        from .outsider import HELPER_JS

        exported = {n.strip() for n in re.search(r"module\.exports\s*=\s*\{([^}]*)\}", HELPER_JS).group(1).split(",")}
    except Exception:
        return list(dict.fromkeys(problems))
    helpers = {n for n in exported if n and n not in NOT_TARGET_HELPERS
               and not re.search(r"\b(?:function|class)\s*\*?\s*" + re.escape(n) + r"\b|(?:const|let|var)\s+" + re.escape(n) + r"\b", code)}

    def enclosing_open(pos: int) -> int | None:
        depth = 0
        for index in range(pos - 1, -1, -1):
            char = code[index]
            if char in ")]}":
                depth += 1
            elif char in "([{":
                if depth == 0:
                    return index
                depth -= 1
        return None

    def callee(open_paren: int) -> str | None:
        found = re.search(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*$", code[:open_paren])
        return found.group(1) if found else None

    held: dict[str, list[tuple[int, str]]] = {}
    for match in re.finditer(r"(?<![\w$.])role\s*:\s*([\"'`])", code):
        value = literal_at(match.end() - 1)
        if value is None or not value.strip() or _helper_accepts_role(value, words):
            continue
        brace = enclosing_open(match.start())
        if brace is None or code[brace] != "{":
            continue
        outer = enclosing_open(brace)
        if outer is not None and code[outer] == "(" and callee(outer) in helpers:
            number, shown = line_of(match.start())
            problems.append(f"{rel}:{number}: `{shown}` -- {{ role: \"{value}\" }}: \"{value}\" is no ARIA role, and the helper "
                            "stops the test with SPEC DEFECT on it at run time. Use a role the element has (button, link, "
                            "textbox, checkbox, combobox, heading ...) or the plain name / label the requirement quotes "
                            "(a field by its label: fill(page, \"<its label>\", ...)).")
            continue
        assigned = re.search(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*$", code[:brace])
        if assigned:
            held.setdefault(assigned.group(1), []).append((match.start(), value))
    if held:
        reported: set[int] = set()
        for match in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code):
            if match.group(1) not in helpers:
                continue
            spans = _argument_spans(code, match.end() - 1)
            if spans is None:
                continue
            for start, end in spans[0]:
                name = code[start:end].strip()
                for position, value in held.get(name, []):
                    if position in reported:
                        continue
                    reported.add(position)
                    number, shown = line_of(position)
                    problems.append(f"{rel}:{number}: `{shown}` -- {{ role: \"{value}\" }} ({name}, passed to {match.group(1)}()): "
                                    f"\"{value}\" is no ARIA role, and the helper stops the test with SPEC DEFECT on it at run "
                                    "time. Use a role the element has (button, link, textbox, checkbox, combobox, heading ...) "
                                    "or the plain name / label the requirement quotes (a field by its label).")
    return list(dict.fromkeys(problems))

SEED_RENAME_CONTROL = re.compile(r"\brenam\w*|重命名|改名", re.IGNORECASE)
SEED_CREATE_CONTROL = re.compile(r"\b(?:new|add|create|duplicate|copy|clone|import|upload|insert)\b|新建|添加|创建|复制|导入|上传|插入", re.IGNORECASE)
SEED_CANCEL_CONTROL = re.compile(r"\b(?:cancel|discard|close)\b|取消|关闭", re.IGNORECASE)
_SEED_NAMED = r"""\(\s*page\s*,\s*(?:\{[^{}]*?\bname\s*:\s*)?(?P<q>["'`])(?P<name>(?:(?!(?P=q))[^\n$\\])*)(?P=q)"""
SEED_CLICK = re.compile(r"(?<![\w$.])(?P<verb>click|doubleClick|item)" + _SEED_NAMED)
SEED_FILL = re.compile(r"(?<![\w$.])fill\(\s*page\s*,\s*(?:\{[^{}]*\}|[\"'`][^\"'`\n]*[\"'`]|[A-Za-z_$][\w$.]*)\s*,\s*(?P<value>`[^`\n]*`|[^,)\n]+)")
SEED_ESCAPE = re.compile(r"(?<![\w$.])press\(\s*page\s*,\s*[\"'`](?:Escape|Esc)[\"'`]")
SEED_UNIQUE = re.compile(r"Date\.now\(\)|Math\.random\(\)|randomUUID\(\)")
SEED_UNIQUE_VAR = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]*)")
SEED_HELPER = re.compile(r"(?:async\s+function\s+(?P<a>[A-Za-z_$][\w$]*)\s*\(|(?:const|let)\s+(?P<b>[A-Za-z_$][\w$]*)\s*=\s*async\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*)")

def _seed_names(batch: dict | None) -> set[str]:
    if not isinstance(batch, dict):
        return set()
    return {v.strip() for v in re.findall(r"`([^`\n]{2,80})`", str(batch.get("seed_sentences") or "")) if v.strip()}

def seed_renames(rel: str, text: str, batch: dict | None) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    seeds = _seed_names(batch)
    if not seeds:
        return []
    source = strip_comments(text)
    code = _blank_in_place(source)
    unique_vars = {m.group(1) for m in SEED_UNIQUE_VAR.finditer(source) if SEED_UNIQUE.search(m.group(2))}
    helpers: dict[str, str] = {}
    for match in SEED_HELPER.finditer(code):
        brace = code.find("{", match.end() - 1)
        if brace < 0:
            continue
        depth = 0
        for index in range(brace, len(code)):
            depth += {"{": 1, "}": -1}.get(code[index], 0)
            if depth == 0:
                helpers[match.group("a") or match.group("b")] = source[brace:index + 1]
                break
    tests: list[tuple[str, str]] = []
    for match in TEST_CALL.finditer(code):
        if match.group(1) in ("skip", "fixme"):
            continue
        call = _argument_spans(code, match.end() - 1)
        if not call or not call[0]:
            continue
        title = _js_literal(source[call[0][0][0]:call[0][0][1]]) or "?"
        body = source[call[0][0][1]:call[1]]
        for _round in range(2):
            for name, helper_body in helpers.items():
                body = re.sub(r"(?<![\w$.])" + re.escape(name) + r"\s*\([^;\n]*?\)", lambda _m, b=helper_body: b, body)
        tests.append((title, body))
    problems: list[str] = []
    for title, body in tests:
        events: list[tuple[int, str, str]] = []
        for m in SEED_CLICK.finditer(body):
            events.append((m.start(), m.group("verb"), m.group("name").strip()))
        for m in SEED_FILL.finditer(body):
            value = m.group("value").strip()
            literal = _js_literal(value)
            unique = value in unique_vars or bool(SEED_UNIQUE.search(value)) and literal is None
            events.append((m.start(), "fill-unique" if unique else "fill", literal if literal is not None else value))
        for m in re.finditer(r"(?<![\w$.])upload\(", body):
            events.append((m.start(), "upload", ""))
        for m in SEED_ESCAPE.finditer(body):
            events.append((m.start(), "cancel", "Escape"))
        events.sort()
        opened: str | None = None
        renamed_at: int | None = None
        created = False
        found: str | None = None
        for index, (_pos, kind, value) in enumerate(events):
            if kind in ("click", "doubleClick", "item"):
                if SEED_CANCEL_CONTROL.search(value) and renamed_at is not None:
                    found = None
                    break
                if renamed_at is None and SEED_CREATE_CONTROL.search(value):
                    created = True
                if value in seeds and renamed_at is None and not created:
                    opened = value
                elif kind == "click" and opened and not created and SEED_RENAME_CONTROL.search(value):
                    renamed_at = index
            elif kind in ("upload",) and renamed_at is None:
                created = True
            elif kind == "fill-unique":
                if renamed_at is None:
                    created = True
                elif found is None:
                    found = opened
            elif kind == "fill" and value in seeds and found is not None and value == found:
                found = None
                break
            elif kind == "cancel" and renamed_at is not None:
                found = None
                break
        if found:
            problems.append(
                f'{rel}: test "{title}" opens the shared seed record "{found}" and renames it (or a part of it) to a '
                "generated name. Every other test -- in this file and in every later spec on the same "
                f'instance -- then finds no "{found}". Create a record of your own through the interface first and rename '
                f'that one, or rename "{found}" back through the interface before the test ends; tests of one file must '
                "not depend on each other's order.")
    return problems

NOT_SEE_CALL = re.compile(r"(?<![\w$.])notSee\s*\(")
BODY_SELECTOR = r"""["'`]\s*(?:body|html|:root)\s*["'`]"""
WHOLE_PAGE_TEXT = re.compile(
    r"\bpage\s*\.\s*(?:textContent|innerText|innerHTML)\s*\(\s*" + BODY_SELECTOR + r"\s*\)"
    r"|\bpage\s*\.\s*locator\s*\(\s*" + BODY_SELECTOR + r"\s*\)\s*\.\s*(?:textContent|innerText|innerHTML)\s*\(\s*\)"
    r"|\bpage\s*\.\s*content\s*\(\s*\)"
    r"|\bdocument\s*\.\s*(?:body|documentElement)\s*\.\s*(?:innerText|textContent|innerHTML|outerHTML)\b")

WHOLE_COMPARED = re.compile(r"^\s*\)\s*(?:\.\s*not\s*)?\.\s*(?:toBe|toEqual|toStrictEqual|toMatchSnapshot|toMatchInlineSnapshot|"
                            r"toHaveLength)\s*\(")
V144L_HINT_HEAD = ("ALSO LOOK AT, while you rewrite these files (advice -- none of these alone sends a file back; each points at "
                   "a check that may prove less than the requirement states):")

def _all_literals(text: str, code: str) -> list[tuple[int, int, str]]:
    found = []
    for match in JS_STRING.finditer(code):
        raw = text[match.start():match.end()]
        value = _js_literal(raw)
        if value is None and raw.startswith("`"):
            value = " ".join(re.split(r"\$\{[^}]*\}", raw[1:-1]))
        if value is not None and value.strip():
            found.append((match.start(), match.end(), _fold(value.replace('""', '"'))))
    return found

@functools.lru_cache(maxsize=4)
def _stated_anywhere(requirement_text: str) -> str:
    parsed = _requirement(requirement_text)
    nodes = "\n".join(t for _i, t in parsed.texts) if parsed else ""
    return _fold(nodes + "\n" + requirement_text)

@functools.lru_cache(maxsize=64)
def _filled_templates(text: str) -> list[re.Pattern]:
    patterns = []
    for quoted in PLACEHOLDER_QUOTE.findall(text):
        parts = re.split(r"<[^<>]{1,60}>", quoted)
        patterns.append(re.compile("(.{1,80}?)".join(re.escape(part) for part in parts), re.S))
    return patterns

@functools.lru_cache(maxsize=16)
def _quoted_values(folded_text: str) -> frozenset[str]:
    found = set()
    for quoted in re.findall(r"'([^'\n]{1,80})'", folded_text):
        found.add(quoted.strip(" :.,;"))
        found.update(part.strip(" :.,;") for part in re.split(r"\s*[/,;|]\s*", quoted))
    found.discard("")
    return frozenset(found)

def _named_somewhere(folded: str, stated: str, others: list[str]) -> bool:
    def used(part: str) -> bool:
        return any(part == o or _occurs(part, o) for o in others)

    def one(part: str) -> bool:
        if _occurs(part, stated) or used(part):
            return True

        for pattern in _filled_templates(stated):
            match = pattern.fullmatch(part)
            if match and all(used(g) or g in _quoted_values(stated) for g in (x.strip(" :.,;") for x in match.groups()) if g):
                return True
        return False
    if one(folded):
        return True
    parts = [p.strip(" :.,;") for p in re.split(r"\s*[/:·|>]\s*", folded)]
    return len(parts) > 1 and all(not p or one(p) for p in parts)

RUNTIME_JOIN = re.compile(r"\.\s*(?:join|concat|replace|replaceAll|padStart|padEnd|repeat)\s*\(|(?<![\w$.])String\s*\(")
PLUS_JOIN = re.compile(r"([\w$\]\)'\"`])\s*\+(?!\+)=?\s*(?=([\w$\[\('\"`]))")
JOIN_SEPARATORS = frozenset(" \t-_/.,:;|·>#+()[]&@")

OPAQUE_BUILD = re.compile(
    r"(?<![\w$.])(?:String\s*\.\s*from(?:CharCode|CodePoint)|atob|btoa|Buffer|TextDecoder|Intl|decodeURI(?:Component)?"
    r"|unescape)\b"
    r"|(?<![\w$.])(?:new\s+)?Date\s*\("
    r"|\.\s*(?:slice|substring|substr|split|charAt|at|replace|replaceAll|reverse|normalize|toDateString|toTimeString"
    r"|toUTCString|toGMTString|toLocale(?:Date|Time)?String)\s*\("
    r"|\.\s*toString\s*\(\s*[^\s)]")

_RANDOM_ROOT = r"(?:Math\s*\.\s*random|Date\s*\.\s*now|crypto\s*\.\s*randomUUID|performance\s*\.\s*now)\s*\(\s*\)"
_BALANCED = r"(?:[^(),;{}]|\((?:[^()]|\([^()]*\))*\))*?"
RANDOM_CHAIN = re.compile(
    r"(?<![\w$.])(?:" + _RANDOM_ROOT + r"|(?:[\w$.]+\s*)?\(" + _BALANCED + _RANDOM_ROOT + _BALANCED + r"\))"
    r"(?:\s*\.\s*[\w$]+(?:\s*\((?:[^()]|\([^()]*\))*\))?)*")

def _builds_strings(text: str, code: str) -> bool:
    if any("${" in m.group(0) for m in JS_STRING.finditer(text) if m.group(0).startswith("`")):
        return True
    if RUNTIME_JOIN.search(code):
        return True
    for match in PLUS_JOIN.finditer(code):
        left = re.search(r"[\w$.]*$", code[:match.start(1) + 1]).group(0)
        right = re.match(r"[\w$.]*", code[match.end():]).group(0)
        if not (re.fullmatch(r"\d[\d.]*", left or "x") or re.fullmatch(r"\d[\d.]*", right or "x")):
            return True
    return False

def _opaque_build(text: str, code: str) -> str | None:
    def shown(source: str, start: int) -> str:
        return " ".join(re.match(r"[^)\n]{0,40}\)?", source[start:]).group(0).split())

    def unrandom(source: str) -> str:
        return RANDOM_CHAIN.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)
    found = OPAQUE_BUILD.search(unrandom(code))
    if found:
        return shown(text, found.start())
    for match in JS_STRING.finditer(text):
        if match.group(0).startswith("`"):
            for hole in re.finditer(r"\$\{([^}]*)\}", match.group(0)):
                inner = OPAQUE_BUILD.search(unrandom(_blank_in_place(hole.group(1))))
                if inner:
                    return shown(hole.group(1), inner.start())
    return None

def _string_pieces(text: str, code: str, skip: tuple[int, int]) -> list[str]:
    pieces = []
    for match in JS_STRING.finditer(code):
        if skip[0] <= match.start() < skip[1]:
            continue
        raw = text[match.start():match.end()]
        value = _js_literal(raw)
        parts = [value] if value is not None else re.split(r"\$\{[^}]*\}", raw[1:-1]) if raw.startswith("`") else []
        pieces.extend(folded for part in parts if (folded := _fold(part).strip()))
    return list(dict.fromkeys(pieces))

def _joinable(folded: str, pieces: list[str]) -> bool:
    size = len(folded)
    if not size or not pieces:
        return False
    reach = [False] * (size + 1)
    for piece in pieces:
        for cut in range(len(piece)):
            tail = piece[cut:]
            if folded.startswith(tail):
                reach[len(tail)] = True
            elif tail.startswith(folded):
                return True
    reach[0] = True
    for index in range(size):
        if not reach[index]:
            continue
        if folded[index] in JOIN_SEPARATORS:
            reach[index + 1] = True
        rest = folded[index:]
        for piece in pieces:
            if rest.startswith(piece):
                reach[index + len(piece)] = True
            elif index and piece.startswith(rest):
                return True
    return reach[size]

def vacuous_absence_checks(rel: str, text: str, batch: dict | None) -> list[str]:
    return _vacuous_absence(rel, text, batch)[0]

def vacuous_absence_hints(rel: str, text: str, batch: dict | None) -> list[str]:
    return _vacuous_absence(rel, text, batch)[1]

def _vacuous_absence(rel: str, text: str, batch: dict | None) -> tuple[list[str], list[str]]:
    if not rel.startswith("backend/test-e2e/") or not isinstance(batch, dict):
        return [], []
    whole = str(batch.get("requirement_text") or "")
    if not whole.strip():
        return [], []
    stated = _stated_anywhere(whole) + " \n " + _fold(str(batch.get("ancestor_context") or "")) + " \n " + _fold(
        str(batch.get("seed_sentences") or ""))
    code = _blank_in_place(text)
    literals = _all_literals(text, code)
    lines = text.splitlines()
    builds = _builds_strings(text, code)
    opaque = _opaque_build(text, code)
    problems: list[str] = []
    hints: list[str] = []
    for match in NOT_SEE_CALL.finditer(code):
        call = _argument_spans(code, match.end() - 1)
        if not call or len(call[0]) < 2 or code[call[0][0][0]:call[0][0][1]].strip() != "page":
            continue
        start, end = call[0][1]
        others = [value for s, _e, value in literals if not start <= s < end]
        for value in _asserted_values(text[start:end]):
            folded = _fold(value).strip(" :.,;")
            if not folded or re.search(r"\d", folded) or _named_somewhere(folded, stated, others):
                continue
            line = text.count("\n", 0, match.start()) + 1
            shown = lines[line - 1].strip() if line <= len(lines) else value
            if builds and _joinable(folded, _string_pieces(text, code, (start, end))):
                hints.append(
                    f"{rel}:{line}: `{shown[:120]}` checks that \"{value}\" is NOT shown; neither the requirement nor any "
                    "single string of this file says it -- only a join of this file's own strings can give it. If the "
                    "test does not create exactly that text, the check proves nothing: name what must be absent with "
                    "the requirement's words or the value the test created.")
                continue
            if opaque:
                made = opaque
                hints.append(
                    f"{rel}:{line}: `{shown[:120]}` checks that \"{value}\" is NOT shown; neither the requirement nor any "
                    f"string of this file says it, and this file makes strings a reading cannot evaluate (`{made}`). If "
                    "the test does not create exactly that text, the check proves nothing: name what must be absent with "
                    "the requirement's words or the value the test created.")
                continue
            problems.append(
                f"{rel}:{line}: `{shown[:160]}` checks that \"{value}\" is NOT shown, but the requirement never uses the "
                f"text \"{value}\" (no requirement, scenario, description or seed sentence says it) and this file never "
                "types, creates or shows it either. A correct application never shows a text nobody stated, so this check "
                "passes whatever the application does and proves nothing. Name what must be absent with the words the "
                "requirement uses for it (its quoted control / message, or the record this test created), or leave the "
                "check out.")
    return problems, hints

def _block_end(code: str, start: int) -> int:
    depth = 0
    for index in range(start, len(code)):
        char = code[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return index
            depth -= 1
    return len(code)

def _body_end(code: str, arrow_end: int) -> int:
    rest = re.match(r"\s*", code[arrow_end:])
    index = arrow_end + rest.end()
    if index < len(code) and code[index] == "{":
        return _block_end(code, index + 1) + 1
    depth = 0
    for position in range(index, len(code)):
        char = code[position]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return position
            depth -= 1
        elif char in ",;" and depth == 0:
            return position
    return len(code)

def _binding_region(code: str, declared: int, start: int, name: str) -> str:
    end = _block_end(code, declared)
    region = list(code[start:end])
    word = re.escape(name)
    shadows = [m.start() for m in re.finditer(r"(?<![\w$.])(?:const|let|var)\s+" + word + r"(?![\w$])", code[start:end])]
    shadows += [m.start() for m in re.finditer(
        r"(?<![\w$.])(?:const|let|var)\s*[{\[][^=;]*?(?<![\w$.:])" + word + r"(?![\w$:])[^=;]*?[}\]]\s*=(?!=)", code[start:end])]
    for found in shadows:
        stop = _block_end(code, start + found) - start
        region[found:stop] = " " * (stop - found)
    params = (r"\(([^()]*)\)\s*=>|(?<![\w$.])([A-Za-z_$][\w$]*)\s*=>|\bfunction\b\s*[\w$]*\s*\(([^()]*)\)\s*(?=\{)"
              r"|\bcatch\s*\(([^()]*)\)\s*(?=\{)")
    for match in re.finditer(params, code[start:end]):
        names = next(g for g in match.groups() if g is not None)
        if re.search(r"(?<![\w$.])" + word + r"(?![\w$])", names):
            stop = min(_body_end(code, start + match.end()), end) - start
            region[match.start():stop] = " " * (stop - match.start())
    scoped = "".join(region)
    again = re.search(r"(?<![\w$.])" + word + r"\s*(?:=(?![=>])|\+=)", scoped)
    return scoped[:again.start()] if again else scoped

def whole_page_text_checks(rel: str, text: str) -> tuple[list[str], list[str]]:
    if not rel.startswith("backend/test-e2e/"):
        return [], []
    code = _blank_in_place(text)
    lines = text.splitlines()
    problems: list[str] = []
    hints: list[str] = []
    for match in WHOLE_PAGE_TEXT.finditer(strip_comments(text)):
        if match.start() >= len(code) or code[match.start()] != text[match.start()]:
            continue
        line = text.count("\n", 0, match.start()) + 1
        shown = lines[line - 1].strip() if line <= len(lines) else match.group(0)
        compared = False

        head = code[:match.start()]
        inside = re.search(r"\bexpect\s*\(\s*\(?\s*(?:await\s+)?\(?\s*$", head)
        if inside:
            rest = code[match.end():]
            depth = 0
            for index, char in enumerate(rest):
                if char in "([{":
                    depth += 1
                elif char in ")]}":
                    if depth == 0:
                        compared = WHOLE_COMPARED.match(rest, index) is not None
                        break
                    depth -= 1

        bound = re.search(r"(?<![\w$.])(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=\s*(?:\(\s*)?(?:await\s+)?(?:\(\s*)?"
                          r"(?:page\s*\.\s*evaluate\s*\(\s*(?:async\s*)?\(\s*\)\s*=>\s*\{?\s*(?:return\s+)?)?$", head)
        if bound and not compared:
            name = re.escape(bound.group(1))
            later = _binding_region(code, bound.start(), match.end(), bound.group(1))
            if (re.search(r"\bexpect\s*\(\s*" + name + r"(?:\s*\|\|\s*[\"'`]\s*[\"'`])?\s*\)\s*(?:\.\s*not\s*)?\.\s*(?:toBe|toEqual|"
                          r"toStrictEqual|toMatchSnapshot|toMatchInlineSnapshot|toHaveLength)\s*\(", later)
                    or re.search(r"\.\s*(?:toBe|toEqual|toStrictEqual)\s*\(\s*" + name + r"\s*\)", later)
                    or re.search(r"(?<![\w$.])" + name + r"\s*[!=]==?(?!>)|[!=]==?\s*" + name + r"(?![\w$])", later)
                    or re.search(r"(?<![\w$.])" + name + r"\s*\.\s*length\b", later)):
                compared = True
        if compared:
            problems.append(
                f"{rel}:{line}: `{shown[:160]}` reads the text of the WHOLE page and compares it (equal / unequal / a "
                "snapshot / a length). The whole page also holds the header, the signed-in user, menus, counters and "
                "times, so it differs -- or matches -- for reasons that are not the stated value, and the check passes on "
                "a wrong application. Check the element that carries the value: see(page, \"<the stated text>\", "
                "<its region / item>) or expect(locate(page, \"<its stated name>\")).toHaveText(...).")
        else:
            hints.append(f"{rel}:{line}: `{shown[:120]}` searches the text of the whole page -- the header and the menus "
                         "count too. Scope it to the element or region the requirement puts the value in.")
    return problems, hints

LOCATE_CONTAINS = re.compile(r"\bexpect\s*\(\s*(?:await\s+)?locate\s*\(")

NAME_FROM_TEXT_ROLES = {"link", "heading", "tab", "menuitem", "option"}

def exact_name_contains(rel: str, text: str, batch: dict | None) -> list[str]:
    if not rel.startswith("backend/test-e2e/") or not isinstance(batch, dict):
        return []
    whole = str(batch.get("requirement_text") or "")
    if not whole.strip():
        return []
    stated = _stated_anywhere(whole)
    code = _blank_in_place(text)
    lines = text.splitlines()
    hints: list[str] = []
    for match in LOCATE_CONTAINS.finditer(code):
        locate_call = _argument_spans(code, match.end() - 1)
        if not locate_call or len(locate_call[0]) < 2:
            continue
        target = text[locate_call[0][1][0]:locate_call[0][1][1]].strip()
        role = re.search(r"\brole\s*:\s*[\"'`]([\w-]+)[\"'`]", target) if target.startswith("{") else None
        if target.startswith("{") and not (role and role.group(1).lower() in NAME_FROM_TEXT_ROLES):
            continue
        names = [n for n in _asserted_values(target) if not CELL_REFERENCE.fullmatch(n.strip())]
        after = code[locate_call[1] + 1:]
        matcher = re.match(r"\s*\)\s*\.\s*toContainText\s*\(", after)
        if not names or not matcher:
            continue
        open_at = locate_call[1] + 1 + matcher.end() - 1
        contains = _argument_spans(code, open_at)
        if not contains or not contains[0]:
            continue
        name = _fold(names[0]).strip(" :.,;")
        for value in _asserted_values(text[contains[0][0][0]:contains[0][0][1]]):
            folded = _fold(value).strip(" :.,;")
            if not name or not folded or folded in name or name not in _quoted_values(stated):
                continue
            line = text.count("\n", 0, match.start()) + 1
            shown = lines[line - 1].strip() if line <= len(lines) else value
            hints.append(f"{rel}:{line}: `{shown[:120]}` -- locate(\"{names[0]}\") finds the element NAMED \"{names[0]}\" first, "
                         f"whose own text is that name; \"{value}\" in it holds only if the application puts it into that "
                         "same element. Check the name exactly (toHaveText) and look for the other value in the element "
                         f"that carries it (its row: see(page, \"{value}\", item(page, \"{names[0]}\"))).")
    return hints

WRITE_CONTROL = re.compile(r"^\s*(?:save|create|submit|update|merge|rename|publish|confirm\s+(?:merge|delete|import|transfer)|"
                           r"delete|archive|restore|transfer|import)\b", re.IGNORECASE)
RELOAD_AFTER = re.compile(r"\bpage\s*\.\s*(?:reload|goto|goBack|goForward)\s*\(|(?<![\w$.])freshSession\s*\(|"
                          r"\brequest\s*\.\s*get\s*\(")
CLICK_NAMED = re.compile(r"(?<![\w$.])(?:click|doubleClick)\s*\(")
REJECTED_TITLE = re.compile(r"\b(?:reject\w*|invalid|empty|blank|duplicate\w*|error\w*|cannot|can't|fail\w*|denied|not\s+allowed|"
                            r"required|refus\w*|forbidden|unauthori[sz]ed|without|cancel\w*|too\s+long|missing)\b", re.IGNORECASE)
REJECTION_TEXT = re.compile(r"already exists|is required|\brequired\b|invalid|incorrect|does not match|fail(?:s|ed)?\b|not allowed|"
                            r"cannot|can't|\berror\b|too long|too short|must (?:be|not|contain)", re.IGNORECASE)
NAVIGATION_WORD = re.compile(r"archive|trash|notes|home", re.IGNORECASE)
CANCEL_CONTROL = re.compile(r"^\s*(?:cancel|close|dismiss|ok|got it)\b", re.IGNORECASE)

def unreloaded_writes(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    code = _blank_in_place(text)
    hints: list[str] = []
    local_helpers = {m.group(1) or m.group(2) for m in re.finditer(
        r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*async\b", code)}
    for match in TEST_CALL.finditer(code):
        call = _argument_spans(code, match.end() - 1)
        if not call or not call[0] or match.group(1) in ("skip", "fixme"):
            continue
        title = _js_literal(text[call[0][0][0]:call[0][0][1]])
        body_start, body_end = match.end(), call[1]
        if REJECTED_TITLE.search(title or ""):
            continue
        first_write = last_write = None
        moved_on = False
        written = ""
        for click in CLICK_NAMED.finditer(code, body_start, body_end):
            args = _argument_spans(code, click.end() - 1)
            if not args or len(args[0]) < 2:
                continue
            names = _asserted_values(text[args[0][1][0]:args[0][1][1]])
            if any(WRITE_CONTROL.match(n) and not NAVIGATION_WORD.fullmatch(n.strip()) for n in names):
                first_write = args[1] if first_write is None else first_write
                last_write = args[1]
                moved_on = False
                written = next(n for n in names if WRITE_CONTROL.match(n))
            elif last_write is not None and not any(CANCEL_CONTROL.match(n) for n in names):
                moved_on = True

        if last_write is None or moved_on or RELOAD_AFTER.search(code, first_write, body_end):
            continue

        if REJECTION_TEXT.search(text[last_write:body_end]):
            continue
        if local_helpers and re.search(r"(?<![\w$.])(?:" + "|".join(map(re.escape, local_helpers)) + r")\s*\(",
                                       code[last_write:body_end]):
            continue
        line = text.count("\n", 0, match.start()) + 1
        hints.append(f"line {line} \"{(title or '')[:70]}\" (clicks \"{written}\")")
    if not hints:
        return []
    return [f"{rel}: {len(hints)} test(s) write and then check the result only on the page the click left them on -- "
            + "; ".join(hints[:4]) + ("; ..." if len(hints) > 4 else "") + ". Where the requirement states what the write "
            "changes, check it where it lands after a reload (page.reload(), or open that page again): the saved value, "
            "the merged / moved / deleted record on its own page."]

SIGN_IN_FIELD = re.compile(r"user\s*name|\buser\b|login|e-?mail|account", re.IGNORECASE)
SIGN_IN_HELPER = re.compile(r"(?<![\w$.])(?:sign|log)[_-]?in\w*\s*\(", re.IGNORECASE)
SEE_CALL = re.compile(r"(?<![\w$.])see\s*\(")

SIGN_IN_NAME = re.compile(r"^\s*(?:sign|log)\s*-?\s*in\b|^\s*登录", re.IGNORECASE)
PASSING_LOOK = re.compile(r"^\s*await\s+(?:see|seeOnce|notSee|expect|page\s*\.\s*(?:reload|waitFor\w*|goto))\b")
FUNCTION_HEADER = re.compile(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*async\b")

def _sign_in_fills(text: str, code: str, start: int, end: int) -> set[str]:
    found: set[str] = set()
    for match in re.finditer(r"(?<![\w$.])fill\s*\(", code[:end]):
        if match.start() < start:
            continue
        args = _argument_spans(code, match.end() - 1)
        if not args or len(args[0]) < 3:
            continue
        field = " ".join(_asserted_values(text[args[0][1][0]:args[0][1][1]]))
        value = _js_literal(text[args[0][2][0]:args[0][2][1]])
        if not (field and value and SIGN_IN_FIELD.search(field) and "@" not in value):
            continue
        for click in CLICK_NAMED.finditer(code, args[1], min(end, args[1] + 500)):
            click_args = _argument_spans(code, click.end() - 1)
            names = _asserted_values(text[click_args[0][1][0]:click_args[0][1][1]]) if click_args and len(click_args[0]) >= 2 else []
            if names:
                if any(SIGN_IN_NAME.match(n) for n in names):
                    found.add(_fold(value))
                break
    return found

def unscoped_account_names(rel: str, text: str) -> list[str]:
    if not rel.startswith("backend/test-e2e/"):
        return []
    code = _blank_in_place(text)

    signing: dict[str, set[str]] = {}
    for header in FUNCTION_HEADER.finditer(code):
        name = header.group(1) or header.group(2) or ""
        brace = code.find("{", header.end())
        body = _argument_spans(code, brace) if brace >= 0 else None
        if not body:
            continue
        typed = _sign_in_fills(text, code, brace, body[1])
        if typed or re.fullmatch(r"(?:sign|log)[_-]?in\w*", name, re.IGNORECASE):
            signing[name] = typed
    signing_call = (re.compile(r"(?<![\w$.])(?:" + "|".join(map(re.escape, signing)) + r")\s*\(") if signing else None)
    found: list[str] = []
    for match in TEST_CALL.finditer(code):
        call = _argument_spans(code, match.end() - 1)
        if not call or not call[0] or match.group(1) in ("skip", "fixme"):
            continue
        body_start, body_end = match.end(), call[1]
        accounts = set(_sign_in_fills(text, code, body_start, body_end))
        for helper_call in list(SIGN_IN_HELPER.finditer(code, body_start, body_end)) + (
                list(signing_call.finditer(code, body_start, body_end)) if signing_call else []):
            called = re.match(r"[A-Za-z_$][\w$]*", code[helper_call.start():]).group(0)
            accounts |= signing.get(called, set())
            args = _argument_spans(code, helper_call.end() - 1)
            literal = next((v for s, e in (args[0] if args else [])[:2] if (v := _js_literal(text[s:e]))), None)
            if literal:
                accounts.add(_fold(literal))
        accounts.discard("")
        if not accounts:
            continue
        awaits = [m.start() for m in re.finditer(r"\bawait\b", code[:body_end]) if m.start() >= body_start]
        for see_call in SEE_CALL.finditer(code, body_start, body_end):
            args = _argument_spans(code, see_call.end() - 1)
            if not args or len(args[0]) != 2 or code[args[0][0][0]:args[0][0][1]].strip() != "page":
                continue
            values = [_fold(v) for v in _asserted_values(text[args[0][1][0]:args[0][1][1]])]
            if not values or not all(v in accounts for v in values):
                continue

            own = max((a for a in awaits if a <= see_call.start()), default=None)
            confirms = False
            earlier = [a for a in awaits if own is not None and a < own]
            for index in range(len(earlier) - 1, -1, -1):
                statement = text[earlier[index]:(earlier[index + 1] if index + 1 < len(earlier) else own)]
                if SIGN_IN_HELPER.search(statement) or (signing_call and signing_call.search(statement)) or (
                        re.search(r"(?<![\w$.])click\s*\(", statement)
                        and any(SIGN_IN_NAME.match(v) for v in JS_LITERAL_VALUES(statement))):
                    confirms = True
                    break
                if not PASSING_LOOK.match(statement):
                    break
            if not confirms:
                found.append(f"line {text.count(chr(10), 0, see_call.start()) + 1} \"{values[0]}\"")
    if not found:
        return []
    return [f"{rel}: see(page, <the account this test signed in with>) without a scope ({', '.join(found[:6])}) -- the "
            "header shows that name on every page, so the check holds whatever the page shows. Where the requirement puts "
            "the name somewhere (an author, an assignee, a reviewer, a member row), pass that part as scope: see(page, "
            "\"<name>\", item(page, \"<its record>\")) or region(page, ...)."]

def JS_LITERAL_VALUES(source: str) -> list[str]:
    return [v for m in JS_STRING.finditer(source) if (v := _js_literal(m.group(0))) is not None]

def v144l_hints(rel: str, text: str, batch: dict | None) -> list[str]:
    try:
        return (unscoped_account_names(rel, text) + whole_page_text_checks(rel, text)[1] + exact_name_contains(rel, text, batch)
                + unreloaded_writes(rel, text) + vacuous_absence_hints(rel, text, batch))
    except Exception:
        return []

SINGLE_ELEMENT_HELPERS = ("locate", "item", "region")
SINGLE_CALL = re.compile(r"(?<![\w$.])(?P<helper>locate|item|region)\s*\(")
SINGLE_KEEPING = {"filter", "first", "last", "and"}
COUNT_MATCHER = re.compile(r"expect(?:\s*\.\s*soft)?\s*\((?P<lhs>[^()]*)\)\s*(?:\.\s*not\s*)?\.\s*(?P<matcher>toBe|toEqual|"
                           r"toStrictEqual|toBeGreaterThan|toBeGreaterThanOrEqual|toBeLessThan|toBeLessThanOrEqual)\s*\((?P<rhs>[^()]*)\)")
COUNT_COMPARE = re.compile(r"(?P<lhs>[^()=!<>&|?:,;{}]*?)(?P<op>===|!==|==|!=|<=|>=|<|>)(?P<rhs>[^()=!<>&|?:,;{}]*)")
COUNT_MARK = "§"

RAW_NARROWING = re.compile(r"\.\s*(?:(?P<end>first|last)\s*\(\s*\)|nth\s*\(\s*(?P<index>\d+)\s*\))")
LOCATOR_EVIDENCE = re.compile(r"\.\s*(?:locator|getBy[A-Z]\w*|filter|frameLocator|first|last|nth)\s*\(|"
                              r"(?<![\w$.])(?:locateAll|tagged|locate|item|region)\s*\(")

LOCATOR_ROOT = re.compile(r"\.\s*(?:locator|getBy[A-Z]\w*|frameLocator)\s*\(|(?<![\w$.])(?:locateAll|tagged|locate|item|region)\s*\(")
ROOT_HOPS = 3

def _matching_open(code: str, close: int) -> int | None:
    depth = 0
    for index in range(close, -1, -1):
        char = code[index]
        if char in ")]}":
            depth += 1
        elif char in "([{":
            depth -= 1
            if depth == 0:
                return index
    return None

def _chain_root(code: str, dot: int) -> int | None:
    position = dot
    while True:
        end = position - 1
        while end >= 0 and code[end].isspace():
            end -= 1
        if end < 0:
            return None
        while code[end] in ")]":
            opened = _matching_open(code, end)
            if opened is None:
                return None
            end = opened - 1
            while end >= 0 and code[end].isspace():
                end -= 1
            if end < 0 or not (code[end].isalnum() or code[end] in "_$)]"):
                return opened
        if not (code[end].isalnum() or code[end] in "_$"):
            return None
        start = end
        while start > 0 and (code[start - 1].isalnum() or code[start - 1] in "_$"):
            start -= 1
        before = start - 1
        while before >= 0 and code[before].isspace():
            before -= 1
        if before >= 0 and code[before] == "." and not code[max(0, before - 2):before + 1] == "...":
            position = before
            continue
        return start

def _chain_step(code: str, position: int) -> tuple[str, list[tuple[int, int]], int, int] | None:
    step = re.compile(r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(").match(code, position)
    if not step:
        return None
    spans = _argument_spans(code, step.end() - 1)
    if spans is None:
        return None
    return step.group(1), spans[0], spans[1], step.end() - 1

def _count_step(code: str, position: int) -> tuple[str, int, int, str] | None:
    while True:
        step = _chain_step(code, position)
        if step is None:
            return "", position, position, ""
        name, args, close, _ = step
        text = " ".join(code[a:b].strip() for a, b in args)
        if name in SINGLE_KEEPING or (name == "nth" and text.strip() == "0"):
            position = close + 1
            continue
        return name, position, close + 1, text

def _bare(text: str) -> str:
    bare = text.strip()
    while True:
        if bare.startswith("(") and bare.endswith(")"):
            bare = bare[1:-1].strip()
        elif re.match(r"await\s", bare):
            bare = bare[5:].strip()
        else:
            return bare

def _count_verdict(other: str, above: bool, equal: bool, single_count) -> str | None:
    bare = _bare(other)
    if re.fullmatch(r"\d+", bare):
        number = int(bare)
        return "reject" if number >= 2 or (number == 1 and above) else None
    return "hint"

def _miscounted(line: str, single_count=lambda name: False) -> tuple[str, str] | None:
    line = line.replace("=>", "  ")
    for match in COUNT_MATCHER.finditer(line):
        lhs, rhs, matcher = match.group("lhs"), match.group("rhs"), match.group("matcher")
        if COUNT_MARK not in lhs + rhs:
            continue
        mine, other = (lhs, rhs) if COUNT_MARK in lhs else (rhs, lhs)
        above = COUNT_MARK in lhs and matcher == "toBeGreaterThan"
        verdict = _count_verdict(other, above, matcher in ("toBe", "toEqual", "toStrictEqual"), single_count)
        if verdict and _bare(mine) != COUNT_MARK:
            verdict = "hint"
        if verdict:
            return verdict, match.group(0).strip()
    for match in COUNT_COMPARE.finditer(COUNT_MATCHER.sub(" ", line)):
        lhs, rhs, op = match.group("lhs"), match.group("rhs"), match.group("op")
        if COUNT_MARK not in lhs + rhs:
            continue
        mine, other = (lhs, rhs) if COUNT_MARK in lhs else (rhs, lhs)
        above = op == ">" if COUNT_MARK in lhs else op == "<"
        verdict = _count_verdict(other, above, op in ("===", "=="), single_count)
        if verdict and _bare(mine) != COUNT_MARK:
            verdict = "hint"
        if verdict:
            return verdict, match.group(0).strip()
    return None

def _raw_single_chains(code: str) -> list[tuple[int, int, str]]:
    chains = []
    for match in RAW_NARROWING.finditer(code):
        root = _chain_root(code, match.start())
        if root is None:
            continue
        strength = _locator_strength(code, code[root:match.start()], ROOT_HOPS, root)
        if not strength:
            continue
        step = f".{match.group('end')}()" if match.group("end") else f".nth({match.group('index')})"
        chains.append((root, match.end(), step, strength == "weak"))
    return chains

def _visible_declaration(code: str, name: str, at: int) -> tuple[re.Match, bool] | None:
    word = re.escape(name)
    visible = None
    unsure = False
    for match in re.compile(r"(?<![\w$.])(?:const|let|var)\s+" + word + r"\s*=\s*([^;\n]*)").finditer(code, 0, at):
        if _block_end(code, match.start()) >= at:
            visible, unsure = match, False
        elif match.group(0).startswith("var"):

            seen = _var_reaches(code, match.start(), at)
            if seen is not False:
                visible, unsure = match, seen is None
    if visible is None:
        return None
    base = visible.end()
    between = code[base:at]

    def holds_use(position: int) -> bool:
        start = base + position
        header = _enclosing_open(code, start, base)
        if header is not None and code[header] == "(":
            return _body_end(code, _block_end(code, header + 1) + 1) >= at
        return _block_end(code, start) >= at

    shadowed = (any(holds_use(m.start()) for m in re.finditer(
                    r"(?<![\w$.])(?:const|let|var)\s+(?:[^=;\n]*[\[{,]\s*)?" + word + r"(?![\w$])", between))
                or any(holds_use(params.start() + 1) and not re.search(
                    r"(?<![\w$.])(?:if|while|for|switch|with)\s*$", between[:params.start()])
                       for params in re.finditer(r"\([^()]*(?<![\w$.])" + word + r"(?![\w$])[^()]*\)\s*(?:=>|\{)", between))
                or any(_body_end(code, base + m.end()) >= at for m in re.finditer(r"(?<![\w$.])" + word + r"\s*=>", between))
                or any(not re.search(r"(?<![\w$.])(?:const|let|var)\s+$", between[:m.start()]) for m in re.finditer(
                    r"(?<![\w$.])" + word + r"\s*(?:[-+*/%&|^]|\*\*|\?\?|&&|\|\|)?=(?![=>])", between)))
    return visible, not shadowed and not unsure

def _var_reaches(code: str, declared: int, at: int) -> bool | None:
    position = declared
    while True:
        opening = _enclosing_open(code, position)
        if opening is None or _block_end(code, opening + 1) >= at:
            return True
        before = code[:opening].rstrip()
        if code[opening] == "(":
            if not re.search(r"(?<![\w$.])for\s*(?:await\s*)?$", before):
                return None
        elif code[opening] != "{":
            return None
        elif before.endswith("=>"):
            return False
        elif before.endswith(")"):
            head = _enclosing_open(code, len(before) - 1)
            keyword = re.search(r"(?<![\w$.])(?:for\s+(?=await\s*$))?([A-Za-z_$][\w$]*)\s*$", code[:head]) if head is not None else None
            if keyword is None:
                return None
            if keyword.group(0).startswith("for") and keyword.group(1) == "await":
                pass
            elif keyword.group(1) not in ("if", "for", "while", "switch", "catch", "with"):
                return False
        elif not (re.search(r"(?<![\w$.])(?:else|try|finally|do)$", before) or before == "" or before[-1] in ";{}"):
            return None
        position = opening

def _locator_strength(code: str, chain: str, hops: int, at: int | None = None) -> str | None:
    if LOCATOR_ROOT.search(chain):
        return "strong"
    weak = bool(LOCATOR_EVIDENCE.search(chain))
    name = re.match(r"\s*(?:await\s+)?\(?\s*(?:await\s+)?([A-Za-z_$][\w$]*)", chain)
    if name and hops > 0 and name.group(1) not in ("await", "new", "this"):
        found = _visible_declaration(code, name.group(1), len(code) if at is None else at)
        if found and found[0].group(1).strip() != chain.strip():
            declared, certain = found
            below = _locator_strength(code, declared.group(1), hops - 1, declared.start(1))
            first = re.search(r"(?<![\w$.])(?:const|let|var)\s+" + re.escape(name.group(1)) + r"\s*=\s*([^;\n]*)", code)

            v146_strong = first is not None and (first.start() == declared.start() or
                                                 _locator_strength(code, first.group(1), hops - 1, first.start(1)) == "strong")
            if below == "strong" and certain and v146_strong:
                return "strong"
            weak = weak or below in ("weak", "strong")
        if not weak and found:

            use = len(code) if at is None else at
            for other in re.finditer(r"(?<![\w$.])var\s+" + re.escape(name.group(1)) + r"\s*=\s*([^;\n]*)", code[:use]):
                if other.start() != found[0].start() and other.group(1).strip() != chain.strip() \
                        and (_block_end(code, other.start()) >= use or _var_reaches(code, other.start(), use) is not False) \
                        and _locator_strength(code, other.group(1), hops - 1, other.start(1)):
                    weak = True
                    break
    return "weak" if weak else None

def single_element_count_findings(rel: str, text: str) -> tuple[list[str], list[str]]:
    if not rel.startswith("backend/test-e2e/"):
        return [], []
    code = _blank_in_place(text)
    imported = {part.split(":")[0].strip() for group in re.findall(
        r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['\"][./]*e2e-support/outsider['\"]\s*\)", text)
        for part in group.split(",")} if "e2e-support/outsider" in text else set()
    own = set(re.findall(r"(?:function\s+|(?:const|let|var)\s+)(locate|item|region)\b", code))
    helpers = {h for h in SINGLE_ELEMENT_HELPERS if h in imported and h not in own}
    raw_chains = _raw_single_chains(code)
    if not helpers and not raw_chains:
        return [], []
    lines = text.splitlines()
    found: dict[int, tuple[str, str]] = {}
    counts: list[tuple[str, int, str]] = []
    collecting = [True]

    def line_of(position: int) -> int:
        return code.count("\n", 0, position) + 1

    def reused(name: str, start: int, region: str) -> bool:
        scope = code[start:start + len(region)]
        bare = re.escape(name)
        word = r"(?<![\w$.])" + bare + r"(?![\w$])"
        if (re.search(r"(?<![\w$.])(?:const|let|var)\s+" + bare + r"(?![\w$])", scope)
                or re.search(word + r"\s*(?:[-+*/%&|^]|\*\*|<<|>>>?|\?\?|\|\||&&)?=(?![=>])", scope)
                or re.search(word + r"\s*(?:\+\+|--)|(?:\+\+|--)\s*" + bare + r"(?![\w$])", scope)
                or re.search(r"[\[{][^;=()]*?" + word + r"(?!\s*:)[^;=()]*?[\]}]\s*=(?![=>])", scope)
                or re.search(r"(?<![\w$.])(?:const|let|var)\s*[\[{][^;=()]*?" + word, scope)
                or re.search(word + r"\s*=>", scope)):
            return True
        for call in re.finditer(r"([\w$]*)\s*\(([^()]*)\)\s*(?:=>|\{)", scope):
            if call.group(1) not in ("if", "while", "for", "switch", "with") and re.search(word, call.group(2)):
                return True
        return False

    def single_count(name: str, position: int) -> bool:
        return any(name == bound and 0 <= position - start < len(region) and not reused(name, start, region)
                   for bound, start, region in counts)

    def report(position: int, helper: str, what: str, verdict: str) -> None:
        if collecting[0]:
            return
        line = line_of(position)
        if line in found and (found[line][0] == "reject" or verdict == "hint"):
            return
        shown = lines[line - 1].strip() if line <= len(lines) else ""
        if helper.startswith("."):
            head = (f"{rel}:{line}: `{shown[:160]}` counts a locator whose last narrowing step is {helper} -- one "
                    "element, so its count is only ever 0 or 1")
            advice = (f"If you mean to count, count the lookup without {helper} -- every match of it: const before = "
                      f"await page.getByRole(\"link\").count(); ... await expect(page.getByRole(\"link\"))"
                      f".toHaveCount(before + 1) (or locateAll(page, <the target>, <scope>) from the helper).")
            kept = f"on the {helper} locator"
        else:
            role = {"locate": "the FIRST visible match of its target", "item": "the ONE entry showing its text",
                    "region": "the ONE part of the page it names"}[helper]
            head = (f"{rel}:{line}: `{shown[:160]}` counts {helper}(...), which is {role} -- one element, so its count is "
                    "only ever 0 or 1")
            advice = ("If you mean to count, use locateAll(page, <the target: its role / name>, <scope>) -- every visible "
                      "match of the same lookup: const before = await locateAll(page, { role: \"link\" }).count(); ... "
                      "await expect(locateAll(page, { role: \"link\" })).toHaveCount(before + 1).")
            kept = f"on {helper}()"
        if verdict == "reject":
            found[line] = (verdict, f"{head}, and {what} never measures the number the requirement states: the check "
                           f"can never pass on a correct application, or passes whatever it shows. {advice} (Present / "
                           f"absent -- a count of 0 or 1 -- may stay {kept}.)")
        else:
            found[line] = (verdict, f"{head}: {what} is right only as a present / absent check (0 or 1), and then it "
                           f"may stay. {advice}")

    def count_value(start: int, end: int, helper: str, soft: bool = False) -> None:
        begin, closing = start, end
        while True:
            awaited = re.search(r"(?<![\w$.])await\s+$", code[:begin])
            if awaited:
                begin = awaited.start()
            before = re.search(r"\(\s*$", code[:begin])
            after = re.match(r"\s*\)", code[closing:])
            if not before or not after:
                break
            previous = code[:before.start()].rstrip()
            if previous and (previous[-1].isalnum() or previous[-1] in "_$)]"):
                break
            begin, closing = before.start(), closing + after.end()
        bound = re.search(r"(?<![\w$.])(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=\s*$", code[:begin])
        rows = code.splitlines()
        lines_here = []
        region_of = ""
        if bound and re.match(r"\s*(?:;|\n|$)", code[closing:]):
            name = re.escape(bound.group(1))
            region = region_of = _binding_region(code, bound.start(), closing, bound.group(1))
            if collecting[0]:
                counts.append((bound.group(1), closing, region))
            for use in re.finditer(r"(?<![\w$.])" + name + r"(?![\w$])", region):
                at = closing + use.start()
                lines_here.append((at, re.sub(r"(?<![\w$.])" + name + r"(?![\w$])", COUNT_MARK, rows[line_of(at) - 1])))
        else:
            first = code.rfind("\n", 0, begin) + 1
            last = code.find("\n", closing)
            last = len(code) if last == -1 else last
            lines_here.append((start, code[first:begin] + COUNT_MARK + code[closing:last]))
        for at, line in lines_here:
            bad = _miscounted(line, lambda other, at=at: single_count(other, at))
            if bad:
                verdict = "hint" if soft or (region_of and reused(bound.group(1), closing, region_of)) else bad[0]
                report(at, helper, f"comparing it (`{bad[1].replace(COUNT_MARK, 'count')[:80]}`)", verdict)

    def judge(position: int, end: int, helper: str, soft: bool = False) -> None:
        name, step_start, step_end, argument = _count_step(code, end)
        if name == "count":
            count_value(position, step_end, helper, soft)
        elif name == "nth":
            literal = re.fullmatch(r"\d+", _bare(argument))
            report(position, helper, f".nth({argument[:20]}) (there is no element after the first)"
                   if literal else f".nth({argument[:20]})", "reject" if literal and not soft else "hint")
        elif name == "all":
            report(position, helper, ".all() (a list of at most one)", "hint")
        elif name == "":
            wrapper = re.search(r"expect(?:\s*\.\s*soft)?\s*\(\s*(?:await\s+)?$", code[:position])
            if wrapper:
                spans = _argument_spans(code, code.index("(", wrapper.start() + len("expect")))
                if spans and not code[step_end:spans[1]].strip():
                    matcher = re.match(r"\s*(?:\.\s*(?P<negated>not)\s*)?\.\s*toHaveCount\s*\(", code[spans[1] + 1:])
                    if matcher:
                        args = _argument_spans(code, spans[1] + 1 + matcher.end() - 1)
                        value = code[args[0][0][0]:args[0][0][1]] if args and args[0] else ""
                        verdict = _count_verdict(value, False, not matcher.group("negated"),
                                                 lambda other: single_count(other, position)) if args else None
                        if verdict:
                            report(position, helper, f"toHaveCount({value.strip()[:40]})", "hint" if soft else verdict)
            return

    def single_value(start: int, value_end: int, helper: str, soft: bool = False) -> None:
        name, _, chain_end, _ = _count_step(code, value_end)
        judge(start, value_end, helper, soft)
        if name:
            return

        bound = re.search(r"(?<![\w$.])(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?$", code[:start])
        if not bound or not re.match(r"\s*(?:;|\n|$)", code[chain_end:]):
            return
        variable = bound.group(1)
        region = _binding_region(code, bound.start(), chain_end, variable)
        for use in re.finditer(r"(?<![\w$.])" + re.escape(variable) + r"(?![\w$])", region):
            at = chain_end + use.start()
            judge(at, at + len(variable), helper, soft or reused(variable, chain_end, region))

    for collecting[0] in (True, False):
        for match in SINGLE_CALL.finditer(code):
            helper = match.group("helper")
            if helper not in helpers:
                continue
            spans = _argument_spans(code, match.end() - 1)
            if spans is None:
                continue
            single_value(match.start(), spans[1] + 1, helper)
        for start, value_end, label, soft in raw_chains:
            single_value(start, value_end, label, soft)
    rejects = [message for verdict, message in (found[line] for line in sorted(found)) if verdict == "reject"]
    hints = [message for verdict, message in (found[line] for line in sorted(found)) if verdict == "hint"]
    return rejects, hints

def single_element_counts(rel: str, text: str) -> list[str]:
    return single_element_count_findings(rel, text)[0]

def single_element_count_hints(rel: str, text: str) -> list[str]:
    return single_element_count_findings(rel, text)[1]

SEED_CONTENT_BEFORE = re.compile(r"\b(?:values?|contents?|texts?|headers?|cells?\s+\$?[A-Z]{1,3}\$?\d{1,5}|\$?[A-Z]{1,3}\$?\d{1,5})"
                                 r"\b[^`]{0,25}$")
LOOK_CALLS = {"see", "seeOnce", "notSee", "expect", "locate", "locateAll", "region", "item", "tagged", "inventory", "test",
              "String", "Number", "Boolean", "Array", "Object", "JSON", "RegExp", "Date", "parseInt", "parseFloat", "require"}
JS_KEYWORD_CALLS = {"if", "for", "while", "switch", "catch", "function", "return", "await", "typeof", "new", "async"}
ACTION_METHODS = {"click", "dblclick", "fill", "type", "press", "pressSequentially", "check", "uncheck", "setChecked",
                  "selectOption", "setInputFiles", "dragTo", "clear", "tap", "goto", "goBack", "goForward", "evaluate",
                  "dispatchEvent", "post", "put", "patch", "delete", "fetch", "focus", "hover", "selectText", "route",
                  "insertText", "down", "up", "move", "wheel", "setContent", "close", "newPage", "addCookies",
                  "clearCookies", "grantPermissions", "writeText"}
PASSWORD_FIELD = re.compile(r"pass\s*word|密码", re.IGNORECASE)

def _local_functions(code: str) -> dict[str, tuple[list[str], int, int]]:
    found: dict[str, tuple[list[str], int, int]] = {}
    for match in re.finditer(r"(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*\{"
                             r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(([^()]*)\)|([A-Za-z_$][\w$]*))\s*=>",
                             code):
        name = match.group(1) or match.group(3)
        params = match.group(2) if match.group(1) else (match.group(4) if match.group(4) is not None else match.group(5))
        if match.group(1):
            start, end = match.end(), _block_end(code, match.end())
        else:
            start, end = match.end(), _body_end(code, match.end())
        found.setdefault(name, ([p.strip() for p in (params or "").split(",") if p.strip()], start, end))
    return found

def _enclosing_open(code: str, pos: int, floor: int = 0) -> int | None:
    depth = 0
    for index in range(pos - 1, floor - 1, -1):
        char = code[index]
        if char in ")]}":
            depth += 1
        elif char in "([{":
            if depth == 0:
                return index
            depth -= 1
    return None

def _callee_at(code: str, open_paren: int) -> tuple[str | None, bool]:
    found = re.search(r"(\.\s*)?([A-Za-z_$][\w$]*)\s*$", code[:open_paren])
    if not found or found.group(2) in JS_KEYWORD_CALLS:
        return None, False
    return found.group(2), bool(found.group(1))

def _test_spans(text: str, code: str) -> list[tuple[int, int, str]]:
    spans = []
    for match in TEST_CALL.finditer(code):
        if match.group(1) in ("skip", "fixme", "fail"):
            continue
        call = _argument_spans(code, match.end() - 1)
        if not call or not call[0]:
            continue
        spans.append((call[0][0][1], call[1], _js_literal(text[call[0][0][0]:call[0][0][1]]) or "?"))
    return spans

def _named_argument(text: str, code: str, span: tuple[int, int]) -> str | None:
    source = text[span[0]:span[1]].strip()
    if source.startswith("{"):
        match = re.search(r"\bname\s*:\s*(" + JS_STRING.pattern + r")\s*[,}]", source)
        return _js_literal(match.group(1)) if match else None
    return _js_literal(source)

def _seed_contents(batch: dict | None) -> list[tuple[str, list[str], str]]:
    if not isinstance(batch, dict):
        return []
    found = []

    digest = str(batch.get("seed_sentences") or "").split("SEED RECORDS WITH DIFFERENT CONTENTS")[0]
    for sentence in re.findall(r"^\s*-\s+(.+)$", digest, re.M):
        ticks = list(re.finditer(r"`([^`\n]{1,80})`", sentence))
        for index, tick in enumerate(ticks):
            if index == 0 or not SEED_CONTENT_BEFORE.search(sentence[ticks[index - 1].end():tick.start()]):
                continue
            if index > 2:
                continue
            holder = ticks[0].group(1).strip()
            value = tick.group(1).strip()

            if (not value or "/" in value or value.startswith("=") or _fold(value) == _fold(holder)
                    or re.fullmatch(r"\$?[A-Z]{1,3}\$?\d{1,5}(?::\$?[A-Z]{1,3}\$?\d{1,5})?", holder)):
                continue
            if (value, [holder]) not in [(v, h) for v, h, _s in found]:
                found.append((value, [holder], " ".join(sentence.split())))
    return found

def _seed_values(batch: dict | None) -> set[str]:
    return {_fold(v) for v in re.findall(r"`([^`\n]{1,80})`", str((batch or {}).get("seed_sentences") or "")) if v.strip()}

def seeded_value_absence(rel: str, text: str, batch: dict | None) -> tuple[list[str], list[str]]:
    if not rel.startswith("backend/test-e2e/"):
        return [], []
    contents = _seed_contents(batch)

    if not contents or re.search(r"\b(?:before|after)(?:Each|All)\s*\(|\bserial\b", text):
        return [], []
    seeds = _seed_values(batch)
    source = strip_comments(text)
    code = _blank_in_place(source)
    functions = _local_functions(code)
    lines = source.splitlines()
    problems: list[str] = []
    hints: list[str] = []

    def events(start: int, end: int, depth: int = 0) -> list[tuple[str, str, str]] | None:
        found: list[tuple[str, str, str]] = []
        for match in re.finditer(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*\(", code[start:end]):
            open_at = start + match.end() - 1
            name, method = _callee_at(code, open_at)
            if name is None:
                continue
            if method:
                chain = re.search(r"([A-Za-z_$][\w$]*)\s*\.\s*(?:[A-Za-z_$][\w$]*\s*\.\s*)*$", code[:start + match.start()])
                root = chain.group(1) if chain else ""
                if name == "reload":
                    continue
                if name == "goto" and root == "page":
                    found.append(("goto", "", ""))
                elif name in ACTION_METHODS:
                    return None
                continue
            if name in LOOK_CALLS:
                continue
            call = _argument_spans(code, open_at)
            if call is None:
                return None
            args = call[0]
            if name in ("click", "doubleClick"):
                target = _named_argument(source, code, args[1]) if len(args) >= 2 else None
                if target is None or len(args) > 2:
                    return None
                found.append(("click", target, " ".join(source[args[1][0]:args[1][1]].split())))
            elif name in ("fill", "typeText", "type"):
                if len(args) < 3:
                    return None
                target = _named_argument(source, code, args[1]) or ""
                found.append(("fill", target, _js_literal(source[args[2][0]:args[2][1]]) or ""))
            elif name == "freshSession":
                found.append(("session", "", ""))
            elif name in functions and depth < 3:
                _params, body_start, body_end = functions[name]
                inner = events(body_start, body_end, depth + 1)
                if inner is None:
                    return None
                found.extend(inner)
            else:
                return None
        return found

    def opened_before(body_start: int, position: int, holders: set[str], folded: str) -> str | None:
        steps = events(body_start, position)
        if steps is None:
            return None
        opened = None
        for index, (kind, target, typed) in enumerate(steps):
            if kind == "click" and _fold(target) in holders and _fold(target) != folded:
                opened = (target, tuple(steps[:index + 1]))
            elif opened:
                return None
            elif kind == "click" and SIGN_IN_NAME.match(target):
                continue
            elif kind == "fill" and (SIGN_IN_FIELD.search(target) or PASSWORD_FIELD.search(target) or _fold(typed) in seeds):
                continue
            elif kind not in ("goto", "session"):
                return None
        return opened

    def looks(pattern: re.Pattern) -> list[tuple[int, str, str, str, str, tuple]]:
        found = []
        for body_start, body_end, title in _test_spans(source, code):
            for match in pattern.finditer(code, body_start, body_end):
                call = _argument_spans(code, match.end() - 1)
                if not call or len(call[0]) < 2 or code[call[0][0][0]:call[0][0][1]].strip() != "page":
                    continue
                value = _named_argument(source, code, call[0][1])
                if value is None or not value.strip():
                    continue
                hit = next((c for c in contents if _fold(c[0]) == _fold(value)), None)
                if hit is None:
                    continue
                opened = opened_before(body_start, match.start(), {_fold(h) for h in hit[1]}, _fold(value))
                if opened is None:
                    continue
                scope = " ".join(source[call[0][2][0]:call[0][2][1]].split()) if len(call[0]) >= 3 else ""
                found.append((match.start(), title, value, _fold(value), scope, (opened[1], hit[2], opened[0])))
        return found

    seen = {(folded, scope, opened[0]) for _p, _t, _v, folded, scope, opened in looks(ASSERT_TEXT_CALL)}
    for position, title, value, folded, scope, (opened_key, sentence, opened) in looks(NOT_SEE_CALL):
        line = source.count("\n", 0, position) + 1
        shown = lines[line - 1].strip() if line <= len(lines) else value
        if (folded, scope, opened_key) in seen:
            problems.append(
                f'{rel}:{line}: `{shown[:160]}` (test "{title}") checks that "{value}" is NOT shown right after the test '
                f'opens "{opened}", while another check of this file opens "{opened}" the same way and SEES "{value}" '
                f"there -- and the seed puts it in \"{opened}\" ({sentence[:220]}). Nothing in this test removes, renames, "
                "clears, filters or leaves it before the check, so a correct application fails it. Check absence only of "
                "what this test itself removed or changed (or of a record it created), after the step that removes it.")
        elif not scope:
            hints.append(
                f'{rel}:{line}: `{shown[:120]}` (test "{title}") checks that "{value}" is NOT shown right after opening '
                f'"{opened}", but the seed puts it in "{opened}" and the test changes nothing before the check -- make '
                "sure a step of the test removes or leaves it, or check it SEEN.")
    return list(dict.fromkeys(problems)), list(dict.fromkeys(hints))

INERT_CALLS = {"see", "seeOnce", "notSee", "expect", "locate", "locateAll", "item", "region", "tagged", "click",
               "doubleClick", "rightClick", "hover", "test"}
UNIQUE_INIT = re.compile(r"""^(?:(?:["'][^"'\n]*["']|`[^`$\n]*(?:\$\{[^}]*\}[^`$\n]*)*`|""" + _RANDOM_ROOT
                         + r"""|\(|\)|\s|\+|\.\s*toString\s*\(\s*\d*\s*\)|\.\s*slice\s*\([\d\s,-]*\)|Math\s*\.\s*floor\s*\(|\*\s*[\d.e]+|[\d.e]+)+)$""")
ASSERT_SEEN = re.compile(r"(?<![\w$.])(?P<helper>see|seeOnce)\s*\(|(?<!\.not)\.\s*(?P<matcher>toHaveText|toContainText|toHaveValue)\s*\(")

def _inert_use(code: str, pos: int, functions: dict[str, tuple[list[str], int, int]], depth: int = 0,
               source: str | None = None) -> bool:
    at = pos
    while True:
        opened = _enclosing_open(code, at)
        if opened is None:
            return False
        if code[opened] in "[{":
            before = code[:opened].rstrip()
            if code[opened] == "{" and (before.endswith("=>") or before.endswith(")")):
                return False
            at = opened
            continue
        name, method = _callee_at(code, opened)
        if name is None:
            return False
        if method:
            return name.startswith("to") and re.search(r"(?<![\w$.])expect\s*\(", code[max(0, opened - 400):opened]) is not None
        if name in INERT_CALLS:
            return True
        if name in functions and depth < 3:
            call = _argument_spans(code, opened)
            params, body_start, body_end = functions[name]
            if call is None:
                return False
            index = next((k for k, (s, e) in enumerate(call[0]) if s <= pos < e), None)
            if index is None or index >= len(params) or code[call[0][index][0]:call[0][index][1]].strip() != code[pos:pos + len(re.match(r"[\w$]+", code[pos:]).group(0))]:
                return False
            param = params[index]
            if (not re.fullmatch(r"[A-Za-z_$][\w$]*", param) or any(not re.fullmatch(r"[A-Za-z_$][\w$]*", p) for p in params)
                    or source is None or re.search(r"(?<![\w$.])arguments(?![\w$])", code[body_start:body_end])
                    or re.search(r"\$\{[^}]*(?<![\w$.])" + re.escape(param) + r"(?![\w$])", source[body_start:body_end])):
                return False
            body = code[body_start:body_end]
            uses = [m.start() + body_start for m in re.finditer(r"(?<![\w$.])" + re.escape(param) + r"(?![\w$])", body)
                    if not (re.match(r"\s*:(?!:)", body[m.end():]) and re.search(r"[{,]\s*$", body[:m.start()]))]
            return all(_inert_use(code, u, functions, depth + 1, source) for u in uses)
        return False

def value_from_nowhere(rel: str, text: str, batch: dict | None) -> tuple[list[str], list[str]]:
    if not rel.startswith("backend/test-e2e/"):
        return [], []
    source = strip_comments(text)
    code = _blank_in_place(source)
    functions = _local_functions(code)
    whole = str((batch or {}).get("requirement_text") or "") if isinstance(batch, dict) else ""
    stated = (_stated_anywhere(whole) if whole.strip() else "") + " \n " + _fold(str((batch or {}).get("ancestor_context") or "")) \
        + " \n " + _fold(str((batch or {}).get("seed_sentences") or ""))
    lines = source.splitlines()
    problems: list[str] = []
    hints: list[str] = []
    literals = _all_literals(source, code)
    skipped = []
    for test_call in TEST_CALL.finditer(code):
        if test_call.group(1) in ("skip", "fixme"):
            call = _argument_spans(code, test_call.end() - 1)
            if call:
                skipped.append((test_call.start(), call[1]))
    for match in re.finditer(r"(?<![\w$.])(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=(?![=>])\s*", code):
        name = match.group(1)
        end = match.end()
        stop = end
        depth = 0
        while stop < len(code):
            char = code[stop]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif char in ";\n" and depth == 0:
                break
            stop += 1
        init = source[end:stop].strip()
        literal = _js_literal(init)
        unique = literal is None and bool(SEED_UNIQUE.search(init)) and UNIQUE_INIT.fullmatch(init) is not None
        if literal is None and not unique:
            continue
        if literal is not None:
            folded = _fold(literal).strip(" :.,;")
            if not folded or re.search(r"\d", folded):
                continue
            others = [v for s, _e, v in literals if not end <= s < stop]
            if whole.strip() == "" or _named_somewhere(folded, stated, others):
                continue
        region = _binding_region(code, match.start(), stop, name)

        uses = [stop + m.start() for m in re.finditer(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", region)
                if not (re.match(r"\s*:(?!:)", region[m.end():]) and re.search(r"[{,]\s*$", region[:m.start()]))]

        if re.search(r"\$\{[^}]*(?<![\w$.])" + re.escape(name) + r"(?![\w$])", source[stop:stop + len(region)]):
            continue
        asserted = []
        for use in uses:
            opened = _enclosing_open(code, use)
            if opened is None or code[opened] not in "({":
                continue
            call_open = opened if code[opened] == "(" else _enclosing_open(code, opened)
            if call_open is None or code[call_open] != "(":
                continue
            head = ASSERT_SEEN.search(code, max(0, call_open - 30), call_open + 1)
            if head and head.end() - 1 == call_open:
                asserted.append(use)
        asserted = [use for use in asserted if not any(s <= use < e for s, e in skipped)]
        if not asserted or not all(_inert_use(code, use, functions, 0, source) for use in uses):
            continue

        if any(re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", code[b0:b1]) or re.search(
                    r"\$\{[^}]*(?<![\w$.])" + re.escape(name) + r"(?![\w$])", source[b0:b1])
                for params, b0, b1 in functions.values()
                if not (match.start() >= b0 and match.start() < b1) and not (b0 >= stop and b1 <= stop + len(region))
                and name not in params
                and not re.search(r"(?<![\w$.])(?:const|let|var)\s+" + re.escape(name) + r"(?![\w$])", code[b0:b1])):
            continue
        line = source.count("\n", 0, asserted[0]) + 1
        shown = lines[line - 1].strip() if line <= len(lines) else name
        if literal is not None:

            hints.append(f"{rel}:{line}: `{shown[:120]}` expects `{name}` (\"{literal}\") shown; no requirement or seed "
                         "sentence states it and the test never types it -- only a record the application invents can "
                         "show it. Type it in the step that creates the record, or use the name the requirement gives.")
            continue
        problems.append(
            f"{rel}:{line}: `{shown[:160]}` expects `{name}` shown -- a name generated in this file "
            f"(`{' '.join(init.split())[:80]}`) -- but the file never types it, sends it or builds anything from it: every "
            f"use of `{name}` only looks for it (a helper it is passed to never types it either). Nothing can have put "
            "that text on the page, so the check fails a correct application. Type the value into its field (fill) in the "
            "step that creates the record, before any check expects it.")
    return list(dict.fromkeys(problems)), list(dict.fromkeys(hints))

CROSSDEP_SETTING = "v162b_crossdep"
CROSSDEP_TYPING_HELPERS = {"fill": (2,), "typeText": (1,), "putClipboard": (1,), "upload": (2, 3)}
CROSSDEP_TYPING_METHODS = {"fill", "type", "pressSequentially", "insertText"}
CROSSDEP_SENDING_METHODS = {"post", "put", "patch"}
CROSSDEP_IMPORTS = re.compile(r"""(?<![\w$.])require\s*\(\s*["'`]([^"'`]+)["'`]\s*\)|^\s*import\b[^;\n]*?from\s*["']([^"']+)["']""", re.M)
CROSSDEP_COVERS = re.compile(r"\bcovers?\s*:?\s*((?:S\s*\d{1,2}\s*(?:,|&|\band\b|\s)\s*)*S\s*\d{1,2})\b", re.IGNORECASE)

def _crossdep_on() -> bool:
    try:
        from .settings import setting_on

        return setting_on(CROSSDEP_SETTING, True)
    except Exception:
        return True

def _crossdep_scenarios(raw: str, test_start: int, title: str,
                        leaves: list[tuple[str, str, list[tuple[str, str]]]]) -> tuple[str | None, list[str]] | None:
    named = [leaf for leaf in leaves if re.search(r"(?<![\w-])" + re.escape(leaf[0]) + r"(?![\w-])", title or "")]
    candidates = named if len(named) == 1 else ([] if named else list(leaves))
    if not candidates:
        candidates = list(leaves)
    if not candidates:
        return None
    ids = [leaf[0] for leaf in candidates]
    own = _own_scenario(title, leaves)
    if own is not None:
        return own, ids
    if len(candidates) == 1:
        scenarios = candidates[0][2]
        rest = _fold(re.sub(r"^\s*REQ[-\w.]*\s*[:\-–]\s*", "", title or "", flags=re.IGNORECASE)).strip(" :.,;")
        names = [(_fold(re.sub(r"^\s*(?:REQ[-\w.]*\s*[:\-–]\s*)?(?:scenario\s*#?\s*\d{1,2}\s*[:.\-–]?\s*)?", "", name,
                               flags=re.IGNORECASE)).strip(" :.,;"), text)
                 for name, text in scenarios]
        by_name = [text for name, text in names if name and name == rest]
        if len(by_name) == 1:
            return by_name[0], ids

        head = raw[:test_start].rstrip()
        comments = []
        for line in reversed(head.splitlines()):
            if line.strip().startswith("//"):
                comments.append(line)
                continue
            break
        covers = CROSSDEP_COVERS.search(" ".join(comments))
        if covers:
            numbers = [int(n) for n in re.findall(r"\d{1,2}", covers.group(1))]
            if numbers and all(1 <= n <= len(scenarios) for n in numbers):
                return "\n".join(scenarios[n - 1][1] for n in dict.fromkeys(numbers)), ids
    return None, ids

CROSSDEP_INLINE_WHEN = re.compile(r"(?<![\w-])(?:WHEN|When)\b")

def _crossdep_givens(leaf: dict) -> str:
    found: list[str] = []
    for scenario in leaf.get("scenarios") or []:
        if not isinstance(scenario, dict):
            continue
        steps = [step for step in scenario.get("steps") or [] if isinstance(step, dict)]
        keyed = any(str(step.get("keyword") or "").strip() for step in steps)
        if not keyed:
            text = " ".join(str(step.get("content") or "") for step in steps)
            cut = CROSSDEP_INLINE_WHEN.search(text)
            found.append(text[:cut.start()] if cut else text)
            continue
        phase = "GIVEN"
        for step in steps:
            keyword = str(step.get("keyword") or "").strip().upper()
            if keyword in ("GIVEN", "WHEN", "THEN"):
                phase = keyword
            if phase == "GIVEN":
                found.append(str(step.get("content") or ""))
    return "\n".join(found)

def _crossdep_strings(text: str, code: str, start: int, end: int, functions: dict[str, tuple[list[str], int, int]],
                      skip: list[tuple[int, int]], seen: set[str] | None = None, depth: int = 0) -> list[str] | None:
    seen = set() if seen is None else seen
    found = [value for s, _e, value in _all_literals(text, code) if start <= s < end and not any(a <= s < b for a, b in skip)]
    for match in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code[start:end]):
        name = match.group(1)
        if name not in functions or name in seen:
            continue
        if depth >= 3:
            return None
        _params, body_start, body_end = functions[name]
        if body_start <= start + match.start() < body_end:
            continue
        inner = _crossdep_strings(text, code, body_start, body_end, functions, [], seen | {name}, depth + 1)
        if inner is None:
            return None
        found.extend(inner)
    return found

def _crossdep_typed(text: str, code: str, start: int, end: int, functions: dict[str, tuple[list[str], int, int]],
                    seen: set[str] | None = None, depth: int = 0) -> list[str]:
    seen = set() if seen is None else seen
    typed: list[str] = []

    def literals_in(span: tuple[int, int]) -> list[str]:
        return [value for s, _e, value in _all_literals(text, code) if span[0] <= s < span[1]]

    for match in re.finditer(r"(?<![\w$])(\.\s*)?([A-Za-z_$][\w$]*)\s*\(", code[start:end]):
        open_at = start + match.end() - 1
        name, method = match.group(2), bool(match.group(1))
        if method and name in CROSSDEP_TYPING_METHODS | CROSSDEP_SENDING_METHODS:
            call = _argument_spans(code, open_at)
            if call and call[0]:
                spans = call[0] if name in CROSSDEP_SENDING_METHODS else [call[0][-1]]
                for span in spans:
                    typed.extend(literals_in(span))
        elif not method and name in CROSSDEP_TYPING_HELPERS:
            call = _argument_spans(code, open_at)
            if call:
                for index in CROSSDEP_TYPING_HELPERS[name]:
                    if index < len(call[0]):
                        typed.extend(literals_in(call[0][index]))
        elif not method and name in functions and name not in seen and depth < 3:
            _params, body_start, body_end = functions[name]
            if not body_start <= open_at < body_end:
                typed.extend(_crossdep_typed(text, code, body_start, body_end, functions, seen | {name}, depth + 1))
    return typed

def _crossdep_seen(text: str, code: str, start: int, end: int) -> list[tuple[int, tuple[int, int], str]]:
    found = []
    for match in ASSERT_TEXT_CALL.finditer(code, start, end):
        call = _argument_spans(code, match.end() - 1)
        if call and len(call[0]) >= 2 and code[call[0][0][0]:call[0][0][1]].strip() == "page":
            found.extend((match.start(), call[0][1], v) for v in _asserted_values(text[call[0][1][0]:call[0][1][1]]))
    for match in ASSERT_MATCHER.finditer(code, start, end):
        call = _argument_spans(code, match.end() - 1)
        if call and call[0] and re.search(r"(?<![\w$.])expect\s*\(", code[max(start, match.start() - 400):match.start()]):
            found.extend((match.start(), call[0][0], v) for v in _asserted_values(text[call[0][0][0]:call[0][0][1]]))
    for match in re.finditer(r"(?<![\w$.])expect\s*\(\s*(?:locate|item)\s*\(", code[start:end]):
        inner_open = start + match.end() - 1
        call = _argument_spans(code, inner_open)
        if not call or len(call[0]) != 2 or code[call[0][0][0]:call[0][0][1]].strip() != "page":
            continue
        if not re.match(r"\s*\)\s*\.\s*toBeVisible\s*\(", code[call[1] + 1:call[1] + 40]):
            continue
        found.extend((start + match.start(), call[0][1], v) for v in _asserted_values(text[call[0][1][0]:call[0][1][1]]))
    return found

def cross_test_dependencies(rel: str, text: str, batch: dict | None) -> list[str]:
    if not rel.startswith("backend/test-e2e/") or not isinstance(batch, dict) or not _crossdep_on():
        return []
    leaves, background, _elsewhere_text = _batch_requirement(batch)
    if not leaves:
        return []
    source = strip_comments(text)
    if re.search(r"\bserial\b|\.only\s*\(", source):
        return []
    for found in CROSSDEP_IMPORTS.finditer(source):
        module = found.group(1) or found.group(2) or ""
        if module not in ("@playwright/test",) and not module.rstrip("/").endswith("e2e-support/outsider"):
            return []
    code = _blank_in_place(source)
    functions = _local_functions(code)
    spans = _test_spans(source, code)
    if len(spans) < 2:
        return []
    parsed = _requirement(str(batch.get("requirement_text") or ""))
    folders = _fold("\n".join(t for node_id, t in parsed.texts if node_id not in parsed.leaves)) if parsed else ""
    general = _fold("\n".join(leaf[1] for leaf in leaves) + "\n" + background + "\n"
                    + str(batch.get("seed_sentences") or "") + "\n" + str(batch.get("ancestor_context") or "")) \
        + " \n " + folders

    hooks: list[tuple[int, int]] = []
    for match in re.finditer(r"(?<![\w$])test\s*\.\s*(?:before|after)(?:Each|All)\s*\(", code):
        call = _argument_spans(code, match.end() - 1)
        if call:
            hooks.append((match.end(), call[1]))
    hook_strings: list[str] = []
    for hook_start, hook_end in hooks:
        inner = _crossdep_strings(source, code, hook_start, hook_end, functions, [])
        if inner is None:
            return []
        hook_strings.extend(inner)

    covered = [(s, e) for s, e, _t in spans] + hooks + [(b0, b1) for _p, b0, b1 in functions.values()]
    loose = [value for s, _e, value in _all_literals(source, code) if not any(a <= s < b for a, b in covered)]
    tests = []
    for body_start, body_end, title in spans:
        test_start = code.rfind("test", 0, body_start)
        seen = _crossdep_seen(source, code, body_start, body_end)
        skip = [span for _p, span, _v in seen]
        own = _crossdep_strings(source, code, body_start, body_end, functions, skip)
        typed = _crossdep_typed(source, code, body_start, body_end, functions)
        tests.append((body_start, body_end, title, test_start, seen, own, typed))
    lines = source.splitlines()
    problems: list[str] = []
    for body_start, _body_end, title, test_start, seen, own, _typed in tests:
        if own is None:
            continue
        scenario = _crossdep_scenarios(text, test_start, title, leaves)
        if scenario is None:
            continue

        givens = "\n".join(_crossdep_givens(parsed.leaves[i]) for i in scenario[1] if parsed and i in parsed.leaves)
        stated = _fold((scenario[0] or "") + "\n" + givens) + " \n " + general
        mine = own + hook_strings
        reported: set[str] = set()
        for position, _span, value in seen:
            folded = _fold(value).strip(" :.,;")

            if not folded or folded in reported or not re.search(r"[^\W\d_]", folded):
                continue
            if _occurs(folded, stated) or any(_occurs(folded, s) for s in loose):
                continue
            if any(_occurs(folded, s) or (len(s.strip(" :.,;")) >= 3 and re.search(r"\w", s)
                                          and _occurs(s.strip(" :.,;"), folded)) for s in mine):
                continue
            others = [t[2] for t in tests if t[0] != body_start and any(_occurs(folded, typed) for typed in t[6])]
            if not others:
                continue
            reported.add(folded)
            line = source.count("\n", 0, position) + 1
            shown = lines[line - 1].strip() if line <= len(lines) else value
            who = " and ".join(f'"{o}"' for o in dict.fromkeys(others))
            problems.append(
                f'{rel}:{line}: `{shown[:160]}` (test "{title}") expects "{value}" shown, which only test {who} of this file '
                f"enters -- this test never types or creates it, and neither its scenario nor any seed / pre-provision "
                "statement gives it. Each test runs ALONE on a fresh server and fresh data, so this check fails a correct "
                "application (and bending the application or its seed to it breaks the stated data). Start from the "
                "scenario's own GIVEN state (the record names it states), or create / type the value inside this test "
                "before checking it.")
    return list(dict.fromkeys(problems))

def cross_test_dependency_defects(project_dir: Path, test_files: list[str], batch: dict | None,
                                  red_titles: list[str] | None = None) -> str:
    found: list[str] = []
    wanted = [str(t).split("›")[-1].strip() for t in red_titles] if red_titles is not None else None
    for rel in test_files:
        path = Path(project_dir) / rel
        if not path.is_file():
            continue
        try:
            for problem in cross_test_dependencies(rel, path.read_text(encoding="utf-8", errors="replace"), batch):
                if wanted is None or any(f'(test "{title}")' in problem for title in wanted if title):
                    found.append(problem)
        except Exception:
            continue
    return "\n\n".join(dict.fromkeys(found))

def static_test_defects(project_dir: Path, test_files: list[str], batch: dict | None = None) -> str:
    import shutil
    import tempfile

    problems: list[str] = []
    hints: list[str] = []
    for rel in test_files:
        source = project_dir / rel
        if not source.is_file():
            continue
        problems.extend(framework_api_misuse(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(outsider_undefined_calls(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(rebuilt_unique_names(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(blank_lookups(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(spec_api_misuse(rel, source.read_text(encoding="utf-8", errors="replace"), batch))
        try:
            problems.extend(press_text_misuse(rel, source.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            pass
        try:
            problems.extend(unknown_roles(rel, source.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            pass
        try:
            problems.extend(unsupported_assertions(rel, source.read_text(encoding="utf-8", errors="replace"), batch))
        except Exception:
            pass
        try:
            problems.extend(seed_renames(rel, source.read_text(encoding="utf-8", errors="replace"), batch))
        except Exception:
            pass
        try:
            problems.extend(vacuous_absence_checks(rel, source.read_text(encoding="utf-8", errors="replace"), batch))
            problems.extend(whole_page_text_checks(rel, source.read_text(encoding="utf-8", errors="replace"))[0])
            hints.extend(v144l_hints(rel, source.read_text(encoding="utf-8", errors="replace"), batch))
        except Exception:
            pass
        try:
            found, advice = single_element_count_findings(rel, source.read_text(encoding="utf-8", errors="replace"))
            problems.extend(found)
            hints.extend(advice)
        except Exception:
            pass
        for check in (seeded_value_absence, value_from_nowhere):
            try:
                found, advice = check(rel, source.read_text(encoding="utf-8", errors="replace"), batch)
                problems.extend(found)
                hints.extend(advice)
            except Exception:
                pass
        try:
            problems.extend(cross_test_dependencies(rel, source.read_text(encoding="utf-8", errors="replace"), batch))
        except Exception:
            pass
        problems.extend(repeated_sign_ins(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(tags_as_names(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(cells_as_items(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(unbound_page(rel, source.read_text(encoding="utf-8", errors="replace")))
        problems.extend(helper_arity_defects(rel, source.read_text(encoding="utf-8", errors="replace")))
        if source.suffix != ".js":
            continue
        suffix = ".cjs" if rel.startswith("backend/test-e2e/") else ".mjs"
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / (source.stem + suffix)
            shutil.copyfile(source, copy)
            code, output = run_cmd(["node", "--check", str(copy)], cwd=project_dir, timeout=60)
        if code != 0:
            lines = [l for l in output.splitlines() if l.strip()][:8]
            problems.append(f"{rel}: does not parse:\n" + "\n".join(lines).replace(str(copy), rel))
        text = source.read_text(encoding="utf-8", errors="replace")
        if rel.startswith("backend/tests/") and re.search(r"require\(\s*['\"]vitest['\"]\s*\)", text):
            problems.append(f"{rel}: uses require(\"vitest\"); Vitest must be imported with ESM `import`.")
        if re.search(r"(localhost|127\.0\.0\.1):\d{4}", text):
            problems.append(f"{rel}: hardcodes a host:port; use relative URLs (page.goto(\"/\"), request.post(\"/api/...\")) so the configured baseURL applies.")
    if problems and hints:
        problems.append(V144L_HINT_HEAD + "\n" + "\n".join("- " + h for h in dict.fromkeys(hints)))
    return "\n\n".join(problems)

def _undeclared_backend_modules(project_dir: Path) -> list[str]:
    import json

    try:
        package = json.loads((project_dir / "backend" / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    declared = set(package.get("dependencies") or {})
    builtins = {
        "assert", "buffer", "child_process", "cluster", "crypto", "dns", "events", "fs", "http", "http2", "https", "net",
        "os", "path", "perf_hooks", "process", "querystring", "readline", "stream", "string_decoder", "timers", "tls",
        "url", "util", "v8", "vm", "worker_threads", "zlib", "test", "module", "async_hooks", "diagnostics_channel",
        "inspector", "punycode", "repl", "sys", "trace_events", "tty", "dgram", "domain", "constants", "console", "wasi", "sqlite",
    }
    problems: list[str] = []
    source = project_dir / "backend" / "src"
    for path in sorted(source.rglob("*.js")) if source.is_dir() else []:
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        rel = path.relative_to(project_dir).as_posix()
        for name in set(re.findall(r"require\(\s*['\"]([^'\"./][^'\"]*)['\"]\s*\)", text)):
            base = name[5:] if name.startswith("node:") else name
            package_name = "/".join(base.split("/")[:2]) if base.startswith("@") else base.split("/")[0]
            if package_name in builtins or package_name in declared:
                continue
            problems.append(
                f"{rel}: requires `{name}`, which is not under \"dependencies\" of backend/package.json (devDependencies are not "
                "installed in production). Use a Node built-in instead (password hashing: crypto.scryptSync) or a declared dependency."
            )
    return problems

NATIVE_VALIDATION = re.compile(r"browser'?s? (?:own|native|built-in) validation|native (?:form )?validation|HTML5 validation|"
                               r"浏览器(?:自带|原生)的?校验|原生校验", re.IGNORECASE)

CODE_TOKEN = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|/\*.*?\*/|//[^\n]*", re.S)

def strip_comments(text: str) -> str:
    return CODE_TOKEN.sub(lambda m: m.group(0) if m.group(0)[0] in "\"'`" else re.sub(r"[^\n]", " ", m.group(0)), text)

LINT_HEADER = "APPLICATION LINT FAILED (fix these too, in the same answer):\n"
E2E_GREEN_HEAD = "E2E PASSED\n"

def lint_only_failure(output: str) -> str | None:
    if not output.startswith(LINT_HEADER):
        return None
    findings, sep, rest = output[len(LINT_HEADER):].partition("\n\n")
    if not sep or not rest.startswith(E2E_GREEN_HEAD):
        return None
    return findings

def lint_application(project_dir: Path, native_validation: bool = False) -> str:
    problems: list[str] = []
    backend = project_dir / "backend" / "src"
    if backend.is_dir():
        for path in backend.rglob("*.js"):
            text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            rel = path.relative_to(project_dir).as_posix()
            if re.search(r"\.(get|use|all|post)\(\s*['\"]\*['\"]", text):
                problems.append(f"{rel}: Express 5 rejects the bare '*' route (path-to-regexp 8). Use a RegExp or '/*splat' like the template's SPA fallback.")
            if re.search(r"\.listen\(\s*\d{2,5}\b", text):
                problems.append(f"{rel}: listens on a literal port. Only backend/src/index.js may listen, on Number(process.env.PORT || 3000).")
    problems.extend(_undeclared_backend_modules(project_dir))
    frontend = project_dir / "frontend" / "src"
    if frontend.is_dir():
        for path in frontend.rglob("*.tsx"):
            text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            rel = path.relative_to(project_dir).as_posix()
            for match in re.finditer(r"<(?:input|textarea)\b", text):
                rest = text[match.start() : match.start() + 1200]
                end = re.search(r"(?<!=)/?>", rest)
                tag = rest[: end.end()] if end else rest
                if re.search(r"\breadOnly\b(?!=\{false\})", tag) and re.search(r"\bon(?:Focus|Click)=", tag):

                    line = text.count("\n", 0, match.start()) + 1
                    problems.append(
                        f"{rel}:{line}: a read-only field is used as a trigger (readOnly + onFocus/onClick). Users and assistive "
                        "technology see an editable field that refuses input. Make the composer expand in place (the clicked "
                        "field becomes editable), or use a <button> with its own name as the trigger."
                    )
            for match in re.finditer(r"<form\b", text):

                rest = text[match.start() : match.start() + 1500]
                end = re.search(r"(?<!=)>", rest)
                tag = rest[: end.end()] if end else rest
                if "noValidate" not in tag and "{..." not in tag and not native_validation:

                    line = text.count("\n", 0, match.start()) + 1
                    problems.append(
                        f"{rel}:{line}: <form> without `noValidate`. Browser-native validation blocks the submit handler, so the "
                        "application's own error messages never show. Add noValidate and validate in the submit handler."
                    )
    problems.extend(undefined_names(project_dir))
    problems.extend(hook_order_errors(project_dir))
    return "\n".join(problems)

UNDEFINED_NAMES_SCRIPT = r"""
const path = require('path');
const fs = require('fs');
const { createRequire } = require('module');
const dir = path.resolve(process.argv[2] || '.');
const from = path.resolve(process.argv[3] || dir);
const out = (value) => process.stdout.write(JSON.stringify(value) + '\n');
let parser, traverse, globalsPkg;
try {
  const req = createRequire(path.join(from, 'package.json'));
  parser = req('@babel/parser');
  traverse = req('@babel/traverse');
  traverse = traverse.default || traverse;
  globalsPkg = req('globals');
} catch (err) {
  out({ skipped: 'modules: ' + String(err && err.message).split('\n')[0] });
  process.exit(0);
}
const known = new Set(Object.getOwnPropertyNames(globalThis));
for (const group of ['browser', 'builtin', 'es2021', 'es2025', 'worker', 'serviceworker']) {
  for (const name of Object.keys(globalsPkg[group] || {})) known.add(name);
}
const SOURCE = /\.(?:[cm]?[jt]sx?)$/;
const TEST = /(?:^|\/)(?:tests?|__tests__|__mocks__|e2e|test-e2e)\/|\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|\/)setupTests\.[jt]sx?$/;
const files = [];
const declared = new Set();
(function walk(folder) {
  let entries = [];
  try { entries = fs.readdirSync(folder, { withFileTypes: true }); } catch (err) { return; }
  for (const entry of entries) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules' && !entry.name.startsWith('.')) walk(full);
    } else if (SOURCE.test(entry.name)) {
      const rel = path.relative(dir, full).split(path.sep).join('/');
      if (/\.d\.[cm]?ts$/.test(entry.name)) {
        const text = fs.readFileSync(full, 'utf8');
        for (const m of text.matchAll(/\bdeclare\s+(?:const|let|var|function|class|enum|namespace|module)\s+([A-Za-z_$][\w$]*)/g)) declared.add(m[1]);
      } else if (!TEST.test(rel)) {
        files.push([rel, full]);
      }
    }
  }
})(path.join(dir, 'src'));
const VALUE_TS = new Set(['TSAsExpression', 'TSSatisfiesExpression', 'TSNonNullExpression', 'TSTypeAssertion',
  'TSInstantiationExpression', 'TSEnumDeclaration', 'TSEnumMember', 'TSEnumBody', 'TSParameterProperty', 'TSExportAssignment']);
function inTypeOnly(p) {
  for (let q = p.parentPath; q; q = q.parentPath) {
    const type = q.node.type;
    if (type.startsWith('TS') && !VALUE_TS.has(type)) return true;
    if (q.node.declare) return true;
  }
  return false;
}
const findings = [];
const unparsed = [];
for (const [rel, full] of files) {
  const code = fs.readFileSync(full, 'utf8');
  const plugins = /\.[cm]?ts$/.test(rel) ? ['typescript'] : /\.tsx$/.test(rel) ? ['typescript', 'jsx'] : ['jsx'];
  let ast;
  try {
    ast = parser.parse(code, { sourceType: 'module', plugins, errorRecovery: false, allowReturnOutsideFunction: true });
  } catch (err) {
    unparsed.push(rel);
    continue;
  }
  const local = new Set();
  traverse(ast, {
    'TSDeclareFunction|TSEnumDeclaration|TSModuleDeclaration|TSImportEqualsDeclaration|ClassDeclaration|FunctionDeclaration'(p) {
      if (p.node.id && p.node.id.name) local.add(p.node.id.name);
    },
  });
  const seen = new Set();
  traverse(ast, {
    ReferencedIdentifier(p) {
      const name = p.node.name;
      if (!name || seen.has(name)) return;
      if (p.scope.hasBinding(name) || known.has(name) || declared.has(name) || local.has(name)) return;
      if (/^__[A-Z0-9_]+__$/.test(name)) return;
      if (p.parentPath.isUnaryExpression({ operator: 'typeof' })) return;
      if (inTypeOnly(p)) return;
      const loc = p.node.loc && p.node.loc.start;
      seen.add(name);
      findings.push({ file: rel, line: loc ? loc.line : 0, name, jsx: p.isJSXIdentifier() });
    },
  });
}
out({ files: files.length, unparsed, findings });
"""
UNDEFINED_NAMES_TIMEOUT_SEC = 120
_undefined_names_cache: dict[str, list[str]] = {}

def undefined_names(project_dir: Path, modules_from: Path | None = None) -> list[str]:
    import hashlib
    import json
    import tempfile

    frontend = project_dir / "frontend"
    base = Path(modules_from) if modules_from is not None else frontend
    source = frontend / "src"
    if not source.is_dir() or not (base / "node_modules" / "@babel" / "traverse").is_dir():
        return []
    digest = hashlib.sha1(str(base.resolve()).encode("utf-8"))
    for path in sorted(p for p in source.rglob("*") if p.is_file() and re.search(r"\.[cm]?[jt]sx?$", p.name)
                       and "node_modules" not in p.parts):
        digest.update(path.relative_to(source).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    key = digest.hexdigest()
    if key in _undefined_names_cache:
        return list(_undefined_names_cache[key])
    lines: list[str] = []
    try:
        script = Path(tempfile.gettempdir()) / ("arc-undefined-names-" + hashlib.sha1(UNDEFINED_NAMES_SCRIPT.encode("utf-8")).hexdigest()[:12] + ".cjs")
        if not script.is_file():
            script.write_text(UNDEFINED_NAMES_SCRIPT, encoding="utf-8")
        code, output = run_cmd(["node", str(script), str(frontend.resolve()), str(base.resolve())], cwd=frontend,
                               timeout=UNDEFINED_NAMES_TIMEOUT_SEC)
        report = json.loads(output.strip().splitlines()[-1]) if code == 0 and output.strip() else {}
        for item in (report.get("findings") or []) if isinstance(report, dict) else []:
            name, rel, line = str(item.get("name")), "frontend/" + str(item.get("file")), int(item.get("line") or 0)
            role = f"<{name}>" if item.get("jsx") else f"`{name}`"
            lines.append(f"{rel}:{line}: {role} is used here but never imported or defined in this file, and it is no browser "
                         f"global. The build passes anyway (vite does not check names), but when this code runs the browser "
                         f"throws \"ReferenceError: {name} is not defined\" and the page renders blank. Import {name} from the "
                         "module that exports it (or define it) -- do not remove the call.")
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        lines = []
    _undefined_names_cache[key] = list(lines)
    return lines

HOOK_ORDER_SCRIPT = r"""
const path = require('path');
const fs = require('fs');
const { createRequire } = require('module');
const dir = path.resolve(process.argv[2] || '.');
const from = path.resolve(process.argv[3] || dir);
const out = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const RULE = 'react-hooks/rules-of-hooks';
let Linter, plugin, babel;
try {
  const req = createRequire(path.join(from, 'package.json'));
  ({ Linter } = req('eslint'));
  plugin = req('eslint-plugin-react-hooks');
  plugin = plugin.default || plugin;
  babel = createRequire(req.resolve('eslint-plugin-react-hooks'))('@babel/parser');
  if (!plugin.rules || !plugin.rules['rules-of-hooks']) throw new Error('the plugin has no rules-of-hooks rule');
} catch (err) {
  out({ skipped: 'modules: ' + String(err && err.message).split('\n')[0] });
  process.exit(0);
}
// ESTree from @babel/parser; eslint walks node types it does not know (the TS ones) by their own keys, so a hook call
// inside `x as T` or `x!` is still seen
function parseForESLint(code, options) {
  const file = String((options && options.filePath) || '');
  const plugins = ['estree', /\.[cm]?ts$/.test(file) ? null : 'jsx', /\.[cm]?tsx?$/.test(file) ? 'typescript' : null].filter(Boolean);
  const ast = babel.parse(code, { sourceType: 'module', plugins, ranges: true, tokens: false, errorRecovery: false,
    allowReturnOutsideFunction: true, allowImportExportEverywhere: true });
  const program = ast.program;
  program.comments = (ast.comments || []).map((c) => ({ type: c.type === 'CommentLine' ? 'Line' : 'Block', value: c.value,
    range: c.range || [c.start, c.end], loc: c.loc }));
  program.tokens = [];
  program.range = [0, code.length];
  return { ast: program };
}
const SOURCE = /\.(?:[cm]?[jt]sx?)$/;
const TEST = /(?:^|\/)(?:tests?|__tests__|__mocks__|e2e|test-e2e)\/|\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|\/)setupTests\.[jt]sx?$/;
const files = [];
(function walk(folder) {
  let entries = [];
  try { entries = fs.readdirSync(folder, { withFileTypes: true }); } catch (err) { return; }
  for (const entry of entries) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules' && !entry.name.startsWith('.')) walk(full);
    } else if (SOURCE.test(entry.name) && !/\.d\.[cm]?ts$/.test(entry.name)) {
      const rel = path.relative(dir, full).split(path.sep).join('/');
      if (!TEST.test(rel)) files.push([rel, full]);
    }
  }
})(path.join(dir, 'src'));
const linter = new Linter({ configType: 'flat', cwd: dir });
// a `files` pattern that ends in `*` alone matches no file in flat config: the extensions are spelled out
const config = [{
  files: ['**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}'],
  plugins: { 'react-hooks': plugin },
  languageOptions: { ecmaVersion: 'latest', sourceType: 'module', parser: { meta: { name: 'arc-babel-ts' }, parseForESLint } },
  linterOptions: { reportUnusedDisableDirectives: 'off' },
  rules: { [RULE]: 'error' },
}];
const findings = [];
const unparsed = [];
for (const [rel, full] of files) {
  let messages = [];
  try {
    messages = linter.verify(fs.readFileSync(full, 'utf8'), config, { filename: full });
  } catch (err) {
    unparsed.push(rel);
    continue;
  }
  for (const m of messages) {
    if (m.fatal) unparsed.push(rel);
    else if (m.ruleId === RULE && m.severity === 2) findings.push({ file: rel, line: m.line || 0, message: m.message });
  }
}
out({ files: files.length, unparsed, findings });
"""
HOOK_ORDER_TIMEOUT_SEC = 30
_hook_order_notes: set[str] = set()

def _hook_order_note(note: str) -> None:
    if note not in _hook_order_notes:
        _hook_order_notes.add(note)
        from .fsutil import log

        log(f"[lint] rules-of-hooks check skipped: {note}")

def hook_order_errors(project_dir: Path, modules_from: Path | None = None) -> list[str]:
    import hashlib
    import json
    import tempfile

    frontend = project_dir / "frontend"
    base = Path(modules_from) if modules_from is not None else frontend
    if not (frontend / "src").is_dir():
        return []
    if not (base / "node_modules" / "eslint").is_dir() or not (base / "node_modules" / "eslint-plugin-react-hooks").is_dir():
        _hook_order_note("eslint or eslint-plugin-react-hooks is not installed in frontend/node_modules")
        return []
    lines: list[str] = []
    try:
        script = Path(tempfile.gettempdir()) / ("arc-hook-order-" + hashlib.sha1(HOOK_ORDER_SCRIPT.encode("utf-8")).hexdigest()[:12] + ".cjs")
        if not script.is_file():
            script.write_text(HOOK_ORDER_SCRIPT, encoding="utf-8")
        code, output = run_cmd(["node", str(script), str(frontend.resolve()), str(base.resolve())], cwd=frontend,
                               timeout=HOOK_ORDER_TIMEOUT_SEC)
        report = json.loads(output.strip().splitlines()[-1]) if code == 0 and output.strip() else None
        if not isinstance(report, dict) or "findings" not in report:
            reason = report.get("skipped") if isinstance(report, dict) else None
            _hook_order_note(str(reason or f"exit {code}: " + (output.strip().splitlines() or [""])[-1][:160]))
            return []
        for item in report.get("findings") or []:
            rel, line, message = "frontend/" + str(item.get("file")), int(item.get("line") or 0), str(item.get("message")).strip()

            lines.append(f"{rel}:{line}: {message} [react-hooks/rules-of-hooks, a possible risk: if this is a real React "
                         "Hook (it calls useState / useEffect / another Hook), React may throw (e.g. \"Minified React error "
                         "#310\") when the number or order of Hook calls changes between renders, and the page may go blank; "
                         "such a Hook belongs at the top of the component, before any early return or condition. A plain "
                         "function whose name merely starts with \"use\" is no Hook: it needs no change for this finding.]")
    except (OSError, ValueError, TypeError, AttributeError, IndexError) as error:
        _hook_order_note(f"{type(error).__name__}: {str(error)[:160]}")
        return []
    return lines

def parse_defects_in_output(output: str, test_files: list[str]) -> str:
    problems: list[str] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        match = re.search(r"FAIL\s+(tests/\S+)\s+\[", line)
        if not match:
            continue
        window = "\n".join(lines[index : index + 8])
        if PARSE_ERROR.search(window):
            problems.append(window)
    return "\n\n".join(problems)

def looks_like_test_defect(output: str) -> bool:
    markers = (
        "SyntaxError",
        "Failed to resolve import",
        "Cannot find module",
        "Unexpected token",
        "No test suite found",
        "No test files found",
        "Error: Failed to parse",
    )
    return any(marker in output for marker in markers)

FAILURE_PAGE = re.compile(r"^[ \t]*url: (\S+)[ \t]*\r?\n[ \t]*on the page now -> clickable: ([^\n]*)\r?\n[ \t]*fields: ([^\n]*)"
                          r"\r?\n[ \t]*headings: ([^\n]*)\r?\n[ \t]*messages: ([^\n]*)", re.M)
FAILURE_URL = re.compile(r"^[ \t]*url: (\S+)", re.M)

ROUTER_UNREADABLE = re.compile(r"\buseRoutes\s*\(|\bcreate(?:Browser|Hash|Memory)Router\s*\(|\bHashRouter\b|\bbasename\s*=")

ROUTE_NOTE_HEAD = ("WHERE THE RED CHECKS STOOD vs THE FRONTEND ROUTES (read from the router and from the output above -- "
                   "facts, not guesses):")

def frontend_routes(project_dir: Path) -> tuple[list[str], list[str]] | None:
    from .nav_edges import App

    src = Path(project_dir) / "frontend" / "src"
    if not src.is_dir():
        return None
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in src.rglob("*")
             if p.is_file() and p.suffix in (".tsx", ".ts", ".jsx", ".js") and "node_modules" not in p.parts]
    if any(ROUTER_UNREADABLE.search(t) for t in texts):
        return None
    written: list[str] = []
    for text in texts:
        for tag in re.finditer(r"<Route\b([^>]*)>", text):
            path = re.search(r"(?<![\w-])path\s*=\s*(\"[^\"]*\"|'[^']*'|\{[^}]*\})", tag.group(1))
            if path and path.group(1).startswith("{") and not re.fullmatch(r"\{\s*([\"'])[^\"'`$]*\1\s*\}", path.group(1)):
                return None
            if path:
                written.append(path.group(1).strip("{} \t\"'"))
    app = App(Path(project_dir))
    paths = list(dict.fromkeys(r["path"] for r in app.routes))
    if not paths:
        return None

    for literal in written:
        bare = literal.strip("/")
        if literal.startswith("/"):
            if not any(p.strip("/") == bare for p in paths):
                return None
        elif bare and not any(p.strip("/") == bare or p.strip("/").endswith("/" + bare) for p in paths):
            return None
    for path in paths:
        segments = [s for s in path.split("/") if s]
        if "*" in segments[:-1]:
            return None
        for segment in segments:
            if any(c in segment for c in "?()[]{}$+") or ("*" in segment and segment != "*") or ":" in segment[1:]:
                return None
    return paths, sorted({"frontend/src/" + r["file"] for r in app.routes})

def route_matches(route: str, path: str) -> bool:
    parts = [s for s in route.split("/") if s]
    segments = [s for s in path.split("/") if s]
    if parts and parts[-1] == "*":
        parts = parts[:-1]
        if len(segments) < len(parts):
            return False
        segments = segments[:len(parts)]
    elif len(parts) != len(segments):
        return False
    return all(a.startswith(":") or a.lower() == b.lower() for a, b in zip(parts, segments))

def unrouted_page_note(project_dir: Path, output: str, limit: int = 6) -> str:
    from urllib.parse import unquote, urlsplit

    try:
        if "url: " not in (output or ""):
            return ""
        empty_at: dict[str, bool] = {}
        for match in FAILURE_PAGE.finditer(output):
            empty = all(part.strip() == "[]" for part in match.groups()[1:])
            empty_at[match.group(1)] = empty_at.get(match.group(1), True) and empty
        routes: tuple[list[str], list[str]] | None = None
        lines: list[str] = []
        for url in dict.fromkeys(FAILURE_URL.findall(output)):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https"):
                continue
            path = unquote(parts.path) or "/"
            if path.rstrip("/").lower() == "/api" or path.lower().startswith("/api/"):
                continue
            if routes is None:
                routes = frontend_routes(project_dir) or ([], [])
            paths, files = routes
            if not paths:
                return ""
            matched = [r for r in paths if route_matches(r, path)]
            blank = empty_at.get(url, False)
            if not matched:
                shown = ", ".join(paths[:20]) + (f", ... ({len(paths)} in all)" if len(paths) > 20 else "")
                lines.append(f"- {path}: NO route of the frontend router matches this address ({', '.join(files)} declares: {shown})"
                             + ("; the page showed no link, button, field, heading or message at all" if blank else "")
                             + ". Nothing the test expects on this page can appear until a route for it exists and renders its page.")
            elif blank:
                lines.append(f"- {path} (route {matched[0]}): the page showed no link, button, field, heading or message at all "
                             "when the check failed.")
            if len(lines) >= limit:
                break
        return (ROUTE_NOTE_HEAD + "\n" + "\n".join(lines)) if lines else ""
    except Exception:
        return ""

ERROR_CONTEXT_HEAD = ("Page snapshot at the failure (Playwright error-context): the page's accessibility tree when the test "
                      "failed, trimmed to the part around what the failing step looked for (information, not a failure):")
ERROR_CONTEXT_TESTS = 2
ERROR_CONTEXT_TEST_CHARS = 1500
ERROR_CONTEXT_FILE_BYTES = 2_000_000
ERROR_CONTEXT_PER_RUN = 12
ERROR_CONTEXT_KEEP = 64
ERROR_CONTEXT_OTHERS = 3
ERROR_CONTEXT_LINE = re.compile(r"Error Context:\s*(\S*error-context\.md)")
_ERROR_CONTEXTS: collections.OrderedDict[str, tuple[str, str]] = collections.OrderedDict()
_ERROR_CONTEXTS_LOCK = threading.Lock()
ERROR_CONTEXT_BOUND_KEEP = 32
_BOUND_NOTES: collections.OrderedDict[str, str] = collections.OrderedDict()
_FRESH_RUN: set[int] = set()
_FAILED_STATUSES = ("failed", "timedOut", "interrupted")

_HELPER_TARGET = re.compile(r"(?:could not find (?:anything to click|a field to type into|anything to check or uncheck): "
                            r"|could not find |No dropdown named |The requirement says )(.+?)"
                            r"(?: is shown, but nothing visible matches)?\.(?:\s|$)")
_HELPER_NAMED = re.compile(r"^([a-z]+) named (.+)$")
_ROLE_CALL = re.compile(r"getByRole\(\s*(['\"`])(\w+)\1(?:\s*,\s*\{[^}]*?\bname\s*:\s*(?:(['\"`])((?:(?!\3).)+)\3|/((?:\\.|[^/\\\n])+)/\w*))?")
_TEXT_CALL = re.compile(r"getBy(?:Text|Label|Placeholder|Title|AltText)\(\s*(?:(['\"`])((?:(?!\1).)+)\1|/((?:\\.|[^/\\\n])+)/\w*)")
_QUOTED_TARGET = re.compile(r"([\"'`])([^\"'`\n]{2,80})\1")
_SNAPSHOT_LINE = re.compile(r"^\s*-\s+([A-Za-z][\w-]*)(?:\s+\"((?:[^\"\\]|\\.)*)\")?([^:]*)(?::\s*(.*))?$")
_SNAPSHOT_NOISE = re.compile(r"\s*\[(?:ref|cursor)=[^\]]*\]")
_SECRET_NAME = re.compile(r"pass(?:word|code|phrase)?|secret|token|api[\s_-]?key|credential|\bpin\b", re.IGNORECASE)

_INPUT_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton"})
HIDDEN_VALUE = "<hidden>"
_STOP_WORDS = frozenset({"a", "an", "the", "of", "to", "and", "or", "in", "on", "for", "named", "with"})

def _flat(text: str) -> str:
    return " ".join(str(text or "").split())

def error_context_key(path_text: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", str(path_text or "").strip()) if p]
    if len(parts) < 2 or parts[-1] != "error-context.md" or parts[-2].startswith("."):
        return ""
    return parts[-2]

def _regex_words(source: str | None) -> str:
    return " ".join(re.sub(r"\\[a-zA-Z]|\\(.)|[\^$.*+?()\[\]{}|]", lambda m: m.group(1) or " ", source or "").split())

def looked_for(message: str) -> tuple[str, str]:
    text = strip_terminal_codes(str(message or ""))
    lines = [line for line in text.splitlines() if line.strip()]
    match = _HELPER_TARGET.search("\n".join(lines[:6]))
    if match:
        first = match.group(1).split(" / ")[0].strip()
        named = _HELPER_NAMED.match(first)
        return (named.group(1), named.group(2).strip()) if named else ("", first)

    chain = next((line for line in lines if "getBy" in line), "")
    calls = [(m.start(), m.group(2), (m.group(4) or _regex_words(m.group(5))).strip()) for m in _ROLE_CALL.finditer(chain)]
    calls += [(m.start(), "", (m.group(2) or _regex_words(m.group(3))).strip()) for m in _TEXT_CALL.finditer(chain)]
    if calls:
        _at, role, name = max(calls)
        return role, name
    match = _QUOTED_TARGET.search(lines[0]) if lines else None
    return ("", match.group(2).strip()) if match else ("", "")

def snapshot_lines(markdown: str) -> list[str]:
    text = str(markdown or "").replace("\r\n", "\n")
    at = text.find("# Page snapshot")
    rest = text[at:] if at >= 0 else text
    match = re.search(r"```[\w-]*\n(.*?)(?:\n```|\Z)", rest, re.DOTALL)
    body = match.group(1) if match else rest
    return [line.rstrip() for line in body.split("\n") if line.strip() and not line.startswith("# ")]

def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP_WORDS}

def _snapshot_score(line: str, role: str, name: str) -> tuple[int, str]:
    clean = _SNAPSHOT_NOISE.sub("", line)
    match = _SNAPSHOT_LINE.match(clean)
    line_role = match.group(1) if match else ""
    line_name = " ".join(re.sub(r"\\(.)", r"\1", match.group(2) or "").split()).lower() if match else ""
    value = " ".join((match.group(4) or "").strip().strip('"').split()).lower() if match else ""
    want = " ".join(name.split()).lower()
    same_role = bool(role) and line_role.lower() == role.lower()
    if want:
        if line_name == want or (value == want and not line_name):
            return (1000, "exact") if same_role or not role else (900, "name")
        if len(want) >= 3 and want in clean.lower():
            return 700 + (10 if same_role else 0), "contains"
        target = _words(want)
        shared = len(target & _words(clean))
        if target and shared:
            return int(100 * shared / len(target)) + (10 if same_role else 0), "words"
        return 0, ""
    return (50, "role") if same_role else (0, "")

def _show_line(line: str) -> str:
    clean = _SNAPSHOT_NOISE.sub("", line)
    match = _SNAPSHOT_LINE.match(clean)
    if match and match.group(4) and match.group(2) and _SECRET_NAME.search(match.group(2)):
        clean = clean[:match.start(4)] + HIDDEN_VALUE
    return clean if len(clean) <= 160 else clean[:157] + "..."

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())

def shown_lines(lines: list[str]) -> list[str]:
    shown = [_show_line(line) for line in lines]
    index = 0
    while index < len(lines):
        match = _SNAPSHOT_LINE.match(_SNAPSHOT_NOISE.sub("", lines[index]))
        if match and match.group(2) and _SECRET_NAME.search(match.group(2)) and match.group(4) is not None:
            level, whole = _indent(lines[index]), match.group(1).lower() in _INPUT_ROLES
            after = index + 1
            while after < len(lines) and _indent(lines[after]) > level:
                if whole or not lines[after].lstrip().startswith("- "):
                    shown[after] = " " * _indent(lines[after]) + HIDDEN_VALUE
                after += 1
        index += 1
    return shown

def best_snapshot_line(lines: list[str], role: str, name: str) -> tuple[int, str]:
    best, kind, score = -1, "", 0
    if role or name:
        for index, line in enumerate(lines):
            value, what = _snapshot_score(line, role, name)
            if value > score:
                best, kind, score = index, what, value
    return best, kind

def snapshot_found(lines: list[str], role: str, name: str) -> str:
    best, kind = best_snapshot_line(lines, role, name)
    if not lines:
        return "the snapshot is empty"
    if best < 0:
        return ("no line of the snapshot shares a word with it; its first lines:" if (role or name) else
                "what the failing step looked for could not be read from the error; the snapshot's first lines:")
    where = f"line {best + 1} of {len(lines)}"
    return {"exact": f"it IS in the snapshot ({where}, marked <--):",
            "name": f"no line has role {role} with this name; {where} (marked <--) has the name with another role:",
            "contains": f"no line has exactly this name; {where} (marked <--) contains it:",
            "words": f"no line contains this name; {where} (marked <--) shares the most words with it:",
            "role": f"no name was read; the first line with role {role} is {where} (marked <--):"}[kind]

def snapshot_excerpt(lines: list[str], role: str, name: str, budget: int) -> str:
    if not lines:
        return ""
    best, _kind = best_snapshot_line(lines, role, name)
    total = len(lines)
    display = shown_lines(lines)
    if best < 0:
        shown, used = [], 0
        for text in display:
            if used + len(text) + 1 > budget:
                break
            shown.append(text)
            used += len(text) + 1
        if len(shown) < total:
            shown.append(f"  ... ({total - len(shown)} more lines)")
        return "\n".join(shown)[:budget]
    level = _indent(lines[best])
    enclosing: list[int] = []
    for index in range(best - 1, -1, -1):
        if _indent(lines[index]) < level:
            level = _indent(lines[index])
            enclosing.append(index)
            if len(enclosing) >= 4 or level == 0:
                break
    start, end = max(0, best - 5), min(total, best + 13)

    def render(start: int, end: int) -> str:
        picked = sorted(set([i for i in enclosing if i < start] + list(range(start, end))))
        out: list[str] = []
        previous = -1
        for index in picked:
            if index > previous + 1:
                out.append("  ...")
            out.append(display[index] + ("   <--" if index == best else ""))
            previous = index
        if previous < total - 1:
            out.append("  ...")
        return "\n".join(out)

    text = render(start, end)
    while len(text) > budget and (end > best + 1 or start < best):
        if end > best + 1 and (end - best > best - start or start >= best):
            end -= 1
        else:
            start += 1
        text = render(start, end)
    return text[:budget]

def error_context_excerpt(markdown: str, message: str, label: str, limit: int = ERROR_CONTEXT_TEST_CHARS) -> str:
    role, name = looked_for(message)
    lines = snapshot_lines(markdown)
    target = (f'{role} "{name}"' if role and name else f'"{name}"' if name else role) or "(not read from the error)"
    head = f"-- {label}: looked for {target}; {snapshot_found(lines, role, name)}"
    if len(head) > 400:
        head = head[:397] + "..."
    best, kind = best_snapshot_line(lines, role, name)
    others = ""
    if kind in ("name", "contains", "words"):

        scored = sorted(((-_snapshot_score(line, role, name)[0], index) for index, line in enumerate(lines)
                         if abs(index - best) > 13 and _snapshot_score(line, role, name)[0] > 0))[:ERROR_CONTEXT_OTHERS]
        if scored:
            display = shown_lines(lines)
            others ="\nother lines that come close: " + " | ".join(
                f"line {index + 1}: {display[index].strip()}" for _score, index in sorted(scored, key=lambda s: s[1]))
            others = others[:500]
    body = snapshot_excerpt(lines, role, name, budget=max(200, limit - len(head) - len(others) - 1))
    return head + ("\n" + body if body else "") + others

def _first_error_line(result: dict) -> tuple[str, str]:
    errors = [e for e in (result.get("errors") or []) if isinstance(e, dict) and e.get("message")]
    if not errors and isinstance(result.get("error"), dict) and result["error"].get("message"):
        errors = [result["error"]]
    message = strip_terminal_codes(str(errors[0]["message"])) if errors else ""
    first = next((line for line in message.splitlines() if line.strip()), "")
    return message, _flat(first)[:200]

def _read_run(report: Path | str | None, backend: Path | str | None) -> tuple[list[tuple[str, str, str]], set[str], bool]:
    kept: list[tuple[str, str, str]] = []
    named: set[str] = set()
    if report is None or backend is None:
        return kept, named, False
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8", errors="replace"))
        root = (Path(backend) / "test-results").resolve()
    except (OSError, ValueError, RuntimeError):
        return kept, named, False
    if not isinstance(data, dict):
        return kept, named, False

    def read(path_text: str) -> tuple[str, str] | None:
        path = Path(path_text)
        if not path.is_absolute():
            path = Path(backend) / path
        if path.name != "error-context.md" or path.is_symlink():
            return None
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            return None
        if any(part.startswith(".") for part in relative.parts) or not resolved.is_file() \
                or resolved.stat().st_size > ERROR_CONTEXT_FILE_BYTES:
            return None
        key = error_context_key(relative.as_posix())
        return (key, resolved.read_text(encoding="utf-8", errors="replace")) if key else None

    def walk(suite: dict, path: list[str]) -> None:
        for spec in suite.get("specs") or []:
            file = Path(str(spec.get("file") or suite.get("file") or "")).name
            title = " › ".join(path + [str(spec.get("title") or "")])
            for test in spec.get("tests") or []:
                for result in test.get("results") or []:
                    if result.get("status") not in _FAILED_STATUSES:
                        continue
                    for attachment in result.get("attachments") or []:
                        if attachment.get("name") != "error-context" or not attachment.get("path"):
                            continue
                        key = error_context_key(str(attachment["path"]))
                        if key:
                            named.add(key)
                        if len(kept) >= ERROR_CONTEXT_PER_RUN:
                            break
                        try:
                            got = read(str(attachment["path"]))
                        except (OSError, ValueError, RuntimeError):
                            got = None
                        if got is None:
                            continue
                        message, first = _first_error_line(result)
                        if first:
                            kept.append((got[0], first, error_context_excerpt(got[1], message, f"{file} › {title}")))
                        break
        for child in suite.get("suites") or []:
            walk(child, path + [str(child.get("title") or "")])

    try:
        for suite in data.get("suites") or []:
            walk(suite, [])
    except Exception:
        return kept, named, False
    return kept, named, True

def read_error_contexts(report: Path | str | None, backend: Path | str | None) -> dict[str, tuple[str, str]]:
    kept, _named, _complete = _read_run(report, backend)
    return {key: (first, text) for key, first, text in kept}

def remember_error_contexts(report: Path | str | None, backend: Path | str | None) -> int:
    kept, named, complete = _read_run(report, backend)
    with _ERROR_CONTEXTS_LOCK:
        if not complete:
            _ERROR_CONTEXTS.clear()
        for key in named:
            _ERROR_CONTEXTS.pop(key, None)
        for key, first, text in kept:
            _ERROR_CONTEXTS.pop(key, None)
            _ERROR_CONTEXTS[key] = (first, text)
        while len(_ERROR_CONTEXTS) > ERROR_CONTEXT_KEEP:
            _ERROR_CONTEXTS.popitem(last=False)
        _FRESH_RUN.add(threading.get_ident())
    return len(kept)

def _note_from(text: str, known: dict) -> str:
    if not known:
        return ""
    flat = _flat(text)
    entries: list[str] = []
    for match in ERROR_CONTEXT_LINE.finditer(text):
        key = error_context_key(match.group(1))
        entry = known.pop(key, None) if key else None
        if not isinstance(entry, (tuple, list)) or len(entry) != 2 or not entry[0] or not entry[1] \
                or str(entry[0]) not in flat:
            continue
        entries.append(str(entry[1]))
        if len(entries) >= ERROR_CONTEXT_TESTS:
            break
    return (ERROR_CONTEXT_HEAD + "\n" + "\n\n".join(entries)) if entries else ""

def bind_error_context_note(output: str, contexts: dict | None = None, passed: bool = False) -> str:
    try:
        with _ERROR_CONTEXTS_LOCK:
            fresh = threading.get_ident() in _FRESH_RUN
            _FRESH_RUN.discard(threading.get_ident())
        if passed:
            return ""
        text = strip_terminal_codes(str(output or ""))
        if "error-context.md" not in text or ERROR_CONTEXT_HEAD in text:
            return ""
        with _ERROR_CONTEXTS_LOCK:
            if contexts is not None:
                note = _note_from(text, dict(contexts))
            else:
                inherited = None if fresh else next((note for earlier, note in reversed(_BOUND_NOTES.items())
                                                     if earlier in text), None)
                note = inherited if inherited is not None else _note_from(text, dict(_ERROR_CONTEXTS))
            _BOUND_NOTES.pop(text, None)
            _BOUND_NOTES[text] = note
            while len(_BOUND_NOTES) > ERROR_CONTEXT_BOUND_KEEP:
                _BOUND_NOTES.popitem(last=False)
        return note
    except Exception:
        return ""

def error_context_note(output: str) -> str:
    try:
        text = strip_terminal_codes(str(output or ""))
        if "error-context.md" not in text:
            return ""
        with _ERROR_CONTEXTS_LOCK:
            known = dict(_ERROR_CONTEXTS)
        return _note_from(text, known)
    except Exception:
        return ""
