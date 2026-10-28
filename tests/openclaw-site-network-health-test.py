#!/usr/bin/env python3
"""Synthetic privacy/projection budgets; no browser, credentials or network."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_network_health as health

PASSWORD, EMAIL, COOKIE = 'synthetic-vault-password', 'synthetic-owner@example.invalid', 'synthetic-cookie-value'
PRIVATE_URL = 'https://user:' + PASSWORD + '@' + PASSWORD + '.cdn.example.com/private/' + PASSWORD + '?email=' + EMAIL + '&cookie=' + COOKIE


class Response:
    def __init__(self, status=202, category='image', mime='image/png; private=' + PASSWORD, url=PRIVATE_URL):
        self.status, self.mime = status, mime
        self.request = SimpleNamespace(url=url, resource_type=category)
    def header_value(self, name):
        if name != 'content-type': raise AssertionError('Only MIME metadata is allowed')
        return self.mime
    @property
    def headers(self): raise AssertionError('Raw headers must never be read')
    def body(self): raise AssertionError('Private image body must never be read')
    def text(self): raise AssertionError('Private response text must never be read')


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.health = health.NetworkHealth(['da.hotels.com'])

    def report(self):
        return self.health.report('human_sign_in_required')

    def test_image_202_is_recorded_without_path_query_userinfo_subdomain_or_headers(self):
        self.health.response(Response())
        report = self.report()
        self.assertEqual(report, {'status': 'human_sign_in_required', 'resources': [
            {'domain': 'example.com', 'category': 'image', 'status': 202, 'mime': 'image/png', 'error': None, 'count': 1}]})
        stored = json.dumps(report) + repr(self.health.__dict__)
        for private in [PASSWORD, EMAIL, COOKIE, '/private/', 'https://', 'userinfo', 'content-type']:
            self.assertNotIn(private, stored)

    def test_only_images_http_errors_and_failed_requests_are_captured(self):
        self.health.response(Response(200, 'script'))
        self.health.response(Response(202, 'image', PASSWORD))
        self.health.response(Response(403, 'script'))
        self.health.failed(SimpleNamespace(url=PRIVATE_URL, resource_type='xhr',
            failure='net::ERR_CERT_AUTHORITY_INVALID private ' + PASSWORD + ' ' + PRIVATE_URL))
        values = self.report()['resources']
        self.assertEqual(len(values), 3)
        self.assertEqual(values[0]['mime'], 'other')
        self.assertEqual(values[1]['status'], 403)
        self.assertEqual(values[2]['error'], 'ERR_CERT_AUTHORITY_INVALID')
        self.assertNotIn(PASSWORD, json.dumps(values))

    def test_exact_profile_hosts_remain_and_unknown_hosts_are_reduced_to_suffix_only(self):
        self.health.response(Response(url='https://da.hotels.com/' + PASSWORD))
        self.health.response(Response(url='https://' + PASSWORD + '.s.go-mpulse.net/?' + PASSWORD))
        self.assertEqual([record['domain'] for record in self.report()['resources']], ['da.hotels.com', 'go-mpulse.net'])
        self.assertEqual(health.safe_domain('https://127.0.0.1/private', frozenset()), 'other')
        self.assertEqual(health.safe_domain('data:image/png;' + PASSWORD, frozenset()), 'other')

    def test_errors_are_fixed_enums_including_actual_http2_failures(self):
        for code in ['ERR_HTTP2_PROTOCOL_ERROR', 'ERR_HTTP2_SERVER_REFUSED_STREAM', 'ERR_NAME_NOT_RESOLVED']:
            self.assertEqual(health.error_code('net::' + code + ' private ' + PASSWORD), code)
        for value in [PASSWORD, None, {'cookie': COOKIE}, 'net::ERR_' + PASSWORD]:
            self.assertEqual(health.error_code(value), 'ERR_OTHER')

    def test_duplicate_counts_and_snapshot_copies_are_bounded(self):
        for _ in range(health.MAX_COUNT + 10): self.health.response(Response())
        report = self.report()
        self.assertEqual(len(report['resources']), 1); self.assertEqual(report['resources'][0]['count'], health.MAX_COUNT)
        report['resources'][0]['count'] = 0
        self.assertEqual(self.report()['resources'][0]['count'], health.MAX_COUNT)
        self.health.clear(); self.assertEqual(self.report()['resources'], [])

    def test_record_count_and_serialized_byte_budgets_are_bounded(self):
        for index in range(200):
            self.health.response(Response(url=f'https://cdn.site{index}.example/image'))
        report = self.report()
        self.assertGreater(len(report['resources']), 0)
        self.assertLessEqual(len(report['resources']), health.MAX_RECORDS)
        self.assertLessEqual(len(json.dumps(report, separators=(',', ':')).encode()), health.MAX_BYTES)

    def test_strict_parent_validator_rejects_smuggling_types_private_subdomains_and_inactive_data(self):
        self.health.response(Response())
        safe = self.report()
        for changes in [{'url': PRIVATE_URL}, {'status': True}, {'count': True}, {'count': 1001},
                        {'status': 600}, {'mime': PASSWORD}, {'error': PASSWORD}, {'domain': PASSWORD + '.example.com'},
                        {'domain': '127.0.0.1'}, {'category': PASSWORD}, {'status': None}]:
            value = copy.deepcopy(safe); value['resources'][0].update(changes)
            with self.subTest(changes=tuple(changes)), self.assertRaises(Exception): health.validate_report(value)
        for value in [dict(safe, cookies=COOKIE), dict(safe, status='expired'), dict(safe, status='unavailable'),
                      dict(safe, resources=safe['resources'] * 65), {'status': PASSWORD, 'resources': []}]:
            with self.assertRaises(Exception): health.validate_report(value)
        self.assertEqual(health.validate_report({'status': 'expired', 'resources': []}), {'status': 'expired', 'resources': []})
        self.health.clear(); self.health.response(Response(url='https://da.hotels.com/'))
        with self.assertRaises(Exception): health.validate_report(self.report())
        self.assertEqual(health.validate_report(self.report(), known_domains=['da.hotels.com']), self.report())


if __name__ == '__main__':
    unittest.main()
