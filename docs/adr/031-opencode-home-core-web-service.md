# ADR-031: Host OpenCode as a private Home Core web service

## Context

ADR-018 requires a separate security and operations review for another remotely
reachable developer harness. The owner wants OpenCode available from software
projects on `home-core`, including its browser client. OpenCode executes model
generated shell commands and edits the active project, so its host identity,
filesystem scope, provider credentials and network route need explicit limits.

## Decision

Run the pinned Nixpkgs OpenCode package as a systemd service under the existing
unprivileged `agent` account. Keep it in the existing resource-limited
`user-1001.slice`. Start the web client in `/srv/state/opencode/projects` and
persist its independent sessions, provider logins and settings under
`/srv/state/opencode`. The service sandbox exposes only that state tree; it
hides user home directories and keeps host SSH keys, Docker, sudo and production
secrets outside the service boundary.

Bind the application to loopback on port 4096 and publish it through
Tailscale Serve on HTTPS port 18793. Do not add a host firewall listener or
Funnel route. Require OpenCode HTTP Basic authentication, with its password
delivered by sops-nix from the dedicated encrypted `secrets/opencode.sops.yaml`
document. The normal Home Core release lifecycle provisions a separate
LiteLLM virtual key limited to the existing `automation-moe` model alias. The
OpenCode config enables only this HomeCompute provider and disables project
OpenCode config; it receives no administrator, shared client, or application
credential.

The owner clones or copies approved projects into the OpenCode project tree.
Keep one top-level worktree per concurrent task, following
[agent harness operations](../agent-harness-operations.md). Review each
provider's data handling before sending project content. The application state
is not in the current HomeCompute backup archive; projects remain recoverable
from their Git remotes, while provider sign-ins can be repeated after restore.

## Alternatives

- Run as `mads`: rejected because a remote coding service should not inherit the
  administrator's shell, SSH key, Home Manager state or sudo authority.
- Expose a LAN listener: rejected because private Tailscale HTTPS already
  provides the approved remote path.
- Reuse the LiteLLM key and default route: rejected because provider choice and
  cloud-data policy must remain explicit for each project.

## Consequences

OpenCode can make arbitrary changes within its isolated project and state
directories and can use its network access to contact the existing local model
gateway and Git remotes. The separate Unix account limits the impact on Home
Core. The owner must place only approved code in the service workspace.
OpenCode sessions and its gateway key file require restoration or
re-provisioning after loss of `/srv/state/opencode`.
Its provider config pins model traffic to HomeCompute's local gateway, while
project shell commands retain ordinary host network access.

## Status

Accepted; implementation is pending deployment and live verification.

## Evidence

- [OpenCode server](https://opencode.ai/docs/server/)
- [OpenCode web](https://opencode.ai/docs/cli/web/)
- [HomeCompute security policy](../../SECURITY.md)
