# Qwen3.6 and Flash-Next profile qualification

**Status: owner-directed `automation-moe` trial; Flash-Next quality qualification remains incomplete**
**Evidence state:** partial synthetic screening collected; full qualification remains incomplete
**Report date:** 2026-09-30

This report is the decision record for the observed Qwen3.6 baseline and the
owner-directed Flash-Next alias trial. Blank/not-run values are deliberate;
no model-quality or capacity results are inferred from synthetic smoke checks
or upstream benchmarks.

## Pinned tuples

| Tuple | Artifact | Revision | Runtime identity | Reasoning |
|---|---|---|---|---|
| Qwen3.6 handoff baseline | `unsloth/Qwen3.6-35B-A3B-NVFP4` | `739af1e7aac320af1682ed1e0cce369af4c5265d` | Requested production reference; not the model observed running on `home-spark` during this task | Do not benchmark as the live baseline until the host and handoff state are reconciled |
| Qwen3.6 observed live | `nvidia/Qwen3.6-35B-A3B-NVFP4` | `1355db6a052410cfd62085d94b58866fd0f2c3c5` | vLLM OpenAI image `vllm/vllm-openai@sha256:2a7cde230b59f3ce6cab33dd245ba6bee41aa87b38c9fe84f966ff24016813ce`; local image ID `sha256:89154ef00dd15368d2b293c167e5cc7dbb521fcfb2fbb77510e0d4df2b820e8f` | Healthy container serves both `automation-moe-nvidia` and `automation-moe`; live reasoning setting remains unverified |
| Flash-Next quality baseline | `nvidia/Qwen3.8-Flash-Next-NVFP4` | `fc694b54fb0174e0913e6adf86691ef85a4ead47` | Blazux `b05e14681325f3cc5bd22e7f48537feeeb0bf266`; vLLM 0.30.0 ARM64 base `sha256:4864d46625cbc3307623e29ac742030655e27249feba7b97ec925ce4cc4dfb56`; local image ID `sha256:12ef55e080d1a050a40f6801127dffa7b297e622f71e20d498456f9336db7127` | `enable_thinking=true`, `xhigh`; `medium` diagnostic |
| Flash-Next UltraFast challenger | `Saren/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid` | `8b82f0b7abe3d1150a7827d298c75e86267636ae`; PLE `50511b0a41aa1d34b8beb7e5d4bb06a0b650dc14` | dime commit `0c391a3e74b6a775cfe248691ca7fd855b1876a5`; v16b iter6d; parent image `sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8`; private launch overlay `sha256:9cf8895b57560381c555970d2d043c089df9fa3eb2b9dab69440a035b14228cb`; local image ID pending build | `enable_thinking=true`, profile default; exact effective effort must be captured |

Runtime settings: hybrid layout, context 262144, YaRN disabled, MTP 2, four
sequences, prefix cache and deterministic top-k enabled, effort alias enabled,
`KV_DTYPE=auto`, GPU memory qualification steps 0.68/0.70/0.72/0.74, long-prefill
threshold 1024, `qwen3_coder` tool parser, `qwen3` reasoning parser, automatic
tool choice, temperature zero where supported. Each change to these settings
creates a distinct tuple.

## Results

`NOT RUN` means the repository has no observed run evidence for that cell.
Private prompts, household content, raw tool results, and credentials belong in
protected local benchmark storage and are not committed.

### Synthetic screening collected on 2026-09-30

The same 3-case, 10-trial profile plan was run against the observed Qwen3.6
NVIDIA baseline and the live Flash-Next quality container. The profile plan
completed all 30 requests for each model. The separate synthetic tool-recovery
loop completed 3 trials for each. Raw generations and comparison JSON remain in
the ignored `benchmarks/results/` directory.

| Metric | Qwen3.6 NVIDIA baseline | Flash-Next quality |
|---|---:|---:|
| Profile requests completed | 30/30 | 30/30 |
| Mean objective score | 13.52% | 37.62% |
| Full profile cases passing every check | 0/30 | 0/30 |
| Median profile request time | 3,861.89 ms | 4,371.25 ms |
| Danish exact-date checks | 0/10 | 0/10 |
| Correct one-call tool name and JSON arguments, after parsing argument JSON | 10/10 | 10/10 |
| Tool-loop mean objective score | 53.33% | 35.00% |
| Tool-loop median duration | 16,540.35 ms | 13,841.82 ms |
| Tool-loop mean tool calls / wrong calls | 16 / 12 | 0 / 0 |

Flash-Next's mean profile objective score was 24.10 points higher, while its
median request was 509 ms slower. It returned valid JSON in all 10 Danish cases
and matched the expected title and child in all 10; it omitted the time and
normalized date required by the fixture in every trial. Both models made the
correct tool selection with semantically matching arguments in all 10 tool
selection trials. The harness marked those tool checks as failed because it
evaluates the empty assistant text instead of the returned tool call and
compares raw argument JSON strings including whitespace. This also means the
0/30 full-case pass counts do not establish a usable quality pass rate.

The Qwen3.6 baseline's live reasoning and runtime settings were not fully
verified. Flash-Next ran at GPU memory utilization 0.74 to provide enough KV
cache for its 262144 context. These are useful screening
results, not a matched-runtime promotion gate. The separate loop result is
also unfavorable to Flash-Next: it made no tool calls in its three trials,
while Qwen3.6 made 16 calls on average, including 12 wrong calls.

| Metric | Qwen3.6 reasoning | Blazux quality baseline | UltraFast challenger |
|---|---:|---:|---:|
| Aula pass rate | NOT RUN | NOT RUN | NOT RUN |
| Aula relevant-source recall | NOT RUN | NOT RUN | NOT RUN |
| Aula incorrect facts | NOT RUN | NOT RUN | NOT RUN |
| Aula missed required lookups | NOT RUN | NOT RUN | NOT RUN |
| Offers pass rate | NOT RUN | NOT RUN | NOT RUN |
| Offer factual accuracy | NOT RUN | NOT RUN | NOT RUN |
| Shopping category accuracy | NOT RUN | NOT RUN | NOT RUN |
| Danish structured extraction | NOT RUN | NOT RUN | NOT RUN |
| Structured-output validity | NOT RUN | NOT RUN | NOT RUN |
| Exact tool selection and arguments | NOT RUN | NOT RUN | NOT RUN |
| Malformed tool calls | NOT RUN | NOT RUN | NOT RUN |
| Average / duplicate tool calls | NOT RUN | NOT RUN | NOT RUN |
| Error recovery rate | NOT RUN | NOT RUN | NOT RUN |
| Median / p95 accepted-task wall time | NOT RUN | NOT RUN | NOT RUN |
| Peak unified memory | NOT RUN | NOT RUN | NOT RUN |
| Swap growth / thrashing | NOT RUN | NOT RUN | NOT RUN |
| 15–30 call Hermes-loop pass rate | NOT RUN | NOT RUN | NOT RUN |
| 32K / 64K / 128K context correctness | NOT RUN | NOT RUN | NOT RUN |
| Cold / warm prefix-cache correctness and TTFT | NOT RUN | NOT RUN | NOT RUN |
| Overnight soak failures | NOT RUN | NOT RUN | NOT RUN |

### Upstream UltraFast numbers (not HomeCompute evidence)

The pinned dime v16b README reports 74.1 tokens/s for one stream and 212.2
tokens/s aggregate across eight streams. It also reports 93.09%/93.29% on its
492-item suite over two seeds and 21/24 versus 22/24 long generations. These
figures help choose what to measure, but they use the UltraFast AutoRound target
and do not demonstrate parity with NVIDIA NVFP4. They are not GB10 mixed-load
or HomeCompute workflow results. See the [pinned v16b source](https://github.com/dime-online/qwen3.8-Flash-DGX-UltraFast/tree/0c391a3e74b6a775cfe248691ca7fd855b1876a5).

### Workflow and capacity evidence

- Aula replay corpus: not present; target 10–15 cases, three repetitions.
- Offers replay corpus: not present; target 10 cases, three repetitions.
- Shopping reviewed corpus: not present; target 50 items across batches.
- Existing 64+ tool stress case: repository fixture/workflow exists, but no
  Flash-Next run is recorded.
- 64K/128K/approximately 200K/near-256K runs: not run.
- Flash-Next + Hviske v5.3 + Plapre Nano v2 mixed load: not run.
- 24-hour candidate soak: not run.
- Read-only live shadows: not run. Production n8n workflows were not changed.

### Promotion decision

The synthetic comparison does not qualify Flash-Next: no profile case passed
all checks, the recovery-loop run made no tool calls, and the harness has a
known false-negative for tool-only responses. Qwen3.6 remains the stored
rollback model. The owner directed LiteLLM's existing `automation-moe`
consumer alias to this Flash-Next runtime without changing n8n. This is an
operator-approved trial route, not a qualification result. UltraFast MTP
verification against its own target cannot establish parity with the distinct
NVIDIA NVFP4 Blazux target.

## Shared profile A/B corpus and execution

`benchmarks/plans/flash-next-profile-ab.json` runs the same deterministic
structured extraction and exact similar-tool selection cases ten times for
each selected profile. Private endpoint and key values are supplied through
environment variables; raw generations stay under ignored `benchmarks/results/`.
The separate tool-loop plan runs a synthetic 16-call Aula-style sequence with
a transient lookup error and retry over three trials. Its deterministic fake
tools verify loop mechanics without external side effects. These are starter
gates, not the completed owner-workload suite. The full run must add
sanitized/replay fixtures for 10–15 real Aula cases, 10 offer cases, 50 reviewed
shopping items, and a real repository repair task. Keep long-context,
memory/co-service, and soak results as separate measurements.

For an A/B run, first install both immutable profiles. Select exactly one
deployment on the isolated route, render/deploy the LiteLLM candidate config,
and cold-swap the matching local profile. For example:

```sh
python3 scripts/model_registry.py select-candidate --alias automation-qualification --deployment automation-flash-next-candidate
python3 scripts/model_registry.py render
sudo ./scripts/setup-compute-flash-next.sh activate-canary --profile quality
export HOMECOMPUTE_BENCHMARK_ALLOWED_ORIGINS=http://10.77.10.10:18300
export COMPUTE_AUTOMATION_CANDIDATE_BASE_URL=http://10.77.10.10:18300/v1
python3 benchmarks/harness.py run --plan benchmarks/plans/flash-next-profile-ab.json --release benchmarks/manifests/flash-next-profile-ab.example.json --candidate flash-quality-blazux-nvidia
sudo ./scripts/setup-compute-flash-next.sh deactivate-canary

python3 scripts/model_registry.py select-candidate --alias automation-qualification --deployment automation-flash-next-ultrafast-challenger
python3 scripts/model_registry.py render
sudo ./scripts/setup-compute-flash-next.sh activate-canary --profile ultrafast
python3 benchmarks/harness.py run --plan benchmarks/plans/flash-next-profile-ab.json --release benchmarks/manifests/flash-next-profile-ab.example.json --candidate flash-ultrafast-dime-autoround
sudo ./scripts/setup-compute-flash-next.sh deactivate-canary

python3 benchmarks/compare_flash_profile_runs.py \
  --baseline-run benchmarks/results/BASELINE_RUN \
  --challenger-run benchmarks/results/ULTRAFAST_RUN \
  --output benchmarks/results/flash-profile-comparison.json
```

The comparison command rejects different plan hashes, case/trial coverage, or
fixture hashes. Its report contains aggregate correctness, exact-check, and
duration deltas only; it never emits a promotion decision. The example address
must be replaced with the configured private Spark address.
Do not test through a public/LAN listener. Do not run both profiles resident
simultaneously. Capture the exact built image IDs, source-data window, hardware
telemetry, and effective request tuple into each private release manifest.
Promotion is blocked by any material drop in task success, factuality, exact
tool use, Danish output, long-context correctness, failure recovery, repeated
reliability, or memory stability, regardless of throughput.

Run the synthetic long agent loop as a separate comparison with candidate IDs
`flash-quality-blazux-hermes-loop` and `flash-ultrafast-dime-hermes-loop`, using
the same cold-swap sequence and `compare_flash_profile_runs.py` with
`--baseline-candidate` and `--challenger-candidate` set to those IDs:

```sh
python3 benchmarks/harness.py run --plan benchmarks/plans/flash-next-hermes-loop-ab.json --release benchmarks/manifests/flash-next-hermes-loop-ab.example.json --candidate flash-quality-blazux-hermes-loop
python3 benchmarks/harness.py run --plan benchmarks/plans/flash-next-hermes-loop-ab.json --release benchmarks/manifests/flash-next-hermes-loop-ab.example.json --candidate flash-ultrafast-dime-hermes-loop
python3 benchmarks/compare_flash_profile_runs.py --baseline-run benchmarks/results/QUALITY_LOOP_RUN --baseline-candidate flash-quality-blazux-hermes-loop --challenger-run benchmarks/results/ULTRAFAST_LOOP_RUN --challenger-candidate flash-ultrafast-dime-hermes-loop
```

### Read-only host snapshot

On 2026-09-29, SSH confirmed `home-spark` is ARM64 with an NVIDIA GB10 and
127.5 million kB total memory. The host had approximately 63.7 GiB available,
16 GiB swap with approximately 2.5 GiB in use, and 614 GiB free on `/`. Port
8005 listened on loopback and `/health` returned 200; unauthenticated
`/v1/models` returned 401. After Docker-group access became available, a
read-only container inspection found healthy `gb10-automation-nvidia-primary`
serving NVIDIA Qwen3.6 revision `1355db6a052410cfd62085d94b58866fd0f2c3c5`,
not the Unsloth revision identified as the production baseline by the handoff.
The container uses vLLM image digest
`sha256:2a7cde230b59f3ce6cab33dd245ba6bee41aa87b38c9fe84f966ff24016813ce`,
context 131072, FP8 KV cache, `qwen3_coder` tool parser, and `qwen3` reasoning
parser. Thinking kwargs and speculative configuration are injected through
runtime environment values whose effective settings have not been disclosed
or verified. The container advertises both `automation-moe-nvidia` and
`automation-moe`. This conflicts with the handoff's Unsloth production
reference, so neither revision is represented as a completed production
baseline here. Port 18300 was not listening. No candidate was installed or
started.

## Collection procedure

Use the existing private benchmark harness and keep raw results in the ignored
`benchmarks/results/` directory with mode 0700. Save only sanitized aggregate
metrics in this report. Use matched fixture hashes and source windows, run the
three tuples close together, and separate model inference time from MCP/network
time. For live tests use an unpublished read-only shadow with writes,
notifications, calendar mutations, shopping writes, and Notion writes blocked.

Before collection, reconcile the live NVIDIA deployment with the Unsloth
production reference in the handoff. Then inspect n8n model nodes and LiteLLM
request metadata without logging prompt or tool bodies. Capture production
runtime/image, context, MTP, KV type, parser, thinking settings, temperature,
token limit, and route. The current repository does not contain sufficient
evidence for either exact production tuple.

The isolated Flash lifecycle is:

```sh
sudo ./scripts/setup-compute-flash-next.sh validate
sudo ./scripts/setup-compute-flash-next.sh install
sudo ./scripts/setup-compute-flash-next.sh status
```

After image, mixed-load, fixture, and approval evidence is complete, an
operator may separately start the qualification canary with
`sudo ./scripts/setup-compute-flash-next.sh activate-canary`. This is not the
production cutover command. Stop it and restore the previously running text
containers with
`sudo ./scripts/setup-compute-flash-next.sh deactivate-canary`.

Production cutover remains a separate owner-approved maintenance procedure;
it is not implemented or executed by the qualification lifecycle.
The later cutover and rollback runbook is documented in
[`docs/operations/flash-next-cutover.md`](../operations/flash-next-cutover.md).
It is a gated procedure, not an executable deployment automation. The
operator's protected LiteLLM/n8n snapshot and credential store are outside this
checkout, so cutover is not technically ready until those recovery steps are
verified on the live hosts.
