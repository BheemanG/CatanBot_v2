from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from src.game.state import GameState
from src.game.strategy import decide

app = Flask(__name__)
CORS(app)

ALLOWED_ORIGIN = 'https://colonist.io'

state = GameState()

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
    print(f'[WS OUT DRAFT] {action}')
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
    if seq is not None:
        state.out_sequence = seq
        print(f'[OUT SEQ UPDATED] {seq}')
    return jsonify({}), 200

if __name__ == '__main__':
    print('[CATAN] Server started on port 5000')
    app.run(port=5000)