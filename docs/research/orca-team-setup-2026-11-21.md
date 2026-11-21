# Orca team setup on Home Core

Reviewed against Orca v1.4.268, the current [team manifest](../../deploy/orca/teams.json),
and the upstream documentation on 2026-11-21.

## Give each project a durable owner

Keep HomeCompute, House-battery, ChessMate, and Daythread as separate Orca Bot
Groups. Each Group has one project-owner bot as its default respondent and one
registered repository. Its purpose covers that project's roadmap and delivery.
Record durable decisions in its bot memory and keep priority in GitHub Projects.
Orca keeps a bot's memory and abilities in its own folder and gives each Group
member its own conversation. Group creation and updates require an owner using
Orca desktop or a paired client. [Orca bots quickstart](https://orca.dev/docs/bots)
· [Pinned Group RPC](https://github.com/stablyai/orca/blob/v1.4.268/src/main/runtime/rpc/methods/bot-groups.ts)
· [Pinned Group data model](https://github.com/stablyai/orca/blob/v1.4.268/src/shared/bots/bot-group.ts)

Use the committed PO and specialist briefs to keep responsibilities consistent.
Give a persistent bot a separate role only when it needs its own ongoing
memory, schedule, or human-facing inbox. For the four project teams, instantiate
Developer, Reviewer, and Designer as workers per assignment. This keeps each
project's recurring reviews and decisions with its PO, and gives the PO one
place to collect worker results. [HomeCompute PO brief](../../deploy/orca/instructions/product-owner.md)
· [Orca bots quickstart](https://orca.dev/docs/bots)
· [Orca orchestration](https://orca.dev/docs/cli/orchestration)

## Assign and review work in Orca

1. Start with one linked GitHub issue or PR. Give each worker its repository,
   exact base or commit, acceptance criteria, allowed tools, and expected
   evidence. Use a supervised Run for work that needs coordination or a
   decision gate. Load the installed version's command guide with
   `orca skills get orchestration --full`. [Orca orchestration](https://orca.dev/docs/cli/orchestration)
2. Give Developer a new worktree and use `--setup skip` for this pilot.
   Assign a single writer to each part of a change. Use Designer for UI or
   accessibility work, with a synthetic fixture and the worktree browser for
   responsive checks and screenshots. Worktree separation protects the
   checkout. The container's OS and network policy govern resource access.
   [Pinned worker options](https://github.com/stablyai/orca/blob/v1.4.268/src/cli/specs/orchestration-worker-specs.ts)
   · [Orca worktrees](https://orca.dev/docs/model/worktrees)
   · [Orca worktree browser](https://orca.dev/docs/browser/overview)
3. Ask an independent Reviewer to inspect the exact diff and verify the
   reported tests. Give the Reviewer the issue, proposed commit, and test
   evidence. Keep review read-only. Have each worker report back through its
   dispatch, including file references and failures. The PO summarizes
   unresolved findings, test results, and the branch or PR draft for the
   owner before publication or deployment. [Orca worker contract](https://orca.dev/docs/cli/orchestration#worker-contract)
   · [HomeCompute PO brief](../../deploy/orca/instructions/product-owner.md)

Use Ask for approval and keep the default POs and scheduled reviews on the
local model. Select a hosted agent for an approved task whose inputs can go to
that provider. Use the least tool access each role needs. Keep source, data, and
credentials outside the Orca runtime when they belong to another trust
boundary. UI actions, issue text, and board content can carry instructions
from third parties, so workers should treat them as data. A bot's Who can ask
list, event triggers, and webhook authorization are separate controls.
[Orca bot security](https://orca.dev/docs/bots/security)
· [Orca supported agents and approvals](https://orca.dev/docs/agents/supported)

For GitHub Projects, supply the PO with the fields it needs for triage from
its approved board. Keep the credential in a dedicated reader and preserve
an explicit status when that board is unavailable. GitHub supports
`read:project` for queries. An Orca Cloud GitHub connector has a separate
purpose and can let a bot reply to GitHub events. [GitHub Projects API authentication](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects#authentication)
· [Orca bot connectors](https://orca.dev/docs/bots/connectors)

## Use Claude Code Agent Teams for attended collaboration

Orca's native orchestration supports mixed providers, persistent project
coordination, and explicit worktrees. Claude Code Agent Teams gives an
interactive Claude lead and Claude teammates a shared task list and direct
messages. Orca v1.4.268 exposes `orca claude-teams` inside an Orca terminal
and uses its terminal split support for teammates.
[Orca supported agents](https://orca.dev/docs/agents/supported)
· [Pinned Claude Teams launcher](https://github.com/stablyai/orca/blob/v1.4.268/src/cli/specs/core.ts)
· [Anthropic Agent Teams](https://code.claude.com/docs/en/agent-teams)

Use Claude Teams for an attended review or design exploration after the owner
authorizes the Claude account and project data. Start with two distinct
read-only assignments. Give each teammate the context, bounds, and expected
result in its brief, since it starts without the lead's conversation. Use a
lead with approvals enabled. Assign nonoverlapping files before trying
parallel implementation. Enable Teams for that session only. The feature is
experimental, can change how Claude launches named subagents, and has
limitations around resumption and shutdown.
[Anthropic Teams permissions and best practices](https://code.claude.com/docs/en/agent-teams#best-practices)

For headless UI work, qualify Orca's own browser with a synthetic local
page and invoke `orca snapshot` from the assigned worktree before relying
on it for reviews. The pinned headless runtime checks both the configured
Chromium executable and its agent-browser driver. A browser tab shares the
runtime's network boundary, so qualify it on public or synthetic app data.
[Pinned browser provider](https://github.com/stablyai/orca/blob/v1.4.268/src/main/orcad/browser-providers/orcad-browser-provider.ts)
· [Orca CLI browser workflow](https://orca.dev/docs/cli/reference)
