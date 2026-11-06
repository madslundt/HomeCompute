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
It also sends `max_len=-1`: the server's HTTP wrapper maps omitted/zero values
to a 60-character segment wrap, while the native decoder wraps only when the
value is positive. This avoids HTTP-only forced word splits. Both image builds
guard these pinned source semantics before compiling.
Explicit `language`, `prompt`, `temperature`, `temperature_inc`,
`beam_size` (-1 or 1–16), `best_of` (1–16), `translate`,
`carry_initial_prompt`, `max_context`, `max_len` (-1 or 1–1000),
`no_timestamps` and response format
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

`Dockerfile.cuda` and `compose-cuda.yaml` define an opt-in, promotion-capable
ARM64 CUDA image. Its base is the locally verified NGC PyTorch image
`nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9`
(resolved on Spark to linux/arm64 image
`sha256:c0e6c0b168faa1be027564e9a004e842c342e22ea51903d5fc7b9008850398d0`).
The base includes CUDA Toolkit 13.1.115, CMake 3.31.6, and GCC 13.3. The
upstream v1.9.1 CMake source supports `121a-real` with CUDA 12.9 and newer;
the candidate pins that architecture explicitly. NVIDIA lists GB10 as compute
capability 12.1, and the inspected host driver is 580.178.04. CUDA 13.x's
documented Linux compatibility floor is R580. This supports building the
candidate. The exact patched CUDA source, target runtime compatibility, model
load, memory use, quality, and latency have since been verified by the guarded
private full-recording qualification recorded below. Permanent production
promotion and route activation are still pending source review and operator
action.

The CUDA adapter mode refuses to start unless `libcudart.so.13` reports a
visible device, and it does not pass `--no-gpu` to the pinned server. The CUDA
build applies `strict-gpu-request-determinism-v3` to the exact pinned upstream source
files after verifying their SHA-256 digests. It refuses model initialization if
the requested GPU backend cannot initialize or if no model weight buffer was
actually allocated on a GPU backend. Native `/health` reports the resulting
GPU model-buffer byte count; the adapter and Compose healthcheck reject a CUDA
candidate reporting zero or CPU placement. This proves model weights were
placed on GPU memory, not that every operation or model buffer runs there. The
CUDA native health handler also try-locks the same mutex held across native
inference and reports `inference_busy`; the adapter refreshes that probe on
every health request and treats a missing or invalid result as busy. This keeps
candidate rollback from stopping whisper.cpp after the HTTP client times out
while the native decoder continues working; CUDA request admission also rejects
new work while that native lock is held or its state cannot be confirmed. The
CPU image is unpatched and
reports only adapter admission activity, so it is not used by this CUDA
rollback idle gate.
The patch also fixes two native sampling defects. Beam search now takes the
distinct highest-ranked token candidates, with token ID breaking equal-logit
ties, instead of randomly drawing candidates after sorting them. Each request
also resets decoder zero's random generator to its original seed, as the
upstream request initialization already does for the other decoders. This
prevents earlier requests from changing later stochastic greedy/fallback
sampling. Model weights, language detection, decoding defaults, and timestamp
probability fields are unchanged. The `hc4` image remains a short-input
reliability candidate; its long-input qualification found omitted speech and
Danish translated into English. Both defects were also reproduced with the
preceding `hc3` runtime. The `hc5` adapter candidate below requires a new full
qualification; the previously qualified `hc3` image below retains its
exact identity until a separately validated promotion.

The patch changes no model weights. Its upstream base commit, patcher checksum,
and image labels are pinned in `model-pin.json` and `Dockerfile.cuda`. CPU remains
the default mode and the separate CPU image is built with `GGML_CUDA=OFF`.
Neither Compose file publishes a host port. Qualification stays on the
candidate's isolated network. Only the exact-image promotion applies
`compose-cuda-production.yaml` to additionally join the existing private
inference network with `whisper-primary` and `whisper-cpp-recording` aliases.
The lifecycle stops the original before attaching it and closes intake while
switching, so the aliases never direct production calls to two GPU models at
once. The service has read-only model and credential mounts, no Docker logs,
one serialized request, and 4 CPU / 12 GiB container bounds. The NVIDIA runtime mounts host driver libraries at container
start with the `compute,utility` capabilities; the image retains those NVIDIA
library search paths. The build-time ELF check allows only the expected
unresolved `libcuda.so.1` host-driver dependency and requires every other
dependency plus `libggml-cuda` to resolve. It also compiles a tiny `cuInit`
probe against the toolkit's link-time stub and inspects its dynamic dependencies
without running it or loading a GPU. The probe confirms the build host does not
provide the runtime driver library. At service startup, `libcuda.so.1`, CUDA
runtime loading and device enumeration must all succeed; native readiness then
requires CUDA backend initialization, GPU model-buffer proof, and the native
mutex health signal. It does not alter production aliases or relay routes.

`prepare-cuda-candidate` provisions the exact pinned GGML artifact and a
separate qualification key outside Git when they are absent. The model is
downloaded only from the immutable Hugging Face snapshot URL in `model-pin.json`,
streamed to a same-filesystem temporary file, checked for its exact byte count,
SHA-256 and full Large-v3 F16 header, then atomically installed as
root:10001 mode 0440. It requires the model size plus a 512-MiB staging reserve
free before downloading. Existing valid files are reused; invalid or
wrong-permission files fail closed and are never replaced. A new private
qualification key is generated locally once as root:10001 mode 0440. An
existing valid key is preserved byte-for-byte. Neither model nor key contents
are printed or committed. A failed preparation prints only a diagnostic ID,
known stage, class, classification and command exit code; bounded details are
stored root-only in the lifecycle state directory.

Use only the source-owned lifecycle from an exact reviewed release:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py prepare-cuda-candidate --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py cuda-candidate --revision FULL_SHA --max-audio-seconds 1200 --max-upload-bytes 40000000 --native-timeout-seconds 14400
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py cuda-candidate-status --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py cuda-candidate-rollback --revision FULL_SHA
```

For a one-command private qualification, use the `cuda-candidate-qualify`
action after the prior Whisper runtime candidate has been rolled back. It
requires `qualification-manifest.json` and its audio inputs in the same private
`/tmp/homecompute-whisper-stt.<token>` directory as the staged bootstrap. Set
the directory to mode 0700 and each input plus manifest to mode 0600, owned by
the invoking operator. The manifest has this exact schema:

```json
{
  "schema_version": 1,
  "max_audio_seconds": 1200,
  "max_upload_bytes": 40000000,
  "native_timeout_seconds": 14400,
  "inputs": [
    {"id": "recording", "file": "original.wav", "bytes": 35913004,
     "sha256": "<64 lowercase hex characters>", "frames": 17956480}
  ]
}
```

It accepts one to four bounded files. Each WAV must match its declared byte
count, SHA-256 and sample count and contain complete mono 16 kHz PCM16 samples.
The helper stages the validated bytes root-only before preparing the pinned
model/key/image. It then sends serial authenticated requests with automatic
language, no prompt, zero context, beam/best-of 5, and `max_len=-1`. Raw
verbose responses and all six provenance headers are preserved in mode-0600
root-owned results before checks. It always runs the exact-primary rollback
after inference or failure, waiting up to the native timeout plus three minutes
for a confirmed idle candidate. It refuses to stop a candidate whose idle
state remains unknown; that case is reported as rollback failure for recovery
through the existing status/rollback lifecycle. `SIGINT`, `SIGTERM` and
`SIGHUP` trigger the bounded rollback path; `SIGKILL` and power loss cannot.

Example after staging the manifest and WAV:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/bootstrap-whisper-stt.py \
  --revision FULL_SHA --action cuda-candidate-qualify \
  --qualification-manifest /tmp/homecompute-whisper-stt.TOKEN/qualification-manifest.json
```

The action exports result JSON and a content-free receipt into that same
operator-owned staging directory using exclusive mode-0600 files. The result
JSON contains the candidate transcript response and provenance headers, never
the key. Matching root-only copies remain under
`/var/lib/homecompute/whisper-stt/cuda-qualifications/`. Standard output shows
only qualification status and export paths. Do not stage prompts, names, dates,
or reference answers in the manifest; request decoding options are fixed by the
source-owned helper.

`prepare-cuda-candidate` provisions/verifies the external artifacts, checks the
native ARM64 host, local pinned base image, GB10 compute capability and driver
floor, then builds and verifies the image; it does not attach a GPU or start a
model. `cuda-candidate` requires a
successfully applied or legacy-retired full Whisper transaction, a healthy
pinned vLLM primary, and no active vLLM candidate. It validates the pinned
model/key, applies the existing intake gate and drain, stops only the exact
healthy primary, then starts this isolated candidate. The existing 12-GiB
pre-load and 10-percent post-load host headroom gates remain mandatory. A
synthetic authenticated smoke must pass before qualification is reported.
Always run `cuda-candidate-rollback` after a standalone qualification; it
removes only the journaled candidate and restores the exact stopped vLLM
primary and restart policy. These qualification actions do not promote the
image. Promotion is a separate exact-image operation documented above.

For the 1,122-second private qualification, pass bounded duration, upload, and
native timeout values explicitly as lifecycle options, for example the values
shown above. The service refuses inconsistent duration/upload limits and caps
the longform path at two hours / 256 MiB / four hours of native time.
The promoted relay keeps both existing loopback mappings, 8007 for OpenAI STT
and 8003 for the Home Assistant compatibility adapter.
The ordinary production relay remains unchanged at 256 MiB of container memory
and a 60-second / 10,000,000-byte request cap with its existing normalization
and header behavior. Promotion starts a separate, profile-gated
`whisper-cpp-http` relay with a 512 MiB hard memory cap for the 38.4 MB PCM
input and bounded multipart validation. The source-owned long-recording route
is separate: Caddy admits only the Daythread backend at
`/recording-stt/v1/audio/transcriptions`, caps the body at 40 MB, and proxies
to the existing private Spark relay tunnel with a 30-minute response-header
deadline. The relay requires the existing Whisper role key before reading the
body, validates complete mono 16 kHz PCM16 WAV bytes and a 1,200-second cap,
then forwards the original multipart bytes without FFmpeg or rewriting. It
preserves the six native model/runtime provenance headers. The CPP adapter
accepts the role key through a root-owned mode-0440 copy; no credential is
printed. The candidate owns the stable `whisper-primary` alias for ordinary
short requests and `whisper-cpp-recording` for the dedicated long-form path.
Promotion preserves the exact original relay container, image, network
attachments, and restart policy under a temporary name, then starts the
separate CPP relay profile on the existing loopback port. Before marking
promotion complete it authenticates through the new relay on both the ordinary
and long-form routes and checks all six provenance headers. Both CPP containers
start with restart disabled; only after both relay smokes, GPU/headroom checks,
and journaled promotion succeed does the lifecycle set `unless-stopped` on the
CPP model and relay. The retained vLLM and old relay keep restart disabled.
The promotion smoke first sends a 30-second synthetic verbose request directly
to the private CPP adapter, then sends the same request through the recording
relay, and finishes with the ordinary one-second JSON route. This gives the
verbose path a full Whisper window while checking timestamps against that exact
input duration. After each request it polls
the existing health endpoint until adapter and native inference are confirmed
idle before issuing another. It reports only fixed probe names, HTTP status,
allowlisted error enums, provenance-header presence booleans, and bounded
timestamp-check metadata; transcript, audio, response bodies and credentials
are never printed. A failed command summary includes both direct-adapter and
relay error enums, while bounded raw command output stays in the root-only
diagnostic receipt.
Rollback removes
only the journaled CPP relay and restores the retained original relay container
plus the exact saved vLLM container. The long-recording path is unavailable
after rollback because the restored older relay does not implement it; requests
fail closed and never fall back to vLLM.
No in-network consumer depends on the old relay service DNS name
`whisper-http`: Core clients enter through the existing protected tunnel and
Caddy routes. The promoted relay uses its explicit `whisper-cpp-http`
service/container name.
Rollback waits for three consecutive native-idle checks and empty host
8003/8007 connection checks, so an upload that has not reached inference keeps
the model alive. Its default bounded wait is the recorded native timeout plus
120 seconds (up to 1,920 seconds for promotion). If rollback cannot verify idle
or restore health, the intake gate remains active; `cuda-candidate-status`
reports `gate_held`, and the same rollback action reuses the verified gate
chain on retry.

The exact image that completed the private full-recording GPU qualification is
`sha256:d6da07a313bc18c336e4ccb9c2680f209d80c079a5fb42e9eb6b522e244448b3`,
built from HomeCompute source `50ee4f9666c26df89bccab30c83e9cd6dcc0faba`.
Promotion verifies that image ID plus its source/runtime/model/CUDA labels;
it does not build or pull a replacement. It applies the existing locks,
intake gate, drain, exact primary capture, native GPU proof, and 12-GiB/10%
host-memory gates. The original vLLM primary remains stopped and available for
exact rollback. There is no automatic Spark Compose or systemd startup for
Whisper. On successful promotion, the source-owned lifecycle persists only the
qualified CPP model and dedicated relay with `unless-stopped`; it keeps both
original vLLM and relay restart policies disabled. The normal vLLM `apply`
action refuses to run while a CPP promotion journal is active. The maintenance
restart action selects the exact qualified CPP container when the journal is
promoted, and status reports the active backend. Rollback switches back to the
retained original pair and restores their recorded restart policies.

After reviewing and publishing the source, promote with the one guarded action:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/bootstrap-whisper-stt.py \
  --revision FULL_SHA --action cuda-candidate-promote
```

Check `cuda-candidate-status` for the `promoted` state. To restore the saved
vLLM primary, run the existing source-owned rollback action:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/bootstrap-whisper-stt.py \
  --revision FULL_SHA --action cuda-candidate-rollback
```

## Bounded native windows for long uploads

The `hc5` adapter candidate preserves the existing native request for inputs
up to 30 seconds. Longer validated WAV uploads are partitioned into contiguous
30-second PCM windows plus the remaining tail. If the tail is shorter than
3 seconds, the final two windows share their remaining frames equally (at most
one frame difference), avoiding an isolated tiny request while keeping both
windows at most 30 seconds. Exact 3-second tails are unchanged. Every input frame is sent once,
without silence removal, VAD, gain changes, overlap or response selection.
Each native request preserves the caller's decoding fields: `auto` detects
language afresh in each window; explicit language and prompt remain explicit.
This addresses the pinned native implementation's single language detection
at the beginning of a whole request. It can also introduce boundary errors,
so short-window evidence is not a substitute for long-recording qualification.

One admission semaphore remains held throughout all windows and response
assembly. The configured native timeout is one monotonic inference deadline
for the complete upload, not a new allowance per window. The existing upload
read timeout remains separate. A failed, malformed or late native window fails
the whole request; no partial canonical transcript is returned. Native response
bytes and the final aggregate share bounded response limits. Health remains
busy between windows even when native inference is momentarily idle, and
rollback waits for adapter idle plus drained client connections.

Text and JSON preserve each normalized native text and insert a separator only
when adjoining words otherwise concatenate. Verbose JSON retains native segment
metadata, rebases timestamps to the original recording, renumbers segment IDs,
and rebases character anchors only when native canonical serialization proved
them. It reports the original WAV duration. `language_windows` records each
window's source times and native detected language; global `language` is `mixed`
when nonempty outputs contain multiple detected languages. This is detected
language metadata, not a human accuracy claim. Optional word alignment that
accepts only one language cannot align `mixed`; consumers must retain canonical
text/native segments and report unsupported alignment rather than force a
single language. The up-to-30-second response contract is unchanged.

## Replace an already-promoted CPP runtime

The replacement action is enabled only after this release pins a separately
qualified replacement image. It never rebuilds that model image: its build
source revision remains the immutable revision recorded by the qualification.
Complete full-recording, repeated-request, timestamp, resource, and client-path
checks before updating the qualified image, catalog, image tracking and
maintenance declarations. Short Daythread windows alone do not qualify the
shared 1,200-second recording route.

Once the reviewed release is published, stage its exact bootstrap through the
existing private staging procedure. The operator's one privileged command is:

```bash
sudo python3 -I -B /tmp/homecompute-whisper-stt.TOKEN/bootstrap-whisper-stt.py \
  --revision FULL_SHA --action cuda-candidate-replace
```

Use an attended terminal if sudo requires authentication; neither Docker
access nor a temporary host edit substitutes for this lifecycle. The action
holds all four existing locks and verifies the active root-owned CUDA journal,
healthy predecessor model/relay, exact retained images, source-owned networks,
and clean published old release before touching services. The previous journal's
revision selects only its rollback implementation; the image's build SHA or a
Compose label is not used as a substitute. It also materializes and verifies
published recovery release `6b5359fbb8af1461aa83e8146af0cc5d88fbd5f1`, whose
explicit `10.253.73.0/28` internal subnet avoids the older release's automatic
address allocation. Both replacement and recovery Compose declarations and
host/Docker route overlap checks pass before gating. The recovery release's
maintenance bundle must match both the active journal and the actually installed
files, verified without installing anything; unknown or newer unrelated bundle
changes stop preflight for a separately reviewed predecessor source. Both the
old and target maintenance bundle hashes are recorded. Recovery checks each
installed file and generated dispatcher against those two exact versions, with
root ownership and exact modes: an interrupted install of our own bundle can
recover, but unrelated later updates cannot be silently overwritten. The
retained vLLM engine must still have its reviewed 0.08 GPU reservation and 12-GiB memory limit, with at least
12 GiB currently available. These checks are not GPU startup proof; native GPU
placement, readiness, post-load headroom and authenticated smokes remain gates.

An outer, journaled intake gate spans the old release's guarded rollback and
the new release's guarded promotion. The old CPP model stops before vLLM is
restored, and vLLM stops before the new CPP model loads. Promotion smokes run
inside the bridged relay container (its own loopback port 8007 and the private
adapter on port 8000), so the host OUTPUT gate does not block them. No new
host clients are admitted during the transition.

On failure, recovery uses each active transaction owner's rollback path and the
verified `6b5359f` release to restore the previous exact model and relay images,
with the recorded, byte-identical predecessor maintenance bundle. Its new inner
journal records `6b5359f` as the recovery owner. A rolled-back inner journal with
an active gate still requires its owning lifecycle to complete gate cleanup
before any new journal can be created. Recovery does not rebuild those artifacts; container identities may
change when restoring them. Preserve the old immutable release, images,
retained vLLM/original relay containers, and both journals. Unknown state,
native busy state, missing artifacts or failed recovery keep intake held.
Inspect or retry recovery through the same new release:

```bash
sudo python3 -I -B /tmp/homecompute-whisper-stt.TOKEN/bootstrap-whisper-stt.py \
  --revision FULL_SHA --action cuda-candidate-replacement-status
sudo python3 -I -B /tmp/homecompute-whisper-stt.TOKEN/bootstrap-whisper-stt.py \
  --revision FULL_SHA --action cuda-candidate-replacement-recover
```

Re-running `cuda-candidate-replace` after an interruption finishes recovery
first and reports `restored`, with a nonzero exit because deployment has not
completed. A later explicit replacement attempt can then start afresh. A
completed replacement is idempotent only while the exact new service remains
healthy and both intake gates are clear. The explicit recovery action can also
restore the recorded predecessor after a completed replacement. Never delete
the replacement journal or flush firewall rules to bypass a held recovery.

The intake gate treats only successful `iptables -w -S` and `-S OUTPUT`
snapshots as evidence that a nonce chain or jump is absent. Command errors,
incomplete listings, conditional jumps, or unexpected rules fail closed. It
accepts only the kernel's default `--reject-with icmp-port-unreachable`
serialization for the gate's REJECT rules; other reject behavior is drift.
The promotion action can recover the specifically pinned pre-transition gate
failure on retry only when its journal matches, the exact original vLLM primary
and relay are healthy and unchanged, no candidate services exist, and the
journaled nonce gate can be safely verified and removed. Other journal states,
unavailable firewall state, and failures after the model transition remain held
for status and rollback review.

Both actions use the deployment, rollout, speech-capacity and Whisper locks.
Promotion reuses the exact qualified image, sets the long-form adapter bounds
to 1,200 seconds / 40 MB / 1,800 seconds, and retains the required memory and
native-inference-idle checks. Rollback is exact and refuses to stop the CPP
decoder while its native busy state is true or unknown.
Before starting the model transition, promotion runs the published
`setup-infrastructure-maintenance.py` bundle installer without a public-key
argument, then verifies that the Spark dispatcher and container-maintenance
helper exactly match the computed bundle hash. The hash is recorded in the
promotion journal. A stale bundle, install error, or verification mismatch
aborts before any model container is stopped. Existing SSH authorization and
private maintenance controller state are preserved.

## Repair an overlapping CUDA candidate subnet

The CUDA candidate bridge is pinned to `10.253.73.0/28`, gateway
`10.253.73.1`, in `compose-cuda.yaml`. Keep this range reserved for this
project. Automatic Docker allocation previously selected `192.168.0.0/20`,
which includes the administrator LAN `192.168.10.0/24`. The connected bridge
route sent management replies into Docker rather than through the LAN gateway.
Changing Compose alone does not replace an existing network.

Publish and validate the selected source first. Use the existing private
bootstrap staging procedure from the [Whisper lifecycle](../whisper-stt/README.md),
then run the dedicated action, through home-core if direct SSH is affected:

```bash
ssh -J home-core -t home-spark 'sudo python3 -I -B /tmp/homecompute-whisper-stt.TOKEN/bootstrap-whisper-stt.py --revision FULL_SHA --action reconcile-cuda-network'
```

This action materializes the exact published release through the bootstrap;
it does not change the general host release pointer. It holds the existing
deployment, rollout, speech-capacity and Whisper locks and refuses active
maintenance/capacity overrides. It requires the exact healthy promoted CUDA
model and relay, their existing production aliases, and sole model ownership
of the internal candidate bridge. It checks the declared subnet against every
other Docker network and all IPv4 routing tables before mutation.

The action journals the original bridge and aliases, disconnects only that
internal bridge, recreates it with the declared subnet and reconnects the same
model container. The production inference endpoint stays attached throughout;
the model image, container ID, restart policy, credentials and model files are
preserved. It verifies the unchanged production endpoint and authenticates a
model-list request from the existing relay. It neither loads another GPU model
nor stops the current decoder. Repeated runs verify the same desired state.

The protected CUDA journal retains `network_reconciliation` with the applied
revision, original network, exact model/inference identities and completion
state. A failed migration restores the original bridge and aliases, including
its colliding route; preserve access through home-core until verification
succeeds. If interrupted or rollback fails, the pending journal supports retry
from the same release. Unknown ownership or inference-endpoint drift aborts
without overwriting it. Keep the prior release and staging directory until the
repair is verified; do not rerun model promotion or delete the journal to repair
a network.

Verify the installed subnet with `docker network inspect
whisper-cpp-gb10-cuda_candidate`, and `ip route get ADMIN_WORKSTATION_IP` on
Spark. The workstation route must use the LAN gateway rather than the Docker
bridge. Confirm direct `ssh home-spark`, the authenticated speech consumer
path, and `homecompute status`, `services`, `doctor` and `drift`. Independent
host release differences and previously failing services need separate
reconciliation; this network action does not establish platform-wide readiness.

The migration/refusal/recovery suite is
`python3 tests/whisper-cuda-network-test.py`; the repository validator runs it
alongside the existing Whisper suites.

The portable synthetic contract and refusal tests run with:

```bash
python3 tests/whisper-cpp-stt-test.py
python3 tests/whisper-cpp-request-rng-test.py
```

They test the real adapter over HTTP with a synthetic native response, prompt
and option forwarding, format rejection, authentication, exact segment/text
anchors, bounds, and model header/checksum refusal. They do not measure model
quality, CPU latency or host resource use. Quality qualification must use the
frozen private request/source artifacts; never commit recordings, prompts,
transcripts or model weights. The request-RNG suite requires a C++17 compiler
and compiles exact, checksummed excerpts of the pinned native sampler. It proves
the original defects and checks sorted unique candidates, ties, timestamp
metadata, and request independence without a model or network. GPU execution
and real-audio quality still require private candidate qualification.
