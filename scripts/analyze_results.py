"""
Read raw JSONL benchmark results and print a summary matrix across
platforms and workloads.

The raw JSONL files under results/raw/ are the source of truth; this
script only aggregates the LATEST record per (platform, workload) pair
for display -- it never recomputes or alters a percentile, since those
are already computed once by the runner at measurement time. A platform
with no data for a workload is shown as "NO DATA", never silently
omitted, so a failed or skipped run stays visible.

Usage:
    python3 scripts/analyze_results.py
"""

import glob
import json
import os

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "raw")

LATENCY_WORKLOADS = [
    "point_lookup",
    "indexed_lookup",
    "one_hop",
    "two_hop",
    "three_hop",
    "aggregation",
]


def load_latest_results() -> dict:
    latest = {}
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                key = (record["platform"], record["workload"])
                latest[key] = record
    return latest


def format_ms(value):
    return f"{value:.3f}" if isinstance(value, (int, float)) else "n/a"


def main():
    results = load_latest_results()
    platforms = sorted({k[0] for k in results})
    if not platforms:
        print(f"No raw results found under {RAW_DIR}")
        return

    print("=== Ingest ===")
    header = f"{'platform':<12} {'wall_s':>10} {'nodes/s':>12} {'rels/s':>12}"
    print(header)
    for platform in platforms:
        r = results.get((platform, "ingest"))
        if r is None:
            print(f"{platform:<12} NO DATA")
            continue
        print(
            f"{platform:<12} {r.get('wall_clock_seconds', 0):>10.3f} "
            f"{r.get('nodes_per_second') or 0:>12.1f} {r.get('relationships_per_second') or 0:>12.1f}"
        )

    for workload in LATENCY_WORKLOADS:
        print(f"\n=== {workload} (ms) ===")
        header = f"{'platform':<12} {'count':>7} {'errors':>7} {'p50':>10} {'p95':>10} {'mean':>10}"
        print(header)
        for platform in platforms:
            r = results.get((platform, workload))
            if r is None:
                print(f"{platform:<12} NO DATA")
                continue
            print(
                f"{platform:<12} {r.get('count', 0):>7} {r.get('errors', 0):>7} "
                f"{format_ms(r.get('p50_ms')):>10} {format_ms(r.get('p95_ms')):>10} "
                f"{format_ms(r.get('mean_ms')):>10}"
            )

    print("\n=== Concurrent read/write ===")
    header = f"{'platform':<12} {'concurrency':>11} {'reads/s':>10} {'writes/s':>10} {'errors':>8}"
    print(header)
    for platform in platforms:
        r = results.get((platform, "concurrent"))
        if r is None:
            print(f"{platform:<12} NO DATA")
            continue
        errors = (
            r.get("read_errors", 0)
            + r.get("write_errors", 0)
            + r.get("connection_errors", 0)
        )
        print(
            f"{platform:<12} {r.get('concurrency', 0):>11} "
            f"{r.get('reads_per_second') or 0:>10.2f} {r.get('writes_per_second') or 0:>10.2f} "
            f"{errors:>8}"
        )

    print("\n=== Resource footprint ===")
    for platform in platforms:
        r = results.get((platform, "footprint"))
        if r is None:
            print(f"{platform}: NO DATA")
            continue
        payload = {k: v for k, v in r.items() if k not in ("platform", "workload", "recorded_at")}
        print(f"{platform}: {json.dumps(payload, default=str)}")


if __name__ == "__main__":
    main()

