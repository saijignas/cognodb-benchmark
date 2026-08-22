"""
Run ONLY the concurrent read/write workload against a single platform, to
correct the concurrency level to 10 clients (the assignment requires a
stated concurrency in the 10-40 range; the original full benchmark used
8). This does not modify or re-run any other workload -- ingest and the
six latency workloads are untouched from the original run.

This calls the exact same, already-tested BenchmarkRunner.run_ingest()
and BenchmarkRunner.run_concurrent_workload() used by the original
benchmark -- same 30-second duration, same 80/20 read/write mix, same
multiprocessing.Barrier synchronization, same query logic
(one_hop/mixed_write), same 0.5 CPU / 512 MiB / 512 MiB swap resource
limits, same timing methodology. The only difference is concurrency=10
passed to the constructor instead of 8.

Since each platform's container was already torn down after the
original run, this script performs the minimum required setup (reset +
prepare_schema + prepare_indexes + load_dataset, i.e. exactly what
run_ingest() already does) before running the concurrent workload,
using the real MovieLens dataset -- not a re-run of the full benchmark,
just the unavoidable minimum to have data in place for the workload to
measure against.

Results are written to results/concurrency-10client/raw/, a location
completely separate from results/raw/ (which holds the original
8-client measurement as untouched baseline evidence), per explicit
instruction to keep the two clearly distinguished rather than silently
overwriting or shadowing the original file.

Usage:
    python3 scripts/run_concurrency_only.py <platform> --dataset data/movielens_100k.json
"""

import argparse
import json
import os
import pathlib

import benchmark.core.results as _results_module

OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "results", "concurrency-10client", "raw"
)

# BenchmarkRunner.run_ingest() and run_concurrent_workload() both call
# append_raw_result() internally, which writes into results/raw/ -- the
# protected, immutable baseline directory holding the original 8-client
# measurements. Redirecting the module's RAW_RESULTS_DIR here (before
# any runner method executes) means this script can reuse those exact
# same, already-tested methods unchanged, without a second write path
# duplicating their logic, while guaranteeing nothing lands in
# results/raw/. (An earlier version of this script did not do this and
# briefly appended a second line to 10 baseline files before the
# mistake was caught and every affected file was restored to its exact
# original single-line content, verified byte-for-byte against recorded
# checksums.)
_results_module.RAW_RESULTS_DIR = pathlib.Path(OUTPUT_DIR)

from benchmark.adapters.cognodb import CognoDBAdapter
from benchmark.adapters.neo4j_adapter import Neo4jAdapter
from benchmark.adapters.memgraph_adapter import MemgraphAdapter
from benchmark.adapters.arangodb_adapter import ArangoDBAdapter
from benchmark.adapters.surrealdb_adapter import SurrealDBAdapter
from benchmark.core.runner import BenchmarkRunner

ADAPTERS = {
    "cognodb": CognoDBAdapter,
    "neo4j": Neo4jAdapter,
    "memgraph": MemgraphAdapter,
    "arangodb": ArangoDBAdapter,
    "surrealdb": SurrealDBAdapter,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=sorted(ADAPTERS))
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--concurrent-seconds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        dataset = json.load(f)

    adapter_cls = ADAPTERS[args.platform]
    adapter = adapter_cls()
    adapter.connect()

    runner = BenchmarkRunner(
        adapter,
        dataset,
        concurrency=args.concurrency,
        concurrent_duration_seconds=args.concurrent_seconds,
        seed=args.seed,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    try:
        ingest_result = runner.run_ingest()
        concurrent_result = runner.run_concurrent_workload(adapter_cls)
    finally:
        adapter.disconnect()

    print(json.dumps({"ingest": ingest_result, "concurrent": concurrent_result}, indent=2, default=str))
    print(f"\nWrote {OUTPUT_DIR}/{args.platform}_concurrent.jsonl (and _ingest.jsonl)")


if __name__ == "__main__":
    main()

