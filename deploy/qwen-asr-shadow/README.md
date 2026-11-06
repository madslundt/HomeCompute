# Isolated Qwen3-ASR diagnostic

This is an unqualified, opt-in diagnostic for omissions and language conversion,
tracked in [issue 74](https://github.com/madslundt/HomeCompute/issues/74).
It does not change ADR-027, speech routes, the model roster, or speaker detection.
No Compose service or production alias is provided.

`model-pin.json` pins the official
[Qwen/Qwen3-ASR-1.7B-hf](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf)
snapshot, all eight public files, the native Transformers implementation and six
overlay wheels. The exact NGC ARM64 base retains its vendor Torch/CUDA packages.
No checkpoint Python code, remote kernels, resolver-selected dependencies,
quantization, CPU inference, or runtime downloads are permitted. The processor
supports Danish and English. That inventory is not evidence of accuracy.

The engine accepts one mono 16 kHz PCM16 WAV of at most 30 seconds per request.
Generation is greedy, batch one, BF16 on CUDA, with deterministic algorithms,
dynamic KV cache, compilation disabled and a 512-token ceiling. It returns raw
generated tokens, observed EOS/budget state and text with only the protocol
header removed. The publisher's parsed-output repetition repair is deliberately
not used. A returned EOS is not a coverage claim. No timestamps are fabricated;
segment timing is explicitly unknown. The separate forced aligner is not loaded
and does not support Danish. Long-input coverage, repeatability, memory and
language preservation still require qualification.

## Source-first operation on Spark

Read [operations](../../docs/homecompute-operations.md) first. Run as the ordinary
Docker-authorized user from a clean checkout of the exact published
`codex/qwen-asr-shadow` branch head. Replace the placeholders below with full
reviewed immutable identities. No sudo or production service stop is required.

```sh
python3 scripts/qwen-asr-shadow.py provision --revision FULL_SHA
python3 scripts/qwen-asr-shadow.py build --revision FULL_SHA
python3 scripts/qwen-asr-shadow.py start --revision FULL_SHA --image-id sha256:IMAGE_ID
```

Provisioning downloads only the eight pinned public artifacts, verifies their
sizes and SHA256/Git identities, and atomically retains a read-only model cache
under `~/.local/share/homecompute/qwen-asr-shadow/`. It never reads an HF token.
An existing corrupt cache fails without repair or automatic download retry.
Build requires the exact cached ARM64 base, uses a 6 GiB/no-swap/4 CPU envelope,
and records a private build log. No model or CUDA initialization occurs in build
smoke checks. `cpu_smoke.py` is an optional processor/synthetic-loader proof;
it performs no transcription and cannot establish live model readiness.

Start creates a uniquely owned, read-only, network-none container with no host
ports, UID/GID 10001, 6 GiB/no swap, four CPUs and one explicit GPU. Its ephemeral
credential exists only in tmpfs. Start checks the mounted checkpoint's metadata
as UID 10001, without reading payload bytes; load verifies every byte. The model
waits until a separate live guardian is present:

```sh
# Keep this running in a separate session before releasing the model load.
python3 scripts/qwen-asr-shadow.py guard
```

```sh
python3 scripts/qwen-asr-shadow.py release-load
python3 scripts/qwen-asr-shadow.py wait-ready
python3 scripts/qwen-asr-shadow.py status
```

The guardian enforces the 10% host-memory floor, no OOM/restarts/swap, a 300-second
startup deadline, a 630-second marked request deadline and a one-hour trial.
It continuously records bounded host/cgroup metadata independently of blocked
container exec calls. An observed failure latches the trial invalid; do not retry
requests or reuse the trial. `emergency-abort` is reserved for an observed failure.

Use only the authenticated `127.0.0.1:8080/v1/audio/transcriptions` endpoint through
`docker exec` into the owned container. Never publish a port or print its key.
The request fields are `file`, `model`, `response_format` and optional `language`.
The neutral `whisper` model alias permits byte-identical comparison requests;
provenance always identifies the actual Qwen checkpoint, runtime, source and image.
Automatic language selection is the default; no translation prompt is injected.
Unknown tuning fields and unsupported language codes are refused before inference.
For every request, mark its deadline and finish with the exact returned marker:

```sh
python3 scripts/qwen-asr-shadow.py request-start
python3 scripts/qwen-asr-shadow.py request-end --request-id EXACT_MARKER
python3 scripts/qwen-asr-shadow.py status
```

The external guardian is the deadline enforcement; the Python model call does not
claim cancellation. Do not clear a marker before the request has actually ended.
An inference failure makes the API unavailable. Preserve its receipt and stop.

## Qualification and cleanup

Freeze request bytes and acceptance criteria before inference. First require the
original missing passage and opening speech together. Then require no new speech
on the Danish quiet tail, digital silence and noise controls. Only after those
pass, compare Danish, English and mixed-language references, repeated identical
audio and frame-conserving long recordings. Keep raw hypotheses, token-budget
evidence, unknown timing, omissions and language conversion visible in scoring.
Do not select a winner by stitching favorable transcripts or moving boundaries
after seeing their text.

Record actual image/source/model/runtime pins, live resource evidence and public
production service identities before and after the trial. Any quality or resource
failure blocks promotion. Production integration and a model-selection decision
are separate from a successful diagnostic. Keep all private audio, transcripts,
summaries, request bodies, results and keys outside Git and container logs.

```sh
python3 scripts/qwen-asr-shadow.py stop
```

Normal stop requires known idle state, stops/removes only the exactly owned
container, archives lifecycle metadata and removes its active journal. The
guardian then exits. Retain the public model/image cache and private receipts;
verify no candidate container or guardian remains and production is unchanged.
Never remove active journals to bypass ownership checks or force-stop production.

Focused checks (stdlib only; no downloads/model/GPU):

```sh
python3 tests/qwen-asr-core-test.py
python3 tests/qwen-asr-api-test.py
python3 tests/qwen-asr-artifacts-test.py
python3 tests/qwen-asr-lifecycle-test.py
bash scripts/validate-repository.sh
```
