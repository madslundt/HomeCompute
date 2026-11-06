# Oh My Hermes managed integration audit

Date: 2026-11-06. Scope: source-only audit of Oh My Hermes commit
`8e9263c14c34834b203135409b49c89831878501`, which declares version 4.0.6.
Target: HomeCompute's existing managed Hermes 0.23.0 / NemoClaw 0.0.141 /
OpenShell 0.0.116. No upstream installer, plugin, or live mutation was executed
for this research. The source was cloned at the exact commit and read locally.
The repository AGENTS, CONTRIBUTING, SECURITY, and Hermes runbook were read.
This document proposes an integration; it does not qualify a deployed runtime.
[Pinned package metadata](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/pyproject.toml#L1-L16).

## Recommended first deployment

Install the pinned distribution in an independent environment under
`/sandbox/.local`, then use the supported setup command to install its managed
skills and plugin under the existing writable Hermes home. Keep Hermes native
memory selected, use the approved local model for every mixture category, cap
actual Hermes delegation at one, and disable optional OMH browser replacement.
The native plugin registers its tools and hooks even when it is not the selected
external memory provider. This permits adding OMH workflows without switching
the established memory provider or message transport.
[Plugin registration](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/__init__.py#L137-L182),
[memory slot behavior](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/install/config_adapter.py#L1416-L1488).

Use the approved existing endpoint `http://ai.home.arpa:18080/v1`, alias
`automation-moe`, reasoning `medium`, and literal deferred
`${COMPATIBLE_API_KEY}`. OMH routing documents need no endpoint or credential.
Preserve HomeCompute's managed model, custom-provider, gateway, Mattermost,
browser/CDP, MCP, and access policy configuration. HomeCompute's owning source
and lifecycle must declare all persistent additions; an interactive installer
receipt alone is insufficient. See [Hermes runbook](../../deploy/hermes/README.md)
and [repository security policy](../../SECURITY.md).

## Distribution and writable runtime

The package is pure Python, requires Python 3.11 or newer, and declares no
runtime dependencies. Building from source requires setuptools >=77. Prefer a
wheel built from the pinned source outside the production sandbox, with recorded
SHA-256, then transfer it as an artifact. A source archive installed by pip can
otherwise request build dependencies. Do not pip-install into the immutable
Hermes `/opt` environment.
[Build/runtime requirements](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/pyproject.toml#L1-L16).

The official pinned `install.sh` supports a local wheel/archive/path through
`OMH_CHANNEL=local` and `OMH_PACKAGE_URL`; its independent venv and command
locations can be configured. A suitable repository-owned wrapper can supply:

```text
OMH_CHANNEL=local
OMH_PACKAGE_URL=/sandbox/.local/share/homecompute/oh_my_hermes-4.0.6-py3-none-any.whl
OMH_PYTHON=<existing compatible Python executable>
OMH_VENV_DIR=/sandbox/.local/share/omh/venv
OMH_BIN_DIR=/sandbox/.local/bin
OMH_PIP_ARGS=--no-index --no-deps
OMH_RUN_SETUP=0
```

The installer deliberately shell-splits `OMH_PIP_ARGS`, so supply only fixed
repository-owned flags. Run only the reviewed pinned installer or an equivalent
repository-owned artifact installer, never a moving remote script. The official
default is a separate venv; direct mode would touch a different Python
installation and is unnecessary here.
[Installer environment](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/install.sh#L9-L41),
[pip invocation](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/install.sh#L309-L338),
[local artifact support](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/install.sh#L526-L589).

The installed plugin is a thin bundle under `$HERMES_HOME/plugins/omh`.
Setup records the installed distribution's `package_site` in its
`.omh-plugin-manifest.json`. When Hermes cannot normally import `omh`, the
bundle adds a narrow namespace finder for `omh` only, checks distribution and
plugin versions match, and does not broaden `sys.path`. A raw source directory
copy without the matching installed distribution metadata is insufficient.
Keep the independent distribution reachable after setup, and restart Hermes
when changing package versions.
[Import bridge contract](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/package_site.py#L1-L43),
[managed bundle installation](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/install/plugin_pack.py).

## Smallest supported setup

With the native gateway stopped for the owning guarded lifecycle, the supported
noninteractive command is:

```bash
/sandbox/.local/bin/omh \
  --omh-home /sandbox/.omh --hermes-home /sandbox/.hermes \
  setup --no-interactive --json --no-menubar \
  --default-executor hermes --memory-mode off --core
```

Add pinned source metadata using `--source-ref` and `--package-url` if the
wrapper installs the command separately. `--core` is the smallest supported
skill set: chat, plan, status, handoff, and doctor essentials. It excludes ULW
engines and most workflow skills. Omit `--core` if those workflows are part of
the intended rollout; full is the default and has a larger recurring skill
index/context footprint. Setup always installs the native plugin. Do not add
`--model-setup`, provider login, `--with-mcp`, or a profile pack to this existing
configured household runtime.
[CLI setup options](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/setup.py#L6718-L6805),
[setup implementation](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/setup.py#L3273-L3422).

Setup registers the managed skill directory in `skills.external_dirs`, enables
`omh` in `plugins.enabled` unless explicitly disabled, records its OMH home,
installs the managed bundle, seeds editable model chains, and writes
`/sandbox/.omh/setup-profile.json`. It may synchronize existing named bot
profiles; profiles are not required for this deployment. Review the complete
rendered config delta rather than assuming setup changes exactly one key.
Its text mutation uses guarded atomic replacement, refuses an unsafe config
symlink, and preserves the existing file mode. Retain HomeCompute's native
file ownership/mode checks.
[Config application](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/setup.py#L2600-L2745),
[plugin enablement](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/install/config_adapter.py#L733-L808),
[guarded config writer](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/install/config_adapter.py#L2280),
[atomic text writer](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/system/local_store.py#L150-L187).

## Local model routing and bounded execution

Write `/sandbox/.omh/routing/model-chains.json` with schema
`mixture_chain_overrides/v1`. Every category must have exactly the single
entry `{"model":"automation-moe","reasoning_effort":"medium"}`. All 18
categories at this pin are:

```text
ultrabrain, deep, architect, unspecified-high, unspecified-low, quick,
writing, visual-engineering, artistry, capable, simple-work, deep-work,
google-search, x-search, review, swarm, forensics, synthesize
```

Example shape (expand to every category):

```json
{
  "schema_version": "mixture_chain_overrides/v1",
  "categories": {
    "ultrabrain": [{"model": "automation-moe", "reasoning_effort": "medium"}]
  }
}
```

The loader replaces only named categories, and invalid documents are ignored
entirely. Omitted categories retain cloud-model defaults. Generate the complete
map from the pinned category vocabulary and assert set equality plus an
`applied` loader status. The sample above alone is not local-only configuration.
[Shipped chains and strict parser](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/hermes_delegation.py#L50-L352).

If needed for the existing named Hermes provider, the supported optional file
`/sandbox/.omh/routing/model-providers.json` has this shape:

```json
{
  "schema_version": "model_provider_routes/v1",
  "models": {
    "automation-moe": {"provider": "custom", "model": "automation-moe"}
  }
}
```

Use the actual existing provider identifier; `custom` here is conditional on
that being the configured identifier. An alias without a route is returned
unchanged with no provider override, allowing native parent inheritance. Never
invent a new provider or copy a runtime-resolved credential. Native delegation
endpoint guard checks must remain active; prove the observed child model and
request endpoint after a real dispatch.
[Provider route format](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/hermes_delegation.py#L1104-L1195),
[delegation route writer](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/delegation_routing.py),
[endpoint guard](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/route_endpoint_guard.py).

Merge the following into setup-profile `parallelism`, preserving unrelated
profile fields:

```json
{
  "schema_version": "parallelism_policy/v1",
  "default_concurrency": 1,
  "global_concurrency": 1,
  "lane_budget_default": 1,
  "per_owner": {},
  "max_depth": 1,
  "run_spawn_ceiling": 1
}
```

Set process `OMH_SECURITY=strict` if zero subprocess retries are required.
`parallelism.max_retries` is not read as a profile override; strict posture
provides the zero-retry clamp. Crucially, OMH's policy enforces only explicit
`omh coding fanout dispatch` subprocess execution. Its Hermes lane budget is
advisory. The native DAG reads `delegation.max_concurrent_children`; preserve
or add Hermes's actual cap of one in the owning managed config and verify its
native enforcement. Default OMH fanout is five with a global ceiling of eight.
[Policy enforcement boundary](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/coding/parallelism_policy.py#L1-L162),
[strict retry clamp](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/system/security_posture.py#L80-L91),
[native DAG capacity](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/tools/dag_tool.py#L523).

## Memory, Mattermost, and browser boundaries

Set `plugins.entries.omh.settings.dreaming_mode: 'off'`. The only upstream
dreaming choices are `off` and `reminder`, with reminder the default; this is
not an autonomous consolidation daemon. Set optional
`plugins.entries.omh.settings.browser_adapter.enabled: false`, preserving the
existing browser. Group-chat activity and egress-attempt integrations are also
opt-in, so do not enable them during this compatibility rollout.
[Dreaming settings](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_dreaming.py#L68-L79),
[optional browser hook](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/__init__.py#L525-L550),
[optional integration settings](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/__init__.py#L183-L202).

`--memory-mode off` preserves a native/unset or foreign memory provider and
clears only an existing OMH selection. Tools remain registered, but OMH memory
capture is refused as `project_memory_disabled`. To support reviewed OMH-local
candidates later, `review-first` sets `review_required: true` and
`auto_approve_safe: false`; default `auto-safe` is unsuitable for an initial
review-only rollout. A setup with review-first can select OMH's external
provider, so explicitly keep the native slot unchanged in the final owning
config. Supported `omh ... memory provider --disable` releases only OMH's
slot without deleting memory or changing a foreign provider. Reviewed
candidates wait for `omh memory review` and are not equivalent to accepted
facts.
[Memory modes](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/profiles/setup.py#L179-L199),
[provider disable API](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/memory.py#L920-L950),
[capture receipts](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/tools/memory_tool.py#L790-L820).

There is no supported configuration-only mapping for a private Mattermost
owner principal at this pin. OMH's verified-messenger classifier covers
Telegram, Slack, WhatsApp, WhatsApp Cloud, Signal, and Discord, but excludes
Mattermost. Its provider comments explicitly state that the host supplies
platform/chat/user context and never supplies `principal_context`; an
unclassified gateway stays shared. Do not spoof another platform or accept
prompt-supplied principal identifiers. Mattermost replying and private-owner
memory qualification are separate claims. An upstream-compatible adapter
would need audited trusted Mattermost source identity, authorization and
negative cross-user recall tests before private memory can be claimed.
[Covered platforms](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_messenger.py#L80-L92),
[host identity intake](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_provider.py#L434-L510).

The provider reads existing Hermes memory snapshots for its briefs; therefore
claiming it never reads native memory would be incorrect. Preserve existing
native memory files and their checksums. Keeping the provider unselected and
dreaming off is the narrowest first rollout.
[Native snapshot reader](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_provider.py#L1845),
[native memory import path](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/memory_provider.py#L2342).

## Qualification and rollback

The plugin declares Hermes >=0.21.1, but the committed compatibility matrix
contains only 0.21.1. Admission of 0.23.0 is not measured compatibility proof.
Require actual native PluginManager loading, nonzero registered OMH tool and
hook counts, matching package-site version, and no load failure. A successful
OMH doctor/setup result explicitly does not prove Hermes reloaded or used the
plugin.
[Manifest](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/plugin_bundle/omh/plugin.yaml),
[tested matrix](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/install/plugin_compat.py#L8-L24),
[setup evidence boundary](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/setup.py#L3440-L3460).

Before readiness, use the existing guarded HomeCompute lifecycle and prove:

- owner-authenticated Mattermost DM and reply after restart;
- public browser navigation and existing private-destination denial;
- unchanged MCP configuration and tools, including browser tool reachability;
- native local inference and one OMH-routed native child using automation-moe,
  medium reasoning, the approved endpoint, and enforced fanout one;
- package/config drift comparison without resolving or logging secrets;
- native memory contents preserved, process health stable, and resource growth
  bounded through restart and an idle observation interval.

These are integration acceptance requirements inferred from HomeCompute's
existing [runbook](../../deploy/hermes/README.md), not upstream test results.
Keep OpenShell network policy as the actual no-cloud enforcement boundary;
model-chain preferences alone are not a general egress policy.

Rollback should stop the gateway, reapply the prior tracked configuration and
plugin selection through the owning lifecycle, restart, then repeat Mattermost
and browser health checks. Preserve OMH and native memory data for review.
`omh uninstall --registration-only` removes only skill registration and leaves
the plugin, so it is insufficient to deactivate OMH. `uninstall --all` removes
managed plugin/state and optionally the independent command environment; use
`--dry-run --json` to inspect its shared-profile reference checks before any
future cleanup, and do not use force to bypass managed-edit safeguards.
[Uninstall implementation](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/setup.py#L2837-L2945),
[uninstall options](https://github.com/rlaope/oh-my-hermes/blob/8e9263c14c34834b203135409b49c89831878501/src/commands/setup.py#L6940-L6952).
