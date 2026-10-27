"""Exercise optional all-inclusive enrichment without changing any supplier selection."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'automations/travel/add-all-inclusive-price.js'


def fixtures():
    context = {
        'offers': [{'id': 'synthetic-offer', 'total_price': 12615, 'supplierVerified': True,
                    'hotel': {'name': 'Synthetic family hotel', 'room_name': 'Base room, breakfast'}}],
        'basketId': '11111111-1111-4111-8111-111111111111',
        'selectedRoom': {'roomId': 100, 'boardSelect': '100~BB~R', 'boardBasis': 'BB', 'roomName': 'Base room'},
        'boardCookie': 'creatorSignature=00000000-0000-0000-0000-000000000000',
        'boardOptionsUrl': 'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getHotelRooms?basketId=synthetic',
        'request': {'ageString': '42-42-8-4', 'childAges': [8, 4]},
        'dataLayer': {'organization': {'country': 'DK'}}, 'n_search_results': 1,
    }
    baseline = {'boardSelect': '100~BB~R', 'boardBasis': 'BB',
                'boardBasisName': 'Breakfast', 'availability': 1, 'upgradePriceInLocaleCurrency': 0}
    inclusive = {'boardSelect': '100~AI~R', 'boardBasis': 'AI',
                 'boardBasisName': 'All inclusive', 'availability': 1, 'upgradePriceInLocaleCurrency': 510}
    response = {'basketId': context['basketId'], 'failure': None,
                'data': [{'roomIndex': 0, 'roomInfo': {'availableHotelRooms': [
                    {'roomId': 100, 'roomName': 'Base room', 'availableBoards': [baseline, inclusive]}
                ]}}]}
    return context, response


def enrich(context=None, response=None):
    default_context, default_response = fixtures()
    context = context if context is not None else default_context
    response = response if response is not None else default_response
    harness = """const fs=require('fs');const p=JSON.parse(fs.readFileSync(0,'utf8'));
const input={all:()=>[{json:p.response}]};
const lookup=name=>{if(name!=='Verify supplier quote')throw Error('Unexpected node');return {all:()=>[{json:p.context}]};};
const value=new Function('$input','$',p.source)(input,lookup);process.stdout.write(JSON.stringify(value));"""
    payload = {'source': SOURCE.read_text(), 'context': context, 'response': response}
    process = subprocess.run(['node', '-e', harness], input=json.dumps(payload),
                             capture_output=True, text=True, check=True, timeout=10)
    rows = json.loads(process.stdout)
    return rows[0]['json']


def rooms(response):
    return response['data'][0]['roomInfo']['availableHotelRooms']


class AllInclusiveOptionTests(unittest.TestCase):
    def test_actual_supplier_option_adds_510_to_12615_for_the_whole_family(self):
        result = enrich()
        base = result['offers'][0]
        self.assertEqual(base['total_price'], 12615)
        self.assertEqual(base['allInclusive'], {'status': 'available', 'totalDKK': 13125,
                                               'roomName': 'Base room', 'roomChanged': False})
        self.assertEqual(base['hotel']['room_name'], 'Base room, breakfast')
        self.assertEqual(result['request']['childAges'], [8, 4])

    def test_cheapest_all_inclusive_room_is_named_when_it_changes(self):
        context, response = fixtures()
        cheaper = copy.deepcopy(rooms(response)[0])
        cheaper.update(roomId=101, roomName='Pool-view double room')
        cheaper['availableBoards'] = [{'boardBasis': 'AI', 'boardSelect': '101~AI~R',
                                      'availability': 9, 'upgradePriceInLocaleCurrency': 505}]
        rooms(response).append(cheaper)
        option = enrich(context, response)['offers'][0]['allInclusive']
        self.assertEqual(option['totalDKK'], 13120)
        self.assertTrue(option['roomChanged'])
        self.assertEqual(option['roomName'], 'Pool-view double room')

    def test_selected_board_upgrade_is_subtracted_from_option_delta(self):
        context, response = fixtures()
        rooms(response)[0]['availableBoards'][0]['upgradePriceInLocaleCurrency'] = 120
        option = enrich(context, response)['offers'][0]['allInclusive']
        self.assertEqual(option['totalDKK'], 12615 + 510 - 120)

    def test_valid_cheaper_all_inclusive_option_can_have_a_negative_delta(self):
        context, response = fixtures()
        rooms(response)[0]['availableBoards'][1]['upgradePriceInLocaleCurrency'] = -100
        self.assertEqual(enrich(context, response)['offers'][0]['allInclusive']['totalDKK'], 12515)

    def test_sold_out_all_inclusive_is_unavailable_and_keeps_base_trip(self):
        context, response = fixtures()
        rooms(response)[0]['availableBoards'][1]['availability'] = 0
        result = enrich(context, response)
        self.assertEqual(result['offers'][0]['total_price'], 12615)
        self.assertEqual(result['offers'][0]['allInclusive']['status'], 'unavailable')
        self.assertIsNone(result['offers'][0]['allInclusive']['totalDKK'])

    def test_half_or_full_board_is_not_reported_as_all_inclusive(self):
        context, response = fixtures()
        rooms(response)[0]['availableBoards'][1]['boardBasis'] = 'FB'
        self.assertEqual(enrich(context, response)['offers'][0]['allInclusive']['status'], 'unavailable')

    def test_failed_or_wrong_basket_enrichment_preserves_verified_base_and_marks_unknown(self):
        for changes in [{'failure': 'Unavailable'}, {'basketId': 'different-basket'},
                        {'data': None}, {'data': []}]:
            context, response = fixtures(); response.update(changes)
            with self.subTest(changes=changes):
                result = enrich(context, response)
                self.assertEqual(result['offers'][0]['total_price'], 12615)
                self.assertTrue(result['offers'][0]['supplierVerified'])
                self.assertEqual(result['offers'][0]['allInclusive']['status'], 'unknown')

    def test_missing_selected_board_baseline_cannot_produce_an_upgrade_total(self):
        context, response = fixtures()
        rooms(response)[0]['availableBoards'].pop(0)
        self.assertEqual(enrich(context, response)['offers'][0]['allInclusive']['status'], 'unknown')

    def test_unknown_all_inclusive_prices_or_availability_are_not_guessed(self):
        for key, value in [('upgradePriceInLocaleCurrency', None), ('upgradePriceInLocaleCurrency', '510'),
                           ('availability', None), ('availability', 'available')]:
            context, response = fixtures(); rooms(response)[0]['availableBoards'][1][key] = value
            with self.subTest(key=key, value=value):
                option = enrich(context, response)['offers'][0]['allInclusive']
                self.assertEqual(option['status'], 'unknown')
                self.assertIsNone(option['totalDKK'])

    def test_already_included_all_inclusive_uses_base_total_even_if_options_unavailable(self):
        context, response = fixtures()
        context['selectedRoom'].update(boardBasis='AI', boardSelect='100~AI~R')
        response['failure'] = 'OptionsUnavailable'
        option = enrich(context, response)['offers'][0]['allInclusive']
        self.assertEqual(option, {'status': 'included', 'totalDKK': 12615, 'roomName': 'Base room'})

    def test_anonymous_basket_identity_and_cookie_do_not_leak_to_downstream_items(self):
        result = enrich()
        for key in ['basketId', 'boardCookie', 'boardOptionsUrl', 'selectedRoom']:
            self.assertNotIn(key, result)
        self.assertEqual(result['dataLayer']['organization']['country'], 'DK')


if __name__ == '__main__':
    unittest.main()
