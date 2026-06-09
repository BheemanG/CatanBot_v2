// ==UserScript==
// @name         Catan WS Logger
// @namespace    http://tampermonkey.net/
// @version      1.7
// @match        *://*.colonist.io/*
// @run-at       document-start
// @require      https://cdnjs.cloudflare.com/ajax/libs/msgpack-lite/0.1.26/msgpack.min.js
// @grant        none
// ==/UserScript==

(function() {
    console.log('[CATAN] Script loaded');

    const OriginalWebSocket = window.WebSocket;

    function PatchedWebSocket(url, protocols) {
        const ws = protocols
        ? new OriginalWebSocket(url, protocols)
        : new OriginalWebSocket(url);

        ws.addEventListener('message', async (event) => {
            try {
                let uint8;
                if (event.data instanceof Blob) {
                    const buf = await event.data.arrayBuffer();
                    uint8 = new Uint8Array(buf);
                } else if (event.data instanceof ArrayBuffer) {
                    uint8 = new Uint8Array(event.data);
                }
                const decoded = msgpack.decode(uint8);
                // filter heartbeats
                if (decoded?.id === '136') return;
                console.log('[WS IN]', JSON.stringify(decoded, null, 2));
            } catch(e) {
                console.log('[WS IN RAW]', event.data, e);
            }
        });

        const originalSend = ws.send.bind(ws);
        ws.send = function(data) {
            if (data instanceof ArrayBuffer || data instanceof Uint8Array) {
                const bytes = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
                const msgType = bytes[0];
                // filter heartbeats
                if (msgType === 4) return originalSend(data);
                try {
                    const payload = msgpack.decode(bytes.slice(9));
                    console.log('[WS OUT]', JSON.stringify(payload, null, 2));
                } catch(e) {
                    console.log('[WS OUT BYTES]', Array.from(bytes));
                }
            } else {
                console.log('[WS OUT RAW]', typeof data, data);
            }
            return originalSend(data);
        };

        window._ws = ws;
        return ws;
    }

    PatchedWebSocket.prototype = OriginalWebSocket.prototype;
    PatchedWebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    PatchedWebSocket.OPEN = OriginalWebSocket.OPEN;
    PatchedWebSocket.CLOSING = OriginalWebSocket.CLOSING;
    PatchedWebSocket.CLOSED = OriginalWebSocket.CLOSED;

    window.WebSocket = PatchedWebSocket;
})();