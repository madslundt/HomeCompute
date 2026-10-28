"""Small static owner review UI. No scripts, credentials or raw upstream text."""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import html
from zoneinfo import ZoneInfo

STYLE = '''
:root{font-family:system-ui,-apple-system,sans-serif;color:#e9eef5;background:#10151e;color-scheme:dark}
body{margin:0;padding:36px 20px}main{max-width:700px;margin:auto}header{margin-bottom:28px}
.eyebrow{color:#9cb5ce;font-size:12px;font-weight:650;letter-spacing:.13em;text-transform:uppercase}
h1{font-size:32px;letter-spacing:-.04em;margin:10px 0}h2{font-size:20px;margin:0 0 18px}
p{line-height:1.6;color:#b9c5d3}.card{background:#19212d;border:1px solid #344153;border-radius:16px;padding:26px;margin:18px 0}
dl{display:grid;grid-template-columns:130px 1fr;gap:14px;margin:0}dt{color:#9eacbd}dd{margin:0;overflow-wrap:anywhere}
label{display:block;margin:22px 0 12px;color:#c8d4e2}input{padding:11px;border:1px solid #56687d;border-radius:7px;background:#111923;font:inherit;max-width:100px}
button{padding:12px 20px;border:0;border-radius:8px;background:#a6e1cb;color:#12291f;font:inherit;font-weight:650;cursor:pointer}
button.secondary{background:#303e50;color:#e9eef5}button.danger{background:#583440;color:#ffe6e9}
.actions{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.note{border-left:3px solid #cba968;padding:4px 16px}
a{color:#a6c9f0}li{margin:16px 0}small{color:#91a1b5}.ref{margin-top:22px}
@media(max-width:440px){dl{grid-template-columns:1fr;gap:7px}dd{margin-bottom:10px}.card{padding:20px}}
'''
CSP = ("default-src 'none'; style-src 'sha256-" +
       base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode() +
       "'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def shell(title: str, body: str) -> str:
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + html.escape(title) + '</title><style>' + STYLE + '</style><main>'
            '<header><div class="eyebrow">HomeCompute · private approvals</div><h1>' +
            html.escape(title) + '</h1></header>' + body + '</main></html>')


def moment(value) -> str:
    return datetime.fromtimestamp(value, ZoneInfo('Europe/Copenhagen')).strftime('%d %b %Y, %H:%M %Z')


def scope_name(value: str) -> str:
    return {'search': 'Search the registered site', 'prepare_booking': 'Prepare a booking for review',
            'prepare_purchase': 'Prepare a purchase for review', 'read': 'Read approved account information'}[value]


def review(item: dict, challenges: dict[str, str]) -> str:
    states = {'pending': 'Waiting for your decision', 'active': 'Permission approved', 'denied': 'Request denied',
              'revoked': 'Permission revoked', 'expired': 'Request expired'}
    rows = [('Account', item['account']), ('Requested by', 'OpenClaw' if item['actor'] == 'openclaw' else item['actor']),
            ('Task', scope_name(item['scope'])), ('Requested time', str(item['requested_minutes']) + ' minutes'),
            ('Status', states[item['status']])]
    if item['expires_at'] is not None:
        rows.append(('Expires', moment(item['expires_at'])))
    fields = ''.join('<dt>' + html.escape(key) + '</dt><dd>' + html.escape(value) + '</dd>' for key, value in rows)
    forms = ''
    for action, nonce in challenges.items():
        forms += ('<form method="post" action="/decision/' + item['request_id'] + '">'
                  '<input type="hidden" name="action" value="' + action + '">'
                  '<input type="hidden" name="digest" value="' + item['request_digest'] + '">'
                  '<input type="hidden" name="csrf" value="' + nonce + '">')
        if action == 'approve':
            forms += ('<label for="minutes">Allow access for</label><input id="minutes" type="number" name="minutes" '
                      'min="1" max="1440" value="' + str(item['requested_minutes']) + '" required> minutes<br><br>')
        else:
            forms += '<input type="hidden" name="minutes" value="1">'
        label = {'approve': 'Approve access', 'deny': 'Deny request', 'revoke': 'Revoke access'}[action]
        style = {'approve': '', 'deny': ' class="secondary"', 'revoke': ' class="danger"'}[action]
        forms += '<button' + style + ' type="submit">' + label + '</button></form>'
    availability = ('A reviewed site adapter is available for this account and task. '
                    'This does not mean the account is signed in. A private owner sign-in step may be required.'
                    if item.get('account_execution_available') is True else
                    'This site adapter is not connected or qualified yet. Approval records permission only.')
    return shell('Review account access', '<section class="card"><dl>' + fields + '</dl>'
                 '<div class="actions">' + forms + '</div></section>'
                 '<p class="note">' + availability + ' '
                 'The agent receives no password, token or browser cookies. Booking and payment require separate approval.</p>'
                 '<p class="ref"><small>Request ' + item['request_id'] + '</small></p><a href="/">All requests</a>')


def listing(items: list[dict]) -> str:
    links = ''.join('<li><a href="/review/' + item['request_id'] + '">' + html.escape(item['account']) + ' · ' +
                    html.escape(scope_name(item['scope'])) + '</a><br><small>' +
                    html.escape(item['status']) + ' · ' + moment(item['requested_at']) + '</small></li>' for item in items)
    return shell('Account access requests', '<section class="card">' + ('<ul>' + links + '</ul>' if links else
                 '<h2>No access requests yet</h2><p>Requests from the agent will appear here and in your private Mattermost messages.</p>') +
                 '</section><p>Approvals apply to the displayed account, task and duration. Purchases and bookings are separate.</p>')


def sign_in(login_url: str) -> str:
    return shell('Sign in to approve access', '<section class="card"><p>Use your existing Mattermost account. '
                 'Only the configured owner can review and approve these requests.</p><a href="' + html.escape(login_url, quote=True) +
                 '" target="_blank" rel="noopener noreferrer">Sign in to Mattermost</a>'
                 '<p>After signing in, return to this tab and reload the page.</p></section>')
