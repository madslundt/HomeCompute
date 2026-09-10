# `home-core` Danish STT recommendation

Verified: 2026-09-10

## Decision

Keep the existing Wyoming Faster Whisper service, but A/B-test `small-int8` as
the preferred model on `home-core`. Promote it if real Danish household commands
still complete within the acceptable end-to-end Assist latency; otherwise keep
the current `base-int8` configuration as the conservative fallback.

This is a model change, not a platform change. Home Assistant officially supports
Whisper running as an external Wyoming service on a more powerful LAN machine,
and the maintained Wyoming Faster Whisper server supports its built-in INT8
models over that protocol. This preserves the existing integration, firewall,
state, health-check, and rollback design.

Do not replace Whisper with Speech-to-Phrase for Danish today. Speech-to-Phrase
is attractive for fast, personalized home-control phrases, but Danish is absent
from its current supported-language list and it is not a general transcription
engine.

Sources: [Home Assistant Wyoming integration](https://www.home-assistant.io/integrations/wyoming/),
[Wyoming Faster Whisper](https://github.com/OHF-Voice/wyoming-faster-whisper), and
[Speech-to-Phrase supported languages](https://github.com/OHF-Voice/speech-to-phrase#supported-languages).

## Why `small-int8`

`home-core` has an Intel Core Ultra 5 125U and 48 GB RAM, while the deployed STT
container is currently limited to four CPUs and 4 GiB. The host therefore has
ample memory for Whisper Small without changing the resource envelope. The
unknown is interactive latency under the host's real concurrent workload, not
whether the model fits.

OpenAI describes the Whisper sizes as a speed/accuracy trade-off: Base has 74M
parameters, Small 244M, Medium 769M, Turbo 809M, and Large 1,550M. Its indicative
A100 figures rate Base at about 7x Large speed, Small at 4x, Medium at 2x, Turbo
at 8x, and Large at 1x, with an explicit warning that actual speed varies by
language and hardware. These GPU figures are not a `home-core` latency forecast.

Faster Whisper supports INT8 execution on CPU. Its published Small INT8 CPU
benchmark used 1,477 MB RAM and transcribed 13 minutes in 1 minute 42 seconds on
an eight-thread Intel Core i7-12700K with beam size 5. That establishes a
plausible resource class, but the different CPU, four-thread limit, beam size 1,
and short far-field Danish commands require measurement on `home-core`.

Sources: [`home-core` observed hardware](../home-core-rollout-plan.md#gateway-deployment--2026-09-04),
[current container limits](../../deploy/wyoming-stt/compose.yaml),
[OpenAI Whisper model table](https://github.com/openai/whisper#available-models-and-languages),
[Faster Whisper CPU benchmark](https://github.com/SYSTRAN/faster-whisper#benchmark), and
[Rhasspy Small INT8 artifact](https://huggingface.co/rhasspy/faster-whisper-small-int8).

## Comparison

| Choice | Danish accuracy case | CPU latency and memory | Home Assistant path | Disposition |
| --- | --- | --- | --- | --- |
| Current `base-int8` | No publisher result establishes its accuracy for this household. It is the lower-capacity quality baseline. | Lowest-risk latency choice; 74M parameters. | Already deployed and verified over Wyoming. | Keep as rollback and use if Small misses the latency gate. |
| `small-int8` | Higher-capacity quality hypothesis, but not a proven Danish winner until the household corpus is scored. | Published Faster Whisper result: 1,477 MB on a different eight-thread Intel CPU; 244M parameters. | Same maintained Wyoming service and official Rhasspy artifact. | Preferred A/B candidate. |
| `medium-int8` | Another capacity increase without a published household-Danish result. | 769M parameters; OpenAI's indicative model table is materially slower than Small. It may fit RAM but is a poor CPU-backup latency bet. | Same service can host the official Rhasspy artifact. | Do not promote on `home-core` without Small first failing accuracy. |
| Turbo or Large | Stronger general multilingual ceiling, not evidence of Danish command accuracy. OpenAI warns Whisper quality varies widely by language and context. | 809M or 1,550M parameters; CPU responsiveness is the concern even though 48 GB RAM can hold them. | Wyoming can accept a Hugging Face model, but this is a larger operational and benchmarking change than switching built-in INT8 sizes. | Reserve for GB10 or an explicit quality experiment. |
| Speech-to-Phrase | Personalized known phrases, but Danish is not currently supported. | Official Home Assistant guidance reports sub-second use even on small devices. | Maintained Wyoming integration, but it only recognizes its known phrase set. | Not a Danish option today. |

Sources: [Rhasspy Base INT8](https://huggingface.co/rhasspy/faster-whisper-base-int8),
[Rhasspy Medium INT8](https://huggingface.co/rhasspy/faster-whisper-medium-int8),
[OpenAI Whisper model card](https://github.com/openai/whisper/blob/main/model-card.md),
[Wyoming Faster Whisper custom model support](https://github.com/OHF-Voice/wyoming-faster-whisper), and
[Home Assistant local STT comparison](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/#speech-to-text-engines).

## Promotion gate

Run Base and Small against the same consented Danish corpus: short commands,
entity and room names, numbers and times, Danish/English product names, and
quiet plus far-field/noisy samples. Record command-semantic accuracy, entity-name
accuracy, final STT latency, end-to-end Assist latency, and peak container RAM/CPU.

Promote `small-int8` only if it materially improves semantic/entity accuracy and
keeps voice interaction responsive under normal `home-core` load. This A/B test
is necessary because OpenAI explicitly says Whisper performance varies by
language and recommends evaluating the intended context; model size alone does
not establish Danish quality.

