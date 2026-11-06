# Owner routine relay patch

`scripts/workflow_assistant_context.py` is a pure patch builder for the two
existing owner routines in `routine-morning-overview.json` and
`routine-tomorrow-preparation.json`. It does not contact n8n or write or
activate workflows. Its patch replaces one existing send node in place with a
fixed HTTPS call to the private routine relay. It adds no node or edge and
leaves the existing schedule, guards, connections, all other nodes and workflow
settings intact. It sets node retries off so an ambiguous relay response is not
blindly resubmitted.

The relay request contains exactly `{routine_id, scheduled_for}`. n8n supplies
the current timestamp in `Europe/Copenhagen`; the server checks it against the
existing routine window and its near-now limit, then derives the daily
occurrence key. The fixed routine bearer comes from a dedicated external n8n
HTTP Header Auth credential reference. No token or owner destination is stored
in source. The relay's standing private-DM route remains responsible for
delivery; it does not take a recipient from n8n or model input.

## Required qualification evidence

The repository contains the workflow IDs and schedule descriptions, but not a
sanitized current graph or a verified owner-only delivery edge for either
workflow. The included `automations/household-routines/n8n-workflow.ts` is a
synthetic template and its tomorrow routine has multiple adult and Mattermost
delivery branches; it is not a valid preimage for this patch. Therefore this
source cannot currently produce an applicable patch for either live routine.

Before using the builder, obtain a fresh sanitized graph out of band and verify
the owner-only single-recipient delivery edge and existing occurrence guard.
Then bind the canonical graph hash, sender node ID/name/type/hash, guard node
IDs, and reviewed owner-private edge in `owner_edge_review`. Use only the
dedicated routine header credential's non-secret `{id, name}` reference from
the n8n credential inventory. The builder refuses stale preimages, unknown or
duplicate senders, unrecognized sender types, missing guards, unbound owner
edge review, and placeholder credentials. Review the returned one-node diff
before any separate, explicitly authorized publication step.

Until that evidence exists, keep both workflows and the routine delivery gate
unmodified. This is a source-only qualification result, not a live workflow
change or a claim that either schedule is wired to the context relay.

## Pinned HTTP Request node contract

The source workflow image pin is n8n `2.46.0` in `config/automation.env.example`.
The HTTP Request node uses type version `4.5`, generic `httpHeaderAuth`, JSON
body mode, a 25-second request timeout, no redirects, and full JSON response.
That budget covers the local model and native Hermes send bounds sequentially.
These fields were checked against the official n8n MCP `get_node_types` result
for `n8n-nodes-base.httpRequest` v4.5 and the pinned source:

- [HTTP Request 4.5 parameter definition (`n8n@2.46.0`)](https://github.com/n8n-io/n8n/blob/n8n%402.46.0/packages/nodes-base/nodes/HttpRequest/V3/Description.ts)
- [HTTP Request 4.5 runtime (`n8n@2.46.0`)](https://github.com/n8n-io/n8n/blob/n8n%402.46.0/packages/nodes-base/nodes/HttpRequest/V3/HttpRequestV3.node.ts)
- [HTTP Header Auth credential (`n8n@2.46.0`)](https://github.com/n8n-io/n8n/blob/n8n%402.46.0/packages/nodes-base/credentials/HttpHeaderAuth.credentials.ts)

The body and auth fields must remain exactly those emitted by `http_parameters`;
secrets belong only in the externally provisioned credential. No n8n workflow
export, credential, live workflow update, activation, or execution is included
here.
