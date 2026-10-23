# Fooocus Upscale Queue

A companion web app for [Fooocus](https://github.com/lllyasviel/Fooocus) that adds a
persistent queue for upscaling and variation jobs.  Browse your existing Fooocus
outputs, select an image, choose an operation, and submit — jobs run one at a time in
the background while you continue adding more.

**Rationale**
I just got started working with Fooocus and found trying to queue items up for Upscaling
to run during the night while I was asleep (so my time while awake could be spent 
generating new images) to be tedious -- opening window after window, copy/pasting data, 
making sure I picked the right image, and trying to remember to set the generated image 
count down to 1, turn off random, and set the correct seed.

I considered scripting it but the Gradio API that runs Fooocus is the absolute worst
nightmarish mystery box API I've ever seen in my life.  It's so terrible that there's a
major project that just provides a RESTful API for it instead (Fooocus-API).
Unfortunately, Fooocus-API is a full fork of Fooocus, not just an API plugin or translation
layer.  I wasn't eager to have to copy all my models again into another spot, just to be
able to have reasonable API access.

Then...it was fate.  My boss started a major company initiative to adopt Claude Code and
use it to make ourselves more productive and nimble. I was specifically told I should
try it out for personal projects, just so long as I gained experience and enthusiasm.
Two nights later, and I actually have a really useful tool that can queue up all the 
upscaling actions I want in a single place, and I don't have to look up the prompts, RNG 
seeds, and image filenames from the log file myself.


---

## Requirements

- Python 3.10+
- Fooocus 2.5.x running locally (tested against Gradio 3.41.2)

---

## Setup

**1. Clone and install dependencies**

```bash
git clone <repo-url>
cd fooocus-upscale-queue
pip install -r requirements.txt
```

**2. Configure environment**

```bash
cp .env.example .env
```

Edit `.env` and set `FOOOCUS_OUTPUTS_ROOT` to the `outputs` folder inside your Fooocus
installation:

```
FOOOCUS_OUTPUTS_ROOT=C:/path/to/Fooocus/outputs
FOOOCUS_URL=http://localhost:7865
QUEUE_FILE=queue.json
```

`FOOOCUS_URL` and `QUEUE_FILE` can be left at their defaults in most cases.

Optionally set `TRAINING_DIR` to choose where LoRA training images are collected (see
*Add to the training set* below).  It defaults to a `training` folder inside
`FOOOCUS_OUTPUTS_ROOT`, beside the dated output folders.

---

## Usage

**1. Start Fooocus** as normal and leave it running.

**2. Start the queue app**

```bash
python app.py
```

Then open **http://localhost:7860** in your browser.

**3. Browse and select an image**

The gallery shows images from your Fooocus output folder, newest first.  Click an image
to select it; its prompt, seed, and performance setting are read automatically from
Fooocus's `log.html`.  The selected image is highlighted in the gallery — no lightbox
popup.

If you have generated new images in Fooocus since the app was started, click
**↻ Refresh Gallery** to rescan the output folder and bring them in.

**4. Choose an operation**

| Option | Description |
|---|---|
| Upscale (2x) | Double resolution, high quality |
| Upscale (1.5x) | Moderate upscale |
| Upscale (Fast 2x) | Faster, slightly lower quality |
| Vary (Subtle) | Small creative variation |
| Vary (Strong) | Larger creative variation |

**5. Choose a performance preset**

The Performance radio is pre-set to match the setting used when the original image was
generated.  You can change it before submitting if you want a different quality/speed
trade-off for this particular job.

| Preset | Description |
|---|---|
| Speed | Balanced quality and generation time (Fooocus default) |
| Quality | Higher quality, slower generation |
| Extreme Speed | Very fast, lower quality |
| Lightning | Fastest preset |
| Hyper-SD | Alternative fast preset |

**6. Check styles (optional)**

Selecting an image also loads the Fooocus styles it was generated with (from `log.html`),
so the upscale keeps the original look instead of picking up whatever styles are Fooocus's
current defaults.  The line above the Submit button shows which styles will be sent and
whether they are inherited or overridden.  To change them, open the **Style** tab: search
and tick/untick styles, **↺ Reset to original** to undo, or **Clear all** to send none.
*Upscale (Fast 2x)* does not run diffusion, so styles have no effect on it.

**7. Add to the training set (optional)**

To collect images for an SDXL LoRA, tick **Add to training set** in the *Training Set*
section of the **Settings** tab and enter a **Trigger word**.  When a job submitted this
way finishes, its result is copied to `<TRAINING_DIR>\<trigger word>\` as a
full-resolution PNG (lossless, so retouching and re-saving doesn't degrade it), with a
caption `.txt` of the same name beside it.  The queue table's *Training* column shows
whether the copy succeeded.

The caption is a first draft built from the positive prompt: the trigger word, then the
prompt's tags with weights such as `(blue eyes:1.3)`, `__wildcards__` and `<lora:...>`
tags removed and duplicates dropped.  Styles and Fooocus V2 expansion are not included.
Expect to edit captions by hand, e.g. removing the traits the trigger word should learn.

The checkbox and trigger word stay set between submissions, so you can queue a batch;
unticking the box clears the trigger word.  **Add selected to training set** copies the
selected gallery image straight away, without running a job.  Existing files are never
overwritten — a name clash gets a `_1`, `_2`, … suffix.

**8. Submit**

Click **Submit for Upscaling**.  The job appears in the queue table with status
`queued`.  It moves to `processing` when Fooocus starts generating and to `done` when
complete.  Results appear in the gallery on the next page load or after clicking
**Load more days**.

Jobs run one at a time; additional submissions wait in the queue automatically.

**Note on processing order:** Jobs are not guaranteed to run in the order they were
submitted.  Fooocus can only process one job at a time, so waiting jobs are held back
by a lock (called a semaphore).  When a job finishes and the lock is released, the
operating system decides which waiting job gets it next — and it doesn't always pick
the one that has been waiting longest.  In practice the order is usually close to
submission order, but don't rely on it.

---

## Running the tests

```bash
pytest tests/ -v
```

---

## Notes

- The app only reads the prompt and seed from images that have a Fooocus `log.html`
  alongside them.  Images from other sources can still be submitted but the metadata
  fields will be blank.
- `queue.json` is created automatically on first run and is excluded from version
  control.  It is **not** cleared between runs, so in-progress jobs from a previous
  session are marked *"submitted (previous session)"* on restart.

---

*This project was written with [Claude Code](https://claude.ai/claude-code).*
