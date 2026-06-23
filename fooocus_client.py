"""
Fooocus Gradio client — submits upscale/vary jobs via the Gradio 3.x queue.

Fooocus runs Gradio 3.41.2.  Its generate button (component id=15) has a
chained event sequence that must be followed exactly:

  [65] click  0  inputs → UI setup (button states, clear gallery)
  [66] then   2  inputs → seed/text update (Random checkbox, Seed textbox)
  [67] then   141 inputs → start generation, outputs state
  [68] then   1  input  → poll for results: html, preview, finished, gallery

We must call fn_index=65 first to initialise the session, fn_index=66 to
fire the seed-update step, then fn_index=67 to start actual generation, then
repeatedly call fn_index=68 until the "Finished Images" gallery is non-empty.

Submission flow
---------------
1. Encode image as base64 data URI
2. WebSocket /queue/join fn_index=65  — session initialisation
3. WebSocket /queue/join fn_index=66  — seed/text update (required chain step)
4. WebSocket /queue/join fn_index=67  — enqueue generation task
5. WebSocket /queue/join fn_index=68  — poll every 5 s until done
All steps run in a single daemon thread.

Image data format
-----------------
Fooocus's gradio_hijack.py overrides Image.preprocess: it asserts isinstance(x, str)
then immediately calls decode_base64_to_image(x), which splits on "," and decodes
the second part.  We therefore send a full "data:<mime>;base64,<data>" URI.

Data array for fn_index=67
---------------------------
The fixed pre-LoRA prefix is:
  index 0  : internal Gradio state component (None)
  index 1  : Generate Image Grid checkbox
  index 2  : positive prompt
  index 3  : negative prompt
  index 5  : Performance radio (overridden to user's choice)
  index 7  : Image Number slider (overridden to 1)
  index 9  : seed (as str)

After the fixed prefix come `default_max_lora_number * 3` LoRA slots
(enabled, filename, weight), so the absolute positions of the UOV block
depend on the user's Fooocus config. We locate the UOV method radio by
searching for the component whose choices include "Upscale (2x)", then
the four UOV-block indices are:
  uov_index - 2 : Input Image tab enabled (True)
  uov_index - 1 : sub-tab selector ("uov")
  uov_index     : UOV method string
  uov_index + 1 : image as "data:<mime>;base64,<data>" URI string

The Output Format radio (choices png/jpeg/webp) is likewise located by
searching component choices, since its position also shifts with the
LoRA slot count.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
import uuid
import urllib.request
from datetime import datetime
from enum import Enum
from pathlib import Path

import websockets
import websockets.exceptions

from log_parser import ImageMetadata


def log(message: str) -> None:
    """Print with a wall-clock timestamp, so console output can be correlated
    with queue.json submitted_at times and output file timestamps."""
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}")


_POLL_INTERVAL  = 5     # seconds between fn_index=68 polls
_POLL_MAX       = 720   # 60 minutes max (720 × 5 s) — Fooocus has a single-worker queue
                         # shared with manual UI use, so a queued job can sit waiting its
                         # turn behind interactive generations for a long time before its
                         # own fn=67/fn=68 calls even start running.
_HEARTBEAT_EVERY = 12   # log a heartbeat line every _HEARTBEAT_EVERY polls (~1 min)
# Outer wait_for cap on the whole generation chain. Needs generous headroom beyond
# _POLL_MAX * _POLL_INTERVAL (3600s): every poll opens its own WebSocket, so connect/
# handshake overhead and any connection-error retries (each up to _OPEN_TIMEOUT) eat
# into the budget without advancing poll_n's "done" check. A tight buffer here causes
# this outer timeout to fire — and the job to be marked failed — before the polling
# loop's own attempt limit is reached, even though Fooocus may still be working.
# fn=65/66/67 can also block for a long time waiting their turn behind manual UI use
# on Fooocus's single-worker queue, before polling even starts — that time isn't
# covered by _POLL_MAX at all, hence the large buffer below.
_RUN_TIMEOUT   = 4500  # 75 min: ~15 min buffer over the 60 min poll budget
_OPEN_TIMEOUT  = 30
_CLOSE_TIMEOUT = 10

# Only one job may execute the Fooocus generation chain at a time.
# Fooocus is single-GPU; concurrent chains produce stale state and broken polling.
_fooocus_semaphore = threading.Semaphore(1)


class UovMethod(str, Enum):
    VARY_SUBTLE     = "Vary (Subtle)"
    VARY_STRONG     = "Vary (Strong)"
    UPSCALE_1_5X    = "Upscale (1.5x)"
    UPSCALE_2X      = "Upscale (2x)"
    UPSCALE_FAST_2X = "Upscale (Fast 2x)"

    def __str__(self) -> str:
        return self.value


class OutputFormat(str, Enum):
    PNG  = "png"
    JPEG = "jpeg"
    WEBP = "webp"

    def __str__(self) -> str:
        return self.value


class PerformancePreset(str, Enum):
    SPEED         = "Speed"
    QUALITY       = "Quality"
    EXTREME_SPEED = "Extreme Speed"
    LIGHTNING     = "Lightning"
    HYPER_SD      = "Hyper-SD"

    def __str__(self) -> str:
        return self.value


class SubmittedJob:
    """
    Tracks a Fooocus generation job.

    A daemon thread walks the generate-button event chain:
        fn_index=65 → fn_index=66 → fn_index=67 → poll fn_index=68 until done.
    """

    def __init__(self, job_id: str, url: str, args: list, args66: list) -> None:
        self.job_id        = job_id
        self._status       = "queued"      # becomes "processing" once semaphore is acquired
        self._url          = url
        self._args         = args
        self._args66       = args66
        self._cancel_event = threading.Event()
        self._start_thread()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_fn(
        self, ws_url: str, session_hash: str, fn_index: int, data: list
    ) -> list | None:
        """Submit one fn_index event and return its output data list."""
        async with websockets.connect(
            f"{ws_url}/queue/join",
            open_timeout=_OPEN_TIMEOUT,
            close_timeout=_CLOSE_TIMEOUT,
        ) as ws:
            async for raw in ws:
                msg   = json.loads(raw)
                mtype = msg.get("msg")

                if mtype == "send_hash":
                    await ws.send(json.dumps({
                        "session_hash": session_hash,
                        "fn_index":     fn_index,
                    }))
                elif mtype == "send_data":
                    await ws.send(json.dumps({
                        "session_hash": session_hash,
                        "fn_index":     fn_index,
                        "data":         data,
                    }))
                elif mtype == "process_completed":
                    return msg.get("output", {}).get("data", [])
                elif mtype == "queue_full":
                    return None
        return None

    async def _run_async(self) -> None:
        ws_url       = self._url.replace("http://", "ws://")
        session_hash = uuid.uuid4().hex[:12]
        tag          = f"[job {self.job_id[:8]}]"
        t_start      = time.monotonic()

        def elapsed() -> float:
            return time.monotonic() - t_start

        # 1. fn_index=65: session init (Generate button click, 0 inputs)
        out65 = await self._call_fn(ws_url, session_hash, 65, [])
        if out65 is None:
            log(f"{tag} fn=65 returned None (queue_full or ws closed) — aborting")
            return
        log(f"{tag} fn=65 ok ({elapsed():.0f}s elapsed)")

        # 2. fn_index=66: seed/text update (2 inputs: Random checkbox, Seed)
        #    Required chain step — ignore the result; non-fatal if it fails.
        await self._call_fn(ws_url, session_hash, 66, self._args66)
        log(f"{tag} fn=66 ok ({elapsed():.0f}s elapsed)")

        # 3. fn_index=67: start generation (141 inputs → state). Fooocus runs a
        # single-worker queue shared with manual UI use, so this call can block for
        # a long time waiting its turn behind interactive generations before it
        # even starts — that wait is silent (no message) until it returns.
        out67 = await self._call_fn(ws_url, session_hash, 67, self._args)
        if out67 is None:
            log(f"{tag} fn=67 returned None (queue_full or ws closed) — aborting")
            return
        log(f"{tag} fn=67 ok ({elapsed():.0f}s elapsed) — generation started, polling for results")
        state67 = out67[0] if out67 else None

        # 4. fn_index=68: poll until Finished Images gallery is non-empty
        #    outputs: [html, preview_image, finished_gallery, all_gallery]
        none_streak = 0
        for poll_n in range(_POLL_MAX):
            try:
                out68 = await self._call_fn(ws_url, session_hash, 68, [state67])
            except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
                # Transient WebSocket failure (dropped connection, handshake timeout
                # while Fooocus is busy generating, etc.) — treat like queue_full and
                # retry rather than letting it kill a job that may be minutes from done.
                log(f"{tag} fn=68 poll {poll_n+1}/{_POLL_MAX}: connection error ({e}) — retrying")
                out68 = None
            if out68 is None:
                # Transient WebSocket failure — sleep and retry
                none_streak += 1
                if none_streak in (1, 5, 20) or none_streak % 60 == 0:
                    log(f"{tag} fn=68 poll {poll_n+1}/{_POLL_MAX}: None (streak={none_streak})")
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            none_streak = 0
            if (poll_n + 1) % _HEARTBEAT_EVERY == 0:
                log(f"{tag} fn=68 poll {poll_n+1}/{_POLL_MAX}: still waiting ({elapsed():.0f}s elapsed)")
            finished = out68[2] if len(out68) > 2 else None
            gallery  = out68[3] if len(out68) > 3 else None
            if _gallery_has_images(finished) or _gallery_has_images(gallery):
                self._status = "done"
                log(f"{tag} done after {poll_n+1} polls ({elapsed():.0f}s elapsed)")
                return
            await asyncio.sleep(_POLL_INTERVAL)
        log(f"{tag} exhausted {_POLL_MAX} polls without seeing images — will be marked failed")

    def _start_thread(self) -> None:
        def run() -> None:
            # Poll for the semaphore in short bursts so cancel can interrupt the wait.
            acquired = False
            while not self._cancel_event.is_set():
                if _fooocus_semaphore.acquire(timeout=0.5):
                    acquired = True
                    break

            # If cancelled while waiting, or cancel raced with acquire, bail out.
            if not acquired or self._cancel_event.is_set():
                if acquired:
                    _fooocus_semaphore.release()
                return

            self._status = "processing"    # now actively using Fooocus
            tag  = f"[job {self.job_id[:8]}]"
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    asyncio.wait_for(self._run_async(), timeout=_RUN_TIMEOUT)
                )
            except asyncio.TimeoutError:
                log(f"{tag} outer wait_for timed out after {_RUN_TIMEOUT}s — Fooocus may still be working")
            except Exception as e:
                log(f"{tag} unexpected exception: {type(e).__name__}: {e}")
            finally:
                if self._status == "processing":
                    log(f"{tag} marking failed (status was still 'processing' after _run_async exited)")
                    self._status = "failed"
                _fooocus_semaphore.release()  # allow next queued job to proceed
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def cancel(self) -> None:
        """Request cancellation. Only effective while status is 'queued'."""
        self._status = "cancelled"
        self._cancel_event.set()

    def get_status(self) -> str:
        return self._status


class FoocusConnection:
    """Holds the fn_index=67 UI defaults and handles job submission."""

    def __init__(self, fooocus_url: str) -> None:
        self._url      = fooocus_url.rstrip("/")
        # Fetch /config once and reuse it for both the fn67 and fn66 lookups;
        # the payload is large, so a single download halves the handshake cost.
        config = _fetch_config(self._url)
        self._defaults, self._uov_index, self._format_index, self._model_indices = \
            _fetch_fn67_defaults(config)
        self._args66   = _fetch_fn66_defaults(config)

    def _encode_image(self, image_path: Path) -> str:
        """
        Return a base64 data URI for the image.

        Fooocus's gradio_hijack.py preprocess asserts isinstance(x, str) and
        calls decode_base64_to_image(x), which splits on "," and decodes the
        second part.  A plain file path fails that decode.
        """
        suffix = image_path.suffix.lower()
        mime   = {".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
        data   = image_path.read_bytes()
        b64    = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def submit(
        self,
        image_path:      Path,
        uov_method:      UovMethod,
        performance:     PerformancePreset,
        positive_prompt: str,
        negative_prompt: str,
        seed:            int,
        output_format:   OutputFormat = OutputFormat.PNG,
        model_metadata:  ImageMetadata | None = None,
    ) -> SubmittedJob:
        file_data = self._encode_image(image_path)

        # State at [0], regular params follow. UOV block position is dynamic
        # because it sits after default_max_lora_number * 3 LoRA slots.
        args = list(self._defaults)
        uov = self._uov_index
        args[2]      = positive_prompt
        args[3]      = negative_prompt
        args[5]      = str(performance)  # Performance radio
        args[7]      = 1                 # Image Number slider — generate exactly 1
        args[9]      = str(seed)
        args[self._format_index] = str(output_format)  # Output Format radio
        args[uov - 2] = True             # Input Image tab enabled
        args[uov - 1] = "uov"            # sub-tab selector
        args[uov]     = str(uov_method)  # Upscale or Variation radio
        args[uov + 1] = file_data        # base64 data URI

        if model_metadata is not None:
            _apply_model_metadata(args, self._model_indices, model_metadata)

        return SubmittedJob(
            job_id=str(uuid.uuid4()),
            url=self._url,
            args=args,
            args66=self._args66,
        )


class LazyFoocusConnection:
    """Defers the Fooocus /config handshake until the connection is first used.

    create_client() returns this immediately so the app can build and launch
    its UI without blocking on Fooocus (which may be slow to respond, or still
    booting).  The real FoocusConnection — two blocking GETs to /config — is
    built on the first submit(), or eagerly via connect() from a background
    warm-up thread.
    """

    def __init__(self, fooocus_url: str) -> None:
        self._url  = fooocus_url
        self._lock = threading.Lock()
        self._conn: FoocusConnection | None = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    def connect(self) -> FoocusConnection:
        """Build the real connection if not already built, and return it.

        Thread-safe: concurrent callers (e.g. the warm-up thread and a user's
        first submit) share a single FoocusConnection.
        """
        with self._lock:
            if self._conn is None:
                self._conn = FoocusConnection(self._url)
            return self._conn

    def submit(self, *args, **kwargs) -> SubmittedJob:
        return self.connect().submit(*args, **kwargs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_config(fooocus_url: str) -> dict:
    """Download and parse Fooocus's /config (the full UI component tree)."""
    raw = urllib.request.urlopen(f"{fooocus_url}/config").read()
    return json.loads(raw)


def _fetch_fn66_defaults(config: dict) -> list:
    """
    Given a parsed Fooocus /config, return the 2 default input values for
    fn_index=66 (seed/text update step).  Inputs are the Random checkbox and
    Seed textbox.
    """
    comps  = {c["id"]: c for c in config.get("components", [])}
    dep66  = config["dependencies"][66]
    return [
        comps.get(cid, {}).get("props", {}).get("value")
        for cid in dep66["inputs"]
    ]


def _fetch_fn67_defaults(config: dict) -> tuple[list, int, int, dict]:
    """
    Given a parsed Fooocus /config, return:
      (defaults, uov_index, format_index, model_indices)

    `defaults` is the list of default values for every fn_index=67 input
    component (index 0 is the Gradio state).  `uov_index` is the position
    of the UOV method radio and `format_index` the position of the Output
    Format radio in that list — needed because the number of LoRA slots in
    front of them varies with `default_max_lora_number`. `model_indices` is
    a dict of positions (by label, see _locate_model_indices) used to
    override the checkpoint/LoRA/sampling settings with those an original
    image was generated with, so "Vary" reproduces it instead of using
    whatever models are currently selected in the Fooocus UI.
    """
    comps  = {c["id"]: c for c in config.get("components", [])}
    dep67  = config["dependencies"][67]
    input_ids = dep67["inputs"]
    defaults  = [
        comps.get(cid, {}).get("props", {}).get("value")
        for cid in input_ids
    ]

    uov_index = None
    format_index = None
    for i, cid in enumerate(input_ids):
        choices = comps.get(cid, {}).get("props", {}).get("choices") or []
        flat = [c[0] if isinstance(c, (list, tuple)) else c for c in choices]
        if "Upscale (2x)" in flat and "Vary (Subtle)" in flat:
            uov_index = i
        elif "png" in flat and "webp" in flat:
            format_index = i
        if uov_index is not None and format_index is not None:
            break
    if uov_index is None:
        raise RuntimeError(
            "Could not locate UOV method radio in Fooocus fn_index=67 inputs — "
            "Fooocus UI may have changed."
        )
    if format_index is None:
        raise RuntimeError(
            "Could not locate Output Format radio in Fooocus fn_index=67 inputs — "
            "Fooocus UI may have changed."
        )

    model_indices = _locate_model_indices(comps, input_ids)

    return defaults, uov_index, format_index, model_indices


def _locate_model_indices(comps: dict, input_ids: list) -> dict:
    """
    Locate fn_index=67 input positions for checkpoint/LoRA/sampling settings,
    by component label rather than fixed offset — their position shifts with
    `default_max_lora_number`, and the number of LoRA slots is itself
    variable, so we can't compute them from uov_index the way the fixed
    pre-LoRA-block fields are addressed.

    Returns a dict with keys: base_model, refiner_model, refiner_switch,
    sharpness, guidance_scale, adm_guidance (3-tuple of indices), clip_skip,
    sampler, scheduler, vae, loras (list of (enable, dropdown, weight) index
    tuples, one per "LoRA N" slot, ordered by N).
    """
    labels = {
        i: (comps.get(cid, {}).get("props", {}).get("label") or "")
        for i, cid in enumerate(input_ids)
    }

    def find(label: str) -> int | None:
        for i, lbl in labels.items():
            if lbl == label:
                return i
        return None

    lora_slots = []
    for i, lbl in labels.items():
        if re.match(r"^LoRA \d+$", lbl):
            lora_slots.append((i - 1, i, i + 1))  # enable, dropdown, weight
    lora_slots.sort()

    return {
        "base_model":     find("Base Model (SDXL only)"),
        "refiner_model":  find("Refiner (SDXL or SD 1.5)"),
        "refiner_switch": find("Refiner Switch At"),
        "sharpness":      find("Image Sharpness"),
        "guidance_scale": find("Guidance Scale"),
        "adm_guidance": (
            find("Positive ADM Guidance Scaler"),
            find("Negative ADM Guidance Scaler"),
            find("ADM Guidance End At Step"),
        ),
        "clip_skip":      find("CLIP Skip"),
        "sampler":        find("Sampler"),
        "scheduler":      find("Scheduler"),
        "vae":            find("VAE"),
        "loras":          lora_slots,
    }


def _apply_model_metadata(args: list, model_indices: dict, metadata: ImageMetadata) -> None:
    """Override args in-place with the model/sampling settings an image was
    originally generated with, so e.g. "Vary" reproduces it faithfully
    instead of using whichever checkpoint/LoRAs are currently selected in
    the Fooocus UI."""
    def set_if_known(key: str, value) -> None:
        idx = model_indices.get(key)
        if idx is not None and value is not None:
            args[idx] = value

    set_if_known("base_model", metadata.base_model)
    if model_indices.get("refiner_model") is not None:
        args[model_indices["refiner_model"]] = metadata.refiner_model or "None"
    set_if_known("refiner_switch", metadata.refiner_switch)
    set_if_known("sharpness", metadata.sharpness)
    set_if_known("guidance_scale", metadata.guidance_scale)
    set_if_known("clip_skip", metadata.clip_skip)
    set_if_known("sampler", metadata.sampler)
    set_if_known("scheduler", metadata.scheduler)
    set_if_known("vae", metadata.vae)

    if metadata.adm_guidance is not None:
        for idx, value in zip(model_indices.get("adm_guidance", ()), metadata.adm_guidance):
            if idx is not None:
                args[idx] = value

    lora_slots = model_indices.get("loras", [])
    for slot_i, (enable_idx, dropdown_idx, weight_idx) in enumerate(lora_slots):
        if slot_i < len(metadata.loras):
            name, weight = metadata.loras[slot_i]
            args[enable_idx]   = True
            args[dropdown_idx] = name
            args[weight_idx]   = weight
        else:
            # No LoRA used in this slot originally — disable it so a
            # currently-enabled default LoRA doesn't leak into the result.
            args[enable_idx] = False


def _gallery_has_images(item) -> bool:
    """
    Return True if a gallery output item contains at least one image.

    Gradio 3.x may return the gallery as:
      - a plain list of file dicts  → [{"name": "path/img.png", ...}, ...]
      - a Gradio update dict        → {"__type__": "update", "visible": True,
                                        "value": [{...}, ...]}
      - an empty list / None when not yet done
    """
    if isinstance(item, list):
        return len(item) > 0
    if isinstance(item, dict):
        val = item.get("value")
        return isinstance(val, list) and len(val) > 0
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_client(fooocus_url: str) -> LazyFoocusConnection:
    """Return a lazy Fooocus connection.

    The actual handshake (two blocking GETs to /config) is deferred to the
    first submit() or an explicit connect(), so importing or launching the app
    never blocks on Fooocus being reachable.
    """
    return LazyFoocusConnection(fooocus_url)


def submit_upscale_job(
    conn:            "FoocusConnection | LazyFoocusConnection",
    image_path:      Path,
    uov_method:      UovMethod,
    performance:     PerformancePreset,
    positive_prompt: str,
    negative_prompt: str,
    seed:            int,
    output_format:   OutputFormat = OutputFormat.PNG,
    model_metadata:  ImageMetadata | None = None,
) -> SubmittedJob:
    """Encode image and start generation. Returns immediately; runs in background."""
    return conn.submit(
        image_path, uov_method, performance, positive_prompt, negative_prompt,
        seed, output_format, model_metadata,
    )


def get_job_status(submitted_job: SubmittedJob) -> str:
    """Return current status: 'processing' | 'done' | 'failed'."""
    return submitted_job.get_status()
