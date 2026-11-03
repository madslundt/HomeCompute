# Managed OpenClaw candidate qualification — 2026-11-03

Status: exact-source and public registry metadata research. No live host access,
image-layer pull, package install, container execution, upgrade or qualification
was performed by this investigation. The managed artifact exists, but is not yet
HomeCompute-qualified. This supplements the [upgrade assessment](openclaw-upgrade-assessment-2026-11-03.md)
and [Atlas plan](../openclaw-grok-bot-plan.md).

## Exact managed artifact

Selected NVIDIA source: NemoClaw `v0.0.140`, commit
`88fe14f3fb9a0bf72997cce19ec88b5b2ee08391`. Both public GHCR tags
`v0.0.140` and that full source SHA resolved to the following same artifacts.
Registry manifest/config bytes and the inspected provenance blob were hashed
against their digests. Only public metadata was downloaded; anonymous registry
bearer values were not printed or retained in source.

| Identity | Observed value |
| --- | --- |
| Managed repository | `ghcr.io/nvidia/nemoclaw/openclaw-sandbox` |
| OCI index | `sha256:349da2829f3050f6ce256f3f270338060607b6360ad910f4f49db6613bf1e847` |
| **linux/amd64 workload manifest** | **`sha256:23b806f148ba3e2c57b923f659fe2e6caa1dded684fc3ae410c41c2e17a73890`** |
| Image config | `sha256:e10fa6554bdd92f0176d0eb405f37dc554c5c02007fe33a46ad6835a59d89478` |
| Publication cohort label | `ghrun-40975670876-1` |
| Revision/source/version labels | selected full SHA / `https://github.com/NVIDIA/NemoClaw` / `v0.0.140` |
| Agent/platform labels | `openclaw` / `linux/amd64` |
| Contract/startup-profile/capabilities labels | all `1` |
| Config user / declared Node | `sandbox` / `NODE_VERSION=24.22.1` |
| Image config creation | `2026-10-30T00:19:11.004120074Z` |
| Compressed layer sizes summed | `789052266` bytes; extracted size and sharing with current layers unverified |
| amd64 attestation manifest | `sha256:6cfd9bac625be7acdb5e3c71457d2ae8e435b6e449bbde497b753a012e8a34be` |
| SLSA v1 statement blob | `sha256:39efbbf191d843a2057bc10c979430fdbc6ada71720fed7478ad2448db4ff295` |

The SLSA statement's subject names the exact amd64 workload digest. Its build
metadata names the selected source revision and builder
`https://github.com/NVIDIA/NemoClaw/actions/runs/40975670876/attempts/1`.
This establishes consistency of inspected registry metadata. Independent signing
identity/attestation verification, package contents, installed versions and
runtime behavior remain unverified. The `sandbox-base` digest mentioned in its
build provenance is a build input, not the managed workload image to deploy.
[Registry index](https://ghcr.io/v2/nvidia/nemoclaw/openclaw-sandbox/manifests/sha256:349da2829f3050f6ce256f3f270338060607b6360ad910f4f49db6613bf1e847),
[workload manifest](https://ghcr.io/v2/nvidia/nemoclaw/openclaw-sandbox/manifests/sha256:23b806f148ba3e2c57b923f659fe2e6caa1dded684fc3ae410c41c2e17a73890),
[build run](https://github.com/NVIDIA/NemoClaw/actions/runs/40975670876/attempts/1),
[publication workflow](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/.github/workflows/managed-images.yaml).

The exact CLI resolves a complete all-agent cohort before selecting a managed
image. Companion metadata at `cohort-ghrun-40975670876-1` was also available,
with the same revision, version and cohort:

| Companion | linux/amd64 manifest |
| --- | --- |
| Hermes | `sha256:7441c24787e8d3787253c5d477dc8ce72168ca157584383edad186f21c8c8f81` |
| Deep Agents Code | `sha256:a25ffe83c9fa155fe72c6baed6a89512422db9df67b1da716f1902228b14ab7f` |

Resolving those metadata records does not authorize pulling their layers,
creating agents, reviving Hermes or connecting their credentials. The catalog
validates unique platform selection, manifest/config hashes, source labels,
revision, release, cohort and contract versions. Preserve that admission; do not
replace it with a caller-written catalog or base-image override.
[Catalog implementation](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/managed-image/catalog.ts),
[contract](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/managed-image/contract.ts).

## Version and lifecycle constraints

The source locks OpenClaw `2026.9.2`. Its blueprint requires OpenShell
`0.0.116` as both minimum and maximum. The CLI package version field is
`0.1.0`, whereas exact release identity comes from generated build identity,
Git describe or release stamps. A plain source archive without the original
Git/stamped identity can select the wrong catalog alias; qualification must
verify the compiled identity, not just `package.json`.
[Runtime lock](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/agents/openclaw/openclaw-runtime/package.json),
[blueprint](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/nemoclaw-blueprint/blueprint.yaml),
[build identity](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/core/version.ts).

The source-revision catalog authority is the installed build's exact identity.
If `NEMOCLAW_INSTALL_REF` supplies a 40-character SHA, it must match that
identity. Source builds without a matching release stamp select their own
revision automatically. Do not use E2E-only catalog or revision environment
overrides to bypass this check.
[Preparation authority](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/workload/preparation.ts).

The supported version-change operation is: prepare the selected CLI, then
`nemoclaw <name> rebuild` through that CLI. It backs up, recreates and restores
the named sandbox using the current managed image. The managed rebuild
preflight binds the previous workload receipt/profile and exact replacement
catalog; it does not fall back to a Dockerfile or mutable image. Its CLI exposes
no `--to` image/version selector. A snapshot restore is state recovery, not a
version selector.
[Update guide](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/docs/manage-sandboxes/update-sandboxes.mdx),
[rebuild command](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/commands/sandbox/rebuild.ts),
[managed rebuild handoff](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/workload/rebuild.ts).

The default installer follows moving `lkg`, can back up/reconcile all registered
sandboxes, and can retire a gateway when replacing an out-of-range OpenShell.
`upgrade-sandboxes --auto` is broad reconciliation. Neither is appropriate for
creating our first isolated candidate. An already-supported `0.0.116` avoids
the need for an OpenShell version change, but does not remove registry, gateway,
forward, credential and state checks. Sandbox names must use 1–19 supported
lowercase characters without consecutive hyphens.
[Exact installer](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/scripts/install.sh),
[upgrade command](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/commands/upgrade-sandboxes.ts).

## Resource-safe qualification recipe

These are implementation requirements and staged command shapes, not commands
executed by this note or a completed installer.

1. **Admit resources before pulling.** Measure actual guest free RAM, disk,
   Docker storage and current production activity. Reserve production's 8 GiB,
   browser and guest overhead. Do not increase VM memory, stop production,
   replace Docker, or invoke shared Spark inference to make a candidate fit.
   Start with metadata/static checks; run executable image checks only after
   a measured budget is available. The upstream noninteractive resource
   preflight can merely warn, so HomeCompute must enforce its own hard gate.
2. **Stage an isolated CLI.** Use a distinct operator-owned source/build prefix
   and absolute CLI entrypoint at the selected SHA, keeping the installed
   production binary and supervisor path unchanged. Preserve Git/tag or
   authenticated source stamps so the generated build identity resolves the
   right revision. Root `npm ci`/`npm install` has a `prepare` script that can
   build and globally link shims. Disable automatic scripts for dependency
   staging, then explicitly execute only reviewed necessary build steps;
   never run `npm link` or the broad installer as an accidental build side
   effect. Confirm the resulting build identity before using it.
3. **Verify image metadata and pull only the exact amd64 digest.** Retain the
   registry contract/provenance receipt and compare runtime image inspection.
   First executable checks use a disposable unprivileged container with no
   network, no published ports, no Docker socket/host state mounts, explicit
   CPU/RAM/PID/time limits, and a fresh synthetic state location. Override
   automatic startup so version/schema/source probes cannot onboard or infer.
   This container proves artifact compatibility only, not NemoClaw operation.
4. **Create a native synthetic candidate.** Use a separate management account
   or equivalently isolated validated registry/state root; select a free
   gateway/dashboard pair and separate sandbox name, for example
   `agent-oc140`, gateway `9124`, dashboard `18792`, only after collision checks.
   The command shape is the exact candidate CLI's
   `nemoclaw onboard --non-interactive --yes-i-accept-third-party-software
   --agents <reviewed-synthetic-manifest>`. Set `NEMOCLAW_AGENT=openclaw`,
   exact install SHA, explicit gateway/bind/ports, bounded CPU/RAM, GPU off,
   search off, direct tool disclosure, recurring heartbeat off and telemetry
   disabled. Never copy production registry entries, state, tokens, channel
   bindings, browser sessions or model credentials into this candidate.
5. **Use a local deterministic fixture provider first.** Configure only a
   bounded synthetic OpenAI-compatible endpoint/principal owned by the
   candidate lifecycle, with explicit ingress/egress policy. No production
   LiteLLM or Spark endpoint is admitted. Onboard/status/start can issue model
   probes, so a denied or absent fixture remains a visible blocker. Later
   real-model qualification needs its own limited credential, inference
   budget and service-capacity admission; a fixture pass does not prove it.
6. **Exercise native rebuild on synthetic state.** Seed synthetic conversations,
   memory, jobs and plugin state, capture hashes/schema/integrity, and rehearse
   the named rebuild using an explicitly selected CLI. Test interrupted
   backup/recreation/restore, permission drift, unavailable credentials,
   gateway failure and retained recovery journals before any production
   replacement. Do not rely on `status` alone or delete a refused receipt.
7. **Port and qualify HomeCompute integration.** Run actual plugin/SDK,
   request/delegate/yield/result, queue, cancellation, duplicate/restart,
   reminder and delivery fixtures. Only then admit a published source revision
   and guarded production transaction after backup/recovery gates. A per-role
   grant, controller or live reminder is not implied by onboarding.

Source for build/prepare behavior and runtime limits:
[package scripts](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/package.json),
[resource test contract](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/preflight-runtime-resources.test.ts),
[onboard managed source selection](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/onboard/managed-workload/onboard-orchestration.ts),
[rebuild phase pipeline](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/actions/sandbox/rebuild-pipeline.ts).

## HomeCompute changes that must travel together

| Source owner | Concrete requirement |
| --- | --- |
| `config/hermes-release.json`, `scripts/setup-hermes-guest.sh` | Shared old tuple/installer pins; preserve retired Hermes scope. Do not run this Hermes-only setup to stage OpenClaw |
| `config/openclaw-nemoclaw.env.example` | Selected exact CLI release, gateway identity and resource declaration; candidate values must be separate from production |
| `scripts/supervise-openclaw-nemoclaw.py` | Source/image/env requirements and exact compiled gateway-recovery module hash; new CLI can break recovery even when core is healthy |
| `scripts/restore-openclaw-nemoclaw.py` | Current image, state schema/table checks and lifecycle grammar; retain old recovery path until new restore is proven |
| `scripts/setup-openclaw-mattermost.py`, `scripts/setup-openclaw-llm-queue.py` | Version/provenance admission, installed plugin project and hashed runtime patch; qualify a version-matched artifact |
| Child-result/yield installers and safety plugin | Old dist filenames/preimage hashes are version-specific; reproduce old failures, retire fixes now upstream or port exactly and regression-test |
| `scripts/openclaw_team_policy.py`, native MCP and agent-office | Effective grants, trusted role identity, session boundaries, native SDK/RPC and source ownership remain enforced |
| Tests/runbooks and candidate installer | Refuse unknown artifacts/state; retain exact receipt, quiescence, rollback and post-apply client checks |

`config/hermes-agent.env.example` contains canary settings, not the shared release
pins. Do not change it merely because its name appears in older plan text.
The selected root installer SHA-256 is
`bafd48cd005e11e064e0c4caa0968a4c781453ccebcf4593855f85d28afbdeb8`;
its versioned payload `scripts/install.sh` hashes to
`1176affa07fc1b58af5fc6ae2a92947a5953b5dd206efb6f1693eb6f491f61cb`.
Pinning those bytes is useful for future lifecycle review, not authorization
to invoke them broadly on the production operator account.

The official `@openclaw/mattermost@2026.9.2` package is published with peer core
and plugin API `>=2026.9.2`. Both core `2026.9.1` and `2026.9.2` advertise
agent schema 19/shared-state schema 15. Reusing those numbers is not evidence
that plugin state, native runtime patches or external effects can roll back.
Restore the matching complete prior tuple/state when required; keep production
and synthetic stores separate throughout qualification.
[Core package](https://registry.npmjs.org/openclaw/2026.9.2),
[Mattermost package](https://registry.npmjs.org/@openclaw%2fmattermost/2026.9.2),
[HomeCompute managed runbook](../openclaw-nemoclaw.md),
[HomeCompute recovery helper](../../scripts/restore-openclaw-nemoclaw.py).

## Published 2026.9.2 bundle patch inventory

Follow-up source-only inspection downloaded the published core and Mattermost
tarballs, verified their npm SHA-512 integrity, and read their extracted source.
No downloaded JavaScript or package lifecycle script was executed. The local
HomeCompute pure Python overlay functions were evaluated against those bytes;
both retained their expected refusal of unknown preimage hashes. Hypothetical
patch hashes below were calculated in memory without modifying implementation
or installed runtime files.

The OpenClaw `v2026.9.2` tag resolves to commit
`3928bad9badfcb6c7d140530435e806fb8092190`. These hashes identify **pristine npm
artifacts**, not yet the corresponding files inside NVIDIA's managed image.
NVIDIA applies its own patches while building that image; inspect and reconcile
the exact managed preimages before using any of these hashes as an installer
admission pin.
[Core tarball](https://registry.npmjs.org/openclaw/-/openclaw-2026.9.2.tgz),
[Mattermost tarball](https://registry.npmjs.org/@openclaw/mattermost/-/mattermost-2026.9.2.tgz),
[NVIDIA managed Dockerfile](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/Dockerfile).

| Seam in published artifact | Exact new relative path | SHA-256 of unmodified npm bytes |
| --- | --- | --- |
| Child result evidence | core `dist/subagent-announce-output-Cy2Rx3_K.js` | `71523669a7f466e8c3a240f7d0a8485a8fa8882f5920add16176685cb5d34dc1` |
| Embedded agent runner | core `dist/embedded-agent-D9hkv0XX.js` | `de162cbb3725c0ddd7bb0521cd54309095b09c5b15c92d994a7c49d442024c00` |
| Reply/yield dispatcher | core `dist/agent-runner.runtime-cwyzm408.js` | `135a52075af80beb1cc5fd22a7e72324308f099208b00de00fb13967bf923911` |
| Mattermost ingress | plugin `dist/channel.runtime-B454n78J.js` | `17f9cc55ddbbd3f2393ba459746dec1c711ae79255b0c18afe16908f780d45eb` |
| Core ingress monitor | core `dist/ingress-monitor--JQILMnk.js` | `153485e44ec82352c72c79662bdf767abca222d739e9f82908aa3b3be02ee251` |
| Active-work inventory | core `dist/gateway-active-work-DdwRky6I.js` | `286cd71c28dd945b266bd96df9c6b2242e419de0052203e070d4e838c8997a48` |
| Native plugin source loader | core `dist/plugin-module-loader-cache-lf3kAaBw.js` | `e09d22fd6e1877f388c44be8e837dec5ebb0cb8002d29ddf41c26ef7f84c0d08` |
| Global hook runner | core `dist/hook-runner-global-0kfmMG4T.js` | `7f9473df33a148d511815fca56b1349cc5525e516680a71da22ca2cc863d047d` |
| Optional browser-schema canary | core `dist/browser-tool.schema-CFr5Al89.js` | `b3893011da781d2ee592f94de9fbe954d6817bb5cf2c9ab0e01bc7064310edfd` |

### Child evidence: behavior remains; prepare a versioned port

The new child module still sets `MAX_CHILD_COMPLETION_RESULT_CHARS = 512` and
passes that limit to `formatChildResultData(resultText)`. Each of the three
existing [child-result overlay](../../scripts/setup-openclaw-child-results.py)
anchors occurs exactly once. Applying the same bounded single-child extension
in memory yields SHA-256
`ed09c0b16e452ec159085ef35b3475cd20c15b6ffbf8620c4b24517588c86bad`.
This seam has not incorporated our extension. Reproduce loss of essential
single-child evidence, then qualify a version-specific port with the same
single/multiple/aggregate limits, truncation and injection cases. Anchor
compatibility alone does not prove a functioning multi-step handoff.

### Yield acknowledgement: retire the old patch for this candidate

The old `suppressToolErrorWarnings: runParams.suppressToolErrorWarnings` seam is
absent. Upstream now marks generated tool warnings with private metadata and
handles the collision in `agent-runner-result-payloads.ts`: when an eligible
yield acknowledgement exists, generated warnings do not consume the final
reply slot; they are filtered before terminal-content selection, and the
acknowledgement is rendered when no other terminal payload exists.

This directly addresses the failure that motivated the old
[yield overlay](../../scripts/setup-openclaw-yield-acknowledgment.py), using a
different ownership point. Do not port that overlay into the new embedded
runner. Retain its old-version recovery support, and qualify the new dispatcher
with one acknowledgement despite an earlier tool error, a genuine terminal
failure, an empty/missing acknowledgement, missing continuation, silent cases,
and subsequent child completion. Source shows the upstream fix; candidate
runtime regression acceptance remains outstanding.
[Exact reply dispatcher source](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/auto-reply/reply/agent-runner-result-payloads.ts),
[acknowledgement eligibility](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/auto-reply/reply/sessions-yield-acknowledgment.ts).

### Queue gate: API seam remains, HomeCompute readiness gate is absent

The Mattermost module retains exactly one
`waitForDeliveryIdleBeforeRepump: true` anchor and does not supply `runPumpTask`.
The core monitor still invokes `options.runPumpTask(runPump)` when provided,
otherwise `runPump()` directly. The existing
[queue overlay](../../scripts/setup-openclaw-llm-queue.py)'s import and callback,
applied in memory, produce SHA-256
`a749d8665eab1280fac1247713c72a3b356f0936b04dafbd72e82bad74247284`.
Prepare a versioned port preserving pre-claim waiting, pump cleanup and abort
semantics; the native monitor must own all admitted work. Resolve the installed
9.2 plugin through verified native install records rather than retaining the
old `openclaw-mattermost-41605c6faa` project directory assumption.
[Core monitor source](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/channels/message/ingress-monitor.ts).

### Safety imports and SDK: port internal references, test real enforcement

The [activity helper](../../deploy/openclaw/safety-plugin/llm-queue.mjs) must
select the candidate's exact active-work filename and preimage. The new module
still exports `createGatewayActiveWorkSnapshot` as `t`, returning
`counts.totalActive`; that count remains a composite inventory of active/queued
work, not a count of distinct user tasks. The old filename/hash will correctly
refuse or fail as unavailable on the new artifact. Verify the managed install's
runtime/symlink ownership before selecting an import path.

The [native loader test](../../tests/openclaw-native-loader.test.mjs) pins an old
filename. The new source loader is still export `n`; its `modulePath`, `rootDir`,
`aliasMap` and `importerUrl` inputs remain visible. Adapt the test's versioned
selection and run it against the real candidate loader. Reading those inputs
does not establish module-cache identity or loaded policy behavior.

The published declarations retain `registerService` and
`registerGatewayMethod(method, handler, {scope})`. The global hook runner
explicitly sets `before_tool_call` to fail closed, sorts higher priorities first,
keeps a blocking result sticky and stops later handlers after a block. These
are compatible source contracts for the current safety approach, but actual
registration, native caller identity, final parameter rewriting, exceptions,
RPC scope and public/private destination denials need candidate SDK tests.
Local plugin unit stubs cannot substitute for those checks. Do not widen grants
to work around a plugin failing to load.
[Hook runner source](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/plugins/hooks.ts),
[tool-call enforcement](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/agents/agent-tools.before-tool-call.ts),
[active-work source](https://github.com/openclaw/openclaw/blob/3928bad9badfcb6c7d140530435e806fb8092190/src/infra/gateway-active-work.ts).

## Native fixture preparation constraints

Both the baseline `26922313…` and candidate `88fe14…` require host CLI Node
`>=22.19.0` and the same vendored OpenShell SDK preparation. Build each exact
checkout independently: run `scripts/check-node-version.js`, then
`scripts/lib/openshell-sdk-install.mts prepare`, `npm ci --ignore-scripts
--include=dev --include=optional --prefer-offline`, the SDK helper's `check`,
`npm --prefix nemoclaw ci --ignore-scripts --include=dev`, `npm run build:cli`
and `npm --prefix nemoclaw run build`. Verify `dist/build-identity.json` and
invoke `node /absolute/source/bin/nemoclaw.js`; root package version `0.1.0`
does not identify the installed release. These explicit builds avoid root
`prepare` and global linking. [Baseline scripts](https://github.com/NVIDIA/NemoClaw/blob/26922313bba96184e65c3663b351683ebae9504d/package.json),
[candidate SDK preparation](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/scripts/lib/openshell-sdk-install.mts),
[identity resolution](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/core/version.ts).

Use a separate non-login test account and its genuine passwd home. Populate
`HOME` from that account: unset registry `HOME` falls back to `/tmp`. Scrub
production XDG paths, proxy variables, credentials and tokens. Gateway 9124,
dashboard 18792 and sandbox `agent-oc140` select a separate registry/state root
and volume, subject to collision checks. Select verified 0.0.116 CLI, gateway
and sandbox executables with the three explicit `NEMOCLAW_OPENSHELL_*_BIN`
variables. Docker socket authorization must be explicit; a separate account
does not reduce Docker's administrative authority.
[Registry home handling](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/state/registry/persistence.ts#L124).

The official 0.0.116 archives are separate. Their SHA-256 pins, confirmed against
the exact installer and downloaded archive contents, are:

| Linux amd64 asset | SHA-256 |
| --- | --- |
| `openshell-x86_64-unknown-linux-musl.tar.gz` | `4fb4476d80a1875a0b83547ec3aba999cf0a2e2d75f95f2f709b622e2103520e` |
| `openshell-gateway-x86_64-unknown-linux-gnu.tar.gz` | `59c6da724eae7a00c28826f9191efbdf4fbaa5c768afdc8dea6a80a949ebcc89` |
| `openshell-sandbox-x86_64-unknown-linux-musl.tar.gz` | `0bb160f73e5007338b94e3c868f66f50c71cd65c27c932ed9a4fa67c49e6d423` |

These are under NVIDIA/OpenShell's `releases/download/v0.0.116/` path.
[Exact installer pins](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/scripts/install-openshell.sh#L220).
An independent Node 22.23.3 linux-x64 archive is available with SHA-256
`df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de`.
[Official Node checksums](https://nodejs.org/dist/v22.23.3/SHASUMS256.txt).

A deterministic fixture must answer discovery and completion probes without
forwarding to production. Arbitrary loopback ports are not automatically
bridge-rewritten: supported ports are 11434, 11435 and the configured
`NEMOCLAW_VLLM_PORT`. Alternatively bind a proven free, exact Docker-bridge
private address and trust only that address. Start with wildcard-denied tools,
no channels, cron or heartbeat. Use the genuine 129 CLI to create baseline
synthetic data, then the 140 CLI's named rebuild and an actual rollback test.
Unchanged agent/state schema numbers cannot substitute for that rehearsal.
[Bridge restrictions](https://github.com/NVIDIA/NemoClaw/blob/88fe14f3fb9a0bf72997cce19ec88b5b2ee08391/src/lib/inference/native-local/eligibility.ts).


## Actual Docker provenance check, 20:20 UTC

After the native NAS recovery gate passed, the exact declared workload was
pulled on the existing agents guest. Docker Engine 29.1.3 returned the manifest
digest as `Id`, with the same exact repository digest and OCI descriptor. User,
architecture and every reviewed label matched. The initial probe refused because
it assumed `Id` was always the config digest; no candidate container ran.

The corrected probe vendors the exact 20,193-byte public OCI manifest. Its SHA256
is the already-declared workload digest, and its `config.digest` is the
already-declared `e10fa6…` config digest. Both pins remain unchanged. It checks
that chain, repository digest, optional descriptor and all existing provenance
fields before supporting either Docker ID representation. Altered manifest
bytes, a wrong config pin, another repository digest or unknown ID still fail.
This is an inspector compatibility repair, not candidate runtime qualification.

Docker documents the [containerd image store](https://docs.docker.com/engine/storage/containerd/)
as the default for fresh Engine 29 installations. The first-party
[containerd inspector implementation](https://github.com/moby/moby/blob/master/daemon/containerd/image_inspect.go)
sets the returned ID from the target digest. The live response is the evidence
for this installed daemon; a moving source page does not qualify the workload.
