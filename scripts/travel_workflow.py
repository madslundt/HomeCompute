"""Build the weekly family travel workflow; private birthdays are supplied in n8n."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/travel'
CHANNEL = 'wxrgwf839tddxyrunhbo8ernqr'
PREFERENCES = {
    'childrenBirthDates': [], 'windowDays': 60, 'minNights': 7, 'maxNights': 14,
    'maxTotalDKK': 10000, 'maxPerPersonNightDKK': 350, 'minStars': 4,
    'minGuestRating': 8.4, 'maxAirportKm': 80, 'maxBeachMetres': 1000,
    'maxDeals': 3, 'minPriceDropPercent': 5,
    'minDaytimeC': 25, 'minSunshineHours': 6, 'maxRainProbabilityPercent': 30,
    'maxRainMmPerDay': 1, 'maxWindKmh': 30, 'maxHistoricalWetFraction': 0.2,
    'weatherMode': 'seasonal_allowed', 'includeAllInclusivePrice': True,
    'preferenceQuery': 'Family-friendly hotel with a pool for children and a nearby beach',
}


def build() -> dict[str, Any]:
    def node(name: str, kind: str, version: float, position: list[int],
             parameters: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return {'id': name.lower().replace(' ', '-'), 'name': name,
                'type': 'n8n-nodes-base.' + kind, 'typeVersion': version,
                'position': position, 'parameters': parameters, **kwargs}

    def code(name: str, file: str, position: list[int]) -> dict[str, Any]:
        return node(name, 'code', 2, position, {'mode': 'runOnceForAllItems',
                    'language': 'javaScript', 'jsCode': (DIRECTORY / file).read_text()})

    nodes = [
        node('Monday 09 Copenhagen', 'scheduleTrigger', 1.4, [0, 0],
             {'rule': {'interval': [{'field': 'weeks', 'weeksInterval': 1,
               'triggerAtDay': [1], 'triggerAtHour': 9, 'triggerAtMinute': 0}]}}),
        node('Run travel search now', 'manualTrigger', 1, [0, 180], {}),
        node('Travel preferences', 'set', 3.5, [240, 0], {'mode': 'manual',
             'assignments': {'assignments': [{'id': 'preferences', 'name': 'preferences',
                 'type': 'object', 'value': PREFERENCES.copy()}]}},
             notes='Birth dates are configured only in the live workflow. Empty template values deliberately fail. Flexible dates include school days.'),
        code('Build exact age searches', 'build-searches.js', [480, 0]),
        node('Find family package prices', 'httpRequest', 4.5, [720, 0], {
            'method': 'GET', 'url': '={{ $json.url }}', 'authentication': 'none',
            'sendHeaders': True, 'headerParameters': {'parameters': [
                {'name': 'Accept', 'value': 'application/json'},
                {'name': 'User-Agent', 'value': 'HomeCompute-family-travel/1.0'}]},
            'options': {'timeout': 25000, 'batching': {'batch': {'batchSize': 1, 'batchInterval': 1000}},
                'redirect': {'redirect': {'followRedirects': False}},
                'response': {'response': {'responseFormat': 'json'}}}},
             retryOnFail=True, maxTries=2, waitBetweenTries=1500,
             notes='Reads the 100 cheapest matching offers per age combination. Exact ages are checked at return; source totals are DKK for all four.'),
        code('Select new bargains', 'select-bargains.js', [960, 0]),
        node('Post bargains to Travel', 'mattermost', 1, [1200, 0], {
            'resource': 'message', 'operation': 'post', 'channelId': CHANNEL,
            'message': '={{ $json.message }}', 'otherOptions': {}},
             credentials={'mattermostApi': {'id': 'dfa46a194bc6ea09',
                  'name': 'HomeCompute Mattermost topic updates'}}, retryOnFail=False),
        code('Remember delivered bargains', 'remember-delivery.js', [1440, 0]),
    ]
    def read(name: str, url: str, *, text: bool = False, quote: bool = False) -> dict[str, Any]:
        parameters = {'method': 'POST' if quote else 'GET', 'url': url, 'authentication': 'none',
            'options': {'timeout': 45000 if quote else 20000,
                'batching': {'batch': {'batchSize': 1, 'batchInterval': 1000}},
                'redirect': {'redirect': {'followRedirects': False}},
                'response': {'response': {'responseFormat': 'text' if text else 'json',
                                         **({'outputPropertyName': 'data'} if text else {})}}}}
        if quote:
            parameters.update(sendBody=True, specifyBody='json', jsonBody='={{ JSON.stringify($json.quoteBody) }}')
        return node(name, 'httpRequest', 4.5, [0, 0], parameters,
                    retryOnFail=False, onError='continueRegularOutput')
    nodes[5:5] = [
        code('Prepare supplier checks', 'prepare-supplier-checks.js', [0, 0]),
        read('Read supplier redirect', '={{ $json.redirectUrl }}', text=True),
        code('Read supplier link', 'parse-supplier-link.js', [0, 0]),
        read('Read supplier hotel', '={{ $json.providerUrl }}', text=True),
        code('Build supplier quote', 'build-supplier-quote.js', [0, 0]),
        read('Reprice family package', '={{ $json.quoteUrl }}', quote=True),
        code('Verify supplier quote', 'verify-supplier-quote.js', [0, 0]),
    ]
    nodes[5]['notes'] = 'Verify at most six distinct Solfaktor hotels. Providers without a current family quote check are omitted.'
    nodes[10]['notes'] = 'Requests a nonbinding package quote with exact child ages. No customer details, payment or reservation submission.'
    nodes[6:6] = [
        read('Read destination weather', '={{ $json.weatherUrl }}'),
        code('Check holiday weather', 'check-weather.js', [0, 0]),
    ]
    board_read = read('Read all-inclusive options', '={{ $json.boardOptionsUrl }}')
    board_read['parameters'].update(sendHeaders=True, headerParameters={'parameters': [
        {'name': 'Cookie', 'value': '={{ $json.boardCookie }}'}]})
    board_index = next(i for i, n in enumerate(nodes) if n['name'] == 'Select new bargains')
    nodes[board_index:board_index] = [board_read,
        code('Add all-inclusive price', 'add-all-inclusive-price.js', [0, 0])]
    preference_index = next(i for i, n in enumerate(nodes) if n['name'] == 'Select new bargains')
    nodes[preference_index:preference_index] = [
        code('Prepare hotel preference search', 'prepare-preference-search.js', [0, 0]),
        node('Use hotel preferences', 'if', 2.2, [0, 0], {'conditions': {
            'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 2},
            'conditions': [{'id': 'enabled', 'leftValue': '={{ $json.embeddingEnabled }}',
                'rightValue': True, 'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
            'combinator': 'and'}, 'options': {}}),
        node('Embed hotel preferences', 'httpRequest', 4.5, [0, 0], {
            'method': 'POST', 'url': 'https://ai.home.arpa/v1/embeddings', 'authentication': 'none',
            'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.embeddingBody) }}',
            'options': {'timeout': 8000, 'redirect': {'redirect': {'followRedirects': False}},
                'response': {'response': {'responseFormat': 'json'}}}},
            retryOnFail=False, onError='continueRegularOutput',
            notes='One bounded local embedding batch; unavailable or invalid vectors retain the cheapest eligible hotel ordering.'),
        code('Apply hotel preferences', 'apply-preference-search.js', [0, 0]),
    ]
    for index, item in enumerate(nodes[2:], 1):
        item['position'] = [index * 240, 0]
    edges = [(nodes[0]['name'], nodes[2]['name']), (nodes[1]['name'], nodes[2]['name'])]
    edges.extend((nodes[i]['name'], nodes[i+1]['name']) for i in range(2, len(nodes)-1))
    connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in edges}
    connections['Use hotel preferences']['main'].append([
        {'node': 'Apply hotel preferences', 'type': 'main', 'index': 0}])
    groups = [
        {'id': 'family-offers', 'name': 'Find family offers',
         'description': 'Find family offers and require 25°C, sunshine and little rain using forecasts or labelled historical averages.',
         'nodeNames': [n['name'] for n in nodes[2:8]]},
        {'id': 'supplier-prices', 'name': 'Check current prices',
         'description': 'Recalculate flight and hotel totals for the actual family and add optional all-inclusive alternatives.',
         'nodeNames': [n['name'] for n in nodes[8:16]]},
        {'id': 'bargain-alerts', 'name': 'Deliver new bargains',
         'description': 'Send up to three distinct hotels to Travel; remember only confirmed deliveries and meaningful price drops.',
         'nodeNames': [n['name'] for n in nodes[16:]]},
    ]
    return {'name': 'Travel · weekly family holiday bargains', 'active': False,
            'nodes': nodes, 'connections': connections, 'nodeGroups': groups,
            'settings': {'executionOrder': 'v1', 'timezone': 'Europe/Copenhagen',
                'executionTimeout': 600, 'saveDataSuccessExecution': 'none',
                'saveDataErrorExecution': 'none', 'saveManualExecutions': False,
                'saveExecutionProgress': False}}


def sdk(definition: dict[str, Any]) -> str:
    lines = ["import { workflow, node, trigger, expr } from '@n8n/workflow-sdk';"]
    handles: dict[str, str] = {}
    for index, item in enumerate(definition['nodes']):
        handle = 'step' + str(index)
        handles[item['name']] = handle
        config = {k: v for k, v in item.items() if k not in {'id', 'type', 'typeVersion'}}
        sample = {'preferences': PREFERENCES, 'message': 'Family holiday deals',
                  'url': 'https://www.afbudsrejser.dk/charter.json', 'offers': [],
                  'redirectUrl': 'https://www.afbudsrejser.dk/charterredirect',
                  'providerUrl': 'https://www.solfaktor.dk/example',
                  'quoteUrl': 'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getPackage', 'quoteBody': {},
                  'weatherUrl': 'https://api.open-meteo.com/v1/forecast',
                  'boardOptionsUrl': 'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getHotelRooms', 'boardCookie': '',
                  'embeddingEnabled': True,
                  'embeddingBody': {'model': 'embedding', 'encoding_format': 'float', 'input': ['sample query', 'sample hotel']},
                  'id': 'sample', 'channel_id': CHANNEL, 'delivered': True}
        encoded = json.dumps({'type': item['type'], 'version': item['typeVersion'],
                              'config': config, 'output': [sample]}, ensure_ascii=False)
        encoded = re.sub(r'"(=(?:[^"\\]|\\.)*)"',
            lambda m: 'expr(' + json.dumps(json.loads('"' + m[1] + '"')[1:]) + ')', encoded)
        factory = 'trigger' if item['type'].endswith(('scheduleTrigger', 'manualTrigger')) else 'node'
        lines.append(f'const {handle} = {factory}({encoded});')
    lines.append("export default workflow('homecompute-family-travel', " + json.dumps(definition['name']) + ')')
    for source, outputs in definition['connections'].items():
        for index, edges in enumerate(outputs['main']):
            for edge in edges:
                lines.append(f"  .add({handles[source]}.output({index}).to({handles[edge['node']]}))")
    for group in definition['nodeGroups']:
        members = ','.join(handles[name] for name in group['nodeNames'])
        lines.append('  .group(' + json.dumps(group['name']) + ', [' + members + '], {description:'
                     + json.dumps(group['description'], ensure_ascii=False) + '})')
    return '\n'.join(lines) + ';\n'


if __name__ == '__main__':
    workflow = build()
    (DIRECTORY / 'n8n-workflow.json').write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + '\n')
    (DIRECTORY / 'n8n-workflow.ts').write_text(sdk(workflow))
