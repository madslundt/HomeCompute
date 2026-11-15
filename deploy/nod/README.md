# Nod on home-core

Nod v0.7.6 runs in its own Ubuntu KVM guest with two vCPUs, 8 GiB RAM and a
60 GiB sparse disk under `/srv/state/nod-vm`. The guest owns its Docker daemon
because upstream Nod's runtime proxy needs the Docker socket. Set up one owner
and use public or synthetic data while qualifying the browser and approval
behavior. The VM starts with home-core and retries if startup fails. It
requires 4 GiB of host memory headroom above its allocation at startup. The browser and workspace retain their upstream
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

1. Deploy the reviewed HomeCompute release to `home-core`. Verify the guest SSH
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
4. Add the record in [nod-dns.json](../../config/nod-dns.json) to the existing
   UniFi private DNS zone. For an existing Nod install with images built, run
   `sudo bash scripts/setup-nod-guest.sh reconfigure` from the published release
   to replace only the web container. Keep the existing private environment
   and volumes.

## Access and integration

Open **Nod** on [Homepage](http://home-core.tail479ad.ts.net/) or go directly
to `https://nod.home.arpa/`. On the phone away from home, connect Tailscale
and use the existing `home.arpa` split DNS and approved subnet route. Devices
need to trust the HomeCompute Caddy CA. On a new browser or device, use the
same owner's `NOD_AUTH_TOKEN` from the guest's private
`/opt/homecompute-nod/deployment/self-host/.env`. The desktop app uses
**Connect to a server** with the same address and token.
[Server access and proxy requirements](https://nod.n8n.io/docs/deploy/self-host/#use-an-existing-proxy)

- **Scheduled tasks.** Open **Scheduled** in Nod or ask in chat to create a
  schedule. Start with a public-source digest. The server runs tasks while
  your phone is offline. [Schedules and background work](https://nod.n8n.io/docs/use/schedules/)
- **Apps.** Open **Apps → Connect** for a supported account. Connections live
  in the server's profile, so each device sees the same assistant. Check which
  accounts and actions the app authorizes. Some managed connectors process
  requests through n8n's connection service and Composio.
  [Connect apps](https://nod.n8n.io/docs/connect/apps/)
- **n8n.** In Nod, choose **Apps → n8n → Connect** and enter
  `https://n8n.home.arpa`. Continue through n8n's OAuth consent and grant only
  **Read workflows** for initial use. Nod's guest DNS resolves that hostname
  to its own bridge. The bridge permits MCP transport and the OAuth discovery,
  registration, and token endpoints. The browser opens the consent screen at
  the existing n8n HTTPS edge and returns to
  `https://nod.home.arpa/api/apps/callback`. Verify the scopes under n8n
  **Settings → Instance-level MCP → Connected clients**. Try listing workflows
  before granting execution or editing. Revoke Nod's grant from the same page
  to disconnect the client.
  [Nod n8n connection](https://nod.n8n.io/docs/connect/mcp-api-cli/#n8n) · [n8n MCP permissions](https://docs.n8n.io/connect/connect-to-n8n-mcp-server/#using-oauth2)

Verify the Homepage link, HTTPS and the owner login with a trusted LAN client
and a phone on cellular with Tailscale. The unauthenticated Nod API should
return 401. The guest's raw HTTP port is reachable through the Caddy bridge.

## Stop and recover

Use `sudo systemctl stop homecompute-nod-vm.service` on Home Core to release
the guest's memory. Systemd asks QEMU to shut the guest down cleanly. Guest
restarts retain Nod's PostgreSQL, workspace, browser, and encrypted Vault
volumes. Keep the guest disk, the owner-only state and the private Compose
configuration together in recovery. Follow the upstream
[backup and restore procedure](https://nod.n8n.io/docs/deploy/server-operations/)
and qualify a restore before using the service for durable personal data.
Revoke the dedicated model key when retiring the guest.
