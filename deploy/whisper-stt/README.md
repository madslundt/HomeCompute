# Whisper Large-v3 STT

All Danish, English, mixed and unknown-language STT uses full
`openai/whisper-large-v3`, revision
`06f233fe06e710322aca913c1bc4249a0d71fce1` (32 encoder and 32 decoder layers).
The 3,087,130,976-byte FP16 safetensors file must match SHA-256
`a8e94b85976e5864ba3e9525c7e6c83b2a1eca42d4b797a0c7c24d778e40fd95`.
The Hugging Face artifact declares Apache-2.0. Its NVIDIA vLLM 0.19 runtime
is pinned in Compose. TTS is outside this lifecycle.

The canonical gateway alias is `whisper`. Existing `stt-danish` and
`whisper-turbo` aliases now select the same full model; the latter name is
compatibility only. The single relay rewrites these names to
`whisper-large-v3`, preserving language, prompt, decoding and timestamp fields.
It retains the bounded upload/normalization policy and rejects unknown names.
Both loopback ports 8003 and 8007 reach that relay, preserving the restricted
core tunnels and Home Assistant's existing Wyoming endpoint on port 10301.
The historical core service name `hviske-wyoming-proxy` is compatibility naming;
it is a CPU protocol adapter, not a Hviske model or fallback.

The standard transcription route remains bounded to 60 seconds and 10 MB with
the existing normalization behavior. A separate source-owned
`/recording-stt/v1/audio/transcriptions` endpoint is available only to the
Daythread backend, requires the existing Whisper role credential, and accepts
complete mono 16 kHz PCM16 WAV up to 1,200 seconds / 40 MB. It preserves the
multipart body and the six CPP provenance headers. The exact qualified CUDA
image and its guarded promotion/rollback are documented in the
[whisper.cpp runbook](../whisper-cpp-stt/README.md); when rolled back, the
long-recording endpoint is unavailable rather than silently using vLLM.
The selected `hc4` CPP replacement is qualified for request determinism and
operational behavior. Known long-input omissions and language conversion remain
unresolved; this selection does not qualify transcription accuracy or complete
speech coverage. The later bounded-window `hc5`/`hc6` experiments are not selected.

## Opt-in vLLM runtime candidate

The NVIDIA vLLM 0.19 image pinned above is retained for rollback when CPP is
promoted. The explicit `prepare-runtime-candidate` action builds a profile-gated
candidate image from that same immutable image and the pinned source checksums
in `scripts/patch-vllm-whisper-runtime.py`. It applies two source edits:

- replaces the invalid Whisper prompt marker `<|prev|>` with the vocabulary
  token `<|startofprev|>`;
- preserves the full decoded plain text in `verbose_json` even when vLLM's
  timestamp parser returns no segment or drops an unclosed passage. It keeps
  only the parser's available segments and invents no timestamps.

The candidate is declared as `whisper-candidate` under the `candidate` Compose
profile. `prepare-runtime-candidate` builds the image but never starts it or
loads a model. The candidate shares the existing verified model cache and
serves the same pinned weights. For this bounded headroom qualification only,
the candidate uses `--gpu-memory-utilization 0.06`; the production primary stays
at `0.08`. Both retain one sequence, 2,048 batched tokens, and eager execution.
The lower candidate allocation is a measured headroom attempt, not a capacity
approval or a waiver: at least 12 GiB available host RAM is still required
before model load and at least 10% available host RAM after load. The candidate
must not run beside `whisper-primary`; both require the same GPU model. Keep
prompt and vocabulary hints disabled by default until the paired real-audio
qualification passes. Source patch tests prove anchor/checksum refusal and
text-preserving behavior; they do not qualify inference quality. The candidate
remains unpromoted until frozen prompted/plain and verbose/JSON comparisons,
full recording review, and capacity checks pass.

An independent full Large-v3 CPU reference candidate is documented in the
[whisper.cpp qualification runbook](../whisper-cpp-stt/README.md). It uses a
converted F16 GGML artifact and does not alter this vLLM lifecycle, production
alias, relay route, or client configuration. Its results must be compared
separately; model conversion is not byte-identical to the safetensors artifact.

Candidate failures print only a random diagnostic ID, stage, exception class,
allowlisted failure classification, executable basename, exit code, and
rollback state. Before rollback removes a candidate, its bounded command output
and candidate log tail are saved in
`/var/lib/homecompute/whisper-stt/candidate-diagnostic-ID.json` with mode
`0600`, under the root-owned state directory. This private receipt can contain
bounded exception and runtime log content; it is never printed or copied into the journal. Public
status is allowlisted and content-free. The read-only
`runtime-candidate-status` action reports this metadata without exposing the
private diagnostic file:

```bash
sudo python3 -I -B /tmp/homecompute-whisper-stt.OPERATOR/scripts/bootstrap-whisper-stt.py --action runtime-candidate-status --revision FULL_SHA
```

For a reviewed release that has already completed the normal `apply`, the
guarded runtime qualification swap is:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py prepare-runtime-candidate --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py runtime-candidate --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py runtime-candidate-rollback --revision FULL_SHA
```

Both actions acquire the existing Spark deployment, rollout, speech-capacity
and Whisper locks. The start action blocks new loopback STT connections, drains
the primary, checks the image labels, stops the NVIDIA primary before starting
the patched image, validates the relay route and enforces the existing memory
gates. The candidate temporarily advertises the stable `whisper-primary`
network alias so the relay and consumers use the same authenticated path.
Rollback stops and removes only the labeled candidate, then restores the exact
saved primary container and restart policy. If interrupted, run the rollback
action from the same immutable release; it recovers any journaled state. Always
run rollback after qualification. These commands never promote the candidate.

For Spark's interactive sudo boundary, the same actions are accepted by the
published bootstrap. From the operator checkout, stage the bootstrap bytes from
the exact reviewed commit on Spark and verify them locally first:

```bash
stage=$(mktemp -d /tmp/homecompute-whisper-stt.XXXXXXXX)
chmod 700 "$stage"
git show FULL_SHA:scripts/bootstrap-whisper-stt.py > "$stage/bootstrap-whisper-stt.py"
chmod 600 "$stage/bootstrap-whisper-stt.py"
test "$(shasum -a 256 "$stage/bootstrap-whisper-stt.py" | cut -d ' ' -f 1)" = \
  "$(git show FULL_SHA:scripts/bootstrap-whisper-stt.py | shasum -a 256 | cut -d ' ' -f 1)"
ssh home-spark "mkdir -m 700 $stage"
scp "$stage/bootstrap-whisper-stt.py" "home-spark:$stage/"
ssh -t home-spark "sudo python3 -I -B $stage/bootstrap-whisper-stt.py --revision FULL_SHA --action prepare-runtime-candidate"
ssh -t home-spark "sudo python3 -I -B $stage/bootstrap-whisper-stt.py --revision FULL_SHA --action runtime-candidate"
ssh -t home-spark "sudo python3 -I -B $stage/bootstrap-whisper-stt.py --revision FULL_SHA --action runtime-candidate-rollback"
ssh home-spark "rm -rf -- $stage"
rm -rf -- "$stage"
```

Use a published full SHA in place of `FULL_SHA`. Keep the staging directories
until rollback completes; if qualification is interrupted, run only the
rollback action before removing them.

## Publication and guarded installation

Run focused tests and `./scripts/validate-repository.sh`, review the exact diff,
and publish the selected full commit on `origin/master` before installation.
Never deploy from a mutable staging checkout. The normal
`./scripts/homecompute deploy home-spark --revision FULL_SHA` only establishes
the host release; it does not apply this speech project. This dedicated
lifecycle owns the model replacement:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py prepare --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py apply --revision FULL_SHA
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py status --revision FULL_SHA
```

For Spark's interactive sudo boundary, stage
`scripts/bootstrap-whisper-stt.py` from that exact published commit in a fresh
operator-owned mode-0700 `/tmp/homecompute-whisper-stt.XXXXXXXX` directory.
Verify its SHA-256 locally against source. The operator runs:

```bash
ssh -t home-spark 'sudo python3 -I -B /tmp/homecompute-whisper-stt.XXXXXXXX/bootstrap-whisper-stt.py --revision FULL_SHA --action install'
```

The bootstrap verifies a clean root-owned GitHub release and its own published
bytes. It changes no sudo policy or general host release pointers. Preparation
downloads only the exact FP16 model/tokenizer files, enforces the existing
cache/disk limits, accepts a root-owned checksum manifest and builds the relay.
It starts no GPU model.

Application refuses pending capacity and Docker maintenance overrides. It holds
the Spark deployment/capacity locks, blocks new loopback STT connections, waits
for existing connections and vLLM queues to drain, then stops both old stacks.
The previous containers/images/cache remain intact, with automatic restart
disabled. Only old Whisper container names move aside to make room for the
new project. A private write-ahead journal records their exact identities,
prior running states and restart policies. At least 12 GiB available host RAM
is required after stopping them. The starting capacity tuple is `.08`, one
sequence and 2,048 batched tokens; this is a candidate until hardware checks
pass, not inherited qualification from the older speech capacity experiment.

A failed/interrupted replacement restores the old containers and intake gate.
After startup, less than 10% available host RAM also triggers rollback.
For an explicit rollback or recovery after power loss/SIGKILL, run the same
published command with `rollback` (bootstrap `--action rollback`). Preserve the
journal and inspect any ownership/gate drift; never delete guards to retry.
Configuration rollback does not modify recordings or application data.

## Remove Hviske and Turbo

After full Large-v3 is healthy, the owner may explicitly remove the old engines
and their cached artifacts through the same published lifecycle:

```bash
sudo python3 -I -B /srv/homecompute/releases/FULL_SHA/scripts/setup-whisper-stt.py retire --revision FULL_SHA
```

The staged bootstrap accepts `--action retire` too. It checks the exact healthy
full model, original container identities, other references and cache types,
then removes the four retained old containers, the old Hviske Wyoming adapter,
both legacy Hugging Face repositories/manifests/locks and the Hviske compiler
cache. Unreferenced legacy HTTP images are removed without forcing shared image
deletion. The NVIDIA runtime used by full Whisper and all credentials remain.
Recordings and application data are outside its finite deletion list.

Retirement is journaled and resumable. Once the owner requests deletion, the
old-container rollback is refused; a historical release would require explicitly
preparing its model artifacts again. Historical Git source is retained.

## Gateway and consumers

After Spark's authenticated relay smoke and private audio checks pass:

```bash
./scripts/homecompute deploy home-core --revision FULL_SHA
# On core, from that release:
sudo python3 scripts/setup-network-inference.py --enable-full-whisper
sudo docker exec -i omi-local-backend-1 python - --apply < scripts/migrate-daythread-whisper.py
```

The credential migration adds only `whisper` to an existing reviewed scope;
compatibility aliases and unrelated embedding/TTS/video permissions remain.
Keys remain internal. Consumers with separate scoped keys may continue using
their existing compatibility aliases without a scope change.

The Daythread migration uses its existing revision-checked configuration API,
rejects unexpected models/endpoints or route-specific secrets, selects
`whisper`, and removes the reviewed Danish replacement route. It leaves the
provider endpoint/token and all audio processing options intact. It saves no
transcripts, summaries or recaps and never schedules retranscription. Its
non-secret procedure lives here; Daythread's application code remains owned by
its separate repository. Run without `--apply` for a read-only plan. If a route
has independently provisioned secrets or an unrelated custom override, review
that route rather than clearing it blindly.

Verify `homecompute status`, `services`, `doctor`, and `drift`; inspect actual
image IDs, command pins, offline manifests, project labels, listening ports and
stopped old engines. Confirm authenticated JSON and verbose JSON through the
HTTPS gateway for all three aliases, then test the existing Wyoming STT
endpoint and Daythread's effective selection. Check OpenClaw's STT consumer and
any Open WebUI/custom clients through their existing credentials. A matching
host revision alone proves none of these paths.

## Quality qualification

Use identical saved request WAVs to isolate the model change. Benchmark the
existing Turbo before replacement and full Large-v3 afterward, including the
same real meeting, repeated problematic clips, quiet speakers, overlap,
English, Danish, mixed language and silence. Separately vary speech filtering,
gain, window boundaries and decoding; never attribute a multi-variable change
to the model. Inspect continuity, omissions, repetition and timestamp bounds.
Retain original audio/provenance outside Git. A summary is a coverage checklist,
not a verified WER reference. Publish only sanitized timings/resource findings;
private transcripts and recordings stay in the private benchmark directory.

Large-v3 is the owner's selected replacement. Hardware qualification and honest
limitations still govern whether deployment succeeds. Do not claim measured
accuracy gains, timestamp quality, memory headroom or concurrency readiness
until those checks actually run.

## Home Assistant primary service identity

The core Wyoming adapter uses `STT_MODEL=whisper` and advertises `Whisper` with
OpenAI attribution. Its listener remains HA-only on core port 10301. The old
Compose service name is retained for firewall and deployment compatibility.
The adapter program is a read-only bind from the selected release, so an image
rebuild is not required for this protocol metadata update.

From a clean published release on home-core, run:

```sh
sudo python3 scripts/setup-ha-speech.py adapter --revision FULL_SHA
sudo python3 scripts/setup-ha-speech.py plan --revision FULL_SHA
sudo python3 scripts/setup-ha-speech.py apply --revision FULL_SHA
```

The lifecycle verifies the existing adapter source/image/environment before replacing only that
service. It reloads the existing Wyoming entry, changes its display title and
entity ID to `stt.whisper`, and updates pipelines using that primary recognizer.
OmniVoice's primary entry/entity display name becomes `OmniVoice`; TTS stays
`tts.omnivoice`. Fallback integrations and their enabled/disabled state remain
unchanged. No `.storage` files are edited and no integrations are deleted.

Private pre-change metadata and verification receipts are kept under
`/var/lib/homecompute/ha-speech`. Retain the prior release and adapter image.
Rollback uses its recorded Compose source for only `hviske-wyoming-proxy`, then
the native registry/pipeline APIs to restore the saved IDs, titles and selection
if required. Update the finite n8n speech-health entity reference through MCP
after the HA rename. Model inference and TTS runtimes are outside this lifecycle.
