"""Exercise the real travel Code nodes with synthetic family and source fixtures."""
import copy
import json
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import parse_qs, urlparse

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/travel'
TODAY = '2026-01-20'
PREFERENCES = {
    'childrenBirthDates': ['2017-02-01', '2021-03-02'],
    'windowDays': 60, 'minNights': 7, 'maxNights': 14,
    'maxTotalDKK': 10000, 'maxPerPersonNightDKK': 300,
    'minStars': 4, 'minGuestRating': 8.4, 'maxAirportKm': 80,
    'maxBeachMetres': 1000, 'maxDeals': 5, 'minPriceDropPercent': 5,
    'minDaytimeC': 25, 'minSunshineHours': 6, 'maxRainProbabilityPercent': 30,
    'maxRainMmPerDay': 1, 'maxWindKmh': 30, 'maxHistoricalWetFraction': 0.2,
    'weatherMode': 'seasonal_allowed', 'includeAllInclusivePrice': True,
}


def run_node(file, rows, contexts=None, state=None, today=TODAY):
    harness = """const fs=require('fs');const p=JSON.parse(fs.readFileSync(0,'utf8'));
const state=p.state||{};
const now={setZone:()=>({toISODate:()=>p.today}),toISO:()=>p.today+'T09:00:00+01:00'};
const input={all:()=>p.rows,first:()=>p.rows[0]};
const lookup=name=>{if(!Object.hasOwn(p.contexts,name))throw Error('Unknown node');
  return {all:()=>p.contexts[name],first:()=>p.contexts[name][0]};};
try {const value=new Function('$input','$now','$','$getWorkflowStaticData',p.source)(input,now,lookup,()=>state);
  process.stdout.write(JSON.stringify({value,state}));}
catch(e){process.stdout.write(JSON.stringify({error:e.message,state}));}"""
    payload = {'source': (DIRECTORY / file).read_text(), 'rows': rows,
               'contexts': contexts or {}, 'state': state or {}, 'today': today}
    result = subprocess.run(['node', '-e', harness], input=json.dumps(payload),
                            text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


def request(ages=(9, 4)):
    return {'url': 'https://www.afbudsrejser.dk/charter.json',
            'ageString': '42-42-' + '-'.join(map(str, ages)), 'childAges': list(ages),
            'firstDeparture': '2026-01-21', 'lastDeparture': '2026-03-21',
            'preferences': copy.deepcopy(PREFERENCES), 'checkedAt': TODAY + 'T09:00:00+01:00'}


def offer():
    return {'id': 'synthetic-offer', 'hotel': {
        'id': '101', 'name': 'Synthetic family hotel', 'rating': 4, 'guest_rating': 4.5,
        'room_name': 'Family apartment, breakfast',
        'url': '/hotel/101/1/family?age=42-42-9-4&offer_id=synthetic'},
        'supplier': {'code': 'SYNTHETIC_DK', 'name': 'Synthetic supplier'},
        'destination': {'name': 'Synthetic beach', 'country_name': 'Synthetic country'},
        'metabool': ['pool', 'childspool', 'direct_flight', 'breakfast'],
        'total_price': 6400, 'price': 999, 'supplierVerified': True,
        'weather': {'verified': True, 'basis': 'forecast', 'minDailyHigh': 25, 'maxDailyHigh': 28, 'minSunshineHours': 8},
        'allInclusive': {'status': 'available', 'totalDKK': 10500, 'roomName': 'Family room'},
        'providerUrl': 'https://www.solfaktor.dk/synthetic-family-hotel',
        'supplierCheckedAt': TODAY + 'T09:00:00+01:00', 'advertisedTotal': 6200,
        'origin': {'airport_iata': 'CPH', 'dt': '2026-01-25T08:00:00Z'},
        'return': {'dt': '2026-02-01T17:00:00Z'}, 'number_of_nights': 7,
        'out_legs': 1, 'return_legs': 1, 'distance_airport': 25,
        'distance_beach': 250, 'temp_air_day': 22}


def response(offers, req=None):
    req = req or request()
    return {'offers': offers, 'n_search_results': len(offers), 'request': req,
            'dataLayer': {'organization': {'country': 'DK'},
                          'page': {'query': {'age': [req['ageString']], 'orig': ['CPH']}}}}


def select(offers=None, req=None, state=None, raw=None):
    req = req or request()
    raw = raw or response([offer()] if offers is None else offers, req)
    return run_node('select-bargains.js', [{'json': raw}],
                    {'Build exact age searches': [{'json': req}]}, state)


class TravelCodeTests(unittest.TestCase):
    def assert_suppressed(self, result):
        self.assertTrue('error' in result or result.get('value') == [], result)

    def test_horizon_queries_cover_both_birthdays_and_preserve_exact_ages(self):
        result = run_node('build-searches.js', [{'json': {'preferences': PREFERENCES}}])
        searches = [row['json'] for row in result['value']]
        self.assertEqual({tuple(s['childAges']) for s in searches}, {(8, 4), (9, 4), (9, 5)})
        for search in searches:
            query = parse_qs(urlparse(search['url']).query)
            self.assertEqual(query['age'], [search['ageString']])
            self.assertEqual(query['orig'], ['CPH'])
            self.assertEqual(query['edepdate'], ['2026-01-21'])
            self.assertIn(query['ldepdate'], [['2026-03-21'], ['2026-01-28']])
            self.assertEqual(query['guest_rating'], ['42'])

    def test_impossible_birthdate_and_out_of_bounds_preferences_fail_closed(self):
        for change in [{'childrenBirthDates': ['2017-02-30', '2021-03-02']},
                       {'childrenBirthDates': ['2025-01-01', '2021-03-02']},
                       {'windowDays': 1000}, {'maxDeals': 6}, {'minNights': 15, 'maxNights': 7}]:
            p = {**PREFERENCES, **change}
            with self.subTest(change=change):
                self.assertIn('error', run_node('build-searches.js', [{'json': {'preferences': p}}]))

    def test_exact_birthday_at_copenhagen_return_controls_eligibility(self):
        self.assertEqual(len(select()['value']), 1)  # Turns nine on 1 February.
        self.assert_suppressed(select(req=request((8, 4))))
        o = offer()
        o['origin']['dt'] = '2026-01-24T23:30:00Z'
        o['return']['dt'] = '2026-01-31T23:30:00Z'  # 1 February in Copenhagen.
        self.assertEqual(len(select([o])['value']), 1)
        o['return']['dt'] = '2026-01-31T22:30:00Z'
        self.assert_suppressed(select([o]))

    def test_actual_family_total_is_used_instead_of_advertised_person_price(self):
        result = select()
        digest = result['value'][0]['json']
        self.assertEqual(digest['deals'][0]['total'], 6400)
        self.assertIn('6.400 kr. for alle fire', digest['message'])
        self.assertNotIn('3.996 kr.', digest['message'])
        self.assertEqual(result['state'], {})

    def test_summary_first_alert_keeps_structured_details_and_price_weather_qualifications(self):
        text = select()['value'][0]['json']['message']
        self.assertEqual(text.split('\n')[0], '1 familieferie fra København: fly + hotel fra 6.400 kr. for alle fire.')
        self.assertIn('### 1. [Synthetic family hotel]', text)
        self.assertIn('- **Rejse:** 2026-01-25 → 2026-02-01', text)
        self.assertIn('- **Hotel:** Synthetic beach, Synthetic country', text)
        self.assertIn('All inclusive, fly + hotel: 10.500 kr. for familien', text)
        self.assertIn('**Før bestilling**\n\n- ', text)
        self.assertIn('Bagage, transfer, lokale afgifter og øvrige tillæg er ikke bekræftet', text)
        self.assertIn('**Kilder og vejr**\n\n- ', text)
        o = offer(); o['weather']['basis'] = 'historical'
        self.assertIn('Sæsonestimat fra 5 års historiske data (ikke en vejrudsigt)', select([o])['value'][0]['json']['message'])

    def test_weak_quality_missing_or_unknown_values_do_not_notify(self):
        changes = [('total_price', 10001), ('total_price', '6400'), ('distance_airport', 81),
                   ('distance_airport', None), ('distance_airport', -1),
                   ('distance_beach', 1001), ('distance_beach', -1),
                   ('out_legs', 2), ('return_legs', None), ('number_of_nights', 6)]
        for key, value in changes:
            o = offer(); o[key] = value
            with self.subTest(key=key, value=value):
                self.assert_suppressed(select([o]))
        for key, value in [('rating', 3), ('rating', None), ('rating', 99),
                           ('guest_rating', 4.1), ('guest_rating', None), ('guest_rating', 99)]:
            o = offer(); o['hotel'][key] = value
            with self.subTest(hotel=key, value=value):
                self.assert_suppressed(select([o]))

    def test_high_price_per_person_night_and_wrong_origin_are_rejected(self):
        req = request(); req['preferences']['maxPerPersonNightDKK'] = 200
        self.assert_suppressed(select(req=req))
        o = offer(); o['origin']['airport_iata'] = 'BLL'
        self.assert_suppressed(select([o]))
        for feature in ['pool', 'childspool', 'direct_flight']:
            o = offer(); o['metabool'].remove(feature)
            self.assert_suppressed(select([o]))
        o = offer(); o['metabool'].append('adultsonly')
        self.assert_suppressed(select([o]))

    def test_missing_identity_or_invalid_dates_do_not_become_deals(self):
        for kind in ['hotel_name', 'supplier_name', 'departure', 'return', 'outside_window']:
            o = offer()
            if kind == 'hotel_name': del o['hotel']['name']
            elif kind == 'supplier_name': del o['supplier']['name']
            elif kind == 'departure': o['origin']['dt'] = 'unparseable'
            elif kind == 'return': o['return']['dt'] = o['origin']['dt']
            else: o['origin']['dt'] = '2026-01-19T08:00:00Z'
            with self.subTest(kind=kind):
                self.assert_suppressed(select([o]))

    def test_source_market_composition_and_unknown_schema_fail_closed(self):
        for change in ['market', 'ages', 'origin', 'count', 'offers', 'query']:
            raw = response([offer()])
            if change == 'market': raw['dataLayer']['organization']['country'] = 'NO'
            elif change == 'ages': raw['dataLayer']['page']['query']['age'] = ['42-42']
            elif change == 'origin': raw['dataLayer']['page']['query']['orig'] = ['BLL']
            elif change == 'count': raw['n_search_results'] = '1'
            elif change == 'offers': raw['offers'] = None
            else: del raw['dataLayer']['page']['query']
            with self.subTest(change=change):
                self.assertIn('error', select(raw=raw))

    def test_untrusted_or_mismatched_family_links_are_rejected(self):
        for url in ['https://evil.invalid/hotel/?age=42-42-9-4',
                    '//evil.invalid/hotel/?age=42-42-9-4', '/hotel/x?age=42-42',
                    '/hotel/x?offer_id=no-age', '/hotel/x?age=%broken',
                    '/hotel/x?age=42-42-9-4&x=)\n@channel']:
            o = offer(); o['hotel']['url'] = url
            with self.subTest(url=url):
                self.assert_suppressed(select([o]))

    def test_empty_results_emit_nothing_and_leave_delivery_state_unchanged(self):
        state = {'sent': {'old': {'price': 5000, 'returnDay': '2026-05-01'}}}
        result = select([], state=state)
        self.assertEqual(result['value'], [])
        self.assertEqual(result['state'], state)

    def test_cached_unverified_quotes_never_become_family_totals(self):
        o = offer(); del o['supplierVerified']
        self.assertEqual(select([o])['value'], [])
        o = offer(); o['providerUrl'] = 'https://evil.invalid/quote'
        self.assertEqual(select([o])['value'], [])

    def test_identical_offers_and_hotels_are_deduplicated_with_cheapest_first(self):
        a = offer(); b = copy.deepcopy(a); b['total_price'] = 6000
        c = copy.deepcopy(a); c['hotel']['id'] = '102'; c['total_price'] = 7000
        deals = select([a, b, c, a])['value'][0]['json']['deals']
        self.assertEqual([d['total'] for d in deals], [6000, 7000])

    def test_repeated_deal_requires_at_least_five_percent_drop(self):
        original = select()['value'][0]['json']['deals'][0]
        state = {'sent': {original['key']: {'price': 6400, 'returnDay': original['returnDay']}}}
        for price in [6400, 7000, 6081]:
            o = offer(); o['total_price'] = price
            with self.subTest(price=price):
                self.assertEqual(select([o], state=state)['value'], [])
        o = offer(); o['total_price'] = 6080
        digest = select([o], state=state)['value'][0]['json']
        self.assertEqual(digest['deals'][0]['previousPrice'], 6400)
        self.assertEqual(state['sent'][original['key']]['price'], 6400)

    def test_only_confirmed_mattermost_post_marks_deals_and_prunes_old_state(self):
        digest = select()['value'][0]['json']
        channel = re.search(r"receipt.channel_id!=='([^']+)'", (DIRECTORY / 'remember-delivery.js').read_text())[1]
        contexts = {'Select new bargains': [{'json': digest}]}
        state = {'sent': {'expired': {'price': 1000, 'returnDay': '2026-01-01'}}}
        for receipt in [{}, {'id': 'p' * 26, 'channel_id': 'wrong'},
                        {'id': '', 'channel_id': channel}, {'id': 123, 'channel_id': channel}]:
            with self.subTest(receipt=receipt):
                result = run_node('remember-delivery.js', [{'json': receipt}], contexts, state)
                self.assertIn('error', result)
                self.assertEqual(result['state'], state)
        result = run_node('remember-delivery.js', [{'json': {'id': 'p' * 26, 'channel_id': channel}}], contexts, state)
        self.assertTrue(result['value'][0]['json']['delivered'])
        self.assertNotIn('expired', result['state']['sent'])
        self.assertEqual(result['state']['sent'][digest['deals'][0]['key']]['price'], 6400)


if __name__ == '__main__':
    unittest.main()
