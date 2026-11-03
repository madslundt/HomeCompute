# OpenClaw / Atlas live audit — 2026-11-03

Status: read-only inspection, approximately 15:10–15:25 UTC
(16:10–16:25 Europe/Copenhagen). No upgrade, service restart, configuration
write, workflow execution, task submission, reminder creation or message send
was performed. This is a bounded operational snapshot, not full acceptance.
The [Atlas plan](../openclaw-grok-bot-plan.md) owns the next changes; the
[upgrade assessment](../research/openclaw-upgrade-assessment-2026-11-03.md)
owns upstream version and compatibility evidence.

## Source and host state

| Item | Observed result | Meaning |
| --- | --- | --- |
| Workstation HEAD | `b0e8b106dd79e30d765ddd38970c29649ebfb37b` | Local master was 29 commits behind origin/master; unrelated documentation edits and two untracked speech notes were preserved |
| Desired revision reported by HomeCompute | `50ee4f9666c26df89bccab30c83e9cd6dcc0faba` | No checkout fast-forward or release was performed |
| home-core release marker/current pointer | `f630fde95a7133b099efa5b1973c77d3b347776b` | Differs from desired; does not identify every scoped installation |
| home-spark release marker/current pointer | `b0e8b106dd79e30d765ddd38970c29649ebfb37b` | Differs from desired; independently managed text/speech projects need their own evidence |
| `status --json`, `services`, `drift` | Completed, exit 0 | Both hosts reachable; drift reports both revision differences |
| `doctor` | Exit 1 | home-core platform-health unit failed; Spark has Docker access but requires interactive sudo for deployment |

No source or runtime difference was automatically classified as unintended drift.
In particular, newer speech deployment source exists on origin/master while this
workstation is behind it. Do not roll that work back by applying this older
checkout or reinstalling the complete communication bundle.

## Managed runtime and Atlas policy

| Boundary | Observed result | Verification limit |
| --- | --- | --- |
| Managed tuple | NemoClaw `v0.0.129`, OpenShell `0.0.116`, OpenClaw `2026.9.1 (ad6fe23)` | Queried existing binaries and installed package; no updater/Doctor repair |
| Image | `sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a` | Running container image matches recorded managed image; mutable installed files need separate checks |
| Sandbox Node | `v24.18.1` | Queried inside sandbox, distinct from guest management runtime |
| Package schema support metadata | shared state 15, agent 19 | From installed `openclaw.schemaVersions`; not an inventory of every retained database |
| Container | Healthy, restart count 0; started `2026-11-02T23:21:52.677977612Z`; 8 GiB / 2 CPUs | Resource limits are not a mixed-load performance qualification |
| Supervisor | User supervisor enabled/active, restart count 0 | Legacy external gateway/canary units inactive/disabled; no restart drill |
| Canonical authenticated health | `ok=true`; Mattermost configured/running/connected; 12 agent entries | Existing `openclaw-chat.py` transport; no channel message or model turn |
| Native config SHA-256 | `04ee9949fdc0d8d23455f6ba60ee825b7e55577b1469ef452d77dca5d1d46c19` | Same before/after selected native probes; this hash alone does not prove source reconciliation |
| Model | `inference/automation-moe`, managed `https://inference.local/v1`, empty fallbacks | Configured route; text readiness gauge 1, no new inference or artifact qualification |
| Atlas delegation | Ten allowed specialists; `sessions_spawn`, `sessions_yield`, `subagents`; isolated context and tree visibility | Configured, not exercised end to end in this audit |
| Execution limits | Main 1 concurrent turn; children 1 concurrent, depth 1, timeout 300 s, announcement timeout 600 s | Leaf agents cannot spawn; `sessions_send` denied, including Atlas |
| Other role | Gaia remains an entry but is excluded from Atlas's ten delegate IDs | Entry existence does not establish active ingress or sharing authority |
| Native automation | `cron.enabled=false`, default/leaf heartbeat `every=0m`; hooks/ACP disabled | No ambient agent scheduler was activated |
| Channel/reload | Mattermost enabled with 12 accounts; Telegram disabled; reload mode off | Account count is not proof of every binding, permission or direct-message path |
| Plugins | Safety, Mattermost, browser, memory, NemoClaw, OpenAI and agent-office enabled; legacy broker plugin disabled | Legacy plugin disablement does not remove separate finite MCP tools; actual tool calls were not exercised |

A direct container-context health invocation returned `ok=false`; the canonical
managed authenticated transport returned `ok=true`. The direct invocation was
not treated as a service outage or a reason to alter authentication. A full
Mattermost request → specialist → final result remains unverified here.

## Source comparison and upgrade-sensitive patches

The installed communication copies of `openclaw_tasks.py`,
`openclaw_household_tasks.py`, `openclaw-household-task-reminders.py`,
`openclaw_family_task_projection.py`, `openclaw_integrations.py`,
`openclaw_location_policy.py` and `openclaw_infrastructure.py` matched the
workstation source by SHA-256. Installed `openclaw_speech.py` differed from the
workstation but matched origin/master exactly:
`421387001caebe926c1ebb2e15ef48593c8cd19c68bd767e06d5f372c094aad8`.
That is evidence of a scoped newer source installation, not a repair target.

The installed safety `index.mjs`, `public-dns.mjs`, `browser-schema.mjs` and
`llm-queue.mjs` matched repository source. The installed Mattermost plugin was
`@openclaw/mattermost@2026.9.1`. Markers for the reviewed single-child result
extension and yield-acknowledgement patch were present in their pinned native
dist modules. Marker presence is not an independent full-file patch validation.
These files, the Mattermost pre-claim queue patch and agent-office integration
must be tested or ported for each candidate; never copy old dist modules into a
new runtime. See the upgrade assessment's compatibility matrix.

## Tasks, reminders, schedules and follow-ups

| Capability | Fresh observation | Remaining gap |
| --- | --- | --- |
| Trusted adapter and coding task feed | Both active, restart counts 0 | Does not prove an actual coding admission/result or general assistant-job lifecycle |
| Coding VM and worker | Host VM active; guest `homecompute-codex-worker.service` enabled/active/running, restart count 0 | Contradicts older stopped-worker notes; no broker ledger, in-flight jobs, credentials or publication readiness inspected |
| Owner/Family reminder timers | Both active; latest service completions exit 0 | Timers reported unit-file state disabled; verify owning boot dependencies/activation before claiming reboot persistence |
| Reminder destinations | Existing finite Mattermost verifier authenticated exact bot/private channel for owner and Family; zero messages sent | Phone receipt, actual scheduled reminder delivery and scope semantics not exercised |
| Location reminders | Listener active, restart count 0; existing configuration present | No location event, phone acceptance or device delivery test |
| Native workflow-status credential | Fixed `integration-n8n-token` file absent | Our operator MCP can inspect n8n; that does not grant Atlas workflow visibility |
| Maps/photo configuration | Geoapify token reference absent; Immich token reference present | File presence is not provider authentication or a successful request |

The coding guest initially refused the default SSH known-hosts lookup. Inspection
then succeeded using the existing dedicated pinned known-hosts file documented in
[runtime research](../openclaw-runtime-research.md#approved-dedicated-vm-provisioning-2026-10-09).
No host key was fetched, trusted anew or changed.

Read-only n8n MCP discovery returned 34 workflow records. Selected active
definitions and their published version IDs matched; Europe/Copenhagen was
configured for these scheduled routines:

| Existing workflow | Recorded trigger/ownership | Execution evidence inspected |
| --- | --- | --- |
| Household tomorrow preparation | n8n, daily 20:30; `WKu7Zv4dE4ztnaRY` | No retained executions returned; success/error execution data saving disabled |
| Morning weather and reminders | n8n, kitchen checks 05–12; `WxRovre260928abc` | Three latest returned trigger runs on November 3 at 10:57–10:59 UTC succeeded; this does not prove a briefing was due or delivered |
| Weekly family holiday bargains | n8n, Monday 09:00; `s91pCneog66WfwG7` | No retained executions returned; success/error execution data saving disabled |
| HA read-only observations | n8n authenticated webhook; `hcOpenClawHA20261010` | No retained executions returned; success/error execution data saving disabled |

Absence of retained executions is not proof of non-execution. No workflow was
started or edited, and no execution payload or report content was read. Introduce
bounded metadata-only run/delivery receipts rather than enabling private payload
logging. Existing morning/tomorrow/travel routines must not be duplicated in a
native scheduler as part of the Atlas work.

## Readiness and recovery gates

The existing platform-health metrics at `2026-11-03T15:17:51Z` reported text,
embedding, TTS, Wyoming STT/TTS and both n8n paths ready. Direct Danish and
multilingual STT readiness were 0, NAS backup readiness 0, NAS repository-check
readiness 1, and overall platform readiness 0. Speech is being independently
managed; this audit neither changed placement nor diagnosed the failing paths.

NAS backup service was `activating/auto-restart`, exit 75, restart count 25;
the documented lifecycle treats 75 as temporary idle-preflight deferral. The
specific busy source was not inspected. Last completed encrypted-backup receipt
was `2026-11-02T12:56:36.432969Z`; last repository check was
`2026-11-02T00:23:22.486218Z`, passed. Backup and check timers were active.
The retained deployment receipt was in `retired` state, consistent with transfer
to canonical units; retirement does not waive freshness/service requirements.
No new backup, VM pause or restore was requested. Actual full-state restore
acceptance and snapshot coverage are unverified in this audit.

Before production state migration or increased unattended execution, resolve or
explicitly account for these readiness failures, obtain a current consistent
backup, and verify isolated restoration of the exact OpenClaw image/state tuple.
Synthetic candidate work need not wait for unrelated speech repairs, but cannot
be presented as full platform or voice readiness.

## What remains unverified

All live user-task execution, specialist result delivery, cancel/restart behavior,
per-role tool enforcement, exact channel bindings, phone delivery, current broker
work, complete state inventory/restore, model artifact identity and mixed-load
capacity remain unverified. Selected matching files, running processes and a
healthy gateway do not establish those outcomes or synchronization of either host.
