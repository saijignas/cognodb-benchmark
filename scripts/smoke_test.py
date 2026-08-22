"""
Generalized correctness smoke test, run against one platform's own live
connection at a time. Loads a small, hand-verified synthetic dataset and
asserts every GraphDBAdapter method against exact expected values --
"the query executed without error" is not treated as a pass anywhere in
this file; every check compares against a value worked out by hand from
the dataset below.

Usage:
    python3 scripts/smoke_test.py <platform>

<platform> is one of: cognodb, neo4j, memgraph, arangodb, surrealdb

Credentials are read exclusively from the environment variables each
platform's config function in benchmark/core/config.py expects. This
script never accepts, prints, or logs a credential itself -- for Neo4j,
Memgraph, and ArangoDB it is expected to point at a local, disposable
container started with a throwaway test password; for CognoDB it is
expected to point at the real instance via variables injected from
GitHub Codespaces/Repository secrets, never typed or pasted manually.

Synthetic dataset used by every platform:

    users:  1, 2, 3
    movies: 10 "Alpha", 20 "Beta"
    genres: "Action", "Film-Noir"
    ratings: 1->10(5), 2->10(4), 2->20(3), 3->20(2)
    genre_edges: 10-Action, 20-Film-Noir

Hand-derived expected results (two_hop/three_hop per the common workload
definition in base.py: two_hop is other users, excluding the start user,
who share a rated movie; three_hop is movies rated by that user set):
    one_hop(2)    -> movies user 2 rated                          = [10, 20]
    two_hop(1)    -> user 1 rated movie 10; other users who also
                     rated movie 10 (excluding user 1 itself)      = [2]
    three_hop(1)  -> movies rated by two_hop(1)'s set ({2}):
                     user 2 rated 10 and 20                        = [10, 20]
    two_hop(2)    -> user 2 rated {10, 20}; other users who rated
                     10 (user 1) or 20 (user 3), excluding user 2  = [1, 3]
    three_hop(2)  -> movies rated by two_hop(2)'s set ({1, 3}):
                     user 1 rated 10, user 3 rated 20              = [10, 20]
    point_lookup(3)          -> {"id": 3}
    indexed_lookup("Beta")   -> [20]
    aggregation()            -> Action: 1, Film-Noir: 1
    mixed_write(3, 10, 1) then one_hop(3) -> [10, 20]  (3 already rated 20)
"""

import sys

from benchmark.adapters.cognodb import CognoDBAdapter
from benchmark.adapters.neo4j_adapter import Neo4jAdapter
from benchmark.adapters.memgraph_adapter import MemgraphAdapter
from benchmark.adapters.arangodb_adapter import ArangoDBAdapter
from benchmark.adapters.surrealdb_adapter import SurrealDBAdapter

ADAPTERS = {
    "cognodb": CognoDBAdapter,
    "neo4j": Neo4jAdapter,
    "memgraph": MemgraphAdapter,
    "arangodb": ArangoDBAdapter,
    "surrealdb": SurrealDBAdapter,
}

USERS = [1, 2, 3]
MOVIES = [
    {"id": 10, "title": "Alpha"},
    {"id": 20, "title": "Beta"},
]
GENRES = ["Action", "Film-Noir"]
RATINGS = [
    {"user_id": 1, "movie_id": 10, "rating": 5, "timestamp": 0},
    {"user_id": 2, "movie_id": 10, "rating": 4, "timestamp": 0},
    {"user_id": 2, "movie_id": 20, "rating": 3, "timestamp": 0},
    {"user_id": 3, "movie_id": 20, "rating": 2, "timestamp": 0},
]
GENRE_EDGES = [
    {"movie_id": 10, "genre": "Action"},
    {"movie_id": 20, "genre": "Film-Noir"},
]


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def run(adapter):
    ok = True
    ok &= check("health_check", adapter.health_check() is True)

    adapter.reset()
    adapter.prepare_schema()
    adapter.prepare_indexes()

    load_result = adapter.load_dataset(USERS, MOVIES, GENRES, RATINGS, GENRE_EDGES)
    ok &= check(
        "load_dataset counts",
        load_result["nodes_loaded"] == len(USERS) + len(MOVIES) + len(GENRES)
        and load_result["relationships_loaded"] == len(RATINGS) + len(GENRE_EDGES),
    )
    print("  load_result:", load_result)

    adapter.warmup(2)

    one = adapter.one_hop(2)
    ok &= check("one_hop(2) == [10, 20]", sorted(one) == [10, 20])
    print("  one_hop(2):", one)

    two = adapter.two_hop(1)
    ok &= check("two_hop(1) == [2]", sorted(two) == [2])
    print("  two_hop(1):", two)

    three = adapter.three_hop(1)
    ok &= check("three_hop(1) == [10, 20]", sorted(three) == [10, 20])
    print("  three_hop(1):", three)

    two_b = adapter.two_hop(2)
    ok &= check("two_hop(2) == [1, 3]", sorted(two_b) == [1, 3])
    print("  two_hop(2):", two_b)

    three_b = adapter.three_hop(2)
    ok &= check("three_hop(2) == [10, 20]", sorted(three_b) == [10, 20])
    print("  three_hop(2):", three_b)

    pl = adapter.point_lookup(3)
    ok &= check("point_lookup(3) == {'id': 3}", pl == {"id": 3})
    print("  point_lookup(3):", pl)

    il = adapter.indexed_lookup("Beta")
    ok &= check("indexed_lookup('Beta') == [20]", sorted(il) == [20])
    print("  indexed_lookup('Beta'):", il)

    agg = adapter.aggregation()
    agg_map = {row["genre"]: row["count"] for row in agg}
    ok &= check(
        "aggregation genre counts (Action=1, Film-Noir=1)",
        agg_map.get("Action") == 1 and agg_map.get("Film-Noir") == 1,
    )
    print("  aggregation:", agg)

    adapter.mixed_write(3, 10, 1)
    one_after = adapter.one_hop(3)
    ok &= check(
        "mixed_write reflected in one_hop(3) == [10, 20]",
        sorted(one_after) == [10, 20],
    )
    print("  one_hop(3) after mixed_write:", one_after)

    fp = adapter.get_footprint()
    ok &= check("get_footprint returns dict", isinstance(fp, dict))

    return ok


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ADAPTERS:
        print(f"Usage: python3 {sys.argv[0]} <{'|'.join(ADAPTERS)}>")
        sys.exit(2)

    platform = sys.argv[1]
    adapter = ADAPTERS[platform]()
    adapter.connect()
    try:
        ok = run(adapter)
    finally:
        adapter.disconnect()

    print()
    print(f"{platform}: ALL PASS" if ok else f"{platform}: SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

