"""
Test calling the full generate button event chain:
  [65] click  (0 inputs)     → UI setup, outputs state
  [66] then   (2 inputs)     → seed/text update
  [67] then   (141 inputs)   → start generation, outputs state
  [68] then   (1 input)      → poll for results

Usage: python scripts/test_chain.py <image_path>
"""
import asyncio
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

import websockets

FOOOCUS_URL = "http://localhost:7865"
WS_URL      = "ws://localhost:7865"


def load_config():
    raw = urllib.request.urlopen(f"{FOOOCUS_URL}/config").read()
    config = json.loads(raw)
    comps = {c["id"]: c for c in config.get("components", [])}
    return config, comps


async def call_fn(fn_index: int, data: list, session_hash: str, label: str) -> list | None:
    """Submit one fn_index event and return the output data list."""
    print(f"\n--- fn_index={fn_index} ({label}) ---")
    async with websockets.connect(
        f"{WS_URL}/queue/join", open_timeout=30, close_timeout=10
    ) as ws:
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("msg")
            if mtype not in ("process_generating",):
                print(f"  {mtype}")

            if mtype == "send_hash":
                await ws.send(json.dumps({
                    "session_hash": session_hash, "fn_index": fn_index
                }))
            elif mtype == "send_data":
                await ws.send(json.dumps({
                    "session_hash": session_hash, "fn_index": fn_index, "data": data
                }))
            elif mtype == "process_completed":
                output = msg.get("output", {})
                out_data = output.get("data", [])
                success = msg.get("success")
                print(f"  => success={success}, data={[str(x)[:60] for x in (out_data or [])]}")
                return out_data
            elif mtype == "queue_full":
                print("  Queue full!")
                return None
    return None


async def main(image_path: Path | None):
    config, comps = load_config()
    import uuid
    session_hash = uuid.uuid4().hex[:12]
    print(f"Session: {session_hash}")

    # Build 141-item args for fn_index=67
    dep67 = config["dependencies"][67]
    input_ids = dep67["inputs"]
    args67 = [comps.get(cid, {}).get("props", {}).get("value") for cid in input_ids]

    if image_path and image_path.exists():
        suffix = image_path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        image_data = f"data:{mime};base64,{b64}"
        print(f"Image: {image_path.name} ({len(image_data)//1024} KB base64)")
    else:
        print("No image - image slot = None")
        image_data = None

    args67[2]  = "1girl, fox ears, fluffy tail"
    args67[3]  = ""
    args67[7]  = 1         # Image Number = 1 (faster for testing)
    args67[9]  = "12345"
    args67[19] = True
    args67[20] = "uov"
    args67[21] = "Upscale (2x)"
    args67[22] = image_data

    # --- Step 1: fn_index=65 (root click, 0 inputs) ---
    out65 = await call_fn(65, [], session_hash, "Generate button click")
    if out65 is None:
        print("FAILED at step 1")
        return
    # Extract the state value (last item in outputs that is a state component)
    dep65 = config["dependencies"][65]
    out65_ids = dep65.get("outputs", [])
    state65 = None
    for i, oid in enumerate(out65_ids):
        c = comps.get(oid, {})
        if c.get("type") == "state" and i < len(out65):
            state65 = out65[i]
    print(f"  State from fn65: {state65!r}")

    # --- Step 2: fn_index=66 (2 inputs - check what they are) ---
    dep66 = config["dependencies"][66]
    in66_ids = dep66.get("inputs", [])
    print(f"\nfn_index=66 inputs: {[(cid, comps.get(cid,{}).get('type'), comps.get(cid,{}).get('props',{}).get('label')) for cid in in66_ids]}")
    args66 = [comps.get(cid, {}).get("props", {}).get("value") for cid in in66_ids]
    out66 = await call_fn(66, args66, session_hash, "seed/text update")

    # --- Step 3: fn_index=67 (141 inputs) — set state from fn65 ---
    args67[0] = state65  # use state value from fn65
    out67 = await call_fn(67, args67, session_hash, "start generation")
    if out67 is None:
        print("FAILED at step 3")
        return
    state67 = out67[0] if out67 else None
    print(f"  State from fn67: {type(state67).__name__} {str(state67)[:80]!r}")

    # --- Step 4: fn_index=68 (1 input = state from fn67) — poll results ---
    print("\nPolling fn_index=68 for results (may take a while)...")
    max_polls = 60
    for poll_n in range(max_polls):
        out68 = await call_fn(68, [state67], session_hash, f"poll #{poll_n+1}")
        if out68 is None:
            print("poll failed")
            break
        # out68 = [html, image, gallery_finished, gallery_all]
        html     = out68[0] if len(out68) > 0 else None
        preview  = out68[1] if len(out68) > 1 else None
        finished = out68[2] if len(out68) > 2 else None
        gallery  = out68[3] if len(out68) > 3 else None
        has_finished = finished and len(finished) > 0
        has_gallery  = gallery  and len(gallery)  > 0
        print(f"  poll {poll_n+1}: html={'yes' if html else 'none'}, "
              f"preview={'yes' if preview else 'none'}, "
              f"finished={len(finished) if finished else 0}, "
              f"gallery={len(gallery) if gallery else 0}")
        if has_finished or has_gallery:
            print("  => Generation complete!")
            break
        time.sleep(3)


if __name__ == "__main__":
    img = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(img))
