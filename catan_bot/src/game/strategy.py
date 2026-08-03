import time
from src.constants.board import (
    RSC_TYPES,
    RSC_IDS,
    DICE_VALUE,
    EDGE_TO_VERTICES,
    VERTEX_TO_HEXES,
    HEX_TO_VERTICES,
    OUTSIDE_VERTICES,
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

ALREADY_HAVE_ENOUGH = 1     # already holding this many of a resource makes receiving more not worth it
HIGH_PRODUCTION_PIPS = 6    # board income at/above this means we don't need to trade for more of it

TRADE_RESPONSE_WAIT_SECONDS = 5  # hold off ending the turn this long after a player trade offer, to give opponents a chance to respond

MIN_HAND_FOR_BANK_TRADE = 5  # below this total hand size, a bank trade's ratio cost eats too much of what little we have

INCOME_EMPHASIS = 2  # exponent on board income in need/shortfall scoring — >1 makes production
                      # (pips we already roll for) matter more than raw demand or hand count when
                      # deciding what's safe to give away vs. worth protecting/receiving

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

# Ore-Wheat-Sheep: a light lean toward ore/grain/wool — kept close to neutral (1.0) so raw
# pips and resource diversity (DIVERSITY_WEIGHT) remain the dominant factors in initial
# placement; this is just a tie-breaking nudge, not a committed archetype.
OWS_WEIGHTS = {5: 1.1, 4: 1.08, 3: 1.03, 1: 0.95, 2: 0.95}

DIVERSITY_WEIGHT = 2.0  # bonus per distinct resource type touched by a single vertex — the primary driver

def score_vertex_weighted(v_id, state):
    score = 0
    resources = set()
    for h_id in VERTEX_TO_HEXES[v_id]:
        h = state.hexes[h_id]
        if not h or not h.resource or h.resource == 0:
            continue
        score += DICE_VALUE.get(h.dice, 0) * OWS_WEIGHTS.get(h.resource, 1.0)
        resources.add(h.resource)
    score += len(resources) * DIVERSITY_WEIGHT
    score += port_value(v_id, state, OWS_WEIGHTS)
    return score

def score_vertex_initial(v_id, state):
    """Score every candidate by raw dice-weighted pips plus a bonus per distinct resource
    type touched (resource diversity is the primary driver), with only a light OWS lean
    as a tie-breaking nudge — not a committed archetype. The second settlement additionally
    gets a smaller bonus for resource types the first settlement didn't already cover, so
    the pair hedges toward covering all resource types rather than doubling up."""
    score = score_vertex_weighted(v_id, state)

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
            score += DICE_VALUE.get(h.dice, 0) * 0.5  # smaller nudge than the primary scoring
            seen.add(h.resource)
    return score

def calculate_placement_settlement(state, msg_payload):
    vertex = max(msg_payload, key=lambda v_id: score_vertex_initial(v_id, state))
    state.vertices[vertex] = state.my_color
    return vertex

def calculate_placement_road(state, msg_payload):
    """Point toward the highest-scoring future settlement vertex actually reachable through
    this road — BFS's past the immediate one-hop vertex (which, by the distance rule, is
    itself never settleable once we own the adjacent settlement) the same way best_road does
    for in-game roads. Without this, a road toward hexes boxed in by an opponent's settlement
    next door could outscore a direction that's genuinely open, since a raw one-hop score has
    no way to tell a rich dead end from a rich, reachable spot.

    In 4-player games the center of the board gets contested fast, so instead of scoring by
    resource value at all, point straight at whichever reachable coastline vertex
    (OUTSIDE_VERTICES — anything touching fewer than 3 hexes is on the outer boundary) is
    closest, since claiming outward territory before it's boxed in matters more here than
    which specific spot has the best pips."""
    four_player = (len(state.players) - 1) == 4  # players dict includes bank at key 0

    def road_score(edge_id):
        reachable = reachable_settleable_vertices(edge_id, state)
        if not reachable:
            return -1
        if four_player:
            outside_dists = [dist for v, dist in reachable.items() if v in OUTSIDE_VERTICES]
            if outside_dists:
                return -min(outside_dists)
            return -999  # no coastline vertex reachable this direction at all
        return max(score_vertex_initial(v, state) / (1 + dist) for v, dist in reachable.items())

    best = max(msg_payload, key=road_score)
    if road_score(best) != -1:
        return best

    # no settleable vertex reachable in any direction (rare) — fall back to raw one-hop score
    my_vertices = {v for v, owner in enumerate(state.vertices) if owner == state.my_color}
    def target_of(edge_id):
        v1, v2 = EDGE_TO_VERTICES[edge_id]
        return v2 if v1 in my_vertices else v1
    return max(msg_payload, key=lambda e: score_vertex_initial(target_of(e), state))

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
    Favours resources we generate little of and need for upcoming builds. The income term is
    raised to INCOME_EMPHASIS so how much we already produce a resource ourselves outweighs raw
    demand/count — a resource with no board income of our own is both much more worth receiving
    and much riskier to give away, since dice rolls will never refill it for us."""
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
    return (demand + 1) / (income.get(r, 0) + 1) ** INCOME_EMPHASIS / (count + 1)

def active_build_goals(state, player):
    """Currently pursuable build costs — settlement/city/road wherever spots are open, plus
    dev card once past EARLY_GAME_VP (or as the sole fallback goal if nothing else applies).
    Shared by is_beneficial_trade and both try_*_trade functions so "what do we actually
    still need" is computed the same way everywhere."""
    goals = []
    if valid_settlement_spots(state):
        goals.append(SETTLEMENT_COST)
    if valid_city_spots(state):
        goals.append(CITY_COST)
    if valid_road_spots(state):
        goals.append(ROAD_COST)
    if player.vp >= EARLY_GAME_VP:
        goals.append(DEV_CARD_COST)
    if not goals:
        goals = [DEV_CARD_COST]
    return goals

def needed_amounts(goals):
    """Max amount any active goal's cost requires, per resource."""
    needed = {}
    for cost in goals:
        for r, amt in cost.items():
            needed[r] = max(needed.get(r, 0), amt)
    return needed

def worth_wanting_more(r, res, income, needed):
    """True if resource r is still worth acquiring more of — either some active goal needs
    more of it than we currently hold, or (for resources no goal needs more of) we're below
    ALREADY_HAVE_ENOUGH and don't already produce plenty of it ourselves."""
    if res.get(r, 0) < needed.get(r, 0):
        return True
    return res.get(r, 0) < ALREADY_HAVE_ENOUGH and income.get(r, 0) < HIGH_PRODUCTION_PIPS

def is_beneficial_trade(offered, wanted, state):
    """offered = resources the creator gives us, wanted = resources they want from us.
    Build costs need every resource type simultaneously (a settlement needs brick AND
    lumber AND wool AND grain), so comparing aggregate resource_need_score sums can accept
    a trade that looks good in the abstract but actually hurts us — e.g. giving up our only
    brick for a 2nd grain scores well on need-score alone (grain's demand weight is high)
    even though it strictly makes every build harder, not easier.

    Instead, simulate the post-trade hand and compare total resource shortfall against our
    best currently-available build goal (settlement/city/road with open spots, or dev card
    as fallback) — only accept if the trade strictly reduces that shortfall for at least one
    real goal. Falls back to the need-score comparison only when the trade is a wash (doesn't
    change shortfall either way) — a genuine lateral resource-shape optimization.

    Before any of that: decline outright if nothing on offer is actually worth receiving —
    i.e. every offered resource is one we already hold enough of for every active build goal
    (a city needing 2 grain still makes a 2nd grain worth receiving even though we already
    hold 1 — ALREADY_HAVE_ENOUGH is only the floor for resources no active goal needs more
    of), and is either at/above ALREADY_HAVE_ENOUGH or something we produce plenty of
    (board_income >= HIGH_PRODUCTION_PIPS). More of a resource no real goal is short on isn't
    worth giving anything up for, whatever the shortfall math below says."""
    player = state.my_player()
    res = player.resources
    wanted_costs = {}
    for r in wanted:
        wanted_costs[r] = wanted_costs.get(r, 0) + 1
    if not can_afford(player, wanted_costs):
        return False

    income = board_income(state)
    goals = active_build_goals(state, player)
    needed_for_goals = needed_amounts(goals)

    if not any(worth_wanting_more(r, res, income, needed_for_goals) for r in offered):
        return False

    post = dict(res)
    for r in wanted:
        post[r] = post.get(r, 0) - 1
    for r in offered:
        post[r] = post.get(r, 0) + 1

    # weight each missing unit by how hard it'd be to replace ourselves — a shortfall in a
    # resource with no (or thin) board income of our own counts for more than the same
    # shortfall in something the dice hand us regularly, since only the former is a real risk
    # to actually giving up (mirrors resource_need_score's income emphasis)
    def shortfall(resources, cost):
        return sum(
            max(0, amt - resources.get(r, 0)) / (income.get(r, 0) + 1) ** INCOME_EMPHASIS
            for r, amt in cost.items()
        )

    before = min(shortfall(res, cost) for cost in goals)
    after = min(shortfall(post, cost) for cost in goals)

    if after != before:
        return after < before

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

    # a bank trade always costs a whole ratio's worth of one resource (2-4 cards) for just
    # 1 back — with a thin hand that's too big a chunk to give up, even for a build-completing
    # trade, so skip bank trading entirely until the hand has some real depth to it
    if sum(res.values()) < MIN_HAND_FOR_BANK_TRADE:
        return None

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

    # the early-game lumber/brick exclusion protects them for future builds, but a
    # build-completing trade already only spends surplus beyond what that same build
    # needs (see best_build_completing_trade's own per-build check) — so it's exempt
    build_give_pool = [r for r in tradeable if income.get(r, 0) > 0]

    if not give_pool and not build_give_pool:
        return None

    settlement_spots = valid_settlement_spots(state)
    city_spots = valid_city_spots(state)
    road_spots = valid_road_spots(state)

    build_trade = best_build_completing_trade(state, build_give_pool, ratios, settlement_spots, city_spots)
    if build_trade:
        give, want = build_trade
    elif not give_pool:
        return None
    else:
        # proactive: any resource at SURPLUS_DUMP_THRESHOLD+ risks a discard on a 7 — trade it down now
        has_surplus = any(res.get(r, 0) >= SURPLUS_DUMP_THRESHOLD for r in give_pool)

        if not has_surplus:
            # targeted: only trade if one trade closes a single-resource deficit for a build
            unlock_goals = []
            if settlement_spots:
                unlock_goals.append(SETTLEMENT_COST)
            if city_spots:
                unlock_goals.append(CITY_COST)
            if not settlement_spots and road_spots:
                unlock_goals.append(ROAD_COST)
            if player.vp >= EARLY_GAME_VP:
                unlock_goals.append(DEV_CARD_COST)

            can_unlock = any(
                res.get(needed, 0) < amt
                and any(r != needed for r in give_pool)
                for cost in unlock_goals
                for needed, amt in cost.items()
                if res.get(needed, 0) < amt
            )
            if not can_unlock:
                return None

        # want: highest-need resource we don't already have 4+ of
        want_candidates = [r for r in range(1, 6) if r not in give_pool]
        if not want_candidates:
            want_candidates = sorted(range(1, 6), key=lambda r: res.get(r, 0))

        if not has_surplus:
            # don't ask for a duplicate of something we already have enough of — only the
            # proactive discard-risk dump (has_surplus) skips this, since converting excess
            # into *anything* still beats losing it to a random 7
            goals = active_build_goals(state, player)
            needed_for_goals = needed_amounts(goals)
            worth_wanting = [r for r in want_candidates if worth_wanting_more(r, res, income, needed_for_goals)]
            if not worth_wanting:
                return None
            want_candidates = worth_wanting

        # prefer rarer resources (board income below HIGH_PRODUCTION_PIPS) over ones we
        # already produce consistently — no point trading for more of what the dice hand
        # us regularly — unless a settlement or city we can currently work toward still
        # needs more of that specific resource than we have
        settlement_city_needs = set()
        for cost in ([SETTLEMENT_COST] if settlement_spots else []) + ([CITY_COST] if city_spots else []):
            for r, amt in cost.items():
                if res.get(r, 0) < amt:
                    settlement_city_needs.add(r)
        rare_candidates = [
            r for r in want_candidates
            if income.get(r, 0) < HIGH_PRODUCTION_PIPS or r in settlement_city_needs
        ]
        if rare_candidates:
            want_candidates = rare_candidates

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

def best_build_completing_player_trade(state, give_pool):
    """Mirrors best_build_completing_trade but for 1-for-1 player trades: if a settlement or
    city is exactly one resource short, and at least one opponent holds any of it at all,
    offering it directly beats the generic opponent-matching search in try_player_trade —
    completing a build is worth asking for even if no opponent happens to fit the usual
    '>1 held, 0 of our give' pattern that search requires."""
    player = state.my_player()
    res = player.resources

    goals = []
    if valid_settlement_spots(state):
        goals.append(SETTLEMENT_COST)
    if valid_city_spots(state):
        goals.append(CITY_COST)

    opponents = [opp for color, opp in state.players.items() if color not in (0, state.my_color)]

    candidates = []
    for cost in goals:
        missing = {r: amt - res.get(r, 0) for r, amt in cost.items() if res.get(r, 0) < amt}
        if len(missing) != 1:
            continue
        want, need = next(iter(missing.items()))
        if need > 1:
            continue
        if not any(opp.resources.get(want, 0) >= 1 for opp in opponents):
            continue  # no one at the table even has one to give
        for give in give_pool:
            if give == want:
                continue
            # giving away one unit must not itself drop below what this same build needs
            if res.get(give, 0) - 1 < cost.get(give, 0):
                continue
            candidates.append((give, want))

    if not candidates:
        return None

    income = board_income(state)
    def give_score(gw):
        give, _ = gw
        surplus = res.get(give, 0)
        caution = 1 / (1 + resource_need_score(give, income, state))
        return surplus * caution
    return max(candidates, key=give_score)

def try_player_trade(state):
    """Propose a 1-for-1 trade to the table (action 49, isBankTrade=False) as a fallback
    when try_bank_trade found nothing. First checks best_build_completing_player_trade — if
    a settlement or city is exactly one resource short and someone at the table holds any of
    it, ask for that directly. Otherwise falls back to the generic search: only proposes
    (give, want) pairs where some opponent holds more than 1 of `want` (so they can actually
    spare one) and none of `give` (so it's something they'd want), we hold multiple of `give`
    (so parting with one still leaves us some), `want` is actually worth acquiring more of
    (worth_wanting_more — not a duplicate of something we already have enough of, like asking
    for a 5th lumber), and `want` scores as genuinely valuable to us via resource_need_score
    among what's left. Never proposes a new offer while one of ours is still outstanding, and
    skips any (give, want) pair already closed without acceptance this turn
    (state.rejected_trade_pairs) so a decline doesn't just get re-asked on the next tick.

    decide_turn() re-runs on every type 91 diff while it's our turn, but colonist doesn't
    echo our offer back with an id (and thus into state.active_offers) instantly — if a
    different, unrelated type 91 arrives in that gap, the active_offers-based outstanding
    check below hasn't caught up yet and this would otherwise fire again and send a
    duplicate before the first send is even acknowledged. state.pending_player_trade closes
    that race: set the instant we send, cleared once GameState.update() sees our offer
    actually land with an id."""
    if state.pending_player_trade:
        return None
    if any(o.get('creator') == state.my_color for o in state.active_offers.values()):
        return None

    player = state.my_player()
    res = player.resources
    income = board_income(state)

    # never give away a resource we don't produce on the board at all
    build_give_pool = [r for r in range(1, 6) if res.get(r, 0) >= 2 and income.get(r, 0) > 0]

    # below EARLY_GAME_VP, also protect lumber/brick specifically for road/settlement
    # building — except for a build-completing trade, which already only spends surplus
    # beyond what that same build needs (see best_build_completing_player_trade's own
    # per-build check), so there's no reason to withhold a spare brick that would directly
    # finish the very settlement this exclusion exists to protect
    give_pool = build_give_pool
    if player.vp < EARLY_GAME_VP:
        give_pool = [r for r in give_pool if r not in (1, 2)]

    if not build_give_pool:
        return None

    build_trade = best_build_completing_player_trade(state, build_give_pool)
    if build_trade:
        give, want = build_trade
    elif not give_pool:
        return None
    else:
        goals = active_build_goals(state, player)
        needed_for_goals = needed_amounts(goals)

        candidates = []
        for color, opp in state.players.items():
            if color == 0 or color == state.my_color:
                continue
            for want in range(1, 6):
                if opp.resources.get(want, 0) <= 1:
                    continue
                if not worth_wanting_more(want, res, income, needed_for_goals):
                    continue
                for give in give_pool:
                    if give == want:
                        continue
                    if opp.resources.get(give, 0) != 0:
                        continue
                    # giving away one unit must not itself drop below what our own active
                    # goals still need — e.g. don't trade away ore for grain if the very
                    # city we're working toward needs that ore too and we have no reliable
                    # way to replenish it
                    if res.get(give, 0) - 1 < needed_for_goals.get(give, 0):
                        continue
                    if (give, want) in state.rejected_trade_pairs:
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

    if (give, want) in state.rejected_trade_pairs:
        return None

    state.pending_player_trade = True
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

    # push to expand the road network only when we don't already have an open, reachable
    # settlement spot — if one exists but isn't affordable yet, hold lumber/brick (protected
    # from trading below EARLY_GAME_VP) and let try_bank_trade/try_player_trade work toward
    # completing it instead of spending those same resources on more roads, which only
    # pushes the settlement further out of reach
    if not settlement_spots and road_spots and can_afford(player, ROAD_COST):
        return {"action": Action.BUILD_ROAD, "payload": best_road(road_spots, state), "sequence": state.next_sequence()}

    trade = try_bank_trade(state)
    if trade:
        return trade

    trade = try_player_trade(state)
    if trade:
        state.player_trade_sent_at = time.time()
        return trade

    if not state.dev_card_played and 13 in state.turn_start_dev_cards:
        state.dev_card_played = True
        return {"action": Action.CONFIRM_DEV_CARD, "payload": 13, "sequence": state.next_sequence()}

    # below EARLY_GAME_VP, don't spend scarce wool/grain/ore on a dev card — hold it for
    # the next road/settlement instead. Exception: a hand at/above SURPLUS_DUMP_THRESHOLD
    # is already at risk of losing half of it to a rolled 7, and buying a dev card is the
    # only way to shed resource cards without gaining any back (unlike a trade), so it's
    # worth doing regardless of VP stage.
    hand_size = sum(player.resources.values())
    if (player.vp >= EARLY_GAME_VP or hand_size >= SURPLUS_DUMP_THRESHOLD) and can_afford(player, DEV_CARD_COST):
        return {"action": Action.BUY_DEV_CARD, "payload": True, "sequence": state.next_sequence()}

    # ending the turn closes any outstanding player-trade offer of ours, so hold off for
    # a few seconds after proposing one to give opponents an actual chance to respond.
    # This blocks synchronously (rather than returning None and waiting to be re-invoked
    # by a future incoming message) because the server has no other way to resume a
    # deferred decision: it's pure request/response with no timer thread, and the
    # Tampermonkey script drops heartbeat messages before they ever reach /incoming. If
    # nothing else happens on the board in that window (e.g. the trade is instantly
    # declined and closed), no further message would ever arrive to end the turn,
    # leaving the bot silently stalled forever on what looks like a frozen game.
    if state.player_trade_sent_at is not None:
        remaining = TRADE_RESPONSE_WAIT_SECONDS - (time.time() - state.player_trade_sent_at)
        if remaining > 0:
            time.sleep(remaining)

    return {"action": Action.END_TURN, "payload": True, "sequence": state.next_sequence()}

def decide(msg_type, msg_payload, state):
    if msg_type == MsgType.INITIALIZE_MAP:
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

        income = board_income(state)

        # tier 1: always keep at least 1 of a resource we don't produce much of — we have
        # no reliable way to get more of it from dice rolls, so losing our only copy is far
        # costlier than losing a duplicate of something the board hands us regularly
        protect = {r: (1 if income.get(r, 0) < HIGH_PRODUCTION_PIPS else 0) for r in counts}

        # tier 2: on top of the rarity floor, protect whatever amount active build goals
        # actually need (e.g. a city's 3 ore outweighs the rarity floor's flat 1)
        goals = []
        if valid_settlement_spots(state):
            goals.append(SETTLEMENT_COST)
        if valid_city_spots(state):
            goals.append(CITY_COST)
        if valid_road_spots(state):
            goals.append(ROAD_COST)
        for cost in goals:
            for r, amt in cost.items():
                protect[r] = max(protect.get(r, 0), amt)

        # discard order: least-produced (rarest) resources last — discard whatever we
        # produce the most of first; ties broken by the biggest stack, then a static
        # fallback ordering (lumber=1 first, ore=5 last)
        least_valuable = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
        def discard_order(c):
            return (-income.get(c, 0), -counts[c], least_valuable.get(c, 99))

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