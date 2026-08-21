"""
Validate the preprocessed MovieLens 100K dataset against known-correct
counts before any of it ever reaches a database.

Expected counts below were independently cross-checked against
GroupLens's own u.info file during earlier manual verification (943
users, 1682 items, 100000 ratings) plus a manual tally of genre edges and
the 'unknown' genre's usage. This script re-derives everything from the
freshly parsed data/movielens_100k.json rather than trusting memory, and
exits non-zero with the exact mismatch printed on any failure -- it never
silently accepts unexpected data.
"""

import json
import os
import sys

DATASET_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "movielens_100k.json")

EXPECTED_USERS = 943
EXPECTED_MOVIES = 1682
EXPECTED_GENRES = 19
EXPECTED_RATINGS = 100000
EXPECTED_GENRE_EDGES = 2893
EXPECTED_UNKNOWN_GENRE_MOVIES = 2


def check(label, actual, expected):
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}: expected={expected} actual={actual}")
    return ok


def main():
    if not os.path.exists(DATASET_PATH):
        print(f"FAIL: {DATASET_PATH} does not exist. Run preprocess_movielens.py first.")
        sys.exit(1)

    with open(DATASET_PATH, encoding="utf-8") as f:
        dataset = json.load(f)

    users = dataset["users"]
    movies = dataset["movies"]
    genres = dataset["genres"]
    ratings = dataset["ratings"]
    genre_edges = dataset["genre_edges"]

    ok = True
    ok &= check("user count", len(users), EXPECTED_USERS)
    ok &= check("movie count", len(movies), EXPECTED_MOVIES)
    ok &= check("genre count", len(genres), EXPECTED_GENRES)
    ok &= check("rating count", len(ratings), EXPECTED_RATINGS)
    ok &= check("genre_edge count", len(genre_edges), EXPECTED_GENRE_EDGES)
    ok &= check(
        "total nodes",
        len(users) + len(movies) + len(genres),
        EXPECTED_USERS + EXPECTED_MOVIES + EXPECTED_GENRES,
    )
    ok &= check(
        "total relationships",
        len(ratings) + len(genre_edges),
        EXPECTED_RATINGS + EXPECTED_GENRE_EDGES,
    )

    unknown_movie_count = sum(1 for e in genre_edges if e["genre"] == "unknown")
    ok &= check(
        "movies tagged 'unknown' genre",
        unknown_movie_count,
        EXPECTED_UNKNOWN_GENRE_MOVIES,
    )

    movie_ids = {m["id"] for m in movies}
    user_ids = set(users)

    seen_pairs = set()
    dup_pairs = set()
    for r in ratings:
        key = (r["user_id"], r["movie_id"])
        if key in seen_pairs:
            dup_pairs.add(key)
        seen_pairs.add(key)
    ok &= check("duplicate (user,movie) rating pairs", len(dup_pairs), 0)

    dangling_rating_refs = sum(
        1
        for r in ratings
        if r["user_id"] not in user_ids or r["movie_id"] not in movie_ids
    )
    ok &= check("ratings referencing missing user/movie", dangling_rating_refs, 0)

    dangling_genre_movie_refs = sum(
        1 for e in genre_edges if e["movie_id"] not in movie_ids
    )
    ok &= check(
        "genre_edges referencing missing movie", dangling_genre_movie_refs, 0
    )

    genre_names = set(genres)
    dangling_genre_names = sum(1 for e in genre_edges if e["genre"] not in genre_names)
    ok &= check(
        "genre_edges referencing unknown genre name", dangling_genre_names, 0
    )

    print()
    print("DATASET VALID" if ok else "DATASET INVALID")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
