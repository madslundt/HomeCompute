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
        self.cfg['providers']['maps']['enabled'] = False
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
                 ('homecompute_hotel_search', {'destination': 'Paris', 'check_in': '2026-12-10', 'check_out': '2026-12-01', 'adults': 2, 'child_ages': [], 'currency': 'DKK'}),
                 ('homecompute_hotel_search', {'destination': 'Paris', 'check_in': '2026-12-01', 'check_out': '2026-12-10', 'adults': True, 'child_ages': [], 'currency': 'DKK'}),
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
        self.assertEqual(self.service.request('homecompute_hotel_search', self.hotel_arguments())['status'], 'credential_required')
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

    @staticmethod
    def hotel_arguments(**changes):
        return {'destination': 'Paris', 'check_in': '2026-12-01', 'check_out': '2026-12-08',
                'adults': 2, 'child_ages': [0, 7], 'currency': 'DKK', **changes}

    @staticmethod
    def hotel_payload(**changes):
        return {'search_metadata': {'status': 'Success', 'processed_at': '2026-10-28 10:00:00 UTC'},
                'search_parameters': {'engine': 'google_hotels', 'q': 'Paris', 'check_in_date': '2026-12-01',
                    'check_out_date': '2026-12-08', 'adults': 2, 'children': 2, 'children_ages': '1,7',
                    'currency': 'DKK', 'gl': 'dk', 'hl': 'en'},
                'properties': [{'name': 'Public hotel', 'link': 'https://hotel.example/',
                    'description': 'synthetic-private-key-123456',
                    'rate_per_night': {'display_price': 'DKK 900', 'extracted_display_price': 900,
                                       'after_taxes_fees': 'DKK 1000', 'extracted_after_taxes_fees': 1000},
                    'total_rate': {'display_price': 'DKK 6300', 'extracted_display_price': 6300,
                                   'after_taxes_fees': 'DKK 7000', 'extracted_after_taxes_fees': 7000},
                    'serpapi_property_details_link': 'https://serpapi.com/search?api_key=private',
                    'images': ['private'], 'instructions': 'private'}] * 8, **changes}

    def test_serpapi_is_one_fixed_get_with_explicit_occupancy_and_separate_tax_labels(self):
        import openclaw_hotels
        self.credential('hotels')
        self.service.opener = Mock(open=Mock(return_value=Response(self.hotel_payload())))
        result = self.service.request('homecompute_hotel_search', self.hotel_arguments())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.service.status('hotels')['backend'], 'serpapi-google-hotels')
        self.assertEqual(len(result['data']['hotels']), 5)
        row = result['data']['hotels'][0]
        self.assertEqual(row['rate_per_night']['extracted_display_price'], 900)
        self.assertEqual(row['total_rate']['extracted_after_taxes_fees'], 7000)
        self.assertEqual(row['price_status'], 'advertised_lead_unconfirmed')
        self.assertEqual(row['url'], 'https://hotel.example/')
        self.assertEqual(result['data']['search']['child_ages'], [0, 7])
        self.assertEqual(result['data']['search']['submitted_child_ages'], [1, 7])
        for excluded in ('synthetic-private', 'instructions', 'images', 'serpapi_property_details_link'):
            self.assertNotIn(excluded, json.dumps(result))
        request = self.service.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.headers, {'Content-type': 'application/json'})
        self.assertTrue(request.full_url.startswith(openclaw_hotels.ENDPOINT + '?'))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(query['children_ages'], ['1,7']); self.assertEqual(query['children'], ['2'])
        self.assertEqual(query['currency'], ['DKK']); self.assertEqual(query['adults'], ['2'])
        self.assertNotIn('no_cache', query); self.assertNotIn('next_page_token', query)
        self.assertEqual(self.service.opener.open.call_count, 1)

    def test_hotel_occupancy_is_required_and_invalid_ages_fail_before_network(self):
        self.service.opener = Mock()
        invalid = [self.hotel_arguments(child_ages=v) for v in ([-1], [18], [True], ['7'], '7', [7] * 7)]
        invalid += [self.hotel_arguments(adults=0), self.hotel_arguments(currency='XXX')]
        missing = self.hotel_arguments(); del missing['child_ages']; invalid.append(missing)
        invalid.append({'destination': 'Paris', 'check_in': '2026-12-01', 'check_out': '2026-12-08', 'travelers': 4})
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.service.request('homecompute_hotel_search', args)
        self.service.opener.open.assert_not_called()

    def test_hotel_errors_mismatched_stays_and_malformed_rows_are_unavailable(self):
        self.credential('hotels')
        samples = [self.hotel_payload(error='synthetic-private-key-123456'),
                   self.hotel_payload(search_metadata={'status': 'Processing'}),
                   self.hotel_payload(properties=None), self.hotel_payload(properties=[None])]
        for key, value in [('check_out_date', '2026-12-09'), ('adults', 4), ('adults', True),
                           ('children_ages', '7,7'), ('currency', 'USD'), ('q', 'London')]:
            payload = self.hotel_payload(); payload['search_parameters'][key] = value; samples.append(payload)
        for payload in samples:
            with self.subTest(payload=payload):
                self.service.opener = Mock(open=Mock(return_value=Response(payload)))
                result = self.service.request('homecompute_hotel_search', self.hotel_arguments())
                self.assertEqual(result['status'], 'unavailable')
                self.assertNotIn('synthetic-private', json.dumps(result))
                self.assertEqual(self.service.opener.open.call_count, 1)

    def test_empty_hotel_page_is_partial_evidence_and_unsafe_links_are_not_returned(self):
        self.credential('hotels')
        payload = self.hotel_payload(properties=[])
        payload['search_parameters']['children'] = 0; del payload['search_parameters']['children_ages']
        self.service.opener = Mock(open=Mock(return_value=Response(payload)))
        result = self.service.request('homecompute_hotel_search', self.hotel_arguments(child_ages=[]))
        self.assertEqual(result['status'], 'completed'); self.assertEqual(result['data']['hotels'], [])
        self.assertIn('partial', result['data']['completeness'])
        import openclaw_hotels
        for url in ('javascript:alert(1)', 'http://hotel.example/', 'https://serpapi.com/search?api_key=private',
                    'https://user:password@hotel.example/', 'https://hotel.example/\nprivate'):
            self.assertIsNone(openclaw_hotels.link(url))
        self.assertIsNone(openclaw_hotels.rate({'extracted_display_price': float('nan')}))

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

    def places_service(self, *responses):
        self.cfg['providers']['maps']['enabled'] = True
        self.catalog.write_text(json.dumps(self.cfg)); self.credential('maps')
        service = integrations.Integrations(self.catalog, self.root / 'state')
        service._places_http = Mock(side_effect=[(json.dumps(row).encode(), {}) for row in responses])
        return service

    @staticmethod
    def city(lat=55.67, lon=12.56, name='Copenhagen, Denmark'):
        return {'lat': lat, 'lon': lon, 'formatted': name, 'rank': {'confidence': 1.0}}

    def test_geoapify_discovery_is_bounded_and_preserves_source_attribution(self):
        service = self.places_service({'results': [self.city()]}, {'features': [
            {'properties': {**self.city(), 'name': 'Public restaurant', 'cookie': 'private'}}]})
        result = service.request('homecompute_places_search', {'query': 'Copenhagen, Denmark', 'category': 'restaurants'})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['data']['places'][0]['name'], 'Public restaurant')
        self.assertEqual([a['name'] for a in result['data']['attribution']], ['Geoapify', 'OpenStreetMap contributors'])
        urls = [call.args[0] for call in service._places_http.call_args_list]
        self.assertTrue(urls[0].startswith('https://api.geoapify.com/v1/geocode/search?'))
        self.assertTrue(urls[1].startswith('https://api.geoapify.com/v2/places?'))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[1]).query)
        self.assertEqual(query['categories'], ['catering.restaurant'])
        self.assertEqual(query['filter'], ['circle:12.56,55.67,5000'])
        self.assertEqual(query['limit'], ['5'])
        self.assertNotIn('private', json.dumps(result))

    def test_geoapify_route_returns_only_estimates_and_resolved_locations(self):
        service = self.places_service({'results': [self.city()]}, {'results': [self.city(55.7, 12.6, 'Public destination')]},
            {'features': [{'properties': {'distance': 1500, 'time': 900}, 'geometry': 'omitted'}]})
        result = service.request('homecompute_places_route', {'origin': 'Copenhagen, Denmark', 'destination': 'Public destination', 'mode': 'WALK'})
        self.assertEqual(result['data']['duration_seconds'], 900)
        self.assertEqual(result['data']['distance_metres'], 1500)
        self.assertNotIn('geometry', json.dumps(result))
        url = service._places_http.call_args.args[0]
        self.assertIn('/v1/routing?', url)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['mode'], ['walk'])

    def test_open_meteo_never_receives_the_geoapify_key_and_checks_requested_date(self):
        weather = {'timezone': 'Europe/Copenhagen', 'daily_units': {'temperature_2m_max': '°C'},
                   'daily': {'time': ['2026-10-29'], 'temperature_2m_max': [12]}}
        service = self.places_service({'results': [self.city()]}, weather)
        result = service.request('homecompute_places_weather', {'location': 'Copenhagen, Denmark', 'date': '2026-10-29'})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['data']['weather']['daily']['time'], ['2026-10-29'])
        url, body, headers, deadline = service._places_http.call_args.args
        self.assertTrue(url.startswith('https://api.open-meteo.com/v1/forecast?'))
        self.assertNotIn('apiKey', url); self.assertNotIn('synthetic-private', url)
        self.assertEqual(headers, {}); self.assertIsNone(body)
        self.assertEqual(result['data']['attribution'][-1]['name'], 'Open-Meteo')

    def test_ambiguous_geocoding_requires_clarification_before_route_or_poi_calls(self):
        service = self.places_service({'results': [self.city(51.5, -0.12, 'London, UK'), self.city(42.98, -81.25, 'London, Canada')]})
        result = service.request('homecompute_places_route', {'origin': 'London', 'destination': 'Public destination', 'mode': 'DRIVE'})
        self.assertEqual(result['status'], 'clarification_required')
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(service._places_http.call_count, 1)

    def test_malformed_coordinates_cannot_reach_a_followup_provider(self):
        service = self.places_service({'results': [self.city(float('nan'), 12)]})
        result = service.request('homecompute_places_search', {'query': 'public city', 'category': 'parks'})
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(service._places_http.call_count, 1)

    def test_catalog_refuses_other_maps_backends_and_redacts_url_encoded_keys(self):
        for provider, backend in [('maps', 'google'), ('hotels', 'expedia')]:
            cfg = json.loads(json.dumps(self.cfg)); cfg['providers'][provider]['backend'] = backend
            self.catalog.write_text(json.dumps(cfg))
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                integrations.Integrations(self.catalog, self.root / 'other-state')
        token = 'synthetic+private/key'
        payload = integrations.evidence({'text': 'url?apiKey=' + urllib.parse.quote_plus(token)}, token)
        self.assertNotIn('private', json.dumps(payload))



if __name__ == '__main__': unittest.main()
