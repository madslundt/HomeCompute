# Home Spark text-model shortlist refresh

Verified: 2026-09-04

Status: primary-source audit with a separate community-evidence review.
Publisher benchmarks nominate candidates; only the repository's pinned Danish,
tool, Codex/Responses, latency, memory, and mixed-load tests can promote one to
production.

## Decision

The planned evaluation set is too large and contains several candidates whose
role has been overtaken. Reduce the mandatory first wave to three models:

1. Keep `nvidia/Qwen3.6-35B-A3B-NVFP4` as the known-good **integration and
   single-Spark baseline**, not the presumed quality winner.
2. Promote `Qwen/Qwen3.8-27B-FP8` to the first **general, Danish, coding, and
   agent quality candidate**.
3. Keep `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` with its DSpark
   draft as the **single-Spark latency and speculative-decoding candidate**.

Add `ornith-ai/Ornith-1.5-35B-A3B` only if Qwen3.8 leaves a coding/agent gap,
and add `nvidia/Muse-Glimmer-30B-NVFP4` only if a dense or multimodal control is
needed. Keep `nvidia/Gemma-4-31B-IT-NVFP4` only as the multilingual/Danish
control. Demote `Qwen/Qwen3-Coder-Next-FP8` to an optional coding control.
Remove Devstral Small 2, GLM-4.7-Flash, GPT-OSS-20B, and GPT-OSS-120B from the
normal install queue. Mistral Small 4 is the successor worth watching instead
of Devstral, but it should not enter the first test wave.

This refresh does not reverse the separate GPT-OSS conclusion in
[`gpt-oss-replacement-assessment.md`](gpt-oss-replacement-assessment.md): 20B
has no remaining role, and 120B is at most a one-time Harmony/MXFP4 control.

## Revised disposition of every planned candidate

| Candidate | Disposition | Primary-source reason |
| --- | --- | --- |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | **Keep, priority 1 for bring-up** | NVIDIA supplies a 23.4 GB artifact and an exact DGX Spark command with ModelOpt, Marlin MoE, FP8 KV cache, Qwen parsers, and optional MTP. Its 35B/3B-active design and 262K context are attractive, but its publisher quality is behind newer models. [NVIDIA card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4), [official artifact API](https://huggingface.co/api/models/nvidia/Qwen3.6-35B-A3B-NVFP4?blobs=true) |
| `Qwen/Qwen3.8-27B-FP8` | **Promote to first quality candidate** | Official FP8 is 30.9 GB, Apache-2.0, dense 27B, native multimodal, 262K context, adaptive reasoning, MTP, and current vLLM/SGLang support. Qwen reports 73.0 Terminal-Bench 2.1, 61.7 SWE-bench Pro, 42.2 DeepSWE 1.1, 79.5 IFBench, and 89.2 GPQA. It has no NVIDIA one-Spark recipe or first-party NVFP4 artifact, so start at 32K and qualify the exact FP8 tuple. [Qwen card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8), [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B), [artifact API](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?blobs=true) |
| `Qwen/Qwen3-Coder-Next-FP8` | **Demote to optional coding control** | It remains a relevant 80B/3B-active, 262K coding-agent checkpoint, but its official FP8 weights are 80.4 GB, its displayed publisher evaluations were run with BF16, the published serving example uses four GPUs, and there is still no first-party Spark path. The newer Qwen3.8-27B is smaller and publishes much stronger current coding/agent evidence. Keep Coder-Next only if a coding-specialization A/B is worth the download. [Qwen card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8), [artifact API](https://huggingface.co/api/models/Qwen/Qwen3-Coder-Next-FP8?blobs=true) |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | **Keep, priority performance candidate** | NVIDIA supplies a 21.6 GB target, exact one-GB10 recipe, 967M DSpark draft, parsers, and target/MTP/DSpark paths. Its official NVFP4 results include 52.8 SWE-bench Verified, 23.46 Terminal-Bench 2.1, and 72.88 IFBench. On GB10 it is W4A16 through Marlin, not a native W4A4 Tensor Core path. Danish appears in pretraining data but not the primary post-training languages, and OpenMDW-1.1 needs license approval. [NVIDIA card and Spark recipe](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4), [DSpark draft](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) |
| `nvidia/Gemma-4-31B-IT-NVFP4` | **Demote to multilingual control** | This remains the clearest 140+-language dense control and NVIDIA's Spark matrix lists it. The 32.6 GB artifact preserves Google/NVIDIA's reported BF16 quality closely. However, the card's launch example is eight-way tensor parallel, not a one-Spark command, and dense 30.7B execution duplicates the role now covered by Qwen3.8 and Muse. Run it only for Danish/multilingual or dense-consistency evidence. [NVIDIA card](https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4), [Spark matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) |
| `zai-org/GLM-4.7-Flash` | **Remove from the normal queue** | Its 30B/3B-active design and strong publisher comparisons originally made it a useful GPT-OSS-20B challenger, but the official artifact is 62.5 GB BF16, requires recent runtime branches, has no exact Spark recipe, and documents English/Chinese rather than Danish. The newly released GLM-5.3-Flash does not rescue this slot: its official weights are 328.3 GB. [GLM-4.7-Flash card](https://huggingface.co/zai-org/GLM-4.7-Flash), [GLM-5.3-Flash card](https://huggingface.co/zai-org/GLM-5.3-Flash), [GLM-5.3 artifact API](https://huggingface.co/api/models/zai-org/GLM-5.3-Flash?blobs=true) |
| `mistralai/Devstral-Small-2-24B-Instruct-2512` | **Remove; superseded as a candidate** | Devstral is a 51.6 GB BF16 artifact despite the 24B label, has no first-party Spark/NVFP4 path, and overlaps the stronger current coding candidates. Mistral explicitly says Mistral Small 4 unifies Instruct, Reasoning, and Devstral; that is the Mistral family checkpoint to watch. [Devstral card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512), [Devstral artifact API](https://huggingface.co/api/models/mistralai/Devstral-Small-2-24B-Instruct-2512?blobs=true), [Mistral Small 4 card](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603-NVFP4) |
| `openai/gpt-oss-20b` | **Remove** | No distinct role remains after Qwen3.8, Nemotron, and other efficient agent models. Harmony is extra integration surface. See the dedicated assessment. [OpenAI card](https://huggingface.co/openai/gpt-oss-20b) |
| `openai/gpt-oss-120b` | **Remove from normal queue; optional one-time control** | Native MXFP4 and exact Spark support remain useful for a Harmony/protocol control, but about 80 GB of weights and model-specific formatting are not justified without a predeclared workload win. [OpenAI card](https://huggingface.co/openai/gpt-oss-120b), [Spark matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) |
| `Qwen/Qwen3.8-Flash-Next[-FP8]` | **Exclude from production; lab-only community experiment** | Official FP8 is about 172.8 GiB. NVIDIA released an official mixed NVFP4/FP8 derivative on 2026-09-03, but its files total 132.7 GB: already larger than the appliance's nominal 128 GB before runtime, KV cache, OS/display, and recovery headroom. There is no first-party one-Spark launch recipe. A community conversion has demonstrated one-Spark execution, but it is a patched, non-publisher stack that misses this repository's production headroom rule. [Qwen card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8), [NVIDIA NVFP4 card](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4), [community experiment](https://github.com/albond/SingleSpark-Qwen3.8-Flash-Next) |

## New candidates that change the queue

### Keep Ornith 1.5 as a conditional measured challenger

`ornith-ai/Ornith-1.5-35B-A3B` is a 35B/3B-active, MIT-licensed reasoning
model with 262K native context, tools, and publisher-supported vLLM 0.19.1+
and SGLang 0.5.9+. Ornith reports that it beats Qwen3.6-35B across its coding
and agent suite, and its card documents the harnesses and anti-hacking setup.
That makes it a stronger candidate than continuing to spend a core test slot
on Coder-Next or Devstral.

The qualification caveat is substantial: the official checkpoint is 71.9 GB
BF16, its reference server uses two 80 GB GPUs for 256K headroom, it requires
`trust_remote_code`, and neither Ornith nor NVIDIA publishes a one-GB10 recipe
or official low-precision derivative. It may nominally fit one Spark at a
reduced context, but that is a hypothesis to test, not support evidence.
[Official card](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B),
[official artifact API](https://huggingface.co/api/models/ornith-ai/Ornith-1.5-35B-A3B?blobs=true)

### Keep Muse Glimmer as a conditional dense Spark agent control

`nvidia/Muse-Glimmer-30B-NVFP4` is a 24.7 GB mixed W4A16-NVFP4/FP8 artifact,
Apache-2.0, 131K, multimodal, and explicitly lists DGX Spark. NVIDIA tested it
with vLLM 0.28.0 and publishes its exact parser/template command. Its measured
NVFP4 results include 47.05 Terminal-Bench 2.1, 49.70 SciCode, 78.74 IFBench,
and 75.56 AA-LCR. This is better operational evidence for a dense agent control
than Devstral, though the card supplies no Danish-language claim.
[NVIDIA card](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4),
[official artifact API](https://huggingface.co/api/models/nvidia/Muse-Glimmer-30B-NVFP4?blobs=true)

### Watch Mistral Small 4, but do not expand the first wave

`mistralai/Mistral-Small-4-119B-2603-NVFP4` is the meaningful Mistral update:
119B/6.5B-active, 256K, multimodal, Apache-2.0, reasoning control, tools/JSON,
and explicit unification of the former Devstral role. The official NVFP4 files
total 70.8 GB, so reduced-context single-Spark experimentation is plausible.
However, Mistral's published command uses tensor parallelism across two GPUs,
SGLang support is marked work in progress, the stated language list omits
Danish, and it has no exact Spark recipe. Keep it on the watchlist and only add
it after the three-model mandatory wave if Mistral-specific or broad multilingual evidence
is still missing.
[Mistral card](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603-NVFP4),
[official artifact API](https://huggingface.co/api/models/mistralai/Mistral-Small-4-119B-2603-NVFP4?blobs=true)

### Treat K2 Horizon and Granite 4.2 as watchlist signals

`IFM/K2-Horizon-MoVA-36B-A4B` is a very recent Apache-2.0 36B/4B-active model
with 512K native context and strong publisher agent/coding comparisons. Its
official BF16 weights total 74.9 GB, it requires custom code and model-specific
router/parser handling, and its validated SGLang recipe uses two H200s. No
official Spark or quantized artifact was found. It is a promising later
challenger, not a bring-up candidate.
[IFM card](https://huggingface.co/IFM/K2-Horizon-MoVA-36B-A4B),
[vLLM recipe](https://recipes.vllm.ai/IFM),
[artifact API](https://huggingface.co/api/models/IFM/K2-Horizon-MoVA-36B-A4B?blobs=true)

IBM's `granite-4.2-30b-nvfp4` is an unusually compact official 17.7 GB,
Apache-2.0 dense artifact with reasoning, tools, 128K native context, and 12
tested languages. IBM's own results—29.24 Terminal-Bench 2.1, 33.29 SWE-bench
Pro, 57 SWE-bench Verified, 66.41 GPQA—do not justify adding it ahead of the
core candidates, Danish is not tested, and the quantized card supplies no vLLM
or Spark recipe. The 8B/3B variants may be revisited only if the dedicated
Home Assistant latency candidate remains unresolved.
[IBM base card and benchmarks](https://huggingface.co/ibm-granite/granite-4.2-30b),
[IBM NVFP4 artifact](https://huggingface.co/ibm-granite/granite-4.2-30b-nvfp4),
[artifact API](https://huggingface.co/api/models/ibm-granite/granite-4.2-30b-nvfp4?blobs=true)

## Popular frontier releases that do not fit one GB10

Do not add these to the appliance queue simply because their active-parameter
counts are modest:

| Family | Official weight evidence | Decision |
| --- | --- | --- |
| DeepSeek V4 Flash / 0731 | 284–304B total/13B active; publisher DSpark files total 166.9 GB and NVIDIA NVFP4 files total 175.6 GB. | Exclude from one Spark. DSpark accelerates decoding; it does not reduce target weight residency. [DeepSeek card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-DSpark), [NVIDIA card](https://huggingface.co/nvidia/DeepSeek-V4-Flash-0731-NVFP4) |
| GLM-5.3-Flash | 320B/18B active; official BF16 files total 328.3 GB. | Exclude pending a publisher/NVIDIA artifact comfortably below the usable memory envelope. [Z.ai card](https://huggingface.co/zai-org/GLM-5.3-Flash) |
| Kimi K3 | 2.8T, native multimodal, 1M context; official files total about 1.56 TB. | Cloud or multi-node comparison only. [Moonshot card](https://huggingface.co/moonshotai/Kimi-K3), [artifact API](https://huggingface.co/api/models/moonshotai/Kimi-K3?blobs=true) |
| MiniMax M3 / newer MiniMax text models | Current open checkpoints remain hundreds of GB; MiniMax H3 is a separate omni-generative system whose complete workflow also relies on hosted preprocessing. | Not a HomeCompute text-service candidate. [MiniMax models API](https://huggingface.co/api/models?author=MiniMaxAI&sort=lastModified&direction=-1&limit=20&full=false), [MiniMax H3 card](https://huggingface.co/MiniMaxAI/MiniMax-H3) |

## Community and owner evidence

Community measurements are useful operational evidence, but they do not have
the same controls or provenance as publisher artifacts. They are used here to
change test priority, not to declare a production winner.

- A reproducible 12-configuration Qwen3.8-27B study on one DGX Spark measured
  roughly 11--13 tok/s for plain serving and 30--37 tok/s with the fastest
  speculative paths, depending on runtime. Tool-call correctness also varied
  by tuple: the fastest SGLang run initially completed 16/18 calls, while a
  vLLM DFlash run completed 18/18. This supports promoting Qwen3.8, but only as
  an exact runtime/model/decoder tuple. [Test report](https://morethanamachine.com/posts/qwen3-8-27b-dgx-spark/),
  [NVIDIA forum reproduction](https://forums.developer.nvidia.com/t/comprehensive-qwen3-8-27b-study-on-dgx-sparks-quantization-speculative-decoding-and-tp-dp-scaling/381102)
- Another owner test found that Qwen3.8-27B was strong at agentic coding and
  structured output, but weaker on its Japanese knowledge set than Gemma 4.
  Backend choice changed speed by as much as fivefold. That is a useful warning
  against deleting the multilingual control before the Danish fixtures run.
  [DGX Spark comparison](https://qiita.com/nabe2030/items/1d4cbb8b7760f080649f)
- Nemotron 3.5 Lightning reached about 80 tok/s target-only and 116 tok/s with
  DSpark in one owner's single-Spark test. Tools and five coding tasks worked,
  but strict-format reliability remained imperfect and long reasoning often
  needed a much larger output budget. It remains a performance candidate, not
  the presumed unattended-automation winner. [Owner test](https://dev.classmethod.jp/articles/dgx-spark-nemotron-3-5-lightning-first-touch/)
- Ornith 1.5 user evidence is contradictory. A published tool-calling test put
  it among the strongest Qwen3.6-derived 35B models but still below Qwen3.8;
  other users report excessive reasoning, indecision loops, or worse results
  than stock Qwen3.6 on real repositories. This moves Ornith from the mandatory
  wave to a targeted A/B only if Qwen3.8 leaves a coding gap.
  [Tool-calling test](https://www.reddit.com/r/LocalLLaMA/comments/1vyaxip/35ba3b_tool_calling_benchmark_original_qwen_vs/),
  [mixed owner reports](https://www.reddit.com/r/LocalLLaMA/comments/1w3bx1n/does_anyone_have_real_experience_with_ornith159b/),
  [failure report](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/discussions/13)
- A community Qwen3.8 Flash-Next conversion has run on one Spark at a reported
  43.34 tok/s median and 65K context using a 101 GiB checkpoint. The experiment
  required a patched vLLM, about 210 GB of disk, had only 4.41 GiB left for KV
  cache at the attempted full-context setting, and did not test concurrency,
  a filled context window, multimodal use, or long tool chains. It proves
  experimental feasibility, not safe production fit.
  [Reproducible project](https://github.com/albond/SingleSpark-Qwen3.8-Flash-Next),
  [NVIDIA forum discussion](https://forums.developer.nvidia.com/t/fitting-qwen3-8-flash-next-180b-onto-one-dgx-spark-44-tok-s-at-four-bits/382117)
- A small DGX Spark benchmark gave GPT-OSS-20B and several other models perfect
  scores on its narrow prompt set. That is a legitimate counter-signal, but the
  suite was saturated and does not establish a unique production role for
  GPT-OSS. [Benchmark report](https://github.com/Kleybrink/dgx-spark-bench/blob/main/results/20260407_135131/REPORT.md)

## Resulting test order and stop rules

1. Qwen3.6 NVFP4 at 32K: prove GB10, gateway, parser, tools, Responses, and
   recovery.
2. Qwen3.8-27B FP8 at 32K, then 64K/128K: first production-quality attempt.
3. Nemotron 3.5 NVFP4 target-only, MTP, then DSpark: retain only the fastest
   tuple that also passes functional tests.
4. Stop the mandatory wave. Test Ornith 1.5 only if Qwen3.8 leaves a measured
   coding/agent gap; retain it only if the gain outweighs runtime/custom-code
   cost and mixed user evidence.
5. Test Muse Glimmer only if a dense/vision/tool control is still needed.
6. Test Gemma 4 only if the first wave fails Danish/multilingual fixtures.

Stop after a model loses its intended role on task success, safety, or the
memory/latency gate. Do not run every optional control merely because it fits.
For each run pin and record `(DGX OS, driver, container digest, runtime, model
revision, precision, context, parser, template, flags)`. Artifact byte size is
only a lower-bound fit check; the 128 GB pool must also hold runtime workspaces,
KV cache, OS/display, audio services, and recovery headroom.

## Sources and scope note

The candidate and artifact audit uses model-publisher cards and APIs, NVIDIA's
official DGX Spark playbooks/cards, and official vLLM/SGLang recipes.
Cross-publisher benchmark numbers are not treated as directly comparable unless
a card documents a shared harness. The separate community section records
reproducible owner tests and contradictory reports as lower-confidence evidence.
