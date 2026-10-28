// Coordinates are percentages of office.png, measured at a character's feet.
// The illustration is a metaphor, never a physical or security location feed.
export const ROOM_LAYOUT = [
  { id: 'operations', label: 'Operations', position: [13, 23.2] },
  { id: 'administration', label: 'Administration', position: [40, 30.2] },
  { id: 'planning', label: 'Planning', position: [56.5, 30.2] },
  { id: 'research', label: 'Research', position: [88, 33.3] },
  { id: 'shopping', label: 'Shopping', position: [13, 36.7] },
  { id: 'lounge', label: 'Lounge', position: [15, 68.8] },
  { id: 'coordination', label: 'Coordination', position: [47, 35.4] },
  { id: 'inventory', label: 'Inventory', position: [65, 50.5] },
  { id: 'finance', label: 'Finance', position: [62, 66.7] },
  { id: 'engineering', label: 'Engineering', position: [39, 81.9] },
  { id: 'travel', label: 'Travel', position: [56, 81.9] },
  { id: 'kitchen', label: 'Kitchen', position: [87, 61] },
  { id: 'family', label: 'Family garden', position: [87, 86.5] },
  { id: 'arrival', label: 'Arrival', position: [13, 93] },
];

export const ROLE_DESTINATIONS = {
  main: [46.5, 63.2], personal: [56.3, 29.3], researcher: [87.5, 26.8],
  engineer: [39, 83.3], operator: [14, 24.3], shopper: [21.5, 46],
  keeper: [64.5, 51.3], travel: [55.5, 83.3], cook: [89.5, 60.6],
  administrator: [39.5, 29.3], finance: [55, 62.5], family: [83.5, 75.5],
};

export const ROLE_FACING = {
  main: 'north', personal: 'north', researcher: 'north', engineer: 'north',
  operator: 'north', shopper: 'west', keeper: 'north', travel: 'north',
  cook: 'north', administrator: 'north', finance: 'east', family: 'south',
};

// Art-derived solid furniture footprints [left, top, right, bottom]. Rugs and
// flooring are deliberately excluded. Keep these in step with the raster art.
export const FURNITURE_BOUNDS = {
  meetingTableAndChairs: [35.5, 38, 57.4, 58.3],
  administrationDesk: [34.5, 13.5, 47, 25.7],
  planningDesk: [50, 12.8, 62.2, 25.8],
  engineeringDesk: [32.5, 65.3, 46.9, 79.7],
  travelDesk: [49, 65.4, 63.8, 79.7],
  financeDesk: [57, 54.5, 68, 65.1],
  inventoryShelves: [60.8, 35, 69.4, 49],
  inventoryPlant: [66.3, 50.2, 69.1, 55.4],
  sofa: [6.4, 48.5, 22.5, 58.5],
  coffeeTable: [8.4, 58.5, 18.3, 64],
  loungePlant: [18.6, 64.5, 22.5, 71.7],
  shoppingCounter: [7.3, 38, 19.1, 48.5],
  serverRacks: [4.6, 6, 16.6, 18.5],
  kitchenIsland: [78.7, 44.5, 92.6, 58.7],
  libraryCabinet: [73.3, 23.4, 79.5, 31.5],
  readingChair: [83.5, 15.8, 90.7, 21.5],
  picnicBlanket: [77.8, 77.5, 90, 83.7],
  gardenTree: [91.5, 75.5, 96.5, 80.5],
  lobbyBookcase: [20.2, 77.5, 24.5, 87.6],
  lobbyMirror: [8, 76.5, 19.2, 86],
  southWestPlant: [28.4, 84, 32.2, 90.2],
};

const nodes = new Map();
const edges = [];
const key = point => point.join(',');
const equal = (a, b) => Math.abs(a[0] - b[0]) < 1e-7 && Math.abs(a[1] - b[1]) < 1e-7;
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function line(...points) {
  for (const point of points) nodes.set(key(point), point);
  for (let i = 1; i < points.length; i++) edges.push([key(points[i - 1]), key(points[i])]);
}

// Private office: enter actual door gaps and pass below or beside furniture.
line([30, 29.5], [30, 31], [30, 46], [30, 63.2], [30, 64.5], [30, 83.3]);
line([30, 31], [39.5, 31], [56.3, 31], [58.7, 31], [58.7, 51.3]);
line([39.5, 31], ROLE_DESTINATIONS.administrator);
line([56.3, 31], ROLE_DESTINATIONS.personal);
line([30, 29.5], [20, 29.5], [20, 32.5], [13, 32.5], [13, 26.5]);
line([13, 26.5], [14, 26.5], ROLE_DESTINATIONS.operator);
line([30, 46], [21.5, 46]);
line([30, 63.2], [22.2, 63.2]);
line([30, 63.2], ROLE_DESTINATIONS.main, [55, 63.2], ROLE_DESTINATIONS.finance);
line([30, 83.3], ROLE_DESTINATIONS.engineer, ROLE_DESTINATIONS.travel, [60, 83.3]);
line([30, 83.3], [27, 83.3], [27, 89], [20.5, 89], [16, 89], [11.5, 89], [7, 89]);

// The stock-shelf approach bends around its potted plant before the kitchen.
line([58.7, 51.3], ROLE_DESTINATIONS.keeper, [64.5, 49.8], [69.6, 49.8], [69.6, 53.8], [74.3, 53.8]);
line([74.3, 53.8], [75.5, 53.8], [75.5, 40], [75.5, 36], [75.5, 32.5], [82, 32.5]);
line([75.5, 53.8], [75.5, 60.6], [83.5, 60.6], ROLE_DESTINATIONS.cook);
line([82, 32.5], [87.5, 32.5], [87.5, 26.8]);

// Family has its own visible garden gateway and independent navigation graph.
// Shared work never sends Gaia through private workspaces, including on errors.
line([83.5, 70.6], [83.5, 74], ROLE_DESTINATIONS.family);
line([83.5, 74], [90.5, 74], [90.5, 84.5]);

function rowPool(xs, ys, attach) {
  const result = [];
  for (const y of ys) for (const x of xs) {
    const point = [x, y];
    line(...attach(point), point);
    result.push(point);
  }
  return result;
}

const arrival = rowPool([7, 11.5, 16, 20.5], [89, 91.5, 93.5], ([x]) => [[x, 89]]);
const lounge = [
  ...rowPool([20.8, 22.3, 23.8], [45, 47.8], ([x]) => [[21.5, 46], [x, 46]]),
  ...rowPool([20, 21.5, 23], [60.5, 63.2], ([x]) => [[22.2, 63.2], [x, 63.2]]),
];
const waiting = rowPool([33, 36.5, 40, 43.5, 48.5, 52], [61, 63.2], ([x]) => [[30, 63.2], [x, 63.2]]);
const research = rowPool([82, 85.5, 89, 92.5], [24, 28, 31], ([x]) => [[82, 32.5], [x, 32.5]]);
const operations = rowPool([7.5, 10.5, 14, 18], [21.5, 23, 26.5], ([x]) => [[13, 26.5], [x, 26.5]]);
const coding = rowPool([34, 37, 40, 43, 46, 48], [86.5, 89.5], ([x]) => [[39, 83.3], [x, 83.3]]);

// Other specialist actions stay near their corresponding counter/workstation.
// The owner gets its exact workstation; visitors use separate floor slots.
const planning = rowPool([50, 52, 54, 58], [29, 30.2, 31.5], ([x]) => [[56.3, 31], [x, 31]]);
const administration = rowPool([34, 37, 42, 45], [29, 30.2, 31.5], ([x]) => [[39.5, 31], [x, 31]]);
const shopping = rowPool([21, 22.5, 24], [44.5, 46, 47.5, 48.5], ([x]) => [[21.5, 46], [x, 46]]);
const inventory = rowPool([60, 61.5, 63, 64.5], [50.2, 51.3, 52.3], ([x]) => [[58.7, 51.3], [x, 51.3]]);
const travel = rowPool([50, 52.5, 55, 57.5], [83.3, 85, 86.5], ([x]) => [[55.5, 83.3], [x, 83.3]]);
const cooking = rowPool([73, 75, 76.3], [42, 45, 48, 51], ([, y]) => [[75.5, 53.8], [75.5, y]]);
const finance = rowPool([50, 52, 54, 55.5], [60, 61.5, 63.2], ([x]) => [[55, 63.2], [x, 63.2]]);
const roleOrder = Object.keys(ROLE_DESTINATIONS);
// Separate rest positions on the existing corridor graph. Keep these clear of
// every other role's home position as well as other resting characters.
const idleDestinations = {
  main: [22.2, 63.2], personal: [58, 31.5], researcher: [85.5, 31],
  engineer: [40, 89.5], operator: [18, 26.5], shopper: [23.8, 47.8],
  keeper: [58.7, 51.3], travel: [55, 86.5], cook: [76.3, 51],
  administrator: [42, 31.5], finance: [55.5, 63.2],
};
const activityDestinations = {
  research: ['researcher', research], coding: ['engineer', coding],
  operations: ['operator', operations], planning: ['personal', planning],
  shopping: ['shopper', shopping], inventory: ['keeper', inventory],
  travel: ['travel', travel], cooking: ['cook', cooking],
  administration: ['administrator', administration], finance: ['finance', finance],
};

export function resolveDestination(agent, index = 0) {
  if (agent.id === 'family') return [...(agent.status === 'unknown' ? [83.5, 70.6]
    : agent.status === 'idle' ? [90.5, 84.5] : agent.status === 'waiting' ? [83.5, 74] : ROLE_DESTINATIONS.family)];
  const ordinal = roleOrder.indexOf(agent.id);
  const slot = ordinal < 0 ? Math.max(0, Math.trunc(index)) : ordinal;
  if (agent.status === 'unknown') return [...(ROLE_DESTINATIONS[agent.id] ?? arrival[slot % arrival.length])];
  if (agent.status === 'idle') return [...(idleDestinations[agent.id] ?? lounge[slot % lounge.length])];
  if (agent.status === 'waiting' || agent.activity === 'waiting') return [...waiting[slot % waiting.length]];
  if (agent.activity === 'coordinating') return [...(agent.id === 'main' ? ROLE_DESTINATIONS.main : waiting[slot % waiting.length])];
  const destination = activityDestinations[agent.activity];
  if (destination && agent.id !== destination[0]) return [...destination[1][slot % destination[1].length]];
  return [...(ROLE_DESTINATIONS[agent.id] ?? arrival[slot % arrival.length])];
}

// Split every edge at on-segment vertices. Pool rows and mid-corridor entries
// therefore share real junctions instead of geometrically crossing disconnected.
function project(point, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) : 0;
  return [a[0] + dx * t, a[1] + dy * t];
}
const graph = new Map([...nodes.keys()].map(id => [id, new Map()]));
for (const [aId, bId] of edges) {
  const a = nodes.get(aId), b = nodes.get(bId);
  const onLine = [...nodes.entries()].filter(([, point]) => distance(point, project(point, a, b)) < 1e-7)
    .sort(([, p], [, q]) => distance(a, p) - distance(a, q));
  for (let i = 1; i < onLine.length; i++) {
    const [u, p] = onLine[i - 1], [v, q] = onLine[i], weight = distance(p, q);
    if (!weight) continue;
    graph.get(u).set(v, weight); graph.get(v).set(u, weight);
  }
}
export const WALKWAY_SEGMENTS = [...graph].flatMap(([id, neighbors]) => [...neighbors.keys()]
  .filter(other => id < other).map(other => [nodes.get(id), nodes.get(other)]));

export function isWalkable(point) {
  if (!Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite)) return false;
  if (Object.values(FURNITURE_BOUNDS).some(([left, top, right, bottom]) => point[0] > left && point[0] < right && point[1] > top && point[1] < bottom)) return false;
  return WALKWAY_SEGMENTS.some(([a, b]) => distance(point, project(point, a, b)) < 0.05);
}

export function findPath(from, to) {
  if (equal(from, to)) return [];
  if (!isWalkable(from) || !isWalkable(to)) return [];
  const local = new Map([...graph].map(([id, neighbors]) => [id, new Map(neighbors)]));
  const positions = new Map(nodes);
  for (const [id, point] of [['@from', from], ['@to', to]]) {
    local.set(id, new Map()); positions.set(id, point);
    for (const [a, b] of WALKWAY_SEGMENTS) {
      if (distance(point, project(point, a, b)) >= 0.05) continue;
      for (const endpoint of [a, b]) {
        local.get(id).set(key(endpoint), distance(point, endpoint));
        local.get(key(endpoint)).set(id, distance(point, endpoint));
      }
    }
  }
  // When interrupted midway along one corridor, use its remaining segment.
  if (WALKWAY_SEGMENTS.some(([a, b]) => distance(from, project(from, a, b)) < 0.05 && distance(to, project(to, a, b)) < 0.05)) {
    local.get('@from').set('@to', distance(from, to));
  }
  const costs = new Map([['@from', 0]]), previous = new Map(), open = new Set(local.keys());
  while (open.size) {
    let current, cost = Infinity;
    for (const id of open) if ((costs.get(id) ?? Infinity) < cost) { current = id; cost = costs.get(id); }
    if (current === undefined || current === '@to') break;
    open.delete(current);
    for (const [next, weight] of local.get(current)) if (cost + weight < (costs.get(next) ?? Infinity)) {
      costs.set(next, cost + weight); previous.set(next, current);
    }
  }
  if (!previous.has('@to')) return [];
  const path = [];
  for (let current = '@to'; current !== '@from'; current = previous.get(current)) path.unshift([...positions.get(current)]);
  return path.filter((point, index) => !equal(point, index ? path[index - 1] : from));
}
