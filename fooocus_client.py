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

Data array for fn_index=67 (141 items)
---------------------------------------
index 0  : internal Gradio state component (None)
index 1  : Generate Image Grid checkbox
index 2  : positive prompt
index 3  : negative prompt
index 7  : Image Number slider (overridden to 1)
index 9  : seed (as str)
index 19 : Input Image tab enabled (True)
index 20 : sub-tab selector ("uov")
index 21 : UOV method string
index 22 : image as "data:<mime>;base64,<data>" URI string
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import uuid
import urllib.request
from enum import Enum
from pathlib import Path

import websockets


_POLL_INTERVAL = 5     # seconds between fn_index=68 polls
_POLL_MAX      = 120   # 10 minutes max (120 × 5 s)
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


class SubmittedJob:
    """
    Tracks a Fooocus generation job.

    A daemon thread walks the generate-button event chain:
        fn_index=65 → fn_index=66 → fn_index=67 → poll fn_index=68 until done.
    """

    def __init__(self, job_id: str, url: str, args: list, args66: list) -> None:
        self.job_id   = job_id
        self._status  = "queued"      # becomes "processing" once semaphore is acquired
        self._url     = url
        self._args    = args
        self._args66  = args66
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

        # 1. fn_index=65: session init (Generate button click, 0 inputs)
        out65 = await self._call_fn(ws_url, session_hash, 65, [])
        if out65 is None:
            return

        # 2. fn_index=66: seed/text update (2 inputs: Random checkbox, Seed)
        #    Required chain step — ignore the result; non-fatal if it fails.
        await self._call_fn(ws_url, session_hash, 66, self._args66)

        # 3. fn_index=67: start generation (141 inputs → state)
        out67 = await self._call_fn(ws_url, session_hash, 67, self._args)
        if out67 is None:
            return
        state67 = out67[0] if out67 else None

        # 4. fn_index=68: poll until Finished Images gallery is non-empty
        #    outputs: [html, preview_image, finished_gallery, all_gallery]
        for _ in range(_POLL_MAX):
            out68 = await self._call_fn(ws_url, session_hash, 68, [state67])
            if out68 is None:
                # Transient WebSocket failure — sleep and retry
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            finished = out68[2] if len(out68) > 2 else None
            gallery  = out68[3] if len(out68) > 3 else None
            if _gallery_has_images(finished) or _gallery_has_images(gallery):
                self._status = "done"
                return
            await asyncio.sleep(_POLL_INTERVAL)

    def _start_thread(self) -> None:
        def run() -> None:
            _fooocus_semaphore.acquire()   # block until previous job finishes
            self._status = "processing"    # now actively using Fooocus
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    asyncio.wait_for(self._run_async(), timeout=630)
                )
            except Exception:
                pass
            finally:
                if self._status == "processing":
                    self._status = "failed"
                _fooocus_semaphore.release()  # allow next queued job to proceed
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def get_status(self) -> str:
        return self._status


class FoocusConnection:
    """Holds the fn_index=67 UI defaults and handles job submission."""

    def __init__(self, fooocus_url: str) -> None:
        self._url      = fooocus_url.rstrip("/")
        self._defaults = _fetch_fn67_defaults(self._url)
        self._args66   = _fetch_fn66_defaults(self._url)

    def _encode_image(self, image_path: Path) -> str:
        """
        Return a base64 data URI for the image.

        Fooocus's gradio_hijack.py preprocess asserts isinstance(x, str) and
        calls decode_base64_to_image(x), which splits on "," and decodes the
        second part.  A plain file path fails that decode.
        """
        suffix = image_path.suffix.lower()
        mime   = "image/png" if suffix == ".png" else "image/jpeg"
        data   = image_path.read_bytes()
        b64    = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def submit(
        self,
        image_path:      Path,
        uov_method:      UovMethod,
        positive_prompt: str,
        negative_prompt: str,
        seed:            int,
    ) -> SubmittedJob:
        file_data = self._encode_image(image_path)

        # 141 items: state at [0], regular params at [1..140]
        args = list(self._defaults)
        args[2]  = positive_prompt
        args[3]  = negative_prompt
        args[7]  = 1                # Image Number slider — generate exactly 1
        args[9]  = str(seed)
        args[19] = True             # Input Image tab enabled
        args[20] = "uov"            # sub-tab selector
        args[21] = str(uov_method)  # Upscale or Variation radio
        args[22] = file_data        # base64 data URI

        return SubmittedJob(
            job_id=str(uuid.uuid4()),
            url=self._url,
            args=args,
            args66=self._args66,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_fn66_defaults(fooocus_url: str) -> list:
    """
    Query Fooocus /config and return the 2 default input values for fn_index=66
    (seed/text update step).  Inputs are the Random checkbox and Seed textbox.
    """
    raw    = urllib.request.urlopen(f"{fooocus_url}/config").read()
    config = json.loads(raw)
    comps  = {c["id"]: c for c in config.get("components", [])}
    dep66  = config["dependencies"][66]
    return [
        comps.get(cid, {}).get("props", {}).get("value")
        for cid in dep66["inputs"]
    ]


def _fetch_fn67_defaults(fooocus_url: str) -> list:
    """
    Query Fooocus /config and return the 141 default parameter values for
    fn_index=67.  Index 0 is the Gradio state (default None); indices 1-140
    are the regular UI parameters.
    """
    raw    = urllib.request.urlopen(f"{fooocus_url}/config").read()
    config = json.loads(raw)
    comps  = {c["id"]: c for c in config.get("components", [])}
    dep67  = config["dependencies"][67]
    return [
        comps.get(cid, {}).get("props", {}).get("value")
        for cid in dep67["inputs"]
    ]


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
# Public API — same interface as before; app.py is unchanged
# ---------------------------------------------------------------------------

def create_client(fooocus_url: str) -> FoocusConnection:
    """Connect to Fooocus and fetch current UI defaults. Raises if unreachable."""
    return FoocusConnection(fooocus_url)


def submit_upscale_job(
    conn:            FoocusConnection,
    image_path:      Path,
    uov_method:      UovMethod,
    positive_prompt: str,
    negative_prompt: str,
    seed:            int,
) -> SubmittedJob:
    """Encode image and start generation. Returns immediately; runs in background."""
    return conn.submit(image_path, uov_method, positive_prompt, negative_prompt, seed)


def get_job_status(submitted_job: SubmittedJob) -> str:
    """Return current status: 'processing' | 'done' | 'failed'."""
    return submitted_job.get_status()
