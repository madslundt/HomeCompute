import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const base = new URL('../automations/agent-investigation/', import.meta.url);
const delivery = JSON.parse(fs.readFileSync(new URL('n8n-notification-workflow.json', base)));
const conversation = JSON.parse(fs.readFileSync(new URL('n8n-conversation-workflow.json', base)));
const code = (flow, name) => flow.nodes.find(n => n.name === name).parameters.jsCode;
const execute = (source, json, nodes = {}) => new Function('$json', '$', source)(json, name => ({first: () => ({json: nodes[name]})}));
const configured = s => s.replace('configured:false', 'configured:true')
  .replaceAll('replace-selected-authorized-destination', 'operator-private')
  .replaceAll('replace-selected-private-chat-id', '1234')
  .replaceAll('replace-selected-operator-id', '5678');

test('delivery defaults closed, workflows inactive and no competing bot trigger', () => {
  assert.throws(() => execute(code(delivery, 'Approved delivery configuration'), {}));
  assert.throws(() => execute(code(conversation, 'Validate private sender and command'), {}));
  for (const flow of [delivery, conversation]) {
    assert.equal(flow.active, false);
    assert.equal(flow.settings.saveDataSuccessExecution, 'none');
    assert.ok(flow.nodes.every(n => n.type !== 'n8n-nodes-base.telegramTrigger'));
    assert.ok(flow.nodes.filter(n => n.type === 'n8n-nodes-base.scheduleTrigger').every(n => n.disabled === true));
    assert.ok(flow.nodes.filter(n => ['n8n-nodes-base.telegram', 'n8n-nodes-base.httpRequest'].includes(n.type)).every(n => n.retryOnFail === false));
  }
  const schedule = delivery.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
  assert.equal(schedule.parameters.rule.interval[0].expression, '*/10 * * * * *');
});

test('notification envelope pins selected destination and HTML escapes assistant output', () => {
  const cfg = execute(configured(code(delivery, 'Approved delivery configuration')), {})[0].json;
  const event = {destination: 'operator-private', delivery_key: 'task:synthetic:completed', claim: 'a'.repeat(32), text: '<b>untrusted & text</b>'};
  const source = code(delivery, 'Validate authorized envelope');
  const nodes = {'Approved delivery configuration': cfg};
  assert.deepEqual(execute(source, {event: null}, nodes), []);
  assert.equal(execute(source, {event}, nodes)[0].json.safe_text, '&lt;b&gt;untrusted &amp; text&lt;/b&gt;');
  assert.throws(() => execute(source, {event: {...event, destination: 'family'}}, nodes));
  assert.throws(() => execute(source, {event: {...event, claim: 'x'}}, nodes));
});

test('delivery acknowledgement requires a real message receipt for selected chat', () => {
  const source = code(delivery, 'Verify delivery receipt');
  const nodes = {'Validate authorized envelope': {delivery_key: 'example', claim: 'a'.repeat(32), chat_id: '1234'}};
  const receipt = execute(source, {message_id: 42, chat: {id: 1234}}, nodes)[0].json;
  assert.equal(receipt.receipt, 'telegram:42');
  assert.equal(receipt.delivery_key, 'example');
  assert.throws(() => execute(source, {message_id: 42, chat: {id: 9999}}, nodes));
  assert.throws(() => execute(source, {error: 'delivery timeout'}, nodes));
});

test('conversation router pins private chat, human sender, command and update identity', () => {
  const source = configured(code(conversation, 'Validate private sender and command'));
  const update = {update_id: 42, message: {chat: {id: 1234, type: 'private'}, from: {id: 5678, is_bot: false}, text: '/openclaw hello'}};
  const result = execute(source, update)[0].json;
  assert.equal(result.request_id, 'telegram:42');
  assert.equal(result.text, 'hello');
  assert.equal(result.conversation, 'operator');
  for (const message of [
    {...update.message, chat: {id: 1234, type: 'group'}},
    {...update.message, from: {id: 9999}},
    {...update.message, from: {id: 5678, is_bot: true}},
    {...update.message, text: 'ordinary unrelated message'},
  ]) assert.deepEqual(execute(source, {...update, message}), []);
});

test('conversation returns processing receipt; outbox alone sends replies', () => {
  const source = code(conversation, 'Return processing status');
  assert.equal(execute(source, {state: 'completed'})[0].json.delivery, 'queued_in_durable_outbox');
  assert.equal(execute(source, {state: 'uncertain'})[0].json.delivery, 'requires_status_or_reconciliation');
  assert.ok(conversation.nodes.every(n => n.type !== 'n8n-nodes-base.telegram'));
});
