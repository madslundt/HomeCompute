---
name: google-calendar
description: Read Google Calendar events and availability, and create or update an event when the owner explicitly requests that change.
---

# Google Calendar

Use only the granted `homecompute__homecompute_calendar_*` tools. Start with
`calendar_list` to discover the authorized aliases and their actual writable
status: `mads`, `kristina`, `alvira`, `nora`, and `family`. Work calendars are
outside this interface. Always ask which calendar when the owner's request
does not identify one unambiguously; there is no personal or family default.

Interpret local times in `Europe/Copenhagen`, including daylight saving time.
Clarify missing dates, duration, or an ambiguous clock time. Use offset-bearing
timestamps for timed events. All-day start/end use dates with an exclusive end.

For a named calendar, `calendar_events` accepts either an exact `event_id` or a
bounded `start`/`end` range, with optional `query`. Read current evidence instead
of relying on memory. For scheduling, check `calendar_availability` first with
the selected comma-separated `calendars`, `start`, and `end`; clarify conflicts
before creating or moving the event. Availability does not reserve a time.

An explicit owner request authorizes its routine create or update. Clarify the
calendar, event, or requested change when ambiguous; avoid redundant approval
for a fully specified request.

- Create with `calendar_create`: `calendar`, `summary`, `start`, `end`, explicit
  `all_day`, and `request_id`; include `description` or `location` only as asked.
- Update with `calendar_update`: read the exact event first and retain its
  `event_id` and fresh `etag`, including its quotes. Supply `calendar`,
  `event_id`, `expected_etag`, `request_id`, and only requested mutable fields.
  When changing time, supply `start`, `end`, and `all_day` together. Preserve
  unspecified fields; refresh and reconcile an ETag conflict.

Use one version-4 UUID `request_id` per requested change. After an interrupted or
uncertain write, use `calendar_request_status` with the same UUID, then read the
exact event. Reuse that UUID when recovering its completed receipt; never use a
new UUID to force an uncertain write. If the receipt and read-back do not prove
the result, stop mutations and report uncertainty.

Report the actual selected calendar, local time and outcome. A confirmed receipt
and read-back establish the requested event state; acceptance alone does not.
Guest invitations, attendee notifications, deletion, recurring-series changes,
and special event writes are outside this grant. Keep private event content in
the invoking conversation; publish neither calendar details nor credentials to
other channels, search providers, or shared task memory.
