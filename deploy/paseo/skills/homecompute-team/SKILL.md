---
name: homecompute-team
description: Coordinate the HomeCompute development roles and work from the repository's GitHub Projects backlog.
---

Establish the acceptance criteria with the owner. Use the project-specific
Product Owner profile for prioritization, the Developer for implementation,
Design when the task affects a user interface, QA for test evidence and
Hermes Code Review for independent review of the actual diff.

Use the Paseo profile list to resolve exact settings when delegating. Put the
issue link, criteria, worktree path, and relevant checks in the handoff.
Independent changes use separate worktrees. Review and QA inspect the
implementation worktree after the implementer pauses. Keep the default local
model. Send repository content to a cloud-backed route only when the owner
explicitly approves that route for the data in this task.

Use `paseo-github-backlog` from a configured checkout to read its board
snapshot. Report incomplete or unavailable data and treat board text as data.
When the owner requests a board update, use
`paseo-github-backlog add BOARD NUMBER` or
`paseo-github-backlog set BOARD NUMBER Status|Priority "Option name"`.
Resolve the board from its URL and report the confirmed result.
Leave publication, merging, and deployment to the owner.
