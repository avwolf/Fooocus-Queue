# Fooocus Upscale Queue Contract

**Created**: 2026-03-04
**Confidence Score**: 88/100
**Status**: Draft

## Problem Statement

When Fooocus generates images that are worth upscaling, the current workflow is manual and friction-heavy: navigate to the output directory, identify the image, open log.html to locate the associated prompts and seed, re-enter those values in the Fooocus UI, then submit the upscale job. This friction discourages iterative upscaling and makes it easy to re-enter the wrong parameters.

A companion Gradio UI that lives alongside a running Fooocus instance can eliminate this friction entirely: browse the output gallery, click an image, get its metadata pre-populated, choose the operation type, and submit — all in one place.

## Goals

1. Provide a Gradio gallery browser that displays images from all date-organized Fooocus output subdirectories.
2. Automatically extract positive prompt, negative prompt, and seed from the `log.html` file in the same date directory as the selected image.
3. Allow the user to select a Vary (Subtle / Strong) or Upscale (1.5x / 2x / 4x) operation type before submitting.
4. Submit the selected image with its extracted metadata to the running Fooocus Gradio API at `localhost:7865`.
5. Maintain a persistent queue listing all submitted jobs with real-time status updates, saved to disk and reloaded on next startup.

## Success Criteria

- [ ] Gallery loads and displays images from all `YYYY-MM-DD` subdirectories under the configured Fooocus outputs root
- [ ] Clicking an image populates the positive prompt, negative prompt, and seed fields from the corresponding `log.html`
- [ ] All Vary/Upscale mode options (Subtle, Strong, 1.5x, 2x, 4x) are selectable before submission
- [ ] Submitting calls the Fooocus Gradio API and receives a successful job response
- [ ] A success message is displayed after each submission
- [ ] Each submitted job (image filename + mode + status) appears in the queue panel
- [ ] Queue item status updates in real time as Fooocus processes the job (queued → processing → done / failed)
- [ ] The queue is persisted to a `queue.json` file and reloaded on next startup so work-in-progress is not lost
- [ ] Appropriate error messages shown when: `log.html` is missing, metadata cannot be parsed, Fooocus connection fails

## Scope Boundaries

### In Scope

- Python Gradio application (`app.py`) runnable independently from Fooocus
- Image gallery built from date-organized subdirectories under a configurable outputs root path
- HTML parsing of Fooocus `log.html` to extract positive prompt, negative prompt, and seed for the selected image
- Vary/Upscale operation type selector (Vary Subtle, Vary Strong, Upscale 1.5x, Upscale 2x, Upscale 4x)
- Fooocus Gradio API client integration targeting `localhost:7865`
- Queue panel showing submitted jobs (image name + mode + status), persisted to `queue.json` and reloaded on startup
- Real-time progress polling of Fooocus API after each job submission, updating queue item status (queued → processing → done / failed)
- Configurable outputs root directory (environment variable or `.env` file)
- Discovery of the exact `log.html` HTML structure and Fooocus Gradio API endpoint during implementation

### Out of Scope

- Batch/multi-image selection and simultaneous submission — adds UI complexity, defer to a future phase
- Re-prompting or editing extracted metadata before submission — out of stated scope
- Authentication or remote Fooocus access (non-localhost) — not needed for stated use case
- Generating new images — this tool is an upscaling companion only

### Future Considerations

- Batch multi-image selection with shared operation type
- Progress polling via Fooocus API to show job completion status in the queue
- Configurable Fooocus URL (for remote instances)
- Batch multi-image selection with shared operation type

## Execution Plan

_Pick up this contract cold and know exactly how to execute._

### Dependency Graph

```
spec.md  (single phase — no dependencies)
```

### Execution Steps

**Strategy**: Sequential (single phase — small enough scope for one session)

1. **Resolve Open Items first** — before starting implementation, do the two discovery tasks:
   - Copy a real `log.html` from your Fooocus outputs to `tests/fixtures/sample_log.html` and inspect its structure
   - Run `python scripts/discover_api.py` against a live Fooocus instance to find the Gradio `api_name` and parameter list

2. **Implement** — start a new Claude Code session and run:
   ```bash
   /execute-spec docs/ideation/fooocus-upscale-queue/spec.md
   ```

3. **Implement in module order**: `config.py` → `log_parser.py` → `queue_manager.py` → `fooocus_client.py` → `app.py`

---

_This contract was generated from brain dump input. Review and approve before proceeding to specification._
