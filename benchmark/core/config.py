import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class BoltConfig:
    uri: str
    user: str
    password: str


@dataclass(frozen=True)
class ArangoConfig:
    uri: str
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class SurrealConfig:
    uri: str
    user: str
    password: str
    namespace: str
    database: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def cognodb_config() -> BoltConfig:
    return BoltConfig(
        uri=_require("COGNODB_URI"),
        user=_require("COGNODB_USER"),
        password=_require("COGNODB_PASSWORD"),
    )


def neo4j_config() -> BoltConfig:
    return BoltConfig(
        uri=_require("NEO4J_URI"),
        user=_require("NEO4J_USER"),
        password=_require("NEO4J_PASSWORD"),
    )


def memgraph_config() -> BoltConfig:
    return BoltConfig(
        uri=_require("MEMGRAPH_URI"),
        user=os.environ.get("MEMGRAPH_USER", ""),
        password=os.environ.get("MEMGRAPH_PASSWORD", ""),
    )


def arangodb_config() -> ArangoConfig:
    return ArangoConfig(
        uri=_require("ARANGO_URI"),
        user=_require("ARANGO_USER"),
        password=_require("ARANGO_PASSWORD"),
        database=os.environ.get("ARANGO_DB", "benchmark"),
    )


def surrealdb_config() -> SurrealConfig:
    return SurrealConfig(
        uri=_require("SURREALDB_URI"),
        user=_require("SURREALDB_USER"),
        password=_require("SURREALDB_PASSWORD"),
        namespace=os.environ.get("SURREALDB_NS", "benchmark"),
        database=os.environ.get("SURREALDB_DB", "benchmark"),
    )
