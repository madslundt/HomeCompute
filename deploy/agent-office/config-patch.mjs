// Pure additive native-config patch builder. Its output uses OpenClaw's
// documented config set --batch-json format, preserving other plugin policy.
import { isDeepStrictEqual } from 'node:util';
export const PLUGIN_ID = 'homecompute-agent-office';
export function configPatch(before, installedPath, port = 18802) {
  if (typeof installedPath !== 'string' || !installedPath.startsWith('/sandbox/.openclaw/homecompute-agent-office/')
      || !/^\/sandbox\/\.openclaw\/homecompute-agent-office\/[a-f0-9]{64}$/.test(installedPath)) throw new Error('Immutable sandbox bundle path required');
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid loopback port');
  const plugins = before.plugins ?? {};
  if (plugins.enabled === false || plugins.deny?.includes(PLUGIN_ID)) throw new Error('Plugin policy denies office activation');
  const entry = { enabled: true, hooks: { allowConversationAccess: true }, config: { port, publicOrigin: 'https://agents.home.arpa' } };
  if (plugins.entries?.[PLUGIN_ID] && !isDeepStrictEqual(plugins.entries[PLUGIN_ID], entry)) {
    throw new Error('Existing office configuration requires reconciliation');
  }
  const paths = plugins.load?.paths ?? [];
  if (!Array.isArray(paths) || paths.some(path => typeof path !== 'string') || (plugins.allow !== undefined && !Array.isArray(plugins.allow))) throw new Error('Invalid native plugin policy');
  const retained = paths.filter(path => !path.startsWith('/sandbox/.openclaw/homecompute-agent-office/'));
  // Do not create an allowlist on an unrestricted installation: doing so could
  // disable unrelated plugins. Extend an existing explicit allowlist only.
  return [
    ...(plugins.allow ? [{ path: 'plugins.allow', value: [...new Set([...plugins.allow, PLUGIN_ID])] }] : []),
    { path: 'plugins.load.paths', value: [...retained, installedPath] },
    { path: `plugins.entries.${PLUGIN_ID}`, value: entry },
  ];
}
