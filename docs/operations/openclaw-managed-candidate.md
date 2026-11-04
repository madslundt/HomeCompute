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

## Qualification using the verified restored VM

The separate-account fixture refused before creating a sandbox: the vendor
allocator scans its complete default dashboard-port range and cannot prove
another Unix account's existing listener ownership. Its provider and gateway
processes were stopped, Docker group membership removed, and its private home
and diagnostics retained. Atlas's container, configuration, registry and CLI
fences remained unchanged. Do not weaken those ownership checks to pass a test.

Use `scripts/prepare-openclaw-upgrade-assets.py --build-dir REVIEWED_BUILD
--revision FULL_SHA` in the published guest release to capture both complete
public GHCR cohorts with each pinned CLI's own digest/label-validating resolver.
It verifies the two previously built archive hashes, exports only the cached
candidate workload image, and produces a hashed public asset manifest. Copy that
finite directory to a new root-private `/srv/restore-tests/ASSETS` on home-core.
No production state or credentials are included in these assets.

`scripts/qualify-openclaw-restored-upgrade.py` takes the same private restored
archive selection as the accepted native recovery receipt, plus `--assets`,
`--expected-assets-sha256` and `--expected-policy-sha256`. Its source must be a
reviewed published core release. It requires the matching accepted native
recovery proof and verifies every input hash before constructing its read-only
qualification ISO. This is a separate receipt; it never replaces the accepted
NAS recovery receipt.

The VM uses the existing networkless unprivileged QEMU lifecycle, four visible
vCPUs pinned to two host CPUs, 10 GiB guest RAM, a 12 GiB physical memory cap and
bounded scratch I/O. Its source disk descriptor is read only, with disposable
COW writes. It has no NIC, host share or forwarded host port. The clone-only
provider keeps the retained endpoint/provider identity, uses clone-only hosts
resolution and a synthetic credential, and logs no requests. Real channel
connections and heartbeats are disabled inside the clone before the upgrade.

The official vendor qualification hook replays the authenticated public GHCR
catalogs; it changes no CLI code, runtime, registry ownership or phase database.
This qualifies lifecycle behavior using public registry metadata replay, not an
unchanged offline production CLI. Production must independently resolve its
normal authenticated GHCR catalog and compare the selected immutable workload.
The test executes normal named140 rebuild and129 rollback, authenticated native
health and plugin loading, a synthetic model turn and a real researcher handoff.
It checks immutable conversation projections for all roles, all workspaces and
the retained paused policy across each transition. Raw diagnostics stay private.
A missing native turn, handoff, state check or rollback keeps acceptance false.

The installed channel probe discovers the active Mattermost project through its
native installation record and accepts only the reviewed9.1 or9.2 package and
runtime preimage. It applies the corresponding queue port, then loads that exact
installed factory in a temporary sibling module with an empty synthetic queue.
Its SDK resolution must point to the candidate core9.2. Controlled readiness
must prevent every queue inspection while unavailable and resume native
inspection after recovery; abort must settle shutdown. No real event, account,
claim, transcript or message delivery is used in this channel-specific fixture.
The actual active package version is retained in the private receipt; do not
assume the workload image upgraded a separately installed channel package.

The first restored-VM attempt reached authenticated baseline health, then stopped
before the upgrade when native129 stop returned a transport error. The fixture
now checks the exact sandbox identity and independently requires both native
`Stopped` and an exited/dead Docker container. A successful command alone is not
quiescence, and a nonzero result cannot hide a completed stop. Ambiguous, running
or changed identities still refuse before image replacement. Failure receipts
retain bounded private native startup diagnostics for the next reviewed attempt.

Supervisor and manual recovery source now recognize both exact release profiles;
this does not select or deploy the candidate. The configured commit/tag, core
version and workload digest must agree, and host recovery resolves the matching
CLI source tree. An unfinished managed-upgrade journal fences automatic recovery.
The9.2 retained recovery recreates only the qualified child port and retires the
old yield overlay; the9.1 rollback retains both legacy ports. The queue installer
also accepts an explicit `--core-version` when a separately installed compatible
Mattermost version is retained. Profile selection remains part of the guarded
production lifecycle after native qualification.

The second VM attempt positively proved stopped state, then refused before
rebuild because Docker's unnamed export lost the exact repository-digest lookup
on load. The transport builder now verifies the sole authenticated manifest and
every referenced config/layer blob, rejects duplicate, unsafe and unreferenced
members, and adds only `io.containerd.image.name` to the top-level OCI index.
Every blob remains byte-identical. This is Docker29/containerd2's supported
[import naming mechanism](https://github.com/containerd/containerd/blob/v2.2.0/client/import.go#L256),
not a replacement manifest or bypass of the digest check. Original/new archive
and index hashes and the unchanged blob count stay in the private test evidence.
Production uses its normal registry-backed image lookup; this transport is only
for the networkless fixture.

The fixture additionally compares the native sandbox policy, plugin grants and
all immutable role state. It carries only allow-listed non-secret deployment
settings into a clean environment and replaces the clone's model credential
with a synthetic value. A real yielded terminal receipt and two independent
zero-activity observations settle the handoff before the rollback snapshot.

A subsequent attempt was refused before boot by the previous16 GiB admission
threshold while available memory remained stable near15.6 GiB and memory
pressure was zero. That attempt used a10 GiB scope
cap plus5 GiB of host headroom. The current fixture instead requires17 GiB
available, budgeting its12 GiB scope plus5 GiB of host reserve. It also requires full memory pressure over ten
seconds at most0.1% and some pressure at most1%. While running, falling below
4 GiB available terminates the owned VM immediately. Full pressure above1% or
some pressure above5% must persist for three consecutive two-second samples.
The previous single-sample startup refusal is retained as failed evidence; each
new sample records available bytes and both pressure values privately. Cleanup
allows60 seconds after TERM and60 after KILL for the I/O-limited VM, then still
requires its scope to be empty. Other restore callers retain their original
cleanup deadlines. The current guest uses10 GiB/four visible vCPUs and two host CPUs, with
the same read-only source disk and I/O limits. Existing recovery callers keep
their original one-CPU affinity and allocation.
No production service is paused to create room for qualification. This change
applies to the managed-upgrade fixture; the accepted NAS recovery verifier and
its receipt are unchanged.

## Guarded production lifecycle

`config/openclaw-managed-deployment.json` separately controls production
promotion. It remains disabled until an accepted restored-VM receipt establishes
the complete native upgrade, rollback, authenticated task, real child handoff,
yield, installed Mattermost, state integrity and immutable image gates. The
archived candidate declaration remains historical artifact evidence. A native
success receipt alone does not change production pins.

The production controller is `scripts/upgrade-openclaw-nemoclaw.py`. Run `plan`
to obtain the exact source bundle hash. Its mutating actions require a reviewed
published commit staged root-owned under `/srv/homecompute/releases/FULL_SHA`
inside the agents guest, UID 1000, an exact private qualification receipt/hash,
and the accepted public CLI build directory. The source guard compares each
lifecycle input with the selected Git commit; the root DNS installer also
requires that protected release ancestry.

```bash
python3 /srv/homecompute/releases/FULL_SHA/scripts/upgrade-openclaw-nemoclaw.py plan
python3 /srv/homecompute/releases/FULL_SHA/scripts/upgrade-openclaw-nemoclaw.py apply \
  --revision FULL_SHA --expected-hash REVIEWED_BUNDLE_HASH \
  --qualification-proof PRIVATE_RECEIPT --expected-qualification-sha256 RECEIPT_SHA256 \
  --expected-config-sha256 CURRENT_COMPLETE_CONFIG_SHA256 --build-dir REVIEWED_BUILD
```

The controller independently resolves the actual authenticated production GHCR
catalog before stopping anything. It requires the exact old image, policy,
resource budget, known host programs and reviewed runtime-layer changes, then
checks two idle observations. It takes the existing recovery lock and its own
upgrade lock, stops the watchdog, writes a durable upgrade fence, and captures
a complete cold private backup, original permissions, SQLite integrity,
all-role conversation/workspace and queue projections before native rebuild.
The existing global 0.0.129 installation remains retained; the OpenClaw-only
wrapper selects the verified side-by-side 0.0.140 build. Hermes is unaffected.
The installed old recovery program contains the previously applied two-CPU
inspection/cap change; its exact observed public hash is required before the
new tracked dual-profile program replaces it.

After supported named rebuild, the controller records positively observed native
UUID/generation and source/image/core ownership even when native finalization
returns nonzero. An uncertain outcome never permits replaying rebuild. It
installs only hash-qualified runtime ports, verifies preserved data while
channels are paused, rebinds the existing fixed MCP/DNS/network policy and
restores the complete prior team policy. Required plugin health, all enabled
Mattermost connections and one private non-delivering local model probe precede
watchdog reactivation. Family remains disabled under the retained policy.

`resume` continues only a journaled Ready paused candidate before active-team
restoration or model-probe submission. `rollback` captures current cold state
before a supported rebuild with retained 0.0.129, preserving post-upgrade data;
it refuses an unresolved model probe or active native turn before stopping.
`resume-rollback` accepts only the positively recorded replacement-created
checkpoint and verifies its cold state/projection/authority hashes. These
continuations take the same revision, bundle and private-proof arguments as
`apply`; they never repeat native replacement. Unknown native outcomes or an
already active team require read-only examination of the retained journal and
private logs. Code rollback cannot reverse an incompatible data migration;
the isolated native rollback gate remains mandatory.


The next native140 preflight passed artifact import but refused before deletion
when its exact pinned BusyBox bridge container exceeded the vendor's fixed
20-second execution budget. This was an actual container-start check after
successful cache inspection, not a registry pull error. The fixture now records
a120-second bounded warm-up with the same immutable BusyBox image, bridge
network and `true` command, plus metadata-only Docker lifecycle events, before
running the unchanged native preflight. The larger clone allocation meets the
vendor's recommended visible CPU/RAM counts; its physical CPU and memory limits
remain bounded separately. No native timeout, ownership rule or preflight is
patched or skipped. See the [pinned preflight source](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/preflight.ts).

A clone-only UDP resolver on its internal Docker bridge returns NXDOMAIN only
for native `nemoclaw-dns-probe-<hex>.invalid` questions, and SERVFAIL for other
valid names. It never forwards or logs questions. The clone's resolver file
points to that fixture so the genuine container-DNS test has a reachable local
negative resolver despite the absent NIC. The synthetic provider retains its
clone-only hosts mapping. Production independently uses its ordinary DNS and
registry paths; this resolver and its settings are never installed there.

The state projection now also covers the actual native delivery queue, task
runs, delivery routing/deduplication, cron receipts and references. Rows are
compared in full with finite row/byte budgets. Native cron runtime-authority
rows preserve owning job keys while their native revalidated authority fields
are excluded; sandbox network policy is checked independently. Terminal-row
expiry or unexpected task maintenance conservatively refuses preservation.
The production controller additionally compares workspace ownership/modes read
from the actual state volume; Docker copy ownership is not used as evidence of
native UID/GID preservation.

The revised fixture's BusyBox warm-up completed in65.246 seconds and recorded
five container lifecycle events. The unchanged native20-second bridge and DNS
checks then passed. Native rebuild still refused before deleting anything:
ordinary `Stopped` is `not_ready`, rather than the terminal-state path that
captures an unreachable sandbox. Rebuild's MCP-state observer therefore tries
live exec and correctly rejects that stopped source. The previous conclusion
that terminal-state support also covered ordinaryStopped was incorrect.
The lifecycle now takes and validates its cold backup, writes the paused policy,
starts the same old native identity and verifies authenticatedReady, unchanged
paused authority/state and zero active turns before normal named rebuild.
The production upgrade and reverse-version lifecycle use the same ordering;
source CLI/model/identity bindings stay on the current tuple until replacement.
The [pinned MCP observer](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/actions/sandbox/mcp-bridge-source.ts)
and [sandbox observer](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/adapters/openshell/sandbox-observer-cli.ts)
own those reachability checks. Positive nativeReady, same UUID, running Docker
state and authenticated plugin health are mandatory after each paused start,
including when optional CLI finalization returns nonzero. These failed receipts
remain evidence of fixture corrections; production qualification is still false.

The next attempt reached exact nativeReady/running after its paused start, then
the fixture's original health assertion refused. That assertion incorrectly
required Mattermost to be loaded while its channel was disabled. Both cores
explicitly give channel `enabled:false` precedence over plugin enablement and
omit disabled plugins from `health.plugins.loaded`:
[pinned9.1 activation](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/plugins/config-activation-shared.ts#L188),
[pinned9.2 health collector](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/gateway/health/collector.ts#L165).
The fixture now requires all four runtime plugins, gateway health, zero plugin
errors/unavailable records and zero running/connected Mattermost accounts while
that channel is explicitly paused. An active channel still requires its plugin;
the independent installed-Mattermost factory/SDK/queue test remains mandatory,
and production verification still requires all five plugins and every enabled
account connected. Failed receipts now preserve a bounded structured health
summary so missing runtime plugins or other failures remain diagnosable without
relying on a truncated private health log. No candidate has yet been promoted.

## Fixed MCP readiness through startup and recovery

A read-only production baseline enumerated69 advertised MCP tools successfully,
while native status reported `adapter.registered=false` / `mismatch` and skipped
the credential-resolution probe. Discovery alone was not an agent-adapter proof.
The managed startup placeholder refresh does not match an Authorization value
prefixed with `Bearer `, so a prior MCP counter can survive native startup.
Native `mcp restart homecompute` is the supported scoped repair: it registers
at the current revision and reloads only the inner gateway. Its inner restart
skips ordinary deferral, so native turns must be idle before repair.
[Restart implementation](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/actions/sandbox/mcp-bridge-restart.ts#L90).

The tracked supervisor reconciles once on initial authenticated readiness and
after owned recovery. It verifies the exact sandbox/registry identity, canonical
HomeCompute URL, streamable-HTTP transport, sole Authorization header, existing
`bundle-mcp` grant, fixed provider/credential and native policy before admitting
a counter-only mismatch. It serializes reconciliation, requires two zero-turn
observations, invokes only the supported scoped restart, and checks readiness,
registration and the complete normalized authority afterward. The admitted
changes are the same native MCP reference and native gateway authentication.
A failed or interrupted mutation leaves a private journal that blocks automatic
replay; inspect its exact identity, authority hash and private native diagnostics
before a reviewed continuation. Never delete its marker to bypass the refusal.
A single-main canary without this bridge stays compatible; full-team callers
require it, and absence cannot bypass an unresolved journal.

The upgrade and reverse-version controller synchronize the paused source before
rebuild. Full-team recovery synchronizes after its final sandbox start and
verifies the resulting policy, with no further sandbox restart afterward.
The restored qualification also requires that binding and compares the complete
MCP section across both native transitions, normalizing only the owned resolver
revision. `mcp_binding_verified` is a mandatory promotion gate. No MCP token is
rotated or installed by this reconciliation, and its receipts contain metadata
and hashes rather than credentials or tool payloads.

After rollout, verify the real private endpoint without executing tools:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw \
  mcp status homecompute --probe --tools --json
```

Require registered adapter, present policy, attached credential-ready provider,
successful credential resolution and successful bounded tool discovery. Check
the JSON fields; exit zero alone does not prove authorization succeeded. Keep
raw receipts private and retain only sanitized counts/results in source.

The next run confirmed the paused health contract (all runtime plugins loaded,
zero errors/unavailable entries and no running Mattermost account) and completed
its fixed MCP reconciliation. The original preparation-state equality then
refused before upgrade. Its previous combined error did not identify whether
policy, conversations, workspaces or durable rows differed. No preservation
exception is inferred from that incomplete evidence.
The fixture now records bounded changed policy paths, conversation roles,
workspace paths, durable table row counts and changed column names derived from
hashes. It records no row values or task payloads in those summaries. Unknown
policy changes still stop immediately. Valid but changed data may continue in
the same networkless disposable VM to collect the remaining native lifecycle
checks, with separate preparation/upgrade/rollback preservation flags.
Promotion requires **all three** flags and the overall preservation flag; a
diagnostic continuation cannot turn a failed state comparison into acceptance.

Inspection also found a preparation defect: the fixture replaced complete
heartbeat objects with `{every:"0m"}`, dropping the retained
`isolatedSession:true` and other heartbeat settings. Both pinned vendor
generators emit that isolation setting. Pausing now copies the policy and
changes only heartbeat intervals, channel enablement and cron enablement;
heartbeat isolation and other options stay intact. The changed-data diagnostics
remain required to establish whether any other preparation difference exists.
