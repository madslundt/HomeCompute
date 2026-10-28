"""Finite HA policy and trusted GET transport; credentials never enter model context."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import urllib.request
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 65536
FIELDS = {'schema_version', 'host', 'generated_at', 'installed_version', 'update_available_count',
          'unavailable_count', 'checks', 'energy'}
# Meaning and units verified against live HA and house_battery sensor/coordinator sources.
NUMERIC = {
 'battery_soc': ('sensor.staircase_battery_battery_state_of_charge', '%', 'device', None),
 'battery_charge_power': ('sensor.staircase_battery_battery_charge_power', 'W', 'device', None),
 'battery_discharge_power': ('sensor.staircase_battery_battery_mode', 'W', 'device', 'output_power_w'),
 'connected_load_power': ('sensor.staircase_battery_connected_load_power', 'W', 'connected_load', None),
 'grid_import_power': ('sensor.watts_live_effekt', 'W', 'whole_house_meter', None),
 'grid_export_power': ('sensor.watts_live_returneret_effekt', 'W', 'whole_house_meter', None),
 'grid_import_energy': ('sensor.watts_live_energiforbrug', 'Wh', 'whole_house_meter', None),
 'grid_export_energy': ('sensor.watts_live_returneret_energi', 'Wh', 'whole_house_meter', None),
 'house_consumption_today': ('sensor.energy_dashboard_house_daily_energy', 'kWh', 'house_meter_import', None),
 'battery_charge_today': ('sensor.staircase_battery_battery_charge_today', 'kWh', 'device_integrated', None),
 'battery_discharge_today': ('sensor.staircase_battery_battery_discharge_today', 'kWh', 'device_integrated', None),
 'estimated_savings_today': ('sensor.staircase_battery_estimated_realized_savings_today', 'DKK', 'estimate', None),
 'nominal_capacity': ('number.staircase_battery_nominal_battery_capacity', 'kWh', 'configured', None),
 'reserve_soc': ('number.staircase_battery_arbitrage_reserve_soc', '%', 'configured', None),
 'maximum_soc': ('number.staircase_battery_maximum_charge_soc', '%', 'configured', None),
 'round_trip_efficiency': ('number.staircase_battery_fallback_round_trip_efficiency', '%', 'configured', None),
 'degradation_cost': ('number.staircase_battery_battery_degradation_cost', 'DKK/kWh', 'configured', None),
 'minimum_profit': ('number.staircase_battery_minimum_required_profit', 'DKK/kWh', 'configured', None),
 'electricity_price': ('sensor.stromligning_current_price_vat', 'DKK/kWh', 'vat_inclusive_tariff', None),
 'distribution_tariff': ('sensor.stromligning_distribution_vat', 'DKK/kWh', 'vat_inclusive_tariff', None),
 'system_tariff': ('sensor.stromligning_systemtariff_vat', 'DKK/kWh', 'vat_inclusive_tariff', None),
 'network_tariff': ('sensor.stromligning_nettariff_vat', 'DKK/kWh', 'vat_inclusive_tariff', None),
}
UPDATES = {'core': 'update.home_assistant_core_update', 'supervisor': 'update.home_assistant_supervisor_update',
 'os': 'update.home_assistant_operating_system_update', 'house_battery': 'update.house_battery_update',
 'stromligning': 'update.stromligning_update', 'watts_live': 'update.watts_live_update'}
HEALTH = ('battery_optimizer', 'battery_telemetry', 'grid_meter', 'tariff', 'operation_plan', 'grid_availability',
          'assist_primary', 'assist_secondary', 'assist_stt', 'assist_tts')
CHECKS = {'ha.' + key: 'health' for key in HEALTH} | {'ha.update.' + key: 'updates' for key in UPDATES}
REASONS = {'missing', 'stale', 'unsupported_schema', 'health_not_reported', 'collection_failed'}
VERSION = re.compile(r'^v?[0-9]+(?:[.][0-9A-Za-z_-]+){0,5}$')


def stamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('invalid HA timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('HA timestamp requires timezone')
    return result.astimezone(timezone.utc)


def number(value: Any) -> bool:
    return type(value) in {float, int} and math.isfinite(value) and abs(value) <= 1e12


def validate(raw: Any) -> dict[str, Any]:
    if (not isinstance(raw, dict) or set(raw) != FIELDS or type(raw['schema_version']) is not int
            or raw['schema_version'] != 2 or raw['host'] != 'home-assistant'
            or not isinstance(raw['installed_version'], str) or not VERSION.fullmatch(raw['installed_version'])):
        raise ValueError('unsupported HA projection')
    generated = stamp(raw['generated_at'])
    if any(type(raw[k]) is not int or not 0 <= raw[k] <= 100 for k in ('update_available_count', 'unavailable_count')):
        raise ValueError('invalid HA counts')
    rows = raw['checks']
    if not isinstance(rows, list) or len(rows) != len(CHECKS) or {r.get('check_id') for r in rows if isinstance(r, dict)} != set(CHECKS):
        raise ValueError('missing HA checks')
    for row in rows:
        if (set(row) != {'check_id', 'status', 'evidence', 'observed_at'} or row['status'] not in {'healthy', 'degraded', 'unknown'}
                or not isinstance(row['evidence'], dict)):
            raise ValueError('invalid HA check')
        if not -30 <= (generated - stamp(row['observed_at'])).total_seconds() <= 900:
            raise ValueError('invalid HA check provenance')
        e = row['evidence']
        if (not e or set(e) - {'reason', 'available', 'failed', 'installed_version', 'latest_version'}
                or any(k in e and e[k] not in REASONS for k in ('reason',))
                or any(k in e and type(e[k]) is not bool for k in ('available', 'failed'))
                or any(k in e and (not isinstance(e[k], str) or not VERSION.fullmatch(e[k])) for k in ('installed_version', 'latest_version'))):
            raise ValueError('unbounded HA check evidence')
    energy = raw['energy']
    if not isinstance(energy, dict) or set(energy) != {'entities', 'price_slots', 'plan', 'solar', 'tomorrow_prices'}:
        raise ValueError('unbounded HA energy fields')
    entities = energy['entities']
    if not isinstance(entities, dict) or set(entities) != set(NUMERIC):
        raise ValueError('invalid HA selection')
    for alias, row in entities.items():
        if not isinstance(row, dict) or set(row) - {'status', 'reason', 'value', 'unit', 'reported_at', 'source', 'sampled_at'}:
            raise ValueError('unbounded HA entity')
        if row.get('source') != NUMERIC[alias][2] or row.get('unit') != NUMERIC[alias][1] or row.get('status') not in {'available', 'unknown'}:
            raise ValueError('invalid HA entity semantics')
        if row['status'] == 'available':
            sampled = row.get('sampled_at')
            ttl = 7200 if NUMERIC[alias][2] == 'vat_inclusive_tariff' else 900
            if (not number(row.get('value')) or 'reason' in row or sampled is None
                    or not 0 <= (generated - stamp(sampled)).total_seconds() <= ttl
                    or (row['unit'] == '%' and not 0 <= row['value'] <= 100)):
                raise ValueError('invalid HA numeric value')
        elif row.get('reason') not in REASONS or 'value' in row:
            raise ValueError('invalid HA missing value')
        for key in ('reported_at', 'sampled_at'):
            if row.get(key) is not None and stamp(row[key]) > generated:
                raise ValueError('future HA sample')
    if energy['solar'] != {'status': 'unknown', 'reason': 'not_provisioned'}:
        raise ValueError('solar source is unprovisioned')
    if energy['tomorrow_prices'] not in {'on', 'off', 'unknown'}:
        raise ValueError('invalid tomorrow price availability')
    slots = energy['price_slots']
    if not isinstance(slots, list) or len(slots) > 200:
        raise ValueError('price slot budget')
    prior_end = None
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) != {'start', 'end', 'price_dkk_per_kwh'} or not number(slot['price_dkk_per_kwh']):
            raise ValueError('invalid tariff slot')
        start, end = stamp(slot['start']), stamp(slot['end'])
        if not start < end or (prior_end and start < prior_end) or not -86400 <= (start - generated).total_seconds() <= 172800:
            raise ValueError('invalid tariff interval')
        prior_end = end
    plan = energy['plan']
    if not isinstance(plan, dict) or plan.get('status') not in {'available', 'unknown'}:
        raise ValueError('invalid HA plan')
    if plan['status'] == 'unknown':
        if set(plan) != {'status', 'reason'} or plan['reason'] not in REASONS:
            raise ValueError('invalid missing HA plan')
    else:
        fields = {'status', 'created_at', 'expected_cost_dkk', 'baseline_cost_dkk', 'expected_savings_dkk', 'blocks'}
        if set(plan) != fields or any(not number(plan[k]) for k in fields - {'status', 'created_at', 'blocks'}):
            raise ValueError('invalid modeled plan totals')
        if not 0 <= (generated - stamp(plan['created_at'])).total_seconds() <= 900:
            raise ValueError('stale modeled plan')
        blocks = plan['blocks']
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 32:
            raise ValueError('plan block budget')
        prior_end = None
        for b in blocks:
            if (not isinstance(b, dict) or set(b) != {'start', 'end', 'action', 'energy_kwh', 'expected_load_kwh', 'expected_grid_import_kwh', 'expected_cost_dkk', 'expected_savings_dkk'}
                    or b['action'] not in {'grid', 'charge', 'battery', 'idle'}
                    or any(not number(v) for k, v in b.items() if k not in {'start', 'end', 'action'})):
                raise ValueError('invalid modeled block')
            start, end = stamp(b['start']), stamp(b['end'])
            if not start < end or (prior_end and start < prior_end) or not -86400 <= (start - generated).total_seconds() <= 172800:
                raise ValueError('invalid plan interval')
            prior_end = end
    return raw


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def local_read(token: str) -> dict[str, Any]:
    request = urllib.request.Request('http://127.0.0.1:15678/webhook/homecompute-openclaw-ha-metadata',
                                    headers={'Authorization': 'Bearer ' + token}, method='GET')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=4) as response:
        if response.status != 200:
            raise ValueError('HA route unavailable')
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('HA projection exceeds budget')
    return validate(json.loads(payload))


def compact(energy: dict[str, Any], generated: str) -> dict[str, Any]:
    """Chat gets a small summary; the authenticated reusable reader retains full slots/blocks."""
    result = dict(energy)
    slots = energy['price_slots']
    result['price_slots'] = {'count': len(slots), 'unit': 'DKK/kWh',
        'min': min((s['price_dkk_per_kwh'] for s in slots), default=None),
        'max': max((s['price_dkk_per_kwh'] for s in slots), default=None),
        'next': [s for s in slots if stamp(s['end']) > stamp(generated)][:4]}
    result['entity_columns'] = ['status', 'value_or_reason', 'unit', 'source', 'sampled_at']
    result['entities'] = {alias: [row['status'], row.get('value', row.get('reason')), row['unit'], row['source'], row.get('sampled_at')]
                          for alias, row in energy['entities'].items()}
    if energy['plan']['status'] == 'available':
        local = ZoneInfo('Europe/Copenhagen')
        blocks = []
        for block in energy['plan']['blocks']:
            if stamp(block['end']) > stamp(generated):
                blocks.append(dict(block, **{key + '_local': stamp(block[key]).astimezone(local).strftime('%a %Y-%m-%d %H:%M %Z')
                                             for key in ('start', 'end')}))
        # Preserve source instants, and supply ready-to-quote local windows so
        # chat does not have to infer DST or convert the same time twice.
        result['plan'] = dict(energy['plan'], timezone='Europe/Copenhagen', blocks=blocks[:4])
    return result
