import time
import uuid

from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from src.game.state import GameState
from src.game.strategy import decide

app = Flask(__name__)
CORS(app)

ALLOWED_ORIGIN = 'https://colonist.io'
STALE_THRESHOLD_SECONDS = 10

state = GameState()
server_boot_id = uuid.uuid4().hex
last_incoming_at = None

def check_origin():
    origin = request.headers.get('Origin', '')
    if origin != ALLOWED_ORIGIN and origin != '':
        print(f'[BLOCKED] Request from unauthorized origin: {origin}')
        abort(403)

def no_action():
    return jsonify({"action": None, "payload": None, "sequence": None})

@app.after_request
def add_headers(response):
    response.headers['Access-Control-Allow-Private-Network'] = 'true'
    response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGIN
    response.headers['Access-Control-Allow-Headers'] = '*'
    return response

@app.route('/incoming', methods=['POST'])
def handle_incoming():
    check_origin()
    global last_incoming_at
    last_incoming_at = time.time()
    data = request.json

    msg_id = data.get('id')
    if msg_id != "130":
        print(f'msg_id = {msg_id} - No Action')
        return no_action()
    
    msg_type = data.get('data', {}).get('type')
    msg_payload = data.get('data', {}).get('payload')

    print(f'[WS IN] type: {msg_type}')

    action = decide(msg_type, msg_payload, state)
    if action is None:
        return no_action()
    return jsonify(action)

@app.route('/state/ui', methods=['GET'])
def handle_state_ui():
    return '''<!doctype html>
<html><head><title>Catan State</title>
<style>
  body { background: #1e1e1e; color: #d4d4d4; font-family: monospace; padding: 16px; margin: 0; }
  pre  { font-size: 13px; line-height: 1.5; }
</style>
</head><body>
<pre id="s">Loading...</pre>
<script>
function refresh() {
    fetch('/state').then(r => r.json()).then(d => {
        document.getElementById('s').textContent = JSON.stringify(d, null, 2);
    });
}
refresh();
setInterval(refresh, 1000);
</script>
</body></html>'''

@app.route('/state/reload_check', methods=['GET'])
def handle_reload_check():
    stale = last_incoming_at is not None and (time.time() - last_incoming_at) > STALE_THRESHOLD_SECONDS
    return jsonify({"boot_id": server_boot_id, "stale": stale})

@app.route('/state', methods=['GET'])
def handle_state():
    players_out = {}
    for color, p in state.players.items():
        players_out[color] = {
            'resources': p.resources,
            'settlements': p.settlements,
            'cities': p.cities,
            'roads': p.roads,
            'vp': p.vp,
            'longest_road': p.longest_road,
            'army_size': p.army_size,
        }
    return jsonify({
        'game_id':      state.id,
        'my_color':     state.my_color,
        'current_turn': state.current_turn,
        'turn_state':   state.turn_state,
        'players':      players_out,
        'robber_hex':   state.robber_hex,
        'my_port_ratios': state.port_ratios(),
        'longest_road_holder': state.longest_road_holder(),
        'largest_army_holder': state.largest_army_holder(),
    })

@app.route('/outgoing', methods=['POST'])
def handle_outgoing():
    check_origin()
    data = request.json
    seq = data.get('sequence')
    # action 67 is sent by the browser itself (not by us) whenever colonist's client
    # detects a desync and resets its own sequence counter — its value is authoritative
    # and must be adopted even if it's lower than what we've been tracking, unlike other
    # outgoing messages where a lower sequence just means an out-of-order/stale POST
    if seq is not None and (data.get('action') == 67 or seq > state.out_sequence):
        state.out_sequence = seq
        print(f'[OUT SEQ UPDATED] {seq}')
    return jsonify({}), 200

if __name__ == '__main__':
    print('[CATAN] Server started on port 5000')
    app.run(port=5000)