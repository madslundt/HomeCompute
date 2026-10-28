// Original role-specific animation art: three characters per sheet, four
// directions, two walk poses and a planted standing pose per direction.
export const WALK_SHEETS = Object.freeze([
  '/assets/walk-coordination.png',
  '/assets/walk-operations.png',
  '/assets/walk-household.png',
  '/assets/walk-planning.png',
]);

const DIRECTIONS = Object.freeze({ south: 0, west: 1, east: 2, north: 3 });
const FRAME_MS = 150;
const COLUMNS = 12;
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
  const index = Number.isInteger(spriteIndex) && spriteIndex >= 0 && spriteIndex < 12 ? spriteIndex : 0;
  const facing = Object.hasOwn(DIRECTIONS, direction) ? DIRECTIONS[direction] : DIRECTIONS.south;
  const phase = Math.floor((Math.max(0, Number.isFinite(timeMs) ? timeMs : 0) + index * 53) / FRAME_MS) % 2;
  const column = moving ? facing * 2 + phase : 8 + facing;
  const row = index % ROWS;
  const asset = WALK_SHEETS[Math.floor(index / ROWS)];
  return {
    asset,
    columns: COLUMNS,
    rows: ROWS,
    column,
    row,
    frameWidth: 128,
    frameHeight: 160,
    backgroundImage: `url("${asset}")`,
    backgroundSize: `${COLUMNS * 100}% ${ROWS * 100}%`,
    backgroundPosition: `${column * 100 / (COLUMNS - 1)}% ${row * 100 / (ROWS - 1)}%`,
    aspectRatio: '4 / 5',
  };
}
