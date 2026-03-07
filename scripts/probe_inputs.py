"""Probe dependencies[67] input list to verify parameter indices."""
import urllib.request
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

raw = urllib.request.urlopen("http://localhost:7865/config").read()
config = json.loads(raw)
comps = {c["id"]: c for c in config.get("components", [])}
dep67 = config["dependencies"][67]
input_ids = dep67["inputs"]

print(f"Total inputs: {len(input_ids)}")
print()
for i, cid in enumerate(input_ids):
    c = comps.get(cid, {})
    props = c.get("props", {})
    label = str(props.get("label", props.get("name", "?")))[:35]
    ctype = c.get("type", "?")
    val = props.get("value")
    vs = str(val)[:35] if val is not None else "None"
    print(f"[{i:3d}] id={cid:<4} type={ctype:<15} label={repr(label):<38} default={vs}")
