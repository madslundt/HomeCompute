# HomeCompute

> **Current priority (2026-09-04):** prepare and install home-core, then migrate
> inventoried HAOS supporting services one at a time. GB10 is not available.
> Follow the [home-core-first rollout](docs/home-core-rollout-plan.md#start-now-without-gb10);
> non-AI migrations require host, backup/restore, networking, and application
> gates, but do not require GB10 or a new AI gateway. Home Assistant stays on
> HAOS and Hermes remains deferred.

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

> **Status:** this repository is a guarded deployment scaffold, not a turnkey
> installer. Physical installation and production qualification have not been
> completed.

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

## GB10 model roster

The final steady state is two text models at most, with only one resident:
Qwen3.8-27B is the normal production workhorse and Flash-Next is an exclusive
operator-controlled heavy mode. Native MTP on vLLM is established first, then
SGLang/DFlash is kept only if it is materially better on real workloads.

| Role | Selection |
| --- | --- |
| Normal text inference | `unsloth/Qwen3.8-27B-NVFP4`; vLLM/native MTP baseline, then SGLang with the `incoai` DFlash2 drafter |
| Heavy coding and research | `RadixArk/Qwen3.8-Flash-Next-NVFP4` through the pinned `blazux` single-GB10 recipe |
| Danish STT | `syvai/hviske-v5.3` |
| English, mixed, or unknown STT | `openai/whisper-large-v3-turbo` |
| Danish TTS | `syvai/plapre-nano-v2` |
| English and supported non-Danish TTS | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` |

Piper `da_DK-talesyntese-medium` remains the independent Danish CPU fallback;
Community-1 diarization is supporting-only and Hviske Tiny is evaluation-only.
Speech services remain inactive until qualification.
The immutable machine-readable policy is
[`config/gb10-model-roster.json`](config/gb10-model-roster.json); see the
[speech ADR](docs/adr/021-final-speech-stack-and-routing.md) for language,
runtime, license, voice-consent, and qualification caveats.

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
8. Benchmark the two Qwen3.8-27B runtime profiles, then migrate automations or
   consumers one at a time, retaining rollback until each gate passes.

The node baselines may be built in parallel. Gateway integration needs both
nodes, and no durable service should move before an off-host restore test.

## What the repository provides

- a guarded setup script for the vendor-managed compute node;
- a pinned NixOS host configuration with integrated Home Manager and sops-nix;
- a hardened control-plane Compose stack with state below `/srv/state`;
- a hardened Compose definition for the selected Qwen3.8 vLLM/MTP baseline;
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

## Deploy the current K15 configuration

See [the Git deployment workflow](docs/git-deployment.md): publish from the
MacBook, then deploy an exact commit on home-core using read-only GitHub access.
