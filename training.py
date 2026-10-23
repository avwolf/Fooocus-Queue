"""
LoRA training-set capture: copy finished images into a per-trigger-word
folder alongside a caption .txt drafted from the prompt.

The captions are a starting point only — the user is expected to hand-edit
them (e.g. removing traits the trigger word should absorb).
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

# Fooocus output dirs are named YYYY-MM-DD, and its output files are prefixed
# with the same date (YYYY-MM-DD_HH-MM-SS_NNNN.ext).
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_ANGLE_TAG_RE = re.compile(r"<[^<>]*>")                         # <lora:name:0.8>, <embedding:x>
_WILDCARD_RE  = re.compile(r"__[\w\-/ ]+?__")                   # Fooocus wildcards: __pose__
_WEIGHT_RE    = re.compile(r"\(([^()]*?)\s*:\s*-?[\d.]+\s*\)")  # (blue eyes:1.3) -> blue eyes
_BRACKETS_RE  = re.compile(r"[()\[\]{}\\]")                     # leftover emphasis/array brackets
_TAG_SPLIT_RE = re.compile(r"[,\n]")
# Booru-style escaped parens (slacks \(pants\)) are literal text, not emphasis:
# park them as placeholders while stripping syntax, then restore them unescaped.
_ESC_OPEN, _ESC_CLOSE = "\x00", "\x01"
_WHITESPACE_RE = re.compile(r"\s+")

_INVALID_NAME_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}

# PIL modes PNG can store as-is; anything else (CMYK, I;16, ...) becomes RGB.
_PNG_MODES = {"RGB", "RGBA", "L", "LA", "P"}

BLANK_TRIGGER_MSG = "Enter a trigger word to add to the training set"


def derive_caption(prompt: str, trigger: str) -> str:
    """Draft a kohya-style caption: '<trigger>, tag, tag, ...'.

    Strips LoRA/embedding tags, wildcards, and weight/emphasis syntax (keeping
    escaped parens like `slacks \\(pants\\)` as literal `slacks (pants)`), splits
    on commas and newlines (Fooocus treats each prompt line as a separate
    prompt), and drops duplicate tags — including any repeat of the trigger.
    """
    text = (prompt or "").replace("\\(", _ESC_OPEN).replace("\\)", _ESC_CLOSE)
    text = _ANGLE_TAG_RE.sub("", text)
    text = _WILDCARD_RE.sub("", text)
    # Repeat so nested weights like ((a:1.2):1.1) fully unwrap.
    while True:
        unwrapped = _WEIGHT_RE.sub(r"\1", text)
        if unwrapped == text:
            break
        text = unwrapped
    text = _BRACKETS_RE.sub("", text).replace(_ESC_OPEN, "(").replace(_ESC_CLOSE, ")")

    seen = {trigger.lower()}
    tags: list[str] = []
    for raw in _TAG_SPLIT_RE.split(text):
        tag = _WHITESPACE_RE.sub(" ", raw).strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return ", ".join([trigger, *tags])


def validate_trigger(trigger: str) -> str | None:
    """Return an error message, or None if `trigger` is usable as a folder name.

    Callers should pass the already-stripped trigger word.
    """
    if not trigger:
        return BLANK_TRIGGER_MSG
    bad = (
        any(c in _INVALID_NAME_CHARS or ord(c) < 32 for c in trigger)
        or trigger.endswith((".", " "))
        or trigger.split(".")[0].upper() in _RESERVED_NAMES
    )
    if bad:
        return f"Trigger word can't be used as a folder name: {trigger!r}"
    return None


def locate_output(gallery_path: str, outputs_root: Path) -> Path | None:
    """Map a Gradio temp-cache path from Fooocus's result gallery to the real
    file in outputs_root/YYYY-MM-DD/. Falls back to the temp copy itself;
    None if neither exists."""
    temp = Path(gallery_path)
    name = temp.name
    if DATE_DIR_RE.match(name[:10]):
        real = outputs_root / name[:10] / name
        if real.exists():
            return real
    return temp if temp.exists() else None


def _free_stem(dest_dir: Path, stem: str) -> str:
    """First of stem, stem_1, stem_2, ... with neither a .png nor a .txt taken,
    so a hand-edited image or caption is never overwritten."""
    candidate, n = stem, 0
    while (dest_dir / f"{candidate}.png").exists() or (dest_dir / f"{candidate}.txt").exists():
        n += 1
        candidate = f"{stem}_{n}"
    return candidate


def add_to_training_set(image_path: Path, prompt: str, trigger: str, training_root: Path) -> Path:
    """Copy image_path into training_root/trigger/ as a full-resolution PNG
    with a caption .txt beside it. Never overwrites. Returns the PNG path.

    PNG (lossless) rather than the source format, so retouching and re-saving
    in an image editor doesn't compound WebP/JPEG artifacts.
    """
    dest_dir = training_root / trigger
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = _free_stem(dest_dir, image_path.stem)
    png_path = dest_dir / f"{stem}.png"

    with Image.open(image_path) as img:
        out = img if img.mode in _PNG_MODES else img.convert("RGB")
        out.save(png_path, format="PNG")
    (dest_dir / f"{stem}.txt").write_text(derive_caption(prompt, trigger), encoding="utf-8")
    return png_path
