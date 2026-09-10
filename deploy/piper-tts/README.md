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
