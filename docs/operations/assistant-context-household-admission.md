# Assistant context household admission — 2026-11-06

This dated record supports the host declaration's household restore gate. It is
evidence at this time, not a statement that every optional assistant feature is
enabled or that later live state is synchronized.

## Qualified recovery and client path

- The normal Core release was `a05d834e34932aef7c085681a784988dbacb8d7d`.
  An attended application backup completed successfully on encrypted NAS;
  paused applications resumed and the recovery drain and hold were removed.
- The focused NAS restore ran from published source
  `9cd2f23fe2cc55f187044786ff014045ac2c53f5`. It restored all six context SQLite
  databases, the private client policy, and both inference credentials into an
  isolated mode-0700 target. Database checks passed and credentials matched
  their originals byte for byte. The read-only FUSE mount was removed before
  the scoped success receipt was written at
  `/var/lib/homecompute/nas-recovery-backup/last-assistant-context-nas-verification.json`.
- A fresh content-free deletion ledger and the empty synthetic admission proof
  were checked against the restored and live scopes. Replay, scrub, and cache
  invalidation completed; the isolated target reached READY. No household data
  had yet entered the live store. This initial empty-state proof does not claim
  provider deletion completeness for future household snapshots.
- An isolated server using the restored policy returned authenticated health
  and search successfully, rejected unauthenticated health and collector tool
  access, and shut down cleanly. The live store was never replaced with the test.
- The guarded bridge lifecycle applied published source
  `9842f59f02bb162cd0932bde64d20536ce456880`. Hermes gained exactly ten context
  tools (60 total); OpenClaw remained at 69. After an idle native MCP restart,
  the actual Hermes registry handler completed a synthetic search.
- The owner then sent a fresh nonce in the existing private Mattermost DM.
  Read-only REST checks verified the owner author and the bot's reply in the
  same thread/channel. Native session records correlated the exact query and
  limit, tool-call ID, successful empty result, and final answer. Its lexical
  degradation and unavailable coverage matched the still-synthetic bootstrap.
  This proves two-way native communication before household enablement.

Private restore paths, exact snapshot and manifest identities, deletion-ledger
hashes, and credential hashes remain in protected operational receipts outside
Git. The focused restore qualifies only assistant context; it does not replace
full application, PostgreSQL, or VM recovery tests.

## Resulting declaration and remaining gates

The host declaration admits household collection and local embeddings/briefing
inference. The owner authorized both currently discovered Aula child entries;
their identifiers remain in private source selection, outside Git. Collection
must still be verified after the normal published-release deployment, including
  provider freshness, partial coverage, local inference, and native household
  retrieval after enablement.

Attention remains in shadow mode. Automatic delivery, background delegation,
and improvement publication remain disabled. Existing multi-recipient n8n
workflows have not been changed. A private native delivery route and dedicated
repository-scoped publication credentials require their own qualified lifecycle.

Code rollback uses the prior published release through the normal deployment
wrapper. It does not undo ingested data; preserve the current deletion ledger
and use the documented admission flow before restoring any data backup.

## First household rollout and populated inference qualification

The normal Core deployment of `4aebdb3c6f212fc4d1e45185538b9b41e97b356f`
completed successfully. Its final source-owned collection passed all four
providers: 43 Home Assistant observations, 141 Aula records including 20 posts,
11 Daythread summaries and one calendar record in the selected rolling window.
Calendar coverage was fresh; the other bounded projections remained explicitly
partial. Authentication and role-denial checks passed. Native Mattermost REST
and WebSocket authentication passed again. Core matched the selected release;
Spark retained its existing independent revision. Both hosts had zero failed
units. No restore-test FUSE mounts remained.

The populated API check exposed limits that the earlier small inference probes
could not establish: cold search attempted all missing vectors in one request,
and the full briefing exhausted its generation budget. Both returned explicit
fallbacks. Frequent home observations also crowded out other briefing sources,
and citation validation treated recent partial feeds as unusable.

The follow-up uses incremental bounded cache warming, source-balanced briefings
and short model excerpts, with exact current-revision checks still enforced.
In an isolated 93-record synthetic canary on the actual inference routes,
retrieval completed without degradation in 2.93 seconds and the validated model
briefing completed in 4.01 seconds with all four sources represented. This was
a one-shot qualification process; no live service code was hand-patched. The
normal published-release lifecycle and original populated API check remain the
required deployment verification for this follow-up.

A final private snapshot of the actual household store also qualified the
384-byte human-text-first representation: cold retrieval completed without
degradation in 1.99 seconds (two of eighty semantic candidates scored, with the
remaining count exposed), and its cited model briefing validated in 4.26 seconds.
The briefing preserved partial coverage and truncation. The current-day view
included Home Assistant, Aula and Daythread; the calendar record was outside
that requested window. Snapshot copies and derived canary caches were removed
on exit; SQLite connections opened only the private copies.
