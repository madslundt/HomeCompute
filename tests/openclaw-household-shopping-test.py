"""Shopping adapter fixtures; no household data, notifications or network calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_household_shopping import Shopping, digest, envelope
from semantic_retrieval import SemanticRanker


def item(uid='synthetic-1', name='Milk', *, watch=False):
    meta = {'schema': 'hygge-shopping/v1', 'amount': 2, 'category': 'Mejeri', 'note': 'Keep this note',
            'future_field': {'keep': True}, 'metadata': {'wrong_matches': [{'product_name': 'Other'}],
            'selected_offer_id': 'synthetic-offer', 'selected_offer_original_name': name,
            **({'watch_discounts': True} if watch else {})}}
    return {'uid': uid, 'summary': name, 'status': 'needs_action', 'description': json.dumps(meta)}


class Fake:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows if rows is not None else [item()])
        self.commands = []; self.fail_after_write = False; self.corrupt_readback = False

    def call(self, command):
        self.commands.append(deepcopy(command))
        if command['type'] == 'todo/item/list':
            return {'items': deepcopy(self.rows)}
        fields = command['service_data']; service = command['service']
        if service == 'add_item':
            self.rows.append({'uid': 'new-synthetic', 'summary': fields['item'], 'status': 'needs_action',
                              'description': fields['description']})
        elif service == 'remove_item':
            self.rows = [r for r in self.rows if r['uid'] not in fields['item']]
        else:
            row = next(r for r in self.rows if r['uid'] == fields['item'])
            for key in ('status', 'description'):
                if key in fields: row[key] = fields[key]
            if 'rename' in fields: row['summary'] = fields['rename']
        if self.corrupt_readback and self.rows: self.rows[0]['summary'] = 'Another device changed it'
        if self.fail_after_write: raise OSError('PRIVATE_TOKEN_NEVER_DISPLAY')
        return None

    def discounts(self):
        return {'entity_id': 'sensor.shopping_discounts', 'attributes': {'updated_at': 'synthetic-time',
                'offers': [{'id': 'offer', 'todo_uid': 'synthetic-1', 'price': 12.5, 'store': 'Synthetic',
                            'product_name': 'Milk', 'private_upstream_field': 'SECRET'}]}}


class ShoppingTest(unittest.TestCase):
    def setUp(self):
        self.client = Fake(); self.ranker = Mock()
        self.ranker.rank.side_effect = lambda query, rows, **kwargs: [r for r in rows if query.casefold() in r['text'].casefold()]
        self.shopping = Shopping(self.client, ranker=self.ranker); self.fences = []

    def test_semantic_discovery_preserves_exact_identity_and_never_merges_products(self):
        self.client.rows.extend([item('danish', 'Havremælk'), item('watch', 'Oat drink', watch=True)])
        def ranked(query, rows, **kwargs):
            self.assertEqual(query, 'oat milk'); self.assertEqual(kwargs['task'], 'shopping')
            self.assertEqual({r['id'] for r in rows}, {'synthetic-1', 'danish'})
            return [{**r, 'retrieval_score': 0.8, 'lexical_score': 0, 'semantic_score': 0.9}
                    for r in rows if r['id'] == 'danish']
        self.ranker.rank.side_effect = ranked
        row = self.request('shopping_list', {'query': 'oat milk'})['items'][0]
        self.assertEqual(row['item_id'], 'danish'); self.assertEqual(row['amount'], 2)
        self.assertEqual(row['expected_digest'], digest(self.client.rows[1]))
        self.assertEqual(row['semantic_score'], 0.9); self.assertEqual(self.fences, [])
        self.client.rows[1]['summary'] = 'Changed after retrieval'
        with self.assertRaises(ValueError):
            self.request('shopping_remove', {'item_id': row['item_id'], 'expected_digest': row['expected_digest'], 'request_id': str(uuid4())})
        self.assertFalse(any(c['type'] == 'call_service' for c in self.client.commands))

    def test_discount_semantic_query_keeps_exact_uid_price_and_freshness(self):
        self.ranker.rank.side_effect = lambda query, rows, **kwargs: [{**rows[0], 'semantic_score': 0.9}]
        result = self.request('discounts_read', {'query': 'mælk', 'item_id': 'synthetic-1'})
        self.assertEqual(result['offers'][0]['todo_uid'], 'synthetic-1')
        self.assertEqual(result['offers'][0]['price'], 12.5)
        self.assertIn('stale', result['freshness']); self.assertEqual(self.client.commands, [])

    def test_embedding_outage_keeps_keyword_discovery_available(self):
        embedding = Mock(); embedding.embed.side_effect = OSError('offline')
        shopping = Shopping(self.client, ranker=SemanticRanker(client=embedding))
        row = shopping.read({'query': 'milk'})['items'][0]
        self.assertEqual(row['item_id'], 'synthetic-1'); self.assertIsNone(row['semantic_score'])
        self.assertFalse(any(c['type'] == 'call_service' for c in self.client.commands))

    def test_large_keyword_fallback_reports_all_matches_and_empty_scope_skips_embedding(self):
        self.client.rows = [item(str(i), 'Milk') for i in range(120)]
        embedding = Mock(); embedding.embed.side_effect = TimeoutError('offline')
        shopping = Shopping(self.client, ranker=SemanticRanker(client=embedding))
        result = shopping.read({'query': 'Milk'})
        self.assertEqual(result['total'], 120); self.assertTrue(result['truncated'])
        embedding.embed.reset_mock()
        self.assertEqual(shopping.read({'query': 'Milk', 'kind': 'watches'})['items'], [])
        embedding.embed.assert_not_called()

    def request(self, operation, args):
        return self.shopping.request('homecompute_' + operation, args, lambda: self.fences.append(len(self.client.commands)))

    def target(self):
        row = self.client.rows[0]
        return {'item_id': row['uid'], 'expected_digest': digest(row), 'request_id': str(uuid4())}

    def test_read_projection_filters_watches_and_preserves_digest(self):
        self.client.rows.append(item('watch', 'Coffee', watch=True))
        result = self.request('shopping_list', {})
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['expected_digest'], digest(self.client.rows[0]))
        self.assertNotIn('wrong_matches', json.dumps(result)); self.assertNotIn('future_field', json.dumps(result))
        self.assertEqual(self.request('shopping_list', {'kind': 'watches'})['items'][0]['name'], 'Coffee')
        self.assertEqual(self.fences, [])

    def test_update_preserves_unknown_metadata_and_fences_immediately_before_write(self):
        original = envelope(self.client.rows[0]); args = self.target()
        result = self.request('shopping_update', {**args, 'amount': 4, 'note': 'New note'})
        self.assertEqual(result['outcome'], 'confirmed'); self.assertEqual(self.fences, [1])
        self.assertEqual(self.client.commands[1]['type'], 'call_service')
        changed = envelope(self.client.rows[0])
        self.assertEqual(changed['future_field'], original['future_field'])
        self.assertEqual(changed['metadata']['wrong_matches'], original['metadata']['wrong_matches'])
        self.assertEqual(changed['metadata']['selected_offer_id'], original['metadata']['selected_offer_id'])
        self.assertEqual(changed['metadata']['selected_offer_original_name'], original['metadata']['selected_offer_original_name'])
        self.assertEqual(changed['amount'], 4); self.assertEqual(changed['note'], 'New note')
        self.assertEqual(self.client.commands[1]['service_data']['entity_id'], 'todo.indkob')

    def test_actual_rename_clears_selection_but_retains_other_metadata(self):
        result = self.request('shopping_update', {**self.target(), 'name': 'Oat milk'})
        self.assertEqual(result['outcome'], 'confirmed')
        changed = envelope(self.client.rows[0])
        self.assertNotIn('selected_offer_id', changed['metadata'])
        self.assertNotIn('selected_offer_original_name', changed['metadata'])
        self.assertEqual(changed['metadata']['wrong_matches'], [{'product_name': 'Other'}])
        self.assertEqual(changed['note'], 'Keep this note')

    def test_no_op_name_does_not_clear_existing_selection(self):
        self.request('shopping_update', {**self.target(), 'name': self.client.rows[0]['summary']})
        changed = envelope(self.client.rows[0])
        self.assertEqual(changed['metadata']['selected_offer_id'], 'synthetic-offer')
        self.assertEqual(changed['metadata']['selected_offer_original_name'], 'Milk')
        self.assertNotIn('rename', self.client.commands[1]['service_data'])

    def test_selected_offer_original_name_matches_read_and_duplicate_add_without_implicit_merge(self):
        self.client.rows[0]['summary'] = 'Chosen organic brand product'
        result = self.request('shopping_list', {'query': 'Milk'})
        self.assertEqual(result['items'][0]['original_name'], 'Milk')
        self.assertEqual(result['items'][0]['name'], 'Chosen organic brand product')
        result = self.request('shopping_add', {'name': 'Milk', 'amount': 2, 'request_id': str(uuid4())})
        self.assertFalse(result['changed']); self.assertEqual(self.fences, [])
        with self.assertRaises(ValueError):
            self.request('shopping_add', {'name': 'Milk', 'amount': 3, 'request_id': str(uuid4())})
        self.assertEqual(len(self.client.rows), 1)

    def test_multiple_generic_matches_require_exact_identity(self):
        second = deepcopy(self.client.rows[0]); second['uid'] = 'other'; second['summary'] = 'Another selected milk'
        self.client.rows.append(second)
        with self.assertRaises(ValueError):
            self.request('shopping_add', {'name': 'Milk', 'amount': 2, 'request_id': str(uuid4())})
        self.assertEqual(self.fences, [])

    def test_stale_or_invalid_preconditions_never_fence_or_write(self):
        for args in [{**self.target(), 'expected_digest': '0' * 64, 'amount': 3},
                     {**self.target(), 'amount': True}, {**self.target(), 'name': 'unsafe\nname'},
                     {**self.target(), 'request_id': 'not-a-uuid', 'amount': 3}]:
            with self.assertRaises(ValueError): self.request('shopping_update', args)
        self.assertEqual(self.fences, [])
        self.assertTrue(all(c['type'] == 'todo/item/list' for c in self.client.commands))

    def test_multiline_note_is_preserved_as_user_data(self):
        note = ' First line\n\tSecond line '
        result = self.request('shopping_update', {**self.target(), 'note': note})
        self.assertEqual(result['outcome'], 'confirmed')
        self.assertEqual(envelope(self.client.rows[0])['note'], note)

    def test_fence_failure_prevents_actual_mutation(self):
        def failed(): raise ValueError('Ledger unavailable')
        with self.assertRaises(ValueError):
            self.shopping.request('homecompute_shopping_remove', self.target(), failed)
        self.assertEqual(len(self.client.rows), 1)
        self.assertEqual([c['type'] for c in self.client.commands], ['todo/item/list'])

    def test_add_has_recovery_marker_and_never_silently_increments_existing_item(self):
        result = self.request('shopping_add', {'name': 'Coffee', 'amount': 1, 'request_id': str(uuid4())})
        self.assertEqual(result['outcome'], 'confirmed')
        self.assertIn('household_request_id', envelope(self.client.rows[-1])['metadata'])
        count = len(self.fences)
        result = self.request('shopping_add', {'name': 'Milk', 'amount': 2, 'request_id': str(uuid4())})
        self.assertFalse(result['changed']); self.assertEqual(len(self.fences), count)
        with self.assertRaises(ValueError):
            self.request('shopping_add', {'name': 'Milk', 'amount': 5, 'request_id': str(uuid4())})

    def test_complete_reopen_and_remove_target_only_one_exact_item(self):
        self.assertEqual(self.request('shopping_complete', {**self.target(), 'completed': True})['outcome'], 'confirmed')
        self.assertEqual(self.client.rows[0]['status'], 'completed')
        self.assertIn('completed_at', envelope(self.client.rows[0])['metadata'])
        self.request('shopping_complete', {**self.target(), 'completed': False})
        self.assertNotIn('completed_at', envelope(self.client.rows[0])['metadata'])
        result = self.request('shopping_remove', self.target())
        self.assertEqual(result['outcome'], 'confirmed'); self.assertEqual(self.client.rows, [])

    def test_watch_separate_from_purchase_and_price_policy_requires_fresh_identity(self):
        self.request('discount_watch', {'name': 'Milk', 'max_unit_price': 8.5, 'request_id': str(uuid4())})
        self.assertEqual(len(self.client.rows), 2); self.assertFalse(envelope(self.client.rows[0])['metadata'].get('watch_discounts', False))
        row = self.client.rows[-1]
        args = {'name': 'Milk', 'item_id': row['uid'], 'expected_digest': digest(row),
                'clear_max_unit_price': True, 'request_id': str(uuid4())}
        self.assertEqual(self.request('discount_watch', args)['outcome'], 'confirmed')
        self.assertNotIn('watch_max_unit_price', envelope(self.client.rows[-1])['metadata'])
        with self.assertRaises(ValueError): self.request('discount_unwatch', self.target())
        self.assertEqual(self.request('discount_unwatch', {'item_id': row['uid'], 'expected_digest': digest(self.client.rows[-1]),
                         'request_id': str(uuid4())})['outcome'], 'confirmed')

    def test_uncertain_postwrite_failure_or_concurrent_change_is_not_retried(self):
        for failure in ('fail_after_write', 'corrupt_readback'):
            client = Fake(); setattr(client, failure, True); shopping = Shopping(client)
            args = {'item_id': client.rows[0]['uid'], 'expected_digest': digest(client.rows[0]),
                    'request_id': str(uuid4()), 'amount': 4}
            result = shopping.request('homecompute_shopping_update', args)
            self.assertEqual(result['outcome'], 'uncertain'); self.assertNotIn('PRIVATE_TOKEN', json.dumps(result))
            self.assertEqual(sum(c['type'] == 'call_service' for c in client.commands), 1)

    def test_discount_projection_omits_private_fields_and_does_not_mutate(self):
        result = self.request('discounts_read', {'item_id': 'synthetic-1'})
        self.assertEqual(result['offers'][0]['price'], 12.5)
        self.assertNotIn('SECRET', json.dumps(result)); self.assertEqual(self.client.commands, [])

    def test_large_list_is_explicitly_bounded(self):
        self.client.rows = [item(str(i), 'Synthetic') for i in range(80)]
        for row in self.client.rows:
            data = envelope(row); data['note'] = 'x' * 1000; row['description'] = json.dumps(data)
        result = self.request('shopping_list', {})
        self.assertTrue(result['truncated']); self.assertEqual(result['total'], 80)
        self.assertLessEqual(len(json.dumps(result).encode()), 22000)


if __name__ == '__main__': unittest.main()
