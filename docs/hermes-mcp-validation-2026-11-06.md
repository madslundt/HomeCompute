# Hermes household MCP verification — 6 November 2026

The owner Hermes instance `hermes-personal-v23` has a verified connection to
the existing HomeCompute MCP bridge and discovers 50 tools. Seven read-only
service checks passed through Hermes's actual MCP discovery and registered
tool handlers, both before and after a native service restart.

## Applied source and credential custody

| Concern | Applied source and lifecycle |
| --- | --- |
| Core adapter, finite household grant and independent credential | `b1fa148da8a5f69c4be417f29c50138b60dfdd04`, guarded `scripts/setup-hermes-mcp.py` |
| Initial native MCP registration | `08d34de2f3c4abbe174da7f493cfc0d9129f54e4`, `scripts/connect-hermes-mcp.py` using native `mcp add` |
| Exact Python executable policy grant and household skill | `92402f8aad0a05e5f37c129c8f1e9679a51bf152`, the same connector using native policy and skill lifecycles |

The endpoint is `https://homecompute-capabilities.home.arpa:19445/mcp`, pinned
to `10.77.20.1`. The independently generated principal is `hermes-owner`;
the retired `hermes` credential remains absent and unauthorized. OpenClaw's
original credential was compared with the private pre-installation backup and
is unchanged. All three installed core modules match their selected source
hashes. The adapter is active.

The credential was piped privately from home-core into the guest connector.
OpenShell stores it in `hermes-personal-v23-mcp-homecompute`; the agent consumes
an opaque resolver handle. Native status verified HTTP 200 for the resolved
credential and HTTP 401 for its control request, with matching private IP pins,
an attached provider, a configured MCP policy and a registered Hermes adapter.
The same verification passed after restart.

## Runtime compatibility and recovery

The selected managed image is
`sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311`.
It was running without an OOM kill. This integration did not replace the image.

The generated MCP policy matched only the old system/venv Python paths.
OpenShell observes the 0.23 interpreter as
`/opt/hermes/python-runtime/bin/python3.14`, causing CONNECT 403 despite correct
endpoint pins and provider attachment. The connector now adds only that exact
executable to the existing MCP policy after checking its original grants,
endpoint, IP and credential binding. It retains private before/after policy
backups, checks for concurrent changes and verifies complete policy readback.
All other policy fields are preserved. Native restart retained this grant.

The first core installation failed its immediate Type=simple readiness check.
It rolled back automatically and restored the original code and credential
file. Installation succeeded after the published installer was changed to wait
for the listener
without retrying an authentication verdict. Its successful installation retains
the private preimage backup and rollback receipt under
`/var/lib/homecompute-hermes-mcp`.

The source-owned household skill was installed through native `skill install`.
Its SHA-256 is
`50192bc8fd1694ca2a9a430ab5343d5237ba816e417138ac4aab10d96cfb3475`.
It covers the finite tool grant, school weekly-letter coverage, explicit
calendar selection, private/shared task separation, write preconditions and
Home Assistant approval semantics.

## Actual client checks

The tracked [probe](../deploy/hermes/probe_mcp.py) exercised these operations
through Hermes's real registry, not a simulated model or a workstation bearer:

| Tool | Verified result before and after restart |
| --- | --- |
| `school_discover` | Authenticated Aula result |
| `shopping_stores` | Tilbudstrolden store lookup |
| `system_read` for `home-assistant` | Healthy collection of admitted metadata |
| `shopping_list` | Existing household shopping list read |
| `storage_list` | Existing physical inventory read |
| `calendar_list` | Allowed household calendars listed |
| `household_task_list` | Existing owner task store read |

Only availability and success/failure were emitted as operational evidence;
household payloads and credentials were not copied into Git. The native user
service was restarted through `homecompute-hermes-personal.service`, which
invokes the retained source-owned native stop/start lifecycle. The tool count
remained 50 and all seven checks passed again.

The grant also exposes the existing bounded shopping, stock, calendar and task
write tools, plus Home Assistant proposal/status/cancel tools. These writes were
not exercised against household records. Home Assistant execution still needs
separate human review/approval. Infrastructure maintenance execution, account
administration, private-site operations and mail are outside the Hermes grant.
A synthetic request through Hermes's native chat client, forwarded to the
running gateway, asked for a public Netto store lookup. It completed with
`tool_use` and `tool_result` events, no error and the requested
`HERMES_MCP_OK` marker. This checks gateway tool use in addition to the seven
direct-handler service checks. No Mattermost message was sent for this test.

## Validation and remaining platform differences

The complete repository validator passed both before installation and for the
final implementation, including its containerized Nix flake evaluation.
All 11 focused Hermes MCP lifecycle/policy tests passed. Other focused tests
cover credential independence,
legacy denial, authenticated actor assignment, service allowlisting, exact policy
transformation, concurrent-drift refusal, startup readiness and rollback.
`git diff --check` passed. Existing environment-dependent skips remain skips;
in particular the pinned LiteLLM routing integration image was not cached
locally, and its test deliberately did not pull it. This task changed no
inference routing. A complete VM/host reboot was not repeated; the live recovery
check was the owning service's native sandbox stop/start.

`homecompute status`, `services`, `doctor` and `drift` were inspected. The normal
host release markers remain `7df6e792262ac330027b47b188eb9c156eaa68b8`
for home-core and `469618169ca43659ffe5e7e3ac7ee3589160811b` for Spark;
this task used the dedicated MCP lifecycle rather than a whole-host release.
The pre-existing `homecompute-platform-health.service` failure on home-core
remains outside this integration. Spark reported no failed units. These
targeted changes do not establish complete platform synchronization.

See the [Hermes runbook](../deploy/hermes/README.md#household-mcp-access) for
reapplication and rollback. Native MCP policy regeneration may require rerunning
the connector to restore the source-pinned interpreter grant.
