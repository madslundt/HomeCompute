"""Patch only reviewed notification destination literals in freshly inspected workflows."""
from copy import deepcopy
import json
import sys

DESTINATIONS = {
    'hrnwimmho7b4fkaa5xcm9d9i8h': 'fth7yi7ea7yutyscphhbjgehce',
    'ttnxhn41ytndicu99dh5nbnxnw': 'fth7yi7ea7yutyscphhbjgehce',
    'pc5q5ko9e3d7zpixr74j49u7uh': 'foxof7h57jrwmcqacmafjw713c',
    'ten7n1oxfinpmkazdx3rqbhdby': '89timi65ppfztp8n5zy9shxear',
}
NODES = {
    'U2kI7V9EGcMc14Su': ('Mirror Aula notifications to Mattermost',),
    'RjId1K2BPpv5lAVN': ('Mirror weekly review to Mattermost',),
    '86FmVCFIF86m8fLx': ('Mirror battery findings to Mattermost', 'Mirror LLM battery review to Mattermost'),
    'FC7HqrZ9UGsMJKao': ('Mirror daily news to Mattermost',),
    'WKu7Zv4dE4ztnaRY': ('Prepare pending deliveries',),
}
OLD_TARGET = dict(zip(NODES, (
    'ttnxhn41ytndicu99dh5nbnxnw', 'pc5q5ko9e3d7zpixr74j49u7uh',
    'pc5q5ko9e3d7zpixr74j49u7uh', 'ten7n1oxfinpmkazdx3rqbhdby',
    'hrnwimmho7b4fkaa5xcm9d9i8h')))


def replace(value, old):
    new = DESTINATIONS[old]
    if not isinstance(value, str) or value.count(old) + value.count(new) != 1:
        raise ValueError('Existing destination literal required')
    return value.replace(old, new)


def patch(workflow):
    operations = []
    expected = NODES[workflow['id']]
    for name in expected:
        matches = [n for n in workflow['nodes'] if n['name'] == name]
        if len(matches) != 1:
            raise ValueError('Unique reviewed destination node required')
        node = matches[0]
        before = node['parameters']
        after = deepcopy(before)
        if name == 'Prepare pending deliveries':
            if node['type'] != 'n8n-nodes-base.code':
                raise ValueError('Reviewed delivery builder required')
            after['jsCode'] = replace(after['jsCode'], OLD_TARGET[workflow['id']])
        else:
            if (node['type'] != 'n8n-nodes-base.httpRequest' or before.get('method') != 'POST'
                    or before.get('url') != 'https://mattermost.home.arpa/api/v4/posts'
                    or before.get('nodeCredentialType') != 'mattermostApi'):
                raise ValueError('Reviewed existing Mattermost transport required')
            if 'jsonBody' in before:
                # Aula's existing bounded expression contains the literal destination.
                after['jsonBody'] = replace(before['jsonBody'], OLD_TARGET[workflow['id']])
            else:
                fields = after['bodyParameters']['parameters']
                targets = [f for f in fields if f.get('name') == 'channel_id']
                if len(targets) != 1:
                    raise ValueError('Single destination field required')
                targets[0]['value'] = replace(targets[0]['value'], OLD_TARGET[workflow['id']])
        if after != before:
            operations.append({'type': 'updateNodeParameters', 'nodeName': name,
                               'parameters': after, 'replace': True})
    return operations


if __name__ == '__main__':
    print(json.dumps(patch(json.load(sys.stdin))))
