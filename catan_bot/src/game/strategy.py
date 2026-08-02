import time
from src.constants.board import (
    RSC_TYPES,
    RSC_IDS,
    DICE_VALUE,
    EDGE_TO_VERTICES,
    VERTEX_TO_HEXES,
    HEX_TO_VERTICES,
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

def port_value(v_id, state, weights=None):
    """Strategic value of settling on a port vertex. A specific 2:1 port is worth roughly
    an extra hex of that resource (scaled by archetype weight when known); a generic 3:1
    port gets a flat, smaller bonus since it helps whatever we're surplus in."""
    port = state.ports.get(v_id)
    if not port:
        return 0
    if port['resource'] is None:
        return 1.5
    w = weights.get(port['resource'], 1.0) if weights else 1.0
    return 2.5 * w

def score_vertex_buildable(v_id, state):
    """score_vertex plus port bonus — used when choosing WHERE to place a new settlement
    or road, since gaining port access is a strategic win on top of hex income. Not used
    for city-upgrade comparisons, where the port (if any) is already secured."""
    return score_vertex(v_id, state) + port_value(v_id, state)

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

def reachable_settleable_vertices(candidate_edge, state):
    """BFS from candidate_edge through open/our-own territory. Returns {vertex: hop_count}
    for every settleable vertex found, so callers can discount distant vertices instead of
    treating anything eventually reachable as equally good."""
    my_color = state.my_color
    visited = {}
    queue = []

    for v in EDGE_TO_VERTICES[candidate_edge]:
        owner = state.vertices[v]
        if owner is None or owner == my_color:
            visited[v] = 0
            queue.append(v)

    i = 0
    while i < len(queue):
        v = queue[i]; i += 1
        dist = visited[v]
        for e in adjacent_edges(v):
            edge_owner = state.edges[e]
            if edge_owner is not None and edge_owner != my_color:
                continue  # opponent road blocks this edge
            for nv in EDGE_TO_VERTICES[e]:
                if nv in visited:
                    continue
                nv_owner = state.vertices[nv]
                if nv_owner is not None and nv_owner != my_color:
                    continue  # opponent settlement blocks traversal
                visited[nv] = dist + 1
                queue.append(nv)

    return {
        v: dist for v, dist in visited.items()
        if state.vertices[v] is None
        and not any(state.vertices[n] is not None for n in neighbors(v))
    }

def best_road(road_spots, state):
    def road_score(e):
        reachable = reachable_settleable_vertices(e, state)
        if not reachable:
            return -1
        # discount by hop count so a nearby decent vertex beats a tied/better one that's farther away
        return max(score_vertex_buildable(v, state) / (1 + dist) for v, dist in reachable.items())
    return max(road_spots, key=road_score)

# Ore-Wheat-Sheep: favours ore/grain heavily (city + dev card engine), sheep moderately.
OWS_WEIGHTS = {5: 1.5, 4: 1.4, 3: 1.1, 1: 0.7, 2: 0.7}
# Lumber-Brick: favours lumber/brick heavily (cheap settlements + roads to rush expansion).
LB_WEIGHTS  = {1: 1.5, 2: 1.5, 3: 1.0, 4: 0.8, 5: 0.6}
ARCHETYPE_WEIGHTS = {'ows': OWS_WEIGHTS, 'lb': LB_WEIGHTS}

def score_vertex_weighted(v_id, state, weights):
    score = 0
    for h_id in VERTEX_TO_HEXES[v_id]:
        h = state.hexes[h_id]
        if not h or not h.resource or h.resource == 0:
            continue
        score += DICE_VALUE.get(h.dice, 0) * weights.get(h.resource, 1.0)
    score += port_value(v_id, state, weights)
    return score

def score_vertex_initial(v_id, state):
    """First settlement: score under both the Ore-Wheat-Sheep and Lumber-Brick archetypes
    and take whichever the board rewards more — lets the opening go wherever the hexes
    actually support. Second settlement: score under the archetype the first settlement
    committed us to (state.build_archetype), plus a smaller bonus for resource types
    the first settlement didn't cover, so we still hedge a little instead of going
    all-in on one archetype."""
    have_settlement = any(owner == state.my_color for owner in state.vertices)

    if not have_settlement:
        return max(
            score_vertex_weighted(v_id, state, OWS_WEIGHTS),
            score_vertex_weighted(v_id, state, LB_WEIGHTS),
        )

    weights = ARCHETYPE_WEIGHTS.get(state.build_archetype, OWS_WEIGHTS)
    score = score_vertex_weighted(v_id, state, weights)

    covered = set()
    for v, owner in enumerate(state.vertices):
        if owner == state.my_color:
            for h_id in VERTEX_TO_HEXES[v]:
                h = state.hexes[h_id]
                if h and h.resource and h.resource != 0:
                    covered.add(h.resource)
    seen = set()
    for h_id in VERTEX_TO_HEXES[v_id]:
        h = state.hexes[h_id]
        if not h or not h.resource or h.resource == 0:
            continue
        if h.resource not in covered and h.resource not in seen:
            score += DICE_VALUE.get(h.dice, 0) * 0.5  # smaller nudge than the archetype weighting
            seen.add(h.resource)
    return score

def calculate_placement_settlement(state, msg_payload):
    is_first = not any(owner == state.my_color for owner in state.vertices)
    vertex = max(msg_payload, key=lambda v_id: score_vertex_initial(v_id, state))
    if is_first:
        ows = score_vertex_weighted(vertex, state, OWS_WEIGHTS)
        lb = score_vertex_weighted(vertex, state, LB_WEIGHTS)
        state.build_archetype = 'ows' if ows >= lb else 'lb'
    state.vertices[vertex] = state.my_color
    return vertex

def calculate_placement_road(state, msg_payload):
    """Point toward the highest-scoring future settlement vertex reachable from this road."""
    my_vertices = {v for v, owner in enumerate(state.vertices) if owner == state.my_color}
    def road_score(edge_id):
        v1, v2 = EDGE_TO_VERTICES[edge_id]
        target = v2 if v1 in my_vertices else v1
        return score_vertex_initial(target, state)
    return max(msg_payload, key=road_score)

def score_robber_hex(hex_id, state):
    my_color = state.my_color
    hex_vertices = HEX_TO_VERTICES.get(hex_id, [])
    if any(state.vertices[v] == my_color for v in hex_vertices):
        return -1  # never block ourselves
    opponent_count = sum(
        1 for v in hex_vertices
        if state.vertices[v] is not None and state.vertices[v] != my_color
    )
    return opponent_count * DICE_VALUE.get(state.hexes[hex_id].dice, 0)

def board_income(state):
    """Expected dice-weighted income per resource type from our current board position."""
    income = {r: 0 for r in range(1, 6)}
    player = state.my_player()
    for v_id, owner in enumerate(state.vertices):
        if owner != state.my_color:
            continue
        mult = 2 if v_id in player.cities else 1
        for h_id in VERTEX_TO_HEXES[v_id]:
            h = state.hexes[h_id]
            if h and h.resource and h.resource != 0:
                income[h.resource] += DICE_VALUE.get(h.dice, 0) * mult
    return income

def resource_need_score(r, income, state):
    """Higher = more beneficial to trade for this resource.
    Favours resources we generate little of and need for upcoming builds."""
    player = state.my_player()
    count = player.resources.get(r, 0)

    settlement_spots = valid_settlement_spots(state)
    city_spots = valid_city_spots(state)
    road_spots = valid_road_spots(state)
    demand = (
        SETTLEMENT_COST.get(r, 0) * (3 if settlement_spots else 0)
        + CITY_COST.get(r, 0) * (3 if city_spots else 0)
        + ROAD_COST.get(r, 0) * (1 if (not settlement_spots and road_spots) else 0)
        + DEV_CARD_COST.get(r, 0)
    )
    return (demand + 1) / (income.get(r, 0) + 1) / (count + 1)

def is_beneficial_trade(offered, wanted, state):
    """offered = resources the creator gives us, wanted = resources they want from us.
    Beneficial if we can actually give what's wanted and what we receive is worth more
    to us (by resource_need_score) than what we give up."""
    player = state.my_player()
    wanted_costs = {}
    for r in wanted:
        wanted_costs[r] = wanted_costs.get(r, 0) + 1
    if not can_afford(player, wanted_costs):
        return False

    income = board_income(state)
    gain = sum(resource_need_score(r, income, state) for r in offered)
    cost = sum(resource_need_score(r, income, state) for r in wanted)
    return gain > cost

def choose_year_of_plenty(state):
    """Pick 2 resources (bank gift, duplicates allowed) favouring unmet build needs.
    Each pick discounts its own score before the second pick, so we cover a second
    distinct deficit instead of doubling up unless one resource is needed twice."""
    income = board_income(state)
    picks = []
    picked_counts = {}
    for _ in range(2):
        def score(r):
            return resource_need_score(r, income, state) / (1 + picked_counts.get(r, 0))
        chosen = max(range(1, 6), key=score)
        picks.append(chosen)
        picked_counts[chosen] = picked_counts.get(chosen, 0) + 1
    return picks

def try_bank_trade(state):
    player = state.my_player()
    res = player.resources
    ratios = state.port_ratios()

    give_pool = [r for r in range(1, 6) if res.get(r, 0) >= ratios[r]]
    if not give_pool:
        return None

    # proactive: any resource at 5+ risks a discard on a 7 — trade it down now
    has_surplus = any(res.get(r, 0) >= 5 for r in give_pool)

    if not has_surplus:
        # targeted: only trade if one trade closes a single-resource deficit for a build
        settlement_spots = valid_settlement_spots(state)
        city_spots = valid_city_spots(state)
        road_spots = valid_road_spots(state)
        goals = []
        if settlement_spots:
            goals.append(SETTLEMENT_COST)
        if city_spots:
            goals.append(CITY_COST)
        if not settlement_spots and road_spots:
            goals.append(ROAD_COST)
        goals.append(DEV_CARD_COST)

        can_unlock = any(
            res.get(needed, 0) < amt
            and any(r != needed for r in give_pool)
            for cost in goals
            for needed, amt in cost.items()
            if res.get(needed, 0) < amt
        )
        if not can_unlock:
            return None

    # want: highest-need resource we don't already have 4+ of
    income = board_income(state)
    want_candidates = [r for r in range(1, 6) if r not in give_pool]
    if not want_candidates:
        want_candidates = sorted(range(1, 6), key=lambda r: res.get(r, 0))

    want = max(want_candidates, key=lambda r: resource_need_score(r, income, state))
    # prefer the resource with the most trades' worth of surplus once its port ratio is
    # accounted for — a modest stack behind a 2:1 port can outrank a bigger 4:1 stack
    give = max(give_pool, key=lambda r: (res.get(r, 0) // ratios[r], res.get(r, 0)))

    if give == want:
        return None

    return {
        "action": Action.SEND_TRADE,
        "payload": {
            "creator": state.my_color,
            "isBankTrade": True,
            "counterOfferInResponseToTradeId": None,
            "offeredResources": [give] * ratios[give],
            "wantedResources": [want],
        },
        "sequence": state.next_sequence(),
    }

def decide_turn(state):
    player = state.my_player()

    settlement_spots = valid_settlement_spots(state)
    if settlement_spots and can_afford(player, SETTLEMENT_COST):
        best = max(settlement_spots, key=lambda v: score_vertex_buildable(v, state))
        return {"action": Action.BUILD_SETTLEMENT, "payload": best, "sequence": state.next_sequence()}

    city_spots = valid_city_spots(state)
    if city_spots and can_afford(player, CITY_COST):
        best = max(city_spots, key=lambda v: score_vertex(v, state))
        return {"action": Action.BUILD_CITY, "payload": best, "sequence": state.next_sequence()}

    if not state.dev_card_played and 15 in state.turn_start_dev_cards:
        state.dev_card_played = True
        return {"action": Action.CONFIRM_DEV_CARD, "payload": 15, "sequence": state.next_sequence()}

    road_spots = valid_road_spots(state)
    if not state.dev_card_played and 14 in state.turn_start_dev_cards and road_spots:
        state.dev_card_played = True
        state.road_building_pending = 2
        return {"action": Action.CONFIRM_DEV_CARD, "payload": 14, "sequence": state.next_sequence()}

    if not settlement_spots and road_spots and can_afford(player, ROAD_COST):
        return {"action": Action.BUILD_ROAD, "payload": best_road(road_spots, state), "sequence": state.next_sequence()}

    trade = try_bank_trade(state)
    if trade:
        return trade

    if not state.dev_card_played and 13 in state.turn_start_dev_cards:
        state.dev_card_played = True
        return {"action": Action.CONFIRM_DEV_CARD, "payload": 13, "sequence": state.next_sequence()}

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
    elif msg_type == MsgType.AVAILABLE_ROAD_PLACEMENTS and msg_payload and state.road_building_pending > 0:
        # Road Building dev card: two free roads. First road's action code is
        # unverified per notes.md (guessed as regular Build Road); second is
        # confirmed as action 21.
        is_first = state.road_building_pending == 2
        state.road_building_pending -= 1
        action_code = Action.BUILD_ROAD if is_first else Action.ROAD_BUILDING_SECOND_ROAD
        time.sleep(1.0)
        return {
            "action": action_code,
            "payload": best_road(msg_payload, state),
            "sequence": state.next_sequence()
        }
    elif msg_type == MsgType.AVAILABLE_ROAD_PLACEMENTS and msg_payload:
        time.sleep(1.5)
        return {
            "action": Action.PLACE_INITIAL_ROAD,
            "payload": calculate_placement_road(state, msg_payload),
            "sequence": state.next_sequence()
        }

    elif msg_type == MsgType.CHOOSE_PLAYER_TO_ROB and msg_payload:
        state.robber_pending = False
        candidates = msg_payload.get('playersToSelect', [])
        target = max(candidates, key=lambda c: sum(state.players[c].resources.values()))
        return {"action": Action.STEAL_FROM_PLAYER, "payload": target, "sequence": state.next_sequence()}

    elif msg_type == MsgType.CHOOSE_PLAYER_TO_ROB_KNIGHT and msg_payload:
        # same steal-selection prompt as type 29, but triggered by playing a knight pre-roll,
        # with candidates nested under selectPlayerFormat instead of top-level
        state.robber_pending = False
        candidates = msg_payload.get('selectPlayerFormat', {}).get('playersToSelect', [])
        target = max(candidates, key=lambda c: sum(state.players[c].resources.values()))
        return {"action": Action.STEAL_FROM_PLAYER, "payload": target, "sequence": state.next_sequence()}

    elif msg_type == MsgType.CARD_SELECTION_PROMPT:
        card_format = msg_payload.get('selectCardFormat', {})
        amount = card_format.get('amountOfCardsToSelect', 1)
        dev_card_used = msg_payload.get('developmentCardUsed')

        if dev_card_used == 15 or amount == 2:
            # Year of Plenty: take 2 resources from the bank, no opponent component
            time.sleep(0.5)
            return {"action": Action.CONFIRM_CARD_SELECTION, "payload": choose_year_of_plenty(state), "sequence": state.next_sequence()}

        # Monopoly: pick resource that maximises (what opponents hold) * (how much we need it)
        income = board_income(state)
        opponent_totals = {r: 0 for r in range(1, 6)}
        for color, player in state.players.items():
            if color != state.my_color and color != 0:
                for r, count in player.resources.items():
                    opponent_totals[r] += count
        chosen = max(
            range(1, 6),
            key=lambda r: opponent_totals[r] * resource_need_score(r, income, state)
        )
        time.sleep(0.5)
        return {"action": Action.CONFIRM_CARD_SELECTION, "payload": [chosen], "sequence": state.next_sequence()}

    elif msg_type == MsgType.DISCARD:
        card_format = msg_payload.get('selectCardFormat', {})
        hand = card_format.get('validCardsToSelect', [])
        n = card_format.get('amountOfCardsToSelect', 0)

        counts = {}
        for c in hand:
            counts[c] = counts.get(c, 0) + 1

        # keep at least 1 of each resource a currently-buildable settlement/city needs
        protect = {}
        if valid_settlement_spots(state):
            for r in SETTLEMENT_COST:
                protect[r] = max(protect.get(r, 0), 1)
        if valid_city_spots(state):
            for r in CITY_COST:
                protect[r] = max(protect.get(r, 0), 1)

        # discard most-held cards first; break ties by least valuable (lumber=1 first, ore=5 last)
        least_valuable = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
        def discard_order(c):
            return (-counts[c], least_valuable.get(c, 99))

        # spare copies (beyond the protected amount) go first; only dip into protected
        # copies if forced to reach the required discard count
        spare_pool = []
        protected_pool = []
        remaining = dict(counts)
        for c in sorted(hand, key=discard_order):
            if remaining[c] > protect.get(c, 0):
                spare_pool.append(c)
                remaining[c] -= 1
            else:
                protected_pool.append(c)

        to_discard = (spare_pool + sorted(protected_pool, key=discard_order))[:n]
        time.sleep(1.0)
        return {"action": Action.CONFIRM_CARD_SELECTION, "payload": to_discard, "sequence": state.next_sequence()}

    elif msg_type == MsgType.AVAILABLE_ROBBER_PLACEMENTS and msg_payload:
        state.robber_pending = True
        time.sleep(1.0)
        best = max(msg_payload, key=lambda h: score_robber_hex(h, state))
        return {
            "action": Action.PLACE_ROBBER,
            "payload": best,
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
        # if only one opponent was adjacent to the robbed hex, the server auto-steals
        # without ever sending us a choose-player prompt (type 20/29) — this exchange
        # is the only signal that the pending steal has resolved
        state.robber_pending = False
        return
    elif msg_type == MsgType.GAME_STATE_UPDATE:
        diff = msg_payload.get('diff', {})
        current_diff = diff.get('currentState', {})
        state.update(diff)

        # robber was moved: if no opponents are adjacent to new hex, steal sequence is done
        if 'mechanicRobberState' in diff and state.robber_pending:
            hex_verts = HEX_TO_VERTICES.get(state.robber_hex, [])
            if not any(
                state.vertices[v] is not None and state.vertices[v] != state.my_color
                for v in hex_verts
            ):
                state.robber_pending = False

        # turnState=1 transition on our turn — set flag and optionally play knight first
        if 'currentTurnPlayerColor' in current_diff:
            is_my_turn = current_diff['currentTurnPlayerColor'] == state.my_color
        else:
            is_my_turn = state.current_turn == state.my_color
        if current_diff.get('turnState') == 1 and is_my_turn:
            state.needs_roll = True
            player = state.my_player()
            # snapshot cards owned as of turn start — cards bought this turn aren't playable yet
            state.turn_start_dev_cards = list(player.dev_cards)
            if not state.dev_card_played and 11 in state.turn_start_dev_cards:
                state.dev_card_played = True
                time.sleep(1.0)
                return {"action": Action.CONFIRM_DEV_CARD, "payload": 11, "sequence": state.next_sequence()}

        # roll when flagged and steal sequence is not pending
        if state.needs_roll and state.current_turn == state.my_color and state.turn_state == 1 and not state.robber_pending:
            state.needs_roll = False
            time.sleep(1.5)
            return {"action": Action.ROLL_DICE, "payload": True, "sequence": state.next_sequence()}

        # respond to pending enemy trade offers before taking any turn action
        my_color_str = str(state.my_color)
        for offer_id, offer in state.active_offers.items():
            if (
                offer.get('creator') != state.my_color
                and my_color_str not in offer.get('playerResponses', {})
                and offer_id not in state.responded_offers
            ):
                state.responded_offers.add(offer_id)
                offered = offer.get('offeredResources', [])
                wanted = offer.get('wantedResources', [])
                response = 0 if is_beneficial_trade(offered, wanted, state) else 1
                return {
                    "action": Action.RESPOND_TO_TRADE,
                    "payload": {"id": offer_id, "response": response},
                    "sequence": state.next_sequence()
                }

        if state.current_turn == state.my_color and state.turn_state == 2 and not state.robber_pending:
            time.sleep(1.0)
            return decide_turn(state)

    return None