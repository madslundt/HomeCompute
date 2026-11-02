# ADR-027: Full Whisper Large-v3 for all speech recognition

## Status

Accepted target, 2026-11-02; deployment and hardware qualification are tracked
separately. Supersedes the STT selection, Danish replacement and optional Hviske
evaluation in ADR-021. Its TTS policies remain unchanged.

## Decision

Replace both Hviske v5.3 and Whisper Large-v3 Turbo with full
`openai/whisper-large-v3` at immutable revision
`06f233fe06e710322aca913c1bc4249a0d71fce1`. Use one resident local model for
Danish, English, mixed and unknown-language audio. The owner explicitly selected
the largest current published Whisper model and values meeting coverage over
latency. Turbo is a newer smaller derivative, with four decoder layers;
full Large-v3 has 32 and approximately 1.55 billion parameters.

Keep `whisper` as the model-neutral API alias. Retain existing `stt-danish` and
`whisper-turbo` names as compatibility routes to the full model. Neither legacy
model remains a desired serving engine or fallback. Remove Daythread's Danish
replacement route through its owning configuration API. Keep original audio,
provenance and saved user transcripts intact; do not merge conflicting outputs
or infer speaker identities.

Use the guarded [Whisper lifecycle](../../deploy/whisper-stt/README.md) from a
published release. Preserve rollback containers, credentials, private networking
and pending capacity gates. The old speech capacity experiment cannot qualify
this different model. Do not change TTS as part of this migration.

## Evidence and limits

On the private meeting comparison, the existing Turbo retained more discussion
than Hviske, which also produced severe repetition. Shifted/raw spot checks
reversed some omissions. These observations motivate replacing the serving
stack and investigating windowing, but do not establish full Large-v3 accuracy
or isolate the preprocessing cause. Full-model latency, quality, timestamps,
memory and concurrent text/TTS behavior require actual Spark measurements.
Without a verified verbatim reference, report coverage instead of WER.

Publisher sources checked 2026-11-02:

- [OpenAI Whisper model list](https://github.com/openai/whisper#available-models-and-languages)
- [Pinned full model](https://huggingface.co/openai/whisper-large-v3/tree/06f233fe06e710322aca913c1bc4249a0d71fce1)
- [Publisher model card](https://huggingface.co/openai/whisper-large-v3)
