# OpenClaw toward a Grok Bot experience

Date: 2026-10-29. Status: proposed implementation plan; no runtime, provider,
credential, schedule, model or deployment change is authorized by this document.
Baseline reviewed: `24cd9602b18127c4dbf825d2be8fbe103e56f562`.
Evidence is repository source and dated rollout receipts, not a fresh live audit.

Follow-up: the [2026-10-29 baseline and runtime diagnostic](benchmarks/openclaw-rakazo-baseline-2026-10-29.md)
now records live read-only checks and a synthetic local-model comparison.
It does not qualify the complete Rakazo product or apply OpenClaw optimizations.

## Recommendation and product decision

Keep the current OpenClaw deployment while qualifying the missing end-to-end
workflows. Evaluate Rakazo before building a custom computer interface, routine
editor or demonstration recorder. Rakazo appears closer to the requested product
experience; whether it is better for HomeCompute depends on local-model quality,
recovery, resource use and preservation of household boundaries. See the
[Rakazo assessment](research/rakazo-assessment-2026-10-29.md).

Rakazo is a separate candidate application/runtime, not an established OpenClaw
frontend. Do not run two owners of the same jobs or copy their credentials,
memories and browser sessions between them. Preserve the trusted HomeCompute
service interfaces whichever assistant wins the comparison. If Rakazo wins,
revise this plan and record the architecture decision before migration; do not
spend the remaining phases duplicating its interface in OpenClaw.

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
| Teammates | [Team manifest](../config/openclaw-team.json), specialist instructions and isolated delegation | Consistent personality, reliable handoffs, visible ownership and qualified concurrency |
| Background execution | [Coding broker](../deploy/codex-worker/broker.py), [task feed](../scripts/openclaw_tasks.py), native turn lifecycle | General multi-step research/preparation jobs with bounded recovery; coding worker availability must be checked separately |
| Everyday tasks | [Household task store](../scripts/openclaw_household_tasks.py), reminders and separate Family projection | Unified presentation; a reminder record must not pretend to execute a project |
| Research | [Protected browser and search fallback](openclaw-tools.md), [specialist integrations](openclaw-integrations.md) | Broader qualified source coverage, complete evidence and multi-step acceptance |
| Apps/computers | Finite site operations, owner-only sign-in viewer and registered account grants | Interactive task computer, explicit action permissions and owner takeover; the owner sign-in viewer is not an agent computer |
| Memory | Workspace memory and local embeddings | Full inspect/correct/delete/export controls, provenance and cross-domain deletion checks |
| Voice | [Mattermost local speech](openclaw-communication.md#mattermost-voice-messages) | Streaming, turn detection, interruption and mobile reconnect for live conversation |
| Routines | Managed skills, n8n schedules and independent timers | Natural-language routine proposals, run history, pause/test controls, and later demonstration capture |

Prefer recent receipts to historical overview paragraphs. The
[2026-10-28 integrations receipt](operations/openclaw-integrations-rollout-2026-10-28.json)
records a verified Lyra hotel call, an unverified private-photo path, missing
Geoapify/n8n credentials, a preserved Telegram receiver mask, unresolved broader
host revisions and a failed NAS recovery backup. These are audit inputs, not
claims about today's state. Do not reactivate Telegram or the stopped coding
worker simply to make inventories look complete.

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
6. **Reuse and voice:** turn a successful workflow into a reviewed routine;
   start a permitted job by Danish voice, interrupt a spoken response, reconnect
   and obtain the same job's status without starting it again.

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

Inspect deployed full SHAs, effective native configuration and source hashes,
all role grants, channel bindings/masks, model and speech routes, worker state,
in-flight jobs, overrides, backups and resource headroom. Use
`./scripts/homecompute status`, `services`, `doctor` and `drift`, then the owning
workload checks. Reconcile intended differences through source; preserve unknown
drift and uncertain operations. Verify an encrypted off-host restore before any
durable-state migration or expansion of unattended work.

Exit: sanitized baseline matrix marks each capability verified, staged,
intentionally disabled, unavailable or unverified, and names its owning lifecycle.
Backup failures, roster mismatches and model identity are resolved or remain
explicit blockers. No full bootstrap merely to replace a newer scoped rollout.

### 1. Compare Rakazo before expensive interface work

Use a pinned, disposable, non-production candidate with synthetic data and a
distinct limited model credential. Start with text research/preparation; no
production MCP credential, account login or household state. Review its
supervisor/Docker boundary and egress policy before execution. Register a
candidate through repository-owned deployment source if retained.

Run the same relevant workloads through current OpenClaw and Rakazo using the
same qualified local model and budgets. Record unsupported features as unsupported.
Test local STT/TTS against the existing speech bridge, total context/tool payloads,
session isolation, cancellation, provider outage, restart and duplicate admission.
The reviewed Rakazo speech adapter requests MP3 and rejects our current WAV
response format; qualify an explicit bounded adapter or upstream fix. Mobile
device recognition/voices also need an offline/privacy check. Give any later
MCP connection a new limited backend principal; native OpenClaw's shared token
and role-policy grants are not transferable authority for another runtime.
Actual desktop vision is a separate gate: our current text route must not be
relabelled vision-capable. Qualify a local vision deployment through the model
catalog/routes if required, or report screenshot tasks blocked. Do not silently
substitute a cloud model to make the comparison succeed.

Exit: a written keep/migrate decision with measured workflow results, operating
cost/headroom, required patches and preservation of security boundaries. Hard
failures are cross-principal disclosure, unauthorized effects, duplicate effects
or private cloud fallback. Prefer Rakazo if it materially improves completion and
reduces custom maintenance while meeting those gates; its feature list alone is
insufficient. OpenClaw continues unchanged if the comparison remains inconclusive.

### 2. Make Atlas a coherent, observable teammate

Refine `config/openclaw-team.json` through scoped instruction installers. Give
Atlas a concise, warm Danish/English voice, stable specialist descriptions and
clear progress/result/error messages. Preserve direct specialist contact.
Conversation style is separate from permissions and durable memory.

Project existing job states into the original Mattermost thread: accepted,
working, waiting for the owner, completed, failed or cancelled, retaining the
underlying authoritative state. Include a job reference, current phase and a
usable result. Preserve steering messages without replaying an admitted turn.
Use existing yield/child-result hooks and notification renderers first. Draft a
separate product surface only if Mattermost cannot expose the necessary controls.

Exit: workloads 1 and 3 complete through Atlas with correct specialist/tool use;
no duplicate final replies, fabricated completion, lost handoff or context leak.

### 3. Add bounded durable background execution

Keep coding state in the broker, household tasks in their store, and existing
scheduled workflows in n8n. Add an assistant-job controller only for the uncovered
research/preparation lifecycle; first document why native lifecycle storage
cannot meet the requirement. Candidate source is `scripts/openclaw_jobs.py`,
with finite MCP operations exposed through the existing trusted facade.

Admission binds authenticated principal/domain, original thread, immutable
request ID, outcome, allowed tools, deadline, inference/step budget and policy
version. Store job/step events, lease/fencing token, attempt and effect receipts,
artifact references, cancellation and delivery state outside agent memory.
Delegate only bounded steps; the controller owns recovery and dispatch.

On restart reconcile the original native session before doing anything else.
Resume only a provably replay-safe read/preparation step. An uncertain write or
lost effect receipt stops for reconciliation; lease expiry alone never permits
re-execution. Cancellation fences future steps and attempts to stop current work;
it cannot undo an already committed effect. Approval expires and binds exact
parameters/preconditions. Agents never gain the operator approval interface.

Begin with one active inference turn, bounded queue admission and fair scheduling
so routines cannot starve household chat or n8n. Qualify any higher concurrency
against Spark and speech contention. An unavailable model leaves work queued or
explicitly expired, without automatic cloud fallback.

Exit: workload 2 and the coding handoff pass restart, duplicate request, lost
acknowledgement, cancel-during-tool, timeout, stale lease and provider-outage
tests. Delivery is verified separately from job completion. No claim of exactly-once
external effects; uncertain outcomes remain visible and held.

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

Propose `config/openclaw-routines.json` plus a schema and idempotent patch builder.
Chat can propose a named routine with owner/domain, versioned skill, bounded
inputs, trigger, Europe/Copenhagen timezone, output destination, freshness policy,
budget, expiry and approval boundary. Persistent routine definitions are managed
configuration: generate a reviewable Git change before activation. Individual
run IDs, outcomes and task records are durable application data outside Git.

Choose one scheduler per routine: reuse its n8n workflow or existing timer where
appropriate; never add a parallel native cron/heartbeat. Bind occurrences to stable
IDs, define overlap and missed-run behavior, and prevent catch-up storms. Add
list/test/history/pause/resume controls. A runtime pause can be an immediate stop;
permanent changes must reconcile source before subsequent deployment.

Exit: a successful one-off research task becomes a reviewed routine with a safe
test and verified scheduled result. DST transitions, missed triggers, duplicate
events, source outage, expiry and cancellation have defined tested outcomes.
Existing travel/news schedules are preserved unless explicitly migrated.

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

Order: 0 → 1 → product decision → 2 → 3 → 5. Memory controls (4) precede
expanded personalization; computer work (6) precedes visual teaching (7).
Voice can be developed after the model/speech capacity audit independently of
computer work. Do not wait for every feature to release useful increments.

The first implementation change should add the sanitized baseline/qualification
matrix and shared acceptance fixtures for research and a background read-only
job. Follow with the bounded candidate comparison. This tests the main product
choice before investing in a custom interface or more browser machinery.

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
