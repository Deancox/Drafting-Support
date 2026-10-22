"""Engineering graph (DATA_MODEL.md section 5): a networkx MultiDiGraph rebuilt per run
from the linked nodes and edges. Node ids are typed keys ("DEVICE:S-TN36D4"); edge keys
are edge ids, so parallel edges stay separate."""

from __future__ import annotations

import networkx as nx

from drawing_qa.model.engineering import EngineeringModel


def build_graph(model: EngineeringModel) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for node in model.nodes.values():
        g.add_node(node.id, type=node.type, key=node.key, status=node.status.value, parsers=node.parsers)
    for edge in model.edges:
        g.add_edge(edge.src, edge.dst, key=edge.id, type=edge.type, status=edge.status.value)
    return g


def neighbours(g: nx.MultiDiGraph, node: str, edge_type: str) -> list[str]:
    """Nodes reached from `node` over edges of one type, in either direction, sorted."""
    out = {v for _, v, d in g.out_edges(node, data=True) if d["type"] == edge_type}
    out |= {u for u, _, d in g.in_edges(node, data=True) if d["type"] == edge_type}
    return sorted(out)
