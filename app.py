"""
Fooocus Upscale Queue — Gradio companion app.

Run with:  python app.py
Then open:  http://localhost:7860
"""
from __future__ import annotations

import html as html_module
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr

from config import load_config
from fooocus_client import (
    SubmittedJob,
    UovMethod,
    PerformancePreset,
    create_client,
    get_job_status,
    submit_upscale_job,
)
from log_parser import LogParseError, parse_log
from queue_manager import QueueEntry, QueueManager

# ---------------------------------------------------------------------------
# App-level singletons
# ---------------------------------------------------------------------------

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAYS_PER_PAGE = 3  # how many date-dirs to load initially and per "load more" click

config = load_config()
fooocus = create_client(config.fooocus_url)
queue = QueueManager(config.queue_file)

# Maps job_id → live SubmittedJob so on_cancel can reach them.
_active_jobs: dict[str, SubmittedJob] = {}


def _requeue_startup_jobs() -> None:
    """Re-submit any jobs that were still queued when the app last shut down."""
    for entry in queue.requeue_candidates():
        image_path = Path(entry.image_path)
        if not image_path.exists():
            queue.update_status(entry.job_id, "failed")
            continue
        try:
            submitted = submit_upscale_job(
                fooocus,
                image_path,
                UovMethod(entry.uov_method),
                PerformancePreset(entry.performance),
                entry.positive_prompt,
                entry.negative_prompt,
                entry.seed,
            )
            queue.update_job_id(entry.job_id, submitted.job_id)
            _start_polling(submitted)
        except Exception:
            queue.update_status(entry.job_id, "failed")

UOV_OPTIONS         = [m.value for m in UovMethod]
PERFORMANCE_OPTIONS = [p.value for p in PerformancePreset]

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_date_dirs(outputs_root: Path) -> list[Path]:
    """Return all YYYY-MM-DD subdirs sorted newest first."""
    return sorted(
        [d for d in outputs_root.iterdir() if d.is_dir() and DATE_DIR_RE.match(d.name)],
        reverse=True,
    )


def images_for_dirs(date_dirs: list[Path]) -> list[str]:
    """Return image paths from the given date dirs, newest first within each dir."""
    images: list[Path] = []
    for date_dir in date_dirs:
        for ext in ("*.png", "*.jpg", "*.webp"):
            images.extend(sorted(date_dir.glob(ext), reverse=True))
    return [str(p) for p in images]


def _load_more_label(loaded: int, total: int) -> str:
    remaining = total - loaded
    if remaining <= 0:
        return "All days loaded"
    return f"Load {DAYS_PER_PAGE} more days ({remaining} remaining)"


def _start_polling(submitted: SubmittedJob) -> None:
    """Background thread: poll job status every 2 s until terminal."""
    _active_jobs[submitted.job_id] = submitted

    def poll() -> None:
        while True:
            time.sleep(2)
            status = get_job_status(submitted)
            queue.update_status(submitted.job_id, status)
            if status in ("done", "failed", "cancelled"):
                _active_jobs.pop(submitted.job_id, None)
                break

    threading.Thread(target=poll, daemon=True).start()


def _action_js(action: str, job_id: str) -> str:
    """Return onclick JS that calls the Gradio queue_action API directly via fetch.

    This bypasses Gradio's component event system (which does not respond to
    synthetic DOM events) and posts straight to the HTTP API instead.
    The queue timer picks up any state changes within 3 s.
    """
    value = f"{action}:{job_id}"   # e.g. "cancel:abc-123" or "retry:abc-123"
    return (
        f"(function(){{"
        f"fetch('/gradio_api/call/queue_action',"
        f"{{method:'POST',"
        f"headers:{{'Content-Type':'application/json'}},"
        f"body:JSON.stringify({{data:['{value}']}})}}"
        f").then(function(r){{return r.json();}}).then(function(d){{"
        f"if(d&&d.event_id){{"
        f"var es=new EventSource('/gradio_api/call/queue_action/'+d.event_id);"
        f"es.onmessage=function(e){{if(e.data!='HEARTBEAT'){{es.close();}}}};"
        f"es.onerror=function(){{es.close();}};"
        f"}}}}).catch(function(e){{console.error('[action] fetch failed',e);}});"
        f"}})()"
    )


def _queue_html() -> str:
    """Render the queue as an HTML table with per-row action buttons."""
    entries = list(reversed(queue.entries))
    if not entries:
        return "<p style='color:var(--body-text-color-subdued,#888);margin:8px 0;'>Queue is empty.</p>"

    rows = ""
    for e in entries:
        action_cell = ""
        if e.status == "queued":
            action_cell = (
                f"<button onclick=\"{_action_js('cancel', e.job_id)}\" "
                f"style='font-size:0.8em;padding:2px 8px;cursor:pointer;'>Cancel</button>"
            )
        elif e.status == "failed" and e.image_path:
            action_cell = (
                f"<button onclick=\"{_action_js('retry', e.job_id)}\" "
                f"style='font-size:0.8em;padding:2px 8px;cursor:pointer;'>Retry</button>"
            )
        rows += (
            "<tr>"
            f"<td style='padding:4px 8px;'>{html_module.escape(e.image_filename)}</td>"
            f"<td style='padding:4px 8px;'>{html_module.escape(e.uov_method)}</td>"
            f"<td style='padding:4px 8px;'>{html_module.escape(e.performance)}</td>"
            f"<td style='padding:4px 8px;'>{html_module.escape(e.status)}</td>"
            f"<td style='padding:4px 8px;white-space:nowrap;'>{html_module.escape(e.submitted_at)}</td>"
            f"<td style='padding:4px 8px;'>{action_cell}</td>"
            "</tr>"
        )

    header = (
        "<tr style='border-bottom:1px solid var(--border-color-primary,#ddd);'>"
        "<th style='text-align:left;padding:4px 8px;'>Image</th>"
        "<th style='text-align:left;padding:4px 8px;'>Operation</th>"
        "<th style='text-align:left;padding:4px 8px;'>Performance</th>"
        "<th style='text-align:left;padding:4px 8px;'>Status</th>"
        "<th style='text-align:left;padding:4px 8px;'>Submitted</th>"
        "<th></th>"
        "</tr>"
    )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:0.9em;'>"
        f"<thead>{header}</thead><tbody>{rows}</tbody>"
        "</table>"
    )


# ---------------------------------------------------------------------------
# Gradio event handlers
# ---------------------------------------------------------------------------


def on_load_more(all_dirs: list, loaded_count: int):
    """Load the next batch of date dirs into the gallery."""
    new_count = min(loaded_count + DAYS_PER_PAGE, len(all_dirs))
    paths = images_for_dirs(all_dirs[:new_count])
    btn = gr.update(
        value=_load_more_label(new_count, len(all_dirs)),
        interactive=(new_count < len(all_dirs)),
    )
    return paths, paths, new_count, btn


def on_refresh_gallery():
    """Re-scan the outputs folder and reset the gallery to the first page."""
    new_all_dirs = get_date_dirs(config.outputs_root)
    new_loaded   = min(DAYS_PER_PAGE, len(new_all_dirs))
    new_paths    = images_for_dirs(new_all_dirs[:new_loaded])
    btn          = gr.update(
        value=_load_more_label(new_loaded, len(new_all_dirs)),
        interactive=(new_loaded < len(new_all_dirs)),
    )
    return new_paths, new_paths, new_all_dirs, new_loaded, btn


def on_image_select(evt: gr.SelectData, original_paths: list):
    """Populate metadata fields when an image is clicked in the gallery.

    We index into `original_paths` (a gr.State holding the real filesystem
    paths) rather than the gallery component's value, which Gradio may have
    replaced with temp-directory copies.
    """
    image_path = Path(original_paths[evt.index])
    log_path = image_path.parent / "log.html"
    try:
        meta = parse_log(log_path, image_path.name)
        return str(image_path), meta.positive_prompt, meta.negative_prompt, meta.seed, meta.performance, ""
    except LogParseError as e:
        return str(image_path), "", "", 0, PerformancePreset.SPEED.value, f"\u26a0 {e}"


def on_action(value: str):
    """Unified handler for Cancel and Retry buttons, called via the Gradio HTTP API.

    Returns an empty string (the single output is the invisible relay textbox).
    The queue table is refreshed by the 3-second timer, not by this handler.
    """
    value = (value or "").strip()
    if not value:
        return ""

    if value.startswith("retry:"):
        _do_retry(value[len("retry:"):])
    elif value.startswith("cancel:"):
        _do_cancel(value[len("cancel:"):])
    return ""


def _do_cancel(job_id: str):
    """Cancel a queued job."""
    job_id = job_id.strip()
    if job_id:
        job = _active_jobs.get(job_id)
        if job:
            job.cancel()
        queue.update_status(job_id, "cancelled")


def _do_retry(job_id: str):
    """Re-submit a failed job."""
    job_id = job_id.strip()
    print(f"[retry] called: job_id={job_id!r}")
    if not job_id:
        return

    entry = queue.get_entry(job_id)
    if entry is None:
        print(f"[retry] bailing: entry not found for {job_id!r}")
        return
    if not entry.image_path:
        print(f"[retry] bailing: no image_path on entry {job_id!r}")
        return

    image_path = Path(entry.image_path)
    print(f"[retry] image_path={image_path!r}  exists={image_path.exists()}")
    if not image_path.exists():
        print(f"[retry] bailing: image not found at {image_path!r}")
        return

    try:
        submitted = submit_upscale_job(
            fooocus,
            image_path,
            UovMethod(entry.uov_method),
            PerformancePreset(entry.performance),
            entry.positive_prompt,
            entry.negative_prompt,
            entry.seed,
        )
        print(f"[retry] submitted OK: new job_id={submitted.job_id!r}")
        queue.update_job_id(entry.job_id, submitted.job_id)
        queue.update_status(submitted.job_id, "queued")
        _start_polling(submitted)
    except Exception:
        import traceback
        traceback.print_exc()


def on_submit(selected_path_str, positive, negative, seed, uov_method, performance):
    """Submit the selected image to Fooocus and add it to the queue."""
    if not selected_path_str:
        return "No image selected.", _queue_html()

    image_path = Path(selected_path_str)
    filename = image_path.name

    try:
        submitted = submit_upscale_job(
            fooocus,
            image_path,
            UovMethod(uov_method),
            PerformancePreset(performance),
            positive,
            negative,
            int(seed),
        )
        entry = QueueEntry(
            job_id=submitted.job_id,
            image_filename=filename,
            uov_method=uov_method,
            performance=performance,
            positive_prompt=positive,
            negative_prompt=negative,
            seed=int(seed),
            status="queued",
            submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            image_path=str(image_path),
        )
        queue.add(entry)
        _start_polling(submitted)
        return f"\u2713 Submitted: {filename} ({uov_method}, {performance})", _queue_html()
    except Exception as e:
        return f"\u2717 Submission failed: {e}", _queue_html()


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

# Re-submit jobs that were still queued when the app last shut down
_requeue_startup_jobs()

# Compute startup state once at module level (pure filesystem reads)
_all_date_dirs = get_date_dirs(config.outputs_root)
_initial_loaded = min(DAYS_PER_PAGE, len(_all_date_dirs))
_initial_paths = images_for_dirs(_all_date_dirs[:_initial_loaded])

_CSS = """
.thumbnail-item.selected {
    box-shadow: inset 0 0 0 2px var(--color-accent), var(--shadow-drop) !important;
}
"""

with gr.Blocks(title="Fooocus Upscale Queue") as demo:
    gr.Markdown("## Fooocus Upscale Queue")

    # --- hidden state ---
    # All available date-dirs (list[Path]); never changes after startup.
    all_dirs_state = gr.State(_all_date_dirs)
    # How many date-dirs are currently shown in the gallery.
    loaded_days_state = gr.State(_initial_loaded)
    # Original filesystem paths for the currently-visible gallery images.
    gallery_paths = gr.State(_initial_paths)
    # Full original path of the currently-selected image.
    selected_path = gr.State("")

    # --- gallery + load-more ---
    gallery = gr.Gallery(
        value=_initial_paths,
        label="Output Images",
        columns=4,
        height=400,
        allow_preview=False,
    )
    load_more_btn = gr.Button(
        value=_load_more_label(_initial_loaded, len(_all_date_dirs)),
        interactive=(_initial_loaded < len(_all_date_dirs)),
        size="sm",
    )
    refresh_btn = gr.Button("↻ Refresh Gallery", size="sm")

    # --- metadata + submit panel ---
    with gr.Row():
        with gr.Column():
            pos_prompt = gr.Textbox(label="Positive Prompt", interactive=True, lines=3)
            neg_prompt = gr.Textbox(label="Negative Prompt", interactive=True, lines=2)
            seed_box = gr.Number(label="Seed", interactive=False)
        with gr.Column():
            uov_radio = gr.Radio(UOV_OPTIONS, label="Operation", value=UOV_OPTIONS[0])
            perf_radio = gr.Radio(
                PERFORMANCE_OPTIONS,
                label="Performance",
                value=PerformancePreset.SPEED.value,
            )
            submit_btn = gr.Button("Submit for Upscaling", variant="primary")
            status_msg = gr.Markdown("")

    gr.Markdown("### Queue")
    queue_table = gr.HTML(value=_queue_html())
    # Invisible textbox used only to attach the queue_action API endpoint.
    # Cancel/Retry buttons call it directly via fetch; Gradio's DOM event
    # system is not used.
    _action_relay = gr.Textbox(visible=False)

    # Refresh queue every 3 s to reflect background polling updates
    gr.Timer(3).tick(fn=_queue_html, outputs=queue_table)

    # "Load more days" — appends next DAYS_PER_PAGE dirs to gallery + state
    load_more_btn.click(
        fn=on_load_more,
        inputs=[all_dirs_state, loaded_days_state],
        outputs=[gallery, gallery_paths, loaded_days_state, load_more_btn],
    )

    # "Refresh Gallery" — re-scans outputs folder and resets to first page
    refresh_btn.click(
        fn=on_refresh_gallery,
        inputs=[],
        outputs=[gallery, gallery_paths, all_dirs_state, loaded_days_state, load_more_btn],
    )

    gallery.select(
        fn=on_image_select,
        inputs=[gallery_paths],          # real paths, not gallery's temp copies
        outputs=[selected_path, pos_prompt, neg_prompt, seed_box, perf_radio, status_msg],
    )

    submit_btn.click(
        fn=on_submit,
        inputs=[selected_path, pos_prompt, neg_prompt, seed_box, uov_radio, perf_radio],
        outputs=[status_msg, queue_table],
    )

    # Expose on_action via the Gradio HTTP API so the HTML buttons can call it
    # with a bare fetch() rather than relying on synthetic DOM events.
    _action_relay.input(
        fn=on_action,
        inputs=[_action_relay],
        outputs=[_action_relay],
        api_name="queue_action",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fooocus Upscale Queue")
    parser.add_argument(
        "--listen",
        nargs="?",
        const="0.0.0.0",
        default=None,
        metavar="IP",
        help="IP address to listen on (default: 0.0.0.0 if flag present, 127.0.0.1 if omitted)",
    )
    args = parser.parse_args()

    server_name = args.listen if args.listen is not None else "127.0.0.1"
    demo.launch(allowed_paths=[str(config.outputs_root)], css=_CSS, server_name=server_name)
