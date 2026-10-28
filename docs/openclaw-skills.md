# HomeCompute OpenClaw skills

## Team workflow skills (source change, 2026-10-28)

The team manifest in [`config/openclaw-team.json`](../config/openclaw-team.json)
assigns four additional instruction-only bundles:

| Skill | Assigned roles | Workflow |
| --- | --- | --- |
| [`meal-planning`](../deploy/openclaw/skills/meal-planning/SKILL.md) | Hestia (`cook`) | Plan meals from current stock and offers; propose shopping changes through Vega and stock changes through Ceres. |
| [`household-paperwork`](../deploy/openclaw/skills/household-paperwork/SKILL.md) | Themis (`administrator`) | Extract sourced obligations/deadlines from supplied documents; route requested durable tasks to Luna. |
| [`budget-analysis`](../deploy/openclaw/skills/budget-analysis/SKILL.md) | Plutus (`finance`) | Reconcile supplied expenses, transfers and budget assumptions without bank access. |
| [`tasks-and-reminders`](../deploy/openclaw/skills/tasks-and-reminders/SKILL.md) | Luna (`personal`), Orion (`operator`), Gaia (`family`) | Maintain tasks, recurrence and timed/zone reminders within existing private/shared grants; verify delivery state. |

Each assigned identity explicitly loads its skill before the workflow. Atlas
continues to delegate these domains; Gaia uses only its existing shared Family
tools without private adult sources or delegation. These bundles add no tools,
credentials, schedules, service connections or execution authority. The full
current source inventory also includes the household shopping/storage, discount,
calendar, travel and public-research skills. `browser-automation` remains a
separately qualified bundled skill, outside these managed instruction bundles.

[`openclaw_team_policy.py`](../scripts/openclaw_team_policy.py) owns the finite
managed skill allowlist. The existing
[`setup-openclaw-team.py`](../scripts/setup-openclaw-team.py) lifecycle installs
reviewed bundles into only the assigned workspaces, records their hashes in
`team-skill-ownership.json`, preserves private memory and refuses unreviewed
overwrites. Stage the selected published release's team manifest, installer,
policy dependencies and `deploy/openclaw/skills/` through the supported managed
upload path. First run the installer in the sandbox as the native config owner:

```bash
python3 scripts/setup-openclaw-team.py \
  --manifest config/openclaw-team.json \
  --skills-source deploy/openclaw/skills
```

Review the candidate, skill hashes and full role configuration against the
actual installed roster before application. The team installer reconciles the
full manifest; do not use an older roster to apply a skills-only change. For an
already qualified team, rerun the reviewed command with `--apply` and
`--expected-config-sha256 HASH`, using its dry-run `source_config_sha256`.
Preserve existing tool revocations and independent qualification gates; new
tool-upgrade flags are not needed for these instruction bundles. Apply startup
changes through the supported gateway restart, then inspect `skills info` and
`skills check` for each assigned agent and verify representative native turns,
including Gaia's shared-only boundary. Retain the installer's private backup
for rollback; restore only the reviewed affected config/identity/skill files
and ownership ledger through the managed lifecycle, preserving later edits.

This addition is source-only: installation, gateway discovery and model skill
use have not been verified for these four bundles. The older canary evidence
below does not establish their deployment. Use the newer
[tool runbook](openclaw-tools.md) and [integration status](openclaw-integrations.md)
for their respective recorded readiness; instructions alone establish neither.

## Original canary bundles and historical verification (2026-10-09)

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
