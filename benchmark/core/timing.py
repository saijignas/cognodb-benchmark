import statistics
from dataclasses import dataclass, field


@dataclass
class TimedRun:
    label: str
    latencies_ms: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def record(self, latency_ms: float) -> None:
        self.latencies_ms.append(latency_ms)

    def record_error(self, message: str) -> None:
        self.errors.append(message)

    def summary(self) -> dict:
        if not self.latencies_ms:
            return {
                "label": self.label,
                "count": 0,
                "errors": len(self.errors),
                "p50_ms": None,
                "p95_ms": None,
                "mean_ms": None,
                "min_ms": None,
                "max_ms": None,
                "stddev_ms": None,
            }
        sorted_lat = sorted(self.latencies_ms)
        return {
            "label": self.label,
            "count": len(sorted_lat),
            "errors": len(self.errors),
            "p50_ms": percentile(sorted_lat, 50),
            "p95_ms": percentile(sorted_lat, 95),
            "mean_ms": statistics.mean(sorted_lat),
            "min_ms": sorted_lat[0],
            "max_ms": sorted_lat[-1],
            "stddev_ms": statistics.pstdev(sorted_lat) if len(sorted_lat) > 1 else 0.0,
        }


def percentile(sorted_values: list, pct: float) -> float:
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)
