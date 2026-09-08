# Model-role trade-off matrix

> Historical evaluation matrix. ADR-019 and the machine-readable roster contain
> the owner-selected final stack as of 2026-09-08.

Verified: 2026-09-08

Status: current single-GB10 scorecard. The machine-readable selection authority
is the [GB10 roster](../../config/gb10-model-roster.json), and the durable
decision is [ADR-019](../adr/019-single-gb10-model-roster.md).

## Current roles

| Role | Selected candidate or competition | Activation and removal rule |
| --- | --- | --- |
| Difficult coding, scientific synthesis, and cross-referencing | `RadixArk/Qwen3.8-Flash-Next-NVFP4` with the pinned `blazux/qwen3.8-Flash-DGX` recipe | Retain as the scheduled quality lane after its exact tuple passes GB10 qualification. Drain the everyday text process before loading it. |
| Assistant, tools, n8n, Home Assistant, and meeting summaries | A/B `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` against `RadixArk/Qwen3.8-27B-NVFP4` | Compare identical Danish, tool, coding, latency, memory, recovery, and mixed-load fixtures. Retain one winner and remove the loser after the rollback window. No default or alias is bound before this decision. |
| Recorded Danish STT | `CoRal-project/roest-v3-whisper-1.5b` | Retain for meeting and asynchronous transcription after local acceptance. |
| Live Danish STT | `nvidia/nemotron-3.5-asr-streaming-0.6b` | Benchmark only if live voice is enabled; remove it if latency does not justify a second STT model. |
| Danish TTS | `CoRal-project/roest-v3-chatterbox-350m` | Preferred natural-voice candidate. Keep CPU Piper as the small operational fallback rather than another GB10 text resident. |
| Private retrieval | `nvidia/Nemotron-3-Embed-1B-NVFP4` | Enable only with a real access-controlled corpus and version the model, chunker, and index together. |
| Retrieval reranking | `nvidia/llama-nemotron-rerank-vl-1b-v2` | Add only after measured top-K ordering failure or when visual document pages require it. |
| Speaker attribution | `pyannote/speaker-diarization-community-1` | Enable only for multi-speaker recordings. |

## Hard operating constraints

- Retain no more than two production text models: Flash-Next and the everyday
  A/B winner.
- Run no more than one text model at a time on the single 128 GiB GB10.
- Treat model, revision, draft, recipe/runtime, template, parsers, precision,
  context, and launch flags as one immutable qualification tuple.
- Do not infer Danish suitability from multilingual aggregate claims. Test
  Danish names, dates, negation, code-switching, schemas, and real tool calls.
- Do not promote an alias from publisher benchmarks or owner tokens/second
  reports alone. Require workload correctness, memory headroom, cancellation,
  recovery, and mixed-load evidence.

## Explicit exclusions

- `nvidia/DeepSeek-V4-Flash-NVFP4` overlaps the quality lane and has stronger
  current operational evidence on paired GB10 systems than on this one-GB10
  target.
- `nvidia/Qwen3.8-Flash-Next-NVFP4` is a distinct checkpoint and is not a
  substitute for the RadixArk artifact in the pinned `blazux` recipe.
- `nvidia/Qwen3.6-35B-A3B-NVFP4` is a legacy integration tuple, not a candidate
  for the retained roster.
- Ornith, Muse, Gemma, GPT-OSS, Devstral, GLM, and other general LLMs remain out
  unless a selected model fails a predeclared acceptance gate.
- NVIDIA Magpie TTS is not a Danish TTS candidate, and Nemotron Diarization
  Preview is not a production diarization choice.

Historical candidate analysis remains available in Git history and in the
dated research documents. It is evidence context, not executable policy.
