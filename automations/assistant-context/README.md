# Assistant source context sync

`scripts/assistant-context-sync.py` reads selected source snapshots on home-core,
assigns restart-safe local revisions, and posts the common event envelope to the
loopback context API. The API assigns principal, domain and visibility from its
separate ingest credential. Those scope values never appear in a source event.
The CLI prints counts only; it does not log source text, IDs, references or
credentials. Provider credentials remain in their existing private bridge
configuration and are not copied into this workflow.

## Current source coverage

| Source | Wired read | Coverage and deletion behavior |
| --- | --- | --- |
| Home Assistant | Existing authenticated `homecompute-openclaw-ha-metadata` projection through `homeassistant_observations.local_read` | Point-in-time metadata, selected checks and energy values only. It is not a change feed; missing entities never produce tombstones. |
| Daythread | Optional authenticated `GET /v1/integrations/assistant-context` export and finite `POST /v1/integrations/assistant-context/resolve` over pinned HTTPS, with a separate mode `0600` token | Saved diary summaries and proposed/confirmed commitments only. Cursor-pinned export is capped at 50 pages. Known IDs are checked in rotating bounded batches; explicit deletion, invalidation or window expiry produces a monotonic tombstone. Missing pages and failed resolution do not imply deletion. |
| Aula | Existing HomeCompute MCP bridge through `openclaw_services.Services`; bounded calendar pages, first-page posts and thread messages, plus configured weekly-letter pages | Calendar/letter pages are complete only when the source's `next_offset` is null, but reads are not a stable provider snapshot. Posts and thread messages are first-page only and never infer deletion. |
| Calendar | Existing `openclaw_calendar.Calendar` read route, for explicitly configured household aliases and a bounded interval of at most 31 days | Uses the route's event projection and truncation flag. A truncated page is partial. Reconciliation is limited to the exact alias and interval coverage key. |

The event journal stores source changes after receiving an authenticated receipt.
Stable content fingerprints are only used to detect equality. Revisions come
from the local SQLite ledger and increase monotonically. Cursor and coverage
state are local restart state, not provider deletion guarantees. A source must
report a complete scan for the exact same coverage key before an absent object
can produce a delete event.

Revisions are monotonic per source object across coverage keys. A rotating
window reuses the existing revision when the object content is unchanged; a
change or deletion uses a revision above every coverage already recorded for
that object. When replay encounters an older revision whose exact event content
has already been accepted at a later revision, the API acknowledges it as
superseded. A different-content conflict remains an error and blocks the
outbox for operator review.

## Private configuration

Provision `/var/lib/homecompute-assistant/context-sync.json` and
`context-ingest-token` as owner-only regular files with mode `0600`. The recommended rolling-window template is
[assistant-context-sync.example.json](../../config/assistant-context-sync.example.json);
[sanitized-config.json](snippets/sanitized-config.json) retains a fixed-window
compatibility example. The token
is issued for source ingest by the context service, which selects trusted scope
and source grants. The sync tool uses a fixed loopback URL and fixed existing
bridge configuration paths; it does not accept provider URLs or arbitrary
credential paths.

`profile_ids`, `child_ids`, institution codes, calendar aliases, date ranges and
ISO weeks are explicit operator-selected source scope. They are not discovered
or widened by the model. Keep the configuration private because these selections
can identify household records.

Calendar rolling bounds resolve from Copenhagen local midnight on every scan.
`past_days + future_days` is at most 30 local days, so autumn DST remains within
the existing provider's 31 elapsed-day limit. Explicit fixed `start`/`end` is
still supported, up to 31 elapsed days, and is exclusive with rolling mode.
Coverage reconciliation uses the exact resolved alias and interval.

Provider failures are isolated. Healthy sources still publish; failed sources
produce no absence deletions and post a source failure receipt. The API preserves
the last successful observation separately and reports the source unavailable
until a later healthy scan. CLI output contains counts and finite source failure
flags, with exit status 1 for partial failure.

## Run and limitations

Run from the exact published repository release on home-core as the existing
source-owning service/operator identity that already has access to the relevant
private bridge files. The separate context server identity never receives Aula,
Calendar or Home Assistant credentials. The collector bearer is only for the
context ingest/coverage API and does not broaden source reads:

```sh
python3 scripts/assistant-context-sync.py
```

The current CLI wires Home Assistant, Aula and Calendar through their existing
fixed HomeCompute bridge readers. Daythread is disabled by default. To enable it,
set `daythread.enabled` and `daythread.host` in the private sync config, provision
`/var/lib/homecompute-assistant/daythread-export-token` as a mode `0600` URL-safe
token file, and enable Daythread's separate export endpoint for the same chosen
profile. The host is restricted to `daythread.home.arpa` or
`daythread.<tailnet-label>.ts.net`; system TLS trust is used, redirects and
proxy settings are disabled, and response bodies are capped. Keep the service
reachable only on a trusted private network. The endpoint chooses its profile
from private Daythread deployment configuration; the client cannot select one.
No workflow export or scheduler is installed by this source-only patch. Do not
claim full Aula history, complete Calendar deltas, or deletion propagation
beyond a completed same-coverage snapshot.
