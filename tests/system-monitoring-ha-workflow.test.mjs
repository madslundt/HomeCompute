import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const flow = JSON.parse(fs.readFileSync(new URL('../automations/agent-investigation/n8n-ha-metadata-workflow.json', import.meta.url)));
const node = name => flow.nodes.find(n => n.name === name);
const gate = node('Approved input-free metadata request').parameters.jsCode;
const projection = node('Project finite HA metadata').parameters.jsCode;
const run = (source, request, states = [], version = '2026.10.1') => new Function('$json', '$input', '$', source)(
  request, {all: () => source === projection ? states.map(json => ({json})) : [{json: request}]},
  name => {assert.equal(name, 'Read HA configuration'); return {first: () => ({json: {version, location: 'PRIVATE'}})};});

test('inactive metadata webhook authenticates a fixed GET and disables data persistence', () => {
  assert.equal(flow.active, false);
  const webhook = node('HA metadata webhook');
  assert.equal(webhook.parameters.httpMethod, 'GET');
  assert.equal(webhook.parameters.path, 'homecompute-openclaw-ha-metadata');
  assert.equal(webhook.parameters.authentication, 'headerAuth');
  assert.ok(webhook.credentials.httpHeaderAuth);
  assert.equal(flow.settings.saveDataSuccessExecution, 'none');
  assert.equal(flow.settings.saveDataErrorExecution, 'none');
  assert.equal(flow.settings.saveManualExecutions, false);
  assert.equal(flow.settings.saveExecutionProgress, false);
  assert.equal(flow.settings.availableInMCP, false);
  assert.equal(flow.settings.executionTimeout, 30);
  assert.equal(flow.nodes.filter(n => n.type === 'n8n-nodes-base.homeAssistant').length, 2);
  for (const n of flow.nodes.filter(n => n.type === 'n8n-nodes-base.homeAssistant')) {
    assert.ok(['config:get', 'state:getAll'].includes(n.parameters.resource + ':' + n.parameters.operation));
    assert.equal(n.retryOnFail, false);
    assert.equal(n.credentials.homeAssistantApi.id, 'Xk0jp9yhKbeuvcVH');
  }
  assert.ok(flow.nodes.every(n => !['n8n-nodes-base.scheduleTrigger', 'n8n-nodes-base.executeCommand', 'n8n-nodes-base.telegram'].includes(n.type)));
  assert.ok(!JSON.stringify(flow).includes('accessToken'));
});

test('activation gate rejects all request parameters before HA collection', () => {
  assert.throws(() => run(gate, {}));
  const enabled = gate.replace('configured=false', 'configured=true');
  assert.deepEqual(run(enabled, {headers: {authorization: 'PRIVATE'}}), [{json: {}}]);
  for (const request of [{query: {entity: 'light.private'}}, {params: {url: 'http://evil'}}, {body: {service: 'unlock'}}]) {
    assert.throws(() => run(enabled, request));
  }
});

test('projection exports version and aggregate counts without states, names or attributes', () => {
  const states = [
    {entity_id: 'person.private', state: 'home', attributes: {friendly_name: 'PRIVATE', token: 'SECRET'}},
    {entity_id: 'sensor.private', state: 'unknown'},
    {entity_id: 'update.core', state: 'on', attributes: {installed_version: 'SECRET'}},
    {entity_id: 'update.os', state: 'off'},
    {entity_id: 'automation.private', state: 'off'},
    {entity_id: 'sensor.missing', state: 'unavailable'},
  ];
  const result = run(projection, {}, states)[0].json;
  assert.deepEqual(Object.keys(result).sort(), ['schema_version','host','generated_at','installed_version','update_available_count','unavailable_count'].sort());
  assert.equal(result.installed_version, '2026.10.1');
  assert.equal(result.update_available_count, 1);
  assert.equal(result.unavailable_count, 2);
  assert.ok(Number.isFinite(Date.parse(result.generated_at)));
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
  assert.ok(!JSON.stringify(result).includes('SECRET'));
});

test('partial, malformed and duplicate inventories fail rather than claiming health', () => {
  for (const states of [[], [{entity_id:'sensor.valid',state:null}], [{entity_id:'not-an-entity',state:'on'}],
    [{entity_id:'sensor.valid',state:'on'},{entity_id:'sensor.valid',state:'off'}]]) {
    assert.throws(() => run(projection, {}, states));
  }
  assert.throws(() => run(projection, {}, [{entity_id:'sensor.valid',state:'on'}], 'PRIVATE'));
});
