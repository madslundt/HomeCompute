// Executed inside the pinned upstream app image; no database/computer/socket.
// This measures the real PiAgentRuntime, not the whole Rakazo product lifecycle.
import { PiAgentRuntime } from '/app/packages/adapters/src/index.ts';
import { readFileSync, writeFileSync } from 'node:fs';
import { randomUUID, createHash } from 'node:crypto';

const raw = readFileSync('/probe/cases.json');
const fixtures = JSON.parse(raw.toString());
const selected = process.env.COMPARISON_CASE;
const runtime = new PiAgentRuntime();
const cases = fixtures.cases.filter((c: any) => selected ? c.id === selected : !c.exclude_from_score);
if (!cases.length) throw new Error('Unknown comparison case');
for (const fixture of cases) {
  const started = performance.now();
  const runId = randomUUID();
  const events: any[] = [];
  let calls = 0;
  const timer = setTimeout(() => { void runtime.abort(runId); }, 180000);
  try {
    for await (const event of runtime.run({
      botId: 'synthetic-comparison', threadId: runId, runId,
      prompt: fixtures.instructions + '\n\n' + fixture.prompt,
      instructions: 'Complete the synthetic supplied-evidence task. Follow the requested output format.',
      history: [], tools: [], fallbackModels: [],
      model: { provider: 'openai-compatible', id: 'automation-moe',
        baseUrl: 'http://host.docker.internal:18796/v1', allowPrivateEndpoint: true,
        apiKey: 'local', maxTokens: 1024, contextWindow: 32768,
        // Capability means the endpoint accepts reasoning_effort. Leaving this
        // false omits "none" even when thinkingLevel is off on Qwen.
        reasoning: process.env.COMPARISON_REASONING_CONTROL === 'true',
        thinkingLevel: 'off', acceptsImages: false },
    })) {
      events.push(event);
      if (event.type === 'usage') calls++;
    }
    const done = events.findLast(e => e.type === 'done');
    const answer = done?.text ?? events.filter(e => e.type === 'text').map(e => e.text).join('');
    const record = { case: fixture.id, mode: 'rakazo-runtime', run_id: runId,
      elapsed_seconds: (performance.now()-started)/1000, answer, calls,
      usage: events.filter(e => e.type === 'usage'),
      event_types: events.map(e=>e.type), fixture_sha256: createHash('sha256').update(raw).digest('hex'),
      finished: Boolean(done), upstream_revision: process.env.GIT_SHA };
    writeFileSync('/evidence/rakazo-runtime-'+fixture.id+'.json', JSON.stringify(record,null,2), {flag:'wx',mode:0o600});
    console.log(JSON.stringify({...record,answer:undefined,usage:undefined}));
  } catch(error) {
    const record = {case:fixture.id,mode:'rakazo-runtime',finished:false,
      elapsed_seconds:(performance.now()-started)/1000, error:String(error)};
    writeFileSync('/evidence/rakazo-runtime-'+fixture.id+'.json',JSON.stringify(record,null,2),{flag:'wx',mode:0o600});
    console.log(JSON.stringify(record));
    process.exitCode=1;
    break;
  } finally { clearTimeout(timer); }
}
