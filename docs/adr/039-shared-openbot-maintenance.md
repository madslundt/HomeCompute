# ADR-039: Use the primary OpenBot server for infrastructure maintenance

## Status

Accepted by the owner. Supersedes the separate OpenBot instance in ADR-037 and
ADR-038. The finite maintenance tools and approval policy in ADR-038 continue.

## Decision

Serve infrastructure maintenance on the existing OpenBot team server in the
agents VM. Its MCP connections are server-wide, so all of its agents can request
the bounded Home Core and Home Spark health reads, Git upgrade proposals, and
registered restart plans. Keep a separate maintenance key outside the general
harness catalog. Limit the delegated principal to the compute host tool set,
and independently require exact root approval for execution. Persistent changes
go through the owning Git deployment or vendor lifecycle.

Retire the duplicate OpenBot server by switching to a single-service guest
bundle at the current team image. Preserve the team data mount, verify it
through the client, and revoke the duplicate's key before purging its local
state. Retain the encrypted VM backup under its normal retention policy.

OpenBot upgrades are digest-pinned guest releases. The operator reviews the
published revision and starts the image-only upgrade from the workstation.
The guest's systemd job is independent from the running containers and leaves
an outcome in the journal. Require a verified recent backup before replacing
the image. Restore the pre-upgrade data when a migration prevents safe image
rollback.

See the [OpenBot runbook](../../deploy/openbot/README.md).
