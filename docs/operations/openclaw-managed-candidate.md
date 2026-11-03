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
exact image config digest, agent/revision/release/cohort/contract labels and
non-root sandbox user. Available RAM, Docker filesystem space and guest full
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

`artifact_checks_passed=true` is explicitly **not production qualification**.
Native NemoClaw fixture-provider onboarding/rebuild/rollback, actual SDK/safety
and Mattermost integration, patch porting, state preservation, authenticated
task delivery and mixed-load tests remain mandatory. Missing old hashed runtime
paths require a port/retirement decision; do not copy old dist files into the new
image or change refusal hashes just to pass. Until those gates pass, keep the
existing production tuple and its recovery path.
