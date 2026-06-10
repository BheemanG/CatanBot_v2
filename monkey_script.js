// ==UserScript==
// @name         Catan WS Logger
// @namespace    http://tampermonkey.net/
// @version      2.1
// @match        *://*.colonist.io/*
// @run-at       document-start
// @require      https://cdnjs.cloudflare.com/ajax/libs/msgpack-lite/0.1.26/msgpack.min.js
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      localhost
// ==/UserScript==

(function() {
    console.log('[CATAN] Script loaded');

    const OriginalWebSocket = unsafeWindow.WebSocket;

    function PatchedWebSocket(url, protocols) {
        const ws = protocols
        ? new OriginalWebSocket(url, protocols)
        : new OriginalWebSocket(url);

        ws.addEventListener('message', async (event) => {
            console.log('[CATAN] Raw message received');
            try {
                let uint8;
                if (event.data instanceof Blob) {
                    const buf = await event.data.arrayBuffer();
                    uint8 = new Uint8Array(buf);
                } else if (event.data instanceof ArrayBuffer) {
                    uint8 = new Uint8Array(event.data);
                }
                const decoded = msgpack.decode(uint8);

                if (decoded?.id === '136') return;

                console.log('[CATAN] Sending to Python:', JSON.stringify(decoded));
                GM_xmlhttpRequest({
                    method: 'POST',
                    url: 'http://localhost:5000/message',
                    headers: { 'Content-Type': 'application/json' },
                    data: JSON.stringify(decoded),
                    onload: function(response) {
                        const result = JSON.parse(response.responseText);
                        if (result.action !== null && result.action !== undefined) {
                            catanSend(result.action, result.payload, result.sequence);
                        }
                    },
                    onerror: function(e) {
                        console.log('[CATAN] Python server not reachable', e);
                    }
                });

            } catch(e) {
                console.log('[WS IN RAW]', event.data, e);
            }
        });

        const originalSend = ws.send.bind(ws);
        ws.send = function(data) {
            if (data instanceof ArrayBuffer || data instanceof Uint8Array) {
                const bytes = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
                const msgType = bytes[0];
                if (msgType === 4) return originalSend(data);
                try {
                    const payload = msgpack.decode(bytes.slice(9));
                    console.log('[WS OUT]', JSON.stringify(payload, null, 2));
                    // forward outgoing to Python too
                    GM_xmlhttpRequest({
                        method: 'POST',
                        url: 'http://localhost:5000/outgoing',
                        headers: { 'Content-Type': 'application/json' },
                        data: JSON.stringify(payload),
                        onerror: function(e) {}
                    });
                } catch(e) {
                    console.log('[WS OUT BYTES]', Array.from(bytes));
                }
            }
            return originalSend(data);
        };

        unsafeWindow._ws = ws;
        return ws;
    }

    PatchedWebSocket.prototype = OriginalWebSocket.prototype;
    PatchedWebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    PatchedWebSocket.OPEN = OriginalWebSocket.OPEN;
    PatchedWebSocket.CLOSING = OriginalWebSocket.CLOSING;
    PatchedWebSocket.CLOSED = OriginalWebSocket.CLOSED;

    unsafeWindow.WebSocket = PatchedWebSocket;

    unsafeWindow.catanSend = function(action, payload, sequence) {
        const encoded = msgpack.encode({
            action,
            payload,
            sequence
        });
        const header = new Uint8Array([3, 1, 6, 48, 49, 49, 48, 49, 55]);
        const full = new Uint8Array(header.length + encoded.length);
        full.set(header);
        full.set(encoded, header.length);
        unsafeWindow._ws.send(full.buffer);
    };

})();