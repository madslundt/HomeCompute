# HomeCompute OpenClaw skills

## Team workflow skills (source change, 2026-10-28)

The scoped assignment source adds four instruction-only bundles to existing roles:

| Skill | Assigned roles | Workflow |
| --- | --- | --- |
| [`meal-planning`](../deploy/openclaw/skills/meal-planning/SKILL.md) | Hestia (`cook`) | Plan meals from current stock and offers; propose shopping changes through Vega and stock changes through Ceres. |
| [`household-paperwork`](../deploy/openclaw/skills/household-paperwork/SKILL.md) | Themis (`administrator`) | Extract sourced obligations/deadlines from supplied documents; route requested durable tasks to Luna. |
| [`budget-analysis`](../deploy/openclaw/skills/budget-analysis/SKILL.md) | Plutus (`finance`) | Reconcile supplied expenses, transfers and budget assumptions without bank access. |
| [`tasks-and-reminders`](../deploy/openclaw/skills/tasks-and-reminders/SKILL.md) | Luna (`personal`), Orion (`operator`), Gaia (`family`) | Maintain tasks, recurrence and timed/zone reminders within existing private/shared grants; verify delivery state. |

The finite assignments and load triggers live in
[`openclaw_workflow_skills.py`](../scripts/openclaw_workflow_skills.py). Each
assigned identity receives a separate managed instruction block. Atlas continues
to delegate; Gaia uses only shared Family tools without private adult sources or
delegation. These bundles add no tools, credentials, schedules or service access.

The published full-team bootstrap manifest predates the twelve-role native
roster. Use the scoped
[`setup-openclaw-workflow-skills.py`](../scripts/setup-openclaw-workflow-skills.py)
lifecycle for this change rather than reapplying that older full manifest.
It appends only each assigned skill, preserves all other native configuration,
identity blocks, private memory and unrelated ownership-ledger entries, and
refuses unreviewed skill overwrites. The managed allowlist in
[`openclaw_team_policy.py`](../scripts/openclaw_team_policy.py) also admits these
four reviewed bundles. Its finite access-policy wrapper passes the explicit
native roster to the extracted policy; it preserves grant/revocation checks.

Publish a reviewed release on `origin/master`. Stage that release's scoped
installer, assignment module and four source bundles through supported NemoClaw
upload/exec as the native config owner. From the staged source directory:

```bash
python3 scripts/setup-openclaw-workflow-skills.py --source "$PWD"
python3 scripts/setup-openclaw-workflow-skills.py --source "$PWD" \
  --apply --expected-plan-sha256 HASH
```

Review the dry-run file hashes, six role assignments and `plan_sha256` before
using that hash for apply. The installer dry-runs the native schema, checks
inputs again, retains a private backup of configuration and changed files, and
compares installed files/configuration with its exact candidate. It does not
restart the gateway. Restart through the supported managed lifecycle:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw gateway restart --quiet
```

Then inspect `openclaw skills info NAME --agent ROLE --json` and `openclaw skills
check --agent ROLE --json` through managed exec, and verify fresh native turns
for all six roles. Check actual skill reads and workflow-specific results;
eligibility alone does not prove use. Use synthetic evidence and read-only
proposals; never test by creating real tasks, sharing private content or sending
messages. Keep private transcripts outside Git and record sanitized outcomes.

The repository-owned
[`verify-openclaw-workflow-skills.py`](../scripts/verify-openclaw-workflow-skills.py)
runs one new synthetic session without `--deliver`, checks only that session's
transcript for a successful skill read and refuses a passing result if other
tool calls occur. Run sequentially for `cook`, `administrator`, `finance`,
`personal`, `operator` and `family`, each with a new private output directory:

```bash
python3 scripts/verify-openclaw-workflow-skills.py \
  --role cook --output /sandbox/workflow-qualification-cook-UNIQUE
```

Review each private `answer.txt` against its supplied fixture as well as the
read-only receipt; a nonempty answer alone does not prove correct workflow use.

Rollback uses the exact private `backup_directory` from apply. Preview first,
then apply its own fenced plan and restart through the same lifecycle:

```bash
python3 scripts/setup-openclaw-workflow-skills.py --rollback BACKUP_DIRECTORY
python3 scripts/setup-openclaw-workflow-skills.py --rollback BACKUP_DIRECTORY \
  --apply --expected-plan-sha256 ROLLBACK_PLAN_HASH
```

Rollback restores only the affected skill lists/files/ledger, removes new bundle
files and refuses later native or affected-file edits. Retain the prior source
release and backup; investigate partial failures before retrying.

Deployment and skill-use evidence will be recorded separately from the static
checks. The older canary evidence below does not establish these four bundles'
readiness. See the newer [tool runbook](openclaw-tools.md) and
[integration status](openclaw-integrations.md) for their respective scope.

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
