# src/game/graph.py

import json
from pathlib import Path

GRAPH_DATA_DIR = Path(__file__).parent.parent.parent / 'data' / 'graph_data'

def _load(filename):
    with open(GRAPH_DATA_DIR / filename) as f:
        raw = json.load(f)
    return {int(k): {int(k2): v2 for k2, v2 in v.items()} for k, v in raw.items()}

# ─── loaded data ──────────────────────────────────────────────────────────────

vertex_distances   = _load('vertex_distances.json')
vertex_to_vertices = _load('vertex_to_vertices.json')
vertex_to_edges    = _load('vertex_to_edges.json')

# ─── queries ──────────────────────────────────────────────────────────────────

def vertex_distance(v1, v2):
    return vertex_distances[v1].get(v2, float('inf'))

def vertices_n_away(vertex, n):
    return [v for v, dist in vertex_distances[vertex].items() if dist == n]

def vertices_within_n(vertex, n):
    return [v for v, dist in vertex_distances[vertex].items() if dist <= n]

def neighbors(vertex):
    return list(vertex_to_vertices[vertex].values())

def adjacent_edges(vertex):
    return list(vertex_to_edges[vertex].values())