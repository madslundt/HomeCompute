# Opt-in whisper.cpp CPU qualification candidate

This is an isolated, opt-in CPU candidate for full Whisper Large-v3. It adds no
production alias, host port, backend route, GPU workload, model download or
automatic fallback. The normal Whisper lifecycle and vLLM primary remain
separately owned. Candidate startup is a manually invoked Compose profile; the
runtime has no external network, uses a read-only model/key mount, and serializes
inference.

## Immutable runtime and artifact

The builder fetches the official `ggml-org/whisper.cpp` repository at tag
`v1.9.1`, verified as commit
`f049fff95a089aa9969deb009cdd4892b3e74916`. It builds the upstream native HTTP
server with CPU/OpenMP support, `GGML_NATIVE=OFF`, and CUDA/Metal disabled. The
Python 3.12 Bookworm build/runtime base is pinned to the Linux AMD64 manifest
digest in `Dockerfile`; Debian build packages are fetched only during an
explicit image build. Runtime inference performs no package, source or model
downloads.

The required external model is the unquantized F16 GGML conversion
`ggerganov/whisper.cpp@c521a4b02f422512d734391fdf08bb08c0862f68`, file
`ggml-large-v3.bin`, size 3,095,033,483 bytes, SHA-256
`64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2`. It is a
conversion of full `openai/whisper-large-v3`, not a byte-identical copy of the
Hugging Face safetensors. `verify_model.py` checks the digest and reads the
GGML header: magic, vocabulary 51,866, 32 encoder layers, 32 decoder layers,
128 mel bins and F16 type. Turbo, quantized or changed files fail closed.
Provision this model outside Git and mount it read-only.

## API and limits

The adapter serves only `POST /v1/audio/transcriptions`, authenticated
`GET /v1/models`, and a private-network `/health` check. The Compose project has
no published ports. The HTTP adapter requires one Bearer token from a separate
root-owned key file mounted read-only; it never logs request bodies, prompts,
responses, file names, authorization values or exceptions. It disables
whisper.cpp stdout/stderr and Docker logs. The adapter listens only on the
candidate's internal Docker network.

Input must be exactly mono 16 kHz PCM16 WAV. The adapter does not transcode or
truncate. Default limits are 180 seconds and 6,000,000 uploaded bytes. Explicit
bounded settings can raise the maximum to 7,200 seconds and 256 MiB; the upload
byte limit must be large enough for the selected WAV duration. The server reads
the complete bounded request into memory, so larger limits require separate
host memory and latency qualification. Compose caps the service at four CPUs,
12 GiB RAM and 128 processes. A single inference is admitted at a time. The
native call deadline is bounded to four hours; its default is 15 minutes.

Default decoding uses automatic language detection, no prompt, and
`max_context=0`. Explicit `language`, `prompt`, `temperature`,
`temperature_inc`, `translate`, `carry_initial_prompt`, `max_context`,
`no_timestamps` and response format values are forwarded. `text`, `json` and
`verbose_json` formats are supported. Unknown fields and unsupported values are
rejected instead of silently ignored. Word timestamp granularity returns 422:
whisper.cpp's token records are not canonical word timings, and this service
does not label them as such.

The `verbose_json` response preserves the native segment boundaries and times
in seconds relative to this request. It adds `kind: "segment"` and exclusive
`start_offset` / `end_offset` character anchors only when the sanitized native
segment serialization exactly matches the returned top-level text. It strips
whisper special-token spellings from returned text, preserves native segment
times without clamping, and rejects out-of-range timestamps. If text and
segments do not serialize identically, it withholds character anchors rather
than inventing a mapping. It removes the upstream `words` field because its
pieces are model tokens, not a validated word alignment. Source-recording
offsets and ownership across uploads belong to the client; this service reports
request-local coordinates only. Successful transcriptions also include
`X-HomeCompute-STT-Model-SHA256`, `X-HomeCompute-STT-Model-Snapshot`,
`X-HomeCompute-STT-Runtime`, and `X-HomeCompute-STT-Runtime-Commit` headers so
each stored result can retain the exact model and runtime provenance.

## Build and private core qualification

The image is for `linux/amd64` CPU qualification (home-core). Do not build/run
it on Spark or add it to the production Whisper Compose project. First stage a
verified model and a candidate-only API key outside the repository. The key
file must be root-owned, group `10001`, mode `0440`; the model must be a regular
file readable by UID/GID `10001`. Neither value is committed. The key should be
a dedicated private qualification credential, not copied from the live backend.

Example on home-core from a reviewed release checkout, with operator-chosen
private file paths:

```bash
MODEL=/srv/private/whisper-cpp/ggml-large-v3.bin
KEY=/srv/private/whisper-cpp/qualification-api-key
python3 deploy/whisper-cpp-stt/verify_model.py "$MODEL"
WHISPER_CPP_MODEL_FILE="$MODEL" WHISPER_CPP_API_KEY_FILE="$KEY" \
  docker compose -f deploy/whisper-cpp-stt/compose.yaml --profile candidate build
WHISPER_CPP_MODEL_FILE="$MODEL" WHISPER_CPP_API_KEY_FILE="$KEY" \
  docker compose -f deploy/whisper-cpp-stt/compose.yaml --profile candidate up -d
docker compose -f deploy/whisper-cpp-stt/compose.yaml --profile candidate ps
```

This only builds and starts an isolated CPU candidate; it does not update the
gateway, client settings, aliases or any transcript. It binds no host port.
Run a later approved private qualification by invoking the authenticated
candidate through `docker exec` inside its container, with an operator-staged
audio file. Confirm the response, hashes and runtime/model health metadata
before considering further work. Then remove only this isolated project:

```bash
docker compose -f deploy/whisper-cpp-stt/compose.yaml --profile candidate down --remove-orphans
```

For the 1,122.28-second source qualification, explicitly set
`WHISPER_CPP_MAX_AUDIO_SECONDS=1200`,
`WHISPER_CPP_MAX_UPLOAD_BYTES=40000000`, and a reviewed bounded
`WHISPER_CPP_NATIVE_TIMEOUT_SECONDS` (up to 14,400 seconds). A two-hour
experimental request needs an explicit 7,200-second / 256-MiB setting and its
own host memory, deadline and latency gate. The 12-GiB container limit remains
in force. No claim is made that core CPU can finish either long request within
the deadline. A limit breach returns an error; requests are never silently
shortened.

The portable synthetic contract and refusal tests run with:

```bash
python3 tests/whisper-cpp-stt-test.py
```

They test the real adapter over HTTP with a synthetic native response, prompt
and option forwarding, format rejection, authentication, exact segment/text
anchors, bounds, and model header/checksum refusal. They do not measure model
quality, CPU latency or host resource use. Quality qualification must use the
frozen private request/source artifacts; never commit recordings, prompts,
transcripts or model weights.
