# HomeCompute documentation guide

> **Current priority (2026-09-04):** prepare and install home-core, then migrate
> inventoried HAOS supporting services one at a time. GB10 is not available.
> Follow the [home-core-first rollout](home-core-rollout-plan.md#start-now-without-gb10);
> non-AI migrations require host, backup/restore, networking, and application
> gates, but do not require GB10 or a new AI gateway. Home Assistant stays on
> HAOS and Hermes remains deferred.

This directory describes a local-first AI platform with two stable node roles:
`home-spark` is a rebuildable NVIDIA GB10 or DGX Spark-class inference
appliance.

`home-core` is an x86 NixOS host for the trusted Caddy/LiteLLM gateway and
its durable state. Because the platform has three machines rather than four, it
also carries automations and personal-agent sandboxes as separately isolated
Compose projects ([ADR-017](adr/017-consolidated-application-host.md)).

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
| Prepare `home-spark` | [AI compute node plan](ai-compute-node-plan.md), [setup guide](setup-guide.md), then verification |
| Prepare `home-core` | [NixOS control-plane plan](nixos-control-plane-node-plan.md), [ADR-016](adr/016-nixos-control-plane-host.md), then the root flake |
| Install `home-core` from bare metal | [NixOS installation runbook](nixos-install-runbook.md) |
| Get from a fresh install to running workloads | [`home-core` rollout plan](home-core-rollout-plan.md) |
| Apply or extend the NixOS configuration | [NixOS operations guide](nixos-operations.md) |
| Understand what is decided versus still hypothetical | [ADRs](#architecture-decisions), [current state](current-state.md), and the phase gates in the [implementation plan](implementation-plan.md) |
| Review security, privacy, and failure handling | [Access policy](access-policy.md), [personal data and memory](personal-data-and-memory.md), [requirements](requirements.md), and [risk analysis](risk-analysis.md) |
| Choose and benchmark models | [Benchmark harness](../benchmarks/README.md), [installation recommendation](research/llm-installation-recommendation.md), then the role-specific evaluations under [research](#research-and-model-evidence) |
| Run the explicit Codex local trial | [Codex GB10 Local trial](codex-local-trial.md), [Codex compatibility](research/codex-compatibility.md), and verification tests V-CODEX-TRIAL-001/V-CODEX-E2E-001 |
| Integrate other developer harnesses | [Agent harness operations](agent-harness-operations.md), [ADR-018](adr/018-multiple-developer-harnesses.md), and their verification tests |
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
| [AI compute node plan](ai-compute-node-plan.md) | `home-spark` installation, deployment, qualification, and operations |
| [NixOS control-plane plan](nixos-control-plane-node-plan.md) | Active `home-core` installation, Home Manager, Compose deployment, and acceptance path |
| [NixOS installation runbook](nixos-install-runbook.md) | Firmware, partitioning, hardware reconciliation, install, and first-console commands |
| [`home-core` rollout plan](home-core-rollout-plan.md) | Ordered post-install stages, blockers, and exit gates from secrets to the n8n migration |
| [Agent harness operations](agent-harness-operations.md) | Remote Pi/OMP access, tmux lifecycle, privilege boundary, and acceptance checks |
| [Codex GB10 Local trial](codex-local-trial.md) | Explicit Cloud/GB10 Local sessions, repository classification, and the real-task evidence gate |
| [NixOS operations guide](nixos-operations.md) | Commit checks, build/test/switch, rollback, input updates, and extension boundaries |
| [Local and Tailscale access](access-policy.md) | Private DNS/TLS, grants, network flows, administrative access, and acceptance checks |
| [Personal data and memory](personal-data-and-memory.md) | Principal/work domains, memory lifecycle, sharing, deletion, and administrator model |

## Architecture decisions

| ADR | Decision |
| --- | --- |
| [ADR-001](adr/001-gb10-inference-only.md) | `home-spark` is an inference-only appliance |
| [ADR-002](adr/002-inference-runtime.md) | Pinned NVIDIA-compatible vLLM is the first text runtime; llama.cpp is required as a quantized baseline |
| [ADR-003](adr/003-ai-api-boundary.md) | Consumers use a stable authenticated AI API boundary |
| [ADR-004](adr/004-model-aliases.md) | Consumers use logical aliases, never concrete artifact names |
| [ADR-005](adr/005-storage-strategy.md) | Internal storage is rebuildable and retains only active/rollback artifacts |
| [ADR-006](adr/006-codex-remains-primary-harness.md) | Historical Codex-only harness decision, superseded by ADR-018 |
| [ADR-007](adr/007-local-first-implementation.md) | Implementation is local-first with explicit cloud review/fallback |
| [ADR-008](adr/008-home-assistant-model-role.md) | Home Assistant has a distinct logical role and remains tool authority |
| [ADR-009](adr/009-logging-policy.md) | Production logs are metadata-only by default |
| [ADR-010](adr/010-cloud-fallback.md) | Cloud fallback is explicit and owned by orchestration/policy |
| [ADR-011](adr/011-reuse-ai-home-control-plane.md) | Reuse and qualify the existing AI Home Caddy/LiteLLM control plane |
| [ADR-012](adr/012-reuse-meeting-assistant.md) | Extend the existing Meeting Assistant for Plaud processing |
| [ADR-013](adr/013-hermes-personal-agent-layer.md) | Hermes runs outside `home-spark` as a separately gated application layer |
| [ADR-014](adr/014-ai-services-node.md) | Historical, superseded services-node virtualization design |
| [ADR-015](adr/015-personal-data-domains-and-memory.md) | Personal memory and employer data use explicit principals, domains, and lifecycle controls |
| [ADR-016](adr/016-nixos-control-plane-host.md) | NixOS, integrated Home Manager, sops-nix, and Compose define the control-plane host |
| [ADR-017](adr/017-consolidated-application-host.md) | `home-core` also hosts automations and personal agents; container isolation replaces the separate application host |
| [ADR-018](adr/018-multiple-developer-harnesses.md) | Codex, Pi, and OMP are supported behind the same restricted developer account boundary |
| [ADR-019](adr/019-single-gb10-model-roster.md) | Final GX10 roster: Qwen3.8-27B workhorse, exclusive Flash-Next cold swap, and four speech models |
| [ADR-020](adr/020-workflow-first-local-inference.md) | Workflow-first rollout: local-only home-core routing, Aula first, explicit local Codex trials, and deferred features |
| [ADR-021](adr/021-final-speech-stack-and-routing.md) | Final 2+2 speech roster, language routing, isolated runtimes, licenses, and qualification gates |

## Research and model evidence

Start with [ADR-019](adr/019-single-gb10-model-roster.md) and the
[machine-readable roster](../config/gb10-model-roster.json). Earlier model
recommendations remain historical evaluation evidence. Qwen3.8-27B is the
selected workhorse and Flash-Next is the exclusive cold-swap heavy lane.
Routing remains disabled and aliases unbound until the pinned vLLM/native-MTP
tuple passes live qualification; SGLang/DFlash is a runtime comparison, not a
second general-purpose model.
The dated [Flash-Next review](research/qwen3.8-flash-next-primary-model-evaluation-2026-09-11.md)
documents the single-GB10 runtime evidence behind that resident-versus-heavy
split.

The selected speech stack is Hviske v5.3 for Danish STT, Whisper large-v3-turbo
for English/mixed/unknown STT, Plapre Nano v2 for Danish TTS, and Qwen3-TTS
0.6B CustomVoice for supported non-Danish TTS. Piper remains the Danish CPU
fallback. The older combined Whisper/Piper scaffold is not the final isolated
speech deployment.

| Topic | Documents |
| --- | --- |
| Platform and runtime | [GB10 appliance validation](research/gx10-platform-validation.md), [runtime evaluation](research/inference-runtime-evaluation.md), [gateway evaluation](research/gateway-evaluation.md), [control-plane runtime split review](research/control-plane-runtime-split-review.md) |
| Current model shortlist | [ADR-019](adr/019-single-gb10-model-roster.md), [machine-readable roster](../config/gb10-model-roster.json), [Flash-Next single-GB10 review](research/qwen3.8-flash-next-primary-model-evaluation-2026-09-11.md) |
| Text roles | [General](research/general-model-evaluation.md), [coding](research/coding-model-evaluation.md), [Home Assistant](research/home-assistant-model-evaluation.md) |
| Speech | [Danish TTS qualification](tts-qualification.md), [STT](research/stt-model-evaluation.md), [TTS](research/tts-model-evaluation.md), [Danish TTS recommendation](research/danish-tts-recommendation.md) |
| Integrations | [Codex compatibility](research/codex-compatibility.md), [Hermes verification](research/hermes-personal-assistant-verification.md) |

## Repository artifacts

The documentation is paired with a deliberately small implementation scaffold:

| Path | Purpose |
| --- | --- |
| `../flake.nix` and `../flake.lock` | Pinned NixOS, Home Manager, and sops-nix activation graph |
| `../hosts/home-core/` | Control-plane host entry point and hardware contract |
| `../modules/nixos/` | Machine-level configuration modules |
| `../home/mads/` | User-level Home Manager modules |
| `../config/compute-node.env.example` | Explicit, pinned release inputs for the first candidate |
| `../deploy/compute-node/compose.yaml` | Hardened Phase C vLLM service definition |
| `../scripts/setup-compute-node.sh` | Preflight, initialization, validation, deployment, smoke, status, stop, and rollback commands |
| `../deploy/control-plane/compose.yaml` | Caddy, LiteLLM, and PostgreSQL application workload |
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
