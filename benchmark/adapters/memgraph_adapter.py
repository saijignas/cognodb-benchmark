from ..core.config import memgraph_config
from .bolt_adapter import BoltCypherAdapter


class MemgraphAdapter(BoltCypherAdapter):
    name = "memgraph"

    def __init__(self):
        cfg = memgraph_config()
        super().__init__(cfg.uri, cfg.user, cfg.password)

    def prepare_indexes(self) -> None:
        # Memgraph uses its own index syntax, not Cypher's CREATE INDEX ... IF NOT EXISTS.
        with self._driver.session() as session:
            session.run("CREATE INDEX ON :User(id)")
            session.run("CREATE INDEX ON :Movie(id)")
            session.run("CREATE INDEX ON :Movie(title)")
            session.run("CREATE INDEX ON :Genre(name)")
