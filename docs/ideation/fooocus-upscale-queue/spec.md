# Implementation Spec: Fooocus Upscale Queue

**Contract**: ./contract.md
**Estimated Effort**: M

## Technical Approach

Build a standalone Python Gradio application that serves as a companion to a running Fooocus instance. The app has four independent modules — config, log parser, Fooocus Gradio client, and queue manager — plus a Gradio UI entry point (`app.py`) that wires them together.

The UI is split into two panels: a gallery/selection panel on top and a queue panel below. The gallery loads images from all `YYYY-MM-DD` subdirectories under the configured Fooocus outputs root. When the user clicks an image, the app reads the `log.html` in the same date directory and extracts the matching image's positive prompt, negative prompt, and seed. The user selects a Vary/Upscale operation type and clicks Submit. The app uses `gradio_client` to connect to Fooocus at `localhost:7865` and submit the job asynchronously. A background thread polls the returned `gradio_client.Job` object for status updates, which are reflected in the queue panel via `gr.Timer`.

The queue metadata (image name, mode, status, timestamp) is persisted to `queue.json` on every state change and reloaded on startup. In-memory `gradio_client.Job` objects cannot be serialized, so jobs loaded from a previous session start in a terminal `"submitted (previous session)"` status rather than being re-polled.

**Spikes / discovery tasks (do these first before implementing):**
1. Examine a real `log.html` from your Fooocus outputs directory to understand its HTML structure — specifically what element contains the image filename, and where positive prompt, negative prompt, and seed appear.
2. With Fooocus running, run `python -c "from gradio_client import Client; c = Client('http://localhost:7865'); c.view_api()"` to discover the available API endpoints, their `api_name` values, and the exact positional parameter order. Identify which endpoint handles the Input Image / Upscale & Vary tab.

## Feedback Strategy

**Inner-loop command**: `python -m pytest tests/ -x -q`

**Playground**: Test suite (pytest) for `log_parser` and `queue_manager`; dev server (`python app.py`) with a real Fooocus instance for end-to-end validation of the client and UI.

**Why this approach**: The highest-complexity logic is in `log_parser.py` and `queue_manager.py`, which are fully unit-testable without Fooocus. The Gradio client integration requires a live Fooocus instance and is validated manually via scripts.

## File Changes

### New Files

| File Path                          | Purpose                                                                |
| ---------------------------------- | ---------------------------------------------------------------------- |
| `app.py`                           | Gradio UI entry point; wires all modules together                      |
| `config.py`                        | Loads configuration from `.env` / environment variables                |
| `log_parser.py`                    | Parses Fooocus `log.html` to extract prompts and seed for an image     |
| `fooocus_client.py`                | Submits upscale/vary jobs to Fooocus via `gradio_client`, polls status |
| `queue_manager.py`                 | Loads, saves, and updates the persistent `queue.json`                  |
| `requirements.txt`                 | Python dependencies                                                    |
| `.env.example`                     | Example configuration file                                             |
| `scripts/discover_api.py`          | One-shot helper: connects and prints `client.view_api()` output        |
| `scripts/test_submit.py`           | One-shot helper: submits a test image and polls until done/failed      |
| `tests/test_log_parser.py`         | Unit tests for the log parser                                          |
| `tests/test_queue_manager.py`      | Unit tests for the queue manager                                       |
| `tests/fixtures/sample_log.html`   | Real Fooocus `log.html` copied from your outputs dir (for tests)       |

### Modified Files

_None — greenfield project._

## Implementation Details

### 1. Configuration (`config.py`)

**Overview**: Loads settings from environment variables with `.env` support via `python-dotenv`. Provides a `Config` dataclass used throughout the app.

```python
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    outputs_root: Path   # Root directory containing YYYY-MM-DD subdirs
    fooocus_url: str     # e.g., "http://localhost:7865"
    queue_file: Path     # Path to queue.json

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
```

**`.env.example`**:
```
FOOOCUS_OUTPUTS_ROOT=C:/path/to/Fooocus/outputs
FOOOCUS_URL=http://localhost:7865
QUEUE_FILE=queue.json
```

**Key decisions**:
- `FOOOCUS_OUTPUTS_ROOT` is required — raises `RuntimeError` with a helpful message if missing
- `FOOOCUS_URL` defaults to `http://localhost:7865` matching the stated constraint

**Implementation steps**:
1. Create `.env.example`
2. Implement `config.py` with `load_config()`
3. No feedback loop needed — trivial config module

---

### 2. Image Gallery Loader (in `app.py`)

**Overview**: Scans `FOOOCUS_OUTPUTS_ROOT` for `YYYY-MM-DD` subdirectories and returns absolute image paths sorted newest-first.

```python
import re
from pathlib import Path

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def collect_images(outputs_root: Path) -> list[str]:
    """Return image paths from all date subdirs, newest date first."""
    date_dirs = sorted(
        [d for d in outputs_root.iterdir() if d.is_dir() and DATE_DIR_RE.match(d.name)],
        reverse=True,
    )
    images: list[Path] = []
    for date_dir in date_dirs:
        for ext in ("*.png", "*.jpg", "*.webp"):
            images.extend(sorted(date_dir.glob(ext), reverse=True))
    return [str(p) for p in images]
```

**Key decisions**:
- Only directories matching `YYYY-MM-DD` are included to avoid unrelated subdirectories
- Images within each date dir are also newest-first (by filename sort — Fooocus uses timestamped filenames)

**Implementation steps**:
1. Implement `collect_images()` in `app.py` (too small to split out)
2. Wire to `gr.Gallery`

**Feedback loop**:
- **Playground**: Python one-liner against a real outputs directory
- **Experiment**: Test with an empty root, single date dir, multiple date dirs with mixed extensions
- **Check command**: `python -c "from app import collect_images; from pathlib import Path; imgs = collect_images(Path('YOUR_PATH')); print(len(imgs), imgs[:2])"`

---

### 3. log.html Parser (`log_parser.py`)

**Overview**: Parses the Fooocus `log.html` in a given date directory to find the metadata entry for a specific image filename, returning positive prompt, negative prompt, and seed.

> **Discovery required first**: Open a real `log.html` from your Fooocus outputs in a browser. Open DevTools → Elements and inspect the structure. Identify:
> - What element contains the image filename (typically an `<img src="...">` tag or a filename text node)
> - Where positive prompt, negative prompt, and seed values appear (likely `<p>` or `<td>` text in the same entry block)
> - Save a real `log.html` as `tests/fixtures/sample_log.html` before writing any implementation

**Anticipated structure** (verify against real file — adjust selectors):
```python
from bs4 import BeautifulSoup
from pathlib import Path
from dataclasses import dataclass

@dataclass
class ImageMetadata:
    positive_prompt: str
    negative_prompt: str
    seed: int

class LogParseError(Exception):
    pass

def parse_log(log_path: Path, image_filename: str) -> ImageMetadata:
    """
    Parse log.html to extract metadata for the given image filename.
    Raises LogParseError if file missing, image not found, or structure unrecognized.
    """
    if not log_path.exists():
        raise LogParseError(f"log.html not found: {log_path}")

    soup = BeautifulSoup(log_path.read_text(encoding="utf-8"), "html.parser")

    # IMPLEMENTATION NOTE: Adjust selectors after inspecting a real log.html.
    # Fooocus log.html likely uses a structure where each generated image
    # has an entry block containing the filename and its metadata.
    for entry in soup.select("ADJUST_SELECTOR"):  # e.g., "tr", ".log-entry", "div.image-block"
        img = entry.find("img")
        if img and Path(img.get("src", "")).name == image_filename:
            return ImageMetadata(
                positive_prompt=_extract_field(entry, "Prompt"),      # adjust label
                negative_prompt=_extract_field(entry, "Negative"),    # adjust label
                seed=int(_extract_field(entry, "Seed")),              # adjust label
            )

    raise LogParseError(f"No log entry found for: {image_filename}")

def _extract_field(entry, label: str) -> str:
    """
    Extract a labeled field's value from a log entry element.
    Adjust implementation to match real log.html structure.
    """
    for elem in entry.find_all(["p", "td", "span"]):  # adjust element types
        text = elem.get_text(separator=" ", strip=True)
        if text.startswith(label):
            return text[len(label):].lstrip(": ").strip()
    return ""
```

**Key decisions**:
- `LogParseError` is always raised (never silently swallowed) so the UI shows a clear error
- Keyed by filename, not by position, since log.html entries may not match file sort order
- `BeautifulSoup` with `"html.parser"` (stdlib, no extra binary deps like `lxml`)

**Implementation steps**:
1. Copy a real `log.html` to `tests/fixtures/sample_log.html`
2. Inspect the HTML structure and update the selectors in `parse_log()` and `_extract_field()`
3. Note the exact filenames that appear in the fixture so you can write a passing test case
4. Write `tests/test_log_parser.py` with the cases below, then implement until all pass

**Feedback loop**:
- **Playground**: Create `tests/test_log_parser.py` with the fixture and a known-good filename before writing the implementation
- **Experiment**: Test the known-good filename (expect prompts + seed), a bad filename (expect `LogParseError`), and a missing log.html path (expect `LogParseError`)
- **Check command**: `python -m pytest tests/test_log_parser.py -x -q`

---

### 4. Fooocus Gradio Client (`fooocus_client.py`)

**Overview**: Connects to the running Fooocus Gradio app via `gradio_client`, submits upscale/vary jobs asynchronously, and polls in-memory `Job` objects for status updates.

> **Discovery required first**: Run `python scripts/discover_api.py` with Fooocus running to print all available Gradio API endpoints, their `api_name` values, and exact parameter signatures. Identify:
> - The `api_name` for the "Input Image" / "Upscale or Vary" operation (the Fooocus tab that handles UOV)
> - The positional parameter order: which argument receives the input image, which receives `uov_method`, prompt, negative prompt, seed, and what the remaining defaults should be
> - The exact `uov_method` string values Fooocus accepts (e.g., `"Vary (Subtle)"`, `"Upscale (2x)"`)

**`scripts/discover_api.py`** (write this first):
```python
from gradio_client import Client
client = Client("http://localhost:7865/")
client.view_api()  # prints all endpoints with param names and types
```

**`scripts/test_submit.py`** (write after discovery, before full implementation):
```python
from gradio_client import Client, handle_file
from gradio_client.utils import Status
import time, sys

client = Client("http://localhost:7865/")
image_path = sys.argv[1]  # pass a real image path as argument

job = client.submit(
    handle_file(image_path),
    "Upscale (2x)",   # uov_method — adjust after discovery
    "test prompt",     # positive prompt
    "",                # negative prompt
    -1,                # seed (-1 = random)
    # ... any other required args at defaults — fill in from view_api()
    api_name="FILL_IN",
)

while True:
    status = job.status()
    print(f"Status: {status.code}")
    if status.code in (Status.FINISHED, Status.CANCELLED):
        break
    time.sleep(2)

print("Done. Result:", job.result())
```

**`fooocus_client.py`** implementation:
```python
from gradio_client import Client, handle_file
from gradio_client.utils import Status
from enum import StrEnum
from pathlib import Path
from dataclasses import dataclass, field
import uuid

class UovMethod(StrEnum):
    # Verify these string values against client.view_api() output
    VARY_SUBTLE  = "Vary (Subtle)"
    VARY_STRONG  = "Vary (Strong)"
    UPSCALE_1_5X = "Upscale (1.5x)"
    UPSCALE_2X   = "Upscale (2x)"
    UPSCALE_4X   = "Upscale (4x)"

@dataclass
class SubmittedJob:
    job_id: str           # UUID generated by us — used as the queue tracking key
    gradio_job: object    # gradio_client.Job — held in memory; not serializable to JSON

def create_client(fooocus_url: str) -> Client:
    """Create and return a Gradio client connected to Fooocus."""
    return Client(fooocus_url)

def submit_upscale_job(
    client: Client,
    image_path: Path,
    uov_method: UovMethod,
    positive_prompt: str,
    negative_prompt: str,
    seed: int,
) -> SubmittedJob:
    """
    Submit an upscale/vary job to Fooocus via Gradio API.

    IMPLEMENTATION NOTE: Fill in the api_name and positional parameter list
    after running scripts/discover_api.py. The parameters shown are the ones
    we care about — verify their position among all parameters Fooocus expects.
    Parameters we don't care about should be passed at their default values.
    """
    gradio_job = client.submit(
        handle_file(str(image_path)),  # input_image
        str(uov_method),               # uov_method
        positive_prompt,               # prompt
        negative_prompt,               # negative_prompt
        seed,                          # seed
        # ... fill in remaining required positional args at their defaults
        api_name="FILL_IN_FROM_VIEW_API",
    )
    return SubmittedJob(job_id=str(uuid.uuid4()), gradio_job=gradio_job)

def get_job_status(submitted_job: SubmittedJob) -> str:
    """
    Return our internal status string for a submitted job.
    Maps gradio_client Status codes to: "queued" | "processing" | "done" | "failed"
    """
    try:
        status = submitted_job.gradio_job.status()
        return {
            Status.PENDING:    "queued",
            Status.STARTING:   "queued",
            Status.PROCESSING: "processing",
            Status.FINISHED:   "done",
            Status.CANCELLED:  "failed",
        }.get(status.code, "queued")
    except Exception:
        return "failed"
```

**Key decisions**:
- We generate our own `uuid` as `job_id` — Fooocus's Gradio API does not expose a stable job identifier we can persist
- `gradio_client.Job` is held in memory; it cannot be serialized. Jobs from previous sessions are marked `"submitted (previous session)"` in the queue display
- A single shared `Client` instance is created at app startup and reused for all submissions
- `handle_file()` is used instead of raw base64 encoding — `gradio_client` handles the file upload protocol

**Implementation steps**:
1. Write and run `scripts/discover_api.py` against your running Fooocus to find the `api_name` and full parameter list
2. Write `scripts/test_submit.py` with the discovered parameters, test it manually with a real image
3. Once `test_submit.py` works end-to-end, copy the verified parameters into `fooocus_client.py`
4. Implement `submit_upscale_job()` and `get_job_status()`

**Feedback loop**:
- **Playground**: `scripts/test_submit.py PATH_TO_IMAGE.png` — submit a real image and watch status update in console
- **Experiment**: Try each `UovMethod` value; verify the job progresses through queued → processing → done; verify the result is accessible via `job.result()`
- **Check command**: `python scripts/test_submit.py PATH_TO_REAL_IMAGE.png`

---

### 5. Queue Manager (`queue_manager.py`)

**Overview**: Maintains a list of submitted job metadata, persisted to `queue.json`. Decoupled from `gradio_client.Job` objects — only stores serializable data.

```python
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timezone

@dataclass
class QueueEntry:
    job_id: str           # our UUID — matches SubmittedJob.job_id
    image_filename: str
    uov_method: str
    positive_prompt: str
    seed: int
    status: str           # "queued" | "processing" | "done" | "failed" | "submitted (previous session)"
    submitted_at: str     # ISO 8601

class QueueManager:
    def __init__(self, queue_file: Path):
        self.queue_file = queue_file
        self.entries: list[QueueEntry] = self._load()

    def _load(self) -> list[QueueEntry]:
        if not self.queue_file.exists():
            return []
        try:
            data = json.loads(self.queue_file.read_text(encoding="utf-8"))
            # Jobs in non-terminal states from previous sessions can't be re-polled
            entries = []
            for e in data:
                if e.get("status") in ("queued", "processing"):
                    e["status"] = "submitted (previous session)"
                entries.append(QueueEntry(**e))
            return entries
        except Exception:
            return []  # corrupt queue.json — start fresh

    def add(self, entry: QueueEntry) -> None:
        self.entries.append(entry)
        self._save()

    def update_status(self, job_id: str, status: str) -> None:
        for entry in self.entries:
            if entry.job_id == job_id:
                entry.status = status
                break
        self._save()

    def _save(self) -> None:
        self.queue_file.write_text(
            json.dumps([asdict(e) for e in self.entries], indent=2),
            encoding="utf-8",
        )

    def as_table_rows(self) -> list[list[str]]:
        """Returns rows for a Gradio DataFrame, newest first."""
        return [
            [e.image_filename, e.uov_method, e.status, e.submitted_at]
            for e in reversed(self.entries)
        ]
```

**Key decisions**:
- Previous-session non-terminal jobs are marked `"submitted (previous session)"` on load — we can't re-attach to a `gradio_client.Job` after restart
- Corrupt `queue.json` on load returns an empty list rather than crashing the app
- `_save()` is called on every mutation so the file always reflects current state

**Implementation steps**:
1. Implement `QueueManager` in full
2. Write `tests/test_queue_manager.py` with the key cases below

**Feedback loop**:
- **Playground**: `tests/test_queue_manager.py` with a `tmp_path` fixture (pytest built-in)
- **Experiment**: Add 2 entries, update 1 status, reload from disk, verify state; test with a corrupt `queue.json`
- **Check command**: `python -m pytest tests/test_queue_manager.py -x -q`

---

### 6. Gradio UI (`app.py`)

**Overview**: Wires all four modules into a Gradio 4.x UI with a gallery, metadata panel, operation selector, submit button, status message, and a live-updating queue table.

**UI layout**:
```
┌─────────────────────────────────────────────────────┐
│  Fooocus Upscale Queue                              │
├─────────────────────────────────────────────────────┤
│  [gr.Gallery — scrollable image grid, 4 cols]       │
├─────────────────────────────────────────────────────┤
│  Left column:               Right column:           │
│  Positive Prompt (readonly) Operation (gr.Radio)    │
│  Negative Prompt (readonly) [Submit for Upscaling]  │
│  Seed (readonly)            Status message          │
├─────────────────────────────────────────────────────┤
│  Queue                                              │
│  [gr.DataFrame — Image | Operation | Status | Time] │
└─────────────────────────────────────────────────────┘
```

```python
import gradio as gr
from pathlib import Path
from config import load_config
from log_parser import parse_log, LogParseError
from fooocus_client import create_client, submit_upscale_job, get_job_status, UovMethod
from queue_manager import QueueManager, QueueEntry
from datetime import datetime, timezone
import threading, time, re

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

config = load_config()
fooocus_client = create_client(config.fooocus_url)
queue = QueueManager(config.queue_file)
UOV_OPTIONS = [m.value for m in UovMethod]

def collect_images(outputs_root: Path) -> list[str]:
    date_dirs = sorted(
        [d for d in outputs_root.iterdir() if d.is_dir() and DATE_DIR_RE.match(d.name)],
        reverse=True,
    )
    images: list[Path] = []
    for date_dir in date_dirs:
        for ext in ("*.png", "*.jpg", "*.webp"):
            images.extend(sorted(date_dir.glob(ext), reverse=True))
    return [str(p) for p in images]

def on_image_select(evt: gr.SelectData, gallery_images):
    image_path = Path(gallery_images[evt.index][0])
    log_path = image_path.parent / "log.html"
    try:
        meta = parse_log(log_path, image_path.name)
        return image_path.name, meta.positive_prompt, meta.negative_prompt, meta.seed, ""
    except LogParseError as e:
        return image_path.name, "", "", 0, f"⚠ {e}"

def on_submit(filename, positive, negative, seed, uov_method):
    if not filename:
        return "No image selected.", queue.as_table_rows()
    image_path = _find_image(config.outputs_root, filename)
    if not image_path:
        return f"Image not found: {filename}", queue.as_table_rows()
    try:
        submitted = submit_upscale_job(
            fooocus_client, image_path,
            UovMethod(uov_method), positive, negative, int(seed),
        )
        entry = QueueEntry(
            job_id=submitted.job_id,
            image_filename=filename,
            uov_method=uov_method,
            positive_prompt=positive,
            seed=int(seed),
            status="queued",
            submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
        queue.add(entry)
        _start_polling(submitted)
        return f"✓ Submitted: {filename} ({uov_method})", queue.as_table_rows()
    except Exception as e:
        return f"✗ Submission failed: {e}", queue.as_table_rows()

def _start_polling(submitted):
    """Background thread: poll job status until terminal, update queue."""
    def poll():
        for _ in range(180):  # max ~6 minutes (2s interval)
            time.sleep(2)
            status = get_job_status(submitted)
            queue.update_status(submitted.job_id, status)
            if status in ("done", "failed"):
                break
    threading.Thread(target=poll, daemon=True).start()

def _find_image(outputs_root: Path, filename: str) -> Path | None:
    for date_dir in outputs_root.iterdir():
        if DATE_DIR_RE.match(date_dir.name):
            candidate = date_dir / filename
            if candidate.exists():
                return candidate
    return None

with gr.Blocks(title="Fooocus Upscale Queue") as demo:
    gr.Markdown("## Fooocus Upscale Queue")

    gallery = gr.Gallery(
        value=collect_images(config.outputs_root),
        label="Output Images", columns=4, height=400, allow_preview=True,
    )
    selected_name = gr.State("")  # holds current image filename (not displayed)

    with gr.Row():
        with gr.Column():
            pos_prompt = gr.Textbox(label="Positive Prompt", interactive=False, lines=3)
            neg_prompt = gr.Textbox(label="Negative Prompt", interactive=False, lines=2)
            seed_box   = gr.Number(label="Seed", interactive=False)
        with gr.Column():
            uov_radio  = gr.Radio(UOV_OPTIONS, label="Operation", value=UOV_OPTIONS[0])
            submit_btn = gr.Button("Submit for Upscaling", variant="primary")
            status_msg = gr.Markdown("")

    gr.Markdown("### Queue")
    queue_table = gr.DataFrame(
        value=queue.as_table_rows(),
        headers=["Image", "Operation", "Status", "Submitted"],
        interactive=False,
    )

    # Refresh queue table every 3 seconds (reflects polling thread status updates)
    gr.Timer(3).tick(fn=lambda: queue.as_table_rows(), outputs=queue_table)

    gallery.select(
        fn=on_image_select,
        inputs=[gallery],
        outputs=[selected_name, pos_prompt, neg_prompt, seed_box, status_msg],
    )
    submit_btn.click(
        fn=on_submit,
        inputs=[selected_name, pos_prompt, neg_prompt, seed_box, uov_radio],
        outputs=[status_msg, queue_table],
    )

if __name__ == "__main__":
    demo.launch()
```

**Key decisions**:
- `fooocus_client` (a `Client` instance) is created once at module level and reused — creating a new `Client` per submission would be expensive
- `gr.Timer(3)` drives queue refresh without user interaction (Gradio 4.x built-in)
- `selected_name` is `gr.State` — passed from gallery selection to the submit handler without displaying it in the UI
- `on_image_select` receives `gallery_images` as the gallery component's current value so we can index into it to get the file path

**Implementation steps**:
1. Stub all four modules (return test/default data) and get the Gradio layout rendering with `python app.py`
2. Wire `on_image_select` after `log_parser.py` passes its unit tests
3. Wire `on_submit` and `_start_polling` after `fooocus_client.py` is verified with `scripts/test_submit.py`
4. Confirm queue refresh works live (submit a job, wait 3+ seconds, watch status column update)

**Feedback loop**:
- **Playground**: Dev server — `python app.py`, open `http://localhost:7860`
- **Experiment**: Select image → prompts populate; submit → row appears in queue; wait → status changes from queued → processing → done
- **Check command**: `python app.py` (visual); `python -m pytest tests/ -x -q` (backend)

---

## Data Model

### Queue Entry Schema (`queue.json`)

```json
[
  {
    "job_id": "d4e7b2a1-...",
    "image_filename": "2024-01-15_001_abc.png",
    "uov_method": "Upscale (2x)",
    "positive_prompt": "a beautiful landscape, highly detailed",
    "seed": 42,
    "status": "done",
    "submitted_at": "2026-03-04 12:00:00 UTC"
  }
]
```

`job_id` is a UUID we generate — it is not a Fooocus job ID. It serves only as the in-memory key linking a `QueueEntry` to its `SubmittedJob` during a session.

---

## Testing Requirements

### Unit Tests

| Test File                     | Coverage                                                          |
| ----------------------------- | ----------------------------------------------------------------- |
| `tests/test_log_parser.py`    | Found case, not-found filename, missing log file, malformed HTML  |
| `tests/test_queue_manager.py` | Add entry, update status, persist-reload round-trip, corrupt file |

**Key test cases**:
- `test_log_parser`: parse `sample_log.html` for a known filename → assert correct prompts and seed
- `test_log_parser`: unknown filename → `LogParseError` raised
- `test_log_parser`: `log_path` does not exist → `LogParseError` raised
- `test_queue_manager`: add 2 entries, reload from disk, assert both present with correct fields
- `test_queue_manager`: update status on `job_id`, reload, assert new status persisted
- `test_queue_manager`: non-terminal status on load → status becomes `"submitted (previous session)"`
- `test_queue_manager`: corrupt `queue.json` → returns empty list (no crash)

### Manual Testing

- [ ] Gallery loads images from all date subdirs with newest images first
- [ ] Clicking an image populates positive prompt, negative prompt, and seed
- [ ] Missing `log.html` shows a clear error message (not a crash)
- [ ] Each UovMethod option is selectable before submission
- [ ] Submitting a job adds a row to the queue immediately
- [ ] Queue status updates from `queued` → `processing` → `done` without page refresh
- [ ] Stopping and restarting `app.py` reloads prior queue entries from `queue.json`
- [ ] Prior in-progress entries show `"submitted (previous session)"` status on reload
- [ ] Fooocus not running: submission shows a clear connection error message

---

## Error Handling

| Error Scenario                      | Handling Strategy                                                               |
| ----------------------------------- | ------------------------------------------------------------------------------- |
| `FOOOCUS_OUTPUTS_ROOT` not set      | `load_config()` raises `RuntimeError` with message pointing to `.env.example`  |
| `log.html` missing for date dir     | `LogParseError` raised; UI shows `⚠ log.html not found: ...`                  |
| Image filename not in `log.html`    | `LogParseError` raised; UI shows `⚠ No log entry found for: X`                |
| Fooocus not running                 | `gradio_client` raises on `create_client()` or `client.submit()` — caught in `on_submit`, shown as `✗ Submission failed: ...` |
| Fooocus connection lost mid-poll    | `get_job_status()` catches exception, returns `"failed"`, polling stops         |
| Corrupt `queue.json` on startup     | `QueueManager._load()` catches `Exception`, returns empty list, logs warning   |
| `UovMethod` string mismatch         | Fooocus will reject the job — shows as submission failure or immediate `failed` status |

---

## Validation Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Discover Fooocus API (run with Fooocus running)
python scripts/discover_api.py

# Test Gradio API submission end-to-end (run with Fooocus running)
python scripts/test_submit.py PATH_TO_IMAGE.png

# Run unit tests (no Fooocus required)
python -m pytest tests/ -x -q

# Quick smoke test of log parser against fixture
python -c "from log_parser import parse_log; from pathlib import Path; print(parse_log(Path('tests/fixtures/sample_log.html'), 'YOUR_IMAGE.png'))"

# Start the app
python app.py
```

**`requirements.txt`**:
```
gradio>=4.0
gradio-client>=0.9
beautifulsoup4>=4.12
python-dotenv>=1.0
pytest>=8.0
```

---

## Open Items

- [ ] **Discover log.html structure**: Copy a real Fooocus `log.html` to `tests/fixtures/sample_log.html`. Inspect its HTML structure and update the selectors in `log_parser.py` before implementing.
- [ ] **Discover Gradio API endpoint**: Run `scripts/discover_api.py` against a live Fooocus instance. Identify the `api_name` for the UOV operation and the full positional parameter list. Update `fooocus_client.py` accordingly.
- [ ] **Verify UovMethod strings**: Confirm the exact strings Fooocus expects for each Vary/Upscale mode (from `view_api()` output) and update `UovMethod` enum values if needed.
- [ ] **Verify Gallery selection API**: Confirm that `gallery_images[evt.index][0]` correctly returns the file path in your installed Gradio 4.x version. Adjust if the gallery value structure differs.

---

_This spec is ready for implementation. Resolve the Open Items first (discovery tasks), then implement modules in order: config → log_parser → queue_manager → fooocus_client → app.py._
