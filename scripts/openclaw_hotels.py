"""Single-page hotel discovery via a self-service provider, without booking authority."""
from __future__ import annotations

import json
import math
import urllib.parse

ENDPOINT = 'https://serpapi.com/search.json'
RATE_FIELDS = ('display_price', 'lowest', 'before_taxes_fees', 'after_taxes_fees')


def text(value, limit):
    return value[:limit] if isinstance(value, str) else None


def rate(value):
    if not isinstance(value, dict): return None
    result = {}
    for key in RATE_FIELDS:
        if isinstance(value.get(key), str): result[key] = value[key][:100]
        amount = value.get('extracted_' + key)
        if type(amount) in {int, float} and math.isfinite(amount) and 0 <= amount <= 100000000:
            result['extracted_' + key] = amount
    return result or None


def link(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value): return None
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.hostname == 'serpapi.com' or parsed.hostname.endswith('.serpapi.com')): return None
    # Returned as untrusted evidence only; the facade never follows a result URL.
    return value


def read(arguments, token, deadline, http):
    ages = [max(1, age) for age in arguments['child_ages']]
    params = {'engine': 'google_hotels', 'q': arguments['destination'],
              'check_in_date': arguments['check_in'], 'check_out_date': arguments['check_out'],
              'adults': arguments['adults'], 'children': len(ages), 'currency': arguments['currency'],
              'gl': 'dk', 'hl': 'en'}
    if ages: params['children_ages'] = ','.join(str(age) for age in ages)
    raw, _ = http(ENDPOINT + '?' + urllib.parse.urlencode({**params, 'api_key': token}), None, {}, deadline)
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or 'error' in payload
            or not isinstance(payload.get('search_metadata'), dict)
            or payload['search_metadata'].get('status') != 'Success'):
        raise ValueError('Hotel search failed')
    # Refuse results for a different destination, stay, currency or occupancy.
    echoed = payload.get('search_parameters')
    if not isinstance(echoed, dict) or any(type(echoed.get(k)) is not type(v) or echoed[k] != v for k, v in params.items()):
        raise ValueError('Hotel search scope refused')
    rows = payload.get('properties')
    if not isinstance(rows, list): raise ValueError('Hotel results refused')
    hotels = []
    for row in rows[:5]:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip():
            raise ValueError('Hotel identity refused')
        hotels.append({'name': row['name'][:200], 'url': link(row.get('link')),
                       'description': text(row.get('description'), 500),
                       'rate_per_night': rate(row.get('rate_per_night')), 'total_rate': rate(row.get('total_rate')),
                       'price_status': 'advertised_lead_unconfirmed'})
    return {'source': 'Google Hotels via SerpApi', 'search': {
                'destination': arguments['destination'], 'check_in': arguments['check_in'],
                'check_out': arguments['check_out'], 'adults': arguments['adults'],
                'child_ages': arguments['child_ages'], 'submitted_child_ages': ages, 'currency': arguments['currency']},
            'provider_processed_at': text(payload['search_metadata'].get('processed_at'), 80), 'hotels': hotels,
            'completeness': 'At most five results from one page; coverage is partial, including when no results are returned.',
            'limitations': 'Provider cache may be up to one hour old. Preserve displayed, before-tax and after-tax labels; do not multiply nightly prices into a group quote. Room allocation, board, member discounts and final checkout total are unverified. Infants are submitted as age 1 per provider convention.'}
