"""AI queue behaviour: FIFO order, single-flight, bounds, timeouts."""

from __future__ import annotations

import threading
import time

import pytest

from src.lib.ai_queue import AIQueue, AIQueueTimeout, QueueFullError


def test_fifo_order_preserved():
    q = AIQueue(max_size=10)
    order = []

    def job(n):
        order.append(n)
        return n

    results = [q.submit(job, n) for n in range(5)]
    assert results == [0, 1, 2, 3, 4]
    assert order == [0, 1, 2, 3, 4]
    q.close()


def test_exactly_one_active_request_at_a_time():
    q = AIQueue(max_size=10)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow():
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return True

    threads = [threading.Thread(target=lambda: q.submit(slow)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_active == 1, "more than one OpenRouter request ran concurrently"
    q.close()


def test_queue_is_bounded_and_rejects_overflow():
    q = AIQueue(max_size=2)

    started = threading.Event()
    release = threading.Event()

    def blocker():
        started.set()
        release.wait(2)
        return "done"

    # Occupy the worker with one job, then fill the 2 waiting slots.
    t = threading.Thread(target=lambda: q.submit(blocker))
    t.start()
    started.wait(2)
    time.sleep(0.05)

    fillers = []
    for _ in range(2):
        fill = threading.Thread(target=lambda: q.submit(lambda: "x"))
        fill.start()
        fillers.append(fill)
    time.sleep(0.1)

    with pytest.raises(QueueFullError):
        q.submit(lambda: "overflow")

    release.set()
    t.join()
    for f in fillers:
        f.join()
    q.close()


def test_submit_timeout_raises_and_job_still_completes():
    q = AIQueue(max_size=5)
    done = threading.Event()

    def slow():
        time.sleep(0.3)
        done.set()
        return "late"

    with pytest.raises(AIQueueTimeout):
        q.submit(slow, timeout=0.05)
    assert done.wait(2), "job should still complete in the background"
    q.close()


def test_exceptions_propagate_no_fake_result():
    q = AIQueue(max_size=5)

    def failing():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        q.submit(failing)
    # Queue keeps working after a failure.
    assert q.submit(lambda: "ok") == "ok"
    q.close()


def test_stats_and_active_count():
    q = AIQueue(max_size=5)
    assert q.submit(lambda: 1) == 1
    stats = q.stats()
    assert stats["submitted"] == 1
    assert stats["completed"] == 1
    assert stats["active"] == 0
    assert q.active_count == 0
    q.close()
