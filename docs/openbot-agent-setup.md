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
- Every local `automation-moe` assignment uses OpenBot's **Hermes** provider
  with the `custom` / `automation-moe` model and **Extra high** effort. The
  Hermes provider exposes Extra high and maps it to the local route's `xhigh`;
  the standard Custom provider only exposes Low, Medium, and High. The local
  endpoint defaults to `xhigh` when effort is omitted and rejects High. The
  Engineer's synthetic route check returned the expected response at Extra
  high. The UI confirms each profile assignment; it does not independently
  prove the request payload received by the inference server.
- **Researcher** is the designated ChatGPT specialist (`GPT-6.1-Sol`, High)
  for complex or broad public research. Keep private household, personal, and
  work material out of its prompts unless the owner explicitly authorizes that
  specific disclosure.
- **Lead Engineer** is the ChatGPT `GPT-6-Luna` / High technical planner and
  reviewer for public or explicitly cleared work. It may critique local plans
  and patches, but receives no private project or household inputs by default.
  The live agent was created in OpenBot on 2026-11-12 and its server ID is
  recorded in `config/openbot-agent-roster.json`.
- The server-level skill learning policy is **Ask first**. Agent backup models
  also ask before switching when a limit is reached before a response starts;
  OpenBot may continue an in-progress turn on the first ordered backup after a
  provider error, so keep the backup order explicit.
- Credentials are held by OpenBot/provider configuration and are not part of
  this manifest.

## Role selection

The OpenBot roster has eight roles: **Atlas** coordinates work, **Lead
Engineer** handles technical direction and review, **Engineer** implements and
debugs, **Home Planner** handles household projects, maintenance and energy,
**Researcher** handles public research, **Designer** shapes product experiences,
**QA** performs independent acceptance review, and **Hermes (OpenBot Pilot)**
remains the existing embedded Hermes trial.

Use the existing separate **Hermes owner** agent for day-to-day Aula, household
and Home Assistant questions. It runs in `hermes-personal-v23` with the local
`automation-moe` model at xhigh, the `day-plan` and `homecompute-household`
skills, and its own `hermes-owner` HomeCompute MCP credential. Its verified
tool surface includes Aula reads, calendar and task tools, Home Assistant status
and bounded proposals; Home Assistant execution still uses its separate review
path. n8n remains the workflow engine; Hermes has no direct n8n administrator
grant. This is the right daily role;
creating a duplicate OpenBot agent would not add those integrations.

The 2026-11-10 audit found `context7` in the server MCP list. The Marketplace
showed GitHub and Context7 among connected apps. Per-agent grants and the exact
effective scope of these connections were not verified; treat server-level
connections as potentially shared until OpenBot demonstrates otherwise. Adding
the personal HomeCompute MCP there could expose it to Researcher. Keep private
service tools in `hermes-personal-v23` until OpenBot supports and verifies
per-agent tool grants or a separate isolated server is qualified. The private
Hermes owner assistant and the embedded OpenBot Hermes agent have separate
data, tools and histories.

Route personal history, Aula, Calendar, Daythread follow-through, live Home
Assistant reads and reminders to
the private Hermes owner lane. Use Home Planner only with approved extracts or
public information. Atlas scopes work; Lead Engineer shapes technical plans;
Engineer handles one assigned implementation at a time. This includes
HomeCompute, Daythread and
ChessMate code only after the execution boundary is qualified. Researcher gets
public questions and cleared material only. Keep employer and commercial
project work in an approved alternative while OpenBot's noncommercial license
and access boundary do not fit those projects. Do not create an OpenBot agent
for every repository; use task-specific context and the owning repository's
guidance.

For independent product work, Atlas already covers product coordination:
intake, scope, decisions, task routing and acceptance. **Designer** handles
user flows, interaction design, accessibility and prototype critique; **QA**
handles independent acceptance review, edge cases and reproducible bug reports.
Researcher can provide public market and competitor research; keep private user
research in an approved isolated lane. Both agents were created in OpenBot on
2026-11-10 at the owner's direction. They use the local Home AI route and add
no provider, tool, schedule or action integration. Their stable server IDs are
recorded in the roster. Their profiles now use Ask first, with Computer Use and
Local scripts disabled; keep their tasks to public or synthetic material until
isolation is qualified.

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

The 2026-11-12 Brave UI readback initially found Builder and Researcher on
**Full access** with Computer Use and Local scripts on. Builder was renamed
**Engineer** with its existing server ID retained; it is now **Ask first**,
with Computer Use and Local scripts off. It now uses the Hermes provider at
Extra high. Researcher remains on GPT-6.1-Sol / High and is now **Ask first**,
with Computer Use and Local scripts off and profile instructions limiting it
to public or explicitly cleared information. Lead Engineer is on GPT-6-Luna /
High with **Workspace only** access, Computer Use and Local scripts off, and
public/cleared-only instructions. OpenBot's Workspace only description still
allows reads and network access, so effective isolation is not established by
that setting. Do not send private material to Researcher or Lead Engineer. A
cloud model receives prompts and files sent to its agent.

Atlas, Home Planner, Designer, and QA were also assigned the OpenBot Hermes
provider with local `automation-moe` / Extra high. The Hermes provider uses the
configured local Home AI endpoint. Researcher and Lead Engineer remain the two
cloud model assignments.

The 2026-11-10 database/UI audit found broad access on the original five
agents. In a subsequent UI reconciliation, all seven agents were set to
**Ask first**, with Computer Use and Local scripts disabled. The profile pages
show no configured routines. `Ask first` requires approval before OpenBot tool
use, but still grants full computer access from the workspace and shared
folder; the profiles also show **All files**. It is an approval mode, not a
filesystem isolation boundary.

If an agent uses the OpenCode provider on the Linux server, OpenBot's documented
Workspace only enforcement is not supported there; its provider-specific
access control fails closed on hosts without confinement support. A role prompt
or separate workspace folder does not enforce isolation. See the [upstream
access-mode limitation](https://github.com/nightly-labs/openbot/issues/715).

## Current live evidence

On 2026-11-12, Brave showed OpenBot connected to `home-core`. Researcher uses
ChatGPT / `GPT-6.1-Sol` / High, and Lead Engineer uses ChatGPT / `GPT-6-Luna` /
High. Atlas, Engineer, Home Planner, Designer, QA, and Hermes (OpenBot Pilot)
use the Hermes provider with custom `automation-moe` at Extra high. These are
UI assignments, not independent request-log evidence. A synthetic Engineer
prompt returned correctly through the Extra high local route.

The following assignment table is a historical 2026-11-10 snapshot, not the
current target roster:

On 2026-11-10, the guest status command reported `home-core` online on OpenBot
**0.66.0**. The first database audit found five agents; Designer and QA were
subsequently added in the UI, bringing the roster to seven. A
read-only query of `openbot.db` selected only agent IDs, names, provider/model,
reasoning effort, access, Computer Use and automation flags; it did not read
chat, memory, attachment, prompt, credential or file contents. Stable IDs for all seven existing agents are recorded in
`config/openbot-agent-roster.json`; no duplicate agents were created.

The UI labels are **Atlas (Team Coordinator)** with the title
**Team Coordinator · Start here**, and **Hermes (OpenBot Pilot)** with the title
**OpenBot Hermes Pilot**. The existing chats and histories remain under those
agents. Each roster agent has a distinct Plush avatar shape and color.

| Agent | Primary model | Backup models | Effort | Access | Computer Use / Local scripts |
| --- | --- | --- | --- | --- | --- |
| Atlas | Home AI (local) / `automation-moe` | None | Medium | Ask first | Off / off |
| Builder | Home AI (local) / `automation-moe` | None | Medium | Ask first | Off / off |
| Home Planner | Home AI (local) / `automation-moe` | None | Medium | Ask first | Off / off |
| Researcher | ChatGPT / `GPT-6.1-Sol` | Home AI (local) / `automation-moe`, ask first | High | Ask first | Off / off |
| Hermes (existing pilot) | Home AI (local) / `automation-moe` | None | xhigh | Ask first | Off / off |
| Designer | Home AI (local) / `automation-moe` | None | Medium | Ask first | Off / off |
| QA | Home AI (local) / `automation-moe` | None | Medium | Ask first | Off / off |

The Researcher model was reconciled to the manifest's available
**GPT-6.1-Sol / High** choice. Its only backup is the local `automation-moe`
route. OpenBot asks before switching when the limit is reached before the
response starts, but may continue an in-progress turn on the first backup after
a provider error. Local-first agents have no cross-provider backup, so an
outage cannot silently send their task material to a cloud provider. The Hermes
pilot stays separate from the private Hermes owner assistant.

All seven stable OpenBot IDs are recorded in
`config/openbot-agent-roster.json`. The role-specific instruction skills are written in
`config/openbot-agent-skills/`, but the live profiles still show zero assigned
skills. The OpenBot Skills panel's Create skill action only prefills a chat prompt;
no supported file import or declarative skill installer was verified. Keep the
tracked skill files as the source of truth and reconcile their exact contents and
assignments through a supported OpenBot workflow before calling them installed.
The embedded Hermes pilot has no extra role skill. Do not install unreviewed
Marketplace skills: the 2026-11-10 UI showed no skills in its **Verified** filter.
Server-level skill learning is now set to **Ask first** to prevent silent skill
revisions.

The target shared OpenBot MCP list uses LiteLLM's `all-harness-mcps` group. It
includes the full configured catalog, including n8n workflow administration,
the owner capability relay and bounded Android UI actions. All OpenBot agents
can discover the shared connection. Apply the documented key provisioning,
role-scoped bearer headers and OpenBot settings after deploying the reviewed
source before treating these tools as live. No agent or channel routines were
found.
Server-level connected apps include GitHub and Context7; per-agent access and
effective tool reach remain unverified. All
seven profiles were reconciled to **Ask first**, with Computer Use and Local
scripts off. They still show **All files**: Ask first requires approval before
OpenBot tool use but is not filesystem confinement. The four regular agents
previously showed automation enabled even though no routines were configured;
that flag was not reconciled in this pass. Workspace enforcement, memberships,
provider account scope and runtime isolation remain unverified.

The current source pins the 0.66.0 Linux/amd64 image. The guest remains healthy
and the service status is read-only. The scheduled NAS backup timer was active;
its last completed archive receipt was 2026-11-09 22:26 UTC, within the
platform's 36-hour readiness window. A later attempt at 2026-11-10 09:43 CET
deferred with exit 75. This is current backup evidence, but recheck it before
any provider sign-in, workspace creation or image change. Do not treat a code
revision or image digest as proof that OpenBot histories and credentials are
backed up.

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

The earlier 2026-11-09 model smoke checks remain evidence for the then-tested
agents and routes, not for the current Researcher binding or isolation. The
OpenBot app's browser client was not authenticated during the initial audit, so
that audit made no UI changes. The signed-in UI reconciliation preserved all seven agent histories and IDs.
The Researcher model, ordered local backup, profile permissions, Atlas/Hermes
labels, distinct avatars and server skill-learning policy were verified in the
UI. Role skill text remains source-only until it can be installed and assigned
through a supported OpenBot workflow.
