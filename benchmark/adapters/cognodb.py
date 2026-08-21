from ..core.config import cognodb_config
from .bolt_adapter import BoltCypherAdapter


class CognoDBAdapter(BoltCypherAdapter):
    name = "cognodb"

    def __init__(self):
        cfg = cognodb_config()
        super().__init__(cfg.uri, cfg.user, cfg.password)
