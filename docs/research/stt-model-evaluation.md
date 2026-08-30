# Speech-to-text model evaluation

Verified: 2026-08-30

Status: Danish benchmark shortlist

## Recommendation

Benchmark three open-ended STT candidates:

1. `openai/whisper-large-v3-turbo` as the operational Home Assistant and
   meeting baseline because a maintained Wyoming path exists.
2. `nvidia/parakeet-rnnt-110m-da-dk` as the Danish low-latency promotion
   candidate after a maintained adapter is available.
3. `openai/whisper-large-v3` as the slower meeting-quality reference.

NVIDIA's model card identifies Parakeet-RNNT 110M as a Danish ASR model. Home
Assistant's maintained Whisper app exposes Whisper Turbo through Wyoming. Its
Parakeet support names a different checkpoint, not the Danish RNNT artifact,
and NVIDIA says the Danish artifact is not yet supported by Riva. Parakeet
therefore needs a repository-owned, maintained protocol adapter before it can
be promoted. Speech-to-Phrase is excluded from the Danish route because its
official current language list does not include Danish.

Sources: [NVIDIA Danish Parakeet model card](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk),
[Whisper large-v3-turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo),
[Whisper large-v3 model card](https://huggingface.co/openai/whisper-large-v3),
[Home Assistant Whisper app](https://github.com/home-assistant/addons/tree/master/whisper),
[Speech-to-Phrase](https://github.com/OHF-Voice/speech-to-phrase),
[Wyoming integration](https://www.home-assistant.io/integrations/wyoming/),
[local voice pipeline](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/).

## Advantages, disadvantages, and selection rule

| Candidate | Advantages | Disadvantages | Selection role |
| --- | --- | --- | --- |
| Whisper large-v3-turbo | Maintained Home Assistant/Wyoming path; multilingual transcription and timestamps; four decoder layers make it substantially faster than large-v3 | Publisher documents a minor quality loss from pruning large-v3; Danish household, far-field, code-switch, and Plaud accuracy remain unmeasured | Day-one operational recommendation. Keep for `home` if it passes latency/semantic gates and for meetings if it has the best passing WER/alignment result |
| Danish Parakeet RNNT 110M | Danish-specific, small, streaming-oriented model; NVIDIA reports Danish WER results and Blackwell compatibility | Danish only; no maintained required endpoint for this exact checkpoint; not yet supported by Riva; punctuation/code-switch behavior may lose to Whisper | Promotion candidate for `home` only after its adapter exists and it beats Whisper on passing Danish command latency/accuracy |
| Whisper large-v3 | Full 32-layer decoder and the most conservative Whisper accuracy reference | Slower and more resource-intensive; may fail real-time and mixed-load gates | Meeting accuracy ceiling; select only if its WER/alignment gain justifies the measured latency and resource cost |
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
