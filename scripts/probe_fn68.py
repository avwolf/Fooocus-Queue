"""Probe dependencies[67] and [68] to understand the start/poll architecture."""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

raw = urllib.request.urlopen("http://localhost:7865/config").read()
config = json.loads(raw)
comps = {c["id"]: c for c in config.get("components", [])}

for idx in (67, 68):
    dep = config["dependencies"][idx]
    print(f"=== dependencies[{idx}] ===")
    print(f"  trigger: {dep.get('trigger', '?')} on targets {dep.get('targets', [])}")
    print(f"  queue:   {dep.get('queue', '?')}")

    inputs = dep.get("inputs", [])
    outputs = dep.get("outputs", [])
    print(f"  inputs ({len(inputs)}):")
    for i, cid in enumerate(inputs):
        c = comps.get(cid, {})
        props = c.get("props", {})
        label = str(props.get("label", props.get("name", "?")))[:40]
        print(f"    [{i}] id={cid} type={c.get('type','?')} label={repr(label)} default={str(props.get('value'))[:40]}")
    print(f"  outputs ({len(outputs)}):")
    for i, cid in enumerate(outputs):
        c = comps.get(cid, {})
        props = c.get("props", {})
        label = str(props.get("label", props.get("name", "?")))[:40]
        print(f"    [{i}] id={cid} type={c.get('type','?')} label={repr(label)}")
    print()

# Also check /info for fn 68
raw2 = urllib.request.urlopen("http://localhost:7865/info").read()
info = json.loads(raw2)
e68 = info.get("unnamed_endpoints", {}).get("68", {})
print("=== /info fn_index=68 ===")
print(f"  parameters: {len(e68.get('parameters', []))}")
print(f"  returns:    {len(e68.get('returns', []))}")
for r in e68.get("returns", []):
    print(f"    return: {r}")
