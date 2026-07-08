"""잡 순차 큐 단위 테스트 — 슬롯이 없으면 queued로 대기, 풀리면 running 전환."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from web import app as webapp  # noqa: E402


def _drain_slots() -> int:
    n = 0
    while webapp._RUNNING.acquire(blocking=False):
        n += 1
    return n


def test_wait_for_slot_queues_until_released():
    held = _drain_slots()
    assert held >= 1
    job = {}
    t = threading.Thread(target=webapp._wait_for_slot, args=(job,), daemon=True)
    try:
        t.start()
        deadline = time.time() + 2
        while job.get("status") != "queued" and time.time() < deadline:
            time.sleep(0.02)
        assert job["status"] == "queued"          # 슬롯 없음 → 대기 노출
        webapp._RUNNING.release()                 # 앞선 잡 종료
        t.join(2)
        assert job["status"] == "running"         # 자동 시작
    finally:
        webapp._RUNNING.release()                 # _wait_for_slot이 잡은 슬롯 반환
        for _ in range(held - 1):
            webapp._RUNNING.release()             # 테스트가 점유한 나머지 원복


def test_wait_for_slot_immediate_when_free():
    job = {}
    webapp._wait_for_slot(job)
    try:
        assert job["status"] == "running"         # 슬롯 여유 → 대기 없이 시작
    finally:
        webapp._RUNNING.release()


def test_queue_full_rejects(monkeypatch):
    monkeypatch.setattr(webapp, "MAX_QUEUED_JOBS", 1)
    monkeypatch.setattr(webapp, "JOBS", {"a": {"status": "queued"}})
    with pytest.raises(HTTPException) as e:
        webapp._reject_if_queue_full()
    assert e.value.status_code == 429


def test_queue_not_full_passes(monkeypatch):
    monkeypatch.setattr(webapp, "JOBS", {"a": {"status": "running"}, "b": {"status": "done"}})
    webapp._reject_if_queue_full()  # 예외 없어야 함
