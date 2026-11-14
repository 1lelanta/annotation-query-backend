from copy import deepcopy

from app.services.path_search import normalize_request, process_candidates
from tests.fixtures.path_graph import CANDIDATES


def request(**changes):
    value = {
        "sources": [{"type": "gene", "id": "gene-a"}],
        "targets": [{"type": "gene", "id": "gene-b"}],
    }
    value.update(changes)
    return normalize_request(value)


def test_paths_rank_and_avoid_hubs_by_default():
    result = process_candidates({"candidates": deepcopy(CANDIDATES)}, request())
    assert len(result["paths"]) == 1
    assert result["paths"][0]["hop_count"] == 3


def test_hub_can_be_allowed_and_never_replaces_endpoints():
    result = process_candidates(
        {"candidates": deepcopy(CANDIDATES)},
        request(hub_avoidance={"enabled": False}),
    )
    assert [node["id"] for node in result["paths"][0]["nodes"]] == ["gene-a", "hub", "gene-b"]


def test_filters_and_no_repeated_nodes():
    normalized = request(
        max_hops=3,
        evidence_filters=[{"field": "score", "operator": ">=", "value": 700}],
        intermediate_node_types={"allow": ["protein", "pathway"]},
    )
    result = process_candidates({"candidates": deepcopy(CANDIDATES)}, normalized)
    assert result["paths"][0]["hop_count"] == 3
    repeated = {"nodes": [CANDIDATES[0]["nodes"][0], CANDIDATES[0]["nodes"][1],
                           CANDIDATES[0]["nodes"][1]], "edges": CANDIDATES[0]["edges"][:2]}
    assert not process_candidates({"candidates": [repeated]}, normalized)["paths"]


def test_max_hops_and_no_path():
    result = process_candidates({"candidates": deepcopy(CANDIDATES)}, request(max_hops=2))
    assert result["paths"] == []
    assert "no path" in result["reason"]


def test_deterministic_order_and_validation():
    first = process_candidates({"candidates": deepcopy(CANDIDATES)}, request(hub_avoidance={"enabled": False}))
    second = process_candidates({"candidates": list(reversed(deepcopy(CANDIDATES)))}, request(hub_avoidance={"enabled": False}))
    assert first["paths"] == second["paths"]
    for hostile in ['"', "`", "(", ")"]:
        try:
            request(sources=[{"type": "gene", "id": hostile}])
        except ValueError:
            pass
        else:
            raise AssertionError("hostile id accepted")
