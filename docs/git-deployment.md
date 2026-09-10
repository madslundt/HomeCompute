# Deploying published commits to home-core

The MacBook authors, validates, commits, and pushes. home-core reads GitHub over
HTTPS and never needs a GitHub write credential. The GB10 is not required for
this workflow.

## Publish from the MacBook

```bash
./scripts/validate-repository.sh
git diff --check
git add <reviewed-files>
git commit -m "Describe the resulting configuration"
git push origin master
git rev-parse HEAD
```

The MacBook remote uses `git@github.com:madslundt/HomeCompute.git`. Use the full
40-character commit printed by the last command for deployment.

## Deploy on home-core

From a clean checkout containing the deployment script:

```bash
sudo bash scripts/deploy-home-core.sh FULL_COMMIT_SHA
```

The script uses a lock, clones the read-only HTTPS repository into
`/srv/homecompute/releases/FULL_COMMIT_SHA`, checks out the exact commit, and
refuses locally modified release directories. It builds and switches
`nixosConfigurations.home-core`, pulls digest-pinned images, and applies the
gateway and production n8n projects with health checks.

NixOS writes the non-secret runtime settings to `/etc/homecompute`. SOPS decrypts
credentials under `/run/secrets`; plaintext credentials are not placed in the
Nix store or Git. Both the host and the configured MacBook age recipient can
read the encrypted source. Never commit either private age identity.

A successful deployment updates `/srv/homecompute/current` and
`/var/lib/homecompute/deployed-revision`. The prior successful release remains
available through `/srv/homecompute/previous`. Docker restarts the containers on
boot; there is no automatic Git pull or unattended image updater.

To redeploy a prior compatible release, run the script with that release's
commit. Configuration rollback cannot undo database migrations. For changes
that migrate data, retain an application-consistent backup and follow the
application's supported rollback procedure; the script does not automatically
roll back databases on failure. If a deployment fails after NixOS activation or
a partial Compose update, `current` still names the last successful deployment;
inspect actual host/container state before retrying.

## Recovery prerequisites

A fresh host needs its disk/hardware configuration, administrative SSH access,
Tailscale enrollment, the age identity, and restored `/srv/state` data. The
script refuses to deploy the production n8n project without its existing database
and encryption configuration. Git alone does not restore workflows, credentials,
PostgreSQL contents, or Caddy CA state. Off-host backups remain deferred.

## Books importer

The book stack is tracked and checked by the repository validator but is not
reconciled by the deployment script. Its application images and Calibre mod are
pinned to registry digests resolved on 2026-09-04. The migrated stack is managed
with its dedicated Compose file so a general host deployment cannot initialize
or replace its state as a side effect.

The existing image startup model uses root and writable image filesystems;
we preserved that model while bounding CPU, memory, process counts, and logs,
and disabling privilege escalation. UI ports are published on loopback plus
the host's explicit LAN and Tailscale addresses. The five state directories and
credentials have been migrated and the three services runtime-qualified on
`home-core`; the retained HAOS source is rollback material. See
`config/README.md`. Encrypted credentials are supplied through SOPS, and NixOS
supplies the image settings and bind addresses at
`/etc/homecompute/books_importer/runtime.env`.
