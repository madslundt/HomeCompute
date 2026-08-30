# Danish text-to-speech recommendation

Verified: 2026-08-25

Status: install shortlist and promotion gate

## Decision

Install all three candidates for one controlled benchmark, but use
`da_DK-talesyntese-medium` Piper as the initial TTS default. Treat
`CoRal-project/roest-v3-chatterbox-350m` as the preferred promotion candidate
for interactive Danish speech. Keep `CoRal-project/roest-v3-chatterbox-500m`
only as the quality ceiling during the benchmark.

This ordering follows the stated priorities:

1. correct Danish pronunciation;
2. naturalness;
3. speed and first-audio latency.

No publisher reports Danish pronunciation accuracy on names, numbers, dates,
compounds, abbreviations, or mixed Danish/English text. Røst's reported Mean
Opinion Scores (MOS) measure perceived naturalness, not pronunciation
correctness. The repository's own acceptance test therefore remains decisive:
at least 95% pronunciation pass, naturalness at least 3.5/5, p95 first audio at
most 750 ms, and p95 real-time factor at most 0.5
([requirements](../requirements.md),
[verification strategy](../verification-strategy.md)).

The day-one Piper default is an operational choice, not a claim that it has the
best voice. Promote Røst 350M if it passes pronunciation and latency and native
Danish listeners prefer it to Piper. Test Røst 500M as the fallback promotion
candidate only if 350M's listening result is inadequate and 500M still meets
the latency gates.

## Evidence comparison

| Candidate | Correct Danish pronunciation | Naturalness evidence | Speed and integration evidence | Decision |
| --- | --- | --- | --- | --- |
| Piper `da_DK-talesyntese-medium` | Danish eSpeak phonemization is explicit in the voice configuration, and Piper permits raw phoneme overrides for troublesome names. There is no published pronunciation score. | The card says only `medium`, one speaker, 22.05 kHz, and that it was fine-tuned from the US-English Lessac voice. It publishes no Danish MOS. | The ONNX file is about 63 MB. The maintained Wyoming server and Home Assistant add-on list this exact voice; Home Assistant auto-discovers Piper over Wyoming. | Initial default and deterministic fallback. |
| Røst 350M | Fine-tuned on more than 2,000 hours of Danish and works with the supplied Mic and Nic voices. There is no published pronunciation score. Its stochastic sampling makes repeat testing necessary. | Publisher MOS 4.01, from 20 native Danish speakers using 10 samples for Mic and Nic. | The Turbo base is designed for lower compute/VRAM and low-latency agents, but neither CoRal nor Resemble publishes local Danish TTFA/RTF for this artifact. The repository is about 4.05 GB. | Preferred interactive-quality promotion candidate. |
| Røst 500M | Fine-tuned on more than 2,000 hours of Danish and works with Mic and Nic. There is no published pronunciation score. | Publisher MOS 4.23 using the same stated panel size and sample design. This is 0.22 above 350M, but the cards provide no confidence interval or controlled head-to-head result. | No published TTFA/RTF. The repository is about 5.36 GB. Long input must be sentence-split, and no maintained Wyoming adapter is supplied. | Quality ceiling, not the initial default. |

Sources: [Piper voice card](https://huggingface.co/rhasspy/piper-voices/blob/main/da/da_DK/talesyntese/medium/MODEL_CARD),
[Piper Danish configuration](https://huggingface.co/rhasspy/piper-voices/blob/main/da/da_DK/talesyntese/medium/da_DK-talesyntese-medium.onnx.json),
[Piper CLI and phoneme controls](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md),
[Piper voice inventory](https://github.com/OHF-Voice/wyoming-piper/blob/main/wyoming_piper/voices.json),
[Home Assistant Piper add-on](https://github.com/home-assistant/addons/blob/master/piper/config.yaml),
[Home Assistant Piper integration](https://www.home-assistant.io/integrations/piper/),
[Røst 350M card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m),
[Røst 350M files](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m/tree/main),
[Chatterbox Turbo card](https://huggingface.co/ResembleAI/chatterbox-turbo),
[Røst 500M card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m), and
[Røst 500M files](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m/tree/main).

The two Røst MOS studies are small publisher evaluations. They support Røst as
the naturalness challenger but do not establish that either model will pronounce
the user's actual Home Assistant text correctly.

## Runtime and production risks

- Piper has the only direct, maintained Home Assistant path in this shortlist.
  The current engine is GPL-3.0 and embeds eSpeak-ng. The Danish voice repository
  is tagged MIT, while its voice card identifies a CC0 source dataset.
- CoRal's inference fork adds Danish-aware number normalization, sentence
  splitting, cached speaker conditioning, sentence-level streaming, and a CUDA
  graph fast path claimed to make the T3 decode stage about twice as fast.
  However, the fork was archived on 2026-07-03. It explicitly targets a single
  caller/single worker, pins PyTorch and torchaudio 2.7.1 for CUDA 12.8, and
  supplies no Home Assistant/Wyoming service.
- Røst streaming is sentence-level, so first audio arrives only after the first
  sentence has been synthesized. This is acceptable for short confirmations
  only if the measured p95 stays within 750 ms.
- Both Røst model pages are tagged only as generic `openrail` and contain no
  exact license file. The archived CoRal code repository carries a customized
  OpenRAIL license that prohibits synthetic speech emulating, or found similar
  to, a natural person. Confirm the exact model-weight terms before production.
  Use the supplied Mic/Nic voices during evaluation; do not use voice cloning.
- Piper can run separately on CPU and preserve GB10 GPU capacity. Røst requires
  a CUDA/PyTorch service and a custom protocol adapter, so mixed-load voice
  latency and recovery need explicit tests.

Sources: [maintained Piper engine](https://github.com/OHF-Voice/piper1-gpl),
[Wyoming Piper](https://github.com/OHF-Voice/wyoming-piper),
[CoRal inference fork](https://github.com/alexandrainst/coral_chatterbox#inference),
[CoRal license](https://github.com/alexandrainst/coral_chatterbox/blob/master/LICENSE),
[Røst 350M repository](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m/tree/main), and
[Røst 500M repository](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m/tree/main).

The experimental OmniVoice backend now present in
[Wyoming Piper](https://github.com/OHF-Voice/wyoming-piper#omnivoice-backend-experimental)
is not added to the first benchmark. Its own documentation describes the
backend as experimental and publishes no Danish pronunciation, naturalness, or
latency result. It can be reconsidered after the three directly relevant
candidates.

## Small acceptance benchmark

Use 60 short, production-shaped Danish utterances and three native Danish
reviewers. Include entity and room names, family and street names, compounds,
times and dates, ordinals, decimals, temperatures, abbreviations, and English
product names inside Danish sentences. Add a few longer informational responses
to exercise sentence splitting.

1. Generate every phrase once with Piper and three times per Røst model to expose
   stochastic pronunciation drift. Use one supplied Røst voice consistently;
   choose Mic or Nic in a short preliminary blind audition.
2. Randomize and blind the clips. Record strict pronunciation pass/fail per
   phrase and 1-5 naturalness independently. A phrase passes pronunciation only
   when all critical words are correct.
3. On the GB10, record cold and warm p50/p95 first-audio latency, RTF, total
   synthesis time, memory high-water, and failures. Measure direct runtime and
   the actual Wyoming or adapter path separately.
4. Repeat warm tests during one Codex generation and one background n8n request.
   Exercise two simultaneous TTS requests even though normal Home Assistant use
   is usually serial.
5. Select only a model that meets all repository gates. If several pass, choose
   the one with the higher blinded naturalness score; if the difference is not
   meaningful, keep the simpler Piper service. Maintain a small pronunciation
   alias/phoneme list for stable entity and product names where Piper wins.

Do not tune text normalization differently between candidates during the first
comparison. After recording the baseline, add only production-safe aliases or
number/date normalization and rerun the affected fixtures.
