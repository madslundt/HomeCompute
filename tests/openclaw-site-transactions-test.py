#!/usr/bin/env python3
"""Exact transaction approval tests; no real account, credentials or payments."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_site_transactions import SiteTransactions, TransactionRefused


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name).resolve() / 'transactions'
        self.now = [1000]
        self.grant_id = '00000000-0000-4000-8000-000000000001'
        self.grant = {'request_id': self.grant_id, 'actor': 'travel', 'account': 'hotels.com',
                      'status': 'active', 'approved_at': 900, 'expires_at': 2800}
        self.authorize_calls = []
        self.quote = {'account': 'hotels.com', 'kind': 'booking', 'payee': 'Synthetic hotel merchant',
                      'amount_minor': 12345, 'currency': 'DKK',
                      'items': [{'description': 'One room, synthetic dates, taxes included', 'quantity': 1, 'unit_amount_minor': 12345}],
                      'terms': ['Free cancellation before the stated deadline.', 'Pay the full displayed total.'], 'expires_at': 2500}
        self.drivers = frozenset({('synthetic-driver-v1', 'hotels.com', 'booking')})
        self.ledger = SiteTransactions(self.directory, self.authorize, clock=lambda: self.now[0], qualified_drivers=self.drivers)

    def tearDown(self):
        self.temporary.cleanup()

    def authorize(self, actor, grant_ref):
        self.authorize_calls.append((actor, grant_ref))
        return dict(self.grant)

    def proposal(self, quote=None, actor='travel', grant_ref=None):
        return self.ledger.propose(actor, grant_ref or self.grant_id, quote or self.quote)

    def decide(self, item, action='approve'):
        return self.ledger.decide(item['transaction_id'], item['proposal_digest'], action,
                                  self.ledger.challenge(item['transaction_id'], action))

    def claim(self, item, quote=None, actor='travel', driver='synthetic-driver-v1'):
        return self.ledger.claim(actor, item['transaction_id'], quote or self.quote, driver)

    def test_exact_snapshot_digest_expiry_and_private_state(self):
        item = self.proposal()
        self.assertEqual(item['expires_at'], 1900)
        self.assertEqual(item['quote_expires_at'], 2500)
        self.assertEqual(item['amount_minor'], 12345)
        self.assertEqual(item['currency'], 'DKK')
        self.assertEqual(item['items'], self.quote['items'])
        self.assertEqual(item['terms'], self.quote['terms'])
        self.assertEqual(item['grant_ref'], self.grant_id)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / 'transactions.sqlite').stat().st_mode), 0o600)
        self.grant['expires_at'] = 1100
        self.assertEqual(self.proposal()['expires_at'], 1100)
        quote = dict(self.quote, expires_at=1050)
        self.assertEqual(self.proposal(quote)['expires_at'], 1050)

    def test_access_grant_alone_never_authorizes_transaction(self):
        item = self.proposal()
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        approved = self.decide(item)
        self.assertEqual(approved['status'], 'approved')
        claim = self.claim(item)
        self.assertEqual(claim['status'], 'executing')
        self.assertTrue(claim['claim_authorized'])
        self.assertEqual(claim['claim_deadline'], 1015)
        self.assertGreaterEqual(len(self.authorize_calls), 3)
        with self.assertRaises(TransactionRefused):
            self.claim(item)

    def test_no_driver_is_qualified_by_default_and_draft_approval_never_arms_later(self):
        self.ledger = SiteTransactions(self.directory, self.authorize, clock=lambda: self.now[0])
        item = self.proposal()
        self.assertFalse(item['execution_available'])
        self.assertFalse(self.decide(item)['execution_available'])
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        self.ledger = SiteTransactions(self.directory, self.authorize, clock=lambda: self.now[0], qualified_drivers=self.drivers)
        self.assertFalse(self.ledger.review(item['transaction_id'])['execution_available'])
        with self.assertRaises(TransactionRefused):
            self.claim(item)

    def test_changed_amount_currency_payee_items_or_terms_requires_fresh_approval(self):
        item = self.proposal()
        self.decide(item)
        variations = []
        amount = copy.deepcopy(self.quote)
        amount['amount_minor'] += 1
        amount['items'][0]['unit_amount_minor'] += 1
        variations.append(amount)
        variations += [dict(self.quote, currency='EUR'), dict(self.quote, payee='Different merchant'),
                       dict(self.quote, terms=['No cancellation.']), dict(self.quote, expires_at=2400)]
        description = copy.deepcopy(self.quote)
        description['items'][0]['description'] = 'Different room or dates'
        variations.append(description)
        for quote in variations:
            with self.assertRaises(TransactionRefused):
                self.claim(item, quote)
        self.assertEqual(self.ledger.review(item['transaction_id'])['status'], 'approved')

    def test_new_driver_qualification_cannot_inherit_an_old_approval(self):
        item = self.proposal()
        self.decide(item)
        changed = frozenset({('synthetic-driver-v2', 'hotels.com', 'booking')})
        self.ledger = SiteTransactions(self.directory, self.authorize, clock=lambda: self.now[0], qualified_drivers=changed)
        self.assertFalse(self.ledger.review(item['transaction_id'])['execution_available'])
        with self.assertRaises(TransactionRefused):
            self.claim(item, driver='synthetic-driver-v2')

    def test_grant_revalidated_and_actor_account_reference_are_bound(self):
        for change in [{'actor': 'researcher'}, {'account': 'another.example'}, {'request_id': '00000000-0000-4000-8000-000000000002'},
                       {'status': 'revoked'}, {'expires_at': 1000}, {'approved_at': 1001}]:
            original = dict(self.grant)
            self.grant.update(change)
            with self.assertRaises(TransactionRefused):
                self.proposal()
            self.grant = original
        item = self.proposal()
        self.decide(item)
        with self.assertRaises(TransactionRefused):
            self.claim(item, actor='researcher')
        with self.assertRaises(TransactionRefused):
            self.ledger.review(item['transaction_id'], actor='researcher')
        self.grant['status'] = 'revoked'
        with self.assertRaises(TransactionRefused):
            self.claim(item)

    def test_exact_digest_action_request_and_single_use_challenge(self):
        first, second = self.proposal(), self.proposal()
        nonce = self.ledger.challenge(first['transaction_id'], 'approve')
        for transaction_id, proposal_digest, action in [(first['transaction_id'], '0' * 64, 'approve'),
                (first['transaction_id'], first['proposal_digest'], 'deny'),
                (second['transaction_id'], second['proposal_digest'], 'approve')]:
            with self.assertRaises(TransactionRefused):
                self.ledger.decide(transaction_id, proposal_digest, action, nonce)
        self.ledger.decide(first['transaction_id'], first['proposal_digest'], 'approve', nonce)
        with self.assertRaises(TransactionRefused):
            self.ledger.decide(first['transaction_id'], first['proposal_digest'], 'approve', nonce)

    def test_challenge_and_proposal_expiry_are_exact_and_cannot_be_revived(self):
        item = self.proposal()
        nonce = self.ledger.challenge(item['transaction_id'], 'approve')
        self.now[0] = 1300
        with self.assertRaises(TransactionRefused):
            self.ledger.decide(item['transaction_id'], item['proposal_digest'], 'approve', nonce)
        self.decide(item)
        self.now[0] = 1900
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        self.now[0] = 1400
        self.assertEqual(self.ledger.review(item['transaction_id'])['status'], 'expired')
        with self.assertRaises(TransactionRefused):
            self.claim(item)

    def test_clock_rollback_inside_valid_window_permanently_expires(self):
        item = self.proposal()
        self.decide(item)
        self.now[0] = 1100
        self.ledger.review(item['transaction_id'])
        self.now[0] = 1099
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        self.now[0] = 1200
        self.assertEqual(self.ledger.review(item['transaction_id'])['status'], 'expired')

    def test_concurrent_claims_consume_one_approval_once(self):
        item = self.proposal()
        self.decide(item)
        def claim(_index):
            try:
                return self.claim(item)
            except TransactionRefused:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.ledger.review(item['transaction_id'])['status'], 'executing')

    def test_unknown_and_failed_outcomes_are_terminal_and_never_retried(self):
        for outcome in ['failed', 'unknown']:
            item = self.proposal()
            self.decide(item)
            claim = self.claim(item)
            result = self.ledger.execution_outcome(item['transaction_id'], claim['execution_id'], outcome)
            self.assertEqual(result['status'], outcome)
            with self.assertRaises(TransactionRefused):
                self.claim(item)
            with self.assertRaises(TransactionRefused):
                self.ledger.execution_outcome(item['transaction_id'], claim['execution_id'], 'succeeded', 'synthetic-receipt')
            self.assertEqual(self.ledger.execution_outcome(item['transaction_id'], claim['execution_id'], outcome), result)

    def test_unresolved_effect_cannot_be_retried_as_a_fresh_proposal_or_parallel_claim(self):
        first, second = self.proposal(), self.proposal()
        self.decide(first)
        self.decide(second)
        claim = self.claim(first)
        with self.assertRaisesRegex(TransactionRefused, 'unresolved'):
            self.proposal()
        with self.assertRaisesRegex(TransactionRefused, 'unresolved'):
            self.claim(second)
        self.ledger.execution_outcome(first['transaction_id'], claim['execution_id'], 'unknown')
        with self.assertRaisesRegex(TransactionRefused, 'unresolved'):
            self.proposal()
        with self.assertRaisesRegex(TransactionRefused, 'unresolved'):
            self.claim(second)

    def test_resolved_success_allows_a_separate_approved_transaction(self):
        first, second = self.proposal(), self.proposal()
        self.decide(first)
        self.decide(second)
        claim = self.claim(first)
        self.ledger.execution_outcome(first['transaction_id'], claim['execution_id'], 'succeeded', 'synthetic-receipt')
        self.assertEqual(self.claim(second)['status'], 'executing')

    def test_claim_survives_restart_as_consumed_and_expiry_becomes_unknown(self):
        item = self.proposal()
        self.decide(item)
        claim = self.claim(item)
        self.ledger = SiteTransactions(self.directory, self.authorize, clock=lambda: self.now[0], qualified_drivers=self.drivers)
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        self.now[0] = 1900
        self.assertEqual(self.ledger.review(item['transaction_id'])['status'], 'unknown')
        result = self.ledger.execution_outcome(item['transaction_id'], claim['execution_id'], 'succeeded', 'synthetic-receipt')
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['receipt_reference'], 'synthetic-receipt')

    def test_denial_revoke_and_bad_outcome_reference_cannot_expand_authority(self):
        item = self.proposal()
        self.assertEqual(self.decide(item, 'deny')['status'], 'denied')
        with self.assertRaises(TransactionRefused):
            self.decide(item)
        item = self.proposal()
        self.decide(item)
        self.assertEqual(self.decide(item, 'revoke')['status'], 'revoked')
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        item = self.proposal()
        self.decide(item)
        claim = self.claim(item)
        with self.assertRaises(TransactionRefused):
            self.ledger.execution_outcome(item['transaction_id'], self.grant_id, 'succeeded', 'synthetic-receipt')
        with self.assertRaises(TransactionRefused):
            self.ledger.execution_outcome(item['transaction_id'], claim['execution_id'], 'failed', 'https://arbitrary.invalid')

    def test_snapshot_rejects_secret_fields_urls_selectors_invalid_totals_and_html(self):
        for change in [{'commit_url': 'https://arbitrary.invalid'}, {'password': 'synthetic-secret'}, {'selector': '#commit'},
                       {'kind': 'anything'}, {'amount_minor': True}, {'amount_minor': 999}, {'currency': 'dkk'},
                       {'account': 'https://hotels.com'}, {'payee': '<script>anything</script>'}, {'terms': ['\x00bad']},
                       {'payee': 'Synthetic\u202emerchant'},
                       {'items': []}, {'expires_at': True}]:
            with self.assertRaises(TransactionRefused):
                self.proposal(dict(self.quote, **change))

    def test_grant_errors_never_leak_upstream_secret_text(self):
        def broken(_actor, _grant):
            raise RuntimeError('synthetic-credential-DO-NOT-LOG')
        ledger = SiteTransactions(self.directory, broken, clock=lambda: self.now[0])
        with self.assertRaises(TransactionRefused) as error:
            ledger.propose('travel', self.grant_id, self.quote)
        self.assertEqual(str(error.exception), 'Active account grant required')
        self.assertTrue(error.exception.__suppress_context__)

    def test_slow_authorization_cannot_issue_a_claim_after_quote_expiry(self):
        item = self.proposal()
        self.decide(item)
        original = self.ledger.authorize_grant
        def slow(actor, grant_ref):
            self.now[0] = 1900
            return original(actor, grant_ref)
        self.ledger.authorize_grant = slow
        with self.assertRaises(TransactionRefused):
            self.claim(item)
        self.assertEqual(self.ledger.review(item['transaction_id'])['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
