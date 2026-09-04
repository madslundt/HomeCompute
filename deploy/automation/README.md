# n8n automation deployment

## Live deployment

The two previously published source workflows were cut over on 2026-09-04.
Use **both** Compose files for production operations:

```sh
sudo docker compose --env-file /etc/homecompute/automation.env \
  -f deploy/automation/compose.yaml -f deploy/automation/production.yaml ps
```

On the home LAN, open `http://192.168.30.122:15678`. Tailscale access remains
available at `http://home-core.tail479ad.ts.net:15678`, and the loopback
publication supports an SSH tunnel. Ports bind to those specific addresses;
there is no wildcard publication or router port forwarding. LAN access uses
HTTP and the existing n8n account login.

`modules/nixos/automation-network.nix` installs egress controls before Docker
starts: internet TCP 443 and HAOS TCP 7878 are allowed; other private-network
destinations are rejected. Home Assistant's name is mapped explicitly in
the container. The target retains the original cloud AI credentials; it does
not depend on an undeployed LiteLLM gateway or GB10.

The archived workflow, Email analyzer, and search sub-workflow retain their
previous unpublished state. Outlook/webhook integrations may require updated
callback URLs if enabled later; a Tailscale-only URL is not public webhook ingress.

## Rollback

For Aula MCP setup and rollback, see the section below. The following rollback
procedure concerns the migrated n8n workflows.

First stop the target container to prevent duplicate schedules. Preserve any
new target executions and reconcile side effects before switching back.
The HAOS add-on data is retained, but its workflows are unpublished. Restore
the following exact versions with the source n8n CLI using
`N8N_USER_FOLDER=/data/n8n` (the add-on's actual user folder), then start the
source add-on through Home Assistant:

```text
publish:workflow --id=reK3QQ0NYgxge1CB --versionId=2fd65c7b-d517-4d3a-b067-df1e5df8d795
publish:workflow --id=679IOmjuYT4oFDfM --versionId=c010dabf-64ef-4b6e-8889-903c745d3a67
```

Aula's saved draft differs from its published version; do not publish the
draft accidentally. The last source execution at cutover was 223, successful.

## Isolated restore procedure

This project restores the existing n8n instance in isolation. Its network has
no external routing and publishes no host ports. Access the container through
an SSH tunnel at `http://localhost:15678`. Obtain its internal address with
`sudo docker inspect homecompute-automation-n8n-1` on home-core, then forward
local port 15678 to that address's port 5678 through SSH. The verified staging
address on 2026-09-04 was `172.30.1.2`; inspect it again after network recreation.

Before first startup, populate `/srv/state/automation/n8n` from a consistent
copy of the source `.n8n` directory, preserving `config` (the encryption key),
SQLite state, community nodes, and storage. Set ownership to UID/GID 1000 and
set the config file to mode 0600. Do not commit this directory or exports.

Use the same runtime env file for all commands:

```sh
sudo docker compose --env-file /etc/homecompute/automation.env \
  -f deploy/automation/compose.yaml config --quiet
sudo docker compose --env-file /etc/homecompute/automation.env \
  -f deploy/automation/compose.yaml up -d
```

Unpublish restored workflows with the pinned n8n CLI before allowing egress.
Retain a record of their original publication state. Verify credential
decryption without printing credential values, community packages, workflow
references, and SQLite integrity. Do not execute production messaging or home
control actions during qualification.

The owner deferred scheduled off-host backups on 2026-09-04. This permits
continuing deployment; it does not make backups complete. Keep the original
HAOS instance and a consistent migration snapshot for rollback. Before each
cutover, disable that workflow at its source and reconcile in-flight work.

Production ingress, external endpoints, per-workflow cutover, and egress policy
must be configured separately after restore validation. The initial 2 CPU /
2 GiB limits are conservative staging limits, not measured production sizing.

## Aula MCP

The production overlay includes [Casperjuel/aula-mcp](https://github.com/Casperjuel/aula-mcp)
at commit `af49805ae9c6d7c9026f6e559f2e01ca209c9e46`. The local Dockerfile
builds frozen upstream dependencies using Node and runs the server with Bun;
both base images are pinned by digest. `scripts/deploy-home-core.sh` builds
this image during deployment. Aula is absent from the isolated restore stack.

Deployed on home-core on 2026-09-04 from the modified checkout at
`/home/mads/HomeCompute`, with NixOS build/test/switch completed. The container
`homecompute-automation-aula-mcp-1` is healthy; host loopback, n8n access, and
outbound Aula HTTPS passed. LAN port 17878 and access from an unrelated Docker
bridge were blocked. MitID login remains an owner action. These changes await
a committed release; `/srv/homecompute/current` still identifies the previous
release, so use the working checkout for Aula operations until promotion.

Access is limited to home-core: host processes use
`http://127.0.0.1:17878/mcp`; n8n uses `http://aula-mcp:7878/mcp` with
Streamable HTTP and authentication **None**. Legacy SSE clients may use
`http://aula-mcp:7878/sse`. The upstream server has no client authentication.
The container listens on its own interfaces, but only host loopback is
published. The NixOS Docker policy permits the automation bridge to reach
`172.28.201.3:7878` and rejects new routed connections from other interfaces.
There is no LAN, Tailscale, reverse-proxy, or setup-UI publication.
Write tools, raw requests, and verbose logging are disabled.
The HTTP transport permits 32 concurrent MCP sessions and evicts sessions after
60 seconds without a request. This accommodates n8n tests and interrupted runs
without allowing abandoned sessions to accumulate indefinitely. If n8n reports
`Too many active MCP sessions`, restart only Aula to clear the in-memory session
map, then inspect the workflow for excessive retries or concurrency:

```sh
sudo docker restart homecompute-automation-aula-mcp-1
```

### Log in again from your Mac

Run this in a terminal on your Mac whenever Aula requires a fresh login:

```sh
ssh -t -o HostKeyAlias=home-core \
  -i ~/.ssh/id_ed25519_ai-services-01 mads@192.168.30.122 \
  'sudo docker exec -it homecompute-automation-aula-mcp-1 bun apps/cli/src/index.ts login'
```

Follow the username prompts, scan the terminal QR code with MitID, and approve
the login. Keep the terminal open until the CLI confirms success. The SSH `-t`
and Docker `-it` options provide the interactive terminal needed for login.
This command works from any Mac directory; it does not need a local Compose
environment file. The private SSH key must already exist on that Mac, and the
Mac must be able to reach home-core's LAN address.

`HostKeyAlias=home-core` verifies the server against the existing trusted
`home-core` entry in `~/.ssh/known_hosts`. It is needed when connecting by IP
with a key saved under the hostname. It does not disable host-key checking.
On a new Mac, establish and verify SSH trust before running this command.

After login, check authenticated access from your Mac:

```sh
ssh -t -o HostKeyAlias=home-core \
  -i ~/.ssh/id_ed25519_ai-services-01 mads@192.168.30.122 \
  'sudo docker exec -it homecompute-automation-aula-mcp-1 bun apps/cli/src/index.ts doctor'
```

Login state persists across container restarts. Logging in does not require
rebuilding the image or recreating the container. Treat `doctor` output as
private; it can include account or school information.

### Check or recreate the container on home-core

Connect from your Mac:

```sh
ssh -o HostKeyAlias=home-core \
  -i ~/.ssh/id_ed25519_ai-services-01 mads@192.168.30.122
```

The following commands run **inside that SSH session on home-core**:

```sh
hostname
sudo docker ps -a --filter name=homecompute-automation-aula-mcp-1 \
  --format '{{.Names}} {{.Status}}'
sudo test -f /etc/homecompute/automation.env && echo 'Automation configuration exists'
```

`hostname` must print `home-core`. If the container is stopped, start it with
`sudo docker start homecompute-automation-aula-mcp-1`, then rerun login. If it
is missing, use the deployment procedure below.

### Deploy or rebuild Aula on home-core

Use the checkout containing the Aula changes. Until those changes are promoted
to a release, this is `/home/mads/HomeCompute`; the previous release directory
does not contain them. On a fresh host, complete the existing home-core setup
first, including Docker, secrets, and the n8n production deployment.

Apply NixOS to provision `/etc/homecompute/automation.env`, the persistent state
directory, and the firewall rules. These commands also run **on home-core**:

```sh
cd /home/mads/HomeCompute
sudo nixos-rebuild build --flake path:/home/mads/HomeCompute#home-core
sudo nixos-rebuild test --flake path:/home/mads/HomeCompute#home-core
sudo systemctl --failed
sudo nixos-rebuild switch --flake path:/home/mads/HomeCompute#home-core
```

Check for activation failures before proceeding. Then build and start only
Aula, leaving n8n running. Run this block in Bash on home-core:

```sh
cd /home/mads/HomeCompute
compose=(sudo docker compose --env-file /etc/homecompute/automation.env \
  -f deploy/automation/compose.yaml -f deploy/automation/production.yaml)
"${compose[@]}" build aula-mcp
"${compose[@]}" up -d --no-deps --wait --wait-timeout 90 aula-mcp
"${compose[@]}" exec -it aula-mcp bun apps/cli/src/index.ts login
"${compose[@]}" exec aula-mcp bun apps/cli/src/index.ts doctor
```

If `/etc/homecompute/automation.env` is missing, first confirm you are on
home-core; otherwise apply the NixOS configuration above. If Docker reports
`No such container`, confirm the same host and run the build/start block above
before retrying login. Running either command against Docker on your Mac does
not operate the home-core container.

Login requires the owner's interactive MitID approval. Health checks only
confirm the HTTP process is alive; successful login and `doctor` are required
before connecting workflows. Existing n8n workflows are not changed or executed
by this addition. Confirm reachability from n8n without invoking Aula tools:

```sh
"${compose[@]}" exec -T n8n node -e \
  'fetch("http://aula-mcp:7878/healthz").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))'
curl --fail http://127.0.0.1:17878/healthz
```

From another machine, port 17878 on home-core's LAN and Tailscale addresses
must be unreachable. Verify this after deployment, alongside the n8n check.

State is `/srv/state/automation/aula-mcp`, owned by UID/GID 1000, mode 0700.
Preserve the complete directory, including encrypted `tokens.json` and `.key`;
the key is generated locally by upstream and is not a repository secret.
Stop Aula before making a consistent encrypted backup or restoring the
directory. Never put token bundles or CLI output containing personal data in
Git. Off-host backups remain deferred under the existing owner decision.

To update, review and change the source commit, image tag, and runtime digests,
build and smoke-test, then deploy. Keep the previous image and a stopped-state
snapshot for rollback. Stop/remove only `aula-mcp` to withdraw this integration;
to roll back its version, restore the previous image/configuration and matching
state snapshot, then repeat health and login checks. Do not use `compose down`
for an Aula-only rollback because that also stops n8n.
