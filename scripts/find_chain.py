"""Find the full generate-button event chain in Fooocus's Gradio config."""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

raw = urllib.request.urlopen("http://localhost:7865/config").read()
config = json.loads(raw)
comps = {c["id"]: c for c in config.get("components", [])}

def comp_summary(cid):
    c = comps.get(cid, {})
    label = str(c.get("props", {}).get("label", c.get("props", {}).get("name", "?")))[:30]
    return f"id={cid} type={c.get('type','?')} label={repr(label)}"

# Print all dependencies with their trigger and targets
print("All dependencies with trigger info:")
for i, dep in enumerate(config["dependencies"]):
    trigger = dep.get("trigger", "?")
    targets = dep.get("targets", [])
    n_in  = len(dep.get("inputs", []))
    n_out = len(dep.get("outputs", []))
    out_types = [comps.get(oid, {}).get("type", "?") for oid in dep.get("outputs", [])[:4]]
    target_summary = [comp_summary(t) for t in targets]
    print(f"  [{i:2d}] trigger={repr(trigger):<8} targets={target_summary} | in={n_in} out={n_out} out_types={out_types}")

print()

# Find all "click" events (root handlers)
print("Root click/change events with many outputs OR interesting targets:")
for i, dep in enumerate(config["dependencies"]):
    trigger = dep.get("trigger", "?")
    targets = dep.get("targets", [])
    n_out = len(dep.get("outputs", []))
    if trigger == "click":
        out_types = [comps.get(oid, {}).get("type", "?") for oid in dep.get("outputs", [])[:6]]
        print(f"  [{i:2d}] click on {[comp_summary(t) for t in targets]} | in={len(dep['inputs'])} out={n_out} out_types={out_types}")

print()

# Show component id=15
c15 = comps.get(15, {})
print(f"Component id=15: type={c15.get('type','?')} props={dict(list(c15.get('props',{}).items())[:5])}")
