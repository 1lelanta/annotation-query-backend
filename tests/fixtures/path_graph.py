"""Backend-neutral graph used by path endpoint tests."""

NODES = [
    {"id": "gene-a", "type": "gene", "properties": {"degree": 1}},
    {"id": "protein-a", "type": "protein", "properties": {"degree": 1}},
    {"id": "pathway-a", "type": "pathway", "properties": {"degree": 1}},
    {"id": "gene-b", "type": "gene", "properties": {"degree": 1}},
    {"id": "hub", "type": "protein", "properties": {"degree": 999}},
    {"id": "gene-c", "type": "gene", "properties": {"degree": 1}},
    {"id": "protein-b", "type": "protein", "properties": {"degree": 1}},
]

EDGES = [
    {"source": "gene-a", "target": "protein-a", "source_type": "gene", "target_type": "protein",
     "type": "interacts with", "evidence": {"score": 900, "p_value": 0.01}},
    {"source": "protein-a", "target": "pathway-a", "source_type": "protein", "target_type": "pathway",
     "type": "participates in", "evidence": {"score": 800, "p_value": 0.02}},
    {"source": "pathway-a", "target": "gene-b", "source_type": "pathway", "target_type": "gene",
     "type": "regulates", "evidence": {"score": 700, "p_value": 0.03}},
    {"source": "gene-a", "target": "hub", "source_type": "gene", "target_type": "protein",
     "type": "interacts with", "evidence": {"score": 950, "p_value": 0.01}},
    {"source": "hub", "target": "gene-b", "source_type": "protein", "target_type": "gene",
     "type": "regulates", "evidence": {"score": 950, "p_value": 0.01}},
    {"source": "gene-a", "target": "protein-b", "source_type": "gene", "target_type": "protein",
     "type": "interacts with", "evidence": {"score": 600, "p_value": 0.04}},
    {"source": "protein-b", "target": "gene-b", "source_type": "protein", "target_type": "gene",
     "type": "regulates", "evidence": {"score": 600, "p_value": 0.04}},
]

CANDIDATES = [
    {"nodes": [NODES[0], NODES[1], NODES[2], NODES[3]], "edges": EDGES[:3]},
    {"nodes": [NODES[0], NODES[4], NODES[3]], "edges": [EDGES[3], EDGES[4]]},
]

THREE_PATH_CANDIDATES = [
    CANDIDATES[0],
    {"nodes": [NODES[0], NODES[1], NODES[2], NODES[3]],
     "edges": [
         {**EDGES[0], "evidence": {"score": 750, "p_value": 0.02}},
         {**EDGES[1], "evidence": {"score": 750, "p_value": 0.02}},
         {**EDGES[2], "evidence": {"score": 750, "p_value": 0.02}},
     ]},
    {"nodes": [NODES[0], NODES[6], NODES[3]], "edges": EDGES[5:7]},
]
