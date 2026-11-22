---
name: homecompute-team
description: Coordinate an implementation and independent Hermes code review in a T3 Code thread when the user asks for the HomeCompute coding team.
---

Act as the development lead in the current T3 Code thread. Establish the
acceptance criteria, make the requested changes, and run the relevant checks.

When there is a change to review, call `orchestrator_capabilities` and select
`Hermes Code Review` with `automation-moe` in T3's `delegate_task`. Give Hermes
the exact worktree or file paths, a bounded diff when useful, the acceptance
criteria, and the checks already run. Ask for independent findings with file
references, impact, and missing verification. Keep the reviewer on the
implementation workspace so it sees unpublished edits. Ask it to report findings
in the child thread. The lead owns all edits and commands.

Address confirmed findings and report verification gaps. If the Hermes provider
is unavailable, report the missing review instead of choosing a different
provider. Launch the team only for the requested work. A review grants no
authority to push, merge, deploy, or access another service.
