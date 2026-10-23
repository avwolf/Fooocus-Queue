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
    training_dir: Path  # LoRA training-set root; images go in <training_dir>/<trigger>/


def load_config() -> Config:
    root = os.environ.get("FOOOCUS_OUTPUTS_ROOT")
    if not root:
        raise RuntimeError(
            "FOOOCUS_OUTPUTS_ROOT is not set. Copy .env.example to .env and configure it."
        )
    outputs_root = Path(root)
    return Config(
        outputs_root=outputs_root,
        fooocus_url=os.getenv("FOOOCUS_URL", "http://localhost:7865"),
        queue_file=Path(os.getenv("QUEUE_FILE", "queue.json")),
        # Beside the dated output dirs; the gallery only scans YYYY-MM-DD dirs.
        training_dir=Path(os.getenv("TRAINING_DIR") or outputs_root / "training"),
    )
