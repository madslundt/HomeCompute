# Open Notebook and podcast-creator foundation evaluation

**Research date:** 2026-09-02

**Scope:** Handoff questions 1–6: whether to use Open Notebook or
podcast-creator, profile expressiveness, coupling, TTS replaceability, and
functionality that must be added. This is a repository/code evaluation, not an
audio-quality benchmark.

**Code inspected:** Open Notebook at
[9bd5f89c](https://github.com/lfnovo/open-notebook/commit/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a)
and podcast-creator at
[904da36c](https://github.com/lfnovo/podcast-creator/commit/904da36cca12846e8c610fff5cab2972735b007d).

## Outcome

Use **podcast-creator standalone as the starting code, but not as an unchanged
production library**. Pin or fork version 0.12.0 behind a project-owned
orchestration API and refactor its speech/assembly boundary before investing in
UI, RSS, or scheduling.

Do **not** use Open Notebook as the MVP's core application. It is the stronger
complete research product and is worth running as a baseline, but its podcast
path ultimately calls podcast-creator 0.12.0. Its extra Next.js, FastAPI,
SurrealDB, source management, model registry, background jobs, and profile
translation do not improve dialogue rendering. The direct dependency and call
are in Open Notebook's
[package manifest](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/pyproject.toml#L39-L45)
and
[podcast command](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/commands/podcast_commands.py#L20-L24).

The important correction to the initial hypothesis is that podcast-creator is
a **useful script-workflow seed, not a ready high-quality podcast engine**. Its
stock path generates one MP3 per dialogue line and concatenates those clips. It
has no time/word budget, contextual or whole-dialogue renderer, fact-check
pass, provenance model, post-production policy, or service/job layer.
[Audio node](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/nodes.py#L167-L295),
[zero-gap issue #37](https://github.com/lfnovo/podcast-creator/issues/37),
[MP3 truncation PR #36](https://github.com/lfnovo/podcast-creator/pull/36)

## Repository and maturity snapshot

| Dimension | Open Notebook | podcast-creator | Consequence |
| --- | --- | --- | --- |
| Product | Full self-hosted NotebookLM-style research app | Pip-installable Python workflow plus optional Streamlit | The library is the cleaner compositional unit. |
| Inspected version | Package version 1.14.0; main has post-release changes | 0.12.0; main equals the release merge | Pin exact revisions. |
| Latest release | [v1.14.0, 2026-07-21](https://github.com/lfnovo/open-notebook/releases/tag/v1.14.0) | [v0.12.0, 2026-03-03](https://github.com/lfnovo/podcast-creator/releases/tag/v0.12.0) | Open Notebook has the stronger maintenance signal. |
| Latest commit inspected | 2026-09-02 | 2026-03-03; no later merge at the snapshot | Treat the small library as lightly maintained unless merges resume. |
| Scale at snapshot | About 601 tracked files and 929 GitHub commits | About 70 tracked files and 129 stars | The library is much easier to understand and adapt. |
| License | [MIT](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/LICENSE) | [MIT](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/LICENSE) | Both permit modification with notice; model/provider terms remain separate. |
| Tests run here | 83 targeted podcast/domain/path tests passed | Full suite: 107 tests passed | Unit confidence only; no live LLM/TTS or long audio was tested. |

Open Notebook's activity is real rather than a README-only impression. Its
history contains 133 commits in the inspected shallow history since 2026-07-01,
including podcast, provider, Docker, source-context, and security work.
Podcast-creator had concentrated retry, per-speaker TTS, and multilingual work
in February/March 2026, followed by no merged commit after March 3.
[Open Notebook commits](https://github.com/lfnovo/open-notebook/commits/main/),
[podcast-creator commits](https://github.com/lfnovo/podcast-creator/commits/main/),
[changelog](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/CHANGELOG.md)

## What the code actually does

### podcast-creator

The public create_podcast function resolves an optional episode profile, loads
a speaker profile, builds a PodcastState, and invokes one compiled LangGraph:

~~~text
START
  -> generate_outline
  -> generate_transcript (one LLM call per outline segment)
  -> generate_all_audio (one TTS call per dialogue turn, batched)
  -> combine_audio (MoviePy concatenation)
  -> END
~~~

Sources:
[graph and Python API](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/graph.py#L20-L61),
[state](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/state.py),
[nodes](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/nodes.py).

Verified useful behavior:

- Outline and transcript can use different providers, models, and config.
- Transcript generation is segment-by-segment. Each call receives the full
  source content, complete outline, and transcript generated so far.
- Speaker names are validated against the selected profile.
- TTS calls run concurrently in sequential batches, with per-speaker
  provider/model/config overrides.
- Outline JSON, transcript JSON, numbered MP3 clips, and the final MP3 remain
  available.
  [Artifact saving](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/graph.py#L173-L193)

Verified limitations:

- Segment length is only short, medium, or long. Code turns these into minimum
  counts of 3, 6, or 10 dialogue turns; there is no word, time, or final-audio
  budget.
  [Transcript node](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/nodes.py#L118-L147)
- Default maximum output is 3,000 tokens for outline and 5,000 per transcript
  segment. Config can override this because it is merged last, but duration
  remains absent.
  [Config merge](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/nodes.py#L28-L43),
  [issue #34](https://github.com/lfnovo/podcast-creator/issues/34)
- Repeating all source content and all prior transcript in every section call
  makes context/cost grow with episode length. There is no summarization,
  retrieval, evidence ledger, or section-level source allocation.
  [Issue #8](https://github.com/lfnovo/podcast-creator/issues/8)
- Multi-speaker means validated transcript rows rendered as independent clips.
  Cross-turn prosody, reactions, overlap, interruptions, and timing are not
  represented in the model.
- Audio errors are returned in-band as strings, and MoviePy may trust incorrect
  MP3 duration headers. The open ffmpeg PR reports 73–88-clip episodes being
  truncated during assembly.
  [Combiner](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/core.py#L259-L369),
  [PR #36](https://github.com/lfnovo/podcast-creator/pull/36)

The package installs independently on Python 3.10.6+ and has no Docker service
of its own. Runtime dependencies include Esperanto, ai-prompter, LangGraph,
content-core, MoviePy, Pydub, Pydantic, and retry/token helpers; Streamlit is
optional. It is not a REST server, durable worker, or database. Local LLM use
means pointing the provider layer at Ollama or another endpoint; the package
does not host models.
[Package manifest](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/pyproject.toml),
[README](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/README.md)

### Open Notebook

Open Notebook adds a full control plane:

- FastAPI endpoints submit jobs, return job IDs, list/get episodes, stream MP3,
  retry failures, and delete episodes.
  [Podcast API](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/api/routers/podcasts.py#L124-L177)
- SurrealDB persists episode and speaker profiles, source content, outline,
  transcript, audio path, and command link.
- Podcast generation accepts raw content or flattens full source text, insights,
  and notes from a notebook into one long string.
  [Generation service](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/api/podcast_service.py#L33-L86),
  [notebook context](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/open_notebook/domain/notebook.py#L70-L134)
- Its model/credential registry converts database profiles to podcast-creator
  dictionaries, configures the library, then calls create_podcast.
  [Adapter](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/commands/podcast_commands.py#L128-L308)

This provides useful product behavior but also adopts the whole
database/UI/worker lifecycle. Default Docker needs the app plus SurrealDB.
Source development needs Python 3.11–3.12, Node 18+, a separate worker, and the
frontend. The full-local example adds Ollama and Speaches; its project-authored
estimate is 8 GB RAM/20 GB disk/4 CPU cores minimum and 16+ GB RAM, 8+ GB VRAM,
50 GB disk recommended. Those are documentation estimates, not measurements
from this investigation.
[Default Compose](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/docker-compose.yml),
[full-local Compose](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/examples/docker-compose-full-local.yml),
[source install](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/docs/1-INSTALLATION/from-source.md)

## Answers to questions 1–6

### 1. Should we use Open Notebook directly?

**No, not as the MVP core. Run it once as an available-today baseline and keep
it as a possible later source-management integration.**

Why not:

1. Its podcast and audio engine is still podcast-creator.
2. Its generation request has episode profile, speaker profile, name, raw
   content/notebook, and briefing suffix—not typed scene, audience, goals,
   required questions, conclusion, timing, or provenance.
3. It adds infrastructure that does not prove listening quality.
4. Its adapter has drift. Open Notebook currently accepts 3–20 segments while
   podcast-creator validates 1–10, so values above ten pass the outer model and
   fail downstream.
   [Outer schema](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/open_notebook/podcasts/models.py#L36-L86),
   [inner schema](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/episodes.py#L8-L48)
5. Open
   [issue #1238](https://github.com/lfnovo/open-notebook/issues/1238)
   reproduces a Hebrew/Gemini run where Open Notebook prompt copies shadow the
   bundled library prompts, omit the language variable, and fail late. This is
   directly relevant to Danish. The downstream fix reported by the author was
   not merged at this snapshot.
6. Local TTS support is real but not equivalent to a qualified long-form path.
   Relevant open reports include
   [choppy Kokoro/Speaches audio #641](https://github.com/lfnovo/open-notebook/issues/641)
   and
   [Gemini TTS failure #837](https://github.com/lfnovo/open-notebook/issues/837).

Why keep it in view: it is MIT licensed, actively maintained, Docker-packaged,
and already supplies source ingestion, credentials, jobs, persistence, profile
CRUD, MP3 streaming, and REST automation. If research-library features later
dominate, integrating the improved generator into Open Notebook could be
cheaper than rebuilding that shell.

### 2. Should we use podcast-creator standalone?

**Yes, as a pinned, project-owned foundation.** It is independently usable with
profile files/dictionaries and one async Python call. There is no Open Notebook
import, SurrealDB model, FastAPI request, or Open Notebook path in the
repository; dependency points the other way.
[Python API](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/graph.py#L43-L193),
[configuration](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/config.py)

The caller must provide source text, profiles, provider endpoints/credentials,
output storage, job state, and an API. That is appropriate: orchestration can
run on the services node and call inference/TTS over APIs without machine
coupling.

Because the project has not merged since March and the fixed graph needs
refactoring, use a small fork pinned to 0.12.0 or reuse the few core modules in
a project-owned graph. Do not preserve its Streamlit UI merely for compatibility.

### 3. What must be added?

#### Before the quality MVP

1. **Typed EpisodeSpec:** topic, language, audience default/override, target
   minutes and WPM, scene, format template, speakers, goals, mandatory
   questions, tone, ending, sources, and requested artifacts. Profiles should
   be references plus overrides, not an opaque briefing.
2. **Separated persistent identities:** semantic persona, conversation behavior,
   voice identity, and provider-specific speech style. Switching TTS must not
   change who a host is.
3. **Duration planning and validation:** minutes × WPM word budget, section
   allocation, transcript word count, final audio measurement, and text
   revision when outside tolerance.
4. **Dialogue planning/checks:** monologue length, speaker balance, follow-ups,
   callbacks, vacuous agreement, repetitive mannerisms, and supported
   performance cues.
5. **Evidence/fact pipeline:** cited source notes, section evidence assignment,
   claim provenance, transcript fact-check/rewrite, and saved non-spoken source
   notes.
6. **Two speech interfaces:** turn synthesis and whole-dialogue synthesis, with
   capability discovery, voice/style references, adjacent context, sample
   format, timestamps, cancellation, and provider metadata.
7. **Real post-processing:** ffmpeg/PCM-aware assembly, normalized sample rate
   and channels, configurable silence/overlap, trimming, loudness/peak policy,
   timings, and typed failures. Adopt or reproduce
   [PR #36](https://github.com/lfnovo/podcast-creator/pull/36).
8. **Checkpoint/resume and stage APIs:** rerun TTS from an edited transcript
   without repeating research/LLM work. Open Notebook users request this in
   [issue #934](https://github.com/lfnovo/open-notebook/issues/934).
9. **Long-input control:** summarize/retrieve and allocate evidence once rather
   than repeat one huge source string in every prompt.
10. **Benchmark harness:** preserve transcript, seed, model/provider versions,
    voice inputs, timings, real-time factor, RAM/VRAM, retry/failure data, and
    human listening scores for a 10–15 minute episode.

#### In the application wrapper

- REST API, durable queue, cancellation/progress, idempotency, authentication.
- Persistent audience/speaker/format profiles and episode/artifact storage.
- MP3/M4A metadata, artwork, chapters, transcript, description, source notes.
- Private RSS only after audio quality passes.

### 4. Can current profiles represent the requested concepts?

| Concept | Current ability | Verdict |
| --- | --- | --- |
| Scene/setting | Only prose in briefing or a custom template | Promptable, not typed/queryable/validated. |
| Audience/knowledge | Only prose; stock prompt considers an audience mentioned in briefing | Cannot cleanly persist a default audience plus episode overrides. |
| Persistent speakers | Named 1–4 speaker profiles with name, backstory, personality, voice ID, and TTS overrides | Yes, but semantic persona and provider voice are coupled. |
| Approximate duration | Number of segments plus short/medium/long mapped to 3/6/10 turns | No reliable minute target. |
| Conversation styles/formats | Reusable briefing and replaceable prompt templates | Partly; no typed format/template constraints or validator. |

The exact schemas confirm this:
[episode profile](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/episodes.py#L8-L62),
[speaker profile](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/speakers.py#L7-L78).

Custom-template warning: Open Notebook notes that podcast-creator compiles
template source and warns not to pass user/profile text as Jinja code. Episode
requests must be data passed into fixed developer-authored templates.
[Security note](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/commands/podcast_commands.py#L273-L286)

### 5. How tightly coupled is podcast-creator to Open Notebook?

**It is not coupled to Open Notebook. Open Notebook is coupled to it.**

The library does have internal coupling that affects extension:

- Graph and concrete nodes are compiled at import time.
- LLM and TTS creation call Esperanto AIFactory directly inside nodes.
- Prompts assume Pydantic JSON and exact speaker names.
- Speech output is numbered MP3 files.
- Final assembly assumes MoviePy and that clips directory.
- Singleton configuration uses a working-directory priority cascade. This let
  Open Notebook prompt files shadow bundled language improvements in issue
  #1238.

Standalone adoption removes the large application dependency, but a new
renderer cannot simply be injected into create_podcast today.

### 6. Can its TTS layer be replaced cleanly?

**For another Esperanto-supported or strictly OpenAI-compatible turn-based TTS
server: mostly yes by configuration. For native Chatterbox integration or a
whole-dialogue backend such as Dia: no, not without refactoring.**

The favorable path:

- Profiles select provider, model, voice, and arbitrary TTS config.
- Individual speakers can override them.
- Base URL, API key, and remaining config reach Esperanto.
- Esperanto has an OpenAI-compatible TTS provider calling the audio/speech
  endpoint.

Sources:
[speaker config](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/speakers.py#L7-L50),
[TTS call](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/nodes.py#L243-L279),
[Esperanto compatible TTS](https://github.com/lfnovo/esperanto/blob/81cb45009265fe009a53a5b2c9de5d42a7d743b2/src/esperanto/providers/tts/openai_compatible.py),
[Open Notebook local-TTS guide](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/docs/5-CONFIGURATION/local-tts.md).

An OpenAI-compatible Chatterbox server is therefore the first integration to
try. Compatibility still must be tested for request fields, voice identifiers,
response format, timeout, concurrency, and long-text behavior.

The architectural obstacle:

- No project-level SpeechProvider protocol or injected provider.
- Only text + voice to one MP3 is represented; no adjacent-turn context, scene,
  duration, emotion plan, timing, or dialogue context.
- Every transcript row is synthesized independently.
- No whole-dialogue operation or renderer capability negotiation exists.

Do not add only a chatterbox string to a provider switch. Define a
TurnSpeechProvider and a DialogueSpeechProvider and let the graph select one
renderer for an episode. Keep Esperanto as the first turn provider, integrate
Chatterbox over a compatible server first, and allow native Chatterbox or
Dia-like renderers without forcing them into the per-turn loop.

## Reuse boundary

Reuse from podcast-creator:

- Outline/transcript Pydantic concepts and exact-name validation.
- Separate outline/transcript model selection.
- Segment-by-segment transcript generation as an initial strategy.
- Retry policy as a starting point.
- Trusted Jinja prompt packaging and profile loading.
- Intermediate artifact structure and bounded TTS parallelism ideas.

Reuse or imitate selectively from Open Notebook:

- REST job/status pattern.
- Episode/profile persistence and safe audio-path containment.
- Provider credential/model registry.
- Source ingestion as an optional upstream service.
- Docker packaging.

Replace immediately:

- Briefing-only request model.
- Segment-size/turn-count duration proxy.
- Flat all-source context.
- Hard-wired AIFactory TTS node.
- Per-turn-only speech contract.
- MoviePy MP3 concatenation and in-band error strings.
- Lack of fact checking, provenance, revision, and TTS-only rerun.

## Bottom line

The preferred hypothesis is validated narrowly: **podcast-creator is
independently usable and is the better of these two foundations for a focused
personalized-podcast MVP.** It is a good starting workflow for planning and
script generation. It is not yet the dialogue renderer or long-form audio
system required by the goal.

Keep Open Notebook as a comparison implementation and possible later
source/control-plane integration. The first milestone should be a pinned,
standalone podcast-creator-derived pipeline with a typed episode spec, duration
budget, evidence/revision stages, injectable turn and dialogue speech
providers, ffmpeg post-processing, and a repeatable 10–15 minute benchmark.
Only after that episode is genuinely enjoyable should the project decide
between a small bespoke shell and reintegration into Open Notebook.

## Verification record

~~~text
podcast-creator:
  uv run pytest -q
  107 passed in 10.15s

Open Notebook targeted podcast/domain/path tests:
  83 passed in 11.88s
  two dependency deprecation warnings
~~~

These runs validate checked-in unit behavior on macOS arm64. They do not
validate live providers, CUDA, Chatterbox, Danish pronunciation, speaker
consistency, real-provider MP3 assembly, or 30–60 minute stability. Those
remain benchmark questions.
