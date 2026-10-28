#!/usr/bin/env python3
"""Real mobile DOM fixture, synthetic RFB only; never opens a user browser."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_site_owner_page import CLIENT, BROWSER_CSP, private_browser

RFB = '''export default class RFB {
 constructor(display) {
  this.events = {}; this.focusOnClick = true;
  const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 400; canvas.tabIndex = 0;
  canvas.style.width = '100%'; canvas.style.height = 'auto';
  display.append(canvas); window.syntheticKeys = []; window.syntheticPointers = 0; window.syntheticViewer = this;
  canvas.addEventListener('mousedown', () => {window.syntheticPointers++; if (this.focusOnClick) canvas.focus();});
  setTimeout(() => this.events.connect?.(), 0);
 }
 addEventListener(name, callback) {this.events[name] = callback;}
 sendKey(keysym, code) {window.syntheticKeys.push([keysym, code]);}
 disconnect() {this.events.disconnect?.();}
}'''


@unittest.skipUnless(os.environ.get('HOMECOMPUTE_REAL_PLAYWRIGHT') == '1',
                     'run in isolated GUI image with synthetic RFB and no credentials')
class MobileBrowserTest(unittest.TestCase):
    def test_touch_keyboard_focus_pointer_delivery_and_private_input(self):
        from playwright.sync_api import sync_playwright
        item = {'request_id': '00000000-0000-4000-8000-000000000001', 'account': 'hotels.com',
                'request_digest': 'a' * 64, 'expires_at': int(time.time()) + 60}
        page_source = private_browser(item, {'status': 'awaiting_owner'}, {})
        symbols = Path('/usr/share/novnc/core/input/keysymdef.js').read_text()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                body, kind = (json.dumps({'status': 'awaiting_owner'}), 'application/json') if self.path.endswith('/state') else (
                    (RFB, 'text/javascript') if self.path.endswith('/vendor/core/rfb.js') else (
                    (symbols, 'text/javascript') if self.path.endswith('/vendor/core/input/keysymdef.js') else (
                    (CLIENT, 'text/javascript') if self.path.endswith('/client.js') else (page_source, 'text/html'))))
                raw = body.encode(); self.send_response(200)
                self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(raw)))
                self.send_header('Content-Security-Policy', BROWSER_CSP)
                self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True, chromium_sandbox=True)
                try:
                    for mobile in [False, True]:
                        with self.subTest(mobile=mobile):
                            context = browser.new_context(is_mobile=mobile, has_touch=mobile,
                                viewport={'width': 390 if mobile else 1280, 'height': 844})
                            try:
                                page = context.new_page(); requests = []
                                page.on('request', lambda request: requests.append((request.method, request.post_data)))
                                page.set_default_timeout(5000)
                                page.goto('http://127.0.0.1:' + str(server.server_port) + '/browser/fixture/')
                                toggle = page.get_by_role('button', name='Show keyboard', exact=True)
                                page.wait_for_function("!document.getElementById('private-keyboard-toggle').disabled")
                                toggle.tap() if mobile else toggle.click()
                                self.assertEqual(page.evaluate('document.activeElement.id'), 'private-keyboard')
                                canvas = page.locator('canvas')
                                canvas.tap(position={'x': 10, 'y': 10}) if mobile else canvas.click(position={'x': 10, 'y': 10})
                                self.assertEqual(page.evaluate('window.syntheticPointers'), 1,
                                    'Keeping software keyboard open must preserve remote mouse/touch compatibility events')
                                self.assertEqual(page.evaluate('document.activeElement.id'), 'private-keyboard')
                                page.keyboard.insert_text('a😀_')
                                self.assertEqual(page.evaluate('window.syntheticKeys'), [[97, None], [0x0101f600, None], [95, None]])
                                page.keyboard.press('Enter')
                                self.assertEqual(page.evaluate('window.syntheticKeys.slice(-1)'), [[0xff0d, 'Enter']])
                                self.assertEqual(page.evaluate('window.syntheticKeys.length'), 4, 'Enter must be sent once')
                                page.keyboard.press('Backspace')
                                self.assertEqual(page.evaluate('window.syntheticKeys.slice(-1)'), [[0xff08, 'Backspace']])
                                self.assertEqual(page.evaluate('window.syntheticKeys.length'), 5)
                                self.assertEqual(page.locator('#private-keyboard').input_value(), '_' * 32)
                                page.get_by_role('button', name='Hide keyboard', exact=True).click()
                                self.assertEqual(page.locator('#private-keyboard').input_value(), '')
                                page.evaluate("document.getElementById('private-keyboard').value = 'synthetic-ephemeral'; window.syntheticViewer.events.disconnect()")
                                self.assertEqual(page.locator('#private-keyboard').input_value(), '')
                                self.assertTrue(page.locator('#private-keyboard-toggle').is_disabled())
                                self.assertTrue(all(method == 'GET' and body is None for method, body in requests))
                            finally: context.close()
                finally: browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
