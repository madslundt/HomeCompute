import { ACTIVITIES, ROLES } from '/catalog.mjs';
import { walkSpriteFrame, walkDirection } from '/walking.mjs';
import { resolveDestination, findPath, ROOM_LAYOUT, ROLE_FACING } from '/scene-layout.mjs';

const $ = id => document.getElementById(id);
const clockFormat = { timeZone: 'Europe/Copenhagen', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
const statusNames = { working: 'Working', waiting: 'Waiting', idle: 'Idle', unknown: 'Unknown', error: 'Needs attention' };
const specialties = {
  main: 'Atlas brings the team together at the coordination table, routes requests, and reviews specialist results.',
  personal: 'Luna’s planning desk is for school, calendars, personal plans, tasks, and reminders.',
  researcher: 'Nova follows public sources in the research library and brings back cited evidence.',
  engineer: 'Apollo’s engineering workstation is for preparing coding requests and following reviewed Codex jobs.',
  operator: 'Orion watches infrastructure, Home Assistant, and household maintenance from the operations room.',
  shopper: 'Vega’s shopping corner is for lists, offers, price comparisons, and discount watches.',
  keeper: 'Ceres keeps track of household supplies, stock, storage, and expiry dates in the inventory corner.',
  travel: 'Lyra’s maps, globe, and suitcase are for holidays, adventures, restaurants, and experiences.',
  cook: 'Hestia plans meals and recipes in the kitchen, using household stock and available offers.',
  administrator: 'Themis’s binders and paperwork are for supplied documents, renewals, and household deadlines.',
  finance: 'Plutus’s ledger desk is for budgets, supplied expenses, subscriptions, and reconciled totals.',
  family: 'Gaia’s garden is a separate shared-family space for expressly shared plans and household tasks.',
};
const colors = { working: '#b08af0', waiting: '#e5bd72', idle: '#83bb9b', unknown: '#8491aa', error: '#ee8e87' };
const sprites = new Map();
let snapshot;
let selected = 'engineer';
let filter = 'all';
let paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
let lastFrame = performance.now();
let lastSuccess = 0;
let rosterSignature = '';

function spriteStyle(element, agent) {
  element.style.setProperty('--sprite-x', `${(agent.sprite % 4) * 100 / 3}%`);
  element.style.setProperty('--sprite-y', `${Math.floor(agent.sprite / 4) * 50}%`);
  element.style.setProperty('--agent-color', agent.color);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function updateCharacters() {
  const present = new Set(snapshot.agents.map(agent => agent.id));
  for (const [id, character] of sprites) if (!present.has(id)) { character.node.remove(); sprites.delete(id); }
  snapshot.agents.forEach((agent, index) => {
    let character = sprites.get(agent.id);
    const destination = resolveDestination(agent, index);
    if (!character) {
      const node = element('button', 'character');
      const sprite = element('span', 'sprite');
      sprite.setAttribute('aria-hidden', 'true');
      node.append(sprite);
      node.addEventListener('click', () => select(agent.id));
      $('characters').append(node);
      const start = snapshot.mode === 'demo' && !paused ? resolveDestination({ ...agent, status: 'unknown' }, index) : destination;
      character = { node, sprite, spriteIndex: agent.sprite, direction: 'south', frameKey: '', position: start, destination, path: findPath(start, destination) };
      sprites.set(agent.id, character);
    }
    spriteStyle(character.node, agent);
    character.node.classList.remove('working', 'waiting', 'idle', 'unknown', 'error');
    character.node.classList.add(agent.status);
    character.status = agent.status;
    character.agentId = agent.id;
    character.node.classList.toggle('filtered', filter !== 'all' && filter !== agent.status);
    character.node.setAttribute('aria-pressed', String(agent.id === selected));
    character.node.setAttribute('aria-label', `${agent.name}, ${statusNames[agent.status]}, ${ACTIVITIES[agent.activity][0]}`);
    if (destination.some((value, i) => value !== character.destination[i])) {
      character.path = findPath(character.position, destination);
      character.destination = destination;
    }
    if (paused && matchMedia('(prefers-reduced-motion: reduce)').matches) { character.position = destination; character.path = []; }
  });
}

function renderRoster() {
  const signature = JSON.stringify([filter, selected, snapshot.agents.map(agent => [agent.id, agent.name, agent.status])]);
  if (signature === rosterSignature) return;
  rosterSignature = signature;
  const fragment = document.createDocumentFragment();
  const agents = snapshot.agents.filter(agent => filter === 'all' || agent.status === filter);
  for (const agent of agents) {
    const row = element('button', 'agent-row');
    row.setAttribute('aria-pressed', String(agent.id === selected));
    row.setAttribute('aria-label', `${agent.name}, ${agent.role}, ${statusNames[agent.status]}`);
    spriteStyle(row, agent);
    const portrait = element('span', 'mini-portrait');
    const sprite = element('span', 'sprite');
    sprite.setAttribute('aria-hidden', 'true');
    portrait.append(sprite);
    const copy = element('span');
    const name = element('strong');
    name.append(element('i', `dot ${agent.status}`), document.createTextNode(agent.name));
    copy.append(name, element('small', '', agent.role));
    row.append(portrait, copy);
    row.addEventListener('click', () => select(agent.id));
    fragment.append(row);
  }
  if (!agents.length) fragment.append(element('p', 'empty-roster', 'No agents in this state.'));
  $('roster-list').replaceChildren(fragment);
  $('team-count').textContent = `${snapshot.agents.length} agents · ${agents.length} shown`;
}

function age(time) {
  if (time === null) return 'Not observed';
  const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
  return seconds < 5 ? 'Just now' : seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`;
}

function renderInspector() {
  const index = snapshot.agents.findIndex(agent => agent.id === selected);
  const agent = snapshot.agents[index];
  if (!agent) return;
  $('agent-name').textContent = agent.name;
  $('agent-role').textContent = `${agent.role} specialist`;
  $('agent-index').textContent = `${index + 1} / ${snapshot.agents.length}`;
  spriteStyle($('profile-sprite'), agent);
  $('agent-status').textContent = statusNames[agent.status];
  $('agent-status').style.color = colors[agent.status];
  const roleLocations = { main: 'Coordination table', personal: 'Planning desk', researcher: 'Research library', engineer: 'Engineering desk', operator: 'Operations room', shopper: 'Shopping counter', keeper: 'Inventory shelves', travel: 'Travel desk', cook: 'Kitchen', administrator: 'Administration desk', finance: 'Finance desk', family: 'Family garden' };
  const activityLocations = { research: 'Research library', coding: 'Engineering area', operations: 'Operations room', planning: 'Planning area', shopping: 'Shopping counter', inventory: 'Inventory shelves', travel: 'Travel desk', cooking: 'Kitchen', administration: 'Administration area', finance: 'Finance area', coordinating: 'Coordination table' };
  $('agent-location').textContent = agent.status === 'unknown' ? 'Unobserved' : agent.id === 'family' ? 'Family garden'
    : agent.status === 'idle' ? (agent.id === 'main' ? 'Lounge' : roleLocations[agent.id]) : agent.status === 'waiting' ? 'Coordination table' : activityLocations[agent.activity] ?? roleLocations[agent.id];
  $('agent-task').textContent = ACTIVITIES[agent.activity][0];
  $('agent-task').style.setProperty('--agent-color', agent.color);
  $('agent-runs').textContent = agent.status === 'unknown' ? 'Unverified' : String(agent.active_runs);
  $('agent-observed').textContent = age(agent.observed_at);
  $('activity-note').textContent = snapshot.mode === 'demo' ? 'Synthetic preview activity. This is not live agent work.' : 'Activity categories come from native lifecycle hooks. Private task content stays in OpenClaw.';
  $('agent-specialty').textContent = specialties[agent.id];
  $('history-mode').textContent = snapshot.mode === 'demo' ? 'DEMO' : 'THIS SESSION';
  const events = snapshot.events.filter(event => event.agent === selected).slice(0, 7);
  const fragment = document.createDocumentFragment();
  for (const event of events) {
    const row = element('li');
    const date = new Date(event.at);
    const time = element('time', '', date.toLocaleTimeString([], clockFormat));
    time.dateTime = date.toISOString();
    row.append(element('i', `dot ${event.status}`), time, element('span', '', ACTIVITIES[event.activity][0]));
    fragment.append(row);
  }
  $('history').replaceChildren(fragment);
  $('history-empty').hidden = events.length > 0;
}

function select(id) { selected = id; renderRoster(); renderInspector(); updateCharacters(); }

function render() {
  if (!snapshot.agents.some(agent => agent.id === selected)) selected = snapshot.agents[0]?.id;
  const counts = snapshot.agents.reduce((counts, agent) => { counts[agent.status] = (counts[agent.status] ?? 0) + 1; return counts; }, {});
  $('office-summary').textContent = `${counts.working ?? 0} working · ${counts.waiting ?? 0} waiting · ${counts.idle ?? 0} idle${counts.unknown ? ` · ${counts.unknown} unknown` : ''}${counts.error ? ` · ${counts.error} need attention` : ''}`;
  const mode = snapshot.mode;
  $('connection').textContent = mode === 'demo' ? 'Demo feed' : mode === 'offline' ? 'Feed disconnected' : mode === 'unconnected' ? 'Observer not connected' : 'Local activity feed';
  $('connection').className = `connection ${mode}`;
  $('feed-notice').textContent = mode === 'demo' ? 'Demo mode uses your configured OpenClaw roster with synthetic activity. Connect the optional local observer to see real runs.' : mode === 'offline' ? 'The activity feed is unavailable. Agent state is unknown until a fresh observation arrives.' : mode === 'unconnected' ? 'Roster preview only. The native OpenClaw observer has not been connected; all activity is unknown.' : '';
  renderRoster(); renderInspector(); updateCharacters();
}

function validate(data) {
  if (data?.schema_version !== 1 || !['live', 'demo', 'unconnected'].includes(data.mode) || !Array.isArray(data.agents) || data.agents.length > 12
      || !Number.isFinite(data.observed_at) || Math.abs(Date.now() - data.observed_at) > 15000 || !Array.isArray(data.events)) throw new Error('Invalid feed');
  const ids = new Set();
  for (const agent of data.agents) {
    if (!Object.hasOwn(ROLES, agent.id) || ids.has(agent.id) || !Object.hasOwn(statusNames, agent.status)
        || !Object.hasOwn(ACTIVITIES, agent.activity) || typeof agent.name !== 'string' || agent.name.length > 64
        || agent.sprite !== ROLES[agent.id][2] || agent.color !== ROLES[agent.id][1]) throw new Error('Invalid agent');
    ids.add(agent.id);
  }
  data.events = data.events.filter(event => ids.has(event.agent) && Object.hasOwn(ACTIVITIES, event.activity)
    && Object.hasOwn(statusNames, event.status) && Number.isFinite(event.at)).slice(0, 40);
  return data;
}

async function poll() {
  try {
    const response = await fetch('/api/office', { cache: 'no-store', signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error('Feed unavailable');
    snapshot = validate(await response.json());
    lastSuccess = Date.now();
    render();
  } catch {
    if (snapshot) {
      snapshot = { ...snapshot, mode: 'offline', events: [], agents: snapshot.agents.map(agent => ({ ...agent, status: 'unknown', activity: 'unknown', active_runs: 0 })) };
      render();
    } else {
      $('connection').textContent = 'Feed unavailable';
      $('connection').className = 'connection offline';
      $('feed-notice').textContent = 'Unable to load the local agent roster. Check that the office server is running.';
    }
  } finally { setTimeout(poll, 2000); }
}

function animate(now) {
  const delta = Math.min((now - lastFrame) / 1000, .08);
  lastFrame = now;
  for (const character of sprites.values()) {
    const point = character.path[0];
    if (!paused && point) {
      const dx = point[0] - character.position[0], dy = point[1] - character.position[1];
      character.direction = walkDirection(dx, dy, character.direction);
      const distance = Math.hypot(dx, dy);
      const step = delta * 11;
      if (distance <= step) { character.position = point; character.path.shift(); }
      else character.position = [character.position[0] + dx / distance * step, character.position[1] + dy / distance * step];
    }
    character.node.classList.toggle('walking', !paused && character.path.length > 0);
    if (!character.path.length && character.status === 'working') character.direction = ROLE_FACING[character.agentId] ?? character.direction;
    const frame = walkSpriteFrame(character.spriteIndex, character.direction, !paused && character.path.length > 0, now);
    const frameKey = `${frame.asset}:${frame.column}:${frame.row}`;
    if (character.frameKey !== frameKey) {
      character.frameKey = frameKey;
      character.sprite.style.backgroundImage = frame.backgroundImage;
      character.sprite.style.backgroundSize = frame.backgroundSize;
      character.sprite.style.backgroundPosition = frame.backgroundPosition;
      character.sprite.style.width = '64px';
      character.sprite.style.height = '80px';
      character.sprite.style.bottom = '-3px'; // Normalized foot baseline is 154/160.
    }
    character.node.style.left = `${character.position[0]}%`;
    character.node.style.top = `${character.position[1]}%`;
    character.node.style.zIndex = String(Math.round(character.position[1]));
  }
  requestAnimationFrame(animate);
}

function setMotion() {
  $('motion').setAttribute('aria-pressed', String(paused));
  $('motion').querySelector('span').textContent = paused ? 'Resume motion' : 'Pause motion';
  $('scene').classList.toggle('paused', paused);
}
$('motion').addEventListener('click', () => { paused = !paused; setMotion(); if (snapshot) updateCharacters(); });
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', event => { paused = event.matches; setMotion(); if (snapshot) updateCharacters(); });
$('filter').addEventListener('change', event => { filter = event.target.value; if (snapshot) { renderRoster(); updateCharacters(); } });
for (const [id, direction] of [['previous', -1], ['next', 1]]) $(id).addEventListener('click', () => {
  if (!snapshot?.agents.length) return;
  const index = snapshot.agents.findIndex(agent => agent.id === selected);
  select(snapshot.agents[(index + direction + snapshot.agents.length) % snapshot.agents.length].id);
});
setInterval(() => {
  $('office-clock').textContent = new Date().toLocaleTimeString([], clockFormat);
  $('office-clock').title = 'Europe/Copenhagen';
  if (snapshot) renderInspector();
  if (lastSuccess && Date.now() - lastSuccess > 8000 && snapshot.mode !== 'offline') {
    snapshot = { ...snapshot, mode: 'offline', events: [], agents: snapshot.agents.map(agent => ({ ...agent, status: 'unknown', activity: 'unknown', active_runs: 0 })) };
    render();
  }
}, 1000);
setMotion();
for (const room of ROOM_LAYOUT) {
  const label = document.querySelector(`[data-room="${room.id}"]`);
  if (label) { label.style.left = `${room.position[0]}%`; label.style.top = `${room.position[1]}%`; label.textContent = room.label; }
}
poll();
requestAnimationFrame(animate);
