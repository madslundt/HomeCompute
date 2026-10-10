# OpenClaw browser and CLI installation

The headless browser is installed on the agents guest. A bounded CLI helper and
the bundled `browser-automation` skill are also installed in the OpenClaw
workspace. Activation is **unfinished**: a native skill-management command
exceeded the sandbox's existing 2 GiB memory limit, and OpenShell 0.0.116 left
`agent-openclaw` in `Error`. The live tool allowlist, disabled browser and denied
exec configuration were still unchanged in the saved configuration.

Phone receiver and communication adapter are paused with their cursors and
receipts retained. A private copy of OpenClaw state and an online, consistent
SQLite gateway backup passed `integrity_check`. Replacement and restoration of
the failed sandbox require operator approval. Hermes remains on gateway 8080.

## Installed components

| Component | Scope | Verified |
| --- | --- | --- |
| Chromium 154.0.8037.92 | Dedicated headless profile and authenticated CDP endpoint | Public page navigation and title via authenticated WebSocket |
| Squid 6.13 | Public HTTP/HTTPS egress for the browser | `example.com` succeeds; private and metadata addresses return 403 |
| `homecompute-cli` | Fixed operations inside the OpenClaw workspace | Native version checks and `calculate '6*7'` returned 42 |
| `browser-automation` | Bundled skill copied to the workspace | File exists in the private backup; model use remains unqualified |
| `read`, `exec`, `browser` | Prepared finite tool grant | Native config dry-run passed; grant not applied |

The CLI helper accepts `versions`, `calculate EXPRESSION`, `files [DIRECTORY]`,
`digest FILE` and `json-format` (JSON on stdin). Its arithmetic parser cannot
execute Python; file operations reject parent traversal and symlink escapes.
The planned native exec allowlist contains only the exact helper executable,
with no automatic skill executable grants or general shell/interpreter grants.
Existing coding and infrastructure actions continue through the broker.

## Browser isolation

Sources are under `deploy/openclaw/tools/`. The installed immutable local image
is `sha256:2328d604f0956fd20475549b3cd000900a9c39ac3671e850ba7b61b8c8e14f41`.
It was built from the pinned Debian base in the Dockerfile and signed package
repositories. Rebuilding that Dockerfile can produce newer package versions;
record and qualify the resulting image before deploying it.

The browser container runs as UID 10001 with a read-only root, dropped
capabilities, no new privileges, a dedicated profile volume, one CPU and 1 GiB
RAM. Chromium uses `--no-sandbox`; container and network restrictions provide
the isolation here. Its only external path is the separate proxy. The proxy
denies private, loopback, link-local, metadata and reserved destination ranges.
The browser has no OpenClaw/model credentials or host workspace mounts.

A small ingress relay publishes only `172.18.0.1:18800` on the Docker bridge.
The actual CDP handler requires a separate random token for both discovery and
WebSocket requests. Token bytes stay in the private operator directory and,
after activation, OpenClaw's protected configuration. They must not appear in
logs, reports or committed configuration. The scoped NemoClaw policy permits
only Node access to that exact bridge endpoint, with fixed private IP pinning.

This setup supplies headless browsing. A visible browser would require a
separately configured display-capable host/profile. OpenClaw supports managed
and remote browser profiles; see the [browser documentation](https://docs.openclaw.ai/tools/browser).

## Installation and activation

For a new browser installation, stage the tools directory and build its image
on the agents guest. Docker Compose is not installed there, so the current
installation uses `scripts/setup-openclaw-browser.py --directory STAGING
--image-id sha256:IMAGE_ID`. The installer refuses to replace existing browser
containers. `compose.yaml` describes the same deployment for a host with
Compose available. Preserve the private token and profile when updating.

Apply the browser policy through the supported scoped lifecycle:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw policy add \
  --from-file STAGING/tools/browser-policy.yaml \
  --trusted-private-host 172.18.0.1 --yes
```

The policy was applied and survived the VM restart. Recover the failed native
sandbox before proceeding. Preserve its saved workspace, sessions, skills and
plugin state, while retaining the replacement runtime's newly generated gateway
authentication and native ownership records. Do not copy an old auth token over
the managed replacement's auth or edit OpenShell's phase database.

After recovery, upload the CLI helper to the workspace's `bin` directory, the
activation script outside the readable workspace, and the dedicated browser
token to `/sandbox/browser-token`. Run `enable-openclaw-tools.py` through
NemoClaw exec for a native schema dry-run. Use `--apply` to back up the current
configuration and approvals, install the bundled skill, and apply the finite
tool configuration and exact executable approval. The script limits management
CLI V8 heaps to 384 MiB within the existing sandbox resource budget. It refuses
unexpected existing agent approvals. Do not blindly repeat an interrupted apply:
inspect the saved config and approvals first.

Restart with `nemoclaw agent-openclaw gateway restart --quiet`. Then verify
actual model turns for reading a skill, the allowed CLI helper and browsing a
public page; verify denial of arbitrary programs and workspace escapes. Native
CLI browser status can contain the authenticated URL: project safe fields
instead of copying its full output into a report.

The communication receipt handler is prepared to accept completed browser/exec
turns only with matching session/run identifiers, the fixed model route and a
visible terminal receipt. Unknown outcomes remain paused and are never retried.
Fixture tests passed, but real native browser/exec receipt qualification remains
pending. Resume the adapter and receiver only after those checks, using the
privately saved exact process arguments without resetting their state.

## Recovery findings

A guest reboot first stopped gateway 9123. Scoped start/recover could not restore
the host gateway, and completed onboarding was not resumable. Supported
non-interactive onboarding **without** `--fresh`, `--recreate-sandbox` or `--yes`
restored the same sandbox and route successfully.

During tool activation, the native skill-install command then caused an OOM
kill. OpenShell now rejects start because the phase is `Error` rather than
`Stopped`, and rejects stop because it is not `Ready`. Supported rebuild also
aborted while inspecting MCP state; it did not replace the sandbox. Restarting
the same Docker container did not repair OpenShell's phase, so that container
was stopped again. No phase database edits, runtime upgrade, Docker storage
changes, test Telegram messages or Hermes lifecycle mutations were performed.

See `openclaw-tools-validation.json` for the sanitized evidence. Unattended
gateway reboot supervision remains unqualified.
