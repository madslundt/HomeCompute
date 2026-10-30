# Home-spark inference efficiency: baseline and experiment plan

**Decision:** keep the existing appliance and model tuple running. Start with a
matched, request-scoped reasoning-control experiment, then reduce repeated
client context. Investigate speech residency before changing the text engine's
capacity. No production optimization, restart, model swap, migration, driver or
firmware change was performed. The experiments below are proposals, not approval
to execute maintenance.

Evidence was collected on **2026-10-29, approximately 22:45–22:56 Copenhagen
(UTC+01:00)**. Machine timestamps below use UTC. This is a short observational
baseline under normal changing load, not a benchmark qualification or capacity
certificate. The [aggregate evidence](../operations/home-spark-efficiency-2026-10-29.json)
contains sanitized numeric summaries, not raw requests, responses, credentials,
audio, telemetry payloads, workflow exports or private artifacts.

Read with the earlier [OpenClaw/Rakazo baseline](openclaw-rakazo-baseline-2026-10-29.md),
its [comparison evidence](../operations/openclaw-rakazo-comparison-2026-10-29.json),
the [runtime primary-source review](../research/home-spark-runtime-sources-2026-10-29.md),
and [operations guide](../homecompute-operations.md). Existing staged, unstaged
and untracked work was preserved; this report does not adopt those changes as a
release. Historical ADRs and runbooks still describe pending older cutovers;
live observations below establish the running state, without granting missing
qualification or reconstructing an approval history.

## Reverified deployment and effective settings

| Boundary | Observed state |
| --- | --- |
| Workstation desired `origin/master` | `24cd9602b18127c4dbf825d2be8fbe103e56f562`; no fetch or release performed |
| Both host markers and current release pointers | `07740fb8a2c94ce7f2f7d06132035fd5ae8840e7`; both revision-drifted |
| Text process | `qwen38-flash-ultrafast`, started 2026-10-27 10:52:33 UTC, restart count 0 |
| Actual image ID | `sha256:b80718ed664f12514a24363894c61c5d36f9ea8ca294244a149ddb672df2dc14` |
| Installed vLLM package | `0.1.dev20073+g8e685d198`, locally built wheel; patched preview, not interchangeable with current stable documentation |
| Gateway | LiteLLM `v1.104.2`; `automation`, `automation-moe`, and `home` map to `openai/automation-qualification` |
| Text argv | 262144 context; 8 sequences; 8192 batched tokens; chunked prefill and prefix caching enabled; `auto` KV dtype; explicit `9g` KV allocation |
| Protocol | `qwen3_xml` tools, `qwen3` reasoning, automatic tool choice |
| Speculation | MTP 3, block rejection, probabilistic draft sampling; existing draft-vocabulary mount |
| Loading | `safetensors`, mmap PLE, prewarm 0, prefetch 0; no loader change |
| Native OpenClaw | Same config SHA `98d2c59f06dc901863c4de99850d21f82208b3e32a0b02409658915ce0375644`; `inference/automation-moe`, 32768 model context, 4096 max output, thinking off, reasoning capability false, max concurrent 1, timeout 660 seconds, no configured cloud fallback |

The mounted LiteLLM config SHA-256 was
`b2eb27e69230f78ab17a7f2f5789bda7fd62ed7615b779f1182bf1ee5185b872`.
Its allowlisted policy fields matched the source policy: text automation timeout
and stream timeout 600 seconds, `home` 20 seconds, STT 120 seconds, Plapre 300
seconds; global request timeout 600, retries 0, `drop_params=false`, simple-shuffle,
one allowed failure and five-second cooldown. No gateway reasoning default was
found. Other layers may retry: this inspection does not establish the n8n node,
SDK, HA integration or native provider retry policies. OpenClaw's 660-second
deadline outlasts its gateway's 600-second limit; neither ensures a fast answer.

Repository pins select
`Saren/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid@8b82f0b7abe3d1150a7827d298c75e86267636ae`,
PLE `Saren/Qwen3.8-Flash-Next-ple-table-fp8@50511b0a41aa1d34b8beb7e5d4bb06a0b650dc14`,
dime source `0c391a3e74b6a775cfe248691ca7fd855b1876a5`, with the
parent image and build/launch hashes in
[`config/model-catalog.json`](../../config/model-catalog.json). The model and PLE
mounts and dense-MTP build-report presence were observed. **Exact artifact/source
provenance remains unverified:** the protected live manifest at
`/srv/gb10-ai/manifests/flash-next/ultrafast/runtime.env` was permission-denied,
and Spark has no noninteractive sudo for this user. No weight scan, provenance
repair or privilege change was attempted. An image match and repository pins do
not independently prove the bound checkpoint bytes.

Installed source, rather than a different vLLM release, verified two important
behaviors:

- `entrypoints/openai/chat_completion/protocol.py:584–588` maps supplied
  `reasoning_effort=none` to `enable_thinking=false` unless the caller explicitly
  supplies that template variable. SHA-256:
  `cb756e3d18e9061a2b306f305e10bd71d43f01ad1b236d0e9cbbb8756cd504dc`.
- `v1/worker/gpu_worker.py:604–625` uses explicit KV bytes, profiles the model to
  compile for the batch-token limit, and bypasses utilization-based KV sizing.
  SHA-256: `923767cea120a027ca36683bd48b7659eadbfa4cbd4acbcf52a80cee0c0a0ec4`.
  Thus `gpu-memory-utilization=0.01` is not a 1% total-memory cap. Lower context
  alone does not shrink the explicit 9g pool.

The pinned public template defaults unspecified thinking to enabled and effort
to `xhigh`; the [source review](../research/home-spark-runtime-sources-2026-10-29.md)
links that exact template and upstream build/launcher implementations. An
OpenClaw display/config setting is not proof that its transport sends the
server control. Fresh direct gateway requests below establish end-to-end
compatibility for `reasoning_effort=none`, not the native client's payload.
The checked-in `config/openclaw-nemoclaw.json` overlay declares
`supportsReasoningEffort=false`; this is a concrete transport compatibility
setting to investigate, not a flag to flip without checking the installed client.
Its model `contextWindow` is 262144 with `contextTokens=32768`, whereas the live
provider reports `contextWindow=32768` and a different compatibility projection
(`supportsStore=false`). Their intent/provenance was not reconstructed here.
Do not overwrite the live native configuration with the overlay to reconcile
these differences during an observation task.

## Memory, cache, throughput and thermal baseline

Five complete retained observations, five seconds apart, span
**21:48:57.742–21:49:17.778 UTC (20.04 seconds)**. A longer sampling command also
ran; only this complete five-sample window is used for interval calculations.
These counters cover normal production traffic, before the six new requests.

| Metric | Observation | Interpretation |
| --- | --- | --- |
| Physical unified memory | 121.625 GiB | Shared CPU/GPU pool |
| `MemAvailable` | 5.371–5.426 GiB | Thin headroom; no permission to add large allocations |
| Swap occupied | 13.186 GiB of approximately 16 GiB | Occupancy does not establish active swapping |
| Swap-in/out delta | 0 / 0 pages | No active swapping observed in this short window |
| Major faults / OOM delta | 25 / 0 | File-backed faults can occur without swap activity |
| Reclaim scan/steal delta | 0 / 0 | No observed kswapd reclaim in the window |
| Memory PSI | some/full avg10, avg60, avg300 all 0.00 | Does not exclude earlier pressure or short future spikes |
| Text running / waiting | 1–2 / 0 | High utilization served active work; no sampled queue saturation |
| KV usage gauge | 10.84–21.67% | Sampled utilization, not an estimate of safely removable KV |
| Preemptions | 285 lifetime, 0 new | Historical pressure exists; workload/time correlation unknown |
| Prefix counters | 6400 hits / 6646 queried tokens, 96.30% interval; about 63% lifetime at first sample | Useful reuse exists; cannot assume every client reuses context |
| MTP counters | 486 accepted / 1518 drafted tokens, 32.02%; 506 draft steps | Acceptance is measurable; net speed benefit remains unproven |
| Generated tokens | 990 / 20.04 s, about 49.4 aggregate tokens/s | Combined active requests, including hidden reasoning; not per-user speed |

One request completed inside this interval. Completion histogram sum/count deltas
give 7082 prompt tokens, 682 computed prefill tokens, 1643 output tokens,
0.545 seconds prefill, 35.679 seconds decode, approximately 9 microseconds queue,
and 36.267 seconds end-to-end. Approximate phase rates are 1251 computed prompt
tokens/s and 46 output tokens/s. Its decode phase consumed about 98.5% of its
inference time. This is strong evidence that **that completion** was decode
dominated; it is not a universal prefill benchmark. Requests started before the
window and global token counters have different accounting boundaries. No
production p50/p95 is estimated from this single completion.

NVIDIA's process allocation snapshot showed text 87657 MiB (85.60 GiB), Hviske
12074 + 170 MiB (11.96 GiB), Whisper 5028 MiB (4.91 GiB), and Plapre 6875 + 1250
MiB (7.93 GiB): approximately **24.80 GiB of speech allocations**. Ownership was
consistent with the resident engine processes. These are running allocations,
not standalone weights or guaranteed reclaimable bytes. Do not add them to host
used RAM. Docker reported only about 2.96 GiB for text, illustrating why its
accounting alone cannot budget GB10 unified memory. Per-process PSS and
attribution of occupied swap to a service were not collected; they cannot be
inferred from Docker RSS or GPU allocation totals. See
[NVIDIA UMA limitations](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)
and [Linux PSI semantics](https://docs.kernel.org/accounting/psi.html).

The GPU snapshot was 95% utilized, 67°C, P0, approximately 40.7 W and 2463 MHz SM
clock. Current power/thermal cap and slowdown flags were inactive. Historical
counters included thermal and power events, so this single snapshot cannot
exclude prior throttling. GPU memory totals, memory clock and power limit were
unsupported/N/A. GPU power is not wall/appliance power; tokens/joule cannot be
claimed. Record supported counters over naturally occurring sustained load
before investigating cooling. No clock, power, kernel, firmware, DGX OS, driver,
CUDA or Container Toolkit setting was changed. Driver was `580.178.04`; CUDA
visibility reported 13.0. Vendor maintenance remains a separate lifecycle.

## Consumers and speech contention

Metadata-only LiteLLM aggregation from **20:45 UTC to query time** found these
completed requests. Counts include the prior diagnostic and normal users; this
was not an isolated workload. The query was read-only with a ten-second database
statement bound and selected no messages, responses, arbitrary metadata, IPs or
credentials. Key aliases were classified in memory; neither aliases nor hashes
are retained here.

| Consumer class / alias | Requests | Input tokens | Output tokens | Mean gateway seconds |
| --- | ---: | ---: | ---: | ---: |
| Shared network admission / automation-moe | 159 | 825000 | 197528 | 38.971 |
| Other or unattributed / automation-moe | 51 | 170482 | 21734 | 15.578 |
| OpenClaw / automation-moe | 7 | 57535 | 747 | 7.459 |
| Shared network admission / embedding | 1 | 68 | 0 | 0.514 |

No failure status was returned for these recorded rows. Completed-spend rows
do not enumerate in-flight requests or establish absence of transport errors.
Most observed text load did **not** carry the OpenClaw principal. The shared
network credential deliberately loses individual-client identity, so these data
cannot identify an individual app or household user, or prove which client owns
the two active requests. Do not weaken metadata privacy to obtain attribution.
Use existing scoped principals and aggregate time windows for future work.

The n8n connector's execution-metadata inventory for the same lower time bound
returned 70 executions: 65 calendar-sync (62 success, 3 error), four shopping
(success), one battery monitor (success). These are workflow executions, not
inference calls; they cannot be joined one-to-one to spend rows. Error payloads
were not read, and no workflow was executed. HA can trigger workflows and consume
speech, but no HA request attribution or live voice canary was established.

All three Spark speech engines were resident and reported healthy containers.
An instantaneous metrics read showed Hviske 0 running, 0 waiting, 0 successful
requests since its current start; Whisper 0 running, 0 waiting, 84 successful
requests. This is a useful low-use lead, not proof Hviske is unnecessary. Plapre
did not expose metrics at the tested `/metrics` path; its contention is unknown.
No artificial speech overlap was induced. Whisper's launch contains eager mode,
0.04 utilization, one sequence and 2048 batched tokens; Plapre's argv contains
0.06 and 512 context. Hviske's effective numeric GPU budget was not recovered
from its shell launcher. Its private baseline Compose ownership and the speech
runbook suggest the retained `.10/.06` baseline, but **the live Hviske budget is
unverified**, not a newly measured `.10`.

## Small new gateway diagnostic

Six synthetic no-tool streaming calls reused `budget-en` and `budget-da` from
the existing fixture file, three serial repetitions each. Settings were matched
within this new run: `automation-moe`, temperature 0, max output 128,
`reasoning_effort=none`, streaming usage enabled; one added request at a time,
one-second gaps. Before each call, admission required available memory >=4 GiB,
zero waiting requests, and <=2 existing running requests. Actual existing running
counts were 1–2. Verified TLS used the workstation's HomeCompute CA through the
approved LAN admission; no credential was created or copied. The synthetic
request/response content was processed in memory and discarded.
The complete existing fixture file SHA-256 was
`af92fe29caee92ae480ed61a7ca53390789a58c114367270c3b0b4fa937ea5f2`.

| Fixture | E2E repetitions (seconds) | First generated = first visible (seconds) | Result |
| --- | --- | --- | --- |
| budget-en | 1.3088, 1.1826, 1.1000 | 0.4163, 0.4340, 0.3733 | 3/3 exact JSON, stop, 39 output tokens, 0 reasoning |
| budget-da | 1.3722, 1.4191, 1.3167 | 0.3999, 0.4836, 0.4386 | 3/3 exact JSON, stop, 39 output tokens, 0 reasoning |

Descriptive pooled median E2E is 1.313 seconds and median first visible is 0.425
seconds. Nearest-rank sample p95 is 1.419 / 0.484 seconds, respectively, but six
short samples cannot establish an SLO. Input counts were 182/216 tokens
(including a per-run synthetic prefix), cached tokens 0 on every repetition.
These were resident-engine repetitions, **not demonstrated prefix-warm runs**.
No cache was flushed or model evicted to obtain a cold run. Startup cost,
long-context performance, tool correctness and mixed speech capacity remain
unmeasured in this new diagnostic.

The earlier comparison's direct median was 1.617 seconds for approximately 157
input tokens versus native OpenClaw's 11.826 seconds for approximately 6992 input
tokens; native runtime median was 8.2005 seconds. Native output included reasoning
despite thinking-off configuration, and five of six semantically correct native
answers failed strict JSON presentation. Its missing-control diagnostic spent
the whole 1024-token budget in hidden reasoning; explicit `none` returned a
correct answer in about 1.7 seconds. Those are dated, non-matched observations:
native retained a 4096 ceiling, transport/wrappers differ, and each scored case
had only one sample. Do not call their ratio a runtime speedup. This new run's
128-token ceiling also prevents direct performance comparison with that matrix.

## Ranked bottlenecks and confidence

| Rank | Finding | Confidence and limiting evidence |
| --- | --- | --- |
| 1 | Unspecified reasoning and long generation can consume decode capacity without useful visible output | High for control semantics and earlier failure; medium for share of normal production cost. New explicit-none calls worked; do not turn reasoning off for every n8n task. |
| 2 | Native prompt/tool-schema volume increases prefill work and cache footprint | High for earlier native sizes; medium for achievable reduction and total savings. Global cache reuse is already effective, and one completion was decode dominated. |
| 3 | Thin unified-memory headroom with roughly 24.8 GiB speech allocations limits safe changes | High for residency; low for safe removable amount. No active swapping observed; speech availability/quality and existing capacity guards block blind unloading or budget cuts. |
| 4 | Shared-consumer fairness and deadlines may hurt short interactive work under long generations | Medium risk, low evidence of current saturation. Zero sampled queue; unattributed load dominates; no per-consumer p95 yet. |
| 5 | Text context/sequence/batch/KV defaults may exceed actual client needs | Medium hypothesis. OpenClaw uses 32K, other consumers are not inventoried; 285 historical preemptions caution against shrinking KV based on this window. |
| 6 | MTP depth may be suboptimal for Danish/short tasks | Medium hypothesis, low causal confidence. 32% interval acceptance is not MTP-off latency evidence; output sampling and foreign traffic confound it. |
| 7 | Cold startup and thermal limits may affect availability | Unverified bottlenecks. Existing loader workaround and stopped rollback containers demonstrate operational complexity, not measured cold-start latency or active thermal throttling. |

## Measurement contract for subsequent experiments

Use existing local models behind stable aliases. Keep the image/model/PLE tuple
fixed until source provenance is joined. Freeze fixture and native prompt-policy
hashes; record route, config and installed-source hashes. Save raw evidence only
in a fresh operator-owned mode-0700 directory outside Git. Publish aggregate
counts, timings, correctness and reviewed hashes. No household prompt replay,
live workflow execution or notification is needed.

| Workload | Fixed content and controls | Order and concurrency |
| --- | --- | --- |
| T1 routine EN/DA | Six unambiguous comparison fixtures; 1024 ceiling for the matched trial, temperature 0, identical controls per arm | One request; interleave A/B then B/A; first 3 pairs per fixture, pause for review before expanding |
| T2 native-size context | Synthetic 8K then 32K inputs with deterministic extraction targets, identical output cap and no live tools; preserve native policy/schema for native comparison | One request; admit 32K only after small trial and current headroom pass; 64K/128K later only with demonstrated consumer need and approval |
| T3 tool protocol | Fixed read-only synthetic function schema, required/auto selection, exact name and argument validation, malformed-result recovery | One request; tools stubbed, never dispatch actions; zero unauthorized or fabricated tool success |
| V1 isolated speech | Existing approved public Danish + English/mixed corpus, fixed hashes/duration/voice/tempo and references; existing STT/TTS aliases | One speech request, no generated text load; record model/adapter/client phases separately |
| V2 mixed interaction | One short T1 while one approved STT **or** one TTS runs; later a serial STT -> T1 -> TTS voice turn | At most two added requests total, only under separately reviewed headroom; no all-services fan-out |
| F1 fairness | One bounded 8K text task plus one short interactive task on existing aliases | At most two added text calls; only after serial baseline and no queued production work; no seq-8 saturation test |

For every row record: E2E p50/p95 (nearest-rank, sample count explicit), first
generated TTFT, first visible output/first playable audio, actual prompt and
output/reasoning tokens, cached/computed prompt tokens, finish reason,
HTTP/timeout/cancellation/retry counts, exact output/tool checks, and speech
WER/CER, pronunciation/naturalness and RTF against the existing qualification
gates. Small pilot percentiles are descriptive; expand in bounded windows toward
>=20 observations per arm/condition only if production remains usable. Never
execute one long unbounded batch simply to improve statistics.

Measure queue, prefill and decode separately using the installed metrics:
completion histogram sum/count deltas, queue bucket deltas, computed-prefill
tokens / prefill seconds, output tokens / decode seconds, prefix-hit/query
deltas, preemptions, draft/accepted-token deltas and per-position acceptance.
Retain histogram `le` boundaries when estimating percentiles; do not sum all
buckets or treat global histogram deltas under foreign traffic as per-fixture
traces. Prefer each response's usage and client timestamps; mark phases
unattributable when production overlaps. Quantify server versus gateway versus
native transport time separately, without subtracting unrelated sample medians.

Distinguish (a) a new synthetic leading prefix with reported cache miss,
(b) exact repeat with **confirmed** cached tokens, (c) naturally idle first use
of a resident speech/text engine, and (d) full process/model cold start. Repeat
the first two with matched content length and alternating order. This task did
not authorize (d); collect startup history passively or schedule a separately
approved guarded maintenance test. No cache flush, swap-off, model eviction,
prewarm changes or deliberate memory exhaustion. A prefix miss does not mean
CUDA graphs, weights, disk pages or the whole engine are cold.

Use five-second memory/vmstat/PSI/GPU observations before/during/after a window,
plus active/waiting counters. For the first pilot, defer if existing running >2,
waiting >0, available memory <4 GiB, a required health path is unknown/unhealthy,
or an operator reports degraded use. Stop issuing new probes on any growing
queue across two samples, sustained swap-in/out across two intervals, available
memory <4 GiB, increasing memory PSI full avg10 above 1%, OOM, new preemption,
timeout, failed correctness or service-health regression. Close only the probe's
own connection on its 20-second pilot deadline; do not kill production engine
work. For long-context trials establish a separate bounded deadline and verified
cancellation behavior first. An interrupted request may continue server-side;
wait for it to settle, never replay automatically. These conservative pilot
thresholds are proposed protection rules, not measured appliance capacity limits.

## Proposed experiment sequence

Common release rule: change owning source first, focused tests and repository
validation, reviewed commit on `origin/master`, then the explicitly authorized
owning lifecycle. Normal releases use `scripts/homecompute deploy HOST
--revision FULL_SHA`; dedicated model/speech/native configuration is not
reconciled merely by that marker. Record the prior effective tuple, image IDs,
private settings snapshot, backups and recovery path. Do not execute the older
Flash cutover runbook against today's route as if it were a fresh candidate.
An experiment rollback restores today's verified prior tuple, not automatically
Qwen3.6. Respect active override markers and drain guards.

### 1. Explicit reasoning control on routine work

- **Owner:** native provider overlay
  [`config/openclaw-nemoclaw.json`](../../config/openclaw-nemoclaw.json) and
  [`scripts/prepare-openclaw-nemoclaw.py`](../../scripts/prepare-openclaw-nemoclaw.py),
  plus a synthetic benchmark request builder. Existing team setup changes agents,
  tools and sessions, not this provider contract: add a narrowly scoped,
  idempotent provider migration with validation, hash fencing and rollback before
  changing an existing native deployment. Gateway defaults only if a scoped
  per-consumer policy is deliberately designed. No global alias-wide off switch.
- **Trial:** first compare explicit `none` against the current request policy on
  synthetic no-tool EN/DA fixtures at matched 1024 output cap. Inspect native
  provider compatibility/source without logging payloads; prove it emits the
  field before proposing native rollout. Separate `medium` reasoning as a later
  quality arm for workflows that need it. Do not rerun a known 1024-token failure
  repeatedly; stop its arm at the first hidden-only exhaustion.
- **Benefit/tradeoff:** remove unnecessary hidden decode cost and reduce short
  task latency; complex reasoning quality may fall. Existing native thinking-off
  intent can be made effective, while n8n reasoning-dependent paths stay scoped.
- **Accept:** all fixture, denial and tool checks pass; zero hidden-only/length
  failures; none arm reports zero reasoning; >=20% lower matched median E2E or
  output-token cost, p95 no worse than 10%; unchanged routing/privacy/action
  boundaries. A native change needs matched native tests, not direct-only speed.
- **Stop/lifecycle/rollback:** common stop rules plus any reasoning/task regression.
  Request-only synthetic control needs no service change. Native persistence
  requires reviewed source, supported config validation and its guarded apply;
  schedule any necessary gateway restart separately. Roll back the exact saved
  config through that installer and verify hash/client path. No change in this task.

### 2. Reduce repeated prompt and schema volume; preserve useful cache prefixes

- **Owner:** team policy and integration-instruction builders, role skill/tool
  definitions under `deploy/openclaw/`,
  [`scripts/setup-openclaw-integration-instructions.py`](../../scripts/setup-openclaw-integration-instructions.py).
- **Trial:** after reasoning is held constant, remove duplication and inject only
  relevant optional schemas; keep stable policy prefixes and dynamic evidence at
  the end where supported. Independently preserve grants, denial rules, domain
  boundaries, read-before-write and HA authority. Do not replace enforcement
  with a shorter prompt. Do not alter cache salts or share private state across principals.
- **Benefit/tradeoff:** target >=25% fewer native prompt tokens and >=20% lower
  uncached TTFT at equal task completion; a reduced tool surface may omit a needed
  tool, and already-cached tokens yield smaller latency savings.
- **Accept:** unchanged security/role/structured/tool suite, correct long-context
  extraction, cold-prefix and confirmed-warm results separately; no >10% p95
  regression or greater timeout rate. Measure absolute computed tokens as well
  as hit fraction; smaller prompts can reduce hit ratio while improving latency.
- **Stop/lifecycle/rollback:** stop on omitted capability or boundary regression.
  Reviewed native instruction/team lifecycle, any required restart separately
  authorized; restore prior policy bundle/config from its protected snapshot,
  verify hashes and native task/tool behavior. No live prompt edit in this task.

### 3. Reclaim residency using the existing speech qualification path

- **Owner:** `config/hviske-stt.env.example`, `config/plapre-tts.env.example`,
  `deploy/hviske-stt/compose.yaml`, `deploy/compute-node/plapre/compose.yaml`, owning
  setup/qualification scripts. Inspect Whisper usage/routing before proposing any
  optional service-idle policy; it currently serves mixed/English requests.
- **Trial:** first join protected active-capacity receipts, real budgets, usage and
  required voice paths. The pre-existing `.05/.05` candidate is an experiment
  to requalify, not a free 6% RAM saving. Prior speech startup failed and was
  restored; follow [startup diagnosis](../operations/speech-startup-diagnostic.md)
  before a fresh [capacity trial](../operations/approved-speech-capacity.md).
  No blanket speech shutdown, demand-loading policy or migration is proposed now.
- **Benefit/tradeoff:** seek >=4 GiB additional minimum available memory at equal
  text load; measure actual allocations, swap deltas and mixed latency. Smaller
  speech cache can harm startup, concurrency and latency. Reclaimable bytes must
  be measured rather than computed from utilization percentages.
- **Accept:** exact candidate starts and passes existing capacity/quality gates;
  zero swap growth/stalls/OOM under matched mix; text p95 <=baseline+10%, speech
  meets its existing absolute gates (Plapre warm first audio <=750 ms, RTF <=0.5,
  pronunciation >=95%, naturalness >=3.5/5) and real HA/fallback checks separately.
- **Stop/lifecycle/rollback:** any startup/health/voice degradation, active guard
  mismatch or common pressure stop. Needs separate explicit maintenance approval,
  exact-plan bootstrap/controller, preserved private receipts, then canonical
  [finalization](../operations/speech-capacity-finalization.md) only after a real
  passing trial. Restore exact prior speech definitions through that lifecycle;
  leave markers active if restoration is unverified. No changes now.

### 4. Right-size text limits, then test queue fairness

- **Owner:** `config/compute-node.env.example`, `config/model-catalog.json`,
  `scripts/setup-compute-flash-next.sh`, launch overlay and lifecycle tests;
  `config/capability-routes.json` owns gateway deadlines. Generated LiteLLM routes
  must use `python3 scripts/model_registry.py render` and `check`.
- **Trial:** inventory metadata-only admitted context/output and client retry
  limits first. Test one factor per approved variant: sequences 8 -> 4; then a
  justified context cap (32K only for a verified <=32K consumer envelope, otherwise
  retain longer); then 8192 -> 4096 batch tokens for interactive fairness. Keep
  KV 9g until long-prompt/concurrency tests explain the 285 historical preemptions;
  only then try a conservative 9g -> 8g pool variant with cache/preemption checks.
  The current installer intentionally rejects altered tuples: update guards,
  source hash contracts and tests, not a host env override.
- **Benefit/tradeoff:** reduce recurrent/graph/workspace pressure where measured,
  bound long-prefill interference, and reserve responsiveness. Lower sequences
  may serialize consumers; smaller batches may slow prefill; a smaller KV pool
  may evict useful prefixes or trigger recomputation. Context is admission, not a
  guaranteed proportional memory saving. Keep stable client aliases.
- **Accept:** >=1 GiB measured headroom gain or >=20% better short-job p95 under
  F1; no new preemptions, cache-correctness failure, >10% representative throughput
  loss or longer-job starvation. Longest admitted context and tools still pass.
  Gate `home` on its real 20-second client deadline. Retry budgets must be finite;
  never retry write steps to compensate for inference congestion.
- **Stop/lifecycle/rollback:** common stops plus repeated starvation/deadline miss.
  Separate authorized drained model maintenance, repository-owned launcher and
  prior image/model/KV tuple retained. Prove startup/rollback command ownership
  before execution; don't assume `deactivate-canary` reconstructs today's exact
  active model. Gateway deadline changes use reviewed core lifecycle and prior
  generated config rollback. No runtime variants or scheduling changes now.

### 5. Tune MTP only after the preceding costs are controlled

- **Owner:** same text declarations/guarded launcher and pinned upstream source;
  existing MTP vocabulary/transform pins remain fixed.
- **Trial:** approved MTP 3 baseline versus 2, then disabled only if that installed
  preview supports it and a source-defined variant is validated. Pair English,
  Danish, structured output and tools at equal settings; preserve correct
  probabilistic sampling/rejection combinations. No modern profiler endpoint or
  tracing switch is assumed supported by this image.
- **Benefit/tradeoff:** lower wasted draft work or improve decode latency for
  low-acceptance workloads; removal may reduce throughput or change numerical
  behavior. 32% acceptance alone does not justify disabling speculation.
- **Accept:** >=10% matched decode/E2E benefit in the weighted workload, no >10%
  tail/capacity regression, unchanged output/tool correctness and memory safety.
  Report acceptance and tokens/step with latency, not instead of latency.
- **Stop/lifecycle/rollback:** common stops or correctness/sampling regression;
  separately authorized guarded text restart, exact old tuple rollback, health
  plus authenticated alias verification. Cold-start timing can be collected in
  that already-approved window; do not create an extra production restart.

## Reproduction and evidence boundaries

Start each window with `git status --short --branch`, relevant diffs, and the
read-only commands `./scripts/homecompute status --json`, `services`, `doctor`,
`drift`. Preserve snapshots outside Git; exit 0 from `drift` is not a clean bill
of health. Reuse the credential-free allowlisted inventory:

```bash
umask 077
evidence_dir="$(mktemp -d /tmp/homecompute-efficiency.XXXXXXXX)"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
  home-spark python3 - < scripts/tensorfold_inventory.py \
  > "$evidence_dir/inventory.json"
```

Do not commit that runtime inventory or Docker/process output. For the passive
window run this **on Spark** under ordinary SSH; it reads no credentials and
does not configure any exporter:

```python
import json, pathlib, time, urllib.request
names = {'num_requests_running', 'num_requests_waiting', 'kv_cache_usage_perc',
         'prefix_cache_queries_total', 'prefix_cache_hits_total',
         'num_preemptions_total', 'spec_decode_num_drafts_total',
         'spec_decode_num_draft_tokens_total', 'spec_decode_num_accepted_tokens_total',
         'generation_tokens_total', 'request_success_total',
         'request_prompt_tokens_sum', 'request_generation_tokens_sum',
         'request_prefill_kv_computed_tokens_sum', 'request_queue_time_seconds_sum',
         'request_prefill_time_seconds_sum', 'request_decode_time_seconds_sum',
         'e2e_request_latency_seconds_sum'}
for index in range(5):
    metrics = {}
    with urllib.request.urlopen('http://127.0.0.1:18300/metrics', timeout=3) as response:
        raw = response.read(2 * 1024 * 1024).decode()
    for line in raw.splitlines():
        name = line.split('{')[0].split(' ')[0]
        if name.removeprefix('vllm:') in names and name.startswith('vllm:'):
            metrics[name] = metrics.get(name, 0) + float(line.rsplit(' ', 1)[1])
    mem = dict(line.split(':', 1) for line in pathlib.Path('/proc/meminfo').read_text().splitlines())
    vm = dict(line.split() for line in pathlib.Path('/proc/vmstat').read_text().splitlines())
    print(json.dumps({'time': time.time(), 'metrics': metrics,
        'memory_kib': {k: int(mem[k].split()[0]) for k in
                       ['MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree']},
        'vm': {k: int(vm[k]) for k in ['pswpin', 'pswpout', 'pgmajfault', 'oom_kill']},
        'memory_psi': pathlib.Path('/proc/pressure/memory').read_text()}), flush=True)
    if index < 4:
        time.sleep(5)
```

Use monotonic time for request durations. To reproduce the six-call diagnostic,
read the two existing synthetic cases, prepend one run-specific synthetic marker,
send each three times serially through verified-TLS `POST /v1/chat/completions`
with the exact settings in the aggregate JSON, and apply the admission gates
before **each** request. Parse SSE `data:` records until `[DONE]`, count the first
nonempty reasoning/content delta and first nonempty visible delta separately,
collect final usage/finish reason, and compare the complete parsed JSON object
with `expected_json`. Discard answer text, never dispatch tools, and stop on
failure rather than retrying. A different synthetic marker can change token
counts slightly; record actual usage. The existing
[`scripts/assistant-comparison.py`](../../scripts/assistant-comparison.py) offers
non-streaming fixture/scoring procedures but does not reproduce TTFT or these
128-token settings unchanged. No reusable performance harness was installed.

### Outstanding drift, health and handoff

- `status` and `services` succeeded; `doctor` returned 1 for the existing core
  `homecompute-nas-recovery-backup.service` and
  `homecompute-platform-health.service` failures. Spark reported no failed units.
  No backup/restore success or root-cause repair is claimed.
- `drift` returned 0 while reporting both hosts revision-drifted. The Spark
  marker does not reconcile this manually launched text container or every
  speech/adapter project. Hviske's Compose owner is a private capacity baseline;
  its HTTP ingress and Whisper point at user-managed projects; Plapre HTTP points
  at release `15fc25ea9cdf4966861701841a3016fabf560c44`, while its engine names
  `/srv/homecompute/current`. Core speech adapters were healthy. Preserve these
  provenance differences for their owning lifecycle; no override was retired.
- Stopped rollback/candidate containers and image-mode service remain untouched.
  Stopped containers are not resident GPU engines; deleting them is not a RAM
  optimization. Text has no Docker healthcheck, so running status alone is weak;
  the six successful gateway calls provide the new limited functional evidence.
- No new native OpenClaw turn, authenticated per-principal isolation test, HA
  voice turn, speech quality/capacity trial, long-context run, cold restart,
  sustained thermal trial, or restore test was performed. Do not call systems
  synchronized or full voice accepted from this report.
- Only this report, sanitized aggregate JSON and companion source review were
  added. No persistent host/service setting changed. Raw stdout and ephemeral
  measurement code were kept outside Git; synthetic answer text was discarded.
  Source validation is documentation/link/JSON/diff checking, not live readiness.
  Those checks passed, including Python syntax in the reproduction block and
  independent aggregate/percentile calculation. The full implementation test
  suite, repository validator and Nix evaluation were not rerun for these
  documentation-only additions.
  Files remain **uncommitted and undeployed**.

**Recommended first experiment:** request-scoped explicit reasoning control on
the existing synthetic EN/DA and tool fixtures, at matched settings, before any
resident service or runtime-budget change. Proceed toward a native client patch
only if it demonstrably sends the verified server control, preserves policy and
task/tool correctness, eliminates hidden-only budget exhaustion, and achieves
the predeclared latency/token benefit without tail or co-service regression.
The existing omitted-control failure, installed mapping, and six correct
zero-reasoning gateway completions justify that small experiment; they do not
justify an alias-wide reasoning change or production rollout.
