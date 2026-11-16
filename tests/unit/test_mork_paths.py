import re
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.lib.db_resilience import QueryTimeoutError, QueryType
from app.services import mork_paths
from app.services.mork_paths import MorkPathMixin
from app.services.path_search import normalize_request, process_candidates
from tests.fixtures.path_graph import CANDIDATES, NODES


NODE_TYPES = {"gene", "protein", "pathway"}
EDGE_TYPES = {"interacts_with", "participates_in", "regulates"}


class FakeMorkPaths(MorkPathMixin):
    path_node_types = NODE_TYPES
    path_edge_types = EDGE_TYPES

    def __init__(self, duplicate=False):
        self.nodes = {(node["type"], node["id"]): node for node in NODES}
        self.edges = []
        for candidate in CANDIDATES:
            for edge in candidate["edges"]:
                self.edges.append({
                    "predicate": edge["type"].replace(" ", "_"),
                    "source": (edge["source_type"], edge["source"]),
                    "target": (edge["target_type"], edge["target"]),
                    "evidence": edge["evidence"],
                })
        if duplicate:
            self.edges.append(deepcopy(self.edges[0]))
        self.patterns = []

    def _run_pattern(self, pattern_str, template_str):
        self.patterns.append(pattern_str)
        resolve = re.fullmatch(r"\((\w+) ([A-Za-z0-9_.:/-]+)\)", pattern_str)
        if resolve:
            return [("resolved", resolve.group(1), resolve.group(2))] if (
                resolve.group(1), resolve.group(2)
            ) in self.nodes else []

        property_match = re.fullmatch(
            r"\(\$property \((\w+) ([^)]+)\) \$value\)", pattern_str
        )
        if property_match:
            node = self.nodes.get((property_match.group(1), property_match.group(2)))
            return [
                ("node_property", key, property_match.group(1), property_match.group(2), value)
                for key, value in (node or {}).get("properties", {}).items()
            ]

        evidence_match = re.fullmatch(
            r"\(\$property \((\w+) \((\w+) ([^)]+)\) \((\w+) ([^)]+)\)\) \$value\)",
            pattern_str,
        )
        if evidence_match:
            predicate, source_type, source_id, target_type, target_id = evidence_match.groups()
            for edge in self.edges:
                if (
                    edge["predicate"], edge["source"], edge["target"]
                ) == (predicate, (source_type, source_id), (target_type, target_id)):
                    return [
                        ("edge_evidence", key, predicate, source_type, source_id,
                         target_type, target_id, value)
                        for key, value in edge["evidence"].items()
                    ]
            return []

        current_match = re.search(r"\((\w+) ([A-Za-z0-9_.:/-]+)\)", pattern_str)
        wildcard = "$predicate" in pattern_str
        if current_match:
            current = (current_match.group(1), current_match.group(2))
            reverse = pattern_str.find(f"({current[0]} {current[1]})") > pattern_str.find(
                "($neighbor_type $neighbor_id)"
            )
            result = []
            for edge in self.edges:
                if reverse and edge["target"] == current:
                    result.append(("path_edge", edge["predicate"], *edge["source"], *edge["target"]))
                elif not reverse and edge["source"] == current:
                    result.append(("path_edge", edge["predicate"], *edge["source"], *edge["target"]))
            if wildcard:
                return result
            predicate = pattern_str.split("(", 2)[1].split()[0]
            return [atom for atom in result if atom[1] == predicate]
        return []


def request(**changes):
    value = {
        "sources": [{"type": "gene", "id": "gene-a"}],
        "targets": [{"type": "gene", "id": "gene-b"}],
    }
    value.update(changes)
    return normalize_request(value)


def expected_candidates():
    candidates = deepcopy(CANDIDATES)
    degrees = {}
    for candidate in candidates:
        for edge in candidate["edges"]:
            degrees[edge["source"]] = degrees.get(edge["source"], 0) + 1
            degrees[edge["target"]] = degrees.get(edge["target"], 0) + 1
    for candidate in candidates:
        for node in candidate["nodes"]:
            node["properties"]["degree"] = degrees[node["id"]]
    return candidates


def test_mork_paths_match_fixture_paths_and_dedupe_edges():
    backend = FakeMorkPaths(duplicate=True)
    normalized = request(hub_avoidance={"enabled": False})
    actual = process_candidates(backend.find_paths(normalized), normalized)
    expected = process_candidates({"candidates": expected_candidates()}, normalized)
    assert actual["paths"] == expected["paths"]
    assert all(
        len({
            (edge["source"], edge["target"], edge["type"])
            for edge in path["edges"]
        }) == len(path["edges"])
        for path in actual["paths"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"edge_types": {"allow": ["interacts with", "regulates"]}},
        {"edge_types": {"exclude": ["participates in"]}},
        {"intermediate_node_types": {"exclude": ["pathway"]}},
        {"evidence_filters": [{"field": "p_value", "operator": "<", "value": 0.05}]},
        {"hub_avoidance": {"enabled": False, "exclude_ids": ["protein-a"]}},
        {"max_hops": 2},
    ],
)
def test_mork_paths_apply_path_filters(changes):
    backend = FakeMorkPaths()
    normalized = request(**changes)
    actual = process_candidates(backend.find_paths(normalized), normalized)
    expected = process_candidates({"candidates": expected_candidates()}, normalized)
    assert actual == expected


def test_mork_paths_handle_unknown_and_hostile_ids():
    backend = FakeMorkPaths()
    unknown = request(sources=[{"type": "gene", "id": "unknown"}])
    result = backend.find_paths(unknown)
    assert result["unresolved_ids"] == ["unknown"]
    patterns = list(backend.patterns)
    with pytest.raises(ValueError):
        backend.find_paths(request(sources=[{"type": "gene", "id": '"'}]))
    assert backend.patterns == patterns


def test_empty_path_schema_whitelist_raises(monkeypatch):
    backend = FakeMorkPaths()
    backend.path_node_types = set()
    backend.path_edge_types = set()
    monkeypatch.setattr(
        "app.services.cypher_generator._path_schema_whitelist",
        lambda: (set(), set()),
    )
    with pytest.raises(ValueError, match="path schema whitelist is empty"):
        backend.find_paths(request())


def test_hostile_predicate_and_node_type_are_rejected_before_patterns():
    backend = FakeMorkPaths()
    backend.path_edge_types = {"bad)"}
    with pytest.raises(ValueError, match="Invalid predicate"):
        backend.find_paths(request())
    assert backend.patterns == []

    backend = FakeMorkPaths()
    normalized = request()
    normalized["sources"][0]["type"] = "bad)"
    with pytest.raises(ValueError, match="Invalid node type"):
        backend.find_paths(normalized)
    assert backend.patterns == []


@pytest.mark.parametrize("hostile_id", ['"', "`", "(", ")", "$", "bad id"])
def test_hostile_ids_are_rejected_before_patterns(hostile_id):
    backend = FakeMorkPaths()
    normalized = request()
    normalized["sources"][0]["id"] = hostile_id
    with pytest.raises(ValueError, match="Invalid node id"):
        backend.find_paths(normalized)
    assert backend.patterns == []


def test_mork_paths_timeout_and_truncation(monkeypatch):
    backend = FakeMorkPaths()
    original_monotonic = mork_paths.time.monotonic
    values = iter([0.0, 11.0])
    monkeypatch.setattr(mork_paths.time, "monotonic", lambda: next(values, 11.0))
    with pytest.raises(QueryTimeoutError):
        backend.find_paths(request())
    monkeypatch.setattr(mork_paths.time, "monotonic", original_monotonic)

    for index in range(6):
        middle = ("protein", f"protein-{index}")
        backend.nodes[middle] = {"id": middle[0], "type": middle[1], "properties": {}}
        backend.edges.extend([
            {"predicate": "interacts_with", "source": ("gene", "gene-a"),
             "target": middle, "evidence": {"score": 1, "p_value": 1.0}},
            {"predicate": "regulates", "source": middle, "target": ("gene", "gene-b"),
             "evidence": {"score": 1, "p_value": 1.0}},
        ])
    normalized = request(k=1, hub_avoidance={"enabled": False})
    result = backend.find_paths(normalized)
    assert result["truncated"] is True
    assert len(result["candidates"]) == 5
