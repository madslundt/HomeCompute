# Personal data and assistant memory contract

This document is the normative interface between ingestion, personal-event
storage, retrieval, assistant runtimes, sharing, deletion, and backup. The model
may suggest a memory operation; deterministic application code validates and
executes it.

## Security dimensions

Every authenticated caller is bound to one principal and one data domain before
request content is processed:

| Dimension | Values | Rule |
| --- | --- | --- |
| `principal_scope` | `owner`, `partner`, `family`, or a separately registered work principal | Never accepted from model output or an untrusted header |
| `data_domain` | `personal`, `household`, `work:<organization>` | A credential grants an explicit allow-list; no wildcard work domain |
| `visibility` | `private`, `shared` | Shared is an audited projection approved by the record owner |

`owner/personal` and `partner/personal` are private. `family/household` reads
only approved shared projections. `work:<organization>` uses separate accounts,
storage, retrieval indexes, sessions, tool credentials, retention, exports, and
backups. It is never readable by partner/family profiles or personal memory.

## Memory record

A retained assistant memory has at least:

```json
{
  "memory_id": "stable identifier",
  "schema_version": 1,
  "principal_scope": "owner",
  "data_domain": "personal",
  "visibility": "private",
  "kind": "preference|goal|relationship|constraint|working_context",
  "statement": "bounded user-visible memory text",
  "source_refs": ["immutable source identifiers"],
  "provenance": "explicit|inferred|imported",
  "sensitivity": "ordinary|sensitive|restricted",
  "confidence": 1.0,
  "created_at": "RFC3339 timestamp",
  "last_confirmed_at": "RFC3339 timestamp",
  "expires_at": null,
  "supersedes": null,
  "status": "active|corrected|expired|deleted"
}
```

Rules:

- Explicit user statements may be retained when they fit an allowed kind.
- Inferred ordinary memories require a visible review queue before promotion.
- Sensitive or restricted inferences are not retained without explicit user
  confirmation; health, finance, identity, intimacy, and employment judgments
  default to non-retention.
- Memory is bounded working context, not the canonical copy of email, meetings,
  documents, transactions, messages, or home telemetry.
- Every answer based on memory can expose its source/provenance to the user.

## User controls

Each person receives authenticated operations to list, inspect, correct,
confirm, expire, export, delete, and explicitly share their own records. A
correction creates a new version and supersedes the old one. Deletion creates a
tombstone, removes online content and embeddings, invalidates caches and derived
summaries, and prevents replay from re-creating the record unless the source is
newer than the tombstone.

Backups are immutable for their documented retention period, so deletion means
removal from live systems immediately and expiry from backups on schedule. A
restore must replay tombstones before serving retrieval.

## Administration and consent

The household must choose and record one of these threat models before partner
data is admitted:

1. **Trusted household operator:** the infrastructure administrator can
   technically access host filesystems and backups, with access logging and a social
   agreement providing governance.
2. **User-held confidentiality:** partner-private content is application-level
   encrypted with keys unavailable to the infrastructure administrator. This
   requires a separately designed key-recovery and server-side-search model.

Separate sandboxes alone provide neither undisclosed administrator
confidentiality nor protection from the host administrator.

## Work admission gate

Before creating `work:<organization>`:

- record employer policy/approval and permitted data classes;
- define approved devices, transports, model providers, storage region,
  retention, deletion, incident reporting, and backup rules;
- use separate gateway, event-store, search, tool, and assistant credentials;
- disable personal/family retrieval and proactive notifications;
- pass cross-domain read, search, cache, log, backup, and notification denials.

If approval is absent or unclear, work data remains on employer-approved
systems and is not imported into HomeCompute.
