# Auxiliary language observer (experimental, disabled)

Source preparation for [Issue89](https://github.com/madslundt/HomeCompute/issues/89),
linked to the experimental transcription foundation in Issue78. This CPU command
classifies a supplied 3–30second PCM recording among107 languages. It does not
transcribe, identify speakers, expose a service, or change Whisper or any
production route. No classification accuracy or safe ASR boundary claim is made.
The actual short-fragment trial requires separate approval and empirical review.
The authoritative GB10 roster records this family as staging-only, unqualified
and disabled. Artifact operations validate that entry; it has no operating
profile, capability route or promotion permission.

The model is Apache-2.0
[`speechbrain/lang-id-voxlingua107-ecapa`](https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa/tree/0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9).
`model.json` pins the repository commit, original107-label order (DA17/EN20),
three public assets, sizes, hashes and resource limits. Neither `hyperparams.yaml`
nor `normalizer.ckpt` is fetched or executed. The explicit pipeline implements the
pinned official Fbank, sentence mean normalization, ECAPA embedding and Xvector
MLP classifier. Checkpoints use `weights_only=True`, CPU placement and exact
state keys, tensor dtypes/shapes and finite values. Unsafe or partial loads fail.

`dependencies.json` locks all32 active ARM64 CPython3.12 wheels including their
complete hashes, exact public URLs, byte lengths and dependency metadata. The
requirements file repeats the install hashes. The cached NVIDIA image is used by
immutable local image ID, without a pull. Export uses a separate CPU
Torch2.7.1/torchaudio2.7.1/SpeechBrain1.1.2 venv without system packages.
The locked Torch wheel reports runtime `2.7.1+cpu`; its distribution version is `2.7.1`. Dynamic
library and Python paths are sanitized for export. The NVIDIA Torch/Torchvision
package file hashes are checked before and after installation; no NVIDIA package
is replaced. Build has no network or model assets. The worker runtime uses only
the unchanged NVIDIA Torch2.11a0 and does not import SpeechBrain/torchaudio.

Public weights and wheels total255,015,678bytes. Fetching is a separate explicitly
authorized operation; exact URL/redirect allowlists, per-artifact hashes and a
persistent accumulated512MiB hard download cap apply. Failed attempts remain for
review and are never retried by the program. Disk must retain2GiB free. Wheels,
model assets, exported graph and all private observations remain outside Git.
The build context excludes private data and model assets; its only additional files are the verified wheel directory. The legacy builder
(`DOCKER_BUILDKIT=0`) enforces2GiB memory,2GiB total memory/swap and4CPU
on installer containers. Source-owned polling checks the actual labeled build
container limits, host floor,600second deadline and2MB log cap. Failures stop and
remove only containers with the exact random build owner/source labels; receipts
and the temporary external exact source/wheels context remain for review. BuildKit
is not used because its build resource flags would not enforce these limits.

The graph accepts batch-one int16 raw samples, with the fixed `/32768` PCM scale
inside it. There is no gain, padding, cropping or format conversion. Export
compares the actual frozen graph against the explicit official pipeline on ten
public nonstationary, silence and quiet synthetic waveforms spanning3–30seconds,
including partial frames. All107 finite normalized log posteriors must agree
within the fixed absolute/relative tolerances and retain the same argmax. The
original model must distinguish the shortest and longest duration probe. Every
trace warning is preserved; its exact SHA requires root review before private
classification. Python/custom operations and unreviewed ATEN operations fail.
No automatic operator-policy expansion is allowed.

A second CPU phase loads the pinned graph on the actual NVIDIA Torch runtime and
repeats all full-posterior comparisons. Backward TorchScript compatibility is a
hypothesis until that phase passes. Graph and manifest SHA verification precedes
`torch.jit.load`; JIT has no `weights_only` option. Only a reviewed source-produced
graph can be admitted. The complete107-class scores retain their original order;
DA/EN are not renormalized into a binary classifier or treated as calibrated
correctness probabilities.

## Source-owned operation

Run the source-only check without Docker, downloads or model imports:

```sh
python3 -B scripts/language_observer.py check-only
python3 -B tests/whisper-language-observer-test.py
```

All other operations require a private owned0600 authorization JSON whose SHA is
supplied on the command line. It must explicitly set `root_reviewed` and
`authorized`, scope `auxiliary-language-observer-only`, the published clean
`source_revision`, and the allowed `operations`. The selected SHA must be the
published `codex/whisper-language-observer` branch head at the fixed origin.
No default authorization is shipped. Authorization binds `public_artifacts`,
`build_receipt`, `build_context`, `image_id`, and an absolute private `lifecycle_directory` outside
Git; later phases additionally bind `graph_directory`, `graph_manifest_sha256`,
`parity_report`, `parity_sha256` and `approved_warning_sha256`. Classification
binds a maximum of11 calls and every case ID's exact WAV/PCM SHA and frame count
in `inputs`. No mode may be restarted within its frozen lifecycle directory.

After separate review, the operator may invoke `fetch`, `verify-artifacts`, then
`build` with a new external `--output` matching `build_receipt`. Build is bounded
to600seconds, never pulls, never accesses model assets and imports no model
packages. A completed image receipt is required for the next reviewed
`image_id` authorization. This source task does not authorize those actions.

Each model phase uses `start --mode export|verify|classify`, then a separately
running `guard` process. The independent guardian must record its PID, boot and
process-start identity and a fresh successful sample before `job --mode ...
--case ...` can import or load a model. Each worker has an absolute1800second
lifetime, with export900, verify300 and each classification60second job caps;
no extension or automatic retry exists. An idle worker contains only a stdlib
sleep loop. The mode-specific command runs inside that owned worker. Classification
additionally takes `--input` pointing to its frozen private WAV.

Every worker has2GiB RAM, zero swap,4CPU, Torch1thread, no GPU, network, ports,
restart, capabilities or writable root filesystem. The model temporary filesystem `/tmp` is256MiB; `/dev/shm` is32MiB. Host admission requires `MemAvailable >= 10% MemTotal + 2GiB`; the
independent guardian preserves the10% floor throughout. It verifies actual Docker
ownership/resources, CPU-only exposure, cgroup swap and memory events. The fixed
`LOGNAME=language-observer` gives NVIDIA Torch a stable cache identity during
deterministic setup for both runtime verification and classification. Host floor,
OOM, memory events, guardian loss, output overflow or deadline failure latches a
failed phase and stops only the owned worker. Failure artifacts remain; forced
termination is never described as successful qualification.

After export or runtime verification succeeds, `collect --output NEW_EXTERNAL_DIR`
streams only the known public graph and manifest or synthetic parity report
from the owned tmpfs through `docker exec` without a TTY. The transfer has a
30-second deadline and checks file type, byte bounds and SHA-256 before recording an
extraction receipt **before** `stop`. Graphs are capped at128MiB. Collected files
are0444 and their directory0555; a private0700 host parent protects the cache
while the read-only bind remains readable by UID10001. The operator reviews the
export manifest/warnings before authorizing verify, and actual runtime parity
before private classify. The graph bind contains only public outputs.

`stop` requires no active job for a successful close, archives the owner journal,
stops/removes only the exact owned worker and allows the independent guardian to
exit normally. Guard failures preserve their failure state and cannot become
normal-idle qualification. `status` reports actual owned state. Keep the guardian
exit receipt alongside extraction and result receipts. A failed start or job
requires new explicit review, not a repeated command with adjusted thresholds.

Private classification output contains input/model/source/runtime hashes, numeric
107-class posteriors, labels and elapsed time. It contains no text, embeddings or
speaker identity. Repeated exact inputs must be reviewed for exact numeric score
parity. There is no fallback to CUDA, cloud inference or a different model. Any
future Whisper integration needs independent source review and frozen human
content gates; this component authorizes no integration or promotion.
