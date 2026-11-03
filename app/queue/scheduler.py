"""Size- and age-aware job ordering shared by every storage engine.

The scheduler is deliberately pure: it takes a job row (dict), a clock and two
policy knobs and returns a small sortable tuple. Keeping it free of DB/async
code makes the ordering policy unit-testable and keeps SQLite, MySQL and
PostgreSQL on exactly the same rules.

Policy (lower tuple wins):

1. ``overdue``: a job that has waited ``max_wait_s`` is promoted ahead of every
   non-overdue job (oldest overdue first). This is the starvation guard.
2. kind band: download > upload > transfer > delete (interactive work first).
3. lane: small uploads/transfers before bulk ones, so a 1 MB file cannot sit
   behind a 10 GB file at the head of the queue.
4. size: smallest first inside a lane (SJF), then FIFO ``seq``.

The aging guard is intentionally time-based, not a score that can be swamped by
size: once a job is overdue it competes only on age.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

KIND_RANK = {"download": 0, "upload": 1, "transfer": 2, "delete": 3}
_UNKNOWN_RANK = 9

#: jobs in these kinds are split into small/bulk lanes by size
_SIZED_KINDS = ("upload", "transfer")


def job_size(row: Dict[str, Any]) -> int:
    """Best-effort size for a job row (column first, payload fallback)."""
    raw = row.get("size_bytes")
    if raw not in (None, ""):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    payload = row.get("payload")
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload or "{}")
        except Exception:
            payload = {}
    if isinstance(payload, dict):
        try:
            return max(0, int(payload.get("size") or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def lane_for(row: Dict[str, Any], small_max_bytes: int) -> str:
    """Human/panel lane label derived from kind + size (no DB column needed)."""
    kind = str(row.get("kind") or "")
    if kind == "download":
        return "interactive"
    if kind == "delete":
        return "control"
    size = job_size(row)
    if small_max_bytes > 0 and 0 < size <= small_max_bytes:
        return "small"
    return "bulk"


def pick_key(
    row: Dict[str, Any],
    *,
    now: float,
    max_wait_s: float,
    small_max_bytes: int,
) -> Tuple[int, int, int, int, float]:
    """Return the ordering tuple for one job row.

    ``now`` is injected so tests can age jobs deterministically. ``max_wait_s``
    <= 0 disables the starvation guard.
    """
    created = float(row.get("created_at") or 0.0)
    if max_wait_s > 0 and created > 0 and (now - created) >= max_wait_s:
        # Overdue: the oldest overdue job wins, regardless of kind/size.
        return (0, 0, 0, 0, created)

    kind = str(row.get("kind") or "")
    size = job_size(row)
    small_rank = 1
    if kind in _SIZED_KINDS and small_max_bytes > 0 and 0 < size <= small_max_bytes:
        small_rank = 0
    return (
        1,
        KIND_RANK.get(kind, _UNKNOWN_RANK),
        small_rank,
        size,
        float(row.get("seq") or 0),
    )


def pick_best(
    rows: Iterable[Dict[str, Any]],
    *,
    now: float,
    max_wait_s: float,
    small_max_bytes: int,
) -> Optional[Dict[str, Any]]:
    """Pick the next row from a small candidate window (None when empty)."""
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, int, int, int, float]] = None
    for row in rows:
        key = pick_key(row, now=now, max_wait_s=max_wait_s, small_max_bytes=small_max_bytes)
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


def is_overdue(
    row: Dict[str, Any], *, now: float, max_wait_s: float
) -> bool:
    created = float(row.get("created_at") or 0.0)
    return max_wait_s > 0 and created > 0 and (now - created) >= max_wait_s
