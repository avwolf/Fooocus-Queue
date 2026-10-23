import pytest
from PIL import Image

from training import (
    BLANK_TRIGGER_MSG,
    add_to_training_set,
    derive_caption,
    locate_output,
    validate_trigger,
)


# ---------------------------------------------------------------------------
# derive_caption
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt, expected", [
    ("(blue eyes:1.3), __pose__, <lora:foo:0.8>, red dress,, red dress",
     "mychar, blue eyes, red dress"),
    ("a woman, red dress\nstanding in a field", "mychar, a woman, red dress, standing in a field"),
    ("((smiling:1.2):1.1), outdoors", "mychar, smiling, outdoors"),
    ("[[sunny, rainy]] day", "mychar, sunny, rainy day"),
    ("MyChar, portrait", "mychar, portrait"),
    ("(detailed face), (((masterpiece)))", "mychar, detailed face, masterpiece"),
    ("  lots   of\tspace ,  here ", "mychar, lots of space, here"),
    ("Red Dress, red dress", "mychar, Red Dress"),
    ("slacks \\(pants\\), (dark crimson fur:1.2)", "mychar, slacks (pants), dark crimson fur"),
    ("(cat \\(animal\\):1.2)", "mychar, cat (animal)"),
    ("", "mychar"),
    ("<lora:x:1>, __wild__", "mychar"),
])
def test_derive_caption(prompt, expected):
    assert derive_caption(prompt, "mychar") == expected


# ---------------------------------------------------------------------------
# validate_trigger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trigger", ["mychar", "my char", "char_v2", "ohwx-woman"])
def test_valid_triggers(trigger):
    assert validate_trigger(trigger) is None


def test_blank_trigger_rejected_with_contract_message():
    assert validate_trigger("") == BLANK_TRIGGER_MSG
    assert BLANK_TRIGGER_MSG == "Enter a trigger word to add to the training set"


@pytest.mark.parametrize("trigger", ["a/b", "a:b", "a\\b", "what?", "dots.", "CON", "nul", "com1.txt", "tab\tbed"])
def test_unusable_folder_names_rejected(trigger):
    msg = validate_trigger(trigger)
    assert msg and "folder name" in msg


# ---------------------------------------------------------------------------
# locate_output
# ---------------------------------------------------------------------------

NAME = "2026-10-22_21-56-12_1234.png"


def test_locate_prefers_real_output_over_temp(tmp_path):
    outputs = tmp_path / "outputs"
    (outputs / "2026-10-22").mkdir(parents=True)
    real = outputs / "2026-10-22" / NAME
    real.write_bytes(b"x")
    temp = tmp_path / "gradio" / "abc123" / NAME
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"x")
    assert locate_output(str(temp), outputs) == real


def test_locate_falls_back_to_temp_copy(tmp_path):
    temp = tmp_path / "gradio" / "abc123" / NAME
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"x")
    assert locate_output(str(temp), tmp_path / "outputs") == temp


def test_locate_returns_none_when_nothing_exists(tmp_path):
    assert locate_output(str(tmp_path / "gone" / NAME), tmp_path / "outputs") is None


def test_locate_non_dated_name_uses_temp(tmp_path):
    temp = tmp_path / "image.png"
    temp.write_bytes(b"x")
    assert locate_output(str(temp), tmp_path) == temp


# ---------------------------------------------------------------------------
# add_to_training_set
# ---------------------------------------------------------------------------

def _make_image(path, fmt, size=(64, 48), mode="RGB"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, "red").save(path, format=fmt)
    return path


@pytest.mark.parametrize("ext, fmt", [("webp", "WEBP"), ("jpg", "JPEG"), ("png", "PNG")])
def test_copies_as_full_size_png_with_caption(tmp_path, ext, fmt):
    src = _make_image(tmp_path / "src" / f"2026-10-22_1.{ext}", fmt)
    root = tmp_path / "training"

    dest = add_to_training_set(src, "(blue eyes:1.2), red dress", "mychar", root)

    assert dest == root / "mychar" / "2026-10-22_1.png"
    with Image.open(dest) as img:
        assert img.format == "PNG"
        assert img.size == (64, 48)
    caption = (root / "mychar" / "2026-10-22_1.txt").read_text(encoding="utf-8")
    assert caption == "mychar, blue eyes, red dress"


def test_cmyk_source_converted_to_rgb(tmp_path):
    src = _make_image(tmp_path / "cmyk.jpg", "JPEG", mode="CMYK")
    dest = add_to_training_set(src, "x", "t", tmp_path / "training")
    with Image.open(dest) as img:
        assert img.mode == "RGB"


def test_collisions_get_numbered_suffixes_and_never_overwrite(tmp_path):
    src = _make_image(tmp_path / "img.png", "PNG")
    root = tmp_path / "training"

    first = add_to_training_set(src, "first", "t", root)
    first_caption = first.with_suffix(".txt")
    first_caption.write_text("hand edited", encoding="utf-8")
    second = add_to_training_set(src, "second", "t", root)
    third = add_to_training_set(src, "third", "t", root)

    assert [first.name, second.name, third.name] == ["img.png", "img_1.png", "img_2.png"]
    assert first_caption.read_text(encoding="utf-8") == "hand edited"
    assert second.with_suffix(".txt").read_text(encoding="utf-8") == "t, second"


def test_lone_existing_caption_forces_suffix(tmp_path):
    src = _make_image(tmp_path / "img.png", "PNG")
    dest_dir = tmp_path / "training" / "t"
    dest_dir.mkdir(parents=True)
    (dest_dir / "img.txt").write_text("orphan caption", encoding="utf-8")

    dest = add_to_training_set(src, "p", "t", tmp_path / "training")

    assert dest.name == "img_1.png"
    assert (dest_dir / "img.txt").read_text(encoding="utf-8") == "orphan caption"
