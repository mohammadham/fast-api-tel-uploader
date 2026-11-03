"""Per-subject concurrent stream caps.

A slow client must not pin every download slot: without a cap, one API key or
public link can open dozens of range requests and starve the pool. Slots are
per-process (the queue's DB lease remains the cross-node authority); the cap is
small and configurable at runtime.
"""
from __future__ import annotations

from typing import Dict

from ..tg.limiter import SlotLimiter


class StreamLimiter:
    def __init__(self) -> None:
        self._slots: Dict[str, SlotLimiter] = {}

    def _slot(self, key: str, cap: int) -> SlotLimiter:
        lim = self._slots.get(key)
        if lim is None:
            lim = SlotLimiter(max(1, int(cap)))
            self._slots[key] = lim
        else:
            lim.set_cap(max(1, int(cap)))
        return lim

    async def acquire(self, key: str, cap: int, timeout: float = 0.0) -> bool:
        if not key:
            return True
        return await self._slot(key, cap).acquire(timeout=timeout)

    def release(self, key: str) -> None:
        lim = self._slots.get(key)
        if lim is not None:
            lim.release()

    def inflight(self, key: str) -> int:
        lim = self._slots.get(key)
        return lim.inflight if lim is not None else 0

    def stats(self) -> Dict[str, int]:
        return {k: v.inflight for k, v in self._slots.items() if v.inflight}


streams = StreamLimiter()
