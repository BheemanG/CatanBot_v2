// ==UserScript==
// @name         Catan Bot
// @namespace    http://tampermonkey.net/
// @version      3.3
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

    // Reload the page if the Flask server restarts (new boot_id) or if no
    // incoming game message has arrived in STALE_MS while we're in a game
    // (signals a hung/desynced connection). RELOAD_GRACE_MS delays the first
    // staleness check after each load so a fresh reconnect has time to
    // receive a message before we'd reload again.
    const RELOAD_GRACE_MS = 15000;
    const RELOAD_POLL_MS = 3000;
    let knownBootId = null;
    const pageLoadedAt = Date.now();

    function checkReload() {
        if (!isInGame()) return;
        GM_xmlhttpRequest({
            method: 'GET',
            url: 'http://localhost:5000/state/reload_check',
            timeout: 3000,
            onload: function(response) {
                let data;
                try {
                    data = JSON.parse(response.responseText);
                } catch (e) {
                    return;
                }
                if (knownBootId === null) {
                    knownBootId = data.boot_id;
                    return;
                }
                if (data.boot_id !== knownBootId) {
                    console.log('[CATAN] Server restarted, reloading page');
                    window.location.reload();
                    return;
                }
                if (data.stale && Date.now() - pageLoadedAt > RELOAD_GRACE_MS) {
                    console.log('[CATAN] No incoming messages recently, reloading page');
                    window.location.reload();
                }
            },
            onerror: function(e) {},
            ontimeout: function(e) {}
        });
    }

    setInterval(checkReload, RELOAD_POLL_MS);

    function postWithRetry(url, data, onload, attempt = 1, maxAttempts = 5, delayMs = 500, onGiveUp) {
        GM_xmlhttpRequest({
            method: 'POST',
            url: url,
            headers: { 'Content-Type': 'application/json' },
            data: data,
            timeout: 3000,
            onload: onload,
            onerror: function(e) { retryOrGiveUp(e); },
            ontimeout: function(e) { retryOrGiveUp(e); }
        });

        function retryOrGiveUp(e) {
            if (attempt < maxAttempts) {
                console.log(`[CATAN] Python server not reachable (attempt ${attempt}/${maxAttempts}), retrying in ${delayMs}ms`, e);
                setTimeout(() => {
                    postWithRetry(url, data, onload, attempt + 1, maxAttempts, delayMs * 2, onGiveUp);
                }, delayMs);
            } else {
                console.log('[CATAN] Python server not reachable, giving up after', maxAttempts, 'attempts', e);
                if (onGiveUp) onGiveUp(e);
            }
        }
    }

    // Action 67 is colonist's own authoritative sequence-resync signal (see server.py's
    // /outgoing handler). Reporting it to Flask happens over an async POST that races
    // against the /incoming POSTs for whatever WS IN messages arrive right after a
    // reconnect (type 1/4/etc). If a response gets computed before that resync POST
    // lands, it stamps the wrong (stale) sequence, which just provokes another action 67
    // from colonist. This promise lets the WS message handler wait for the resync to be
    // durably applied server-side before forwarding any further message to /incoming.
    let pendingActionResync = null;

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

                console.log('[WS IN]', JSON.stringify(decoded, null, 2));

                if (pendingActionResync) {
                    await pendingActionResync;
                    pendingActionResync = null;
                }

                postWithRetry('http://localhost:5000/incoming', JSON.stringify(decoded), function(response) {
                    const result = JSON.parse(response.responseText);
                    if (result.action !== null && result.action !== undefined) {
                        unsafeWindow.catanSend(result.action, result.payload, result.sequence);
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

                    // action 67 is colonist's own desync-correction signal (see server.py's
                    // /outgoing handler) — it must always reach Flask even if isInGame()'s
                    // URL-hash heuristic reads false at that moment, unlike every other
                    // outgoing message which is fine to skip while not in a game
                    if (!isInGame() && payload?.action !== 67) return originalSend(data);

                    if (payload?.action === 67) {
                        pendingActionResync = new Promise((resolve) => {
                            postWithRetry('http://localhost:5000/outgoing', JSON.stringify(payload), () => resolve(), 1, 5, 500, () => resolve());
                        });
                    } else {
                        postWithRetry('http://localhost:5000/outgoing', JSON.stringify(payload), function(response) {});
                    }
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