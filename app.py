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
from PIL import Image

from config import load_config
from fooocus_client import (
    OutputFormat,
    SubmittedJob,
    UovMethod,
    PerformancePreset,
    create_client,
    get_job_status,
    log,
    submit_upscale_job,
)
from log_parser import ImageMetadata, LogParseError, parse_log
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


def _model_metadata_for(image_path: Path) -> ImageMetadata | None:
    """Look up the checkpoint/LoRA/sampling settings an image was originally
    generated with, so re-submitting it (e.g. Vary) reproduces those settings
    instead of whatever the Fooocus UI currently has selected. Returns None
    if log.html is missing or has no entry for this image — callers fall
    back to Fooocus's current UI defaults in that case.
    """
    try:
        return parse_log(image_path.parent / "log.html", image_path.name)
    except LogParseError:
        return None


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
                OutputFormat(entry.output_format),
                _model_metadata_for(image_path),
                entry.styles,
            )
            queue.update_job_id(entry.job_id, submitted.job_id)
            _start_polling(submitted)
        except Exception:
            queue.update_status(entry.job_id, "failed")


# Warm-up retry budget: Fooocus may still be loading models when this app
# starts, so keep trying the /config handshake with capped exponential backoff.
WARMUP_MAX_ELAPSED  = 300   # seconds to keep retrying (covers a slow Fooocus boot)
WARMUP_BACKOFF_CAP  = 30    # max seconds between attempts


def _startup_warmup() -> None:
    """Connect to Fooocus and re-queue interrupted jobs, off the launch path.

    Establishing the connection makes two blocking /config calls; doing it here
    in a background thread (rather than at import) lets the UI come up
    immediately even when Fooocus is slow or still booting.

    Fooocus may still be starting up when this app launches, so we retry the
    handshake with capped exponential backoff for up to WARMUP_MAX_ELAPSED
    seconds. Until the connection is live we leave interrupted jobs 'queued'
    rather than failing them, so they survive to be re-submitted once Fooocus
    answers.
    """
    deadline = time.monotonic() + WARMUP_MAX_ELAPSED
    delay    = min(2.0, WARMUP_BACKOFF_CAP)
    attempt  = 0
    while True:
        attempt += 1
        try:
            fooocus.connect()
            break
        except Exception as e:
            if time.monotonic() >= deadline:
                log(
                    f"[startup] Fooocus still unreachable after {WARMUP_MAX_ELAPSED}s "
                    f"({attempt} attempts) — skipping re-queue: {e}"
                )
                return
            log(f"[startup] Fooocus not reachable (attempt {attempt}); retrying in {delay:.0f}s: {e}")
            time.sleep(delay)
            delay = min(delay * 2, WARMUP_BACKOFF_CAP)
    _requeue_startup_jobs()


UOV_OPTIONS           = [m.value for m in UovMethod]
PERFORMANCE_OPTIONS   = [p.value for p in PerformancePreset]
OUTPUT_FORMAT_OPTIONS = [f.value for f in OutputFormat]

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
        dir_images: list[Path] = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            dir_images.extend(date_dir.glob(ext))
        # Filenames are timestamp-prefixed (YYYY-MM-DD_HH-MM-SS...), so sorting
        # by name across all extensions together yields true chronological order.
        dir_images.sort(reverse=True)
        images.extend(dir_images)
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
    display_name = _filename_with_dimensions(image_path)
    all_styles, default_styles = fooocus.style_options()
    try:
        meta = parse_log(log_path, image_path.name)
        fields = (meta.positive_prompt, meta.negative_prompt, meta.seed, meta.performance, "")
        original_styles = meta.styles
    except LogParseError as e:
        fields = ("", "", 0, PerformancePreset.SPEED.value, f"\u26a0 {e}")
        original_styles = None

    if original_styles is not None and all_styles:
        # Mirror what submit will actually send: unknown styles get dropped.
        original_styles = [s for s in original_styles if s in all_styles]
    chosen = original_styles if original_styles is not None else default_styles
    style_update = gr.update(choices=_style_choices(chosen), value=chosen)
    return (str(image_path), display_name, *fields,
            style_update, original_styles, "", _style_summary(chosen, original_styles))


def _style_choices(selected: list[str], query: str = "") -> list[str]:
    """Checkbox choices for the Style tab: the selected styles first, in
    selection order (as Fooocus itself lists them), then every other style
    Fooocus knows that matches the search box."""
    all_styles, _ = fooocus.style_options()
    q = query.strip().lower()
    return list(selected) + [s for s in all_styles if s not in selected and q in s.lower()]


def _style_summary(chosen: list[str] | None, original: list[str] | None) -> str:
    """One-line description of the styles a submit will send, and where they came from."""
    chosen = chosen or []
    if original is None and not chosen:
        return "**Styles:** Fooocus defaults *(original image's styles unknown)*"
    if original is not None:
        source = "inherited from original" if chosen == original else "overridden"
    else:
        _, defaults = fooocus.style_options()
        source = "Fooocus defaults \u2014 original unknown" if chosen == defaults else "overridden"
    names = ", ".join(chosen) if chosen else "none"
    return f"**Styles** ({len(chosen)}, {source}): {names}"


def on_style_search(query: str, chosen: list[str]):
    """Filter the unselected styles by name; selected ones always stay visible."""
    return gr.update(choices=_style_choices(chosen or [], query))


def on_style_reset(original: list[str] | None):
    """Restore the original image's styles (or Fooocus defaults if unknown)."""
    _, defaults = fooocus.style_options()
    chosen = original if original is not None else defaults
    return gr.update(choices=_style_choices(chosen), value=chosen), ""


def on_style_clear():
    """Deselect every style, so the job runs with no style templates at all."""
    return gr.update(choices=_style_choices([]), value=[]), ""


def _filename_with_dimensions(image_path: Path) -> str:
    """Return the filename suffixed with its pixel dimensions, e.g. 'foo.png (1024x1536)'."""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
        return f"{image_path.name} ({width}x{height})"
    except Exception:
        return image_path.name


def on_clear_completed():
    """Remove finished entries (done/failed/cancelled) from the queue table.

    Active jobs ('queued' / 'processing') are kept so nothing in flight is lost.
    """
    queue.clear_completed()
    return _queue_html()


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
    log(f"[retry] called: job_id={job_id!r}")
    if not job_id:
        return

    entry = queue.get_entry(job_id)
    if entry is None:
        log(f"[retry] bailing: entry not found for {job_id!r}")
        return
    if not entry.image_path:
        log(f"[retry] bailing: no image_path on entry {job_id!r}")
        return

    image_path = Path(entry.image_path)
    log(f"[retry] image_path={image_path!r}  exists={image_path.exists()}")
    if not image_path.exists():
        log(f"[retry] bailing: image not found at {image_path!r}")
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
            OutputFormat(entry.output_format),
            _model_metadata_for(image_path),
            entry.styles,
        )
        log(f"[retry] submitted OK: new job_id={submitted.job_id!r}")
        queue.update_job_id(entry.job_id, submitted.job_id)
        queue.update_status(submitted.job_id, "queued")
        _start_polling(submitted)
    except Exception:
        import traceback
        traceback.print_exc()


def on_submit(selected_path_str, positive, negative, seed, uov_method, performance, output_format,
              chosen_styles, original_styles):
    """Submit the selected image to Fooocus and add it to the queue."""
    if not selected_path_str:
        return "No image selected.", _queue_html()

    image_path = Path(selected_path_str)
    filename = image_path.name
    # An empty selection with no known original means the Style tab was never
    # populated (Fooocus unreachable at select time) — let Fooocus apply its
    # defaults rather than sending an explicit "no styles".
    styles = list(chosen_styles or [])
    if original_styles is None and not styles:
        styles = None

    try:
        submitted = submit_upscale_job(
            fooocus,
            image_path,
            UovMethod(uov_method),
            PerformancePreset(performance),
            positive,
            negative,
            int(seed),
            OutputFormat(output_format),
            _model_metadata_for(image_path),
            styles,
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
            output_format=output_format,
            styles=styles,
        )
        queue.add(entry)
        _start_polling(submitted)
        style_note = "default styles" if styles is None else f"{len(styles)} styles"
        return f"\u2713 Submitted: {filename} ({uov_method}, {performance}, {style_note})", _queue_html()
    except Exception as e:
        return f"\u2717 Submission failed: {e}", _queue_html()


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

# Connect to Fooocus and re-submit interrupted jobs in the background, so the
# UI launches immediately instead of blocking on the /config handshake.
threading.Thread(target=_startup_warmup, daemon=True).start()

# Compute startup state once at module level (pure filesystem reads)
_all_date_dirs = get_date_dirs(config.outputs_root)
_initial_loaded = min(DAYS_PER_PAGE, len(_all_date_dirs))
_initial_paths = images_for_dirs(_all_date_dirs[:_initial_loaded])

_CSS = """
.thumbnail-item.selected {
    box-shadow: inset 0 0 0 2px var(--color-accent), var(--shadow-drop) !important;
}
/* Fooocus ships ~280 styles; keep the list from pushing Submit off-screen. */
#style-checks .wrap {
    max-height: 360px;
    overflow-y: auto;
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
        height=520,
        allow_preview=False,
    )
    load_more_btn = gr.Button(
        value=_load_more_label(_initial_loaded, len(_all_date_dirs)),
        interactive=(_initial_loaded < len(_all_date_dirs)),
        size="sm",
    )
    refresh_btn = gr.Button("↻ Refresh Gallery", size="sm")

    # --- metadata + submit panel ---
    filename_display = gr.Textbox(label="Selected Image", interactive=False, value="")
    # Styles the selected image was generated with (None if unknown), for
    # "Reset to original" and the inherited/overridden summary.
    original_styles_state = gr.State(None)
    with gr.Tabs():
        with gr.Tab("Settings"):
            with gr.Row():
                with gr.Column():
                    pos_prompt = gr.Textbox(label="Positive Prompt", interactive=True, lines=3)
                    neg_prompt = gr.Textbox(label="Negative Prompt", interactive=True, lines=2)
                    seed_box = gr.Number(label="Seed", interactive=False)
                with gr.Column():
                    uov_radio = gr.Radio(UOV_OPTIONS, label="Operation", value=UovMethod.UPSCALE_2X.value)
                    perf_radio = gr.Radio(
                        PERFORMANCE_OPTIONS,
                        label="Performance",
                        value=PerformancePreset.SPEED.value,
                    )
                    format_radio = gr.Radio(
                        OUTPUT_FORMAT_OPTIONS,
                        label="Output Format",
                        value=OutputFormat.WEBP.value,
                    )
        with gr.Tab("Style"):
            gr.Markdown(
                "Selecting an image loads the styles it was generated with. "
                "Change them here to override. Styles apply in the order listed; "
                "*Fooocus V2* is prompt expansion. *Upscale (Fast 2x)* ignores styles."
            )
            with gr.Row():
                style_search = gr.Textbox(
                    placeholder="Search styles…", show_label=False, container=False, scale=4,
                )
                style_reset_btn = gr.Button("↺ Reset to original", size="sm", scale=1)
                style_clear_btn = gr.Button("Clear all", size="sm", scale=1)
            style_checks = gr.CheckboxGroup(
                choices=[], value=[], label="Selected Styles", elem_id="style-checks",
            )
    style_summary = gr.Markdown("")
    submit_btn = gr.Button("Submit for Upscaling", variant="primary")
    status_msg = gr.Markdown("")

    gr.Markdown("### Queue")
    clear_completed_btn = gr.Button("🗑 Clear Completed", size="sm")
    queue_table = gr.HTML(value=_queue_html())
    # Invisible textbox used only to attach the queue_action API endpoint.
    # Cancel/Retry buttons call it directly via fetch; Gradio's DOM event
    # system is not used.
    _action_relay = gr.Textbox(visible=False)

    # Refresh queue every 3 s to reflect background polling updates
    gr.Timer(3).tick(fn=_queue_html, outputs=queue_table)

    # "Clear Completed" — drop finished entries, keep active jobs
    clear_completed_btn.click(fn=on_clear_completed, outputs=queue_table)

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
        outputs=[selected_path, filename_display, pos_prompt, neg_prompt, seed_box, perf_radio, status_msg,
                 style_checks, original_styles_state, style_search, style_summary],
    )

    # --- Style tab ---
    style_checks.change(
        fn=_style_summary,
        inputs=[style_checks, original_styles_state],
        outputs=style_summary,
    )
    style_search.change(
        fn=on_style_search,
        inputs=[style_search, style_checks],
        outputs=style_checks,
    )
    style_reset_btn.click(fn=on_style_reset, inputs=[original_styles_state], outputs=[style_checks, style_search])
    style_clear_btn.click(fn=on_style_clear, outputs=[style_checks, style_search])

    submit_btn.click(
        fn=on_submit,
        inputs=[selected_path, pos_prompt, neg_prompt, seed_box, uov_radio, perf_radio, format_radio,
                style_checks, original_styles_state],
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
