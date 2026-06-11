// ==UserScript==
// @name         Catan Bot
// @namespace    http://tampermonkey.net/
// @version      3.2
// @match        *://*.colonist.io/*
// @run-at       document-start
// @require      https://cdnjs.cloudflare.com/ajax/libs/msgpack-lite/0.1.26/msgpack.min.js
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      localhost
// ==/UserScript==

(function() {
    console.log('[CATAN] Script loaded');

    function isInGame() {
        return window.location.hash.length > 1;
    }

    let capturedHeader = null;

    const OriginalWebSocket = unsafeWindow.WebSocket;

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

                // extract serverId from first game message to build header
                if (decoded?.data?.type === 1 && decoded?.data?.payload?.serverId) {
                    const serverId = decoded.data.payload.serverId;
                    const idBytes = Array.from(serverId).map(c => c.charCodeAt(0));
                    capturedHeader = new Uint8Array([3, 1, 6, ...idBytes]);
                    console.log('[CATAN] Header captured from serverId:', serverId, Array.from(capturedHeader));
                }

                // only process id 130 messages
                if (decoded?.id !== '130') return;

                if (!isInGame()) return;

                // temporarily filter type 91
                if (decoded?.data?.type !== 91) return;

                console.log('[WS IN]', JSON.stringify(decoded, null, 2));

                GM_xmlhttpRequest({
                    method: 'POST',
                    url: 'http://localhost:5000/incoming',
                    headers: { 'Content-Type': 'application/json' },
                    data: JSON.stringify(decoded),
                    onload: function(response) {
                        const result = JSON.parse(response.responseText);
                        if (result.action !== null && result.action !== undefined) {
                            unsafeWindow.catanSend(result.action, result.payload, result.sequence);
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
            if (typeof data === 'string') return originalSend(data);

            if (data instanceof ArrayBuffer || data instanceof Uint8Array) {
                const bytes = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
                if (bytes[0] === 4) return originalSend(data);

                try {
                    const payload = msgpack.decode(bytes.slice(9));

                    // ignore if decoded result is just a string
                    if (typeof payload === 'string') return originalSend(data);

                    console.log('[WS OUT]', JSON.stringify(payload, null, 2));

                    if (!isInGame()) return originalSend(data);

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
        unsafeWindow.catanSend = function(action, payload, sequence) {
            if (!capturedHeader) {
                console.log('[CATAN] No header yet — waiting for game to start');
                return;
            }
            const encoded = msgpack.encode({ action, payload, sequence });
            const full = new Uint8Array(capturedHeader.length + encoded.length);
            full.set(capturedHeader);
            full.set(encoded, capturedHeader.length);
            unsafeWindow._ws.send(full.buffer);
        };

        return ws;
    }

    PatchedWebSocket.prototype = OriginalWebSocket.prototype;
    PatchedWebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    PatchedWebSocket.OPEN = OriginalWebSocket.OPEN;
    PatchedWebSocket.CLOSING = OriginalWebSocket.CLOSING;
    PatchedWebSocket.CLOSED = OriginalWebSocket.CLOSED;

    unsafeWindow.WebSocket = PatchedWebSocket;

})();