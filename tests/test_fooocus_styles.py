"""Tests for how FoocusConnection resolves the Selected Styles it sends."""
import pytest

import fooocus_client
from fooocus_client import FoocusConnection, OutputFormat, PerformancePreset, UovMethod
from log_parser import ImageMetadata

STYLES_INDEX = 4
DEFAULT_STYLES = ["Fooocus V2", "Fooocus Enhance", "Fooocus Sharp"]
ALL_STYLES = DEFAULT_STYLES + ["Fooocus Masterpiece", "SAI Anime"]


def _synthetic_config() -> dict:
    """A minimal Fooocus /config: just enough components for fn 66/67."""
    def comp(cid, label=None, value=None, choices=None):
        props = {"label": label, "value": value}
        if choices is not None:
            props["choices"] = [[c, c] for c in choices]
        return {"id": cid, "props": props}

    components = [
        comp(100),                                   # 0  state
        comp(101, value=False),                      # 1  image grid
        comp(102, value=""),                         # 2  prompt
        comp(103, "Negative Prompt", ""),            # 3  negative prompt
        comp(104, "Selected Styles", DEFAULT_STYLES, ALL_STYLES),  # 4
        comp(105, "Performance", "Speed"),           # 5
        comp(106),                                   # 6
        comp(107, value=2),                          # 7  image number
        comp(108),                                   # 8
        comp(109, value="0"),                        # 9  seed
        comp(110, value=False),                      # 10 input image tab
        comp(111, value=""),                         # 11 sub-tab selector
        comp(112, value="Disabled", choices=["Disabled", "Vary (Subtle)", "Upscale (2x)"]),
        comp(113),                                   # 13 uov image
        comp(114, value="png", choices=["png", "jpeg", "webp"]),
        comp(120, value=True),                       # fn66: random seed
        comp(121, value="0"),                        # fn66: seed
    ]
    dependencies = [{} for _ in range(66)]
    dependencies.append({"inputs": [120, 121]})
    dependencies.append({"inputs": list(range(100, 115))})
    return {"components": components, "dependencies": dependencies}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(fooocus_client, "_fetch_config", lambda url: _synthetic_config())
    return FoocusConnection("http://localhost:7865")


@pytest.fixture
def submitted_args(monkeypatch):
    """Capture the fn67 args instead of starting a real job thread."""
    captured = {}

    class FakeJob:
        def __init__(self, job_id, url, args, args66):
            captured["args"] = args

    monkeypatch.setattr(fooocus_client, "SubmittedJob", FakeJob)
    return captured


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "img.png"
    path.write_bytes(b"not really a png")
    return path


def _submit(conn, image, metadata=None, styles=None):
    conn.submit(
        image, UovMethod.UPSCALE_2X, PerformancePreset.SPEED, "prompt", "negative",
        42, OutputFormat.PNG, metadata, styles,
    )


def _metadata(styles):
    return ImageMetadata(positive_prompt="p", negative_prompt="n", seed=42, styles=styles)


def test_connection_exposes_style_choices_and_defaults(conn):
    assert conn.style_choices == ALL_STYLES
    assert conn.default_styles == DEFAULT_STYLES


def test_no_metadata_or_override_sends_fooocus_defaults(conn, image, submitted_args):
    _submit(conn, image)
    assert submitted_args["args"][STYLES_INDEX] == DEFAULT_STYLES


def test_styles_inherited_from_original_image(conn, image, submitted_args):
    _submit(conn, image, metadata=_metadata(["Fooocus V2", "Fooocus Masterpiece"]))
    assert submitted_args["args"][STYLES_INDEX] == ["Fooocus V2", "Fooocus Masterpiece"]


def test_metadata_without_styles_falls_back_to_defaults(conn, image, submitted_args):
    _submit(conn, image, metadata=_metadata(None))
    assert submitted_args["args"][STYLES_INDEX] == DEFAULT_STYLES


def test_explicit_styles_override_original(conn, image, submitted_args):
    _submit(conn, image, metadata=_metadata(["Fooocus V2"]), styles=["SAI Anime"])
    assert submitted_args["args"][STYLES_INDEX] == ["SAI Anime"]


def test_explicit_empty_styles_means_no_styles(conn, image, submitted_args):
    _submit(conn, image, metadata=_metadata(["Fooocus V2"]), styles=[])
    assert submitted_args["args"][STYLES_INDEX] == []


def test_unknown_styles_are_dropped_preserving_order(conn, image, submitted_args):
    _submit(conn, image, metadata=_metadata(["SAI Anime", "Renamed Style", "Fooocus V2"]))
    assert submitted_args["args"][STYLES_INDEX] == ["SAI Anime", "Fooocus V2"]


def test_defaults_are_not_mutated_by_submit(conn, image, submitted_args):
    _submit(conn, image, styles=["SAI Anime"])
    _submit(conn, image)
    assert submitted_args["args"][STYLES_INDEX] == DEFAULT_STYLES
