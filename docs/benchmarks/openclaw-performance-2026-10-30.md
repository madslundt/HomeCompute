# OpenClaw reasoning and tool-surface qualification — 2026-10-30

The compact browser surface reduced isolated prompt input by about **35%**,
with **6/6** schema-only correctness checks passing. Explicit reasoning-off
made simple answers faster, but the combined candidate had a read-tool loop.
**No production change was applied.** Both optimizations remain canary source;
reasoning-off is held rather than promoted into the canonical configuration.

[Sanitized measurements](../operations/openclaw-performance-2026-10-30.json) ·
[Reproduction and activation gate](../../benchmarks/openclaw-optimization/README.md) ·
[Earlier diagnostic baseline](openclaw-rakazo-baseline-2026-10-29.md)

## What was measured

Production stayed on the existing OpenClaw 2026.9.1 / NemoClaw 0.0.129 /
OpenShell 0.0.116 tuple and local `inference/automation-moe` route. No Spark
runtime, model, channel, credential, network policy or model promotion changed.
The source base was `24cd9602b18127c4dbf825d2be8fbe103e56f562`.
These additions are uncommitted, unpublished and undeployed.

A fresh authenticated production finance turn reproduced the baseline symptom:
`--thinking off` still reported 67 reasoning tokens. It used 6,968 input tokens,
113 output tokens, 8.014 seconds native duration and 15.287 seconds workstation
wall time. Its tool schemas occupied 6,825 characters, including 4,873 for
browser. This production observation is **not** the isolated before/after pair.

The differential ran native `openclaw agent exec` in the existing sandbox with
fresh private configuration, workspace, conversation store and session for each
run. Each synthetic main agent copied only the relevant finance, travel or
researcher policy file. It retained three tools (`read`, `browser`,
`session_status`) and the independent safety plugin, with no channels, memory,
delegation or write tools. The local model output limit was 1,024 tokens per request.
This is a smaller controlled prompt than a real specialist conversation.

There are 31 scored executions across three cohorts, retaining the failed run.
Setup and smoke pilots are excluded, including rejected CLI/schema attempts
and pilots with a different tool profile. Runs were serial, with rotating variant
order and no model/runtime changes. Sampling was not seeded and the shared
backend was warm. Wall time includes native process startup, not just inference.
Prompt totals include uncached input **plus cache reads/writes**, including both
requests in a successful one-read turn.

## Changes and results

The reasoning candidate declares model reasoning capability, maps OpenClaw
`off` to `none`, enables `supportsReasoningEffort`, and explicitly includes
`none` in `supportedReasoningEfforts`. The last setting matters: the installed
provider's generic compatible-model effort list otherwise silently drops `none`.
The capability flag alone did not fix the request. An offline test of the
installed provider proves current and flag-only configurations omit the effort,
the full candidate sends `none`, and explicit `high` still reaches the payload.
The same test confirms assistant tool calls and tool results survive serialization.
It does not qualify high-effort generation quality.

Initial supplied-evidence cohort, five runs per variant:

| Candidate | Median prompt tokens | Median wall seconds | Semantic checks | Strict JSON |
| --- | ---: | ---: | ---: | ---: |
| Current metadata and browser surface | 5,252 | 12.668 | 5/5 | 0/5 |
| Explicit reasoning-off | 5,220 | 9.433 | 5/5 | 3/5 |
| Reasoning-off + compact browser | 3,358 | 10.047 | 5/5 | 4/5 |

Reasoning control reduced this small sample's median wall time by 25.5%.
No reasoning-token count was reported in candidate receipts; that absence is
recorded as null, not claimed as an independently measured zero.
Strict JSON remains a separate gap: semantically correct fenced JSON fails the
strict-format check. No extra formatting instruction was introduced to inflate
the candidate's score.

The browser candidate removes instructions and schema for actions already
blocked by the protected browser guard. It advertises only `navigate`, `focus`,
`close`, `snapshot` and `screenshot`, with profile `homecompute` and target `host`.
Every native field permitted by the guard survives; compatibility aliases absent
from the native schema are not invented. The existing before-tool veto, public
host/DNS enforcement and worker remain independent and unchanged.

| Browser surface | Before | Candidate |
| --- | ---: | ---: |
| Schema characters | 4,873 | 1,030 |
| Description characters | 3,182 | 438 |
| Properties | 66 | 20 |
| Actions | 24 | 5 |

Schema-only controls keep the original reasoning behavior:

| Fixture group | Runs per variant | Prompt tokens before → after | Median seconds before → after | Semantic checks |
| --- | ---: | ---: | ---: | ---: |
| English, Danish, travel and injection evidence | 4 | 5,257 → 3,408 | 13.474 → 12.000 | 4/4 each |
| Read fixture once, calculate 50 DKK | 2 | 10,402 → 6,710 | 16.518 → 13.381 | 2/2 each |

The prompt reduction is 35.2% and 35.5%, respectively. All six schema-only
results were also strict JSON. Latency is variable: one travel candidate was
slower than its control, and the two tool controls ranged from 12.348 to
20.688 seconds. These samples establish a prompt-size improvement, not a
production latency guarantee or a general accuracy improvement.

## Tool-loop blocker and limits

The initial combined read fixture successfully read the supplied file, then
kept issuing reads instead of answering. Workspace enforcement blocked an
out-of-workspace path; no private production content was read. The run made
81 read calls with three tool errors and reached the native deadline after
186.736 seconds. Total consumed prompt input, including cache, was 571,918
tokens. The matrix stopped on that failure; it is not omitted from the evidence.

That initial minimal canary omitted production's loop-detection configuration.
The final harness copies it and shortens the native deadline from 180 to 30
seconds, with a 60-second outer bound. A regression test protects that guard.
Later controls passed: reasoning-only read 1/1, schema-only read 2/2, and
combined read 2/2, each with exactly one successful read and no tool errors.
The successful retests do **not** prove that missing loop detection caused the
original behavior. The provider payload retains the fixture result, so simple
OpenClaw tool-result loss is not demonstrated. The remaining possibilities
include model variability and interaction between reasoning suppression and the
smaller prompt. Keep global reasoning-off held until that failure is understood
and repeated bounded tool tests pass reliably.

The harness does not qualify a public-browser round trip, actual specialist
identity/routing, live household tools, long reasoning, channel end-to-end
latency, voice, concurrency or production load. Its synthetic main identity
retains the coordinator's domain-execution veto. No real browser action or
household effect was attempted. This work is OpenClaw-side qualification; it
adds no new Rakazo product comparison.

## Verification and next changes

The production configuration remains SHA-256
`f50bb86eab2a75ce28941c027235c13088effbe9e01c69a5b73f301234b70971`.
The native browser module remains the exact original pinned hash recorded in the
JSON. The gateway is still PID 442, in the sandbox started at
2026-10-29 23:29:11 UTC. Authenticated gateway health returned `ok: true`, with
Mattermost configured and running. No message was sent as a health test; an
end-to-end channel reply is unverified.

Focused checks cover reasoning payload metadata, pinned-runtime refusal,
canonical promotion hold, isolated state/loop guards, browser projection and
independent vetoes. The installed provider and native schema probes passed.
Repository validation passed; the final rerun and its dependency skips are
recorded in the JSON. `status`, `services` and `drift` completed; `doctor`
remains degraded by pre-existing core failures and missing Spark passwordless
sudo. Both release markers remain at
`07740fb8a2c94ce7f2f7d06132035fd5ae8840e7`, behind the desired revision.
This work does not reconcile those existing differences.

Next, in order:

1. Keep the schema change independent of reasoning control. Qualify one bounded
   public-browser flow and a synthetic specialist workflow with the same guard.
2. Reproduce the combined read loop with identical guards and controlled
   sampling. Compare current, reasoning-only, schema-only and combined requests;
   include a turn/tool budget and retain every failure. Do not make reasoning-off
   the global production default based on the simple-answer result.
3. Investigate a native structured-output contract for JSON-dependent workflows;
   prompt-only formatting remains unreliable. Preserve normal chat behavior.
4. Prepare a reviewed, source-owned installer and exact rollback for the pinned
   browser module/helper before any activation. The gateway's reload mode is
   `off`, so applying runtime/schema changes requires a managed restart in an
   agreed idle window. Keep OpenClaw running under the current instruction.

Only transient qualification sources, fresh synthetic state and private receipts
were added inside the sandbox, plus private staging copies on the agents guest.
They remain outside production configuration and are retained for diagnosis;
no production runtime overlay, systemd override, container or background service
was installed. A timed-out run completed with a known native timeout, and the
matrix stopped; no turn was replayed. Review uncertain markers before any future
retry. The repository candidate has no production apply path and changes no
canonical settings.
