# OmniVoice TTS replacement

The owner selected OmniVoice to replace Plapre for Danish-first household
speech and English. This independent project leaves STT and text runtimes
unchanged. Weights are CC-BY-NC; this configuration is for noncommercial
household use. The code is pinned to a full revision, artifacts are downloaded
and checksum-verified at build time, and inference is offline.

## Placement measurement

Run from a reviewed commit published on `origin/master`:

```bash
docker build -f deploy/omnivoice/Dockerfile.cpu -t homecompute/omnivoice:cpu-REVISION .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --cpus 4 --memory 8g --memory-swap 8g \
  --pids-limit 256 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  homecompute/omnivoice:cpu-REVISION --benchmark
```

Replace `REVISION` with the selected full commit. Use `sudo -n docker` on
home-core. The measurement synthesizes only fixed synthetic Danish/English
phrases in memory, logs timing and memory metadata, and retains no audio.
It uses a deterministically seeded synthetic voice; a reference recording and
an extra Whisper model are not required. These measurements select placement,
not native-listener pronunciation qualification or service readiness.

The existing MOSS/Piper CPU service is independent. Retain it during migration.
No general host release is needed to build this isolated image.

On Spark, `python3 scripts/setup-omnivoice.py benchmark-gpu --revision FULL_SHA`
builds the pinned NVIDIA ARM64 image and runs the same fixed in-memory test.
It installs torchaudio from the exact source revision against NVIDIA's Torch
ABI; a generic PyPI torchaudio wheel would replace the vendor Torch tuple.
The engine accepts only fixed Danish/English speakers and no reference paths.

The initial home-core test at `dede6633b4716d8e507ac53990ed717da9447473`
measured 15.659s for 1.880s of Danish audio and 23.791s for 2.690s, with peak
RSS about 2.5GiB. English exposed an optional-normalizer dependency, fixed by
using the existing Danish num2words normalizer only for Danish. These first
samples do not establish warm p95, but reject CPU placement for the intended
interactive latency. GPU measurement remains necessary.

## Retire Plapre

From the selected clean published checkout on each host, run:

```bash
# First, on home-core, preserve voice availability in both HA pipelines:
sudo -n python3 scripts/setup-omnivoice.py ha-fallback --revision FULL_SHA
# Then retire only Plapre's containers on each host:
python3 scripts/setup-omnivoice.py retire-plapre --revision FULL_SHA
```

Use `sudo -n python3` on home-core. The Spark operator already has Docker
access. This command inspects the exact Compose project/service/name tuple
before any mutation, records image/container identities in private operator
state, disables restart, stops, and removes only those containers. It is
idempotent after successful retirement. It never prunes volumes or images.
Do not run general speech deployment during the replacement: old published
releases still contain Plapre. Consumer routing is migrated by the replacement
lifecycle before normal releases resume.

The retained previous release and image can recreate Plapre only through its
documented lifecycle, with existing external environment files. That rollback
is explicit and is not performed automatically after the owner requested
Plapre removal. MOSS/Piper remains independent.

## Dedicated installation and clients

Placement is Spark GPU inference, with home-core adapters and independent CPU
MOSS/Piper fallback. The initial GPU test at
`4a9a5a73552944faf1313a15ed365133e689200b` measured warm Danish 0.594–0.694s
and English 0.566–0.591s for the fixed short phrases; the first Danish request
took 2.267s. Peak process RSS was 3.13GiB, not total shared GPU memory. These
voice-design measurements precede the stable synthetic-reference service.
Native Danish pronunciation, long-form reliability and loaded p95 remain gates.

All commands below run from the same clean published full-SHA checkout. Core
uses root; Spark uses the existing Docker-authorized operator. `apply` checks
active speech-capacity, speech-finalization and Docker maintenance receipts and
refuses pending overrides; never delete a receipt to bypass it. It does not
restart text or STT, adjust their memory, change host networking, or set the
normal host release pointer. Every container carries its dedicated revision.

```bash
# home-spark: build and start the internal GPU engine and loopback HTTP edge
python3 scripts/setup-omnivoice.py apply --revision FULL_SHA
python3 scripts/setup-omnivoice.py smoke --revision FULL_SHA
# home-core: adapters on the existing HA address/port and private gateway network
sudo -n python3 scripts/setup-omnivoice.py apply --revision FULL_SHA
# Rebind only LiteLLM to this release; require its existing pinned image/entrypoint.
sudo -n python3 scripts/setup-omnivoice.py apply-gateway --revision FULL_SHA
# Change only the exact tracked trusted speech module; retain communication policy.
sudo -n python3 scripts/setup-omnivoice.py apply-bridge --revision FULL_SHA
# Create the native Wyoming integration, update the two retired HA pipelines,
# preserving their fresh STT/conversation settings, then remove the Plapre entry.
sudo -n python3 scripts/setup-omnivoice.py ha-apply --revision FULL_SHA
```

The gateway key migration removes only `plapre` and adds `omnivoice`, retaining
full Whisper, compatibility, embedding and video access. The authenticated
OpenAI endpoint accepts `model=omnivoice`, `voice=danish-default` or
`english-default`, and WAV output. The Wyoming API advertises Danish and English
with the same fixed voices. Concurrency is one at the inference engine. Voice
references are generated synthetically at startup with a fixed seed and never
accept caller-provided audio, paths, speaker IDs or instructions.

Run the selected release's `scripts/setup-openclaw-speech.py --replace-plapre`
inside the existing sandbox as its configuration owner, then repeat with
`--apply` after its native schema check passes. It updates only the two managed
root TTS model fields and keeps current STT, credentials and other settings.
Use the existing native supervisor to reload/restart if required; retain its
private `speech-setup-backups`. No channel message is needed for verification.
Daythread has no TTS configuration and needs no TTS migration.

Reconcile the monitoring registry and readiness bundle with the documented
`setup-platform-observability.py` hash-bound lifecycle. It installs the new
container names and probes `OMNIVOICE_TTS_BASE_URL`; verify its private registry
against source. Docker image tracking marks locally built speech images as
source pins rather than proposing mutable upstream tags.

After installation, check both container projects, revision labels, health,
raw PCM metadata, authenticated gateway Danish/English WAVs, HA engine/voice
readback and a synthetic HA synthesis without playback. Check
`homecompute status`, `services`, `doctor`, and `drift` as well. The normal host
revision marker alone does not prove this dedicated workload is installed.

## Recovery

Keep both prior releases and locally built images. For a failed OmniVoice
revision, select the previous published OmniVoice revision, run its dedicated
`apply` on each host and verify before migrating clients. Use the private
`gateway-before.json` source-mount receipt to review a gateway rollback. Restore
the trusted bridge only from its saved exact predecessor or a reviewed source
release, with the adapter stopped, and retain the new private settings backup.

For immediate Danish availability, run the selected published release
`ha-fallback` action on core. It selects the existing `tts.piper` voice
`da_DK-talesyntese-medium` for Plapre/OmniVoice pipelines while retaining STT and conversation settings;
Mattermost retains its local Danish CPU fallback and text replies. English has
no qualified CPU fallback. Restoring Plapre is an explicit owner-selected
rollback through its historical guarded lifecycle; its retired profile is
excluded from normal speech startup and the current Plapre start command
refuses activation. Never prune model/data volumes as part of this migration.
