import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scripts = Object.fromEntries(['morning-overview', 'tomorrow-preparation'].map(name =>
  [name, fs.readFileSync(path.join(root, 'automations/household-routines', name + '.js'), 'utf8')]));
const at = '2026-10-27T06:00:00Z';
function sources(now = at) {
  const calendar = {entity_id: 'sensor.familien_lundt_dashboard_agenda', state: '0', attributes: {events: [], refreshed_at: now}};
  const school = {entity_id: 'sensor.aula_summaries', state: 'inactive', attributes: {summary_payload: {active: false, tasks: [], updated_at: now}}};
  return {
    'Get Home daytime forecast': {entity_id: 'sensor.home_daytime_forecast', state: now, attributes: {daytime_forecast: [{datetime: now, temperature: 8, condition: 'sunny'}]}},
    'Get today calendar': calendar, 'Get tomorrow calendar': calendar,
    'Get important reminders': school, 'Get tomorrow school reminders': school,
    'Get morning reminders': {entity_id: 'sensor.morning_reminder', state: 'inactive', attributes: {updated_at: now, active: false, tasks: []}},
    'Get shared family tasks': {entity_id: 'sensor.homecompute_family_tasks', state: '0', attributes: {tasks: [], total: 0, updated_at: now, truncated: false}},
  };
}
function run(name, nodes = sources(), now = at) {
  const called = [];
  const context = {Date, Intl, $now: {toISO: () => now}, $: node => {
    called.push(node);
    if (!(node in nodes)) throw new Error('Node missing');
    return {first: () => ({json: nodes[node]})};
  }};
  const output = vm.runInNewContext('(function(){' + scripts[name] + '\n})()', context, {timeout: 1000});
  return {json: JSON.parse(JSON.stringify(output[0].json)), called};
}
function size(message) {
  const json = JSON.stringify(message);
  return json.length + (json.match(/[^\x00-\x7f]/g) || []).length * 5;
}
function event(summary, start, end, extra = {}) {return {summary, start, end, ...extra};}
function familyTask(title, due, extra = {}) {return {task_id: title, title, due_at: due, status: 'open', assignee: '', ...extra};}

test('morning weather failure still delivers calendar, overdue and today shared tasks', () => {
  const nodes = sources();
  nodes['Get Home daytime forecast'] = {error: 'offline'};
  nodes['Get today calendar'].attributes.events = [event('Dentist', '2026-10-27T10:00:00+01:00', '2026-10-27T11:00:00+01:00')];
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('Pack bag', at), familyTask('Replace filter', '2026-10-26T18:00:00+01:00'), familyTask('Later', '2026-10-29T18:00:00+01:00')];
  const {json} = run('morning-overview', nodes);
  assert.match(json.message, /Dentist/);
  assert.match(json.message, /Pack bag/);
  assert.match(json.message, /Overskredet: Replace filter/);
  assert.doesNotMatch(json.message, /Later/);
  assert.equal(json.source_status.weather, 'unavailable');
  assert.match(json.message, /Datagrundlag: vejr/);
});

test('morning preserves bounded rain windows without bridging missing forecast hours', () => {
  const nodes = sources();
  nodes['Get Home daytime forecast'].attributes.daytime_forecast = [
    {datetime: '2026-10-27T07:00:00+01:00', temperature: 8, condition: 'rainy'},
    {datetime: '2026-10-27T08:00:00+01:00', temperature: 9, condition: 'cloudy'},
    {datetime: '2026-10-27T12:00:00+01:00', temperature: 10, condition: 'rainy'},
  ];
  const {json} = run('morning-overview', nodes);
  assert.match(json.message, /Regn frem til ca\. kl\. 08/);
  assert.match(json.message, /Regn fra ca\. kl\. 12 \(sluttid ukendt\)/);
  assert.doesNotMatch(json.message, /07–12/);
});

test('Home receives weather only while adult phones retain planning and school reminders', () => {
  const nodes = sources();
  nodes['Get today calendar'].attributes.events = [event('Dentist', '2026-10-27T10:00:00+01:00', '2026-10-27T11:00:00+01:00')];
  nodes['Get important reminders'].state = 'active';
  nodes['Get important reminders'].attributes.summary_payload = {active: true, updated_at: at, tasks: [{title: 'Bring books', requires_action: true, due_at: '2026-10-27'}]};
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('Shared chore', at)];
  const {json} = run('morning-overview', nodes);
  assert.match(json.message, /Dentist/);
  assert.match(json.message, /Bring books/);
  assert.match(json.message, /Shared chore/);
  assert.match(json.home_message, /Vejret i dag/);
  assert.doesNotMatch(json.home_message, /Dentist|Bring books|Shared chore|Husk|Datagrundlag/);
  nodes['Get Home daytime forecast'] = {error: 'offline'};
  assert.match(run('morning-overview', nodes).json.home_message, /Vejrdata er ikke tilgængelige/);
});

test('calendar and school freshness cannot be replaced by fresh HA state metadata', () => {
  const nodes = sources();
  nodes['Get today calendar'].last_updated = at;
  nodes['Get today calendar'].attributes.refreshed_at = '2026-10-27T03:59:59Z';
  nodes['Get today calendar'].attributes.events = [event('Old appointment', '2026-10-27', '2026-10-29')];
  nodes['Get important reminders'].last_updated = at;
  nodes['Get important reminders'].state = 'active';
  nodes['Get important reminders'].attributes.summary_payload = {updated_at: '2026-10-25T23:59:59Z', active: true, tasks: [{title: 'Old action', requires_action: true, due_at: '2026-10-27'}]};
  for (const name of Object.keys(scripts)) {
    const {json} = run(name, nodes);
    assert.equal(json.source_status.calendar, 'stale');
    assert.equal(json.source_status.school, 'stale');
    assert.doesNotMatch(json.message, /Old appointment|Old action/);
    assert.match(json.message, /kalender \(forældet\)/);
    assert.match(json.message, /Aula \(forældet\)/);
  }
});

test('shared source freshness uses explicit timestamp and never consumes stale or future payload', () => {
  for (const [stamp, status] of [['2026-10-27T05:54:59Z', 'stale'], ['2026-10-27T06:01:01Z', 'unknown'], [undefined, 'unknown']]) {
    const nodes = sources();
    Object.assign(nodes['Get shared family tasks'], {last_updated: at});
    nodes['Get shared family tasks'].attributes.updated_at = stamp;
    nodes['Get shared family tasks'].attributes.tasks = [familyTask('Stale secret', at)];
    for (const name of Object.keys(scripts)) {
      const {json} = run(name, nodes);
      assert.equal(json.source_status.family_tasks, status);
      assert.doesNotMatch(json.message, /Stale secret/);
      assert.match(json.message, /familieopgaver/);
    }
  }
});

test('morning excludes completed and undated family tasks and reports unassigned ones', () => {
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('Done', at, {status: 'completed'}), familyTask('Undated', ''), familyTask('Assigned', at, {assignee: 'Mads'}), familyTask('Unassigned', at)];
  const {json} = run('morning-overview', nodes);
  assert.doesNotMatch(json.message, /Done|Undated/);
  assert.match(json.message, /Assigned \(Mads\)/);
  assert.match(json.message, /Unassigned \(ikke fordelt\)/);
});

test('tomorrow follows Copenhagen calendar dates across DST transitions and UTC midnight', () => {
  for (const [now, date, target] of [
    ['2026-03-28T23:30:00Z', '2026-03-29', '2026-03-30'],
    ['2026-10-24T22:30:00Z', '2026-10-25', '2026-10-26'],
    ['2026-12-31T23:30:00Z', '2027-01-01', '2027-01-02'],
  ]) {
    const {json} = run('tomorrow-preparation', sources(now), now);
    assert.equal(json.date, date);
    assert.equal(json.target_date, target);
  }
});

test('tomorrow includes exact overlapping events and excludes exclusive midnight ends and declined events', () => {
  const nodes = sources();
  nodes['Get tomorrow calendar'].attributes.events = [
    event('All day tomorrow', {date: '2026-10-28'}, {date: '2026-10-29'}),
    event('Ended today', {date: '2026-10-27'}, {date: '2026-10-28'}),
    event('Overnight', '2026-10-27T23:00:00+01:00', '2026-10-28T02:00:00+01:00'),
    event('Ends at midnight', '2026-10-27T23:00:00+01:00', '2026-10-28T00:00:00+01:00'),
    event('Cancelled', '2026-10-28T08:00:00+01:00', '2026-10-28T09:00:00+01:00', {status: 'cancelled'}),
    event('Declined', '2026-10-28T08:00:00+01:00', '2026-10-28T09:00:00+01:00', {participation_status: 'declined'}),
    event('Future', '2026-10-29T08:00:00+01:00', '2026-10-29T09:00:00+01:00'),
  ];
  const {json} = run('tomorrow-preparation', nodes);
  assert.match(json.message, /Hele dagen · All day tomorrow/);
  assert.match(json.message, /Fortsætter · Overnight/);
  assert.doesNotMatch(json.message, /Ended today|Ends at midnight|Cancelled|Declined|Future/);
});

test('tomorrow includes sourced homework and preparation but no conditional or undated obligations', () => {
  const nodes = sources();
  nodes['Get tomorrow school reminders'].state = 'active';
  nodes['Get tomorrow school reminders'].attributes.summary_payload = {
    active: true, updated_at: at, tasks: [
      {title: 'Read chapter', child: 'Alvira', kind: 'homework', due_at: '2026-10-28'},
      {title: 'Bring boots', child: 'Nora', requires_action: true, event_date: '2026-10-28'},
      {title: 'Conditional', requires_action: true, event_date: '2026-10-28', conditional: true},
      {title: 'Undated', requires_action: true},
      {title: 'Completed', requires_action: true, due_at: '2026-10-28', completed: true},
    ],
  };
  const {json} = run('tomorrow-preparation', nodes);
  assert.match(json.message, /Aula · Alvira: Read chapter/);
  assert.match(json.message, /Aula · Nora: Bring boots/);
  assert.doesNotMatch(json.message, /Conditional|Undated|Completed/);
});

test('shared tasks due tomorrow and overdue are included, later tasks omitted', () => {
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('Tomorrow chore', '2026-10-28T18:00:00+01:00'), familyTask('Past chore', '2026-10-26T18:00:00+01:00'), familyTask('Next week', '2026-11-02T18:00:00+01:00')];
  const {json} = run('tomorrow-preparation', nodes);
  assert.match(json.message, /Tomorrow chore/);
  assert.match(json.message, /Overskredet: Past chore/);
  assert.doesNotMatch(json.message, /Next week/);
});

test('a task due later today is preparation for today, not overdue', () => {
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('Tonight chore', '2026-10-27T21:00:00+01:00')];
  const {json} = run('tomorrow-preparation', nodes);
  assert.match(json.message, /I dag: Tonight chore/);
  assert.doesNotMatch(json.message, /Overskredet/);
});

test('unavailable or malformed sources remain visible even with no known items', () => {
  for (const name of Object.keys(scripts)) {
    const {json} = run(name, {});
    assert.match(json.message, /Datagrundlag:/);
    assert.equal(json.source_status.family_tasks, 'unavailable');
    assert.equal(json.source_status.calendar, 'unavailable');
    assert.ok(size(json.message) <= 1500);
  }
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.tasks = 'not a list';
  const {json} = run('tomorrow-preparation', nodes);
  assert.equal(json.source_status.family_tasks, 'unknown');
});

test('partial task projection is explicitly labelled', () => {
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.truncated = true;
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('Shared item', at)];
  for (const name of Object.keys(scripts)) {
    const {json} = run(name, nodes);
    assert.equal(json.source_status.family_tasks, 'partial');
    assert.match(json.message, /familieopgaver \(delvis\)/);
    assert.match(json.message, /Shared item/);
  }
});

test('no private task, mail, memory or unrelated node is ever queried', () => {
  const nodes = sources();
  nodes['Get private tasks'] = {secret: 'PRIVATE_TASK'};
  nodes['Get mail'] = {secret: 'PRIVATE_MAIL'};
  for (const name of Object.keys(scripts)) {
    const {json, called} = run(name, nodes);
    assert.doesNotMatch(json.message, /PRIVATE_TASK|PRIVATE_MAIL/);
    assert.ok(called.every(node => !/private|mail|memory/i.test(node)));
  }
});

test('untrusted mentions, links and control characters cannot escape notification content', () => {
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.tasks = [familyTask('@channel https://evil.example/a javascript:evil file:///private\nDo this', at, {assignee: '@all'})];
  for (const name of Object.keys(scripts)) {
    const {json} = run(name, nodes);
    assert.doesNotMatch(json.message, /@channel|@all|https:\/\/evil|javascript:evil|file:\/\/\/private/);
    assert.match(json.message, /@\u200bchannel/);
    assert.match(json.message, /\[link\]/);
  }
});

test('large Danish and emoji content stays inside pessimistic APNs encoded budget', () => {
  const nodes = sources();
  nodes['Get shared family tasks'].attributes.tasks = Array.from({length: 100}, (_, i) => familyTask('Æøå 🦄 '.repeat(50) + i, at));
  for (const name of Object.keys(scripts)) {
    const {json} = run(name, nodes);
    assert.ok(size(json.message) <= 1500, String(size(json.message)));
    assert.match(json.message, /flere punkter/);
  }
});
