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
interactive latency. GPU results and the eventual stable-reference service are recorded below.

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

The project declares explicit nonoverlapping Docker subnets: Spark edge
`10.253.71.0/28`, Spark internal inference `10.253.72.0/28`, and core internal
inference `10.253.73.0/28`. Core's existing edge remains external. Do not rely
on Docker's automatic address pool: this host has enough retained projects to
allocate `192.168.0.0/20`, which overlaps the operator workstation network.
If a first start fails before any container runs, connect through home-core
(`ssh -J home-core home-spark`) and run the selected published release's
`reset-failed-networks --revision FULL_SHA`, then `apply`. Recovery first saves
bounded network/container metadata, refuses unknown or running containers,
and removes only this new project's stopped containers and networks. It never
changes host routes, firewall rules or other projects.

## Observed rollout, 2026-11-02

The engine and both core adapters run dedicated revision
`6a6155822a76e4bd729ab7fa5dec315f260ca4c8`. The authenticated gateway uses that
release's generated routes and both native HA pipelines use `tts.omnivoice`.
Plapre containers on both hosts and its HA integration were removed; rollback
images and data remain. Core's CPU fallback stays independently available.

| Measurement | home-core CPU test | Spark GPU, stable synthetic references |
| --- | --- | --- |
| Danish synthesis | 15.659–23.791s for 1.88–2.69s audio | warm 0.453–0.526s for 1.93–2.69s audio |
| English synthesis | not completed in initial CPU run | warm 0.470–0.476s for 1.68s audio |
| Peak process RSS | about 2.5GiB | about 3.1GiB |
| Torch CUDA memory | none | 1.9GiB allocated, 2.95GiB reserved, 2.65GiB peak allocated |
| Container allocation | 4 CPUs, 8GiB cap | 4 CPUs, 12GiB cap; Docker reported about 2.7GiB used |

RSS, CUDA allocator statistics and container accounting overlap differently on
GB10 shared memory; do not add these numbers into a supposed total. The stable
reference test loaded in 22.846s. The installed raw-HTTP smoke took 0.614s Danish
and 0.488s English. Authenticated HTTPS synthesis took 0.541s Danish and 0.604s
English in another sample; both passed the existing recognition-match check.
These are short synthetic samples with the text engine resident, not loaded
p95, throughput, long-form reliability or native-listener quality qualification.

HA-generated Danish/English PCM matched direct OmniVoice output exactly, without
speaker playback. HA's streaming WAV intentionally declares zero frames in its
header; validate its actual PCM payload rather than treating that streaming
header as an empty buffered WAV. The native OpenClaw Mattermost TTS conversion
also produced a valid WAV after the supported gateway restart:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw gateway restart --quiet
```

This restart is required after the native model migration; schema validation
and writing the candidate alone do not establish that the gateway uses it.
The synthetic native conversion took 3.654s including CLI, bridge and recognition
verification, for 2.0s audio. No message was sent to a channel. Existing native
conversations, STT selection and credentials were preserved.

`status`, `services`, `doctor` and `drift` were inspected separately. The regular
host markers track different lifecycles (core `f630fde`, Spark `b0e8b10` at this
observation), so they do not establish this dedicated TTS rollout. Revision
labels, actual image/configuration, healthy containers and the client probes
above establish the TTS state. Retained, stopped rollback containers are not
required services. Ancillary image-tracking and disabled-recovery declarations
follow their maintenance/Nix lifecycles rather than this Docker-only installer.

The final monitoring check exposed HTTP 429 when its two STT probes reached the
same single-request Whisper relay in parallel. The readiness probe now checks
those two routes sequentially while checking other dependencies concurrently;
both STT routes remain required, and the eight-second STT bound fits inside its
nine-second process budget. The changed probe passed against the live services.
Install its exact published hash through `setup-platform-observability.py` and
verify the receipt and resulting gauges; do not mark the platform ready merely
because synthesis passed. This does not resize or restart the Whisper model.
