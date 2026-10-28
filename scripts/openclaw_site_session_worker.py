#!/usr/bin/env python3
"""Owner-only sign-in display and private-pipe, finite authenticated operations.

Only the trusted parent may send commands or proxy noVNC to an authenticated
owner. The parent kills this entire disposable container on grant revocation.
No browser content, cookies, credentials, raw diagnostics or URLs reach stdout.
"""
from __future__ import annotations

import copy
import json
import os
import select
import signal
# Fixed image-owned GUI executables only.
import subprocess  # nosec B404
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from openclaw_site_drivers import FormDriver, SiteStopped, challenge_present, credential_input, driver_config, validated_parameters
from openclaw_site_registry import account_name, validate_profile
from openclaw_site_network_health import NetworkHealth
from openclaw_site_worker import Guard, silence_process_output

PROXIES = frozenset({'http://172.30.192.10:3128', 'http://172.30.192.11:3128'})


class InitialFailure(Exception):
    """Finite trusted-parent diagnostics only; never an upstream message."""
    def __init__(self, stage, error):
        self.stage = stage if stage in {'validate', 'display', 'browser', 'navigate'} else 'validate'
        self.code = {PermissionError: 'permission', FileNotFoundError: 'missing_dependency',
                     TimeoutError: 'timeout', ValueError: 'invalid_input', SiteStopped: 'refused'}.get(type(error))
        if self.code is None:
            self.code = {'Error': 'browser_error', 'TargetClosedError': 'browser_closed',
                         'TimeoutError': 'timeout'}.get(type(error).__name__, 'unknown')
        super().__init__('Private session initialization unavailable')


class Bootstrap:
    stage, initialized = 'validate', False


def unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError('Duplicate private command refused')
        value[name] = item
    return value


def pure_query(raw: bytes) -> bool:
    """Parse complete GraphQL documents; unknown persisted hashes never pass."""
    try:
        from graphql import parse
        from graphql.language import FragmentDefinitionNode, OperationDefinitionNode
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 131072:
            return False
        value = json.loads(raw, object_pairs_hook=unique)
        batch = value if isinstance(value, list) else [value]
        if not 1 <= len(batch) <= 20:
            return False
        for item in batch:
            if (not isinstance(item, dict) or set(item) - {'query', 'variables', 'operationName', 'extensions'}
                    or not isinstance(item.get('query'), str) or not 1 <= len(item['query']) <= 65536):
                return False
            document = parse(item['query'], max_tokens=20000)
            operations = [node for node in document.definitions if isinstance(node, OperationDefinitionNode)]
            if (not operations or any(node.operation.value != 'query' for node in operations)
                    or any(not isinstance(node, (FragmentDefinitionNode, OperationDefinitionNode))
                           for node in document.definitions)):
                return False
            name = item.get('operationName')
            if name is not None and (not isinstance(name, str)
                    or not any(node.name and node.name.value == name for node in operations)):
                return False
            if len(operations) > 1 and name is None:
                return False
        return True
    except Exception:
        return False


def graphql_target(url: str) -> bool:
    return 'graphql' in urlsplit(url).path.lower().split('/')


class SessionGuard(Guard):
    def __init__(self, profile, deadline, **kwargs):
        super().__init__(profile, deadline, **kwargs)
        self.manual, self.verified_post = True, False
        self.observed = set(profile['domains'])

    def observe(self, request):
        if not self.manual:
            return
        try:
            host = account_name(urlsplit(request.url).hostname)
            if len(self.observed) < 64:
                self.observed.add(host)
        except Exception:
            return

    def allowed(self, value, method):
        self.step()
        parsed = urlsplit(value)
        if method in {'POST', 'OPTIONS'} and self.verified_post and graphql_target(value):
            method = 'GET'
        query = parse_qs(parsed.query, keep_blank_values=True)
        if 'query' in query or 'extensions' in query:
            if len(query.get('query', [])) != 1 or not pure_query(json.dumps({
                    'query': query['query'][0], 'operationName': query.get('operationName', [None])[0]}).encode()):
                raise SiteStopped('unavailable')
        # Explicit READ POST exceptions from automatic recipes do not apply to
        # an owner session: only a parsed query can admit a non-GET request.
        original = self.posts
        self.posts = {}
        try:
            return super().allowed(value, method)
        finally:
            self.posts = original

    def intercept(self, route):
        if self.manual:
            try:
                self.step()
                parsed = urlsplit(route.request.url)
                account_name(parsed.hostname)
                if (parsed.scheme != 'https' or parsed.port not in (None, 443)
                        or parsed.username or parsed.password):
                    raise SiteStopped('unavailable')
                self.observe(route.request)
                route.continue_()  # Public-only pinning proxy checks every CONNECT.
            except Exception:
                route.abort('blockedbyclient')
            return
        request = route.request
        self.verified_post = (request.method in {'POST', 'OPTIONS'} and graphql_target(request.url)
                              and pure_query(request.post_data_buffer))
        try:
            self.allowed(request.url, request.method)
        except Exception:
            # Analytics and unknown writes are dropped without making a useful
            # read fail. Deadlines still fail the next driver step.
            route.abort('blockedbyclient')
            self.verified_post = False
            return
        try:
            super().intercept(route)
        finally:
            self.verified_post = False

    def freeze(self):
        self.manual, self.refused, self.phase = False, False, 'read'
        self.profile = copy.deepcopy(self.profile)
        self.profile['domains'] = sorted(self.observed)

    def block_extra_page(self, page):
        if not self.manual:
            super().block_extra_page(page)

    def websocket(self, websocket):
        if self.manual:
            websocket.connect_to_server()
        else:
            websocket.close()


class Display:
    def __init__(self):
        self.processes, self.bridges = [], []

    def spawn(self, command):
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # nosec B603
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self.processes.append(process)
        return process

    def start(self):
        os.environ['DISPLAY'] = ':99'
        self.spawn(['/usr/bin/Xvfb', ':99', '-screen', '0', '1280x900x24', '-nolisten', 'tcp', '-ac'])
        for _ in range(50):
            # This is the container's private memory filesystem.
            if os.path.exists('/tmp/.X11-unix/X99'):  # nosec B108
                self.enable()
                return
            time.sleep(0.05)
        raise SiteStopped('unavailable')

    def enable(self):
        self.disable()
        self.bridges = [self.spawn(['/usr/bin/x11vnc', '-display', ':99', '-localhost', '-rfbport', '5900',
                                   '-forever', '-shared', '-nopw', '-quiet', '-noxdamage']),
                        self.spawn(['/usr/bin/websockify', '--web', '/usr/share/novnc',
                                    '0.0.0.0:6080', '127.0.0.1:5900'])]

    @staticmethod
    def stop(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except ProcessLookupError:
                pass

    def disable(self):
        for process in self.bridges:
            self.stop(process)
        self.bridges = []

    def close(self):
        for process in reversed(self.processes):
            self.stop(process)


class Session:
    def __init__(self, profile, browser, display, deadline, *, resolver=None, clock=time.monotonic):
        self.profile, self.browser, self.display = validate_profile(profile), browser, display
        options = {'clock': clock}
        if resolver is not None:
            options['resolver'] = resolver
        self.guard = SessionGuard(self.profile, deadline, **options)
        self.health = NetworkHealth(self.profile['domains'])
        self.challenge_pending = False
        self.context = self.page = None
        self.replace_context(manual=True)

    def replace_context(self, *, manual):
        self.guard.step()
        storage = self.context.storage_state() if self.context else None
        previous = self.context
        self.context = self.browser.new_context(storage_state=storage, accept_downloads=False,
            service_workers='block', locale='da-DK', permissions=[])
        if previous:
            previous.close()  # Terminates old sockets, popups and pending pages.
        self.guard.manual = manual
        self.context.route('**/*', self.guard.intercept)
        self.context.on('request', self.guard.observe)
        self.context.on('response', lambda response: self.health.response(response) if self.guard.manual else None)
        self.context.on('requestfailed', lambda request: self.health.failed(request) if self.guard.manual else None)
        if not manual:
            self.context.route_web_socket('**/*', self.guard.websocket)
        self.page = self.context.new_page()
        self.page.on('download', lambda download: download.cancel())
        if not manual:
            self.context.on('page', self.guard.block_extra_page)

    def registered(self, value):
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and parsed.hostname in self.profile['domains']
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)

    def fill(self, login):
        if (not self.guard.manual or not isinstance(login, dict) or set(login) != {'username', 'password'}
                or not all(isinstance(v, str) for v in login.values())
                or not 1 <= len(login['username']) <= 320 or not 1 <= len(login['password']) <= 4096):
            raise SiteStopped('unavailable')
        recipe = driver_config(self.profile)
        trusted_origin = urlsplit(recipe['login_url']).netloc
        # The initial registered origin is the only fill destination, even if
        # another registered site domain hosts a third-party identity provider.
        fields = 0
        for name, selector in [('username', recipe['login']['username_selector']),
                               ('password', recipe['login']['password_selector'])]:
            self.guard.step()
            if not self.registered(self.page.url) or urlsplit(self.page.url).netloc != trusted_origin:
                raise SiteStopped('human_sign_in_required')
            locator = self.page.locator(selector)
            if locator.count() != 1:
                continue
            element = locator.element_handle(timeout=self.guard.timeout())
            frame = element.owner_frame() if element else None
            if (frame is not self.page.main_frame or not self.registered(frame.url)
                    or urlsplit(frame.url).netloc != trusted_origin or not element.is_visible()
                    or not credential_input(element, name)):
                continue
            # An element handle is bound to this checked document. Navigation
            # detaches it rather than retargeting the credential to a new page.
            element.fill(login[name], timeout=self.guard.timeout())
            fields += 1
        return {'status': 'filled' if fields else 'human_sign_in_required', 'filled_fields': fields}

    def target(self):
        if self.profile['driver'] == 'hotels':
            return 'https://da.hotels.com/'
        return driver_config(self.profile).get('operations', {}).get('read', {}).get('url',
            'https://' + self.profile['account'] + '/')

    def command(self, value):
        operation = 'read'
        try:
            if isinstance(value, dict) and value == {'command': 'manual'}:
                self.guard.refused = False
            self.guard.step()
            if not isinstance(value, dict) or not isinstance(value.get('command'), str):
                raise SiteStopped('unavailable')
            command = value['command']
            expected = {'fill': {'command', 'login'}, 'run': {'command', 'operation', 'parameters'}}.get(command, {'command'})
            if set(value) != expected:
                raise SiteStopped('unavailable')
            if command == 'fill':
                return self.fill(value['login'])
            if command == 'diagnostics':
                self.page.wait_for_timeout(1)
                self.guard.step()
                return self.health.report('human_sign_in_required' if self.guard.manual else 'ready')
            if command == 'ready':
                if not self.guard.manual or not any(self.registered(page.url) for page in self.context.pages):
                    raise SiteStopped('human_sign_in_required')
                if any(challenge_present(page) for page in self.context.pages):
                    raise SiteStopped('human_sign_in_required')
                self.health.clear()
                self.guard.freeze()
                self.display.disable()
                self.replace_context(manual=False)
                self.page.goto(self.target(), wait_until='domcontentloaded', timeout=self.guard.timeout())
                if challenge_present(self.page):
                    raise SiteStopped('human_sign_in_required')
                self.guard.step()
                self.challenge_pending = False
                return {'status': 'ready', 'observed_hosts': sorted(self.guard.observed)}
            if command == 'manual':
                self.guard.refused = False
                parsed = urlsplit(self.page.url)
                if not (parsed.scheme == 'https' and parsed.hostname in self.guard.observed
                        and parsed.port in (None, 443) and not parsed.username and not parsed.password):
                    raise SiteStopped('unavailable')
                # Preserve POST/session challenge state and the exact document.
                self.guard.manual = True
                self.display.enable()
            elif command == 'run':
                operation = value['operation'] if value['operation'] in {'search', 'prepare', 'read'} else 'read'
                parameters = validated_parameters(self.profile, value['operation'], value['parameters'])
                if self.guard.manual or self.challenge_pending or challenge_present(self.page):
                    raise SiteStopped('human_sign_in_required')
                count = FormDriver(self.profile, self.page, self.guard).operate(value['operation'], parameters)
                return {'status': 'ok', 'operation': operation, 'results_count': count}
            elif command == 'close':
                self.health.clear()
                self.context.close()
                return {'status': 'closed', 'results_count': None}
            elif command != 'status':
                raise SiteStopped('unavailable')
            return {'status': 'human_sign_in_required' if self.guard.manual else 'ready', 'results_count': None}
        except Exception as error:
            status = error.status if isinstance(error, SiteStopped) else 'unavailable'
            if self.guard.clock() >= self.guard.deadline:
                status = 'expired'
            if status == 'human_sign_in_required':
                self.challenge_pending = True
            if (status != 'expired' and isinstance(value, dict) and value.get('command') == 'ready'
                    and not self.guard.manual):
                self.guard.manual, self.guard.refused = True, False
                self.display.enable()
            if isinstance(value, dict) and value.get('command') == 'run':
                return {'status': status, 'operation': operation, 'results_count': None}
            if isinstance(value, dict) and value.get('command') == 'fill':
                return {'status': status, 'filled_fields': 0}
            if isinstance(value, dict) and value.get('command') == 'diagnostics':
                return {'status': status, 'resources': []}
            return {'status': status, 'results_count': None}


class Pipe:
    def __init__(self):
        self.buffer = b''

    def read(self, timeout=0):
        if b'\n' not in self.buffer and select.select([0], [], [], timeout)[0]:
            part = os.read(0, 65537)
            if not part:
                raise EOFError
            self.buffer += part
        if len(self.buffer) > 65536:
            raise ValueError('Private command budget refused')
        if b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            return json.loads(raw, object_pairs_hook=unique)
        return None


def _serve(emit, bootstrap):
    if len(sys.argv) != 1 or sys.stdin.isatty():
        raise ValueError('Private session pipe required')
    pipe, value = Pipe(), None
    until = time.monotonic() + 5
    while value is None and time.monotonic() < until:
        value = pipe.read(0.05)
    wall_now, monotonic_now = time.time(), time.monotonic()
    if (not isinstance(value, dict) or set(value) != {'profile', 'expires_at', 'proxy'}
            or type(value['expires_at']) is not int or not 0 < value['expires_at'] - wall_now <= 86400
            or value['proxy'] not in PROXIES):
        raise ValueError('Private session envelope refused')
    profile = validate_profile(value['profile'])
    deadline = monotonic_now + value['expires_at'] - wall_now
    timer = threading.Timer(max(0.01, deadline - time.monotonic()), lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    display = Display()
    try:
        bootstrap.stage = 'display'
        display.start()
        bootstrap.stage = 'browser'
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False, chromium_sandbox=True,
                proxy={'server': value['proxy'], 'bypass': ''},
                args=['--disable-quic', '--disable-background-networking', '--disable-component-update',
                      '--proxy-bypass-list=<-loopback>'])
            try:
                session = Session(profile, browser, display, deadline)
                bootstrap.stage = 'navigate'
                try:
                    session.page.goto(driver_config(profile)['login_url'], wait_until='domcontentloaded',
                                      timeout=session.guard.timeout())
                except Exception:
                    session.guard.step()  # The owner can retry using the address bar.
                emit({'status': 'human_sign_in_required', 'results_count': None})
                bootstrap.initialized = True
                while True:
                    if time.monotonic() >= deadline:
                        raise SiteStopped('expired')
                    command = pipe.read()
                    if command is not None:
                        result = session.command(command)
                        emit(result)
                        if result['status'] in {'closed', 'expired'}:
                            return
                    session.page.wait_for_timeout(50)  # Pump owner-driven Playwright routes.
            finally:
                browser.close()
    finally:
        timer.cancel()
        display.close()


def serve(emit):
    bootstrap = Bootstrap()
    try:
        _serve(emit, bootstrap)
    except Exception as error:
        if not bootstrap.initialized:
            raise InitialFailure(bootstrap.stage, error) from None
        raise


def main():
    descriptor = os.dup(1)
    def emit(value):
        os.write(descriptor, (json.dumps(value, separators=(',', ':')) + '\n').encode())
    try:
        for name in ('DEBUG', 'PWDEBUG', 'NODE_OPTIONS', 'NODE_PATH', 'PW_TEST_CONNECT_WS_ENDPOINT',
                     'PW_TEST_CONNECT_HEADERS', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                     'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            os.environ.pop(name, None)
        with silence_process_output():
            try:
                serve(emit)
            except EOFError:
                pass
            except BaseException as error:
                result = {'status': 'unavailable', 'results_count': None}
                if isinstance(error, InitialFailure):
                    result.update(stage=error.stage, exception=error.code)
                emit(result)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    main()
