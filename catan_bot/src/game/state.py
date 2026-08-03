from src.constants.board import VERTEX_TO_HEXES, PORT_TYPES
from src.game.graph import port_to_vertex

class Hex:
    def __init__(self, hex_id, resource, dice):
        self.hex_id   = hex_id
        self.resource = resource
        self.dice     = dice

class Player:
    def __init__(self):
        self.resources = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        self.settlements = [] #vertex ids
        self.cities = [] #vertex ids
        self.roads = [] #edge ids
        self.vp = 0
        self.dev_cards = [] #dev card ids
        self.longest_road = 0
        self.army_size = 0 # knights played, from mechanicDevelopmentCardsState.developmentCardsUsed

    def gain_resources(self, cards):
        for c in cards:
            self.resources[c] += 1

    def lose_resources(self, cards):
        for c in cards:
            self.resources[c] = max(0, self.resources[c] - 1)
    
    def place_settlement(self, v_id):
        self.settlements.append(v_id)
    
    def upgrade_city(self, v_id):
        if v_id in self.settlements:
            self.settlements.remove(v_id)
        self.cities.append(v_id)
    
    def place_road(self, e_id):
        self.roads.append(e_id)
    
    def update_vp(self, new_vp):
        self.vp = new_vp
        

class GameState:
    def __init__(self):
        self.id = ""
        self.hexes        = [None] * 19 #contains 19 Hex objects from parse_board
        self.vertices     = [None] * 54 #contains id of occupying player, 0 if unoccupied
        self.my_color     = None
        self.current_turn = None
        self.turn_state   = None
        self.edges        = [None] * 72
        self.robber_hex       = None
        self.ports            = {}    # vertex_id -> {"resource": rsc_id or None, "ratio": 2 or 3}
        self.active_offers    = {}    # trade_id -> offer dict
        self.responded_offers = set() # offer IDs we've already sent action 50 for
        self.finalized_offers = set() # offer IDs (created by us) we've already sent action 51 for
        self.rejected_trade_pairs = set() # (give, want) player-trade pairs closed without acceptance this turn
        self.player_trade_sent_at = None # time.time() of our most recent player-trade offer this turn — hold off ending the turn a few seconds to give opponents a chance to respond
        self.pending_player_trade = False # True from the instant we send a player trade until its id is confirmed in active_offers — closes the race where a slow echo lets try_player_trade fire again and send a duplicate before the first is even acknowledged
        self.needs_roll       = False # True after turnState=1 transition; cleared on roll
        self.dev_card_played  = False # True after playing a dev card this turn
        self.robber_pending   = False # True after type 33 handled; cleared after steal or if no opponents adjacent
        self.road_building_pending = 0 # roads left to place from an active Road Building dev card
        self.turn_start_dev_cards = [] # snapshot of dev_cards owned as of the start of our current turn (playable set)
        self.out_sequence     = 1
        self.players = {0: self._make_bank()}
    
    def _make_bank(self):
        bank = Player()
        bank.resources = {1: 19, 2: 19, 3: 19, 4: 19, 5: 19}
        return bank

    def my_player(self):
        return self.players.get(self.my_color)

    def _title_holder(self, attr, minimum):
        """Color of the player currently holding a count-based bonus (Longest Road /
        Largest Army) — None if no one has met the minimum, or if tied for the max."""
        counts = {color: getattr(p, attr) for color, p in self.players.items() if color != 0}
        if not counts:
            return None
        best = max(counts.values())
        if best < minimum or sum(1 for v in counts.values() if v == best) > 1:
            return None
        return max(counts, key=lambda c: counts[c])

    def longest_road_holder(self):
        return self._title_holder('longest_road', 5)

    def largest_army_holder(self):
        return self._title_holder('army_size', 3)

    def port_ratios(self):
        """Best trade ratio per resource (1-5) reachable from our settlements/cities.
        Defaults to 4 (no port) when we hold no matching or generic port."""
        ratios = {r: 4 for r in range(1, 6)}
        player = self.my_player()
        if not player:
            return ratios
        my_vertices = set(player.settlements) | set(player.cities)
        generic_ratio = min(
            (p['ratio'] for v, p in self.ports.items() if v in my_vertices and p['resource'] is None),
            default=None,
        )
        for r in range(1, 6):
            specific_ratio = min(
                (p['ratio'] for v, p in self.ports.items() if v in my_vertices and p['resource'] == r),
                default=None,
            )
            best = min(x for x in (specific_ratio, generic_ratio, 4) if x is not None)
            ratios[r] = best
        return ratios

    def next_sequence(self):
        self.out_sequence += 1
        return self.out_sequence

    def parse_board(self, msg_payload):
        self.id           = msg_payload.get('gameSettings', {}).get('id')
        self.my_color     = msg_payload.get('playerColor')
        self.vertices     = [None] * 54
        self.edges        = [None] * 72
        self.current_turn = None
        self.turn_state   = None
        self.ports             = {}
        self.active_offers    = {}
        self.responded_offers = set()
        self.finalized_offers = set()
        self.rejected_trade_pairs = set()
        self.player_trade_sent_at = None
        self.pending_player_trade = False
        self.needs_roll       = False
        self.dev_card_played  = False
        self.robber_pending   = False
        self.road_building_pending = 0
        self.turn_start_dev_cards = []

        self.players = {0: self._make_bank()}
        for p in msg_payload.get('playerUserStates'):
            self.players[p.get('selectedColor')] = Player()

        map_state = msg_payload.get('gameState', {}).get('mapState', {})

        tiles = map_state.get('tileHexStates', {})
        for hex_id, hex_data in tiles.items():
            self.hexes[int(hex_id)] = Hex(
                hex_id=int(hex_id),
                resource=hex_data.get('type'),
                dice=hex_data.get('diceNumber')
            )
        self.robber_hex = next(
            int(hid) for hid, h in tiles.items() if h.get('type') == 0
        )

        for port_id, port_data in map_state.get('portEdgeStates', {}).items():
            resource, ratio = PORT_TYPES[port_data['type']]
            for v_id in port_to_vertex[int(port_id)]:
                self.ports[v_id] = {'resource': resource, 'ratio': ratio}

        for v_id, v_data in map_state.get('tileCornerStates', {}).items():
            v_id = int(v_id)
            owner = v_data.get('owner')
            if owner is None:
                continue
            building = v_data.get('buildingType')
            if building == 1:
                self.vertices[v_id] = owner
                self.players[owner].place_settlement(v_id)
            elif building == 2:
                self.vertices[v_id] = owner
                self.players[owner].upgrade_city(v_id)

        for e_id, e_data in map_state.get('tileEdgeStates', {}).items():
            owner = e_data.get('owner')
            if owner is None:
                continue
            e_id = int(e_id)
            self.edges[e_id] = owner
            self.players[owner].place_road(e_id)

        current = msg_payload.get('gameState', {}).get('currentState', {})
        if 'currentTurnPlayerColor' in current:
            self.current_turn = current['currentTurnPlayerColor']
        if 'turnState' in current:
            self.turn_state = current['turnState']

        for offer_id, offer in msg_payload.get('gameState', {}).get('tradeState', {}).get('activeOffers', {}).items():
            self.active_offers[offer_id] = offer

        game_state = msg_payload.get('gameState', {})
        for p_id, lr_data in game_state.get('mechanicLongestRoadState', {}).items():
            longest_road = lr_data.get('longestRoad')
            if longest_road is not None and int(p_id) in self.players:
                self.players[int(p_id)].longest_road = longest_road

        for p_id, p_dev in game_state.get('mechanicDevelopmentCardsState', {}).get('players', {}).items():
            used = p_dev.get('developmentCardsUsed')
            if used is not None and int(p_id) in self.players:
                self.players[int(p_id)].army_size = used.count(11)

        print('[STATE] Board Initialized')

    def update(self, diff):
        if diff is None:
            return

        for rsc_id, count in diff.get('bankState', {}).get('resourceCards', {}).items():
            self.players[0].resources[int(rsc_id)] = count

        map_state = diff.get('mapState', {})
        for v_id, v_data in map_state.get('tileCornerStates', {}).items():
            v_id = int(v_id)
            owner = v_data.get('owner') or self.vertices[v_id]
            if v_data.get('buildingType') == 1:
                self.vertices[v_id] = owner
                self.players[owner].place_settlement(v_id)
            elif v_data.get('buildingType') == 2:
                self.players[owner].upgrade_city(v_id)
        for e_id, e_data in map_state.get('tileEdgeStates', {}).items():
            owner = e_data.get('owner')
            e_id = int(e_id)
            self.edges[e_id] = owner
            self.players[owner].place_road(e_id)

        for p_id, p_data in diff.get('playerStates', {}).items():
            player = self.players[int(p_id)]
            vp = p_data.get('victoryPointsState', {}).get('0')
            if vp is not None:
                player.update_vp(vp)
            cards = p_data.get('resourceCards', {}).get('cards')
            if cards is not None and int(p_id) == self.my_color:
                player.resources = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
                for c in cards:
                    if c > 0:
                        player.resources[c] += 1

        dev_state = diff.get('mechanicDevelopmentCardsState', {})
        my_dev = dev_state.get('players', {}).get(str(self.my_color), {})
        dev_cards = my_dev.get('developmentCards', {}).get('cards')
        if dev_cards is not None:
            self.my_player().dev_cards = list(dev_cards)

        # developmentCardsUsed (unlike the hand itself) is public for every player —
        # use it to track each player's knight count for the Largest Army bonus
        for p_id, p_dev in dev_state.get('players', {}).items():
            used = p_dev.get('developmentCardsUsed')
            if used is not None and int(p_id) in self.players:
                self.players[int(p_id)].army_size = used.count(11)

        robber = diff.get('mechanicRobberState', {})
        if 'locationTileIndex' in robber:
            self.robber_hex = robber['locationTileIndex']

        for p_id, lr_data in diff.get('mechanicLongestRoadState', {}).items():
            longest_road = lr_data.get('longestRoad')
            if longest_road is not None and int(p_id) in self.players:
                self.players[int(p_id)].longest_road = longest_road

        # null value = offer closed/cancelled; non-null = a (possibly partial) update —
        # later diffs often carry only a single player's playerResponses entry, so this
        # must merge into the stored offer rather than replace it wholesale, or a lone
        # response update would wipe out creator/offeredResources/wantedResources and
        # every other player's previously recorded response
        for offer_id, offer in diff.get('tradeState', {}).get('activeOffers', {}).items():
            if offer is None:
                was_finalized = offer_id in self.finalized_offers
                closed = self.active_offers.pop(offer_id, None)
                self.responded_offers.discard(offer_id)
                self.finalized_offers.discard(offer_id)
                # closed without ever being finalized == nobody accepted (or we never
                # got to finalize before it expired) — remember the pair so
                # try_player_trade doesn't immediately re-propose the same ask.
                # (no isBankTrade check here: colonist's activeOffers echoes never carry
                # that field — bank trades resolve instantly via type 43 and never enter
                # activeOffers at all, so every entry here is already a player trade)
                if (
                    closed
                    and not was_finalized
                    and closed.get('creator') == self.my_color
                ):
                    offered = closed.get('offeredResources') or []
                    wanted = closed.get('wantedResources') or []
                    if offered and wanted:
                        self.rejected_trade_pairs.add((offered[0], wanted[0]))
            else:
                is_new = offer_id not in self.active_offers
                existing = self.active_offers.setdefault(offer_id, {})
                if is_new and offer.get('creator') == self.my_color:
                    # confirmation that our pending send actually landed and got an id —
                    # the active_offers-based outstanding-offer guard in try_player_trade
                    # takes over from here, so the local pending flag is no longer needed
                    self.pending_player_trade = False
                for key, value in offer.items():
                    if key in ('playerResponses', 'playersCreatingCounterOffer') and isinstance(value, dict):
                        existing.setdefault(key, {}).update(value)
                    else:
                        existing[key] = value

        current = diff.get('currentState', {})
        if 'currentTurnPlayerColor' in current:
            if current['currentTurnPlayerColor'] != self.current_turn:
                # turn changed — reset per-turn flags
                self.needs_roll      = False
                self.dev_card_played = False
                self.road_building_pending = 0
                self.rejected_trade_pairs = set()
                self.player_trade_sent_at = None
                self.pending_player_trade = False
            self.current_turn = current['currentTurnPlayerColor']
        if 'turnState' in current:
            self.turn_state = current['turnState']