"""Deterministic virtual clock and event queue.

All time in the simulator is virtual. No wall-clock (``time.sleep``) is ever
used: events are scheduled with a delay and executed in
(virtual-time, insertion-order) order, so identical inputs always produce
identical results.
"""

import heapq
import itertools


class VirtualClock:
    """A monotonic virtual clock backed by a FIFO-stable min-heap."""

    def __init__(self):
        self._now = 0.0
        self._queue = []
        # Tie-breaker counter: events scheduled for the same virtual time
        # execute in FIFO (insertion) order.
        self._counter = itertools.count()

    @property
    def now(self):
        return self._now

    def schedule(self, delay, callback, *args):
        """Schedule ``callback(*args)`` to run ``delay`` time units from now."""
        if delay < 0:
            raise ValueError("cannot schedule an event in the past")
        self.schedule_at(self._now + delay, callback, *args)

    def schedule_at(self, when, callback, *args):
        """Schedule ``callback(*args)`` at absolute virtual time ``when``."""
        if when < self._now:
            raise ValueError("cannot schedule an event in the past")
        heapq.heappush(self._queue, (when, next(self._counter), callback, args))

    def peek_time(self):
        """Virtual time of the next pending event, or None if queue is empty."""
        return self._queue[0][0] if self._queue else None

    def has_events(self):
        return bool(self._queue)

    def run_next(self):
        """Pop and execute the next event. Returns False if queue is empty."""
        if not self._queue:
            return False
        when, _, callback, args = heapq.heappop(self._queue)
        if when < self._now:  # monotonicity guard, should never trigger
            raise RuntimeError("virtual clock moved backwards")
        self._now = when
        callback(*args)
        return True
