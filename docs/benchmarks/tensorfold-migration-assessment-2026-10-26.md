# TensorFold migration assessment — 2026-10-26

**Decision: retain UltraFast; migration blocked and stopped before deployment.**
The owner agreed to defer the migration if it is a poor fit, potentially until
Qwen4 can be evaluated. No production service, route, model, driver, kernel,
image, or startup policy was changed. No target weights were downloaded.
Waiting is a decision to preserve verified functionality, not a prediction about
Qwen4's release, hardware fit, or quality.

## Observed system

Read-only SSH inspection reached both hosts on 2026-10-26. The detailed,
metadata-only snapshot is in the ignored local result file
`benchmarks/results/tensorfold-20261026/spark-inventory.json`, captured at
15:38:19 UTC (16:38:19 Europe/Copenhagen). Reproduce without copying credentials:

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-spark python3 - \
  < scripts/tensorfold_inventory.py > /tmp/spark-inventory.json
python3 tests/tensorfold-inventory-test.py
```

| Observed metric | Value |
| --- | ---: |
| Total unified memory | 121.625 GiB |
| MemAvailable | 7.561 GiB |
| Swap used / total | 12.177 / 16.000 GiB |
| UltraFast GPU allocation | 83.729 GiB |
| Hviske GPU allocations | 10.895 GiB |
| Whisper GPU allocation | 4.910 GiB |
| Plapre GPU allocations | 7.923 GiB |
| Combined speech GPU allocations | 23.728 GiB |
| GPU utilization / temperature at capture | 95% / 67 C |
| Filesystem available at initial inspection | approximately 477 GiB |

GPU allocations are part of unified host memory, **not additional RAM**.
Docker's approximately 2.23 GiB UltraFast RSS-style accounting substantially
understates its GPU/unified allocation. These measurements are a running-state
snapshot, not idle residency, isolated weight sizes, or inference peaks.
A four-sample vmstat check showed no sustained swap-out in its three live
intervals, and memory PSI averages were zero. Existing swap use does not prove
current thrashing, but neither establishes safe new allocation headroom.

Nine containers were running on Spark: UltraFast, three Plapre components,
Whisper plus HTTP adapter, and Hviske plus Wyoming and HTTP adapters. The active
speech models are continuously resident at the sampled instant; on-demand
loading behavior under every workflow has not been established. The stopped
ComfyUI image service and several older UltraFast rollback containers also
exist. No failed systemd units were reported. The active
`homecompute-comfyui-control` lifecycle service must be accounted for in any
future cutover; its root-owned unit was not readable by the SSH user.

UltraFast serves `automation-qualification` on `127.0.0.1:18300`, mapped to
container port 8000. `/v1/models` answered successfully and reported 262144.
Its observed tuple is 9g KV pool, 8 sequences, MTP 3, safetensors loader,
`qwen3_xml` tools, `qwen3` reasoning, and `unless-stopped` restart policy.
The container image ID is
`sha256:b80718ed664f12514a24363894c61c5d36f9ea8ca294244a149ddb672df2dc14`.
Its AutoRound hybrid/dense-MTP checkpoint and PLE table remain at their existing
bind-mounted paths under `/srv/gb10-ai/models/flash-ultrafast/`.
Exact source/checkpoint provenance must still be joined to the protected live
build records; repository pins alone are not proof of the running artifact.

Home-core also runs CPU embeddings, CPU Whisper, Piper, and Immich machine
learning, in addition to LiteLLM, n8n and the other application containers.
Its Plapre OpenAI TTS adapter was **already unhealthy** at discovery. This is
an existing integration issue, not a TensorFold regression. Neither health
labels nor model-list discovery establish speech synthesis/transcription
accuracy or complete application functionality.

## Startup feasibility

The inspected Mia recipe is pinned to commit
`e1f564eb915bb5272474b6c1c527aec3b9ed3de6`; it pins TensorFold engine commit
`db281878ddb836fd0df510d8771ecb7e0fe47d26` and NVIDIA QAD checkpoint
`b9ce18a6887e18c089fdea8509a33e63429a3300`. Its current base image is a mutable
NVIDIA PyTorch 26.07 tag; no target image digest was built or verified.
The full checkpoint retains 10 routed experts per token. The reduced-expert
INT4 alternative was not selected.

For 524288 total context tokens, the upstream startup guard calculates:

| Configuration | One-sequence KV estimate | Required MemAvailable at start |
| --- | ---: | ---: |
| FP8, text-only | 8.734 GiB (rounded to 9) | 110 GiB |
| FP8, vision | 8.734 GiB | 112 GiB |
| BF16, text-only | 15.031 GiB (rounded to 16) | 117 GiB |
| BF16, vision | 15.031 GiB | 119 GiB |

The guard sums its configured 81 GiB weight estimate, rounded KV, 8 GiB scratch,
10 GiB free floor, 2 GiB overhead and optionally 2 GiB vision workspace.
These are **upstream estimates/checks, not locally observed TensorFold memory**.
The README also describes disk-backed n-gram loading; that does not justify
assuming a smaller local peak or bypassing the implemented startup guard.
See the [pinned calculation](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark-TensorFold/blob/e1f564eb915bb5272474b6c1c527aec3b9ed3de6/scripts/config.sh#L137-L164).

Even after unloading UltraFast, total physical memory minus the observed speech
GPU allocations gives an optimistic ceiling of **97.898 GiB**, before OS,
CPU allocations, adapters, or transient buffers. This fails the current guarded
startup configuration by at least 12.102 GiB for FP8 text-only, or 19.102 GiB
for BF16 text-only. This is a feasibility inference, not an observed target OOM.
Reducing Qwen concurrency to one and disabling vision are already assumed in
the best case. Simply overriding memory checks or lowering the 10 GiB floor
would violate the requested safety conditions.

## Compatibility

The source rejects `response_format` and `guided_*` with HTTP 400; `logprobs`
and multiple completions are also unsupported. Prompt JSON and schema-validated
retries are not constrained decoding. The repository capability registry
requires structured output. Inspection of the published versions of all 25
active n8n workflows found two OpenAI nodes using `automation-moe` with explicit
`json_object` response formats, plus five structured output parsers (three
explicit schemas and two example schemas). Live LiteLLM has `drop_params=false`.
Sanitized evidence is retained in the ignored local result file
`benchmarks/results/tensorfold-20261026/core-compatibility.json`. Consequently
this is an actual integration concern rather than only a theoretical API gap.
See [pinned API notes](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark-TensorFold/blob/e1f564eb915bb5272474b6c1c527aec3b9ed3de6/README.md#L557-L575).

The existing launcher is not sufficient unchanged: host networking defaults to
`0.0.0.0`, automatic restart is absent, and authenticated startup smoke calls
omit Authorization. Existing HomeCompute activation code does not record the
currently running UltraFast container as its predecessor. No candidate was
activated through that path.

## Context, quality, performance and acceptance

`CONTEXT=524288` selects `TF_FLASHNEXT_YARN=4` in the pinned TensorFold
configuration. This verifies a source contract only. No 500K inference,
needle retrieval, BF16-versus-FP8 quality comparison, sustained mixed workload,
or reboot/rollback exercise was performed. Actual quality may improve, match,
or regress; no conclusion is supported yet.

| Requested comparison | UltraFast evidence | TensorFold evidence |
| --- | --- | --- |
| Idle / peak memory | Not isolated; running allocation 83.729 GiB | Not measured |
| Available host memory | 7.561 GiB at snapshot | Not measured |
| TTFT / prefill / decode / end-to-end | Not benchmarked in this assessment | Not measured |
| Concurrency throughput / mixed workload | Not newly tested | Not tested |
| STT / TTS latency and accuracy | Containers inspected; full suite not run | Not tested |
| JSON / tools / summarization quality | Active workflow dependencies inspected | API incompatibility identified; outputs not tested |
| Stable, accurate maximum context | Not established; API reports 262144 | Not established; source allows 524288 |
| Startup / reboot / rollback | Existing container remains running | Not exercised |

The migration acceptance criteria are **not met**. Large baseline benchmarks
were not started while MemAvailable was below the requested 10 GiB floor.
After the owner's decision to defer, inference, speech, concurrency and cutover
experiments were stopped before they could affect availability. There is no
new complete rollback backup or tested rollback procedure to claim: production
never left UltraFast, and existing images, weights, containers and routes were
preserved. Passwordless root access on Spark was unavailable; protected host
configuration was not copied or accessed through an alternate privilege path.

## Recommendation and reconsideration gates

Keep UltraFast and the current 262144 advertised context. Do not advertise
512K, remove speech models, change gateway parameter handling, or route schema
workloads to an unverified replacement. Track Qwen4 or later TensorFold releases
only when they can be evaluated against this machine and the actual workflows.

Reconsider when a full-quality target can start with all required services,
retain at least 10 GiB available through real mixed inference, and satisfy the
deployed structured-output requirements. Moving selected speech workloads to
existing CPU capacity could be investigated with separate latency/accuracy
qualification, but this assessment does not authorize or implement relocation.
A smaller quality-preserving checkpoint or improved runtime may offer a better
fit. Neither future model names nor README throughput numbers establish that.

The current low headroom and pre-existing TTS-adapter health failure deserve
separate diagnosis; no unrelated production repair was attempted here.
