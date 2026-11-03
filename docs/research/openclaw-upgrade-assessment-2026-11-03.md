# OpenClaw upgrade assessment — 2026-11-03

Status: primary-source assessment and repository compatibility review. No runtime,
package, image, database, channel, schedule or permission was changed. This note
does not qualify a production upgrade. See the companion
[live audit](../operations/openclaw-atlas-audit-2026-11-03.md) and
[Atlas plan](../openclaw-grok-bot-plan.md) for deployment evidence and the
implementation sequence.

## Decision

Keep OpenClaw. Evaluate two distinct upgrade candidates rather than equating
the newest core package with a compatible managed deployment:

1. **Managed maintenance candidate:** NemoClaw source tag `v0.0.140`, commit
   `88fe14f3fb9a0bf72997cce19ec88b5b2ee08391`, with its locked OpenClaw
   `2026.9.2` and OpenShell `0.0.116`. This is the newest non-prerelease
   source tag found in the NVIDIA tag listing, not a qualified HomeCompute
   image or a GitHub stable release asset. The GitHub releases endpoint did
   not expose a normal stable release. Its CLI package version is `0.1.0`;
   that does not make an arbitrary npm package named `nemoclaw` authoritative.
2. **Feature candidate:** OpenClaw `2026.10.10`, the latest stable core on both
   GitHub Releases and npm at inspection. It is worth qualification because
   of its automation and delegated-work improvements. It is **not yet a
   compatible HomeCompute managed tuple**: NVIDIA's reviewed managed build
   still locks `2026.9.2` and refuses arbitrary versions without committed
   integrity and lifecycle policy.

`2026.9.1` remains the verified deployed core until one of those candidates
passes the gates below. A successful version query, compatible Node engine, or
healthy bare Gateway would not qualify our Mattermost, MCP, safety hooks,
memory, speech and recovery paths. [NVIDIA tags](https://api.github.com/repos/NVIDIA/NemoClaw/tags?per_page=100),
[managed runtime lock](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/agents/openclaw/openclaw-runtime/package.json),
[managed manifest](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/agents/openclaw/manifest.yaml),
[managed Dockerfile](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/Dockerfile).

## Exact release evidence

The GitHub latest-release API reported `v2026.10.10`, published
`2026-11-03T03:22:35Z`, with `draft=false` and `prerelease=false`. The annotated
tag object is `6eef4472c85436c7deb8563248827d7f6d29df67`, resolving to commit
`7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be`. npm's latest package and official
Mattermost plugin both reported `2026.10.10`.
[Release](https://github.com/openclaw/openclaw/releases/tag/v2026.10.10),
[release API](https://api.github.com/repos/openclaw/openclaw/releases/latest),
[tag object](https://api.github.com/repos/openclaw/openclaw/git/tags/6eef4472c85436c7deb8563248827d7f6d29df67),
[core registry metadata](https://registry.npmjs.org/openclaw/2026.10.10),
[Mattermost registry metadata](https://registry.npmjs.org/@openclaw%2fmattermost/2026.10.10).

| Property | Existing core `2026.9.1` | Managed candidate `2026.9.2` | Feature candidate `2026.10.10` |
| --- | --- | --- | --- |
| npm Node engine | `>=22.22.3 <23 \|\| >=24.15.0 <25 \|\| >=25.9.0` | Same | `>=24.16.0 <25 \|\| >=26.1.0` |
| Advertised agent schema | 19 | 19 | 27 |
| Advertised shared-state schema | 15 | 15 | 22 |
| HomeCompute qualification | Existing deployed tuple | Not performed | Not performed |
| NVIDIA managed build pin at reviewed tag | Historical HomeCompute pin | Committed runtime lock | No committed managed pin/lifecycle |

These schema numbers are package support metadata, not a read of every live
database. The live audit found sandbox Node `v24.18.1`, which satisfies the
latest core's declared engine. It is distinct from the guest supervisor's
Node executable. The `2026.10.10` changelog recommends Node `24.22.1` or
`26.12.1` for security; evaluate a pinned sandbox runtime change as part of the
image tuple, without silently changing the guest's CLI runtime.
[Existing package](https://registry.npmjs.org/openclaw/2026.9.1),
[managed candidate package](https://registry.npmjs.org/openclaw/2026.9.2),
[feature package](https://registry.npmjs.org/openclaw/2026.10.10),
[tagged changelog](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/CHANGELOG/2026.10.10.md).

The reviewed NemoClaw `v0.0.140` blueprint sets both minimum and maximum
OpenShell to `0.0.116`. OpenShell's latest release API reported `v0.1.6`, but
that is outside this managed blueprint; it is not an independent upgrade
recommendation. Preserve managed credential custody and the inference-local
boundary. OpenClaw's optional OpenShell backend documentation describes a
different arrangement in which its Gateway delegates tool sandboxes; it does
not prove compatibility for our Gateway running inside NemoClaw.
[Blueprint](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/nemoclaw-blueprint/blueprint.yaml),
[OpenShell release API](https://api.github.com/repos/NVIDIA/OpenShell/releases/latest),
[tagged backend guide](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/gateway/openshell.md).

## Features to reuse before adding controllers

The following are documented in the exact `2026.10.10` tag. They need a
synthetic runtime rehearsal with HomeCompute policy before being promised to
Atlas users.

| Need | Released upstream behavior | HomeCompute implication |
| --- | --- | --- |
| Schedules and reminders | Gateway-owned automations persist definitions, state and run history in shared SQLite; the Gateway must remain running | Reuse for uncovered assistant routines only; preserve existing n8n/timer ownership |
| Scheduled completion | Execution and delivery outcomes are recorded separately; an interrupted one-shot with an uncertain result stays disabled rather than replaying effects | Expose failed/unknown delivery and inspect before manual retry |
| Multi-agent work | Children run in separate sessions with per-controller concurrency budgets; completed child results and requester settlement retain recovery state | Keep Atlas as owner and initially retain serial depth-one delegation |
| Restart follow-up | Interrupted children settle instead of being automatically relaunched; the parent inspects partial work and decides how to continue | Test read-only resumption; never interpret restart as permission to repeat a write |
| Proactive monitoring | Heartbeat is a system-owned automation; `cron.enabled=false` stops its recurring schedule, while `heartbeat.every=0m` disables recurring cadence | Enabling cron is an explicit policy change; do not turn on ambient monitoring for every specialist |
| Routine proposals | The agent can propose promotion from a repeated task when granted the automation tool | Native enabled-on-creation/test behavior must be adapted to Git-managed persistent configuration |

Sources: [tagged automation lifecycle](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/automation/cron-jobs/how-it-works.md),
[tagged subagent operations](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/tools/subagents/operations.md),
[tagged restart recovery](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/gateway/restart-recovery.md),
[tagged heartbeat](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/gateway/heartbeat.md).

**Documentation drift matters.** During this assessment the live heartbeat
documentation described retiring `agents.*.heartbeat` into ordinary editable
automation jobs. The `2026.10.10` tagged guide instead says heartbeat config
remains desired state and tells operators to edit that config, not the monitor
row. Live restart documentation also described a different recovery design.
Use exact tagged documentation and installed behavior for this candidate;
do not apply moving documentation's migration commands to production.

The latest release notes specifically include delegated continuation/restart
and paused-parent result fixes, automation fixes, and Mattermost ordering and
preview pacing fixes. These are reasons to test the candidate, not proof that
our local patches are obsolete.
[Tagged release record](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/CHANGELOG/2026.10.10.md).

## Repository compatibility work

| Boundary | Evidence in HomeCompute | Required change or gate |
| --- | --- | --- |
| Native child results | [Installer](../../scripts/setup-openclaw-child-results.py) pins `subagent-announce-output-CgCr3vGC.js` and its SHA-256 | Reproduce result truncation on each candidate; retire patch if upstream solves it, otherwise port against exact new source and test |
| Yield acknowledgement | [Installer](../../scripts/setup-openclaw-yield-acknowledgment.py) pins `embedded-agent-DaSvA-Yk.js` and its SHA-256 | Prove one waiting acknowledgement and one final result, including earlier tool warnings; do not transplant old dist bytes |
| Mattermost queue | [Installer](../../scripts/setup-openclaw-llm-queue.py) hard-pins plugin `2026.9.1`, a project directory, runtime filename and preimage hash | Requalify pre-claim gating, cancellation and the read-only activity RPC on the candidate plugin |
| Channel package provenance | [Mattermost setup](../../scripts/setup-openclaw-mattermost.py) admits exactly `@openclaw/mattermost@2026.9.1` | Pin a qualified matching plugin artifact, integrity and install record; update setup and provenance tests together |
| Independent safety enforcement | [Safety plugin](../../deploy/openclaw/safety-plugin/index.mjs) uses `before_tool_call`, native execution identity, sticky veto assumptions, service registration and custom activity RPC | Prove fail-closed vetoes, trusted identity, ordering and public-browser policy with real candidate SDK; loading is insufficient |
| Broker/finite MCP tools | [Broker plugin](../../deploy/openclaw/broker-plugin/index.mjs) and native MCP role policy | Prove finite grants and denials outside prompts, result serialization, token custody and role isolation |
| Team configuration | [Team policy builder](../../scripts/openclaw_team_policy.py) retains ownership, tools, models, session visibility, one child and depth one | Validate rendered effective multi-agent configuration against exact candidate; preserve revoked grants and protected sections |
| State-preserving recovery | [Recovery tool](../../scripts/restore-openclaw-nemoclaw.py) hard-pins current image and checks specific state tables and native lifecycle | Add a separate reviewed candidate/upgrade lifecycle; do not repurpose incident recovery by changing only its image constant |

The latest official Mattermost package declares peer core and plugin API
`>=2026.10.10`. A core update can succeed while optional plugin maintenance
warns or a local operator-managed plugin remains unchanged. Therefore core
updater success must not be our release acceptance signal.
[Plugin metadata](https://registry.npmjs.org/@openclaw%2fmattermost/2026.10.10),
[tagged updater contract](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/install/updating.md),
[tagged plugin compatibility](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/plugins/compatibility.md).

## Qualification and rollback gates

1. Capture the deployed image/runtime tuple, effective non-secret policy hashes,
   plugin artifacts, patch hashes, state schemas, supervisors and dedicated
   lifecycle receipts. Classify drift before replacing anything.
2. Prepare a repository-owned, immutable candidate image and finite lifecycle.
   For `2026.10.10`, first establish a supported NVIDIA recipe or a reviewed
   extension of its locked build; do not overwrite the live npm tree. Keep
   production inference credentials, channels, browser sessions and schedules
   out of the candidate. A retained new lifecycle/trust decision needs the
   architecture issue required by [CONTRIBUTING](../../CONTRIBUTING.md).
3. Rehearse migration and rollback on synthetic databases that exercise the
   existing schema versions, with candidate plugins and scoped fixture tools.
   Inspect native dry-run/Doctor output only on those copies. The tagged guide
   requires bridge `2026.9.5` for pre-July legacy stores and `2026.9.7` for the
   retired same-file memory index; our September version alone does not prove
   that no older retained files exist. Inventory before selecting a bridge.
4. Run real candidate SDK/policy checks plus the meaningful HomeCompute team,
   recovery, child-result, yield, queue, browser, MCP, communication and reminder
   tests. Test restart, hard kill, duplicate admission, cancellation, provider
   outage, unknown write outcome, failed delivery and revoked authority.
   Prove the original thread receives one final result and shared/private
   boundaries hold. No production conversation is a test fixture.
5. Before a production migration, require an encrypted off-host backup and
   isolated restore drill covering the actual OpenClaw state, config, plugin
   installs, protected patches, memory and matching image. Preserve writer
   quiescence and in-flight work. Retain the current full tuple and state
   snapshot; backups of config alone are insufficient.
6. Publish a reviewed source revision, then use its owning guarded lifecycle
   with native supported stop/start and explicit backup/migration admission.
   Do not run upstream `openclaw update`, `npm install -g`, `doctor --fix`, or
   NVIDIA's general installer directly against the live managed deployment as
   an exploratory check. They can mutate packages, state, plugins or services.
7. Verify authenticated client paths, delivered result, all required plugins,
   policy denials, state integrity, capacity and service recovery. Repeat
   HomeCompute `status`, `services`, `doctor` and `drift`; retain unrelated
   failures and unknowns explicitly.

`2026.10.10` advances both advertised schema families. Upstream explicitly
states that downgrading packages does not reverse database/config migrations;
once state is beyond the old release's format, restore a verified matching
pre-update backup. Automatic rollback is limited to proven schema-neutral
conditions. Even `2026.9.2` retaining the same advertised numbers does not prove
that our image, plugins, native state checks or external effects can roll back.
Keep recovery originals until acceptance; do not edit schema markers to force
old code to open new state.
[Tagged rollback guide](https://github.com/openclaw/openclaw/blob/7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be/docs/install/updating/rollback-and-recovery.md),
[existing managed recovery](../openclaw-nemoclaw.md#retained-production-team-recovery).

The lowest-risk next implementation is the pinned managed `2026.9.2` candidate
and patch compatibility matrix. In parallel, resolve whether `2026.10.10` can
be built and supported inside the managed boundary. Before adding a general
durable-job controller, exercise native parent recovery and automation lifecycle
on that qualified candidate. Add only the missing controller behavior that the
tests demonstrate; keep household task records, coding state and existing n8n
workflow state in their current authoritative stores.
