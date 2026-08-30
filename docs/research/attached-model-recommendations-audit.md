# Audit of the attached GX10 model recommendations

Verified: 2026-08-26

Scope: the attached Danish document's named text models, model roles, claimed
specifications, GB10 performance, runtime/quantization compatibility, and the
changes that would be needed in this repository. Sources are limited to model
publishers, official runtime projects, and NVIDIA's own DGX Spark material.
Community benchmark numbers are treated as unverified until reproduced.

## Executive verdict

The document's architecture is directionally good: use deterministic code
first, route simple work to a small model, keep an efficient shared model for
most local work, and load specialized coding/reasoning models only when their
quality is worth the memory and operational cost. Its model list is also much
more current than the names initially suggest.

However, it should **not** be implemented as five simultaneously resident text
models, and its token-rate ranges should **not** become requirements. The
recommended disposition is:

| Role in the attachment | Audit disposition |
| --- | --- |
| Small: Qwen3 1.7B/4B | Real but no longer the best default shortlist. Benchmark `Qwen/Qwen3.5-4B` as the current small multimodal/tool-use candidate; retain Qwen3-1.7B only as the minimum-memory/latency control. |
| General: Qwen3.6-35B-A3B | Real, current, and already the repository's correct first GB10 artifact through NVIDIA's NVFP4 derivative. Keep it as the Phase C baseline. |
| Coding: Qwen3-Coder-Next | Real, current, and still the correct first specialized coding candidate. It needs its own FP8 recipe and workload qualification; do not inherit the Qwen3.6 NVFP4 flags. |
| Reasoning: gpt-oss-120b | Real and suitable as a high-capacity local challenger. Add Nemotron 3 Super NVFP4 as the NVIDIA-native single-Spark control; do not assume either can coexist with all other services. |
| Cloud frontier: GPT-5.6 Sol | Real and current. Keep Sol for the highest-risk planning/review; consider GPT-5.6 Terra for ordinary cloud escalation where cost matters. |

There is **no immediate Phase C configuration change** justified by this audit.
The repository already uses the strongest first-party-supported baseline and
already documents the major challengers. The useful optimization is to add
benchmark work and model-specific deployment recipes in Phase D, then change
alias mappings only after measured acceptance.

## Model identity and claim audit

### Qwen3 1.7B and 4B

**Identity: verified, but an older generation.** Qwen released dense
`Qwen/Qwen3-1.7B` and `Qwen/Qwen3-4B` under Apache-2.0. The 1.7B card specifies
1.7B total parameters and a 32,768-token context; the Qwen3 launch lists both
models and documents thinking/non-thinking and agent/tool capabilities.
[Qwen3-1.7B model card](https://huggingface.co/Qwen/Qwen3-1.7B),
[official Qwen3 launch](https://qwenlm.github.io/blog/qwen3/).

**Currency: replace the first-choice 4B candidate.** `Qwen/Qwen3.5-4B` is a
newer 4B model with native vision, 262,144-token context, native MTP, tool-call
serving instructions, and 201-language coverage. Qwen describes the generation
as improved across reasoning, coding, agents, visual understanding, and
efficiency. This makes Qwen3.5-4B the better current candidate for a reserved
small process, subject to local Danish and structured-call tests.
[Qwen3.5-4B model card](https://huggingface.co/Qwen/Qwen3.5-4B).

**Performance claims: not verified from primary sources.** The attachment's
`~100-160+ tok/s`, `146 tok/s at ~2K`, and `42 tok/s for Qwen3 8B` figures lack
a publisher or NVIDIA benchmark with the exact model artifact, runtime commit,
prompt/output lengths, and sampling settings. Treat them as hypotheses. The
small model must be tested for end-to-end routing accuracy, JSON/tool validity,
warm TTFT, and mixed-load latency rather than selected on decode rate alone.

**Recommendation:** add Qwen3.5-4B to the Phase D `home`/simple-automation
scorecard. Do not add a generic `fast` public alias yet. If it wins, route the
existing `home` alias—or a narrowly evidenced internal classification route—to
its process. Qwen3-1.7B remains useful only if 4B residency or latency is too
expensive and its lower task accuracy still passes.

### Qwen3.6-35B-A3B

**Identity and specifications: verified.** The official checkpoint is
`Qwen/Qwen3.6-35B-A3B`: 35B total parameters, 3B activated per token, native
262,144-token context, a vision encoder, MTP, reasoning controls, and tool-use
serving instructions. It is Apache-2.0.
[Qwen3.6 model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B).

**GB10 artifact: strongly verified.** NVIDIA publishes
`nvidia/Qwen3.6-35B-A3B-NVFP4` and an explicit one-DGX-Spark vLLM command using
FP8 KV cache, FlashInfer attention, Marlin MoE, native MTP, reasoning and tool
parsers, and tensor parallel size 1. NVIDIA says its ModelOpt conversion cuts
disk and GPU-memory requirements by about 3.06x versus BF16 and publishes a
BF16/NVFP4 accuracy comparison.
[NVIDIA Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[NVIDIA DGX Spark vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#run-agent-ready-qwen36-35b-model-with-vllm).

**Performance claims: unverified.** Neither Qwen's card nor NVIDIA's quantized
card publishes the attachment's `60-110`, `86 at 4K`, or `112 tok/s` single-Spark
figures. MTP support is verified; that exact speedup is not. MTP also changes the
decoding path, so acceptance must cover tool-call correctness and end-to-end task
time, not only accepted draft tokens.

**Currency: keep, but benchmark newer quality controls.** Qwen calls the newer
dense `Qwen3.8-27B` its most capable open-model generation to date and reports
substantial publisher-side gains in coding, professional work, research, and
agent execution. It is a dense 27B model with native 262,144-token context and
official FP8 weights; dense execution means it is not automatically faster than
a 3B-active MoE. Qwen3.8 therefore belongs after Qwen3.6 as a quality challenger,
not as an automatic replacement.
[Qwen3.8-27B-FP8 model card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8).

`nvidia/Gemma-4-31B-IT-NVFP4` is the other useful dense control already in this
repository's shortlist. Google documents Gemma 4 31B as a dense, multimodal,
256K-context model with function calling, coding, and multilingual support.
[Gemma 4 31B model card](https://huggingface.co/google/gemma-4-31B-it).

### Qwen3-Coder-Next

**Identity and specifications: verified.** The exact official model is
`Qwen/Qwen3-Coder-Next` (with an official FP8 artifact). It has 80B total and
3B activated parameters, native 262,144-token context, Apache-2.0 licensing,
tool calling, and a coding-agent focus including long-horizon tool use and
recovery from execution failures. It is a **non-thinking-only** model, a detail
omitted from the attachment. Qwen requires recent serving versions, documents a
`qwen3_coder` tool parser, and explicitly suggests reducing context to 32,768 on
OOM.
[Qwen3-Coder-Next model card and deployment guide](https://huggingface.co/Qwen/Qwen3-Coder-Next).

**Performance claims: too broad.** The attachment's `55-70 tok/s single-user`
can be plausible for a short-context community configuration, but it is not a
safe general figure. NVIDIA's own single-DGX-Spark FP8/vLLM results are 28.95
output tok/s for 128K input + 1K output and 38 output tok/s for 32K input + 1K
output at concurrency one. Aggregate generation throughput rises to 47 and 53
tok/s at concurrency two and four respectively; those are not per-user rates.
[NVIDIA Qwen3-Coder-Next DGX Spark benchmark](https://developer.nvidia.com/blog/scaling-autonomous-ai-agents-and-workloads-with-nvidia-dgx-spark/).

The claimed `22 tok/s per user at 16 requests`, `52 tok/s llama.cpp`, and `58
tok/s with DFlash` were not found in first-party GB10 material. NVIDIA's DFlash
article demonstrates the technique on datacenter Blackwell systems, not a
single GB10, so its numbers cannot be transferred.
[NVIDIA DFlash article](https://developer.nvidia.com/blog/boost-inference-performance-up-to-15x-on-nvidia-blackwell-using-dflash-speculative-decoding/).

**Currency: keep as primary coding candidate.** Qwen3.8-27B should be compared
as a newer dense coding/general model, but its publisher results do not prove it
beats Coder-Next on this repository's Codex implementation loop. Promote only
the model that produces accepted repository changes, passes builds/tests, and
survives cloud review with lower total task time.

### gpt-oss-120b

**Identity and specifications: verified.** OpenAI publishes
`openai/gpt-oss-120b` under Apache-2.0. It has 117B total and 5.1B active
parameters, MXFP4 MoE weights, configurable low/medium/high reasoning, function
calling, and structured outputs. OpenAI says it fits a single 80 GB accelerator.
It must be used with the Harmony response format; using another chat/response
format is not a harmless implementation detail.
[OpenAI gpt-oss-120b model card](https://huggingface.co/openai/gpt-oss-120b).

**Memory and upper performance claim: verified for a specific tuple.** NVIDIA's
DGX Spark multi-agent example reports about 63.5 GB for the MXFP4 model. NVIDIA's
official batch-one test at input/output lengths 2048/128 with llama.cpp reports
55.37 generated tok/s. This supports the attachment's upper end only for that
short-context configuration; it does not validate `38-55 tok/s` across long
reasoning requests or other runtimes.
[NVIDIA DGX Spark multi-agent model table](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/multi-agent-chatbot/assets/README.md),
[NVIDIA DGX Spark performance benchmark](https://developer.nvidia.com/blog/how-nvidia-dgx-sparks-performance-enables-intensive-ai-tasks/).

**Currency: keep, with a first-party challenger.** NVIDIA's
`NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` is 120B total/12B active, supports
reasoning/tool use, explicitly lists one DGX Spark as its minimum configuration,
and has an official Spark recipe. It is therefore the right local high-capacity
control. Its post-trained supported-language list omits Danish, its license is
NVIDIA-specific rather than Apache-2.0, and its Spark vLLM path uses a custom
reasoning parser and current nightly/runtime constraints; it is not a drop-in
replacement for gpt-oss.
[Nemotron 3 Super NVFP4 model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4),
[NVIDIA Nemotron DGX Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md).

### GPT-5.6 Sol

**Identity and currency: verified and current.** The exact model ID is
`gpt-5.6-sol`; the `gpt-5.6` alias routes to it. Official OpenAI documentation
describes Sol as the frontier model for complex professional work and documents
a 1,050,000-token context, 128,000 maximum output tokens, configurable reasoning,
function calling, structured outputs, and the Responses API.
[OpenAI GPT-5.6 Sol documentation](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

The attachment's use of Sol for architecture, high-risk work, and final review
is reasonable. OpenAI also positions `gpt-5.6-terra` as the intelligence/cost
balance and `gpt-5.6-luna` for cost-sensitive high-volume work. A three-step
local-to-cloud escalation can therefore use Terra for ordinary cloud fallback
and reserve Sol for the highest-impact decisions, if evaluation shows the extra
route is operationally worthwhile.
[OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model).

## What the current repository already gets right

Phase C is deliberately smaller than the attachment's final architecture:

- `deploy/compute-node/compose.yaml:35-60` launches exactly one `text-primary` vLLM
  process and exposes all six logical names—`coding`, `automation`, `research`,
  `home`, `meeting`, and `assistant`—from it.
- `config/compute-node.env.example:11-27` selects the exact
  `nvidia/Qwen3.6-35B-A3B-NVFP4` artifact; lines 50-70 use a conservative 32K,
  concurrency-two, 40%-memory baseline with MTP off. The exact model-specific
  MTP-on tuple remains available only as an explicit experiment.
- `docs/adr/004-model-aliases.md:9-35` correctly separates stable workload
  aliases from concrete models and explicitly defers extra `fast`/`reasoning`
  aliases until workload evidence exists.
- `docs/implementation-plan.md:134-151` already requires Qwen3.6 MTP on/off,
  Gemma 4 31B, Qwen3.8-27B, role-specific challengers, mixed load, memory
  measurement, and a measured resident-set decision before promotion.
- `docs/research/coding-model-evaluation.md` already puts Qwen3-Coder-Next FP8
  first for coding and `docs/research/general-model-evaluation.md` already
  includes gpt-oss-120b and Nemotron 3 Super.

This means the attachment does not expose a mistaken Phase C model choice. It
exposes useful Phase D work that has not yet been encoded as runnable model
profiles.

## Optimization gaps and recommended work

### 1. Reserved small model: benchmark it, do not deploy it immediately

Add `Qwen/Qwen3.5-4B` and optionally `Qwen/Qwen3-1.7B` to the Phase D fixture
matrix. Test deterministic extraction/classification, strict JSON/tool syntax,
Danish, Home Assistant schema accuracy, warm TTFT, and behavior while the shared
model and speech stack are busy.

Only create a second resident process if the shared Qwen3.6 misses the p95
`home` latency or mixed-load gate. This matches the existing design order in
`docs/design-specification.md:276-325`. If it wins, initially remap the existing
`home` alias; introduce a new `fast` alias only when a separate authenticated
consumer/workload genuinely needs it.

### 2. Dedicated coding and reasoning recipes: required before evaluation

Changing only `MODEL_ID` in the current Compose service is unsafe:

- Qwen3-Coder-Next should start with official FP8 weights, 32K context,
  `qwen3_coder` tool parsing, and its non-thinking response behavior.
- gpt-oss-120b requires MXFP4/Harmony handling and its own reasoning/tool tuple.
- Nemotron 3 Super requires NVIDIA's custom reasoning parser, NVFP4-specific
  settings, and a separately pinned compatible runtime.
- Qwen3.8-27B FP8 requires a recent Qwen3.8-capable runtime and its own
  reasoning/thinking settings.

Create evaluation-only Compose profiles or generated manifests for these exact
tuples. Do not add them to the default `up` path, and do not inherit Qwen3.6's
ModelOpt/Marlin/MTP/parser settings implicitly.

### 3. Residency and swapping: explicit policy needed, but only after load tests

The attachment's “always / nearly always / on demand” split is sensible. On a
128 GB unified-memory appliance, however, a 63.5 GB gpt-oss process, an FP8 80B
coder, KV caches, OS/display, vLLM workspaces, STT/TTS, and telemetry cannot be
assumed to coexist safely.

The benchmark harness should record cold model load, warm-up, page-cache effects,
peak unified memory, unload/recovery, and time to atomically remap an alias.
Interactive `home` must never wait for a large model swap. A practical target is:

1. keep one qualified shared model resident;
2. add a small reserved `home` process only if measured necessary;
3. serialize coder/reasoner activation behind a controlled job queue;
4. swap aliases only after readiness passes, with rollback to the prior process.

This is research/design work, not authorization to add a model manager to Phase C.

### 4. Embeddings and reranking: valid omission, but outside the current text API

The attachment is correct that retrieval should use dedicated models rather
than a generative LLM. Qwen's official family offers 0.6B, 4B, and 8B embedding
and reranking models, each with 32K input and multilingual/code-retrieval support.
Start with `Qwen/Qwen3-Embedding-0.6B` and `Qwen/Qwen3-Reranker-0.6B`; move to 4B
only if pinned retrieval fixtures show a meaningful quality gain.
[Official Qwen3 Embedding and Reranking release](https://qwenlm.github.io/blog/qwen3-embedding/).

The current architecture intentionally keeps vector databases, canonical
memory, and application state off GB10 (`docs/architecture.md:129-131`). Preserve
that boundary: GB10 may host stateless embedding/reranking inference, while the
existing application host owns indexes, authorization, and persistence. Add
stable fixed routes only after a concrete Hermes/Meeting Assistant retrieval
contract and privacy test exists.

### 5. Vision: use existing multimodality before adding another resident model

Qwen3.6-35B-A3B, Qwen3.5-4B, Gemma 4 31B, and Qwen3.8-27B are already
multimodal. The attachment's separation of classical object detection from VLM
reasoning is sound, but a separate vision model is not yet justified by a named
repository requirement or fixture. First evaluate the selected shared or small
model on document/image understanding. Add a dedicated VLM only if it improves
latency, quality, or isolation enough to offset another runtime and memory
budget. Keep deterministic camera detection in the existing computer-vision
pipeline rather than routing every frame through a VLM.

## Recommended staged model matrix

| Stage | Artifact | Purpose | Residency expectation |
| --- | --- | --- | --- |
| C baseline | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Shared protocol, Danish, tool and mixed-load baseline | Resident |
| D small control | `Qwen/Qwen3.5-4B` | `home`, routing, extraction, simple multimodal | Resident only if it proves necessary |
| D small floor | `Qwen/Qwen3-1.7B` | Minimum memory/latency comparison | Evaluation only |
| D coding | `Qwen/Qwen3-Coder-Next-FP8` | Codex implementation loop | On demand / serialized |
| D dense quality | `Qwen/Qwen3.8-27B-FP8` and `nvidia/Gemma-4-31B-IT-NVFP4` | General/coding quality controls | Evaluation; one at a time |
| D local reasoning | `openai/gpt-oss-120b` | High-capacity reasoning/tool comparison | On demand / serialized |
| D NVIDIA reasoner | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` | Single-Spark NVIDIA-native high-capacity control | Evaluation; not co-resident by assumption |
| Later retrieval | Qwen3 Embedding/Reranker 0.6B, then 4B if needed | Stateless retrieval inference | Small resident services only after a retrieval contract |
| Cloud | `gpt-5.6-terra`, then `gpt-5.6-sol` | Ordinary escalation, then frontier/high-risk review | Cloud |

## Acceptance rule

Do not promote a model from its name, active-parameter count, publisher stars,
or peak short-context tok/s. Promote the exact tuple of model revision,
quantization, tokenizer/chat template, parser, runtime image, launch flags,
context, and concurrency only after it passes:

1. Danish and English task-quality fixtures;
2. strict structured output and real tool loops;
3. Codex Responses/streaming/compaction where applicable;
4. build/test and cloud-review acceptance for coding;
5. p50/p95 TTFT and total task time at representative context;
6. mixed-load Home Assistant, Codex, n8n, meeting, and speech traffic;
7. peak unified-memory, OOM recovery, cold load, and rollback;
8. license, privacy, and no-body-logging gates.

That preserves the attachment's useful routing idea while removing its two
riskiest assumptions: that every role needs a permanently loaded model, and
that a headline decode speed transfers across contexts, runtimes, and agent
workflows.
