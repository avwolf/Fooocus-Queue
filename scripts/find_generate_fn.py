"""Find the Fooocus generation fn_index by inspecting outputs."""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

raw = urllib.request.urlopen("http://localhost:7865/config").read()
config = json.loads(raw)
comps = {c["id"]: c for c in config.get("components", [])}

print(f"Total dependencies: {len(config['dependencies'])}")
print()
print(f"{'idx':>4}  {'inputs':>6}  {'outputs':>7}  output_types")
print("-" * 70)

for i, dep in enumerate(config["dependencies"]):
    n_in  = len(dep.get("inputs", []))
    outs  = dep.get("outputs", [])
    n_out = len(outs)

    out_types = []
    for oid in outs[:6]:
        c = comps.get(oid, {})
        out_types.append(c.get("type", "?"))

    # Highlight functions with many inputs AND image/gallery outputs
    has_image_out = any(
        comps.get(oid, {}).get("type") in ("image", "gallery", "html")
        for oid in outs
    )
    marker = " <<<<" if (n_in > 50 and has_image_out) else ""
    print(f"[{i:3d}]  {n_in:6d}  {n_out:7d}  {out_types}{marker}")
