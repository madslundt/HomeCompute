# ADR-043: Share opted-in Nod schedule results

Status: Accepted for source implementation. Schedule sharing requires an
owner-selected allowlist.

Date: 2026-11-20

## Decision

Add `nod_schedule_result` to the OpenBot delegate on the existing private
Nod MCP route. A private guest configuration maps aliases to approved
schedule UUIDs. With an empty configuration the lookup shares zero
schedules. Hermes clients using the owner OpenBot connection see the same
allowlist. The independent Hermes delegate retains its two task tools.

Read Nod's existing schedule and run records. For a requested alias, return
the latest run's status and timestamps alongside the last saved successful
text result and its age. Bound the result text to 4,000 characters. Restrict
the lookup to the Nod owner and active conversation. Approval messages,
hidden messages, and rich results stay in Nod. The lookup provides no
schedule mutation, refresh, or enqueue operation.

See [configuration and verification](../../deploy/nod/README.md#share-a-saved-schedule-result).
