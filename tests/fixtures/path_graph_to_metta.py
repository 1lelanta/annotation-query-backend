import argparse
from pathlib import Path

from tests.fixtures.path_graph import CANDIDATES, NODES


def _atom(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).replace(" ", "_")


def write_fixture(directory):
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    nodes = {node["id"]: node for node in NODES}
    edges = {}
    for candidate in CANDIDATES:
        for node in candidate["nodes"]:
            nodes.setdefault(node["id"], node)
        for edge in candidate["edges"]:
            key = (edge["type"], edge["source"], edge["target"])
            edges.setdefault(key, edge)

    lines = []
    for node in nodes.values():
        node_type = node["type"]
        node_id = node["id"]
        lines.append(f"({node_type} {node_id})")
        for key, value in node.get("properties", {}).items():
            lines.append(f"({key} ({node_type} {node_id}) {_atom(value)})")
    for edge in edges.values():
        predicate = edge["type"].replace(" ", "_")
        source = f"({edge['source_type']} {edge['source']})"
        target = f"({edge['target_type']} {edge['target']})"
        lines.append(f"({predicate} {source} {target})")
        for key, value in edge.get("evidence", {}).items():
            lines.append(
                f"({key} ({predicate} {source} {target}) {_atom(value)})"
            )
    (output / "path_graph.metta").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    write_fixture(args.directory)


if __name__ == "__main__":
    main()
