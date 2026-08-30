# Speech-to-text model evaluation

Verified: 2026-08-25

Status: Danish benchmark shortlist

## Recommendation

Benchmark three open-ended STT candidates and one deterministic baseline:

1. `nvidia/parakeet-rnnt-110m-da-dk` for Danish low-latency streaming-oriented
   recognition.
2. `openai/whisper-large-v3-turbo` for multilingual open-ended accuracy.
3. `openai/whisper-large-v3` as the slower meeting-quality reference.
4. Home Assistant Speech-to-Phrase for supported deterministic home-control
   commands; it is a routing baseline, not the general STT endpoint.

NVIDIA's model card identifies Parakeet-RNNT 110M as a Danish ASR model. Home
Assistant's maintained Whisper app supports faster-whisper, Transformers, and
sherpa-onnx Parakeet backends. Home Assistant can connect external STT services
through Wyoming.

Sources: [NVIDIA Danish Parakeet model card](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk),
[Whisper large-v3-turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo),
[Whisper large-v3 model card](https://huggingface.co/openai/whisper-large-v3),
[Home Assistant Whisper app](https://github.com/home-assistant/addons/tree/master/whisper),
[Wyoming integration](https://www.home-assistant.io/integrations/wyoming/),
[local voice pipeline](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/).

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
