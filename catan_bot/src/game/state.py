from src.constants.board import VERTEX_TO_HEXES

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
        self.action_state = None
        self.turn_state   = None
        self.dice_thrown  = False
        self.edges        = [None] * 72
        self.robber_hex    = None
        self.active_offers = {}  # trade_id -> offer dict
        self.out_sequence  = 1
        self.players = {0: self._make_bank()}
    
    def _make_bank(self):
        bank = Player()
        bank.resources = {1: 19, 2: 19, 3: 19, 4: 19, 5: 19}
        return bank

    def my_player(self):
        return self.players.get(self.my_color)

    def next_sequence(self):
        self.out_sequence += 1
        return self.out_sequence

    def parse_board(self, msg_payload):
        game_id = msg_payload.get('gameSettings', {}).get('id')
        if game_id != self.id:
            self.id = game_id
            self.my_color = msg_payload.get('playerColor')

            #fills self.players
            self.players = {0: self._make_bank()}
            for p in msg_payload.get('playerUserStates'):
                self.players[p.get('selectedColor')] = Player()

            #fills self.hexes
            tiles = msg_payload.get('gameState', {}).get('mapState', {}).get('tileHexStates', {})
            for hex_id, hex_data in tiles.items():
                self.hexes[int(hex_id)] = Hex(
                    hex_id=int(hex_id),
                    resource=hex_data.get('type'),
                    dice=hex_data.get('diceNumber')
                )
            self.robber_hex = next(
                int(hid) for hid, h in tiles.items() if h.get('type') == 0
            )
            print('[STATE] Hexes Parsed')
            return
        print('[STATE] Not Updated')

    def update(self, diff):
        if diff is None:
            return

        dice = diff.get('diceState', {})
        if 'diceThrown' in dice:
            self.dice_thrown = dice['diceThrown']

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

        robber = diff.get('mechanicRobberState', {})
        if 'locationTileIndex' in robber:
            self.robber_hex = robber['locationTileIndex']

        trade_state = diff.get('tradeState', {})
        for offer_id, offer in trade_state.get('activeOffers', {}).items():
            self.active_offers[offer_id] = offer
        for offer_id in trade_state.get('closedOffers', {}):
            self.active_offers.pop(offer_id, None)

        current = diff.get('currentState', {})
        if 'currentTurnPlayerColor' in current:
            self.current_turn = current['currentTurnPlayerColor']
        if 'actionState' in current:
            self.action_state = current['actionState']
        if 'turnState' in current:
            self.turn_state = current['turnState']