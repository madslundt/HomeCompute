# Proactive personal assistant: capability check

Date: 2026-11-06. Scope: source and official documentation review for the
personal-assistant plan. No live access, deployment, credentials, or personal
messages were used. Capability in source is not proof of installed behavior.

## Source baseline

The working checkout is behind the published branch. This review therefore
also inspected `origin/master` at
`0774a4a72f15cf9f162a7999720ce555ea42c627` with `git show`, without changing
the checkout or its existing edits. At that revision:

- `config/hermes-release.json` selects Hermes 0.23.0 at
  `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`, NemoClaw 0.0.141 and
  OpenShell 0.0.116.
- `config/hermes-omh.json` selects Oh My Hermes 4.0.6 at
  `8e9263c14c34834b203135409b49c89831878501`, local `automation-moe`, medium
  reasoning, concurrency one, and OMH memory off.
- `deploy/hermes/README.md` documents the owner Mattermost bot, existing MCP
  bridge, managed overlay and guarded add-on lifecycle. Its old local
  retirement notice is historical and does not describe the published source.

These are desired-state declarations and existing documentation, not a fresh
live inventory. See the
[published Hermes declaration](https://github.com/madslundt/HomeCompute/blob/0774a4a72f15cf9f162a7999720ce555ea42c627/config/hermes-release.json),
[OMH declaration](https://github.com/madslundt/HomeCompute/blob/0774a4a72f15cf9f162a7999720ce555ea42c627/config/hermes-omh.json)
and [runbook](https://github.com/madslundt/HomeCompute/blob/0774a4a72f15cf9f162a7999720ce555ea42c627/deploy/hermes/README.md).

## Hermes: a viable conversation owner

Hermes has a native Mattermost adapter using REST API v4 and WebSocket events.
This supplies a two-way chat transport; a new incoming-webhook-only bot is
unnecessary. Keep the dedicated owner allowlist and token separation in the
HomeCompute runbook.
[Official Mattermost adapter documentation](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/mattermost).

The selected Hermes commit documents authenticated inbound routes, route-bound
profiles and toolsets, Mattermost delivery, filtering, coalescing and
event-triggered cron. Generic V2 signs the timestamp and body; its five-minute
acceptance window still needs application-level deduplication. Delivery IDs
are cached for one hour. Coalescing buffers live in memory and can be lost on
a hard kill. `mirror_to_session: true` copies delivered text into the
destination chat history, allowing contextual replies. Mirroring is
best-effort and skips chats without an existing session; delivery success
does not prove mirroring. Mirrored text becomes a user-history turn, so raw
untrusted content must not be promoted to authority through that path.
Durable events, correlation IDs and recoverable notification context remain
application responsibilities.
[Webhook documentation at the selected commit](https://github.com/NousResearch/hermes-agent/blob/2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19/website/docs/user-guide/messaging/webhooks.md).

Hermes documents persistent gateway sessions across restarts and separate
compact memory files plus session search. Its memory guidance also warns that
a textual claim to have remembered something is not evidence of a persisted
write. These facilities do not establish a shared authorized database across
Daythread, OpenClaw and n8n.
[Official memory documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory).

Native cron can schedule one-shot and recurring work and deliver it to a
configured chat. Native `delegate_task` uses isolated child contexts and
supports background completion. That does not provide a turnkey Hermes-to-
OpenClaw bridge; the cross-framework request/result adapter must be implemented
and verified separately.
[Cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron),
[delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation).

## Learning: supported mechanisms, limited guarantees

At the selected commit, Hermes documents agent-authored skills, background
review and `skills.write_approval`. Enabling that gate stages skill changes
persistently for review, including changes from background review. Memory has
a separate write gate. A content scanner is distinct from approval. These are
mechanisms for learning procedures and facts, not evidence that an improvement
is correct or that code, workflows, permissions or production releases are
validated automatically. HomeCompute still needs evaluation cases and a
source-owned promotion path.
[Skills and approval at the selected commit](https://github.com/NousResearch/hermes-agent/blob/2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19/website/docs/user-guide/features/skills.md).

Oh My Hermes is a separate project, not a Nous native component. At HomeCompute's
4.0.6 pin, its memory messenger classifier covers several platforms but omits
Mattermost. Unrecognized gateway conversations remain shared in that
classifier; a Mattermost DM alone does not qualify private OMH owner memory.
Keep the declared OMH memory mode off until a trusted identity adapter and
cross-user denial tests qualify a change. This limitation concerns OMH's
memory classification, not Hermes's ability to reply in Mattermost.
[Pinned messenger classification source](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_messenger.py).

OMH's pinned dreaming code offers only `off` and `reminder`. It constructs a
consolidation brief but makes no model call and cannot launch Hermes's review
itself. Therefore it should not be described as a separate autonomous
consolidation daemon. The existing HomeCompute integration audit provides
further pin-specific routing and concurrency qualifications.
[Pinned dreaming source](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_dreaming.py),
[published integration audit](https://github.com/madslundt/HomeCompute/blob/0774a4a72f15cf9f162a7999720ce555ea42c627/docs/research/oh-my-hermes-managed-integration-2026-11-06.md).

## OpenClaw: retain specialist execution

Current upstream documentation describes Mattermost as a downloadable plugin
with bot-token and WebSocket transport. It documents schedules, background
tasks and persistent sessions. Generic HTTP hooks accept a dedicated bearer
credential and can restrict target agents and session keys. Hook acceptance
does not establish completed execution or delivery. Hook authentication also
does not make payload content trusted.
[Mattermost](https://docs.openclaw.ai/channels/mattermost),
[automation](https://docs.openclaw.ai/automation),
[hook contract](https://docs.openclaw.ai/gateway/config-hooks).

The 2026-11-03 local audit records OpenClaw 2026.9.1. Current upstream docs show
newer scheduler and memory concepts; commands and migrations there must not be
applied blindly to that installed tuple. First inventory the actual selected
runtime and use HomeCompute's existing task transport and safety policies.
OpenClaw workspace memories are framework state, not an automatic shared
personal-context authority. Memory itself is not permission enforcement.
[Existing dated audit](../operations/openclaw-atlas-audit-2026-11-03.md),
[official memory overview](https://docs.openclaw.ai/concepts/memory).

## Mattermost actions: useful after plain replies work

Mattermost supports interactive buttons and menus with HTTP callbacks carrying
user, post, channel and team IDs. The integration must authenticate callbacks;
the documented patterns include secret tokens or cryptographic signatures.
Current documentation recommends Blocks and retains legacy attachment
actions. Select the format against the deployed server version. An action
handler must additionally check the authorized actor, action version, expiry,
current preconditions and duplicate use; a button click is not general authority
to execute whatever a message requests. Restrict internal callback reachability
to its specific service rather than widening private-network access broadly.
[Official interactive-message documentation](https://docs.mattermost.com/developers/integrate/plugins/interactive-messages).

## Planning consequences and remaining verification

The findings support Hermes as the proposed personal conversation owner while
reusing OpenClaw specialists, existing n8n integrations and the HomeCompute MCP
boundary. The missing work is durable shared context, deliberate attention,
source-linked tasks and completion handling. A new agent runtime is not required.
This is an architectural inference from the capabilities above.

Before enabling proactive delivery, verify on the exact deployed tuple:

1. A signed event reaches the intended owner profile and rejects invalid or
   stale signatures, wrong principals and disallowed tools.
2. The bot posts to the intended private conversation; a reply resolves the
   original decision after restart and when transcript mirroring is absent.
3. Retries, concurrent events and a process kill do not lose accepted events
   or repeat externally visible actions.
4. One delegated OpenClaw task produces a correlated terminal result; timeout
   is represented as uncertain until reconciled.
5. Skill proposals survive restart, remain source-reviewable, and cannot
   acquire credentials, cloud routes or broader action authority.

This review ran source reads and official web-document checks only. It does not
claim any of these live acceptance tests passed.
