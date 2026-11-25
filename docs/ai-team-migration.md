# OpenBot personal-team rollout

OpenBot owns personal chat and notifications. Hermes owns personal memory.
Home Assistant and existing scoped services keep authority over device actions.
The OpenBot 0.73.0 owner inventory below is rollout evidence.

## Inventory and preservation

The OpenBot owner inventory contained the following role groups.

| Existing agents | Disposition |
| --- | --- |
| Hermes, Home Planner, Home Assistant Product Owner, Researcher | Keep the same records and history. Reuse Home Planner for Life Planner and Home Assistant Product Owner for Home Guardian. |
| Atlas | Keep until its unique memory, watchdog and nightly signed webhook have an accounted-for successor. |
| Engineer (formerly Builder), Lead Engineer, QA, UX Designer, UI / Visual Designer, Daythread Product Owner | Preserve history. Migrate open work to its GitHub issue/PR and review all channel and workflow references before reversible retirement. |
| PodWave Product Owner | Preserve the distinct project, manual workflow and history pending a separate handoff. |
| Telegram Orchestrator, WhatsApp Orchestrator | Retain while their existing channel subscriptions and memory are in use. |

The owner server had 14 agents, four active channels, two manual Ready-work
workflows and four active routines. Atlas owned the development channels for
Daythread, PodWave and Engineering. Hermes and Home Planner shared Household
Projects. The owner inventory found no OpenBot morning briefing. An existing
n8n morning overview is already declared and must be accounted for before
introducing a second delivery path. See the
[existing owner routine patch qualification](../automations/assistant-context/routine-patch-contract.md).

Owner OpenBot has an embedded Hermes home and a separate standalone
`hermes-personal-v2` with existing private memory and skills. The fast choice
changes routing while preserving the embedded profile on the same server.
Keep the standalone profile, its existing privileged tools, and the separate
partner server in their existing trust boundaries. OpenBot 0.73.0 exposes
enabled server MCP connections to every server agent. Workspace access and
Computer Use controls are independent. Audit the owner server registrations
before enabling personal-tool delegation to additional agents or cloud
providers. [OpenBot agent architecture](https://github.com/nightly-labs/openbot/blob/v0.73.0/docs/architecture/agents.md).

Preserve history by pausing legacy routines or transferring their owner,
removing channel, workflow and subscription references and then hiding the
retired agent. OpenBot Hide preserves history and still allows work to run.
Verify no active jobs remain for a retired agent. Preserve active conversation
history, drafts, unique memory and unmerged branches. Keep the full encrypted
owner archive rather than exporting credentials or private memory into Git.
[OpenBot hidden agents](https://github.com/nightly-labs/openbot/blob/v0.73.0/docs/architecture/agents.md#hidden-agents).

## Model routes

| Role | Release target | Escalation and access |
| --- | --- | --- |
| Hermes | [ChatGPT Sol (latest)](openbot-agent-setup.md#chatgpt-sol-defaults-for-the-existing-hermes-agents) | Use only provider-approved inputs. |
| Life Planner, Home Guardian | Embedded Hermes, `automation-moe`, `none` | Explicit local `medium` when useful. Standalone Hermes retains its scoped personal tools. |
| Researcher | Existing explicit Sol route, `high`, for approved public material only | Request Hermes local support for private material. Preserve Ask-first fallback and review enabled MCP before delegating. |

## Apply, verify and roll back

1. Capture OpenBot before changes.3. Capture OpenBot before changes. The wrapper requires verified encrypted
   backup and a clean checkout of the exact published and deployed revision.
   Run `./scripts/setup-openbot-guest.sh personal-models plan`, then `apply`.
   Verify the same agent IDs, provider, model and effort through the signed-in
   owner Team client and use a synthetic Danish chat and tool request.
   OpenBot sends the saved per-agent effort on ACP turns. The embedded
   Hermes CLI coerces the unknown config path's bare `none` to YAML null,
   so use OpenBot's settings API for this rollout. Check
   separate Hermes service restart readiness before moving privileged jobs.
2. Use the source definitions in [openbot-personal-team.json](../config/openbot-personal-team.json)
   to retain, rename and restrict the owner agents in place through the
   signed-in client once references and permissions are reconciled. Prepare
   the morning routine for 07:00 Copenhagen weekdays after choosing the
   existing morning workflow's owner-only delivery edge. Review the native
   routine history and test the owner's iPhone with notifications set to All
   and signed-in host registration. Verify locked-device display, opening the
   correct chat, reply and approval, and voice separately.
3. Use existing n8n ingress for GitHub events if the signature and repository
   can be verified. Start with a single allowlisted PR ready-for-review event
   and deduplicate by delivery ID. Send only repository, PR number and URL
   in a signed native OpenBot Signal. The HMAC and credential stay in n8n.
   A successful webhook response confirms admission. A completed routine and
   phone receipt prove delivery. A standalone Hermes cron job cannot generate
   this OpenBot push itself. Keep automatic merge, deployments and appliance
   actions behind their existing approval surfaces.
   [OpenBot webhooks](https://github.com/nightly-labs/openbot/blob/v0.73.0/docs/webhooks.md).
   [iPhone notification path](https://github.com/nightly-labs/openbot/blob/v0.73.0/docs/architecture/channels-and-messaging.md#iphone-notifications).

For model rollback, run `personal-models rollback` from the approved release
after checking that the captured pre-migration choices still apply.

Track rollout evidence in [HomeCompute issue #185](https://github.com/madslundt/HomeCompute/issues/185).
