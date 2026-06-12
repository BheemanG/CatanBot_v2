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
            self.resources[c] -= 1
    
    def place_settlement(self, v_id):
        self.settlements.append(v_id)
    
    def upgrade_city(self, v_id):
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
        self.out_sequence = 1
        self.players = {}
        self.my_color = None
    
    def my_player(self):
        return self.players.get(self.my_color)

    def next_sequence(self):
        self.out_sequence += 1
        return self.out_sequence

    def parse_board(self, msg_payload):
        if msg_payload.get('gameSettings').get('id') != self.id:
            self.my_color = msg_payload.get('playerColor')

            #fills self.players
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
            print('[STATE] Hexes Parsed')
            return
        print('[STATE] Not Updated')

    def update(self, diff):
        for v_id in diff.get('mapState').get('tileCornerStates'):
            if (v_id.get('buildingType' == 1)):
                self.players[v_id.get('owner')].place_settlement(v_id)
            elif (v_id.get('buildingType' == 2)):
                self.players[v_id.get('owner')].upgrade_city(v_id)
        for e_id in diff.get('mapState').get('tileEdgeStates'):
            self.players[e_id.get('owner')].place_road(e_id)
        for p_id in diff.get('mapState').get('playerStates'):
            self.players[p_id].update_vp(p_id.get('victoryPointsState').get('0'))
        