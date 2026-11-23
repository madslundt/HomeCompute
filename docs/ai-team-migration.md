# Orca and OpenBot team migration

Orca owns development worktrees, implementation, independent review and PR
preparation. GitHub Projects owns software backlog priority. OpenBot owns
personal chat and notifications. Hermes owns personal memory. Home Assistant
and existing scoped services keep authority over device actions.

This release prepares the migration from Home Core
`7074407d3879a2b952c4c94e5ef9a0225dbaf020`, Orca 1.4.268 and OpenBot 0.73.0.
The team manifests describe rollout targets. Use the postchecks to determine
which settings are present.

## Inventory and preservation

Orca had HomeCompute, ChessMate and Daythread groups and PO bots, with one
enabled weekday summary for ChessMate. The manifest adds the public
`madslundt/House-battery` repository on `main`. No active IRIS backlog or
repository was identified. Keep the three existing Orca bot IDs, repository
checkouts, chat history and memory. The four POs share task-scoped Developer,
Reviewer and Designer instructions in [teams.json](../deploy/orca/teams.json).
Each worker receives its assigned project and issue. Use separate review agents
and Git worktrees, and obtain approval before merge or production deployment.

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
| Four Orca POs | OpenCode 2.3.2, `automation-moe`, `medium` | Clear scope and authenticate a cloud model before opting in. The local triage fixture failed. |
| Developer | OpenCode 2.3.2, `automation-moe`, `medium`, with explicit queued `xhigh` for complex work | Native Codex after approved login. AI execution uses Ask and isolated worktrees. |
| Reviewer | Independent OpenCode session, `medium` | Prefer a separately approved, authenticated provider for important review. |
| Designer | OpenCode 2.3.2, `medium` | Use approved design tools and invite only for UI/UX work. |

`automation-moe` reached Spark's live Qwen3.8 Flash-Next UltraFast trial via
`openai/automation-qualification`. The gateway advertised no second local
text model. Spark had 13 GiB available and used 8 GiB swap at one sample.
Preserve the resident model and its existing qualification policy. There is
no implicit cloud fallback. The Orca deep relay queues concurrent deep
requests using one upstream HTTP/1.1 connection. Interactive chat shares GPU
capacity and mixed-load performance remains a separate acceptance test.
The pinned Orca runtime has Codex installed and reported no authenticated
account. Claude Code was not installed there. Existing OpenBot consent and
authentication stay in the OpenBot boundary.

Synthetic local Chat Completions tests used verified TLS, serial requests
and no private inputs or cloud calls. Each cell shows end-to-end seconds,
completion tokens and outcome. Values are single samples.

| Fixture | None | Low | Medium | Xhigh |
| --- | --- | --- | --- | --- |
| Short Danish chat | 0.432 / 2 / pass | 2.418 / 51 / pass | 2.353 / 52 / pass | 1.783 / 54 / pass |
| Exact read-only tool selection | 1.289 / 29 / pass | 2.576 / 71 / pass | 2.039 / 71 / pass | 6.103 / 152 / pass |
| Issue triage | — | — | 9.347 / 311 / fail | 10.997 / 384 / length limit |
| Small coding fixture | — | — | 9.614 / 315 / pass | 7.135 / 228 / pass |
| Complex coding fixture | — | — | 12.453 / 503 / pass | 17.234 / 506 / pass |
| Independent-review fixture | — | — | 7.434 / 195 / pass | 3.209 / 124 / pass |

Tool calling at `none` produced its first visible action after 0.825 s.
Omitting effort enabled thinking. `minimal`, `high` and `max` gave HTTP 400.
The coding fixtures assessed bounded answers, without worktree execution.
Measure agent E2E, queue wait, TTFT, completion tokens, errors, approval waits
and actual provider costs in the corresponding runtime before broad rollout.
[Earlier Spark benchmark](benchmarks/home-spark-efficiency-plan-2026-10-29.md).

Two gateway mixed-load probes completed the `none` chat in 0.497 s and
0.749 s while one `xhigh` request remained active. Both heavy runs reached
their 640–650 token budgets before producing a visible answer. The small
chat remained responsive in those samples, and the heavy coding fixture
needs repeated qualification. OpenCode 2.3.2 also passed an exact synthetic
reply at `medium` through the live scoped relay using disposable process state.


## Apply, verify and roll back

1. Review the release. Capture and verify the Orca cold backup according to
   the [Orca runbook](../deploy/orca/README.md). Deploy a reviewed published
   full SHA through `./scripts/homecompute deploy home-core --revision FULL_SHA`.
2. Reconcile Orca teams through the paired client, preview before applying,
   then reconcile the weekly summaries. Verify a snapshot and completed chat
   output in all four projects. GitHub Project boards need a separately
   approved Projects-read credential. The existing CLI credential lacked
   `read:project`. Public GitHub issue, PR and check metadata is available
   without a credential. Use a scoped owner approval to actually publish a PR.
   [GitHub Projects authentication](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects#authentication).
3. Capture OpenBot before changes. The wrapper requires verified encrypted
   backup and a clean checkout of the exact published and deployed revision.
   Run `./scripts/setup-openbot-guest.sh personal-models plan`, then `apply`.
   Verify the same agent IDs, provider, model and effort through the signed-in
   owner Team client and use a synthetic Danish chat and tool request.
   OpenBot sends the saved per-agent effort on ACP turns. The embedded
   Hermes CLI coerces the unknown config path's bare `none` to YAML null,
   so use OpenBot's settings API for this rollout. Check
   separate Hermes service restart readiness before moving privileged jobs.
4. Use the source definitions in [openbot-personal-team.json](../config/openbot-personal-team.json)
   to retain, rename and restrict the owner agents in place through the
   signed-in client once references and permissions are reconciled. Prepare
   the morning routine for 07:00 Copenhagen weekdays after choosing the
   existing morning workflow's owner-only delivery edge. Review the native
   routine history and test the owner's iPhone with notifications set to All
   and signed-in host registration. Verify locked-device display, opening the
   correct chat, reply and approval, and voice separately.
5. Use existing n8n ingress for GitHub events if the signature and repository
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
after checking that the captured pre-migration choices still apply. Roll
Orca code back to the prior full revision; use the documented cold-archive
restore for bot and schedule state. Restore a verified archive to an
alternate directory and check ownership before selecting it. Source rollback
and application data rollback have separate effects.

Track rollout evidence in [HomeCompute issue #185](https://github.com/madslundt/HomeCompute/issues/185).
