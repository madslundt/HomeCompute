// Three distinct robot designs per sheet, with shared ground anchors across
// movement, folded-tool standing poses, and separate physical work cycles.
export const WALK_SHEETS = Object.freeze([
  '/assets/walk-coordination.png',
  '/assets/walk-operations.png',
  '/assets/walk-household.png',
  '/assets/walk-planning.png',
]);
export const WORK_SHEETS = Object.freeze([
  '/assets/work-coordination.png',
  '/assets/work-operations.png',
  '/assets/work-household.png',
  '/assets/work-planning.png',
]);

export const ROBOT_PROFILES = Object.freeze([
  { kind: 'Modular coordinator', speed: 9, height: 128 },
  { kind: 'Calendar rover', speed: 10, height: 108 },
  { kind: 'Floating research companion', speed: 9, height: 112 },
  { kind: 'Humanoid maker', speed: 12, height: 128 },
  { kind: 'Rack technician', speed: 7, height: 132 },
  { kind: 'Single-wheel scout', speed: 11, height: 126 },
  { kind: 'Humanoid stock keeper', speed: 8, height: 120 },
  { kind: 'Navigation drone', speed: 13, height: 92 },
  { kind: 'Soft kitchen companion', speed: 8, height: 86 },
  { kind: 'Humanoid document assistant', speed: 9, height: 136 },
  { kind: 'Humanoid analyst', speed: 8, height: 122 },
  { kind: 'Humanoid garden companion', speed: 9, height: 105 },
]);

const DIRECTIONS = Object.freeze({ south: 0, west: 1, east: 2, north: 3 });
const FRAME_MS = 150;
const ROWS = 3;

/** Keep the last facing when stationary, and choose a cardinal walking view. */
export function walkDirection(dx, dy, previous = 'south') {
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || Math.hypot(dx, dy) < 0.0001) {
    return Object.hasOwn(DIRECTIONS, previous) ? previous : 'south';
  }
  return Math.abs(dx) > Math.abs(dy) ? (dx < 0 ? 'west' : 'east') : (dy < 0 ? 'north' : 'south');
}

/** Return the atlas cell and CSS background properties for one character. */
export function walkSpriteFrame(spriteIndex, direction = 'south', moving = false, timeMs = 0) {
  return robotSpriteFrame(spriteIndex, { direction, traveling: moving, timeMs });
}

/** Only a stationary, observed working agent uses its deployed-tool artwork.
 * Pass a frozen time for pause/reduced motion: the work pose remains visible. */
export function robotSpriteFrame(spriteIndex, { direction = 'south', traveling = false, status = 'unknown', timeMs = 0 } = {}) {
  const index = Number.isInteger(spriteIndex) && spriteIndex >= 0 && spriteIndex < 12 ? spriteIndex : 0;
  const facing = Object.hasOwn(DIRECTIONS, direction) ? DIRECTIONS[direction] : DIRECTIONS.south;
  const working = status === 'working' && !traveling;
  const columns = working ? 8 : 12;
  const phase = Math.floor((Math.max(0, Number.isFinite(timeMs) ? timeMs : 0) + index * 53) / (working ? 450 : FRAME_MS)) % 2;
  const column = traveling || working ? facing * 2 + phase : 8 + facing;
  const row = index % ROWS;
  const asset = (working ? WORK_SHEETS : WALK_SHEETS)[Math.floor(index / ROWS)];
  return {
    asset,
    columns,
    rows: ROWS,
    column,
    row,
    frameWidth: 128,
    frameHeight: 160,
    backgroundImage: `url("${asset}")`,
    backgroundSize: `${columns * 100}% ${ROWS * 100}%`,
    backgroundPosition: `${column * 100 / (columns - 1)}% ${row * 100 / (ROWS - 1)}%`,
    aspectRatio: '4 / 5',
  };
}
