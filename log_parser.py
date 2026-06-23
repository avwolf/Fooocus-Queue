import re
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from pathlib import Path

_LORA_LABEL_RE = re.compile(r"^LoRA \d+$")
_LORA_VALUE_RE = re.compile(r"^(.*?)\s*:\s*([\d.]+)$")
_ADM_GUIDANCE_RE = re.compile(r"([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)")


@dataclass
class ImageMetadata:
    positive_prompt: str
    negative_prompt: str
    seed: int
    performance: str = "Speed"  # absent in older logs; default matches Fooocus's default

    # Model/sampling settings used to generate the original image. Absent in
    # older logs (and on parse failure of an individual field) — None/empty
    # means "fall back to Fooocus's current UI defaults" when submitting.
    base_model: str | None = None
    refiner_model: str | None = None
    refiner_switch: float | None = None
    loras: list[tuple[str, float]] = field(default_factory=list)
    sharpness: float | None = None
    guidance_scale: float | None = None
    adm_guidance: tuple[float, float, float] | None = None
    clip_skip: int | None = None
    sampler: str | None = None
    scheduler: str | None = None
    vae: str | None = None


class LogParseError(Exception):
    pass


def parse_log(log_path: Path, image_filename: str) -> ImageMetadata:
    """
    Parse a Fooocus log.html to extract metadata for the given image filename.

    The log.html structure uses div.image-container blocks, each containing:
      - <img src='FILENAME.png'> identifying the image
      - <table class='metadata'> with <td class='label'> / <td class='value'> rows
        for fields: Prompt, Negative Prompt, Seed, and others.

    Raises LogParseError if the file is missing, the image is not found,
    or required fields cannot be extracted.
    """
    if not log_path.exists():
        raise LogParseError(f"log.html not found: {log_path}")

    soup = BeautifulSoup(log_path.read_text(encoding="utf-8"), "html.parser")

    for container in soup.select("div.image-container"):
        img = container.find("img")
        if img and Path(img.get("src", "")).name == image_filename:
            return _extract_metadata(container, image_filename)

    raise LogParseError(f"No log entry found for: {image_filename}")


def _extract_metadata(container, image_filename: str) -> ImageMetadata:
    """Extract Prompt, Negative Prompt, Seed, and model/sampling settings."""
    metadata = {}
    loras: list[tuple[str, float]] = []
    for row in container.select("table.metadata tr"):
        cells = row.find_all("td")
        if len(cells) == 2:
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            if _LORA_LABEL_RE.match(label):
                m = _LORA_VALUE_RE.match(value)
                if m:
                    loras.append((m.group(1), float(m.group(2))))
                continue
            metadata[label] = value

    missing = [f for f in ("Prompt", "Negative Prompt", "Seed") if f not in metadata]
    if missing:
        raise LogParseError(
            f"Missing fields {missing} in log entry for: {image_filename}"
        )

    try:
        seed = int(metadata["Seed"])
    except ValueError:
        raise LogParseError(
            f"Seed value {metadata['Seed']!r} is not an integer for: {image_filename}"
        )

    adm_guidance = None
    adm_match = _ADM_GUIDANCE_RE.search(metadata.get("ADM Guidance", ""))
    if adm_match:
        adm_guidance = tuple(float(g) for g in adm_match.groups())

    refiner_model = metadata.get("Refiner Model")
    if refiner_model == "None":
        refiner_model = None

    def _float_or_none(label: str) -> float | None:
        value = metadata.get(label)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def _int_or_none(label: str) -> int | None:
        value = metadata.get(label)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    return ImageMetadata(
        positive_prompt=metadata["Prompt"],
        negative_prompt=metadata["Negative Prompt"],
        seed=seed,
        performance=metadata.get("Performance", "Speed"),
        base_model=metadata.get("Base Model"),
        refiner_model=refiner_model,
        refiner_switch=_float_or_none("Refiner Switch"),
        loras=loras,
        sharpness=_float_or_none("Sharpness"),
        guidance_scale=_float_or_none("Guidance Scale"),
        adm_guidance=adm_guidance,
        clip_skip=_int_or_none("CLIP Skip"),
        sampler=metadata.get("Sampler"),
        scheduler=metadata.get("Scheduler"),
        vae=metadata.get("VAE"),
    )
