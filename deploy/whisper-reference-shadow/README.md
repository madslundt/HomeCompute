# Isolated Whisper reference diagnostic

Tracks [issue 70](https://github.com/madslundt/HomeCompute/issues/70). This opt-in
diagnostic compares OpenAI Whisper's reference decoder with the existing native
runtime using the **same approved full Large-v3 GGML weights**. It does not add a
production route, change the primary service, or qualify a new speech model.
Speaker detection is outside this work.

`model-pin.json` owns the exact model, runtime, loader, base image and decoding
profile. The GGML file remains external at
`/srv/gb10-ai/cache/whisper-cpp/ggml-large-v3.bin`. There is no model provisioner,
alternate checkpoint, CPU fallback, network access at runtime, or remote model
code. Public dependency artifacts are pinned in the adjacent dependency lock;
the build preserves the Torch/CUDA installation in the pinned ARM64 NGC base.

## What is being compared

The loader verifies the complete existing artifact SHA and strict tensor layout,
then copies each stored F16/F32 tensor directly to CUDA. It constructs the pinned
reference architecture on the meta device first, avoiding a second full model
allocation. Exact vocabulary and mel-filter linkage is verified independently.
The two convolution biases require the documented inverse GGML reshape. The
causal mask is a nonpersistent buffer; the unused alignment-head buffer follows
the reference default. No weights are requantized or substituted.

The fixed profile is transcribe, single temperature zero, beam five, no previous
text conditioning, no prompt, and no word timestamps. The explicit token mask
matches native mandatory control/language suppression without enabling the broad
non-speech token list. `best_of` is removed by canonical `transcribe` at zero
temperature. The full resolved options, runtime versions, storage types, CUDA
residency and deterministic-algorithm setting are returned in health metadata.
TF32 and cuDNN benchmarking are disabled; the pinned reference attention policy
still applies. CUBLAS deterministic workspace configuration is set before Torch
imports. This does **not** establish cross-runtime numerical identity: native
entropy and reference gzip-compression checks are different, and decoder/search
implementations differ. No multi-temperature fallback is enabled in this arm.

An observational model subclass delegates every decode to the original reference
method without changing its input, output, ranking, filtering or search. It
records generated-token count and effective sample limit (canonical default 224)
before segment trimming. Any observed limit hit blocks qualification. A count
below the limit is **not** evidence of complete speech coverage. The API preserves
the canonical text exactly, retains original segment records, and marks invalid
or unmappable timing as unknown. It never fills a missing span with an invented
timestamp or selects a preferred transcript from multiple attempts.

## Source and runtime gates

Review and publish a clean full commit on `codex/whisper-reference-shadow` before
using the lifecycle. Build/start refuse a local-only or dirty checkout, mutable
image reference, mismatched image labels, alternate base, or active trial. Use a
separate clean checkout of that published commit on Spark; do not modify the
primary service checkout. Read [HomeCompute operations](../../docs/homecompute-operations.md)
for host access. No sudo password is accepted by this tool.

From that exact checkout, run the repository-owned commands in this order:

```bash
python3 scripts/whisper-reference-shadow.py build --revision FULL_SHA
python3 scripts/whisper-reference-shadow.py start --revision FULL_SHA --image-id sha256:IMAGE_ID
```

In a separate supervised process, start the continuous guardian:

```bash
python3 scripts/whisper-reference-shadow.py guard
```

Only after it is alive, release the model load and wait for readiness:

```bash
python3 scripts/whisper-reference-shadow.py release-load
python3 scripts/whisper-reference-shadow.py wait-ready
python3 scripts/whisper-reference-shadow.py status
```

The candidate has 6 GiB memory, no swap, four CPUs, a read-only filesystem, one
read-only model mount, bounded tmpfs, no published ports, no network, no logging
driver and an ephemeral private credential. Its only API listens on container
loopback 8080. A host guardian runs **before** model loading, enforces the 10% host
available-memory floor, 300-second load deadline, 630-second admitted-request
deadline and one-hour trial deadline, and latches observed failures. A failed
startup is evidence to inspect, not authorization to retry. The guard can abort
the owned candidate for a real resource/deadline/monitor failure; it cannot stop
unrelated services. Deadline enforcement is external and may terminate the
candidate; it does not claim cancellation inside an opaque CUDA operation.

Use `request-start` immediately before each authorized private benchmark request
and `request-end --request-id ID` after it has completed and actual health is
idle. The API also serializes uploads and inference, rejects overlapping work,
and keeps admission held if the client disconnects during inference. At trial
end, archive private evidence externally, verify idle state, then use `stop`.
Normal cleanup refuses a busy or unknown service. `emergency-abort` is reserved
for an actual current guard failure, not ordinary cleanup. Preserve its bounded
host cgroup snapshot and failure receipt. Every cleanup must verify the primary
service remains healthy and the candidate and guardian have ended.

## Request and qualification contract

Authenticated multipart `POST /v1/audio/transcriptions` accepts exactly one file,
`model`, optional `language` (canonical code or `auto`), and `response_format`
(`json`, `text`, or `verbose_json`). Input is at most 30 seconds of mono 16 kHz PCM16
WAV and at most 1,000,000 request bytes. Ambiguous/truncated RIFF input is rejected.
Use `verbose_json` for qualification so timing, budget observations and the
unaltered reference segments are retained. Every successful response includes
actual source/image/model/runtime provenance headers. There is no tuning,
translation, prompt, language-candidate or speaker option.

1. Source checks and synthetic CPU toy parity establish only implementation
   properties. They do not establish GPU capacity, accuracy or repeatability.
2. First establish actual guarded CUDA loading and healthy, exact producer
   identity. Record memory peak/events and host floor for load and inference.
3. Freeze the first omission-case audio bytes, settings and content-coverage
   acceptance gate before the request. Evaluate canonical output and token-budget
   evidence. Failure stops broader qualification of this arm.
4. Only if that gate passes, compare the same Danish, English, mixed-language,
   quiet/noise and long-recording benchmark inputs, with repeatability checks.
   Public reference-text scores and private content observations are distinct;
   an ASR/Plaud comparison is not a human-verified verbatim reference.
5. Promotion requires complete quality, long-input, repeatability, resource and
   authenticated integration qualification plus reviewed production source.
   This diagnostic alone never promotes itself.

All recordings, transcripts, summaries, credentials, private plans and receipts
stay outside Git. Repository tests use synthetic content and public upstream
code/assets only. Run the four `tests/whisper-reference-*-test.py` suites and
`scripts/validate-repository.sh`. CPU architecture/logit parity tests require
Torch; a skipped test is not a parity pass. The adjacent canonical `model.py`
test fixture is pinned upstream MIT source, not a model checkpoint.
