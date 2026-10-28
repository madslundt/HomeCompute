# OpenClaw browser and CLI installation

## Current browser access (2026-10-25)

The running protected browser is enabled for Nova and Lyra. Atlas delegates
research to specialists; the coordinator guard blocks his direct domain calls.
Other specialists retain their existing service tools without browser access.
Chromium already uses modern headless mode (`--headless=new`).

Approved travel hosts are `www.afbudsrejser.dk`, `www.solfaktor.dk`,
`www.spies.dk`, `www.tui.dk`, `www.apollorejser.dk` and `www.sunweb.dk`.
The native browser configuration, protected DNS modules and browser/DNS image
share that finite policy. Private destinations and interactive page actions
remain blocked. Lyra starts with navigation directly; the bundled skill's
generic status/tabs setup loop is unsupported by this protected profile.

A live Lyra turn successfully navigated Solfaktor, read the rendered page and
closed its tab. Authenticated forwarding now handles Chromium's plain-text
close/focus responses, fixing a prior HTTP 403 during tab cleanup. Current image:
`sha256:53fdd15f25c86b8dd959683ca6243351ac83f5cc05a9884ded457d932acbac7a`.
It derives from the existing browser image without package upgrades. Original
containers and private configuration backups were retained for rollback.

Browser evidence can establish current advertised prices. Matching group quotes
also require dates, traveler ages, room/board, currency and the complete total;
interactive selectors, cookie walls and CAPTCHAs remain explicit coverage gaps.
The existing n8n supplier quote workflow remains separate.

## Tavily fallback

Nova and Lyra load the shared `public-web-research` skill. The trusted search
tool returns `browser_fallback_required` when Tavily is unavailable, rate limited
or over quota. The result contains a fixed Marginalia search URL and empty result
and source lists; it is a navigation plan, not retrieved evidence. The specialist
then uses the protected headless browser to navigate, snapshot and close its tab.
`provider: browser` requests the same plan without making a Tavily request.

Quota responses (432/433) suppress further Tavily attempts for six hours, rate
limits (429) for one minute, and other failures for 30 seconds. This cooldown is
in memory and resets when the communication adapter restarts. Public queries are
encoded as URL data, with an explicit shorter-query response if the browser's
4096-character URL limit would be exceeded. Upstream error bodies and credentials
are never returned to the agent. Other household service errors retain their
existing behavior.

Marginalia returned relevant results in the live headless probe and its exact
host, `marginalia-search.com`, is now approved. It favors non-commercial pages and
has a smaller index; news/date filters and commercial price coverage are limited.
Check dates and prices at approved primary sources. DuckDuckGo, Google and several
other public engines presented bot challenges or unavailable results from this
network, so they are not treated as a dependable automatic fallback. A challenge
or policy refusal ends the attempt and is reported, without a retry loop.

The live Nova verification called `provider: browser`, searched Marginalia,
inspected a discovered official nix.dev page and closed its tab successfully.
No Tavily request was made. The shared workflow is also installed for Lyra.
The CLI exit waiter timed out after the terminal reply was saved; successful
tool results and the final report were independently checked in the canonical
transcript, and a native read confirmed that no verification run remained active.

## Bitwarden custody and private access requests (2026-10-27)

The operator created a separate `openclaw` Bitwarden organization/project and
approved read-only project access for `openclaw-credential-service`. The web
vault holds the Hotels.com password. No Bitwarden values belong in OpenClaw's
workspace, model context, tool output, logs, repository or browser CDP profile.

`scripts/provision-openclaw-bitwarden.py` accepts a machine token and sign-in
email through a hidden, interactive root terminal on home-core. It stores an
exact-ID account mapping and token in `/var/lib/homecompute-credential-secrets`
with directory mode 0700 and file modes 0600, refusing symlinks and overwrites.
It starts no service and grants no agent access. Its companion provider only
executes a qualified Nix-store `bws`, retrieves the configured password UUID,
verifies organization/project/key, discards CLI stderr, suppresses raw error
details and uses private temporary CLI state. Tests use synthetic credentials.

The custody helpers are staged root-only under
`/opt/homecompute/openclaw-credentials` on home-core. The official static
`bws 2.1.0` Linux x86_64 MUSL release is stored immutably in Nix with a GC root
at `bws-package` in that directory. Its release ZIP SHA-256 is
`f59ee150e42b82128d437087e9bac920053c6bfddcb960d20ce9386e5ac9bba6`;
the installed binary SHA-256 is
`c1e62be60735e71a82699bb7286043c6cb8b525a70f838d1fc81f10bc83f40b1`.
The root-private `bws-artifact.json` records provenance. The native CLI version,
argument schema and executable custody guard were checked. The operator installed
the machine token through the hidden terminal, and an exact-ID password retrieval
succeeded without displaying the value. Private file modes were verified.
The operator selected private Mattermost notifications linking to a private
authenticated approval page for each grant.
The operator chose a non-expiring, revocable Bitwarden service token; individual
agent grants retain independently enforced durations. A vault token lifetime is
not an agent session lifetime, and monthly service-token replacement is not part
of the intended workflow.

The first interactive bootstrap failed before the first prompt because Python's
buffered `r+` update mode cannot open the non-seekable `/dev/tty`. The prompt
stream now uses write mode while getpass owns its separate read handle. A root
Linux controlling-PTY regression drives the actual CLI with synthetic inputs,
checks that no values are echoed, checks private output file modes and confirms
that mismatched token confirmation creates no credential files. Fixed refusal
diagnostics now retain their safe reason rather than always suggesting a partial
installation. Terminal-mode restoration remains enforced on every exit.

The installed `scripts/openclaw_access_grants.py` ledger and
`scripts/openclaw-access.py` service separate authenticated assistant requests
from owner decisions. Requests name an operator-registered account, a scope
allowed by that profile, a duration of 1–1440 minutes and an idempotency key.
The finite scopes are `search`, `read`, `prepare_booking` and `prepare_purchase`;
each website allows only its reviewed subset. Trusted configuration supplies
the shared `openclaw` identity; callers cannot choose an actor. Native policy
grants `homecompute_access_request`, `homecompute_access_status`,
`homecompute_site_run` and `homecompute_site_status` only to `travel` (Lyra)
and `shopper` (Vega). This role restriction is not a separate cryptographic
identity. None of these tools can approve access, retrieve secrets, view the
private sign-in screen, book or pay.

The canonical owner page is `https://mattermost.home.arpa:18814/`, on the
admitted home network and through Tailscale when away. Restricted DNS for
`home.arpa` already points to `192.168.30.1`, and the `192.168.30.0/24` subnet
route is already approved. Away from home, the device must connect to Tailscale
and use that DNS and route. The device must also trust the existing home private
CA certificate. Both routes use the same hostname and existing Mattermost web
sign-in cookie; logging in through the native Mattermost app alone does not
create the browser cookie. New notifications contain one review link.
The previous `https://home-core.tail479ad.ts.net:18814/` URL remains supported
with that hostname's existing Mattermost sign-in cookie.
Sessions are checked against Mattermost on every request and must match the
configured owner ID; bot accounts and Basic/bearer fallback are rejected.
There is no new approval password. Decisions require an exact origin, the
immutable request digest and a single-use action-bound CSRF challenge. Active
grants can be revoked, and expired/denied/revoked grants cannot be revived.
Mattermost notifications go only to the verified owner/delivery-bot private
conversation. Notification delivery has durable deduplication; uncertain sends
are not retried automatically. Replies in Mattermost do not grant access.

`scripts/setup-openclaw-access.py` installs attached NixOS units for the
loopback backend, dedicated HTTPS Caddy proxy and certificate-renewal watcher.
It never modifies the primary Mattermost proxy. The HTTPS listeners bind only
the host's specific LAN/tailnet addresses, with dedicated firewall admission.
The backend has no capabilities, no request logging, no external IP access
except Mattermost's inspected application /24 (needed after Docker DNAT), and
filesystem denial of the vault directory, Mattermost bootstrap/admin state and
OpenClaw state. It holds the notifier bot credential, exact owner ID and a
separate root-only browser-control key. That control key cannot match the
request, operation or native MCP identities and is never copied to the core adapter. The adapter holds
separate assistant request and finite-operation credentials. Neither service
receives the Bitwarden token or Hotels.com password. Caddy mounts only the leaf
certificates and TLS configuration; the LAN CA private key is not mounted.

`scripts/setup-openclaw-access-native.py` applies only the four finite
travel/shopper grants after native schema validation and an exact source-config
hash check, keeps a
private backup and verifies protected configuration sections are unchanged.
The core adapter's pre-change source and attached unit are backed up root-only
under `/opt/homecompute/openclaw-credentials/adapter-backup-*`. The access
installer can reconcile its own units; after a Mattermost application-network
change, rerun it to inspect and update the narrow network allowance.

An earlier native travel-agent request/status smoke test recorded a five-minute
search request with a verified Mattermost delivery receipt and left it pending
for the owner. HTTPS sign-in, owner access, anonymous and bot rejection, and the
service's hidden vault mount were checked live. The four-tool native policy is
now applied to travel and shopper; managed gateway discovery refresh remains
pending. That earlier request/status check does not verify the new tool inventory.

**Not yet verified:** signing in to the real Hotels.com account and running an
authenticated Hotels search. A pending private 15-minute read request is the
owner handoff. Adapter availability describes its reviewed implementation;
it does not claim that the account is signed in. Purchases and bookings require
separate approval, and no transaction finalizer is enabled. The existing public
research browser remains separate and restricted.

## Private registered-site worker (2026-10-27)

`scripts/setup-openclaw-sites.py` installs the generic framework under
`/opt/homecompute/openclaw-credentials`: the site service, executor, registry,
drivers, worker, owner-session worker and transport modules, `site-seccomp.json`
and the hostname policy. Root-private runtime and transport configuration lives
in `/var/lib/homecompute-sites/config`; finite operation metadata lives in
`/var/lib/homecompute-sites/operations`. Attached units are
`homecompute-openclaw-sites.service` and `homecompute-openclaw-site-egress.service`.
The assistant-facing service binds only `127.0.0.1:18816`.

The trusted registry is `/var/lib/homecompute-access/config/sites.json`.
Profiles specify exact domains, allowed scopes/actions, bounded operation
parameters, reviewed selectors and explicit network rules. Another website
requires its own root-reviewed profile and private Bitwarden account mapping.
`login_mode` is either `automatic` or `owner`; old profiles without the field
remain automatic. Qualification describes the reviewed implementation for that
mode, with a separate deployed-worker readiness check. Models cannot supply URLs,
selectors, credentials, browser code, login modes or profile overrides.

Hotels uses owner mode with the fixed `https://da.hotels.com/login` entry point.
The installer marks this mode available only after a real headful Chromium
sandbox fixture succeeds; it does not fetch credentials or sign in to Hotels.
A grant starts a private, memory-only browser session that lasts at most until
that grant expires or is revoked. Cookies and authenticated browser state stay
inside that session's disposable worker, separate from the public research
browser. The owner opens the private page through their existing owner-only
Mattermost cookie authentication. A distinct root-only backend key protects
start, Fill, Ready, close, viewer assets and the VNC socket; the model's request
and operation keys cannot use those routes.

The owner's Fill action retrieves the exact mapped Bitwarden credentials only
under an active grant and fills a document-bound field at the registered HTTPS
origin through a private process pipe. It never clicks Continue or submits
sign-in. The owner handles One Key prompts, MFA, legal terms, CAPTCHA and account
recovery directly on the private canvas. Ready is an explicit owner attestation,
not a guessed automatic login result. Neither this chat nor any model tool can
view the canvas, receive its images, type a password or request a verification
code. Passwords are absent from command arguments, environment variables and
host files.

On phones, select a field in the private screen and choose Show keyboard. The
owner-only input control forwards keys over the existing viewer socket and
clears its temporary buffer after input, on disconnect and when leaving the
page. It never sends typed text in an HTTP request, form, log or model tool.
Fill reports zero, one or two filled fields. Email-first sign-in may require
Continue followed by another Fill action. Visible verification prompts do not
prevent filling eligible email/password fields, but codes, recovery fields,
consent and submission remain entirely under the owner's control.

For sign-in failures, the trusted operator can request a bounded network-health
report through the root-only browser-control API during owner sign-in. It
contains domain categories, resource types, HTTP status, MIME categories and
fixed network-error codes. Unknown hostnames lose their subdomains. Cookies,
full URLs, headers, page contents and image data never enter this report. It is
not available through native agent tools and is cleared at Ready or close.

MFA codes are entered only into the private website screen; push approvals stay
on the owner's trusted device. Codes, recovery codes and authenticator seeds
must never be sent to the agent or Mattermost. A detected challenge during an
operation pauses queued automation and returns the session to private owner
control. Ready refuses a visible challenge; the owner completes verification
and explicitly resumes. This handoff does not extend the grant or bypass
revocation, and does not authorize a purchase or booking.

Owner-session containers run without root or capabilities, with a read-only
filesystem, temporary memory storage, no host profile mounts and no published
ports. VNC port 6080 is reachable from the host only as UID 0. Two fixed GUI slots
can reach only their own exact-domain TLS proxy: GUI `.20` to proxy `.10`, and
GUI `.21` to proxy `.11`. Sibling sessions and private destinations are blocked;
only established reply traffic returns to the host. The proxy pins public DNS
answers. The approved owner page relays the authenticated viewer; the browser
port is never exposed directly to the LAN, tailnet or model.

After Ready, the same private session can run finite registered read/search or
preparation operations under readonly network policy; no financial finalizers
are installed. `homecompute_site_run` otherwise returns `human_sign_in_required`; the model
receives only fixed state fields and a bounded result count, never credentials,
cookies, page text, screenshots, sign-in URLs or viewer access. Automatic-mode
sites retain disposable per-operation workers. Each operation is bounded to 45
seconds. Grant checks run before private credential retrieval, browser actions
and results, and during the session lifetime. Expiry, revocation or a changed
grant closes the worker and suppresses its result. Installer/profile changes
invalidate old pending and active grants before publishing new capabilities.

A credential-free public Example.com fixture passed against the installed
private viewer: vendor assets returned 200, the VNC WebSocket upgraded with 101,
and the headful Chromium sandbox ran. Revocation closed the fixture in about
200 ms. This used the actual root unit's `UMask=0077`, empty capability set,
`ProtectSystem=strict`, private-range IP denials and explicit browser-subnet
allowance. These checks qualify the private GUI mechanism, not Hotels account
sign-in or authenticated search. Real-browser tests have explicit dependency
and opt-in gates; a skipped test is not evidence of a working browser.

The owner-only `/transactions/review/<uuid>` page displays an exact account,
payee, amount/currency, items, terms and expiry, bound to an immutable digest and
one-use CSRF challenge. No transaction finalizer is qualified, and no assistant
endpoint can create or claim a transaction. The framework cannot currently buy
or book. Unknown external outcomes require operator reconciliation rather than
a retry.

## Earlier installation and recovery record

The following records the original installation and unfinished activation during
the 2026-10-10 recovery, rather than the current browser permissions above.

The headless browser is installed on the agents guest. A bounded CLI helper and
the bundled `browser-automation` skill are also installed in the OpenClaw
workspace. Tool activation is **unfinished**: a native skill-management command
exceeded the former 2 GiB limit and left OpenShell 0.0.116 in `Error`.
The operator approved replacement and recovery on 2026-10-10. The native runtime
has been restored with an 8 GiB hard limit in the 24 GiB agents VM, preserving
workspace and conversation state and newly issued native authentication.
The disabled browser and denied exec configuration are retained until native
tool receipt qualification is completed. Only the built-in read-only
`session_status` tool is callable during recovery. Denying every member of an
explicit allowlist makes the native runtime reject turns before inference;
memory, write and external tools remain denied.

Private OpenClaw state and consistent SQLite gateway backups are retained.
The communication adapter is supervised on home-core; the Telegram receiver's
cursor and admission safeguards survive process restarts. Hermes remains on
gateway 8080 with an 8 GiB limit.

## Installed components

| Component | Scope | Verified |
| --- | --- | --- |
| Chromium 154.0.8037.92 | Dedicated headless profile and authenticated CDP endpoint | Public page navigation and title via authenticated WebSocket |
| Squid 6.13 | Public HTTP/HTTPS egress for the browser | `example.com` succeeds; private and metadata addresses return 403 |
| `homecompute-cli` | Fixed operations inside the OpenClaw workspace | Native version checks and `calculate '6*7'` returned 42 |
| `browser-automation` | Bundled skill copied to the workspace | File exists in the private backup; model use remains unqualified |
| `read`, `exec`, `browser` | Prepared finite tool grant | Native config dry-run passed; grant not applied |

The CLI helper accepts `versions`, `calculate EXPRESSION`, `files [DIRECTORY]`,
`digest FILE` and `json-format` (JSON on stdin). Its arithmetic parser cannot
execute Python; file operations reject parent traversal and symlink escapes.
The planned native exec allowlist contains only the exact helper executable,
with no automatic skill executable grants or general shell/interpreter grants.
Existing coding and infrastructure actions continue through the broker.

## Browser isolation

Sources are under `deploy/openclaw/tools/`. The installed immutable local image
is `sha256:2328d604f0956fd20475549b3cd000900a9c39ac3671e850ba7b61b8c8e14f41`.
It was built from the pinned Debian base in the Dockerfile and signed package
repositories. Rebuilding that Dockerfile can produce newer package versions;
record and qualify the resulting image before deploying it.

The browser container runs as UID 10001 with a read-only root, dropped
capabilities, no new privileges, a dedicated profile volume, one CPU and 4 GiB
RAM, with no additional swap allowance. Since Hermes retirement on 2026-10-27,
the approved guest allocation is 16 GiB: 8 GiB for OpenClaw, 4 GiB for the browser
and 4 GiB for guest services. OpenClaw has a two-CPU ceiling within the four-vCPU
guest; the browser retains its one-CPU ceiling. The browser's separate egress
proxy and ingress relay retain their 128 MiB and 64 MiB limits. Browser containers
use `unless-stopped` restart policies, and Docker preserves an updated memory
limit across their restarts. Compose and the standalone installer both specify
the same 4 GiB browser limit for future recreation.

Chromium uses `--no-sandbox`; container and network restrictions provide
the isolation here. Its only external path is the separate proxy. The proxy
denies private, loopback, link-local, metadata and reserved destination ranges.
The browser has no OpenClaw/model credentials or host workspace mounts.

A small ingress relay publishes only `172.18.0.1:18800` on the Docker bridge.
The actual CDP handler requires a separate random token for both discovery and
WebSocket requests. Token bytes stay in the private operator directory and,
after activation, OpenClaw's protected configuration. They must not appear in
logs, reports or committed configuration. The scoped NemoClaw policy permits
only Node access to that exact bridge endpoint, with fixed private IP pinning.

This setup supplies headless browsing. A visible browser would require a
separately configured display-capable host/profile. OpenClaw supports managed
and remote browser profiles; see the [browser documentation](https://docs.openclaw.ai/tools/browser).

## Installation and activation

For a new browser installation, stage the tools directory and build its image
on the agents guest. Docker Compose is not installed there, so the current
installation uses `scripts/setup-openclaw-browser.py --directory STAGING
--image-id sha256:IMAGE_ID`. The installer refuses to replace existing browser
containers. `compose.yaml` describes the same deployment for a host with
Compose available. Preserve the private token and profile when updating.

Apply the browser policy through the supported scoped lifecycle:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw policy add \
  --from-file STAGING/tools/browser-policy.yaml \
  --trusted-private-host 172.18.0.1 --yes
```

The policy was applied and survived the VM restart. Recover the failed native
sandbox before proceeding. Preserve its saved workspace, sessions, skills and
plugin state, while retaining the replacement runtime's newly generated gateway
authentication and native ownership records. Do not copy an old auth token over
the managed replacement's auth or edit OpenShell's phase database.

After recovery, upload the CLI helper to the workspace's `bin` directory, the
activation script outside the readable workspace, and the dedicated browser
token to `/sandbox/browser-token`. Run `enable-openclaw-tools.py` through
NemoClaw exec for a native schema dry-run. Use `--apply` to back up the current
configuration and approvals, install the bundled skill, and apply the finite
tool configuration and exact executable approval. The script limits management
CLI V8 heaps to 384 MiB within the existing sandbox resource budget. It refuses
unexpected existing agent approvals. Do not blindly repeat an interrupted apply:
inspect the saved config and approvals first.

Restart with `nemoclaw agent-openclaw gateway restart --quiet`. Then verify
actual model turns for reading a skill, the allowed CLI helper and browsing a
public page; verify denial of arbitrary programs and workspace escapes. Native
CLI browser status can contain the authenticated URL: project safe fields
instead of copying its full output into a report.

The communication receipt handler is prepared to accept completed browser/exec
turns only with matching session/run identifiers, the fixed model route and a
visible terminal receipt. Unknown outcomes remain paused and are never retried.
Fixture tests passed, but real native browser/exec receipt qualification remains
pending. Resume the adapter and receiver only after those checks, using the
privately saved exact process arguments without resetting their state.

## Recovery findings

A guest reboot first stopped gateway 9123. Scoped start/recover could not restore
the host gateway, and completed onboarding was not resumable. Supported
non-interactive onboarding **without** `--fresh`, `--recreate-sandbox` or `--yes`
restored the same sandbox and route successfully.

During tool activation, the native skill-install command then caused an OOM
kill. OpenShell now rejects start because the phase is `Error` rather than
`Stopped`, and rejects stop because it is not `Ready`. Supported rebuild also
aborted while inspecting MCP state; it did not replace the sandbox. Restarting
the same Docker container did not repair OpenShell's phase, so that container
was stopped again. No phase database edits, runtime upgrade, Docker storage
changes, test Telegram messages or Hermes lifecycle mutations were performed.

See `openclaw-tools-validation.json` for the sanitized evidence. Unattended
gateway reboot supervision remains unqualified.
