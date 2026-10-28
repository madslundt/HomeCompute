# Hviske Danish STT on home-spark

## Wyoming placement (2026-10-28)

Spark serves model inference and private HTTP ingress. Wyoming runs on
`home-core` in the existing `hviske-wyoming-proxy` service; the historical
service name and Home Assistant endpoint `192.168.30.122:10301` stay stable.
The core adapter retains the Danish-only alias, 60-second/10 MB audio limits,
four-connection bound, and file-mounted compute credential. It reaches Spark's
loopback HTTP relay through the restricted SSH forward on core port 18301.
The `legacy-wyoming` Spark profile exists only for rollback and is not started
by the normal `up` lifecycle. The pinned inference engine remains unchanged.

The migration is source-defined and must run from the same published full SHA
on both hosts. Do not use an uncommitted checkout or a general host release
just to migrate these adapters. Once `/srv/homecompute/releases/$RELEASE_SHA`
has been materialized from GitHub on both hosts, run the commands there:

```bash
# On home-spark: add private Plapre HTTP ingress and exact forwarding grants.
sudo python3 scripts/setup-speech-boundary.py spark-prepare --revision "$RELEASE_SHA"
# On home-core: build CPU adapters and stage the dynamic tunnel owner.
sudo python3 scripts/setup-speech-boundary.py core-stage --revision "$RELEASE_SHA"
# Confirm speech models are idle; HTTP requests drain automatically.
sudo python3 scripts/setup-speech-boundary.py core-apply --revision "$RELEASE_SHA" --tunnel-idle-confirmed
sudo python3 scripts/setup-speech-boundary.py core-smoke --revision "$RELEASE_SHA"
# Only after synthetic STT and TTS pass on core, retire the three old adapters.
# On home-spark:
sudo python3 scripts/setup-speech-boundary.py spark-retire --revision "$RELEASE_SHA" --core-verified
```

`core-stage` holds the deployment lock and preserves the old adapter release in
a private rollback receipt, so staging cannot replace a plan during cutover.
It also derives a standalone, image-pinned rollback document for each previous
service from that service's exact immutable source. Historical full projects may
still require retired Caddy `HERMES_FQDN/HERMES_UPSTREAM` address fields during
parsing. Two distinct public parser-only address pairs must produce identical
selected services; those fields and all unrelated services are discarded.
The resulting documents reference only the already-owned external edge network
and each original read-only helper, with no retired service dependencies.

Staging checks actual entrypoint/command, image, environment, mount modes,
published ports, network/IP and baseline health against that old source. Before
gating traffic or changing the tunnel, `core-apply` verifies the private plan's
hashes, original Git source/helper bytes, existing images/network, standalone
Compose rendering and unchanged baseline container IDs. Existing legacy
`before.json` receipts are upgraded without replacing their original release or
environment provenance. Rollback uses only these selected-service documents,
disables pulls/builds/dependency startup and never parses the retired project.

Its managed systemd drop-in resolves the immutable Nix tunnel source at every
start, changes only the reviewed speech forwards and preserves the existing
text-admission route. It does not restart models or the tunnel. `core-apply`
restarts the drained tunnel and only the two core adapters; readiness requires
both Wyoming Describe and the inference dependency's HTTP health. `core-smoke`
uses a short synthetic silence clip and fixed Danish phrase, retaining neither
transcript nor audio. Verify the real Home Assistant canary separately.

`core-apply` holds the normal deployment lock throughout drain, cutover and
rollback, excluding NAS cold preparation and normal deployments. Its temporary
`HC-SPEECH-CUTOVER` chain in mangle PREROUTING drops only new TCP connections from
`172.28.200.3` to `172.28.200.1` on ports `18003,18005,18006,18007`; existing
connections continue through the unchanged firewall policy. The earlier mangle
hook keeps this gate effective when the canonical tunnel startup rebuilds or
prepends filter INPUT rules. Clients opening new connections wait for their
normal TCP timeout/retry delay while maintenance is active. The installer
requires three consecutive samples with no active sockets on those HTTP
forwards before changing the active runner or restarting the tunnel. Persistent
Wyoming connections on `18201/18301` are excluded: the explicit confirmation
still requires speech model activity to be checked separately.

The default drain deadline is 120 seconds; `--drain-timeout 600` allows at most
ten minutes. A busy deadline aborts before tunnel changes and never terminates
requests. Failure, SIGTERM, SIGINT and SIGHUP remove only the exact temporary
jump and chain; interrupted cutovers attempt rollback while the gate remains
in place. SIGKILL or power loss requires inspecting a surviving gate before
retrying; an existing chain is refused rather than overwritten. When selecting
a new installer release, rerun `core-stage` for that same SHA before applying,
even if the CPU image was already built by an older release.

The real firewall regression runs entirely inside disposable local Docker
namespaces, with no host network, Docker socket or writable host mount. It
keeps an established synthetic TCP stream alive while rebuilding and prepending
the canonical filter rules, proves the old INPUT placement could be bypassed,
and proves the mangle gate still blocks new connections:

```bash
docker build -t homecompute/speech-cutover-test -f tests/fixtures/Dockerfile.speech-cutover tests/fixtures
python3 tests/speech-cutover-firewall-test.py
```

For rollback, first run `spark-rollback` on Spark to restore the saved legacy
containers, drain requests, then run `core-rollback --tunnel-idle-confirmed` on
core, passing the same `--revision`. The old CPU adapters and Wyoming forwards
are restored; the model engines and their artifacts are untouched. Piper and
CPU Faster Whisper remain independent. The receipt is not a data backup.

The canonical Nix source now uses raw inference ports 8004/8003 for the speech
adapters, with exact source-IP policy on the dedicated link. Physical-link
cutover is a separate verification: keep the SSH transport until carrier,
addresses, private bindings, both firewalls and synthetic protocol checks pass.
The HTTP relays remain loopback-only during SSH migration; a connected cable
does not automatically change active routes or expose model listeners.

The original staged deployment and license gates below remain applicable to
the model; their Spark Wyoming topology describes the rollback profile.

This is an opt-in, isolated Danish speech-to-text profile. It does not replace
the CPU Faster Whisper fallback on `home-core`, alter the existing modality
scaffold, or enable a Home Assistant route by itself.

The runtime tuple is intentionally separate:

- `syvai/hviske-v5.3` at commit
  `5d1a09822018702dc51d763e3a867b62d26b3501`;
- NVIDIA vLLM 26.04 ARM64 at image digest
  `sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d`;
- vLLM 0.19.0 and Transformers 4.57.6, matching the publisher-tested service
  path;
- Danish, deterministic `/v1/audio/transcriptions` requests with
  `language=da` and `temperature=0`.

The model publisher documents the vLLM 0.19 launch and OpenAI transcription
endpoint in the [Hviske v5.3 model card](https://huggingface.co/syvai/hviske-v5.3).
NVIDIA documents the packaged versions in the
[26.04 release notes](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-04.html).

## Network boundary

`hviske-primary` has no host port. The standard-library adapter is its only
client and publishes Wyoming on `10301/tcp`. During preparation it binds
`127.0.0.1:10301`; promotion changes that exact bind to
`10.77.10.10:10301`. The shared compute firewall must already allow only
`10.77.10.2/32` to that destination before
`HVISKE_PRIVATE_INGRESS_CONFIRMED=true` may be set.

The adapter advertises one ASR program and one Danish model, requires external
VAD, accepts at most 60 seconds or 10 MB of PCM, rejects non-Danish/model
requests, preserves the incoming PCM format in a bounded WAV, and authenticates
to vLLM with the existing file-mounted API key. It admits at most four active
Wyoming connections, writes neither audio nor
transcripts to disk and vLLM request/access logging is disabled.

## License gate

Hviske v5.3 is CC BY-NC 4.0. The default
`HVISKE_LICENSE_DECISION=review-required` permits validation and artifact
preparation but blocks service activation. Set it to
`personal-noncommercial` only after confirming the deployment is personal and
noncommercial. Employer, work, or commercial use requires documented SYVAI
approval and the value `syvai-commercial-license-approved`.

## Prepare without routing

On `home-spark`:

```bash
sudo install -m 0640 -o root -g gb10-ai \
  config/hviske-stt.env.example /etc/gb10-ai/hviske.env
sudo editor /etc/gb10-ai/hviske.env

sudo ./scripts/setup-compute-hviske-stt.sh validate
sudo ./scripts/setup-compute-hviske-stt.sh preflight
sudo ./scripts/setup-compute-hviske-stt.sh prepare
```

`prepare` downloads the exact public revision through the bounded cache helper,
then atomically accepts an integrity manifest. It does not start either service.

## Stage and qualify

After the license decision is reviewed, start on loopback first:

```bash
sudo ./scripts/setup-compute-hviske-stt.sh up
sudo ./scripts/setup-compute-hviske-stt.sh smoke
sudo ./scripts/setup-compute-hviske-stt.sh status
```

Run Danish clean/noisy/name/address/device fixtures and representative mixed
GPU load before promotion. `smoke` proves Wyoming Describe plus an actual
bounded transcription request; it is not accuracy or production evidence.

After the physical private link and exact firewall rule pass, change only:

```dotenv
GB10_BIND_ADDRESS=10.77.10.10
GATEWAY_CIDR=10.77.10.2/32
HVISKE_PRIVATE_INGRESS_CONFIRMED=true
```

Then rerun `validate`, `up`, and `smoke`. On `home-core`, start the guarded
`hviske-wyoming-proxy` only after its upstream health check passes. Home
Assistant registers the LAN relay at `192.168.30.122:10301`; it must not route
directly to the compute-only `10.77.10.10` address. Select the advertised Dansk
model first in a canary Assist pipeline and retain the existing Faster Whisper
entity as rollback. Promotion remains blocked until the link, both firewalls,
relay, model qualification, and license gates all have evidence.

To stop only this profile while retaining artifacts:

```bash
sudo ./scripts/setup-compute-hviske-stt.sh down
```

## Same-model speech capacity qualification

`scripts/qualify-speech-capacity.py` stages an explicit candidate that changes
only Hviske's GPU budget from `0.10` to `0.05` and Plapre's from `0.06` to
`0.05`. Images, checkpoints, sequence counts, context, voice and tempo stay
unchanged. The normal configuration remains the rollback baseline until the
candidate has measured evidence and is reconciled into the canonical settings.
This does not change Flash or move speech inference to the CPU.

Identity checks project Compose's serialized `$$` into the single `$` received
by Docker's Engine command, exactly once on the expected declaration. This
preserves the existing file-based API-key assignment. Observed commands remain
untouched, and every other byte, model flag, authentication path and image must
still match. The regression creates and inspects stopped public fixture
containers from both original and rendered Compose documents; it runs no model
or fixture shell command and removes its unique test project afterward.
Recovery-marker cleanup also requires a running, healthy container with the
exact saved image and projected command. Stale health on a stopped container,
an unrecognized image or unreadable state keeps the marker for recovery.

Run from a reviewed, published immutable release on `home-spark`, with root
access. Use the pinned public FLEURS corpus prepared by
`benchmarks/whisper_cpu_benchmark.py`; the capacity benchmark accepts only its
12 clean and 6 noisy Danish clips and verifies every audio hash. Keep at least
one representative LLM request active throughout the concurrent ASR/TTS trials.
Four concurrent ASR requests preserve the existing sequence contract; one TTS
stream runs at the same time. The benchmark stores only timings, word-error
counts, hashes and capacity metadata; it does not store transcripts, audio,
prompts or credentials.

```bash
speech_release=/srv/homecompute/releases/FULL_COMMIT_SHA
speech_evidence=/var/lib/homecompute-speech-capacity/evidence
sudo python3 "$speech_release/benchmarks/speech_capacity_benchmark.py" \
  --corpus /PATH/TO/PINNED_PUBLIC_CORPUS --label baseline \
  --output "$speech_evidence/baseline.json"
sudo python3 "$speech_release/scripts/qualify-speech-capacity.py" plan
```

Pause speech intake and verify existing speech requests have finished before
applying the exact plan hash. This replaces only the two speech engines and
waits for healthy readiness. A partial activation attempts both necessary
restorations; an unsuccessful restoration retains the active receipt for repair.

```bash
sudo python3 "$speech_release/scripts/qualify-speech-capacity.py" apply \
  --plan-sha256 REVIEWED_PLAN_SHA256 --speech-idle
```

Resume speech intake and repeat the same concurrent workload:

```bash
sudo python3 "$speech_release/benchmarks/speech_capacity_benchmark.py" \
  --corpus /PATH/TO/PINNED_PUBLIC_CORPUS --label candidate \
  --baseline "$speech_evidence/baseline.json" \
  --output "$speech_evidence/candidate.json"
sudo python3 "$speech_release/scripts/qualify-speech-capacity.py" status
```

`capacity_qualified` requires at least 10% available memory at every sample with
active LLM inference, zero speech failures, unchanged model/image/voice/context
and concurrency, and no more than one percentage point of ASR word-error
regression against the same-corpus baseline. ASR and TTS p95 latency and RTF must
not regress by more than 10%, allowing a 100 ms timing margin or 0.01 RTF.
Absolute p95 RTF at most 0.5 and first audio at most 750 ms are reported separately;
a capacity improvement does not turn an existing voice-SLO miss into a pass.
A failed or incomplete capacity run requires rollback: pause intake, drain
speech, and restore:

```bash
sudo python3 "$speech_release/scripts/qualify-speech-capacity.py" rollback --speech-idle
```

Receipts deliberately report `accepted: false`: full Wyoming/Home Assistant
voice latency, fallback/recovery, and the canonical configuration promotion are
separate gates. Do not perform an ordinary speech redeployment while a capacity
candidate is active. Reconcile the successful budget and validators in source,
publish and apply that revision through the speech lifecycle, verify actual
commands and images, then retire the superseded private override receipt.

## Supervised Spark rollout when sudo needs a password

The operator stages only the reviewed `scripts/bootstrap-spark-rollout.py` and
external public `corpus.tar` in a private `0700` directory named
`/tmp/homecompute-spark-rollout.XXXXXXXX` on Spark. Verify the bootstrap SHA-256
against its published Git revision before running it. The fixed bootstrap
fetches only `https://github.com/madslundt/HomeCompute.git`, verifies the selected
full SHA is on `origin/master`, creates a root-owned immutable release below
`/srv/homecompute/releases`, and execs that release's wizard. Existing dirty,
foreign or mismatched releases are refused. Mutable checkouts are untouched.

The single human command uses the real published SHA and staging path supplied
by the coordinator:

```bash
ssh -t home-spark 'sudo python3 /tmp/homecompute-spark-rollout.XXXXXXXX/bootstrap-spark-rollout.py --revision FULL_COMMIT_SHA --corpus-archive /tmp/homecompute-spark-rollout.XXXXXXXX/corpus.tar'
```

Sudo requests the password once through the terminal. Neither the wizard nor
its receipts read, save, transmit or change it. No package, sudo policy or
privileged remote control service is installed. The reusable wizard library is
unchanged; its stages verify source/corpus, review the maintenance window,
measure/review/apply the hash-bound plan, then summarize remaining acceptance.

The corpus is the exact external 14,848,000-byte public archive pinned by
`config/spark-rollout-corpus.json`; only its 12 clean and 6 noisy Danish WAVs
are extracted, from a hash-verified in-memory snapshot. Root-only receipts live
below `/var/lib/homecompute-spark-rollout`. The runner prepares raw HTTP ingress,
rejects new model-bound connections while existing speech drains, and pauses
only previously running Hviske/Plapre ingress containers. Model-local trials
remain reachable on container loopback. Two bounded synthetic local LLM streams
supply comparable concurrent load; generated text is discarded and Flash's
configuration is untouched.

After the baseline, type `APPLY` for the exact displayed plan within 120 seconds.
Only the two speech engines are replaced. A failed, incomplete, declined or
interrupted trial attempts baseline rollback and restores only the ingress
containers that were running before maintenance. Cleanup failures retain
truthful failure receipts and the capacity helper's recovery marker; a stale
maintenance firewall chain is refused rather than overwritten. SIGKILL or power
loss cannot run process cleanup and requires inspection of those records before
retrying. Do not treat a missing terminal response as success.

A passing result remains `accepted: false` and `full_voice_accepted: false`.
The measured capacity override needs canonical source reconciliation; absolute
voice latency, the full Home Assistant path and recovery acceptance remain
separate gates. Core adapter cutover and legacy Spark retirement happen only
after the separate synthetic core protocol checks pass.

After the human run, the same SSH account can read its private staging
`rollout-result.json` without sudo. The report is root-owned mode `0644` inside
the original user-owned `0700` staging directory, created with exclusive,
no-follow semantics. It exposes only the source SHA, finite capacity/latency
flags, observed budget/headroom metadata, rollback state and the private
receipt path/hash. It contains no plan objects, env values, prompts, transcripts
or audio. The coordinator verifies ownership and schema before using it. A
missing/malformed report remains unverified; use a fresh staging directory for
each attempt rather than overwriting any earlier result.

If raw ingress preparation fails, the runner reports a finite error category
such as `invalid-mount`, `port-conflict`, or `image-unavailable`; Docker output
and environment values remain suppressed. This step precedes ingress pausing
and capacity replacement. Inspect actual service health and the finite result
before retrying from a new staging directory with a reviewed release. The
rendered ingress tmpfs is also checked with an isolated Docker create/remove
test, because Compose rendering alone accepts malformed relative mount paths.

Capacity planning explicitly enables only the `hviske` profile for read-only
Compose rendering; inactive model/legacy profiles keep their existing admission
rules. The wizard verifies a read-only, approval-gated plan during preflight and
again before measuring the baseline, then requires the same plan after the
baseline. A rejected plan cannot consume an expensive speech benchmark or replace
an engine. Fixed stage/service/reason categories survive privileged subprocesses
into the finite public failure report, without raw commands, env values or Docker
output. Current license/voice approval, exact runtime identity and all capacity
qualification gates remain required.

Rollback alone may recognize an authoritatively stopped or absent model as idle
after an interrupted replacement. It first verifies Docker inventory and exact
running-state booleans; every running model still needs the strict activity
probe. The rollback intake gate also tolerates those stopped/absent endpoints,
then restores only the saved baseline images/configs. Unknown Docker state or
unreadable running activity keeps recovery unverified and retains the marker.
