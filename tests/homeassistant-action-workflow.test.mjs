import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { createHash } from 'node:crypto';

const raw = fs.readFileSync(new URL('../automations/agent-investigation/n8n-ha-approved-actions-workflow.json', import.meta.url));
const flow = JSON.parse(raw);
const node = name => flow.nodes.find(n => n.name === name);
const gate = node('Validate HA action request').parameters.jsCode.replace('configured=false', 'configured=true');
const project = node('Verify selected snapshot').parameters.jsCode;
const good = { operation: 'snapshot', action: { domain: 'light', service: 'turn_on', entity_ids: ['light.study'], data: { brightness: 50 } } };
function validate(body, extras = {}) {
  const input = { all: () => [{ json: { body } }] };
  return new Function('$json', '$input', gate)({ body, query: {}, params: {}, ...extras }, input)[0].json.request;
}
function snapshot(request, states, config = { unit_system: { temperature: '°C' } }) {
  const lookup = name => ({ first: () => ({ json: name === 'Validate HA action request' ? { request } : config }) });
  return new Function('$', '$input', project)(lookup, { all: () => states.map(json => ({ json })) })[0].json;
}
function execution(request = structuredClone(good)) {
  const now = Date.now() / 1000;
  return { ...request, operation: 'execute', proposal_id: 'a'.repeat(32), approval_digest: 'b'.repeat(64),
    approved_at: now, approval_expires_at: now + 300, expected_entities: { 'light.study': { state: 'off', brightness: 0 } } };
}

test('inert template, fixed authenticated route and finite effect graph', () => {
  assert.equal(flow.active, false);
  assert.match(node('Validate HA action request').parameters.jsCode, /configured=false/);
  assert.equal(createHash('sha256').update(raw).digest('hex'), '05c3d9ce159101109d33befe1854a08299920e4c462407b55ab12079d97ecc18');
  assert.deepEqual(node('HA approved action webhook').parameters, { httpMethod: 'POST', path: 'homecompute-approved-ha-actions', authentication: 'headerAuth', responseMode: 'responseNode', options: {} });
  for (const n of flow.nodes.filter(n => n.type === 'n8n-nodes-base.homeAssistant')) {
    assert.equal(n.retryOnFail, false);
    assert.equal(n.credentials.homeAssistantApi.id, 'Xk0jp9yhKbeuvcVH');
  }
  assert.deepEqual(flow.connections['Execute approved action'].main.map(x => x[0].node), ['Call finite HA service', 'Bounded snapshot response']);
  assert.equal(flow.settings.saveDataSuccessExecution, 'none');
  assert.equal(flow.settings.saveDataErrorExecution, 'none');
  assert.equal(flow.settings.availableInMCP, false);
  assert.equal(flow.settings.saveManualExecutions, false);
});

test('snapshot request reads selected targets only and has no effects', () => {
  const r = validate(structuredClone(good));
  const result = snapshot(r, [{ entity_id: 'light.study', state: 'off', attributes: { brightness: 0, secret: 'not projected' } }]);
  assert.deepEqual(result.snapshot.entities, { 'light.study': { state: 'off', brightness: 0 } });
  assert.equal(result.request.operation, 'snapshot');
});

test('inline route rejects forbidden domains, arbitrary URLs and malformed data independently', () => {
  const bad = [
    { ...good, operation: 'approve' }, { ...good, arbitrary: true },
    ...['lock', 'alarm_control_panel', 'switch', 'script', 'automation'].map(domain => ({ operation: 'snapshot', action: { domain, service: 'turn_on', entity_ids: [domain + '.study'], data: {} } })),
    { ...good, action: { ...good.action, service: 'http://ha/api/services/light/turn_on' } },
    { ...good, action: { ...good.action, entity_ids: ['light.*'] } },
    { ...good, action: { ...good.action, entity_ids: ['climate.study'] } },
    { ...good, action: { ...good.action, entity_ids: [] } },
    { ...good, action: { ...good.action, entity_ids: ['light.study', 'light.study'] } },
    { ...good, action: { ...good.action, entity_ids: Array.from({ length: 9 }, (_, i) => `light.e${i}`) } },
    ...[true, -1, 256, Infinity, NaN, '50', {}].map(brightness => ({ ...good, action: { ...good.action, data: { brightness } } })),
    { ...good, action: { ...good.action, data: { entity_id: 'light.other' } } },
    { ...good, action: { ...good.action, data: { transition: 31 } } },
  ];
  for (const body of bad) assert.throws(() => validate(body));
  assert.throws(() => validate(good, { query: { command: 'anything' } }));
});

test('effect request requires exact approval shape, expiry and precondition match', () => {
  const effect = execution();
  const r = validate(effect);
  assert.equal(snapshot(r, [{ entity_id: 'light.study', state: 'off', attributes: { brightness: 0 } }]).request.operation, 'execute');
  assert.throws(() => snapshot(r, [{ entity_id: 'light.study', state: 'on', attributes: { brightness: 0 } }]));
  assert.throws(() => snapshot(r, [{ entity_id: 'light.other', state: 'off' }]));
  assert.throws(() => validate({ ...effect, approval_expires_at: effect.approved_at + 301 }));
  assert.throws(() => validate({ ...effect, approval_expires_at: effect.approved_at - 1 }));
  assert.throws(() => validate({ ...effect, proposal_id: 'arbitrary' }));
  assert.throws(() => validate({ ...effect, expected_entities: {} }));
  assert.throws(() => validate({ ...effect, approval_digest: undefined }));
});

test('climate Celsius and supported HVAC modes, unavailable target refusals', () => {
  const request = { operation: 'snapshot', action: { domain: 'climate', service: 'set_temperature', entity_ids: ['climate.study'], data: { temperature: 21 } } };
  const state = [{ entity_id: 'climate.study', state: 'heat', attributes: { temperature: 20, hvac_modes: ['heat', 'off'] } }];
  validate(request);
  assert.equal(snapshot(request, state).snapshot.entities['climate.study'].temperature_unit, '°C');
  assert.throws(() => snapshot(request, state, { unit_system: { temperature: '°F' } }));
  for (const temperature of [true, 4.9, 35.1, Infinity, '21']) assert.throws(() => validate({ ...request, action: { ...request.action, data: { temperature } } }));
  const mode = { ...request, action: { ...request.action, service: 'set_hvac_mode', data: { hvac_mode: 'cool' } } };
  validate(mode);
  assert.throws(() => snapshot(mode, state));
  assert.throws(() => snapshot(request, [{ entity_id: 'climate.study', state: 'unavailable' }]));
});

test('finite service node builds only approved IDs and numeric primitive attributes', () => {
  const expression = node('Call finite HA service').parameters.serviceAttributes.slice(3, -2);
  const actual = new Function('$json', `return (${expression});`)({ request: good });
  assert.deepEqual(actual, { attributes: [{ name: 'entity_id', value: 'light.study' }, { name: 'brightness', value: 50 }] });
});
