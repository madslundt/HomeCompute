// Fixed visual vocabulary only: no task text or additional activity inference.
const STATES = Object.freeze({
  working: ['▥', 'Working'], waiting: ['Ⅱ', 'Waiting'], idle: ['–', 'Idle'],
  unknown: ['?', 'Unknown'], error: ['!', 'Needs attention'],
});
export function characterPresence(status) {
  const [symbol, label] = STATES[status] ?? STATES.unknown;
  return { symbol, label };
}
