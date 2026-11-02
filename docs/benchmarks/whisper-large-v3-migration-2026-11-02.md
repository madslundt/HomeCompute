# Whisper Large-v3 migration evidence — 2026-11-02

Full Large-v3 is serving from `5c4b046e5230001ae028064bf09117e6e4721cf9`.
The owner then requested deletion of the retained Hviske/Turbo assets;
`f86c16464a93d9d20ac00f74ebde70a2fabb5f27` performed that guarded retirement.
Both legacy model repositories, all five retained legacy containers and both
legacy HTTP image tags were verified absent. The full-model cache and healthy
serving containers remain. Whole-speech quality/capacity acceptance is open.

## Selection, pins and consumers

The owner selected full Large-v3 for Danish, English, mixed and unknown speech.
The model is `openai/whisper-large-v3` at
`06f233fe06e710322aca913c1bc4249a0d71fce1`: approximately 1.55B parameters,
32 encoder and 32 decoder layers. Turbo has four decoder layers. Publisher
sources: [OpenAI model list](https://github.com/openai/whisper#available-models-and-languages),
[pinned full artifact](https://huggingface.co/openai/whisper-large-v3/tree/06f233fe06e710322aca913c1bc4249a0d71fce1).

The 3,087,130,976-byte FP16 safetensors SHA-256 is
`a8e94b85976e5864ba3e9525c7e6c83b2a1eca42d4b797a0c7c24d778e40fd95`.
The NVIDIA vLLM image is pinned at
`sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d`.
The relay is `homecompute/stt-http:2.0.0`; runtime allocation is 8%, with one
sequence, 2,048 batched tokens, eager execution and a single admitted request.
The [owning lifecycle](../../deploy/whisper-stt/README.md) verifies immutable
source, offline cache manifests and the weight checksum.

The canonical alias is `whisper`. Compatibility names `stt-danish` and
`whisper-turbo` select that same full model, with no legacy serving/fallback
engine. All three returned HTTP 200 through the verified HTTPS gateway.
Daythread's revision-checked settings API saved `stt_model=whisper` and an empty
language-route map. Readback after a separate Daythread deployment confirmed
those values, speech filtering and quiet preservation enabled, and segment
timestamps disabled. The migration changed no saved recordings or transcripts.
The existing Wyoming STT adapter completed an authenticated round trip to the
new engine. Its historical `hviske-wyoming-proxy` name preserves Home Assistant's
endpoint; it contains no Hviske weights. Native Home Assistant microphone and
OpenClaw channel voice uploads were not exercised by this task. Their unchanged
STT API aliases were checked; channel-level acceptance remains separate.

The gateway was initially deployed through `homecompute deploy home-core` at
`5c4b046...`; a concurrent, separately owned TTS rollout later advanced core to
`f630fde...`. Spark's general host marker remains `b0e8b106...`, because its
normal wrapper does not reconcile speech. Actual STT image/command/mount labels
identify `5c4b046...`, and the retirement receipt identifies `f86c164...`.
Status/services/doctor/drift were inspected; these markers do not imply every
workload matches the newest `origin/master`. TTS changes were performed by
another chat and are outside this STT change.

## Same-input private meeting comparison

`benchmarks/daythread_stt.py` read ten original parts of a 1,122.28-second
recording without saving application transcripts. It froze exact WAV bytes,
SHA-256s and source offsets after the installed consumer's speech filtering,
gain and pause-aware 30-second windowing. Language replacement and optional
cleanup were disabled. Both passes requested verbose segment timestamps.

| Pinned model | Meeting requests | Summed request time | Mean | p95 request time |
| --- | ---: | ---: | ---: | ---: |
| Turbo, `41f01f3fe87f28c78e2fbf8b568835947dd65ed9` | 51 | 110.50 s | 2.17 s | 4.60 s |
| Full Large-v3, `06f233fe06e710322aca913c1bc4249a0d71fce1` | 51 | 198.36 s | 3.89 s | 6.06 s |

The full pass took 1.80 times as long in this run. Times exclude preprocessing;
p95 uses the nearest-rank estimator.
The co-resident TTS stack changed between passes: Turbo ran with Plapre, while
Plapre was independently retired before the full pass. This is a measured
same-input comparison, not an isolated hardware speed ratio. The earlier
89.16-second Turbo JSON-only pass is a separate observation.

Nine shifted raw clips, each repeated three times, and three silence requests
brought each pass to 81 requests. Total request time was 186.10 s for Turbo
and 380.40 s for full. Repeated outputs were stable within each pass. Maximum
consecutive identical lexical tokens were two for Turbo and three for full;
neither reproduced Hviske's severe repetition. No segment endpoint exceeded
its request audio bounds by more than 100 ms. Those structural checks do not
establish timestamp alignment accuracy or reliable speaker identity.

### Omission diagnosis

A frozen raw speech clip produced an empty full-model response on all three
verbose-timestamp repeats. Shifting the window five seconds either way
recovered speech. A one-variable decoding probe then found:

| Full-model request | Recognized words on that same clip |
| --- | ---: |
| `verbose_json`, automatic language | 0 |
| `json`, automatic language | 111 |
| `verbose_json`, explicit Danish | 0 |
| `json`, explicit Danish | 111 |
| `verbose_json`, temperature 0.2 | 123 in one probe |

This isolates timestamp decoding as a contributor; forcing Danish did not
resolve it. The 0.2 setting is unqualified and was not deployed. A bounded
beam-search probe timed out; the queue subsequently returned to zero and a
normal JSON request passed. Production decoding was left unchanged.

Removing Daythread's language override also removes its forced verbose
language-detection pass. Its ordinary requests now use JSON, which recovered
speech in this specific test. Optional timestamp mode retains the observed
limitation. Window/context sensitivity, quiet interjections and boundary
continuity still need listening-based review. No blind retry, transcript merge,
word invention or speaker assignment was introduced.

There is no verified verbatim reference for this private meeting. Its external
summary is a coverage checklist. Word counts do not prove relative accuracy,
so no meeting WER or blanket full-model quality improvement is claimed.

## Public reference and stress cases

`benchmarks/whisper_corpus.py` evaluated the existing pinned public
[Google FLEURS](https://huggingface.co/datasets/google/fleurs) corpus from
`config/spark-rollout-corpus.json`: revision
`168de341b3db6859a9bac1c50a2ef5e3b47647e0`, CC BY 4.0, archive SHA-256
`d673313799df319cf26873981d017458b58e91cc70320f1d66c3b59c405af06b`.
Run inside the existing Daythread backend with a staged verified corpus:

```bash
python - --corpus /private/corpus --output /private/results.jsonl < benchmarks/whisper_corpus.py
```

The script refuses changed manifest/audio hashes and writes a fresh private
result file. Requests used the full model, JSON, temperature zero, requested seed zero,
automatic language and no optional hints or cleanup. WER lowercases and removes
punctuation; it does not normalize spoken numbers into digits.

| Condition | Cases | WER |
| --- | ---: | ---: |
| Danish clean | 12 | 12.42% |
| Danish, 10 dB noise | 6 | 12.69% |
| English clean | 12 | 5.28% |
| English, 10 dB noise | 6 | 7.26% |
| Danish, synthetic 20 dB attenuation | 2 | 20.00% |
| Synthetic Danish/English concatenation | 4 | 60.12% |

The 24 clean utterances are the distinct source speech; noise, concatenated
switches and quiet copies reuse them. This is a small reference-backed sample,
not a population benchmark. The high language-switch error is a material
limitation. One synthetic overlapping bilingual case was not scored because
there is no verified output ordering; it returned 13 lexical words. One
10-second digital-silence case emitted one lexical word. Daythread's own
silence filtering was bypassed for that raw-model stress test.

Turbo was removed at the owner's request before this public suite; no fresh
Turbo WER comparison on this corpus is claimed. All private audio/transcripts
and raw experiment results remain outside Git.

## Resource and concurrent-workload observations

Before migration, Spark reported about 5.4 GiB available and 13 GiB swap in use
with text, both old STT engines and Plapre resident. Immediately after the STT
swap, with Plapre independently removed, about 16 GiB was available and 5.5 GiB
swap was in use. These host snapshots cannot isolate per-model peak memory.

With full Whisper, the existing text engine and the separately deployed
OmniVoice resident, a synchronized smoke ran eight repeated public STT requests
alongside one 1,473-input-token text request (119 output tokens) and sequential
Danish/English synthetic TTS. Every request passed. Mean STT latency was 1.52 s,
maximum 2.24 s; text took 7.00 s, and TTS took 0.79/1.11 s. The first STT request
overlapped text and TTS; later STT requests overlapped text or ran alone.
This is a bounded warm smoke, not maximum-context, sustained-concurrency or
24-hour qualification.

Twelve one-second host samples during STT recorded minimum available RAM of
10.38 GiB (8.53%) and zero new swap-out pages. About 5.5 GiB of prior swap
remained in use. This is below the earlier 10% whole-speech headroom gate.
Full-voice capacity remains unqualified with the newer co-resident TTS stack;
healthy requests alone do not waive that gate. GPU/process-specific memory
accounting remains unverified on GB10's unified memory.

## Validation and remaining acceptance

Repository validation, including containerized Nix evaluation, passed for the
migration and retirement releases. Focused tests cover aliases/parameter
preservation, cache selection, consumer migration, ownership/refusal, rollback
and retirement path safety. Exact staged and unstaged diffs were checked.
The public benchmark ran against the real adapter and records reference-backed
errors; it changes no production settings.

Remaining acceptance: mixed-language coverage, timestamp decoding/alignment,
quiet/overlapping meeting review, native channel/device voice paths, sustained
mixed load and whole-speech headroom. Historical rollback source remains in
Git, but restoring Hviske/Turbo now requires an explicit artifact refetch.
