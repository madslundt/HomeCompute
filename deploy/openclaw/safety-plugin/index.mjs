// Installed outside the writable workspace. No configuration supplied by the
// model can expand this policy. The pinned gateway runs this policy hook before
// browser effects and fails closed when a registered policy hook fails.
import { createBrowserRelay } from './browser-relay.mjs';
import { RESEARCH_HOSTS } from './public-dns.mjs';
const RESEARCH = new Set(RESEARCH_HOSTS);
const PROFILE = 'homecompute';
const COMMON = ['action', 'profile', 'target'];
const TAB = ['targetId', 'tabId', 'suggestedTargetId', 'label'];
const ACTIONS = {
  navigate: [...TAB, 'targetUrl', 'url'],
  focus: TAB,
  close: TAB,
  snapshot: [...TAB, 'snapshotFormat', 'refs', 'mode', 'limit', 'maxChars', 'interactive', 'compact', 'depth'],
  screenshot: [...TAB, 'fullPage', 'labels', 'type', 'ref', 'element'],
};
const DENIED = { block: true, blockReason: 'Browser interaction requires a separately reviewed operator path. This browser permits public research only.' };
const boundedString = value => typeof value === 'string' && value.length > 0 && value.length <= 256
  && !/[\x00-\x1f\x7f]/.test(value);

function publicUrl(value) {
  if (typeof value !== 'string' || value.length > 4096 || /[\x00-\x20\x7f]/.test(value)) return false;
  const url = new URL(value);
  const host = url.hostname.toLowerCase();
  return url.protocol === 'https:' && !url.username && !url.password
    && RESEARCH.has(host)
    && (!url.port || url.port === '443')
    && /^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(host)
    && !/^[\d.]+$/.test(host)
    && !/(?:^|\.)(?:localhost|local|internal|lan|home|arpa|test|invalid|example)$/.test(host);
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

export default {
  id: 'homecompute-safety',
  name: 'HomeCompute browser safety',
  register(api) {
    // Validate after ordinary rewriting hooks. A blocking result is sticky in
    // the pinned native runner and cannot be undone by a subsequent hook.
    api.on('before_tool_call', browserPolicy, { priority: -1000000 });
    api.registerService(createBrowserRelay());
  },
};
