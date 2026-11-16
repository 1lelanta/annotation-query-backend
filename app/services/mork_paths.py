import re
import time
from copy import deepcopy
from numbers import Number

from app.lib.db_resilience import QueryTimeoutError, QueryType
from app.services.path_search import _ID_RE, _compare


_PATH_TIMEOUT_SECONDS = 10.0
_EVIDENCE_FIELDS = {
    "score",
    "p_value",
    "confidence",
    "evidence_score",
    "publication_count",
    "support",
}
_ATOM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class MorkPathMixin:
    """Shared MORK path traversal for the server and CLI-backed generators."""

    def _run_pattern(self, pattern_str, template_str):
        raise NotImplementedError

    def _path_schema(self, request):
        node_types = set(request.get("_path_node_types", ()))
        edge_types = set(request.get("_path_edge_types", ()))
        node_types.update(getattr(self, "path_node_types", ()))
        edge_types.update(getattr(self, "path_edge_types", ()))
        if not node_types or not edge_types:
            from app.services.cypher_generator import _path_schema_whitelist

            schema_nodes, schema_edges = _path_schema_whitelist()
            node_types.update(schema_nodes)
            edge_types.update(schema_edges)
        if not node_types or not edge_types:
            raise ValueError("path schema whitelist is empty")
        return node_types, edge_types

    @staticmethod
    def _safe(value, kind, label, pattern=False):
        if kind not in {"id", "atom"}:
            raise ValueError(f"Invalid safety kind: {kind}")
        regex = _ID_RE if kind == "id" else _ATOM_RE
        if not isinstance(value, str) or not regex.fullmatch(value):
            raise ValueError(f"Invalid {label}")
        if pattern and any(char in value for char in ('"', "'", "`", "$", "(", ")", " ", "\t", "\n")):
            raise ValueError(f"Invalid {label}")
        return value

    @staticmethod
    def _atom_tuples(atoms):
        if not atoms:
            return []
        if all(isinstance(atom, tuple) for atom in atoms):
            return [tuple(atom) for atom in atoms]
        from app.services.metta.metta_seralizer import metta_seralizer

        return [tuple(atom) for atom in metta_seralizer(atoms)]

    def _query(self, deadline, pattern, template):
        if time.monotonic() >= deadline:
            raise QueryTimeoutError(QueryType.PATHS, _PATH_TIMEOUT_SECONDS)
        return self._atom_tuples(self._run_pattern(pattern, template))

    @staticmethod
    def _value(value):
        if isinstance(value, Number) and not isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return value
        value = value.strip('"')
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

    @staticmethod
    def _node_key(node_type, node_id):
        return node_type, node_id

    def _resolve(self, request, deadline):
        resolved = set()
        unresolved = []
        for endpoint in request["sources"] + request["targets"]:
            node_type = self._safe(endpoint["type"], "atom", "node type", pattern=True)
            node_id = self._safe(endpoint["id"], "id", "node id", pattern=True)
            found = self._query(
                deadline,
                f"({node_type} {node_id})",
                f"(resolved ({node_type} {node_id}))",
            )
            key = self._node_key(node_type, node_id)
            if found:
                resolved.add(key)
            else:
                unresolved.append(node_id)
        return resolved, sorted(set(unresolved))

    def _neighbors(self, node, predicate, reverse, deadline):
        node_type, node_id = node
        self._safe(node_type, "atom", "node type", pattern=True)
        self._safe(node_id, "id", "node id", pattern=True)
        self._safe(predicate, "atom", "predicate", pattern=True)
        if reverse:
            pattern = (
                f"({predicate} ($neighbor_type $neighbor_id) "
                f"({node_type} {node_id}))"
            )
            template = (
                f"(path_edge {predicate} ($neighbor_type $neighbor_id) "
                f"({node_type} {node_id}))"
            )
        else:
            pattern = (
                f"({predicate} ({node_type} {node_id}) "
                f"($neighbor_type $neighbor_id))"
            )
            template = (
                f"(path_edge {predicate} ({node_type} {node_id}) "
                f"($neighbor_type $neighbor_id))"
            )
        atoms = self._query(deadline, pattern, template)
        return list(dict.fromkeys(atoms))

    def _degree(self, node, deadline, cache):
        if node in cache:
            return cache[node]
        node_type, node_id = node
        edges = set()
        for reverse in (False, True):
            if reverse:
                pattern = f"($predicate ($neighbor_type $neighbor_id) ({node_type} {node_id}))"
                template = (
                    f"(path_edge $predicate ($neighbor_type $neighbor_id) "
                    f"({node_type} {node_id}))"
                )
            else:
                pattern = f"($predicate ({node_type} {node_id}) ($neighbor_type $neighbor_id))"
                template = (
                    f"(path_edge $predicate ({node_type} {node_id}) "
                    f"($neighbor_type $neighbor_id))"
                )
            for atom in self._query(deadline, pattern, template):
                if len(atom) == 6 and atom[0] == "path_edge":
                    edges.add((atom[1], atom[2], atom[3], atom[4], atom[5]))
        cache[node] = len(edges)
        return cache[node]

    def _node_properties(self, node, deadline, cache, degree_cache):
        if node not in cache:
            node_type, node_id = node
            pattern = f"($property ({node_type} {node_id}) $value)"
            template = f"(node_property $property ({node_type} {node_id}) $value)"
            properties = {}
            for atom in self._query(deadline, pattern, template):
                if len(atom) == 5 and atom[0] == "node_property":
                    properties[str(atom[1])] = self._value(atom[4])
            properties.pop("id", None)
            cache[node] = properties
        result = dict(cache[node])
        result["degree"] = int(self._degree(node, deadline, degree_cache))
        return result

    def _edge_evidence(self, edge, deadline, cache):
        key = (edge["predicate"], edge["source"], edge["target"])
        if key in cache:
            return cache[key]
        predicate = self._safe(edge["predicate"], "atom", "predicate", pattern=True)
        source_type, source_id = edge["source"]
        target_type, target_id = edge["target"]
        pattern = (
            f"($property ({predicate} ({source_type} {source_id}) "
            f"({target_type} {target_id})) $value)"
        )
        template = (
            f"(edge_evidence $property ({predicate} ({source_type} {source_id}) "
            f"({target_type} {target_id})) $value)"
        )
        evidence = {}
        for atom in self._query(deadline, pattern, template):
            if len(atom) == 8 and atom[0] == "edge_evidence":
                evidence[str(atom[1])] = self._value(atom[7])
        cache[key] = evidence
        return evidence

    @staticmethod
    def _output_predicate(predicate, request):
        requested = (
            request["edge_types"]["allow"] + request["edge_types"]["exclude"]
        )
        return predicate.replace("_", " ") if not requested or any(" " in value for value in requested) else predicate

    @staticmethod
    def _score(edges):
        scores = [
            edge["evidence"].get("score")
            for edge in edges
            if isinstance(edge["evidence"].get("score"), Number)
        ]
        return sum(float(score) for score in scores) / len(scores) if scores else 0.0

    def find_paths(self, request):
        deadline = time.monotonic() + _PATH_TIMEOUT_SECONDS
        node_types, edge_types = self._path_schema(request)
        endpoint_types = {endpoint["type"] for endpoint in request["sources"] + request["targets"]}
        intermediate_types = set(request["intermediate_node_types"]["allow"])
        intermediate_types.update(request["intermediate_node_types"]["exclude"])
        for node_type in endpoint_types | intermediate_types:
            self._safe(node_type, "atom", "node type", pattern=True)
            if node_types and node_type not in node_types:
                raise ValueError(f"Unknown node type: {node_type}")

        predicates = {value.replace(" ", "_") for value in edge_types}
        allowed = request["edge_types"]["allow"]
        excluded = set(request["edge_types"]["exclude"])
        for requested_predicate in allowed + request["edge_types"]["exclude"]:
            canonical = requested_predicate.replace(" ", "_")
            self._safe(canonical, "atom", "predicate", pattern=True)
            if edge_types and canonical not in predicates:
                raise ValueError(f"Unknown predicate: {requested_predicate}")
        if allowed:
            predicates &= {value.replace(" ", "_") for value in allowed}
        predicates -= {value.replace(" ", "_") for value in excluded}
        for predicate in predicates:
            self._safe(predicate, "atom", "predicate", pattern=True)
        if not predicates:
            return {"candidates": [], "unresolved_ids": [], "truncated": False}

        resolved, unresolved = self._resolve(request, deadline)
        source_nodes = [
            (endpoint["type"], endpoint["id"])
            for endpoint in request["sources"]
            if (endpoint["type"], endpoint["id"]) in resolved
        ]
        target_nodes = {
            (endpoint["type"], endpoint["id"])
            for endpoint in request["targets"]
            if (endpoint["type"], endpoint["id"]) in resolved
        }
        if not source_nodes or not target_nodes:
            return {"candidates": [], "unresolved_ids": unresolved, "truncated": False}

        degree_cache = {}
        property_cache = {}
        evidence_cache = {}
        paths = []
        frontier = [
            {"nodes": [source], "edges": []}
            for source in source_nodes
        ]
        max_hops = request["max_hops"]
        hub = request["hub_avoidance"]
        excluded_ids = set(hub["exclude_ids"])
        intermediate_filter = request["intermediate_node_types"]

        for _ in range(max_hops):
            next_frontier = []
            for state in frontier:
                current = state["nodes"][-1]
                for predicate in sorted(predicates):
                    for reverse in (False, True):
                        for atom in self._neighbors(current, predicate, reverse, deadline):
                            if len(atom) != 6 or atom[0] != "path_edge":
                                continue
                            _, atom_predicate, src_type, src_id, tgt_type, tgt_id = atom
                            source = (str(src_type), str(src_id))
                            target = (str(tgt_type), str(tgt_id))
                            if atom_predicate != predicate:
                                continue
                            neighbor = source if reverse else target
                            if neighbor in state["nodes"]:
                                continue
                            if neighbor not in target_nodes:
                                if neighbor[1] in excluded_ids:
                                    continue
                                if intermediate_filter["allow"] and neighbor[0] not in intermediate_filter["allow"]:
                                    continue
                                if neighbor[0] in intermediate_filter["exclude"]:
                                    continue
                                if hub["enabled"] and self._degree(neighbor, deadline, degree_cache) > hub["max_degree"]:
                                    continue
                            edge = {
                                "predicate": predicate,
                                "source": source,
                                "target": target,
                            }
                            next_state = {
                                "nodes": state["nodes"] + [neighbor],
                                "edges": state["edges"] + [edge],
                            }
                            if neighbor in target_nodes:
                                paths.append(next_state)
                            elif len(next_state["edges"]) < max_hops:
                                next_frontier.append(next_state)
            frontier = next_frontier
            if not frontier:
                break

        candidates = []
        for path in paths:
            edges = []
            valid = True
            for raw_edge in path["edges"]:
                evidence = self._edge_evidence(raw_edge, deadline, evidence_cache)
                if any(
                    not _compare(evidence.get(item["field"]), item["operator"], item["value"])
                    for item in request["evidence_filters"]
                ):
                    valid = False
                    break
                edges.append({
                    "source": raw_edge["source"][1],
                    "target": raw_edge["target"][1],
                    "source_type": raw_edge["source"][0],
                    "target_type": raw_edge["target"][0],
                    "type": self._output_predicate(raw_edge["predicate"], request),
                    "evidence": deepcopy(evidence),
                })
            if not valid:
                continue
            nodes = [
                {
                    "id": node[1],
                    "type": node[0],
                    "properties": self._node_properties(
                        node, deadline, property_cache, degree_cache
                    ),
                }
                for node in path["nodes"]
            ]
            candidates.append({"nodes": nodes, "edges": edges})

        candidates.sort(
            key=lambda candidate: (
                len(candidate["edges"]),
                -self._score(candidate["edges"]),
                tuple(node["id"] for node in candidate["nodes"]),
                tuple(edge["type"] for edge in candidate["edges"]),
            )
        )
        cap = min(max(request["k"] * 5, request["k"] + 1), 1000)
        truncated = len(candidates) > cap
        return {
            "candidates": candidates[:cap],
            "unresolved_ids": unresolved,
            "truncated": truncated,
        }
