# Podcast alternatives and private iPhone delivery

Verified: 2026-09-02

Status: primary-source repository review; no project was installed or subjected
to a listening benchmark

## Decision summary

No newly found, active open-source project is a verified better overall fit
than the current `podcast-creator`-standalone hypothesis. The one material
change to the handoff is **SurfSense**: its podcast subsystem was rebuilt in
June 2026 and is now a serious technical challenger, not merely a broad
knowledge application with incidental podcast output. It is still a large
application to adopt for an audio-quality MVP, and its renderer still generates
and concatenates one TTS clip per dialogue turn.

For iPhone playback, do not deploy Audiobookshelf for the first version. After
the audio benchmark passes, publish one private/capability-scoped RSS feed and
follow it directly in Apple Podcasts. This needs only an HTTPS-served `feed.xml`
and episode files, not a podcast server or Apple Podcasts Connect submission.
Apple explicitly supports private, personalized, password-protected, and
authenticated feed URLs followed directly in the app, and recommends
`<itunes:block>` for private feeds. See [Apple's private RSS guidance](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners).

## Fit ranking

This ranking is for the requested personalized, source-grounded, multi-speaker
generator. It is not a popularity ranking. Activity dates are repository
activity, not evidence of production quality.

| Rank | Project | License | Activity | API | Locality | Fit verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [SurfSense](https://github.com/MODSetter/SurfSense) | Apache-2.0 for podcast code | [`v0.0.39`](https://github.com/MODSetter/SurfSense/releases/tag/v0.0.39), 2026-08-29 | Full REST lifecycle | Self-hosted; local Kokoro or hosted TTS; workspace LLM is configurable | Strongest complete implementation reviewed here, but too much application surface and still turn-concatenated audio |
| 2 | [Podcastfy](https://github.com/souzatharsis/podcastfy) | Apache-2.0 | Last tag [`v0.4.0`](https://github.com/souzatharsis/podcastfy/releases/tag/v0.4.0), 2024-11; core changes sparse after 2025 | Python; beta FastAPI | Local LLM possible; bundled Edge TTS is online; other TTS mostly hosted | Runnable comparison baseline, not the preferred foundation for persistent personas or dialogue rendering |
| 3 | [TTS Fun](https://github.com/sgb-io/tts-fun) | **No license file**; README says MIT; default weights non-commercial | Last push 2026-06-30; no tags | REST plus UI | Local Docker/Ollama/FishAudio | Closest newly found end-to-end prototype, but immature, weakly grounded, and reuse is legally unclear |
| 4 | [text-to-podcast](https://github.com/jessedc/text-to-podcast) | MIT | Code changes through 2026-08; no tags | CLI/agent protocol, no service API | Local Kokoro; chosen LLM harness may be local or hosted | Useful packaging reference; voices existing prose/dialogue rather than creating a personalized episode |
| 5 | [PersonaPod](https://github.com/treynorman/PersonaPod) | MIT | Last push 2026-03-23; no tags | No application API; calls local-model APIs | Local LLM/MaskGCT; publishing is public R2 by default | Reuse ideas only for recurring RSS ingestion, container swapping, and feed publication |
| 6 | [Hexu](https://github.com/broomva/hexu) | MIT | One commit, 2026-06-04; no tags | CLI, no service API | TTS adapters can be local; default LLM and default Edge TTS are online | Useful compact RSS/daily-briefing reference, not a mature base |

## Candidate evidence

### 1. SurfSense: reassess upward, but do not adopt blindly

The current implementation has a real podcast domain module. Its editable
[`PodcastSpec`](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/schemas/spec.py)
models language, format, 1-6 speaker slots, role, voice, a duration range, and a
free-form focus. The transcript workflow computes a word budget at 150 words
per minute, plans an outline, drafts it segment by segment, carries a recap, and
validates speaker-slot attribution; see
[`generation/transcript/nodes.py`](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/generation/transcript/nodes.py).
The HTTP surface supports create, inspect, update/approve brief, regenerate,
cancel, delete, voice listing, language listing, and voice previews; see
[`api/routes.py`](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/api/routes.py).

The TTS boundary is cleaner than Podcastfy's. `TextToSpeech.synthesize()` accepts
one explicit synthesis request, with adapters for
[local Kokoro](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/tts/adapters/kokoro.py)
and [LiteLLM speech providers](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/tts/adapters/litellm.py).
Rendering is concurrent, content-addressed, and resumable at segment level, then
FFmpeg-concatenated into MP3; see
[`rendering/renderer.py`](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/rendering/renderer.py)
and [`rendering/merge.py`](https://github.com/MODSetter/SurfSense/blob/main/surfsense_backend/app/podcasts/rendering/merge.py).
The repository's mixed license makes content outside
`surfsense_backend/app/proprietary/` Apache-2.0, so the podcast module is under
Apache-2.0; see the [root license](https://github.com/MODSetter/SurfSense/blob/main/LICENSE).

Limitations for this project:

- A speaker has name, role, and voice, but no structured semantic persona,
  conversational behavior, speech style, or persistence contract. Audience and
  scene can only be smuggled into `focus`.
- Grounding is instruction-only. The source is truncated to 12,000 characters
  per call, and there is no source-note, citation, or fact-check/rewrite stage in
  the podcast graph.
- The per-turn TTS port cannot express a Dia-like whole-dialogue renderer without
  adding a second provider abstraction.
- The local Kokoro adapter supports English, Spanish, French, Hindi, Italian,
  Japanese, Portuguese, and Chinese, not Danish. The open
  [multilingual Kokoro issue](https://github.com/MODSetter/SurfSense/issues/1440)
  is further reason to benchmark rather than trust language claims.
- The podcast code is recent: the TTS port and renderer landed on
  [2026-06-10](https://github.com/MODSetter/SurfSense/commit/75287020e1a109f0c4b2aef02ec45b82009978f6).
  Active maintenance is positive, but long-form operational history is short.

Recommendation: use SurfSense as an architecture/code-donor candidate and run
one thin end-to-end comparison. Do not deploy the full SurfSense stack unless
its source ingestion/UI is independently desired. In particular, borrow or
adapt its typed brief, speaker-slot contract, duration budgeting, lifecycle,
segment cache, and provider port; add persistent personas, provenance, a
fact-check pass, and whole-dialogue rendering.

### 2. Podcastfy: standalone, but narrower and older than its README suggests

Podcastfy is a standalone Python package; it is not coupled to Open Notebook.
Its [Apache-2.0 license](https://github.com/souzatharsis/podcastfy/blob/main/LICENSE),
[`client.generate_podcast`](https://github.com/souzatharsis/podcastfy/blob/main/podcastfy/client.py),
Docker files, Python configuration, and
[beta FastAPI](https://github.com/souzatharsis/podcastfy/blob/main/podcastfy/api/fast_app.py)
make it easy to trial independently. Conversation configuration exposes two
roles, style strings, structure, language, and TTS voice choices; see
[`conversation_config.yaml`](https://github.com/souzatharsis/podcastfy/blob/main/podcastfy/conversation_config.yaml).

The implementation is not a general multi-speaker dialogue engine. Its TTS
base parser looks specifically for alternating `<Person1>`/`<Person2>` pairs,
and the ordinary render path synthesizes each side separately and concatenates
the clips; see [`tts/base.py`](https://github.com/souzatharsis/podcastfy/blob/main/podcastfy/tts/base.py)
and [`text_to_speech.py`](https://github.com/souzatharsis/podcastfy/blob/main/podcastfy/text_to_speech.py).
The TTS factory is extensible, but its interface is awkwardly shaped around
`voice`, `voice2`, and model strings rather than a stable speaker/context
contract. The "local" Edge provider uses the `edge-tts` client, which talks to
Microsoft's online service; it is not offline TTS. See Podcastfy's
[`edge.py`](https://github.com/souzatharsis/podcastfy/blob/main/podcastfy/tts/providers/edge.py)
and the [edge-tts project description](https://github.com/rany2/edge-tts).

Maintenance is mixed. The repository was pushed in May 2026, but those latest
commits were README/support changes; the ordinary TTS assembly code last
changed in November 2024, the FastAPI file last materially changed in October
2025, and the latest tagged release remains
[`v0.4.0` from 2024-11-16](https://github.com/souzatharsis/podcastfy/releases/tag/v0.4.0).
Open defects include
[long-form generating one part](https://github.com/souzatharsis/podcastfy/issues/248),
[duplicate long-form content](https://github.com/souzatharsis/podcastfy/issues/202),
[local-model failure](https://github.com/souzatharsis/podcastfy/issues/215), and
[more-than-two-speaker requests](https://github.com/souzatharsis/podcastfy/issues/130).

Recommendation: keep it as a comparison baseline and a source-extraction
reference. Do not add the required persona, provenance, fact-checking,
whole-dialogue, and Danish TTS work on top of its two-speaker tag format unless
the benchmark shows an unexpected quality or integration advantage.

### 3. PersonaPod: a recurring single-host news job, not a dialogue system

PersonaPod's actual `create_episode()` path fetches one news RSS feed, generates
character summaries, creates one monologue/news segment, sends that text to one
MaskGCT voice reference, mixes optional background music, converts it to MP3,
uploads it, and rewrites a feed; see
[`utils/podcast.py`](https://github.com/treynorman/PersonaPod/blob/main/utils/podcast.py).
It uses an OpenAI-compatible llama.cpp endpoint for the LLM, but its TTS service
is a separate Gradio/MaskGCT container and the application itself has no REST
API. The R2 path writes `public-read` objects, so its default delivery is not a
private feed; see
[`utils/cloud.py`](https://github.com/treynorman/PersonaPod/blob/main/utils/cloud.py).

The project is MIT-licensed, small, and honest in calling itself a hobby project
in its [README](https://github.com/treynorman/PersonaPod). It has no releases and
no repository activity after 2026-03-23. Its tests are helper/integration-style
and do not demonstrate 30-60 minute stability or multi-speaker dialogue.

Recommendation: do not use it as the generator base. Its useful ideas are the
resource-constrained stop/start of LLM and TTS containers, simple FFmpeg
post-processing, and RSS publishing for a later recurring-news pipeline.

### 4. Newly found alternatives

#### TTS Fun

TTS Fun is the only newly found project that closely mirrors the requested
interaction: create an episode with target minutes and 1-4 named speakers, give
each a bio and cloned voice, ask a local Ollama model for a script, review/edit
turns, synthesize clips, and finalize audio. The endpoints are visible in its
single [FastAPI application](https://github.com/sgb-io/tts-fun/blob/master/web/main.py).

It is not a better foundation:

- Script generation is one broad prompt plus a recovery prompt, with no source
  ingestion, outline, audience schema, scene, goals, provenance, or fact check.
- Finalization inserts fixed silence between independently generated WAV clips.
- The repository has only a few commits, no releases, and no root `LICENSE`
  file. Its README says the app code is MIT, but a README statement is weaker
  and more ambiguous than an actual license grant. Treat reuse as blocked until
  the maintainer adds a license file.
- Its default FishAudio S1-mini weights are identified by its own
  [README](https://github.com/sgb-io/tts-fun#license) as CC-BY-NC-SA-4.0, which
  makes that default unsuitable for an unrestricted production dependency.

Recommendation: optionally run it as a UX/TTS comparator, not a code or model
dependency.

#### text-to-podcast

[`text-to-podcast`](https://github.com/jessedc/text-to-podcast) is MIT-licensed
and actively maintained. It has unusually useful deterministic gates around
URL/PDF/text ingestion, speech-friendly expansion, dialogue tag verification,
local Kokoro rendering, loudness mastering, and metadata-rich M4A packaging;
see its [pipeline documentation](https://github.com/jessedc/text-to-podcast#the-pipeline)
and [orchestrator](https://github.com/jessedc/text-to-podcast/blob/main/text-to-podcast/scripts/podcast.py).
It does not generate a new personalized dialogue: multi-voice mode expects an
existing labelled transcript. It also does not publish RSS.

Recommendation: study or reuse its verification and mastering ideas after the
dialogue pipeline exists. It does not answer question 12 as a replacement.

#### Hexu

Hexu is a compact MIT-licensed successor-style daily briefing project. It has
Gmail/Calendar/RSS fetchers, two hard-coded host slots, LiteLLM, several TTS
adapters, local/S3 storage, and an iTunes-compatible feed publisher; see its
[`README`](https://github.com/broomva/hexu) and
[`feed/publisher.py`](https://github.com/broomva/hexu/blob/main/src/hexu/feed/publisher.py).
However, it is a single-commit repository with no releases. Its default
Podcastfy/Edge speech path is online despite being keyless, its default LLM is
cloud Gemini, and its dialogue generator is a single-call two-host prompt with
no grounding verifier; see
[`dialogue/generator.py`](https://github.com/broomva/hexu/blob/main/src/hexu/dialogue/generator.py).

Recommendation: its feed/storage split is a useful small reference for later
delivery and recurring shows. Do not use it as the core generator.

## Answer to question 12

> Is there another active open-source project not listed here that is a better
> fit?

**No verified one among the current candidates inspected.** TTS Fun is the
closest unlisted end-to-end project, but its missing license file, restrictive
default model weights, immature history, and shallow script pipeline disqualify
it as a better foundation. `text-to-podcast` is stronger engineering in its
narrow domain but is an article/dialogue renderer, not a personalized podcast
planner. Hexu is a useful recurring-feed example, not a mature generator.

SurfSense, which was already listed, is the important challenger. Its June 2026
podcast rewrite may be a better **technical donor** than Podcastfy and deserves
a thin benchmark. It is not automatically a better **application choice** than
`podcast-creator` standalone because adopting SurfSense brings a much larger
research platform and still does not solve persistent personas, factual
verification, Danish, or holistic dialogue audio.

This is a scoped conclusion, not proof that no such repository exists. GitHub
search is incomplete, young projects can change quickly, and README activity is
not a substitute for running a 10-15 minute benchmark.

## Simplest private iPhone playback path

### Recommendation: direct private RSS in Apple Podcasts

Add delivery only after a benchmark episode is worth listening to. The minimum
runtime is:

```text
episode.mp3 + feed.xml
          -> small HTTPS endpoint/object storage
          -> private listener URL
          -> Apple Podcasts: Library > … > Follow a Show by URL
```

Apple says following a URL does not publish a show to the directory, and direct
feeds can automatically download, notify, and sync playback across Apple
devices. See [distribution guidance](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners)
and [feed testing steps](https://podcasters.apple.com/support/828-test-your-podcast).

The first feed publisher needs only:

- Stable show metadata and `<itunes:block>Yes</itunes:block>`.
- One `<item>` per episode with a stable, never-reused GUID.
- An absolute `<enclosure>` URL with byte length and MIME type.
- MP3 or AAC audio. Apple accepts both; MP3 is adequate for the MVP. See
  [Apple audio requirements](https://podcasters.apple.com/support/893-audio-requirements).
- HTTP `HEAD` and byte-range support so seeking/streaming work reliably. See
  [Apple RSS technical requirements](https://podcasters.apple.com/support/823-podcast-requirements).
- HTTPS from a trusted certificate is strongly preferred; see
  [Apple hosting guidance](https://podcasters.apple.com/support/826-find-a-hosting-solution).

For one listener, a long random listener token in the feed URL is the smallest
practical access mechanism. Treat it as a bearer credential: do not log it,
commit it, or put it in episode descriptions. The enclosure URLs must be
equally protected, either by the same capability path/token or short-lived
signed URLs that remain valid long enough for podcast downloads. A merely
unlisted but globally readable object is not strong privacy. If the content is
sensitive, put token validation in front of private storage and make the token
revocable. Apple documents authenticated feeds, but the exact authentication
scheme still needs an iPhone acceptance/refresh test before it is selected.

A LAN- or Tailscale-only URL can be used for an immediate device test, but
background refresh, multi-device sync, and behavior while the VPN is asleep are
deployment-specific and unverified. Do not make that the only delivery route
without a 24-hour refresh test.

### Why not Audiobookshelf now

[Audiobookshelf](https://github.com/advplyr/audiobookshelf) is a mature,
actively maintained GPL-3.0 audiobook/podcast server with users, permissions,
APIs, playback progress, and offline clients. Server release
[`v2.36.0`](https://github.com/advplyr/audiobookshelf/releases/tag/v2.36.0) was
published 2026-07-27, and its app also had a
[`v0.14.0-beta`](https://github.com/advplyr/audiobookshelf-app/releases/tag/v0.14.0-beta)
release in August 2026.

It is nevertheless the wrong first dependency:

- The project still labels the iOS app beta, and its current README says the
  TestFlight beta is full; see the
  [official repository](https://github.com/advplyr/audiobookshelf#ios-app-beta).
- Its "Open RSS Feed" is deliberately accessible without login, and server-side
  playback progress is not tracked for RSS listeners; see
  [Audiobookshelf's public-share documentation](https://audiobookshelf.org/docs/documentation/libraries/common-content/public-shares/).
  Therefore putting Apple Podcasts in front of an Audiobookshelf open feed adds
  a server but not stronger feed privacy.
- Using Audiobookshelf's authenticated native/web app is valuable only when its
  library, multi-user permissions, offline downloads, and progress sync are
  desired. It adds a database, media-library conventions, user management,
  reverse-proxy/WebSocket work, and ingestion automation that the single-user
  MVP does not need.

Revisit Audiobookshelf if the project grows into a private multi-user audio
library, if the iOS app becomes readily installable and reliable, or if the
user wants books and generated podcasts in one progress-synced service.

## Defer list

Defer these until the benchmark establishes enjoyable English and Danish
audio:

1. Audiobookshelf deployment and integration.
2. A custom iOS app or custom web player.
3. Multi-user authentication, per-user feeds, token rotation UI, and analytics.
4. Recurring Gmail/news/calendar ingestion and scheduling from PersonaPod/Hexu.
5. Chapters, transcript feeds, artwork generation, source-note web pages, and
   Podcasting 2.0 extensions.
6. A generalized podcast-hosting service. The first publisher should be a
   deterministic function that writes an episode file and a small RSS document.

Do not defer the provider boundary between turn-based TTS and whole-dialogue
TTS, source provenance in intermediate artifacts, or reproducible duration and
long-form checks. Those choices affect the core architecture and are expensive
to retrofit after the MVP.

## Confidence and required validation

- High confidence: repository licenses, current code shapes, tagged releases,
  Apple custom-URL support, and Audiobookshelf open-feed behavior.
- Medium confidence: relative integration effort; it is inferred from code
  structure and dependency surface, not a completed installation.
- Low confidence until measured: speech naturalness, 30-60 minute stability,
  Danish quality, memory/VRAM use, and Apple Podcasts refresh behavior behind a
  VPN or a specific authentication middleware.

Before promoting any candidate, run the same 10-15 minute transcript through
its real pipeline, record setup time and failures, inspect every network call,
and retain the final audio plus resource measurements. Repository inspection
cannot answer the listening-quality question.
