"""
Download and parse the canonical MovieLens 100K dataset into the
users/movies/genres/ratings/genre_edges shape every adapter's
load_dataset() expects.

Source: https://files.grouplens.org/datasets/movielens/ml-100k.zip
(GroupLens Research, University of Minnesota).

Raw extracted files are cached under data/raw/ (gitignored) so re-running
this script doesn't re-download; the parsed dataset is written to
data/movielens_100k.json (also gitignored) so it is always regenerated
deterministically from the canonical source rather than trusted as a
committed artifact that could silently drift from it.
"""

import io
import json
import os
import urllib.request
import zipfile

DATASET_URL = "https://files.grouplens.org/datasets/movielens/ml-100k.zip"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
OUTPUT_PATH = os.path.join(DATA_DIR, "movielens_100k.json")


def download_and_extract():
    os.makedirs(RAW_DIR, exist_ok=True)
    marker = os.path.join(RAW_DIR, "ml-100k", "u.data")
    if os.path.exists(marker):
        print("Raw dataset already present at", marker, "- skipping download.")
        return
    print(f"Downloading {DATASET_URL} ...")
    with urllib.request.urlopen(DATASET_URL) as resp:
        payload = resp.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        zf.extractall(RAW_DIR)
    print("Extracted to", RAW_DIR)


def parse_genre_names(path):
    names = []
    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            name, _genre_id = line.rsplit("|", 1)
            names.append(name)
    return names


def parse_movies(path, genre_names):
    movies = []
    genre_edges = []
    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split("|")
            movie_id = int(fields[0])
            title = fields[1]
            flags = fields[5 : 5 + len(genre_names)]
            movies.append({"id": movie_id, "title": title})
            for genre_name, flag in zip(genre_names, flags):
                if flag == "1":
                    genre_edges.append({"movie_id": movie_id, "genre": genre_name})
    return movies, genre_edges


def parse_ratings(path):
    ratings = []
    users = set()
    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            user_id, movie_id, rating, timestamp = line.split("\t")
            users.add(int(user_id))
            ratings.append(
                {
                    "user_id": int(user_id),
                    "movie_id": int(movie_id),
                    "rating": int(rating),
                    "timestamp": int(timestamp),
                }
            )
    return ratings, users


def main():
    download_and_extract()
    base = os.path.join(RAW_DIR, "ml-100k")

    genre_names = parse_genre_names(os.path.join(base, "u.genre"))
    movies, genre_edges = parse_movies(os.path.join(base, "u.item"), genre_names)
    ratings, users = parse_ratings(os.path.join(base, "u.data"))

    dataset = {
        "users": sorted(users),
        "movies": movies,
        "genres": genre_names,
        "ratings": ratings,
        "genre_edges": genre_edges,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f)

    print("Wrote", OUTPUT_PATH)
    print("users:", len(dataset["users"]))
    print("movies:", len(dataset["movies"]))
    print("genres:", len(dataset["genres"]))
    print("ratings:", len(dataset["ratings"]))
    print("genre_edges:", len(dataset["genre_edges"]))


if __name__ == "__main__":
    main()
