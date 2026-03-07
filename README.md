# Fooocus Upscale Queue

A companion web app for [Fooocus](https://github.com/lllyasviel/Fooocus) that adds a
persistent queue for upscaling and variation jobs.  Browse your existing Fooocus
outputs, select an image, choose an operation, and submit — jobs run one at a time in
the background while you continue adding more.

> **Rationale**
>
> *(To be filled in.)*

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
to select it; its prompt and seed are read automatically from Fooocus's `log.html`.

**4. Choose an operation**

| Option | Description |
|---|---|
| Upscale (2x) | Double resolution, high quality |
| Upscale (1.5x) | Moderate upscale |
| Upscale (Fast 2x) | Faster, slightly lower quality |
| Vary (Subtle) | Small creative variation |
| Vary (Strong) | Larger creative variation |

**5. Submit**

Click **Submit for Upscaling**.  The job appears in the queue table with status
`queued`.  It moves to `processing` when Fooocus starts generating and to `done` when
complete.  Results appear in the gallery on the next page load or after clicking
**Load more days**.

Jobs run one at a time; additional submissions wait in the queue automatically.

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
