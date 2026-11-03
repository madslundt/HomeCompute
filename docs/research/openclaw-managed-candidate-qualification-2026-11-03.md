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
