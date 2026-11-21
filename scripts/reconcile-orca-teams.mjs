#!/usr/bin/env node
// Invoke v1.4.268's owner RPCs through the authenticated browser shipped by Orca.
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

export async function reconcile(rpc, projects, agent, model, applyChanges) {
  if (applyChanges) {
    await rpc('settings.update', { agentDefaultSurface: 'chat', nativeChatPermissionMode: 'ask' });
    await rpc('settings.mutateAgentLaunch', { type: 'permission', choice: 'ask' });
  }
  const { repos } = await rpc('repo.list', {});
  const { bots } = await rpc('bots.list');
  const { groups } = await rpc('bots.groups.list');
  const summary = [];
  for (const project of projects) {
    const checkout = '/data/projects/' + project.id;
    const expectedRemote = 'https://github.com/' + project.repository + '.git';
    const expectedBase = 'origin/' + project.base;
    let repo = repos.find((row) => row.path === checkout);
    let bot = bots.find((row) => row.name === project.po);
    let group = groups.find((row) => row.name === project.name);
    const groupHasOnlyPO = () => group.members.length === 1 && group.members[0].botId === bot?.id;
    if (repo && repo.gitRemoteIdentity?.canonicalKey?.toLowerCase() !==
        ('github.com/' + project.repository).toLowerCase()) {
      throw new Error('Repository identity differs or is still loading at the managed checkout path.');
    }
    if (group && !groupHasOnlyPO()) {
      throw new Error('Existing project group has different members. Review before reconciling.');
    }
    if (applyChanges) {
      if (!repo) {
        ({ repo } = await rpc('repo.clone', {
          url: expectedRemote, destination: '/data/projects', folderName: project.id,
        }));
      }
      if (repo.worktreeBaseRef !== expectedBase) {
        ({ repo } = await rpc('repo.setBaseRef', { repo: 'id:' + repo.id, ref: expectedBase }));
      }
      if (!bot) {
        ({ bot } = await rpc('bots.create', {
          name: project.po, agent, launch: { model }, permissionMode: 'ask',
          seed: {
            purpose: 'Own the roadmap, backlog and delivery for ' + project.name + '.',
            watches: 'Only ' + project.repository + '. Use the registered project checkout.',
            does: project.context,
            reports: 'Follow /etc/homecompute-orca/instructions/product-owner.md. Use the shared role instructions in /etc/homecompute-orca/teams.json.',
          },
        }));
      }
      if (bot.agent !== agent || bot.launch?.model !== model || bot.permissionMode !== 'ask' ||
          JSON.stringify(bot.skillProjects ?? []) !== JSON.stringify([repo.id])) {
        ({ bot } = await rpc('bots.update', {
          botId: bot.id,
          updates: { agent, launch: { model }, permissionMode: 'ask', skillProjects: [repo.id] },
        }));
      }
      if (!group) {
        ({ group } = await rpc('bots.groups.create', { name: project.name, botIds: [bot.id] }));
      }
      if (group.replyMode.kind !== 'bot' || group.replyMode.botId !== bot.id) {
        ({ group } = await rpc('bots.groups.update', {
          groupId: group.id, revision: group.revision, replyMode: { kind: 'bot', botId: bot.id },
        }));
      }
    }
    const ready = repo?.worktreeBaseRef === expectedBase && bot?.agent === agent &&
      bot.launch?.model === model && bot.permissionMode === 'ask' &&
      JSON.stringify(bot.skillProjects ?? []) === JSON.stringify([repo.id]) &&
      group && groupHasOnlyPO() && group.replyMode.kind === 'bot' && group.replyMode.botId === bot.id;
    if (applyChanges && !ready) throw new Error('Postcheck failed for ' + project.id);
    summary.push({ project: project.id, bot: bot?.name, agent: bot?.agent, model: bot?.launch?.model,
      group: group?.name, ready: Boolean(ready) });
  }
  return summary;
}

async function main() {
  const require = createRequire(import.meta.url);
  const teams = JSON.parse(await readFile(new URL('../deploy/orca/teams.json', import.meta.url)));
  const { chromium } = require(process.env.ORCA_PLAYWRIGHT_MODULE || 'playwright');
  const origin = 'https://orca.home.arpa';
  const apply = process.argv[2] === '--apply';
  if ((!apply && process.argv.length !== 2) || process.argv.length > 3 || process.stdin.isTTY) {
    console.error('Usage: feed an Orca browser pairing URL on stdin. Add --apply to reconcile.');
    process.exitCode = 2;
    return;
  }

  let stage = 'reading a private pairing link';
  let browser;
  try {
    const parts = [];
    let length = 0;
    for await (const part of process.stdin) {
      length += part.length;
      if (length > 8192) throw new Error('Input too long');
      parts.push(part);
    }
    const link = new URL(Buffer.concat(parts).toString('utf8').trim());
    if (link.origin !== origin || !link.hash || link.username || link.password || link.search ||
        !['/', '/web-index.html'].includes(link.pathname)) throw new Error('Unexpected pairing URL');
    stage = 'launching a trusted Chromium profile';
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage();
    stage = 'pairing and checking the runtime';
    await page.goto(link.href);
    await page.waitForFunction(async () => {
      try {
        return !!(await window.api.runtime.call({ method: 'status.get' })).result?.pairedDeviceId;
      } catch {
        return false;
      }
    }, null, { timeout: 25_000 });
    const rpc = (method, params) => {
      stage = method;
      return page.evaluate(async (request) => {
        const reply = await window.api.runtime.call(request);
        if (!reply.ok) throw new Error('Orca rejected the request.');
        return reply.result;
      }, { method, params });
    };
    console.log(JSON.stringify(await reconcile(rpc, teams.projects, teams.default_agent,
      teams.default_model, apply), null, 2));
  } catch {
    console.error('Orca team setup stopped while ' + stage + '. Re-run in preview mode or inspect the paired UI.');
    process.exitCode = 1;
  } finally {
    await browser?.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
