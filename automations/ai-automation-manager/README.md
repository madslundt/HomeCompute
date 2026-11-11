# HomeCompute AI automation manager

This is a parallel, source-owned replacement for the Notion database
and the active `Notion AI automations` workflow. It provides a private site for
creating, editing, archiving, and deleting automations, plus a separate data
store and a dedicated n8n workflow integration. The existing Notion database and
workflow remain unchanged. Do not copy current Notion items into this service;
transitioning data is a separate operator-approved step.

The website source lives in
[`deploy/ai-automation-manager`](../../deploy/ai-automation-manager). The
initial design concept is [here](../../docs/images/ai-automation-manager-concept.png),
and the implemented dashboard with synthetic sample items is [here](../../docs/images/ai-automation-manager-preview.png).

## Fields

| Site field | Existing Notion field / purpose |
| --- | --- |
| Name | Name |
| Task description | AI prompt |
| Working context | Notion page Markdown body; updated after a successful run |
| Success criteria | Success criteria |
| Status | Draft / Ready / Resolved / Archived |
| Run days | Days to run (one or more weekdays) |
| Schedule | Morning 07:00, Evening 21:00, or hourly at minute 00; Europe/Copenhagen |
| Project / topic | Editable text in place of the Project relation |
| Tag | Editable text in place of the Tag relation |
| Recipients | Active Home Assistant Inbox user IDs with their current names |
| Update style | New field: fresh briefing / changes since last run / current-status check |
| Notify me | New field: every run / when content changes / when action is needed |
| Completion rule | New field: success criteria / manual archive |
| Last run / run history | Replaces AI last run and exposes delivery outcomes |

The data model and definitions are in [`CONTEXT.md`](CONTEXT.md).

## Deployment target

The internal URL is `https://automations.home.arpa`. The
Compose service joins the existing control-plane clients network and the
automation bridge at `172.28.201.5`; the firewall allows that address to reach
only the Home Assistant HTTP Inbox webhook. It has no published container port.
SQLite state is stored in `/srv/state/automation/ai-automation-manager`.
The Home LAN DNS A record points `automations.home.arpa` to `192.168.30.122`;
its source declaration is `config/ai-automation-manager-dns.json` and it must
be applied in UniFi DNS before clients can resolve the URL.

The source enables the service for Home Core. Authentication and workflow token
files are encrypted in `secrets/ai-automation-manager.sops.yaml` and materialized
only by sops-nix into the restricted `homecompute-secrets` group:

- `/run/secrets/automation/ai-automation-workflow-token` — random bearer token
  for n8n to call the site's private workflow API.
- `/run/secrets/automation/ai-automation-username` and
  `/run/secrets/automation/ai-automation-password` — site sign-in credentials.
- `/run/secrets/automation/inbox-webhook-env` — existing SOPS-managed webhook
  environment file.

Do not expose the site outside the approved private LAN/Tailscale entry points.

## n8n integration and delivery

The site exposes bearer-authenticated, internal endpoints for schedule claims,
run results, pending delivery recovery, delivery receipts, and failure records.
The n8n workflow must be a separate workflow from `Notion AI automations`, use
the HomeCompute local model and the current research tool credential, and call
the Home Assistant Inbox webhook directly. Recipient IDs are revalidated by the
Inbox when each notification is ingested. Use the run UUID as `external_id` for
Inbox idempotency and set `source` to `homecompute-ai-automations`.

The source workflow definition is
[`workflow.ts`](workflow.ts). It was validated with the connected n8n workflow
SDK and remains separate from the existing active Notion workflow. Its credential
references are placeholders for the local model, Tavily, and the site's bearer
token; bind these in a reviewed n8n draft before a future activation.

Schedule claiming is idempotent. Time-slot runs deduplicate per local date and
slot; hourly runs deduplicate per Copenhagen local hour **including UTC offset**
so the repeated autumn DST hour remains distinct. Pending notifications can be
retried without re-running the AI step. Daily schedule windows remain 07:00 and
21:00, Europe/Copenhagen, matching the existing workflow.

Only Home Assistant changes in the companion HA-automations checkout add the
read-only `action: recipients` webhook action. It returns the minimal user IDs
and names for active non-system users with configured Inbox delivery mappings.
Its default `ingest` behavior stays unchanged.

## Checks and rollout state

- Frontend: `npm ci && npm run build` in `deploy/ai-automation-manager`.
- Backend syntax: `python3 -m py_compile deploy/ai-automation-manager/backend/app.py`.
- HA recipient mapping: run `python3 tests/hygge-inbox-recipients.test.py` from the companion HA-automations checkout.
- Repository validation: `./scripts/validate-repository.sh`.

The companion Home Assistant recipient action and the separate n8n workflow
must also be installed before scheduled automation runs can complete. Use their
documented Git add-on and n8n MCP lifecycles; do not modify the existing Notion
workflow.
