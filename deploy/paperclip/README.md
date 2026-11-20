# Paperclip on home-core

Open [Paperclip](https://paperclip.home.arpa/) or select **Paperclip** under
**AI assistants** on [Homepage](http://home-core/). Use the approved LAN or
the tailnet with the existing `home.arpa` split DNS and subnet route. Clients
must trust the [HomeCompute Caddy CA](../homepage/README.md). The UniFi local
record is declared in [paperclip-dns.json](../../config/paperclip-dns.json).

Paperclip uses its own sign-in. On a new instance, create your owner account,
then select **Claim this instance**. The first authenticated user to complete
the claim becomes instance admin. The base deployment provisions instance
signing secrets. Local inference is enabled separately.

## Release and verification

Use the normal [home-core release procedure](../../docs/homecompute-operations.md).
The upstream Paperclip 2026.1117.0 image is pinned by digest. It runs as UID
1000 on a dedicated internal Docker bridge shared with Caddy. Paperclip
publishes no host port and has no external network access. Caddy serves TLS
and admits the approved LAN and tailnet ranges. SOPS provisions the instance's
signing secrets in `/run/secrets/paperclip/environment`. Keep those secrets
stable across deployments and restores.

When a full rollout is blocked by unrelated workload prerequisites, use the
scoped first-install path from the exact published release on home-core:

```sh
revision=FULL_RELEASE_SHA
sudo git clone https://github.com/madslundt/HomeCompute.git "/srv/homecompute/releases/$revision"
sudo git -C "/srv/homecompute/releases/$revision" checkout --detach "$revision"
sudo python3 "/srv/homecompute/releases/$revision/scripts/deploy-paperclip.py"
```

The scoped installer locks deployment, checks the running Caddy and Homepage
Compose service hashes, and generates read-only overlays that preserve their
other settings. It uses the same SOPS-encrypted signing secrets via a root-only
file under `/var/lib/homecompute/paperclip`. The receipt records the two
previous source paths and configuration hashes for recovery. It leaves the
full host revision marker in place. The next full rollout replaces the
overlays with the canonical Caddy and Homepage files and switches the
container to its regular SOPS secret path.

After rollout, run the following from a client that trusts the CA:

```sh
dig +short paperclip.home.arpa
curl --fail --show-error https://paperclip.home.arpa/api/health
```

The DNS answer is `192.168.30.122`. The API reports `status: ok`,
`deploymentMode: authenticated`, and `bootstrapStatus: bootstrap_pending`
until the owner claims the instance. Confirm login and the Homepage link in
a browser. For container health, run
`sudo docker compose -f /srv/homecompute/current/deploy/paperclip/compose.yaml ps`
on home-core after a full rollout. For the scoped rollout, use the selected
release directory and export
`PAPERCLIP_SECRETS_FILE=/var/lib/homecompute/paperclip/environment` in the
root shell running Compose.

## Local inference

The normal Home Core release attaches Paperclip to a dedicated, internal
Docker network for the canonical LiteLLM gateway. The app trusts the
HomeCompute CA and uses the same HTTPS endpoint as LAN clients.

Probe keyless model listing and a synthetic Responses turn with the Engineering
Lead’s `xhigh` thinking effort from the Paperclip container after deploying that
release:

```sh
sudo python3 "/srv/homecompute/releases/$revision/scripts/deploy-paperclip-inference.py" \
  --revision "$revision" --verify-direct
```

In Paperclip, create a shared **Local endpoint** connection with the
**OpenAI Responses** format, base URL `https://llm.home.arpa/v1`,
**No authentication**, and model `automation-moe`. Grant the existing
five engineering-team agents access. Under each agent's **Advanced** →
**Custom Gateway**, choose the new connection and keep `automation-moe`.
Run the connection test and leave periodic heartbeats off. The prior loopback
connection remains available throughout the cutover and for rollback.

Complete runtime setup before customizing `AGENTS.md` on new agents.
Paperclip 2026.1117.0 can save the runtime and report an instruction revision
error when finishing setup for an existing draft with custom instructions.
If that happens, reopen the same agent. Check the saved connection and model
under **Harness / Runtime**, the existing `AGENTS.md` under **Identity**, and
the agent’s readiness on the roster before retrying setup.

After verifying all five agents use the HTTPS connection, retire the old
loopback connection. Stop and remove the `paperclip-inference` sidecar through
the running Compose release and revoke its dedicated legacy key. The file
`/var/lib/homecompute/paperclip/inference-key` controls legacy profile startup.
Archive it with the protected pre-migration state before removing it from that
path.

## External connectors

Paperclip starts on an internal bridge. The optional connector proxy allows
outbound TLS to exactly `my.paperclip.app`, `api.github.com`, and `github.com`.
The managed GitHub connector also opens direct TLS sockets to
`api.githubcopilot.com` and `github.com` for MCP and OAuth discovery. Paperclip
resolves those names to two fixed listeners on internal Docker networks. The
relay resolves and validates each public GitHub destination and passes through
TLS so Paperclip checks the certificate against the original hostname.
Run the following from a published release on home-core to enable it. The
installer takes the deployment lock, restarts Paperclip and any running model
relay, and checks connectivity from Paperclip before recording the opt-in.

```sh
sudo python3 "/srv/homecompute/releases/$revision/scripts/deploy-paperclip-connector-egress.py" \
  --revision "$revision"
```

In Brave, use **Connect GitHub** to enroll with Paperclip Cloud, then approve
the GitHub installation for selected repositories. Project **Configuration**
lists the repositories visible to that account. Use it to attach the source
repository for each project. Paperclip Cloud enrollment and GitHub repository
consent are stored through their supported application flows.

The full host rollout honors the root-owned `connector-egress-enabled` marker
in `/var/lib/homecompute/paperclip`. Run the same installer with `--disable`
to return Paperclip to its original internal network. To add another provider,
review its required endpoints and transport before extending `connector-egress.py`.

## Codex sandbox

The Paperclip container uses `codex-seccomp.json`, based on the effective
capability-free Docker Engine 29.9.2 profile observed on home-core. The added
rules allow the bundled Bubblewrap to create descendant namespaces and mount
its private filesystem. Linux still denies privileged mounts and mount
namespace creation in the outer container. The container remains non-root
with all capabilities dropped, `no-new-privileges`, private Docker networks,
and Docker's existing masked paths.

The scoped connector installer runs `verify-codex-sandbox.sh` in a disposable
container before restarting Paperclip. The probe verifies workspace writes and
checks that Codex cannot access a separate file, the outer process environment,
or the outer loopback listener. Review the baseline against Docker's effective
profile when updating Docker, and rerun the probe when updating Paperclip or
its Codex runtime.

## Backup and rollback

Embedded PostgreSQL, database dumps, workspace files, uploads, and the local
encryption key live under `/srv/state/paperclip`. Take a consistent snapshot
before upgrading the image. From a root shell on home-core with no active
Paperclip work:

```sh
umask 077
compose=/srv/homecompute/current/deploy/paperclip/compose.yaml
backup="/srv/state/control-plane/backups/paperclip-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
docker compose -f "$compose" stop paperclip
tar --numeric-owner --xattrs --acls -czf "$backup" -C /srv/state paperclip
sha256sum "$backup" > "$backup.sha256"
docker compose -f "$compose" up -d --wait paperclip
```

To roll back after an upgrade, stop Paperclip, verify the snapshot checksum,
retain the current state separately, restore the snapshot under `/srv/state`
with UID/GID 1000, and deploy the previous compatible revision. To roll back
the initial installation, stop and remove this Compose project using its
deployed release file before redeploying the previous host revision. Remove
the UniFi record if retiring the service. Take the Compose project down
without deleting its state directory.

For a scoped first-install rollback, take down Paperclip using the selected
release Compose file and the scoped secret path. The receipt under
`/var/lib/homecompute/paperclip/FULL_RELEASE_SHA/receipt.json` identifies the
prior Caddy and Homepage config paths. Use the corresponding Compose files with
their existing `/etc/homecompute/control-plane.env` or
`/etc/homecompute/homepage.env` and run `up -d --no-deps --pull never --wait`
for the `caddy` and `homepage` services respectively. Keep the state and
receipt for recovery.
