"""Shared OpenRouter request queue.

``/AI`` and ``/Gf`` both funnel their chat completions through one
:class:`AIQueue` instance. The queue is:

* **FIFO** — jobs run in submission order,
* **single-flight** — exactly one job is ever active at a time (one worker
  thread pulls jobs one by one),
* **bounded** — at most ``max_size`` waiting jobs; overflow raises
  :class:`QueueFullError` instead of growing memory, and
* **time-bounded** — callers can cap how long they wait for their result.

The queue never fabricates results: if the wrapped callable raises, the
exception is re-raised to the caller.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    """The AI queue already holds the maximum number of waiting jobs."""


class AIQueueTimeout(Exception):
    """A submitted job did not finish within the caller's timeout."""


class QueueClosedError(Exception):
    """The queue is shutting down and no longer accepts jobs."""


@dataclass
class _Job:
    fn: Callable[..., Any]
    args: tuple
    kwargs: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[BaseException] = None


class AIQueue:
    """A bounded, FIFO, single-worker job queue."""

    def __init__(self, max_size: int = 50, name: str = "ai-queue"):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self.max_size = max_size
        self.name = name
        self._queue: "queue.Queue[Optional[_Job]]" = queue.Queue(maxsize=max_size)
        self._lock = threading.Lock()
        self._active = 0
        self._submitted = 0
        self._completed = 0
        self._closed = False
        self._worker = threading.Thread(target=self._run, name=f"{name}-worker", daemon=True)
        self._started = False

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._worker.start()

    def submit(self, fn: Callable[..., Any], *args: Any, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        """Queue *fn* and block until it has run.

        Raises :class:`QueueFullError` when the queue is at capacity,
        :class:`AIQueueTimeout` when the job did not finish in time (the job
        itself may still complete later — callers should treat the answer as
        lost), and re-raises any exception thrown by *fn*.
        """
        self.start()
        job = _Job(fn=fn, args=args, kwargs=kwargs)
        with self._lock:
            if self._closed:
                raise QueueClosedError("AI queue is closed")
        try:
            self._queue.put_nowait(job)
        except queue.Full as exc:
            raise QueueFullError(
                f"The AI queue is full ({self.max_size} pending requests). "
                "Try again in a minute."
            ) from exc
        with self._lock:
            self._submitted += 1

        if not job.done.wait(timeout=timeout):
            raise AIQueueTimeout(
                f"The AI request did not finish within {timeout:.0f}s."
                if timeout
                else "The AI request did not finish in time."
            )
        if job.error is not None:
            raise job.error
        return job.result

    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:  # shutdown sentinel
                self._queue.task_done()
                return
            with self._lock:
                self._active += 1
            try:
                job.result = job.fn(*job.args, **job.kwargs)
            except BaseException as exc:  # noqa: BLE001 - forwarded to caller
                job.error = exc
                logger.debug("AI job failed: %s", exc)
            finally:
                with self._lock:
                    self._active -= 1
                    self._completed += 1
                job.done.set()
                self._queue.task_done()

    # ------------------------------------------------------------------ #
    @property
    def active_count(self) -> int:
        """Number of jobs currently running (0 or 1 by construction)."""
        with self._lock:
            return self._active

    def qsize(self) -> int:
        return self._queue.qsize()

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "max_size": self.max_size,
                "pending": self._queue.qsize(),
                "active": self._active,
                "submitted": self._submitted,
                "completed": self._completed,
            }

    def close(self, wait: bool = False) -> None:
        """Stop accepting jobs; optionally wait for pending jobs to finish."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if wait:
            self._queue.join()
        if self._started:
            self._queue.put(None)
            self._worker.join(timeout=5.0)
