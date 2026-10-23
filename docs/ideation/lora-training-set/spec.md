# Implementation Spec: LoRA Training Set Capture

**Contract**: ./contract.md
**Estimated Effort**: M

## Technical Approach

All of the training logic goes in a new module, `training.py`, made of pure functions so it can be unit-tested without Fooocus or Gradio:

- caption derivation
- trigger-word validation
- locating the job's real output file
- collision-safe PNG copy and caption write

`app.py` only connects it to the UI and to the existing completion hook in `_start_polling`.

The one change to the Fooocus client is to keep the image paths that the fn=68 poll already receives. Today `SubmittedJob._run_async` checks only whether the finished gallery is non-empty (`_gallery_has_images`) and then discards the result. It will now also record the paths on `SubmittedJob.output_paths`. These are Gradio temp-cache copies (`…/gradio/<hash>/<basename>`). Fooocus names its outputs `YYYY-MM-DD_HH-MM-SS_NNNN.ext`, so the real file is found at `outputs_root / basename[:10] / basename`. If it isn't there, the temp copy is used instead. Both are local because Fooocus runs on the same machine.

The training settings (trigger word and copy status) are stored on `QueueEntry`. The copy happens in the polling thread when a job reaches `done`, so jobs re-queued at startup and retried jobs pick it up automatically. Neither the startup re-queue path nor the retry path needs any changes.

## Feedback Strategy

**Inner-loop command**: `pytest tests/test_training.py -q`

**Playground**: Test suite for `training.py` and the gallery-path parsing. Then a real run of the app (`python app.py`, with Fooocus running) for the UI wiring and the end-to-end copy.

**Why this approach**: Nearly all the logic is string and file manipulation that a fast pytest loop covers. Only the UI wiring and the shape of Fooocus's gallery payload need a live run.

## File Changes

### New Files

| File Path | Purpose |
| --- | --- |
| `training.py` | Caption derivation, trigger validation, output location, and collision-safe PNG and caption write |
| `tests/test_training.py` | Unit tests for `training.py` |

### Modified Files

| File Path | Changes |
| --- | --- |
| `config.py` | Add a `training_dir: Path` field: `TRAINING_DIR` env var, defaulting to `outputs_root / "training"` |
| `.env.example` | Add a commented `# TRAINING_DIR=C:/path/to/Fooocus/outputs/training` line |
| `fooocus_client.py` | Add `_gallery_image_paths()` and make `_gallery_has_images()` use it. Add `SubmittedJob.output_paths: list[str]`, filled in when the job finishes. |
| `queue_manager.py` | Add `QueueEntry.training_trigger` / `training_status` fields and a `QueueManager.update_training_status()` method |
| `app.py` | Checkbox, trigger box and "Add selected to training set" button in a new "Training Set" section of the Settings tab (right column, under Output Format); validation in `on_submit`; copy on `done` in `_start_polling`; reset in `_do_retry`; Training column in `_queue_html` |
| `tests/test_queue_manager.py` | Test that the new fields round-trip and default correctly for legacy entries |
| `tests/test_fooocus_client.py` | Tests for `_gallery_image_paths` payload shapes |
| `README.md` | New "Add to training set" usage section and the `TRAINING_DIR` setting |

## Implementation Details

### 1. `training.py`: caption derivation

**Pattern to follow**: `log_parser.py` (module-level compiled regexes, small pure functions, docstrings explaining Fooocus quirks)

```python
_ANGLE_TAG_RE = re.compile(r"<[^<>]*>")                     # <lora:name:0.8>, <embedding:x>
_WILDCARD_RE  = re.compile(r"__[\w\-/ ]+?__")                # Fooocus wildcards: __pose__
_WEIGHT_RE    = re.compile(r"\(([^()]*?)\s*:\s*-?[\d.]+\s*\)")  # (blue eyes:1.3) -> blue eyes
_BRACKETS_RE  = re.compile(r"[()\[\]{}]")                   # leftover emphasis/array brackets

def derive_caption(prompt: str, trigger: str) -> str:
    """Draft a kohya-style caption: '<trigger>, tag, tag, ...'."""
```

**Algorithm**, in this order:

1. Remove `_ANGLE_TAG_RE` and `_WILDCARD_RE` matches.
2. Apply `_WEIGHT_RE` repeatedly until the string stops changing, which handles nesting like `((a:1.2):1.1)`. Then delete what `_BRACKETS_RE` matches.
3. Split on commas and newlines. Fooocus treats each prompt line as a separate prompt, so a newline counts as a tag separator.
4. Strip each tag, collapse internal whitespace to single spaces, and drop empty tags.
5. Remove duplicates case-insensitively, keeping the first occurrence. Also drop any tag equal to the trigger word, so it doesn't appear twice.
6. Return `", ".join([trigger, *tags])`. If no tags remain, return just the trigger.

**Key decisions**:

- Only the user's positive prompt is used. Style templates and Fooocus V2 expansion are excluded (see the contract's Out of Scope section).
- Backslash-escaped parentheses are kept as literal text, so `slacks \(pants\)` becomes `slacks (pants)`. Booru-style tags use them, and real prompts contain them. They're swapped for placeholders while the syntax is stripped, then restored without the backslashes.

**Feedback loop**:

- **Playground**: Parametrized table in `tests/test_training.py`
- **Experiment**: Run the contract's example (`(blue eyes:1.3), __pose__, <lora:foo:0.8>, red dress,, red dress` with trigger `mychar`, expecting `mychar, blue eyes, red dress`), plus:
  - a multi-line prompt
  - nested weights
  - `[[a, b]]` array syntax
  - a prompt that already contains the trigger in different case
  - an empty prompt
  - `(word)` emphasis with no weight
- **Check command**: `pytest tests/test_training.py -q -k caption`

### 2. `training.py`: trigger validation

```python
def validate_trigger(trigger: str) -> str | None:
    """Return an error message, or None if `trigger` is usable as a folder name."""
```

- If the value is blank after stripping, return `"Enter a trigger word to add to the training set"`. Use this exact text, since it's in the contract.
- Reject the characters `<>:"/\|?*` and control characters. Also reject names that end with `.` or a space, and reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`). The error is `"Trigger word can't be used as a folder name: …"`.
- Callers pass in `trigger.strip()`. Spaces inside the word are allowed, e.g. `my char`.

### 3. `training.py`: locating the output and adding it to the set

```python
def locate_output(gallery_path: str, outputs_root: Path) -> Path | None:
    """Map a Gradio temp-cache path from Fooocus's result gallery to the real
    file in outputs_root/YYYY-MM-DD/. Falls back to the temp copy itself;
    None if neither exists."""

def add_to_training_set(image_path: Path, prompt: str, trigger: str, training_root: Path) -> Path:
    """Copy image_path into training_root/trigger/ as a full-resolution PNG
    with a caption .txt beside it. Never overwrites. Returns the PNG path."""
```

**`locate_output`**: take `name = Path(gallery_path).name`. If `DATE_DIR_RE` matches `name[:10]` and `outputs_root / name[:10] / name` exists, return that path. Otherwise, return the temp path if it exists, or `None` if it doesn't. Don't import `DATE_DIR_RE` from `app.py`, which would be a circular import. Move it into `training.py` (or a tiny shared module) and import it back into `app.py`.

**`add_to_training_set`**:

1. `dest_dir = training_root / trigger`, then `mkdir(parents=True, exist_ok=True)`.
2. Pick a free stem: try `image_path.stem`, then `stem_1`, `stem_2`, and so on, until **neither** `<stem>.png` **nor** `<stem>.txt` exists. Checking both protects a caption that's been hand-edited after its image was deleted, and the other way round.
3. Open the image with `PIL.Image.open(...)`. If the mode isn't one PNG can store (`RGB`, `RGBA`, `L`, `LA`, `P`), convert it to `RGB`. Save it as `<stem>.png` without resizing. A PNG source is saved through PIL as well, so every file goes through the same path. Its pixels are unchanged.
4. Write `derive_caption(prompt, trigger)` to `<stem>.txt` as UTF-8, with no trailing newline.
5. Return the PNG path.

**Key decisions**:

- Converting to PNG at full resolution means retouching and re-saving in GIMP or Affinity never compounds WebP/JPEG artifacts. Bucketing is left to the trainer.
- PIL's `exists()` checks followed by a write leave a small race window. That's acceptable, because jobs finish one at a time (Fooocus has a single-worker queue) and direct adds are user-paced.

**Feedback loop**:

- **Playground**: `tmp_path` fixtures that create small WebP, JPEG and PNG images with PIL
- **Experiment**:
  - a WebP source produces a `.png` with the same size
  - a second add of the same stem produces `_1`
  - a stray `stem.txt` alone also forces `_1`
  - the trigger subfolder is created
  - caption content matches `derive_caption`
  - `locate_output` covers the real-file-found, temp-fallback and neither cases
- **Check command**: `pytest tests/test_training.py -q`

### 4. `fooocus_client.py`: capturing output paths

**Pattern to follow**: the existing `_gallery_has_images` in `fooocus_client.py`

```python
def _gallery_image_paths(item) -> list[str]:
    """File paths in a gallery output item, in order. Handles every shape
    Gradio 3.x uses: a list of file dicts, a list of [file_dict, caption]
    pairs, a {"__type__": "update", "value": [...]} wrapper, or None/[]."""

def _gallery_has_images(item) -> bool:
    return bool(_gallery_image_paths(item))
```

- A file dict's path is in `"name"`. Newer Gradio versions use `"path"`, so read `d.get("name") or d.get("path")`. Skip entries that don't have a string path.
- In `SubmittedJob.__init__`, set `self.output_paths: list[str] = []`. In `_run_async`, at the point where it currently sets `self._status = "done"`, first set `self.output_paths = _gallery_image_paths(finished) or _gallery_image_paths(gallery)`. Then log `f"{tag} outputs: {self.output_paths}"`, which shows the payload shape during the live check.
- The current `_gallery_has_images` docstring may not cover the `[file_dict, caption]` pair form that Gradio 3.41 Gallery postprocess produces. **Verify against a live run before relying on it**, using the log line above. Test every shape either way.
- Set `output_paths` **before** `_status = "done"`. The polling thread in `app.py` reads both, and it has to see the paths as soon as it sees `done`.

### 5. `queue_manager.py`: entry fields

**Pattern to follow**: `QueueEntry.styles` / `output_format`, which are defaulted fields that keep old `queue.json` files loadable

```python
training_trigger: str = ""   # trigger word; "" = not flagged for the training set
training_status:  str = ""   # "" | "pending" | "copied" | "copy failed"

def update_training_status(self, job_id: str, status: str) -> None: ...
```

`update_training_status` mirrors `update_status`. Leave `as_table_rows` alone, since only `_queue_html` renders the table.

### 6. `app.py`: wiring

**UI**: put this in the **Settings** tab, in the right-hand `gr.Column()`, directly under `format_radio`. It goes in a new section headed "Training Set", so it sits below the Operation, Performance and Output Format controls. Stack the controls vertically, because the column is only half the page wide:

```python
                    format_radio = gr.Radio(...)   # existing
                    gr.Markdown("#### Training Set")
                    train_check   = gr.Checkbox(label="Add to training set", value=False,
                                                info=f"Copies results to {config.training_dir}\\<trigger word>")
                    trigger_box   = gr.Textbox(label="Trigger word", visible=False)
                    add_train_btn = gr.Button("Add selected to training set", size="sm", visible=False)
```

- `train_check.change`: when unchecked, return `gr.update(value="", visible=False)` for `trigger_box` and `gr.update(visible=False)` for the button. When checked, make both visible. The direct-add button only shows while the checkbox is on, which keeps one source of truth for the trigger word.
- Don't add either component to the `gallery.select` outputs or the `on_submit` outputs. That's what keeps their values across submits and image selections.

**`on_submit`**: add `train_check` and `trigger_box` as inputs. Before submitting:

```python
trigger = (trigger or "").strip() if add_to_training else ""
if add_to_training and (err := training.validate_trigger(trigger)):
    return f"\u2717 {err}", _queue_html()
```

Then set `training_trigger=trigger` and `training_status="pending" if trigger else ""` on the new `QueueEntry`. Add ` → training/<trigger>` to the success message when flagged.

**`_start_polling.poll()`**: when `status == "done"`, call `_copy_job_to_training(submitted)` **after** `queue.update_status`. It must be wrapped in a `try/except Exception` that logs the error and sets the status to `copy failed`, so a copy error can never kill the polling thread or change the job's status.

```python
def _copy_job_to_training(submitted: SubmittedJob) -> None:
    entry = queue.get_entry(submitted.job_id)
    if not entry or not entry.training_trigger:
        return
    # locate_output per path → add_to_training_set(...) with entry.positive_prompt
    # all succeeded → "copied"; none found / any exception → "copy failed" + log(...)
```

If the gallery holds several images, which is unusual since jobs produce one, copy each of them. Log every destination with the `[training]` prefix, matching the existing `[retry]` and `[startup]` log tags.

**`_do_retry`**: after `update_job_id`, if `entry.training_trigger` is set, call `queue.update_training_status(submitted.job_id, "pending")`. Startup re-queue needs no change, because the trigger is stored on the entry and `_start_polling` does the copy.

**`on_add_to_training(selected_path, positive, trigger)`**:
1. If no image is selected, return `"No image selected."`.
2. Validate the trigger.
3. Call `add_to_training_set(Path(selected_path), positive, trigger.strip(), config.training_dir)`.
4. Return `f"\u2713 Added to training set: {dest}"` or `f"\u2717 Could not add to training set: {e}"`. Output goes to `status_msg`.

**`_queue_html`**: add a "Training" column after Status. Show `f"{e.training_trigger}: {e.training_status}"` when the job is flagged, and leave it blank otherwise. Escape it with `html_module.escape`, like the other cells.

## Data Model

### State Shape (`queue.json` entry, new keys)

```json
{ "training_trigger": "mychar", "training_status": "copied" }
```

Legacy entries without these keys load as `""` and `""` through the dataclass defaults.

## Testing Requirements

### Unit Tests

| Test File | Coverage |
| --- | --- |
| `tests/test_training.py` | `derive_caption` (parametrized), `validate_trigger`, `locate_output`, `add_to_training_set` |
| `tests/test_queue_manager.py` | New fields round-trip; legacy entries default to `""`; `update_training_status` persists |
| `tests/test_fooocus_client.py` | `_gallery_image_paths` with file-dict list, `[dict, caption]` pairs, update-dict wrapper, `"path"` key, `None`, `[]` |

**Key test cases**:

- The contract's caption example produces exactly `mychar, blue eyes, red dress`
- A blank trigger and a trigger containing `/` or `:` are both rejected
- A trigger of `CON` is rejected
- A WebP source becomes a PNG of the same size, and the caption file sits next to it
- Collisions get `_1` and then `_2`. A lone existing `.txt` also forces a suffix.
- `locate_output` prefers `outputs_root/2026-10-22/2026-10-22_…png` over the temp copy, and returns `None` when neither exists

### Manual Testing

- [ ] Check the box: the trigger box and button appear. Uncheck it: the trigger is cleared and both hide.
- [ ] Submit with a blank trigger: a refusal message appears and nothing is queued.
- [ ] Submit two upscales with trigger `testchar`: both keep the trigger value. The queue shows `testchar: pending`, then `testchar: copied`. `outputs\training\testchar\` contains two PNGs and their `.txt` files. The console shows the `outputs:` log line with the real payload shape.
- [ ] Select a gallery image and click "Add selected to training set": the PNG and `.txt` appear, and the status shows the path.
- [ ] Add the same image twice: you get a `_1` file. The original is untouched.
- [ ] The gallery and Refresh don't list the `training` folder.

## Error Handling

| Error Scenario | Handling Strategy |
| --- | --- |
| Checkbox on, trigger blank or invalid | Refuse to submit or add, show the validation message in `status_msg`, queue nothing |
| Job finishes but no gallery paths were captured | Training status `copy failed`; log `[training] no output paths for job …` |
| Output file not found (real file or temp copy) | Same as above, logging the missing path |
| PIL can't open the file or the disk write fails | Catch and log the exception, set `copy failed`; the job stays `done` |
| Destination name collision | Add a `_N` suffix; never overwrite |
| Job fails or is cancelled | No copy is attempted. `training_status` stays `pending`, and a retry starts the copy again |

## Validation Commands

```bash
pytest tests/ -q
```

```bash
python app.py
```

## Rollout Considerations

- **Feature flag**: none. The feature is off until the checkbox is ticked.
- **Rollback plan**: revert the commit. Existing `queue.json` files with the new keys would fail to load on old code (`QueueEntry(**e)` would raise), and `_load` would silently start with an empty queue. Clear completed jobs before rolling back.

## Open Items

- [ ] Confirm the real fn=68 gallery payload shape from the `outputs:` log line on the first live run, and adjust `_gallery_image_paths` if it doesn't match.

---

_This spec is ready for implementation. Follow the patterns and validate at each step._
