from ..core.config import neo4j_config
from .bolt_adapter import BoltCypherAdapter


class Neo4jAdapter(BoltCypherAdapter):
    name = "neo4j"

    def __init__(self):
        cfg = neo4j_config()
        super().__init__(cfg.uri, cfg.user, cfg.password)
