import pytest
from pathlib import Path
from log_parser import parse_log, LogParseError

FIXTURE = Path(__file__).parent / "fixtures" / "sample_log.html"

# Known filename from the fixture file (first entry in sample_log.html)
KNOWN_FILENAME = "2026-03-04_22-14-09_2747.png"
KNOWN_SEED = 792845999704457840


def test_parse_known_image_returns_correct_seed():
    meta = parse_log(FIXTURE, KNOWN_FILENAME)
    assert meta.seed == KNOWN_SEED


def test_parse_known_image_has_nonempty_positive_prompt():
    meta = parse_log(FIXTURE, KNOWN_FILENAME)
    assert "best quality" in meta.positive_prompt


def test_parse_known_image_has_nonempty_negative_prompt():
    meta = parse_log(FIXTURE, KNOWN_FILENAME)
    assert len(meta.negative_prompt) > 0


def test_unknown_filename_raises_log_parse_error():
    with pytest.raises(LogParseError, match="No log entry found"):
        parse_log(FIXTURE, "nonexistent_image_12345.png")


def test_missing_log_file_raises_log_parse_error(tmp_path):
    missing = tmp_path / "log.html"
    with pytest.raises(LogParseError, match="log.html not found"):
        parse_log(missing, KNOWN_FILENAME)


def test_malformed_log_html_raises_log_parse_error(tmp_path):
    bad_log = tmp_path / "log.html"
    # A div.image-container with the right image but no metadata table
    bad_log.write_text(
        "<div class='image-container'>"
        f"<img src='{KNOWN_FILENAME}'/>"
        "</div>",
        encoding="utf-8",
    )
    with pytest.raises(LogParseError, match="Missing fields"):
        parse_log(bad_log, KNOWN_FILENAME)
