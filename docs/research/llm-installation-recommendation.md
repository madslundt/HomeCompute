# LLM installation recommendation

> Superseded on 2026-09-08 by ADR-019. Keep this document as historical
> evaluation context, not as the current install list.

Verified: 2026-09-07

Status: selected GB10 roster with production promotion still gated by live
measurement. The machine-readable authority is
[`config/gb10-model-roster.json`](../../config/gb10-model-roster.json).

## Decision

Retain at most two text models after evaluation and run at most one at a time:

1. `RadixArk/Qwen3.8-Flash-Next-NVFP4` through the pinned
   `blazux/qwen3.8-Flash-DGX` recipe is the scheduled quality lane for difficult
   coding, research synthesis, corpus cross-referencing, and long-running
   agents.
2. Benchmark `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` plus its
   DSpark draft against `RadixArk/Qwen3.8-27B-NVFP4` plus its DFlash2 draft.
   Promote one as the everyday assistant, automation, Home Assistant, and
   meeting-summary model; remove the loser after the rollback window.

The checked-in router fails closed while qualification is pending: it has no
default, no bound aliases, and no exact-model allow-list. Benchmark tools must
invoke the candidate runtime directly. After one everyday winner and the
Flash-Next tuple pass their gates, record the winner, bind aliases, and enable
routing in a separately reviewed change. The router enforces the one-resident
text-model limit when serving is enabled.

## Supporting services

| Role | Model | Activation rule |
| --- | --- | --- |
| Private retrieval | `nvidia/Nemotron-3-Embed-1B-NVFP4` | Enable with a real RAG corpus; version the index tuple |
| Recorded Danish STT | `CoRal-project/roest-v3-whisper-1.5b` | Preferred meeting and asynchronous transcription candidate |
| Live Danish STT | `nvidia/nemotron-3.5-asr-streaming-0.6b` | Benchmark only when live voice is enabled; retain only if latency offsets its weaker Danish evidence |
| Natural Danish TTS | `CoRal-project/roest-v3-chatterbox-350m` | Preferred GB10 TTS candidate; CPU Piper remains an independent fallback |
| Speaker attribution | `pyannote/speaker-diarization-community-1` | Enable only for multi-speaker meetings |
| Retrieval reranking | `nvidia/llama-nemotron-rerank-vl-1b-v2` | Add only after measured top-K ordering failure or when visual page content matters |

Do not add a separate vision model initially. Flash-Next and Qwen3.8-27B
already provide vision capability; qualify the selected text tuple before
creating another resident service.

## Exclusions

- Do not retain `nvidia/DeepSeek-V4-Flash-NVFP4` on this single GB10. The best
  current operational evidence is for paired GB10 systems and it overlaps the
  Flash-Next quality lane.
- Do not substitute `nvidia/Qwen3.8-Flash-Next-NVFP4` into the `blazux` recipe.
  It is a distinct quantized checkpoint without the same pinned single-GB10
  evidence.
- Retire `nvidia/Qwen3.6-35B-A3B-NVFP4` after the new tuples pass integration
  smoke tests. It remains represented only as the legacy installer baseline
  until one new runtime profile is audited.
- Ornith, Muse, Gemma, GPT-OSS, Devstral, GLM, and other general LLMs stay out
  of the retained roster unless a selected model fails a named acceptance gate.
- Do not use NVIDIA Magpie TTS for Danish; Danish is absent from its listed
  languages. Do not use Nemotron Diarization Preview in production.

## Promotion sequence

1. Audit and pin each candidate's complete tuple: model and draft revisions,
   recipe/runtime revision, image digest, template, parsers, precision,
   context, and launch flags.
2. Qualify Nemotron and Qwen27 independently on the GB10, then compare them on
   identical Danish, structured-tool, Home Assistant, n8n, coding, memory,
   throughput, cold-start, and recovery fixtures.
3. Record one everyday winner. Update the router's default and everyday aliases
   if Qwen27 wins; do not keep both as ordinary production models.
4. Qualify Flash-Next independently with deterministic output, prefix-cache
   correctness, cold/warm NVMe behavior, 262K context, unload/reload recovery,
   and the deep-work acceptance corpus.
5. Test each enabled speech/RAG service with the everyday profile, then repeat
   only the required coexistence tests with Flash-Next. Do not infer mixed-load
   safety from isolated success.
6. Promote exact passing tuples. Remove failed candidate artifacts only after
   their evidence and rollback decision have been retained.

The detailed evidence, owner reports, and direct source links are in the
[dated recommendation](gb10-model-installation-recommendation-2026-09-07.md).
