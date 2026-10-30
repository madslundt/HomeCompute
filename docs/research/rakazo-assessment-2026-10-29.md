# Rakazo assessment for a Grok Bot-like HomeCompute assistant

Evidence date: 2026-10-29. Status: source/documentation assessment only; no
installation, target-hardware benchmark, migration or live-system verification.
Upstream source snapshot: [`953160d53fa594e6712b42ff038dd886aafc5bd6`](https://github.com/elie222/rakazo/tree/953160d53fa594e6712b42ff038dd886aafc5bd6).
Documentation at that revision is used below; marketing claims are not acceptance
evidence. This note does not change accepted ADRs or authorize a deployment.

Follow-up: a [runtime-only local-model comparison](../benchmarks/openclaw-rakazo-baseline-2026-10-29.md)
has now been exercised. It measures the real Pi runtime with no tools; full
application, computer, voice and recovery acceptance remain unverified.

## Recommendation

Rakazo is a credible candidate for the **complete teammate-and-computer user
experience** the owner wants. Its documented interface covers several gaps that
would otherwise require custom OpenClaw work: a computer beside chat, human
takeover, teach-by-demonstration, editable skills, routines, mobile access and
approval cards. It is worth a bounded comparison before building equivalents.
This is an engineering judgment from the feature evidence below, not a measured
claim that it works better on HomeCompute.

Keep OpenClaw as the current household assistant while qualifying that candidate.
Rakazo runs its own Pi-based agent/runtime, API, worker and database. The reviewed
material does not establish a native mode in which its interface simply controls
our existing OpenClaw sessions. Treat it as another runtime requiring adapters and
policy review, rather than assuming a drop-in frontend. The upstream
[README](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/README.md)
explicitly labels Rakazo beta and lists its separate stack.

## What is supported by the reviewed material

| Area | Evidence and implication |
| --- | --- |
| Persistent teammates | Bots have private conversations, memory and role instructions; shared memory exists at Space scope. Useful product foundation, but personal/shared data boundaries still need explicit mapping. [Memory](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/memory.md) |
| Computer interface | Chat opens the computer screen, offers human takeover and challenge handoff, and delivers files as artifacts. Team is the default computer mode and shares files and site sign-ins; Private gives a bot its own computer. These defaults are not our household privacy policy. [Computer](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/computer.md) |
| Teaching | Web/desktop can record a demonstrated task, produce an editable draft skill, test it, and attach a routine. Protected input is documented. An upstream browser test exercises recording, draft editing and save failures, but we have not run it or verified real task generalization. [Skills](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/skills.md), [test source](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/apps/web/e2e/teach-task.spec.ts) |
| Routines | Chat-created schedules, change watches, webhooks and Git events are documented, with pause, history and stop controls. They are another scheduling authority; running duplicate household routines in n8n and Rakazo would need prevention. [Routines](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/routines.md) |
| Durable execution | Documentation describes atomic leases/fences, heartbeats, startup/periodic recovery, shutdown handoff and coalescing pristine recurring catch-up work. It separates server-side first-response timing from client delivery. This is a useful design, not proof of exactly-once effects in external services. [Run execution](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/run-execution.md) |
| Approvals | Ask, Auto and Never ask modes, allow/block rules and chat approval cards exist. Auto uses a model judgment. None substitutes for HomeCompute's independently enforced HA, publication or maintenance authority. [Approvals](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/approvals.md) |

## Local inference, tools and speech

Rakazo supports operator-controlled OpenAI-compatible model endpoints, exact model
IDs, context/token limits, optional API keys and custom headers. Private DNS names
need explicit host configuration and private-endpoint permission; HTTPS still
validates certificates. A LiteLLM connection is therefore plausible, but requires
testing our CA trust, authentication, tool-call schema, streaming, cancellation,
timeouts and context handling. No connection was attempted. Keep cloud fallbacks
empty for private tasks.
[Self-hosted models](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/self-host-models.md).

Screenshot-based computer tools require a genuinely image-capable model. A
Supports images switch advertises a capability; it does not add one. The current
OpenClaw automation route must not be assumed qualified for screenshots. Start
comparison tasks with text/API/DOM capabilities, then qualify a separate vision
route only through the existing catalog and model lifecycle.
[Model guide](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/models.md),
[HomeCompute catalog](../../config/model-catalog.json).

Remote MCP and API connectors are supported; private MCP/API endpoints require
an operator setting. This could reuse our finite HomeCompute facade, but not by
copying the native OpenClaw token. Our existing specialist permissions narrow a
shared credential through native role policy, rather than independent
cryptographic credentials per specialist. A different runtime needs its own
limited principal and backend-enforced capabilities, with denials tested outside
the agent prompt. Third-party connector catalogs are optional. No native
Mattermost adapter was established by this review; documented Telegram reception
is webhook-only, unlike our existing private long-polling path. Preserve current
communication until an explicit replacement is qualified.
[Rakazo integrations](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/self-host-integrations.md),
[HomeCompute integrations](../openclaw-integrations.md),
[HomeCompute communication](../openclaw-communication.md).

Local voice is feasible in principle: Rakazo documents self-hosted
OpenAI-compatible speech servers, alongside optional cloud providers and macOS
voices. Its mobile calls use the phone's recognition/voices, which requires a
separate privacy/offline check; do not assume those remain on-device.
[Voice guide](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/voice-and-calls.md).

There is a concrete protocol gap. The reviewed OpenAI-compatible voice adapter
declares adapter version `0.1.0`, posts to `/audio/speech` requesting MP3, and
rejects responses outside `audio/mpeg` or `audio/mp3`. The current Mattermost
speech bridge returns Danish WAV. A bounded encoding/protocol adapter or an
upstream change is needed, plus checks of model and voice discovery. Relevant
source model pins are Whisper large-v3-turbo
`41f01f3fe87f28c78e2fbf8b568835947dd65ed9`, Hviske v5.3
`5d1a09822018702dc51d763e3a867b62d26b3501`, and Plapre Nano v2
`007e0b471e377dd5061786f7df6ad659d15c4d5f`; inventory entries do not establish
current live placement/readiness. Preserve the approved Danish voice, verifier
and fallback behavior.
[Voice adapter](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/packages/adapters/src/openai-compatible-voice.ts),
[model catalog](../../config/model-catalog.json),
[communication speech behavior](../openclaw-communication.md#mattermost-voice-messages).

## Hosting and trust work a migration would add

Rakazo requires a long-running API, worker, Postgres and computer provider. The
quick start defaults to mutable `edge` images; HomeCompute would need an immutable
source/image tuple, declarations, external secrets, upgrade/rollback handling and
backup/restore qualification. Do not run its installer directly on a managed host
as the desired-state implementation.
[Self-hosting](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/self-host.md).

The Docker supervisor holds the Docker socket, equivalent to control of that
Docker host. A candidate should use a separately bounded guest/daemon, not our
shared host socket. Computer defaults are 2 GiB, two CPUs and 2048 PIDs; API,
worker and database add overhead. Restricted egress blocks private networks,
host and metadata, but permits the public internet. Enforcement needs compatible
Linux Docker/iptables rules; unsupported installs can select open egress.
HomeCompute must implement reviewed policy through its own lifecycle, with an
explicit fail-closed candidate configuration and capacity checks.
[Docker computers](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/self-host-docker-computers.md).

Upstream openly documents prompt-injection gaps: shell/browser actions can send
data to reachable public hosts; saved memory/skills are trusted context; an
approval model can pass a plausible attacker-chosen URL. Restricted network mode
is not a public-domain allowlist. This differs from our protected research browser
and finite site workers. A pilot must test these paths without real private data
or credentials.
[Security policy](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/SECURITY.md).

Backend configuration, approved skills/routines and permission policy must remain
reproducible from tracked non-secret source. UI-only setup is insufficient.
Account defaults, separate database migration/runtime roles, optional unencrypted
session recording and desktop-device access also require review; keep recording
off and personal computer access disabled during the candidate comparison.
[Configuration](https://github.com/elie222/rakazo/blob/953160d53fa594e6712b42ff038dd886aafc5bd6/docs/self-host-configuration.md),
[HomeCompute security](../../SECURITY.md).

## Bounded comparison and decision gate

Before implementation, record the candidate architecture issue/ADR required by
[CONTRIBUTING](../../CONTRIBUTING.md). Prepare a synthetic-only, isolated candidate
without connecting real household logins or duplicating existing schedules.

Run the same tasks against current OpenClaw and the pinned Rakazo candidate:

1. Public research with source links and one useful delivered artifact.
2. A two-role handoff with task ownership and no private/shared context leakage.
3. A scheduled change-only report across restart, with exactly one delivered
   notification and cancellation that prevents subsequent effects.
4. Lost responses before and after a synthetic write commits, worker death,
   expired lease and expired/revoked approval. Verify effect receipts and explicit
   uncertain state, not just a single claimed run.
5. Teach a credential-free fixture, edit its learned skill, rerun on changed
   fixture data, then reproduce the approved routine from tracked source.
6. Danish voice with the local speech path, explicit voice/format checks, measured
   latency and packet/endpoint evidence of no unintended cloud fallback.
7. Unauthorized backend calls, public exfiltration attempts, cross-Space access,
   arbitrary private endpoints and missing firewall rules must fail or stop at a
   clearly documented boundary.
8. Restore database, keys and computer files into a disposable instance; measure
   CPU/RAM plus local model latency while the existing assistant remains usable.

Measure completion quality, intervention count, time to first useful response,
end-to-end latency, delivery evidence and operating burden. Set pass thresholds
before running the comparison. No numerical performance or reliability advantage
is established by this assessment.

Adopt only if the candidate's measurable product benefit exceeds the migration,
policy and maintenance cost, and all required boundaries pass. Otherwise keep
OpenClaw and implement its missing capabilities incrementally. Do not merge agent
memories wholesale or switch the working household channel during this trial.
