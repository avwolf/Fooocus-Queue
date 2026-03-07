"""
Diagnostic: probe the Fooocus WebSocket queue and /info endpoint.

Usage: python scripts/test_ws.py [image_path]

Tests BOTH 141-item (with state) and 140-item (without state) data arrays
and prints every WebSocket message and the full process_completed output.
"""
import asyncio
import base64
import json
import sys
import urllib.request
from pathlib import Path

import websockets

FOOOCUS_URL = "http://localhost:7865"
WS_URL      = "ws://localhost:7865"
FN_INDEX    = 67


def load_config():
    raw = urllib.request.urlopen(f"{FOOOCUS_URL}/config").read()
    config = json.loads(raw)
    comps = {c["id"]: c for c in config.get("components", [])}
    dep = config["dependencies"][FN_INDEX]
    input_ids = dep["inputs"]
    output_ids = dep.get("outputs", [])
    return comps, input_ids, output_ids


def print_info(comps, input_ids, output_ids):
    print("=== dependencies[67] ===")
    print(f"  inputs:  {len(input_ids)} items")
    print(f"  outputs: {len(output_ids)} items")
    for i, oid in enumerate(output_ids[:10]):
        c = comps.get(oid, {})
        print(f"    out[{i}] id={oid} type={c.get('type','?')} label={repr(str(c.get('props',{}).get('label','?'))[:40])}")
    print()
    print("=== /info fn_index=67 ===")
    raw = urllib.request.urlopen(f"{FOOOCUS_URL}/info").read()
    info = json.loads(raw)
    e67 = info.get("unnamed_endpoints", {}).get("67", {})
    params = e67.get("parameters", [])
    returns = e67.get("returns", [])
    print(f"  parameters: {len(params)}")
    print(f"  returns:    {len(returns)}")
    print()


async def run_ws(args, label):
    import uuid
    session_hash = uuid.uuid4().hex[:12]
    print(f"=== WebSocket test: {label} ({len(args)} items, session={session_hash}) ===")

    async with websockets.connect(
        f"{WS_URL}/queue/join",
        open_timeout=30,
        close_timeout=10,
    ) as ws:
        msg_count = 0
        async for raw_msg in ws:
            msg = json.loads(raw_msg)
            mtype = msg.get("msg")
            msg_count += 1

            printable = {k: v for k, v in msg.items() if k not in ("output",)}
            print(f"  [{msg_count}] {json.dumps(printable)}")

            if mtype == "send_hash":
                await ws.send(json.dumps({"session_hash": session_hash, "fn_index": FN_INDEX}))
            elif mtype == "send_data":
                await ws.send(json.dumps({"session_hash": session_hash, "fn_index": FN_INDEX, "data": args}))
            elif mtype == "process_completed":
                output = msg.get("output", {})
                data   = output.get("data")
                print(f"  => success={msg.get('success')}, error={output.get('error')!r}")
                if isinstance(data, list):
                    for i, item in enumerate(data):
                        print(f"     data[{i}] = {str(item)[:80]}")
                break
            elif mtype == "queue_full":
                print("  Queue full")
                break
    print()


if __name__ == "__main__":
    import uuid

    comps, input_ids, output_ids = load_config()
    print_info(comps, input_ids, output_ids)

    img_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if img_path and img_path.exists():
        suffix = img_path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        image_data = f"data:{mime};base64,{b64}"
        print(f"Using image: {img_path} ({len(image_data)} chars base64)")
    else:
        print("No image provided — image slot will be None")
        image_data = None
    print()

    # --- Test A: 141 items WITH state at [0] (current approach) ---
    all_141 = [comps.get(cid, {}).get("props", {}).get("value") for cid in input_ids]
    all_141[2]  = "test positive prompt"
    all_141[3]  = ""
    all_141[9]  = "42"
    all_141[19] = True
    all_141[20] = "uov"
    all_141[21] = "Upscale (2x)"
    all_141[22] = image_data
    asyncio.run(run_ws(all_141, "141-item WITH state at [0]"))

    # --- Test B: 140 items WITHOUT state (original indices) ---
    no_state_140 = [comps.get(cid, {}).get("props", {}).get("value") for cid in input_ids[1:]]
    no_state_140[1]  = "test positive prompt"
    no_state_140[2]  = ""
    no_state_140[8]  = "42"
    no_state_140[18] = True
    no_state_140[19] = "uov"
    no_state_140[20] = "Upscale (2x)"
    no_state_140[21] = image_data
    asyncio.run(run_ws(no_state_140, "140-item WITHOUT state"))
