# Isolated Parakeet transcription comparison

This is a diagnostic candidate for investigating omissions and language
conversion. It is not a production route or a fallback. [Issue 64](https://github.com/madslundt/HomeCompute/issues/64)
tracks the comparison; [ADR-027](../../docs/adr/027-unified-whisper-large-v3-stt.md)
continues to select full Whisper Large-v3 for production. No speaker detection,
summaries, recap, or chat behavior changes here.

## Reproducible candidate

`model-manifest.json` pins NVIDIA's `parakeet-tdt-0.6b-v3` snapshot, seven public
files, the FP32 safetensors digest, Transformers source revision, six overlay
wheels, and the cached NVIDIA ARM64 PyTorch image. `requirements.lock` installs
only those hashed wheels without dependency resolution. Vendor Torch and
torchvision versions are checked before and after installation; CUDA libraries
are not replaced. NVIDIA distributes the model under CC-BY-4.0. Keep the
publisher's README attribution alongside the weights. See the [publisher model
card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/tree/541d1f99c6b0c3cd0b11a95167540bb8edefd82b).

Runtime imports and model loading are offline, local-files-only, and disable
remote model code. One explicit device uses FP32, deterministic algorithms,
greedy decoding and batch size one. CUDA failure does not silently select CPU,
offloading, quantization, or a different model. The publisher/native generation
capacity is used without an arbitrary output-token cap. Success requires native
frame advances to consume the encoder's valid frame count. This is structural
completion evidence, not proof that all spoken content was retained.

## Isolation and resource gates

Use only the dedicated `scripts/parakeet-shadow.py` lifecycle from a reviewed,
published full source SHA. Do not add this service to production Compose,
LiteLLM, Daythread settings, or the normal release wrapper. The lifecycle owns
one distinctly named, non-restarting diagnostic container and its private
working directory. Model provisioning uses a fresh operator-owned candidate
directory; inaccessible production caches must not be inspected through
privilege escalation or broader container mounts.

The initial budget is 6 GiB RAM, no additional swap, four CPUs, one request,
and a 180-second input ceiling. Fresh host available memory must cover both the
6 GiB trial budget and 10% of total host RAM before launch. This only admits a
load and short-input trial; it does not qualify the largest input. Advance from
30 seconds to 60 seconds and then the longer discriminator only after measuring
headroom. CUDA unified allocations may not be fully represented by the Docker
memory limit. Monitor real host MemAvailable during loading and each request.
The adapter samples every 100 ms, latches any breach of the 10% floor, and checks
the guard before and after loading and processing and at every decoder step.
An encoder call is not cooperatively interruptible; measured peaks and the
external lifecycle monitor remain necessary. Never stop production workloads
to create space or lower the reserve to force admission.

From the clean published candidate checkout on Spark, as its ordinary
Docker-authorized user:

```sh
revision=$(git rev-parse HEAD)
python3 scripts/parakeet-shadow.py provision --revision "$revision"
python3 scripts/parakeet-shadow.py build --revision "$revision"
# Select the actual immutable image_id returned by build, not a mutable tag.
python3 scripts/parakeet-shadow.py start --revision "$revision" --image-id sha256:REVIEWED_IMAGE_ID --device cuda
python3 scripts/parakeet-shadow.py status
# Keep this foreground companion running in the benchmark controller while
# requests execute; its failure invalidates concurrent request results.
python3 scripts/parakeet-shadow.py guard
# In the controller's separate command path, arm a non-extendable 630-second
# request deadline before every upload. Retain the returned request_id.
python3 scripts/parakeet-shadow.py request-start
# Submit exactly one request, capture the response, and verify idle state.
python3 scripts/parakeet-shadow.py request-end --request-id RETURNED_REQUEST_ID
# In the controller's separate command path, after inference is idle:
python3 scripts/parakeet-shadow.py stop
```

The lifecycle refuses root execution and checks that the selected revision is
the published candidate branch head. Its private state and receipts are under
`~/.local/share/homecompute/parakeet-shadow`. A second active trial is refused.
The trial has a bounded lifetime (one hour by default); an explicit CPU trial
has its own recorded identity and must repeat the applicable gates.
The request marker cannot be rearmed or extended while present. Its external
deadline covers the 30-second upload allowance and 600-second inference budget,
including a blocked native encoder that cannot honor cooperative callbacks.

The container has no network, host ports, capabilities, writable model mount,
or persistent payload logging. It runs as UID/GID 10001 with a read-only root
filesystem and a bounded private tmpfs. Authentication uses an ephemeral key
file on that tmpfs; the key is not an argument, environment value, image layer,
or log entry. Requests travel through the operator's authenticated SSH/Docker
stdio path to container loopback. Normal cleanup requires idle inference and
SIGTERM requests a cooperative drain. An actual memory-floor breach or trial
deadline is an emergency failure: the lifecycle may terminate only its verified,
owned diagnostic container after a bounded cooperative grace period. This can
interrupt an unresponsive encoder; its partial output is discarded. Production
containers and requests are never stopped. Preserve the failed attempt and its
resource measurements; an emergency abort cannot count as qualification.

## Diagnostic API

The adapter binds only `127.0.0.1:8080` after verified model loading. `/health`
reports readiness, actual model/package/device identity, measured parameter
storage, request admission state and minimum observed host memory. Authenticated
`/v1/models` advertises only `parakeet-tdt-0.6b-v3`.

`POST /v1/audio/transcriptions` requires a Bearer key and bounded multipart form
with `model=parakeet-tdt-0.6b-v3`, one `file`, and optional `response_format`
(`json`, `verbose_json`, or `text`). The file must contain one complete mono
16 kHz PCM16 WAV data chunk, at most 180 seconds. Unknown/duplicate fields,
Whisper-specific options, malformed uploads, competing requests and resource
failures are rejected. PCM is neither amplified, trimmed nor split here.

Verbose JSON preserves exact native text and includes the PCM digest and input
duration. `segments` is empty and there is no fabricated detected language or
speech interval. Token IDs, predicted frame durations/timestamps and structural
completion counters remain explicitly separate from qualified word alignment;
native end predictions can overrun the input. No silence filtering or transcript
postprocessing hides model errors. Successful responses carry actual model,
snapshot, runtime, runtime revision, HomeCompute revision, image and device
headers. The external harness must independently verify image/container pins;
headers alone do not prove provenance.

## Qualification and privacy

Freeze audio identity, reference text, source-language retention checks and the
candidate's complete execution identity before inference. Compare the same PCM
against the selected Whisper baseline. A lower aggregate word error rate cannot
override an omitted passage, a lost language, repetition or invented content on
silence. Report per-language results and explicit content-gate failures. Repeat
accepted cases across intervening requests before considering long recordings.
Long-recording and client-path qualification remain separate gates.

All recordings, transcripts, summaries, reference fixtures, per-case results,
credentials and host receipts remain in private Git-ignored benchmark storage.
Only source, synthetic tests and public model metadata belong in this directory.
Do not import these outputs through a Whisper-specific provenance contract.
Model selection or production promotion requires a separate decision and
qualified lifecycle; a successful shadow comparison does not change ADR-027.

Run the synthetic checks without model downloads:

```sh
python3 tests/parakeet-shadow-runtime-test.py
python3 tests/parakeet-shadow-adapter-test.py
python3 tests/parakeet-shadow-lifecycle-test.py
./scripts/validate-repository.sh
```
