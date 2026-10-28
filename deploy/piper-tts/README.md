# Danish MOSS-TTS-Nano on home-core

This project serves MOSS-TTS-Nano ONNX over Wyoming at
`tcp://192.168.30.122:10200`. The advertised voice is `da_DK-moss-nano`; it uses
MOSS's built-in `Adam` reference, which the upstream project uses for its Danish
demo. If MOSS cannot synthesize a request, the gateway automatically sends the
same text to the internal `da_DK-talesyntese-medium` Piper service.

MOSS source and both model repositories are pinned to immutable revisions and
baked into the locally built image. Piper's image, voice repository revision,
and downloaded artifacts remain pinned. Both containers run non-root with
read-only filesystems. Their dedicated bridge has deny-all runtime egress.
Wyoming does not authenticate clients, so the host `DOCKER-USER` policy permits
only Home Assistant at `192.168.30.30` to reach the LAN publication. A loopback
publication is retained for host smoke tests.

Prepare and start the service on `home-core` from a published release:

```bash
sudo bash scripts/setup-home-core-piper.sh prepare
sudo bash scripts/setup-home-core-piper.sh up
sudo bash scripts/setup-home-core-piper.sh status
```

In Home Assistant, add or reload the **Wyoming Protocol** integration using host
`192.168.30.122` and port `10200`. Then select `da_DK-moss-nano` in the Danish
Assist pipeline. No second endpoint is required for fallback.

## Recovery after a boot-time network failure

Docker can attempt the LAN publication before its address exists. If MOSS has
exited with `cannot assign requested address`, the published recovery helper
waits for the LAN address, then starts that existing container. It also waits
for the exact Immich CIFS source before recovering its failed library mount.
Running containers, deliberately stopped containers, and unrelated application
failures are left alone. The helper retries a failed, already-configured TTLock
Funnel without enabling a stopped service or changing its target.

Run from the selected immutable release on home-core:

```bash
sudo python3 scripts/home-core-service-recovery.py check
sudo python3 scripts/home-core-service-recovery.py install \
  --expected-sha256 "$(sha256sum scripts/home-core-service-recovery.py | cut -d ' ' -f 1)"
sudo systemctl start homecompute-service-recovery.service
systemctl status homecompute-service-recovery.timer
```

Review the source and its hash before installation. The dedicated installer uses
the existing NixOS attached-unit lifecycle and preserves a previous installed
helper and units under `/opt/homecompute/service-recovery/rollback`. The NixOS
`service-recovery.nix` declaration owns the equivalent service for full host
releases. To roll back the dedicated lifecycle, stop its timer and service,
restore the retained attached units and helper, then reload systemd. Neither
rollback nor recovery replaces model images, databases, credentials, or media.
