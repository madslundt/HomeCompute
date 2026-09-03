# Adopt-first decision: personalized podcast generator

**Research date:** 2026-09-02

**Question:** Should the project adopt Open Notebook or another product instead
of building a service for prompt-first, optionally source-grounded podcasts
across learning and entertainment formats?

**Method:** Current source, Compose files, releases, and open defects were
inspected. This is an implementation-fit assessment, not a listening test.

## Decision

**Do not build the custom wrapper yet. For the clarified product—a podcast can
start from only a topic or creative brief and may be educational, current,
fictional, comedic, or motivational—trial Jellypod first.** It is the closest
existing match to the product contract: prompt-only generation, optional web
research and sources, one to four reusable characters, exact 1–75 minute API
targets, script/segment editing, Danish voices, and hosted publishing.

Run **Wondercraft** beside it as the creative-production control. Wondercraft
accepts an unconstrained prompt, can generate podcasts, meditations, audiobooks,
and other audio, and provides selected/custom voices, music, delivery direction,
sound effects and a timeline editor. Its two-host Convo Mode is especially
relevant to comedy and entertainment. It is less convincing as the factual
research system: prompts may contain links, but the inspected API does not
promise Jellypod-style autonomous cited web research.

Keep **Open Notebook** as the self-hosted fallback when local models/privacy are
hard requirements or when most episodes are actually grounded in collected
sources. Its REST API can be made topic-first by placing a brief in `content`,
but the current web UI refuses generation until notebook content is selected,
and the product neither researches an arbitrary topic nor treats fiction,
comedy, sound design, or other creative production as first-class workflows.
The underlying `podcast-creator` library is more flexible than that UI: its own
Perplexity example passes `content=None` and puts the topic in `briefing`.

Treat **Google NotebookLM/Gemini Notebook** as a learning and source-grounded
control, not a general entertainment generator. It can discover sources from a
research question and run Fast or Deep Research, but generated artifacts remain
grounded in imported notebook sources. **ElevenLabs Studio/GenFM** remains a
strong speech/editor benchmark, but its podcast flow also requires source
content; it is not the cleanest idea-to-episode product.

This changes the **product fit and sequence** of the earlier recommendation,
not the underlying technical findings:

1. Jellypod covers substantially more of the clarified product without code:
   its Podcast Agent starts from a prompt, researches the web by default, and
   can use sources optionally; its API accepts an integer target from 1 to 75
   minutes; reusable characters carry name, backstory, and voice across shows.
2. Open Notebook already supplies source ingestion, profile management, a UI,
   REST submission/status/audio endpoints, persistence, and background jobs.
   Rebuilding those before trying the existing product is poor adopt-first
   practice. Its latest release is
   [v1.14.0 from 2026-07-21](https://github.com/lfnovo/open-notebook/releases/tag/v1.14.0),
   and current `main` remained active at the inspection date.
3. Open Notebook does **not** have exact duration control, factual verification,
   contextual/whole-dialogue speech, private RSS, or rerender-from-transcript.
   It still delegates generation to `podcast-creator`. Those gaps matter only
   if the generated episodes fail the actual listening and workflow bar.
4. Danish is not a trustworthy configuration-only path on current Open
   Notebook `main`: the application-owned prompt files shadow the fixed
   library prompts and omit the `language` variable. This is a reproducible,
   still-open defect, not a theoretical concern
   ([issue #1238](https://github.com/lfnovo/open-notebook/issues/1238)). A
   Danish-only instruction in the briefing is a reasonable no-code experiment,
   but not a production guarantee.
5. SurfSense now has the strongest stock self-hosted podcast workflow: an actual duration
   range and word budget, an approved brief, resumable segment rendering, and
   FFmpeg assembly. It is nevertheless a disproportionately large platform to
   deploy for one listener.
6. Podcastfy is the smallest runnable comparison, but its two-speaker tag
   format, approximate length modes, older core, and beta API make it a worse
   long-term adoption target.

The stop rule is important: **if Jellypod passes representative learning,
current-events, and creative gates, adopt it instead of building.** Its main
delivery caveat is privacy: Public shows (and legacy Unlisted shows) have RSS,
but a Private show has no RSS feed. If a private authenticated feed is
mandatory, export the MP3 through its API and add only the small private-RSS
publisher.

## Scope correction: sources are optional

The original analysis overweighted notebook ingestion because the first brief
looked like a “turn my material into audio” workflow. The clarified primitive is
instead an **episode request**: it may include a topic, question, premise, genre,
audience, characters, recurring format, desired research policy, optional
sources, and a duration. That changes the ranking.

| Candidate | Topic/brief only | Learning/current events | Fiction/comedy/entertainment | Reusable show identity | Source-optional verdict |
| --- | --- | --- | --- | --- | --- |
| **Jellypod** | **Native.** Prompt is the primary input; sources are optional. | **Strongest inspected fit.** The agent searches the web by default and cites discovered sources; exact 1–75 minute API target. | **Plausible, not proven by docs.** An open prompt, editable script/timeline, up to four characters, music, and an August 2026 audio-drama workflow give it the right primitives. Benchmark humor and dramatic delivery. | **Yes.** Workspace characters persist with name, backstory, voice/clone; a show stores up to four hosts and podcast defaults. | **Best first product trial.** |
| **Wondercraft** | **Native.** Its UI and API accept a simple or detailed prompt; links are optional. | Good for explainers, but no inspected guarantee of autonomous cited research. | **Strongest creative-production control.** General audio formats, natural two-host Convo Mode, delivery prompts, custom voices, music, sound effects, clip editing, and a timeline. | Partial. Voices and workspace assets persist; the API exposes voice IDs, but no equally strong typed recurring-character model was found. | **Best creative challenger.** |
| **Open Notebook** | **API workaround, not native UI.** REST requires non-empty `content`; the brief can be duplicated there. The UI requires selected notebook content. | Strong when the user already has sources; it does not autonomously browse/research a bare topic during podcast generation. | Technically steerable through briefing/personas, but its fixed outline/discussion prompts and clip concatenation are not an entertainment production environment. | Yes: 1–4 speaker profiles plus episode profiles. | **Self-hosted/source-heavy fallback, not default.** |
| **`podcast-creator`** | **Yes.** The library's official Perplexity example uses `content=None` and a topic briefing. | Can use Perplexity as both outline/transcript provider, but source provenance and a research plan are not explicit pipeline stages. | Custom prompts can reshape it, but sound design, script approval, timeline editing, and publishing must be built. | Yes: speaker and episode profiles. | Useful engine/exit path; not a finished product. |
| **ElevenLabs GenFM** | Not cleanly. The podcast API requires `source`; Studio expects a document, URL, or existing project. | Good source-to-script and excellent speech/editing benchmark. | Studio can render authored fiction and multi-character text, but GenFM podcast generation is still source-led and only conversation or bulletin. | Selected/cloned voices persist; podcast mode documents one host and one guest. | Speech backend/editor, not the primary orchestrator. |
| **Gemini Notebook** | Indirectly. Start from a research question, discover/import sources, then create an Audio Overview. | Strong zero-setup grounded control with Fast/Deep Research. | Poor fit: answers and artifacts are designed around notebook sources and controls/voices remain limited. | No reusable custom host identities comparable to Jellypod/Open Notebook. | Learning/research baseline only. |

Primary product evidence for this correction: [Jellypod product
overview](https://www.jellypod.com/docs/help/products/ai-podcasts), [Podcast
Agent](https://www.jellypod.com/docs/help/podcast-agent/how-the-podcast-agent-works),
[web search](https://www.jellypod.com/docs/help/podcast-agent/using-web-search),
[API duration and input contract](https://www.jellypod.com/docs/api),
[characters](https://www.jellypod.com/docs/help/podcasts-and-episodes/characters-and-voices),
[Danish voice release](https://www.jellypod.com/product-updates), and [RSS
behavior](https://www.jellypod.com/docs/help/publishing-and-distribution/your-rss-feed).
Wondercraft evidence: [prompt-first
quickstart](https://support.wondercraft.ai/articles/5881256467-quick-start),
[API capabilities](https://docs.wondercraft.ai/capabilities), [Convo Mode
API](https://docs.wondercraft.ai/api-reference/endpoint/convo_mode_ai_scripted),
and [Danish support](https://support.wondercraft.ai/articles/5237054727-what-languages-do-you-support).

### Content modes to test and route separately

| Mode | Expected path | Acceptance focus |
| --- | --- | --- |
| Learning / evergreen explainer | Jellypod prompt-only, optionally with sources | Correctness, teaching structure, remembered host identity and requested duration |
| Current events / news | Jellypod with its default web research | Source quality, citations, date awareness and separation of fact from interpretation |
| Source-grounded digest | Jellypod first; Gemini Notebook as the factual control; Open Notebook when self-hosting is required | Faithfulness to supplied material, omissions, claim traceability and privacy |
| Fiction / comedy / entertainment | Compare Jellypod with Wondercraft; expect Wondercraft to win when acting, music, effects or clip direction matter | Character continuity, dramatic/comic timing, emotional delivery and absence of unwanted factual framing |
| Meditation / motivation / narrative solo | Wondercraft creative control; Jellypod when series identity and publishing dominate | Pacing, performance direction, music bed and repeatability |

Do not use one factuality rubric for every mode. Current-events episodes need
fresh evidence and citations; fiction needs premise/character continuity and
must not be “corrected” into nonfiction. Jellypod's initial web search is on by
default and can only be disabled for follow-up edits, so the creative benchmark
must explicitly check whether that research behavior harms fictional prompts.

## What “configuration only” means here

Configuration includes deploying the project's published container(s), adding
credentials and model endpoints, creating episode/speaker profiles, selecting
voices, and supplying a per-episode briefing. It does not include editing
prompts, changing Python, replacing the audio combiner, or adding a sidecar
service. Running an existing TTS server is configuration; writing or adapting a
TTS server is code.

“Can generate” below means the code path can produce an MP3. It does not mean a
native Danish listener has accepted the voice, the dialogue sounds natural, or
the final duration lands inside the requested range. Those are benchmark
results that repository inspection cannot establish.

## Configuration-only fit

| Candidate | 10–15 minute English | 10–15 minute Danish | Personalization without code | Hard limit |
| --- | --- | --- | --- | --- |
| **Open Notebook** | **Conditional yes.** Configure 1–4 speaker profiles, a provider, and a detailed briefing. Segment count is only a coarse length dial. | **No reliable supported path on current `main`.** A Danish-capable TTS provider can be configured, but the known prompt-shadowing defect makes the profile language ineffective. A briefing workaround must be measured. | Speaker name, backstory, personality, voice and per-speaker model are typed. Audience, scene, required questions, format details and ending can be placed in `default_briefing`/`briefing_suffix`, but are not first-class fields. | No minute/word target or post-render duration correction; no private RSS. |
| **SurfSense** | **Closest to yes.** Its spec accepts a 10–15 minute range and its planner budgets 150 words/minute. | **Conditional yes with hosted speech.** `da` is accepted as a BCP-47 language and OpenAI/Azure catalog voices are treated as language-agnostic. Local Kokoro explicitly has no Danish mapping. Provider quality and Danish pronunciation remain unverified. | Typed language, style, 1–6 speaker slots/roles/voices, duration range and free-form focus. Audience, scene, deep persona behavior, must-answer questions and conclusion live in `focus`, not separate fields. | Still independent turn synthesis; 12,000-character source sample per drafting call; no evidence ledger/fact-check/private RSS. |
| **Podcastfy** | **Basic one-off only.** A target `word_count` and two roles can plausibly land near 10–15 minutes, but the project documents short form as 2–5 minutes and long form as roughly 20–30. `word_count` is explicitly only a target. | **Conditional basic yes.** `output_language` plus a Danish-capable hosted voice can be configured. The keyless Edge option is Microsoft's online service, not local speech. | Two roles, style, dialogue structure, language, engagement techniques and free-form instructions. No general speaker/persona registry or more-than-two-speaker renderer. | Fragile alternating `<Person1>/<Person2>` parser, approximate duration, no durable jobs, provenance, fact-check or private RSS. |
| **`podcast-creator` directly / custom wrapper** | Direct library use is configuration-only for an approximate MP3; the **wrapper is code**. | The 0.12.0 library has working language plumbing and can call a configured Danish-capable TTS endpoint, but quality is unverified. | 1–4 persistent speaker records plus episode profiles and free-form briefing. | Same coarse segment/turn length and per-turn MP3 assembly; the project must supply API, jobs, storage and delivery. |

## Hosted shortcuts

These products are not substitutes for a private/local requirement, but they
are important controls against unnecessary engineering.

| Candidate | What it can replace | Important control | Main reason not to adopt |
| --- | --- | --- | --- |
| **Jellypod** | Prompt/topic research, optional source ingestion, script generation/editing, persistent cast, speech, music/timeline, exact-duration API target, MP3, hosting and RSS. | Generate three episodes with the same cast: one researched explainer, one current-events briefing and one fictional/comedic format. Inspect citations and edit/rerender individual segments. | Cloud-only; credits; factual research still needs review; the creative ceiling is unproven; Private visibility disables the website, share links **and RSS**. |
| **Wondercraft** | Prompt-to-script/audio, two-host natural dialogue, general creative audio, persistent/custom voices, music/SFX, detailed delivery and timeline editing. | Use the same creative brief and voices as Jellypod, then score acting, comic timing, music/SFX integration and amount of manual editing. | Cloud-only; paid API; no inspected autonomous cited-research contract; API cannot perform the Studio's iterative/timeline edits. |
| **ElevenLabs Studio / GenFM** | Source import, podcast script generation, editable manuscript, voice assignment, long-form rendering, export, and an API. The current API accepts a conversation or bulletin, language, short/default/long duration, style/tone instructions, and host/guest voice IDs. | Run the benchmark source with chosen persistent voices. Review the generated script before converting it so script and audio quality can be judged separately. | Cloud data path and ongoing generation cost; only one host plus one guest in the documented podcast mode; duration is a coarse class rather than a minute target; no inspected claim-level grounding/verifier. GenFM requires a paid plan. |
| **Google NotebookLM / Gemini Notebook** | Source discovery, Fast/Deep Research, source ingestion and one-click grounded audio overviews with downloads. Google documents 80+ languages. | Start from the same learning/current-events question, review the discovered sources, then generate English and Danish overviews as factual-coverage baselines. | Cloud-only and source-bound; much less controllable: no reusable custom speaker identities/voices, structured personas, general creative-production contract, local models, or application API suitable for this system. |

Official product evidence: [ElevenLabs GenFM workflow](https://elevenlabs.io/docs/help-center/product/distribution-publishing/gen-fm/how-do-i-use-gen-fm),
[Studio overview](https://elevenlabs.io/docs/projects/audio-native),
[Create Podcast API](https://elevenlabs.io/docs/api-reference/studio/create-podcast),
[NotebookLM product help](https://support.google.com/notebooklm/answer/16164461?hl=en),
[Gemini Notebook source discovery and Deep Research](https://support.google.com/notebooklm/answer/16215270?hl=en),
and [Google's multilingual Audio Overview update](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebook-lm-audio-video-overviews-more-languages-longer-content/).

Primary code evidence:

- Open Notebook's actual schemas expose `language`, `default_briefing`,
  `num_segments`, and speaker dictionaries—not the “length target,” audience,
  tone, and focus fields suggested by parts of its user guide
  ([models.py](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/open_notebook/podcasts/models.py)).
  Its REST job and audio lifecycle is real
  ([router](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/api/routers/podcasts.py),
  [service](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/api/podcast_service.py)).
- Open Notebook resolves profiles and then calls `create_podcast()` from the
  standalone library
  ([podcast command](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/commands/podcast_commands.py)).
  Its [local-TTS guide](https://github.com/lfnovo/open-notebook/blob/9bd5f89c5985ffd277cfacece995a75c3ff2ea2a/docs/5-CONFIGURATION/local-tts.md)
  confirms configuration of OpenAI-compatible speech endpoints and distinct
  voices, but the documented Kokoro example is not a Danish solution.
- SurfSense's [`PodcastSpec`](https://github.com/MODSetter/SurfSense/blob/3448772bd3d5d439114f810ac5da8e5a86967917/surfsense_backend/app/podcasts/schemas/spec.py)
  stores a duration range, language, style and 1–6 speaker slots. Its
  [transcript planner](https://github.com/MODSetter/SurfSense/blob/3448772bd3d5d439114f810ac5da8e5a86967917/surfsense_backend/app/podcasts/generation/transcript/nodes.py)
  computes the midpoint budget at 150 words/minute; its
  [renderer](https://github.com/MODSetter/SurfSense/blob/3448772bd3d5d439114f810ac5da8e5a86967917/surfsense_backend/app/podcasts/rendering/renderer.py)
  caches and resumes independent turn clips before
  [FFmpeg concatenation](https://github.com/MODSetter/SurfSense/blob/3448772bd3d5d439114f810ac5da8e5a86967917/surfsense_backend/app/podcasts/rendering/merge.py).
  The [Kokoro adapter](https://github.com/MODSetter/SurfSense/blob/3448772bd3d5d439114f810ac5da8e5a86967917/surfsense_backend/app/podcasts/tts/adapters/kokoro.py)
  lists its languages and omits Danish.
- Podcastfy exposes the relevant fields in its
  [conversation configuration](https://github.com/souzatharsis/podcastfy/blob/053b54a0917e35edb2808049499292361ccaae7b/usage/conversation_custom.md),
  while its [parser](https://github.com/souzatharsis/podcastfy/blob/053b54a0917e35edb2808049499292361ccaae7b/podcastfy/tts/base.py)
  and [assembly](https://github.com/souzatharsis/podcastfy/blob/053b54a0917e35edb2808049499292361ccaae7b/podcastfy/text_to_speech.py)
  show the alternating two-person, clip-concatenation implementation.
- `podcast-creator` 0.12.0's
  [episode schema](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/episodes.py),
  [speaker schema](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/speakers.py), and
  [generation nodes](https://github.com/lfnovo/podcast-creator/blob/904da36cca12846e8c610fff5cab2972735b007d/src/podcast_creator/nodes.py)
  confirm that short/medium/long means 3/6/10 requested turns per outline
  segment, not minutes or words.

## Exact gaps that require code

### Open Notebook

These are not reasons to reject the pilot; they are the patch boundary if the
pilot is otherwise good:

1. **Reliable non-English generation.** Restore the language block in the two
   application prompt files (or stop shadowing the library prompts), and add a
   Danish regression fixture. The open report reproduced an English outline
   from a non-English profile and identifies the exact shadowing path
   ([#1238](https://github.com/lfnovo/open-notebook/issues/1238)).
2. **Duration contract.** Add target minutes/WPM, section word budgets, measured
   final duration, and transcript revision outside tolerance. Increasing
   `num_segments` or `max_tokens` is not a duration controller.
3. **Safe, resumable audio.** The library still assembles independently rendered
   MP3 turns. An open upstream change replaces MoviePy after reported long MP3
   truncation
   ([podcast-creator PR #36](https://github.com/lfnovo/podcast-creator/pull/36)).
   Open Notebook also cannot rerender an accepted manuscript with new voices
   without rerunning the LLM stages
   ([issue #934](https://github.com/lfnovo/open-notebook/issues/934)).
4. **Quality/provenance.** Add source-note/claim provenance, a transcript
   fact-check/rewrite stage, dialogue-quality checks, and either contextual turn
   synthesis or a whole-dialogue provider. No inspected Open Notebook code does
   these today.
5. **Delivery.** Add a small authenticated RSS publisher. Open Notebook streams
   and downloads MP3s; it does not publish the private feed required for normal
   iPhone podcast-client behavior.

### SurfSense

1. Add a local Danish TTS adapter/catalog entry or use a hosted provider.
2. Model persistent audience, persona behavior, speech style, required
   questions, and ending separately if free-form `focus` proves too weak.
3. Replace the 12,000-character source slice with evidence selection and add a
   fact-check/rewrite pass.
4. Add a whole-dialogue renderer if independent turn concatenation fails the
   listening gate.
5. Add private RSS.

### Podcastfy

Supporting more than two speakers requires changing the transcript contract,
parser, and renderer, not adding another config entry
([issue #130](https://github.com/souzatharsis/podcastfy/issues/130)). Robust
10–15 minute generation needs a real word/section budget rather than tuning
short/long form; a current long-form report shows the workflow producing only
one part ([issue #248](https://github.com/souzatharsis/podcastfy/issues/248)).
Persistent profiles, durable jobs, retries/resume, source provenance,
fact-checking, a general speech boundary, and RSS would all be project code.
Its beta FastAPI also writes request credentials into process-global environment
variables and has no queue or authentication
([fast_app.py](https://github.com/souzatharsis/podcastfy/blob/053b54a0917e35edb2808049499292361ccaae7b/podcastfy/api/fast_app.py)); do not expose it as a multi-user service unchanged.

## Operational footprint and exit cost

| Candidate | Operational footprint | Migration / exit cost |
| --- | --- | --- |
| **Open Notebook** | Default Compose is the app plus SurrealDB; a single-container image also bundles DB, API, worker and frontend. Local LLM/TTS add their own services. Persistent volumes hold notebooks, database and episode artifacts. | **Medium-low.** MP3, outline and transcript are normal artifacts and available through its API. Episode/profile records need export from SurrealDB. Generator exit is unusually easy because the implementation already calls the independent MIT `podcast-creator` package. |
| **SurfSense** | **High.** Its production Compose declares Postgres/pgvector, a migration job, Redis, SearXNG, an OpenSandbox server plus image, Caddy, backend, Celery worker, Celery beat, Zero cache, frontend, and multiple volumes before any external model service. See the [current Compose](https://github.com/MODSetter/SurfSense/blob/3448772bd3d5d439114f810ac5da8e5a86967917/docker/docker-compose.yml). | **Medium-high.** Podcast spec/transcript/audio are explicit and extractable, and the non-proprietary podcast module is Apache-2.0. Workspace sources, auth, jobs and UI are nevertheless embedded in the wider Postgres/object-store/Celery application model. |
| **Podcastfy** | **Low.** Python package/CLI plus FFmpeg and provider endpoints; optional single API process. No required database or queue. Its large dependency set includes document/browser and multiple provider clients even for a narrow run. | **Low.** Config, transcript and MP3 are files; there is little durable application state to migrate. The other side of that simplicity is that the project must add all service-grade behavior. |
| **Custom wrapper** | **Low initially, medium when honest.** A Python service plus chosen LLM/TTS endpoints looks small; durable jobs, storage, auth, metrics and RSS still have to exist somewhere. | **Low platform lock-in, high ownership.** The project owns the contract and can replace libraries, but it also owns every migration, retry and compatibility regression indefinitely. |

Maturity changes the risk profile. SurfSense is actively released
([v0.0.39, 2026-08-29](https://github.com/MODSetter/SurfSense/releases/tag/v0.0.39)),
but its rewritten podcast module is only a few months old. Open Notebook is
active but inherits `podcast-creator`, whose latest release is
[0.12.0 from 2026-03-03](https://github.com/lfnovo/podcast-creator/releases/tag/v0.12.0).
Podcastfy's latest tag is
[v0.4.0 from 2024-11-16](https://github.com/souzatharsis/podcastfy/releases/tag/v0.4.0),
and the inspected 2026 commits do not materially modernize its core renderer.

## Fastest credible path

### Phase 0: two-day, no-product-code adoption trial

1. In Jellypod, create the intended recurring host/character set and a private
   test show. Generate three 10–15 minute fixtures from prompts alone: a durable
   learning topic, a current-events briefing that requires web research, and a
   fictional/comedic or entertainment premise. Make one episode Danish.
2. Generate the creative fixture in Wondercraft with the same brief and nearest
   equivalent voices. Use its music, performance direction and timeline only
   when they materially improve the result; record the manual effort.
3. For the learning fixture, run Gemini Notebook as the grounded control after
   its research/source-discovery step. This tests factual coverage, not format
   breadth. Use ElevenLabs only as a speech/editor control if voice quality is
   still the primary uncertainty.
4. Record prompt-to-audio time, final duration, citations and unsupported claims,
   host consistency, Danish pronunciation, turn rhythm, acting/comic timing,
   revisions, provider cost and—most importantly—whether the listener wants the
   next episode.
5. Export MP3 and transcript from the winner. Confirm the API path and decide
   whether a Public RSS feed is acceptable; if not, test private MP3 retrieval
   before committing to a tiny authenticated RSS publisher.

Only run Open Notebook afterward if self-hosting/locality is a real gate or the
benchmark reveals that source-heavy episodes dominate. Run SurfSense only if
Open Notebook then misses duration/resume gates and its broader research
workspace is also wanted. Neither deployment is the cheapest first experiment
for a prompt-first, format-diverse product.

### Phase 1 decision

- **Jellypod passes across learning, current and creative fixtures:** adopt it.
  Add only private RSS if that delivery requirement cannot use Public/Unlisted.
- **Jellypod wins research/show continuity but Wondercraft wins entertainment:**
  use both intentionally, or use Jellypod for research/script/series state and
  Wondercraft for selected creative renders. Do not build merely to force a
  single vendor.
- **Cloud is unacceptable:** deploy Open Notebook as the source-heavy UI or use
  `podcast-creator` directly for topic-first generation. Patch the Open Notebook
  UI/API boundary rather than pretending notebook selection is a good episode
  request model.
- **Existing tools fail a repeated, high-value format:** only then build the thin
  orchestration layer, reusing a speech provider. Prove the missing creative or
  research capability before adding general UI, storage or publishing.

## Decision triggers

Adopt **Jellypod** when all of the following are true:

- topic/brief-only generation and optional web research cover the desired mix;
- the same characters remain recognizable across learning and entertainment;
- the 1–75 minute target is accurate enough in the tested range;
- Danish script and voice quality pass a native-listener check;
- script/segment revision is sufficient for editorial control;
- its cloud data path and credits are acceptable;
- Public/Unlisted RSS is acceptable, or private RSS is small enough to add from
  API-fetched finished media.

Adopt **Wondercraft**, alone or as a creative renderer, when acting, comedy,
music/SFX, delivery direction and timeline control matter more than autonomous
cited research or typed recurring characters.

Adopt **Open Notebook** when all of the following are true:

- source/notebook UI and background job history have real value;
- 1–4 speakers and prose briefing are expressive enough;
- final duration is within an agreed soft tolerance (suggested: 9–16.5 minutes
  for the initial 10–15 request);
- Danish can be made reliable with configuration or at most the known small
  prompt patch;
- independent-turn audio is enjoyable enough that whole-dialogue synthesis is
  not a release blocker.

Adopt **SurfSense** when duration range, approved brief, segment cache/resume,
and its research platform outweigh the cost of the full stack. Do not adopt it
solely because its podcast module is cleaner.

Use **Podcastfy** only when the product is definitively a two-speaker,
cloud-provider, stateless generator and the shortest setup matters more than
durable jobs, persistent hosts, exact duration and extension quality.

Build the **custom wrapper** only when a measured requirement forces it:

- neither Jellypod nor Wondercraft can express a required recurring format;
- hard duration tolerance or automatic transcript correction beyond Jellypod's
  1–75 minute target;
- structured episode requests that must be queried/validated rather than put
  in a briefing;
- native local Danish Chatterbox integration that does not work through an
  existing compatible endpoint;
- rerender-from-transcript and partial checkpoint recovery;
- whole-dialogue/adjacent-context speech;
- source-level claim provenance and factual rewrite;
- a narrow agent-first API is required and the notebook/research UI has no
  value.

## Bottom line

The earlier custom-wrapper recommendation was technically defensible but too
eager in sequencing, and the Open Notebook-first revision still assumed that a
podcast begins with notes or sources. **For the clarified prompt-first,
multi-genre product, trial Jellypod first and Wondercraft as the creative
control.** Open Notebook becomes the local/source-heavy fallback; Gemini
Notebook is the learning control; ElevenLabs is primarily the voice/editor
control. Custom code becomes justified only after representative episodes show
a repeatable gap that these products cannot cover.
