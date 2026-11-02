# Speech API contract

Applications use `https://ai.home.arpa/v1` through Caddy and LiteLLM. Inference
remains local. Use separate alias-scoped virtual keys for new clients. The
existing private-network credential and keyless LAN compatibility behavior are
preserved; this rollout does not create a public internet service.

| Capability | Endpoint | Model | Contract |
| --- | --- | --- | --- |
| STT | `POST /v1/audio/transcriptions` | `whisper` | Multipart `file` and `model`; optional `language`; JSON `text` response |
| Danish TTS | `POST /v1/audio/speech` | `tts-danish` | JSON `model`, `input`, `voice: danish-default`, `response_format: wav`; binary WAV response |
| English TTS | Same speech endpoint | `tts-english` | Same contract with `voice: english-default` |

`stt-danish` and `whisper-turbo` remain compatibility aliases for full Large-v3.
Both TTS aliases select the same bilingual OmniVoice backend; the explicit
`voice` field selects the output language. Existing clients may retain model
`omnivoice`. Plapre is retired and its route is disabled. MOSS/Piper remains an
independent Danish Wyoming fallback. Candidate state and voice/concurrent-load
qualification gates remain in force; an API smoke pass is not model promotion.

STT accepts at most 10,000,000 upload bytes and 60 seconds of decoded audio.
Phone/container formats supported by the pinned FFmpeg decoder are normalized
in memory. Use `language: da` or `en` when known. Optional prompt, decoding and
timestamp parameters pass through to Whisper; support must be checked against
the pinned backend. JSON text is the baseline. Timestamps are not diarization.

TTS requires nonempty text of at most 2,000 characters. Only the two advertised
voices, WAV, and speed 1.0 are supported. Unsupported fields, voices and formats
return 400. The adapter admits one request at a time and rejects excess work
with 503. Its synthesis timeout is 120 seconds; the gateway has a 300-second
request budget. Request/upstream errors are sanitized. The adapter buffers the
complete WAV. It does not advertise progressive playback, partial transcripts,
or a bidirectional realtime session. Long recordings belong in the consumer's
bounded chunking/job workflow, with original audio and provenance outside Git.

Home Assistant uses Wyoming through the separately owned OmniVoice adapters.
Its action authority remains unchanged. The authenticated HTTP adapter is
private to Docker; the Wyoming LAN publication retains the existing HA-only
firewall. Existing externally provisioned secrets remain authoritative. Logs
exclude input text, audio, transcripts, and exception payloads, including in the
retained MOSS fallback wrapper.

## Deployment, verification and rollback

Publish reviewed source on origin/master. Use the normal
`scripts/homecompute deploy home-core --revision FULL_SHA` for the gateway and
MOSS privacy wrapper. Apply the [OmniVoice lifecycle](../deploy/omnivoice/README.md)
for its independently owned core adapters and Spark engine. Never activate or
resize the Spark engine implicitly as part of a gateway alias change.

After OmniVoice readiness is verified, run on home-core from that release:

```sh
sudo python3 scripts/setup-network-inference.py --enable-omnivoice
sudo python3 scripts/setup-network-inference.py --enable-speech-aliases
sudo python3 scripts/speech-api-smoke.py
```

The alias migration adds only `tts-danish` and `tts-english`, preserving existing
Whisper, embedding, video and other reviewed permissions. It requires the
selected OmniVoice scope and is idempotent. Other client keys are not broadened.
Registry inputs own aliases; run `python3 scripts/model_registry.py render` and
`check` after changing them.

The smoke verifies the TLS certificate and authenticates through Caddy/LiteLLM,
synthesizes fixed Danish/English phrases, and transcribes the Danish WAV. It
prints only booleans, duration and elapsed times; it retains no audio or text.
Inspect `status`, `services`, `doctor`, and `drift` separately. Monitor speech
latency and failures without recording private content.

Retain the previous release and images. Gateway code rollback uses the normal
full-SHA deployment. OmniVoice rollback uses its dedicated lifecycle and saved
source-mount receipt; neither rollback reverses application data or key scopes.
Remove only the two new aliases through LiteLLM's key API when reverting their
scope migration, preserving the remaining model list. Never recreate retired
Plapre implicitly. Keep MOSS/Piper and all durable data. Realtime voice and
streaming have separate qualification gates.
