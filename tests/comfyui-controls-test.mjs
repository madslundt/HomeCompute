#!/usr/bin/env node
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../deploy/comfyui-control/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const terminal = (mode = 'text') => ({ mode, target_mode: mode, text_running: mode === 'text', comfy_running: ['image', 'video'].includes(mode), comfy_ready: ['image', 'video'].includes(mode), queue_running: 0, queue_pending: 0, idle_seconds: 0, idle_timeout_seconds: 1800, transition: null });
const transition = (phase, fields = {}) => ({ ...terminal(), mode: 'starting', target_mode: 'image', transition: { action: 'start', phase, elapsed_seconds: 40, phase_elapsed_seconds: 3, workflows: null, agent_turns: null, text_requests: null, reason: null, ...fields } });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function page(initial, { compact = false, hostname = 'models.home.arpa' } = {}) {
  let now = 0, sequence = 0;
  const timers = new Map(), requests = [];
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map(match => match[1]);
  const elements = Object.fromEntries(ids.map(id => [id, {
    value: id === 'note' ? html.match(/id="note"[^>]*>([^<]*)</)[1] : '', writes: 0, attributes: {}, dataset: {}, listeners: {}, disabled: false,
    get textContent() { return this.value; },
    set textContent(value) { this.value = value; this.writes++; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  }]));
  const schedule = (callback, delay, interval = 0) => { const id = ++sequence; timers.set(id, { callback, at: now + delay, interval }); return id; };
  const fixture = { status: initial, handler: null };
  const response = value => ({ ok: !value?.httpError, status: value?.httpError || 200, json: async () => structuredClone(value) });
  const fetch = async (url, options = {}) => {
    requests.push({ url, ...options, time: now });
    const value = fixture.handler ? await fixture.handler(url, options) : fixture.status;
    if (value instanceof Error) throw value;
    return response(value);
  };
  const context = vm.createContext({ URLSearchParams, AbortController, location: { hostname, origin: `https://${hostname}:18188`, search: compact ? '?compact=1' : '' },
    document: { body: { classList: { add() {} } }, getElementById: id => elements[id] }, fetch,
    performance: { now: () => now }, setTimeout: (fn, delay) => schedule(fn, delay), clearTimeout: id => timers.delete(id),
    setInterval: (fn, delay) => schedule(fn, delay, delay) });
  const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
  vm.runInContext(script, context);
  await flush();
  return { elements, fixture, requests, flush,
    async advance(milliseconds) {
      const until = now + milliseconds;
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.at <= until).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        const [id, timer] = next; now = timer.at; timers.delete(id);
        if (timer.interval) timers.set(id, { ...timer, at: now + timer.interval });
        timer.callback(); await flush();
      }
      now = until; await flush();
    },
    click(id) { elements[id].listeners.click({ preventDefault() {} }); },
  };
}

let checks = 0;
async function test(name, run) { await run(); checks++; console.log('PASS ' + name); }

await test('each exclusive model has accurate readiness and only its own launch link', async () => {
  for (const mode of ['text', 'image', 'video']) {
    const p = await page(terminal(mode));
    assert.equal(p.elements[`${mode}-state`].textContent, 'Running · ready');
    assert.equal(p.elements[`start-${mode}`].disabled, true);
    for (const other of ['text', 'image', 'video'].filter(value => value !== mode)) {
      assert.equal(p.elements[`${other}-state`].textContent, 'Stopped');
      assert.equal(p.elements[`start-${other}`].disabled, false);
    }
    for (const media of ['image', 'video']) {
      assert.equal(p.elements[`open-${media}`].getAttribute('aria-disabled'), String(mode !== media));
      assert.equal(p.elements[`stop-${media}`].disabled, mode !== media);
    }
    assert.equal(p.elements['open-video-chat'].getAttribute('aria-disabled'), String(mode !== 'video'));
  }
  const p = await page({ ...terminal('video'), comfy_ready: false });
  assert.match(p.elements.status.textContent, /LTX-2.5 is loading/);
  assert.equal(p.elements['open-video'].getAttribute('aria-disabled'), 'true');
  assert.equal(p.elements['open-video-chat'].getAttribute('aria-disabled'), 'true');
  const generic = await page({ ...terminal(), text_display_name: 'Text model' });
  assert.equal(generic.elements['text-name'].textContent, 'Qwen3.8 Flash Next');
});

await test('all transition phases reflect the target model and protect every action', async () => {
  const phases = {
    waiting_workflows: 'Waiting for text work…', draining_text: 'Finishing text responses…',
    stopping_text: 'Stopping the text model…', starting_comfyui: 'Starting video mode…',
    waiting_comfyui: 'Checking video readiness…', stopping_comfyui: 'Stopping the current media model…',
    starting_text: 'Starting the text model…', waiting_text: 'Loading the text model…',
  };
  for (const [phase, heading] of Object.entries(phases)) {
    const p = await page({ ...transition(phase), target_mode: phase.endsWith('_text') ? 'text' : 'video' });
    assert.equal(p.elements.status.textContent, heading);
    assert.equal(p.elements.status.getAttribute('aria-busy'), 'true');
    for (const action of ['start-text', 'start-image', 'start-video', 'stop-image', 'stop-video']) assert(p.elements[action].disabled);
    assert.equal(p.elements['open-video'].getAttribute('aria-disabled'), 'true');
    assert.equal(p.elements['open-video-chat'].getAttribute('aria-disabled'), 'true');
    assert.equal(p.elements['open-image'].getAttribute('aria-disabled'), 'true');
  }
  const draining = await page({ ...transition('waiting_workflows'), text_running: true });
  assert.equal(draining.elements['text-state'].textContent, 'Running · finishing work');
  const p = await page(transition('starting_comfyui'));
  assert.equal(p.elements.status.textContent, 'Starting image mode…');
  assert.match(p.elements.detail.textContent, /Qwen Image 2.1/);
});

await test('unknown, positive and confirmed-zero counts stay distinct; model and HTTP counts never add', async () => {
  const p = await page(transition('waiting_workflows'));
  assert.match(p.elements.detail.textContent, /Checking running workflows/);
  p.fixture.status = transition('waiting_workflows', { workflows: 2, agent_turns: 1 });
  await p.advance(3000);
  assert.match(p.elements.detail.textContent, /2 workflows · 1 agent turn active/);
  p.fixture.status = transition('waiting_workflows', { workflows: 0, agent_turns: 0 });
  await p.advance(3000);
  assert.match(p.elements.detail.textContent, /Confirming completion/);
  p.fixture.status = { ...transition('draining_text'), llm: { active_requests: 1 } };
  await p.advance(3000);
  assert.match(p.elements.detail.textContent, /Cannot read model activity yet/);
  p.fixture.status = { ...transition('draining_text', { text_requests: { running: 1, waiting: 0 } }), llm: { active_requests: 1 } };
  await p.advance(3000);
  assert.match(p.elements.detail.textContent, /Model: 1 running · 0 queued. 1 response connection open/);
  assert.match(p.elements.requests.textContent, /Text requests: 1 running · 0 queued. 1 response connection open/);
  assert.doesNotMatch(p.elements.requests.textContent, /2.*active/);
});

await test('elapsed ticks between polls, does not reset at a new phase, and does not rewrite live labels', async () => {
  const p = await page(transition('waiting_comfyui'));
  assert.equal(p.elements.elapsed.textContent, 'Loading image model · 3s (40s total)');
  const statusWrites = p.elements.status.writes, detailWrites = p.elements.detail.writes;
  await p.advance(1000);
  assert.equal(p.elements.elapsed.textContent, 'Loading image model · 4s (41s total)');
  assert.equal(p.elements.status.writes, statusWrites);
  assert.equal(p.elements.detail.writes, detailWrites);
  p.fixture.status = transition('starting_comfyui', { elapsed_seconds: 43, phase_elapsed_seconds: 0 });
  await p.advance(2000);
  assert.equal(p.elements.elapsed.textContent, 'Starting image model · 0s (43s total)');
  p.fixture.status = terminal('image');
  await p.advance(3000);
  assert.equal(p.elements.elapsed.textContent, '');
});

await test('one labelled timer avoids duplicate total and step values in full and compact controls', async () => {
  for (const compact of [false, true]) {
    const p = await page(transition('waiting_workflows', { elapsed_seconds: 840, phase_elapsed_seconds: 840 }), { compact });
    assert.equal(p.elements.elapsed.textContent, 'Waiting for active work · 14m');
    await p.advance(1000);
    assert.equal(p.elements.elapsed.textContent, 'Waiting for active work · 14m 1s');
    assert.doesNotMatch(p.elements.elapsed.textContent, /total|step/i);
    const near = await page(transition('waiting_text', { elapsed_seconds: 24.999, phase_elapsed_seconds: 20 }), { compact });
    assert.equal(near.elements.elapsed.textContent, 'Loading text model · 20s');
    const boundary = await page(transition('waiting_text', { elapsed_seconds: 25, phase_elapsed_seconds: 20 }), { compact });
    assert.equal(boundary.elements.elapsed.textContent, 'Loading text model · 20s (25s total)');
    p.fixture.status = { ...transition('waiting_text', { elapsed_seconds: 920, phase_elapsed_seconds: 20 }), target_mode: 'text' };
    await p.advance(2000);
    assert.equal(p.elements.elapsed.textContent, 'Loading text model · 20s (15m 20s total)');
  }
});

await test('partial and unknown elapsed metadata never invents a phase duration', async () => {
  const totalOnly = await page(transition('waiting_text', { elapsed_seconds: 40, phase_elapsed_seconds: null }));
  assert.equal(totalOnly.elements.elapsed.textContent, 'Switching models · 40s elapsed');
  const phaseOnly = await page(transition('waiting_text', { elapsed_seconds: null, phase_elapsed_seconds: 3 }));
  assert.equal(phaseOnly.elements.elapsed.textContent, 'Loading text model · 3s');
  const unknown = await page(transition('future_phase', { elapsed_seconds: 3, phase_elapsed_seconds: 3 }));
  assert.equal(unknown.elements.elapsed.textContent, 'Switching models · 3s');
  const invalid = await page(transition('waiting_text', { elapsed_seconds: -1, phase_elapsed_seconds: '40' }));
  assert.equal(invalid.elements.elapsed.textContent, '');
});

await test('expected starts use each model measured history, and never guess from active-work waits', async () => {
  const p = await page({ ...transition('waiting_workflows', { elapsed_seconds: 1800, phase_elapsed_seconds: 1800 }),
    startup_timings: { text: { seconds: 486, samples: 2 }, image: { seconds: 8.4, samples: 1 }, video: { seconds: null, samples: 0 } } });
  assert.equal(p.elements['text-startup'].textContent, 'Expected start: ~8m 6s');
  assert.equal(p.elements['image-startup'].textContent, 'Expected start: ~8s');
  assert.equal(p.elements['video-startup'].textContent, 'Expected start: Not measured yet');
  assert.match(html, /expected start times exclude that wait/);
  assert.match(html, /first image or video can take longer while its model loads/);
  p.fixture.status = { ...terminal('video'), startup_timings: { text: { seconds: '486', samples: 2 }, image: { seconds: 10, samples: 0 }, video: { seconds: 12.7, samples: 3 } } };
  await p.advance(3000);
  assert.equal(p.elements['text-startup'].textContent, 'Expected start: Not measured yet');
  assert.equal(p.elements['image-startup'].textContent, 'Expected start: Not measured yet');
  assert.equal(p.elements['video-startup'].textContent, 'Expected start: ~13s');
});

await test('all mode actions POST explicit empty JSON to the correct exclusive target', async () => {
  for (const [action, from, target] of [
    ['start-image', 'text', 'image'], ['start-video', 'text', 'video'],
    ['start-image', 'video', 'image'], ['start-video', 'image', 'video'],
    ['start-text', 'video', 'text'], ['stop-image', 'image', 'text'], ['stop-video', 'video', 'text'],
  ]) {
    const p = await page(terminal(from));
    const waiting = deferred();
    p.fixture.handler = (url, options) => options.method === 'POST' ? { status: { ...transition(target === 'text' ? 'stopping_comfyui' : 'starting_comfyui'), target_mode: target } } : waiting.promise;
    p.click(action); await p.flush();
    const posts = p.requests.filter(request => request.method === 'POST');
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, `/control/api/start/${target}`);
    assert.equal(posts[0].body, '{}');
    assert.equal(posts[0].headers['Content-Type'], 'application/json');
    assert.equal(p.elements.status.getAttribute('aria-busy'), 'true');
    assert.match(p.elements.status.textContent, target === 'text' ? /Stopping the current media/ : new RegExp(`Starting ${target} mode`));
  }
});

await test('running, queued and unknown media activity protect every switch for both media modes', async () => {
  for (const mode of ['image', 'video']) {
    for (const queue of [{ queue_running: 1, queue_pending: 0 }, { queue_running: 0, queue_pending: 2 }, { queue_running: null, queue_pending: null }]) {
      const p = await page({ ...terminal(mode), ...queue });
      for (const action of ['start-text', 'start-image', 'start-video', 'stop-image', 'stop-video']) {
        assert(p.elements[action].disabled, action);
        p.click(action); await p.flush();
      }
      assert.equal(p.requests.filter(request => request.method === 'POST').length, 0);
      assert.match(p.elements['start-text'].title, /media queue to be empty/);
      assert.equal(p.elements[`open-${mode}`].getAttribute('aria-disabled'), 'false');
    }
  }
});

await test('server busy refusal stays visible and cannot silently switch models', async () => {
  const p = await page(terminal('video'));
  p.fixture.handler = (url, options) => options.method === 'POST' ? { httpError: 409, error: 'Wait for the media queue to finish.' } : { ...terminal('video'), queue_running: 1 };
  p.click('start-image'); await p.flush();
  assert.match(p.elements.error.textContent, /media queue to finish/);
  assert.equal(p.elements['video-state'].textContent, 'Running · ready');
  assert(p.elements['start-image'].disabled);
});

await test('three-second polling refreshes live activity and media queue using only the status endpoint', async () => {
  const p = await page({ ...terminal('text'), llm: { active_requests: 1 } });
  assert.match(p.elements.requests.textContent, /1 response connection open/);
  p.fixture.status = { ...terminal('text'), llm: { active_requests: 3 } };
  await p.advance(3000);
  assert.match(p.elements.requests.textContent, /3 response connections open/);
  await p.advance(3000);
  assert.deepEqual(p.requests.map(request => request.time), [0, 3000, 6000]);
  assert(p.requests.every(request => request.url === '/control/api/status'));
  const media = await page({ ...terminal('video'), queue_running: 1, queue_pending: 2 });
  assert.match(media.elements.requests.textContent, /Media queue: 1 running · 2 queued/);
});

await test('both media modes use the 1800-second idle return countdown', async () => {
  for (const mode of ['image', 'video']) {
    const p = await page({ ...terminal(mode), idle_seconds: 0 });
    assert.match(p.elements.detail.textContent, /30 min/);
    p.fixture.status = { ...terminal(mode), idle_seconds: 900 };
    await p.advance(3000);
    assert.match(p.elements.detail.textContent, /15 min/);
    p.fixture.status = { ...terminal(mode), idle_seconds: 1801 };
    await p.advance(3000);
    assert.match(p.elements.detail.textContent, /0 min/);
  }
});

await test('connection failure hides readiness, disables actions, then announces recovery', async () => {
  const p = await page(terminal('video'));
  p.fixture.handler = () => new Error('Connection lost');
  await p.advance(3000);
  assert.equal(p.elements.status.textContent, 'Progress updates interrupted');
  assert.equal(p.elements['open-video'].getAttribute('aria-disabled'), 'true');
  assert.match(p.elements.error.textContent, /Connection lost/);
  assert(p.elements['start-text'].disabled);
  p.fixture.handler = null;
  await p.advance(3000);
  assert.equal(p.elements.status.textContent, 'Video is ready');
  assert.equal(p.elements.error.textContent, '');
  assert.equal(p.elements.elapsed.textContent, 'Connection restored.');
});

await test('hung requests visibly mark stale status before timeout', async () => {
  const p = await page(terminal('image'));
  p.fixture.handler = () => deferred().promise;
  await p.advance(12000);
  assert.equal(p.elements.status.textContent, 'Progress updates interrupted');
  assert.equal(p.elements['open-image'].getAttribute('aria-disabled'), 'true');
  assert.match(p.elements.elapsed.textContent, /Last update 12s ago/);
  assert.match(p.elements.error.textContent, /out of date/);
});

await test('old polls cannot overwrite a newly accepted video switch', async () => {
  const p = await page(terminal());
  const old = deferred(), latest = deferred(); let gets = 0;
  p.fixture.handler = (url, options) => options.method === 'POST' ? { ...transition('waiting_comfyui'), target_mode: 'video' } : (++gets === 1 ? old.promise : latest.promise);
  await p.advance(3000);
  p.click('start-video'); await p.flush();
  assert.equal(p.elements.status.textContent, 'Checking video readiness…');
  old.resolve(terminal('image')); await p.flush();
  assert.equal(p.elements.status.textContent, 'Checking video readiness…');
  assert.equal(p.elements['open-video'].getAttribute('aria-disabled'), 'true');
});

await test('rollback identifies video errors, keeps compact controls and preserves exact workflow destinations', async () => {
  const p = await page({ ...transition('waiting_text', { reason: 'video_start_failed' }), target_mode: 'text' }, { compact: true });
  assert.equal(p.elements.status.textContent, 'Restoring text after video error…');
  assert.match(p.elements.detail.textContent, /Video startup failed/);
  assert.match(html, /href="https:\/\/chat\.home\.arpa\/\?models=homecompute-images"/);
  assert.match(html, /href="https:\/\/chat\.home\.arpa\/\?models=homecompute_videos"/);
  assert.match(html, /href="https:\/\/comfyui\.home\.arpa\/"/);
  const tailnet = await page(terminal('video'), { hostname: 'home-core.tail479ad.ts.net' });
  assert.equal(tailnet.elements['open-video'].href, 'https://home-core.tail479ad.ts.net:18188/');
  assert.match(html, /LTX-2.5-Text-to-Video/);
  assert.match(html, /30 minutes idle/);
  assert.match(html, /id="elapsed" aria-live="off"/);
});

await test('missing transition metadata uses target_mode and unknown phases cannot invent readiness', async () => {
  const legacy = { ...terminal(), mode: 'starting', target_mode: 'video', text_running: true };
  delete legacy.transition;
  const p = await page(legacy);
  assert.equal(p.elements.status.textContent, 'Switching to LTX-2.5…');
  assert.match(p.elements.detail.textContent, /Waiting for text work to finish/);
  const unknown = await page({ ...transition('future_phase'), error: '<img src=x onerror=alert(1)>' });
  assert.equal(unknown.elements.status.textContent, 'Checking switch progress…');
  assert.equal(unknown.elements['open-image'].getAttribute('aria-disabled'), 'true');
  assert.equal(unknown.elements.error.textContent, '<img src=x onerror=alert(1)>');
  assert.doesNotMatch(script, /innerHTML/);
});

await test('old controller compatibility disables every mutation without inventing the new idle policy', async () => {
  for (const mode of ['text', 'image']) {
    const legacy = { ...terminal(mode), idle_timeout_seconds: 3600 };
    delete legacy.target_mode;
    const p = await page(legacy);
    for (const action of ['start-text', 'start-image', 'start-video', 'stop-image', 'stop-video']) {
      assert(p.elements[action].disabled);
      p.click(action); await p.flush();
    }
    assert.equal(p.requests.filter(request => request.method === 'POST').length, 0);
    assert.match(p.elements.compatibility.textContent, /30-minute idle policy will apply after deployment/);
    assert.match(p.elements.note.textContent, /current controller still uses its existing idle policy/);
    if (mode === 'image') assert.match(p.elements.detail.textContent, /60 min/);
    p.fixture.status = terminal(mode);
    await p.advance(3000);
    assert.equal(p.elements.compatibility.textContent, '');
    assert.match(p.elements.note.textContent, /after 30 minutes idle/);
    assert.equal(p.elements[mode === 'text' ? 'start-image' : 'start-text'].disabled, false);
  }
});

console.log(`${checks} behavior checks passed`);
