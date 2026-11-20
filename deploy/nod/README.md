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

## Hermes owner bridge

The optional bridge adds two MCP tools to the private Hermes owner sandbox.
`nod_submit` starts one Nod background run in its own session. It uses that
Nod instance's existing connections and saved logins. `nod_status` returns
status and up to 4,000 characters of text from that session. It returns
`owner_action_required` when the run is waiting. Open Nod to handle approvals
and sign-in there, then poll the same request ID. Nod retains the tasks and
their results in its existing database. Files and richer responses stay in
Nod. By design, Telegram remains available for owner chat.

The bridge uses Nod's v0.7.6 background-event path so delegated text does not
become a direct owner instruction in Nod's Trust checker. Nod's standing rules
still apply. Review those rules before allowing Hermes to submit real tasks.
Delegated turns and owner-approved continuations see only the delegated
session's transcript. They use a separate workspace, a restricted runtime
and a tool allowlist for existing app connections and browser sign-in. Owner
chat search, memory, learned skills, local files and shell access are excluded.
The credential grants task submission and access to results from this bridge.
Keep it in the Nod guest and the owner sandbox's OpenShell credential store.
The native relay accepts only the agents guest, the exact POST path and a
bounded body. Nod checks the relay hostname separately from its normal web
interface. OpenShell binds the credential to `/mcp/hermes-nod` and the
`nod` MCP registration. The shared LiteLLM catalog does not declare this tool.

Use the same reviewed published HomeCompute revision for all three steps.
Keep the current Nod volume backup and the prior Home Core relay release for
rollback. The owner connector checks the existing Hermes backup and network
readiness records before registration.

1. On the Nod guest, stage the `deploy/nod` directory and
   `scripts/setup-nod-guest.sh` from the selected release as for the current
   Nod installer. Provision a separate credential once in the guest:

   ```bash
   sudo install -d -m 0700 /var/lib/homecompute-nod/hermes-bridge
   sudo bash -c '
     umask 077
     set -o noclobber
     printf "HOMECOMPUTE_NOD_BRIDGE_TOKEN=nod_%s\n" \
       "$(openssl rand -base64 32 | tr "+/" "-_" | tr -d "=\n")" \
       > /var/lib/homecompute-nod/hermes-bridge/token.env
   '
   sudo bash scripts/setup-nod-guest.sh enable-hermes
   sudo bash scripts/setup-nod-guest.sh verify
   ```

   This recreates only the web container with read-only source mounts.
   The original image, PostgreSQL and Nod volumes are retained. Subsequent
   Nod installer and reconfigure runs use the applied bridge until disabled.
   If the mounted files drift, compare against the selected release before
   enabling again.

2. On Home Core, verify the installed native relay and source before applying.
   The Nod route is additive to the existing native relay. With the relay
   installer from the selected release, stage, check and restart only that
   service. If health fails, restage and restart with the prior release.

   ```bash
   sudo python3 scripts/setup-native-capabilities.py install
   sudo python3 scripts/setup-native-capabilities.py check
   sudo systemctl restart homecompute-native-mcp-relay.service
   sudo systemctl is-active homecompute-native-mcp-relay.service
   ```

3. From the operator workstation, pipe the dedicated credential to the agents
   guest. Substitute the verified checkout path and full SHA. The connector
   matches the observed active `hermes-personal-v2` sandbox. It checks the
   native endpoint and adds only `mcp-nod` to the API toolsets.

   ```bash
   ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 nod-operator@10.77.60.2 \
     'sudo -n cut -d= -f2 /var/lib/homecompute-nod/hermes-bridge/token.env' |
   ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 hermes-operator@10.77.20.2 \
     'PATH="$HOME/.local/bin:$PATH" python3 CHECKOUT/scripts/connect-hermes-nod.py --revision FULL_SHA'
   ```

Verify native discovery with
`nemohermes hermes-personal-v2 mcp status nod --json --tools` and check that it
lists exactly `nod_submit` and `nod_status`. From the private Hermes owner UI,
ask it to submit “Return the exact text NOD-BRIDGE-OK, without using any app or
website” and report the returned result. It should submit once, poll the
same request ID, and return `NOD-BRIDGE-OK`. Repeat with an owner-selected
read-only operation against one saved connection for authenticated app
verification. Keep results in the owner UI. Verify the HomeCompute services,
doctor and drift checks after rollout.

For rollback, run `connect-hermes-nod.py --revision FULL_SHA --remove` in the
agents guest, then `setup-nod-guest.sh disable-hermes` in the Nod guest, and
restore the prior native relay installer and restart the relay. Remove the
external `token.env` once the bridge is revoked. Any work already accepted
stays in Nod and can be inspected or stopped there with Nod's own controls.

## OpenBot delegation

OpenBot uses the same delegated runtime with its own credential and request
namespace. It can submit a new task and check that task's status. Nod keeps
owner approvals and sign-in in the Nod app. Existing connected accounts and
saved logins may be used under Nod's Trust rules. The delegated runtime excludes
owner chat history, memory, local files and shell access. Review [ADR-041](../../docs/adr/041-openbot-nod-delegation.md)
and Nod's standing rules before granting the connection.

1. Publish and deploy the reviewed Home Core release. Retain the prior relay
   release and a current Nod volume backup. On Home Core, use the deployed
   release to stage and restart the native relay:

   ```sh
   cd /srv/homecompute/current
   sudo python3 scripts/setup-native-capabilities.py install
   sudo python3 scripts/setup-native-capabilities.py check
   sudo systemctl restart homecompute-native-mcp-relay.service
   sudo systemctl is-active homecompute-native-mcp-relay.service
   ```

2. Stage the same release's Nod files and installer on the Nod guest using the
   [installation process above](#install). If a Hermes bridge from an older
   release is active and the installer reports differing sources, disable it
   using its originating release, then re-enable it using the selected release.
   This recreates the web container and keeps its volumes. Create the
   independent OpenBot credential once and activate the OpenBot route:

   ```sh
   sudo install -d -m 0700 /var/lib/homecompute-nod/hermes-bridge
   sudo bash -c '
     set -euo pipefail
     umask 077
     set -o noclobber
     token=$(openssl rand -base64 32 | tr "+/" "_-" | tr -d "=\n")
     printf "HOMECOMPUTE_NOD_OPENBOT_TOKEN=nod_%s\n" "$token" > /var/lib/homecompute-nod/hermes-bridge/openbot.env
   '
   sudo bash scripts/setup-nod-guest.sh enable-openbot
   sudo bash scripts/setup-nod-guest.sh verify
   ```

3. Configure [the owner OpenBot MCP connection](../openbot/README.md#ask-the-local-nod-assistant).
   Verify one synthetic submission and response over that connection. Check
   the HomeCompute status, services, doctor, and drift commands after rollout.

Revoke the dedicated LiteLLM key and remove the OpenBot connection to withdraw
client access. Run `setup-nod-guest.sh disable-openbot` from the applied
release and retire `openbot.env` to withdraw the backend credential. To remove
the relay route, deploy the prior release's relay configuration and verify its
health. Hermes and accepted Nod runs retain their separate lifecycles.
