# HomeCompute documentation guide

This directory describes a local-first AI platform with two stable node roles:
`ai-compute-01` is a rebuildable NVIDIA GB10 or DGX Spark-class inference
appliance.

`ai-services-01` is an x86 Proxmox host for the gateway, automations, tools,
and durable application state.

Existing services remain in place until their replacements pass. Neither node
is yet an observed production deployment.

Start with the [setup guide](setup-guide.md). It contains the complete ordered
path for both nodes. Use the detailed plans only when you need rationale,
edge-case handling, or full acceptance criteria.

## Current gate and next action

The repository is at **Phase B review / Phase C0 preparation**. Design approval
and access to the physical nodes are still required. No production model,
gateway path, speech service, or personal-agent deployment has passed yet.

After approval, use the setup guide to build the empty services baseline and
the direct compute baseline. Do not migrate Home Assistant, audio, Meeting
Assistant, automations, or personal agents before their preceding gates pass.

## Start here

| If you want to… | Read these documents |
| --- | --- |
| Set up both nodes in order | [Setup guide](setup-guide.md) |
| Understand the platform in ten minutes | [Architecture](architecture.md), then [current state](current-state.md) |
| Execute the complete two-node program | [Platform execution plan](platform-execution-plan.md), then both node plans |
| Prepare `ai-compute-01` | [AI compute node plan](ai-compute-node-plan.md), [setup guide](setup-guide.md), then verification |
| Prepare `ai-services-01` | [AI services node plan](ai-services-node-plan.md), [ADR-014](adr/014-ai-services-node.md), then the provisioning script |
| Understand what is decided versus still hypothetical | [ADRs](#architecture-decisions), [current state](current-state.md), and the phase gates in the [implementation plan](implementation-plan.md) |
| Review security, privacy, and failure handling | [Requirements](requirements.md), [risk analysis](risk-analysis.md), and [design specification](design-specification.md) |
| Choose and benchmark models | [Installation recommendation](research/llm-installation-recommendation.md), then the role-specific evaluations under [research](#research-and-model-evidence) |
| Integrate Codex | [Codex compatibility](research/codex-compatibility.md), [ADR-006](adr/006-codex-remains-primary-harness.md), and verification tests V-CODEX-001/V-CODEX-E2E-001 |
| Integrate Home Assistant voice and tools | [Home Assistant model evaluation](research/home-assistant-model-evaluation.md), [ADR-008](adr/008-home-assistant-model-role.md), and verification tests V-HA-001/V-HA-002 |
| Extend Meeting Assistant or process Plaud recordings | [ADR-012](adr/012-reuse-meeting-assistant.md), the meeting sections in [architecture](architecture.md) and [verification](verification-strategy.md) |
| Add the Hermes personal assistant layer | [NemoClaw placement and Hermes setup](research/nemoclaw-machine-placement.md), [Hermes verification](research/hermes-personal-assistant-verification.md), [ADR-013](adr/013-hermes-personal-agent-layer.md), and Phases I/J in the [implementation plan](implementation-plan.md) |

The two rendered overview diagrams are also useful entry points:

- [Target platform diagram](../diagrams/gb10-platform.svg)
- [Installation and qualification path](../diagrams/gb10-installation.svg)

## Document authority

The repository separates requirements, decisions, design, plans, evidence, and
generated acceptance results:

1. [Requirements](requirements.md) define what an accepted system must do.
2. Files under [ADR](#architecture-decisions) record architectural decisions
   and their status.
3. [Architecture](architecture.md) and the [design specification](design-specification.md)
   define boundaries, interfaces, security, storage, and deployment behavior.
4. The [implementation plan](implementation-plan.md) orders work behind gates;
   it does not mean later phases already exist.
5. The [verification strategy](verification-strategy.md) defines the evidence
   required to pass those gates.
6. Files under `research/` are dated evidence and candidate evaluations. A
   publisher benchmark or user report can add a candidate, but cannot select a
   production model.
7. Future `docs/benchmarks/` records will contain measured results from the
   actual pinned compute-node tuples and become the authority for promotion.

When two research notes differ, prefer the newer verified note and the staged
[installation recommendation](research/llm-installation-recommendation.md),
then reconcile the older note rather than silently carrying both conclusions.

## Core design set

| Document | Purpose |
| --- | --- |
| [Current state](current-state.md) | What exists today, what can be reused, and what remains unverified |
| [Requirements](requirements.md) | Functional, performance, privacy, operations, and acceptance requirements |
| [Architecture](architecture.md) | System context, deployment, request flows, boundaries, routing, and failure domains |
| [Design specification](design-specification.md) | Concrete artifact, API, scheduling, security, storage, and rollback design |
| [Dependency graph](dependency-graph.md) | Phase, component, consumer, and critical-input dependencies |
| [Risk analysis](risk-analysis.md) | Ranked risks, mitigations, owners, evidence, and review triggers |
| [Implementation plan](implementation-plan.md) | Phase A–J execution order and promotion gates |
| [Verification strategy](verification-strategy.md) | Acceptance suites and requirements traceability |
| [Setup guide](setup-guide.md) | Self-contained, ordered setup path for both physical nodes |
| [Platform execution plan](platform-execution-plan.md) | Controlling order, names, dependencies, gates, and definition of done |
| [AI compute node plan](ai-compute-node-plan.md) | `ai-compute-01` installation, deployment, qualification, and operations |
| [AI services node plan](ai-services-node-plan.md) | `ai-services-01` installation, VMs, networking, backups, and migrations |

## Architecture decisions

| ADR | Decision |
| --- | --- |
| [ADR-001](adr/001-gb10-inference-only.md) | `ai-compute-01` is an inference-only appliance |
| [ADR-002](adr/002-inference-runtime.md) | Pinned NVIDIA-compatible vLLM is the first text runtime; llama.cpp is required as a quantized baseline |
| [ADR-003](adr/003-ai-api-boundary.md) | Consumers use a stable authenticated AI API boundary |
| [ADR-004](adr/004-model-aliases.md) | Consumers use logical aliases, never concrete artifact names |
| [ADR-005](adr/005-storage-strategy.md) | Internal storage is rebuildable and retains only active/rollback artifacts |
| [ADR-006](adr/006-codex-remains-primary-harness.md) | Codex remains the developer harness |
| [ADR-007](adr/007-local-first-implementation.md) | Implementation is local-first with explicit cloud review/fallback |
| [ADR-008](adr/008-home-assistant-model-role.md) | Home Assistant has a distinct logical role and remains tool authority |
| [ADR-009](adr/009-logging-policy.md) | Production logs are metadata-only by default |
| [ADR-010](adr/010-cloud-fallback.md) | Cloud fallback is explicit and owned by orchestration/policy |
| [ADR-011](adr/011-reuse-ai-home-control-plane.md) | Reuse and qualify the existing AI Home Caddy/LiteLLM control plane |
| [ADR-012](adr/012-reuse-meeting-assistant.md) | Extend the existing Meeting Assistant for Plaud processing |
| [ADR-013](adr/013-hermes-personal-agent-layer.md) | Hermes runs outside `ai-compute-01` as a separately gated application layer |
| [ADR-014](adr/014-ai-services-node.md) | `ai-services-01` runs a minimal Proxmox host with three role-separated Debian VMs |

## Research and model evidence

Start with [the staged LLM installation recommendation](research/llm-installation-recommendation.md).
The first text integration candidate is NVIDIA Qwen3.6 35B A3B NVFP4. It
exercises all aliases, but it is not a production selection.

Qwen3-Coder-Next is the primary coding specialist. Nemotron 3.5 Lightning is
the efficient agent/coding challenger. Gemma 4, Qwen3.8, Devstral, and gpt-oss
provide dense, low-resource, or high-capacity comparisons.

Danish Parakeet and Whisper are the STT candidates. Piper Danish and Røst are
the TTS candidates. Retrieval models remain deferred until a real private
corpus and access-control test exist.

| Topic | Documents |
| --- | --- |
| Platform and runtime | [GB10 appliance validation](research/gx10-platform-validation.md), [runtime evaluation](research/inference-runtime-evaluation.md), [gateway evaluation](research/gateway-evaluation.md) |
| Current model shortlist | [Installation recommendation](research/llm-installation-recommendation.md), [GB10 precision audit](research/gb10-optimized-model-audit.md), [alignment review](research/model-use-case-alignment-review.md) |
| Text roles | [General](research/general-model-evaluation.md), [coding](research/coding-model-evaluation.md), [Home Assistant](research/home-assistant-model-evaluation.md) |
| Speech | [STT](research/stt-model-evaluation.md), [TTS](research/tts-model-evaluation.md), [Danish TTS recommendation](research/danish-tts-recommendation.md) |
| Integrations | [Codex compatibility](research/codex-compatibility.md), [Hermes verification](research/hermes-personal-assistant-verification.md) |

## Repository artifacts

The documentation is paired with a deliberately small implementation scaffold:

| Path | Purpose |
| --- | --- |
| `../config/compute-node.env.example` | Explicit, pinned release inputs for the first candidate |
| `../deploy/compute-node/compose.yaml` | Hardened Phase C vLLM service definition |
| `../scripts/setup-compute-node.sh` | Preflight, initialization, validation, deployment, smoke, status, stop, and rollback commands |
| `../config/services-node.env.example` | Services-node Proxmox template, network, storage, and VM inputs |
| `../deploy/services-node/cloud-init-vendor.yaml` | Common hardened Debian guest baseline |
| `../scripts/setup-services-node.sh` | Services-node validation, host baseline, cloud template, and VM provisioning |
| `../diagrams/*.d2` | Editable D2 sources |
| `../diagrams/*.svg` and `*.png` | Rendered diagrams for docs and quick viewing |

The scaffold is not a claim that the complete platform is deployed. The first
safe mutation is still gated by an immutable image digest, exact revisions,
license/provenance, firewall/VPN evidence, and target-hardware validation.

## Maintaining the documentation

- Update the D2 source and regenerate both SVG and PNG outputs after a diagram
  change.
- Keep model, runtime, parser, MTP, quantization, context, and launch flags
  together as one qualified tuple.
- Add a dated research note when evidence changes, then reconcile the canonical
  shortlist and role pages in the same change.
- Add benchmark summaries only from sanitized fixtures; never commit prompts,
  credentials, meeting content, Home Assistant secrets, or personal data.
- Check relative links, D2 rendering, shell syntax, ShellCheck, and rendered
  Compose configuration before treating documentation as release-ready.
