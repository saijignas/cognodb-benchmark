"""
Run the full benchmark suite against one platform.

Usage:
    python3 scripts/run_benchmark.py <platform> --dataset data/movielens_100k.json

<platform> is one of: cognodb, neo4j, memgraph, arangodb, surrealdb

Credentials are read exclusively from each platform's environment
variables (see benchmark/core/config.py) -- this script never accepts,
prints, or logs a credential itself.

Every workload's raw results are appended to results/raw/<platform>_<workload>.jsonl
as they run (see benchmark/core/results.py); the JSON printed to stdout
at the end is a convenience summary of the same data, not a separate
source of truth.
"""

import argparse
import json

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
    parser.add_argument(
        "--dataset", required=True, help="Path to a preprocessed dataset JSON file"
    )
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=100)
    # Default concurrency (8) matches the original full-benchmark run
    # recorded in results/raw/. The final-reported concurrency correction
    # (10 clients) is applied via scripts/run_concurrency_only.py, or by
    # passing --concurrency 10 explicitly (see README Section 8).
    parser.add_argument("--concurrency", type=int, default=8)
    # 30 seconds matches every recorded run (results/raw/*_concurrent.jsonl
    # all show duration_seconds=30); this default previously said 10 and
    # was never actually used to produce a reported measurement.
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
        warmup_iterations=args.warmup,
        measured_iterations=args.iterations,
        concurrency=args.concurrency,
        concurrent_duration_seconds=args.concurrent_seconds,
        seed=args.seed,
    )

    try:
        results = runner.run_all(adapter_cls)
    finally:
        adapter.disconnect()

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()

