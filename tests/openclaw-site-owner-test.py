#!/usr/bin/env python3
"""Synthetic owner UI/transport boundaries; never open an authenticated viewer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from email.message import Message
from html.parser import HTMLParser
import hashlib
import base64
import http.client
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_owner_bridge as bridge_module
from openclaw_site_owner_bridge import BrowserOwnerBridge, BrowserOwnerRefused, TOKEN_FILE
from openclaw_site_owner_page import CLIENT, BROWSER_CSP, private_browser

SPEC = importlib.util.spec_from_file_location('owner_access_service', ROOT / 'scripts/openclaw-access.py')
service = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(service)
TOKEN = 'synthetic-private-operator-key-0123456789abcdef'


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tick = 100.0
        self.grant = {'request_id': str(uuid4()), 'actor': 'openclaw', 'account': 'hotels.com',
                      'scope': 'search', 'status': 'active', 'approved_at': 90, 'expires_at': 600,
                      'request_digest': 'a' * 64, 'requested_minutes': 10, 'requested_at': 80,
                      'account_execution_available': False, 'transaction_execution_available': False}
        config = {'schema_version': 1, 'endpoint': 'http://127.0.0.1:18816', 'token_file': TOKEN_FILE}
        def private(_directory, name, _size):
            return TOKEN.encode() if name == 'browser-owner-token' else json.dumps(config).encode()
        with patch.object(bridge_module, 'read_private', private):
            self.bridge = BrowserOwnerBridge(Path('/unused/config.json'), lambda _ref: copy.deepcopy(self.grant),
                                            clock=lambda: self.tick, wall=lambda: self.tick)
        self.root_calls = []
        def upstream(ref, suffix, **kwargs):
            self.root_calls.append((ref, suffix, kwargs))
            if suffix.startswith('vendor/'):
                return b'export default class RFB {}'
            value = {'status': 'awaiting_owner', 'request_id': ref, 'account': self.grant['account']}
            if suffix == 'fill': value['filled_fields'] = 2
            return json.dumps(value).encode()
        self.bridge.request = upstream
        ledger = SimpleNamespace(status=lambda ref, **_kw: copy.deepcopy(self.grant),
                                 challenge=lambda _ref, action: 'fixture-' + action)
        self.server = service.make_server(ledger, 'operator', 0, {'openclaw': 's' * 40}, None,
            operator_auth=lambda headers: headers.get('Cookie') == 'MMAUTHTOKEN=synthetic-owner-cookie',
            browser=self.bridge)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True); thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.path = '/browser/' + self.grant['request_id'] + '/'

    def http(self, method='GET', path=None, fields=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        values = {'Cookie': 'MMAUTHTOKEN=synthetic-owner-cookie'}
        if method == 'POST':
            values.update({'Origin': self.server.operator_origin, 'Content-Type': 'application/x-www-form-urlencoded',
                           'Sec-Fetch-Site': 'same-origin'})
        values.update(headers or {})
        connection.request(method, path or self.path, urlencode(fields) if fields is not None else None, values)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def fields(self, action):
        return {'digest': self.grant['request_digest'], 'csrf': self.bridge.challenge(self.grant, action)}

    def test_owner_page_is_private_script_local_and_review_links(self):
        status, headers, raw = self.http()
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Security-Policy'], BROWSER_CSP)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn(b'Fill Hotels.com sign-in from Bitwarden', raw)
        self.assertIn(b'only for you, the authenticated owner', raw)
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertNotIn(b'synthetic-owner-cookie', raw)
        self.assertIn(self.path.encode() + b'client.js', raw)
        self.assertIn(b'Open private browser', self.http(path='/review/' + self.grant['request_id'])[2])
        self.assertIn("import('./vendor/core/rfb.js')", CLIENT)
        self.assertIn('display.replaceChildren()', CLIENT)
        for forbidden in ['clipboardPasteFrom', 'sendCredentials(', 'https://', 'console.', 'innerHTML']:
            self.assertNotIn(forbidden, CLIENT)

    def test_nonowner_and_assistant_cannot_reach_browser(self):
        self.assertEqual(self.http(headers={'Cookie': ''})[0], 401)
        self.assertEqual(self.http(headers={'Cookie': 'MMAUTHTOKEN=another'})[0], 401)
        self.assertEqual(self.root_calls, [])
        with self.assertRaises(Exception):
            service.make_server(SimpleNamespace(), 'assistant', 0, {'openclaw': 's' * 40}, None,
                                operator_auth=lambda _h: True, browser=self.bridge)
        with self.assertRaises(Exception):
            service.make_server(SimpleNamespace(), 'operator', 0, {'openclaw': 's' * 40}, 'o' * 40,
                                browser=self.bridge)

    def test_mobile_viewer_provides_gesture_focused_local_keyboard_without_form_name(self):
        page = self.http()[2].decode()
        self.assertIn('id="private-keyboard-toggle"', page)
        self.assertIn('Show keyboard', page)
        class Keyboard(HTMLParser):
            attributes = None
            def handle_starttag(self, tag, attributes):
                if tag == 'textarea' and dict(attributes).get('id') == 'private-keyboard':
                    self.attributes = dict(attributes)
        parser = Keyboard(); parser.feed(page)
        self.assertIsNotNone(parser.attributes, 'Canvas alone cannot invoke a phone software keyboard')
        self.assertNotIn('name', parser.attributes)
        self.assertIn('disabled', parser.attributes)
        self.assertEqual(parser.attributes['autocomplete'], 'off')
        self.assertEqual(parser.attributes['autocorrect'], 'off')
        self.assertEqual(parser.attributes['spellcheck'], 'false')

    def test_mfa_guidance_uses_private_screen_without_metadata_secret_inputs(self):
        status, _headers, raw = self.http()
        self.assertEqual(status, 200)
        page = raw.decode()
        self.assertIn('enter email, SMS or authenticator codes only in the private website screen below', page)
        self.assertIn('Approve sign-in pushes on your trusted device', page)
        self.assertIn('Never send codes, recovery codes or authenticator setup seeds to the agent or Mattermost', page)
        self.assertIn('MFA or sign-in may be required again', page)
        self.assertIn('Permission still expires and can be revoked', page)
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__(); self.inputs = []
            def handle_starttag(self, tag, attributes):
                if tag in {'input', 'textarea'}: self.inputs.append((tag, dict(attributes)))
        parser = Inputs(); parser.feed(page)
        self.assertEqual(len(parser.inputs), 9)
        for tag, attributes in parser.inputs:
            if tag == 'textarea':
                self.assertEqual(attributes.get('id'), 'private-keyboard')
                self.assertNotIn('name', attributes)
                self.assertNotIn('form', attributes)
                continue
            self.assertEqual(tag, 'input')
            self.assertEqual(attributes.get('type'), 'hidden')
            self.assertIn(attributes.get('name'), {'digest', 'csrf'})
        for name in ['otp', 'recovery_code', 'authenticator_seed']:
            self.assertEqual(self.http('POST', self.path + 'ready', dict(self.fields('ready'), **{name: 'synthetic-value'}))[0], 400)
        self.assertEqual([call for call in self.root_calls if call[1] == 'ready'], [])

    def test_each_fixed_action_uses_empty_body_bridge_and_single_use_nonce(self):
        for action in ['start', 'fill', 'ready', 'close']:
            fields = self.fields(action)
            self.assertEqual(self.http('POST', self.path + action, fields)[0], 303)
            self.assertEqual(self.root_calls[-1], (self.grant['request_id'], action, {'method': 'POST', 'limit': 4096}))
            self.assertEqual(self.http('POST', self.path + action, fields)[0], 400)
        self.assertEqual(len(self.root_calls), 4)

    def test_crossorigin_action_mismatch_digest_and_extra_fields_cannot_mutate(self):
        fields = self.fields('fill')
        for origin in ['https://attacker.example', 'null']:
            self.assertEqual(self.http('POST', self.path + 'fill', fields, {'Origin': origin})[0], 403)
        self.assertEqual(self.http('POST', self.path + 'fill', fields, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.http('POST', self.path + 'ready', fields)[0], 400)
        for changes in [{'digest': 'b' * 64}, {'password': 'never-accepted'}, {'url': 'https://attacker.example'}]:
            values = dict(self.fields('fill'), **changes)
            self.assertEqual(self.http('POST', self.path + 'fill', values)[0], 400)
        self.assertEqual(self.root_calls, [])

    def test_nonce_expiry_clockrollback_restart_and_concurrent_consume_fail_closed(self):
        for tick in [99, 400]:
            fields = self.fields('start'); self.tick = tick
            with self.assertRaises(BrowserOwnerRefused): self.bridge.consume(self.grant, 'start', fields)
            self.tick = 100
        fields = self.fields('start'); self.bridge.nonces.clear()
        with self.assertRaises(BrowserOwnerRefused): self.bridge.consume(self.grant, 'start', fields)
        fields = self.fields('fill')
        def consume():
            try:
                self.bridge.consume(self.grant, 'fill', fields); return True
            except BrowserOwnerRefused:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _v: consume(), range(2))), 1)

    def test_active_grant_required_for_every_resource_and_action(self):
        for changes in [{'status': 'pending'}, {'status': 'revoked'}, {'expires_at': 100},
                        {'approved_at': 101}, {'actor': 'other'}, {'account': 'localhost'}]:
            original = copy.deepcopy(self.grant); self.grant.update(changes)
            for suffix in ['', 'client.js', 'vendor/core/rfb.js', 'state', 'socket']:
                self.assertEqual(self.http(path=self.path + suffix)[0], 400)
            self.grant = original
        self.assertEqual(self.root_calls, [])

    def test_second_account_uses_grant_account_and_root_checks_mode(self):
        self.grant['account'] = 'library.example.com'
        status, _headers, raw = self.http()
        self.assertEqual(status, 200)
        self.assertIn(b'Private library.example.com sign-in', raw)
        self.assertIn(b'Fill library.example.com sign-in from Bitwarden', raw)
        self.assertNotIn(b'Hotels.com', raw)
        self.assertEqual(self.http('POST', self.path + 'start', self.fields('start'))[0], 303)
        self.bridge.request = lambda *_a, **_k: (_ for _ in ()).throw(BrowserOwnerRefused('Root profile mode refused'))
        self.assertEqual(self.http('POST', self.path + 'start', self.fields('start'))[0], 400)

    def test_assets_traversal_queries_and_arbitrary_actions_refused(self):
        self.assertEqual(self.http(path=self.path + 'vendor/core/rfb.js')[0], 200)
        self.assertEqual(self.root_calls[-1][1], 'vendor/core/rfb.js')
        self.root_calls.clear()
        for suffix in ['vendor/../secret.js', 'vendor/%2e%2e/secret.js', 'vendor//secret.js',
                       'vendor/core/key.json', 'vendor/core/rfb.js?token=x', 'vendor/core/rfb.js#x', 'state?x=y']:
            self.assertIn(self.http(path=self.path + suffix)[0], {400, 403})
        self.assertEqual(self.http('POST', self.path + 'purchase', self.fields('start'))[0], 400)
        self.assertEqual(self.root_calls, [])

    def test_upstream_http_sends_only_fixed_operator_key_and_empty_json(self):
        calls = []
        connection = SimpleNamespace(request=lambda *args, **kwargs: calls.append((args, kwargs)),
            getresponse=lambda: SimpleNamespace(status=200, read=lambda _n: b'{"status":"idle"}'), close=lambda: None)
        with patch.object(bridge_module.http.client, 'HTTPConnection', return_value=connection) as transport:
            BrowserOwnerBridge.request(self.bridge, self.grant['request_id'], 'fill', method='POST')
        transport.assert_called_once_with('127.0.0.1', 18816, timeout=35)
        args, _kwargs = calls[0]
        self.assertEqual(args[0], 'POST'); self.assertEqual(args[2], b'{}')
        self.assertEqual(args[3], {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
        self.assertNotIn('Cookie', args[3]); self.assertNotIn('Origin', args[3])

    def test_state_discards_any_upstream_private_fields(self):
        self.bridge.request = lambda *_a, **_k: b'{"status":"ready","password":"never-public","cookies":"private"}'
        status, _headers, raw = self.http(path=self.path + 'state')
        self.assertEqual(status, 400)
        self.assertNotIn(b'never-public', raw)
        self.assertNotIn(b'cookies', raw)
        page = private_browser(self.grant, {'status': 'ready'}, {})
        self.assertNotIn('never-public', page)

    def test_client_waits_for_startup_retries_disconnect_and_hides_ready_display(self):
        harness = r'''
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(0, 'utf8').replace("await import('./vendor/core/rfb.js')", '({default: globalThis.FakeRFB})')
 .replace("await import('./vendor/core/input/keysymdef.js')", '({default: {lookup: value => value}})');
let interval, cleared = false, now = 0, erased = 0;
const states = ['starting', 'awaiting_owner', 'awaiting_owner', 'ready', 'starting', 'awaiting_owner', 'revoked'];
const viewers = [];
class FakeRFB {
  constructor() {this.events = {}; viewers.push(this);}
  addEventListener(name, callback) {this.events[name] = callback;}
  disconnect() {this.disconnected = true; this.events.disconnect?.();}
}
const display = {dataset: {connect: 'yes'}, replaceChildren: () => {erased++;}, addEventListener: () => {}};
const controls = {};
const context = {
  URL, AbortSignal, FakeRFB, Date: {now: () => now},
  document: {addEventListener: () => {}, getElementById: id => id === 'private-display' ? display : (controls[id] ??= {
    textContent: '', value: '', addEventListener: () => {}, setAttribute: () => {}, blur: () => {}, setSelectionRange: () => {}})},
  window: {location: {href: 'https://owner.example/browser/fixture/', protocol: 'https:'},
    setInterval: callback => {interval = callback; return 7;},
    clearInterval: id => {assert.equal(id, 7); cleared = true;}, addEventListener: () => {}},
  fetch: async () => ({ok: true, json: async () => ({status: states.shift()})}),
};
(async () => {
  vm.runInNewContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(viewers.length, 0, 'starting must not request vendor modules/socket');
  await interval(); assert.equal(viewers.length, 1);
  viewers[0].events.disconnect(); now = 2000;
  await interval(); assert.equal(viewers.length, 2, 'awaiting owner reconnects after backoff');
  await interval(); assert.ok(viewers[1].disconnected, 'ready must remove owner display');
  await interval(); assert.equal(viewers.length, 2);
  await interval(); assert.equal(viewers.length, 3, 'Take control awaits owner before reconnect');
  await interval(); assert.ok(cleared); assert.ok(viewers[2].disconnected); assert.ok(erased >= 4);
})().catch(error => {process.stderr.write(String(error)); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', harness], input=CLIENT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonce_capacity_is_bounded_and_expired_entries_reclaimed(self):
        for _ in range(1000): self.bridge.challenge(self.grant, 'fill')
        with self.assertRaises(BrowserOwnerRefused): self.bridge.challenge(self.grant, 'fill')
        self.tick = 401
        self.bridge.challenge(self.grant, 'fill')
        self.assertEqual(len(self.bridge.nonces), 1)

    def test_mobile_keyboard_forwards_only_rfb_keys_and_clears_sensitive_buffers(self):
        harness = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
const source = fs.readFileSync(0, 'utf8').replace("await import('./vendor/core/rfb.js')", '({default: globalThis.FakeRFB})')
 .replace("await import('./vendor/core/input/keysymdef.js')", '({default: {lookup: point => point <= 255 ? point : (0x01000000 | point)}})');
let interval, pagehide, viewer;
const sent = [], fetched = [], elements = {};
const document = {activeElement: null, addEventListener() {}, getElementById: id => elements[id] ??= new Element(id)};
class Element {
  constructor(id) {this.id = id; this.value = ''; this.disabled = true; this.events = {}; this.dataset = {connect: 'yes'};}
  addEventListener(name, callback) {this.events[name] = callback;}
  setAttribute() {}
  setSelectionRange(start, end) {this.selectionStart = start; this.selectionEnd = end;}
  replaceChildren() {}
  focus() {document.activeElement = this; this.events.focus?.({});}
  blur() {if (document.activeElement === this) {document.activeElement = null; this.events.blur?.({});}}
  fire(name, values = {}) {const event = {stopPropagation() {}, preventDefault() {this.defaultPrevented = true;}, cancelable: true, ...values}; this.events[name]?.(event); return event;}
}
class FakeRFB {
  constructor() {viewer = this; this.events = {}; this.focusOnClick = true;}
  addEventListener(name, callback) {this.events[name] = callback;}
  sendKey(keysym, code) {sent.push([keysym, code]);}
  disconnect() {this.events.disconnect?.();}
}
const context = {document, URL, AbortSignal, FakeRFB,
 window: {location: {href: 'https://owner.example/browser/fixture/', protocol: 'https:'},
  setInterval: callback => {interval = callback; return 1;}, clearInterval() {},
  addEventListener: (name, callback) => {if (name === 'pagehide') pagehide = callback;}},
 fetch: async (...args) => {fetched.push(args); return {ok: true, json: async () => ({status: 'awaiting_owner'})};}};
(async () => {
 vm.runInNewContext(source, context); await new Promise(resolve => setImmediate(resolve));
 const keyboard = elements['private-keyboard'], toggle = elements['private-keyboard-toggle'];
 assert.equal(toggle.disabled, true); viewer.events.connect(); assert.equal(toggle.disabled, false);
 toggle.fire('click'); assert.equal(document.activeElement, keyboard, 'focus must happen synchronously in user gesture');
 assert.equal(viewer.focusOnClick, false);
 const sentinel = keyboard.value; assert.equal(sentinel, '_'.repeat(32));
 keyboard.value = sentinel + 'a😀'; keyboard.fire('input', {inputType: 'insertText'});
 assert.deepEqual(sent, [[97, null], [0x0101f600, null]]); assert.equal(keyboard.value, sentinel);
 keyboard.value = sentinel + '_'; keyboard.fire('input', {inputType: 'insertText'}); assert.deepEqual(sent.at(-1), [95, null]);
 let event = keyboard.fire('beforeinput', {inputType: 'deleteContentBackward'});
 assert.equal(event.defaultPrevented, true); assert.deepEqual(sent.at(-1), [0xff08, 'Backspace']);
 const count = sent.length; keyboard.fire('input', {inputType: 'insertText'}); assert.equal(sent.length, count);
 keyboard.fire('compositionstart'); keyboard.value = sentinel + '漢';
 keyboard.fire('input', {isComposing: true}); assert.equal(sent.length, count);
 keyboard.fire('compositionend'); assert.deepEqual(sent.at(-1), [0x01006f22, null]);
 assert.equal(keyboard.value, sentinel);
 keyboard.value = 'é'; keyboard.fire('input', {inputType: 'insertReplacementText'});
 assert.deepEqual(sent.at(-1), [233, null]); assert.equal(sent.length, count + 2, 'placeholder replacement must not erase remote text');
 event = keyboard.fire('beforeinput', {inputType: 'insertLineBreak'});
 assert.equal(event.defaultPrevented, true); assert.deepEqual(sent.at(-1), [0xff0d, 'Enter']);
 keyboard.value = 'synthetic-ephemeral'; keyboard.blur(); assert.equal(keyboard.value, ''); assert.equal(viewer.focusOnClick, true);
 const beforeBlurInput = sent.length; keyboard.fire('compositionend'); assert.equal(sent.length, beforeBlurInput);
 toggle.fire('click'); keyboard.value = 'synthetic-ephemeral'; viewer.events.disconnect();
 assert.equal(keyboard.value, ''); assert.equal(toggle.disabled, true); assert.equal(keyboard.disabled, true);
 pagehide(); assert.equal(keyboard.value, '');
 assert.ok(fetched.every(args => args[0] === './state' && !('body' in args[1])));
})().catch(error => {process.stderr.write(String(error)); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', harness], input=CLIENT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_upstream_failures_are_fixed_diagnostics_and_no_redirect_following(self):
        for response in [SimpleNamespace(status=302, read=lambda _n: b'private-upstream-secret'),
                         SimpleNamespace(status=500, read=lambda _n: b'private-upstream-secret')]:
            connection = SimpleNamespace(request=lambda *_a, **_k: None, getresponse=lambda: response, close=lambda: None)
            with patch.object(bridge_module.http.client, 'HTTPConnection', return_value=connection):
                with self.assertRaises(BrowserOwnerRefused) as error:
                    BrowserOwnerBridge.request(self.bridge, self.grant['request_id'], 'state')
                self.assertNotIn('private-upstream-secret', str(error.exception))
                self.assertNotIn(TOKEN, str(error.exception))

    def test_websocket_exact_origin_and_key_then_private_stream_revocation(self):
        key = base64.b64encode(b'0123456789abcdef').decode()
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        headers = Message()
        for name, value in [('Origin', self.server.operator_origin), ('Upgrade', 'websocket'),
                            ('Connection', 'Upgrade'), ('Sec-WebSocket-Version', '13'), ('Sec-WebSocket-Key', key)]:
            headers[name] = value
        calls, outputs = [], []
        class Socket:
            def sendall(self, raw): calls.append(raw)
            def recv(self, _n):
                return ('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        'Sec-WebSocket-Accept: ' + accept + '\r\n\r\n').encode() + b'owner-only-pixels'
            def settimeout(self, _n): pass
            def close(self): outputs.append('closed')
        handler = SimpleNamespace(headers=headers, server=self.server, connection=Socket(), wfile=io.BytesIO(),
            send_response=lambda code: outputs.append(code), send_header=lambda *_a: None, end_headers=lambda: None)
        def revoke(*_args):
            self.tick += 0.6
            self.grant.update(status='revoked')
            return [], [], []
        with patch.object(bridge_module.socket, 'create_connection', return_value=Socket()) as connect, \
             patch.object(bridge_module.select, 'select', side_effect=revoke):
            self.bridge.websocket(handler, self.grant['request_id'], copy.deepcopy(self.grant))
        self.assertIn(101, outputs); self.assertIn('closed', outputs)
        upstream = calls[0]
        self.assertIn(('Authorization: Bearer ' + TOKEN).encode(), upstream)
        self.assertNotIn(b'Cookie', upstream); self.assertNotIn(b'Origin:', upstream)
        self.assertEqual(calls[1], b'owner-only-pixels')
        self.grant['status'] = 'active'
        headers.replace_header('Origin', 'https://attacker.example')
        with patch.object(bridge_module.socket, 'create_connection') as connect:
            with self.assertRaises(BrowserOwnerRefused):
                self.bridge.websocket(handler, self.grant['request_id'], self.grant)
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
