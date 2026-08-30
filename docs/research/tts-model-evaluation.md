# Text-to-speech model evaluation

Verified: 2026-08-25

Status: Danish benchmark shortlist

## Recommendation

Use Piper with `da_DK-talesyntese-medium` as the first latency and integration
baseline. Add higher-naturalness Danish candidates only when their model card,
license, maintained runtime, and streaming path are verified. Do not include
XTTS-v2 as a Danish candidate: its official model card does not list Danish.

The maintained Open Home Foundation Piper repository provides a local TTS
engine and the published voice inventory contains the Danish
`da_DK-talesyntese-medium` voice. Home Assistant already integrates Piper, and
external TTS services can connect through Wyoming.

Sources: [maintained Piper repository](https://github.com/OHF-Voice/piper1-gpl),
[Piper Danish voice inventory](https://github.com/rhasspy/piper/blob/master/VOICES.md),
[Home Assistant Piper app](https://github.com/home-assistant/addons/tree/master/piper),
[Wyoming integration](https://www.home-assistant.io/integrations/wyoming/),
[XTTS-v2 supported languages](https://huggingface.co/coqui/XTTS-v2).

## Danish listening set

- Home Assistant confirmations and questions.
- Entity/area names, family names, addresses, times, dates, decimals, and
  temperatures.
- Abbreviations and English product names embedded in Danish.
- Short urgent responses and longer informational responses.
- Repeated synthesis to detect instability and pronunciation drift.

## Measures

Record time to first audio, total synthesis time, real-time factor, peak
resource use, first-run versus warm performance, streaming chunk continuity,
pronunciation error count, mean-opinion score, voice consistency, and Home
Assistant/Wyoming reliability.

The selected service must expose Home Assistant's required streaming path and
the stable `/v1/audio/speech` boundary. A small CPU-friendly Piper service may be
operationally preferable to consuming GB10 GPU capacity if listening tests meet
the agreed quality threshold; that decision requires measurement.

