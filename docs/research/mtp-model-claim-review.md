# MTP and quoted GB10 model-claim review

Verified: 2026-08-26

Status: primary-source correction implemented in the canonical shortlist and
Phase C scaffold; quoted throughput remains anecdotal until reproduced on the
target GX10/GB10

## Bottom line

The quoted post does **not** justify replacing the repository's current first
model, `nvidia/Qwen3.6-35B-A3B-NVFP4`. MTP remains a valid experimental
performance variable, but it is no longer the safe default. NVIDIA's August 4,
2026 NemoClaw release stopped enabling MTP by default for this exact managed
single-Spark profile and warns that lower context, concurrency, and batch limits
do not guarantee protection from a host freeze. The scaffold therefore defaults
to MTP off and accepts only the exact prior MTP tuple as an explicit experiment.
[NVIDIA NemoClaw release note](https://docs.nvidia.com/nemoclaw/user-guide/deepagents/release-notes/2026/8/4)

The model names in the post need qualification. `google/gemma-4-31B-it`,
`Qwen/Qwen3.6-27B`, and `Qwen/Qwen3.6-35B-A3B` are official models. Gemma 4
31B is dense, not MoE. No official Google source found in this review announces
a Gemma 4 124B MoE; Google's current family list contains E2B, E4B, 12B, 26B
A4B, and 31B. Treat 124B as an unconfirmed rumor, not a shortlist entry or a
reason to buy hardware.

The phrase "MTP version" is also potentially misleading. Qwen's official
Qwen3.6 checkpoints already contain native MTP weights. With GGUF, a converter
may keep those weights with the target, omit them, or export them as a separate
draft file, so community repositories often label one package "MTP." That is a
packaging/runtime difference, not a better Qwen target-model revision.

## Claim disposition

| Quoted claim or name | Primary-source finding | Repository consequence |
| --- | --- | --- |
| Gemma 4 31B | Official `google/gemma-4-31B-it`; Google lists it as a 30.7B dense model with 256K context, text/image input, function calling, coding, and multilingual support. NVIDIA's current Spark vLLM matrix lists both the Google model and `nvidia/Gemma-4-31B-IT-NVFP4`. [Google model card](https://huggingface.co/google/gemma-4-31B-it), [NVIDIA Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) | Add the NVIDIA NVFP4 artifact as a dense general/coding challenger, but do not displace Qwen solely on the quoted user's experience. |
| Rumored Gemma 4 124B MoE | Not an announced Google model as of this verification. Google's official list has only one Gemma 4 MoE, 26B A4B; the 31B is dense. [Google model card and family table](https://huggingface.co/google/gemma-4-31B-it#models-overview) | Do not add it. Revisit only after an official model card, weights, license, and runtime recipe exist. |
| Qwen3.6 27B | Official dense vision-language model with 262,144 native context. Its config includes one MTP hidden layer. [Qwen model card](https://huggingface.co/Qwen/Qwen3.6-27B), [official config](https://huggingface.co/Qwen/Qwen3.6-27B/raw/main/config.json) | Do not replace the already-shortlisted `Qwen/Qwen3.8-27B`: Qwen describes 3.8 as its newer, more capable dense generation and reports gains over 3.6. Qwen3.8 is also trained for MTP. [Qwen3.8 model card](https://huggingface.co/Qwen/Qwen3.8-27B) |
| Qwen3.6 35B-A3B | Official 35B-total/3B-active MoE vision-language model. Its config also includes one MTP hidden layer. [Qwen model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B), [official config](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/raw/main/config.json) | Keep NVIDIA's Spark-ready NVFP4 derivative as the first install. |
| "MTP version" is much faster | Plausible, but not a portable performance number. Qwen documents vLLM MTP commands; NVIDIA's exact Spark recipe uses three speculative tokens. llama.cpp supports `--spec-type draft-mtp`, and its converter can include, exclude, or separately export MTP tensors. [NVIDIA Qwen3.6 NVFP4 card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4#usage), [llama.cpp speculative-decoding docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md), [llama.cpp conversion options](https://github.com/ggml-org/llama.cpp/blob/master/convert_hf_to_gguf.py) | Add MTP-on versus MTP-off measurements to the exact-runtime benchmark. Do not copy 15–25 tokens/s into requirements. |
| Q8 is much better quality than Q4 with only a modest speed loss | This is one user's result for unspecified GGUF revisions and prompts. MTP controls decoding; Q4/Q8 controls weight approximation. They are independent variables. The repository's first artifact is NVIDIA ModelOpt NVFP4, not llama.cpp Q4/Q8 GGUF. NVIDIA reports its own BF16-versus-NVFP4 accuracy table for that artifact. [NVIDIA model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4#evaluation) | Keep quantization as a benchmarked artifact property. Do not infer that Q8 GGUF should replace the documented NVFP4 baseline. |
| GB10 has 273 GB/s memory bandwidth, not 1,000 GB/s | NVIDIA's product specification and user guide both say 273 GB/s. NVIDIA's launch claim was up to 1,000 **trillion operations per second** of FP4 AI compute, not 1,000 GB/s of memory bandwidth. This review found no first-party GB10 memory-bandwidth claim of 1,000 GB/s. [NVIDIA product specification](https://www.nvidia.com/en-us/products/workstations/dgx-spark/), [NVIDIA hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html), [NVIDIA launch announcement](https://nvidianews.nvidia.com/news/nvidia-announces-dgx-spark-and-dgx-station-personal-ai-computers) | Keep 273 GB/s as the planning value. Describe the quote's "false marketing claim" as unsubstantiated, likely a confusion with the 1,000-TOPS claim. |

## What MTP changes

MTP is speculative decoding. A fast predictor proposes more than one future
token and the target model verifies proposals in parallel. It targets decode
latency; it does not change the identity or intended quality of the target
model. Google describes Gemma 4 MTP as preserving the same target quality and
publishes a separate assistant checkpoint, such as
`google/gemma-4-31B-it-assistant`, for the 31B target. [Google Gemma 4 MTP
assistant card](https://huggingface.co/google/gemma-4-31B-it-assistant)

Qwen packages MTP differently. Both official Qwen3.6 configs contain
`mtp_num_hidden_layers: 1`, and Qwen's serving instructions enable the native
head through vLLM speculative configuration. There is no separate official
"Qwen3.6-35B-A3B-MTP" target model. In llama.cpp, `draft-mtp` uses MTP heads
from the main model. The converter's `--mtp` option can instead export only the
MTP head, while `--no-mtp` excludes it from the target GGUF. This explains why
GGUF publishers and users may talk about an "MTP version."

vLLM supports native MTP and Gemma 4 assistant checkpoints. Its current guide
uses `method: "mtp"`; for Gemma 4 it explicitly warns that older versions may
mistreat the assistant as a generic draft model. [vLLM MTP
guide](https://docs.vllm.ai/en/latest/features/speculative_decoding/mtp/)
This makes the runtime version part of the artifact tuple.

For the repository's selected NVIDIA Qwen artifact, NVIDIA's vLLM playbook has
documented this experimental single-Spark configuration:

```text
--speculative-config '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}'
```

The repository retains this configuration as an explicit, pinned experimental
input alongside the surrounding NVIDIA settings—FlashInfer attention, Marlin
MoE, async scheduling, prefix caching, and parsers—but leaves it disabled by
default. It must be qualified on/off on the appliance. Record accepted draft
tokens, output tokens/s, TTFT, end-to-end agent task time, peak unified memory,
host stability, recovery, and regressions under long context, vision, tools,
and concurrency. A faster token stream can still lose on a coding task if it
causes more retries or if the runtime path is unstable.

## Recommended documentation changes

1. Keep `nvidia/Qwen3.6-35B-A3B-NVFP4` as priority 1.
2. Keep MTP off by default. Retain the exact vLLM speculative configuration as
   a separate experimental tuple; never promote it without the MTP-on/off,
   24-hour mixed-load soak, host-stability, and recovery gates. **Implemented
   in the scaffold.**
3. Add `nvidia/Gemma-4-31B-IT-NVFP4` as a dense general/coding challenger after
   the efficient Qwen baseline. Evaluate its official MTP assistant path only
   with a vLLM version that recognizes Gemma 4 MTP. **Implemented in the
   canonical shortlists.**
4. Keep `Qwen/Qwen3.8-27B-FP8` ahead of Qwen3.6-27B as the dense Qwen
   challenger. Ensure its benchmark tuple also records whether native MTP is
   enabled.
5. Do not add a Gemma 4 124B MoE unless Google releases an official artifact.
6. Keep 273 GB/s as the GB10 memory-bandwidth constraint and do not repeat the
   alleged 1,000-GB/s marketing claim.

These changes affect runtime qualification and add one credible challenger;
they do not warrant replacing the staged shortlist wholesale.
