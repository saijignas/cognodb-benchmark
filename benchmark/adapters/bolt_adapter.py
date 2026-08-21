import time

from neo4j import GraphDatabase

from .base import GraphDBAdapter


class BoltCypherAdapter(GraphDBAdapter):
    """Shared Cypher/Bolt implementation for CognoDB, Neo4j, and Memgraph.

    These three platforms speak the same wire protocol and query language
    (confirmed for CognoDB in the assignment's own setup instructions), so
    the logical workloads are implemented once here; each platform's
    adapter subclass only supplies connection parameters.
    """

    def __init__(self, uri: str, user: str, password: str):
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None

    def connect(self) -> None:
        self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))

    def disconnect(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def health_check(self) -> bool:
        with self._driver.session() as session:
            return session.run("RETURN 1 AS ok").single()["ok"] == 1

    def reset(self) -> None:
        with self._driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def prepare_schema(self) -> None:
        pass  # Cypher property graphs are schema-optional; nothing to declare upfront.

    def prepare_indexes(self) -> None:
        with self._driver.session() as session:
            session.run("CREATE INDEX user_id IF NOT EXISTS FOR (u:User) ON (u.id)")
            session.run("CREATE INDEX movie_id IF NOT EXISTS FOR (m:Movie) ON (m.id)")
            session.run("CREATE INDEX movie_title IF NOT EXISTS FOR (m:Movie) ON (m.title)")
            session.run("CREATE INDEX genre_name IF NOT EXISTS FOR (g:Genre) ON (g.name)")

    def load_dataset(self, users, movies, genres, ratings, genre_edges) -> dict:
        start = time.time()
        with self._driver.session() as session:
            session.run(
                "UNWIND $rows AS row CREATE (:User {id: row.id})",
                rows=[{"id": u} for u in users],
            )
            session.run(
                "UNWIND $rows AS row CREATE (:Movie {id: row.id, title: row.title})",
                rows=movies,
            )
            session.run(
                "UNWIND $rows AS row CREATE (:Genre {name: row.name})",
                rows=[{"name": g} for g in genres],
            )
            for i in range(0, len(ratings), 1000):
                batch = ratings[i : i + 1000]
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (u:User {id: row.user_id})
                    MATCH (m:Movie {id: row.movie_id})
                    CREATE (u)-[:RATED {rating: row.rating, timestamp: row.timestamp}]->(m)
                    """,
                    rows=batch,
                )
            for i in range(0, len(genre_edges), 1000):
                batch = genre_edges[i : i + 1000]
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (m:Movie {id: row.movie_id})
                    MATCH (g:Genre {name: row.genre})
                    CREATE (m)-[:HAS_GENRE]->(g)
                    """,
                    rows=batch,
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
        with self._driver.session() as session:
            result = session.run(
                "MATCH (u:User {id: $id})-[:RATED]->(m:Movie) RETURN m.id AS id",
                id=start_user_id,
            )
            return [r["id"] for r in result]

    def two_hop(self, start_user_id: int) -> list:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (u:User {id: $id})-[:RATED]->(:Movie)<-[:RATED]-(u2:User)
                RETURN DISTINCT u2.id AS id
                """,
                id=start_user_id,
            )
            return [r["id"] for r in result]

    def three_hop(self, start_user_id: int) -> list:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (u:User {id: $id})-[:RATED]->(:Movie)<-[:RATED]-(:User)-[:RATED]->(m2:Movie)
                RETURN DISTINCT m2.id AS id
                """,
                id=start_user_id,
            )
            return [r["id"] for r in result]

    def point_lookup(self, user_id: int) -> dict:
        with self._driver.session() as session:
            record = session.run(
                "MATCH (u:User {id: $id}) RETURN u.id AS id", id=user_id
            ).single()
            return dict(record) if record else {}

    def indexed_lookup(self, movie_title: str) -> list:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (m:Movie {title: $title}) RETURN m.id AS id", title=movie_title
            )
            return [r["id"] for r in result]

    def aggregation(self) -> list:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (:Movie)-[:HAS_GENRE]->(g:Genre)
                RETURN g.name AS genre, count(*) AS count
                ORDER BY count DESC
                """
            )
            return [dict(r) for r in result]

    def mixed_write(self, user_id: int, movie_id: int, rating: float) -> None:
        with self._driver.session() as session:
            session.run(
                """
                MATCH (u:User {id: $uid})
                MATCH (m:Movie {id: $mid})
                CREATE (u)-[:RATED {rating: $rating, timestamp: 0}]->(m)
                """,
                uid=user_id,
                mid=movie_id,
                rating=rating,
            )

    def get_footprint(self) -> dict:
        return {"note": "not observable"}
