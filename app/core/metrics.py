"""Minimal Prometheus-style metrics registry (no external dependency)."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List


class Metrics:
    def __init__(self) -> None:
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._start = time.time()

    def inc(self, name: str, value: float = 1.0) -> None:
        self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def observe(self, name: str, value: float, keep_last: int = 500) -> None:
        values = self._histograms[name]
        values.append(value)
        if len(values) > keep_last:
            del values[: len(values) - keep_last]

    def render(self) -> str:
        lines = [
            "# HELP tgdrive_uptime_seconds process uptime",
            "# TYPE tgdrive_uptime_seconds gauge",
            f"tgdrive_uptime_seconds {time.time() - self._start:.0f}",
        ]
        for name, value in sorted(self._counters.items()):
            safe = name.replace(".", "_")
            lines.append(f"# TYPE {safe}_total counter")
            lines.append(f"{safe}_total {value}")
        for name, value in sorted(self._gauges.items()):
            safe = name.replace(".", "_")
            lines.append(f"# TYPE {safe} gauge")
            lines.append(f"{safe} {value}")
        for name, values in sorted(self._histograms.items()):
            safe = name.replace(".", "_")
            if not values:
                continue
            ordered = sorted(values)
            count = len(ordered)
            lines.append(f"# TYPE {safe} summary")
            lines.append(f'{safe}{{quantile="0.5"}} {ordered[count // 2]}')
            lines.append(f'{safe}{{quantile="0.95"}} {ordered[min(count - 1, int(count * 0.95))]}')
            lines.append(f"{safe}_sum {sum(values)}")
            lines.append(f"{safe}_count {count}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
