from flask import Flask, request, jsonify, abort
from flask_cors import CORS
import time
import sys

app = Flask(__name__)
CORS(app)

ALLOWED_ORIGIN = 'https://colonist.io'

out_sequence = 1

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

    #placement settlements
    if msg_type == 30:
#         return jsonify({
# #             "actions": [
# #                 {"action": 66, "payload": 28, "sequence": next_out_sequence()},
# #                 {"action": 66, "payload": , "sequence": next_out_sequence()},
# #                 {"action": 15, "payload": 28, "sequence": next_out_sequence()},
# #             ]
# # })
        action = 15
        payload = 28
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