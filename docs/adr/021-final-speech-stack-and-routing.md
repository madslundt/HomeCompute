# ADR-021: Final 2+2 speech stack and language routing

## Context

The earlier single-GB10 roster selected Parakeet for English STT and Qwen3-TTS
1.7B for English TTS. Follow-up scenario research selected a smaller, clearer
2+2 production roster and identified incompatible runtime requirements. Model
selection does not itself authorize downloads, deployment, or live routing.

## Decision

Use these pinned production candidates:

- Danish STT: `syvai/hviske-v5.3`;
- English, mixed, or unknown-language STT: `openai/whisper-large-v3-turbo`;
- Danish TTS: `syvai/plapre-nano-v2`;
- English and publisher-supported non-Danish TTS:
  `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`.

Route known Danish audio to Hviske. Route English, mixed, and unknown audio to
Whisper Turbo. Route Danish output only to Plapre; never route Danish text to
Qwen3-TTS. Route supported non-Danish output to Qwen3-TTS. When the Danish GPU
voice is unavailable or overloaded, use CPU Piper
`da_DK-talesyntese-medium`. There is no selected non-Danish CPU fallback, so
that outage path fails closed rather than speaking in the wrong language.

Keep `pyannote/speaker-diarization-community-1` as an optional meeting-pipeline
stage. Evaluate `syvai/hviske-v5-tiny` later only if a measured low-latency gap
justifies it; it is not a primary route.

Each model runs in an isolated environment. Hviske targets vLLM 0.19, Plapre
targets vLLM `>=0.15,<0.16`, and Qwen3-TTS uses its own pinned runtime profile.
Whisper is also isolated so upgrades cannot silently change the other three.

Hviske is CC BY-NC 4.0. Employer, work, or commercial use requires a reviewed
SYVAI license decision before activation. Plapre is CC BY 4.0. Only an approved
supplied voice or approved reference voice may be configured; arbitrary voice
cloning requires documented consent and a separately approved policy.

Plapre promotion requires representative mixed LLM load, warm p95 first audio
at or below 750 ms, warm p95 RTF at or below 0.5, at least 95% pronunciation
pass, and mean naturalness at least 3.5/5. The fixture covers Danish names,
addresses, numbers, device names, and mixed tokens. Because Plapre can
occasionally mis-speak, the recovery suite must prove ASR verification, no more
than one resynthesis, and Piper fallback after a second mismatch or timeout.

## Consequences

The language decision occurs before a model is selected and is testable without
loading a model. Speech routes remain `qualification-only` until exact images,
protocol adapters, resources, and physical-host evidence pass. The legacy
Whisper/Piper Compose scaffold remains non-production and is not silently
repurposed as the four isolated runtimes.

## Status

Accepted; supersedes the speech-model and speech-exclusion portions of ADR-019.
No model download, deployment, or live route change is authorized by this ADR.

## Evidence

- `config/gb10-model-roster.json`
- `config/speech-routing-policy.json`
- `config/tts-qualification.json`
- `docs/tts-qualification.md`
- External scenario report: `/Users/madslundt/Documents/gb10setup/docs/research/full-scenario-model-install-shortlist-2026-09-09.md`
