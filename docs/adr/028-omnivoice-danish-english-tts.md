# ADR-028: OmniVoice replaces Plapre for Danish and English TTS

## Decision

The owner selected `k2-fsa/OmniVoice` for Danish-first household TTS with
English support, and explicitly requested stopping and removing Plapre.
Use the pinned source and model revisions in `config/omnivoice-tts.json`.
Use an isolated GB10 runtime for inference and home-core for adapters.
The measured home-core CPU latency was unsuitable for interactive speech. Preserve the existing CPU MOSS/Piper fallback.

Keep inference local, runtime egress disabled, and client/network scopes
unchanged. Weights are noncommercial; this deployment is household-only.
Reference audio must be approved; placement measurements use synthetic voices.
Do not claim Danish listening or latency qualification from model selection.

## Lifecycle

Implement, validate and publish source before the owning workload lifecycle
retires Plapre. Remove its active containers and routes without deleting
unrelated images, model caches, recordings, STT services or text engines.
Retain the previous image/revision for explicit rollback.

## Status

Accepted by the owner on 2026-11-02. Supersedes ADR-021's Plapre/Qwen TTS
selection. STT selection is outside this decision. Implementation and live
verification are recorded separately in the workload runbook.

## Evidence

- [Danish/English comparison](../research/tts-danish-english-plapre-comparison-2026-11-02.md)
- [OmniVoice model card and weight terms](https://huggingface.co/k2-fsa/OmniVoice)
- [Runtime lifecycle](../../deploy/omnivoice/README.md)
