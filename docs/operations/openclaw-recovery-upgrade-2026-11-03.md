# OpenClaw recovery and managed-upgrade execution — 2026-11-03

Status: recovery gate accepted; managed candidate is blocked by Mattermost
credential custody. Retained-version runtime repairs are deployed at `49216e9`;
the post-repair encrypted NAS backup completed successfully. Production retains
NemoClaw 0.0.129 / OpenClaw 2026.9.1.

A fresh encrypted NAS backup, archive integrity checks, four PostgreSQL imports,
both isolated VM boots, and native Atlas recovery from that same snapshot have
passed. The native proof at approximately 20:16 UTC uses published
`e67d0b308720231160267081dc983e94991d4420`: 39 native SQLite databases,
twelve roles/workspaces, retained main conversations, original image/core,
required plugins, authenticated gateway health and policy rechecked after startup.
Its actual accepted receipt was indexed only after matching the snapshot,
manifest and engine proof; the owned VM/scope was collected. The source disk
remained read-only. External channel/model delivery is not established by the
networkless copy. Production retains its original managed tuple.

The owner authorized backup/recovery repairs followed by
managed upgrade qualification and deployment only after qualification. Source is
implemented in the isolated `codex/openclaw-recovery-upgrade` worktree based on
published master; the original dirty workstation checkout is preserved.

## Backup diagnosis and repair

The repeatable live failure signal was `homecompute_nas_backup_ready=0`, with
the backup service in `activating/auto-restart`, exit 75. STT readiness had
recovered by this investigation and was left unchanged. Finite read-only
inspection found no running/claimed/publishing broker work, private browsers,
local VM backup, retained resume journal or recovery marker. The actual blocker
was main-HTTP inference activity on port 18005, also confirmed by the existing
backup diagnostic. The preflight was correctly preserving active work.

A naturally quiet window closed before the backup's second idle check; that
attempt safely deferred without producing a fresh snapshot. No guard or failure
marker was deleted. The existing scheduled retry policy remains unchanged.

Published commit `7db94d6d2f92f5d1115e4189b647b056ab01e10d` adds
[`backup-application-recovery.py`](../../scripts/backup-application-recovery.py).
The attended root operator lifecycle verifies published source and installed
NAS bundle integrity, reuses the finite connection-admission/drain mechanism,
and invokes the existing canonical cold-backup service. It holds only NEW
LiteLLM/main-HTTP connections; active requests drain without termination.
`HC-RECOVERY-DRAIN` is distinct from the speech cutover chain. The hold ends
when LiteLLM is confirmed stopped, before writer resumption and NAS transfer.
The backup service gains no network capability and retains every existing
broker/browser/storage/idle/resumption guard.

The published helper was invoked from its exact home-core release at
`2026-11-03T15:46:35Z`. It passed admission, paused declared resources, removed
its temporary admission hold, and began the cold archive. This paragraph is
not itself restore acceptance. A fresh encrypted snapshot completed at
`2026-11-03T16:12:51.893180Z`; the service exited successfully, its admission
hold/resume journal/recovery marker cleared, and backup/overall platform
readiness became 1. Caddy, LiteLLM, n8n, Mattermost and Omi were healthy again.
The isolated NAS restore began at `2026-11-03T16:12:54Z`.

The restore exposed a second recovery issue: the operator restore/extraction
processes inherited normal disk priority while sharing the VM backing disk.
The guest showed approximately 96% full I/O pressure, blocked native/journal
processes and timed-out authenticated health checks. Idle I/O priority alone
did not relieve the contention. Pausing only the three identity-checked scratch
restore processes dropped guest full I/O pressure below 1%, establishing the
cause. Those processes were then resumed in the automatically collected transient
`homecompute-restore-resume-20261103.scope`, with verified backing-device limits
of 50 MB/s writes and 100 MB/s reads. The source lifecycle now supplies these
limits, idle I/O priority and ownership-scoped timeout cleanup for both restore
and extraction. No persistent production service property changed.

At 16:25 UTC the existing Atlas container was OOM-killed within its existing
8 GiB cgroup. The kernel records identify the gateway process; host-wide memory
exhaustion was not observed. Contention preceded this event, but causation of
the OOM is not established. The supervisor refused automatic replacement of the
twelve-agent production roster using its narrow single-agent recovery policy.
The stopped configuration and private kernel evidence were preserved. The
complete-team recovery described below subsequently restored the original tuple.
No upgrade or agent turn was submitted during recovery.

The published full-team recovery at `d6f589db26f4c20896a7bea6a0488c5b75118e03`
verified and retained complete state before native deletion. Deletion succeeded,
but NemoClaw's final observation failed, leaving its failed transaction fence
and local cleanup records intact. Native not-found and exact-container absence
were independently observed. The source now adds an operator-only continuation
for that exact checkpoint, with backup/identity/patch verification, native cleanup
retry and repeated absence proof before creation. Existing mutation fences stay
in place. Fourteen retained-team and nineteen base recovery tests pass.

Extraction again increased guest I/O pressure despite bandwidth limits. Its
owned scratch scope was frozen while production recovery proceeds. Its cache
had reached approximately 10 GiB; the tightened source and transient properties
now bound memory at 512 MiB high / 2 GiB maximum, writes at 25 MB/s / 100 IOPS
and reads at 50 MB/s / 200 IOPS. Actual `io.max`, memory limits and frozen state
were verified. This is an in-progress incident record; thaw/cleanup and resulting
guest readiness must still be recorded.

The continuation from `d40bb5410866f26216c3bdd77156369b3be018f8` completed
at approximately 16:58 UTC. The replacement was positively Ready on the original
managed image, with complete offline file hashes, retained conversation state
and all specialist workspaces checked by the recovery lifecycle. Independent
readback confirmed twelve agents/accounts and unchanged agents, tools, channels,
cron, hooks and browser sections, the original 8 GiB memory/swap cap and two CPUs.
Public DNS and the watchdog were active. The canonical authenticated gateway
health returned `ok=true`, no plugin errors and connected Mattermost accounts.
This is current-runtime recovery, not upgrade qualification or a synthetic
end-user task/delivery test.

The owned restore scope was thawed at 16:59:05 UTC. The final source limits
are 512 MiB memory high / 2 GiB maximum, 20 MB/s writes / 100 write IOPS,
and 40 MB/s reads / 8,000 read IOPS. The first outer controller timed out
after extraction; the bounded integrity reader subsequently finished, and its
scope was collected. No owned restore process remains frozen. All 93 GB had
already been extracted, so repeating the bulk transfer would add contention
without improving evidence.

Published `9865c00a028f063c275996b6a0072a4592234118` adds a guarded
`verify-extracted` continuation. It requires the latest successful snapshot,
private scratch ownership, exact archive/target manifest agreement and complete
member types/sizes/links before rerunning every original integrity check. It
never overwrites the extraction or invents a successful receipt. Actual
revalidation passed: three qcow2 disks, four compressed dumps and 39 SQLite
databases. Four isolated PostgreSQL imports and both networkless VM boots
also passed; the fresh engine receipt was written at approximately 18:57 UTC.
Media was excluded by the declared backup policy.

The remaining recovery gate boots the restored agents disk read-only with a
synthetic seed, unprivileged one-CPU QEMU, 8 GiB guest RAM, bounded COW/logs
and no NIC or host sharing. The guest checks policy, all native SQLite files
with adjacent WAL/SHM, retained main conversations, exact image/core version,
authenticated gateway health and required plugins. The first attempt failed
before QEMU execution because hard resource limits were raised after dropping
privileges. A regression reproduced that failure; published
`e66542a9ea3d650b6276e6f8ce672966a548daa1` selects limits before dropping
privileges. Seven focused tests and full repository validation pass. The actual
native gate executed and verified the original policy, all 39 native SQLite
databases, twelve workspaces and retained main conversations (89 session nodes,
89 session windows and 1,034 transcript events). Its authenticated gateway check
failed. The gate therefore remains red. Published
`791a7a34e251bf695f4baa5026489e2f48edb215` adds bounded private startup
diagnostics and is being rerun without modifying the production VM or source
disk. Neither data readability nor a login prompt alone is restore acceptance.

The next diagnostic run confirmed Docker/containerd/socket readiness but native
transport refusal, followed by NemoClaw's container-identity refusal during
permission cleanup. This is not authority to bypass identity checks. A later
run terminated with QEMU's `qemu_thread_create: Resource temporarily unavailable`
before it could produce the enhanced startup evidence. Host memory headroom
was approximately 22 GiB, with only one other unprivileged-reader task and a
large process limit. The harness now allows 32 GiB of virtual address space for
QEMU reservations/thread stacks while retaining the 8 GiB guest and 10 GiB
physical-memory cgroup cap. Resource counters will be retained privately.
The cause of the original native startup refusal remains unverified; candidate
execution and production upgrade remain behind the recovery gate.

Production was checked independently during the isolated boot: authenticated
health was good, zero plugin errors, and all eleven enabled Mattermost accounts
were connected. Twelve accounts remain configured; Family is deliberately
disabled. Native cron and heartbeats retain their previous disabled state.

The address-space-adjusted test reached native recovery without another thread
allocation failure. At approximately 19:52 UTC its evidence showed that named
host-gateway recovery succeeded and the retained sandbox start was attempted.
Native phase became Error while the retained container remained exited, and
automatic Error recovery correctly refused the
twelve-agent roster (`agent-roster-refused`). The container was retained, not
missing. No roster or identity guard was bypassed. A later probe revision waits
for native Ready before exec permission housekeeping, captures structured phase
and current-boot error/exit diagnostics, fails promptly on that known refusal,
and rechecks policy after startup. Focused tests cover these gates. The
one-CPU fixture's failure was not accepted; the later capacity-matched successful
native restore supersedes that failed fixture result.

## Managed candidate source

At approximately 20:11 UTC, a fresh remote Git stable-tag and peeled-commit
check still identified `v0.0.140` at
`88fe14f3fb9a0bf72997cce19ec88b5b2ee08391` as the newest stable vendor tag.
This confirms candidate selection, not compatibility or deployment readiness.

The separate [candidate policy](../../config/openclaw-managed-candidate.json)
pins NemoClaw v0.0.140 / OpenClaw 2026.9.2 / OpenShell 0.0.116 and its exact
amd64 workload/config digests. [The offline probe](../../scripts/qualify-openclaw-managed-candidate.py)
checks resource headroom, provenance, Node/core/schema metadata, native dry-run
configuration and a bounded native child-evidence fixture in a disposable
networkless container. It has no production mounts, credentials, ports or
model calls. Ownership-fenced cleanup also handles a timed-out Docker client.
Raw diagnostics remain private. See the [runbook](openclaw-managed-candidate.md).

`production_qualified` remains false. No production CLI/supervisor/image pin was
updated. The [source comparison](../research/openclaw-managed-candidate-qualification-2026-11-03.md)
identifies child-result/queue ports, activity/loader reference changes, and an
upstream yield-warning fix that must be exercised rather than mechanically
reapplying the old overlay. npm source hashes are not yet managed-image runtime
qualification.

## Validation and outstanding gates

Focused attended-backup, application-integrity, NAS continuation, engine and
full-team recovery tests pass. The current native recovery probe has eleven
focused tests; the candidate has nine. Published implementation batches passed
`git diff --check` and full repository validation, including containerized Nix
flake evaluation. Independent concurrent Whisper changes were retained, with
45 CPP and 79 migration tests passing after the rebase. Optional installed-native
runtime suites skip on the workstation and do not establish live qualification.

Fresh status/doctor checks found home-core reachable with no failed units. The
workstation's direct home-spark SSH route timed out; the same repository-owned
status payload through home-core succeeded with no failed units and fifteen
containers. Its deployment marker remained unchanged. Neither host is declared
synchronized with current master; no broad release was applied.

The restored sandbox never advanced its Docker start timestamp after the
original intentional stop; the process did not reach a new startup error log.
The test VM exposed one CPU while the retained production container has a
two-CPU NanoCPUs limit. Published
`e67d0b308720231160267081dc983e94991d4420` exposes two virtual CPUs
while retaining affinity to one host CPU and records the native database's
stored failure conditions. That rerun started the original retained container,
reached DependenciesReady and passed authenticated health/version/plugin and
post-start policy checks. The smaller one-CPU fixture did not represent the
retained two-CPU runtime budget. No production resource or roster guard changed.

Actual candidate execution from published
`2acc66c4131efc34bd029d8379c55fa008ed7aea` completed at approximately
20:40 UTC. The corrected config dry-run passed without changing its synthetic
configuration. Its strict result was exit 1 because the stock child-evidence
gate failed; that failure was not converted into qualification.

| Gate | Actual result |
| --- | --- |
| Immutable manifest/config chain, labels, platform/user | Passed |
| Actual Node 24.22.1, core 2026.9.2, agent/state schemas 19/15 | Passed |
| Three runtime modules match reviewed npm hashes | Passed |
| Native synthetic config validation, no write | Passed |
| Actual SDK loader, safety authorization, sticky veto, failed-hook rejection | Passed |
| Native ingress readiness/abort lifecycle | Passed with an empty in-memory queue; not real Mattermost delivery |
| Upstream yield warning/acknowledgment and quiet-delivery rules | Passed without the old overlay |
| Stock single-child tail evidence | **Failed**; aggregate bound/omission checks passed |
| Exact-hash temporary child overlay | Passed, including retained tail evidence |
| Native managed onboarding/rebuild/restart/rollback | Unverified |
| Real integration paths and mixed-load client acceptance | Unverified |
| Production qualification/deployment | **No-go; not deployed** |

The Docker 29 inspector initially returned a manifest ID where the first harness
expected a config ID. The source now verifies the same two pins through the
byte-preserved public manifest, exact repository digest and descriptor. The
first config probe also used an invalid empty batch; it now proposes a harmless
synthetic name change and proves that no bytes were written. Neither harness
correction changes production or blesses an unknown image.

The immutable candidate image is retained in the guest cache (approximately
0.74 GiB reported image size; over 15 GiB Docker filesystem space remained).
Every owned candidate container was removed. All owned restore scopes and the
backup admission chain are absent. Private snapshot, engine and native receipts
remain outside Git; the accepted native index is mode 0600.

Next implementation order:

1. Port the child-result installer against the reviewed 9.2 before/after hashes;
   the temporary module result is not an installed upgrade recipe.
2. Port official Mattermost's pre-claim gate and the safety activity module's
   new exact runtime path/hash. Retain finite MCP, agent-office and domain policy.
3. Retire the yield overlay only for the new tuple after its native regression;
   preserve the qualified old-tuple patch.
4. Qualify synthetic native 129 → 140 rebuild/restart → 129 rollback, retained
   data/identity, plugin integration, deterministic provider routing and task
   handoffs before any production tuple change.
5. Apply a qualified tuple only through a reviewed published scoped lifecycle,
   with the owning backup/rollback procedure and actual client verification.

The current 9.1 tuple remains the qualified production baseline. Expanded task,
schedule or proactive behavior has not been rolled out. Scheduled backups keep
the conservative idle guard; attended drain now has a reproducible source-owned
path, while unattended admission needs a separate maintenance-window rollout.
Both host deployment markers remain behind current source; scoped recovery and
successful tests do not establish global synchronization.


Final readbacks refreshed status, services, doctor and drift through the
repository CLI, using the approved home-core SSH jump for home-spark. Both hosts
were reachable with zero failed units; existing deployment-marker drift remained.
Production's authenticated gateway health at approximately 20:40 UTC was good,
with zero plugin errors and eleven of eleven enabled Mattermost accounts
connected. Recovery transaction status was complete. Independent checks found
zero owned candidate containers or restore scopes, no backup resume journal or
recovery marker, and no admission chain. No real task turn or channel message
was sent during these checks.

## Retired installer material and native backup — November 4

The isolated native rebuild reached its backup gate and refused unreadable
root-owned operator before-images in `/sandbox/.openclaw` and a retired office
installer staging directory in the historical container layer. It stopped before
native deletion. Production remains on the original tuple. Making these private
configuration copies agent-readable would expose historical credentials.

[`archive-openclaw-operator-artifacts.py`](../../scripts/archive-openclaw-operator-artifacts.py)
now owns their archival lifecycle. From an exact published root-protected guest
release, it checks the managed sandbox identity, generation and image, and selects
only the documented operator backup directories, digest-named office before-images,
retired root- or operator-private office staging, and Python installer bytecode. It refuses
runtime references, links, special files, in-use staging and unexpected changes.
It copies them with original ownership and modes into root-private
`/var/lib/homecompute-openclaw-operator-archives`, verifies every file digest and
metadata, fsyncs the private receipt, and then removes only the verified retired
copies through a root-private quarantine. An interrupted or failed operation
retains its pending fence and requires review; it is never blindly replayed.
Active configuration, office authentication and agent workspaces are not selected.
The whole-VM NAS backup covers the external archive.

The managed upgrade controller invokes this lifecycle only after pausing channels,
reconciling MCP and verifying idle state, before each native rebuild, including
rollback. The networkless restored qualification runs the same helper and must
prove operator archival as an additional acceptance gate. Archival unit tests
cover copy verification before removal, changed-source refusal, pending-operation
fences, runtime references and links. Actual native upgrade/rollback acceptance
is still required before production promotion.

A metadata-only networkless boot confirmed the historical office staging tree
uses UID/GID 1000 (the operator), mode 0700, with bundle/deploy/scripts children.
The initial root-only guard correctly refused before copying/removing anything;
it now accepts only root/root or operator/operator private staging. Agent UID
998, unexpected groups, public staging and non-directories remain refused. The
diagnostic scope was collected; this metadata check is not native upgrade
acceptance.

The next rehearsal refused a copied-archive metadata mismatch before removing
originals. A disposable networkless public-data Docker fixture reproduced root
ownership after `docker cp --archive` on this Docker 29 installation. The
archiver now first verifies exact bytes, modes and types, accepts only original
or root copy ownership, restores the captured numeric UID/GID, and verifies the
complete inventory again before the durable removal fence. Source ownership and
permissions remain unchanged. Focused tests require content/mode/foreign-owner
refusal before any ownership adjustment. The fixture container was collected.

The controller also passes its fenced full sandbox UUID, lifecycle generation
and managed source revision to archival. This permits rollback after a positively
observed replacement but before the final supervisor identity-file commit. The
root helper independently checks that exact named Ready sandbox, registry tuple
and image; partial or noncanonical witnesses are refused. Default manual plans
still use the retained supervisor identity files.

The `8c5629e` rehearsal verified operator archival and complete preparation
preservation (including the unchanged scheduler rows). Native backup then
refused a historical installer checkout containing public setup scripts, before
deletion. A metadata-only read-only boot inventoried all remaining installer
residue together: three browser-policy checkouts, two workflow-skill staging
trees and an office idle-install bundle. Their source directories are not active
configuration paths; current production's read-only archival plan found fourteen
groups / forty-four files and no pending archive.

The archiver now selects only those exact historical staging names. Browser
checkout fingerprints are derived from Git archives of full revisions
`cb9cc4a63f2de48053947e3a0fceb8ca18efad66`,
`07d8f8aa804c613eeb62f28d13efce8547dcb88c` and
`d187627d62be119311de27ebde72246806213f65`. Workflow fingerprints derive
from the seven tracked installer/skill files at
`9609afc633f95603655e4a07a684359a72cec9b3` and
`19131206e8c28a86336a6368cadcf5682a3c1704`, with the historical nested
staging prefix. Classification ignores only bounded CPython caches whose module
has a matching source file; complete copy and removal inventories still verify
those caches byte for byte with original ownership and modes. Extra or changed
source files are refused. The agent-owned office idle bundle remains in native home: its generated art
was not reproduced from the directory-named revision, so it is excluded from
retirement rather than assigned exact source provenance. Its active installed
copy is retained unchanged. All stages must be unused by
processes and runtime configuration, including home-relative references. No
native credential-filter exception was added.

The next attempt stopped on one plugin health error in the paused original
sandbox, before archival/rebuild. All required plugins were loaded, the gateway
reported healthy, and channels remained stopped; the count-only receipt did not
identify the plugin error. Qualification now retains bounded redacted plugin
error details and waits up to 180 seconds for positive zero-error readiness, as
the production verifier already does. Persistent errors still refuse; a running
paused channel is refused immediately. No model task or channel message is
issued by these readiness retries.

The `92013fc` rehearsal passed zero-error paused health, all verified checkout
archival, and complete preparation preservation. Native backup then refused
`openclaw.json.bak.1` before deletion. Core's exact recovery-history ring is
`.bak` through `.bak.4`; these contain earlier credentials that are no longer
supported by native backup even when canonical configuration is valid. They
are now preserved in the same verified external private archive, with the
complete pre-upgrade cold copy retained. This is intentional retirement of
native recovery history, not disposal of cache. Canonical `openclaw.json` and
`openclaw.json.last-good` remain untouched by archival.

Qualification now proves the strict actual native backup before loading the
candidate image. Vendor `backup-all` has no named-sandbox selector, so this
networkless gate first requires that the selected port-9123 registry contains
only `agent-openclaw`, enables `NEMOCLAW_REQUIRE_ALL_SANDBOX_BACKUPS=1`, and
removes the unreachable-backup skip setting. It writes real private native
backup/provider/policy recovery metadata; it performs no image load, rebuild or
sandbox deletion. Its success is an additional required acceptance flag, and
normal native upgrade and rollback still perform their own backups.

## Managed qualification decision and retained-version repair — November 4

The pinned vendor scanner rejects nonplaceholder Mattermost `botToken` values
under current configuration, irrespective of channel enablement. Its managed
messaging registry supports seven other channel types and has no Mattermost
credential handoff. A value-free live classification counted twelve literal
Mattermost token fields. SecretRef objects are also rejected by this scanner;
Mattermost's one default-account environment fallback does not solve a twelve
account setup. This is a compatibility blocker, beyond the retired-file issues.
Native upgrade/rollback qualification has not passed and the deployment policy
remains false, with the blocker recorded in source. No credentials were printed,
no scanner exception added, and no production version changed.

The new `repair` action in
[`upgrade-openclaw-nemoclaw.py`](../../scripts/upgrade-openclaw-nemoclaw.py) is
limited to the retained 0.0.129 tuple. It uses the same exact published-source
guard, reviewed bundle/config hashes and transaction locks. It validates the
current twelve-role authority, canonical health, existing host-program hashes
and idle state, retains private host/config backups, then installs the tracked
watchdog/recovery programs and reconciles the CPU declaration to the actual two
CPUs. Supported native MCP restart repairs only the existing fixed binding.
Whole-config authority comparison, unchanged sandbox identity/generation/image,
five required plugins, enabled account connections and authenticated discovery
of all sixty-nine MCP tools are mandatory. It neither stops/recreates the
sandbox nor changes its version or CLI wrapper. Failed host-code deployment
restores the retained host files; unresolved native MCP fences stay intact for
review rather than replay.

This repair does not need a successful 0.0.140 qualification receipt because it
cannot select that tuple. The `apply`, `resume` and version-rollback paths still
require the complete matching native qualification proof. Native history
archival is not applied independently while the managed upgrade is blocked.

The credential integration must be designed and qualified before reconsidering
0.0.140: supported per-account custody, HTTP and websocket authentication, full
team backup/restore and rollback, no raw credential disclosure, and unchanged
private/shared boundaries. Tasks, schedules and proactive-follow-up work can
continue on the retained core using the ordered Atlas plan.

The subsequent scheduled NAS attempt failed before quiescence because available
space was below source-file sizes plus the mandatory 100 GiB reserve. Storage
inspection found two retained completed local generations, the verified extracted
restore, an extra Restic-restored copy of the same newest archive, and duplicate
public qualification ISOs. The local backup generations and extracted native
data remain retained. Completed qualification now retires only its reproducible
public ISO after owned VM cleanup and durable receipt creation; source/assets
and private evidence remain available. Scratch cleanup and the next actual
backup result must be recorded separately from the earlier restore acceptance.

Scratch retirement at approximately 04:02 UTC released 109,324,787,712 allocated
bytes: thirteen duplicate public qualification ISOs and the extra Restic-restored
copy of the already verified newest archive. The selected accepted native and
application receipts agreed on archive/snapshot; the original local archive and
the complete extracted restore remain retained. The cleanup held the engine
lock, refused open file descriptors, checked private ownership/types and distinct
inodes, and retained a private retirement receipt. Both completed local archive
generations remain present. Available space rose to approximately 240.9 GB; no
headroom guard was relaxed. The previously failed scheduled service still needs
a successful canonical backup run before it can be described as recovered.

## Applied retained-version runtime repairs

Published `49216e9b73d0c1ff36735c13f252cf9e19e8ea02` was applied through
the guarded `repair` action at approximately 04:05–04:08 UTC. Both installed
watchdog/recovery programs match that source. The CPU declaration now equals
the existing two-CPU limit; the CLI wrapper, original NemoClaw/core/OpenShell
tuple, sandbox identity/generation/image and full-team policy remain retained.
The private rollback copies and completed repair transaction are in the owning
operator state directory. No native-history archive or version-change action
was applied to production.

The supported native MCP refresh corrected the existing counter-reference
mismatch. Postconditions passed: unchanged normalized full authority, canonical
gateway readiness, all required plugins, all eleven enabled Mattermost
connections, actual provider credential resolution, adapter registration and
complete authenticated discovery of sixty-nine tools. The new watchdog is
active. Family remains disabled and no real task/channel message was submitted.
This establishes current-runtime/client repair, not feature acceptance for new
schedules, reminders or proactive routines.

After the space cleanup and installed NAS bundle verification, the attended
backup was launched from the same published release at approximately 04:09 UTC.
It passed admission and entered canonical cold preparation with the agents VM
stopped and resume journal retained. The local cold archive completed and
services resumed at approximately 04:48 UTC. Encrypted off-host publication
completed at `2026-11-04T04:53:50.169445Z`, snapshot
`3b8cfb7cd434f1205fbaf3269dd439c8b4b5d01482fbb874b5e7b13351ac9edd`.
The canonical service and attended wrapper exited successfully. Independent
readback verified the exact separate NAS mount and that recorded snapshot in
Restic. Admission chain, resume journal and recovery marker are absent. The new
100,379,699,200-byte application archive and the November 3 generation remain
local; approximately 233 GB is available. This establishes successful backup
publication, not a new restore test: accepted restore evidence still belongs to
the November 3 snapshot described above.

The new watchdog journal records host-gateway recovery, retained-sandbox start
and readiness after the agents VM restart. Independent post-restart checks
confirmed the same sandbox UUID/generation and original managed tuple, both
installed host programs matching `49216e9`, unchanged CLI/DNS hashes, two CPUs,
8 GiB memory/swap and no GPU request. Authenticated gateway health reports
`ok=true`, zero plugin errors, all required plugins and eleven enabled
Mattermost accounts connected; Family remains disabled. The actual MCP probe
again verified provider credential resolution, adapter/policy presence and all
sixty-nine tools without truncation. No real task or message was submitted.

## Final verification and remaining scope

Repository validation passed, including containerized Nix evaluation. Focused
archive, restored-upgrade, deployment and supervisor tests passed; subsequent
media-retirement changes passed their focused checks. `git diff --check` passed.
These checks establish source behavior separately from the live evidence above.

The required `homecompute status --json`, `services`, `doctor` and `drift` were
run after resumption. Core reports zero failed units and healthy affected
containers. Its independently observed general release marker is now
`7df6e792262ac330027b47b188eb9c156eaa68b8`, rather than this scoped repair's
revision; no general host deployment was performed by this lifecycle. Direct
workstation access to Spark timed out, so the four CLI commands returned a
nonzero result with Spark unverified. A supplemental invocation of the same
repository status collector through Core reached Spark: zero failed units,
fifteen observed containers and release marker
`b0e8b106dd79e30d765ddd38970c29649ebfb37b`. Neither marker establishes that all
independently managed workloads match current source. Broad host reconciliation,
authenticated inference/task delivery and Spark model qualification remain
outside this scoped deployment's verification.

The retained-version repairs are deployed; the 0.0.140 version-change gate stays
closed. The next Atlas work remains task ownership, handoffs, schedule/reminder
acceptance and bounded proactive follow-ups in the ordered plan. Native cron
and heartbeats remain disabled; this deployment does not claim those new
features are installed or accepted.
