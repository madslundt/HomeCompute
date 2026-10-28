import { ACTIVITIES, ROLES, toolActivity } from './catalog.mjs';

export const STALE_MS = 5 * 60 * 1000;
const MAX_RUNS = 256;

export class OfficeActivity {
  constructor(roster, clock = Date.now) {
    this.roster = roster;
    this.clock = clock;
    this.runs = new Map();
    this.finished = new Map();
    this.events = [];
    this.sequence = 0;
  }

  identity(event, ctx) {
    // Identities come only from native context, never prompt/tool parameters.
    const session = ctx?.sessionKey;
    const agent = typeof session === 'string' ? /^agent:([^:]+):/.exec(session)?.[1] : undefined;
    if (!Object.hasOwn(ROLES, agent) || !this.roster.some(a => a.id === agent)
        || (ctx?.agentId !== undefined && ctx.agentId !== agent)) return null;
    const run = ctx?.runId ?? event?.runId;
    if (typeof session !== 'string' || session.length > 512
        || typeof run !== 'string' || !run.length || run.length > 256) return null;
    return { agent, key: JSON.stringify([agent, session, run]) };
  }

  observe(kind, event, ctx) {
    const identity = this.identity(event, ctx);
    if (!identity) return;
    const now = this.clock();
    // Expired runs are removed, not kept active by the dashboard's heartbeat.
    for (const [key, run] of this.runs) if (now - run.observed_at > STALE_MS) this.runs.delete(key);
    const previous = this.runs.get(identity.key);
    let activity = 'thinking';
    let status = 'working';
    if (kind === 'end') {
      this.runs.delete(identity.key);
      status = event.success === true ? 'idle' : 'error';
      activity = status;
      this.finished.set(identity.agent, { status, activity, observed_at: now, started_at: previous?.started_at ?? now });
    } else {
      if (!previous && this.runs.size >= MAX_RUNS) return;
      if (kind === 'tool') activity = toolActivity(ctx.toolName);
      if (activity === 'waiting') status = 'waiting';
      this.runs.set(identity.key, { agent: identity.agent, status, activity, observed_at: now, started_at: previous?.started_at ?? now });
    }
    if (!previous || previous.activity !== activity || kind === 'end') {
      this.events.unshift({ id: ++this.sequence, agent: identity.agent, activity, status, at: now });
      this.events.length = Math.min(40, this.events.length);
    }
  }

  snapshot() {
    const now = this.clock();
    return {
      schema_version: 1, mode: 'live', observed_at: now,
      agents: this.roster.map(agent => {
        const active = [...this.runs.values()].filter(run => run.agent === agent.id && now - run.observed_at <= STALE_MS);
        // One completed session must never hide another session still working.
        const latest = active.sort((a, b) => (a.status === 'waiting') - (b.status === 'waiting') || b.observed_at - a.observed_at)[0]
          ?? this.finished.get(agent.id);
        const fresh = latest && now - latest.observed_at <= STALE_MS;
        return { ...agent, status: fresh ? latest.status : 'unknown', activity: fresh ? latest.activity : 'unknown',
          active_runs: active.length, observed_at: latest?.observed_at ?? null,
          started_at: fresh ? latest.started_at : null };
      }),
      events: this.events.filter(event => now - event.at <= STALE_MS),
    };
  }
}

export function demoSnapshot(roster, now = Date.now()) {
  const phase = Math.floor(now / 14000);
  const activities = ['coordinating', 'planning', 'research', 'coding', 'operations', 'shopping', 'idle', 'travel', 'cooking', 'administration', 'finance', 'family'];
  const agents = roster.map((agent, index) => {
    const cycle = (phase + index * 3) % 13;
    const activity = cycle === 0 ? 'waiting' : cycle === 1 ? 'idle' : activities[index];
    return { ...agent, status: activity === 'idle' ? 'idle' : activity === 'waiting' ? 'waiting' : 'working',
      activity, active_runs: activity === 'idle' ? 0 : 1, observed_at: now, started_at: now - (index + 1) * 9000 };
  });
  return { schema_version: 1, mode: 'demo', observed_at: now, agents,
    events: agents.map((agent, i) => ({ id: `${phase}-${i}`, agent: agent.id,
      status: agent.status, activity: Object.hasOwn(ACTIVITIES, agent.activity) ? agent.activity : 'thinking', at: phase * 14000 - i * 7000 })) };
}
