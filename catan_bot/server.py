from flask import Flask, request, jsonify, abort
from flask_cors import CORS
import time
import sys

app = Flask(__name__)
CORS(app)

ALLOWED_ORIGIN = 'https://colonist.io'

out_sequence = 1


class Hex:
    def __init__(self, hex_id, resource, dice):
        self.hex_id = hex_id
        self.resource = resource  # 0=desert, 1=lumber, 2=brick, 3=wool, 4=grain, 5=ore
        self.dice = dice          # 2-12, 0 for desert

board = [None] * 19

VERTEX_TO_HEXES = {
    0:  [0],
    1:  [0, 11],
    2:  [0, 11, 12],
    3:  [0, 1, 12],
    4:  [0, 1],
    5:  [0],
    6:  [1, 12, 13],
    7:  [1, 2, 13],
    8:  [1, 2],
    9:  [1],
    10: [2, 3, 13],
    11: [2, 3],
    12: [2],
    13: [2],
    14: [3, 13, 14],
    15: [3, 4, 14],
    16: [3, 4],
    17: [3],
    18: [4, 5, 14],
    19: [4, 5],
    20: [4],
    21: [4],
    22: [5, 14, 15],
    23: [5, 6, 15],
    24: [5, 6],
    25: [5],
    26: [6, 7, 15],
    27: [6, 7],
    28: [6],
    29: [6],
    30: [7, 8, 16],
    31: [7, 8],
    32: [7],
    33: [7, 15, 16],
    34: [8, 9],
    35: [8],
    36: [8],
    37: [8, 9, 16],
    38: [9, 10],
    39: [9],
    40: [9, 16, 17],
    41: [9, 10, 17],
    42: [10],
    43: [10],
    44: [10, 11, 17],
    45: [10, 11],
    46: [11],
    47: [11, 12, 17],
    48: [12, 17, 18],
    49: [12, 13, 18],
    50: [13, 14, 18],
    51: [14, 15, 18],
    52: [15, 16, 18],
    53: [16, 17, 18],
}

def parse_board(data):
    tile_hex_states = data.get('data', {}).get('payload', {}).get('gameState', {}).get('mapState', {}).get('tileHexStates', {})
    for hex_id, hex_data in tile_hex_states.items():
        board[int(hex_id)] = Hex(
            hex_id=int(hex_id),
            resource=hex_data.get('type'),
            dice=hex_data.get('diceNumber')
        )


def check_origin():
    origin = request.headers.get('Origin', '')
    # allow empty origin (GM_xmlhttpRequest) and colonist.io
    if origin != ALLOWED_ORIGIN and origin != '':
        print(f'[BLOCKED] Request from unauthorized origin: {origin}')
        abort(403)

@app.after_request
def add_headers(response):
    response.headers['Access-Control-Allow-Private-Network'] = 'true'
    response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGIN
    response.headers['Access-Control-Allow-Headers'] = '*'
    return response

@app.route('/incoming', methods=['POST', 'OPTIONS'])
def handle_incoming():
    check_origin()
    data = request.json
    seq_in = data.get('data', {}).get('sequence')
    msg_type = data.get('data', {}).get('type')

    print(f'[WS IN] type: {msg_type} seq: {seq_in}')

    action =  None
    payload = None
    seq_out = None

    msg_type = data.get('data', {}).get('type')

    #construct board
    if msg_type == 4:
        parse_board(data)

    #placement settlements
    else if msg_type == 30:
        action = 15
        payload = calculate_placement()
        seq_out = next_out_sequence()

    print(f'[WS DRAFT OUT] action: {action}, payload: {payload}, sequence: {seq_out}')
    
    return jsonify({
        "action": action,
        "payload": payload,
        "sequence": seq_out
    })

@app.route('/outgoing', methods=['POST', 'OPTIONS'])
def handle_outgoing():
    global out_sequence
    check_origin()
    data = request.json

    seq = data.get('sequence')
    if seq is not None:
        out_sequence = seq
        print(f'[OUT SEQ UPDATED] {out_sequence}')

    print(f'[WS OUT] action: {data.get("action")} seq: {seq}')
    return jsonify({}), 200

def next_out_sequence():
    global out_sequence
    out_sequence += 1
    return out_sequence

if __name__ == '__main__':
    print('[CATAN] Python server started on port 5000')
    app.run(port=5000)