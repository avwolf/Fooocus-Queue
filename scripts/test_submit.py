"""
Manual end-to-end test: submit a real image to Fooocus and poll until done.

Usage:
    python scripts/test_submit.py PATH_TO_IMAGE.png [uov_method]

uov_method defaults to "Upscale (2x)". Valid values:
    "Vary (Subtle)"     "Vary (Strong)"
    "Upscale (1.5x)"   "Upscale (2x)"   "Upscale (Fast 2x)"
"""
import sys
import time

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

image_path = sys.argv[1]
uov_method = sys.argv[2] if len(sys.argv) > 2 else "Upscale (2x)"

print(f"Image:  {image_path}")
print(f"Method: {uov_method}")
print()

# Use the same client/submit logic as the main app
from fooocus_client import create_client, submit_upscale_job, get_job_status, UovMethod
from pathlib import Path

conn = create_client("http://localhost:7865/")
print("Connected to Fooocus.")

submitted = submit_upscale_job(
    conn,
    Path(image_path),
    UovMethod(uov_method),
    positive_prompt="test prompt",
    negative_prompt="",
    seed=-1,
)

print(f"Job submitted (id={submitted.job_id}). Polling for status...")
prev = None
for _ in range(180):
    status = get_job_status(submitted)
    if status != prev:
        print(f"  Status: {status}")
        prev = status
    if status in ("done", "failed"):
        break
    time.sleep(2)

print()
if prev == "done":
    print("Result:", submitted.gradio_job.result())
else:
    print("Job did not complete successfully.")
