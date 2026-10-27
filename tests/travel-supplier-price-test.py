"""Supplier quote regressions using synthetic data and the actual n8n JavaScript."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'automations/travel'
NOW = '2026-10-25T09:00:00Z'


def fixture():
    offer = {
        'id': 'synthetic-family-offer', 'total_price': 5609, 'price_2_2': 5609,
        'hotel': {'id': '101', 'name': 'Synthetic family hotel', 'rating': 4,
                  'guest_rating': 4.5, 'room_name': 'Cached room and breakfast',
                  'url': '/hotel/101/260/family?age=42-42-8-4'},
        'supplier': {'code': 'SOLFAKTOR_DK', 'name': 'Solfaktor'},
        'origin': {'airport_iata': 'CPH', 'dt': '2026-12-04T06:10:00Z'},
        'return': {'dt': '2026-12-11T10:35:00Z'}, 'number_of_nights': 7,
        'metabool': ['pool', 'childspool', 'direct_flight'], 'out_legs': 1, 'return_legs': 1,
        'distance_airport': 11, 'distance_beach': 200,
        'destination': {'latitude': 27.2, 'longitude': 33.8},
    }
    market = {'organization': {'country': 'DK'},
              'page': {'query': {'age': ['42-42-8-4'], 'orig': ['CPH']}}}
    request = {
        'ageString': '42-42-8-4', 'childAges': [8, 4],
        'firstDeparture': '2026-10-26', 'lastDeparture': '2026-12-24',
        'checkedAt': NOW, 'dataLayer': market,
        'preferences': {'childrenBirthDates': ['2018-09-12', '2022-08-14'],
                        'windowDays': 60, 'minNights': 7, 'maxNights': 14,
                        'maxTotalDKK': 10000, 'maxPerPersonNightDKK': 300,
                        'minStars': 4, 'minGuestRating': 8.4,
                        'maxAirportKm': 80, 'maxBeachMetres': 1000, 'maxDeals': 5,
                        'minPriceDropPercent': 5, 'minDaytimeC': 25, 'minSunshineHours': 6,
                        'maxRainProbabilityPercent': 30, 'maxRainMmPerDay': 1, 'maxWindKmh': 30,
                        'maxHistoricalWetFraction': 0.2, 'weatherMode': 'seasonal_allowed'},
    }
    provider_url = ('https://www.solfaktor.dk/synthetic/family-hotel?airport=CPH'
                    '&departure=2026-12-04&return=2026-12-11&genId=1000'
                    '&room0adults=2&room0childages=8,4&meta=reisegiganten'
                    '&cs1=synthetic-cache&directFlight=true')
    context = {'offer': offer, 'request': request, 'dataLayer': market,
               'providerUrl': provider_url,
               'providerParams': {'airport': 'CPH', 'departure': '2026-12-04',
                                  'return': '2026-12-11', 'room0adults': '2',
                                  'room0childages': '8,4', 'genId': '1000',
                                  'cs1': 'synthetic-cache', 'directFlight': 'true'},
               'quoteUrl': 'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getPackage',
               'quoteBody': {'pageData': {'sapiId': 987}, 'locale': 'dk',
                             'sapiSearchData': {'departure': {'code': 'CPH'},
                                                'dates': {'startDate': '2026-12-04', 'endDate': '2026-12-11'},
                                                'roomConfiguration': [{'id': 0, 'adults': 2, 'children': 2, 'childAges': [8, 4]}]}}}
    start = {'departureAirport': 'CPH', 'destinationAirport': 'AGP',
             'itinerary': [{'departureAirport': 'CPH', 'destinationAirport': 'AGP',
                            'departureTime': '2026-12-04T06:10:00', 'arrivalTime': '2026-12-04T10:00:00'}]}
    end = {'departureAirport': 'AGP', 'destinationAirport': 'CPH',
           'itinerary': [{'departureAirport': 'AGP', 'destinationAirport': 'CPH',
                          'departureTime': '2026-12-11T10:35:00', 'arrivalTime': '2026-12-11T14:20:00'}]}
    basket = {
        'basketId': '11111111-1111-4111-8111-111111111111',
        'basketTtl': '2026-10-25T10:00:00Z', 'isExpired': False,
        'hotelId': 987, 'hotelFromDate': '2026-12-04', 'hotelToDate': '2026-12-11',
        'priceInLocalCurrencyTotal': 5820, 'priceInLocalCurrencyPerPerson': 1455,
        'selectedFlight': {'startLeg': start, 'endLeg': end},
        'selectedHotelRooms': [{'roomIndex': 0, 'roomId': 123, 'adults': 2, 'childAges': [8, 4],
                                'roomName': 'Verified family room', 'boardBasis': 'BB', 'boardBasisName': 'Breakfast'}],
        'originalRequest': {'countryId': 'DK', 'request': {'hotelId': 987,
                            'fromDate': '2026-12-04', 'toDate': '2026-12-11', 'originAirport': 'CPH',
                            'useDirectFlight': True, 'roomConfig': [{'adults': 2, 'children': 2, 'infants': 0, 'childAges': [8, 4]}]}},
        'hotelFees': {'structuredFees': None, 'rawFeesDescription': ''},
        'directFlightWasRequested': True,
    }
    quote = {'basketId': basket['basketId'], 'basketTtl': basket['basketTtl'],
             'failure': None, 'data': {'basketData': basket, 'failure': None,
             'basketValidation': {'priceOutsideElastic': True,
                                  'invalidPrice': {'requestedPrice': 5609, 'resultPrice': 5819.2203},
                                  'boardInvalidation': None}}}
    return context, quote


def run_code(filename, rows, contexts=None):
    harness = """const fs=require('fs');const p=JSON.parse(fs.readFileSync(0,'utf8'));
const NativeDate=Date;class FixedDate extends NativeDate{constructor(...a){super(...(a.length?a:[p.now]));}static now(){return NativeDate.parse(p.now);}}
const now={toISO:()=>p.now,toISODate:()=>p.now.slice(0,10),toMillis:()=>NativeDate.parse(p.now),setZone(){return this;},valueOf:()=>NativeDate.parse(p.now)};
const input={all:()=>p.rows,first:()=>p.rows[0],item:p.rows[0]};
const lookup=name=>{let rows=p.contexts[name];if(!rows)throw Error('Unknown context '+name);return{all:()=>rows,first:()=>rows[0],item:rows[0]};};
try{let value=new Function('$input','$now','$','$getWorkflowStaticData','Date',p.source)(input,now,lookup,()=>({}),FixedDate);
process.stdout.write(JSON.stringify({value}));}catch(e){process.stdout.write(JSON.stringify({error:e.message}));}"""
    payload = {'source': (DIRECTORY / filename).read_text(), 'rows': rows,
               'contexts': contexts or {}, 'now': NOW}
    result = subprocess.run(['node', '-e', harness], input=json.dumps(payload),
                            capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


def verify(context=None, quote=None):
    default_context, default_quote = fixture()
    context = context if context is not None else default_context
    quote = quote if quote is not None else default_quote
    return run_code('verify-supplier-quote.js', [{'json': quote}],
                    {'Build supplier quote': [{'json': context}]})


class SupplierPriceTests(unittest.TestCase):
    def assert_no_verified_deal(self, result):
        self.assertTrue('error' in result or result.get('value') == [], result)

    def test_fresh_supplier_total_replaces_cached_5609_with_5820(self):
        result = verify()
        rows = result['value']
        self.assertEqual(len(rows), 1)
        result_offer = rows[0]['json']['offers'][0]
        self.assertEqual(result_offer['total_price'], 5820)
        self.assertNotEqual(result_offer['total_price'], 5609)
        self.assertTrue(result_offer['supplierVerified'])
        self.assertEqual(result_offer['advertisedTotal'], 5609)
        self.assertEqual(rows[0]['json']['dataLayer']['organization']['country'], 'DK')

    def test_discovery_only_prepares_supported_supplier_family_packages(self):
        context, _ = fixture()
        context['offer']['redirect_url'] = '/charterredirect?id=synthetic&age=42-42-8-4'
        raw = {'offers': [context['offer']], 'n_search_results': 1, 'dataLayer': context['dataLayer']}
        lookup = {'Build exact age searches': [{'json': context['request']}]}
        prepared = run_code('prepare-supplier-checks.js', [{'json': raw}], lookup)['value']
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]['json']['offer']['total_price'], 5609)
        self.assertEqual(prepared[0]['json']['redirectUrl'],
                         'https://www.afbudsrejser.dk/charterredirect?id=synthetic&age=42-42-8-4')
        for kind in ['unsupported_supplier', 'adults_only', 'external_redirect']:
            invalid = copy.deepcopy(raw)
            if kind == 'unsupported_supplier': invalid['offers'][0]['supplier']['code'] = 'UNVERIFIED_DK'
            elif kind == 'adults_only': invalid['offers'][0]['metabool'].append('adultsonly')
            else: invalid['offers'][0]['redirect_url'] = 'https://evil.invalid/charterredirect'
            with self.subTest(kind=kind):
                self.assertEqual(run_code('prepare-supplier-checks.js', [{'json': invalid}], lookup)['value'], [])

    def test_provider_link_is_decoded_but_host_ages_and_trip_must_match(self):
        context, _ = fixture()
        def parse(url):
            html = '<html><a class="redirect-link mt-4" href="' + url.replace('&', '&amp;') + '">Continue</a></html>'
            return run_code('parse-supplier-link.js', [{'json': {'data': html}}],
                            {'Check holiday weather': [{'json': context}]})
        valid = parse(context['providerUrl'])['value'][0]['json']
        self.assertEqual(valid['providerUrl'], context['providerUrl'])
        self.assertEqual(valid['providerParams']['room0childages'], '8,4')
        for url in [context['providerUrl'].replace('www.solfaktor.dk', 'www.solfaktor.dk.evil.invalid'),
                    context['providerUrl'].replace('8,4', '10,10'),
                    context['providerUrl'].replace('airport=CPH', 'airport=BLL'),
                    context['providerUrl'].replace('departure=2026-12-04', 'departure=2026-12-05'),
                    context['providerUrl'].replace('directFlight=true', 'directFlight=false')]:
            with self.subTest(url=url):
                self.assert_no_verified_deal(parse(url))

    def test_quote_request_contains_exact_family_and_only_the_fixed_supplier_endpoint(self):
        context, _ = fixture()
        html = '<script>self.__next_f.push([1,"{\\\"hotelId\\\":987}"])</script>'
        result = run_code('build-supplier-quote.js', [{'json': {'data': html}}],
                          {'Read supplier link': [{'json': context}]})['value'][0]['json']
        self.assertEqual(result['quoteUrl'], 'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getPackage')
        self.assertEqual(result['quoteBody']['pageData']['sapiId'], 987)
        search = result['quoteBody']['sapiSearchData']
        self.assertEqual(search['departure']['code'], 'CPH')
        self.assertEqual(search['roomConfiguration'], [{'id': 0, 'adults': 2, 'children': 2, 'childAges': [8, 4]}])
        self.assertEqual(search['dates'], {'startDate': '2026-12-04', 'endDate': '2026-12-11'})
        self.assertTrue(result['quoteBody']['isDirectFlight'])
        for html in ['No hotel metadata', '<script>{"hotelId":987,"other":{"hotelId":999}}</script>']:
            with self.subTest(html=html):
                result = run_code('build-supplier-quote.js', [{'json': {'data': html}}],
                                  {'Read supplier link': [{'json': context}]})
                self.assert_no_verified_deal(result)

    def test_different_child_ages_or_adult_composition_are_never_verified(self):
        for changes in [{'adults': 1}, {'childAges': [10, 10]}, {'childAges': [8]}, {'childAges': [4, 8, 8]}]:
            context, quote = fixture()
            quote['data']['basketData']['selectedHotelRooms'][0].update(changes)
            with self.subTest(changes=changes):
                self.assert_no_verified_deal(verify(context, quote))

    def test_missing_flight_leg_stopover_wrong_airport_or_date_are_rejected(self):
        for kind in ['missing', 'stopover', 'origin', 'return_airport', 'outbound_date', 'return_date', 'segment_origin']:
            context, quote = fixture()
            flights = quote['data']['basketData']['selectedFlight']
            if kind == 'missing': del flights['endLeg']
            elif kind == 'stopover': flights['startLeg']['itinerary'].append(copy.deepcopy(flights['startLeg']['itinerary'][0]))
            elif kind == 'origin': flights['startLeg']['departureAirport'] = 'BLL'
            elif kind == 'return_airport': flights['endLeg']['destinationAirport'] = 'BLL'
            elif kind == 'outbound_date': flights['startLeg']['itinerary'][0]['departureTime'] = '2026-12-05T06:10:00'
            elif kind == 'segment_origin': flights['startLeg']['itinerary'][0]['departureAirport'] = 'BLL'
            else: flights['endLeg']['itinerary'][0]['departureTime'] = '2026-12-12T10:35:00'
            with self.subTest(kind=kind):
                self.assert_no_verified_deal(verify(context, quote))

    def test_missing_non_numeric_non_positive_or_excessive_price_is_rejected(self):
        for price in [None, '5820', 0, -1, 1.5, 9007199254740992]:
            context, quote = fixture()
            quote['data']['basketData']['priceInLocalCurrencyTotal'] = price
            with self.subTest(price=price):
                self.assert_no_verified_deal(verify(context, quote))

    def test_expired_or_failed_supplier_response_is_not_a_price(self):
        for kind in ['expired_flag', 'expired_ttl', 'top_failure', 'data_failure', 'missing_basket']:
            context, quote = fixture()
            if kind == 'expired_flag': quote['data']['basketData']['isExpired'] = True
            elif kind == 'expired_ttl': quote['data']['basketData']['basketTtl'] = '2026-10-25T08:59:59Z'
            elif kind == 'top_failure': quote['failure'] = 'Unavailable'
            elif kind == 'data_failure': quote['data']['failure'] = 'Unavailable'
            else: quote['data']['basketData'] = None
            with self.subTest(kind=kind):
                self.assert_no_verified_deal(verify(context, quote))

    def test_wrong_hotel_hotel_dates_or_market_are_not_verified(self):
        for key, value in [('hotelId', 999), ('hotelFromDate', '2026-12-05'), ('hotelToDate', '2026-12-12')]:
            context, quote = fixture(); quote['data']['basketData'][key] = value
            with self.subTest(key=key):
                self.assert_no_verified_deal(verify(context, quote))
        context, quote = fixture(); quote['data']['basketData']['originalRequest']['countryId'] = 'NO'
        self.assert_no_verified_deal(verify(context, quote))

    def test_unpriced_mandatory_fees_prevent_verified_total(self):
        context, quote = fixture()
        quote['data']['basketData']['hotelFees']['rawFeesDescription'] = 'Mandatory local fee payable at hotel; amount not yet known'
        self.assert_no_verified_deal(verify(context, quote))
        context, quote = fixture()
        del quote['data']['basketData']['hotelFees']
        self.assert_no_verified_deal(verify(context, quote))


if __name__ == '__main__':
    unittest.main()
