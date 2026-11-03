# Managed OpenClaw candidate: offline artifact gate

The candidate policy is [the immutable declaration](../../config/openclaw-managed-candidate.json).
It does not change the production CLI, supervisor, image, credentials or runtime
pins. See [artifact/lifecycle research](../research/openclaw-managed-candidate-qualification-2026-11-03.md)
and the [Atlas plan](../openclaw-grok-bot-plan.md) for the remaining native gates.

Run the probe only after the current backup and isolated recovery verification,
from a reviewed commit published on origin/master. Stage that exact public
checkout under the existing guest operator's
`~/.local/share/homecompute/releases/FULL_SHA`. Use the existing pinned SSH key
and known host through home-core; do not change either production CLI symlink.

```bash
candidate_tool="$HOME/.local/share/homecompute/releases/FULL_SHA/scripts/qualify-openclaw-managed-candidate.py"
python3 "$candidate_tool" plan
python3 "$candidate_tool" probe --revision FULL_SHA --expected-hash REVIEWED_BUNDLE_HASH \
  --evidence "$HOME/.local/state/homecompute/oc140-UNIQUE" --pull
```

The default plans without Docker operations. `--pull` fetches only the declared
amd64 workload digest; subsequent probes can omit it. Inspection requires the
exact image manifest/config chain, agent/revision/release/cohort/contract labels
and non-root sandbox user. The byte-preserved public
[OCI manifest](../../deploy/openclaw/qualification/openclaw-0.0.140-linux-amd64.manifest.json)
must hash to the declared workload digest and name the declared config digest.
Classic Docker config IDs and containerd manifest IDs are accepted only with
that same chain, exact repository digest and (when present) matching descriptor.
Unknown IDs, changed manifest bytes, a changed config pin or inconsistent labels
still refuse before container execution. Available RAM, Docker filesystem space and guest full
I/O-pressure average over ten seconds must satisfy the declaration before
pulling; insufficient headroom refuses without changing
production resources.

Execution is bounded to one CPU, 1536 MiB without additional swap, 128 PIDs and
90 seconds. The disposable container has no network, published ports, host
binds, Docker socket or credentials; its image root is read-only and synthetic
state uses private container tmpfs. The probe reads installed Node/core/schema
metadata, executes the native version command, validates a synthetic config
through the native dry-run CLI, and inventories old version-specific patch paths.
No model call, gateway start, native registry operation or production data is used.

Raw diagnostics and `report.json` remain in a new operator-private directory
outside Git. Only sanitized aggregates belong in the repository. A Docker client
timeout does not stop a container: cleanup finds only the unique candidate label,
checks exact name/ID/label ownership, then removes that owned instance. Ambiguous
or unavailable cleanup is an error; inspect it before another probe. The pulled
immutable image cache is retained and accounted for by the candidate declaration.

The child-evidence gate separately requires actual native fixture execution,
single-child tail retention, and a bounded nonempty aggregate with its omission
notice. Either a failed artifact gate or child-evidence gate returns nonzero;
metadata success alone cannot hide an unexecuted or truncated child fixture.
The stock candidate is expected to fail tail retention until its exact runtime
patch is ported and qualified.

The probe also checks a proposed port of the tracked child-result changes against
both declared before/after hashes, mirrors public runtime files using temporary
symlinks and runs the same native fixture against the temporary changed module.
`proposed_child_overlay_checks_passed` describes only that isolated overlay;
the unmodified image's child gate remains separate and the CLI still returns
nonzero when it fails. Neither the installed image nor production state is
changed. A passing temporary overlay does not qualify an upgrade installer,
native lifecycle, integration or rollback.

The additional `native_regression_checks_passed` gate stages only the tracked
public safety source into tmpfs. It loads the plugin through the candidate's
actual source-transform loader, dispatches policy through its global native
hook runner, and checks coordinator/browser authorization, sticky vetoes and
fail-closed hook errors without starting registered services. A temporary copy
of the exact-hash dispatcher exposes two internal entry points while retaining
its original dependencies. Genuine tool-warning metadata exercises the native
yield acknowledgment, ordinary-warning, terminal-answer and quiet-delivery
rules. The production yield overlay is not applied. Missing imports, assertions
or execution fail the separate gate; local syntax/unit checks do not count as
native success. The native ingress monitor is also exercised with an empty in-memory queue:
unavailable readiness leaves all queue methods untouched, recovery reaches native
queue inspection, and an explicit controller abort settles the pump. This does
not establish Mattermost patch wiring or real delivery. No gateway, channel or
model is started by these regressions.

`artifact_checks_passed=true` is explicitly **not production qualification**.
Native NemoClaw fixture-provider onboarding/rebuild/rollback, actual SDK/safety
and Mattermost integration, patch porting, state preservation, authenticated
task delivery and mixed-load tests remain mandatory. Missing old hashed runtime
paths require a port/retirement decision; do not copy old dist files into the new
image or change refusal hashes just to pass. Until those gates pass, keep the
existing production tuple and its recovery path.
