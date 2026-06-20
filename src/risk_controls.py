"""
Risk Control Components — Debounce, Rate Limiter & Timeout Guard

Generic building blocks for safe automated trading.

These components are strategy-agnostic — they implement common patterns
for preventing erroneous signals and runaway behavior:

- ``Debounce``: requires N consecutive triggers within a time window
  before confirming a signal.  Reduces false positives from transient
  market noise.
- ``RateLimiter``: caps the number of operations within a sliding
  time window.  Prevents order-spam in fast-moving markets.
- ``TimeoutGuard``: tracks pending operations and auto-resets them
  after a configurable timeout.  Prevents deadlocks when async
  confirmations are lost.
"""

import time
import logging
import threading
from collections import deque
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ======================================================================
# Debounce
# ======================================================================

class Debounce:
    """
    Require *threshold* consecutive activations inside a *window*
    before confirming a signal.

    Think of it as a "are you sure?" filter: a single outlier tick
    resets the counter, and only a sustained condition reaches the
    threshold.

    Parameters
    ----------
    threshold:
        Number of consecutive hits required.
    window_seconds:
        Time window (seconds) within which the hits must occur.
        Expired windows reset the counter.
    on_confirmed:
        Optional callback invoked when the threshold is reached.

    Example
    -------
    >>> deb = Debounce(threshold=3, window_seconds=5)
    >>> for price_pulse in [True, True, False, True, True, True]:
    ...     if deb.record(price_pulse):
    ...         print("Signal confirmed!")
    """

    def __init__(
        self,
        threshold: int = 3,
        window_seconds: float = 5.0,
        on_confirmed: Optional[Callable[[], None]] = None,
    ):
        self.threshold = threshold
        self.window = window_seconds
        self._on_confirmed = on_confirmed

        self._counter: int = 0
        self._window_start: float = time.time()
        self._lock = threading.Lock()

    def record(self, condition: bool) -> bool:
        """
        Record a new observation.

        Parameters
        ----------
        condition:
            ``True`` if the monitored condition is currently active.

        Returns
        -------
        ``True`` when the threshold has been reached.
        """
        with self._lock:
            now = time.time()

            # Reset window if expired
            if now - self._window_start > self.window:
                self._counter = 0
                self._window_start = now

            if condition:
                self._counter += 1
                logger.debug("Debounce: %d / %d", self._counter, self.threshold)

                if self._counter >= self.threshold:
                    self._counter = 0
                    self._window_start = now
                    logger.info("Debounce threshold reached (%d hits)", self.threshold)
                    if self._on_confirmed:
                        self._on_confirmed()
                    return True
            else:
                # Reset on any non-trigger observation
                if self._counter > 0:
                    logger.debug("Debounce reset (counter was %d)", self._counter)
                self._counter = 0
                self._window_start = now

            return False

    @property
    def count(self) -> int:
        """Current consecutive-hit count."""
        with self._lock:
            return self._counter

    def reset(self) -> None:
        """Manually reset the counter and window."""
        with self._lock:
            self._counter = 0
            self._window_start = time.time()


# ======================================================================
# RateLimiter
# ======================================================================

class RateLimiter:
    """
    Limit the frequency of operations.

    Ensures that no more than *max_operations* are performed within
    any *window_seconds* rolling window.

    Parameters
    ----------
    max_operations:
        Maximum number of allowed operations per window.
    window_seconds:
        Length of the sliding window in seconds.

    Example
    -------
    >>> rl = RateLimiter(max_operations=3, window_seconds=60)
    >>> for i in range(5):
    ...     if rl.allow():
    ...         print(f"Operation {i} allowed")
    ...     else:
    ...         print(f"Operation {i} BLOCKED")
    """

    def __init__(self, max_operations: int = 3, window_seconds: float = 60.0):
        self.max_ops = max_operations
        self.window = window_seconds
        self._timestamps: deque = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """
        Check whether a new operation is allowed.

        Returns ``True`` if under the limit, ``False`` if throttled.
        """
        with self._lock:
            now = time.time()

            # Purge timestamps outside the window
            while self._timestamps and self._timestamps[0] < now - self.window:
                self._timestamps.popleft()

            if len(self._timestamps) >= self.max_ops:
                logger.warning(
                    "Rate limit hit: %d ops in last %.0f s (max %d)",
                    len(self._timestamps),
                    self.window,
                    self.max_ops,
                )
                return False

            self._timestamps.append(now)
            logger.debug("Rate limiter: %d / %d", len(self._timestamps), self.max_ops)
            return True

    def reset(self) -> None:
        """Clear all recorded timestamps (re-opens the window)."""
        with self._lock:
            self._timestamps.clear()

    @property
    def count(self) -> int:
        """Number of operations recorded in the current window."""
        with self._lock:
            return len(self._timestamps)


# ======================================================================
# TimeoutGuard
# ======================================================================

class TimeoutGuard:
    """
    Track a pending operation and auto-reset it after a timeout.

    Useful for async workflows where an operation is initiated, a
    confirmation is expected, but the confirmation may never arrive
    (e.g. network hiccup, TWS restart).  The guard prevents the
    system from getting stuck waiting forever.

    Parameters
    ----------
    timeout_seconds:
        Maximum time to wait for confirmation before resetting.
    on_timeout:
        Optional callback invoked when the timeout fires.
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        on_timeout: Optional[Callable[[str], None]] = None,
    ):
        self.timeout = timeout_seconds
        self._on_timeout = on_timeout

        self._pending_label: Optional[str] = None
        self._pending_ts: float = 0.0
        self._lock = threading.Lock()

    def start(self, label: str = "operation") -> bool:
        """
        Mark an operation as pending.

        Returns ``False`` if another operation is already pending
        and hasn't timed out yet.
        """
        with self._lock:
            now = time.time()
            if self._pending_label is not None:
                if now - self._pending_ts < self.timeout:
                    logger.warning(
                        "Operation '%s' still pending (timeout in %.0f s)",
                        self._pending_label,
                        self.timeout - (now - self._pending_ts),
                    )
                    return False
                else:
                    # Previous operation timed out — auto-reset
                    logger.warning(
                        "Operation '%s' timed out, auto-resetting",
                        self._pending_label,
                    )
                    if self._on_timeout:
                        self._on_timeout(self._pending_label)

            self._pending_label = label
            self._pending_ts = now
            logger.debug("TimeoutGuard: '%s' started", label)
            return True

    def finish(self) -> None:
        """Confirm that the pending operation completed successfully."""
        with self._lock:
            if self._pending_label:
                logger.debug("TimeoutGuard: '%s' completed", self._pending_label)
            self._pending_label = None
            self._pending_ts = 0.0

    def reset(self) -> None:
        """Force-cancel any pending operation."""
        with self._lock:
            self._pending_label = None
            self._pending_ts = 0.0

    @property
    def is_pending(self) -> bool:
        """Whether an operation is currently pending."""
        with self._lock:
            if self._pending_label is None:
                return False
            if time.time() - self._pending_ts > self.timeout:
                # Expired — treat as not pending
                return False
            return True

    @property
    def pending_label(self) -> Optional[str]:
        """Label of the currently pending operation, if any."""
        return self._pending_label


# ======================================================================
# OperationQueue
# ======================================================================

import queue as _queue


class OperationQueue:
    """
    A thread-safe queue for serializing trading operations.

    Ensures operations are processed one-at-a-time and never overlap.
    Useful when multiple market conditions could trigger competing
    actions (e.g. price ⬇ triggers stop-loss while price ⬈ triggers
    take-profit).

    Parameters
    ----------
    processor:
        Callable that processes each operation.
    """

    def __init__(self, processor: Optional[Callable[[dict], None]] = None):
        self._queue: _queue.Queue = _queue.Queue()
        self._processor = processor

    def enqueue(self, op_type: str, **params) -> None:
        """Add an operation to the queue."""
        self._queue.put({"type": op_type, "timestamp": time.time(), "params": params})
        logger.info("Operation enqueued: %s", op_type)

    def process_all(self, guard: Optional[TimeoutGuard] = None) -> None:
        """
        Drain the queue, processing each operation via the *processor*
        callback or the default loop.

        If a *guard* is provided, processing pauses while the guard
        reports a pending operation.
        """
        while not self._queue.empty():
            if guard is not None and guard.is_pending:
                logger.debug("Pending operation, deferring queue processing")
                break

            try:
                op = self._queue.get_nowait()
                if self._processor:
                    self._processor(op)
                else:
                    logger.info("No processor registered — operation dropped: %s", op["type"])
            except _queue.Empty:
                break
            except Exception as exc:
                logger.error("Queue processing error: %s", exc)

    @property
    def size(self) -> int:
        """Number of pending operations in the queue."""
        return self._queue.qsize()
