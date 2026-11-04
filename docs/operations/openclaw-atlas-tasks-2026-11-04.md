# Atlas task phase execution — 2026-11-04

Status: source implementation and qualification in progress; production apply
has not yet been established by this receipt. Runtime stays on 2026.9.1.

The scope and guarded lifecycle are in [the runbook](openclaw-atlas-tasks.md).
Source changes add Atlas task ownership instructions, native status/cancellation
procedures, specialist return evidence, corrected private reminder defaults,
explicit night-time reminders and opt-in expiring unfinished-task follow-ups.
Existing n8n schedules remain unchanged. General recurring agent execution and
arbitrary source monitoring are not activated by this change.

Initial live readback confirmed twelve native roles, one concurrent child,
depth one, 300-second default child timeout, disabled cron and ambient heartbeat,
and active supervisor. Both existing reminder timers were active with persistent
links under the repository-owned attached `timers.target.wants`, despite
`systemctl list-unit-files` reporting their attached units as disabled. Existing
owner and Family fixed destinations are configured. The three modified installed
Core modules match source at `12925c9`; the new follow-up helper was absent.

n8n MCP inspected the active published schedules without executing them:
morning kitchen checks each minute 05:00–11:59, tomorrow preparation at 20:30,
20:45, 21:00 and 21:15 with existing occurrence guards, news daily 10:00 and
travel Monday 09:00. All use Europe/Copenhagen and published versions matched
current drafts. This is scheduler configuration evidence, not proof of every
future delivery or Atlas's own n8n credential readiness.

Focused synthetic tests passed for the existing task/reminder store, new watch
occurrences, location reminder compatibility, skill ownership and rollback,
team policy and scoped rollout fences. Full repository validation and live
rollout results will be appended after completion; no real user task or channel
message was submitted by these synthetic tests.

Managed identity comparison against the prior published source found eight exact
matches. Engineer, operator, shopper and Family blocks were missing already
tracked integration/skill-loading sentences; their existing text added no extra
authority. The scoped identity refresh reconciles those omissions and preserves
text outside the managed markers. Family keeps its separate nondelegating
boundary and disabled ingress.

The first full validation attempt exposed a flaky existing UDP/TCP DNS test:
its child could not claim the same randomly chosen port for both protocols.
An immediate focused rerun passed both cases. The watch test was also corrected
to find its exact task ID instead of depending on UUID-dependent list order.
A full clean rerun is underway; neither failure was treated as a passing gate.

The complete repository validator subsequently passed, including containerized
Nix flake checks and optional host extensions. Focused follow-up, task store,
location, workflow, team and deployment suites passed on the final source;
local capability-dependent skips remain identified in the validation log.
`git diff --check`, the generated routine-index check and runbook local links
passed. The native handoff verifier is prepared but has not yet run against
the deployed instructions.


Core apply from `4e935e65175987eac0deefec7fe08db5647a6ce6` completed with
both empty task stores and all task/request/event counts preserved, the additive
purpose column present, four installed modules matching source, active adapter,
location service and both reminder timers, and zero failed Core units. Both
fixed destinations authenticated with `--check`, sending no messages. Its private
backup is `/var/lib/homecompute-atlas-tasks/backup-a8117862f4ce40c48f58109f228271b6`.

The initial guest apply stopped before any native mutation while collecting
before images: host UID 1000 differs from the retained image's native sandbox
UID/GID 998, whose state is mode 0700. Native config, identities and skills were
unchanged; the supervisor remained active, and no transaction mutation stage was
entered. Source now explicitly verifies 998:998 and stages/runs file operations
under that native principal. A regression distinguishes it from host UID 1000.
The preserved incomplete operator backup is evidence only, not an applied change.


The corrected-UID attempt reached the native dry-run, which refused because
Docker retained `HOME=/root` for UID 998. Read-only diagnosis proved the same
native dry-run passes with `HOME=/sandbox`. No config or scoped files changed;
the failed `prepared` transaction is retained at backup
`atlas-tasks-backup-1dd899775a7f49d391b72aa214c0a872`. Source now sets the native
home explicitly, retains per-operation failure diagnostics, and provides a
hash-bound reconciliation path that requires complete before-image equality
before allowing another apply. It never clears a mutated or unknown checkpoint.

The actual authenticated Core MCP path advertised all sixty-nine tools and both
follow-up fields on all four owner/shared create/update definitions. A single
synthetic private owner task was created with a future watch, read back, replayed
with the same UUID without duplication, then cancelled before dispatch. Saved
follow-up and ordinary reminder states were distinct; cancellation disabled the
watch. No notification was sent. Its private RPC evidence is
`/var/lib/homecompute-atlas-tasks/qualification/synthetic-mcp-task.json`; the
cancelled synthetic record remains ordinary durable qualification history.


Guest task apply completed at approximately 08:14 Copenhagen from
`5bf5adad7868487c23621477cb3394d39ae45cc1`, with unchanged runtime version and
authority apart from the reviewed Atlas skill assignment. All twelve roles,
required plugin/account health and sixty-nine authenticated MCP tools passed.
Its private backup is `atlas-tasks-backup-6d7fd3952e85440e82013dcc1bc0a137`.

The first direct-Docker handoff probe never reached the gateway: the local
client selected dashboard port 18791, which is not the managed internal route.
The subsequent managed native invocation ran successfully: Atlas read its skill,
spawned exactly one isolated Nova run with a 300-second cap, yielded once with an
acknowledgment, and received Nova's correct 13 + 29 = 42 result. No other child
tools or effects occurred. However, the parent continuation produced no answer:
it ended with `length`, one output token, and about 29,230 input/cache tokens.
This run is retained as failed final-answer acceptance; no success claim was
made from the CLI's `status=ok`.

A fresh live path check joined Core's `automation-moe` mapping to SSH forward
`172.28.200.1:18005` → Spark loopback 18400. The admitted `/v1/models` response
advertises context 262,144 for `automation-qualification`; the running
`qwen38-flash-ultrafast` vLLM process declares that same capacity. The selected
bounded correction is a 65,536-token OpenClaw allowance, retaining the model,
4,096 output-token cap and reasoning settings. Its separate hash-bound context
lifecycle and rollback are source-owned; real final-answer qualification remains
pending until the corrected context is applied and tested.

The context-change source passed its complete repository validation, including
containerized Nix evaluation, plus focused context projection/env fencing and
unmodified-preparation reconciliation regressions. Its live apply and real
English/Danish final-answer results remain pending at this publication point.
