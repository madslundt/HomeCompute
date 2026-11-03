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

The pinned native endpoint uses cpp-httplib's default in-memory multipart parser
and does not set its own payload maximum; the authenticated adapter's configured
256-MiB cap is the limiting request bound. The native process is started without
`--convert`, so it reads the submitted WAV directly and does not spool an audio
copy under `/tmp`. The Compose 32-MiB `/tmp` tmpfs therefore does not constrain
raw WAV uploads. Keep `--convert` disabled for this candidate.

Default decoding uses automatic language detection, no prompt, `max_context=0`,
and the pinned `whisper.cpp` CLI sampling defaults: beam search with
`beam_size=5`, `best_of=5`, temperature `0`, temperature increment `0.2`,
entropy threshold `2.4`, logprob threshold `-1`, and `no_context=true`. The
upstream server alone defaults to greedy (`beam_size=-1`, `best_of=2`), so the
adapter sends explicit beam/best-of values to preserve CLI comparability.
Explicit `language`, `prompt`, `temperature`, `temperature_inc`,
`beam_size` (-1 or 1–16), `best_of` (1–16), `translate`,
`carry_initial_prompt`, `max_context`, `no_timestamps` and response format
values are forwarded. `text`, `json` and `verbose_json` formats are supported.
Unknown fields and unsupported values are rejected instead of silently ignored.
Word timestamp granularity returns 422:
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
`X-HomeCompute-STT-Runtime`, `X-HomeCompute-STT-Runtime-Commit`,
`X-HomeCompute-STT-HomeCompute-Revision`, and `X-HomeCompute-STT-Image-ID`
headers so each stored result can retain the exact model, upstream runtime,
HomeCompute source and built image provenance. The HomeCompute source revision
is baked into the image at build. The operator supplies the daemon's resolved
image ID at runtime after building; the adapter refuses an absent or malformed
revision/image identity.

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
export WHISPER_CPP_HOME_COMPUTE_REVISION="$(git rev-parse HEAD)"
export WHISPER_CPP_MODEL_FILE="$MODEL" WHISPER_CPP_API_KEY_FILE="$KEY"
python3 deploy/whisper-cpp-stt/verify_model.py "$MODEL"
docker compose -f deploy/whisper-cpp-stt/compose.yaml --profile candidate build
export WHISPER_CPP_IMAGE_ID="$(docker image inspect --format '{{.Id}}' homecompute/whisper-cpp-stt:1.9.1-large-v3-f16-hc1)"
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

## Opt-in native GB10 CUDA candidate

`Dockerfile.cuda` and `compose-cuda.yaml` define a separate qualification-only
ARM64 CUDA image. Its base is the locally verified NGC PyTorch image
`nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9`
(resolved on Spark to linux/arm64 image
`sha256:c0e6c0b168faa1be027564e9a004e842c342e22ea51903d5fc7b9008850398d0`).
The base includes CUDA Toolkit 13.1.115, CMake 3.31.6, and GCC 13.3. The
upstream v1.9.1 CMake source supports `121a-real` with CUDA 12.9 and newer;
the candidate pins that architecture explicitly. NVIDIA lists GB10 as compute
capability 12.1, and the inspected host driver is 580.178.04. CUDA 13.x's
documented Linux compatibility floor is R580. This supports building the
candidate, but the patched CUDA source has not yet been compiled on the target;
exact runtime compatibility, model load, memory use, quality, and latency
remain unverified until a guarded GPU qualification completes.

The CUDA adapter mode refuses to start unless `libcudart.so.13` reports a
visible device, and it does not pass `--no-gpu` to the pinned server. The CUDA
build applies `strict-gpu-buffer-proof-v1` to the exact pinned upstream source
files after verifying their SHA-256 digests. It refuses model initialization if
the requested GPU backend cannot initialize or if no model weight buffer was
actually allocated on a GPU backend. Native `/health` reports the resulting
GPU model-buffer byte count; the adapter and Compose healthcheck reject a CUDA
candidate reporting zero or CPU placement. This proves model weights were
placed on GPU memory, not that every operation or model buffer runs there.
The patch changes no model weights. Its upstream base commit, patcher checksum,
and image labels are pinned in `model-pin.json` and `Dockerfile.cuda`. CPU remains
the default mode and the separate CPU image is built with `GGML_CUDA=OFF`.
Neither Compose file publishes a host port or joins the production inference
network. The candidate has its own private Docker network, read-only model and
credential mounts, no Docker logs, one serialized request, and 4 CPU / 12 GiB
container bounds. It does not alter production aliases or relay routes.

Provision the exact pinned GGML artifact and a separate qualification key
outside Git before starting. Required Spark paths are
`/srv/gb10-ai/cache/whisper-cpp/ggml-large-v3.bin` and
`/etc/gb10-ai/secrets/whisper_cpp_api_key`; both must be regular root-owned,
group-10001, mode-0440 files. Candidate startup hashes and validates the model
before stopping the primary. The key is never shown by lifecycle status or
health output.

Use only the source-owned lifecycle from an exact reviewed release:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py prepare-cuda-candidate --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py cuda-candidate --revision FULL_SHA --max-audio-seconds 1200 --max-upload-bytes 40000000 --native-timeout-seconds 14400
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py cuda-candidate-status --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py cuda-candidate-rollback --revision FULL_SHA
```

`prepare-cuda-candidate` checks the native ARM64 host, local pinned base image,
GB10 compute capability and driver floor, then builds and verifies the image;
it does not attach a GPU or start a model. `cuda-candidate` requires a
successfully applied or legacy-retired full Whisper transaction, a healthy
pinned vLLM primary, and no active vLLM candidate. It validates the pinned
model/key, applies the existing intake gate and drain, stops only the exact
healthy primary, then starts this isolated candidate. The existing 12-GiB
pre-load and 10-percent post-load host headroom gates remain mandatory. A
synthetic authenticated smoke must pass before qualification is reported.
Always run `cuda-candidate-rollback` after qualification; it removes only the
journaled candidate and restores the exact stopped vLLM primary and restart
policy. The candidate is never promoted by these actions.

For the 1,122-second private qualification, pass bounded duration, upload, and
native timeout values explicitly as lifecycle options, for example the values
shown above. The service refuses inconsistent duration/upload limits and caps
the longform path at two hours / 256 MiB / four hours of native time.
The current production relay still caps requests at 60 seconds / 10,000,000
bytes and strips response headers, so this private candidate is not reachable
through the current full-recording client path. Do not claim a client route or
production readiness from a successful direct qualification.

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
