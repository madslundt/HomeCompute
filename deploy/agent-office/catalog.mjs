// Role appearance metadata; scene-layout.mjs owns all spatial coordinates.
export const ROLES = {
  main: ['Coordination', '#ecc477', 0],
  personal: ['Personal', '#8bc8ec', 1],
  researcher: ['Research', '#cb91e9', 2],
  engineer: ['Engineering', '#ae8bfa', 3],
  operator: ['Operations', '#65cbbd', 4],
  shopper: ['Shopping', '#e7a26b', 5],
  keeper: ['Inventory', '#abbc79', 6],
  travel: ['Travel', '#75b6eb', 7],
  cook: ['Meals', '#8fcb8d', 8],
  administrator: ['Administration', '#b7c0d8', 9],
  finance: ['Finance', '#e0c56c', 10],
  family: ['Family', '#d6a7c5', 11],
};

export const ACTIVITIES = {
  thinking: ['Thinking through a request', 'Workspace'],
  research: ['Researching public sources', 'Research library'],
  coding: ['Reviewing repository work', 'Engineering desk'],
  operations: ['Checking infrastructure', 'Operations room'],
  planning: ['Organizing plans and tasks', 'Planning desk'],
  shopping: ['Checking shopping and stock', 'Household desk'],
  inventory: ['Checking household supplies', 'Inventory shelves'],
  travel: ['Exploring places and routes', 'Travel desk'],
  cooking: ['Planning meals', 'Kitchen'],
  administration: ['Reviewing administration', 'Administration desk'],
  finance: ['Reviewing a budget', 'Finance desk'],
  family: ['Coordinating shared tasks', 'Family garden'],
  coordinating: ['Coordinating a handoff', 'Coordination table'],
  workspace: ['Working in own workspace', 'Workspace'],
  waiting: ['Waiting for a specialist', 'Coordination table'],
  idle: ['Ready for the next request', 'Break area'],
  error: ['Run ended with an error', 'Workspace'],
  unknown: ['No fresh activity observation', 'Unobserved'],
};

export function projectRoster(team) {
  return Object.entries(team.agents).filter(([id]) => Object.hasOwn(ROLES, id))
    .map(([id, agent]) => ({ id,
      name: typeof agent?.name === 'string' && /^[^\x00-\x1f\x7f]{1,64}$/.test(agent.name) ? agent.name : ROLES[id][0],
      role: ROLES[id][0], color: ROLES[id][1], sprite: ROLES[id][2] }));
}

export function toolActivity(name) {
  // Match trusted, fixed tool identifiers only. Never inspect parameters/results
  // or publish arbitrary tool names (which may include private identifiers).
  if (name === 'sessions_yield') return 'waiting';
  if (name === 'sessions_spawn') return 'coordinating';
  if (['web_search', 'web_fetch', 'browser', 'homecompute__homecompute_web_search'].includes(name)) return 'research';
  if (['read', 'write', 'edit', 'memory_search', 'memory_get'].includes(name)) return 'workspace';
  if (['exec', 'apply_patch', 'homecompute_task_submit', 'homecompute_task_status'].includes(name)) return 'coding';
  if (['homecompute_machine_status', 'homecompute_maintenance_status', 'homecompute_home_assistant_status'].includes(name)) return 'operations';
  const native = typeof name === 'string' && name.startsWith('homecompute__homecompute_') ? name.slice('homecompute__homecompute_'.length) : '';
  for (const [activity, names] of Object.entries({
    coding: ['task_submit', 'task_status', 'task_cancel'],
    operations: ['systems', 'system_read', 'ha_propose', 'ha_status', 'ha_cancel', 'maintenance_prepare', 'maintenance_preparation', 'maintenance_list', 'maintenance_status', 'maintenance_run', 'operations_search', 'automation_status'],
    research: ['web_search', 'research_search'],
    planning: ['school_discover', 'school_calendar', 'school_posts', 'school_threads', 'school_thread', 'school_weekly_letter', 'school_search', 'calendar_list', 'calendar_events', 'calendar_availability', 'calendar_create', 'calendar_update', 'calendar_request_status', 'household_task_list', 'household_task_create', 'household_task_update', 'household_task_complete', 'household_task_cancel'],
    shopping: ['shopping_search', 'shopping_stores', 'shopping_offers', 'shopping_list', 'shopping_add', 'shopping_update', 'shopping_complete', 'shopping_remove', 'discounts_read', 'discount_watch', 'discount_unwatch'],
    inventory: ['storage_list', 'storage_add', 'storage_update', 'storage_remove', 'storage_adjust'],
    travel: ['places_search', 'places_route', 'places_weather', 'hotel_search'],
    family: ['family_task_list', 'family_task_create', 'family_task_update', 'family_task_complete', 'family_task_cancel'],
  })) if (names.includes(native)) return activity;
  return 'thinking';
}
