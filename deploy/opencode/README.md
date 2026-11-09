# OpenCode on Home Core

OpenCode runs as the unprivileged `agent` account on `home-core` and serves its web client
over Tailscale Serve at `https://home-core.<tailnet>.ts.net:18793`. Find the
exact MagicDNS URL with `tailscale status --json | jq -r '.Self.DNSName'` and
append `:18793`. The application listens on
`127.0.0.1:4096`; NixOS does not open a LAN or public firewall port. Tailscale
limits network access to the tailnet, and OpenCode requires HTTP Basic auth
(user `opencode`) using the password materialized by sops-nix.

The service starts in `/run/homecompute-opencode/projects`, backed by
`/srv/state/opencode/projects`. Its systemd sandbox exposes that writable
project tree and OpenCode's private state; the host home directories are
hidden. OpenCode can run commands and modify files within the projects tree, so
only put projects here that you trust this service to edit. Clone or copy a
repository into this directory before opening it. Existing Git repositories
can be used without exposing either account's SSH keys to the service; the
OpenCode process cannot use host SSH keys. Use HTTPS remotes for operations
from the UI. Existing directories under `/home/mads/projects` do not appear
automatically; copy selected repositories into the OpenCode workspace. The
service has no GitHub token or outbound SSH key, so authenticated pushes from
OpenCode need a separately reviewed credential setup. Push private changes from
your workstation until then.

OpenCode's sessions, configuration and LiteLLM key persist in
`/srv/state/opencode` with mode `0700`, owned by `agent`. Provider traffic uses
the existing HomeCompute gateway at
`https://home-core.tail479ad.ts.net:8443/v1`. Its virtual key is separately
revocable and scoped only to `automation-moe`; administrator and other
application keys are not shared. OpenCode is configured to use only the
checked-in `homecompute` provider, and project-level OpenCode configuration is
disabled so a repository cannot silently add a cloud provider.

The provider configuration routes model requests through the local gateway,
but shell commands still have the host's ordinary network access. OpenCode can
run arbitrary project commands, so keep restricted source out of the workspace
unless its network and data use are approved.

This private state is not currently included in the HomeCompute application
backup archive. Git is the recovery path for project content. If the LiteLLM
key file is lost, the normal Home Core deployment provisions a replacement;
revoke the lost key through LiteLLM key management. Do not put
employer or other restricted source in this shared service unless its data
handling is approved for the selected model provider.

## First use

After deployment:

1. Retrieve the HTTP Basic password from `/run/secrets/opencode/server-password`
   on `home-core`; it is an `OPENCODE_SERVER_PASSWORD=...` environment line. Keep it
   in the password manager and do not paste it into a project or log.
2. Open the Tailscale URL above and sign in as `opencode` with that password.
3. Select `HomeCompute local coding model` with `/models`. It uses the
   `automation-moe` LiteLLM alias and the service-specific key provisioned by
   the normal Home Core deployment.
4. Add a repository below `/srv/state/opencode/projects` and choose it in the web
   interface. Use HTTPS for public repository clones or copy a private working
   tree from your workstation. The service has `git`, Node.js, Python, and common shell tools on
   its command path; add project-specific toolchains through the repository's
   own documented setup.

The selected route stays within HomeCompute's existing local inference
gateway. Model requests can include project content; keep only source approved
for the local gateway in this workspace.

## Operations

Check service and tailnet route state with:

```bash
systemctl status homecompute-opencode.service homecompute-opencode-serve.service
tailscale serve status
```

OpenCode logs can contain project paths and application diagnostics; inspect
them locally and redact before sharing. Restart after a package or configuration
change with `sudo systemctl restart homecompute-opencode.service`. To stop web
access while retaining its state, stop both units:

```bash
sudo systemctl stop homecompute-opencode-serve.service homecompute-opencode.service
```

Restore the previous NixOS generation to roll back the service and route. The
rollback does not delete `/srv/state/opencode` or project files.
