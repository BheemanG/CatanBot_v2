// ==UserScript==
// @name         Catan WS Logger
// @namespace    http://tampermonkey.net/
// @version      2.5
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

        if (decoded?.id === '136') return;
        if (!isInGame()) return;

        console.log('[WS IN]', JSON.stringify(decoded, null, 2));

        GM_xmlhttpRequest({
          method: 'POST',
          url: 'http://localhost:5000/incoming',
          headers: { 'Content-Type': 'application/json' },
          data: JSON.stringify(decoded),
          onload: function(response) {
            const result = JSON.parse(response.responseText);
            if (result.actions && result.actions.length > 0) {
              result.actions.forEach((item, i) => {
                setTimeout(() => {
                  unsafeWindow.catanSend(item.action, item.payload, item.sequence);
                }, i * 500);
              });
            } else if (result.action !== null && result.action !== undefined) {
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
      if (data instanceof ArrayBuffer || data instanceof Uint8Array) {
        const bytes = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
        const msgType = bytes[0];
        if (msgType === 4) return originalSend(data);

        // capture real header from every outgoing message
        capturedHeader = bytes.slice(0, 9);
        console.log('[WS HEADER CAPTURED]', Array.from(capturedHeader));

        try {
          const payload = msgpack.decode(bytes.slice(9));
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
      } else {
        console.log('[WS OUT RAW]', typeof data, data);
      }
      return originalSend(data);
    };

    unsafeWindow._ws = ws;
    unsafeWindow.catanSend = function(action, payload, sequence) {
      if (!capturedHeader) {
        console.log('[CATAN] No header captured yet — make a manual move first');
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