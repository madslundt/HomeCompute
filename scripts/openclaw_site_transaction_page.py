"""Owner-only exact transaction review. Static HTML; no credentials or scripts."""
from __future__ import annotations

import html

from openclaw_access_page import moment, shell

STATES = {'pending': 'Waiting for your transaction decision', 'approved': 'Transaction approved',
          'denied': 'Transaction denied', 'revoked': 'Approval revoked', 'expired': 'Proposal expired',
          'executing': 'Execution claimed; do not retry', 'succeeded': 'Transaction completed',
          'failed': 'Transaction failed', 'unknown': 'Outcome uncertain; operator review required'}
PRECISION = {'DKK': 2, 'EUR': 2, 'USD': 2, 'GBP': 2, 'SEK': 2, 'NOK': 2, 'CHF': 2,
             'CAD': 2, 'AUD': 2, 'NZD': 2, 'JPY': 0, 'KRW': 0, 'BHD': 3, 'KWD': 3}


def amount(value: int, currency: str) -> str:
    """Never guess decimal placement for an unsupported currency."""
    if currency not in PRECISION:
        return f'{value:,} minor units · {currency}'
    places = PRECISION[currency]
    scale = 10**places
    number = str(value) if not places else f'{value // scale:,}.{value % scale:0{places}d}'
    return currency + ' ' + number


def instant(value: int) -> str:
    try:
        return moment(value)
    except (ValueError, OverflowError, OSError):
        return str(value) + ' Unix seconds'


def review_transaction(item: dict, challenges: dict[str, str]) -> str:
    rows = [('Account', item['account']), ('Requested by', item['actor']),
            ('Transaction', {'booking': 'Booking', 'purchase': 'Purchase'}[item['kind']]),
            ('Payee', item['payee']), ('Exact total', amount(item['amount_minor'], item['currency'])),
            ('Status', STATES[item['status']]), ('Approval expires', instant(item['expires_at'])),
            ('Quote expires', instant(item['quote_expires_at']))]
    if item['approved_at'] is not None:
        rows.append(('Approved', instant(item['approved_at'])))
    details = ''.join('<dt>' + html.escape(label) + '</dt><dd>' + html.escape(str(value)) + '</dd>'
                      for label, value in rows)
    lines = ''.join('<li>' + html.escape(line['description']) + '<br><small>' +
                    html.escape(str(line['quantity']) + ' × ' + amount(line['unit_amount_minor'], item['currency'])) +
                    '</small></li>' for line in item['items'])
    terms = ''.join('<li>' + html.escape(term) + '</li>' for term in item['terms'])
    forms = ''
    for action, nonce in challenges.items():
        forms += ('<form method="post" action="/transactions/decision/' + html.escape(item['transaction_id'], quote=True) + '">'
                  '<input type="hidden" name="action" value="' + html.escape(action, quote=True) + '">'
                  '<input type="hidden" name="digest" value="' + html.escape(item['proposal_digest'], quote=True) + '">'
                  '<input type="hidden" name="csrf" value="' + html.escape(nonce, quote=True) + '">')
        label = {'approve': 'Approve exact transaction', 'deny': 'Deny transaction', 'revoke': 'Revoke transaction approval'}[action]
        style = {'approve': '', 'deny': ' class="secondary"', 'revoke': ' class="danger"'}[action]
        forms += '<button' + style + ' type="submit">' + label + '</button></form>'
    if item.get('driver_qualified') is not True:
        availability = ('No transaction finalizer is qualified. Approval records your decision only; '
                        'it cannot purchase or book anything.')
    elif item['status'] == 'approved' and item.get('execution_available') is not True:
        availability = ('This approval cannot be executed with the current finalizer qualification. '
                        'A fresh exact proposal and owner approval are required.')
    else:
        availability = ('A qualified transaction finalizer is configured. The account grant must remain active, '
                        'and every displayed item, term, payee and total must still match before execution.')
    if item.get('reconciliation_required'):
        availability += ' The execution may already have taken effect. Do not retry; inspect the outcome with the operator.'
    reference = ('<p class="ref"><small>Transaction ' + html.escape(item['transaction_id']) + '<br>Account grant ' +
                 html.escape(item['grant_ref']) + '<br>Exact proposal digest ' + html.escape(item['proposal_digest']) + '</small></p>')
    return shell('Review exact transaction', '<section class="card"><dl>' + details + '</dl></section>'
                 '<section class="card"><h2>Items and included charges</h2><ul>' + lines + '</ul></section>'
                 '<section class="card"><h2>Exact terms</h2><ul>' + terms + '</ul><div class="actions">' + forms + '</div></section>'
                 '<p class="note">' + availability + '</p><p>Account access approval alone never approves this transaction. '
                 'Changed price or terms require a new proposal and a new decision. '
                 'This page cannot submit a purchase or booking.</p>' + reference + '<a href="/">Account access requests</a>')
