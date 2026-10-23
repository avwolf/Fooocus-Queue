# LoRA Training Set Capture Contract

**Created**: 2026-10-22
**Confidence Score**: 95/100
**Status**: Approved

## Problem Statement

Training an SDXL LoRA takes a curated folder of images, each with a `.txt` caption file beside it. The images worth training on are already coming out of this queue as upscale and vary results. Today, getting one into a dataset means finding the finished file in Fooocus's dated output folders, copying it, creating a caption file, and retyping or pasting the prompt. Doing that for dozens of images is tedious, and it's easy to mismatch an image with the wrong caption.

The captions will always need hand-editing; for example, character-defining traits have to come out so the trigger word learns them. A first draft generated from the prompt still removes most of the typing. The images will also be retouched in GIMP or Affinity before training, so the copies should be in a format that survives repeated editing.

## Goals

1. When an "Add to training set" checkbox is on at submit time, the job's finished image is copied into `<training dir>\<trigger word>\` automatically, with a caption `.txt` beside it.
2. Captions are drafted from the submitted positive prompt: trigger word first, then the prompt with weight syntax, wildcards and LoRA tags removed and commas and whitespace cleaned up.
3. The checkbox and trigger word stay set across submits, so a batch can be queued without retyping. Unchecking the box clears the trigger word.
4. An existing gallery image can be added to the training set directly, without running a job.
5. Copies are lossless PNGs at full resolution, so they can be retouched and re-saved without losing quality.

## Success Criteria

- [ ] A "Add to training set" checkbox and a "Trigger word" textbox appear in a "Training Set" section of the Settings tab, in the right column under Output Format. The trigger word textbox is cleared whenever the checkbox is unchecked.
- [ ] The checkbox and trigger word keep their values after Submit and after selecting a different gallery image.
- [ ] Submitting with the checkbox on and a blank trigger word is refused with the message "Enter a trigger word to add to the training set", and nothing is queued.
- [ ] When a training-flagged job reaches `done`, `<training dir>\<trigger>\<output stem>.png` and `<output stem>.txt` exist.
- [ ] The default training directory is `FOOOCUS_OUTPUTS_ROOT\training`, beside the dated output folders, and a `TRAINING_DIR` entry in `.env` overrides it. The directory and trigger subfolder are created on demand. The gallery never lists this folder, because it only scans `YYYY-MM-DD` folders.
- [ ] The caption for the prompt `(blue eyes:1.3), __pose__, <lora:foo:0.8>, red dress,, red dress` with trigger `mychar` is `mychar, blue eyes, red dress`.
- [ ] WebP and JPEG results are saved as PNG at their original pixel size. PNG results are saved at their original pixel size too.
- [ ] An existing file is never overwritten. A name collision gets a `_1`, `_2`, … suffix, so hand-edited files are safe.
- [ ] The training flag and trigger are stored on the queue entry, so jobs re-queued on restart and failed jobs that are retried still copy when they finish.
- [ ] An "Add selected to training set" button copies the selected gallery image, with a caption built from the current Positive Prompt textbox, and reports the destination path.
- [ ] The queue table shows each training-flagged job's trigger word and the result of the copy (`copied` or `copy failed`).
- [ ] If the copy fails (output can't be found, disk error), the job still shows `done`, the failure is logged, and the queue shows `copy failed`.
- [ ] Unit tests cover caption derivation, PNG conversion, collision naming, and the queue entry fields round-tripping through `queue.json`.

## Scope Boundaries

### In Scope

- A `training` module with caption derivation, trigger-word validation, and copy/convert/collision-safe save
- A `TRAINING_DIR` setting in `config.py`, `.env.example` and the README
- Capturing the finished output's file path from the job (`SubmittedJob`) so it can be copied when the job completes
- A UI checkbox, trigger word box, "Add selected to training set" button, and a training column in the queue table
- `QueueEntry` fields for the training trigger and the copy result, backward-compatible with existing `queue.json`
- Unit tests in `tests/`

### Out of Scope

- Resizing or cropping to SDXL buckets. The trainer's bucketing handles this, and full resolution is better for retouching.
- Removing character traits from captions automatically. This needs judgement, and the user expects to do it by hand.
- Editing captions inside the app. The user will edit `.txt` files in their own editor.
- Kohya `N_trigger` repeat-count folders and trainer config generation. The user adds these when setting up a run.
- Including Fooocus style templates or Fooocus V2 expansion text in the caption. Only the user's own prompt is used. Expansion words are generic quality filler that changes with every seed, and style templates describe the look that was asked for, not necessarily what the image shows. A visible medium tag such as `photo` is better added by hand during caption editing.

### Future Considerations

- A caption preview or editor before copying
- A configurable list of tokens to strip from captions (e.g. a character's fixed traits)
- An optional style name → medium tag mapping (e.g. `Fooocus Photograph` → `photo`), for when a dataset is mostly one look and the style needs to stay promptable instead of being absorbed by the trigger word
- Generating a kohya dataset config, or renaming the folder to `N_trigger`
- A training-set browser showing image count per trigger word

## Execution Plan

_Added during Phase 5 handoff. Pick up this contract cold and know exactly how to execute._

### Dependency Graph

```
Single phase: spec.md (training module → client output paths → queue fields → app wiring)
```

### Execution Steps

**Strategy**: Sequential (single spec)

1. **Implement the spec** _(the only step)_
   ```bash
   /execute-spec docs/ideation/lora-training-set/spec.md
   ```
2. Run `pytest tests/ -q`. Then do the spec's Manual Testing checklist against a live Fooocus, and confirm the gallery payload shape from the `outputs:` log line.
