# Model shortlist omission review

Verified: 2026-08-25

Status: historical 2026-08-25 omission review. Candidate dispositions are
superseded by the
[2026-09-04 shortlist refresh](text-model-shortlist-refresh-2026-09-04.md).

## Bottom line

Two items were already present but were hidden by the phrase "LLMs":
`Qwen/Qwen3-Coder-Next` is the official 80B-total/3B-active coding model already
recommended for `coding`, and `openai/whisper-large-v3-turbo` is already an STT
candidate. Three real omissions should change the shortlist:
the official DGX Spark-ready `nvidia/Qwen3.6-35B-A3B-NVFP4` artifact,
`Qwen/Qwen3.8-27B`, and the Danish
`CoRal-project/roest-v3-chatterbox-500m` TTS challenger.

`Qwen3.8-35B-A3B` is not an official model identity. As of the verification
date, Qwen lists Qwen3.8 open releases at 2.4T-A95B and 27B, while the official
35B-A3B release is Qwen3.6. The most likely intended model is therefore
`Qwen/Qwen3.6-35B-A3B`; `Qwen/Qwen3.5-35B-A3B` is the older model already in the
repository. Sources: [official Qwen release list](https://github.com/QwenLM/Qwen3.8#news)
and [official Qwen model identifiers](https://github.com/QwenLM/Qwen3.8#models).

## Identity and disposition

| User label | Exact official identity | Already documented? | Disposition |
| --- | --- | --- | --- |
| Qwen3 Coder 80B A3B | `Qwen/Qwen3-Coder-Next` | Yes, in the [coding shortlist](coding-model-evaluation.md) and [installation recommendation](llm-installation-recommendation.md) | Current disposition: optional coding control after Qwen3.8 and a conditional Ornith test. “80B/3B active” describes this same artifact, not another omitted model. |
| Qwen3.8 35B A3B | No official artifact under that name; likely `Qwen/Qwen3.6-35B-A3B` | No | Add NVIDIA's `nvidia/Qwen3.6-35B-A3B-NVFP4` as the first efficient shared-text GB10 candidate and retire Qwen3.5 as the default first install. |
| Qwen3.8 27B | `Qwen/Qwen3.8-27B` and publisher FP8 variant `Qwen/Qwen3.8-27B-FP8` | No | Add as a high-priority dense quality challenger for `automation` and `coding`; do not assume it wins the Home Assistant latency role. |
| Whisper large-v3-turbo | `openai/whisper-large-v3-turbo` | Yes, in the [STT shortlist](stt-model-evaluation.md) | Install for the STT benchmark. Its absence from the earlier answer was a scope/wording error, not a model rejection. |
| Røst / Danish Chatterbox | `CoRal-project/roest-v3-chatterbox-500m` | No | Add as the first Danish naturalness challenger to Piper, while Piper remains the integration/latency baseline. |

## Evidence and GB10 consequences

### Qwen3-Coder-Next

The official card says Qwen3-Coder-Next is designed for coding agents and local
development, has 80B total parameters with 3B activated, a 256K context, tool
calling, vLLM/SGLang serving instructions, and Apache-2.0 licensing. The
publisher itself advises reducing context to 32,768 if the server cannot start.
That is exactly the model the existing documents call `Qwen3-Coder-Next`; it
was not excluded. Sources: [Qwen3-Coder-Next model card](https://huggingface.co/Qwen/Qwen3-Coder-Next#highlights),
[deployment guidance](https://huggingface.co/Qwen/Qwen3-Coder-Next#vllm), and
[license metadata](https://huggingface.co/Qwen/Qwen3-Coder-Next).

There is no current first-party NVIDIA DGX Spark recipe for Coder-Next in the
Spark vLLM playbook. The official Qwen FP8 checkpoint is therefore a benchmark
candidate, not proof of GB10 mixed-load headroom. Do not substitute an
unqualified community “GB10” quantization for a pinned publisher artifact.

### Qwen3.6-35B-A3B, not Qwen3.8-35B-A3B

Qwen3.6 is 35B total/3B active, Apache-2.0, has 262K native context, and its
official card documents vLLM tool serving. It reports material agentic-coding
gains over Qwen3.5-35B-A3B on several publisher tests, although results are
mixed and do not replace this repository's workload benchmark. Sources:
[Qwen3.6 model overview and evaluations](https://huggingface.co/Qwen/Qwen3.6-35B-A3B#model-overview)
and [vLLM tool-serving command](https://huggingface.co/Qwen/Qwen3.6-35B-A3B#vllm).

More importantly, NVIDIA publishes `nvidia/Qwen3.6-35B-A3B-NVFP4`, labels it
Apache-2.0, supports vLLM on Blackwell, and gives an explicit single-DGX-Spark
launch command with FP8 KV cache, tool and reasoning parsers, 262K context, and
tensor parallel size 1. NVIDIA reports approximately a 3.06x reduction in disk
and GPU-memory requirements versus BF16, with small changes on its listed
accuracy evaluations. This is much stronger GB10 evidence than the older
Qwen3.5 choice. Sources: [NVIDIA model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4)
and [NVIDIA DGX Spark vLLM recipe](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#run-agent-ready-qwen36-35b-model-with-vllm).

Recommendation change: make the NVIDIA Qwen3.6 NVFP4 artifact the first
efficient `automation`/`home` shared-text install and also exercise it
in the code suite. Retain Qwen3.5 only as a comparison if Danish regression
testing shows a reason. Neither Qwen3.6's card nor NVIDIA's quantized card
establishes Danish quality, so the repository's Danish fixtures remain a gate.

### Qwen3.8-27B

`Qwen/Qwen3.8-27B` is a real, Apache-2.0, dense 27B vision-language model with
262K native context. Qwen describes improvements in coding, professional work,
research, agent execution, and harness compatibility. The official vLLM recipe
documents tool and reasoning parsers and estimates 67 GB for BF16 serving,
38 GB for FP8, and 32 GB for NVFP4 on its supported datacenter-GPU recipes.
Sources: [Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[official Qwen3.8 repository](https://github.com/QwenLM/Qwen3.8), and
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

This model was released on 2026-08-14 and requires a very recent vLLM stack;
the current vLLM recipe calls for vLLM 0.27.2+ and `transformers>=5.8.0` and
does not list DGX Spark among its tested hardware. It is dense—27B parameters
participate in inference rather than 3B active experts—so nominally smaller
weights do not imply better Home Assistant TTFT than Qwen3.6-35B-A3B. Qwen's
public card also does not provide Danish-specific results. Recommendation
change: install it as the high-quality shared/code challenger after the
Spark-tested Qwen3.6 baseline, then measure Danish, tool correctness, Codex
Responses behavior, TTFT, throughput, and memory headroom.

### Whisper large-v3-turbo

The official artifact is MIT-licensed, approximately 809M parameters, and
tagged for 99 languages. It is a pruned large-v3 with decoding layers reduced
from 32 to 4 for much faster decoding with minor quality degradation. The card
also warns that performance is uneven across languages and that Whisper can
hallucinate text, so aggregate multilingual support is not Danish acceptance
evidence. Sources: [Whisper large-v3-turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo)
and [performance limitations](https://huggingface.co/openai/whisper-large-v3-turbo#performance-and-limitations).

Recommendation change: none to the STT shortlist—install and benchmark it
against Danish Parakeet. The installation summary should list speech models in
a separate section so an “LLM” answer does not appear to reject them.

### Røst-v3 Chatterbox

The exact model is `CoRal-project/roest-v3-chatterbox-500m`, a 500M Chatterbox
Multilingual finetune trained on more than 2,000 hours of Danish. Its model card
reports MOS 4.23 from 20 native Danish speakers over 10 samples for two voices,
supports CUDA inference and zero-shot voice cloning, and identifies important
limits: long text must be split, exaggeration control is unavailable, and
English has a heavy Danish accent. Source: [Røst-v3 model card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m).

The operational evidence is weaker than Piper's. The CoRal inference fork is
archived, pins PyTorch 2.7.1/CUDA 12.8, and says its wrapper is intended for a
single caller rather than concurrent multi-user serving, although it provides
sentence-chunk streaming and a CUDA-graph fast path. There is no first-party
GB10 or Home Assistant/Wyoming recipe. Source: [CoRal Chatterbox repository](https://github.com/alexandrainst/coral_chatterbox#inference).

Licensing also needs clarification before production: the model repository is
tagged only as generic `openrail` and contains no license file, whereas the
inference fork is MIT-licensed. Treat the model-weight license as unresolved
until the publisher supplies or confirms the exact OpenRAIL text. This does not
block a controlled local evaluation, but it is a production gate.

Recommendation change: add Røst-v3 as the first high-naturalness Danish TTS
challenger and install it for listening/latency tests. Keep
`da_DK-talesyntese-medium` Piper as the known Home Assistant/Wyoming and
low-resource baseline. Promote Røst only if it passes pronunciation,
first-audio, real-time-factor, concurrency, license, and adapter tests.

## Revised staged install set

1. `nvidia/Qwen3.6-35B-A3B-NVFP4` — Spark-tested efficient shared-text baseline.
2. `Qwen/Qwen3-Coder-Next-FP8` — specialized coding candidate; begin at 32K context.
3. `Qwen/Qwen3.8-27B-FP8` — dense general/coding quality challenger; requires a separately pinned, newly compatible runtime.
4. `openai/whisper-large-v3-turbo` and `nvidia/parakeet-rnnt-110m-da-dk` — STT comparison, not LLMs.
5. `CoRal-project/roest-v3-chatterbox-500m` and Piper `da_DK-talesyntese-medium` — TTS naturalness versus integration/latency comparison.

Download and test sequentially. “Install” still means stage a pinned artifact
for qualification, not keep every model resident at once.
