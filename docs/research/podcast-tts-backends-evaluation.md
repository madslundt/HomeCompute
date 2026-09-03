# Podcast TTS backend evaluation

Verified: 2026-09-02

Status: benchmark-gated recommendation for handoff questions 7-11

## Decision

Use **Chatterbox Multilingual V3 as the first Danish backend** and
**Qwen3-TTS 12 Hz 1.7B as the leading English turn-synthesis challenger**.
Implement both behind a turn-oriented speech adapter. Add a separate
dialogue-oriented adapter, but keep **Dia2** and **VibeVoice** experimental until
the same 10-15 minute listening benchmark shows that their joint dialogue
generation is materially more enjoyable than carefully assembled turns.

This is not a final audio-quality ranking. The source material can establish
language support, model behavior, interfaces, limits, licensing, and reported
benchmarks. It cannot establish what a particular Danish voice sounds like to a
native listener, whether two fictional hosts remain recognizably identical
through an hour, or whether joint generation makes a whole episode more
enjoyable. Those are listening-test questions.

The recommended order is:

1. **Danish production candidate: Chatterbox Multilingual V3.** It is the only
   candidate in this set that officially lists Danish. It is MIT-licensed and
   comparatively small, but it is still a single-utterance generator. A
   30-60-minute episode must be split into many independent generations, so
   episode-scale speaker and delivery consistency are unproven.
2. **English production candidate: Qwen3-TTS 12 Hz 1.7B.** It has the strongest
   documented combination of English content accuracy, reusable voice cloning,
   voice design, style instructions, and long-speech testing. It is Apache-2.0.
   It is not a dialogue renderer: separate turns are separate outputs.
3. **English dialogue experiment: Dia2 2B, with original Dia as a short-segment
   baseline.** These models consume tagged two-speaker dialogue together and
   can therefore model pacing across turns. They are English-only and require
   audio prefixes or fine-tuning for stable voices. Dia2 stops at two minutes,
   has no released server, and has very little release/maintenance history.
4. **Long-dialogue research experiment: VibeVoice 1.5B.** Its architecture is
   the closest fit to whole-podcast synthesis: up to four speakers and a claimed
   90-minute context. However, Microsoft removed the usable long-form TTS
   implementation after misuse. The official weights remain available, but the
   new Hugging Face Transformers implementation landed on 2026-08-27 through an
   external contributor and currently points to unofficial converted weights.
   This is too new and too operationally ambiguous for an MVP dependency.

## Evidence matrix

| Backend | Actual synthesis unit | Official language support | Persistent voice mechanism | Published long-form evidence | Local deployment path | License and policy risk |
| --- | --- | --- | --- | --- | --- | --- |
| Chatterbox Multilingual V3, 500M | One text input to one waveform; no previous-turn/dialogue context | 23 languages including `da` and `en` | A reference recording is converted to reusable speaker and prompt conditionals | V3 publisher claims improved similarity and fewer hallucinations, but publishes no 30-60-minute speaker-drift result; current code caps one call at 1,000 25 Hz speech tokens, about 40 seconds | Upstream Python package/source; CUDA, CPU, or MPS; official Gradio demo; no upstream REST server | Code and model metadata are MIT; generated audio is watermarked |
| Qwen3-TTS 12 Hz, 0.6B/1.7B | One text/speaker/instruction item to one waveform; a list is a batch of independent samples | Ten languages including English; **not Danish** | Built-in voice IDs, a reusable clone prompt from reference audio, or voice-design-then-clone | Official paper evaluates internal samples longer than ten minutes for WER; it does not report multi-speaker cross-turn consistency or podcast naturalness | Upstream Python package and Gradio; DashScope cloud API; vLLM-Omni is documented as offline-only as of this review | Code and released model cards are Apache-2.0 |
| Dia 1.6B | Joint `[S1]`/`[S2]` dialogue waveform | English only | Seed or 5-10 second audio prompt; issue reports show seed alone is not reliable across chunks | Upstream explicitly recommends roughly 5-20 seconds; longer input can become unnaturally fast | Upstream Python/CLI/Gradio and Hugging Face Transformers | Apache-2.0, plus a README acceptable-use disclaimer |
| Dia2 1B/2B | Streaming joint `[S1]`/`[S2]` dialogue, at most two minutes | English only | Per-speaker audio prefixes or fine-tuning; upstream warns that unconditioned voices and quality vary per generation | Maximum context is 1,500 steps/about two minutes; no episode-scale evaluation is published | Python/CLI/Gradio, CUDA 12.8+ recommended; upstream server is still listed as upcoming | Apache-2.0, plus a README acceptable-use disclaimer |
| VibeVoice 1.5B | Joint long-form script with up to four voice prompts | English and Chinese only; no Danish | One reference audio per speaker inside the long context | Microsoft reports up to 90 minutes and four speakers, but also says overlapping speech is not explicitly modeled; public issue reports include voice degradation after 4-5 minutes | Official weights exist; official long-form usage is disabled. Transformers `main` support and unofficial converted weights now provide an experimental path | Repository/model metadata say MIT, while the model card says research-only and describes uses as “not intended or licensed”; obtain legal review before non-research deployment |
| VibeVoice Realtime 0.5B | Streaming single-speaker speech | Trained for English; nine other languages are explicitly experimental; no Danish | Supplied preset/reference voice | About ten minutes, single speaker | Microsoft repository, Python demo, Transformers model | MIT metadata with the same research-use warnings; not a multi-speaker podcast renderer |

Primary sources: [Chatterbox repository and V3 quickstart](https://github.com/resemble-ai/chatterbox),
[Chatterbox model card and files](https://huggingface.co/ResembleAI/chatterbox),
[Qwen3-TTS repository](https://github.com/QwenLM/Qwen3-TTS),
[Qwen3-TTS technical report](https://arxiv.org/abs/2601.15621),
[Dia repository](https://github.com/nari-labs/dia),
[Dia2 repository](https://github.com/nari-labs/dia2),
[Microsoft VibeVoice repository](https://github.com/microsoft/VibeVoice),
[Microsoft VibeVoice 1.5B model card](https://huggingface.co/microsoft/VibeVoice-1.5B),
[Microsoft VibeVoice Realtime model card](https://huggingface.co/microsoft/VibeVoice-Realtime-0.5B), and
[Transformers VibeVoice documentation](https://huggingface.co/docs/transformers/main/model_doc/vibevoice).

## 7. How Chatterbox should be integrated

### Use the upstream model directly behind a small owned service

For the MVP, wrap the upstream Python package in a dedicated, single-GPU worker
instead of making a community server part of the core architecture. Load V3
explicitly:

```python
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

model = ChatterboxMultilingualTTS.from_pretrained(
    device="cuda",
    t3_model="v3",
)
```

The `t3_model="v3"` argument is essential. In the current upstream source,
omitting it still selects the legacy V2 checkpoint. The upstream README calls
this out, and the [implementation maps `v3` to the V3 safetensors file](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py).
Pin both the package/commit and the selected checkpoint in episode provenance;
the repository version is 0.1.7, while the latest formal GitHub release remains
0.1.2, so a generic “latest Chatterbox” identifier is not reproducible.

The worker should expose the project's own provider contract rather than make
OpenAI's single-utterance schema the domain model. A minimal internal request
needs `text`, `language`, `voice_id`, generation parameters, and an idempotency
key. The outer podcast renderer should retain a distinct whole-dialogue method
for Dia/VibeVoice-like engines.

### Register and cache voices, do not re-encode them per turn

For each persistent host:

1. Store a clean, consented reference WAV, its language, transcript/source,
   checksum, and rights metadata separately from the semantic persona.
2. Prefer approximately ten seconds of clean speech in the target language.
   Chatterbox uses at most six seconds for its encoder prompt and ten seconds
   for its decoder conditionals. The publisher warns that a reference recorded
   in another language can transfer that accent; for Danish hosts use a Danish
   reference and `language_id="da"`.
3. Call `prepare_conditionals()` once and cache the resulting conditionals by
   `(model revision, voice id, reference hash, exaggeration)`. The upstream
   [`Conditionals` object can be saved and loaded](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py).
4. Treat the model instance as stateful. `generate()` mutates `model.conds`, and
   changing `exaggeration` mutates part of the cached condition. A shared model
   is therefore not safe for concurrent requests for different speakers without
   an adapter-level lock or a patch that passes conditionals explicitly. Start
   with one serialized GPU worker; add batching/parallel workers only after
   measuring memory and correctness.

### Chunk conservatively and add automatic quality control

The current upstream implementation invokes T3 with `max_new_tokens=1000` and
the speech tokenizer runs at 25 Hz. One call therefore has a theoretical ceiling
of about 40 seconds, and quality can fail before that ceiling. A podcast renderer
should normally synthesize one coherent short turn or sentence group at a time,
targeting roughly 8-25 seconds and splitting only at punctuation. It must also
exercise very short reactions: an open upstream report describes sequential
short-text CUDA failures, so `"Yes."`, laughter, and one-word interruptions are
important adversarial cases, not safe assumptions
([issue #201](https://github.com/resemble-ai/chatterbox/issues/201)).

After each clip, automatically check:

- duration against a text-length envelope;
- long leading/trailing silence and near-silent regions;
- clipping, loudness, and unexpected high-frequency/noise energy;
- ASR transcript agreement, repeated phrases, and missing final words;
- expected speaker similarity against the host reference.

Retry a failed clip with a recorded seed and bounded parameter changes, then
route persistent failures to manual review. These checks are justified by open
reports of early termination/repetition on a DGX Spark
([issue #519](https://github.com/resemble-ai/chatterbox/issues/519)), silent
failures near the generation ceiling ([issue #531](https://github.com/resemble-ai/chatterbox/issues/531)),
and delivery/pronunciation variation between otherwise matching generations
([issue #550](https://github.com/resemble-ai/chatterbox/issues/550)). These are
user reports rather than maintainer-confirmed failure rates; benchmark locally
instead of copying their percentages into a service-level objective.

The assembler should trim only pathological silence, preserve intentional
breaths, normalize loudness at the episode level, and control inter-turn gaps.
Chatterbox receives no previous dialogue audio or timing context, so natural
turn-taking remains the responsibility of the script, clip boundaries, and
post-processing.

### API and deployment choices

The upstream project provides a Python API, model download, and Gradio apps; it
does not provide an official REST or OpenAI-compatible server. The most visible
community option, [travisvn/chatterbox-tts-api](https://github.com/travisvn/chatterbox-tts-api),
adds FastAPI, Docker, voice management, and `/v1/audio/speech`, but is AGPL-3.0,
contains its own chunking behavior, and its current README warns about upstream
non-CUDA/multilingual breakage. It is useful reference code or an optional
deployment if AGPL and its V3 behavior are accepted, but it should not silently
become the project's TTS abstraction. A small owned wrapper is less code than
adapting around incompatible chunking and licensing later.

Current upstream requirements are Python 3.10+, with the README saying it was
tested on Python 3.11/Debian 11. The current
[package manifest](https://github.com/resemble-ai/chatterbox/blob/master/pyproject.toml)
pins PyTorch/torchaudio 2.6 for Python below 3.14 and Transformers 5.2. It
supports CUDA, CPU, and MPS in code, but publishes no V3 VRAM or GB10 throughput
figure. The V3 T3 checkpoint is about 2.14 GB and its vocoder about 1.06 GB on
the [model repository](https://huggingface.co/ResembleAI/chatterbox/tree/main);
that is artifact size, not peak VRAM. Measure cold load, warm RTF, peak unified
memory, and concurrent behavior on the actual DGX Spark.

Every generated Chatterbox file carries the upstream PerTh watermark. Preserve
it through assembly and record that fact in episode metadata. The upstream code
and weight repository identify the license as MIT
([license](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE)).

## 8. Can Chatterbox keep speakers consistent for 30-60 minutes?

**Unknown; there is not enough published evidence to answer yes.**

There is a plausible mechanism for voice identity consistency: the same cached
speaker embedding, prompt speech tokens, and decoder reference can be applied to
every clip. V3's publisher says it improves speaker similarity and accent
preservation and reduces hallucinations relative to V2. That is useful evidence
for choosing V3 over V2, but it is not an episode-scale test
([V3 model card](https://huggingface.co/ResembleAI/chatterbox/raw/main/README.md)).

It cannot synthesize an hour as one continuous context. At the current 40-second
hard ceiling, a 30-60-minute episode requires at least roughly 45-90 calls, and
real dialogue turns will usually require many more. Reusing conditionals can
anchor timbre, but it does not carry forward prosody, energy, microphone level,
or conversational timing. The open long-form issue reports above specifically
raise cross-generation style variation, pronunciations that change on repeated
text, occasional silence/repetition, and throughput degradation during many
continuous calls ([issue #352](https://github.com/resemble-ai/chatterbox/issues/352)).

The promotion gate should therefore require a complete 45-minute two-host
episode, not a demo clip. For each host, compare speaker embeddings for at least
ten clips from the first, middle, and final thirds against the reference and
against the other host. Blind listeners should separately rate voice identity,
delivery/style drift, loudness drift, pronunciation consistency, and whether
speaker A is ever mistaken for speaker B. Also regenerate edited clips on a new
process/day to test whether patches blend with an existing episode. Until that
passes, phrase the capability as “reference-conditioned per-turn consistency,”
not “consistent hosts across an hour.”

## 9. What Danish quality actually sounds like

**The repository proves support, not quality. Danish quality remains unknown
without a native listening benchmark.**

The official language table and tokenizer include `da`, and the
[first-party demo page](https://resemble-ai.github.io/chatterbox_demopage/)
contains one Danish sample. That sample is curated, short, and the page does not
identify it as a V3 long-form run. It cannot establish native pronunciation,
voice stability, handling of technical English inside Danish, or two-host
podcast quality. No V3 model card reports Danish WER, Danish MOS, a Danish
speaker-similarity score, or a 10-60-minute Danish evaluation. There is also no
dedicated Danish model in the six-model Single Language Pack.

Run a blind native-Danish test with at least these categories:

- ordinary conversational Danish in both male and female voices;
- compounds, vowel length, stress and stød-sensitive minimal cases;
- names, place names, dates, times, ordinals, decimals, currencies and units;
- acronyms and technical terms such as GPU, CUDA, Kubernetes, DGX Spark and
  Strix Halo inside otherwise Danish sentences;
- code-switching and English quotations;
- short reactions, questions, disagreement, jokes and emotionally marked turns;
- five repeated renders of selected lines to expose stochastic pronunciation
  and delivery drift;
- a complete 10-15-minute Danish episode plus a 45-minute stability episode.

Use at least three native Danish reviewers and hide model/parameter identity.
Record critical-word pass/fail, naturalness, accent nativeness, intelligibility,
speaker identity, conversational pacing, and listening fatigue. ASR WER can
flag omissions and repetitions, but it must not replace native judgment because
the ASR model has its own Danish errors. Compare Chatterbox V3 with the Danish
baselines already documented in
[the repository's Danish TTS recommendation](./danish-tts-recommendation.md).

No honest source-only answer can say that Chatterbox's Danish is “good enough.”
The result should be recorded as an audio benchmark artifact and a reviewer
scorecard, then used as the promotion decision.

## 10. How Qwen3-TTS compares for English

Qwen3-TTS is the strongest English challenger for **independent turn
synthesis**, especially when persistent fictional hosts and controlled delivery
matter. It should be benchmarked against Chatterbox V3 on the same English
episode rather than selected from demos.

### Advantages over Chatterbox for this project

- The released 12 Hz family offers 0.6B and 1.7B Base voice-cloning models,
  CustomVoice models with fixed named speaker IDs, and a 1.7B VoiceDesign model.
  English is explicitly supported across all of them
  ([released model table](https://github.com/QwenLM/Qwen3-TTS#released-models-description-and-download)).
- Voice Design accepts a natural-language description, while CustomVoice
  accepts per-utterance style instructions. This directly supports the idea of
  a skeptical, calm, middle-aged engineering host more richly than
  Chatterbox's two scalar controls.
- The Base model can create a reusable clone prompt once and reuse it for a
  batch or later calls. The official README also documents a useful
  **voice-design-then-clone** workflow: generate one canonical reference with
  VoiceDesign, freeze that audio, then use the Base model's reusable clone
  prompt. That is safer for a persistent host than asking VoiceDesign to invent
  the voice independently on every turn.
- The official evaluation reports English WER and speaker-similarity results,
  plus an internal long-speech set containing samples longer than ten minutes.
  The public README reports long-English WER for the 12 Hz 1.7B CustomVoice
  model, and the technical report says the long-form comparison evaluates
  content consistency. Chatterbox V3 publishes no comparable long-form table.
- The code and released model cards use Apache-2.0
  ([repository license](https://github.com/QwenLM/Qwen3-TTS/blob/main/LICENSE),
  [VoiceDesign model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)).

### Limitations and integration consequences

- **Danish is unsupported.** The official set is Chinese, English, Japanese,
  Korean, German, French, Russian, Portuguese, Spanish and Italian. The Python
  wrapper validates the selected model's language set and rejects unsupported
  values. A community discussion about training Danish is experimentation, not
  official support ([discussion #79](https://github.com/QwenLM/Qwen3-TTS/discussions/79)).
- It does not jointly render a conversation. `generate_custom_voice()`,
  `generate_voice_design()`, and `generate_voice_clone()` accept text items and
  return corresponding waveforms. Batching two speakers improves throughput but
  does not create cross-turn acoustic context. Dialogue pacing still belongs to
  the assembler.
- The >10-minute result is an internal WER benchmark, not proof of persistent
  host identity or enjoyable dialogue. It does not report start/middle/end
  voice drift, cross-speaker leakage, patch consistency, or listening fatigue.
- The official evaluation uses bfloat16 and `max_new_tokens=2048`. At roughly
  12.5 acoustic frames per second, that default is about 2.7 minutes of token
  budget. Longer generation is technically configurable, but memory, ending
  reliability, and quality must be measured. An open discussion reports
  occasional non-termination that became more frequent with longer text
  ([discussion #211](https://github.com/QwenLM/Qwen3-TTS/discussions/211)); treat
  it as an operational warning, not a measured upstream failure rate.
- Qwen publishes Python and Gradio paths and optional DashScope cloud APIs. It
  documents FlashAttention 2 as optional/recommended to reduce memory. As of the
  reviewed README, vLLM-Omni supports **offline** inference only and says online
  serving will come later. A production local endpoint still needs a wrapper.
- The 1.7B runtime is materially larger than Chatterbox's 500M T3 model, and the
  official project gives no universal VRAM figure. It recommends Python 3.12,
  bfloat16/float16 with FlashAttention, and a compatible CUDA GPU. Measure the
  full tokenizer/model high-water mark on GB10 rather than estimating from
  parameter count.

For the benchmark, use two strategies: the built-in English CustomVoice hosts
(`Ryan` and `Aiden`) as the lowest-operational-risk Qwen path, and two canonical
VoiceDesign references subsequently frozen through Base cloning. Do not render
every turn directly with a free-form VoiceDesign instruction and assume the
result is the same person.

## 11. Does Dia improve conversational realism enough for a separate backend?

**Possibly within short chunks, but there is no primary-source comparison that
justifies the extra backend yet. A controlled listening test is required.**

Dia is structurally different from Chatterbox and Qwen: it receives alternating
`[S1]` and `[S2]` text in one request and generates one dialogue waveform. It
can condition both speakers on audio and can emit non-verbal events such as
laughter, coughing, sighing and throat clearing. That gives it a real chance to
coordinate pauses, reactions and prosody across a turn boundary; it is not just
concatenating two independently synthesized clips
([Dia README](https://github.com/nari-labs/dia)).

Original Dia is nevertheless a poor whole-episode engine. It is English-only,
supports two alternating speakers, and explicitly says that output under about
five seconds sounds unnatural while input over about 20 seconds can become
unnaturally fast. The official RTX 4090 table reports about 4.4 GB VRAM and
roughly 1.5x real time in bfloat16 without compilation, but this is not a GB10
measurement. An open issue from a podcast chunking user reports that fixed seeds
did not preserve voices across calls
([issue #109](https://github.com/nari-labs/dia/issues/109)). Audio prompting is
therefore mandatory for a persistent host.

Dia2 is the current candidate to test. It expands generation to at most two
minutes, supports streaming and word timestamps, and accepts a prefix for each
speaker. Its own README warns that voice and quality vary per generation unless
the model is prefixed or fine-tuned. It is still English-only, and the official
runtime requires Python 3.10+, PyTorch 2.8+, and CUDA 12.8+ for the documented
path. A TTS server is listed under “Upcoming”; the available interfaces are a
Python API, CLI and Gradio app. There are no tagged releases, and the source
repository's last commit at the time of review was 2025-11-29. This is research
software, not a mature service.

Test Dia2 through a distinct method such as:

```python
async def synthesize_dialogue_segment(
    turns: list[DialogueTurn],
    speakers: list[SpeakerVoice],
    prior_audio_context: bytes | None,
) -> DialogueAudio:
    ...
```

Render 20-60 second two-speaker scenes containing interruptions in the script,
short acknowledgements, disagreement, a callback, a laugh, and a transition.
Use the same voices and equivalent script with Chatterbox and Qwen turn
rendering. Blind listeners should score natural turn boundaries, pause length,
prosodic response to the previous speaker, speaker stability, intelligibility,
non-verbal appropriateness, artifacts, and overall preference. Also measure how
well consecutive Dia2 segments join; a two-minute context does not solve a
15-60-minute episode.

Do not claim overlapping speech. Dia's README shows joint dialogue and
non-verbal tags, but does not document controllable overlap. VibeVoice is even
more explicit that its current long-form model does not model overlapping
speech. Scripted interruption can mean rapid turn-taking without actual
simultaneous voices.

Promote Dia2 only if it wins blind overall preference by a meaningful margin
and its prefix-conditioned speakers survive a full 10-15-minute assembled
episode. Otherwise, keep one turn-based backend plus good script generation and
post-processing; a second inference stack is not justified by occasional
laughter alone.

## VibeVoice status and implications

The handoff's statement that VibeVoice was removed is partly current and partly
stale:

- Microsoft did remove the original long-form TTS inference implementation on
  2025-09-05 after misuse, and its current
  [TTS documentation says installation and usage are disabled](https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-tts.md).
- Microsoft still publishes the MIT-tagged
  [VibeVoice-1.5B weights](https://huggingface.co/microsoft/VibeVoice-1.5B),
  and its repository still describes up to 90 minutes and four speakers. The
  model card limits official support to English and Chinese and says overlap is
  not modeled.
- Microsoft later released VibeVoice Realtime 0.5B, but it is a single-speaker,
  approximately ten-minute streaming model trained for English. It does not
  restore the multi-speaker podcast path.
- Hugging Face Transformers `main` added VibeVoice support on 2026-08-27. Its
  official documentation currently points to `vibevoice/VibeVoice-1.5B-hf` and
  `vibevoice/VibeVoice-7B-hf`, which are community/unofficial converted
  checkpoints, and requires installing Transformers from source until a stable
  release includes the code. This makes experimentation easier, but it is only
  days old as of this review and is not equivalent to Microsoft restoring and
  supporting the original inference stack.

VibeVoice deserves a post-MVP experiment because it is the only candidate here
whose intended unit can be a four-speaker, long-form podcast. Its technical
report and official model card claim speaker consistency and natural turn-taking
over long contexts. But the model card also documents random or unexpected
output risk, no explicit overlap, English/Chinese-only support, and a research
recommendation. An open issue reports cloned voice degradation after four to
five minutes ([issue #106](https://github.com/microsoft/VibeVoice/issues/106));
as with all issue evidence, reproduce it locally.

There is also a licensing-policy ambiguity. The repository and model metadata
say MIT, while the model card says the model is limited to research purposes
and calls several scenarios “not intended or licensed.” Dia/Dia2 similarly pair
Apache-2.0 with a stricter-sounding README disclaimer. This document is not legal
advice. A personal research benchmark fits the stated intent, but do not treat
the SPDX tag alone as clearance for a product or public feed.

## Required benchmark before selection

Use one frozen, source-grounded English transcript and one independently
natural Danish adaptation. Do not compare different scripts, because writing
quality will swamp acoustic differences.

### Phase A: 10-15 minute enjoyable-episode gate

Render the same two-host English episode with:

1. Chatterbox V3, turn by turn;
2. Qwen3-TTS 1.7B Base with frozen designed/cloned voices, turn by turn;
3. Dia2 in 20-60 second joint-dialogue segments;
4. optionally VibeVoice 1.5B as one long joint generation.

Render the Danish version with Chatterbox V3 and the existing Danish baselines.
Dia, Dia2, Qwen3-TTS and VibeVoice are not Danish candidates.

Record model/checkpoint hashes, reference hashes, text chunks, seeds,
parameters, retries, raw clips, assembled audio, generation logs, GPU/unified
memory high-water, cold/warm RTF, and failure reasons. Apply the same final
loudness target and codec to listening copies, while retaining untouched WAVs
for artifact analysis.

Blind reviewers should score:

- intelligibility and pronunciation;
- voice naturalness and identity;
- identity drift from first to last third;
- pacing inside a turn and between turns;
- whether responses sound acoustically related to the previous turn;
- non-verbal event appropriateness;
- artifacts, omissions, repetitions and hallucinated speech;
- listening fatigue and overall “would continue listening” preference.

### Phase B: 45-minute stability gate

Only Phase A winners proceed. Render a 45-minute episode or three contiguous
15-minute chapters. Sample clips from start/middle/end for blind identity tests,
speaker-embedding similarity and cross-speaker confusion. Restart the service
and patch several lines to test edit continuity. Run two repeated complete jobs
to expose stochastic failures and memory/throughput degradation.

### Promotion rule

- Promote Chatterbox for Danish only after native reviewers accept its full
  episode, not its curated demo.
- Prefer Qwen over Chatterbox for English only if listeners hear a meaningful
  quality/control gain that justifies its larger runtime.
- Add Dia2 only if joint generation clearly improves overall conversational
  preference and stable chunk joining is operationally acceptable.
- Keep VibeVoice experimental until a stable Transformers release or restored
  upstream path exists, its licensing position is reviewed, and the 45-minute
  run passes without unacceptable voice or content drift.

## Source audit notes

Repository activity at the review date was not inferred from star counts. The
latest visible commits were 2026-07-21 for Chatterbox, 2026-03-17 for Qwen3-TTS,
2025-11-19 for Dia, 2025-11-29 for Dia2, and active 2026 work in VibeVoice
(mostly ASR/realtime after long-form TTS removal). Chatterbox has a PyPI package
and old tagged release; Qwen3-TTS, Dia, Dia2 and VibeVoice have no conventional
GitHub release history suitable for pinning. Pin immutable commits and weight
revisions in any benchmark.

Additional primary references:

- [Chatterbox multilingual implementation](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py)
- [Chatterbox supported languages and V3 model card](https://huggingface.co/ResembleAI/chatterbox/raw/main/README.md)
- [Chatterbox MIT license](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE)
- [Qwen3-TTS Python inference wrapper](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py)
- [Qwen3-TTS 1.7B VoiceDesign card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)
- [Dia generation guidance and hardware table](https://github.com/nari-labs/dia/blob/main/README.md)
- [Dia Apache-2.0 license](https://github.com/nari-labs/dia/blob/main/LICENSE)
- [Dia2 limits, requirements and license](https://github.com/nari-labs/dia2/blob/main/README.md)
- [VibeVoice technical report](https://arxiv.org/abs/2508.19205)
- [VibeVoice long-form documentation and disabled usage notice](https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-tts.md)
- [VibeVoice MIT license](https://github.com/microsoft/VibeVoice/blob/main/LICENSE)
- [Transformers VibeVoice implementation documentation](https://huggingface.co/docs/transformers/main/model_doc/vibevoice)
