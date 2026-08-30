# HomeCompute documentation guide

This directory describes a local-first AI platform with two stable node roles:
`ai-compute-01` is the rebuildable ASUS GX10/NVIDIA GB10 inference appliance;
`ai-services-01` is the GMKtec/Proxmox host for the gateway, automations, tools,
and durable application state. Existing services remain in place until their
replacement gates pass.

The stable node roles are `ai-compute-01` and `ai-services-01`; neither is yet
an observed production deployment. Start with the
[platform execution plan](platform-execution-plan.md), then use the separate
[compute-node](ai-compute-node-plan.md) and
[services-node](ai-services-node-plan.md) runbooks.

## Current gate and next action

The repository is at **Phase B review / Phase C0 preparation**. An independent
documentation, diagram, configuration, and script review has been applied; user
design approval and `ai-compute-01` access are still required. No production model,
gateway path, speech service, or personal-agent deployment has passed
acceptance yet.

After design approval, the next action is to resolve the immutable
image/model/template provenance inputs in `config/compute-node.env.example` on the
actual GX10, then run the read-only preflight and validation from the
[setup guide](setup-guide.md). Do not begin
Home Assistant, audio, Meeting Assistant, or Hermes migration before the
preceding gates pass.

## Start here

| If you want to… | Read these documents |
| --- | --- |
| Understand the platform in ten minutes | [Architecture](architecture.md), then [current state](current-state.md) |
| Prepare the first compute deployment | [Setup guide](setup-guide.md), [implementation plan](implementation-plan.md), then [verification strategy](verification-strategy.md) |
| Execute the complete two-node build | [Platform execution plan](platform-execution-plan.md), then both node plans |
| Prepare `ai-compute-01` | [AI compute node plan](ai-compute-node-plan.md), [setup guide](setup-guide.md), then verification |
| Prepare `ai-services-01` | [AI services node plan](ai-services-node-plan.md), [ADR-014](adr/014-ai-services-node.md), then the provisioning script |
| Understand what is decided versus still hypothetical | [ADRs](#architecture-decisions), [current state](current-state.md), and the phase gates in the [implementation plan](implementation-plan.md) |
| Review security, privacy, and failure handling | [Requirements](requirements.md), [risk analysis](risk-analysis.md), and [design specification](design-specification.md) |
| Choose and benchmark models | [Installation recommendation](research/llm-installation-recommendation.md), then the role-specific evaluations under [research](#research-and-model-evidence) |
| Integrate Codex | [Codex compatibility](research/codex-compatibility.md), [ADR-006](adr/006-codex-remains-primary-harness.md), and verification tests V-CODEX-001/V-CODEX-E2E-001 |
| Integrate Home Assistant voice and tools | [Home Assistant model evaluation](research/home-assistant-model-evaluation.md), [ADR-008](adr/008-home-assistant-model-role.md), and verification tests V-HA-001/V-HA-002 |
| Extend Meeting Assistant or process Plaud recordings | [ADR-012](adr/012-reuse-meeting-assistant.md), the meeting sections in [architecture](architecture.md) and [verification](verification-strategy.md) |
| Add the Hermes personal assistant layer | [Hermes verification](research/hermes-personal-assistant-verification.md), [ADR-013](adr/013-hermes-personal-agent-layer.md), and Phases I/J in the [implementation plan](implementation-plan.md) |

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
| [Setup guide](setup-guide.md) | Safe Phase C deployment and operating commands |
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
The current first text candidate is `nvidia/Qwen3.6-35B-A3B-NVFP4`, to be
qualified with MTP on and off. `nvidia/Gemma-4-31B-IT-NVFP4`, Qwen3-Coder-Next, and
Qwen3.8-27B are challengers for different roles, not preselected resident
models.

| Topic | Documents |
| --- | --- |
| Platform and runtime | [GX10 validation](research/gx10-platform-validation.md), [runtime evaluation](research/inference-runtime-evaluation.md), [gateway evaluation](research/gateway-evaluation.md) |
| Current model shortlist | [Installation recommendation](research/llm-installation-recommendation.md), [omission review](research/model-shortlist-omission-review.md), [MTP/claim review](research/mtp-model-claim-review.md) |
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
