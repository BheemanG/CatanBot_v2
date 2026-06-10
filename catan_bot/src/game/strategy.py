from src.constants.board import (
    RESOURCE_TYPES, 
    DICE_VALUE,
    EDGE_TO_VERTICES,
    VERTEX_TO_HEXES
)
from src.game.graph import (
    vertex_distances,
    vertex_to_vertices,
    vertex_to_edges,
    vertex_distance,
    vertices_n_away,
    vertices_within_n,
    neighbors,
    adjacent_edges,
)

def score_hex(h):
    value = DICE_VALUE.get(h.dice, 0)
    if h.resource == 3:  # wool penalty
        value = max(0, value - 1)
    return value

def score_vertex(v_id, state):
    return sum(score_hex(state.hexes[h_id]) for h_id in VERTEX_TO_HEXES[v_id])

def calculate_placement(state, msg_payload):
    return max(msg_payload, key=lambda v_id: score_vertex(v_id, state))


def decide(msg_type, msg_payload, state):
    if msg_type == 4:
        state.parse_board(msg_payload)
    elif msg_type == 30:
        return {
            "action": 15,
            "payload": calculate_placement(state, msg_payload),
            "sequence": state.next_sequence()
        }
    return None