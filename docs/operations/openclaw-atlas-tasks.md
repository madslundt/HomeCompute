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
from the host operator UID 1000 before staging or native file operations.
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
