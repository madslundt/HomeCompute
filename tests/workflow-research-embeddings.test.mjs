import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const directory = new URL('../automations/semantic-research/', import.meta.url);
const common = readFileSync(new URL('retrieval-common.js', directory), 'utf8');
const timestamp = '2026-10-26T11:00:00+01:00';
function run(file, input = {}, upstream = {}) {
  const context = {$json: input, $now: {toISO: () => timestamp},
    $input: {all: () => input, first: () => input[0]},
    $: name => ({item: {json: upstream[name]}, first: () => ({json: upstream[name]})})};
  return JSON.parse(JSON.stringify(vm.runInNewContext('(function(){' + common + '\n' + readFileSync(new URL(file, directory), 'utf8') + '})()', context)));
}
const pageA = {id: '12345678-1234-1234-1234-123456789abc', property_ai_prompt: 'Quiet family experiences', property_success_criteria: 'Price checked today'};
const pageB = {id: 'abcdefab-abcd-abcd-abcd-abcdefabcdef', property_ai_prompt: 'Public transport', property_success_criteria: 'Accessible'};
function story(id = 41) {
  const comments = Array.from({length: 20}, (_, index) => ({id: id * 100 + index,
    text: index < 10 ? 'same technical point repeated number ' + index : 'Different battery environmental tradeoff ' + index, replies: []}));
  return {id, rank: id - 40, title: 'Battery research', submission: 'New research', articleUrl: 'https://example.com/' + id,
    discussionUrl: 'https://news.ycombinator.com/item?id=' + id, comments: comments.slice(0, 8), commentCandidates: comments};
}

test('Notion context and constraints survive; every page gets its own exact scope', () => {
  for (const page of [pageA, pageB]) {
    const prepared = run('notion-prepare.js', {markdown: 'Preferences: quiet. Reject noisy resorts.'}, {Filter: page}).json;
    assert.equal(prepared.context, 'Preferences: quiet. Reject noisy resorts.');
    assert.equal(prepared.source_id, 'notion:' + page.id.replaceAll('-', ''));
    assert.equal(prepared.retrievalBody.source_id, prepared.source_id);
    assert.ok(prepared.retrievalBody.query.includes(page.property_success_criteria));
  }
  assert.throws(() => run('notion-prepare.js', {}, {Filter: {id: 'bad'}}), /identity/);
});

test('Multilingual retrieval queries stay inside the service UTF-8 byte budget', () => {
  const page = {...pageA, property_ai_prompt: '🌍ø旅'.repeat(1000)};
  const query = run('notion-prepare.js', {}, {Filter: page}).json.retrievalBody.query;
  assert.ok(Buffer.byteLength(query) <= 1100);
  const source = {...story(), title: page.property_ai_prompt};
  const request = run('news-requests.js', {}, {'Prepare five stories and comments': {stories: [source]}})[0].json;
  assert.ok(Buffer.byteLength(request.retrievalBody.query) <= 1100);
});

test('Notion cross-page, malformed, offline and timeout responses never supply context', () => {
  const prepared = run('notion-prepare.js', {context: 'Keep all constraints'}, {Filter: pageA}).json;
  for (const response of [{}, {error: 'timeout'}, {available: false, results: []}, {available: true, results: null},
    {available: true, results: [{id: 'bad', text: 'private elsewhere', source_id: 'notion:' + pageB.id.replaceAll('-', '')}]}]) {
    const applied = run('notion-apply.js', response, {'Prepare page research retrieval': prepared}).json;
    assert.equal(applied.context, 'Keep all constraints');
    assert.deepEqual(applied.retrieved_history, []);
  }
});

test('Notion history is bounded and archives only saved output with dated page provenance', () => {
  const prepared = run('notion-prepare.js', {context: 'Current'}, {Filter: pageA}).json;
  const response = {available: true, results: Array.from({length: 10}, (_, i) => ({id: '' + i, text: 'x'.repeat(3000), source_id: prepared.source_id,
    metadata: {title: 'History', url: 'https://www.notion.so/' + pageA.id.replaceAll('-', ''), observed_at: timestamp}}))};
  const applied = run('notion-apply.js', response, {'Prepare page research retrieval': prepared}).json;
  assert.equal(applied.retrieved_history.length, 6);
  assert.equal(applied.retrieved_history[0].text.length, 1400);
  const saved = run('notion-remember.js', {id: pageA.id}, {Filter: pageA, 'AI Agent': {output: {updated_context: '<b>Confirmed</b> research'}}}).json;
  assert.equal(saved.retrievalBody.records[0].text, 'Confirmed research');
  assert.equal(saved.retrievalBody.records[0].metadata.observed_at, timestamp);
  assert.ok(saved.retrievalBody.records[0].metadata.url.endsWith(pageA.id.replaceAll('-', '')));
  assert.equal(run('notion-remember.js', {}, {Filter: pageA, 'AI Agent': {output: {}}}), null);
});

test('News ranking and links remain fixed while comment selection uses only submitted IDs', () => {
  const source = story();
  const requests = run('news-requests.js', {}, {'Prepare five stories and comments': {stories: [source]}});
  assert.equal(requests[0].json.retrievalBody.records.length, 20);
  const response = {available: true, results: [
    {id: '999999', text: 'fabrication'},
    ...[...source.commentCandidates].reverse().map(comment => ({id: String(comment.id), text: 'ignored manipulated text'}))]};
  const selected = run('news-select.js', response, {'Prepare news semantic requests': requests[0].json}).json;
  assert.equal(selected.story.comments.length, 8);
  assert.equal(selected.story.comments[0].id, source.commentCandidates.at(-1).id);
  assert.deepEqual([selected.story.id, selected.story.rank, selected.story.articleUrl, selected.story.discussionUrl],
    [source.id, source.rank, source.articleUrl, source.discussionUrl]);
  for (const comment of selected.story.comments) assert.ok(source.commentCandidates.some(original => original.id === comment.id && original.text === comment.text));
  assert.equal(selected.story.commentCandidates, undefined);
});

test('News empty/malformed/unavailable rank fallback preserves original eight-comment budget', () => {
  for (const response of [{}, {error: 'timeout'}, {available: false}, {available: true, results: {}}, {available: true, results: []}]) {
    const source = story();
    const selected = run('news-select.js', response, {'Prepare news semantic requests': {story: source}}).json;
    assert.deepEqual(selected.story.comments, source.comments);
  }
  const empty = story(); empty.comments = []; empty.commentCandidates = [];
  assert.deepEqual(run('news-select.js', {available: true, results: []}, {'Prepare news semantic requests': {story: empty}}).json.story.comments, []);
});

test('Earlier coverage requires dated public source and excludes current story', () => {
  const original = story();
  const response = {available: true, results: [
    {id: 'current', text: 'Current', source_id: 'hn:41', metadata: {url: original.discussionUrl, observed_at: '2026-10-25T00:00:00Z'}},
    {id: 'future', text: 'Future', source_id: 'hn:40', metadata: {url: 'https://news.ycombinator.com/item?id=40', observed_at: '2030-01-01T00:00:00Z'}},
    {id: 'invalid', text: 'Invalid', source_id: 'hn:40', metadata: {url: 'https://private.home.arpa', observed_at: 'yesterday'}},
    {id: 'past', text: 'Earlier evidence', source_id: 'hn:40', metadata: {title: 'Old story', url: 'https://news.ycombinator.com/item?id=40', observed_at: '2026-10-25T00:00:00Z'}}]};
  const applied = run('news-coverage.js', response, {'Select relevant diverse comments': {story: original}}).json;
  assert.equal(applied.story.earlier_coverage.length, 1);
  assert.equal(applied.story.earlier_coverage[0].text, 'Earlier evidence');
});

test('Assembly restores official order and rejects changed links; archive has checked date and exact IDs', () => {
  const stories = Array.from({length: 5}, (_, index) => story(41 + index));
  const incoming = [...stories].reverse().map(story => ({json: {story}}));
  const assembled = run('news-assemble.js', incoming, {'Prepare five stories and comments': {stories, date: '26 Oct 2026'}})[0].json;
  assert.deepEqual(assembled.stories.map(s => s.id), [41, 42, 43, 44, 45]);
  incoming[0].json.story = {...incoming[0].json.story, articleUrl: 'https://evil.example'};
  assert.throws(() => run('news-assemble.js', incoming, {'Prepare five stories and comments': {stories}}), /identity/);
  const archived = run('news-remember.js', {}, {'Assemble ranked news context': assembled});
  assert.equal(archived.length, 5);
  assert.equal(archived[0].json.retrievalBody.source_id, 'hn:41');
  assert.equal(archived[0].json.retrievalBody.records[0].metadata.observed_at, timestamp);
});
