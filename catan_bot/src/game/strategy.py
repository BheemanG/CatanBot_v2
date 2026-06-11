import random, time
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

def calculate_placement_settlement(state, msg_payload):
    vertex = max(msg_payload, key=lambda v_id: score_vertex(v_id, state))
    state.vertices[vertex] = state.my_color
    return vertex

def calculate_placement_road(state, msg_payload):
    
    return random.choice(msg_payload)


def decide(msg_type, msg_payload, state):
    if msg_type == 4:
        state.parse_board(msg_payload)
    elif msg_type == 30: #payload contains available settlement locations
        if not msg_payload:
            return None
        time.sleep(1.5)
        return {
            "action": 15,
            "payload": calculate_placement_settlement(state, msg_payload),
            "sequence": state.next_sequence()
        }
    elif msg_type == 31:
        if not msg_payload:
            return None
        time.sleep(1.5)
        return {
            "action": 11,
            "payload": calculate_placement_road(state, msg_payload),
            "sequence": state.next_sequence()
        }
    return None