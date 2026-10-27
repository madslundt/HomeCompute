"""Finite operations on the existing Hygge shopping list and discount watches.

The caller owns a durable request ledger and serializes writes. HA todo has no
atomic compare-and-swap: the digest check detects already stale edits, while
readback detects uncertainty. Neither this module nor its transport retries.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Callable, Protocol
import unicodedata
from uuid import UUID

from semantic_retrieval import SemanticRanker, default_ranker

ENTITY = 'todo.indkob'
SCHEMA = 'hygge-shopping/v1'
MARKER = 'household_request_id'


class Client(Protocol):
    def call(self, command: dict[str, Any]) -> Any: ...
    def discounts(self) -> dict[str, Any]: ...


def text(value: Any, maximum: int, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or value != value.strip()
            or not empty and not value or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid household text')
    return value


def digest(item: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def normalized(value: str) -> str:
    value = ''.join(c for c in unicodedata.normalize('NFD', value.lower()) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9æøå]+', ' ', value).strip()


def envelope(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get('description', '') or ''
    if not isinstance(raw, str) or len(raw.encode()) > 16384:
        raise ValueError('Shopping metadata unavailable')
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get('schema') != SCHEMA:
        return {'schema': SCHEMA, 'amount': 1, **({'note': raw} if raw else {}), 'metadata': {}}
    result = deepcopy(parsed)
    if (type(result.get('amount', 1)) is not int or not 1 <= result.get('amount', 1) <= 9999
            or not isinstance(result.get('metadata', {}), dict)):
        raise ValueError('Shopping metadata unavailable')
    result.setdefault('amount', 1)
    result.setdefault('metadata', {})
    return result


def watched(item: dict[str, Any]) -> bool:
    return envelope(item)['metadata'].get('watch_discounts') is True


def names(item: dict[str, Any]) -> list[str]:
    original = envelope(item)['metadata'].get('selected_offer_original_name')
    return [item['summary'], *([original] if isinstance(original, str) and original else [])]


def result(**values: Any) -> dict[str, Any]:
    value = {'source': 'home-assistant:todo.indkob', 'retrieved_at': datetime.now(timezone.utc).isoformat(),
             'untrusted_content': True, **values}
    for key in ('items', 'offers'):
        while len(json.dumps(value, allow_nan=False).encode()) > 22000 and value.get(key):
            value[key].pop(); value['truncated'] = True
    return value


def projected(item: dict[str, Any]) -> dict[str, Any]:
    meta = envelope(item)
    row = {'item_id': item['uid'], 'name': item['summary'][:100], 'status': item['status'],
           'amount': meta['amount'], 'category': str(meta.get('category', ''))[:100],
           'note': str(meta.get('note', ''))[:1000], 'watched': watched(item), 'expected_digest': digest(item)}
    maximum = meta['metadata'].get('watch_max_unit_price')
    if type(maximum) in {float, int} and math.isfinite(maximum) and maximum > 0:
        row['max_unit_price'] = maximum
    original = meta['metadata'].get('selected_offer_original_name')
    if isinstance(original, str) and original:
        row['original_name'] = original[:100]
    return row


class Shopping:
    def __init__(self, client: Client, ranker: SemanticRanker | None = None):
        self.client = client
        self.ranker = ranker if ranker is not None else default_ranker()

    def _items(self) -> list[dict[str, Any]]:
        response = self.client.call({'type': 'todo/item/list', 'entity_id': ENTITY})
        rows = response.get('items') if isinstance(response, dict) else None
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError('Shopping list unavailable')
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('uid'), str) or not row['uid']
                    or len(row['uid']) > 128 or row['uid'] in seen or not isinstance(row.get('summary'), str)
                    or row.get('status') not in {'needs_action', 'completed'}):
                raise ValueError('Shopping list unavailable')
            seen.add(row['uid'])
        return rows

    def read(self, args: dict[str, Any]) -> dict[str, Any]:
        query = text(args.get('query', ''), 100, True).casefold()
        kind = args.get('kind', 'shopping')
        if kind not in {'shopping', 'watches', 'all'}:
            raise ValueError('Invalid shopping list kind')
        rows = [projected(i) for i in self._items() if kind == 'all' or watched(i) == (kind == 'watches')]
        if query:
            candidates = [{'id': i['item_id'], 'text': ' '.join(str(i.get(k, '')) for k in
                           ('name', 'original_name', 'category', 'note')), 'payload': i} for i in rows]
            ranked = self.ranker.rank(query, candidates, task='shopping', limit=len(candidates)) if candidates else []
            rows = [{**i['payload'], **{k: i[k] for k in ('retrieval_score', 'lexical_score', 'semantic_score')
                                       if k in i}} for i in ranked]
        return result(items=rows[:50], total=len(rows), truncated=len(rows) > 50)

    def _target(self, rows: list[dict[str, Any]], args: dict[str, Any]) -> dict[str, Any]:
        uid = text(args.get('item_id'), 128)
        expected = args.get('expected_digest')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('Fresh shopping digest required')
        found = next((i for i in rows if i['uid'] == uid), None)
        if found is None or digest(found) != expected:
            raise ValueError('Shopping item changed; read the current list first')
        return found

    def _discounts(self, args: dict[str, Any]) -> dict[str, Any]:
        uid = text(args['item_id'], 128) if 'item_id' in args else None
        query = text(args.get('query', ''), 100, True)
        raw = self.client.discounts()
        attrs = raw.get('attributes') if isinstance(raw, dict) else None
        if not isinstance(attrs, dict) or raw.get('entity_id') != 'sensor.shopping_discounts':
            raise ValueError('Discounts unavailable')
        offers = attrs.get('offers', [])
        if isinstance(offers, str):
            offers = json.loads(offers)
        if not isinstance(offers, list) or len(offers) > 5000:
            raise ValueError('Discounts unavailable')
        keys = {'id', 'todo_uid', 'item_name', 'product_name', 'store', 'price', 'currency', 'unit_price',
                'unit', 'starts_at', 'ends_at', 'checked_at', 'source'}
        selected = [o for o in offers if isinstance(o, dict) and (uid is None or o.get('todo_uid') == uid)]
        rows = []
        for offer in selected:
            row = {}
            for key in keys:
                value = offer.get(key)
                if isinstance(value, str): row[key] = value[:200]
                elif type(value) in {int, float} and math.isfinite(value): row[key] = value
            rows.append(row)
        if query:
            candidates = [{'id': str(index), 'text': ' '.join(str(row.get(k, '')) for k in
                           ('item_name', 'product_name', 'store')), 'payload': row} for index, row in enumerate(rows)]
            ranked = self.ranker.rank(query, candidates, task='shopping', limit=len(candidates)) if candidates else []
            rows = [{**i['payload'], **{k: i[k] for k in ('retrieval_score', 'lexical_score', 'semantic_score')
                                       if k in i}} for i in ranked]
        return result(source='home-assistant:sensor.shopping_discounts', offers=rows[:50], total=len(rows),
                      truncated=len(rows) > 50, updated_at=str(attrs.get('updated_at', ''))[:100],
                      freshness='Use checked_at and offer validity; a stored offer may be stale.')

    def _write(self, service: str, fields: dict[str, Any], verify: Callable[[list[dict[str, Any]]], Any],
               before_write: Callable[[], None]) -> dict[str, Any]:
        before_write()
        try:
            self.client.call({'type': 'call_service', 'domain': 'todo', 'service': service,
                              'service_data': {'entity_id': ENTITY, **fields}})
            checked = verify(self._items())
            if checked is None: raise ValueError('Unconfirmed readback')
            return result(outcome='confirmed', changed=True, **checked)
        except Exception:
            return result(outcome='uncertain', changed=None,
                          reason='Write outcome uncertain; inspect request status and current list. Do not repeat automatically.')

    def request(self, fullname: str, args: dict[str, Any], before_write: Callable[[], None] = lambda: None) -> dict[str, Any]:
        operation = fullname.removeprefix('homecompute_')
        if operation == 'shopping_list': return self.read(args)
        if operation == 'discounts_read': return self._discounts(args)
        if operation not in {'shopping_add', 'shopping_update', 'shopping_complete', 'shopping_remove', 'discount_watch', 'discount_unwatch'}:
            raise ValueError('Unknown household operation')
        request_id = text(args.get('request_id'), 36)
        try:
            if str(UUID(request_id)) != request_id: raise ValueError()
        except ValueError:
            raise ValueError('Canonical request UUID required') from None
        rows = self._items()
        if operation in {'shopping_add', 'discount_watch'}:
            name = text(args.get('name'), 100)
            watch = operation == 'discount_watch'
            amount = args.get('amount', 1)
            if type(amount) is not int or not 1 <= amount <= 9999:
                raise ValueError('Shopping amount must be an integer from 1 to 9999')
            maximum = args.get('max_unit_price')
            clear = args.get('clear_max_unit_price', False)
            if (type(clear) is not bool or maximum is not None and (type(maximum) not in {int, float}
                    or not math.isfinite(maximum) or not 0 < maximum <= 1000000) or maximum is not None and clear):
                raise ValueError('Invalid watch price ceiling')
            existing = None
            if 'item_id' in args or 'expected_digest' in args:
                if not watch: raise ValueError('Addition takes no existing item')
                existing = self._target(rows, args)
                if (not watched(existing) or existing['status'] != 'needs_action'
                        or normalized(existing['summary']) != normalized(name)):
                    raise ValueError('Watch identity does not match')
            else:
                if clear: raise ValueError('Clearing a watch ceiling requires its fresh identity')
                matches = [i for i in rows if i['status'] == 'needs_action' and watched(i) == watch
                           and any(normalized(candidate) == normalized(name) for candidate in names(i))]
                if len(matches) > 1:
                    raise ValueError('Multiple matching entries exist; choose the exact current item identity')
                if matches:
                    current = matches[0]
                    if watch and maximum is not None and envelope(current)['metadata'].get('watch_max_unit_price') != maximum:
                        raise ValueError('Read the existing watch identity before changing its price ceiling')
                    if not watch and envelope(current)['amount'] != amount:
                        raise ValueError('Item exists; use its fresh identity to update the amount')
                    return result(outcome='confirmed', changed=False, item=projected(current))
            meta = envelope(existing) if existing else {'schema': SCHEMA, 'amount': amount, 'metadata': {'added_at': datetime.now(timezone.utc).isoformat()}}
            meta['metadata'][MARKER] = request_id
            if watch: meta['metadata']['watch_discounts'] = True
            if maximum is not None: meta['metadata']['watch_max_unit_price'] = maximum
            if clear: meta['metadata'].pop('watch_max_unit_price', None)
            encoded = json.dumps(meta, ensure_ascii=False, allow_nan=False)
            fields = {'item': existing['uid'], 'description': encoded} if existing else {'item': name, 'description': encoded}
            def verify(items: list[dict[str, Any]]) -> Any:
                found = [i for i in items if envelope(i)['metadata'].get(MARKER) == request_id
                         and i['summary'] == (existing['summary'] if existing else name) and i.get('description') == encoded]
                return {'item': projected(found[0])} if len(found) == 1 else None
            return self._write('update_item' if existing else 'add_item', fields, verify, before_write)
        target = self._target(rows, args)
        if operation == 'discount_unwatch' and not watched(target):
            raise ValueError('Selected item is not a discount watch')
        if operation == 'shopping_remove' and watched(target):
            raise ValueError('Use the unwatch operation for a discount watch')
        if operation in {'shopping_remove', 'discount_unwatch'}:
            return self._write('remove_item', {'item': [target['uid']]},
                               lambda items: {'removed_item_id': target['uid']} if not any(i['uid'] == target['uid'] for i in items) else None,
                               before_write)
        meta = envelope(target)
        meta['metadata'][MARKER] = request_id
        fields = {'item': target['uid']}
        if operation == 'shopping_complete':
            completed = args.get('completed')
            if type(completed) is not bool or watched(target): raise ValueError('Completion requires an ordinary shopping item')
            fields['status'] = 'completed' if completed else 'needs_action'
            if completed: meta['metadata']['completed_at'] = datetime.now(timezone.utc).isoformat()
            else: meta['metadata'].pop('completed_at', None)
        else:
            if watched(target): raise ValueError('Use the watch operation for discount watch changes')
            if not {'name', 'amount', 'note'} & args.keys(): raise ValueError('No shopping update supplied')
            if 'name' in args:
                name = text(args['name'], 100)
                if name != target['summary']:
                    fields['rename'] = name
                    meta['metadata'].pop('selected_offer_id', None)
                    meta['metadata'].pop('selected_offer_original_name', None)
            if 'amount' in args:
                if type(args['amount']) is not int or not 1 <= args['amount'] <= 9999: raise ValueError('Invalid shopping amount')
                meta['amount'] = args['amount']
            if 'note' in args:
                note = args['note']
                if (not isinstance(note, str) or len(note) > 1000
                        or any(ord(c) < 32 and c not in '\n\t' for c in note)):
                    raise ValueError('Invalid shopping note')
                meta['note'] = note
        fields['description'] = json.dumps(meta, ensure_ascii=False, allow_nan=False)
        def verify(items: list[dict[str, Any]]) -> Any:
            found = next((i for i in items if i['uid'] == target['uid']), None)
            if (found is None or found.get('description') != fields['description']
                    or found['summary'] != fields.get('rename', target['summary'])
                    or found['status'] != fields.get('status', target['status'])): return None
            return {'item': projected(found)}
        return self._write('update_item', fields, verify, before_write)
