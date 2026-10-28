"""Optional local preference retrieval cannot override exact travel eligibility."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('travel_embedding_fixtures', Path(__file__).with_name('travel-workflow-test.py'))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
sys.path.insert(0, str(ROOT / 'scripts'))
from travel_workflow import build, sdk


def rows():
    request = FIXTURE.request()
    request['preferences']['preferenceQuery'] = 'Roligt børnevenligt hotel ved stranden'
    first = FIXTURE.offer()
    second = copy.deepcopy(first)
    second['id'] = 'quiet-offer'; second['hotel'].update(id='102', name='Quiet family hotel', description='Quiet sandy beach and a children pool')
    second['total_price'] = 7000
    return [{'json': FIXTURE.response([first, second], request)}]


def prepare(source):
    return FIXTURE.run_node('prepare-preference-search.js', source)['value']


def apply(source, prepared, response):
    return FIXTURE.run_node('apply-preference-search.js', [{'json': response}], {
        'Add all-inclusive price': source, 'Prepare hotel preference search': prepared})['value']


def embedding(*vectors):
    return {'data': [{'index': i, 'embedding': v + [0.0] * (1024-len(v))} for i, v in enumerate(vectors)]}


class TravelEmbeddingTests(unittest.TestCase):
    def test_query_batch_contains_only_bounded_public_text_and_no_family_dates(self):
        source = rows(); source[0]['json']['offers'][0]['hotel']['description'] = 'ø' * 3000
        prepared = prepare(source)[0]['json']
        self.assertTrue(prepared['embeddingEnabled'])
        body = prepared['embeddingBody']
        self.assertEqual(body['model'], 'embedding'); self.assertEqual(len(body['input']), 3)
        self.assertTrue(body['input'][0].startswith('Instruct:'))
        self.assertTrue(all(len(text.encode()) < 1800 for text in body['input']))
        for birthday in source[0]['json']['request']['preferences']['childrenBirthDates']:
            self.assertNotIn(birthday, str(body))
        self.assertNotIn('ageString', str(body)); self.assertNotIn('providerUrl', str(body))

    def test_valid_multilingual_preference_vectors_rank_eligible_hotels(self):
        source = rows(); prepared = prepare(source)
        enriched = apply(source, prepared, embedding([1.0, 0.0], [0.0, 1.0], [1.0, 0.0]))
        deals = FIXTURE.select(raw=enriched[0]['json'])['value'][0]['json']['deals']
        self.assertEqual([d['hotelId'] for d in deals], ['102', '101'])
        self.assertEqual([d['total'] for d in deals], [7000, 6400])
        self.assertEqual(deals[0]['ages'], [9, 4])

    def test_invalid_or_unavailable_vectors_keep_original_price_order(self):
        source = rows(); prepared = prepare(source)
        bad_index = embedding([1.0], [1.0], [1.0]); bad_index['data'][2]['index'] = 1
        bad_dimension = embedding([1.0], [1.0], [1.0]); bad_dimension['data'][1]['embedding'].pop()
        for payload in [{'error': 'offline'}, {'data': []}, bad_index, bad_dimension, embedding([0.0], [1.0], [1.0])]:
            with self.subTest(payload_type=list(payload)):
                enriched = apply(source, prepared, payload)
                deals = FIXTURE.select(raw=enriched[0]['json'])['value'][0]['json']['deals']
                self.assertEqual([d['total'] for d in deals], [6400, 7000])
                self.assertNotIn('preferenceScore', deals[0])

    def test_empty_query_disables_request_and_discards_upstream_scores(self):
        source = rows(); source[0]['json']['request']['preferences']['preferenceQuery'] = ''
        source[0]['json']['offers'][0]['preferenceScore'] = 999
        prepared = prepare(source)
        self.assertFalse(prepared[0]['json']['embeddingEnabled'])
        self.assertEqual(prepared[0]['json']['embeddingBody']['input'], [])
        enriched = apply(source, prepared, prepared[0]['json'])
        self.assertNotIn('preferenceScore', enriched[0]['json']['offers'][0])

    def test_exact_identity_mismatch_cannot_inherit_semantic_score(self):
        source = rows(); prepared = prepare(source)
        source[0]['json']['offers'][1]['id'] = 'replacement-offer'
        enriched = apply(source, prepared, embedding([1.0], [0.0, 1.0], [1.0]))
        self.assertNotIn('preferenceScore', enriched[0]['json']['offers'][1])
        deals = FIXTURE.select(raw=enriched[0]['json'])['value'][0]['json']['deals']
        self.assertEqual([d['total'] for d in deals], [6400, 7000])

    def test_preference_rank_never_overrides_price_age_weather_or_identity_gates(self):
        for mutation in [lambda o: o.update(total_price=10001), lambda o: o.update(weather={'verified': False}),
                         lambda o: o['hotel'].update(url='/hotel/102?age=42-42-8-4')]:
            source = rows(); mutation(source[0]['json']['offers'][1]); prepared = prepare(source)
            enriched = apply(source, prepared, embedding([1.0], [0.0, 1.0], [1.0]))
            deals = FIXTURE.select(raw=enriched[0]['json'])['value'][0]['json']['deals']
            self.assertEqual([d['hotelId'] for d in deals], ['101'])

    def test_generated_workflow_has_bounded_fixed_http_and_disabled_branch(self):
        workflow = build(); nodes = {n['name']: n for n in workflow['nodes']}
        http = nodes['Embed hotel preferences']
        self.assertEqual(http['parameters']['url'], 'https://ai.home.arpa/v1/embeddings')
        self.assertEqual(http['parameters']['options']['timeout'], 8000)
        self.assertFalse(http['retryOnFail']); self.assertEqual(http['onError'], 'continueRegularOutput')
        branches = workflow['connections']['Use hotel preferences']['main']
        self.assertEqual(branches[0][0]['node'], 'Embed hotel preferences')
        self.assertEqual(branches[1][0]['node'], 'Apply hotel preferences')
        text = sdk(workflow)
        self.assertIn('.output(1).to(', text)


if __name__ == '__main__': unittest.main()
