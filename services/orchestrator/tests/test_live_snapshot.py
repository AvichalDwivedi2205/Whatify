from __future__ import annotations

from app.live.director import LiveDirectorService


def test_bounded_snapshot_truncates_large_payload() -> None:
    payload = {"text": "x" * 5000}
    bounded = LiveDirectorService._bounded_snapshot(payload, max_chars=120)
    assert bounded.get("truncated") is True
    assert isinstance(bounded.get("snippet"), str)

