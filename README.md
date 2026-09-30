# HomeCompute

> **Observed live state (2026-09-29):** `home-core` and `home-spark` are
> deployed. n8n production remains on `unsloth/Qwen3.6-35B-A3B-NVFP4` at
> revision `739af1e7aac320af1682ed1e0cce369af4c5265d`, served as
> `automation-moe`. Flash-Next is not production-qualified. See
> [current state](docs/current-state.md) for the exact snapshot and open gaps.

HomeCompute is a local-first AI platform for private inference, automation,
voice, meetings, coding, research, and personal agents.

The platform has two physical node roles:

- `home-core` is an always-on x86 NixOS control-plane host. It
  owns the trusted gateway, its dedicated database, backups, and other approved
  durable state; untrusted workloads remain elsewhere.
- `home-spark` is an NVIDIA GB10 or DGX Spark-class appliance. It runs
  rebuildable text, speech, and diarization inference services.

Clients use `https://ai.home.arpa`. They do not call the compute node or concrete
model names directly.

> **Status:** the guarded definitions now back a live deployment, but this is
> not a turnkey installer. Off-host restore testing, direct private-link
> cutover, production-run evidence, and complete model failover remain open.
>
> The model registry and generated LiteLLM route section now exist in source;
> the generated configuration has not been applied to the live gateway or
> n8n. See the [routing baseline](docs/model-routing-refactor-baseline-2026-09-27.md)
> and [ADR-024](docs/adr/024-model-artifact-deployment-capability-separation.md).

![Target platform overview](diagrams/gb10-platform.svg)

## What the platform is for

| Use case | Public route or alias | Intended capability |
| --- | --- | --- |
| Coding | `coding` | Codex-compatible editing, tools, builds, tests, and repository work |
| Automation | `automation` | n8n workflows, structured output, and approved MCP tools |
| Private research | `research` | Source-bounded synthesis over approved private or public material |
| Home Assistant | `home` | Danish/English conversation and safe tool proposals; Home Assistant executes actions |
| Meetings | `meeting` | Transcription, diarization, summaries, decisions, and action extraction |
| Personal agents | `assistant` | Isolated assistant sessions with explicit tool and data permissions |
| Speech to text | fixed STT route | Danish, English, and mixed-language transcription |
| Text to speech | language-specific TTS routes | Danish and English agent speech |

Logical aliases are stable contracts. A model may back several aliases only
after it passes each use case's quality, safety, latency, and recovery tests.

## GB10 model state

**CURRENT:** Qwen3.6 remains the production automation model at the immutable
revision shown above. Preserve its artifact as the rollback baseline. Its live
reasoning and runtime request tuple still needs metadata-only capture before
benchmarking.

**QUALITY BASELINE CANDIDATE:** NVIDIA NVFP4 Flash-Next with the pinned Blazux
hybrid runtime. **PERFORMANCE CHALLENGER:** dime-online UltraFast with a
different AutoRound W4A16 target and FP8 PLE table. Both remain isolated
qualification profiles. UltraFast must match Blazux on real HomeCompute quality
and reliability gates before any performance comparison can matter.

**TARGET (pending evidence and owner approval):** one resident general text
model shared across semantic aliases, with alias-specific access and reasoning
policies. Keep Hviske v5.3 for Danish STT and Plapre Nano v2 for Danish TTS.
Qwen3.8-27B remains a smaller cold fallback/workhorse artifact; routing is
undecided. Gemma and Qwen3-TTS are outside the target. Whisper is optional and
deferred to `home-core`.

The immutable model roster is
[`config/gb10-model-roster.json`](config/gb10-model-roster.json). ADR-023 records
the current Qwen3.6 automation decision. [ADR-025](docs/adr/025-flash-next-primary-candidate.md)
and the [benchmark report](docs/benchmarks/qwen36-vs-flash-next-automation.md)
track the pending Flash-Next qualification. Consumer-facing aliases and
candidate isolation remain governed by the separate
[`config/capability-routes.json`](config/capability-routes.json) registry.
The later production procedure remains gated in
[`docs/operations/flash-next-cutover.md`](docs/operations/flash-next-cutover.md).

## Setup in order

The complete operator path is in the [setup guide](docs/setup-guide.md). You
can follow it without reading the detailed plans.

Interactive use is private: approved local networks or Tailscale reach only
the authenticated gateway at `https://ai.home.arpa`. Apply the
[local/Tailscale access policy](docs/access-policy.md) before enabling remote
clients.

1. Record hostnames, IP addresses, DNS, the private compute subnet, backup
   target, and rollback owners.
2. Install the pinned NixOS 26.05 flake on `home-core`, then configure its
   observed LAN and private-compute interfaces declaratively.
3. Deploy and verify the digest-pinned Caddy/LiteLLM/PostgreSQL stack on
   loopback, then configure and restore-test off-host backup.
4. Prepare the supported DGX OS baseline on `home-spark` and configure its
   management and private-compute addresses.
5. Initialize, validate, and install the first pinned text-inference tuple with
   `setup-compute-node.sh`.
6. Connect the private link and allow only `home-core` to reach inference
   ports.
7. Point the already-loopback-tested gateway at the qualified compute endpoint,
   apply its exact ingress/egress policy, then expose `https://ai.home.arpa` to
   approved clients.
8. Capture the Qwen3.6 production request tuple, then qualify Flash-Next against
   Aula, offers, shopping categorization, and the complete speech mixed load.

The node baselines may be built in parallel. Gateway integration needs both
nodes, and no durable service should move before an off-host restore test.

## What the repository provides

- a guarded setup script for the vendor-managed compute node;
- a pinned NixOS host configuration with integrated Home Manager and sops-nix;
- a hardened control-plane Compose stack with state below `/srv/state`;
- guarded lifecycle tooling for the current Qwen3.6 route and isolated Flash-Next profiles;
- configuration templates that reject unresolved placeholders;
- architecture, detailed plans, verification criteria, risks, and research;
- editable D2 diagrams with rendered SVG and PNG versions.

It does not include an OS installer, credentials, model weights, container
images, production data, or completed benchmark evidence. It also does not yet
provide a turnkey gateway or application-service deployment.

## Prerequisites

For `home-core`:

- an x86 host with enough CPU, memory, storage, and two network ports;
- a public SSH key, reserved LAN/private-compute
  values, a persistent age identity, and an off-host backup target;
- reviewed immutable image digests and the matching compute API credential.

For `home-spark`:

- an NVIDIA GB10 or DGX Spark-class appliance with supported DGX OS;
- working NVIDIA drivers, Docker, Compose, and NVIDIA Container Toolkit;
- Bash, `curl`, `jq`, `openssl`, and standard Linux administration tools;
- pinned image/model revisions, provenance records, and external secret files.

## Safety boundaries

- Keep durable data off the rebuildable compute node.
- Keep inference on loopback until the private gateway path is ready.
- Never expose compute runtime ports, Docker, databases, or host management to
  the internet.
- Use separate, revocable client credentials at the gateway.
- Keep logs metadata-only; exclude prompts, outputs, audio, and credentials.
- Keep private workloads local unless a route explicitly allows cloud fallback.
- Let Home Assistant validate and execute home actions; the model only proposes
  calls to an allow-listed tool surface.

See the [risk analysis](docs/risk-analysis.md) before exposing a service or
migrating production state.

## Documentation

| Need | Document |
| --- | --- |
| Follow the full installation | [Setup guide](docs/setup-guide.md) |
| Understand the architecture | [Architecture](docs/architecture.md) |
| See the complete build order | [Platform execution plan](docs/platform-execution-plan.md) |
| Install the control-plane host | [NixOS control-plane plan](docs/nixos-control-plane-node-plan.md) |
| Apply, roll back, update, or extend NixOS | [NixOS operations guide](docs/nixos-operations.md) |
| Inspect compute-node details | [Compute-node plan](docs/ai-compute-node-plan.md) |
| Pilot Hermes on `home-core` | [ADR-017](docs/adr/017-consolidated-application-host.md), then [NemoClaw placement and Hermes setup](docs/research/nemoclaw-machine-placement.md) |
| Run acceptance tests | [Verification strategy](docs/verification-strategy.md) |
| Understand model choices | [Model recommendation](docs/research/llm-installation-recommendation.md) |

## Repository layout

| Directory | Contents |
| --- | --- |
| [`automations/`](automations/README.md) | Importable monitoring and operations workflow templates |
| [`config/`](config/README.md) | Operator configuration templates |
| [`hosts/`](hosts/home-core/default.nix) | Per-host NixOS entry points and hardware contracts |
| [`modules/nixos/`](modules/nixos/system.nix) | System configuration, networking, firewall, Docker, SSH, Tailscale, storage, backups, and secrets |
| [`home/`](home/mads/default.nix) | Home Manager user environments |
| [`deploy/`](deploy/README.md) | Docker Compose application workloads |
| [`diagrams/`](diagrams/README.md) | D2 sources and rendered diagrams |
| [`docs/`](docs/README.md) | Plans, requirements, verification, and research |
| [`scripts/`](scripts/README.md) | Guarded setup and operations commands |

## Development validation

```bash
./scripts/validate-repository.sh
```

See the [NixOS operations guide](docs/nixos-operations.md) for the staged
commit check, build/test/switch workflow, rollback, input updates, and extension
rules.

Target-host preflight, smoke, benchmark, recovery, and acceptance tests still
require the actual systems and operator-supplied configuration.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) and report security issues using
[SECURITY.md](SECURITY.md).

HomeCompute uses the [Apache License 2.0](LICENSE). Models, images, and other
third-party software keep their own licenses and are not redistributed here.

## Deploy the latest pushed code

From the operator workstation, push reviewed changes to `master` and deploy
the same latest GitHub revision to both hosts:

```bash
git push origin master
./scripts/homecompute deploy all
./scripts/homecompute status
```

Run deployment in a terminal so you can enter a remote sudo password if
prompted. See [HomeCompute operations](docs/homecompute-operations.md) for
preflight, single-host deployment, verification, and rollback details. The
lower-level [Git deployment notes](docs/git-deployment.md) document the
home-core release mechanism.
