# ADR-015: Personal memory and work data use explicit security domains

## Context

The platform serves private household use and may also process employer source
code, meetings, research, calendar data, and documents. The existing
`owner_scope` rule separates household principals, but it does not distinguish
personal data from employer-controlled data. A personal assistant that learns
preferences also needs correction, deletion, provenance, and sharing semantics;
model-curated memory alone is not a durable authorization or privacy model.

## Decision

Every canonical record, retrieval credential, tool credential, session, and
derived artifact is bound below the model to both:

1. a household principal: `owner`, `partner`, or `family`; and
2. a data domain: `personal`, `household`, or `work:<organization>`.

Personal and work domains never share retrieval indexes, credentials, raw
artifacts, assistant memory, chat history, or backup sets by default. Enabling a
work domain requires recorded employer authorization, retention rules, approved
storage/transport providers, and a separate agent security principal. Work data
is not copied into owner-personal memory or a family projection.

Personal memory uses the versioned contract in
[`personal-data-and-memory.md`](../personal-data-and-memory.md). Users can
inspect, correct, expire, export, and delete memory. Inferred sensitive traits
are not retained without explicit confirmation. Sharing creates an audited
shared projection; it never follows merely because a model considers something
useful to the household.

The operator threat model is explicit. VM/sandbox separation protects against
accidental and agent-mediated crossover, but a host/root operator can access
application-host data and backups unless separately user-held encryption is added.
Partner onboarding must disclose that fact and record the accepted model.

## Consequences

- A work-capable assistant is another security principal, not a mode selected
  inside the owner-personal prompt.
- Deletion must propagate to derived summaries, embeddings, caches, and online
  indexes; backup expiry follows a documented bounded schedule.
- Local inference does not make Discord, Plaud, Notion, search providers, or
  other transports local. Each external copy remains in the data map.
- Cross-domain tests become mandatory before work or partner data is enabled.

## Status

Accepted as the design baseline; no private work or partner data is admitted
until the corresponding verification gates pass.
