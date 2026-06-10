from src.constants.board import *
from src.game.graph

def calculate_placement(state):
    # your placement logic here
    return 0

def decide(msg_type, state):
    if msg_type == 4:
        state.parse_board
    elif msg_type == 30:
        return {
            "action": 15,
            "payload": calculate_placement(state),
            "sequence": state.next_sequence()
        }
    return None