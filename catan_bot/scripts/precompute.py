from collections import deque
import json
import os
import sys
from src.constants.board import EDGE_TO_VERTICES

# ─── paths ────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'graph_data')
os.makedirs(DATA_DIR, exist_ok=True)

DISTANCES_PATH     = os.path.join(DATA_DIR, 'vertex_distances.json')
VERT_TO_VERT_PATH  = os.path.join(DATA_DIR, 'vertex_to_vertices.json')
VERT_TO_EDGES_PATH = os.path.join(DATA_DIR, 'vertex_to_edges.json')

# ─── source of truth ──────────────────────────────────────────────────────────

EDGE_TO_VERTICES = {
    0:  [0, 1],
    1:  [1, 2],
    2:  [2, 3],
    3:  [3, 4],
    4:  [4, 5],
    5:  [5, 0],
    6:  [3, 6],
    7:  [6, 7],
    8:  [7, 8],
    9:  [8, 9],
    10: [9, 4],
    11: [7, 10],
    12: [10, 11],
    13: [11, 12],
    14: [12, 13],
    15: [13, 8],
    16: [10, 14],
    17: [14, 15],
    18: [15, 16],
    19: [16, 17],
    20: [17, 11],
    21: [15, 18],
    22: [18, 19],
    23: [19, 20],
    24: [20, 21],
    25: [21, 16],
    26: [22, 23],
    27: [23, 24],
    28: [24, 25],
    29: [25, 19],
    30: [18, 22],
    31: [26, 27],
    32: [27, 28],
    33: [28, 29],
    34: [29, 24],
    35: [23, 26],
    36: [30, 31],
    37: [31, 32],
    38: [32, 27],
    39: [26, 33],
    40: [33, 30],
    41: [34, 35],
    42: [35, 36],
    43: [36, 31],
    44: [30, 37],
    45: [37, 34],
    46: [38, 39],
    47: [39, 34],
    48: [37, 40],
    49: [40, 41],
    50: [41, 38],
    51: [42, 43],
    52: [43, 38],
    53: [41, 44],
    54: [44, 45],
    55: [45, 42],
    56: [46, 45],
    57: [44, 47],
    58: [47, 2],
    59: [1, 46],
    60: [47, 48],
    61: [48, 49],
    62: [6, 49],
    63: [49, 50],
    64: [50, 14],
    65: [50, 51],
    66: [51, 22],
    67: [52, 33],
    68: [51, 52],
    69: [52, 53],
    70: [40, 53],
    71: [48, 53],
}

MAX_DISTANCE = 11

# ─── auto-generate adjacency ──────────────────────────────────────────────────

VERTEX_TO_VERTICES = {}
VERTEX_TO_EDGES = {}

for edge_id, (v1, v2) in EDGE_TO_VERTICES.items():
    VERTEX_TO_VERTICES.setdefault(v1, []).append(v2)
    VERTEX_TO_VERTICES.setdefault(v2, []).append(v1)
    VERTEX_TO_EDGES.setdefault(v1, []).append(edge_id)
    VERTEX_TO_EDGES.setdefault(v2, []).append(edge_id)

# ─── BFS ──────────────────────────────────────────────────────────────────────

def bfs(start, max_dist):
    distances = {}
    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        vertex, dist = queue.popleft()
        if dist > max_dist:
            continue
        distances[vertex] = dist
        for neighbor in VERTEX_TO_VERTICES.get(vertex, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, dist + 1))
    return distances

# ─── precompute ───────────────────────────────────────────────────────────────

def precompute_distances(max_dist=MAX_DISTANCE):
    all_distances = {}
    for vertex in VERTEX_TO_VERTICES:
        all_distances[vertex] = bfs(vertex, max_dist)
        print(f'[PRECOMPUTE] vertex {vertex} done')
    return all_distances

# ─── save / load ──────────────────────────────────────────────────────────────

def save(data, path):
    serializable = {
        str(k): {str(k2): v2 for k2, v2 in v.items()}
        for k, v in data.items()
    }
    with open(path, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f'[PRECOMPUTE] saved to {path}')

def load(path):
    with open(path, 'r') as f:
        raw = json.load(f)
    return {
        int(k): {int(k2): v2 for k2, v2 in v.items()}
        for k, v in raw.items()
    }

# ─── main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('[PRECOMPUTE] building adjacency lists...')
    print('[PRECOMPUTE] computing distances...')
    distances = precompute_distances()
    save(distances, DISTANCES_PATH)

    print('[PRECOMPUTE] saving adjacency lists...')
    save({k: {str(i): v for i, v in enumerate(vs)}
          for k, vs in VERTEX_TO_VERTICES.items()}, VERT_TO_VERT_PATH)
    save({k: {str(i): v for i, v in enumerate(vs)}
          for k, vs in VERTEX_TO_EDGES.items()}, VERT_TO_EDGES_PATH)

    print('[PRECOMPUTE] verifying...')
    d = load(DISTANCES_PATH)
    print(f'  distance 0 -> 53: {d[0].get(53, "not reachable")}')
    print(f'  distance 0 -> 1:  {d[0].get(1,  "not reachable")}')
    print(f'  distance 0 -> 0:  {d[0].get(0,  "not reachable")}')
    print(f'  distance 11 -> 26:  {d[11].get(26,  "not reachable")}')

    print('[PRECOMPUTE] done')