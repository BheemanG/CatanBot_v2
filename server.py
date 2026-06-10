from flask import Flask, request, jsonify, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ALLOWED_ORIGIN = 'https://colonist.io'

def check_origin():
    origin = request.headers.get('Origin', '')
    if origin != ALLOWED_ORIGIN:
        print(f'[BLOCKED] Request from unauthorized origin: {origin}')
        abort(403)

@app.after_request
def add_private_network_header(response):
    response.headers['Access-Control-Allow-Private-Network'] = 'true'
    response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGIN
    response.headers['Access-Control-Allow-Headers'] = '*'
    return response

@app.route('/message', methods=['POST', 'OPTIONS'])
def handle_message():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    # temporarily skip origin check
    data = request.json
    print(f'[CATAN] Received: {data}')

    return jsonify({
        "action": None,
        "payload": None,
        "sequence": None
    })

out_sequence = 0

@app.route('/outgoing', methods=['POST', 'OPTIONS'])
def handle_outgoing():
    global out_sequence
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    data = request.json
    seq = data.get('sequence')
    if seq is not None:
        out_sequence = seq
    print(f'[WS OUT] action: {data.get("action")} sequence: {seq}')
    return jsonify({}), 200

def next_out_sequence():
    global out_sequence
    out_sequence += 1
    return out_sequence

if __name__ == '__main__':
    app.run(port=5000)