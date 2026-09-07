# GB10 model recommendation from owner reports

**Verified:** 2026-09-07
**Scope:** one NVIDIA GB10/DGX Spark with 128 GB unified memory; models on the
Mac are excluded.
**Decision:** keep two complementary text models at most, one primary model per
speech function, and retrieval components only when private-corpus search is
enabled. Installed models do not all remain resident simultaneously.

## Recommended set

| Role | Exact candidate | Disposition | Reason |
| --- | --- | --- | --- |
| Difficult coding, research synthesis, cross-referencing, vision, and long-horizon agent work | [`RadixArk/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4) through the pinned [`blazux/qwen3.8-Flash-DGX`](https://github.com/blazux/qwen3.8-Flash-DGX) recipe | **Keep; quality lane** | This is the strongest current single-GB10 owner-supported quality candidate. The recipe memory-maps the 48 GiB PLE table from NVMe, keeps about 76 GiB resident, and reports roughly 26 tok/s with the published checkpoint. Owner reports particularly favor it for scientific corpus work and complex cross-referencing. Run it in drained or scheduled windows because startup, NVMe page-cache behavior, and memory pressure make it a poor always-on companion to another large LLM. |
| General chat, tool calls, Home Assistant reasoning, n8n automations, and inexpensive subagents | [`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) plus its [`DSpark` draft](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) | **Benchmark, then keep only if it passes** | NVIDIA provides an exact one-GB10 vLLM recipe, 1M validated context, tool parsing, 30B total/3B active parameters, and recommends DSpark for latency-sensitive low-concurrency use. It is the most plausible complement to Flash-Next rather than another quality-first model. Evidence is primarily publisher evidence, not mature owner consensus, and Danish is absent from its supported-language list. It must pass Danish, tool-schema, latency, and recovery fixtures before promotion. |
| Fast/general challenger | [`RadixArk/Qwen3.8-27B-NVFP4`](https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4) plus [`z-lab/Qwen3.8-27B-DFlash2`](https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2), using the pinned [single-Spark recipe](https://github.com/CharmiUwU/Qwen3.8-27B-DFlash2-DGX-Spark) | **A/B only; do not keep all three text models** | This has unusually good one-Spark operational evidence: native 262K context, ten request slots, measured medians of 56.32 tok/s on code and 28.08 tok/s on prose. Owners often describe 27B as the routine daily driver and Flash-Next as the hard-task model. Compare it directly with Nemotron Lightning; retain whichever produces more successful automation/tool tasks per minute with acceptable Danish. |
| Private-corpus embeddings | [`nvidia/Nemotron-3-Embed-1B-NVFP4`](https://huggingface.co/nvidia/Nemotron-3-Embed-1B-NVFP4) | **Keep when RAG is enabled** | Roughly 1 GB, 32K input, Danish included in its multilingual evaluation, and intended for multilingual QA over large corpora. Schedule ingestion and re-index whenever the model, dimensions, normalization, or prompting changes. |
| Danish recorded-audio STT | [`CoRal-project/roest-v3-whisper-1.5b`](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b) | **Preferred quality candidate** | The local Danish comparisons reviewed for this project favor Røst strongly for conversational recordings. Use it for meetings, interviews, and asynchronous transcription. |
| Danish streaming STT | [`nvidia/nemotron-3.5-asr-streaming-0.6b`](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) | **A/B for live voice** | It has true streaming modes from 80 to 1,120 ms and explicitly includes Danish, but Danish is only in its broad-coverage tier and its published Danish WER is not a quality win. Keep it only if its live latency/turn-taking advantage outweighs Røst's transcription quality. [`nvidia/parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) remains an alternate batch/low-complexity baseline, not the automatic Danish winner. |
| Natural Danish TTS | [`CoRal-project/roest-v3-chatterbox-350m`](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m) | **Preferred GB10 TTS candidate** | Small and Danish-specific; its publisher reports MOS 4.01 from 20 native listeners. Keep CPU-hosted Piper `da_DK-talesyntese-medium` as the independent low-latency/GB10-outage fallback. |

## Text-model decision

The target steady state is **Flash-Next plus one fast model**, not Flash-Next,
DeepSeek, Nemotron, and Qwen 27B together.

1. Qualify Flash-Next as the quality lane.
2. Benchmark Nemotron Lightning against Qwen3.8-27B on the actual Danish and
   tool-use workload.
3. Keep Nemotron if its smaller active footprint, 1M context, and exact NVIDIA
   Spark path translate into materially better task latency and reliability.
4. Keep Qwen3.8-27B instead if it is more reliable in Danish, coding, and
   structured tool calls or owner-reported throughput reproduces better.
5. Delete or archive the losing fast-model checkpoint after the rollback
   window.

For a single GB10, choose Qwen3.8 Flash-Next over
[`nvidia/DeepSeek-V4-Flash-NVFP4`](https://huggingface.co/nvidia/DeepSeek-V4-Flash-NVFP4)
for the first quality lane. DeepSeek is a credible coding/agent model, but it
has 284B total/13B active parameters and the strongest current owner recipes
and throughput reports use a two-GB10 TP2 system with roughly 156 GiB of
weights. NVIDIA's model card demonstrates TP8 serving rather than a clean
single-Spark production path. Current owner comparisons are mixed on quality;
that does not justify maintaining both huge checkpoints on this machine.

The user-linked
[`nvidia/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4)
is a different quantized artifact from the RadixArk checkpoint used by the
blazux recipe. Do not substitute it into that recipe. NVIDIA's checkpoint is a
valid future challenger, but its card documents B200/B300 validation; test it
only with a recipe that pins and validates that exact checkpoint on one GB10.

## Optional pipeline models

- Add [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
  only when recorded meetings require speaker attribution.
- Add [`nvidia/llama-nemotron-rerank-vl-1b-v2`](https://huggingface.co/nvidia/llama-nemotron-rerank-vl-1b-v2)
  only if retrieval evaluation shows poor top-K ordering or the corpus contains
  visually important tables, charts, or page layouts. An embedder alone is the
  simpler first RAG implementation.
- Do not use [`nvidia/magpie_tts_multilingual_357m`](https://huggingface.co/nvidia/magpie_tts_multilingual_357m)
  for this role: its twelve listed languages do not include Danish.
- Do not use [`nvidia/Nemotron-3-Diarization-preview`](https://huggingface.co/nvidia/Nemotron-3-Diarization-preview)
  in production; the card describes it as early-access evaluation software.

## Minimal operating profiles

| Profile | Resident/active components |
| --- | --- |
| Everyday automation and voice | Winning fast LLM (Nemotron Lightning **or** Qwen3.8-27B), one STT service, Røst 350M TTS when needed; embeddings loaded for query/ingestion only as measured |
| Deep work | Drain the fast LLM if required, activate Flash-Next, retain only speech services that pass the memory/latency soak |
| Meeting ingestion | Røst Whisper, optional pyannote, embedder, then the active text model for grounded summary/extraction |

All aliases may route to the same loaded model. `coding`, `assistant`,
`automation`, and `research` are not reasons by themselves to keep four LLMs
resident or installed.

## Qualification gates

Use an identical local acceptance set for Nemotron and Qwen27:

- Danish household/entity instructions and Danish/English code switching;
- valid structured tool calls, malformed-call recovery, and JSON-schema
  adherence;
- Home Assistant p95 first-token and complete-turn latency;
- n8n multi-step tool sequences and idempotent retry behavior;
- representative repository coding fixes with tests;
- grounded corpus questions scored for citation support and hallucination;
- one-, four-, and eight-request throughput, peak UMA, cold start, and recovery.

Pin the complete tuple `(model revision, container digest, runtime revision,
precision, draft model, context, parsers, template, flags)`. Community posts
nominate candidates; only this workload selects the retained model.

## Owner-report evidence

- The [scientific-corpus owner report](https://www.reddit.com/r/LocalLLM/comments/1w91it3/qwen38_flash_next_is_the_best_model_i_tested_for/)
  particularly favors Flash-Next for cross-referencing and recursive research.
- A [27B versus Flash-Next owner discussion](https://www.reddit.com/r/LocalLLaMA/comments/1w9v6qp/are_you_running_qwen_38_27b_or_qwen_flash_next/)
  is mixed but repeatedly describes 27B as the routine model and Flash-Next as
  the difficult-task model.
- A [multi-model DGX Spark owner test](https://www.reddit.com/r/LocalLLaMA/comments/1w1jhh4/dgx_sparks_and_new_models_my_tests_and_results/)
  found Flash-Next low/medium thinking the best quality/efficiency balance in
  its small coding set, while Qwen27 supplied strong single-Spark concurrency.
- Current [DeepSeek versus Qwen owner reports](https://www.reddit.com/r/LocalLLM/comments/1w8mkoh/qwen_38_next_flash_or_deepseek_v4_flash/)
  have advocates for both; the concrete DeepSeek measurements shown there use
  a two-GB10 system, so they do not overturn the single-machine fit decision.

Research stopped when every active GB10 role had a candidate, overlapping
models had explicit A/B and removal rules, exact single-Spark deployment
evidence was distinguished from publisher benchmark claims, and further model
discovery was unlikely to change the small initial set.
