# Atlas task ownership and unfinished-task follow-ups

The selected runtime remains NemoClaw 0.0.129 / OpenClaw 2026.9.1 /
OpenShell 0.0.116. This change uses native session-tree tasks and the existing
household task/reminder stores. It adds no general execution engine, model,
channel, credential, unrestricted messaging or native cron grant.

Atlas loads `task-ownership` for multi-step work, progress, cancellation,
schedules and follow-ups. Native task IDs stay authoritative for specialist
work; broker references stay authoritative for coding jobs. Atlas passes bounded
steps through named isolated specialists and checks their evidence before
returning a result in the original thread. Status and cancellation use native
`subagents` list/cancel within that session tree. Corrections do not silently
resubmit running work. The four-step instruction budget is separate from the
runtime's serial/depth-one/300-second child limits; it is not a durable token
quota or proof that every model follows the instructions.

Luna's conflicting Family default is removed. Private owner reminders use the
existing private Tasks destination; explicit shared records use Family. A new
`follow_up_at` plus `follow_up_expires_at` on an existing task authorizes one
unfinished-task check, expiring within thirty days. Existing task timers own the
check and delivery. Completed/cancelled tasks and cleared or expired watches do
not dispatch. Changing only a title never resends a delivered watch. A watch
remains quiet from 22:00–07:00 Europe/Copenhagen; explicitly timed reminders
retain the time requested, including overnight.

Follow-ups use a `purpose` discriminator in the existing reminder receipt table
and stable occurrence hashes. Execution/task state remains separate from
`follow_up_delivery` and ordinary `reminder_delivery`. A lost delivery receipt
is uncertain and reconciled by its existing marker, never sent again merely
because it was not observed. Phone admission remains distinct from device
receipt. Follow-ups always use the selected store's fixed Mattermost room;
a task's phone setting cannot redirect a private follow-up.

`config/openclaw-routines.json` records existing n8n-owned coverage. The
idempotent `scripts/setup-openclaw-routines.py` renders that inventory into
Atlas's skill; `--check` verifies it. It does not install or activate schedules.
November 4 MCP inspection confirmed morning, tomorrow preparation, technology
news and travel routines active with published versions matching their drafts.
Native cron and ambient heartbeats remain disabled. Arbitrary new recurring
research/action routines still require their own source-owned proposal and
qualified backend deployment; Atlas must report that boundary truthfully.

## Validation and scoped deployment

Use the clean isolated worktree, publish reviewed source to `origin/master`,
and clone/check out that exact full SHA into root-protected
`/srv/homecompute/releases/FULL_SHA` on Core and the agents VM. Do not use a
broad host release or the full communication installer for this scoped change.

Run focused tests and the repository validator:

```bash
python3 tests/openclaw-task-followups-test.py
python3 tests/openclaw-household-tasks-test.py
python3 tests/openclaw-location-tasks-test.py
python3 tests/openclaw-workflow-skills-test.py
python3 tests/openclaw-team-test.py
python3 tests/openclaw-atlas-deployment-test.py
./scripts/validate-repository.sh
git diff --check
```

The default lifecycle command plans without changing application state. Core
runs as root; guest commands run as the existing `hermes-operator` (UID 1000).
Review each plan's source hash, affected modules/identities, preserved counts,
identity, timer boot links and exact `plan_sha256` before its apply:

```bash
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py core --revision FULL_SHA
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py core --revision FULL_SHA --apply --expected-plan-sha256 CORE_PLAN_SHA256
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py guest --revision FULL_SHA
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py guest --revision FULL_SHA --apply --expected-plan-sha256 GUEST_PLAN_SHA256
```

Core requires exact baseline/target modules, no in-flight communication or
reminder dispatch, existing private task ledgers and both source-owned timer
boot links. It pauses only the two timers and previously active adapter/location
services, preserves private SQLite/code backups and task/request/event counts,
installs four source modules, applies the additive discriminator migration and
resumes previously active units. It leaves configuration, credentials and unit
files in place. The normal communication/location installers also declare the
new helper for reproduction on a rebuilt host.

The guest requires the original exact managed tuple, full twelve-role roster,
ready/idle gateway, serial depth-one delegation and existing recovery fences.
The retained image owns native state as UID/GID 998; this is verified separately
from the host operator UID 1000 before staging or native file operations. Native
commands also set the image’s sandbox home explicitly; Docker otherwise retains
root’s home even after selecting the sandbox UID.
It stages tracked public source outside native durable state, uses the existing
workflow-skill and identity installers, and compares the full configuration with
only role skill assignments normalized. Only Atlas gains `task-ownership`;
existing workflow assignments and all twelve role identity blocks are refreshed.
Native MCP restart reloads the updated tool schemas. Required plugin/account
health and actual credential resolution with all sixty-nine tools are checked.
The watchdog is paused during the guarded reload and resumed afterward. Its
private transaction fences a failed deployment against automatic replay.

After apply, compare installed bytes, actual gateway/Mattermost/MCP behavior,
timer activity and boot links. Run `homecompute status --json`, `services`,
`doctor` and `drift`; report inaccessible hosts separately. A general host marker
is not the revision of this dedicated lifecycle. Use synthetic native sessions
and task/destination fixtures for qualification; no production routine should
be executed merely as an inspection probe. Unit tests are not real phone or
Mattermost delivery acceptance.

## Rollback and partial outcomes

Retain the returned Core and guest backup paths. The guest's private transaction
and after images identify the completed change. Plan rollback with the same
published source; it refuses later configuration/workspace/code edits:

```bash
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py guest --revision FULL_SHA --rollback GUEST_BACKUP
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py guest --revision FULL_SHA --rollback GUEST_BACKUP --apply --expected-plan-sha256 REVIEWED_ROLLBACK_SHA256
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py core --revision FULL_SHA --rollback CORE_BACKUP
python3 /srv/homecompute/releases/FULL_SHA/scripts/deploy-openclaw-atlas-tasks.py core --revision FULL_SHA --rollback CORE_BACKUP --apply --expected-plan-sha256 REVIEWED_ROLLBACK_SHA256
```

Disable/reconcile saved active watches and pending/sending/uncertain follow-up
receipts before rolling Core code back; its guard requires that reconciliation.
Do not restore old SQL over newer tasks. The additive column may remain after
code rollback. Guest rollback restores only captured identity/skill/ownership
files and skill-list configuration, retaining current native MCP credentials.
A failure before complete after images exist needs bounded operator inspection
of the retained before images and transaction stage, rather than blind replay
or guessing a native outcome. Preserve uncertain external effects and delivery
receipts through either rollback or forward reconciliation.

Implementation and deployment evidence is recorded in
[the November 4 receipt](openclaw-atlas-tasks-2026-11-04.md).

For a failed preparation that provably changed no native files or configuration,
`guest --reconcile-unmodified GUEST_BACKUP --revision FULL_SHA` creates a bounded
reconciliation plan. Its hash-bound `--apply` compares the entire config and all
captured identity/skill/ownership before images again under the lifecycle locks
before marking only that failed preparation reconciled. Any changed file,
identity/generation, transaction stage or config refuses this path and requires
recovery. Private failure output is retained per operation rather than depending
on the watchdog's rotating diagnostic log.


## Context allowance for full-team continuation

The full native team/tool prompt can crowd the original 32,768-token model
context declaration. A November 4 real handoff received the child's correct
answer, then ended with a length stop and only one output token. The actual
provider path was checked: Core `automation-moe` maps through its SSH forward
to Spark's text admission endpoint, whose `/v1/models` advertises 262,144 tokens
for the selected `automation-qualification` model. Model identity, output limit,
reasoning configuration and serving process stay unchanged.

`configure-openclaw-task-context.py` plans an allowance of 65,536, from the
source-owned native declaration and managed env template. It verifies the
retained model and 4,096 output limit, exact source/config/env hashes and idle
native identity. Its hash-bound apply retains private model/env before images,
changes only the context allowance, uses native validation/config lifecycle,
updates the matching supervised env setting, reloads via native MCP and verifies
full authority, connections and all sixty-nine tools. A failed transaction is
fenced. This is a current-version parameter qualification, not the blocked
managed version upgrade.

```bash
python3 /srv/homecompute/releases/FULL_SHA/scripts/configure-openclaw-task-context.py --revision FULL_SHA --verified-backend-context 262144
python3 /srv/homecompute/releases/FULL_SHA/scripts/configure-openclaw-task-context.py --revision FULL_SHA --verified-backend-context 262144 --apply --expected-plan-sha256 REVIEWED_CONTEXT_PLAN_SHA256
```

The explicit capacity argument records the operator's actual route/capacity
verification; it is not discovered or guessed from an old model catalog.
Rollback uses the same plan/apply command with `--rollback CONTEXT_BACKUP` and
its new plan hash, retaining current MCP credentials and refusing later authority
changes. Reconcile any retained failed context transaction before a new apply.
Rollback to the smaller allowance also needs its desired declarations restored
in source; code rollback does not erase task history.

Run native probes through the managed execution route, whose gateway transport
is required; a direct Docker CLI uses the dashboard port and fails admission:

```bash
NEMOCLAW_GATEWAY_PORT=9123 ~/.local/bin/nemoclaw agent-openclaw exec -- python3 PUBLIC_SOURCE_STAGE/scripts/verify-openclaw-atlas-handoff.py --output NEW_PRIVATE_OUTPUT --language en
```

Use `--language da` with a separate new output for the Danish control. The
verifier checks the actual fresh parent and child transcripts, one accepted
isolated researcher run, one yield acknowledgment and a reviewed terminal final answer.
Isolation must be confirmed by the native acceptance receipt; an omitted context
argument is accepted only when that receipt resolves it to `isolated`.
It preserves failed runs, including terminal length stops; it sends no channel
message. A matching RPC success alone is insufficient.


## Owner-private destination and audience enforcement

The former `tasks → Family` alias is shared UI history, not a private delivery
boundary. The root-only `setup-openclaw-owner-task-delivery.py` lifecycle reads
`config/openclaw-task-delivery.json`, verifies retained owner/partner/team/bot
identities, and creates or verifies the fixed private `owner-tasks` room. It adds
only the existing delivery bot and owner; existing Family memberships/messages
are preserved. It installs the reviewed Core task modules through their scoped
lifecycle while timers are paused, writes service-owned destination configs
with explicit human/bot allowlists, verifies authenticated destinations and
resumes the intended timers. No new account, token or message is created.

```bash
python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-openclaw-owner-task-delivery.py --revision FULL_SHA
python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-openclaw-owner-task-delivery.py --revision FULL_SHA --apply --expected-plan-sha256 REVIEWED_DESTINATION_PLAN_SHA256
```

Owner delivery requires exactly its admitted human, the existing delivery bot,
and only admitted bots before each delivery pass. A joined partner or unknown
bot holds delivery. Family verifies its approved two-human audience separately.
Legacy owner configs without an explicit audience fail closed. The household
all-except-Systems reconciler excludes the declared owner room; the historical
organizer action that retargeted owner reminders to Family is retired.

Private before-configs and an exact new-room creation receipt remain in the
returned backup. Restore code only after task/watch reconciliation; never
rollback the owner destination to shared Family. A failure keeps owner delivery
paused and retains the safe private room/config for forward reconciliation;
Family's original timer state is preserved. Source-owned maintenance receipts
retain the prior intended owner timer state across an attended privacy repair.
The notifications room does not add a new agent ingress grant: manage tasks
through the existing private Atlas/Luna conversation paths.
