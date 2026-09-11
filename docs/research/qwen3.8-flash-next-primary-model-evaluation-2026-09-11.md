# Qwen3.8-Flash-Next as the always-loaded primary model

Verified: 2026-09-11

Scope: whether Qwen3.8-Flash-Next should replace the current provisional shared
model as the always-loaded primary model on one NVIDIA GB10 / DGX Spark. This
note reads all three supplied links, distinguishes their checkpoints and
profiles, and checks their claims against Qwen, NVIDIA, Hugging Face, vLLM, and
the published recipe source. Community benchmark results are evidence of
feasibility, not vendor validation. Recommendations are inferences unless
explicitly identified as source facts.

Status: focused runtime evidence supporting
[ADR-019](../adr/019-single-gb10-model-roster.md). ADR-019 and the
[machine-readable roster](../../config/gb10-model-roster.json) are authoritative
for the selected checkpoints and deployment mode.

## Verdict

**Do not make Qwen3.8-Flash-Next the always-loaded production primary on one
GB10 today. Keep Qwen3.8-27B as the resident workhorse and Flash-Next as a
mutually exclusive, operator-controlled heavy mode. Do not include Qwen3.6 in
the planned evaluation.**

The model itself is unusually compelling: Qwen reports materially stronger
coding and agent results than Qwen3.8-27B, NVIDIA's official NVFP4 evaluation is
close to its FP8 baseline, and community measurements prove that useful
single-Spark throughput is possible. Its 6B active MoE path, native 262K
context, image/video input, reasoning controls, and tools make it a plausible
single-model replacement for several current aliases.

The deployment needed to make the official checkpoint fit is not production
mature, however. NVIDIA's checkpoint occupies about 124 GiB on disk and its
model card lists B200/B300, not GB10, as supported hardware. The successful
single-Spark lane leaves its 47.68 GiB PLE table on NVMe, carries roughly 76 GiB
of weights in unified memory, and applies a patch set to a pinned vLLM nightly.
The required upstream disk-backed PLE and FP8-KV paths are still open pull
requests. The most attractive profile leaves only about 16 GB host
`MemAvailable` after boot, takes about 10.5 minutes to load, and has not passed
this repository's Danish, Codex Responses/tool-loop, Home Assistant,
privacy/security, mixed-load, forced-OOM recovery, or 24-hour soak gates.

An always-loaded deployment may ultimately be the right operational choice
because reloading takes more than ten minutes. That conclusion should follow a
successful qualification run; it should not be inferred from nominal fit or
tokens per second.

## Revised three-way role: newer does matter

Qwen3.6 was selected as the first integration baseline because NVIDIA publishes
an explicit one-DGX-Spark vLLM command for its checkpoint. That makes it the
lowest-risk way to prove the original protocol and deployment path; it does not
make it the best model generation to standardize on now that an official
Qwen3.8-27B NVFP4 artifact exists.

| Candidate | Exact artifact and operational fit | Role now |
| --- | --- | --- |
| Qwen3.6 35B A3B | `nvidia/Qwen3.6-35B-A3B-NVFP4`, revision `1355db6a052410cfd62085d94b58866fd0f2c3c5`, 23,462,477,857 bytes (21.85 GiB). It is a 35B-total/3B-active MoE, and NVIDIA provides a TP=1 DGX Spark command. [Model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4), [artifact tree](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4/tree/main) | **Historical integration reference only.** The checked-in profile explains the repository's earlier setup, but the older 3B-active model is not a planned install, benchmark, or fallback. |
| Qwen3.8 27B | `nvidia/Qwen3.8-27B-NVFP4`, revision `dbb8f445b3145f8a4c18ddc769f032d57d32867c`, 21,945,291,128 bytes (20.44 GiB). It is dense, so all 27B parameters participate in decode, but its artifact is actually smaller than the Qwen3.6 artifact and leaves ample room in 128 GB unified memory for KV cache, audio inference and recovery headroom. NVIDIA lists Blackwell with vLLM and SGLang; its published example was tested on GB300 and uses TP=4, so TP=1 GB10 remains a local qualification item. [Model card and quant evaluation](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4), [artifact tree](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4/tree/main) | **Evidence for the selected 27B model class.** Qwen calls Qwen3.8 its most capable open-model generation and reports large gains over the prior dense generation in coding, agent, instruction-following and multimodal tasks. ADR-019 selects the Unsloth NVFP4 checkpoint for deployment, so NVIDIA's artifact is not a second planned install. [Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B#benchmark-results) |
| Qwen3.8 Flash-Next | `nvidia/Qwen3.8-Flash-Next-NVFP4`, revision `fc694b54fb0174e0913e6adf86691ef85a4ead47`, 132,734,506,847 bytes (123.62 GiB). It is 125B-total/6B-active plus a 51B n-gram table and 4B MTP. The official card lists B200/B300, while single-GB10 operation relies on disk-backed PLE and a moving patched runtime. [Model card](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4), [artifact tree](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/main) | **High-capability challenger.** Qwen reports it ahead of 27B on coding, tool use, professional work and reasoning, but its narrow memory margin and deployment complexity make it the wrong default resident tuple until it passes soak and mixed-load testing. [Qwen comparison](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#benchmark-results) |

Accordingly, the practical order is **Qwen3.8-27B first and Flash-Next as the
quality-ceiling heavy mode**. “First primary
candidate” is deliberately not the same as “already accepted production
primary”: the exact Qwen3.8-27B TP=1 tuple still has to pass Danish, Codex tool
loops, Home Assistant safety, p95 latency, mixed audio/text load and the 10%
measured-memory-headroom gate. If its dense decode latency misses those gates,
test a current-generation smaller model against the same acceptance suite; if
Flash-Next later clears its harder operational gates and wins accepted-task
time, it can supersede 27B.

## The three supplied links do not describe one configuration

| Supplied evidence | Actual checkpoint/profile | What is established | What is not established |
| --- | --- | --- | --- |
| [MiaAI forum thread](https://forums.developer.nvidia.com/t/miaai-lab-new-qwen3-8-flash-next-nvfp4-recipe-for-1x-dgx-spark-1m-context-vision-video-37-tok-s-c1/382446) | Community `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4`, about 99 GiB, with a roughly 27 GiB PLE table offloaded; the current shipped profile is native 262K, while most later measurements use 512K YaRN. | A single Spark can serve text, images, and video with useful decode/prefill speed and a carefully guarded memory budget. | The headline's 1M context was not run. The current source explicitly says 1M has never been run on that host. The linked post at `/6` and other replies report noticeable real-work quality degradation versus a less aggressive community quant. |
| [Official-NVFP4 forum thread](https://forums.developer.nvidia.com/t/qwen3-8-flash-next-on-1-2-and-4-dgx-sparks-with-nvidias-official-nvfp4-quant-64-tok-s-peak-single-stream/382476) | `nvidia/Qwen3.8-Flash-Next-NVFP4`, disk-backed PLE, vLLM nightly and patches. | The initial one-Spark profile measured 32.5 tok/s median over 40 prompts and 43.8 on a count-to-100 peak. The source repository later reports 43.9 median after staged PLE gather and reduced-vocabulary MTP. | The title's approximately 64 tok/s peak is the **two-Spark SPEED profile**, not one Spark. The 40-prompt harness is mainly a performance suite and its reported 0.88 quality score cannot establish production task quality. |
| [molo-molo SparkRun repository](https://github.com/molo-molo/qwen3.8-flash-next-single-sparkrun) | Repackages the preceding official-NVFP4 single-Spark lane for SparkRun: 262K, FP8 KV, disk-backed PLE, MTP3, six sequences, vLLM nightly. | The recipe is transparent and pins a vLLM commit-tagged nightly; its settings match the upstream community lane. | Its README says the live `sparkrun run` was deliberately **not executed**. At verification it had two commits and no release, so it is packaging evidence, not an independently reproduced result. |

The first thread therefore cannot validate the third recipe's quality, and the
second thread's two-Spark peak cannot be used as a one-Spark expectation.

## Exact model identity

The open-weight model is exactly `Qwen/Qwen3.8-Flash-Next`. Qwen calls it an
**experimental preview** of the architecture intended to underpin Qwen4. It is
not the same offering as `Qwen3.8-Flash`: Qwen describes that as the official
version based on Flash-Next, with 1M context by default and built-in tools.
[Qwen model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#qwen38-flash-next)

The base is a multimodal causal model with a vision encoder. Its language side
has 125B parameters with 6B activated per token, plus a 51B n-gram/PLE embedding
and a 4B MTP module. It combines Gated DeltaNet, Qwen Sparse Attention, 512
routed experts with 10 active plus one shared expert, and a native 262,144-token
window extendable to 1,000,000 with YaRN. It accepts text, images, and video and
produces text. [Qwen model overview](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#model-overview),
[NVIDIA model architecture and input](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#model-architecture)

The artifact relevant to the conservative trial is exactly
`nvidia/Qwen3.8-Flash-Next-NVFP4`, revision
`fc694b54fb0174e0913e6adf86691ef85a4ead47` at verification. Hugging Face
reports 132,734,506,847 bytes of repository storage, about 123.6 GiB. The model
is mixed precision rather than uniformly four-bit: main routed experts are W4A4
NVFP4, attention/shared experts/other main-model layers remain BF16, MTP routed
experts are block-scaled FP8, and the PLE table is per-tensor FP8. NVIDIA says
this is about 2.7 times smaller than BF16 and was not fine-tuned.
[NVIDIA checkpoint tree](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/main),
[NVIDIA quantization description](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#post-training-quantization)

The Mia checkpoint is a different, community-produced artifact. Its recipe
states approximately 98.6 GiB on disk, 71.75 GiB of resident GPU weights, and a
26.82 GiB offloaded PLE table. It changes more precision-sensitive state than
the official NVIDIA artifact, so its speed, memory, and subjective quality
reports must not be transferred to the NVIDIA checkpoint.
[Mia recipe source](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark)

## Capability and quality evidence

Qwen's own evaluation makes Flash-Next a serious quality candidate. Against
Qwen3.8-27B, it reports 58.7 versus 42.2 on DeepSWE 1.1, 62.5 versus 61.7 on
SWE-bench Pro, 81.0 versus 73.8 on SWE-bench Multilingual, 73.5 versus 67.1 on
Toolathlon Verified, 81.3 versus 79.5 on IFBench, and 91.7 versus 89.2 on GPQA
Diamond. These are publisher results; harnesses and settings vary, and some
benchmarks are in-house. They justify a trial, not automatic promotion.
[Qwen benchmark table and methodology](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#benchmark-results)

NVIDIA's official quantization comparison is reassuring. Across nine reported
reasoning, coding, tool-use, long-context, instruction, and multimodal tests,
NVFP4 stays close to the official FP8 checkpoint and sometimes scores higher.
For example, GPQA changes 92.0 to 91.5, tau2 Telecom 90.8 to 90.1,
Terminal-Bench 2.1 83.3 to 82.9, while HLE changes 34.7 to 35.4 and MMMU Pro
77.1 to 78.3. The evaluation used `reasoning_effort=xhigh`, temperature 1.0,
and up to 131,072 generated tokens. It does not test Danish or this repository's
exact prompts and consumers. [NVIDIA evaluation](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#evaluation)

Community quality evidence is mixed:

- One run in the official-NVFP4 forum thread reports 89/100 on a hard-mode
  third-party tool-eval suite, with autonomous planning weakest at 67%; a
  three-trial normal run reports 92.7 plus or minus 0.6. Thinking at medium did
  not improve that suite's score but increased total time from about 1,160 to
  1,720 seconds. This is promising integration evidence, not a controlled
  comparison with the current model.
- The supplied Mia thread contains multiple reports that the aggressively
  optimized Mia artifact is visibly worse in coding, reasoning, browser/tool
  use, and long agent sessions than the `blazux` variants. The linked `/6` post
  explicitly calls its chart subjective rather than scientific.
- Mia's own source says FP8 KV quality is unsettled: sparse-attention key
  quantization can change which blocks are selected, needle tests cannot rule
  out fluent but wrong long reasoning, and a matched long-reasoning A/B had not
  been run. [Mia quality caveat](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark#fp8-kv-cache)

No supplied or primary source establishes Danish factuality, Danish entity and
tool selection, long-lived Hermes behavior, the exact Codex protocol, or
security-sensitive code generation. Those are central workloads here.

## Single-GB10 performance and residency

The best documented official-checkpoint single-Spark profile uses the model
byte-for-byte while leaving the 47.68 GiB PLE table on local NVMe. Approximately
76 GiB of weights remain resident. With FP8 KV, `gpu-memory-utilization=0.80`,
MTP3, staged PLE gather, decode-only CUDA graphs, six sequences, and native
262K context, the source reports:

| Metric | Community measurement |
| --- | --- |
| Model load to ready | about 10.5 minutes |
| Single-stream median, 40 prompts | 43.9 tok/s |
| Single-stream prose | 29.0 tok/s |
| Single-stream coding | 44.3 tok/s |
| Six-stream aggregate | 68.8 tok/s; 21.7 tok/s per stream |
| Cold prefill | 1,269 tok/s at 7K; 1,757 at 28K; 1,760 at 113K |
| KV pool | 1,027,392 tokens |
| Earlier matched memory row | about 105 GB used / 16 GB available after boot |

Source, launcher, raw JSON and KV ledger:
[tonyd2wild single-Spark lane](https://github.com/tonyd2wild/Qwen3.8-Flash-Next-NVFP4-DGX-Spark/tree/main/single-spark-vllm-tp1).

These numbers are credible feasibility evidence because the repository includes
the prompts, harnesses, raw outputs, launch scripts, and boot-by-boot memory
ledger. They are still a single operator's measurements on a rapidly changing
nightly and custom patch set. They also reveal the operational trade-off:
Qwen3.8-Flash-Next can be the one large resident model, but it leaves little
space for another substantial resident service or model.

The approximately 16 GB post-boot availability is nominally above this
project's 10% headroom threshold (about 12.2 GiB of a 121.7 GiB usable pool),
but the requirement applies under the project's acceptance mixed load and
includes runtime, telemetry, workspaces, recovery headroom, and relevant
co-tenants. The cited official-checkpoint run does not prove that gate. The Mia
recipe's different checkpoint fell to 12.8 GiB during a 2.5-hour coding load,
illustrating how narrow the margin can become; it also records three server
losses before a stricter host-side cap was added.
[Mia memory and incident evidence](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark#measured-profile),
[project memory requirements](../requirements.md#performance-and-capacity-requirements)

One million tokens should not be a selection reason on one Spark today. Native
context is 262K; Qwen warns that static YaRN can hurt shorter-text performance.
The current Mia source defaults to 262K, caps BF16 YaRN at 512K, and says 1M
with FP8 KV fits arithmetically but has never been run on that host. The
official-checkpoint SparkRun recipe is also explicitly 262K.
[Qwen YaRN guidance](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#best-practices),
[Mia long-context source](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark#long-context-beyond-262k-yarn),
[SparkRun recipe](https://github.com/molo-molo/qwen3.8-flash-next-single-sparkrun/blob/main/qwen3.8fn-nvidia-nvfp4-mtp-vllm-m0l0.yaml)

## Runtime outlook: expected, but not yet a stock GB10 deployment

**Yes: efficient upstream vLLM and SGLang support is now a reasonable
expectation. No upstream project or NVIDIA source gives a delivery date, and
generic Blackwell support must not be read as a qualified GB10/SM121 recipe.**
The core model work is landing quickly, but the exact combination needed on one
Spark still spans open or newly merged work.

| Runtime | Already upstream | Still required for a clean one-GB10 official-NVFP4 tuple |
| --- | --- | --- |
| vLLM | Core Qwen3.8-Flash-Next support, including BF16/FP8/NVFP4 without PLE offload, merged on 2026-08-31. NVIDIA's validation cited by that PR covered GB200/GB300/H200 and TP2+, not GB10 TP1. Fused PLE kernels, separate QSA prefill/decode paths, PLE stride fixes, and UVA host offload have since merged. [core model PR](https://github.com/vllm-project/vllm/pull/53896), [UVA PLE PR](https://github.com/vllm-project/vllm/pull/54371) | A reclaimable disk-backed PLE implementation remains open; the normal host-offload path still consumes the GB10's shared physical memory and has deadlocked or OOMed in reported TP1 tests. FP8/NVFP4 QSA KV support is also still open and being split into narrower PRs. [mmap PLE PR](https://github.com/vllm-project/vllm/pull/54129), [TP1 offload issue](https://github.com/vllm-project/vllm/issues/53960), [QSA KV PR](https://github.com/vllm-project/vllm/pull/54846), [narrower FP8 KV PR](https://github.com/vllm-project/vllm/pull/55557) |
| SGLang | Core Qwen3.8-Flash-Next support merged on 2026-09-08 and explicitly includes an SM121/GB10 QSA correctness kernel, so SGLang is no longer merely a generic B200/B300 prospect. [model-support PR](https://github.com/sgl-project/sglang/pull/37500), [SM121 kernel source](https://github.com/sgl-project/sglang/tree/main/python/sglang/kernels/kda_kernels/qwen38_qsa_sm121) | Loading NVIDIA's mixed NVFP4/FP8 checkpoint and file-backed PLE for GB10 are separate draft PRs. The combined branches have booted and served on one GB10, but that is pre-merge validation; a one-time kernel build also drove more than 82 GiB of swap when attempted beside the loaded model. Quantized QSA KV and several correctness guards remain open. [mixed-checkpoint loader PR](https://github.com/sgl-project/sglang/pull/38569), [file-backed PLE PR](https://github.com/sgl-project/sglang/pull/38570), [quantized QSA KV PR](https://github.com/sgl-project/sglang/pull/37798) |

The likely performance ingredients are therefore visible rather than
speculative: native NVFP4 expert kernels, an SM121-specific sparse-attention
path, MTP/NEXTN speculative decoding, fused/staged PLE gathers, disk-backed PLE,
and FP8 KV. Community vLLM already demonstrates roughly 44 tok/s median on one
Spark with most of this stack, while the SGLang draft stack reports successful
single-GB10 evaluation with CUDA graphs and file-backed PLE active. That makes
Flash-Next a credible future replacement candidate for the dense 27B model,
not merely a model that can be made to load.

Mature runtime support would not remove the hardware trade-off. Qwen3.8-27B's
20.44 GiB artifact leaves broad unified-memory headroom. Flash-Next still needs
roughly 76–82 GB resident plus runtime, KV and staging memory, while serving
its 47.7 GiB PLE table from NVMe. Disk-backed PLE avoids keeping the whole table
anonymous or GPU-resident, but it does not make PLE access free; it introduces
NVMe/page-cache dependence and a much longer cold-start path. FP8 KV improves
context capacity, not the base-weight footprint.

There are also SM121-specific correctness gates, not just missing packaging.
Open reports cover long/repeated prefill hangs and QSA nondeterminism in vLLM,
plus invalid probabilities or silent corruption around NEXTN/CUDA graphs in
SGLang. SGLang's core merge contains an SM121 kernel, but further exact-top-k
and mixed-chunk guards remain open. [vLLM long-prefill issue](https://github.com/vllm-project/vllm/issues/54629),
[vLLM QSA nondeterminism](https://github.com/vllm-project/vllm/issues/54521),
[SGLang decode-graph crash](https://github.com/sgl-project/sglang/issues/37052),
[SGLang output-corruption issue](https://github.com/sgl-project/sglang/issues/37111),
[SGLang exact-top-k fix](https://github.com/sgl-project/sglang/pull/38144).

The planning implication is: **expect Flash-Next to become trialable without a
private fork soon, but do not schedule 27B's retirement against an assumed
date.** Re-evaluate when one engine ships a pinned release/container in which
the official NVIDIA checkpoint loads on TP1 GB10 with file-backed PLE, the
chosen compressed-KV and MTP paths are upstream, and the cited SM121 failures
are fixed or excluded by a documented safe profile. At that point Flash-Next
can replace 27B if it also passes this project's mixed-load, memory-headroom,
quality and soak gates; until then, 27B remains the safer resident primary.

## Runtime, security, and operational caveats

NVIDIA's model card lists vLLM as the supported runtime, Model Optimizer 0.46.0
and Transformers 5.16.0 or later, Linux, and B200/B300 hardware. It does not
list GB10. NVIDIA's general DGX Spark vLLM support matrix also does not yet list
Qwen3.8-Flash-Next. Successful GB10 use is therefore a community-validated port,
not an NVIDIA-qualified single-Spark tuple.
[NVIDIA software and hardware compatibility](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#software-integration),
[NVIDIA DGX Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix),
[DGX Spark hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)

The upstream situation is improving but still moving:

- The ModelOpt mixed-checkpoint MTP fix was merged on 2026-09-08.
  [vLLM PR 55513](https://github.com/vllm-project/vllm/pull/55513)
- The fused PLE state-stride corruption fix was merged on 2026-09-05.
  [vLLM PR 55375](https://github.com/vllm-project/vllm/pull/55375)
- Disk-backed PLE support remains open and requires Model Runner V2.
  [vLLM PR 54129](https://github.com/vllm-project/vllm/pull/54129)
- FP8/NVFP4 KV support on the QSA path remains open; the PR had merge conflicts,
  and a narrower FP8-only replacement was also open at verification.
  [vLLM PR 54846](https://github.com/vllm-project/vllm/pull/54846)
- Open reports cover prefix-cache plus MTP corruption on hybrid models,
  nondeterministic greedy output near the QSA indexer budget, and other
  long-context/GB10 failures. These reports are not proof that every recipe is
  affected, but they identify paths that require direct regression tests.
  [prefix/MTP issue 53912](https://github.com/vllm-project/vllm/issues/53912),
  [QSA nondeterminism issue 54521](https://github.com/vllm-project/vllm/issues/54521),
  [GB10 long-context issue 54629](https://github.com/vllm-project/vllm/issues/54629)

The supplied SparkRun recipe also starts the service on `0.0.0.0`, enables
`--trust-remote-code`, runs the container as root so it can overwrite files in
`site-packages`, and applies local patches at startup. Its image name embeds a
git commit but is not pinned by OCI digest. These are acceptable lab mechanics,
not this repository's production network, immutability, and least-privilege
posture. If trialled, bind privately, put it behind the existing authenticated
gateway, build a reviewed derived image, pin that image by digest, and pin the
model revision and patch hashes. SparkRun itself remains an optional benchmark
launcher rather than the production deployment layer under the existing
[SparkRun evaluation](sparkrun-evaluation-2026-09-09.md).

The base model uses Qwen Community License 1.0, not Apache-2.0. NVIDIA's quant
is governed by the NVIDIA Open Model License with the Qwen license as additional
terms. The Qwen license grants broad rights but contains attribution and
separate-license conditions for some large-scale and commercial hosted/work-
assistant uses. Complete the project's license review before promotion.
[Qwen license](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE),
[NVIDIA checkpoint terms](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#licenseterms-of-use)

## Recommended qualification path

1. Install and qualify ADR-019's pinned `unsloth/Qwen3.8-27B-NVFP4` tuple first
   as the always-loaded primary. Add the selected
   `RadixArk/Qwen3.8-Flash-Next-NVFP4` plus pinned `blazux` recipe as the
   mutually exclusive high-capability mode, not a new permanent alias. Do not
   schedule Qwen3.6 for installation or evaluation. Pin each model commit,
   runtime source commit, container
   digest, patches, parsers, template, and every precision flag as one release
   tuple.
2. Establish a conservative reference at native 262K with MTP off and BF16 KV
   where practical. Then test MTP and FP8 KV separately. Treat each change as a
   new quality tuple; do not combine them before attribution is possible.
3. Evaluate the intended Qwen3.8-27B primary against the declared acceptance
   thresholds on the same Danish automation, research, meeting, Home Assistant,
   Hermes, coding, and long-tool-loop fixtures. Add only current-generation
   controls with a concrete quality, latency, or capacity hypothesis. Measure
   accepted end-to-end task time, not only decode rate.
4. Run the exact Codex Responses/SSE/tool/cancellation/retry suite. A working
   `/v1/chat/completions` endpoint and third-party tool score do not prove the
   Codex contract.
5. Exercise 32K, 64K, 128K, and 262K buckets with prefix caching both off and on;
   include repeated long reasoning, multi-turn preserved thinking, images, and
   video. Defer 512K/1M until native-context operation passes.
6. Run the required concurrent mixed load, 24-hour soak, forced allocation
   failure, restart, and cold-boot recovery tests. Require at least 10% measured
   unified-memory headroom at the worst point, no swap growth, no driver
   allocation errors, and predictable recovery.
7. Promote Qwen3.8-27B to always-loaded primary if it passes every hard gate and
   the predeclared weighted workload threshold. Promote Flash-Next over 27B only
   if it later passes the additional
   runtime and headroom gates and wins accepted-task time. Keep the previous
   tuple locally cached and rollbackable.

## Promotion decision rule

Promote Qwen3.8-Flash-Next only when all of the following are true:

- the official NVIDIA checkpoint, or a separately approved artifact, passes
  license and provenance review;
- Danish, tool/schema, Home Assistant safety, Hermes, and Codex acceptance are
  at least as reliable as the current baseline;
- the optimized FP8-KV/MTP tuple shows no meaningful regression from the
  conservative reference on repeated long-reasoning and agent workloads;
- p95 latency and accepted-task time win the project's weighted workload;
- at least 10% unified-memory headroom remains through mixed load and soak, with
  zero swap growth, output corruption, driver allocation failures, or host
  hangs;
- the deployment no longer depends on mutable runtime downloads or
  startup-time patching, and only the authenticated private gateway is exposed.

Until Qwen3.8-27B passes, there is no accepted default primary and the legacy
Qwen3.6 configuration must not be deployed unchanged. Once 27B passes, leave it
resident and route selected evaluation traffic to Flash-Next. If Flash-Next later passes, its broad
capability makes it a better candidate to back several existing semantic
aliases than to add another model-specific alias, consistent with
[ADR-004](../adr/004-model-aliases.md).

## Primary and supplied sources

- [Qwen Qwen3.8-Flash-Next model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next)
- [Qwen Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [NVIDIA Qwen3.8-27B-NVFP4 model card](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4)
- [NVIDIA Qwen3.6-35B-A3B-NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4)
- [Qwen Qwen3.8-Flash-Next source repository](https://github.com/QwenLM/Qwen3.8-Flash-Next)
- [NVIDIA Qwen3.8-Flash-Next-NVFP4 model card](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4)
- [NVIDIA DGX Spark hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [NVIDIA DGX Spark vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)
- [vLLM upstream source and issue tracker](https://github.com/vllm-project/vllm)
- [MiaAI single-Spark recipe](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark)
- [Official-NVFP4 single-Spark source, launchers, and raw results](https://github.com/tonyd2wild/Qwen3.8-Flash-Next-NVFP4-DGX-Spark/tree/main/single-spark-vllm-tp1)
- [Supplied MiaAI NVIDIA forum thread](https://forums.developer.nvidia.com/t/miaai-lab-new-qwen3-8-flash-next-nvfp4-recipe-for-1x-dgx-spark-1m-context-vision-video-37-tok-s-c1/382446)
- [Supplied official-NVFP4 NVIDIA forum thread](https://forums.developer.nvidia.com/t/qwen3-8-flash-next-on-1-2-and-4-dgx-sparks-with-nvidias-official-nvfp4-quant-64-tok-s-peak-single-stream/382476)
- [Supplied SparkRun packaging repository](https://github.com/molo-molo/qwen3.8-flash-next-single-sparkrun)
