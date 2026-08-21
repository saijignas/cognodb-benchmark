import time

from surrealdb import Surreal

from ..core.config import surrealdb_config
from .base import GraphDBAdapter


class SurrealDBAdapter(GraphDBAdapter):
    name = "surrealdb"

    def __init__(self):
        self._cfg = surrealdb_config()
        self._db = None

    def connect(self) -> None:
        self._db = Surreal(self._cfg.uri)
        self._db.signin({"username": self._cfg.user, "password": self._cfg.password})
        self._db.use(self._cfg.namespace, self._cfg.database)

    def disconnect(self) -> None:
        if self._db:
            self._db.close()

    def health_check(self) -> bool:
        result = self._db.query("RETURN 1")
        return result[0]["result"] == 1

    def reset(self) -> None:
        self._db.query(
            "REMOVE TABLE IF EXISTS user; REMOVE TABLE IF EXISTS movie; "
            "REMOVE TABLE IF EXISTS genre; REMOVE TABLE IF EXISTS rated; "
            "REMOVE TABLE IF EXISTS has_genre;"
        )

    def prepare_schema(self) -> None:
        pass  # SurrealDB tables are created implicitly on first write (SCHEMALESS by default).

    def prepare_indexes(self) -> None:
        self._db.query("DEFINE INDEX movie_title ON movie FIELDS title")
        self._db.query("DEFINE INDEX genre_name ON genre FIELDS name")

    def load_dataset(self, users, movies, genres, ratings, genre_edges) -> dict:
        start = time.time()
        for u in users:
            self._db.create(f"user:{u}", {"id": u})
        for m in movies:
            self._db.create(f"movie:{m['id']}", {"id": m["id"], "title": m["title"]})
        for g in genres:
            self._db.create(f"genre:{g}", {"name": g})
        for r in ratings:
            self._db.query(
                "RELATE $user->rated->$movie SET rating = $rating, timestamp = $ts",
                {
                    "user": f"user:{r['user_id']}",
                    "movie": f"movie:{r['movie_id']}",
                    "rating": r["rating"],
                    "ts": r["timestamp"],
                },
            )
        for e in genre_edges:
            self._db.query(
                "RELATE $movie->has_genre->$genre",
                {"movie": f"movie:{e['movie_id']}", "genre": f"genre:{e['genre']}"},
            )
        end = time.time()
        return {
            "load_start": start,
            "load_end": end,
            "wall_clock_seconds": end - start,
            "nodes_loaded": len(users) + len(movies) + len(genres),
            "relationships_loaded": len(ratings) + len(genre_edges),
        }

    def warmup(self, iterations: int) -> None:
        for _ in range(iterations):
            self.one_hop(1)

    def one_hop(self, start_user_id: int) -> list:
        result = self._db.query(f"SELECT ->rated->movie AS movies FROM user:{start_user_id}")
        return result[0]["result"][0]["movies"] if result[0]["result"] else []

    def two_hop(self, start_user_id: int) -> list:
        result = self._db.query(
            f"SELECT ->rated->movie<-rated<-user AS users FROM user:{start_user_id}"
        )
        return result[0]["result"][0]["users"] if result[0]["result"] else []

    def three_hop(self, start_user_id: int) -> list:
        result = self._db.query(
            f"SELECT ->rated->movie<-rated<-user->rated->movie AS movies FROM user:{start_user_id}"
        )
        return result[0]["result"][0]["movies"] if result[0]["result"] else []

    def point_lookup(self, user_id: int) -> dict:
        result = self._db.select(f"user:{user_id}")
        return result if isinstance(result, dict) else {}

    def indexed_lookup(self, movie_title: str) -> list:
        result = self._db.query("SELECT * FROM movie WHERE title = $title", {"title": movie_title})
        return result[0]["result"]

    def aggregation(self) -> list:
        result = self._db.query("SELECT genre, count() AS count FROM has_genre GROUP BY genre")
        return result[0]["result"]

    def mixed_write(self, user_id: int, movie_id: int, rating: float) -> None:
        self._db.query(
            "RELATE $user->rated->$movie SET rating = $rating, timestamp = 0",
            {"user": f"user:{user_id}", "movie": f"movie:{movie_id}", "rating": rating},
        )

    def get_footprint(self) -> dict:
        try:
            info = self._db.query("INFO FOR DB")
            return {"db_info": info[0]["result"]}
        except Exception:
            return {"note": "not observable"}
