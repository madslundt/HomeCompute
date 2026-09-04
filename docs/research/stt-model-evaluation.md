# Speech-to-text model evaluation

Verified: 2026-09-05

Status: Danish benchmark shortlist

## Recommendation

Benchmark the following STT candidates:

1. `CoRal-project/roest-v3-whisper-1.5b` as the first Danish meeting-quality
   candidate.
2. `syvai/hviske-v5.3` as the strongest conversational-Danish challenger, with
   its CC-BY-NC-4.0 terms treated as incompatible with work use unless reviewed.
3. `openai/whisper-large-v3-turbo` as the maintained Home Assistant/Wyoming and
   mixed-language integration baseline.
4. `syvai/hviske-v5-tiny` and `nvidia/parakeet-rnnt-110m-da-dk` as small Danish
   latency candidates. Both require an owned adapter; Hviske is noncommercial
   and lacks timestamps, diarization, and streaming.
5. `openai/whisper-large-v3` and `nvidia/parakeet-tdt-0.6b-v3` as multilingual
   controls rather than presumed Danish winners.

NVIDIA's model card identifies Parakeet-RNNT 110M as a Danish ASR model. Home
Assistant's maintained Whisper app exposes Whisper Turbo through Wyoming. Its
Parakeet support names a different checkpoint, not the Danish RNNT artifact,
and NVIDIA says the Danish artifact is not yet supported by Riva. Parakeet
therefore needs a repository-owned, maintained protocol adapter before it can
be promoted. Speech-to-Phrase is excluded from the Danish route because its
official current language list does not include Danish.

The Open Danish ASR Leaderboard evaluates saved raw outputs through one public
normalization and scoring harness across five Danish datasets. Its 2026-09-05
results materially change the accuracy shortlist:

| Model | Mean WER | CoRal conversation WER |
| --- | ---: | ---: |
| Røst v3 Whisper 1.5B | 13.92% | 21.08% |
| Hviske v5 tiny | 14.12% | 26.12% |
| Hviske v5.3 | 14.42% | 19.68% |
| Danish Parakeet RNNT 110M | 19.92% | 50.87% |
| Whisper large-v3 | 24.05% | 49.51% |
| Whisper large-v3-turbo | 28.59% | 63.83% |
| Parakeet TDT 0.6B v3 | 30.81% | 50.96% |

These figures rank candidates for local testing; they do not prove Plaud,
far-field household, entity-name, code-switch, timestamp, or GB10 latency
performance. The best model also changes by domain: Røst has the lower mean WER
of the two leading unrestricted candidates, while Hviske v5.3 has the lower
conversation WER.

Sources: [NVIDIA Danish Parakeet model card](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk),
[Røst v3 Whisper model card](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b),
[Hviske v5.3 model card](https://huggingface.co/syvai/hviske-v5.3),
[Hviske v5 tiny model card](https://huggingface.co/syvai/hviske-v5-tiny),
[Open Danish ASR Leaderboard](https://huggingface.co/datasets/RyeAI/danish-asr-leaderboard),
[Whisper large-v3-turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo),
[Whisper large-v3 model card](https://huggingface.co/openai/whisper-large-v3),
[Home Assistant Whisper app](https://github.com/home-assistant/addons/tree/master/whisper),
[Speech-to-Phrase](https://github.com/OHF-Voice/speech-to-phrase),
[Wyoming integration](https://www.home-assistant.io/integrations/wyoming/),
[local voice pipeline](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/).

## Advantages, disadvantages, and selection rule

| Candidate | Advantages | Disadvantages | Selection role |
| --- | --- | --- | --- |
| Røst v3 Whisper 1.5B | Danish-specific Whisper fine-tune; best mean WER among the unrestricted local models in the common Danish benchmark; standard Transformers path | Custom OpenRAIL-derived terms; no maintained Wyoming service; timestamps, mixed-language behavior, and GB10 performance need qualification | First Danish meeting-quality candidate |
| Hviske v5.3 | Lowest CoRal conversation WER in the selected local candidates; publisher documents Transformers and OpenAI-compatible vLLM paths | CC-BY-NC-4.0; custom code; no maintained Home Assistant adapter; publisher speed is from an RTX 3090 | Personal-use conversation challenger; do not use for work meetings without license review |
| Whisper large-v3-turbo | Maintained Home Assistant/Wyoming path; multilingual transcription and timestamps; four decoder layers make it substantially faster than large-v3 | Publisher documents a minor quality loss from pruning large-v3; Danish household, far-field, code-switch, and Plaud accuracy remain unmeasured | Day-one operational recommendation. Keep for `home` if it passes latency/semantic gates and for meetings if it has the best passing WER/alignment result |
| Hviske v5 tiny | 263M Danish model; CUDA, vLLM, ONNX, MLX, and GGUF paths; strong aggregate Danish benchmark result for its size | CC-BY-NC-4.0, gated download, Danish only, clips up to 35 seconds, no timestamps/diarization/streaming | Personal Home Assistant latency challenger after an adapter exists |
| Danish Parakeet RNNT 110M | Danish-specific, small, streaming-oriented model; NVIDIA reports Danish WER results and Blackwell compatibility | Danish only; no maintained required endpoint for this exact checkpoint; not yet supported by Riva; punctuation/code-switch behavior may lose to Whisper | Promotion candidate for `home` only after its adapter exists and it beats Whisper on passing Danish command latency/accuracy |
| Whisper large-v3 | Full multilingual 32-decoder-layer reference with established tooling | Slower and substantially weaker than Danish specialists in the common benchmark | Multilingual meeting control, not the Danish accuracy ceiling |
| Parakeet TDT 0.6B v3 | Danish and English support, punctuation, word/segment timestamps, long-audio modes, and permissive CC-BY-4.0 terms | Weak Danish result in the common benchmark; no exact GB10/Home Assistant production path is established | Throughput, timestamp, and multilingual control |
| Speech-to-Phrase | Deterministic known-phrase routing can be efficient in supported languages | Danish is not currently supported and it is not general transcription | Not a Danish candidate; reconsider only if official Danish support is added |

The likely result is intentionally allowed to split: the lowest-latency passing
candidate can serve `home`, while the lowest-WER passing candidate can serve
meetings. Use one model for both only if it wins both rules and the mixed-load
gate; operational simplicity must not override a hard accuracy or latency gate.

## Benchmark corpus

- Danish household commands from multiple speakers, ages, rooms, and microphone
  distances.
- Entity names, street names, English product names inside Danish sentences,
  numbers, temperatures, times, and negation.
- Quiet, television, kitchen noise, music, reverberation, and packetized audio.
- English comparison set and Danish/English code switching.
- Short commands and longer open-ended dictation.
- Danish-only, English-only, and code-switched Plaud meetings with multiple
  speakers; compare Plaud's transcript when available.

Use consented or synthetic recordings and store derived metrics rather than
private household audio unless explicitly approved.

## Measures and gate

Record word error rate, entity-name error rate, command intent preservation,
punctuation, language-detection failures, first-partial latency, final latency,
real-time factor, peak CPU/GPU memory, and concurrent-request degradation.

The selected GB10 service must support Home Assistant's integration path and
Meeting Assistant's stable `/v1/audio/transcriptions` boundary. Those may
require two adapters over one engine; endpoint naming alone is not
compatibility. A fast interactive winner and a slower batch-meeting winner may
share the STT capability only after priority/memory tests prove coexistence.
