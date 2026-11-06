# Hermes 0.23 qualification candidate

This directory is an isolated upgrade candidate, not the production image.
`../Dockerfile` and `config/hermes-release.json` remain the production authority
until the checks in `qualification.json` pass. Never point the working household
sandbox at an image merely because this build succeeds.

The immutable upstream release is `v0.23.0`, commit
`2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`. Its source uses inert package-version
placeholders; use the official install-stamp generator for runtime provenance.
There is no matching `hermes-agent@0.23.0` npm package at the initial review.
Do not invent an npm integrity value or label the source placeholder a release
version. The source archive and Python runtime are checksum pinned.

The candidate retains the complete pinned NVIDIA managed image, replaces only
Hermes's source and dependency environment, and ports its managed changes onto
the new source. Python 3.14.9 is the release's own `pm/lock.json` Linux x64 pin.
The dependency environment is built through upstream PM with its frozen lock;
the only dependency addition is the exact PyYAML extra used by NVIDIA's retained
plugin. Regenerating that lock changed only the extra's metadata, not versions
or artifact hashes. The managed CLI adapter must validate against the new parser. Frontend builds
use the release-pinned Node 26.12.1; NVIDIA's Node 24 remains installed for its
managed configuration and supervisor helpers. The source assembler binds Python
imports, while the original console entrypoints retain NVIDIA's process identity.

## Integration review

`managed-integration.patch` carries the existing cross-identity ledger modes,
runtime metadata paths, cron restore drain/re-arm, bounded auxiliary requests,
ledger-to-agent-home mapping, A2A neutralization, owner DM handling, profile
defaults, and disabled duplicate request dumps. Refactored source locations are
explicit in the patch. External-supervisor restart and proxy-aware MCP HTTP
transports now have upstream implementations; do not reintroduce the old
implementations over them. The removed legacy session-reset datatype needs no
port. The session preview now changes the single shared SQL helper.

Hermes 0.23 reserves profiles with a no-follow lock open. For the fixed managed
home, map that open to the real `runtime/gateway.lock` inode, retaining all inode,
ownership and flock checks. Its control socket uses an owner-private
`runtime/control` directory; discovery and the server validate that directory
and the kernel-reported peer UID. The logical profile home remains unchanged.
The runtime probe exercises competing lock owners, a real Unix connection and
rejection of an unsafe control-directory mode. Copy the managed plugin into the
allowed `/opt/hermes` tree rather than linking to a path outside Landlock roots.
The first synthetic sandbox `hermes-v023-check` is retained in Error after these
startup incompatibilities. `hermes-v023-verify` is retained after the next
session-authority startup check; the corrected attempt uses `hermes-personal-v23`. This name can become the owner instance after qualification, leaving the previous instance intact for rollback.

Session authority, policy and credential readers must map the exact declared
`state.db -> runtime/state.db` alias back to the logical profile home. They keep
their original resolution behavior for every other path, and reject a foreign
database or an unexpected alias. Database registry/inode identity remains the
physical path. This prevents the new gateway from reading configuration or
credentials below `runtime` instead of the profile directory.

The old browser tools and agent-browser resolver were removed upstream.
`browser_exec` uses the release-pinned browser-harness package. The CDP override
resolver moved to `tools/browser_session_cdp.py`; only the exact owned endpoint
is routed through the existing image-owned opaque-credential relay. Public
navigation and private-network denial must pass through the actual new tool.
Browser worker networks, credentials and profile remain separate from OpenClaw.

Only Mattermost is in scope. WhatsApp proxy/dependency patches, the removed
Hindsight/Langfuse built-ins, and Google Chat adapter compatibility are not
qualified by this candidate. They must remain unconfigured. Additional profiles
also remain deferred.

## Build and qualification

From an exact reviewed commit published on `origin/master`, stage the complete
`deploy/hermes` directory in an operator-owned guest release, then build without
touching the running sandbox:

```bash
docker build --progress=plain \
  --file deploy/hermes/Dockerfile.latest \
  --tag homecompute/hermes-qualification:FULL_SHA deploy/hermes
```

Retain the build receipt and observed immutable image ID privately. A failed
managed adapter check is an upgrade blocker; do not weaken or skip it. Building
an image does not migrate application data or establish managed readiness.
After a successful image build, the guarded guest helper can create the
separate `hermes-personal-v23` sandbox using the same published recipe:

```bash
./scripts/setup-hermes-guest.sh qualify-latest --config "$config"
./scripts/setup-hermes-guest.sh probe-latest --config "$config"
```

This command still requires the existing household backup/network gates and
approved local route. It clears inherited messaging credentials, changes no
owner configuration and installs no boot unit. Do not connect it as a second
listener for the owner's bot. Qualification must use synthetic turns only.
`probe-latest` verifies the exact runtime stamp before attaching existing
OpenShell-held credentials. Its Mattermost policy permits only identity lookup
and WebSocket authentication, and it explicitly disables the candidate's bot
listener. It opens and closes a named public browser tab in the dedicated Hermes
worker; OpenClaw's browser remains separate. Native canary stop/start is part of
the probe. The owner DM and final boot test still require the selected release.

Before any cutover, complete all runtime gates in `qualification.json` on an
isolated sandbox, retain the prior image and verified recovery boundary, then
publish the selected production recipe and apply the guarded native lifecycle.
Code rollback alone cannot undo state migrations.

The frontend uses the release-pinned Node 26.12.1 and npm 12.0.2. Its only additional system library is checksum-pinned Debian libatomic1 14.2.0-19; package-manager repair is unnecessary. Linux upstream tests run without a preselected profile home so their own isolation fixtures control it.
