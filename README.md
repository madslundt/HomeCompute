# HomeCompute

HomeCompute is a local-first AI platform for private inference, automation,
voice, meetings, coding, research, and personal agents.

The platform has two physical node roles:

- `ai-services-01` is an always-on x86 Proxmox host. It owns the gateway,
  automations, tools, databases, backups, and other durable state.
- `ai-compute-01` is an NVIDIA GB10 or DGX Spark-class appliance. It runs
  rebuildable text, speech, and diarization inference services.

Clients use `https://ai.home`. They do not call the compute node or concrete
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
| Text to speech | fixed TTS route | A qualified Danish voice for short and long responses |

Logical aliases are stable contracts. A model may back several aliases only
after it passes each use case's quality, safety, latency, and recovery tests.

## Model candidates

No production model has been selected. The repository starts with one efficient
shared text candidate, then compares specialists and controls one at a time.

| Candidate | What it is evaluated for |
| --- | --- |
| NVIDIA Qwen3.6 35B A3B NVFP4 | First text integration baseline across all six aliases; especially automation, research, home, meetings, and assistant tools |
| Qwen3-Coder-Next FP8 | Primary specialized coding candidate |
| NVIDIA Nemotron 3.5 Lightning NVFP4 | Efficient coding and agent-performance challenger |
| Gemma 4 31B NVFP4 and Qwen3.8 27B FP8 | Dense general, automation, and coding quality challengers |
| Devstral Small 2 | Lower-memory and latency control |
| gpt-oss-120b | Serialized high-capacity coding and automation comparison |
| Danish Parakeet and Whisper large-v3 variants | Speech-to-text candidates for home and meeting audio |
| Piper Danish and Røst | Danish text-to-speech reliability and naturalness candidates |
| Qwen3 Embedding and Reranker | Deferred private retrieval candidates, added only with a real corpus |

The first deployed text model is a smoke-test candidate, not a production
winner. See the [model recommendation](docs/research/llm-installation-recommendation.md)
for exact artifact names, order, and qualification caveats.

## Setup in order

The complete operator path is in the [setup guide](docs/setup-guide.md). You
can follow it without reading the detailed plans.

1. Record hostnames, IP addresses, DNS, the private compute subnet, backup
   target, and rollback owners.
2. Install Proxmox on `ai-services-01` and create the LAN and private-compute
   bridges.
3. Configure off-host backup, then provision and verify the three empty service
   VMs with `setup-services-node.sh`.
4. Prepare the supported DGX OS baseline on `ai-compute-01` and configure its
   management and private-compute addresses.
5. Initialize, validate, and install the first pinned text-inference tuple with
   `setup-compute-node.sh`.
6. Connect the private link and allow only `ai-gateway-01` to reach inference
   ports.
7. Install and qualify the gateway on `ai-gateway-01`, then expose
   `https://ai.home` to approved clients.
8. Benchmark model candidates and migrate automations or consumers one at a
   time, retaining rollback until each acceptance gate passes.

The node baselines may be built in parallel. Gateway integration needs both
nodes, and no durable service should move before an off-host restore test.

## What the repository provides

- guarded setup scripts for the services and compute nodes;
- a Proxmox cloud-init baseline for the three service VMs;
- a hardened Compose definition for the first compute-node text candidate;
- configuration templates that reject unresolved placeholders;
- architecture, detailed plans, verification criteria, risks, and research;
- editable D2 diagrams with rendered SVG and PNG versions.

It does not include an OS installer, credentials, model weights, container
images, production data, or completed benchmark evidence. It also does not yet
provide a turnkey gateway or application-service deployment.

## Prerequisites

For `ai-services-01`:

- a supported x86 host with enough CPU, memory, storage, and two network ports;
- Proxmox VE 9, VM and snippet storage, and LAN/private-compute bridges;
- a public SSH key, reserved network values, and an off-host backup target;
- the verified SHA-512 digest for the configured Debian 13 cloud image.

For `ai-compute-01`:

- an NVIDIA GB10 or DGX Spark-class appliance with supported DGX OS;
- working NVIDIA drivers, Docker, Compose, and NVIDIA Container Toolkit;
- Bash, `curl`, `jq`, `openssl`, and standard Linux administration tools;
- pinned image/model revisions, provenance records, and external secret files.

## Safety boundaries

- Keep durable data off the rebuildable compute node.
- Keep inference on loopback until the private gateway path is ready.
- Never expose compute runtime ports or Proxmox management to the internet.
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
| Inspect services-node details | [Services-node plan](docs/ai-services-node-plan.md) |
| Inspect compute-node details | [Compute-node plan](docs/ai-compute-node-plan.md) |
| Run acceptance tests | [Verification strategy](docs/verification-strategy.md) |
| Understand model choices | [Model recommendation](docs/research/llm-installation-recommendation.md) |

## Repository layout

| Directory | Contents |
| --- | --- |
| [`automations/`](automations/README.md) | Importable monitoring and operations workflow templates |
| [`config/`](config/README.md) | Operator configuration templates |
| [`deploy/`](deploy/README.md) | Compose and cloud-init artifacts |
| [`diagrams/`](diagrams/README.md) | D2 sources and rendered diagrams |
| [`docs/`](docs/README.md) | Plans, requirements, verification, and research |
| [`scripts/`](scripts/README.md) | Guarded setup and operations commands |

## Development validation

```bash
bash -n scripts/setup-compute-node.sh scripts/setup-services-node.sh
shellcheck scripts/setup-compute-node.sh scripts/setup-services-node.sh
d2 diagrams/gb10-platform.d2 /tmp/gb10-platform.svg
d2 diagrams/gb10-installation.d2 /tmp/gb10-installation.svg
```

Target-host preflight, smoke, benchmark, recovery, and acceptance tests still
require the actual systems and operator-supplied configuration.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) and report security issues using
[SECURITY.md](SECURITY.md).

HomeCompute uses the [Apache License 2.0](LICENSE). Models, images, and other
third-party software keep their own licenses and are not redistributed here.
