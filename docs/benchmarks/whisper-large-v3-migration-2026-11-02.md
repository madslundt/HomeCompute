# Whisper Large-v3 migration evidence — 2026-11-02

Status: full Large-v3 is deployed from
`5c4b046e5230001ae028064bf09117e6e4721cf9`; its frozen-corpus replay is running.
The owner subsequently requested removal of the retained Hviske/Turbo assets.
The guarded `retire` lifecycle implements that explicit deletion. Complete
quality and concurrent-workload qualification remains pending.

## Selection

The owner explicitly selected full Large-v3 to replace both Hviske and Turbo,
rather than retaining a speed-first default. The pinned model is
`openai/whisper-large-v3` at `06f233fe06e710322aca913c1bc4249a0d71fce1`.
Its FP16 weight checksum and byte size are enforced by the
[owning deployment](../../deploy/whisper-stt/README.md). TTS is unchanged.

## Observed starting point

Both hosts recorded release `b0e8b106dd79e30d765ddd38970c29649ebfb37b`.
Actual Spark commands confirmed pinned Hviske v5.3 and Turbo, not full Large-v3.
The old Whisper project belongs to a user-owned staging checkout; the new
lifecycle moves ownership to an immutable published release. Existing containers
are retained rather than reconstructing rollback from that mutable checkout.

Spark reported 121 GiB total RAM, about 5.4 GiB available, and about 13 GiB of
swap in use with text, both STT engines and Plapre running. These are one-time
host observations, not per-model memory or an acceptance result. The installer
replaces both STT engines, requires headroom, preserves text/TTS and refuses
active capacity/maintenance overrides. Full-model concurrent capacity remains
unmeasured.

## Frozen Turbo baseline

`benchmarks/daythread_stt.py` ran read-only inside the installed Daythread
backend. It preserved the consumer's speech filter, gain and pause-aware
30-second windowing; language replacement and optional cleanup were disabled.
It saved exact request WAV bytes, SHA-256s, source offsets and private results.
No application transcript, recap or recording was changed.

The real recording was 1,122.28 seconds across ten original source parts.
Its 51 requests took **110.50 seconds** summed wall time through the gateway
with verbose segment timestamps enabled (mean 2.17 seconds per request).
This excludes preprocessing and is one serial run. It is not directly
comparable with the earlier 89.16-second JSON-only pass.

Nine shifted raw clips (three offsets around each problematic position), each
repeated three times, and three digital-silence requests brought the total to
81 requests and 186.10 seconds. Raw silence returned two words in every repeat;
this is a model-level no-speech failure, not evidence that Daythread's own
silence suppression failed. Segment timestamps were present; temporal accuracy
has not yet been reviewed against the audio.

The private frozen corpus and results are retained on the Mac outside Git.
Full Large-v3 must replay those exact files before attributing differences to
the model. Further runs must separately change filtering, gain, decoding or
window boundaries. Do not mix those changes in a model-only comparison.

## Remaining evidence

- Full Large-v3 replay, repeated omission/repetition checks and timestamp review.
- Representative English and mixed-language material; Danish, quiet and
  overlapping speech coverage still needs listening-based review.
- Actual memory, swap, latency and throughput under concurrent text/TTS load.
- Authenticated gateway, Wyoming, OpenClaw and Daythread checks after cutover.
- Retirement of active old engines and installed monitoring/recovery policy.

There is no verified verbatim reference for the private meeting. Its external
summary is a coverage checklist, not a WER reference or speaker-identity proof.
No full-model accuracy improvement is claimed before these measurements.
