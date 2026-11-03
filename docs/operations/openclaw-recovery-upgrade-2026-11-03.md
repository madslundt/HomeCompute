# OpenClaw recovery and managed-upgrade execution — 2026-11-03

Status: in progress. The owner authorized backup/recovery repairs followed by
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

Production was checked independently during the isolated boot: authenticated
health was good, zero plugin errors, and all eleven enabled Mattermost accounts
were connected. Twelve accounts remain configured; Family is deliberately
disabled. Native cron and heartbeats retain their previous disabled state.

## Managed candidate source

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

The attended backup has 10 focused tests covering admission order, safe retry
handling, no backup on a failed drain, interruption/resumption ordering, early
hold release and truthful completion. The offline candidate has nine focused
isolation/provenance-refusal/ownership-cleanup tests. Both repository validation
runs passed, including containerized Nix flake evaluation. The later probe
instrumentation changes also passed focused tests and whitespace checks.
Existing optional native-runtime tests skip on the workstation; these synthetic
results are separate from live readiness and candidate SDK acceptance.

The restore lifecycle now has 18 focused tests and the candidate has eight,
including refusal under high or unavailable I/O pressure. The complete
repository validator passed after the resource limits were added, including
containerized Nix flake evaluation.

Outstanding: successful authenticated gateway startup in the restored copy;
then actual managed-artifact checks, native fixture lifecycle/rebuild and
rollback, integration/patch/identity tests and authenticated client acceptance.
Do not deploy from an artifact-only pass or imply host synchronization from
release markers. Retain the prior production tuple until those gates pass.
