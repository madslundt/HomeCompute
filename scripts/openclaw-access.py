#!/usr/bin/env python3
"""Access approval service with a separate owner-only private viewer transport.

Both listeners are loopback only. The operator origin must be the exact trusted
HTTPS reverse-proxy origin, or its explicit loopback HTTP origin for SSH-forward
testing. Neither listener is suitable for direct LAN exposure. Mattermost
notification delivery and the authenticated OpenClaw bridge are separate work.
Only the configured owner can reach the optional viewer; assistant routes
remain finite metadata with no credentials, cookies or private browser data.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from openclaw_access_grants import AccessGrants, GrantRefused, identity
from openclaw_bitwarden import read_private
from openclaw_access_page import CSP, listing, review, sign_in
from openclaw_site_registry import SiteRegistry
from openclaw_site_transactions import SiteTransactions
from openclaw_site_transaction_page import review_transaction

STARTUP_STAGE = 'arguments'


def report_failure(error: Exception, stage: str) -> None:
    """Journal only a finite failure category, never exception/configuration text."""
    category = ('listener_unavailable' if isinstance(error, OSError) and error.errno in {48, 98}
                else 'permission_denied' if isinstance(error, PermissionError)
                else 'invalid_configuration' if isinstance(error, (ValueError, GrantRefused))
                else 'dependency_unavailable' if isinstance(error, ImportError)
                else 'startup_failed')
    allowed = {'arguments', 'private_configuration', 'controllers', 'assistant_listener',
               'operator_listener', 'lan_listener', 'serving'}
    print(json.dumps({'event': 'access_service_failed', 'stage': stage if stage in allowed else 'unknown',
                      'error_code': category}), file=sys.stderr, flush=True)


def valid_token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', value):
        raise GrantRefused('Service token refused')
    return value


class BoundedServer(ThreadingHTTPServer):
    # Permit a closed loopback listener to restart despite TCP TIME_WAIT.
    # SO_REUSEPORT stays disabled: an active listener cannot be duplicated.
    daemon_threads, allow_reuse_address, request_queue_size = True, True, 8

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, *_args):
        # The standard handler prints exceptions. Upstream and auth text must
        # never become server logs, even on unexpected errors.
        pass


def make_server(ledger: AccessGrants, role: str, port: int, assistant_tokens: dict[str, str],
                operator_token: str | None, *, operator_origin: str | None = None, notify=None,
                operator_auth=None, login_url=None, transactions: SiteTransactions | None = None, browser=None):
    if role not in {'assistant', 'operator'} or type(port) is not int or not 0 <= port <= 65535:
        raise GrantRefused('Listener configuration refused')
    if browser is not None and (role != 'operator' or operator_auth is None):
        raise GrantRefused('Private browser requires owner authentication')
    if operator_auth is None:
        valid_token(operator_token)
    if not isinstance(assistant_tokens, dict) or not assistant_tokens or any(identity(actor) != actor for actor in assistant_tokens):
        raise GrantRefused('Assistant identities refused')
    tokens = [valid_token(value) for value in assistant_tokens.values()] + ([operator_token] if operator_token else [])
    if len(set(tokens)) != len(tokens):
        raise GrantRefused('Distinct role credentials required')
    # Explicit Basic auth uses the browser's native credential prompt. No
    # operator credential enters generated HTML, script, a cookie or a URL.
    operator_basic = 'Basic ' + base64.b64encode(('operator:' + (operator_token or '')).encode()).decode()
    operator_bearer = 'Bearer ' + (operator_token or '')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass

        def send(self, status: int, value: dict | str | bytes, *, location=None, content_type=None, csp=CSP):
            raw = value if isinstance(value, bytes) else value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.close_connection = True
            self.send_response(status)
            self.send_header('Content-Type', content_type or ('text/html; charset=utf-8' if isinstance(value, str) else 'application/json'))
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            # Native form POSTs use an opaque Origin under no-referrer. Keep
            # same-origin forms compatible with the strict Origin/CSRF checks
            # while suppressing Referer entirely on cross-origin requests.
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', csp)
            self.send_header('Connection', 'close')
            if location:
                self.send_header('Location', location)
            if status == 401 and role == 'operator' and operator_auth is None:
                self.send_header('WWW-Authenticate', 'Basic realm="HomeCompute access approvals", charset="UTF-8"')
            self.end_headers()
            self.wfile.write(raw)

        def authenticate(self):
            if (len(self.path) > 256 or '?' in self.path or '#' in self.path
                    or len(self.headers) > 32 or sum(len(k) + len(v) for k, v in self.headers.items()) > 8192
                    or len(self.headers.get_all('Host', [])) != 1
                    or self.headers.get('Host') != self.server.host_authority
                    or len(self.headers.get_all('Authorization', [])) > 1):
                self.send(403, {'error': 'access_refused'})
                return None
            if role == 'operator' and operator_auth is not None:
                # Cookie-auth mode has no Basic/bearer backdoor. The callback
                # verifies the exact owner on every request against Mattermost.
                try:
                    authorized = not self.headers.get('Authorization') and operator_auth(self.headers) is True
                except Exception:
                    authorized = False
                if authorized:
                    return 'operator'
                self.send(401, sign_in(login_url) if login_url else {'error': 'authentication_required'})
                return None
            if not self.headers.get('Authorization'):
                self.send(401, {'error': 'authentication_required'})
                return None
            auth = self.headers['Authorization']
            if any(ord(c) > 127 for c in auth):
                self.send(403, {'error': 'access_refused'})
                return None
            if role == 'operator':
                if hmac.compare_digest(auth, operator_basic) or hmac.compare_digest(auth, operator_bearer):
                    return 'operator'
            else:
                if self.headers.get('Origin') is not None:
                    self.send(403, {'error': 'access_refused'})
                    return None
                for actor, token in assistant_tokens.items():
                    if hmac.compare_digest(auth, 'Bearer ' + token):
                        return actor
            self.send(401, {'error': 'authentication_required'})
            return None

        def read_body(self) -> dict:
            sizes = self.headers.get_all('Content-Length', [])
            if len(sizes) != 1 or not re.fullmatch(r'[0-9]{1,4}', sizes[0]) or self.headers.get('Transfer-Encoding'):
                raise GrantRefused('Request body refused')
            size = int(sizes[0])
            if not 1 <= size <= 4096:
                raise GrantRefused('Request body refused')
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise GrantRefused('Request body refused')
            kind = self.headers.get('Content-Type', '').split(';', 1)[0]
            if role == 'assistant' and kind == 'application/json':
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise GrantRefused('Duplicate field refused')
                        result[key] = value
                    return result
                return json.loads(raw, object_pairs_hook=unique)
            if role == 'operator' and kind == 'application/x-www-form-urlencoded':
                values = parse_qs(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True, max_num_fields=5)
                if any(len(v) != 1 for v in values.values()):
                    raise GrantRefused('Duplicate field refused')
                return {k: v[0] for k, v in values.items()}
            raise GrantRefused('Request body refused')

        def do_GET(self):
            actor = self.authenticate()
            if actor is None:
                return
            try:
                if role == 'operator' and browser is not None and self.path.startswith('/browser/'):
                    return browser.get(self)
                if role == 'assistant' and self.path.startswith('/access/'):
                    return self.send(200, ledger.status(self.path.removeprefix('/access/'), actor=actor))
                if role == 'operator' and self.path == '/':
                    return self.send(200, listing(ledger.listing()))
                if role == 'operator' and self.path.startswith('/review/'):
                    item = ledger.status(self.path.removeprefix('/review/'))
                    actions = ['approve', 'deny'] if item['status'] == 'pending' else ['revoke'] if item['status'] == 'active' else []
                    page = review(item, {action: ledger.challenge(item['request_id'], action) for action in actions})
                    if browser is not None and item['status'] == 'active':
                        page = page.replace('</main>', '<p><a href="/browser/' + item['request_id'] +
                                            '/">Open private browser</a></p></main>')
                    return self.send(200, page)
                if role == 'operator' and transactions is not None and self.path.startswith('/transactions/review/'):
                    item = transactions.review(self.path.removeprefix('/transactions/review/'))
                    actions = ['approve', 'deny'] if item['status'] == 'pending' else ['revoke'] if item['status'] == 'approved' else []
                    return self.send(200, review_transaction(item, {action: transactions.challenge(item['transaction_id'], action)
                                                                  for action in actions}))
                self.send(404, {'error': 'not_found'})
            except Exception:
                self.send(400, {'error': 'access_request_refused'})

        def do_POST(self):
            actor = self.authenticate()
            if actor is None:
                return
            try:
                if role == 'assistant' and self.path == '/access/request':
                    item, created = ledger.request(actor, self.read_body())
                    delivery = 'not_configured' if notify is None else 'not_replayed'
                    # Only a notifier declaring durable reconciliation may
                    # recover the commit-before-callback crash gap. Ordinary
                    # callbacks remain duplicate-suppressed. The production
                    # notifier refuses to retry any uncertain reservation.
                    reconcile = getattr(notify, 'reconcile', None)
                    callback = (notify if created else reconcile if item['status'] == 'pending'
                                and callable(reconcile) else None)
                    if callback is not None:
                        try:
                            receipt = callback(item)
                            delivery = 'delivered' if isinstance(receipt, dict) and receipt.get('delivery') == 'delivered' else 'submitted'
                        except Exception:
                            # The request remains pending; never replay notifications automatically.
                            delivery = 'delivery_uncertain'
                    return self.send(200, dict(item, notification_delivery=delivery))
                transaction_decision = transactions is not None and self.path.startswith('/transactions/decision/')
                browser_decision = browser is not None and self.path.startswith('/browser/')
                if (role != 'operator' or not (self.path.startswith('/decision/') or transaction_decision or browser_decision)
                        or len(self.headers.get_all('Origin', [])) != 1
                        or self.headers.get('Origin') != self.server.operator_origin
                        or len(self.headers.get_all('Sec-Fetch-Site', [])) > 1
                        or self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin'):
                    return self.send(403, {'error': 'access_refused'})
                values = self.read_body()
                if browser_decision:
                    return browser.post(self, values)
                if transaction_decision:
                    if set(values) != {'action', 'digest', 'csrf'}:
                        raise GrantRefused('Exact transaction decision refused')
                    reference = self.path.removeprefix('/transactions/decision/')
                    transactions.decide(reference, values['digest'], values['action'], values['csrf'])
                    return self.send(303, {}, location='/transactions/review/' + reference)
                if set(values) != {'action', 'digest', 'csrf', 'minutes'} or not re.fullmatch(r'[0-9]{1,4}', values['minutes']):
                    raise GrantRefused('Decision refused')
                reference = self.path.removeprefix('/decision/')
                ledger.decide(reference, values['digest'], values['action'], int(values['minutes']), values['csrf'])
                self.send(303, {}, location='/review/' + reference)
            except Exception:
                self.send(400, {'error': 'access_request_refused'})

    server = BoundedServer(('127.0.0.1', port), Handler)
    if role == 'operator':
        origin = operator_origin or 'http://127.0.0.1:' + str(server.server_port)
        parsed = urlsplit(origin)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment
                or parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost'}):
            server.server_close()
            raise GrantRefused('Private operator origin refused')
        server.operator_origin, server.host_authority = origin, parsed.netloc
    else:
        server.host_authority = '127.0.0.1:' + str(server.server_port)
    return server


def main():
    global STARTUP_STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--assistant-tokens-file', type=Path, required=True)
    owner = parser.add_mutually_exclusive_group(required=True)
    owner.add_argument('--operator-token-file', type=Path)
    owner.add_argument('--owner-config', type=Path)
    parser.add_argument('--notify-config', type=Path)
    parser.add_argument('--notify-state', type=Path)
    parser.add_argument('--sites-config', type=Path, help='trusted, private website profile registry')
    parser.add_argument('--site-worker-ready', action='store_true', help='operator-qualified deployed worker gate; false by default')
    parser.add_argument('--transactions-state', type=Path, help='private exact-transaction ledger; no qualified committers')
    parser.add_argument('--site-owner-config', type=Path, help='private owner-only browser transport; never an assistant credential')
    parser.add_argument('--assistant-port', type=int, default=18812)
    parser.add_argument('--operator-port', type=int, default=18813)
    parser.add_argument('--operator-origin', required=True)
    parser.add_argument('--lan-operator', action='store_true', help='same owner-only page on the fixed admitted LAN origin')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise GrantRefused('Trusted root service required')
    os.umask(0o077)
    STARTUP_STAGE = 'private_configuration'
    tokens = json.loads(read_private(args.assistant_tokens_file.parent, args.assistant_tokens_file.name, 4096))
    token, operator_auth, login_url, notify = None, None, None, None
    if args.owner_config:
        from openclaw_access_auth import MattermostOwnerAuth, LOGIN_URL
        operator_auth, login_url = MattermostOwnerAuth(args.owner_config), LOGIN_URL
    else:
        token = read_private(args.operator_token_file.parent, args.operator_token_file.name, 1024).decode().strip()
    if bool(args.notify_config) != bool(args.notify_state):
        raise GrantRefused('Notification config and state must be configured together')
    registry = SiteRegistry.from_file(args.sites_config) if args.sites_config else SiteRegistry()
    if args.notify_config:
        from openclaw_access_notifications import MattermostNotifier
        notify = MattermostNotifier(args.notify_config, args.notify_state, registry=registry)
    available = lambda account, scope: (args.site_worker_ready and registry.profile(account)['qualified'] is True
                                        and registry.validate_scope(account, scope) == scope)
    STARTUP_STAGE = 'controllers'
    ledger = AccessGrants(args.state, registry=registry, execution_available=available)
    transactions = (SiteTransactions(args.transactions_state, lambda actor, ref: ledger.status(ref, actor=actor))
                    if args.transactions_state else None)
    browser = None
    if args.site_owner_config:
        if not operator_auth:
            raise GrantRefused('Private browser requires Mattermost owner authentication')
        from openclaw_site_owner_bridge import BrowserOwnerBridge
        browser = BrowserOwnerBridge(args.site_owner_config, lambda ref: ledger.status(ref, actor='openclaw'))
    STARTUP_STAGE = 'assistant_listener'
    assistant = make_server(ledger, 'assistant', args.assistant_port, tokens, token, notify=notify,
                            operator_auth=operator_auth, login_url=login_url, transactions=transactions)
    try:
        STARTUP_STAGE = 'operator_listener'
        operator = make_server(ledger, 'operator', args.operator_port, tokens, token, operator_origin=args.operator_origin,
                               operator_auth=operator_auth, login_url=login_url, transactions=transactions, browser=browser)
    except BaseException:
        assistant.server_close()
        raise
    lan = None
    if args.lan_operator:
        STARTUP_STAGE = 'lan_listener'
        if not operator_auth:
            raise GrantRefused('LAN approval page requires Mattermost owner authentication')
        lan = make_server(ledger, 'operator', 18815, tokens, None,
                          operator_origin='https://mattermost.home.arpa:18814', operator_auth=operator_auth,
                          login_url='https://mattermost.home.arpa/login', transactions=transactions, browser=browser)
    threading.Thread(target=assistant.serve_forever, daemon=True).start()
    if lan:
        threading.Thread(target=lan.serve_forever, daemon=True).start()
    try:
        STARTUP_STAGE = 'serving'
        operator.serve_forever()
    finally:
        assistant.shutdown()
        assistant.server_close()
        operator.server_close()
        if lan:
            lan.shutdown()
            lan.server_close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except Exception as error:
        report_failure(error, STARTUP_STAGE)
        raise SystemExit(1) from None
