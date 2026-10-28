"""Finite household stock operations through Hygge Storage's existing owner.

Only explicit item identities and revisions can change existing stock. The HA
integration owns persistence, deduplication, category scheduling and conflicts.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Any, Callable, Protocol
from uuid import UUID

from semantic_retrieval import SemanticRanker, default_ranker

FIELDS = ('name', 'location_id', 'quantity', 'unit', 'expiry_date', 'expiry_kind')


class Client(Protocol):
    def call(self, command: dict[str, Any]) -> Any: ...
    def snapshot(self) -> dict[str, Any]: ...


def text(value: Any, maximum: int = 100, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or value != value.strip()
            or not empty and not value or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid storage text')
    return value


def identity(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Fresh storage identity required')
    return value


def quantity(value: Any, signed: bool = False) -> Decimal:
    pattern = r'-?\d{1,10}(?:\.\d{1,3})?' if signed else r'\d{1,10}(?:\.\d{1,3})?'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('Storage quantities require decimal strings with at most three decimal places')
    parsed = Decimal(value)
    if abs(parsed) > 1000000000 or signed and parsed == 0:
        raise ValueError('Storage quantity is outside the allowed range')
    return parsed


def amount(value: Decimal) -> str:
    raw = format(value, 'f')
    return raw.rstrip('0').rstrip('.') if '.' in raw else raw


def expiry(values: dict[str, Any], existing: dict[str, Any]) -> tuple[str, str]:
    day = values.get('expiry_date', existing.get('expiry_date', ''))
    if day != '':
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('Invalid storage expiry date')
        try: date.fromisoformat(day)
        except ValueError: raise ValueError('Invalid storage expiry date') from None
    kind = values.get('expiry_kind', existing.get('expiry_kind', '') if day == existing.get('expiry_date', '') and day else '')
    if kind not in {'', 'best_before', 'use_by'} or kind and not day:
        raise ValueError('An expiry kind requires a calendar date')
    return day, kind


def result(**values: Any) -> dict[str, Any]:
    import json
    value = {'source': 'home-assistant:hygge_storage', 'retrieved_at': datetime.now(timezone.utc).isoformat(),
             'untrusted_content': True, **values}
    while len(json.dumps(value, allow_nan=False).encode()) > 22000 and value.get('items'):
        value['items'].pop(); value['truncated'] = True
    return value


def projected(item: dict[str, Any]) -> dict[str, Any]:
    keys = ('id', 'name', 'location_id', 'quantity', 'unit', 'revision', 'category', 'expiry_date', 'expiry_kind', 'package_size')
    return {key: value[:100] if isinstance(value, str) else value for key in keys
            if type(value := item.get(key)) in {str, int}}


class Storage:
    def __init__(self, client: Client, ranker: SemanticRanker | None = None):
        self.client = client
        self.ranker = ranker if ranker is not None else default_ranker()

    def _snapshot(self) -> dict[str, Any]:
        snap = self.client.snapshot()
        if (not isinstance(snap, dict) or type(snap.get('revision')) is not int or snap['revision'] < 0
                or not isinstance(snap.get('locations'), list) or not isinstance(snap.get('items'), list)
                or len(snap['locations']) > 100 or len(snap['items']) > 5000):
            raise ValueError('Storage snapshot unavailable')
        seen = set()
        for entry in [*snap['locations'], *snap['items']]:
            if not isinstance(entry, dict): raise ValueError('Storage snapshot unavailable')
            eid = identity(entry.get('id'))
            if eid in seen or type(entry.get('revision')) is not int or entry['revision'] < 0:
                raise ValueError('Storage snapshot unavailable')
            text(entry.get('name')); seen.add(eid)
        locations = {v['id'] for v in snap['locations']}
        for item in snap['items']:
            if item.get('location_id') not in locations: raise ValueError('Storage snapshot unavailable')
            quantity(item.get('quantity')); text(item.get('unit'), 24)
        return snap

    def read(self, args: dict[str, Any]) -> dict[str, Any]:
        snap = self._snapshot()
        query = text(args.get('query', ''), 100, True).casefold()
        location = identity(args['location_id']) if 'location_id' in args else None
        if location and not any(i['id'] == location for i in snap['locations']):
            raise ValueError('Storage location no longer exists')
        rows = [i for i in snap['items'] if location is None or i['location_id'] == location]
        if query:
            locations = {i['id']: i['name'] for i in snap['locations']}
            candidates = [{'id': i['id'], 'text': ' '.join([str(i.get('name', '')), str(i.get('category', '')),
                           str(i.get('package_size', '')), locations[i['location_id']],
                           *[alias[:100] for alias in (i.get('aliases') if isinstance(i.get('aliases'), list) else [])[:20]
                             if isinstance(alias, str)]])[:4000],
                           'payload': projected(i)} for i in rows]
            ranked = self.ranker.rank(query, candidates, task='storage', limit=len(candidates)) if candidates else []
            rows = [{**i['payload'], **{k: i[k] for k in ('retrieval_score', 'lexical_score', 'semantic_score')
                                       if k in i}} for i in ranked]
        else:
            rows = [projected(i) for i in rows]
        return result(revision=snap['revision'], locations=[{k: v[k] for k in ('id', 'name', 'revision')} for v in snap['locations']],
                      items=rows[:50], total=len(rows), truncated=len(rows) > 50)

    @staticmethod
    def _target(snap: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        iid = identity(args.get('item_id'))
        revision = args.get('expected_revision')
        if type(revision) is not int or revision < 0: raise ValueError('Fresh storage revision required')
        target = next((i for i in snap['items'] if i['id'] == iid), None)
        if target is None or target['revision'] != revision:
            raise ValueError('Storage item changed; read its current revision first')
        return target

    def request(self, fullname: str, args: dict[str, Any], before_write: Callable[[], None] = lambda: None) -> dict[str, Any]:
        operation = fullname.removeprefix('homecompute_')
        if operation == 'storage_list': return self.read(args)
        if operation not in {'storage_add', 'storage_update', 'storage_remove', 'storage_adjust'}:
            raise ValueError('Unknown storage operation')
        reqid = text(args.get('request_id'), 36)
        try:
            if str(UUID(reqid)) != reqid: raise ValueError()
        except ValueError:
            raise ValueError('Canonical request UUID required') from None
        snap = self._snapshot()
        target = None if operation == 'storage_add' else self._target(snap, args)
        command: dict[str, Any] = {'request_id': reqid}
        if target:
            command.update(item_id=target['id'], expected_revision=target['revision'])
        if operation == 'storage_remove':
            command['action'] = 'item_remove'
            desired = None
        elif operation == 'storage_adjust':
            new_quantity = quantity(target['quantity']) + quantity(args.get('delta'), True)
            if not 0 <= new_quantity <= 1000000000: raise ValueError('Insufficient stock or quantity outside range')
            command.update(action='set', quantity=amount(new_quantity))
            desired = {**target, 'quantity': command['quantity']}
        else:
            if operation == 'storage_update' and not set(FIELDS) & args.keys(): raise ValueError('No storage update supplied')
            if target and 'unit' in args and args['unit'] != target['unit'] and 'quantity' not in args:
                raise ValueError('Changing a stock unit requires an explicit quantity in that unit')
            desired = {**(target or {}), **{key: args[key] for key in FIELDS if key in args}}
            desired['name'] = text(desired.get('name'))
            desired['location_id'] = identity(desired.get('location_id'))
            if not any(v['id'] == desired['location_id'] for v in snap['locations']): raise ValueError('Storage location no longer exists')
            desired['quantity'] = amount(quantity(desired.get('quantity')))
            desired['unit'] = text(desired.get('unit'), 24)
            desired['expiry_date'], desired['expiry_kind'] = expiry(args, target or {})
            if any(i['id'] != (target or {}).get('id') and i['location_id'] == desired['location_id']
                   and ' '.join(i['name'].casefold().split()) == ' '.join(desired['name'].casefold().split()) for i in snap['items']):
                raise ValueError('Storage item already exists in that location; use its exact identity')
            command.update(action='item_edit' if target else 'item_add', **{key: desired[key] for key in FIELDS})
            # The underlying edit supports these exact stored fields. Preserve
            # barcode links, package notes and manual categories on partial edits.
            if target:
                for key in ('barcode', 'barcodes', 'package_size', 'category'):
                    if key in target: command[key] = target[key]
        before_write()
        try:
            self.client.call({'type': 'hygge_storage/command', 'command': command})
            current = self._snapshot()
            if desired is None:
                if any(i['id'] == target['id'] for i in current['items']): raise ValueError('Unconfirmed removal')
                return result(outcome='confirmed', changed=True, removed_item_id=target['id'])
            if target:
                found = next((i for i in current['items'] if i['id'] == target['id']), None)
                if found is None or found['revision'] != target['revision'] + 1: raise ValueError('Unconfirmed revision')
            else:
                matches = [i for i in current['items'] if i['id'] not in {v['id'] for v in snap['items']}
                           and i['location_id'] == desired['location_id'] and i['name'] == desired['name']]
                if len(matches) != 1: raise ValueError('Unconfirmed addition')
                found = matches[0]
            keys = ('quantity',) if operation == 'storage_adjust' else FIELDS
            if any(found.get(key, '') != desired.get(key, '') for key in keys): raise ValueError('Unconfirmed storage fields')
            return result(outcome='confirmed', changed=True, item=projected(found))
        except Exception:
            return result(outcome='uncertain', changed=None,
                          reason='Write outcome uncertain; inspect request status and current inventory. Do not repeat automatically.')
