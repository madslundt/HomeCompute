# GPT-OSS replacement assessment for Home Spark

Current disposition is maintained in the
[2026-09-04 full shortlist refresh](text-model-shortlist-refresh-2026-09-04.md).
Where this focused comparison names older controls, the full refresh wins.

Verified: 2026-09-04

Status: primary-source reassessment; final promotion still requires the
repository's Danish, Codex/Responses, tool-call, latency, memory, and mixed-load
benchmarks on the target DGX Spark

## Decision

Do **not** make either GPT-OSS model part of the default Home Spark deployment.

- Remove `openai/gpt-oss-20b` from the planned install set. It no longer has a
  distinct role: newer efficient models are at least as easy to fit, and the
  publisher comparison on the GLM-4.7-Flash card shows GPT-OSS-20B behind that
  30B/3B-active model on GPQA, HLE, SWE-bench Verified, tau2-Bench, and
  BrowseComp. GPT-OSS-20B wins only narrowly on AIME 2025 in that table.
  [GLM-4.7-Flash card](https://huggingface.co/zai-org/GLM-4.7-Flash#performances-on-benchmarks)
- Demote `openai/gpt-oss-120b` from a planned high-capacity model to an
  optional, one-time control. Keep no resident copy unless it wins a defined
  workload. Its native MXFP4 checkpoint, Apache-2.0 license, 5.1B active
  parameters, and exact NVIDIA Spark runtime support are still useful controls,
  but its approximately 80 GB fit, 128K context, mostly-English text-only
  training, and mandatory Harmony format no longer justify a permanent role by
  themselves. [OpenAI release](https://openai.com/index/introducing-gpt-oss/),
  [OpenAI model card](https://huggingface.co/openai/gpt-oss-120b),
  [NVIDIA Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix)
- Replace the *role*, not merely the checkpoint, with a small portfolio:
  `nvidia/Qwen3.6-35B-A3B-NVFP4` for the first known-good GB10 deployment,
  `Qwen/Qwen3.8-27B-FP8` as the leading general/coding quality challenger,
  `Qwen/Qwen3-Coder-Next-FP8` for coding, and
  `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` plus its DSpark draft
  for a GB10-specific latency/agent experiment.

The current evidence is strong enough to remove GPT-OSS from the default plan,
but not strong enough to declare one unmeasured replacement the production
winner. Publisher benchmarks use different harnesses and inference settings;
the repository scorecards remain the deciding evidence.

## Naming correction

`Qwen3.8` and `Qwen3.8-Flash-Next` are now real official identities. They are
not the older `Qwen3-Next` family:

- `Qwen/Qwen3.8-27B` is the deployable dense Qwen3.8 open model. It is 27B
  parameters, has native vision/video input, MTP, tool-oriented serving, 262K
  native context extendable to 1M, and Apache-2.0 licensing.
  [Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- `Qwen/Qwen3.8-Flash-Next` is a Qwen4-architecture preview with a 125B MoE
  main model, 6B active parameters, another 51B parameters in n-gram embeddings,
  and a 4B MTP component. It has 262K native context and is extensible to 1M.
  Its license is Qwen Community License 1.0, not Apache-2.0.
  [Qwen model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next),
  [Qwen license](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE)
- Qwen calls the hosted, production-featured service `Qwen3.8-Flash`; the
  open-weight artifact is `Qwen3.8-Flash-Next`. “Flash Next” therefore does not
  mean a smaller successor to Qwen3.8-27B.
  [Qwen Flash-Next card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#qwen38-flash-next)

“HB10” does not appear to be the NVIDIA part used by the project. The Home
Spark target is the DGX Spark's **GB10** with 128 GB unified memory. NVIDIA's
Spark playbooks consistently use DGX Spark/GB10.
[NVIDIA DGX Spark hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html),
[NVIDIA Spark vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)

## Candidate comparison

Memory figures below are checkpoint or publisher fit claims, not an operational
capacity guarantee. The 128 GB pool is shared by weights, runtime workspaces,
KV cache, containers, the operating system, and display. Context and concurrency
must therefore be qualified on the actual machine.

| Candidate | Architecture and context | Precision / approximate fit evidence | License | Strength and GB10 disposition |
| --- | --- | --- | --- | --- |
| `openai/gpt-oss-20b` | 21B total / 3.6B active MoE; 128K; text only | Native MXFP4; OpenAI says it runs within 16 GB; NVIDIA lists it for Spark vLLM | Apache-2.0 | Drop. Compact and well supported, but mostly-English training, Harmony coupling, and newer small-model competition leave no distinct project role. |
| `openai/gpt-oss-120b` | 117B / 5.1B active MoE; 128K; text only | Native MXFP4; OpenAI says one 80 GB accelerator; NVIDIA lists it for Spark vLLM and TensorRT-LLM | Apache-2.0 | Optional control only. It remains the cleanest large native-MXFP4/Harmony comparison, but consumes far more unified-memory headroom than the 20-30 GB-class candidates. |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | 35B / 3B active hybrid-attention MoE; 262K; text/image/video | NVIDIA ModelOpt NVFP4; about 3.06x less disk/GPU memory than BF16; NVIDIA and Ollama describe the Spark build as roughly 22 GB | Apache-2.0 | Keep as first boot/smoke-test model. It has an exact one-Spark recipe with FP8 KV, MTP, and Qwen parsers. This is the strongest operational evidence, not proof it has the best task quality. |
| `Qwen/Qwen3.8-27B-FP8` | Dense 27B; 262K native, extendable to 1M; vision/video, MTP and adaptive thinking | Official block FP8. The vLLM recipe records about 28.6 GiB total weights in a two-GPU run; a community NVFP4 build is about 24.6 GiB, but is not a Qwen/NVIDIA artifact | Apache-2.0 | Promote to mandatory general/coding challenger. It is much newer and the publisher reports large gains over Qwen3.6-27B, but there is no NVIDIA one-Spark recipe or first-party NVFP4 checkpoint yet. Benchmark the official FP8 artifact first. |
| `Qwen/Qwen3.8-Flash-Next-FP8` | 125B / 6B active MoE + 51B n-gram embeddings + 4B MTP; 262K to 1M; multimodal | Official FP8 is 172.78 GiB; BF16 is 335.28 GiB. vLLM's minimum validated FP8 deployment is two GB300 GPUs | Qwen Community 1.0 | Do not install on one Spark. Low active compute does not offset total resident state. The official checkpoint exceeds 128 GB before runtime/KV overhead; current NVFP4 attempts are third-party and can still exceed Spark memory because the large embedding table remains high precision. |
| `Qwen/Qwen3-Coder-Next-FP8` | 80B / 3B active hybrid MoE; 262K; non-thinking coding agent | Official fine-grained FP8; publisher warns to reduce context to 32K if serving does not start | Apache-2.0 | Keep as specialized coding challenger. Excellent role alignment and low active compute, but no exact NVIDIA one-Spark recipe and the card's published quality results used BF16, not the FP8 artifact. |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | 30B / 3B active Mamba-2/MoE/attention hybrid; up to 1M | NVIDIA NVFP4, 21.6 GB artifact; exact one-Spark vLLM recipe and 967M DSpark speculative draft | OpenMDW-1.1 | Keep as the most concrete GB10 performance challenger. It targets autonomous agents and local inference, but Danish is absent from its six listed post-training languages and the license needs project review. |
| `zai-org/GLM-4.7-Flash` | 30B / 3B active MoE; 200K | Publisher BF16 artifact is about 62.5 GB; vLLM/SGLang support currently calls for recent main branches; no exact Spark matrix entry | MIT | Add only if benchmark capacity allows. Its publisher comparison strongly defeats GPT-OSS-20B on most reported agent/reasoning tasks, but its documented languages are English and Chinese and its GB10 path is less mature. |
| `nvidia/Gemma-4-31B-IT-NVFP4` | Dense 30.7B; 256K; 140+ languages; multimodal | NVIDIA ModelOpt NVFP4, Blackwell/vLLM, and listed in the Spark vLLM matrix | Apache-2.0 | Keep as a dense, multilingual quality control. It is the best check against sparse-MoE weaknesses, but dense execution may cost latency and concurrency. |
| `mistralai/Devstral-Small-2-24B-Instruct-2512` | Dense 24B; 262K; coding/tool model | Official FP8 is roughly 26 GB; publisher recommends vLLM but its example uses two GPUs; no exact Spark recipe | Apache-2.0 | Keep only as a low-memory coding baseline. Qwen3.8-27B covers a broader role, while Coder-Next is more directly optimized for long-horizon coding. |

Sources for the table: [OpenAI GPT-OSS architecture and fit](https://openai.com/index/introducing-gpt-oss/),
[NVIDIA Qwen3.6 card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[vLLM Qwen3.8-27B recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B),
[vLLM Flash-Next recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next),
[Qwen Coder-Next FP8 card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8),
[NVIDIA Nemotron 3.5 Lightning card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4),
[GLM-4.7-Flash card](https://huggingface.co/zai-org/GLM-4.7-Flash),
[NVIDIA Gemma 4 card](https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4), and
[Devstral Small 2 card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512).

## What the quality evidence actually says

### GPT-OSS-20B has been overtaken in its own efficiency tier

The cleanest direct publisher comparison found is GLM-4.7-Flash versus
GPT-OSS-20B. GLM reports 75.2 versus 71.5 GPQA, 14.4 versus 10.9 HLE, 59.2
versus 34.0 SWE-bench Verified, 79.5 versus 47.7 tau2-Bench, and 42.8 versus
28.3 BrowseComp. GPT-OSS-20B scores 91.7 versus 91.6 on AIME 2025. These are
publisher results and still need local replication, but they remove the case
for treating GPT-OSS-20B as a necessary small-model control.
[GLM-4.7-Flash evaluation table and settings](https://huggingface.co/zai-org/GLM-4.7-Flash#performances-on-benchmarks)

### Qwen3.8-27B is the practical new-generation quality candidate

Within Qwen's own evaluation, Qwen3.8-27B improves over Qwen3.6-27B from 63.4
to 73.0 on Terminal-Bench 2.1, 53.5 to 61.7 on SWE-bench Pro, 13.3 to 42.2 on
DeepSWE 1.1, 61.0 to 70.7 on CoWorkBench, 69.1 to 79.5 on IFBench, and 24.0
to 30.8 on HLE. The same card reports 89.2 GPQA Diamond. This evidence is from
one publisher and cannot be numerically merged with OpenAI's 2025 GPT-OSS
table, but it supports making 27B the principal newer-model challenger.
[Qwen3.8-27B evaluation table](https://huggingface.co/Qwen/Qwen3.8-27B#benchmark-results)

### Flash-Next looks stronger, but it is the wrong size for one Spark

Qwen reports Flash-Next ahead of its 27B model on DeepSWE 1.1 (58.7 versus
42.2), SWE-bench Pro (62.5 versus 61.7), SWE-bench Multilingual (81.0 versus
73.8), CoWorkBench (73.9 versus 70.7), JobBench (55.7 versus 33.4), and
Toolathlon Verified (73.5 versus 67.1). That makes it a credible future model
for larger hardware, not evidence that it belongs on this 128 GB appliance.
[Qwen Flash-Next evaluation table](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#benchmark-results),
[vLLM checkpoint sizes and validated hardware](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next)

### GPT-OSS-120B still has one useful purpose

OpenAI trained GPT-OSS for native function calling, browsing, Python use,
structured outputs, adjustable reasoning effort, and the Harmony response
format. All released evaluations used the same MXFP4 quantization shipped in
the checkpoint. That makes 120B a valuable protocol and quantization control
if HomeCompute specifically wants to test Harmony/OpenAI-style behavior. It
does not make GPT-OSS necessary for a Qwen-parser/vLLM architecture, and the
mandatory Harmony formatting is extra integration surface rather than a
benefit for the project's stable model aliases.
[OpenAI GPT-OSS model card](https://huggingface.co/openai/gpt-oss-120b#highlights),
[OpenAI Harmony requirement](https://huggingface.co/openai/gpt-oss-120b#inference-examples)

## GB10 support is a separate axis from model quality

NVIDIA's current Spark vLLM matrix explicitly lists GPT-OSS-20B/120B,
Gemma-4-31B NVFP4, Nemotron-3.5 Lightning, and several older Qwen3 NVFP4
checkpoints. Qwen3.8-27B and Flash-Next are not currently in that Spark matrix.
NVIDIA does, however, publish a dedicated one-Spark Qwen3.6 recipe, while the
Nemotron 3.5 card publishes an even more specific low-concurrency DSpark recipe.
[NVIDIA Spark matrix and Qwen3.6 recipe](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md),
[Nemotron DGX Spark recipe](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#quick-start)

This means the first model to install and the likely quality winner should be
different candidates:

1. Start with NVIDIA Qwen3.6 NVFP4 to validate the appliance and runtime.
2. Qualify official Qwen3.8-27B FP8 next at 32K, then 64K/128K contexts.
3. Compare Nemotron 3.5 Lightning with DSpark on/off for latency and agent
   completion rate.
4. Run Coder-Next FP8 only on the coding scorecard.
5. Run GPT-OSS-120B once only if a large-model/Harmony control is worth the
   download and approximately 80 GB residency. Delete or archive it if it does
   not win a predeclared workload.

Do not use a third-party `NVFP4`, `GGUF`, or pruned Flash-Next artifact to turn
an oversized model into the default. That changes the artifact under test and
adds quantization/provenance risk. A 4-bit label also does not prove the exact
native GB10 FP4 compute path; record the selected kernel and backend.

## Proposed acceptance decision

Promote a model only after it passes the same redacted fixtures with a pinned
tuple `(DGX OS, driver, container digest, runtime version, model revision,
precision, context, parser, template, launch flags)`.

The minimum comparison should measure:

- Danish and English instruction following, summarization factuality, and
  strict JSON Schema output;
- multi-turn tool correctness, large tool surfaces, invalid tool recovery, and
  prompt-injection resistance;
- Codex Responses streaming, terminal events, repository edit/build/test
  success, and cloud-review acceptance;
- p50/p95 time to first token, decode rate, end-to-end task time, model load
  time, peak unified memory, KV-cache headroom, concurrency degradation, and
  OOM recovery;
- MTP/DSpark enabled and disabled, recording accepted draft tokens rather than
  assuming speculative decoding helps.

The default-plan change does not need to wait for those measurements:
**GPT-OSS is no longer required.** Keep 120B only as an explicit control with a
sunset rule; remove 20B; do not substitute Flash-Next on one Spark; make
Qwen3.8-27B FP8 the main quality challenger while retaining Qwen3.6 NVFP4 as
the deployment baseline.
