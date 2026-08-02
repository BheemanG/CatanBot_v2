import random, time
from src.constants.board import (
    RSC_TYPES,
    RSC_IDS,
    DICE_VALUE,
    EDGE_TO_VERTICES,
    VERTEX_TO_HEXES
)
from src.constants.message_types import MsgType, Action
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


ROAD_COST       = {1: 1, 2: 1}
SETTLEMENT_COST = {1: 1, 2: 1, 3: 1, 4: 1}
CITY_COST       = {4: 2, 5: 3}
DEV_CARD_COST   = {3: 1, 4: 1, 5: 1}

def score_hex(h):
    value = DICE_VALUE.get(h.dice, 0)
    if h.resource == RSC_IDS['wool']:
        value = max(0, value - 1)
    return value

def score_vertex(v_id, state):
    return sum(score_hex(state.hexes[h_id]) for h_id in VERTEX_TO_HEXES[v_id])

def can_afford(player, costs):
    return all(player.resources.get(rsc, 0) >= amt for rsc, amt in costs.items())

def valid_settlement_spots(state):
    my_edges = {e for e, owner in enumerate(state.edges) if owner == state.my_color}
    spots = []
    for v in range(54):
        if state.vertices[v] is not None:
            continue
        if any(state.vertices[n] is not None for n in neighbors(v)):
            continue
        if any(e in my_edges for e in adjacent_edges(v)):
            spots.append(v)
    return spots

def valid_city_spots(state):
    return list(state.my_player().settlements)

def valid_road_spots(state):
    my_color = state.my_color
    my_edges = {e for e, owner in enumerate(state.edges) if owner == my_color}
    valid = []
    for e, (v1, v2) in EDGE_TO_VERTICES.items():
        if state.edges[e] is not None:
            continue
        for v in (v1, v2):
            owner = state.vertices[v]
            if owner == my_color:
                valid.append(e)
                break
            if owner is None and any(adj in my_edges for adj in adjacent_edges(v)):
                valid.append(e)
                break
    return valid

def best_road(road_spots, state):
    return max(road_spots, key=lambda e: max(
        score_vertex(v, state) for v in EDGE_TO_VERTICES[e]
    ))

def calculate_placement_settlement(state, msg_payload):
    vertex = max(msg_payload, key=lambda v_id: score_vertex(v_id, state))
    state.vertices[vertex] = state.my_color
    return vertex

def calculate_placement_road(state, msg_payload):
    return random.choice(msg_payload)

def decide_turn(state):
    player = state.my_player()

    settlement_spots = valid_settlement_spots(state)
    if settlement_spots and can_afford(player, SETTLEMENT_COST):
        best = max(settlement_spots, key=lambda v: score_vertex(v, state))
        return {"action": Action.BUILD_SETTLEMENT, "payload": best, "sequence": state.next_sequence()}

    city_spots = valid_city_spots(state)
    if city_spots and can_afford(player, CITY_COST):
        best = max(city_spots, key=lambda v: score_vertex(v, state))
        return {"action": Action.BUILD_CITY, "payload": best, "sequence": state.next_sequence()}

    road_spots = valid_road_spots(state)
    if not settlement_spots and road_spots and can_afford(player, ROAD_COST):
        return {"action": Action.BUILD_ROAD, "payload": best_road(road_spots, state), "sequence": state.next_sequence()}

    if can_afford(player, DEV_CARD_COST):
        return {"action": Action.BUY_DEV_CARD, "payload": True, "sequence": state.next_sequence()}

    return {"action": Action.END_TURN, "payload": True, "sequence": state.next_sequence()}

def decide(msg_type, msg_payload, state):
    if msg_type == MsgType.INITIALIZE_MAP:
        state.parse_board(msg_payload)
        return
    elif msg_type == MsgType.RESOURCE_DISTRIBUTION:
        for p in msg_payload:
            state.players[p.get('owner')].gain_resources([p.get('card')])

    elif msg_type == MsgType.AVAILABLE_SETTLEMENT_PLACEMENTS and state.my_player().vp <= 1 and msg_payload:
        time.sleep(1.5)
        return {
            "action": Action.PLACE_INITIAL_SETTLEMENT,
            "payload": calculate_placement_settlement(state, msg_payload),
            "sequence": state.next_sequence()
        }
    elif msg_type == MsgType.AVAILABLE_ROAD_PLACEMENTS and msg_payload:
        time.sleep(1.5)
        return {
            "action": Action.PLACE_INITIAL_ROAD,
            "payload": calculate_placement_road(state, msg_payload),
            "sequence": state.next_sequence()
        }

    elif msg_type == MsgType.RESOURCE_EXCHANGE:
        giving_player   = msg_payload.get('givingPlayer')
        receiving_player = msg_payload.get('receivingPlayer')
        giving_cards    = msg_payload.get('givingCards', [])
        receiving_cards = msg_payload.get('receivingCards', [])
        state.players[giving_player].lose_resources(giving_cards)
        state.players[giving_player].gain_resources(receiving_cards)
        state.players[receiving_player].gain_resources(giving_cards)
        state.players[receiving_player].lose_resources(receiving_cards)
        return
    elif msg_type == MsgType.GAME_STATE_UPDATE:
        state.update(msg_payload.get('diff'))
        if state.current_turn == state.my_color and state.turn_state == 1:
            time.sleep(1.5)
            #consider using a dev card before rolling the dice
            return {
                "action": Action.ROLL_DICE,
                "payload": True,
                "sequence": state.next_sequence()
            }
        if state.current_turn == state.my_color and state.turn_state == 2:
            time.sleep(1.0)
            return decide_turn(state)

        my_color_str = str(state.my_color)
        for offer_id, offer in state.active_offers.items():
            if offer.get('creator') != state.my_color and my_color_str not in offer.get('playerResponses', {}):
                return {
                    "action": Action.RESPOND_TO_TRADE,
                    "payload": {"id": offer_id, "response": 1},  # 1 = decline
                    "sequence": state.next_sequence()
                }

    return None