# ADR-012: Extend Meeting Assistant for Plaud processing

## Context

The handoff proposes a new speech/meeting service, worker, queue, database, and
artifact model. The existing `meeting-assistant` application already owns live
capture, local/network transcription selection, summaries, meeting lifecycle,
and a crash-durable meeting library. Recreating those capabilities would split
the meeting domain and its source of truth.

## Decision

Keep Meeting Assistant as the meeting-domain owner. First connect its existing
LLM account and optional transcription backend to the shared `ai.home` API.
Add Plaud import, original-audio retention, raw/speaker-attributed/cleaned
transcript layers, diarization, and the extended structured summary to Meeting
Assistant in a later separately reviewed workstream.

Run only speech and diarization inference on GB10. Keep imported audio,
transcripts, meeting metadata, retries, and user-visible state in the Meeting
Assistant library or another explicitly selected durable store. Do not add
Redis, PostgreSQL, MinIO, or a generic meeting worker until measurements prove
the app's current orchestration is insufficient.

Use a documented Plaud export or manual watched-folder import first. An
undocumented Plaud API, credential scraper, or automated cloud-account access
is outside the accepted design.

## Alternatives

- New generic meeting service: rejected as duplicate domain logic and storage.
- Put the meeting library on GB10: rejected because the appliance is
  rebuildable and should not own irreplaceable audio/transcripts.
- Send recordings to cloud after local failure: rejected by the local-only
  privacy boundary.

## Consequences

Meeting Assistant must gain provenance and immutable artifact relationships
without overwriting its raw transcript. Its current source labels
(`Microphone`/`System Audio`) remain distinct from diarized speaker labels.
Imported batch recordings and live meetings may share storage and summary
contracts while retaining different capture/transcription paths.

## Status

Proposed; Meeting Assistant working-tree reconciliation and Plaud ingestion
contract pending.

## Evidence

- `docs/current-state.md`
- `docs/requirements.md` meeting requirements
- Existing `meeting-assistant/CONTEXT.md` and meeting-library implementation
