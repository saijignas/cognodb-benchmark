import functools
import time

import surrealdb.connection_ws as _surrealdb_connection_ws

# The surrealdb Python client's WebsocketConnection.connect() calls
# websockets.asyncio.client.connect() with no `max_size` override, so it
# inherits that library's 1 MiB (1,048,576 byte) default incoming-message
# limit -- confirmed via inspect.signature(). On real, densely connected
# MovieLens data, a correct (properly deduplicated) three_hop result can
# legitimately exceed 1 MiB: MovieLens exhibits a "small world" property
# where even a sparse user's co-rater set reaches a large fraction of the
# whole movie catalog within two hops (confirmed empirically -- user 926,
# with only 20 ratings, correctly produces all 1,682 movies in the
# dataset, a 1,162,895-byte CBOR response). This is not a bug in the
# query, not a server resource-limit problem, and not something to fix by
# capping the result -- it is an artificial client-side networking
# ceiling unrelated to the server's own CPU/memory constraints. There is
# no public parameter on SurrealDB() to configure this, so the
# underlying websockets.connect call is wrapped here, once at import
# time, to remove the limit entirely (max_size=None) rather than
# truncating any query result. Verified this does not increase server
# memory usage: peak observed was 243.9 MiB/512 MiB even for the
# full-catalog result above.
_surrealdb_connection_ws.connect = functools.partial(
    _surrealdb_connection_ws.connect, max_size=None
)

from surrealdb import SurrealDB, RecordID

from ..core.config import surrealdb_config
from .base import GraphDBAdapter


class SurrealDBAdapter(GraphDBAdapter):
    """SurrealDB implementation.

    Record-identity quirk (empirically confirmed against a live container,
    not assumed from docs): a bare `table:id` literal embedded in SurrealQL
    query text is parsed with its own type inference (a numeric-looking id
    becomes a number), which does NOT match the string-typed RecordID that
    the Python client's own create()/select() calls produce for the same
    logical id. Every query below therefore builds RecordID objects in
    Python and binds them as query parameters instead of interpolating
    `table:id` text, which sidesteps the ambiguity entirely.

    RELATE's edge-endpoint syntax additionally rejects function calls and
    dotted expressions in that position (confirmed via probing), so bulk
    edge creation binds each row's endpoints through LET first.

    two_hop/three_hop dedupe results (the raw graph traversal can revisit
    the same node via multiple paths) and, per the common workload
    definition in base.py, explicitly exclude the start user from the
    co-rater set rather than relying on any language's incidental
    edge-uniqueness behavior. three_hop stages this as (1) the co-rater
    set with the start user removed, (2) movies rated by that set --
    matching the Bolt/Cypher and Arango adapters' staged implementations.

    Ids are stored as strings (required for the identity match above) but
    converted back to int at every method boundary, matching the native
    int type the Bolt/Cypher and Arango adapters return for the same
    MovieLens ids.
    """

    name = "surrealdb"

    _BATCH_SIZE = 1000

    def __init__(self):
        self._cfg = surrealdb_config()
        self._db = None

    def connect(self) -> None:
        self._db = SurrealDB(self._cfg.uri)
        self._db.connect()
        self._db.sign_in(self._cfg.user, self._cfg.password)
        self._db.use(self._cfg.namespace, self._cfg.database)

    def disconnect(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    def health_check(self) -> bool:
        result = self._db.query("RETURN 1")
        return result[0]["result"] == 1

    def reset(self) -> None:
        for table in ("rated", "has_genre", "user", "movie", "genre"):
            self._db.query(f"DELETE {table}")

    def prepare_schema(self) -> None:
        pass  # SurrealDB is schemaless by default; nothing to declare upfront.

    def prepare_indexes(self) -> None:
        # No index needed on user/movie id: in SurrealDB the RecordID itself
        # *is* the primary key, unlike Neo4j/Memgraph/CognoDB where `.id` is
        # a regular property requiring its own index. This is a genuine
        # architectural difference between the platforms, not an oversight.
        self._db.query("DEFINE INDEX movie_title ON TABLE movie COLUMNS title")
        self._db.query("DEFINE INDEX genre_name ON TABLE genre COLUMNS name")

    def load_dataset(self, users, movies, genres, ratings, genre_edges) -> dict:
        start = time.time()

        for i in range(0, len(users), self._BATCH_SIZE):
            batch = users[i : i + self._BATCH_SIZE]
            rows = [{"id": RecordID("user", str(u))} for u in batch]
            self._db.query("INSERT INTO user $rows", {"rows": rows})

        for i in range(0, len(movies), self._BATCH_SIZE):
            batch = movies[i : i + self._BATCH_SIZE]
            rows = [
                {"id": RecordID("movie", str(m["id"])), "title": m["title"]}
                for m in batch
            ]
            self._db.query("INSERT INTO movie $rows", {"rows": rows})

        for i in range(0, len(genres), self._BATCH_SIZE):
            batch = genres[i : i + self._BATCH_SIZE]
            rows = [{"id": RecordID("genre", g), "name": g} for g in batch]
            self._db.query("INSERT INTO genre $rows", {"rows": rows})

        relate_ratings = """
            FOR $row IN $rows {
                LET $u = $row.u;
                LET $m = $row.m;
                RELATE $u->rated->$m SET rating = $row.rating, timestamp = $row.timestamp;
            }
        """
        for i in range(0, len(ratings), self._BATCH_SIZE):
            batch = ratings[i : i + self._BATCH_SIZE]
            rows = [
                {
                    "u": RecordID("user", str(r["user_id"])),
                    "m": RecordID("movie", str(r["movie_id"])),
                    "rating": r["rating"],
                    "timestamp": r["timestamp"],
                }
                for r in batch
            ]
            self._db.query(relate_ratings, {"rows": rows})

        relate_genres = """
            FOR $row IN $rows {
                LET $m = $row.m;
                LET $g = $row.g;
                RELATE $m->has_genre->$g SET x = 1;
            }
        """
        for i in range(0, len(genre_edges), self._BATCH_SIZE):
            batch = genre_edges[i : i + self._BATCH_SIZE]
            rows = [
                {
                    "m": RecordID("movie", str(e["movie_id"])),
                    "g": RecordID("genre", e["genre"]),
                }
                for e in batch
            ]
            self._db.query(relate_genres, {"rows": rows})

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
        u = RecordID("user", str(start_user_id))
        result = self._db.query("SELECT ->rated->movie AS movies FROM $u", {"u": u})
        rows = result[0]["result"]
        if not rows:
            return []
        return [int(m.id) for m in rows[0]["movies"]]

    def two_hop(self, start_user_id: int) -> list:
        u = RecordID("user", str(start_user_id))
        result = self._db.query(
            "SELECT ->rated->movie<-rated<-user AS users FROM $u", {"u": u}
        )
        rows = result[0]["result"]
        if not rows:
            return []
        return sorted({int(usr.id) for usr in rows[0]["users"]} - {start_user_id})

    def three_hop(self, start_user_id: int) -> list:
        # Staged as two explicit steps within one query (LET the co-rater
        # set, exclude the start user via array::complement, then expand
        # to their rated movies) -- a deliberate expansion of the same
        # co-rater set two_hop uses, not a single flat multi-hop pattern.
        # SurrealQL's plain traversal has no relationship-uniqueness
        # constraint, so this naturally includes a movie even if reached
        # via the same edge that identified the co-rater; that is the
        # intended semantic (see base.py's three_hop docstring), not
        # something to "fix" by excluding it.
        #
        # The raw traversal ->rated->movie<-rated<-user is NOT
        # deduplicated by SurrealQL itself: a co-rater who shares K
        # movies with the start user appears K times in that array.
        # array::complement only removes exact matches to $u -- it does
        # not deduplicate the remaining entries. Without the
        # array::distinct() wrapped around the raw traversal below, the
        # final `FROM $others` stage would independently re-traverse and
        # materialize that co-rater's full rated-movie list once per
        # duplicate occurrence, instead of once per unique co-rater. On
        # tiny synthetic data no co-rater ever shares more than one movie
        # with the start user, so this was invisible in the smoke test;
        # on the real, densely connected MovieLens data (a popular movie
        # can have hundreds of raters, many of whom share several movies
        # with any given start user) this caused a genuine memory spike
        # that OOM-killed the server (confirmed empirically, independent
        # of the underlying storage backend -- switching from in-memory
        # to RocksDB did not help, since the problem is transient
        # per-query materialization, not data residency). Deduplicating
        # the co-rater set immediately after the raw traversal bounds it
        # to the number of *unique* co-raters before any expansion
        # happens, which is what "the co-rater set" in base.py's
        # docstring actually means.
        u = RecordID("user", str(start_user_id))
        result = self._db.query(
            """
            LET $co_raters = array::distinct((SELECT VALUE ->rated->movie<-rated<-user FROM $u)[0]);
            LET $others = array::complement($co_raters, [$u]);
            SELECT VALUE array::distinct(->rated->movie) FROM $others;
            """,
            {"u": u},
        )
        rows = result[-1]["result"]
        if not rows:
            return []
        movies = set()
        for arr in rows:
            for m in arr:
                movies.add(int(m.id))
        return sorted(movies)

    def point_lookup(self, user_id: int) -> dict:
        record = self._db.select(RecordID("user", str(user_id)))
        return {"id": int(record["id"].id)} if record else {}

    def indexed_lookup(self, movie_title: str) -> list:
        result = self._db.query(
            "SELECT id FROM movie WHERE title = $title", {"title": movie_title}
        )
        rows = result[0]["result"]
        return [int(r["id"].id) for r in rows]

    def aggregation(self) -> list:
        result = self._db.query(
            "SELECT out.name AS genre, count() AS count FROM has_genre "
            "GROUP BY out.name ORDER BY count DESC"
        )
        return result[0]["result"]

    def mixed_write(self, user_id: int, movie_id: int, rating: float) -> None:
        self._db.query(
            "RELATE $u->rated->$m SET rating = $rating, timestamp = 0",
            {
                "u": RecordID("user", str(user_id)),
                "m": RecordID("movie", str(movie_id)),
                "rating": rating,
            },
        )

    def get_footprint(self) -> dict:
        return {"note": "not observable"}

