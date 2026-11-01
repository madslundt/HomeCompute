// Installed outside the writable workspace. No configuration supplied by the
// model can expand this policy. The pinned gateway runs this policy hook before
// browser effects and fails closed when a registered policy hook fails.
import { createBrowserRelay } from './browser-relay.mjs';
import { publicHostname } from './public-dns.mjs';
import { registerLlmActivity } from './llm-queue.mjs';
const PROFILE = 'homecompute';
const COMMON = ['action', 'profile', 'target'];
const TAB = ['targetId', 'tabId', 'suggestedTargetId', 'label'];
const ACTIONS = {
  navigate: [...TAB, 'targetUrl', 'url'],
  focus: TAB,
  close: TAB,
  snapshot: [...TAB, 'snapshotFormat', 'refs', 'mode', 'limit', 'maxChars', 'interactive', 'compact', 'depth'],
  screenshot: [...TAB, 'fullPage', 'labels', 'type', 'ref', 'element'],
  act: ['targetId', 'kind', 'ref', 'text', 'submit', 'slowly', 'key', 'values', 'fields',
    'doubleClick', 'button', 'startRef', 'endRef', 'timeoutMs'],
};
const DENIED = { block: true, blockReason: 'Use public HTTP(S) URLs and bounded page actions in the isolated homecompute profile. Private destinations, scripts, files and account imports are blocked.' };
const boundedString = value => typeof value === 'string' && value.length > 0 && value.length <= 256
  && !/[\x00-\x1f\x7f]/.test(value);

function publicUrl(value) {
  if (typeof value !== 'string' || value.length > 4096 || /[\x00-\x20\x7f]/.test(value)) return false;
  const url = new URL(value);
  const host = url.hostname.toLowerCase();
  return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
    && !url.port && publicHostname(host);
}

const ACT_FIELDS = {
  click: ['ref', 'doubleClick', 'button'], type: ['ref', 'text', 'submit', 'slowly'],
  press: ['key'], hover: ['ref'], scrollIntoView: ['ref'],
  drag: ['startRef', 'endRef'], select: ['ref', 'values'], fill: ['fields'],
};
const textValue = (value, max) => typeof value === 'string' && value.length <= max
  && !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value);

function pageAction(params) {
  if (typeof params.kind !== 'string' || !Object.hasOwn(ACT_FIELDS, params.kind)
      || !boundedString(params.targetId)) return false;
  const allowed = new Set([...COMMON, 'targetId', 'kind', 'timeoutMs', ...ACT_FIELDS[params.kind]]);
  if (Object.keys(params).some(key => !allowed.has(key))) return false;
  if (params.timeoutMs !== undefined && (!Number.isInteger(params.timeoutMs)
      || params.timeoutMs < 1 || params.timeoutMs > 15000)) return false;
  for (const key of ['doubleClick', 'submit', 'slowly']) {
    if (params[key] !== undefined && typeof params[key] !== 'boolean') return false;
  }
  if (params.button !== undefined && !['left', 'right', 'middle'].includes(params.button)) return false;
  if (ACT_FIELDS[params.kind].includes('ref') && !boundedString(params.ref)) return false;
  if (params.kind === 'type' && !textValue(params.text, 8000)) return false;
  // Named page keys only: do not expose browser/OS shortcut chords.
  if (params.kind === 'press' && !['Enter', 'Tab', 'Escape', 'Space', 'Backspace', 'Delete',
    'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(params.key)) return false;
  if (params.kind === 'drag' && (!boundedString(params.startRef) || !boundedString(params.endRef))) return false;
  if (params.kind === 'select' && (!Array.isArray(params.values) || params.values.length < 1
      || params.values.length > 40 || params.values.some(value => !textValue(value, 256)))) return false;
  if (params.kind === 'fill') {
    if (!Array.isArray(params.fields) || !params.fields.length || params.fields.length > 40) return false;
    let size = 0;
    for (const field of params.fields) {
      if (!field || typeof field !== 'object' || Array.isArray(field)
          || Object.keys(field).some(key => !['ref', 'type', 'value'].includes(key))
          || !boundedString(field.ref) || !['text', 'checkbox', 'radio'].includes(field.type)) return false;
      if (field.type === 'text' ? !textValue(field.value, 8000) : typeof field.value !== 'boolean') return false;
      size += String(field.value).length;
    }
    if (size > 16000) return false;
  }
  return true;
}

export function browserPolicy(event) {
  try {
    if (!event || event.toolName !== 'browser') return undefined;
    const params = event.params;
    if (!params || typeof params !== 'object' || Array.isArray(params)
        || !Object.hasOwn(ACTIONS, params.action)) return { ...DENIED };
    const allowed = new Set([...COMMON, ...ACTIONS[params.action]]);
    if (Object.keys(params).some(key => !allowed.has(key))) return { ...DENIED };
    if ((params.profile !== undefined && params.profile !== PROFILE)
        || (params.target !== undefined && params.target !== 'host')) return { ...DENIED };
    for (const key of TAB) {
      if (params[key] !== undefined && !boundedString(params[key])) return { ...DENIED };
    }
    if (params.action === 'navigate') {
      const urls = ['url', 'targetUrl'].filter(key => params[key] !== undefined);
      if (urls.length !== 1 || !publicUrl(params[urls[0]])) return { ...DENIED };
    }
    if (params.action === 'act' && !pageAction(params)) return { ...DENIED };
    for (const key of ['fullPage', 'labels', 'interactive', 'compact']) {
      if (params[key] !== undefined && typeof params[key] !== 'boolean') return { ...DENIED };
    }
    for (const [key, max] of [['limit', 1000], ['maxChars', 32768], ['depth', 50]]) {
      if (params[key] !== undefined && (!Number.isInteger(params[key]) || params[key] < 0 || params[key] > max)) return { ...DENIED };
    }
    for (const [key, choices] of [['snapshotFormat', ['ai', 'aria']], ['refs', ['role', 'aria']],
      ['mode', ['efficient']], ['type', ['png', 'jpeg']]]) {
      if (params[key] !== undefined && !choices.includes(params[key])) return { ...DENIED };
    }
    for (const key of ['ref', 'element']) {
      if (params[key] !== undefined && !boundedString(params[key])) return { ...DENIED };
    }
    // Force the gateway's empty isolated profile and prevent sandbox/node/user
    // browser selection even when the model omits routing arguments.
    return { params: { ...params, profile: PROFILE, target: 'host' } };
  } catch {
    return { ...DENIED };
  }
}

const SPECIALISTS = new Set(['personal', 'researcher', 'engineer', 'operator', 'shopper', 'keeper', 'travel',
  'cook', 'administrator', 'finance', 'family']);
const COORDINATOR_DENIED = { block: true, blockReason: 'Coordinator domain execution is blocked. Delegate to the appropriate existing specialist via sessions_spawn with mode="run", context="isolated", then await its result with sessions_yield.' };

export function coordinatorPolicy(event, ctx) {
  try {
    const name = typeof event?.toolName === 'string' ? event.toolName.trim().toLowerCase() : '';
    if (name !== 'browser' && !name.startsWith('homecompute_')) return undefined;
    // Identity comes from the native execution context, never tool parameters.
    if (SPECIALISTS.has(ctx?.agentId) && typeof ctx?.sessionKey === 'string'
        && ctx.sessionKey.startsWith(`agent:${ctx.agentId}:`)) return undefined;
    return { ...COORDINATOR_DENIED };
  } catch {
    return { ...COORDINATOR_DENIED };
  }
}

export default {
  id: 'homecompute-safety',
  name: 'HomeCompute browser safety',
  register(api) {
    // Validate after ordinary rewriting hooks. A blocking result is sticky in
    // the pinned native runner and cannot be undone by a subsequent hook.
    api.on('before_tool_call', browserPolicy, { priority: -1000000 });
    api.on('before_tool_call', coordinatorPolicy, { priority: -1000000 });
    api.registerService(createBrowserRelay());
    registerLlmActivity(api);
  },
};
