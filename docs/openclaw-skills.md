# HomeCompute OpenClaw skills

The source bundles are under `deploy/openclaw/skills/`:

| Skill | Purpose |
| --- | --- |
| `homecompute-operations` | Interpret health/update observations and broker task/action state. |
| `household-summary` | Summarize supplied household reports with Copenhagen dates and n8n ownership. |
| `incident-triage` | Investigate supplied failure evidence and prepare a bounded broker request. |

These bundles contain instructions only. They add no binaries, credentials,
network destinations, schedules, broker registration, or execution permissions.
`config/openclaw-nemoclaw.json` names all three in the default skill allowlist.
The managed `main` agent inherits that list. The standalone Compose fallback is
not a deployment target for this installation.

Installation was verified on 2026-10-09: native installs succeeded, installed
file hashes matched the source bundles, the managed gateway restart passed, and
the running Gateway inventory reported all three skills eligible with no missing
requirements or agent-filter block. Three skill-frontmatter validations and five
existing managed-config preservation tests passed. The native inventory's
`modelVisible` flag describes skill selection metadata; it does not override the
file-read requirement below. See `openclaw-skills-validation.json` for the
sanitized runtime receipt. No model skill-use probe was claimed.

## Managed installation

Run commands in the existing agents guest as `hermes-operator`, with gateway
9123. Copy the source bundles into an operator staging directory on that guest
before uploading; an upload source is a guest-host path, not a Mac path.

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw upload \
  /path/to/staged/skills /sandbox/homecompute-skills/
```

Check the uploaded directory layout before selecting each source directory.
For each bundle, use the native installer without overwriting another skill:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw exec -- \
  openclaw skills install /sandbox/homecompute-skills/homecompute-operations --agent main
```

Repeat with `household-summary` and `incident-triage`. Inspect the existing
allowlist before setting the reviewed union of its names and these three. On
the initially empty canary, the compare-and-set operation is:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw exec -- \
  openclaw config set agents.defaults.skills \
  '["homecompute-operations","household-summary","incident-triage"]' \
  --strict-json --expect-current-json '[]'
```

Apply startup-only config changes through the supported in-sandbox lifecycle:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw gateway restart --quiet
```

Inspect each skill using `openclaw skills info <name> --agent main --json` and
check requirements with `openclaw skills check --agent main --json`, through the
same managed exec prefix. New skill sources live in
`/sandbox/.openclaw/workspace/skills/<name>/SKILL.md`. Preserve all unrelated
managed config fields and the supported NemoClaw lifecycle. Management calls
must be sequential: NemoClaw uses a shared gateway-state migration lock even
for some read commands.

## Availability and tool restrictions

OpenClaw 2026.9.1's built-in runtime loads skill bodies through `read` (or Code
Mode's skill reader). Its system-prompt builder omits the automatic skill catalog
when neither path is available. Installation, eligibility, and allowlisting do
not prove that the model can read the instructions.

The current supervised phone canary has no effective native tools: its finite
allowlist omits `read`, Code Mode is disabled, and all four previously allowed
tools are additionally denied. These restrictions follow the native replay
issue documented in `openclaw-communication.md`. This installation preserves
them. The skills are installed and allowlisted but automatic use remains
unavailable until a qualified file-read path exists. Do not claim a synthetic
model skill-use test passed on the tool-free phone canary.

Browser and CLI capability are also separate from skill installation. The live
config still disables the browser and shell. A dedicated browser and narrow
CDP network policy are now installed, but activation is blocked by the failed
sandbox; see [tool installation](openclaw-tools.md). A browser needs a
Chromium/profile or remote browser service,
the browser tool, and suitable network permissions; a visible browser also needs
a display-capable host. CLI work needs the required executable plus scoped
execution permissions. For HomeCompute, retain the broker for host/device
actions and use a separately scoped browser/CLI worker for new capabilities.

Primary references:

- [Pinned skills CLI](https://github.com/openclaw/openclaw/blob/v2026.9.1/docs/cli/skills.md)
- [Pinned skill-reading prompt gate](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/agents/system-prompt.ts)
- [Browser configuration](https://docs.openclaw.ai/tools/browser/configuration)
- [Exec tool](https://docs.openclaw.ai/tools/exec)
