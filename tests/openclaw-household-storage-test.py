"""Stock adapter protocol fixtures; never change real inventory."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_household_storage import Storage
from semantic_retrieval import SemanticRanker

LOCATION = 'a' * 32
ITEM = 'b' * 32


def snapshot():
    return {'revision': 10, 'locations': [{'id': LOCATION, 'name': 'Freezer', 'revision': 1}],
            'items': [{'id': ITEM, 'location_id': LOCATION, 'name': 'Peas', 'quantity': '1.25', 'unit': 'kg',
                       'revision': 3, 'barcode': '0123456789012', 'barcodes': ['12345678'],
                       'package_size': '500g bags', 'category': 'Frost', 'expiry_date': '2027-01-01',
                       'expiry_kind': 'best_before', 'aliases': ['Frozen peas']}],
            'barcode_products': {'private': {'product': {'unneeded_data': 'SECRET'}}}}


class Fake:
    def __init__(self):
        self.data = snapshot(); self.commands = []; self.reads = 0
        self.fail_after_write = False; self.concurrent_change = False

    def snapshot(self):
        self.reads += 1
        return deepcopy(self.data)

    def call(self, value):
        self.commands.append(deepcopy(value)); command = value['command']; action = command['action']
        if action == 'item_remove': self.data['items'] = [i for i in self.data['items'] if i['id'] != command['item_id']]
        elif action == 'item_add':
            self.data['items'].append({'id': 'c' * 32, 'revision': 0,
                **{k: v for k, v in command.items() if k not in {'action', 'request_id'}}})
        else:
            target = next(i for i in self.data['items'] if i['id'] == command['item_id'])
            target.update({k: v for k, v in command.items() if k not in {'action', 'request_id', 'item_id', 'expected_revision'}})
            target['revision'] += 1
            if self.concurrent_change: target['revision'] += 1
        self.data['revision'] += 1
        if self.fail_after_write: raise OSError('PRIVATE_HA_TOKEN')
        return {'snapshot': deepcopy(self.data)}


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.client = Fake(); self.ranker = Mock()
        self.ranker.rank.side_effect = lambda query, rows, **kwargs: [r for r in rows if query.casefold() in r['text'].casefold()]
        self.storage = Storage(self.client, ranker=self.ranker); self.fences = []

    def test_semantic_query_respects_location_scope_and_exact_stock_revision(self):
        other = 'd' * 32
        self.client.data['locations'].append({'id': other, 'name': 'Pantry', 'revision': 0})
        self.client.data['items'].append({**self.client.data['items'][0], 'id': 'e' * 32, 'location_id': other})
        def ranked(query, rows, **kwargs):
            self.assertEqual(query, 'grønne ærter'); self.assertEqual(kwargs['task'], 'storage')
            self.assertEqual([r['id'] for r in rows], [ITEM])
            self.assertIn('Frozen peas', rows[0]['text'])
            return [{**rows[0], 'retrieval_score': 0.8, 'lexical_score': 0, 'semantic_score': 0.9}]
        self.ranker.rank.side_effect = ranked
        row = self.request('storage_list', {'query': 'grønne ærter', 'location_id': LOCATION})['items'][0]
        self.assertEqual(row['id'], ITEM); self.assertEqual(row['quantity'], '1.25'); self.assertEqual(row['revision'], 3)
        self.client.data['items'][0]['revision'] += 1
        with self.assertRaises(ValueError):
            self.request('storage_adjust', {'item_id': row['id'], 'expected_revision': row['revision'], 'delta': '-0.25', 'request_id': str(uuid4())})
        self.assertEqual(self.client.commands, []); self.assertEqual(self.fences, [])

    def test_embedding_outage_keeps_keyword_discovery_available(self):
        embedding = Mock(); embedding.embed.side_effect = OSError('offline')
        storage = Storage(self.client, ranker=SemanticRanker(client=embedding))
        row = storage.read({'query': 'Peas'})['items'][0]
        self.assertEqual(row['id'], ITEM); self.assertIsNone(row['semantic_score'])
        self.assertEqual(self.client.commands, [])

    def target(self):
        return {'item_id': ITEM, 'expected_revision': self.client.data['items'][0]['revision'], 'request_id': str(uuid4())}

    def request(self, operation, args):
        return self.storage.request('homecompute_' + operation, args,
                                    lambda: self.fences.append((self.client.reads, len(self.client.commands))))

    def test_catalog_projection_has_exact_revision_and_no_barcode_products(self):
        result = self.request('storage_list', {'query': 'Peas'})
        self.assertEqual(result['items'][0]['revision'], 3)
        self.assertEqual(result['items'][0]['quantity'], '1.25')
        self.assertEqual(result['locations'][0]['id'], LOCATION)
        self.assertNotIn('SECRET', json.dumps(result)); self.assertNotIn('aliases', json.dumps(result))
        self.assertEqual(self.client.commands, []); self.assertEqual(self.fences, [])

    def test_partial_update_preserves_existing_metadata_and_expiry(self):
        result = self.request('storage_update', {**self.target(), 'name': 'Garden peas'})
        self.assertEqual(result['outcome'], 'confirmed'); self.assertEqual(self.fences, [(1, 0)])
        command = self.client.commands[0]['command']
        self.assertEqual(command['action'], 'item_edit'); self.assertEqual(command['expected_revision'], 3)
        for key in ('barcode', 'barcodes', 'package_size', 'category', 'expiry_date', 'expiry_kind'):
            self.assertEqual(command[key], snapshot()['items'][0][key])
        self.assertEqual(self.client.data['items'][0]['aliases'], ['Frozen peas'])

    def test_adjust_uses_exact_revision_set_instead_of_non_idempotent_relative_write(self):
        result = self.request('storage_adjust', {**self.target(), 'delta': '-0.125'})
        self.assertEqual(result['outcome'], 'confirmed')
        self.assertEqual(self.client.commands[0]['command']['action'], 'set')
        self.assertEqual(self.client.commands[0]['command']['quantity'], '1.125')
        self.assertEqual(self.client.commands[0]['command']['expected_revision'], 3)

    def test_invalid_and_stale_changes_never_fence_or_mutate(self):
        cases = [('storage_adjust', {**self.target(), 'delta': '-2'}),
                 ('storage_adjust', {**self.target(), 'delta': '0'}),
                 ('storage_adjust', {**self.target(), 'delta': '0.0001'}),
                 ('storage_update', {**self.target(), 'expected_revision': 2, 'quantity': '2'}),
                 ('storage_update', {**self.target(), 'unit': 'g'}),
                 ('storage_update', {**self.target(), 'expiry_date': '2026-02-30'}),
                 ('storage_update', {**self.target(), 'expiry_date': '', 'expiry_kind': 'use_by'}),
                 ('storage_update', {**self.target(), 'quantity': 'NaN'}),
                 ('storage_remove', {**self.target(), 'request_id': 'not-uuid'})]
        for operation, args in cases:
            with self.subTest(operation=operation, args=args), self.assertRaises(ValueError): self.request(operation, args)
        self.assertEqual(self.client.commands, []); self.assertEqual(self.fences, [])

    def test_add_requires_existing_location_and_does_not_merge_duplicate_stock(self):
        args = {'location_id': LOCATION, 'name': 'Beans', 'quantity': '2.000', 'unit': 'cans', 'request_id': str(uuid4())}
        result = self.request('storage_add', args)
        self.assertEqual(result['outcome'], 'confirmed'); self.assertEqual(result['item']['quantity'], '2')
        with self.assertRaises(ValueError): self.request('storage_add', {**args, 'request_id': str(uuid4())})
        self.assertEqual(len(self.client.commands), 1)
        with self.assertRaises(ValueError): self.request('storage_add', {**args, 'location_id': 'd' * 32})

    def test_unit_change_requires_explicit_quantity_and_expiry_change_resets_kind(self):
        result = self.request('storage_update', {**self.target(), 'unit': 'g', 'quantity': '1250',
                                               'expiry_date': '2027-02-01'})
        self.assertEqual(result['outcome'], 'confirmed'); self.assertEqual(result['item']['unit'], 'g')
        self.assertEqual(result['item']['quantity'], '1250'); self.assertEqual(result['item']['expiry_kind'], '')

    def test_remove_exact_item_with_revision(self):
        result = self.request('storage_remove', self.target())
        self.assertEqual(result['outcome'], 'confirmed'); self.assertEqual(result['removed_item_id'], ITEM)
        self.assertEqual(self.client.data['items'], [])

    def test_postwrite_disconnect_and_concurrent_change_are_uncertain_and_never_retried(self):
        for flag in ('fail_after_write', 'concurrent_change'):
            client = Fake(); setattr(client, flag, True)
            result = Storage(client).request('homecompute_storage_adjust', {**self.target(), 'delta': '0.5'})
            self.assertEqual(result['outcome'], 'uncertain'); self.assertEqual(len(client.commands), 1)
            self.assertNotIn('PRIVATE_HA_TOKEN', json.dumps(result))

    def test_failed_durable_fence_prevents_send(self):
        def failed(): raise ValueError('Ledger refused')
        with self.assertRaises(ValueError): self.storage.request('homecompute_storage_remove', self.target(), failed)
        self.assertEqual(self.client.commands, [])

    def test_catalog_is_bounded_and_reports_total(self):
        template = self.client.data['items'][0]
        self.client.data['items'] = [{**template, 'id': f'{i:032x}', 'name': 'x' * 100} for i in range(100)]
        result = self.request('storage_list', {})
        self.assertEqual(result['total'], 100); self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result).encode()), 22000)


if __name__ == '__main__': unittest.main()
