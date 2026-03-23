"""Tests for SubmittedJob cancellation behaviour."""
import time
import threading

import pytest

from fooocus_client import SubmittedJob, _fooocus_semaphore


def _make_job(job_id: str = "test-job") -> SubmittedJob:
    """Create a SubmittedJob pointing at an unreachable URL."""
    return SubmittedJob(
        job_id=job_id,
        url="http://localhost:19999",  # nothing listening here
        args=[None] * 141,
        args66=[False, "0"],
    )


class TestCancellation:
    """Hold the global semaphore so job threads block on acquire, then test cancel."""

    def setup_method(self):
        # Grab the semaphore so any SubmittedJob thread will block waiting for it.
        acquired = _fooocus_semaphore.acquire(timeout=5)
        assert acquired, "Could not acquire semaphore for test setup"

    def teardown_method(self):
        # Always release so later tests are not affected.
        try:
            _fooocus_semaphore.release()
        except ValueError:
            pass  # already released — fine

    def test_initial_status_is_queued(self):
        job = _make_job()
        assert job.get_status() == "queued"

    def test_cancel_sets_status_immediately(self):
        job = _make_job()
        assert job.get_status() == "queued"
        job.cancel()
        assert job.get_status() == "cancelled"

    def test_cancel_is_idempotent(self):
        job = _make_job()
        job.cancel()
        job.cancel()
        assert job.get_status() == "cancelled"

    def test_cancelled_job_does_not_hold_semaphore(self):
        """After cancel the thread must exit without keeping the semaphore."""
        job = _make_job()
        job.cancel()
        # Release our hold so the thread can finish its current acquire attempt.
        _fooocus_semaphore.release()

        # Give the thread up to 2 s to exit.
        deadline = time.time() + 2
        while time.time() < deadline:
            if _fooocus_semaphore.acquire(timeout=0.1):
                _fooocus_semaphore.release()
                return  # semaphore is free — thread exited cleanly
        pytest.fail("Semaphore was not released after cancel within 2 s")
