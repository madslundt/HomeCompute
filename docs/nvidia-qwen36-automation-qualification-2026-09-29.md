# NVIDIA Qwen3.6 automation qualification, 2026-09-29

The user selected `nvidia/Qwen3.6-35B-A3B-NVFP4` as the replacement for the
Unsloth Qwen3.6 automation service. The candidate is pinned to revision
`1355db6a052410cfd62085d94b58866fd0f2c3c5` and serves with vLLM 0.28.0,
Marlin MoE, FlashInfer attention, FP8 KV cache, and three MTP draft tokens.
Both builds used temperature 0, explicit thinking enabled, a 4,096-token
thinking budget, 8,192 maximum output tokens, and JSON-object output for the
same six frozen Aula prompts. No retries were used. The exact prompts and
responses were kept outside the repository because they contain family data.

| Frozen case | Unsloth seconds | NVIDIA seconds | Observation |
| --- | ---: | ---: | --- |
| Afternoon | 63.4 | 39.3 | Unsloth returned `{}`; NVIDIA returned two relevant sourced facts. |
| New delta | 67.4 | 34.8 | Both found the new relevant item. |
| Empty delta | 42.5 | 41.4 | Unsloth returned `no_changes`; NVIDIA repeated old items. The workflow's source timestamp filter removes them. |
| Weekly preparation | 81.5 | 44.6 | NVIDIA found the essential items but added routine lessons. The workflow now filters those lessons and restricts dashboard items to next-school-day actions. |
| Stale relative date | 70.0 | 35.4 | Both omitted the stale message and kept the relevant next-day item. |
| Afternoon repeat | 63.7 | 40.8 | Unsloth again returned `{}`; NVIDIA again returned the relevant facts. |

All twelve requests returned HTTP 200 and completed normally. Median warm
latency was 65.5 seconds for Unsloth and 40.0 seconds for NVIDIA, a 1.64×
speedup in this small, sequential workload. NVIDIA cold startup took about
4 minutes 25 seconds. Its Responses, authentication, required/automatic basic
tool-call smokes passed. A separate request selected the correct Aula tool and
arguments from 64 available tools. The candidate ran alongside the resident
Gemma and speech services, and the trial restored Unsloth successfully.

The workflow's deterministic source checks and the published weekly filter are
part of this qualification: the raw NVIDIA model alone did not satisfy the
empty-delta and weekly-selection rules.

The 2026-09-29 production cutover repeated five semantic Aula tool selections
against 64 available tools. All five passed, including `page: 0` after the
test prompt stated that index explicitly. The NVIDIA service passed health,
authentication, Responses, basic tool-call checks, and persistent restart
policy checks. The old Unsloth containers and activation rollback marker were
removed. The home-core LiteLLM gateway now mounts the new route configuration;
an authenticated request to `automation-moe` through `ai.home.arpa` completed
with thinking enabled. The active n8n Aula worker uses that model name. No
post-cutover full Aula execution has been observed yet.

A full home-core NixOS deployment was attempted but stopped before switching
because the SOPS file lacks the separate model-manager `known-hosts` key. The
gateway service was updated independently and passed its health check. The
home-core `current` release symlink remains at the prior revision until that
unrelated SOPS issue is resolved.
