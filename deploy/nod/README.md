# Nod on home-core

Nod v0.7.6 runs in its own Ubuntu KVM guest with two vCPUs, 8 GiB RAM and a
60 GiB sparse disk under `/srv/state/nod-vm`. The guest owns its Docker daemon
because upstream Nod's runtime proxy needs the Docker socket. Set up one owner
and use public or synthetic data while qualifying the browser and approval
behavior. The VM is started on demand and requires 4 GiB of host memory
headroom above its allocation. The browser and workspace retain their upstream
firewall. The guest can reach the public internet and the dedicated model edge.

## Recover memory

Confirm that the preferred Home Assistant pipeline uses Spark-backed
`stt.whisper` and `tts.omnivoice` and that both Wyoming relays on `home-core`
are healthy. With no active client using CPU fallback, remove the two dedicated
Compose projects from a reviewed published release on `home-core`:

```bash
sudo bash scripts/setup-home-core-piper.sh down
sudo bash scripts/setup-home-core-stt.sh down
```

The models, images and Home Assistant integrations are retained for manual
rollback. Normal releases leave the CPU fallbacks under their dedicated
lifecycle. Recheck the preferred pipeline, both Wyoming relays, and host
`MemAvailable` after the removal. If voice needs CPU fallback, stop the Nod
guest first if necessary, start the required speech project with its `up`
command and select its existing Wyoming integration in Assist.

## Install

1. Deploy the reviewed HomeCompute release to `home-core` and start
   `sudo systemctl start homecompute-nod-vm.service`. Verify the guest SSH
   host key against the cloud-init console journal on Home Core before first
   use. The operator account is `nod-operator@10.77.60.2` via `-J home-core`.
2. Provision a dedicated LiteLLM virtual key with models `automation-moe` and
   MCP access group `no-mcp-servers`. Deliver it inside the guest as
   `OPENAI_COMPATIBLE_API_KEY=…` in root-owned
   `/var/lib/homecompute-nod/model.env` mode 0600. Copy the public CA
   certificate from
   `/var/lib/homecompute-nod-edge/caddy/pki/authorities/local/root.crt` on
   Home Core to `/var/lib/homecompute-nod/gateway-ca.crt` in the guest. The
   root CA private key stays on Home Core.
3. Copy this directory and `scripts/setup-nod-guest.sh` from the selected
   published release, preserving paths in the guest. Run
   `sudo bash scripts/setup-nod-guest.sh install`, then
   `sudo bash scripts/setup-nod-guest.sh status` and
   `sudo bash scripts/setup-nod-guest.sh verify`. The installer builds a
   source-pinned release under `/opt/homecompute-nod` and creates its private
   Compose environment there. It preserves that environment on retry.
4. Open the browser through the SSH tunnel and visit
   `http://localhost:4310`:

   ```bash
   ssh -N -J home-core -L 4310:127.0.0.1:4310 nod-operator@10.77.60.2
   ```

   Enter the guest's `NOD_AUTH_TOKEN` from
   `/opt/homecompute-nod/deployment/self-host/.env` in the browser. Retrieve
   it in an operator terminal and keep it out of logs and chat. The local
   server checks this token. The configured model is `automation-moe` through
   the guest-only, certificate-verified model edge. Test a public browser
   task and a harmless approval in Nod before connecting accounts.

## Stop and recover

Use `sudo systemctl stop homecompute-nod-vm.service` on Home Core to release
the guest's memory. Systemd asks QEMU to shut the guest down cleanly. Guest
restarts retain Nod's PostgreSQL, workspace, browser, and encrypted Vault
volumes. Keep the guest disk, the owner-only state and the private Compose
configuration together in recovery. Follow the upstream
[backup and restore procedure](https://nod.n8n.io/docs/deploy/server-operations/)
and qualify a restore before using the service for durable personal data.
Revoke the dedicated model key when retiring the guest.
