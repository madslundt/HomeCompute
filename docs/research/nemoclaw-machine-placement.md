# NemoClaw machine-placement recommendation

**Research date:** 2026-08-30  
**Status:** Technical research retained; production placement superseded by
[ADR-016](../adr/016-nixos-control-plane-host.md)
**Scope:** What NemoClaw is, its requirements and security boundaries, and how
Hermes should be placed across the planned K15 services node and GX10/GB10
compute node. No machine or configuration was changed.

## Outcome

Do **not** deploy NemoClaw broadly or make it production-critical yet. ADR-016
removes the planned VM substrate from `ai-services-01`, so the personal-agent
pilot is blocked until a separate application host and trust domain are chosen.
That host should follow NVIDIA's validated operating-system path and route its
inference through `ai.home.arpa` to GB10.

A direct NemoClaw install on the ASUS GX10/GB10 is technically the most
strongly validated demo path: NVIDIA marks DGX OS on a single DGX Spark as
**Tested**, provides a Spark playbook, and offers managed vLLM with
`nvidia/Qwen3.6-35B-A3B-NVFP4`. It is still the wrong production placement for
this project. NemoClaw hosts an agent and persistent sandbox state; it is not
an inference runtime. Keeping that state and the agent loop on GB10 would
violate the accepted inference-only boundary and compete with the text, speech,
meeting, Home Assistant, and Codex workloads. A short-lived, non-sensitive
compatibility demo on GB10 is reasonable, then it should be removed or rebuilt
on the application host. [NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support),
[NVIDIA DGX Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemoclaw/README.md),
[local ADR-001](../adr/001-gb10-inference-only.md),
[local ADR-013](../adr/013-hermes-personal-agent-layer.md)

NemoClaw is moving too quickly for an unattended production promise. The
current documentation labels it **alpha / Early preview**, offers no production
SLA, and the latest release at the research date is v0.0.116 from 2026-08-28.
[NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support),
[NVIDIA release notes](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/release-notes)

## What NemoClaw is

NemoClaw is NVIDIA's open-source reference stack for running always-on AI
agents inside NVIDIA OpenShell sandboxes. It installs and configures OpenShell,
onboards an agent, routes inference, manages credentials and egress policy, and
provides lifecycle operations. It does **not** replace vLLM, LiteLLM, Codex,
n8n, or the agent itself. [NVIDIA NemoClaw overview](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/home),
[official repository](https://github.com/NVIDIA/NemoClaw)

The current supported agents are:

| Agent | NVIDIA status | Relevance here |
| --- | --- | --- |
| OpenClaw | Tested; default | Optional general always-on assistant, not a replacement for the repository's Codex development harness. |
| Hermes | Tested; not default | The repository's intended personal-agent layer. NVIDIA says it is suitable for evaluation and documented onboarding, but production parity with OpenClaw is not asserted. |
| LangChain Deep Agents Code | Tested; not default | Optional terminal coding-agent experiment. It does not displace Codex without a separate decision and acceptance record. |

Source: [NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support).

NemoClaw can use hosted providers, a custom OpenAI-compatible endpoint, local
Ollama, an already-running local vLLM server, or supported managed-local
options. A custom compatible endpoint is only **Tested with limitations**
because proxy and server behavior varies. Therefore `ai.home.arpa` is a candidate
route, not assumed compatible: qualify its Chat Completions/Responses mode,
streaming, tool calls, authentication, privacy, and 64K Hermes behavior as one
pinned path. Managed vLLM is offered normally on DGX Spark; starting managed
vLLM on a generic NVIDIA Linux host remains experimental. [NVIDIA inference-provider matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support#inference-providers)

## Host and runtime requirements

| Resource | Minimum | Recommended |
| --- | --- | --- |
| CPU | 4 vCPU | 4+ vCPU |
| RAM | 8 GB | 16 GB |
| Free disk | 20 GB | 40 GB |

The documented software baseline is Node.js 22.19 or later, npm 10 or later,
trusted system Python 3 with POSIX descriptor-relative filesystem support,
Docker Engine/Desktop or Colima on a tested platform, and the documented hash,
compression, and binary utilities. GPU hardware is **not required** when the
agent uses hosted or remote inference. GPU-backed local vLLM/NIM requires an
eligible NVIDIA GPU plus the NVIDIA Container Toolkit and healthy CDI;
NemoClaw does not support AMD, Intel, or Apple GPUs for those local NVIDIA
paths. [NVIDIA prerequisites](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/get-started/prerequisites),
[NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support)

Only these NVIDIA platform results affect the planned machines:

| Target | Status | Important qualification |
| --- | --- | --- |
| DGX OS on one DGX Spark/GB10 | Tested | Strongest local-inference path; the automatic two-Spark profile is experimental and lacks completed physical two-node end-to-end validation. |
| Generic Linux with Docker | Tested | Ubuntu 24.04 has host-level onboarding validation. Other distributions may work but do not inherit that validation. |
| Headless Linux server over SSH | Tested with limitations | Reboot recovery is manual; NemoClaw does not guarantee Docker, gateway, sandbox, tunnel, or forward auto-start. |

Sources: [NVIDIA prerequisites](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/get-started/prerequisites),
[NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support),
[headless-server guide](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/deployment/deploy-to-headless-server).

## Security and isolation implications

NemoClaw/OpenShell is a meaningful improvement over running an autonomous
agent directly on a host. OpenShell runs the agent child as an unprivileged
user and layers Landlock filesystem policy, reduced process privilege,
seccomp, a network namespace, and a policy proxy that evaluates destination,
binary identity, HTTP/TLS rules, SSRF restrictions, and inference routing.
NemoClaw configures deny-by-default egress and can keep provider credentials in
the OpenShell gateway, substituting them only at an approved egress boundary so
the sandbox sees placeholders instead of raw secrets. [OpenShell sandbox architecture](https://github.com/NVIDIA/OpenShell/blob/main/architecture/sandbox.md),
[NemoClaw security posture](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/best-practices),
[NemoClaw credential storage](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/credential-storage)

It is still **risk reduction, not permission to trust the agent**:

- Docker access is effectively root-level host authority. Do not install it on
  `ai-services-01` or another critical control-plane host merely to run
  NemoClaw. [NVIDIA headless-server guide](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/deployment/deploy-to-headless-server)
- The default Balanced onboarding tier allows package-registry presets. For an
  always-on private assistant, start with Restricted, no web search, and add
  one endpoint at a time. The Personal tier allows arbitrary TCP egress on
  destination ports 80 and 443 and is unsuitable for private household data.
  [NemoClaw security posture](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/best-practices)
- OpenClaw configuration is writable by default. NVIDIA recommends a reviewed
  host-side immutability workflow for sensitive always-on workloads because a
  writable configuration can redirect inference or weaken settings.
  [NemoClaw filesystem controls](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/security-controls/filesystem-controls)
- Multi-user host sharing is unsupported. One operator should own the host;
  household trust domains still need separate OpenShell sandboxes, credentials,
  data scopes, and negative tests below the model.
  [NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support)
- The current platform matrix documents a remaining capability-bounding caveat
  for interactive `connect` shells on hosts without `CAP_SETPCAP`, including
  Docker Desktop and WSL. Do not treat every supported platform as having
  identical effective isolation. [NVIDIA platform matrix](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support#known-caveats-and-active-blockers)
- Agent shell approval and network-egress approval do not authorize an email,
  calendar change, Home Assistant write, deletion, or financial action. Keep
  those behind the repository's separate authenticated proposal/approval
  workflow. [Local Hermes verification](hermes-personal-assistant-verification.md)

## Recommendation by planned machine role

| Machine or role | Recommendation | Reason |
| --- | --- | --- |
| Separately qualified application host | **Required before the pilot.** | Keep autonomous agent state outside `ai-services-01`; meet NVIDIA's sizing and platform requirements, route inference to `ai.home.arpa`, and prove backup/restore. |
| Automation hosts | **No personal-agent state.** | Keep deterministic workflows, MCP services, queues, and their credentials separate from autonomous agent sandboxes. |
| `ai-compute-01` ASUS GX10 / GB10 | **Demo only, not production.** | It is the strongest NVIDIA-supported Express/local-vLLM target, but persistent agent state and lifecycle services violate the accepted inference-only boundary and add contention. |
| `ai-services-01` NixOS host | **No.** | Do not mix an early-preview autonomous agent with the authentication, secrets, and routing control plane. |
| Toolbox hosts | **Only a disposable coding-agent experiment.** | The role is appropriate for untrusted experiments but must not receive production household credentials or databases. |

These are the only planned roles needed for the pilot. Home Assistant remains
an external consumer and action authority; it is not a NemoClaw host. The local
role evidence is in the [NixOS control-plane plan](../nixos-control-plane-node-plan.md) and
[platform execution plan](../platform-execution-plan.md).

## Hermes pilot setup path

This repository does not yet automate Hermes or NemoClaw installation. The
operator path is:

1. Finish the K15 `ai-services-01` NixOS baseline, the GX10/GB10
   `ai-compute-01` baseline, and the `ai.home.arpa` gateway.
2. Select a separate application host with at least NVIDIA's recommended
   resources and a supported or explicitly qualified operating-system path.
3. Install one pinned NemoClaw/OpenShell tuple there and onboard Hermes using
   NVIDIA's documented integration. Do not install the agent runtime on
   `ai-services-01` or `ai-compute-01`.
4. Configure the Hermes inference provider to use a dedicated, revocable
   `assistant` credential at `https://ai.home.arpa`. The gateway routes that alias to
   the qualified GB10 model; Hermes never receives a direct compute-node URL.
5. Create one synthetic-data `owner` sandbox with Restricted policy,
   deny-by-default egress, no host bind mounts, and no consequential-action
   credentials. Keep Hermes state on the application VM and include it in the
   encrypted snapshot/restore procedure.
6. Verify the live policy, streaming and tool calls, 64K context, failure
   behavior, reboot recovery, snapshot/restore, and GB10 mixed-load impact.
7. Add `partner` and `family` only as separate OpenShell sandboxes, credentials,
   state roots, and data scopes after the single-sandbox pilot passes every
   exit gate below.

The exact install commands must come from the pinned NemoClaw release being
qualified; do not copy floating `latest` commands into the production runbook.
The detailed behavioral and isolation tests are in the
[Hermes verification plan](hermes-personal-assistant-verification.md).

## Pilot exit gates

Do not add real household data merely because onboarding succeeds. Promotion
requires all of the following:

1. Pin the NemoClaw release, OpenShell release, agent image/digest, policy,
   inference endpoint, model revision, and context settings as one tuple.
2. Use one synthetic `owner` sandbox, Restricted policy, no web search, no
   cloud fallback, no host bind mounts, and no consequential-action credentials.
3. Prove allowed and denied filesystem, process, LAN, internet, inference, and
   credential paths against the **effective live policy**.
4. Verify inference through direct GB10 vLLM, then through `ai.home.arpa`, including
   streaming, tools, failures, private-data logging, and Hermes' 64K context.
5. Test reboot recovery, upgrade/rollback, snapshot/restore, forced destroy,
   Docker/gateway/GB10 outages, and a 24-hour mixed-load soak.
6. Add separate partner/family sandboxes only after adversarial tests prove
   memory, session, credential, API, database-scope, and notification isolation.

NVIDIA's headless deployment path does not guarantee automatic recovery after
reboot, and NemoClaw sandboxes persist agent workspace/state across ordinary
restarts. Operational recovery and backups therefore remain application-host
responsibilities, not reasons to move the state onto GB10.
[NVIDIA headless-server guide](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/deployment/deploy-to-headless-server),
[NVIDIA sandbox-state guide](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/manage-sandboxes/state-and-backups/understand-sandbox-state)

## Final answer

Run NemoClaw only after selecting a separately qualified application host for
the personal-agent pilot, using the GB10 only for inference. Use the GB10 itself
only for NVIDIA's short-lived compatibility demo. Do not install NemoClaw on
`ai-services-01`, and do not call the pilot production-ready until
the isolation, recovery, 64K, and mixed-load gates pass.
