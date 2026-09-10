# Danish Piper TTS on home-core

This project serves `da_DK-talesyntese-medium` over Wyoming at
`tcp://192.168.30.122:10200`. It is the only Danish voice in the official Piper
catalogue and therefore the highest-quality official Piper option currently
available for Danish. The model runs efficiently on `home-core`'s CPU.

The image, voice repository revision, and all three downloaded artifacts are
pinned. The container runs non-root with a read-only filesystem and no runtime
internet access. Wyoming does not authenticate clients, so the host
`DOCKER-USER` policy permits only Home Assistant at `192.168.30.30` to reach
the LAN publication. A loopback publication is retained for host smoke tests.

Prepare and start the service on `home-core` from a published release:

```bash
sudo bash scripts/setup-home-core-piper.sh prepare
sudo bash scripts/setup-home-core-piper.sh up
sudo bash scripts/setup-home-core-piper.sh status
```

In Home Assistant, add the **Wyoming Protocol** integration using host
`192.168.30.122` and port `10200`. Then select
`da_DK-talesyntese-medium` in the Danish Assist pipeline.
