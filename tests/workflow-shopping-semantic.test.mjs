import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {
  CATEGORY_GUIDANCE, OFFER_GUIDANCE, SHOPPING_AGENT_TEXT, SHOPPING_CATEGORY_BODY,
  SHOPPING_CATEGORY_RESTORE, SHOPPING_OFFER_RESTORE, SHOPPING_RANK_BODY,
  STORAGE_CATEGORY_BODY, STORAGE_CATEGORY_RESTORE, STORAGE_REFRESH_RESTORE,
  shoppingOperations, storageOperations,
} from '../automations/semantic-shopping/nodes.mjs';

function run(code, input, nodes = {}) {
  const items = input.map(json => ({json}));
  const result = vm.runInNewContext(`(function () {${code}\n})()`, {
    $input: {first: () => items[0], all: () => items},
    $: name => ({first: () => ({json: nodes[name][0]}), all: () => nodes[name].map(json => ({json}))}),
  });
  return JSON.parse(JSON.stringify(result));
}

function expression(source, json, nodes = {}) {
  return JSON.parse(vm.runInNewContext(source.slice(3, -2), {
    $json: json,
    $: name => ({first: () => ({json: nodes[name][0]})}),
  }));
}

const shoppingContext = () => ({
  activeItems: [{uid: 'current', name: 'mælk', current_category: '', amount: 2,
    clarification_selection: 'sødmælk', wrong_matches: [{product_name: 'havredrik'}]}],
  snapshotItems: [
    {uid: 'confirmed', summary: 'letmælk', description: JSON.stringify({schema: 'hygge-shopping/v1', category: 'Mejeri'})},
    {uid: 'unknown-envelope', summary: 'new', description: JSON.stringify({category: 'Mejeri'})},
    {uid: 'invalid-category', summary: 'new', description: JSON.stringify({schema: 'hygge-shopping/v1', category: 'invented'})},
    {uid: 'invalid-description', summary: 'new', description: '{oops'},
  ],
  agentPrompt: JSON.stringify({items: [{uid: 'current', name: 'mælk'}]}),
  requestId: 'request-untouched', partial: true, existingOffers: [{price: 12}],
});

test('shopping request teaches only current confirmed source envelopes', () => {
  const context = shoppingContext();
  const body = expression(SHOPPING_CATEGORY_BODY, context);
  assert.equal(body.profile, 'shopping');
  assert.deepEqual(body.items, [{id: 'current', name: 'mælk'}]);
  assert.deepEqual(body.examples, [{id: 'confirmed', name: 'letmælk', category: 'Mejeri'}]);
  assert.equal('wrong_matches' in body.items[0], false);
});

test('retrieved examples cannot introduce unknown IDs/categories or mutate original shopping context', () => {
  const context = shoppingContext();
  const result = run(SHOPPING_CATEGORY_RESTORE, [{body: {available: true, matches: [
    {id: 'current', category: 'Husholdning', score: 0.999, margin: 0.999, examples: [
      {name: 'letmælk', category: 'Mejeri', score: 0.85},
      {name: 'bad', category: 'invented', score: 0.99},
      {name: 'bad', category: 'Mejeri', score: null},
    ]},
    {id: 'unknown', examples: [{name: 'bad', category: 'Andet', score: 1}]},
  ]}}], {'Prepare AI shopping context': [context]})[0].json;
  const {categoryExamples, ...original} = result;
  assert.deepEqual(original, context);
  assert.deepEqual(categoryExamples, [{id: 'current', examples: [{name: 'letmælk', category: 'Mejeri', score: 0.85}]}]);
  // Even extreme similarity/margin does not automatically overwrite a category.
  assert.equal(result.activeItems[0].current_category, '');
});

test('shopping retrieval outage preserves context and existing LLM path', () => {
  const context = shoppingContext();
  for (const failure of [{error: {message: 'timeout'}}, {body: {available: false, matches: []}}, {}]) {
    const result = run(SHOPPING_CATEGORY_RESTORE, [failure], {'Prepare AI shopping context': [context]})[0].json;
    assert.deepEqual(result, {...context, categoryExamples: []});
  }
});

test('agent prompt receives only examples for its actual input IDs', () => {
  const payload = {mode: 'categories', items: [{uid: 'current', name: 'mælk', clarification_requested: true}]};
  const context = {agentPrompt: JSON.stringify(payload)};
  const result = expression(SHOPPING_AGENT_TEXT, context, {'Shopping category examples': [{categoryExamples: [
    {id: 'current', examples: [{name: 'letmælk', category: 'Mejeri', score: 0.9}]},
    {id: 'other', examples: []},
  ]}]});
  assert.deepEqual(result.items, payload.items);
  assert.deepEqual(result.confirmed_category_examples.map(match => match.id), ['current']);
});

function offerContext(uid = 'current') {
  const offers = [
    {uid, product_name: 'Letmælk 1 liter', brand: 'Arla', price: 12, unit: 'l', store: 'Netto', ends_at: '2099-01-01'},
    {uid, product_name: 'Havredrik 1 liter', brand: 'Example', price: 10, unit: 'l', store: 'Lidl', ends_at: '2099-01-01'},
  ];
  return {...shoppingContext(), scopedItemUid: uid,
    agentPrompt: JSON.stringify({items: [{uid, name: 'Arla sødmælk 1 liter'}], tilbudsugen_offers: offers})};
}

test('offer request ranks only current supplied records and selected meaning', () => {
  const context = offerContext();
  const body = expression(SHOPPING_RANK_BODY, context);
  assert.equal(body.profile, 'shopping');
  assert.match(body.query, /sødmælk/);
  assert.deepEqual(body.records.map(row => row.id), ['offer-0', 'offer-1']);
  assert.equal(body.records[0].text, 'Letmælk 1 liter Arla l');
});

test('offer relevance only adds valid hints while preserving all facts, guards and candidates', () => {
  const context = offerContext();
  const originalPayload = JSON.parse(context.agentPrompt);
  const result = run(SHOPPING_OFFER_RESTORE, [{body: {available: true, results: [
    {id: 'offer-1', semantic_score: 0.94}, {id: 'offer-1', semantic_score: 0.94},
    {id: 'offer-100', semantic_score: 0.99}, {id: 'new-offer', semantic_score: 0.99},
    {id: 'offer-0', semantic_score: null},
  ]}}], {'Prepare per-item AI context': [context]})[0].json;
  const {semantic_offer_candidates, ...payload} = JSON.parse(result.agentPrompt);
  assert.deepEqual(payload, originalPayload);
  assert.deepEqual(semantic_offer_candidates, [{candidate_index: 1, score: 0.94}]);
  assert.deepEqual(result.activeItems, context.activeItems);
});

test('offer timeouts and unavailable embeddings preserve each item and every candidate', () => {
  const contexts = [offerContext('a'), offerContext('b')];
  const result = run(SHOPPING_OFFER_RESTORE, [{error: 'timeout'}, {body: {available: false}}],
    {'Prepare per-item AI context': contexts});
  assert.equal(result.length, 2);
  for (let index = 0; index < result.length; index++) {
    const payload = JSON.parse(result[index].json.agentPrompt);
    assert.deepEqual(payload.tilbudsugen_offers, JSON.parse(contexts[index].agentPrompt).tilbudsugen_offers);
    assert.deepEqual(payload.semantic_offer_candidates, []);
    assert.equal(result[index].json.scopedItemUid, contexts[index].scopedItemUid);
  }
});

test('Lager retrieval excludes claim tokens and revisions; restore preserves exact guarded batch', () => {
  const original = {batch_token: 'private-claim', items: [{id: 'inventory-id', name: 'shampoo', revision: 8}],
    due_at: '2026-01-01T00:00:00Z', agentPrompt: JSON.stringify({items: [{id: 'inventory-id', name: 'shampoo'}]})};
  const body = expression(STORAGE_CATEGORY_BODY, {}, {'Prepare Lager category context': [original]});
  assert.deepEqual(body.items, [{id: 'inventory-id', name: 'shampoo'}]);
  assert.deepEqual(body.examples, []);
  assert.equal(JSON.stringify(body).includes('private-claim'), false);
  const result = run(STORAGE_CATEGORY_RESTORE, [{body: {available: true, matches: [{id: 'inventory-id', examples: [
    {name: 'hårshampoo', category: 'Personlig pleje', score: 0.88},
  ]}]}}], {'Prepare Lager category context': [original]})[0].json;
  assert.equal(result.batch_token, original.batch_token);
  assert.deepEqual(result.items, original.items);
  assert.deepEqual(JSON.parse(result.agentPrompt).items, JSON.parse(original.agentPrompt).items);
  assert.equal(JSON.parse(result.agentPrompt).confirmed_category_examples[0].examples[0].category, 'Personlig pleje');
});

test('Lager retrieval outage still sends original payload to existing guarded LLM/writer', () => {
  const original = {batch_token: 'claim', items: [{id: 'i', name: 'item', revision: 2}], agentPrompt: '{"items":[{"id":"i","name":"item"}]}'};
  const result = run(STORAGE_CATEGORY_RESTORE, [{error: 'timeout'}], {'Prepare Lager category context': [original]})[0].json;
  assert.equal(result.batch_token, 'claim');
  assert.deepEqual(result.items, original.items);
  assert.deepEqual(JSON.parse(result.agentPrompt).confirmed_category_examples, []);
});

test('postapply source refresh preserves validated complete/deferred status even on failure', () => {
  const statuses = [{state: 'complete', applied: 3, skipped: 0}, {state: 'deferred', reason: 'Inventory changed'}];
  for (const status of statuses) {
    const result = run(STORAGE_REFRESH_RESTORE, [{error: 'timeout'}], {'Category sync result': [status]})[0].json;
    assert.deepEqual(result, status);
  }
});

test('operations change only new retrieval nodes, routing and additive prompt fields', () => {
  const live = {nodes: ['Category sync AI Agent', 'Parallel categorizer', 'Shopping AI Agent'].map(name => ({name,
    parameters: {text: '={{ $json.agentPrompt }}', options: {systemMessage: 'Existing exact rules', maxIterations: 5}},
    credentials: {retained: {id: 'original'}}, id: name + '-original',
  }))};
  live.nodes.push({name: 'Prepare AI shopping context', position: [1000, 1000]},
    {name: 'Prepare per-item AI context', position: [1500, 1000]});
  const before = JSON.stringify(live);
  const operations = shoppingOperations(live, 'verified-shopping-id');
  assert.equal(JSON.stringify(live), before);
  for (const operation of operations.filter(op => op.type === 'updateNodeParameters')) {
    const existing = live.nodes.find(node => node.name === operation.nodeName).parameters;
    assert.equal(operation.replace, true);
    assert.ok(operation.parameters.options.systemMessage.startsWith('Existing exact rules'));
    const {text, options, ...rest} = operation.parameters;
    const {text: originalText, options: originalOptions, ...originalRest} = existing;
    assert.deepEqual(rest, originalRest);
    const {systemMessage, ...otherOptions} = options;
    const {systemMessage: originalMessage, ...originalOtherOptions} = originalOptions;
    assert.deepEqual(otherOptions, originalOtherOptions);
  }
  const requests = operations.filter(op => op.node?.type === 'n8n-nodes-base.httpRequest');
  assert.equal(requests.length, 2);
  assert.ok(requests.every(op => op.node.parameters.url === 'https://ai.home.arpa/internal/workflow-retrieval'));
  assert.ok(requests.every(op => op.node.credentials.httpHeaderAuth.id === 'verified-shopping-id'));
  assert.ok(requests.every(op => op.node.parameters.options.batching.batch.batchSize === 2
    && op.node.parameters.options.batching.batch.batchInterval === 250));
  assert.equal(operations.some(op => ['removeNode', 'setWorkflowSettings', 'setNodeCredential'].includes(op.type)), false);
  assert.match(CATEGORY_GUIDANCE, /similarity is not identity/);
  assert.match(OFFER_GUIDANCE, /Evaluate all original candidates/);
});

test('storage operations preserve existing claim/apply, credentials, schedules and settings', () => {
  const original = {nodes: [{name: 'Lager categorizer', parameters: {options: {systemMessage: 'IDs and revisions are exact'}}}]};
  const operations = storageOperations(original, 'verified-storage-id');
  assert.ok(operations.some(op => op.source === 'Category sync result' && op.target === 'Refresh confirmed Lager examples'));
  assert.equal(operations.some(op => op.nodeName === 'Apply Lager categories' || op.nodeName === 'Claim due Lager batch'), false);
  assert.equal(operations.some(op => ['removeNode', 'setWorkflowSettings', 'setNodeCredential'].includes(op.type)), false);
  assert.throws(() => storageOperations(original, ''), /Verified retrieval credential/);
});

test('targeted migration is idempotent', () => {
  assert.deepEqual(shoppingOperations({nodes: [{name: 'Shopping category examples'}]}, 'verified'), []);
  assert.deepEqual(storageOperations({nodes: [{name: 'Lager categorizer'}, {name: 'Lager category examples'}]}, 'verified'), []);
});
