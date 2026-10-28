#!/usr/bin/env python3
"""Disposable private-pipe browser worker. Stdout is a finite status/count only.

Parent responsibilities: validate an approved grant, launch in its own process
group/container, enforce a monotonic deadline, kill on expiry/revocation, deny
all network paths except an exact-domain public-IP-pinning CONNECT proxy, and
never mount the public research browser's profile or expose a debugging port.
"""
from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import json
import math
import os
import re
import socket
import sys
import time
from urllib.parse import unquote, urljoin, urlsplit

from openclaw_bitwarden import Login
from openclaw_site_drivers import FormDriver, SiteStopped, driver_config, validated_parameters

PROXY = 'http://172.30.192.2:3128'
STATUSES = frozenset({'ok', 'human_sign_in_required', 'unavailable', 'expired'})
TRANSACTION = re.compile(r'(?:^|/)(?:checkout|payments?|book(?:ing)?s?|reserv(?:e|ations?)|purchas(?:e|ing)|confirm(?:ation)?)(?:$|[/_.-])', re.I)
CONFIRM = re.compile(r'(?:^|/)confirm(?:ation)?(?:$|[/_.-])', re.I)


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    return 'https://' + parsed.hostname.lower() + (parsed.path or '/')


class Guard:
    def __init__(self, profile: dict, deadline: float, *, resolver=socket.getaddrinfo, clock=time.monotonic):
        self.profile, self.deadline, self.resolver, self.clock = profile, deadline, resolver, clock
        self.phase, self.refused, self.requests = 'login', False, 0
        self.posts = {canonical_url(url): labels for url, labels in driver_config(profile).get('network_posts', {}).items()}

    def step(self):
        if self.clock() >= self.deadline:
            raise SiteStopped('expired')
        if self.refused:
            raise SiteStopped('human_sign_in_required' if self.phase == 'login' else 'unavailable')

    def timeout(self) -> int:
        self.step()
        return max(1, min(10000, int((self.deadline - self.clock()) * 1000)))

    def require_action(self, action: str):
        self.step()
        if action not in self.profile['actions']:
            raise SiteStopped('unavailable')

    def allowed(self, value: str, method: str):
        self.step()
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.hostname.lower() not in self.profile['domains']
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or len(value) > 8192 or '\\' in value or any(ord(c) < 32 for c in value)):
            raise SiteStopped('human_sign_in_required' if self.phase == 'login' else 'unavailable')
        path = parsed.path
        for _ in range(3):
            path = unquote(path)
        path = path.replace('\\', '/')
        labels = self.posts.get(canonical_url(value), [])
        login_target = self.phase == 'login' and 'LOGIN' in labels
        if TRANSACTION.search(path) and not (login_target and CONFIRM.search(path)
                                                and not TRANSACTION.search(CONFIRM.sub('/', path))):
            raise SiteStopped('unavailable')
        if method not in {'GET', 'HEAD'}:
            allowed_post = (login_target or self.phase in {'search', 'read'} and 'READ' in labels)
            if method not in {'POST', 'OPTIONS'} or not allowed_post:
                raise SiteStopped('human_sign_in_required' if self.phase == 'login' else 'unavailable')
        records = self.resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(row[4][0]) for row in records}
        if not addresses or len(addresses) > 16 or any(not address.is_global or address.is_reserved
                or address.is_multicast or getattr(address, 'ipv4_mapped', None) is not None for address in addresses):
            raise SiteStopped('unavailable')
        self.step()

    def intercept(self, route):
        """Follow redirects privately with a checked target at every network hop.

        Browser redirect chains may bypass later route callbacks. Fetch with
        max_redirects=0 and fulfill only the final nonredirect response instead.
        The context request jar keeps all cookies private. No route.continue_.
        """
        try:
            self.requests += 1
            if self.requests > 250:
                raise SiteStopped('unavailable')
            request = route.request
            url, method, body = request.url, request.method, request.post_data_buffer
            headers = {key: value for key, value in request.all_headers().items()
                       if key.lower() not in {'cookie', 'host', 'content-length', 'proxy-authorization'}}
            for hop in range(5):
                self.allowed(url, method)
                response = route.fetch(url=url, method=method, headers=headers,
                                       post_data=body if method == 'POST' else b'',
                                       max_redirects=0, max_retries=0, timeout=self.timeout())
                self.step()
                if response.status not in {301, 302, 303, 307, 308}:
                    if response.status >= 400:
                        raise SiteStopped('human_sign_in_required' if self.phase == 'login' else 'unavailable')
                    route.fulfill(response=response)
                    return
                location = response.headers.get('location')
                if not isinstance(location, str) or hop == 4:
                    raise SiteStopped('unavailable')
                target = urljoin(url, location)
                if response.status in {301, 302, 303} and method == 'POST':
                    method, body = 'GET', b''
                if urlsplit(target).netloc != urlsplit(url).netloc:
                    headers = {k: v for k, v in headers.items() if k.lower() not in {'authorization', 'cookie', 'origin', 'referer'}}
                    if method == 'POST':
                        raise SiteStopped('human_sign_in_required')
                url = target
        except Exception:
            self.refused = True
            route.abort('blockedbyclient')

    def block_extra_page(self, page):
        self.refused = True
        page.close()


@contextmanager
def silence_process_output():
    """Keep library, driver and browser diagnostics off both public streams."""
    stdout, stderr = os.dup(1), os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        sys.stdout.flush(); sys.stderr.flush()
        os.dup2(null, 1); os.dup2(null, 2)
        yield
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        os.dup2(stdout, 1); os.dup2(stderr, 2)
        os.close(null); os.close(stdout); os.close(stderr)


def _run(profile: dict, operation: str, parameters: dict, login: Login, deadline: float, *,
        proxy: str = PROXY, playwright_factory=None, resolver=socket.getaddrinfo, clock=time.monotonic) -> dict:
    safe_operation = operation if isinstance(operation, str) and operation in {'search', 'prepare', 'read'} else 'read'
    result = {'status': 'unavailable', 'operation': safe_operation, 'results_count': None}
    browser = context = None
    phase = 'validation'
    try:
        from openclaw_site_registry import validate_profile
        profile = validate_profile(profile)
        arguments = validated_parameters(profile, operation, parameters)
        if (not isinstance(login, Login) or not isinstance(login.username, str) or not 1 <= len(login.username) <= 320
                or not isinstance(login.password, str) or not 1 <= len(login.password) <= 4096
                or type(deadline) not in {int, float} or not math.isfinite(deadline)
                or deadline - clock() > 45 or proxy != PROXY):
            raise SiteStopped('unavailable')
        guard = Guard(profile, deadline, resolver=resolver, clock=clock)
        guard.step()
        if playwright_factory is None:
            from playwright.sync_api import sync_playwright
            playwright_factory = sync_playwright
        with playwright_factory() as playwright:
            try:
                # launch() owns a private pipe. No connect(), shared profile,
                # remote debugging port, trace, video or screenshots are used.
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                    proxy={'server': proxy, 'bypass': ''}, timeout=guard.timeout(),
                    args=['--disable-quic', '--disable-background-networking', '--disable-component-update',
                          '--proxy-bypass-list=<-loopback>'])
                context = browser.new_context(accept_downloads=False, service_workers='block',
                    proxy={'server': proxy, 'bypass': ''}, locale='da-DK', permissions=[])
                context.set_default_timeout(guard.timeout())
                context.route('**/*', guard.intercept)
                context.route_web_socket('**/*', lambda websocket: websocket.close())
                page = context.new_page()
                context.on('page', guard.block_extra_page)
                page.on('download', lambda download: (setattr(guard, 'refused', True), download.cancel()))
                driver = FormDriver(profile, page, guard)
                phase = 'login'
                driver.login(login)
                phase = operation
                count = driver.operate(operation, arguments)
                guard.step()
                result = {'status': 'ok', 'operation': operation, 'results_count': count}
            finally:
                try:
                    if context is not None:
                        context.close()
                finally:
                    if browser is not None:
                        browser.close()
    except SiteStopped as error:
        result['status'] = error.status if error.status in STATUSES else 'unavailable'
        result['results_count'] = None
    except Exception:
        result['status'] = 'expired' if isinstance(deadline, (int, float)) and clock() >= deadline else (
            'human_sign_in_required' if phase == 'login' else 'unavailable')
        result['results_count'] = None
    return result


def run(profile: dict, operation: str, parameters: dict, login: Login, deadline: float, **kwargs) -> dict:
    # This module runs in a disposable process; never call it in a shared daemon.
    # Explicit proxy policy wins over inherited debugging and proxy variables.
    for name in ('DEBUG', 'PWDEBUG', 'NODE_OPTIONS', 'NODE_PATH', 'PW_TEST_CONNECT_WS_ENDPOINT',
                 'PW_TEST_CONNECT_HEADERS', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        os.environ.pop(name, None)
    with silence_process_output():
        return _run(profile, operation, parameters, login, deadline, **kwargs)


def main():
    operation = 'read'
    try:
        # Do not accept secrets in command arguments or environment variables.
        if len(sys.argv) != 1 or sys.stdin.isatty():
            raise ValueError('Private worker pipe required')
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('Private worker envelope refused')
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'profile', 'operation', 'parameters', 'login', 'timeout_seconds', 'proxy'}
                or value['operation'] not in {'search', 'prepare', 'read'}
                or type(value['timeout_seconds']) is not int or not 1 <= value['timeout_seconds'] <= 45
                or not isinstance(value['login'], dict) or set(value['login']) != {'username', 'password'}):
            raise ValueError('Private worker envelope refused')
        operation = value['operation']
        result = run(value['profile'], operation, value['parameters'], Login(**value['login']),
                     time.monotonic() + value['timeout_seconds'], proxy=value['proxy'])
    except BaseException:
        result = {'status': 'unavailable', 'operation': operation, 'results_count': None}
    # Values are fixed enums and a bounded integer, never browser-supplied text.
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
