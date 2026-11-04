# OpenClaw toward a Grok Bot experience

Updated: 2026-11-04. Status: implementation underway. The attended backup and
full-team recovery repairs are published and were applied through their scoped
lifecycles. Atlas retains NemoClaw 0.0.129 / OpenClaw 2026.9.1; the managed
upgrade is blocked by Mattermost credential custody. Retained-version MCP,
watchdog and recovery repairs were deployed through the scoped lifecycle at
`49216e9`. Authenticated MCP discovery verifies all sixty-nine tools, including
after the backup restart. A new encrypted NAS snapshot completed successfully
on November 4 at 04:53 UTC; the earlier November 3 snapshot retains its separate
accepted restore proof. The
[execution receipt](operations/openclaw-recovery-upgrade-2026-11-03.md) distinguishes
actual recovery evidence from pending gates. Proposed capability interfaces,
schedules, defaults and acceptance thresholds below are not installed.

The next retained-version task phase is implemented and being qualified through
[its scoped lifecycle](operations/openclaw-atlas-tasks.md), with actual apply and
acceptance tracked in [the November 4 receipt](operations/openclaw-atlas-tasks-2026-11-04.md).
Native task ownership/status/cancellation and opt-in unfinished-task watches
reuse existing controllers. General recurring research and arbitrary source
watches remain separate qualification work.

Start with the [fresh live audit](operations/openclaw-atlas-audit-2026-11-03.md)
and [version-pinned upgrade assessment](research/openclaw-upgrade-assessment-2026-11-03.md).
They supersede older stopped-worker and host-revision assumptions for this plan.
The [October 29 comparison](benchmarks/openclaw-rakazo-baseline-2026-10-29.md)
and [October 30 performance experiments](benchmarks/openclaw-performance-2026-10-30.md)
remain historical measurements, not current acceptance or promoted settings.

## Recommendation and product decision

Improve OpenClaw and make Atlas responsible for task completion across its
existing specialists and controllers. Retain Mattermost as the first user
surface. Prioritize task ownership, bounded handoffs, schedules, reminders and
useful proactive follow-ups before a new computer interface or voice product.

Qualify the latest compatible managed tuple rather than installing the latest
core package into a live sandbox. The deployed verified tuple is NemoClaw
`0.0.129` / OpenClaw `2026.9.1` / OpenShell `0.0.116`. The newest reviewed
managed-source candidate is NemoClaw tag `v0.0.140`, locking OpenClaw
`2026.9.2` and OpenShell `0.0.116`; it is not HomeCompute-qualified. Native backup rejects inline Mattermost account credentials, and NemoClaw 0.0.140 provides no managed Mattermost messaging-credential handoff. All twelve live accounts use that representation. Keep the current tuple until a separately qualified credential integration supports the full team; do not weaken the native scanner or promote standalone core.
Standalone core `2026.10.10` is the latest stable release found on November 3,
but its managed build, plugins, state migration and local patches are unqualified.
Its features must not be promised on the `2026.9.2` candidate. See the cited
release/source evidence in the upgrade assessment.

Use native task/session/automation lifecycle where it passes our requirements.
Do not add a parallel general-purpose task engine before demonstrating a native
gap. Existing n8n workflows, household task stores and coding broker retain
their authority. Rakazo comparison is deferred, not a prerequisite for improving
Atlas. Revisit the [candidate assessment](research/rakazo-assessment-2026-10-29.md)
before expensive custom computer/teaching UI work if OpenClaw's qualified
workflow results or maintenance cost justify reopening the product choice.

The target is the experience described by [Grok Bot](https://x.ai/bot) and its
[overview](https://docs.x.ai/grok-bot/overview): message a persistent teammate,
delegate useful multi-step work, see progress and results, keep confirmed
preferences, and reuse successful workflows. Its [routine documentation](https://docs.x.ai/grok-bot/skills-routines-and-automations)
distinguishes reusable instructions from triggers and treats demonstration as
a draft that should be reviewed and tested. These are vendor descriptions,
not measured promises of reliability or a requirement to copy cloud hosting.

HomeCompute keeps local private inference, private LAN/Tailscale access,
separate personal/shared/work domains, secret custody outside agents, and
independent action controllers. A named specialist is not automatically an
independent security principal. See [SECURITY](../SECURITY.md),
[ADR-015](adr/015-personal-data-domains-and-memory.md) and the
[memory contract](personal-data-and-memory.md).

## Baseline and gaps

| Area | Existing source or recorded evidence | Work still needed |
| --- | --- | --- |
| Teammates | Live Atlas plus ten eligible specialists; serial, depth-one isolated delegation; direct session messaging denied | Multi-step ownership, reliable result return, cancellation and status; concurrency remains gated |
| Background execution | Live native lifecycle, [coding broker](../deploy/codex-worker/broker.py), [task feed](../scripts/openclaw_tasks.py); coding VM and guest worker currently running | Inspect actual broker activity and qualify general research/preparation recovery; worker process health is not coding acceptance |
| Everyday tasks | [Household task store](../scripts/openclaw_household_tasks.py), active owner/Family reminder timers and location listener; authenticated Mattermost destinations verified without sending | End-to-end reminder/phone delivery, reboot persistence, explicit private/shared destination semantics and a unified view |
| Research | [Protected browser and search fallback](openclaw-tools.md), [specialist integrations](openclaw-integrations.md) | Broader qualified source coverage, complete evidence and multi-step acceptance |
| Apps/computers | Finite site operations, owner-only sign-in viewer and registered account grants | Interactive task computer, explicit action permissions and owner takeover; the owner sign-in viewer is not an agent computer |
| Memory | Workspace memory and local embeddings | Full inspect/correct/delete/export controls, provenance and cross-domain deletion checks |
| Voice | [Mattermost local speech](openclaw-communication.md#mattermost-voice-messages) | Streaming, turn detection, interruption and mobile reconnect for live conversation |
| Routines | Existing n8n morning/tomorrow/travel routines active; native cron and heartbeats disabled | Source-owned proposals, bounded metadata-only run history, pause/test controls and native qualification for uncovered jobs |
| Proactive follow-ups | Existing observations, update feed and scheduled briefings | Authorized goal/expiry, change thresholds, deduplicated meaningful notifications, quiet periods and explicit escalation |

The fresh audit reports a healthy authenticated gateway/Mattermost connection,
both host release markers behind desired source, a deferred NAS backup service,
and failing direct STT readiness. Seven selected communication helpers and four
safety files match workstation source; installed speech source instead matches
the newer origin/master. The native n8n-status credential is absent even though
the operator MCP can inspect workflows. Preserve the Telegram mask and current
worker state. Do not use a full bootstrap or broad host deployment to make these
inventories look equal. No real task or channel message was sent in this audit.

## Task and scheduling ownership

| User request | Authoritative lifecycle | Atlas responsibility |
| --- | --- | --- |
| Answer/research/preparation | Native root session and bounded specialist runs | Own the outcome, retain step references, review evidence and return the final result |
| Coding change | Existing broker/worker ledger | Submit only through granted tools, track actual state and return the reviewable result; do not infer completion from worker activity |
| Chore, deadline or reminder | Existing owner or explicitly shared Family task store | Record through finite tools and confirm actual reminder/delivery status |
| Existing daily/weekly household automation | Its existing n8n workflow or timer | Present status and propose scoped changes without creating another schedule |
| New repeated research/watch | Reviewed routine declaration; one qualified backend | Propose source, show next run/history and dispatch bounded steps through Atlas |

One-off task records, reminders, occurrence receipts and execution state are
durable application data outside Git; creating an ordinary reminder does not
require a commit. Managed routine definitions, reusable skills, permissions and
workflow changes remain reproducible source. A due date alone never schedules
a reminder, and a saved reminder does not execute a research or coding project.

## Acceptance workloads

Freeze synthetic fixtures and a human scoring rubric before implementation.
The initial product must complete these vertical workflows in Danish and English:

1. **Research handoff:** ask Atlas for a dated family-holiday shortlist; Lyra
   supplies links, full party assumptions, price basis and uncertainties. Missing
   availability is explicit; there is no booking or invented matching quote.
2. **Background follow-up:** request a read-only price watch with an explicit
   expiry, threshold and destination. It survives a service restart and reports
   meaningful changes, completion, failure or required input without unchanged
   status spam. New persistent routine configuration follows source review.
3. **Household collaboration:** prepare meals from synthetic stock and offers,
   then make an expressly requested shopping-list edit through the owning tool.
   Prove one intended write despite duplicate delivery; Family sees only shared data.
4. **Coding handoff:** prepare a patch in a disposable allowlisted test project,
   run independent checks and return a reviewable result. Publication remains a
   separate exact-result approval; proposal completion is not deployment.
5. **Computer task:** complete a multi-page workflow in a synthetic account on a
   registered test site, pause for owner sign-in when needed, return an artifact
   and stop before any unapproved external effect.
6. **Reuse and voice (later):** turn a successful workflow into a reviewed routine;
   start a permitted job by Danish voice, interrupt a spoken response, reconnect
   and obtain the same job's status without starting it again.
7. **Reminder and proactive follow-up:** create one owner-private reminder and
   one explicitly shared Family reminder with synthetic content. Confirm their
   distinct destinations, revision/idempotency receipts and cancellation. For a
   bounded read-only watch, unchanged findings stay quiet, a threshold change
   notifies once and expiry stops all future dispatch.

For each workload record completion, unsupported steps, operator interventions,
tool failures, elapsed time, queue time, inference calls and CPU/RAM/GPU use.
No prompt, recording, screenshot, credential or private result enters production
logs or Git. Retain necessary test content only in a separate access-controlled
qualification store with a declared expiry; commit sanitized aggregates.

## Ordered implementation

Each package is a separate reviewable change with focused tests and a runbook.
The proposed paths below are design targets, not existing commands or interfaces.
Create the required architecture issue before implementing a new runtime or
trust boundary, and add an ADR for any accepted decision change as required by
[CONTRIBUTING](../CONTRIBUTING.md).

### 0. Establish the actual baseline and recovery gate

Implementation is tracked in the [November 3 execution receipt](operations/openclaw-recovery-upgrade-2026-11-03.md).
The attended backup admission/drain and guarded full-team recovery changes are
published. The isolated [native recovery verifier](operations/openclaw-recovery-verification.md)
adds actual state/database/conversation and authenticated gateway checks to the
existing archive, PostgreSQL and VM boot gates. Its source/tests do not themselves
establish a successful live restore; use the corresponding execution evidence.

The same-day recovery implementation now has actual acceptance evidence:
fresh encrypted NAS snapshot, archive/SQLite checks, four PostgreSQL imports,
both isolated VM boots, and native Atlas startup from the restored disk with
retained conversations, twelve roles, required plugins and original policy.
See the execution receipt for the exact published lifecycle revisions and
hardware-fixture corrections. External channel/inference delivery and global
host synchronization remain separate claims. Scheduled backups retain their
conservative idle guard; reliable unattended admission needs its own scoped
maintenance-window rollout before expanded unattended work.

The November 3 [audit](operations/openclaw-atlas-audit-2026-11-03.md) completes
the bounded read-only inventory. Remaining work is to inspect current broker
activity, inventory all retained native databases/patch receipts and exact
channel bindings, establish timer boot ownership, and retain the accepted
OpenClaw recovery evidence. Preserve the running worker; an old stopped-worker receipt is
not authority to start, stop or replay it.

Reconcile selected installations against published source, including newer
speech helpers and known overrides, before choosing an apply revision. Use the
owning recovery/observability lifecycles to investigate backup deferrals and
direct STT readiness separately. Do not clear failure flags to pass the gate.
Synthetic candidate/fixture development can proceed without declaring those
production checks passed.

Exit: every capability has verified/configured/disabled/unavailable/unverified
status, an owning lifecycle and evidence date. A current consistent encrypted
backup and isolated OpenClaw restore are mandatory before state migration or
expanded unattended production work; a running timer or old restore receipt is
insufficient.

### 1. Qualify the managed upgrade and native lifecycle

Prepare a source-owned candidate for NemoClaw `v0.0.140` at
`88fe14f3fb9a0bf72997cce19ec88b5b2ee08391`, OpenClaw `2026.9.2` and
OpenShell `0.0.116`. Resolve immutable amd64 image/provenance and retain the
existing management, credential-custody, inference and network boundary. No
image digest has been qualified by this plan. Select a separate registry,
gateway and state volume, with synthetic data and no production channel tokens,
browser sessions, schedules or model credentials. Inference qualification needs
a separately limited candidate credential and budget.

The change must account for shared tuple pins in `config/hermes-release.json`
and `scripts/setup-hermes-guest.sh`, `config/openclaw-nemoclaw.env.example`,
the OpenClaw supervisor's compiled-module identity and managed recovery declarations,
`scripts/verify-openclaw-recovery.py` and its guest probe's old-image/core gates,
and `docs/openclaw-nemoclaw.md`, plus a reviewed candidate/upgrade lifecycle. Do not
run the general installer or change only a recovery image constant. Preserve
the retired Hermes state; a shared template name does not authorize recreating it.
The [managed-candidate artifact/lifecycle research](research/openclaw-managed-candidate-qualification-2026-11-03.md)
pins the available amd64 workload and its provenance metadata. Candidate source
and runtime checks must remain separate from the production tuple declarations
until qualification passes.

Build a candidate compatibility matrix for the actual installed Mattermost
plugin, native child-result/yield/queue patches, safety hooks, finite MCP grants,
agent-office plugin, model serialization, team policy and state restoration.
Use `setup-openclaw-child-results.py`, `setup-openclaw-yield-acknowledgment.py`,
`setup-openclaw-llm-queue.py` and `setup-openclaw-mattermost.py` as migration
inventory, not version-independent installers. Reproduce each old failure on the
new tuple; retire an obsolete patch or port it against a new exact preimage and
add the relevant regression. Keep current refusal guards intact.

Separately resolve a supported managed recipe for core `2026.10.10`, commit
`7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be`. NVIDIA's reviewed build pins
`2026.9.2`; `2026.10.10` is a feature candidate, not a drop-in override. Any
reviewed extension needs the architecture issue/ADR process. Assess its
matching Mattermost/plugin API and schema support change (agent 19 → 27,
state 15 → 22) on synthetic stores first. Node `24.18.1` satisfies its engine,
but a pinned supported/security-updated image runtime also needs evaluation.
Use exact tagged documentation: moving heartbeat/restart guides already differ
from this release. Never run `doctor --fix` or a migration against production
as a compatibility probe.

Exit: select the newest tuple that passes all required paths and rollback, or
explicitly retain `2026.9.1`. Document native recovery/automation gaps per
candidate. Do not claim `2026.9.2` has `2026.10.10` features, or that shared
schema numbers alone prove safe rollback. Production upgrade remains a separate
reviewed deployment after phase 0 recovery gates.

The November 3 implementation gate now has actual candidate evidence. Its
artifact/config checks and native safety, ingress and yield regressions passed.
The unmodified 9.2 child-result path failed required single-child tail retention;
an exact-hash temporary port passed. The decision is **no-go as-is**, retaining
9.1. Before promotion, port the child installer, official Mattermost gate and
activity hash/path, retire only the new tuple's obsolete yield overlay, and
complete native synthetic rebuild/restart/rollback and integration acceptance.
The [execution receipt](operations/openclaw-recovery-upgrade-2026-11-03.md) records
which gates ran and which remain unverified. This decision does not require
replacing OpenClaw; later Atlas behavior work can use the existing qualified
tuple, with its own acceptance and unattended-work gates.

### 2. Make Atlas a coherent, observable teammate

Refine `config/openclaw-team.json`, `scripts/openclaw_team_policy.py` and the
scoped team/instruction installers. Define a task envelope with authenticated
principal/domain, originating channel/thread, stable request ID, desired outcome,
acceptance checks, owner, deadline and step/tool/inference budgets. Bind it below
the prompt to the native/broker/task-store reference; never use workspace memory
as its execution ledger. A native reference can be the task ID if its lifecycle
meets the tests; do not create another database just for a nicer label.

Atlas owns a task until it returns a result, a concrete blocker, cancellation
or expiry. It plans bounded steps, spawns a named specialist with isolated
current-task context, yields with one short acknowledgement, reviews the child
evidence and decides the next step. Example: Ceres reads permitted stock →
Hestia prepares meals → Vega performs only the expressly requested list edit →
Atlas returns the evidence and result in the original thread. Specialist output
is evidence, never new authority. Keep direct specialist contact.

Retain one child at a time, depth one and denied `sessions_send` initially.
Pass tasks between roles through Atlas; unrestricted peer messaging or bot
message loops are unnecessary. Bound repeated delegation and count both calls
and tokens. Only evaluate two concurrent read-only children after shared
inference/speech load, fairness and cancellation tests pass.

Project accepted, queued, working, waiting-for-owner, completed, failed,
cancelled and expired states into the original Mattermost thread, preserving
underlying states and separate delivery outcome. Add finite owner-scoped
status/list/cancel/reconcile views through `scripts/openclaw-communication.py`,
`scripts/openclaw_tasks.py` and existing notification renderers as appropriate.
These are proposed controls; existing `/task` addresses broker tasks only.
Cancellation must fence future steps and report any already committed effect.
An ordinary steering reply attaches to the current task; it does not silently
resubmit it. Idle/finished sessions must not resume as fresh work.

Exit: Danish/English research and household handoffs return one final reply,
with usable evidence and no duplicate acknowledgement, invented completion,
cross-thread disclosure or unrelated memory retrieval. Extend the existing team,
child-result, yield, communication and recovery test suites.

### 3. Add bounded durable background execution

Qualify native accepted-input persistence, parent/child completion delivery,
restart reconciliation, cancellation and queue fairness on the selected tuple.
Exercise graceful restart, crash, provider outage, duplicate admission, lost
acknowledgement and cancellation during a tool call. Recovery must inspect
native and external receipts before continuing; a failed child is not permission
to rerun its last action. The exact release may stop a child and require parent
reconciliation rather than transparently resume it.

Reuse `deploy/codex-worker/broker.py` for coding and
`scripts/openclaw_household_tasks.py` for chores/reminders. For general
research/preparation, start with native run/session state and a thin finite
projection if required. Only propose `scripts/openclaw_jobs.py` if a written
gap matrix demonstrates missing durable admission, deadlines or recovery that
cannot be supplied by the native lifecycle. Any such controller requires its
own issue, state schema, idempotent installer and rollback; it is not yet a
selected architecture.

If needed, the controller owns job/step events, leases/fencing tokens, attempts,
effect receipts, artifact references, cancellation and delivery state. Lease
expiry never authorizes replay of an uncertain write. Resume only provably
replay-safe read/preparation steps; hold unknown effects for reconciliation.
Approvals bind exact parameters/preconditions and expiry, outside agent tools.
Do not claim exactly-once external effects.

Start with one active inference turn and a bounded fair queue so routines do
not starve chat or n8n. Provider loss leaves work queued or visibly expired,
with no private cloud fallback. Carry deadlines across retries, restart and
fallback decisions; never reset the task budget by spawning another child.

Exit: the background watch and coding fixture retain stable task references,
bounded work and truthful status through those failure cases. A completed job
with failed delivery remains visibly undelivered. A coding process merely
running is not acceptance; inspect its ledger and use only an allowlisted
synthetic project with separate execution/publication authority.

### 4. Make memory useful and controllable

Implement missing user controls from `docs/personal-data-and-memory.md` through
the authenticated trusted layer: inspect, confirm, correct, expire, export,
delete and explicitly share. Keep per-task evidence in its originating job/thread;
retain only allowed preferences in long-term memory. Use source references,
versions, expiry and deletion tombstones, including derived indexes/caches.
Bring existing Markdown preferences across only through a reviewed importer;
do not import all conversations or other agents' workspaces.

Exit: a confirmed preference survives restart, a correction replaces it, deletion
prevents retrieval and resurrection after restore, and Family/partner/work
denial tests pass. Memory never changes authority or schedules work by itself.

### 5. Qualify reusable routines

Add a proposed `config/openclaw-routines.json`, schema and idempotent builder
`scripts/setup-openclaw-routines.py`. Start with an inventory referencing existing
n8n IDs/timers; it must not recreate their schedules. Use chat to propose a named
routine with owner/domain, versioned skill, bounded inputs, trigger, IANA
timezone, destination, freshness rules, budgets, expiry and approval boundary.
Show its source diff, backend, next run and whether it is inactive/proposed.
Activation requires published source through the owning lifecycle. No generic
native cron/exec tool or n8n administrator token is granted to Atlas.

Choose one backend per routine. Retain existing n8n morning weather, tomorrow
preparation and travel/news schedules. Qualify native automation only for an
uncovered assistant watch/research routine on the selected runtime; enabling
native cron is an explicit protected-policy change. Keep all specialist ambient
heartbeats disabled. Native features remain conditional if only `2026.9.2`
qualifies; a small finite n8n patch builder is preferable to pretending that
later native behavior exists. Use n8n MCP for live workflow inspection and
publication, not an alternate raw workflow-write API.

Extend `scripts/openclaw_integrations.py` and its finite `automation_status`
projection with backend-owned metadata only. Provision any missing native n8n
read credential by the documented external custody path and minimum required
scope; operator MCP access does not transfer to Atlas. Where the platform cannot
scope a credential narrowly, keep it in a trusted adapter that enforces the fixed
workflow allowlist. No private report payloads enter execution logging. Expose
list, test, next-run, history, pause and resume with distinct configuration,
execution and delivery outcomes. Pausing must stop future dispatch immediately;
persist the desired pause through source before deployment can re-enable it.

Use stable occurrence IDs, explicit no-overlap behavior and bounded catch-up.
Test both Europe/Copenhagen DST transitions, ambiguous/nonexistent local times,
missed slots, duplicate events, expiry, outages and cancellation. A test run uses
synthetic input/destination and never invokes an existing production routine
with real messaging/actions just to inspect it.

#### Reminder completion

Update `deploy/openclaw/skills/tasks-and-reminders/SKILL.md`, relevant Luna/Orion
instructions and task/delivery helpers together. Clarify who, when, destination
and sharing only when missing; use explicit RFC3339 instants and stable UUID
request IDs on writes. Luna's current source instruction includes a timed-reminder
Family default while other clauses require explicit sharing; remove that
ambiguity in source and enforce destination/scope below the model. Default
private owner tasks to an admitted private destination; Family delivery requires
explicit sharing. If no private destination is configured, ask for the
destination rather than exposing it to Family. Reuse existing task stores and
revision checks; do not schedule a second native reminder for the same task.

Keep due time separate from reminder time. Confirm the saved receipt and next
occurrence; report scheduled, delivery accepted, delivered, failed or uncertain
according to the owning transport. Home Assistant acceptance is not phone
receipt. Prove that private details remain private, shared projection is explicit,
duplicate requests create one intended reminder, cancelled reminders do not fire,
and overdue/recurring tasks do not create notification storms. Preserve the
supported none/daily/weekly/monthly recurrence until a tested extension exists.
Verify owner/Family timer boot ownership as well as current active state.

#### Proactive follow-ups

Begin with a read-only expiring watch and existing unfinished-task follow-up,
then reuse existing morning/tomorrow briefing coverage. Each follow-up records
the owner's authorized goal, data sources/freshness, threshold, owner/domain,
destination, cadence, deadline/expiry, notification budget and stop condition.
Proposed initial non-urgent quiet hours are 22:00–07:00 Europe/Copenhagen;
confirm those defaults in the reviewed routine configuration before activation.
Do not silently delay an explicitly timed reminder under a watch's quiet policy.

Trigger from trusted observations or the single owning scheduler. Notify on a
material change, completion, failure, approaching agreed deadline or required
owner action; remain quiet for unchanged state. Deduplicate by task/occurrence
and finding version, impose a cooldown, and stop on expiry, cancellation or
revocation. An unavailable source produces an explicit stale/blocked status,
never fabricated fresh evidence. Private findings never expand Family scope.
Proactivity can prepare advice or an exact proposal; device actions, publication,
purchases, messages to others and deployments keep their existing authority.

Exit: a successful one-off research task becomes a reviewed routine with a safe
test, a real scheduled result and separate delivery receipt. A restart preserves
the same watch; unchanged checks stay quiet, a threshold change notifies once,
expiry stops it, and all reminder/privacy/DST cases pass. These are future
acceptance runs, not results of the November 3 read-only audit.

### 6. Expand computer and app work one workflow at a time

Prefer finite APIs/MCP when available. Extend registered-site workers for a
specific unsupported browser workflow before considering generic desktop tools.
Public research, private account sessions and code execution keep separate
credentials, storage and egress. A new interactive computer worker belongs in
the isolated agents workload, with a distinct identity, no host Docker socket
or arbitrary host mounts, bounded resources and a per-job allowlist.

Expose an authenticated owner view/takeover and revoke agent input during owner
control. Preserve private sign-in and MFA handoff. Restrict navigation, downloads,
uploads, clipboard and artifacts; treat page content as untrusted. Public-site
instructions cannot expand tools or exfiltrate private data. A screenshot loop
requires a qualified local vision route; DOM/structured tools remain usable for
the current text-only route. Purchases, payments, messages, publication and device
actions use their existing independent authorization paths. No financial
finalizer is presently qualified by the site framework.

Exit: workload 5 passes on a synthetic site, including owner takeover, expired
login, revocation, malicious page instructions, private-address denial and
crash/uncertain-effect handling. Qualify each real site separately; a working
viewer or fixture does not establish account login or booking readiness.

### 7. Add demonstration teaching and conversational voice

Teaching: capture only an explicitly started, bounded session on approved
non-secret pages. Exclude sign-in, tokens, private form fields and unrelated
windows. Convert observed steps into a parameterized draft skill with validation,
failure handling and required grants; never grant tools from the recording.
Review the generated diff, test against changed fixture inputs, publish through
the skill lifecycle, then optionally bind a reviewed routine. If capture cannot
exclude secrets reliably, retain written/completed-task-to-skill creation first.

Voice: retain current Mattermost recordings as the fallback. Design a separate
authenticated live session over approved LAN/Tailscale access with local STT,
text inference and TTS, turn detection, streaming, interruption, reconnect,
finite audio buffers and no recording retention by default. Test installed
speech-provider contracts rather than assuming a Realtime API. Measure whether
Spark can host the simultaneous speech/text load without disrupting n8n.
Voice input never confirms a sensitive action solely because transcription
resembles approval. Reconcile current Mattermost-only TTS scope before adding
another native channel.

Exit: workload 6 produces a reviewed runnable draft, handles a changed page
safely, and completes Danish/English live turns with interruption and reconnect.
Measure p50/p95 end-of-speech to first intelligible audio, transcription errors
and false turns. Proposed voice target is p95 below five seconds for short
ordinary turns under the declared mixed load; a miss remains a performance gap,
not permission to add cloud fallback.

## Release gates, sequencing and first change

Order: 0 → 1 → qualified tuple decision → 2 → 3 → 5. Memory controls (4) precede
expanded personalization; computer work (6) precedes visual teaching (7).
Voice can be developed after the model/speech capacity audit independently of
computer work. Do not wait for every feature to release useful increments.

P0 backup/recovery and retained-version runtime repairs are now deployed with
the evidence recorded above. P1 reached a no-go decision for 0.0.140 because
full-team Mattermost credential custody is unsupported. Continue P2 onward on
the retained qualified tuple; reopen the version change only after separately
qualifying that integration. The implementation packages are:

| Package | Concrete source change | Acceptance before production |
| --- | --- | --- |
| P0: recovery and drift | Reconcile selected published/scoped source; use existing backup/restore and observability lifecycles | Consistent current OpenClaw backup/isolated restore; current worker activity and readiness gaps accounted for |
| P1: upgrade candidate | Version/image pins and candidate lifecycle; port/retire exact native/plugin patches with provenance tests | Matching SDK/plugin/config, safety denials, native state migration and rollback pass on synthetic state; choose compatible tuple |
| P2: Atlas ownership/handoffs | Team manifest/policy, scoped instruction installer, native task projection and communication rendering | One request → bounded specialist chain → one final result in original thread; status/cancel/steering work without replay |
| P3: background reliability | Reuse native lifecycle; add only demonstrated missing projection/controller behavior | Restart/crash/duplicate/provider outage/unknown-effect and failed-delivery cases pass with stable references |
| P4: reminder semantics | Tasks-and-reminders skill, Luna/Orion instructions, task-store/delivery policy and timer ownership | Private/shared destination conflict removed; timed/zone/recurrence/cancel/DST and delivery checks pass |
| P5: routines/proactivity | Routine schema/patch builder, finite status facade, metadata receipts and quiet/change policy | Existing schedules preserved; one expiring watch survives restart, stays quiet unchanged, notifies once and stops on expiry |

P2/P4 fixture and instruction work can proceed against the retained pinned
runtime, followed by their own acceptance and deployment. P3/P5 behavior must be
evaluated on that same tuple. Memory, computer UI and live voice are
later packages; they do not block a useful task/reminder release.

Extend the existing tests rather than mirroring prompts: `openclaw-team-test.py`,
`openclaw-team-household-test.py`, `openclaw-team-recovery-test.py`,
`openclaw-child-results-test.py`, `openclaw-yield-acknowledgment-test.py`,
`openclaw-communication-test.py`, `openclaw-household-tasks-test.py`,
`openclaw-family-projection-test.py`, location/delivery tests and native queue
tests under `tests/`. Add candidate-backed plugin/SDK and schedule/recovery tests
where existing fixtures cannot verify the real contract.

For initial product acceptance, propose at least 20 scored runs of supported
workflows with at least 90% meeting their declared outcomes, plus all destructive
boundary/recovery cases passing. Record excluded/unsupported workloads; never
hide them from the parity matrix. Expand to a 72-hour bounded mixed-load soak
with outage/restart injections before unattended rollout. These thresholds are
proposed project gates, not evidence of current quality or full Grok parity.

Every implementation PR runs focused tests, `git diff --check` and
`./scripts/validate-repository.sh`; Nix changes additionally require the CI
flake check. Review and publish only intended files. Normal releases use
`./scripts/homecompute deploy <home-core|home-spark|all> --revision FULL_SHA`;
scoped OpenClaw changes use their documented guarded installer from that
reviewed release. New components need an idempotent, hash-bound installer and
rollback runbook before live use. A model change edits canonical catalog/routes
first and uses `python3 scripts/model_registry.py render` and `check`.

Before apply retain the prior release and a consistent private state backup.
Version database migrations and test restoration; code rollback alone cannot
reverse a migration. To roll back jobs/routines, stop new admission and dispatch,
reconcile in-flight effects, restore the supported code/state tuple and verify
before resuming. Do not delete receipts, offsets or pause/override markers.

After each authorized rollout inspect `status`, `services`, `doctor`, `drift`,
installed image/source/config hashes and the actual authenticated user path.
Keep a capability matrix of source revision, lifecycle, exercised workload,
verification receipt and remaining gaps. Report synthetic evidence, live
acceptance and unverified checks separately. A matching revision marker alone
does not establish synchronization or Grok Bot parity.
