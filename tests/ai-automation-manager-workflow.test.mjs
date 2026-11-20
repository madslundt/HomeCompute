import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const workflowPath = process.argv[2];
assert.ok(workflowPath, 'Pass the compiled workflow JSON path');
const { nodes, connections } = JSON.parse(readFileSync(workflowPath, 'utf8'));
const findNode = name => nodes.find(node => node.name === name);
const input = values => ({ all: () => values.map(json => ({ json })) });
const failure = 'Record failed automation run';

test('caps and deduplicates search requests independently per automation', () => {
  const prepare = new Function('$input', findNode('Limit public search requests').parameters.jsCode);
  const items = prepare(input([
    { queries: ['  one  ', 'ONE', 'two   words', ' ', 'x'.repeat(401), 'three', 'four', 'five', 'six'] },
    { queries: ['one', 'seven'] },
    { queries: [] },
  ]));
  assert.deepEqual(items.map(({ json, pairedItem }) => [json.query, pairedItem.item]), [
    ['one', 0], ['two words', 0], ['three', 0], ['four', 0], ['five', 0],
    ['one', 1], ['seven', 1], ['', 2],
  ]);
});

test('keeps evidence and provider errors with the correct claimed run', () => {
  const collect = new Function('$input', '$', findNode('Collect evidence by automation').parameters.jsCode);
  const associations = ['a', 'b', 'a', 'b'];
  const byNode = name => {
    assert.equal(name, 'Split claimed automations');
    return { itemMatching: index => ({ json: { run_id: associations[index] } }) };
  };
  const source = { url: 'https://example.org/story', title: 'Story', content: 'Evidence' };
  const other = { url: 'https://example.org/other', title: 'Other', content: 'Other evidence' };
  const output = collect(input([
    { results: [source] },
    { results: [other] },
    { results: [source, { url: 'file:///private/example' }] },
    { error: 'Synthetic provider error' },
  ]), byNode);
  assert.deepEqual(output, [{
    json: { evidence: [{ url: source.url, title: source.title, excerpt: source.content }], search_failed: false },
    pairedItem: { item: 0 },
  }, {
    json: { evidence: [{ url: other.url, title: other.title, excerpt: other.content }], search_failed: true },
    pairedItem: { item: 1 },
  }]);
});

test('advances successful items and records failures without another search loop', () => {
  const writer = 'Write the structured automation update';
  assert.equal(findNode(writer).type, '@n8n/n8n-nodes-langchain.chainLlm');
  assert.equal(findNode('Run public searches').retryOnFail, undefined);
  assert.ok(!Object.values(connections).some(c => c.ai_tool));
  for (const name of ['Plan public searches', 'Limit public search requests', 'Collect evidence by automation',
    writer, 'Save update and completion state']) {
    assert.equal(findNode(name).onError, 'continueErrorOutput');
    assert.deepEqual(connections[name].main[1].map(e => e.node), [failure]);
  }
  assert.deepEqual(connections['Did a public search fail?'].main.map(output => output.map(e => e.node)),
    [[failure], [writer]]);
});

test('bounds evidence and gives both local model calls a longer request budget', () => {
  assert.equal(findNode('Run public searches').parameters.options.max_results, 3);
  const collect = new Function('$input', '$', findNode('Collect evidence by automation').parameters.jsCode);
  const hits = Array.from({ length: 5 }, (_, index) => ({
    url: 'https://example.org/' + index,
    title: 'T'.repeat(180),
    content: '   Short    text    ' + 'x'.repeat(2000),
  }));
  const [{ json }] = collect(input([{ results: hits }]), () => ({
    itemMatching: () => ({ json: { run_id: 'a' } }),
  }));
  assert.equal(json.evidence.length, 3);
  assert.ok(json.evidence.every(hit => hit.title.length === 160 && hit.excerpt.length === 600 &&
    hit.excerpt.startsWith('Short text ')));

  for (const [name, consumer, maxTokens] of [
    ['HomeCompute local search planner', 'Plan public searches', 512],
    ['HomeCompute local automation model', 'Write the structured automation update', 4096],
  ]) {
    const { model, options } = findNode(name).parameters;
    assert.equal(model.value, 'automation-moe');
    assert.deepEqual(connections[name].ai_languageModel[0].map(edge => edge.node), [consumer]);
    assert.equal(options.maxRetries, 0);
    assert.equal(options.maxTokens, maxTokens);
    assert.equal(options.timeout, 960000);
    assert.deepEqual(JSON.parse(options.extraBody), { timeout: 900, chat_template_kwargs: { enable_thinking: false } });
  }
});
