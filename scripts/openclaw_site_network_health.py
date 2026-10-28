"""Bounded owner-only network metadata. No URLs, values, headers or messages."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit

from openclaw_site_registry import account_name

MAX_RECORDS, MAX_BYTES, MAX_COUNT = 64, 8192, 1000
STATUSES = frozenset({'human_sign_in_required', 'ready', 'expired', 'unavailable'})
CATEGORIES = frozenset({'document', 'stylesheet', 'image', 'media', 'font', 'script', 'texttrack',
                        'xhr', 'fetch', 'eventsource', 'websocket', 'manifest', 'other'})
MIMES = frozenset({'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/svg+xml', 'image/avif',
                  'image/apng', 'image/bmp', 'image/tiff', 'image/x-icon', 'image/vnd.microsoft.icon', 'other'})
ERRORS = frozenset({'ERR_ABORTED', 'ERR_ADDRESS_UNREACHABLE', 'ERR_BLOCKED_BY_CLIENT',
    'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_COMMON_NAME_INVALID', 'ERR_CERT_DATE_INVALID',
    'ERR_CERT_INVALID', 'ERR_CERT_REVOKED', 'ERR_CONNECTION_ABORTED', 'ERR_CONNECTION_CLOSED',
    'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_TIMED_OUT', 'ERR_EMPTY_RESPONSE',
    'ERR_HTTP2_PROTOCOL_ERROR', 'ERR_HTTP2_SERVER_REFUSED_STREAM',
    'ERR_FAILED', 'ERR_HTTP_RESPONSE_CODE_FAILURE', 'ERR_INTERNET_DISCONNECTED', 'ERR_NAME_NOT_RESOLVED',
    'ERR_NETWORK_CHANGED', 'ERR_PROXY_CONNECTION_FAILED', 'ERR_SSL_PROTOCOL_ERROR',
    'ERR_SSL_VERSION_OR_CIPHER_MISMATCH', 'ERR_TIMED_OUT', 'ERR_TUNNEL_CONNECTION_FAILED', 'ERR_OTHER'})
FIELDS = frozenset({'domain', 'category', 'status', 'mime', 'error', 'count'})


def safe_domain(url: str, known_domains: frozenset[str]) -> str:
    try:
        host = account_name(urlsplit(url).hostname)
        return host if host in known_domains else '.'.join(host.split('.')[-2:])
    except Exception:
        return 'other'


def error_code(value: object) -> str:
    if isinstance(value, str):
        match = re.search(r'\b(ERR_[A-Z0-9_]{1,64})\b', value[:256])
        if match and match[1] in ERRORS:
            return match[1]
    return 'ERR_OTHER'


def validate_report(value: object, *, known_domains=()) -> dict:
    """Revalidate the private worker boundary without reflecting rejected data."""
    if (not isinstance(value, dict) or set(value) != {'status', 'resources'}
            or not isinstance(value['status'], str) or value['status'] not in STATUSES
            or not isinstance(value['resources'], list) or len(value['resources']) > MAX_RECORDS):
        raise ValueError('Private resource report refused')
    if value['status'] in {'expired', 'unavailable'} and value['resources']:
        raise ValueError('Inactive private resource report refused')
    trusted = frozenset(account_name(host) for host in known_domains)
    for record in value['resources']:
        if not isinstance(record, dict) or set(record) != FIELDS:
            raise ValueError('Private resource metadata refused')
        domain = record['domain']
        if domain != 'other':
            account_name(domain)
            if domain not in trusted and len(domain.split('.')) != 2:
                raise ValueError('Private resource domain refused')
        if (not isinstance(record['category'], str) or record['category'] not in CATEGORIES
                or record['status'] is not None and (type(record['status']) is not int or not 100 <= record['status'] <= 599)
                or not isinstance(record['mime'], str) or record['mime'] not in MIMES
                or record['error'] is not None and (not isinstance(record['error'], str) or record['error'] not in ERRORS)
                or type(record['count']) is not int or not 1 <= record['count'] <= MAX_COUNT):
            raise ValueError('Private resource metadata refused')
        if (record['status'] is None) == (record['error'] is None):
            raise ValueError('Private resource outcome refused')
    if len(json.dumps(value, separators=(',', ':')).encode()) > MAX_BYTES:
        raise ValueError('Private resource budget refused')
    return copy.deepcopy(value)


class NetworkHealth:
    def __init__(self, known_domains):
        self.known_domains = frozenset(account_name(host) for host in known_domains)
        self.records = {}

    def clear(self):
        self.records.clear()

    def add(self, url, category, status, mime, error):
        domain = safe_domain(url, self.known_domains)
        category = category if category in CATEGORIES else 'other'
        key = (domain, category, status, mime, error)
        previous = self.records.get(key)
        if previous is None and len(self.records) >= MAX_RECORDS:
            return
        record = {'domain': domain, 'category': category, 'status': status, 'mime': mime, 'error': error,
                  'count': min(MAX_COUNT, previous['count'] + 1) if previous else 1}
        self.records[key] = record
        try:
            validate_report({'status': 'human_sign_in_required', 'resources': list(self.records.values())},
                            known_domains=self.known_domains)
        except Exception:
            if previous is None:
                del self.records[key]
            else:
                self.records[key] = previous

    def response(self, response):
        try:
            status, request = response.status, response.request
            if type(status) is not int or not 100 <= status <= 599:
                return
            category = request.resource_type
            if category != 'image' and status < 400:
                return
            mime = 'other'
            if category == 'image':
                # Only the fixed MIME category survives this transient read.
                candidate = response.header_value('content-type')
                if isinstance(candidate, str):
                    candidate = candidate[:128].split(';', 1)[0].strip().lower()
                    mime = candidate if candidate in MIMES else 'other'
            self.add(request.url, category, status, mime, None)
        except Exception:
            return

    def failed(self, request):
        try:
            self.add(request.url, request.resource_type, None, 'other', error_code(request.failure))
        except Exception:
            return

    def report(self, status: str) -> dict:
        return validate_report({'status': status, 'resources': list(self.records.values())},
                               known_domains=self.known_domains)
