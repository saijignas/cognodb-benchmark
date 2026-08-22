import time

from arango import ArangoClient

from ..core.config import arangodb_config
from .base import GraphDBAdapter


class ArangoDBAdapter(GraphDBAdapter):
    name = "arangodb"

    def __init__(self):
        cfg = arangodb_config()
        self._cfg = cfg
        self._client = ArangoClient(hosts=cfg.uri)
        self._sys_db = None
        self._db = None

    def connect(self) -> None:
        self._sys_db = self._client.db("_system", username=self._cfg.user, password=self._cfg.password)
        if not self._sys_db.has_database(self._cfg.database):
            self._sys_db.create_database(self._cfg.database)
        self._db = self._client.db(self._cfg.database, username=self._cfg.user, password=self._cfg.password)

    def disconnect(self) -> None:
        pass  # python-arango has no persistent connection object to close.

    def health_check(self) -> bool:
        cursor = self._db.aql.execute("RETURN 1")
        return next(cursor) == 1

    def reset(self) -> None:
        for name in ("users", "movies", "genres", "rated", "has_genre"):
            if self._db.has_collection(name):
                self._db.delete_collection(name)

    def prepare_schema(self) -> None:
        self._db.create_collection("users")
        self._db.create_collection("movies")
        self._db.create_collection("genres")
        self._db.create_collection("rated", edge=True)
        self._db.create_collection("has_genre", edge=True)
        if not self._db.has_graph("movielens"):
            graph = self._db.create_graph("movielens")
            graph.create_edge_definition(
                edge_collection="rated", from_vertex_collections=["users"], to_vertex_collections=["movies"]
            )
            graph.create_edge_definition(
                edge_collection="has_genre", from_vertex_collections=["movies"], to_vertex_collections=["genres"]
            )

    def prepare_indexes(self) -> None:
        self._db.collection("movies").add_persistent_index(fields=["title"])
        self._db.collection("genres").add_persistent_index(fields=["name"])

    def load_dataset(self, users, movies, genres, ratings, genre_edges) -> dict:
        start = time.time()
        self._db.collection("users").insert_many([{"_key": str(u), "id": u} for u in users])
        self._db.collection("movies").insert_many(
            [{"_key": str(m["id"]), "id": m["id"], "title": m["title"]} for m in movies]
        )
        self._db.collection("genres").insert_many([{"_key": g, "name": g} for g in genres])
        self._db.collection("rated").insert_many(
            [
                {
                    "_from": f"users/{r['user_id']}",
                    "_to": f"movies/{r['movie_id']}",
                    "rating": r["rating"],
                    "timestamp": r["timestamp"],
                }
                for r in ratings
            ]
        )
        self._db.collection("has_genre").insert_many(
            [{"_from": f"movies/{e['movie_id']}", "_to": f"genres/{e['genre']}"} for e in genre_edges]
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
        cursor = self._db.aql.execute(
            "FOR m IN OUTBOUND @start rated RETURN m.id",
            bind_vars={"start": f"users/{start_user_id}"},
        )
        return list(cursor)

    def two_hop(self, start_user_id: int) -> list:
        cursor = self._db.aql.execute(
            """
            FOR m IN OUTBOUND @start rated
              FOR u2 IN INBOUND m rated
                FILTER u2.id != @start_id
                RETURN DISTINCT u2.id
            """,
            bind_vars={"start": f"users/{start_user_id}", "start_id": start_user_id},
        )
        return list(cursor)

    def three_hop(self, start_user_id: int) -> list:
        # Stages the co-rater set exactly like two_hop -- computed and
        # deduplicated via RETURN DISTINCT inside the co_raters subquery --
        # BEFORE expanding to rated movies. The previous version filtered
        # u2 but never deduplicated it before the final FOR loop, so a
        # co-rater sharing K movies with the start user had their full
        # rated-movie list re-expanded K times instead of once. On the
        # real MovieLens data this caused a genuine combinatorial blowup
        # (confirmed empirically: a single three_hop call hung for 90+
        # minutes under the 0.5 CPU cap during the real benchmark run).
        # This mirrors the Bolt/Cypher adapter's "WITH DISTINCT u2" staging
        # and the SurrealDB adapter's array::distinct staging -- all three
        # now correctly implement "the co-rater set" as a set, per the
        # workload definition in base.py, rather than a set in name only.
        cursor = self._db.aql.execute(
            """
            LET co_raters = (
              FOR m IN OUTBOUND @start rated
                FOR u2 IN INBOUND m rated
                  FILTER u2.id != @start_id
                  RETURN DISTINCT u2
            )
            FOR u2 IN co_raters
              FOR m2 IN OUTBOUND u2 rated
                RETURN DISTINCT m2.id
            """,
            bind_vars={"start": f"users/{start_user_id}", "start_id": start_user_id},
        )
        return list(cursor)

    def point_lookup(self, user_id: int) -> dict:
        doc = self._db.collection("users").get(str(user_id))
        return {"id": doc["id"]} if doc else {}

    def indexed_lookup(self, movie_title: str) -> list:
        cursor = self._db.aql.execute(
            "FOR m IN movies FILTER m.title == @title RETURN m.id",
            bind_vars={"title": movie_title},
        )
        return list(cursor)

    def aggregation(self) -> list:
        cursor = self._db.aql.execute(
            """
            FOR e IN has_genre
              COLLECT genre = DOCUMENT(e._to).name WITH COUNT INTO count
              SORT count DESC
              RETURN {genre, count}
            """
        )
        return list(cursor)

    def mixed_write(self, user_id: int, movie_id: int, rating: float) -> None:
        self._db.collection("rated").insert(
            {"_from": f"users/{user_id}", "_to": f"movies/{movie_id}", "rating": rating, "timestamp": 0}
        )

    def get_footprint(self) -> dict:
        try:
            stats = self._db.collection("rated").statistics()
            return {"rated_collection_stats": stats}
        except Exception:
            return {"note": "not observable"}

