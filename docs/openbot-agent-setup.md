# OpenBot agent roster

`config/openbot-agent-roster.json` is the HomeCompute record for the named
agents and model choices on the OpenBot server `home-core`. It is a
HomeCompute-owned manifest, not an OpenBot import format. OpenBot currently
provides agent creation and model selection in its UI; no supported declarative
roster import was verified. Apply and reconcile the manifest through
`https://openbot.run/app` and review every changed profile against this file.

## Model policy

- OpenBot runs in a Docker container inside the `home-core` agents VM, so its
  Home AI endpoint is `http://ai.home.arpa:18080/v1`. The guest's DNS maps
  `ai.home.arpa` to the host-only `10.77.20.1` bridge; that bridge verifies TLS
  while forwarding to Caddy. The former `https://ai.home.arpa:8443/v1` URL
  resolved to a LAN address where port 8443 does not listen.
- Omit a provider API key for this LAN endpoint. The Caddy gateway injects its
  restricted network-inference key. Keep TLS verification enabled and ensure
  the OpenBot runtime trusts Home Core's private Caddy CA.
- New Home AI agents use **Home AI (local)** / `automation-moe` at **Medium**.
- The OpenBot effort selector only offers Low, Medium, and High. The live
  `automation-moe` route accepts Low, Medium, and `xhigh`; sending High fails
  with `Unexpected reasoning effort high`. Use Medium for local agents until
  OpenBot can select `xhigh` directly or a reviewed compatibility layer is
  implemented. The model's default when effort is omitted is `xhigh`.
- **Researcher** is the designated ChatGPT specialist (`GPT-6.1-Sol`, High)
  for complex or broad public research. Keep private household, personal, and
  work material out of its prompts unless the owner explicitly authorizes that
  specific disclosure.
- Agent-level backup models remain set to **Ask first** unless the owner
  approves a different policy.
- Credentials are held by OpenBot/provider configuration and are not part of
  this manifest.

## Role selection

Keep the OpenBot roster to four roles: **Atlas** coordinates general work,
**Builder** handles HomeCompute and code, **Home Planner** handles household
projects, maintenance and energy, and **Researcher** uses ChatGPT for complex
public research.

Use the existing separate **Hermes owner** agent for day-to-day Aula, household
and Home Assistant questions. It runs in `hermes-personal-v23` with the local
`automation-moe` model at Medium, the `day-plan` and `homecompute-household`
skills, and its own `hermes-owner` HomeCompute MCP credential. Its verified
tool surface includes Aula reads, calendar and task tools, Home Assistant status
and bounded proposals; Home Assistant execution still uses its separate review
path. n8n remains the workflow engine; Hermes has no direct n8n administrator
grant. This is the right daily role;
creating a duplicate OpenBot agent would not add those integrations.

OpenBot's server MCP list currently contains only `context7`. OpenBot exposes
those server MCP connections to the server's agents as a shared tool set. Adding
the personal HomeCompute MCP there would also expose it to Researcher, which is
assigned ChatGPT. Keep private service tools in Hermes until OpenBot can scope
them to one agent or a separate isolated server is used. The Hermes CLI
embedded in OpenBot is separate from `hermes-personal-v23`; its connection to
Home AI does not import the owner sandbox's skills, memory or MCP tools.

OpenBot's Hermes provider runs its own Hermes CLI inside the
`homecompute-openbot` container. The CLI observed there is v0.22.7. Configure
that CLI with `hermes model` and select the custom OpenAI-compatible endpoint
`http://ai.home.arpa:18080/v1`, model `automation-moe`, and no API key. This
connects Hermes to Home AI without Nous Portal sign-in. The CLI's `/data/.hermes`
state is separate from the other Hermes/OpenShell sandbox; this connection does
not share that sandbox's memory, profiles, or sessions. Keep Home AI as the
default provider for new OpenBot agents; the Hermes provider is available for
future deliberate assignment.

## Access policy

Computer Use and Local scripts are disabled for every agent. The current
OpenBot UI reports **Full access** for Atlas, Researcher, Builder, and Home
Planner. Attempts during the 2026-11-09 audit to set Atlas and Home Planner to
**Workspace only** returned `host/500/team_api_error`; neither change took
effect. Treat this as unresolved live drift from the workspace-only target in
the roster: these profiles can still have broad command and file access on the
OpenBot host. Do not enable computer control or local scripts, and resolve the
access-level limitation before giving the agents sensitive host data or broader
tasks.

## Current live evidence

On 2026-11-09, OpenBot was connected to server `home-core` and reported version
**0.65.0**. The app offers **0.66.0**. HomeCompute source now pins the 0.66.0
Linux/amd64 OCI manifest digest, but the update is not applied yet. The server
settings page says its host controls installation. Use
`scripts/setup-openbot-guest.sh install` from a clean published HomeCompute
revision after Home Core is deployed at that revision. This preserves the
existing `/data` volume. The host backup lifecycle currently reports a retired
state and last recorded snapshot `2026-10-28`; resolve and verify the documented
backup lifecycle before changing the image.

OpenBot runs in the `agents` VM; its container resolves `ai.home.arpa` to
`10.77.20.1`. The original `https://ai.home.arpa:8443/v1` endpoint failed with
`ECONNREFUSED` because that port does not listen at the resolved address. A
local Home AI endpoint at `http://ai.home.arpa:18080/v1` was added without an
API key. From the OpenBot container, this endpoint returned the model list and
a real `automation-moe` completion. The two misconfigured custom endpoints were
removed; **Home AI (local)** is now the only custom endpoint, and the OpenBot
default provider remains **Custom provider**.

The app's Hermes CLI is already installed in that container at version
v0.22.7. Its current configuration was `provider=auto`,
`model=anthropic/claude-opus-4.6`, `base_url=https://openrouter.ai/api/v1`.
Those values describe OpenBot's private Hermes CLI configuration, not the
separate Hermes/OpenShell sandbox. The supported local setup is to choose the
custom endpoint through `hermes model` in the OpenBot container, using the
values in `config/openbot-agent-roster.json`. The CLI reported the endpoint
reachable, found ten model ids, selected `automation-moe`, and detected a
262144-token context window. Reasoning is set to `xhigh` because the backend
rejects `high`.

The OpenBot provider page now reports Hermes **Connected** and exposes
`custom:automation-moe` in the model picker. No current agent uses the Hermes
provider, and the default for new agents remains **Custom provider** / Home AI.
To reconcile it from a released HomeCompute checkout in the agents VM, run
`python3 scripts/configure-openbot-hermes.py`. The script updates only the
OpenBot container's Hermes CLI settings and refuses to rewrite unrelated
custom providers when their configuration contains credentials.

The 2026-11-09 audit verified Atlas, Builder, and Home Planner on **Home AI
(local)** / `automation-moe` at **Medium**; each returned an exact-match smoke
reply through OpenBot. Researcher uses ChatGPT/GPT-6.1-Sol at High. High effort
on the local route failed with an upstream validation error; Medium succeeded.
OpenBot's selector does not expose `xhigh`, even though that is the local
model's highest accepted level and its no-effort default. Keep the local agents
at Medium until that interface or a reviewed adapter can represent the model's
effort levels.

The prior [Home Core OpenBot assessment](research/openbot-home-core-assessment-2026-11-09.md)
predates deployment. Current read-only checks confirm the container is running
from the source-managed service and matches the 0.65.0 digest. They do not
establish a fresh agents-VM backup or a successful 0.66.0 rollout. The repeated
`host/500/team_api_error` when changing agent access remains unverified against
0.66.0; retry Workspace only after the update is qualified.
