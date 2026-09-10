# Danish Wyoming STT on home-core

This project runs the official `rhasspy/wyoming-whisper` CPU image as the
stable `home-core` speech-to-text fallback. It serves Danish Faster Whisper at
`tcp://192.168.30.122:10300` with the higher-accuracy `small-int8` model, beam size
`1`, and persistent model storage in `/srv/state/wyoming-stt/models`.

The service name and state path describe the stable role, not the future GB10
address. A future primary STT service can therefore be added without renaming
or relocating this fallback.

The checked-in image is upstream version `3.7.0`, pinned to its multi-platform
OCI digest as resolved on 2026-09-10. See the upstream
[Wyoming Faster Whisper documentation](https://github.com/OHF-Voice/wyoming-faster-whisper)
and [official image tags](https://hub.docker.com/r/rhasspy/wyoming-whisper/tags).

## Deploy

The general home-core deployment installs the NixOS directory, environment,
and firewall policy, validates and pulls the digest-pinned image, but leaves
STT stopped until its model has been prepared. From the deployed release on
`home-core`, run:

```bash
sudo bash scripts/setup-home-core-stt.sh prepare
sudo bash scripts/setup-home-core-stt.sh up
sudo bash scripts/setup-home-core-stt.sh status
```

`prepare` starts a temporary fetch-only container on a dedicated network. It
downloads the model to the persistent directory, loads it, waits up to 15
minutes for the Compose-defined Wyoming-aware health check, records a readiness
marker, and removes the fetch container. The steady-state container then starts
with both `local-files-only` and Hugging Face offline mode, and does not need
network egress. No audio or transcript is written to the model directory.

This repository does not authorize an unattended live-host deployment. Publish
and deploy a reviewed commit using the documented Git deployment workflow,
then run the explicit preparation and start commands above.

## Verify

Confirm Compose state and the server's Wyoming `Describe`/`Info` exchange:

```bash
sudo docker compose --env-file /etc/homecompute/wyoming-stt.env \
  -f deploy/wyoming-stt/compose.yaml ps
sudo docker exec home-core-wyoming-stt \
  /usr/src/.venv/bin/python3 /opt/homecompute/wyoming-stt-health-check.py
sudo ss -lnt | grep '192.168.30.122:10300'
```

From a LAN client other than Home Assistant, connection attempts should fail.
From Home Assistant, add the **Wyoming Protocol** integration with exactly:

- Host: `192.168.30.122`
- Port: `10300`

Select the reported `small-int8` Faster Whisper engine in the Danish Assist
pipeline and test several short Danish commands, names, numbers, and a sentence
with background noise. Treat successful container health as service readiness,
not as Danish accuracy qualification.

## Upgrade and rollback

To upgrade the runtime, change `WYOMING_STT_IMAGE` to a reviewed version plus
immutable registry digest, validate the repository, publish the commit, and
deploy that exact commit. Re-run `prepare` if the configured model changes;
otherwise the persistent cache is reused. Then run `up` and repeat the Wyoming
and Danish speech checks.

Roll back by deploying the prior known-good commit using
`scripts/deploy-home-core.sh FULL_COMMIT_SHA`. Its image digest and settings are
restored while `/srv/state/wyoming-stt/models` remains intact. Do not delete the
model directory during rollback. If a model change is incompatible, set the
prior model in the rollback commit and run its `prepare` before starting it.

## LAN and firewall boundary

Wyoming has no client authentication or transport encryption. The LAN port is
therefore published only on `192.168.30.122`, and the NixOS `DOCKER-USER` policy
accepts new connections only from Home Assistant at `192.168.30.30` on TCP
`10300`. Do not forward this port, publish it on all interfaces, or expose it to
guest, IoT, Tailscale, or internet clients. Keep upstream UniFi policy equally
restrictive; the host rule is a second boundary, not a replacement for VLAN
policy.

The runtime bridge denies outbound connections except replies to established
Home Assistant sessions. The temporary model-fetch bridge allows DNS and
public HTTPS, rejects private and special-use destinations, and is used only by
the explicit `prepare` action. The optional Hugging Face Xet transport is
disabled so acquisition uses this predictable HTTPS path. Review
`iptables -S DOCKER-USER`,
`iptables -S HC-STT-INGRESS`, and both STT egress chains after a NixOS switch.
