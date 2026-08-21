"""
Smoke test for SurrealDBAdapter against a real, ephemeral, resource-capped
container. Not part of the pytest suite (it needs Docker + a live network
port), run manually:

    SURREALDB_URI=ws://localhost:8000 \
    SURREALDB_USER=root \
    SURREALDB_PASSWORD=testpassword123 \
    SURREALDB_NS=benchmark \
    SURREALDB_DB=benchmark \
    python3 scripts/smoke_surrealdb.py

Credentials here are throwaway values for a local disposable container,
never the real platform credentials, and never committed anywhere.
"""

import sys

from benchmark.adapters.surrealdb_adapter import SurrealDBAdapter


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def main():
    ok = True
    adapter = SurrealDBAdapter()
    adapter.connect()

    try:
        ok &= check("health_check", adapter.health_check() is True)

        adapter.reset()
        adapter.prepare_schema()
        adapter.prepare_indexes()

        # Small synthetic dataset, deliberately shaped to exercise every
        # workload: a genre name with punctuation (Film-Noir) to confirm
        # RecordID sidesteps SurrealQL literal-parsing issues for real.
        users = [1, 2, 3]
        movies = [
            {"id": 10, "title": "Alpha"},
            {"id": 20, "title": "Beta"},
        ]
        genres = ["Action", "Film-Noir"]
        ratings = [
            {"user_id": 1, "movie_id": 10, "rating": 5, "timestamp": 0},
            {"user_id": 2, "movie_id": 10, "rating": 4, "timestamp": 0},
            {"user_id": 2, "movie_id": 20, "rating": 3, "timestamp": 0},
            {"user_id": 3, "movie_id": 20, "rating": 2, "timestamp": 0},
        ]
        genre_edges = [
            {"movie_id": 10, "genre": "Action"},
            {"movie_id": 20, "genre": "Film-Noir"},
        ]

        load_result = adapter.load_dataset(users, movies, genres, ratings, genre_edges)
        ok &= check(
            "load_dataset counts",
            load_result["nodes_loaded"] == len(users) + len(movies) + len(genres)
            and load_result["relationships_loaded"] == len(ratings) + len(genre_edges),
        )
        print("  load_result:", load_result)

        adapter.warmup(2)

        one = adapter.one_hop(2)
        ok &= check("one_hop(2) == [10, 20]", sorted(one) == [10, 20])
        print("  one_hop(2):", one)

        two = adapter.two_hop(1)
        # user 1 rated movie 10; users who also rated movie 10: user 1 itself, user 2.
        ok &= check("two_hop(1) == [1, 2]", two == [1, 2])
        print("  two_hop(1):", two)

        three = adapter.three_hop(1)
        # user1->rated->m10<-rated<-{user1,user2}->rated->movie:
        #   via user1 (self-loop): m10 again
        #   via user2: m10 and m20
        # dedup => {10, 20}
        ok &= check("three_hop(1) == [10, 20]", three == [10, 20])
        print("  three_hop(1):", three)

        pl = adapter.point_lookup(3)
        ok &= check("point_lookup(3) == {'id': 3}", pl == {"id": 3})
        print("  point_lookup(3):", pl)

        il = adapter.indexed_lookup("Beta")
        ok &= check("indexed_lookup('Beta') == [20]", il == [20])
        print("  indexed_lookup('Beta'):", il)

        agg = adapter.aggregation()
        agg_map = {row["genre"]: row["count"] for row in agg}
        ok &= check(
            "aggregation genre counts",
            agg_map.get("Action") == 1 and agg_map.get("Film-Noir") == 1,
        )
        print("  aggregation:", agg)

        adapter.mixed_write(3, 10, 1)
        one_after = adapter.one_hop(3)
        ok &= check("mixed_write reflected in one_hop(3)", sorted(one_after) == [10, 20])
        print("  one_hop(3) after mixed_write:", one_after)

        fp = adapter.get_footprint()
        ok &= check("get_footprint returns dict", isinstance(fp, dict))

    finally:
        adapter.disconnect()

    print()
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
