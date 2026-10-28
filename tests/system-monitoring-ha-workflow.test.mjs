import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const flow = JSON.parse(fs.readFileSync(new URL('../automations/agent-investigation/n8n-ha-metadata-workflow.json', import.meta.url)));
const node = name => flow.nodes.find(n => n.name === name);
const gate = node('Approved input-free metadata request').parameters.jsCode;
const projection = node('Project finite HA metadata').parameters.jsCode;
const policy = JSON.parse(projection.match(/const policy=(.*);\n/)[1]);
const NOW = Date.parse('2026-10-10T12:00:00Z');
const stamp = seconds => new Date(NOW + seconds * 1000).toISOString();
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [NOW])); }
  static now() { return NOW; }
}
const run = (source, request, states = [], version = '2026.10.1') => new Function('$json', '$input', '$', 'Date', source)(
  request, {all: () => source === projection ? states.map(json => ({json})) : [{json: request}]},
  name => {assert.equal(name, 'Read HA configuration'); return {first: () => ({json: {version, location: 'PRIVATE'}})};}, FixedDate);
const project = states => run(projection, {}, states)[0].json;
const check = (result, key) => result.checks.find(row => row.check_id === 'ha.' + key);
const block = (index = 0) => ({start: stamp(index * 1800), end: stamp((index + 1) * 1800), action: 'charge',
  energy_kwh: 0.5, expected_load_kwh: 0.2, expected_grid_import_kwh: 0.7, expected_cost_dkk: 1.2, expected_savings_dkk: 0.3, private_reason: 'PRIVATE'});
function fixture() {
  const rows = Object.entries(policy.numeric).map(([alias, [entity_id, unit, , attribute]]) => ({entity_id,
    state: attribute ? 'charging' : String(unit === '%' ? 52 : 1.5), last_updated: stamp(-20), last_reported: stamp(-10),
    attributes: {unit_of_measurement: unit, friendly_name: 'PRIVATE', token: 'SECRET', ...(attribute ? {[attribute]: 300} : {})}}));
  rows.push({entity_id: 'sensor.staircase_battery_optimizer_state', state: 'ready', last_updated: stamp(-86400),
    attributes: {healthy: true, last_refresh: stamp(-10), private_text: 'PRIVATE'}});
  rows.push({entity_id: 'binary_sensor.staircase_battery_grid_available', state: 'on', last_updated: stamp(-86400)});
  rows.push({entity_id: 'binary_sensor.stromligning_tomorrow_available_vat', state: 'on'});
  for (const [alias, entity_id] of Object.entries(policy.updates)) rows.push({entity_id,
    state: alias === 'core' ? 'on' : 'off', last_updated: stamp(-86400),
    attributes: {installed_version: alias === 'os' ? '16.3' : '2026.10.1', latest_version: alias === 'os' ? '16.3' : '2026.10.2', release_summary: 'PRIVATE'}});
  rows.push({entity_id: 'sensor.staircase_battery_operation_plan', state: 'ready', last_updated: stamp(-86400),
    attributes: {created_at: stamp(-10), expected_cost_dkk: 5, baseline_cost_dkk: 7, expected_savings_dkk: 2,
      blocks: [block()], private_note: 'PRIVATE'}});
  rows.find(row => row.entity_id === policy.numeric.electricity_price[0]).attributes.prices = [
    {start: stamp(0), end: stamp(3600), price: 1.5, private_text: 'PRIVATE'},
    {start: stamp(3600), end: stamp(7200), price: -0.25}];
  for (const entity_id of ['conversation.automation_moe', 'conversation.gemma_4_e4b', 'stt.hviske_vllm', 'tts.plapre_nano_v2']) rows.push({entity_id, state: 'unknown'});
  rows.push({entity_id: 'person.private', state: 'home', attributes: {latitude: 55.1, token: 'SECRET'}});
  rows.push({entity_id: 'sensor.unrelated', state: 'unavailable', attributes: {friendly_name: 'PRIVATE'}});
  rows.push({entity_id: 'automation.private', state: 'off'});
  return rows;
}

test('inactive route authenticates fixed GET, bounds runtime and disables retention', () => {
  assert.equal(flow.active, false);
  const webhook = node('HA metadata webhook');
  assert.equal(webhook.parameters.httpMethod, 'GET');
  assert.equal(webhook.parameters.path, 'homecompute-openclaw-ha-metadata');
  assert.equal(webhook.parameters.authentication, 'headerAuth');
  assert.ok(webhook.credentials.httpHeaderAuth);
  for (const key of ['saveDataSuccessExecution', 'saveDataErrorExecution']) assert.equal(flow.settings[key], 'none');
  for (const key of ['saveManualExecutions', 'saveExecutionProgress', 'availableInMCP']) assert.equal(flow.settings[key], false);
  assert.equal(flow.settings.executionTimeout, 12);
  assert.equal(flow.nodes.filter(n => n.type === 'n8n-nodes-base.homeAssistant').length, 2);
  for (const n of flow.nodes.filter(n => n.type === 'n8n-nodes-base.homeAssistant')) {
    assert.ok(['config:get', 'state:getAll'].includes(n.parameters.resource + ':' + n.parameters.operation));
    assert.equal(n.retryOnFail, false);
    assert.equal(n.credentials.homeAssistantApi.id, 'Xk0jp9yhKbeuvcVH');
  }
  assert.ok(flow.nodes.every(n => !['n8n-nodes-base.scheduleTrigger', 'n8n-nodes-base.executeCommand', 'n8n-nodes-base.telegram'].includes(n.type)));
  assert.ok(!JSON.stringify(flow).includes('accessToken'));
});

test('closed activation gate rejects requests; enabled route rejects every input selector', () => {
  assert.throws(() => run(gate, {}));
  const enabled = gate.replace('configured=false', 'configured=true');
  assert.deepEqual(run(enabled, {headers: {authorization: 'PRIVATE'}}), [{json: {}}]);
  for (const request of [{query: {entity: 'light.private'}}, {params: {url: 'http://evil'}}, {body: {service: 'unlock'}}]) assert.throws(() => run(enabled, request));
});

test('verified household projection excludes personal entities and raw attributes', () => {
  const result = project(fixture());
  assert.deepEqual(Object.keys(result).sort(), ['schema_version', 'host', 'generated_at', 'installed_version', 'update_available_count', 'unavailable_count', 'checks', 'energy'].sort());
  assert.equal(result.schema_version, 2);
  assert.equal(result.generated_at, stamp(0));
  assert.equal(result.update_available_count, 1);
  assert.equal(result.unavailable_count, 0);
  assert.equal(result.checks.length, 16);
  assert.equal(result.energy.entities.battery_soc.value, 52);
  assert.equal(result.energy.entities.battery_discharge_power.value, 300);
  assert.equal(result.energy.entities.grid_import_energy.unit, 'Wh');
  assert.equal(result.energy.entities.house_consumption_today.source, 'house_meter_import');
  assert.deepEqual(result.energy.solar, {status: 'unknown', reason: 'not_provisioned'});
  assert.equal(result.energy.price_slots[1].price_dkk_per_kwh, -0.25);
  assert.equal(result.energy.plan.expected_savings_dkk, 2);
  const serialized = JSON.stringify(result);
  for (const token of ['PRIVATE', 'SECRET', 'person.private', 'latitude', 'friendly_name', 'attributes']) assert.ok(!serialized.includes(token));
});

test('unchanged state timestamps use actual optimizer and meter refresh for current measurements', () => {
  const rows = fixture();
  for (const [alias, [id, , source]] of Object.entries(policy.numeric)) if (['device', 'device_integrated', 'estimate', 'whole_house_meter', 'house_meter_import'].includes(source)) rows.find(e => e.entity_id === id).last_updated = stamp(-86400);
  const result = project(rows);
  for (const alias of ['battery_soc', 'battery_charge_today', 'estimated_savings_today', 'grid_import_power', 'grid_export_energy']) {
    assert.equal(result.energy.entities[alias].status, 'available');
    assert.equal(result.energy.entities[alias].reported_at, stamp(-86400));
    assert.equal(result.energy.entities[alias].sampled_at, stamp(-10));
  }
  assert.equal(check(result, 'battery_optimizer').status, 'healthy');
  assert.equal(check(result, 'grid_availability').status, 'healthy');
  assert.equal(check(result, 'operation_plan').status, 'healthy');
});

test('missing refresh and stale measurements cannot claim current telemetry', () => {
  for (const last_refresh of [undefined, stamp(-901), stamp(1)]) {
    const rows = fixture();
    rows.find(e => e.entity_id === 'sensor.staircase_battery_optimizer_state').attributes.last_refresh = last_refresh;
    const result = project(rows);
    assert.equal(result.energy.entities.battery_soc.status, 'unknown');
    assert.equal(result.energy.entities.battery_soc.reason, 'stale');
    assert.equal(check(result, 'battery_optimizer').status, 'degraded');
    assert.equal(check(result, 'battery_telemetry').status, 'degraded');
  }
  const rows = fixture();
  rows.find(e => e.entity_id === policy.numeric.grid_import_power[0]).last_reported = stamp(-901);
  assert.equal(project(rows).energy.entities.grid_export_power.reason, 'stale');
});

test('units, numeric bounds and missing required entities fail narrowly', () => {
  for (const mutation of [e => e.attributes.unit_of_measurement = 'kWh', e => e.state = 'NaN', e => e.state = 'Infinity', e => e.state = '101', e => e.state = 'unavailable']) {
    const rows = fixture(); mutation(rows.find(e => e.entity_id === policy.numeric.battery_soc[0]));
    const result = project(rows);
    assert.equal(result.energy.entities.battery_soc.status, 'unknown');
    assert.ok(!Object.hasOwn(result.energy.entities.battery_soc, 'value'));
    assert.equal(check(result, 'battery_telemetry').status, 'degraded');
    assert.equal(result.energy.entities.grid_import_power.status, 'available');
  }
  const missing = project(fixture().filter(e => e.entity_id !== policy.numeric.battery_soc[0]));
  assert.equal(missing.energy.entities.battery_soc.reason, 'missing');
  const rows = fixture(); rows.find(e => e.entity_id === policy.numeric.electricity_price[0]).attributes.unit_of_measurement = 'kr/kWh';
  assert.equal(project(rows).energy.entities.electricity_price.unit, 'DKK/kWh');
});

test('tariff slots and modeled plan intervals are finite, ordered and bounded', () => {
  for (const mutate of [a => a.prices = Array(201).fill(a.prices[0]), a => a.prices[1].start = stamp(100), a => a.prices[0].price = 'NaN', a => a.prices[0].end = a.prices[0].start]) {
    const rows = fixture(); mutate(rows.find(e => e.entity_id === policy.numeric.electricity_price[0]).attributes);
    assert.deepEqual(project(rows).energy.price_slots, []);
  }
  for (const mutate of [a => a.blocks = Array.from({length: 33}, (_, i) => block(i)), a => a.created_at = stamp(-901), a => a.blocks[0].action = 'unlock', a => a.blocks[0].energy_kwh = Infinity, a => a.blocks[0].end = a.blocks[0].start, a => a.expected_savings_dkk = 'NaN']) {
    const rows = fixture(); mutate(rows.find(e => e.entity_id === 'sensor.staircase_battery_operation_plan').attributes);
    const result = project(rows);
    assert.equal(result.energy.plan.status, 'unknown');
    assert.equal(check(result, 'operation_plan').status, 'degraded');
  }
});

test('update signals retain safe installed and candidate versions without release payloads', () => {
  const rows = fixture(); rows.find(e => e.entity_id === policy.updates.house_battery).attributes.latest_version = 'PRIVATE arbitrary instruction';
  const result = project(rows);
  assert.deepEqual(check(result, 'update.core').evidence, {available: true, installed_version: '2026.10.1', latest_version: '2026.10.2'});
  assert.equal(check(result, 'update.os').evidence.installed_version, '16.3');
  assert.ok(!Object.hasOwn(check(result, 'update.house_battery').evidence, 'latest_version'));
  assert.equal(check(project(rows.filter(e => e.entity_id !== policy.updates.watts_live)), 'update.watts_live').evidence.reason, 'missing');
});

test('malformed and duplicate inventories cannot produce partial healthy reports', () => {
  for (const states of [[], [{entity_id: 'sensor.valid', state: null}], [{entity_id: 'not-an-entity', state: 'on'}], [{entity_id: 'sensor.valid', state: 'on'}, {entity_id: 'sensor.valid', state: 'off'}]]) assert.throws(() => project(states));
  assert.throws(() => run(projection, {}, fixture(), 'PRIVATE'));
});
