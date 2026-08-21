import inspect

import pytest

from benchmark.adapters.base import GraphDBAdapter
from benchmark.adapters.cognodb import CognoDBAdapter
from benchmark.adapters.neo4j_adapter import Neo4jAdapter
from benchmark.adapters.memgraph_adapter import MemgraphAdapter
from benchmark.adapters.arangodb_adapter import ArangoDBAdapter
from benchmark.adapters.surrealdb_adapter import SurrealDBAdapter

ADAPTER_CLASSES = [CognoDBAdapter, Neo4jAdapter, MemgraphAdapter, ArangoDBAdapter, SurrealDBAdapter]

REQUIRED_METHODS = [
    name
    for name, member in inspect.getmembers(GraphDBAdapter, predicate=inspect.isfunction)
    if not name.startswith("_")
]


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
def test_adapter_implements_full_contract(adapter_cls):
    for method in REQUIRED_METHODS:
        assert hasattr(adapter_cls, method), f"{adapter_cls.__name__} is missing {method}"
        assert callable(getattr(adapter_cls, method))


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
def test_adapter_has_platform_name(adapter_cls):
    assert isinstance(adapter_cls.name, str)
    assert adapter_cls.name != "unknown"
