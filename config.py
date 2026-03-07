from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    outputs_root: Path  # Root directory containing YYYY-MM-DD subdirs
    fooocus_url: str    # e.g., "http://localhost:7865"
    queue_file: Path    # Path to queue.json


def load_config() -> Config:
    root = os.environ.get("FOOOCUS_OUTPUTS_ROOT")
    if not root:
        raise RuntimeError(
            "FOOOCUS_OUTPUTS_ROOT is not set. Copy .env.example to .env and configure it."
        )
    return Config(
        outputs_root=Path(root),
        fooocus_url=os.getenv("FOOOCUS_URL", "http://localhost:7865"),
        queue_file=Path(os.getenv("QUEUE_FILE", "queue.json")),
    )
