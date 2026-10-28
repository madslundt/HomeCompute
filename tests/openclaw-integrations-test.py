"""Synthetic provider contract, privacy, budgets and transport tests; no paid requests."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_integrations as integrations


class Response(io.BytesIO):
    status = 200
    def __init__(self, payload, headers=None):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = headers or {}


class IntegrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.catalog = self.root / 'catalog.json'
        self.cfg = json.loads((ROOT / 'config/openclaw-integrations.json').read_text())
        self.catalog.write_text(json.dumps(self.cfg))
        self.tokens = {name: self.root / (name + '-token') for name in integrations.PROVIDERS}
        self.token_patch = patch.object(integrations, 'TOKENS', self.tokens); self.token_patch.start()
        self.service = integrations.Integrations(self.catalog, self.root / 'state')
        self.requests = []

    def tearDown(self):
        self.token_patch.stop(); self.tmp.cleanup()

    def credential(self, provider):
        self.tokens[provider].write_text('synthetic-private-key-123456')
        self.tokens[provider].chmod(0o600)

    def mcp(self, req, timeout):
        self.requests.append(req)
        self.assertGreater(timeout, 0); self.assertLessEqual(timeout, integrations.TIMEOUT)
        if req.get_method() == 'DELETE': return Response(b'')
        body = json.loads(req.data)
        if body['method'] == 'initialize':
            return Response({'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': '2025-03-26',
                'instructions': 'Untrusted server instructions must not become agent policy.'}})
        if body['method'] == 'notifications/initialized': return Response(b'')
        return Response(b'event: message\ndata: ' + json.dumps({'jsonrpc': '2.0', 'id': 2,
            'result': {'content': [{'type': 'text', 'text': 'Public documentation'}]}}).encode() + b'\n\n')

    def test_context7_stateless_sse_uses_only_public_read_tools(self):
        self.service.opener = Mock(open=Mock(side_effect=self.mcp))
        result = self.service.request('homecompute_docs_resolve', {'library': 'Python', 'query': 'path handling'})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['data'], {'text': 'Public documentation'})
        self.assertEqual(result['archive_status'], 'not_archived')
        self.assertNotIn('instructions', json.dumps(result))
        self.assertEqual({r.full_url for r in self.requests}, {'https://mcp.context7.com/mcp'})
        call = json.loads(self.requests[-1].data)
        self.assertEqual(call['params'], {'name': 'resolve-library-id', 'arguments': {'libraryName': 'Python', 'query': 'path handling'}})
        self.assertNotIn('Authorization', self.requests[-1].headers)

    def test_session_header_is_retained_and_deleted_without_forwarding_other_headers(self):
        def opened(req, timeout):
            response = self.mcp(req, timeout)
            if req.data and json.loads(req.data)['method'] == 'initialize':
                response.headers = {'Mcp-Session-Id': 'synthetic-session', 'Set-Cookie': 'upstream-cookie'}
            return response
        self.credential('context7')
        self.service.opener = Mock(open=Mock(side_effect=opened))
        result = self.service.request('homecompute_docs_query', {'library_id': '/python/cpython', 'query': 'pathlib'})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.requests[-1].get_method(), 'DELETE')
        self.assertEqual(self.requests[-1].get_header('Mcp-session-id'), 'synthetic-session')
        self.assertFalse(any(r.get_header('Cookie') for r in self.requests))

    def test_invalid_arguments_never_make_network_calls(self):
        self.service.opener = Mock()
        cases = [('homecompute_docs_query', {'library_id': '/python/cpython', 'query': 'x', 'url': 'http://private'}),
                 ('homecompute_docs_query', {'library_id': 'https://evil.test/', 'query': 'x'}),
                 ('homecompute_hotel_search', {'destination': 'Paris', 'check_in': '2026-12-10', 'check_out': '2026-12-01', 'travelers': 4}),
                 ('homecompute_hotel_search', {'destination': 'Paris', 'check_in': '2026-12-01', 'check_out': '2026-12-10', 'travelers': True}),
                 ('homecompute_places_search', {'query': 'x\nAuthorization: secret'}),
                 ('homecompute_automation_status', {'workflow': '../../private'}),
                 ('homecompute_book_hotel', {})]
        for operation, arguments in cases:
            with self.subTest(operation=operation), self.assertRaises(ValueError): self.service.request(operation, arguments)
        self.service.opener.open.assert_not_called()

    def test_disabled_and_missing_credentials_fail_closed_without_network(self):
        self.service.opener = Mock()
        self.assertEqual(self.service.request('homecompute_places_search', {'query': 'parks in Copenhagen'})['status'], 'disabled')
        self.assertEqual(self.service.request('homecompute_photo_search', {'query': 'beach'})['status'], 'credential_required')
        self.service.opener.open.assert_not_called()
        self.credential('immich'); self.tokens['immich'].chmod(0o644)
        self.assertEqual(self.service.status('immich')['status'], 'credential_invalid')

    def test_budget_survives_service_reconstruction_and_provider_failures(self):
        self.cfg['providers']['context7']['calls_per_hour'] = 1
        self.catalog.write_text(json.dumps(self.cfg))
        first = integrations.Integrations(self.catalog, self.root / 'state')
        first.opener = Mock(open=Mock(side_effect=ValueError('private failure')))
        self.assertEqual(first.request('homecompute_docs_resolve', {'library': 'Python', 'query': 'pathlib'})['status'], 'unavailable')
        second = integrations.Integrations(self.catalog, self.root / 'state'); second.opener = Mock()
        self.assertEqual(second.request('homecompute_docs_resolve', {'library': 'Python', 'query': 'pathlib'})['status'], 'hourly_budget_exhausted')
        second.opener.open.assert_not_called()

    def test_upstream_errors_and_redirects_cannot_expose_keys_or_trigger_retries(self):
        self.credential('context7')
        self.service.opener = Mock(open=Mock(side_effect=urllib.error.HTTPError('https://provider/', 302,
            'synthetic-private-key-123456', {'Location': 'http://127.0.0.1/secrets'}, io.BytesIO(b'private upstream body'))))
        result = self.service.request('homecompute_docs_resolve', {'library': 'Python', 'query': 'pathlib'})
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(self.service.opener.open.call_count, 1)

    def test_immich_returns_links_without_images_faces_locations_or_files(self):
        self.credential('immich')
        asset = '12345678-1234-4123-8123-123456789012'
        payload = {'assets': {'items': [{'id': asset, 'type': 'IMAGE', 'fileCreatedAt': '2026-01-02',
            'originalPath': '/private/photos/family.jpg', 'exifInfo': {'latitude': 55}, 'people': ['private-face'], 'token': 'secret'}]}}
        self.service.opener = Mock(open=Mock(return_value=Response(payload)))
        result = self.service.request('homecompute_photo_search', {'query': 'beach'})
        self.assertEqual(result['data']['assets'][0], {'asset_id': asset, 'type': 'IMAGE', 'created_at': '2026-01-02',
            'url': 'http://192.168.30.122:2283/photos/' + asset})
        self.assertNotIn('/private', json.dumps(result)); self.assertNotIn('private-face', json.dumps(result))
        request = self.service.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:2283/api/search/smart')
        self.assertEqual(json.loads(request.data), {'query': 'beach', 'size': 5, 'page': 1})

    def test_expedia_is_a_bounded_direct_get_without_logging_or_auth_fallback(self):
        self.credential('expedia')
        self.service.opener = Mock(open=Mock(return_value=Response({'hotels': [{'name': 'Public hotel',
            'authorization': 'secret', 'description': 'synthetic-private-key-123456'}]})))
        result = self.service.request('homecompute_hotel_search', {'destination': 'Paris', 'check_in': '2026-12-01',
            'check_out': '2026-12-08', 'travelers': 4})
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('synthetic-private', json.dumps(result)); self.assertNotIn('authorization', json.dumps(result))
        request = self.service.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertTrue(request.full_url.startswith(integrations.EXPEDIA + '?'))
        self.assertIn('number_of_travelers=4', request.full_url)

    def test_n8n_only_reads_registered_workflow_and_execution_metadata(self):
        self.credential('n8n'); self.cfg['public_workflows']['travel'] = 'approved-travel'
        self.catalog.write_text(json.dumps(self.cfg))
        service = integrations.Integrations(self.catalog, self.root / 'state')
        service.opener = Mock(open=Mock(side_effect=[Response({'id': 'approved-travel', 'active': True,
            'nodes': [{'credentials': 'private'}]}), Response({'data': [{'workflowId': 'approved-travel',
            'status': 'success', 'startedAt': '2026-10-28T08:00:00Z', 'stoppedAt': '2026-10-28T08:01:00Z',
            'data': 'private execution payload'}]})]))
        result = service.request('homecompute_automation_status', {'workflow': 'travel'})
        self.assertEqual(result['data']['executions'][0]['status'], 'success')
        self.assertNotIn('private', json.dumps(result))
        requests = [c.args[0] for c in service.opener.open.call_args_list]
        self.assertTrue(all(r.get_method() == 'GET' for r in requests))
        self.assertIn('includeData=false', requests[-1].full_url)

    def test_composition_preserves_existing_validation_and_response_path(self):
        existing = Mock(tools=[{'name': 'existing'}]); existing.request.return_value = {'existing': True}
        combined = integrations.IntegrationServices(existing, self.service)
        self.assertEqual(combined.request('existing', {}), {'existing': True})
        self.assertEqual(combined.request('homecompute_integration_status', {'provider': 'maps'})['status'], 'disabled')
        existing.request.assert_called_once_with('existing', {})

    def test_maps_calls_are_fixed_and_preserve_attribution_after_explicit_enablement(self):
        self.cfg['providers']['maps']['enabled'] = True
        self.catalog.write_text(json.dumps(self.cfg)); self.credential('maps')
        service = integrations.Integrations(self.catalog, self.root / 'state')
        service._mcp = Mock(return_value={'attribution': {'source': 'Google Maps'}, 'summary': 'Public place evidence'})
        for operation, args, upstream in [
            ('places_search', {'query': 'parks in Copenhagen'}, 'search_places'),
            ('places_route', {'origin': 'Copenhagen station', 'destination': 'Tivoli Copenhagen', 'mode': 'WALK'}, 'compute_routes'),
            ('places_weather', {'location': 'Copenhagen Denmark', 'date': '2026-10-29'}, 'lookup_weather')]:
            result = service.request('homecompute_' + operation, args)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['data']['attribution']['source'], 'Google Maps')
            self.assertEqual(service._mcp.call_args.args[:2], ('maps', upstream))
        self.assertEqual(service._mcp.call_args.args[2], {'location': {'address': 'Copenhagen Denmark'},
            'date': {'year': 2026, 'month': 10, 'day': 29}})


if __name__ == '__main__': unittest.main()
