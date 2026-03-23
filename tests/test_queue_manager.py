import pytest
import json
from queue_manager import QueueManager, QueueEntry


def make_entry(job_id: str, status: str = "queued") -> QueueEntry:
    return QueueEntry(
        job_id=job_id,
        image_filename=f"image_{job_id}.png",
        uov_method="Upscale (2x)",
        positive_prompt="a test prompt",
        seed=42,
        status=status,
        submitted_at="2026-03-04 12:00:00 UTC",
    )


def test_empty_queue_on_missing_file(tmp_path):
    qm = QueueManager(tmp_path / "queue.json")
    assert qm.entries == []


def test_add_entry_persists_to_disk(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa"))
    assert qf.exists()
    data = json.loads(qf.read_text())
    assert len(data) == 1
    assert data[0]["job_id"] == "aaa"


def test_add_two_entries_reload_both_present(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa"))
    qm.add(make_entry("bbb"))

    qm2 = QueueManager(qf)
    assert len(qm2.entries) == 2
    ids = {e.job_id for e in qm2.entries}
    assert ids == {"aaa", "bbb"}


def test_update_status_persists(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa"))
    qm.update_status("aaa", "done")

    qm2 = QueueManager(qf)
    assert qm2.entries[0].status == "done"


def test_update_status_unknown_job_is_noop(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa"))
    qm.update_status("nonexistent", "done")  # should not crash
    assert qm.entries[0].status == "queued"


def test_non_terminal_without_image_path_becomes_previous_session(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa", status="queued"))      # no image_path
    qm.add(make_entry("bbb", status="processing"))  # no image_path
    qm.add(make_entry("ccc", status="done"))

    qm2 = QueueManager(qf)
    statuses = {e.job_id: e.status for e in qm2.entries}
    assert statuses["aaa"] == "submitted (previous session)"
    assert statuses["bbb"] == "submitted (previous session)"
    assert statuses["ccc"] == "done"  # terminal — unchanged


def test_non_terminal_with_image_path_stays_queued_on_reload(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    entry = make_entry("aaa", status="queued")
    entry.image_path = "/some/path/image.png"
    qm.add(entry)
    entry2 = make_entry("bbb", status="processing")
    entry2.image_path = "/some/path/image2.png"
    qm.add(entry2)

    qm2 = QueueManager(qf)
    statuses = {e.job_id: e.status for e in qm2.entries}
    assert statuses["aaa"] == "queued"
    assert statuses["bbb"] == "queued"


def test_requeue_candidates_returns_queued_with_image_path(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    entry_with_path = make_entry("aaa", status="queued")
    entry_with_path.image_path = "/some/path/image.png"
    qm.add(entry_with_path)
    qm.add(make_entry("bbb", status="queued"))  # no image_path
    qm.add(make_entry("ccc", status="done"))

    candidates = qm.requeue_candidates()
    assert len(candidates) == 1
    assert candidates[0].job_id == "aaa"


def test_update_job_id_persists(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("old-id"))
    qm.update_job_id("old-id", "new-id")

    qm2 = QueueManager(qf)
    ids = [e.job_id for e in qm2.entries]
    assert "new-id" in ids
    assert "old-id" not in ids


def test_get_entry_returns_correct_entry(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa"))
    qm.add(make_entry("bbb"))
    assert qm.get_entry("aaa").job_id == "aaa"
    assert qm.get_entry("bbb").job_id == "bbb"
    assert qm.get_entry("zzz") is None


def test_cancelled_status_persists(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa"))
    qm.update_status("aaa", "cancelled")

    qm2 = QueueManager(qf)
    assert qm2.entries[0].status == "cancelled"


def test_cancelled_status_unchanged_on_reload(tmp_path):
    """Cancelled is a terminal state — it must not be re-mapped on reload."""
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("aaa", status="queued"))
    qm.update_status("aaa", "cancelled")

    qm2 = QueueManager(qf)
    assert qm2.entries[0].status == "cancelled"


def test_corrupt_queue_json_returns_empty(tmp_path):
    qf = tmp_path / "queue.json"
    qf.write_text("not valid json {{{", encoding="utf-8")
    qm = QueueManager(qf)
    assert qm.entries == []


def test_as_table_rows_newest_first(tmp_path):
    qf = tmp_path / "queue.json"
    qm = QueueManager(qf)
    qm.add(make_entry("first"))
    qm.add(make_entry("second"))
    rows = qm.as_table_rows()
    # Newest (last added) appears first
    assert rows[0][0] == "image_second.png"
    assert rows[1][0] == "image_first.png"
