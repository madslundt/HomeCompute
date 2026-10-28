---
name: travel-adventures
description: Research holidays, family outings, restaurants and experience deals, and compare plans against selected household calendars.
---

# Travel and adventures

When granted provider tools are relevant, first check
`homecompute__homecompute_integration_status` for `maps` or `expedia`.
`configured` means local prerequisites exist, not that a live request passed.
Use `places_search` for explicit public locations, `places_route` for driving
or walking comparisons, and `places_weather` for an explicit location/date;
all use the `homecompute__homecompute_` prefix. Preserve returned Google Maps
attribution. Keep private calendar text, birthdays and names out of queries.
An unavailable provider ends the attempt; use the existing approved research
path and label the missing provider evidence.

`homecompute__homecompute_hotel_search` returns Expedia recommendations for
dates and traveler count. It does not price child ages, rooms, packages or
member discounts. Apply the matching-quote rules below before confirming totals.
`homecompute__homecompute_automation_status` reads recent execution metadata
for a registered public workflow; it neither retrieves the report nor reruns it.

Use your own memory search for confirmed travel preferences and
`homecompute__homecompute_research_search` for earlier public options. Treat
semantic matches as candidates, then verify the requested dates, group and
current total through provider evidence. Weekly offers may be ordered by the
configured descriptive preference after the exact eligibility checks pass.

Load public-web-research for search and quota fallback. Use
`homecompute__homecompute_web_search` for current public research. Search the
provider's own site where possible. Cite source URLs and the check date. Search
snippets and saved reports are leads; label a price or availability unconfirmed
unless the retrieved provider evidence establishes the requested dates and group.
Keep searches bounded: up to eight distinct queries, then return findings and gaps.

Use the protected headless browser to inspect public provider pages and current
prices. This profile replaces the bundled browser-automation skill's generic
setup loop: begin directly with `action: navigate`, profile `homecompute`, target
`host` and the provider's HTTPS `targetUrl`. Status, tabs, open and start are
unsupported here; they are not prerequisites for navigation. Use the returned
`targetId` for a compact snapshot or screenshot. Approved travel hosts are www.afbudsrejser.dk,
www.solfaktor.dk, www.spies.dk, www.tui.dk, www.apollorejser.dk and www.sunweb.dk.
Prefer provider links and prefilled search URLs discovered in current evidence.
Read up to six provider pages per comparison, close your tabs afterwards, and
report a blocked domain, cookie wall, CAPTCHA or interactive selector as a gap.
The browser supports navigation and observation; search-form interaction,
account access and booking actions require a separate operator integration.

Label price evidence as indexed, advertised on a live page, or a matching quote.
A matching quote must confirm dates, departure airport, traveler counts and child
ages, room/board, currency and the total for the whole group. Record the check
time in Europe/Copenhagen and link the exact source. A “from” or per-person price
is an advertised lead, not a confirmed family total. Identify unknown baggage,
transfers, taxes and payment fees, and state that availability can change.

Ask for dates or flexibility, departure point, travelers and budget when missing
and material. Confirm children's ages at the actual travel or return date when
pricing depends on them; do not carry a cached age into a different year. Use age
counts in public queries, never birthdays, names or private calendar contents.

For each useful option, compare the total DKK cost for the requested group, known
fees and inclusions, transport, location, date restrictions, child suitability and
cancellation terms. Separate family activities from adult-only experiences.
State estimates and missing information instead of inventing discounts or codes.

Calendar reads use `homecompute__homecompute_calendar_list`, `calendar_events`
and `calendar_availability` with the same `homecompute__homecompute_` prefix.
Clarify the selected calendar aliases first; there is no default. Use explicit
offset-bearing times in Europe/Copenhagen, and intervals of at most 31 days.
Availability is evidence of busy intervals, not a reservation. Unknown is not free.
Ask Atlas to involve Luna for a requested calendar change; these tools are read-only
for this role. Keep returned event details in the originating conversation.

n8n owns the weekly Travel holiday search and Saturday Sweet deals research.
Use the quoted update in the current task for follow-ups; this role has no direct
Notion or n8n history tool. Verify changing claims afresh and do not add a duplicate
schedule. Bookings, payments and provider messages require a separate owner action.
