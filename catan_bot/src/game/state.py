from src.constants.board import VERTEX_TO_HEXES

class Hex:
    def __init__(self, hex_id, resource, dice):
        self.hex_id   = hex_id
        self.resource = resource
        self.dice     = dice

class GameState:
    def __init__(self):
        self.hexes        = [None] * 19
        self.my_color     = None
        self.current_turn = None
        self.out_sequence = 1

    def next_sequence(self):
        self.out_sequence += 1
        return self.out_sequence

    def parse_board(self, msg_payload):
        tiles = msg_payload.get('gameState', {}).get('mapState', {}).get('tileHexStates', {})
        for hex_id, hex_data in tiles.items():
            self.hexes[int(hex_id)] = Hex(
                hex_id=int(hex_id),
                resource=hex_data.get('type'),
                dice=hex_data.get('diceNumber')
            )
        print('[STATE] hexes parsed')