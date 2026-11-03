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

## Deployment continuation: source ports and CLI artifacts

The owner subsequently authorized deployment. The 9.2 child and Mattermost
installers now require an explicit `--runtime-version 2026.9.2`, with reviewed
before/after hashes; their defaults remain the qualified 9.1 profiles. The queue
installer renders the activity module's 9.2 path/hash from reviewed source.
Unknown versions, changed modules and ambiguous native Mattermost projects refuse.
The candidate regression also invokes the ported activity RPC through the actual
native loader, with read scope and no real work.

Prepare public CLI artifacts from a reviewed published checkout on the guest:

```bash
python3 "$HOME/.local/share/homecompute/releases/FULL_SHA/scripts/build-openclaw-managed-cli.py" plan
python3 "$HOME/.local/share/homecompute/releases/FULL_SHA/scripts/build-openclaw-managed-cli.py" \
  prepare --revision FULL_SHA --expected-hash REVIEWED_BUILD_BUNDLE_HASH
```

The helper uses the existing pinned Node 22.23.3, exact authenticated vendor
source commits and lockfiles, isolated npm cache/user configuration, ignored
package installation hooks and explicit reviewed build commands. It pins build
processes to one guest CPU and bounds each Node heap. It never executes a runtime
CLI, changes a global shim, operates a native registry or recreates a sandbox.
It emits byte-hashed public archives and build identities in a new private build
directory. Archives exclude Git metadata and reject external symlinks. These
artifacts are inputs to the separate synthetic native lifecycle qualification;
a successful build alone does not authorize production replacement.

The native lifecycle fixture uses `scripts/qualify-openclaw-native-lifecycle.py`
from a reviewed published guest release. `plan` prints its hash. Root `prepare`
requires that hash, revision and `--build-dir` naming the retained CLI build
receipt. It verifies both public archive hashes before creating a locked,
nonlogin fixture account. The fixture has its own home, registry, state volume,
loopback gateway 9124 and dashboard 18792; Atlas remains on gateway 9123.
A local synthetic provider binds only the Docker bridge address on port 18081,
returns fixed model responses, and logs no request bodies. The trusted fixture
management account can use Docker, while the agent container receives neither
the socket nor production credentials. CLI input environment is constructed
explicitly; production environment files are never loaded.

Run `baseline`, `upgrade`, then `rollback` with the same published revision/hash.
Each action checks the production container ID, image, resource limits, config
and native registry hashes and CLI target before and after its native command.
The fixture uses two vCPUs and 3 GiB RAM; CLI commands have bounded deadlines and
terminate their own process group on timeout. Private step logs use exclusive
creation, preventing an accidental replay from overwriting diagnostic evidence.
`cleanup` destroys only the named fixture through its own registry, stops its
provider and remaining account processes, and retains the locked account/home
and private diagnostics for inspection. A failed step requires inspection and a
reviewed continuation; no production checkpoint or phase database is edited.
A completed baseline/upgrade/rollback still does not establish task delivery,
Mattermost integration or full state preservation; those need separate evidence.

```bash
native_tool="$HOME/.local/share/homecompute/releases/FULL_SHA/scripts/qualify-openclaw-native-lifecycle.py"
python3 "$native_tool" plan
sudo python3 "$native_tool" prepare --revision FULL_SHA --expected-hash NATIVE_BUNDLE_HASH \
  --build-dir "$HOME/.local/share/homecompute/openclaw-cli-builds/REVIEWED_BUILD"
sudo python3 "$native_tool" baseline --revision FULL_SHA --expected-hash NATIVE_BUNDLE_HASH
sudo python3 "$native_tool" upgrade --revision FULL_SHA --expected-hash NATIVE_BUNDLE_HASH
sudo python3 "$native_tool" rollback --revision FULL_SHA --expected-hash NATIVE_BUNDLE_HASH
sudo python3 "$native_tool" cleanup --revision FULL_SHA --expected-hash NATIVE_BUNDLE_HASH
```
