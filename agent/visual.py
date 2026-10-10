from __future__ import annotations

import base64
import copy
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .fsutil import write_text
from .llm import LLM
from .settings import SETTINGS

IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
MAX_IMAGE_BYTES = 4 * 1024 * 1024

VISUAL_WORKERS = 4
_USAGE_LOCK = threading.Lock()

PROMPT = (
    "This is a reference screenshot for a web page that must be re-implemented. Extract the visible texts verbatim, in "
    "the original language, under EXACTLY these section markers (one item per line starting with '- '; write '- (none)' "
    "for an empty section):\n"
    "HEADINGS:\n(titles of the page's main content: page title, form section titles, panel/tab titles. NOT the site "
    "logo or brand name, NOT advertising banners or slogans, NOT footer columns -- those go under OTHER TEXTS)\n"
    "NAVIGATION:\n(menu items and link texts)\n"
    "BUTTONS:\n"
    "LABELS:\n(form field labels, table column headers)\n"
    "OTHER TEXTS:\n(placeholders, default dropdown values, helper texts, checkbox texts)\n"
    "LAYOUT:\n(3-5 short lines: sections, their order, where forms and primary buttons sit)\n"
    "Be exhaustive about texts, terse about layout."
)

def mandatory_texts(notes: str) -> list[str]:
    texts: list[str] = []
    active = False
    foreign = False
    for raw in notes.splitlines():
        line = raw.strip()
        if line.startswith("Visual reference notes for"):
            foreign = "LANGUAGE MISMATCH" in line
            active = False
            continue
        if foreign:
            continue
        if re.match(r"^[A-Z ]+:\s*$", line.strip("*# ")):
            active = line.strip("*# ").upper().startswith("HEADINGS")
            continue
        if active and line.startswith("-"):
            text = line.lstrip("- ").strip().strip("\"'“”")
            if text and text.lower() != "(none)" and len(text) <= 40:
                texts.append(text)
    return texts

def script_of(text: str) -> str:
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "cjk" if cjk * 4 > latin else "latin"

def structure_only(notes: str, requirement_script: str) -> str:
    kept: list[str] = []
    section = ""
    for raw in notes.splitlines():
        line = raw.strip().strip("*# ")
        if re.match(r"^[A-Z ]+:\s*$", line):
            section = line.upper()
        elif requirement_script == "latin":
            raw = re.sub(r"[一-鿿]+(?:[\s,、·:：/]*[一-鿿]+)*", "[text]", raw)
        elif section.startswith(("HEADINGS", "NAVIGATION", "BUTTONS", "LABELS", "OTHER")) and raw.strip().startswith("-") and re.search(r"[A-Za-z]", raw) and script_of(raw) == "latin":
            raw = raw[: len(raw) - len(raw.lstrip())] + "- [text]"
        kept.append(raw)
    return "\n".join(kept)

SCREENSHOT_TEXT = "[screenshot text]"

def requirement_texts_only(notes: str, requirement: str) -> str:
    req = requirement.lower()
    removed: set[str] = set()
    kept: list[str] = []
    section = ""
    for raw in notes.splitlines():
        line = raw.strip().strip("*# ")
        if re.match(r"^[A-Z ]+:\s*$", line):
            section = line.upper()
            kept.append(raw)
            continue
        item = re.match(r"^(\s*-\s*)(.+?)\s*$", raw)
        if item and section.startswith(("HEADINGS", "NAVIGATION", "BUTTONS", "LABELS", "OTHER")):
            text = item.group(2).strip()
            if text.lower() not in req and "(none)" not in text.lower():
                removed.add(text)
                if kept and kept[-1].strip() == f"- {SCREENSHOT_TEXT}":
                    continue
                raw = item.group(1) + SCREENSHOT_TEXT
        kept.append(raw)
    result = "\n".join(kept)
    layout = result.find("LAYOUT")
    if layout >= 0:
        tail = result[layout:]
        for text in sorted(removed, key=len, reverse=True):
            if len(text) >= 3:
                tail = re.sub(r"(?<![\w-])" + re.escape(text) + r"(?![\w-])", SCREENSHOT_TEXT, tail)
        result = result[:layout] + tail
    return result

def _interface_texts(notes: str) -> str:
    kept: list[str] = []
    active = False
    for raw in notes.splitlines():
        line = raw.strip().strip("*# ")
        if re.match(r"^[A-Z ]+:\s*$", line):
            active = line.upper().startswith(("HEADINGS", "NAVIGATION", "BUTTONS", "LABELS"))
            continue
        if active and "(none)" not in line.lower():
            kept.append(line)
    return "\n".join(kept)

def _mime(path: Path) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(path.suffix.lower().lstrip("."), "image/png")

def _downscale(data: bytes, log, name: str) -> bytes | None:
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    try:
        picture = Image.open(io.BytesIO(data)).convert("RGB")
        if picture.width > 1600:
            picture = picture.resize((1600, int(picture.height * 1600 / picture.width)))
        if picture.height > 4000:
            picture = picture.crop((0, 0, picture.width, 4000))
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=80)
        log(f"[visual] {name}: downscaled {len(data)} -> {buffer.tell()} bytes")
        return buffer.getvalue()
    except Exception as exc:
        log(f"[visual] {name}: downscale failed: {exc}")
        return None

def describe_image(llm: LLM, image: Path, cache_dir: Path, log=print) -> str | None:
    cache = cache_dir / (image.stem + ".md")
    if cache.is_file():
        return cache.read_text(encoding="utf-8")
    try:
        data = image.read_bytes()
    except OSError:
        return None
    mime = _mime(image)
    if len(data) > 600 * 1024:

        shrunk = _downscale(data, log, image.name)
        if shrunk is not None:
            data, mime = shrunk, "image/jpeg"
    if len(data) > MAX_IMAGE_BYTES:
        log(f"[visual] {image.name}: {len(data)} bytes, too large, skipped")
        return None

    candidates = [str(SETTINGS.get("vision_model") or "").strip() or llm.model, "deepseek-v4-flash-vision-exp"]
    message = None
    for model in dict.fromkeys(candidates):
        try:
            message = _ask_vision(llm, model, image, data, mime)
            if str(message.get("content") or "").strip():
                break
        except Exception as exc:
            log(f"[visual] {image.name}: {model} cannot read it ({str(exc)[:120]})")
            message = None
    if message is None:
        return None
    text = str(message.get("content") or "").strip()
    if not text:
        return None
    write_text(cache, text)
    log(f"[visual] {image.name}: {len(text)} chars of notes")
    return text

def _ask_vision(llm: LLM, model: str, image: Path, data: bytes, mime: str) -> dict:
    return llm.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}},
                ],
            }
        ],
        max_tokens=1500,
        tag=f"visual:{image.name}",
        thinking=False,
        model=model,
    )

def _worker_llm(llm: LLM) -> LLM:
    worker = copy.copy(llm)

    def record(usage: dict[str, Any], tag: str) -> None:
        with _USAGE_LOCK:
            llm._record_usage(usage, tag)

    worker._record_usage = record
    return worker

def _describe_safely(llm: LLM, image: Path, cache_dir: Path, log) -> str | None:
    try:
        return describe_image(llm, image, cache_dir, log)
    except Exception as exc:
        log(f"[visual] {image.name}: description failed ({type(exc).__name__}: {str(exc)[:120]})")
        return None

def _describe_all(images: dict[str, Path], llm: LLM, cache_dir: Path, log) -> dict[str, str | None]:
    notes: dict[str, str | None] = {}
    pending: dict[str, Path] = {}
    for key, image in images.items():
        if (cache_dir / (image.stem + ".md")).is_file():
            notes[key] = _describe_safely(llm, image, cache_dir, log)
        else:
            pending[key] = image
    if len(pending) <= 1 or VISUAL_WORKERS <= 1:
        for key, image in pending.items():
            notes[key] = _describe_safely(llm, image, cache_dir, log)
    else:
        with ThreadPoolExecutor(max_workers=min(VISUAL_WORKERS, len(pending)), thread_name_prefix="visual") as pool:
            futures = {key: pool.submit(_describe_safely, _worker_llm(llm), image, cache_dir, log) for key, image in pending.items()}
            for key, future in futures.items():
                try:
                    notes[key] = future.result()
                except Exception as exc:
                    log(f"[visual] {pending[key].name}: description failed ({type(exc).__name__}: {str(exc)[:120]})")
                    notes[key] = None
    return {key: notes.get(key) for key in images}

def attach_visual_notes(tree: dict[str, Any], requirements_dir: Path, project_dir: Path, llm: LLM, log=print) -> int:
    cache_dir = project_dir / ".arc" / "design" / "visual"
    seen: dict[str, str | None] = {}
    count = 0
    mismatched: set[str] = set()

    def resolve(ref: str) -> Path | None:
        rel = ref.strip()
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        candidates = [requirements_dir / rel, requirements_dir / Path(rel).name, requirements_dir / "reference" / Path(rel).name]
        return next((c for c in candidates if c.is_file()), None)

    first: dict[str, Path] = {}

    def collect(node: dict[str, Any]) -> None:
        for ref in IMAGE_REF.findall(str(node.get("description") or "")):
            image = resolve(ref)
            if image is not None and image.name not in first:
                first[image.name] = image
        for child in node.get("children") or []:
            if isinstance(child, dict):
                collect(child)

    collect(tree)
    seen.update(_describe_all(first, llm, cache_dir, log))
    count = sum(1 for value in seen.values() if value)

    def all_text(node: dict[str, Any]) -> str:
        parts = [str(node.get("name") or ""), IMAGE_REF.sub("", str(node.get("description") or ""))]
        for scenario in node.get("scenarios") or []:
            parts.extend(str(step.get("content") or "") for step in scenario.get("steps") or [])
        parts.extend(all_text(child) for child in node.get("children") or [] if isinstance(child, dict))
        return "\n".join(parts)

    whole = all_text(tree)
    quoted = re.findall(r"`([^`\n]{2,40})`|\"([^\"\n]{2,40})\"|“([^”\n]{2,40})”", whole)
    quoted_text = "\n".join(next(g for g in groups if g) for groups in quoted)
    requirement_script = script_of(quoted_text) if len(quoted) >= 10 else script_of(whole)

    def walk(node: dict[str, Any]) -> None:
        nonlocal count
        description = str(node.get("description") or "")
        notes: list[str] = []
        for ref in IMAGE_REF.findall(description):
            image = resolve(ref)
            if image is None:
                continue
            key = image.name
            if key not in seen:
                seen[key] = describe_image(llm, image, cache_dir, log)
                if seen[key]:
                    count += 1
            interface = _interface_texts(seen[key] or "")
            foreign_cjk = requirement_script == "latin" and len(re.findall(r"[一-鿿]", interface)) >= 4

            if seen[key] and re.search(r"[A-Za-z一-鿿]", interface) and (foreign_cjk or script_of(interface) != requirement_script):
                mismatched.add(key)
                notes.append(
                    f"Visual reference notes for {key} (extracted from the screenshot). LANGUAGE MISMATCH: this screenshot "
                    "shows a differently localised build. The application's interface language is the REQUIREMENT's language: "
                    "render every text in the requirement's language and with the requirement's own terms. Use these notes "
                    "for STRUCTURE ONLY (which sections, entries and controls exist, in which order). The screenshot's own texts "
                    f"were removed and show as [text]; take every wording from the requirement:\n{structure_only(seen[key], requirement_script)}"
                )
            elif seen[key]:
                notes.append(

                    f"Visual reference notes for {key} (extracted from the screenshot). Use them for LAYOUT AND STRUCTURE: "
                    "which sections exist, in which order, what kind of controls they hold. The requirement text above decides "
                    "every name, role and wording. The screenshot shows ANOTHER installation with its own data: its accounts, "
                    "repositories, files, ids and records do not exist in this application and are never used as names or "
                    "seed data -- every text the requirement does not use has been replaced by [screenshot text]:\n"
                    f"{requirement_texts_only(seen[key], whole)}"
                )
        if notes:
            node["description"] = description + "\n\n" + "\n\n".join(notes)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    walk(tree)
    if mismatched:
        log(f"[visual] requirement is written in {requirement_script} script, {len(mismatched)} screenshot(s) are not: {sorted(mismatched)[:6]} -> structure only")
    return count
