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
