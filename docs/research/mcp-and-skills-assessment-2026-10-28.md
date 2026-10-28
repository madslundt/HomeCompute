# MCP and skills assessment — 2026-10-28

Status: research and recommendations only. No integration was installed, no
credentials were provisioned, and no live deployment was inspected in this
assessment. Current upstream documentation describes candidates; compatibility
with HomeCompute's installed client and service versions remains unverified.

Implementation follow-up: the selected tools, source catalog and finite role
grants are now implemented in [specialist integrations](../openclaw-integrations.md).
That runbook records qualification and rollout status; the findings below
remain the assessment that informed the selection.

Replacement follow-up: the owner selected an alternative to Google Maps.
The draft implementation now uses Geoapify plus Open-Meteo with the same
finite agent grants; the Google candidate below is retained as assessment
history, not the selected deployment.

Hotel replacement follow-up: the owner has no Expedia partnership. The draft
now uses self-service [SerpApi Google Hotels](https://serpapi.com/google-hotels-api)
for bounded hotel discovery, with explicit adults, child ages, currency and
separate nightly/stay tax labels. [Pricing](https://serpapi.com/pricing) currently
lists 250 free searches/month; no paid plan is needed for initial qualification.
This is Google Hotels data through SerpApi, not a Google Maps API dependency.
The former Expedia candidate below remains historical; its endpoint and key
are no longer used. SerpApi credentials and live behavior remain unverified.

## Recommendation

HomeCompute can support additional MCP-backed capabilities through its existing
trusted adapter, finite tool schemas and role grants. Prioritize Context7 for
Apollo and small repository-owned skills for the newer household roles. Evaluate
Google Maps Grounding Lite for Lyra after reviewing retention/attribution terms.
Use GitHub CLI (`gh`) in the authorized coding environment for repository,
issue and PR operations. No GitHub MCP addition is recommended without a
concrete access gap that the existing coding workflow cannot meet.

MCP provides tool access; a skill teaches a workflow. Neither grants a role new
authority merely by being installed. Keep credentials and provider selection
below the model, and use the existing source-first deployment lifecycle.

## Hotels listing

**Do not adopt the linked server as-is.** The
[marketplace listing](https://mcpmarket.com/server/hotels) matches
[`corneyc/hotels-mcp`](https://github.com/corneyc/hotels-mcp). GitHub's API
reported HEAD `cf10f9b82246bfddb40a33515f79816d3e601b3d`, committed on
2025-08-27, when checked for this assessment.

The pinned [request handler](https://github.com/corneyc/hotels-mcp/blob/cf10f9b82246bfddb40a33515f79816d3e601b3d/src/index.ts)
immediately returns `OK` for `/search`, making its later RapidAPI branch
unreachable. That branch only resolves destinations; it does not implement
dated hotel availability, room rates or booking. The handler has no MCP
initialization, tool discovery or tool-call handling. Its
[package manifest](https://github.com/corneyc/hotels-mcp/blob/cf10f9b82246bfddb40a33515f79816d3e601b3d/package.json)
also contains no MCP SDK dependency. The
[tests](https://github.com/corneyc/hotels-mcp/blob/cf10f9b82246bfddb40a33515f79816d3e601b3d/test/index.spec.ts)
cover greeting, random ID and unknown-path responses, not hotel search or MCP.
These are source findings; no upstream package was installed or executed.

Supporting the desired hotel-search capability is feasible, but would require a
qualified provider API and a real finite adapter. A RapidAPI subscription is
separate from the owner's Hotels.com account. Preserve the existing guarded
account path for member/account evidence; neither this listing nor Expedia
recommendations establish member prices or booking authority.

## Existing coverage to reuse

These are source capabilities, not fresh claims about deployed readiness:

| Capability | Existing source and implication |
| --- | --- |
| Public research | [`openclaw_services.py`](../../scripts/openclaw_services.py) provides Tavily search and archived public evidence. [`public-web-research`](../../deploy/openclaw/skills/public-web-research/SKILL.md) handles the protected browser fallback. Another general search MCP would mostly duplicate this. |
| School and shopping | The same facade already wraps Aula and Tilbudstrolden. Keep their finite projections rather than exposing the upstream server's entire tool inventory. |
| Calendar and household | [`openclaw-team.json`](../../config/openclaw-team.json) declares role-specific Google Calendar, task, shopping and stock use. Additional generic calendar/task MCPs would create overlapping authorities. |
| Travel | [`travel-adventures`](../../deploy/openclaw/skills/travel-adventures/SKILL.md) already defines quote evidence, child ages, full-group totals, selected calendar reads and n8n schedule ownership. [`openclaw-tools.md`](../openclaw-tools.md) describes separate guarded account access; authenticated Hotels.com search remains unverified there. |
| Home control | [`openclaw_capabilities.py`](../../scripts/openclaw_capabilities.py) uses a fixed local proposal/status/cancel path; execution belongs to the separate operator/HA boundary. Do not add another device-control path casually. |
| Workflow execution | [`automations/README.md`](../../automations/README.md) already assigns travel, bargains, Aula and other schedules to n8n. New chat tools should retrieve results or invoke deliberately selected workflows, not duplicate schedules. |

The working tree contains substantial pre-existing changes, including account
access, mail, household routines and site workers. This note leaves those files
untouched. Mail remains explicitly disabled in the current role declarations;
the presence of setup code does not prove an authorized live mailbox connection.

## Ranked additions and conditional candidates

| Priority | Candidate | Benefit and owner | Recommended scope and limitations |
| --- | --- | --- | --- |
| 1 | **Context7 MCP; CLI + skill for developer harnesses** | Apollo: current, version-specific library/API documentation during implementation. | Prefer a finite MCP facade for OpenClaw, whose role policy denies general shell execution. The CLI/skill alternative fits an authorized developer harness; choose one mode there. Public documentation queries only initially; never send private source or personal evidence as query context. Local execution still calls Context7's hosted backend. Check relevant library coverage before rollout. [Official source](https://github.com/upstash/context7). |
| 2 | **Google Maps Grounding Lite MCP, conditional** | Lyra: restaurants, outings, nearby places, route distances and weather. | Official hosted MCP with API key/OAuth and usage billing. Send only the location/query needed. Provides driving/walking estimates, not live traffic/navigation or hotel rates. Its content retention and attribution policies require review against HomeCompute's research archive and chat persistence before activation. [Official documentation and policies](https://developers.google.com/maps/ai/grounding-lite). |
| 3 | **Finite n8n workflow tools** | Nova/Lyra: retrieve a previous report with source/capture time; Orion: request bounded automation diagnostics. | Prefer existing facades or a selected workflow's MCP Server Trigger. Current instance-level MCP can build/edit workflows and search previews of all workflows visible to its user; enabled workflows are not independently scoped per client. Do not give household roles general workflow administration. Match behavior to the pinned n8n version. [Official MCP documentation](https://docs.n8n.io/connect/connect-to-n8n-mcp-server.md). |
| 4 | **Paperless-ngx API adapter, conditional** | Themis: find an explicitly authorized warranty, contract or household letter with source evidence. | This adds a document service and durable data lifecycle; it is not a drop-in MCP installation. Official REST API supports full-text search and object permissions. If adopted, expose bounded read-only search/read through a dedicated non-superuser principal; test document visibility before indexing. Do not assume a community MCP enforces these limits. [Official API](https://docs.paperless-ngx.com/api/). |
| 5 | **Immich search adapter, later** | Owner-private photo lookup without duplicating the library or sending images to public services. | Reuse the existing local service described in [`immich.md`](../immich.md). Upstream supports metadata/contextual search, including location and date filters; an adapter still needs version-matched API and permission qualification. Backups/import readiness and personal-versus-shared access come first. No official MCP was qualified here. [Official search documentation](https://docs.immich.app/features/searching/). |
| 6 | **Expedia Travel Recommendations MCP, evaluate/harden only** | Lyra: structured hotel, flight, activity and car discovery through a first-party server. | Supports stdio/Streamable HTTP and requires an Expedia API key. Eligibility, cost and coverage remain unknown. Source needs logging, timeout, dependency and output-projection hardening; its hotel parameters do not establish complete family quotes or Hotels.com member rates. See the source review below. [Official project](https://github.com/ExpediaGroup/expedia-travel-recommendations-mcp). |

The ordering is an assessment of fit, not an approved rollout sequence. Maps and
Context7 are external services even with local inference. Their use needs the
same explicit external-data treatment as current public search. API limits,
provider terms, cost ceilings and exact tool inventories need checking at
implementation time; this note deliberately does not assert fixed prices.

Expedia source at `64cdfb15525bb2e4146ddf1be708693f91c110a2` logs request
parameters and complete authentication headers, and calls `requests.get`
without a timeout in
[`api/client.py`](https://github.com/ExpediaGroup/expedia-travel-recommendations-mcp/blob/64cdfb15525bb2e4146ddf1be708693f91c110a2/expedia_travel_recommendations/api/client.py).
Do not deploy that revision as-is. Metadata-only logging, bounded timeouts,
sanitized failures and qualified dependencies are prerequisites. Recommendation
results still need exact dates, rooms, child ages, currency and full-group total
verification; they do not authorize booking or access an owner's Hotels account.

## Skills worth adding

Prefer small, repository-owned instruction skills for capabilities already
available through finite tools:

- **Household paperwork:** Themis extracts obligations and deadlines with page
  references, flags missing evidence, and proposes reminders through Luna. Start
  with owner-supplied documents; a skill does not connect a document repository.
- **Budget and expense analysis:** Plutus reconciles supplied CSVs/statements,
  distinguishes transfers from expenses, documents coverage and suggests
  categories. No bank credentials, transaction writes or implicit imports.
- **Meals and pantry planning:** Hestia combines granted stock/list/offer reads,
  separates confirmed dietary requirements from guesses, and routes list writes
  to Vega and stock writes to Ceres.
- **Trip comparison:** extend the existing travel skill when new provider tools
  exist. Preserve its matching-quote criteria and distinguish package holidays,
  hotel-only rates, advertised leads and owner-account prices.
- **Documentation lookup:** a small Apollo skill should request exact package
  versions, prefer upstream docs and fall back clearly when Context7 lacks
  coverage; retrieved instructions cannot override repository policy.

The [Agent Skills specification](https://agentskills.io/specification) defines
instruction files and optional executable resources. HomeCompute's
[`setup-openclaw-team.py`](../../scripts/setup-openclaw-team.py) deliberately
accepts reviewed instruction-only managed bundles, and
[`openclaw_team_policy.py`](../../scripts/openclaw_team_policy.py) has a finite
skill allowlist. Adding a skill requires that owning policy and its tests to
change; copying a registry directory alone is insufficient.

Anthropic's [official example collection](https://github.com/anthropics/skills)
is useful reference material for document, data and testing workflows. Review
each chosen skill's own license, scripts and dependencies before adapting it.
The Codex desktop already provides document/PDF/spreadsheet skills in this
session; those capabilities do not automatically exist inside OpenClaw. Avoid
installing a whole marketplace into every role.

## Keep existing boundaries

Use existing CLIs and APIs when they already meet the task. MCP is useful
when a client needs structured remote tools or a deliberately constrained
interface; wrapping an existing CLI is not itself a capability gain.
GitHub CLI remains the default in the coding workflow. OpenClaw
specialists do not have general shell access, so any future direct GitHub
reads would need a finite adapter or MCP facade. No such unmet need was
established here; do not add another GitHub service or credential for it.

- **Home Assistant MCP:** the official integration is a credible upstream
  capability, but current documentation defaults to exposing all LLM APIs and
  requiring an administrator. Any use must deliberately select exposed APIs
  and preserve HomeCompute's existing action authority. It is not a reason to
  replace the current proposal path or publish HA to the internet.
  [Official integration](https://www.home-assistant.io/integrations/mcp_server).
- **General Playwright MCP:** useful in an isolated developer/test environment,
  but its origin controls are explicitly not a security boundary. Preserve
  the protected research browser and separate owner-account sessions rather
  than exposing arbitrary interaction to household roles.
  [Microsoft's source and security guidance](https://github.com/microsoft/playwright-mcp).
- **Generic filesystem, shell, database, password-vault and banking MCPs:** defer
  broad access. The present gaps need named bounded functions, not tools that
  bypass principal boundaries or give model context raw secrets.

## Implementation acceptance

The current upstream MCP reader in
[`openclaw_services.py`](../../scripts/openclaw_services.py) is explicitly
configured for Aula and Tilbudstrolden, including protocol/session handling
and output projection. A new server is not enabled by adding its URL to chat
instructions. For the first selected candidate:

1. Add only its needed functions to a dedicated provider facade, with fixed
   origins, external credential references, bounded inputs/results and a
   tested compatible transport. Keep local provider containers in the owning
   `deploy/` project with immutable pins; record hosted endpoints explicitly.
2. Extend the role policy in
   [`openclaw_team_policy.py`](../../scripts/openclaw_team_policy.py), the
   manifest in [`openclaw-team.json`](../../config/openclaw-team.json), and the
   owning guarded setup tooling. Atlas continues to delegate domain calls;
   a new tool must not implicitly appear for Family or other specialists.
3. Reconcile its instruction skill and managed-skill allowlist, run focused
   facade/role/installer tests and repository validation, then publish a
   reviewed release before any separately authorized rollout. Verify actual
   authenticated discovery, allowed calls, denied roles and failure behavior.

For a selected addition, declare the upstream pin or hosted endpoint, license,
external secret reference, egress, exact tool/schema projection, owning role,
cost/rate limits and failure behavior in source. Extend the guarded installer
and operational documentation; do not provision it by UI alone. Add focused
tests for tool visibility, malformed upstream output, injected instructions,
private-data exclusion, outages and unexpected writes. Keep synthetic results
separate from authenticated live qualification, then use the normal reviewed
publication and deployment lifecycle. Publishing this research note alone does not deploy or enable integrations.

## Assessment validation

Reviewed the research note and index change, checked 33 local Markdown links,
and checked whitespace and final newlines, including this new untracked file.
`git diff --check` and `git diff --cached --check` passed. Full repository tests
and validation were not run for this documentation-only change. Provider API
access, credentials, billing, transport compatibility and live role enforcement
remain unverified; no system synchronization or production readiness is claimed.
