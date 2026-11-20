# OpenBot on the agents VM

The `home-core` OpenBot team server runs in the Ubuntu agents VM. It uses
OpenBot's hosted account, Signal, and TURN services. The container publishes
no ports and has no bundled web client;
after sign-in, use the desktop/iPhone app or `https://openbot.run/app`.

The Compose project pins the OpenBot 0.73.0 release by digest. The systemd
unit pulls the pinned image before each service start, and the health check
verifies the running containers against the pin. Its upstream seccomp profile is tracked here and was checked
against SHA-256 `665289e6a1c41045f55166d0b01993c929f71df78e3a3b264f3252bb8e0b3d9e`.
The guest installer verifies the Ubuntu 24.04 `docker-compose-v2` package
version and `.deb` SHA-256 from `config/openbot-install.json` before installing
it, then checks the reported Compose CLI version.
The container has no Docker socket, host-home mount, host networking, or
published ports. Its only persistent mount is its private `/data` directory;
all capabilities are dropped except `SYS_CHROOT`, which Chromium's sandbox
needs inside the container. `no-new-privileges` remains enabled. The agents-VM Docker bridge egress policy
allows public TCP 80/443, the private LiteLLM gateway at `llm.home.arpa:443`, and
the retained `ai.home.arpa:18080` compatibility endpoint during migration, and
the documented TURN ports for the two currently resolved OpenBot relays. The
gateway hostname resolves to the host-only bridge; egress uses TCP 443 there for model inference and authenticated MCP. The Android relay remains separately pinned to TCP
19445. Other private destinations and public UDP destinations are blocked. The
Home Core firewall repeats the TURN restriction by relay IP.

TURN IPv4 addresses in `config/openbot-network.json` were resolved on
2026-11-09. Refresh them with `getent ahostsv4 turn.openbot.run turn2.openbot.run`
before a future OpenBot network update. Update the JSON, review the resulting
Home Core firewall diff, and deploy through the normal host release before
reconciling the guest policy. Stale entries fail closed and prevent reliable
remote WebRTC sessions.

## Install and sign in

After publishing the source revision and deploying it to Home Core:

```bash
./scripts/setup-openbot-guest.sh install
./scripts/setup-openbot-guest.sh status
./scripts/setup-openbot-guest.sh login
```

The login command asks for the account email and one-time code in the local
terminal. It stores the server session only in OpenBot's `/data` keyring. Then
sign in to the desktop/iPhone app or `openbot.run/app` with the same account and
select the `home-core` server.

For the independently enrolled household server and its embedded Hermes agent,
follow the [private household server runbook](PARTNER.md).

## Models and teams

Use the existing OpenBot model providers at `https://llm.home.arpa/v1`.
Leave their API key fields empty. If a client requires a value, enter `local`.
Keep `automation-moe` for local agents and the existing ChatGPT alias assignments
for agents already configured to use the subscription. Keep the ask-first backup
policy. The private agents bridge authorizes model requests from OpenBot.

After deploying Home Core from the reviewed release, update the embedded
Hermes provider from that exact release:

```sh
./scripts/setup-openbot-guest.sh configure-model-gateway
```

In the OpenBot server **Providers** settings, edit the existing Home AI and
HomeCompute ChatGPT subscription entries. Set the same HTTPS base and clear
their model API keys. Retain the provider IDs and model assignments. The
subscription provider uses `chatgpt-sol-latest`, `chatgpt-terra-latest`,
`chatgpt-luna-latest`, and `chatgpt-astra-latest`. Test a synthetic turn on
`automation-moe` and on an explicitly selected subscription alias after the
provider updates. The [agent setup guide](../../docs/openbot-agent-setup.md)
covers roles and data boundaries.

## Ask the local Nod assistant

After [enabling the private Nod bridge](../nod/README.md#openbot-delegation),
grant `openbot-nod` to the owner `openbot-agent` key. A normal Home Core
release applies the grant. For an already-running gateway with the `nod`
alias, run the targeted grant on Home Core from a reviewed checkout of the
published revision:

```sh
sudo python3 scripts/setup-litellm-harness-mcp-access.py --openbot-nod-only
```

On the owner **Mads home-core** server, edit the existing `homecompute`
connection at `https://llm.home.arpa/mcp`. Add this secure header and keep its
other headers:

- `x-mcp-nod-authorization: Bearer <dedicated OpenBot token from Nod's openbot.env>`

If the connection has an `x-mcp-servers` filter, include `nod` in its existing
list. Copy the token value directly from the private operator terminal into
the secure header field. OpenBot shares the connection with its agents on
that server, so grant it only where the agents and their model providers are
trusted with results from Nod's connected apps.

Use **Test connection** to verify the existing tools and the additional
`nod_submit`, `nod_status`, and `nod_schedule_result` tools, then save.
If a separate `nod` connection
was configured during the pilot, disable it after confirming the combined
connection. In a fresh owner thread, ask OpenBot to delegate a synthetic
request to Nod and poll the same request ID for its result. Handle approval
or sign-in in Nod.

To reuse a scheduled result, opt in the schedule in the
[Nod guest](../nod/README.md#share-a-saved-schedule-result). For example, ask
Hermes to read the saved `morning-brief` result, report its age, and check for
a newer failed run. The lookup reads a saved result without rerunning the
schedule.

Remove the Nod bearer header to withdraw access from this connection. Remove
the `openbot-nod` group from the key when rolling back the source grant.

## Shared LiteLLM MCP connections

OpenBot's MCP list is shared with every agent on its server. The owner has
chosen to expose the full LiteLLM catalog to all OpenBot agents. The
`openbot-mcp` key is restricted to `automation-moe` and the
`all-harness-mcps` group. This includes n8n workflow administration and the
bounded Android UI tools as well as Aula, offers, and public documentation.
Each HomeCompute relay still requires its own role-scoped bearer header.

After deployment, `setup-litellm-harness-mcp-access.py` assigns the existing
OpenBot key the full catalog group. It remains restricted to `automation-moe`
for model requests. Copy it directly from the operator terminal into OpenBot's
secure connection header field:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'cat ~/.config/homecompute-openbot/secrets/litellm-api-key'
```

In OpenBot's `home-core` server settings, add a **Streamable HTTP** connection:

- Name: `homecompute`
- URL: `https://llm.home.arpa/mcp`
- Header: `x-litellm-api-key: Bearer <OpenBot MCP key>`

Add these role-specific headers using the guarded export commands in
`scripts/setup-hermes-mcp.py` (run on Home Core from the deployed full SHA):

```sh
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --export-credential
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --export-openbot-android-credential
```

Copy each command's output directly into its matching OpenBot secure header
field. Do not paste these values into Codex, chat, issue text, shell history, or
logs.

- `x-mcp-homecompute_hermes-authorization: Bearer <Hermes owner credential>`
- `x-mcp-homecompute_openbot_android-authorization: Bearer <OpenBot Android credential>`

Do not reuse one role's bearer as another role. Test before saving; the list
should include the six shared aliases. Remove the previous OpenClaw header from any existing connection after the retired role is withdrawn. This connection is available to every
OpenBot agent, so protect the key and delegated bearers as full household and
workflow access. Revoke the `openbot-agent` key to withdraw LiteLLM catalog access.

<a id="isolated-infrastructure-maintenance-server"></a>

### Infrastructure maintenance on the shared server

The `home-core` OpenBot server uses a distinct maintenance key and the
`openbot-maintenance` native principal. The LiteLLM alias is excluded from
`all-harness-mcps`. Every agent on `home-core` can use this connection, so use
the local model for infrastructure tasks. Home Core enforces exact root
approval for any restart. Tools expose bounded host and update metadata,
Git upgrade proposals, and the supported restart plans.

For an initial installation, deploy the published Home Core release. From
that exact release on Home Core, preview the controller bundle and relay
plan, then apply the reviewed digests:

```bash
sudo python3 scripts/setup-infrastructure-maintenance.py --source scripts
sudo python3 scripts/setup-infrastructure-maintenance.py --source scripts \
  --bundle-sha256 REVIEWED_BUNDLE_SHA256
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --apply \
  --expected-plan-sha256 PLAN_SHA256 --admit-openbot-maintenance
```

Existing deployments with a verified scoped maintenance alias can reuse the
controller and gateway configuration. To rotate the relay bearer before
activating the team connection, revoke it using the guarded installer, generate
a new plan against the updated state, then admit it again. This temporarily
withdraws only the maintenance grant. Provision the separate team key from a
clean checkout of the current published revision:

```bash
python3 scripts/provision-openbot-maintenance-key.py
```

In **home-core** only, add a separate **Streamable HTTP** connection named
`homecompute-maintenance` at `https://llm.home.arpa/mcp`. Set all three headers
in the secure connection fields:

- `x-litellm-api-key: Bearer <team maintenance LiteLLM key>`
- `x-mcp-servers: homecompute_openbot_maintenance`
- `Authorization: Bearer <HomeCompute maintenance bearer>`

The team key is in
`~/.config/homecompute-openbot/secrets/maintenance-litellm-api-key` in the
guest. Use the guarded `setup-hermes-mcp.py --export-openbot-maintenance-credential`
command on Home Core from the active relay release to retrieve the separate
bearer. Copy credentials directly from the private operator terminal. Verify
that eight tools appear and ask the team agent to report health for each host
and prepare a read-only Git upgrade proposal.

### Retire the duplicate server

Stage the current reviewed published revision under
`/srv/homecompute/releases/FULL_SHA` on Home Core to use the existing guarded
VM backup utility. Verify a fresh encrypted VM backup in the active NAS
repository. If a new snapshot is needed, use
`backup-openbot-agents-vm.py backup --revision FULL_SHA --acknowledge-guest-pause`
in the approved maintenance window.

From the same clean published workstation checkout, first switch the guest
to the team-only release:

```bash
./scripts/setup-openbot-guest.sh retire-operator
./scripts/setup-openbot-guest.sh status
```

The migration verifies the previous managed bundle and keeps the team image
and team data directory. It briefly restarts the service. Confirm that
existing team chats load and the new connection works before deleting the
retired service. Sign out the retired server before the switch if it is still
registered. Then revoke its old LiteLLM key and explicitly purge its stopped
container, bridge, local credentials and data:

```bash
python3 scripts/provision-openbot-maintenance-key.py --revoke-operator
./scripts/setup-openbot-guest.sh purge-retired-operator
./scripts/setup-openbot-guest.sh status
```

The encrypted VM snapshots still follow the NAS retention policy. Rotate the
native maintenance bearer if old credentials were exported or could remain
accessible beyond the approved backup boundary.

### Propose upgrades in Git

Start with current host reports. Ask OpenBot to draft the source change for
owner review. For Home Core NixOS packages, select `nixos` with
`homecompute_maintenance_proposal`. It points to the existing flake update
workflow and its pull request search. Use the workflow's `flake.lock` change
as the candidate and wait for review and CI.

For an eligible Docker component, the tool returns the source template and
setting, and includes a candidate when a fresh registry report shows a digest
change for the tracked tag. Compare the observed image with the checked-in pin
before drafting the proposed edit. Use Dependabot and the owning workload
runbook for new tags, coupled image changes, and migrations. Treat missing
candidate evidence as a follow-up to verify with the source maintainer.
OpenBot returns the proposal for human review. The owner publishes it in Git.

Deploy reviewed Home Core changes and roll back from the operator workstation
using the [HomeCompute CLI](../../docs/homecompute-operations.md#deploy-one-host).

```bash
./scripts/homecompute deploy home-core --revision FULL_SHA
```

### Request an approved service restart

Use `homecompute_maintenance_prepare` with target `home-core` and one of
`restart-homepage`, `restart-open-webui`, `restart-piper`, or `restart-stt`.
Ask OpenBot for the plan ID and digest, then review and approve the exact plan
from a trusted Home Core terminal.

```bash
sudo python3 /opt/homecompute/infrastructure-maintenance/infrastructure_maintenance.py review PLAN_ID
sudo python3 /opt/homecompute/infrastructure-maintenance/infrastructure_maintenance.py approve PLAN_ID PLAN_DIGEST
```

OpenBot can request execution by plan ID after approval. Check the receipt
and the actual client path. Reconcile an uncertain restart on Home Core
before preparing another attempt. The owner handles other service recoveries
through their workload runbooks.

### Withdraw access

To withdraw access, remove the maintenance connection from the shared
server, revoke its dedicated LiteLLM key, and remove the relay bearer
through the guarded source installer on Home Core:

```bash
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --apply \
  --expected-plan-sha256 PLAN_SHA256 --revoke-openbot-maintenance
```

## Operations and recovery

Verify a gateway request from an OpenBot agent after a guest rollout. The
installer builds a read-only agent CA bundle from the guest's public system
roots and the Home Core root. Agent processes read it through `SSL_CERT_FILE`.
Node and embedded Codex also receive the Home Core root through
`NODE_EXTRA_CA_CERTS` and `CODEX_CA_CERTIFICATE`. Rerun the installer after a
CA rotation to refresh both mounts.

The [certificate maintenance runbook](../certificate-maintenance/README.md)
checks the gateway hourly from the bundled agent runtime and sends failures to
the owner through Home Assistant Inbox.

`homecompute-openbot.service` owns startup, restart, and graceful stop. It starts
only after `homecompute-openbot-firewall.service` installs the container egress
rules. Its post-start check waits up to one minute for Compose to create and
start the container before checking the image pin and absence of published
ports. Docker itself does not auto-start the container outside that ordering.
Re-running the source installer upgrades only an exact managed release and
preserves `/data`; unknown paths or changed installed files stop the operation.
Use `./scripts/setup-openbot-guest.sh status` for a read-only status check and
the following command for a guarded restart:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'sudo -n systemctl restart homecompute-openbot.service'
```

OpenBot can prepare an upgrade proposal. For upgrades to OpenBot itself,
review the release notes, resolve the Linux AMD64 image to an immutable
digest, and update the matching pins in `compose.yaml` and
`homecompute-openbot-health` through Git. From a clean checkout of the current
published `origin/master` revision, review the plan before approving it in the
operator terminal:

```bash
./scripts/setup-openbot-guest.sh plan-team-image
./scripts/setup-openbot-guest.sh upgrade-team-image
./scripts/setup-openbot-guest.sh status
```

The upgrade runs as a guest systemd job that survives the OpenBot restart and
records its result in the journal. The helper checks the current guest bundle against its source and checks the
installed units and helpers against that bundle. It carries forward the existing
systemd and network configuration, then installs the published image pin in a
new guest release. Use the full installer to reconcile other guest changes.

OpenBot keeps its database, credentials, keyring, workspaces, and logs together
under `/data`. Before an image change, confirm an encrypted NAS backup that
includes all of this directory. The scheduled Immich and Hermes backup does not
include it. For an attended OpenBot backup, stop `homecompute-openbot.service`,
confirm its container has stopped, and archive the entire guest directory
`~/.local/share/homecompute-openbot/data` with numeric owners, ACLs, and extended
attributes. Resume the original service after capture, including on error.
Store the archive in the encrypted HomeCompute Restic repository under the
`homecompute-openbot-pre-upgrade` tag, verify the hash by reading the archive
back with `restic dump`, and retain the snapshot ID, archive path, and hash in
the private backup receipt. Remove the temporary plaintext archive after
verification. A fresh full agents-VM backup with OpenBot data also qualifies.

Keep the previous guest release and image until the replacement and backup
have been verified. To recover from a data migration failure, stop the service
and restore the entire pre-upgrade data archive into a separate directory first.
Verify the hash, ownership and permissions, then preserve the failed directory
and move the verified copy into place. Select the prior guest release and
reinstall its recorded health helper before starting the service. Restoring
only the previous image does not restore the database or other application data.

The source-managed Hermes CLI inside OpenBot uses its own `/data/.hermes`
configuration. To reconcile its local Home AI route after deployment, run:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'python3 ~/.local/share/homecompute-openbot/current/configure-openbot-hermes.py'
```

The embedded Hermes runtime starts summarizing history at 128,000 tokens or
its model-specific trigger, whichever is lower. This setting is shared by
agents that use OpenBot's embedded Hermes. It gives the local summarizer
smaller histories to process under OpenBot's five-minute inactivity limit.

To apply only the compression setting to an existing guest, use a clean
checkout of the current published `origin/master` revision:

```bash
./scripts/setup-openbot-guest.sh configure-compression
```

This invokes the released settings helper without reinstalling the guest or
changing its provider bindings. New Hermes ACP sessions load the cap when
they initialize. If existing sessions need it immediately, wait until the
server is idle and run `sudo -n systemctl reload homecompute-openbot.service`
in the guest to restart the running container and reopen its sessions.

The helper also disables Hermes' passive startup update check. Git 2.43 in the
OpenBot image can otherwise lazy-fetch the partial Hermes checkout during its
ACP startup probe, which exceeds OpenBot's 10-second provider check. Explicit
`hermes update --check` and `hermes update` commands remain available. This does
not import the separate `hermes-personal-v23` owner's memories, skills or
HomeCompute tools into OpenBot.

OpenBot is licensed under PolyForm Noncommercial 1.0.0. This deployment is for
personal, noncommercial use. Commercial use requires a separate license.
