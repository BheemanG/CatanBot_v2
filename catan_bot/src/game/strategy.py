import time
from src.constants.board import (
    RSC_TYPES,
    RSC_IDS,
    DICE_VALUE,
    EDGE_TO_VERTICES,
    VERTEX_TO_HEXES,
    HEX_TO_VERTICES,
    HEX_RING,
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

MAX_SETTLEMENTS = 5  # physical piece limit — must upgrade one to a city to free a piece
MAX_CITIES      = 4  # physical piece limit

EARLY_GAME_VP = 4  # below this, prioritize expanding (roads/settlements) over trading or dev cards

SURPLUS_DUMP_THRESHOLD = 6  # at/above this count, trade the resource away regardless of other give-side protections

def score_hex(h):
    value = DICE_VALUE.get(h.dice, 0)
    if h.resource == RSC_IDS['wool']:
        value = max(0, value - 1)
    return value

def score_vertex(v_id, state):
    return sum(score_hex(state.hexes[h_id]) for h_id in VERTEX_TO_HEXES[v_id])

def port_value(v_id, state, weights=None):
    """Strategic value of settling on a port vertex. A generic 3:1 port is always somewhat
    useful. A specific 2:1 port is only worth anything if we actually produce (or would
    produce, by settling at v_id) that resource somewhere in our network — a wheat port is
    dead weight if nothing we own makes wheat, no matter how well wheat fits our archetype.
    Scaled by how many pips of that resource we produce: a 2:1 port feeding a small trickle
    is only marginally better than dead weight, but one feeding a big production engine (e.g.
    several lumber hexes) is worth heavily prioritizing over a similar-distance non-port spot."""
    port = state.ports.get(v_id)
    if not port:
        return 0
    if port['resource'] is None:
        return 1.5
    pips = board_income(state).get(port['resource'], 0)
    for h_id in VERTEX_TO_HEXES[v_id]:
        h = state.hexes[h_id]
        if h and h.resource == port['resource']:
            pips += DICE_VALUE.get(h.dice, 0)
    if pips == 0:
        return 0.5
    w = weights.get(port['resource'], 1.0) if weights else 1.0
    return (2.0 + 0.3 * pips) * w

def score_vertex_buildable(v_id, state):
    """score_vertex plus port bonus — used when choosing WHERE to place a new settlement
    or road, since gaining port access is a strategic win on top of hex income. Not used
    for city-upgrade comparisons, where the port (if any) is already secured."""
    return score_vertex(v_id, state) + port_value(v_id, state)

def can_afford(player, costs):
    return all(player.resources.get(rsc, 0) >= amt for rsc, amt in costs.items())

def is_open_vertex(v_id, state):
    """True if v_id could legally hold a settlement — unoccupied and not adjacent to any
    existing settlement/city (the two-road distance rule applies regardless of owner)."""
    return state.vertices[v_id] is None and not any(state.vertices[n] is not None for n in neighbors(v_id))

def valid_settlement_spots(state):
    if len(state.my_player().settlements) >= MAX_SETTLEMENTS:
        return []  # no settlement pieces left — must upgrade one to a city first
    my_edges = {e for e, owner in enumerate(state.edges) if owner == state.my_color}
    spots = []
    for v in range(54):
        if not is_open_vertex(v, state):
            continue
        if any(e in my_edges for e in adjacent_edges(v)):
            spots.append(v)
    return spots

def valid_city_spots(state):
    if len(state.my_player().cities) >= MAX_CITIES:
        return []  # no city pieces left
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

    return {v: dist for v, dist in visited.items() if is_open_vertex(v, state)}

def best_road(road_spots, state):
    def road_score(e):
        reachable = reachable_settleable_vertices(e, state)
        if not reachable:
            return -1
        # discount by hop count so a nearby decent vertex beats a tied/better one that's farther away
        return max(score_vertex_buildable(v, state) / (1 + dist) for v, dist in reachable.items())
    return max(road_spots, key=road_score)

# Ore-Wheat-Sheep: favours ore/grain, sheep moderately — halved deviation from neutral (1.0)
# vs. the original weights so raw pips and resource diversity drive vertex scoring first,
# with archetype fit acting as a lighter tie-breaking nudge rather than the dominant term.
OWS_WEIGHTS = {5: 1.25, 4: 1.2, 3: 1.05, 1: 0.85, 2: 0.85}
# Lumber-Brick: favours lumber/brick — same halved-deviation treatment as OWS_WEIGHTS.
LB_WEIGHTS  = {1: 1.25, 2: 1.25, 3: 1.0, 4: 0.9, 5: 0.8}
ARCHETYPE_WEIGHTS = {'ows': OWS_WEIGHTS, 'lb': LB_WEIGHTS}

DIVERSITY_WEIGHT = 1.5  # bonus per distinct resource type touched by a single vertex

def score_vertex_weighted(v_id, state, weights):
    score = 0
    resources = set()
    for h_id in VERTEX_TO_HEXES[v_id]:
        h = state.hexes[h_id]
        if not h or not h.resource or h.resource == 0:
            continue
        score += DICE_VALUE.get(h.dice, 0) * weights.get(h.resource, 1.0)
        resources.add(h.resource)
    score += len(resources) * DIVERSITY_WEIGHT
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

OUTWARD_BIAS_WEIGHT = 2.0  # per ring step; only applied in 4-player games

def outward_bias(v_id):
    """Average ring distance from the board center of hexes touching v_id (0 = center,
    2 = outer ring) — higher means closer to the map's edge."""
    hexes = VERTEX_TO_HEXES[v_id]
    return sum(HEX_RING.get(h, 0) for h in hexes) / len(hexes)

def calculate_placement_road(state, msg_payload):
    """Point toward the highest-scoring future settlement vertex actually reachable through
    this road — BFS's past the immediate one-hop vertex (which, by the distance rule, is
    itself never settleable once we own the adjacent settlement) the same way best_road does
    for in-game roads. Without this, a road toward hexes boxed in by an opponent's settlement
    next door could outscore a direction that's genuinely open, since a raw one-hop score has
    no way to tell a rich dead end from a rich, reachable spot.

    In 4-player games the center of the board gets contested fast, so reachable vertices get
    an extra nudge toward the map's outside (higher HEX_RING) on top of their resource score."""
    four_player = (len(state.players) - 1) == 4  # players dict includes bank at key 0

    def vertex_value(v):
        score = score_vertex_initial(v, state)
        if four_player:
            score += outward_bias(v) * OUTWARD_BIAS_WEIGHT
        return score

    def road_score(edge_id):
        reachable = reachable_settleable_vertices(edge_id, state)
        if not reachable:
            return -1
        return max(vertex_value(v) / (1 + dist) for v, dist in reachable.items())

    best = max(msg_payload, key=road_score)
    if road_score(best) != -1:
        return best

    # no settleable vertex reachable in any direction (rare) — fall back to raw one-hop score
    my_vertices = {v for v, owner in enumerate(state.vertices) if owner == state.my_color}
    def target_of(edge_id):
        v1, v2 = EDGE_TO_VERTICES[edge_id]
        return v2 if v1 in my_vertices else v1
    return max(msg_payload, key=lambda e: vertex_value(target_of(e)))

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

def best_build_completing_trade(state, give_pool, ratios, settlement_spots, city_spots):
    """Look for a single bank trade that would leave us able to afford a settlement or
    city immediately this turn — preferred over chasing our generally rarest resource,
    since it turns the trade directly into a build instead of just rebalancing the hand.
    Only fires when a build is exactly one resource short (a single bank trade only nets
    1 unit of the wanted resource, so a 2+ resource deficit can't be closed in one trade)."""
    player = state.my_player()
    res = player.resources

    goals = []
    if settlement_spots:
        goals.append(SETTLEMENT_COST)
    if city_spots:
        goals.append(CITY_COST)

    candidates = []
    for cost in goals:
        missing = {r: amt - res.get(r, 0) for r, amt in cost.items() if res.get(r, 0) < amt}
        if len(missing) != 1:
            continue
        want, need = next(iter(missing.items()))
        if need > 1:
            continue
        for give in give_pool:
            if give == want:
                continue
            # trading away `give` must not itself drop below what this same build needs
            if res.get(give, 0) - ratios[give] < cost.get(give, 0):
                continue
            candidates.append((give, want))

    if not candidates:
        return None
    # prefer trading away whichever resource leaves the biggest post-trade surplus, discounted
    # by how badly we still need that resource ourselves (resource_need_score) — a big pile of
    # something we barely produce (e.g. from a steal or Monopoly) is riskier to give up than an
    # equal-sized pile of something our board generates plenty of, since we can't easily refill it
    income = board_income(state)
    def give_score(gw):
        give, _ = gw
        surplus = res.get(give, 0) - ratios[give]
        caution = 1 / (1 + resource_need_score(give, income, state))
        return surplus * caution
    return max(candidates, key=give_score)

def try_bank_trade(state):
    player = state.my_player()
    res = player.resources
    ratios = state.port_ratios()
    income = board_income(state)

    tradeable = [r for r in range(1, 6) if res.get(r, 0) >= ratios[r]]

    # never give away a resource we don't produce on the board at all — we'd have no way
    # to replenish it — and below EARLY_GAME_VP, also protect lumber/brick specifically
    # since we still need them to build roads/settlements rather than trade them away
    protected_pool = [r for r in tradeable if income.get(r, 0) > 0]
    if player.vp < EARLY_GAME_VP:
        protected_pool = [r for r in protected_pool if r not in (1, 2)]

    # ...but at SURPLUS_DUMP_THRESHOLD+ those protections stop mattering — a pile that
    # large is worth shedding for something useful even if we can't easily replenish it
    overflow_pool = [r for r in tradeable if res.get(r, 0) >= SURPLUS_DUMP_THRESHOLD]

    give_pool = sorted(set(protected_pool) | set(overflow_pool))
    if not give_pool:
        return None

    settlement_spots = valid_settlement_spots(state)
    city_spots = valid_city_spots(state)
    road_spots = valid_road_spots(state)

    build_trade = best_build_completing_trade(state, give_pool, ratios, settlement_spots, city_spots)
    if build_trade:
        give, want = build_trade
    else:
        # proactive: any resource at SURPLUS_DUMP_THRESHOLD+ risks a discard on a 7 — trade it down now
        has_surplus = any(res.get(r, 0) >= SURPLUS_DUMP_THRESHOLD for r in give_pool)

        if not has_surplus:
            # targeted: only trade if one trade closes a single-resource deficit for a build
            goals = []
            if settlement_spots:
                goals.append(SETTLEMENT_COST)
            if city_spots:
                goals.append(CITY_COST)
            if not settlement_spots and road_spots:
                goals.append(ROAD_COST)
            if player.vp >= EARLY_GAME_VP:
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
        want_candidates = [r for r in range(1, 6) if r not in give_pool]
        if not want_candidates:
            want_candidates = sorted(range(1, 6), key=lambda r: res.get(r, 0))

        want = max(want_candidates, key=lambda r: resource_need_score(r, income, state))
        # prefer the resource with the most trades' worth of surplus once its port ratio is
        # accounted for — a modest stack behind a 2:1 port can outrank a bigger 4:1 stack —
        # but discount that by resource_need_score so a resource we barely produce isn't
        # traded away just because we're currently sitting on a pile of it
        give = max(
            give_pool,
            key=lambda r: (
                (res.get(r, 0) // ratios[r]) / (1 + resource_need_score(r, income, state)),
                res.get(r, 0),
            ),
        )

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

def try_player_trade(state):
    """Propose a 1-for-1 trade to the table (action 49, isBankTrade=False) as a fallback
    when try_bank_trade found nothing. Only proposes (give, want) pairs where some opponent
    holds more than 1 of `want` (so they can actually spare one) and none of `give` (so it's
    something they'd want), we hold multiple of `give` (so parting with one still leaves us
    some), and `want` scores as genuinely valuable to us via resource_need_score (low income,
    low count) — not just a resource we happen to be short on right now."""
    player = state.my_player()
    res = player.resources
    income = board_income(state)

    # never give away a resource we don't produce on the board at all, and below
    # EARLY_GAME_VP also protect lumber/brick specifically for road/settlement building
    give_pool = [r for r in range(1, 6) if res.get(r, 0) >= 2 and income.get(r, 0) > 0]
    if player.vp < EARLY_GAME_VP:
        give_pool = [r for r in give_pool if r not in (1, 2)]
    if not give_pool:
        return None

    candidates = []
    for color, opp in state.players.items():
        if color == 0 or color == state.my_color:
            continue
        for want in range(1, 6):
            if opp.resources.get(want, 0) <= 1:
                continue
            for give in give_pool:
                if give == want:
                    continue
                if opp.resources.get(give, 0) != 0:
                    continue
                candidates.append((give, want))

    if not candidates:
        return None

    # prefer the pair where `want` is most valuable to us, discounted by how much we'd
    # still need `give` ourselves (don't hand over something we're also short on)
    def pair_score(gw):
        give, want = gw
        return resource_need_score(want, income, state) / (1 + resource_need_score(give, income, state))
    give, want = max(candidates, key=pair_score)

    return {
        "action": Action.SEND_TRADE,
        "payload": {
            "creator": state.my_color,
            "isBankTrade": False,
            "counterOfferInResponseToTradeId": None,
            "offeredResources": [give],
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

    # below EARLY_GAME_VP, push to expand the road network toward future settlement spots
    # even when settlement_spots isn't empty (we just can't afford one yet) — better than
    # trading resources away this early
    if player.vp < EARLY_GAME_VP and road_spots and can_afford(player, ROAD_COST):
        return {"action": Action.BUILD_ROAD, "payload": best_road(road_spots, state), "sequence": state.next_sequence()}

    if not settlement_spots and road_spots and can_afford(player, ROAD_COST):
        return {"action": Action.BUILD_ROAD, "payload": best_road(road_spots, state), "sequence": state.next_sequence()}

    trade = try_bank_trade(state)
    if trade:
        return trade

    trade = try_player_trade(state)
    if trade:
        return trade

    if not state.dev_card_played and 13 in state.turn_start_dev_cards:
        state.dev_card_played = True
        return {"action": Action.CONFIRM_DEV_CARD, "payload": 13, "sequence": state.next_sequence()}

    # below EARLY_GAME_VP, don't spend scarce wool/grain/ore on a dev card — hold it for
    # the next road/settlement instead
    if player.vp >= EARLY_GAME_VP and can_afford(player, DEV_CARD_COST):
        return {"action": Action.BUY_DEV_CARD, "payload": True, "sequence": state.next_sequence()}

    return {"action": Action.END_TURN, "payload": True, "sequence": state.next_sequence()}

def decide(msg_type, msg_payload, state):
    if msg_type == MsgType.GAME_SETTINGS:
        # type 1 is the first message on a fresh WS connection (new game or reconnect) —
        # out_sequence is per-connection state, so it resets here, not on type 4
        state.out_sequence = 1
        return None
    elif msg_type == MsgType.INITIALIZE_MAP:
        state.parse_board(msg_payload)

        # parse_board already set current_turn/turn_state from currentState, so we can
        # act on them immediately instead of waiting for the next type 91 diff (e.g. a
        # mid-game reconnect that re-sends the full board on our own roll/build turn)
        if state.turn_state == 1 and state.current_turn == state.my_color:
            state.needs_roll = True
            player = state.my_player()
            state.turn_start_dev_cards = list(player.dev_cards)
            if not state.dev_card_played and 11 in state.turn_start_dev_cards:
                state.dev_card_played = True
                time.sleep(1.0)
                return {"action": Action.CONFIRM_DEV_CARD, "payload": 11, "sequence": state.next_sequence()}
            state.needs_roll = False
            time.sleep(1.5)
            return {"action": Action.ROLL_DICE, "payload": True, "sequence": state.next_sequence()}

        if state.current_turn == state.my_color and state.turn_state == 2:
            time.sleep(1.0)
            return decide_turn(state)

        return None
    elif msg_type == MsgType.RESOURCE_DISTRIBUTION:
        for p in msg_payload:
            state.players[p.get('owner')].gain_resources([p.get('card')])

    elif msg_type == MsgType.AVAILABLE_SETTLEMENT_PLACEMENTS and state.my_player().vp <= 1 and msg_payload:
        # type 4/91 don't reliably carry currentTurnPlayerColor during the setup phase,
        # so state.current_turn can still be None here — receiving this prompt at all is
        # proof it's our turn, and leaving it unset breaks the roll-detection fallback in
        # the type 91 handler once normal turns begin (see GAME_STATE_UPDATE branch above)
        state.current_turn = state.my_color
        time.sleep(1.5)
        return {
            "action": Action.PLACE_INITIAL_SETTLEMENT,
            "payload": calculate_placement_settlement(state, msg_payload),
            "sequence": state.next_sequence()
        }
    elif msg_type == MsgType.AVAILABLE_ROAD_PLACEMENTS and msg_payload and state.road_building_pending > 0:
        # Road Building dev card: both free roads use action 21, confirmed via live testing
        state.road_building_pending -= 1
        time.sleep(1.0)
        return {
            "action": Action.ROAD_BUILDING_SECOND_ROAD,
            "payload": best_road(msg_payload, state),
            "sequence": state.next_sequence()
        }
    elif msg_type == MsgType.AVAILABLE_ROAD_PLACEMENTS and msg_payload:
        state.current_turn = state.my_color  # see comment in AVAILABLE_SETTLEMENT_PLACEMENTS above
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

        # state not fully initialized yet (e.g. server restarted mid-game and this diff
        # arrived before a fresh type 4) — nothing below can be decided without knowing
        # our own color/hand, and several checks assume state.my_player() is not None
        if state.my_color is None:
            return None

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

        # finalize our own trade offers as soon as some opponent accepts (response=1)
        for offer_id, offer in state.active_offers.items():
            if offer.get('creator') != state.my_color or offer_id in state.finalized_offers:
                continue
            accepted_by = [int(c) for c, r in offer.get('playerResponses', {}).items() if r == 1]
            if accepted_by:
                state.finalized_offers.add(offer_id)
                return {
                    "action": Action.FINALIZE_TRADE,
                    "payload": {"tradeId": offer_id, "playerToExecuteTradeWith": accepted_by[0]},
                    "sequence": state.next_sequence()
                }

        # respond to pending enemy trade offers before taking any turn action
        my_color_str = str(state.my_color)
        for offer_id, offer in state.active_offers.items():
            if (
                offer.get('creator') != state.my_color
                and offer.get('playerResponses', {}).get(my_color_str, 0) == 0
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