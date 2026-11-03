"""Resizable per-backend in-flight limiter.

A plain ``asyncio.Semaphore`` cannot be resized live and cannot report useful
per-direction occupancy. This tiny primitive can:

- acquire without waiting (``timeout=0``) for the scheduler's "try this backend"
  loop,
- wait with a deadline for callers that prefer queueing,
- change capacity while calls are in flight (settings change / pool change),
- expose ``inflight`` and ``free`` for health and least-loaded selection.

It is intentionally per-process. Cross-node account leases are a later phase;
correctness here is about one worker pool sharing a few accounts.
"""
from __future__ import annotations

import asyncio
import time


class SlotLimiter:
    def __init__(self, cap: int = 1) -> None:
        self.cap = max(1, int(cap))
        self.inflight = 0
        self._wake = asyncio.Event()

    @property
    def free(self) -> int:
        return max(0, self.cap - self.inflight)

    def set_cap(self, cap: int) -> None:
        self.cap = max(1, int(cap))
        self._wake.set()

    async def acquire(self, timeout: float = 0.0) -> bool:
        """Take a slot; returns False on timeout.

        ``timeout <= 0`` is a single non-blocking attempt. The check and the
        increment happen without an ``await`` between them, so two tasks on one
        event loop can never both take the last slot.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if self.inflight < self.cap:
                self.inflight += 1
                return True
            if timeout <= 0:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return False

    def release(self) -> None:
        if self.inflight > 0:
            self.inflight -= 1
        self._wake.set()
