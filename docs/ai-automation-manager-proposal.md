# Notion AI automations cutover proposal

## Goal

Complete the migration from the Notion database UI and active **Notion AI
automations** workflow to the already deployed HomeCompute site and a separately
staged n8n workflow. Keep the existing Notion workflow available until the
replacement passes qualification. This proposal does not authorize a live n8n
publication or migration of personal Notion records.

## Existing behavior observed

The active n8n workflow has 29 nodes. It runs at
07:00 and 21:00 in Europe/Copenhagen. It selects Ready items for the current
weekday, filters by Morning/Evening and skips an item already run on the current
Copenhagen date. It considers up to 10 database rows, prioritizing the oldest
AI last run. For each selected item, it loads the item's Markdown context,
retrieves same-item research, runs the local `automation-moe` model with the
Tavily search tool, replaces the context, records the run date and a summary,
and resolves only when non-empty success criteria are clearly satisfied.
One existing task has a special Home Assistant Inbox branch; that branch does
not establish the general recipient or message contract for other items.

## Record model

| Existing Notion field | Proposed site field | Notes |
| --- | --- | --- |
| Name | Name | Required title |
| AI prompt | Task description | Required instructions for the task |
| Page Markdown | Current context | Markdown replaced with each run result |
| Success criteria | Success criteria | Optional text; required before Resolved |
| Status | Status | Draft, Ready, Resolved, Archived |
| Days to run | Days to run | Monday through Sunday, one or more |
| Time to run | Time to run | Morning (07:00) or Evening (21:00) |
| AI last run | Last run | Timestamp in Europe/Copenhagen |
| Project | Project | Editable text/select label, replacing the Notion relation |
| Tag | Tag | Editable text/select label, replacing the Notion relation |
| Created / Created by / Updated | Audit metadata | Created/updated times are system-managed; creator comes from the signed-in site identity |
| Notion comments | Run history | Timestamp, outcome, and change summary; no prompt/response content in operational logs |
| — | Recipients | Required selection of one or more current Home Assistant people |

The current-context text and run-history entries are household data. They stay
in durable application state, outside Git. Archive remains distinct from
permanent deletion; deletion behavior and whether history is purged need to be
specified in the implementation issue.

## Proposed boundaries

- A private site provides create, read, update, archive, and delete operations.
  Browser writes use authenticated same-origin requests with CSRF protection.
- A small backend owns the records and run history in durable local storage.
  The browser never talks to Home Assistant or n8n with service credentials.
- The recipient picker obtains only person entity IDs and display names from
  Home Assistant. The backend discards every unrelated entity field. A selected
  recipient that is absent from the latest Home Assistant response blocks
  delivery.
- n8n retains the two existing schedule times and the bounded local model and
  research integrations. A staged copy replaces Notion read/write nodes with
  authenticated site API calls; it remains unpublished until qualified.
- Run summaries are sent through the existing Home Assistant Inbox integration.
  The existing Sweet deals workflow shows an `action: ingest` payload with
  `title`, `body`, `category`, `source`, `recipients`, `priority`,
  `send_push`, and `external_id`. Before generalizing it, confirm that the
  receiver accepts arbitrary selected person recipients and a task/result
  category, and define idempotency and retry behavior.
- Home Assistant remains the delivery authority. This site cannot choose an
  arbitrary HA action or service.

## Security and operational gates

- Site ingress stays within the existing approved LAN/tailnet boundary. The
  site has separate authentication and does not share n8n's login.
- HA credentials, webhook IDs, site credentials, and real item contents remain
  outside Git. The backend returns only the person-picker fields and never logs
  prompts, context, criteria, results, or HA state payloads.
- Persisted records require an application-consistent backup and restore test.
  A source release rollback must preserve the database and must not silently
  roll back user records.
- Qualification covers CRUD, recurrence, same-day deduplication, stale
  recipients, delivery receipts, retry uncertainty, success-criteria handling,
  backup/restore, and rollback. The original Notion workflow stays active until
  an explicit cutover decision.

## Open decisions for implementation

1. Confirm whether existing Notion automation items and their current Markdown
   contexts should be migrated. No item rows were read or copied for this
   proposal.
2. Confirm the Home Assistant Inbox receiver's supported recipient identity
   and category contract before building the delivery adapter.
3. Decide whether a manual “run now” action is in the first release or only the
   existing scheduled runs are supported.
4. Define permanent deletion: purge the item and run history, or retain a
   recoverable tombstone.

## Repository process

`CONTRIBUTING.md` requires a GitHub issue before a large architecture change or
new runtime. An issue was attempted through the connected GitHub integration,
but the API returned 403 (`Resource not accessible by integration`). No site,
workflow copy, deployment, or live Notion migration was created in this change.
