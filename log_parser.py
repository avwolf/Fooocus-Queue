from bs4 import BeautifulSoup
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ImageMetadata:
    positive_prompt: str
    negative_prompt: str
    seed: int


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
    """Extract Prompt, Negative Prompt, and Seed from a div.image-container."""
    metadata = {}
    for row in container.select("table.metadata tr"):
        cells = row.find_all("td")
        if len(cells) == 2:
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
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

    return ImageMetadata(
        positive_prompt=metadata["Prompt"],
        negative_prompt=metadata["Negative Prompt"],
        seed=seed,
    )
