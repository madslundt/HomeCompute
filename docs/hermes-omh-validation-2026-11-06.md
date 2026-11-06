# Hermes OMH and dashboard verification — 6 November 2026

The existing Hermes frontpager card now opens
`https://agents.home.arpa/hermes/`, the authenticated native Hermes dashboard.
The separate Mattermost card remains available. Hermes 0.23.0 and OMH 4.0.6
passed owner Mattermost, browser and native restart checks on home-core's
agents VM. This is bounded rollout evidence, not an overnight stability claim.

## Applied source and ownership

| Concern | Published source / lifecycle |
| --- | --- |
| Native OMH installation, configuration and protected dashboard authentication | `019234655a1b53f83d190d70f90733db3c250fd5`, guarded `scripts/setup-hermes-addons.py` |
| Guest dashboard relay | Same revision, `scripts/install-hermes-dashboard-relay.py` |
| Core relay/firewall, Caddy route and frontpager card | `6a1b458628e809c6738f6d92922e187b18150fc8`, guarded `scripts/setup-hermes-dashboard-ingress.py` |
| Preserved native startup unit | `75975d2f7e342ed33865391d58d766dc5fcefc82`, `homecompute-hermes-personal.service` |

These dedicated lifecycles retain private configuration preimages and receipts.
The final core ingress receipt is
`/var/lib/homecompute-hermes-dashboard/ingress-1793967311438609831/receipt.json`.
The [runbook](../deploy/hermes/README.md#pinned-omh-and-dashboard-lifecycle)
defines installation, read-only probes and rollback. No broad host deployment
or OpenClaw state deletion was performed for this integration.

The native owner remains `hermes-personal-v23`, using image
`sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311`.
Hermes is pinned at `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`; OMH at
`8e9263c14c34834b203135409b49c89831878501`. OMH has an independent writable
venv and leaves the sealed Hermes runtime intact. NemoClaw 0.0.141 and
OpenShell 0.0.116 remain unchanged.

## Effective behavior and client checks

| Check | Observed result |
| --- | --- |
| Frontpager navigation | Clicking the existing Hermes card opened the native dashboard login; authenticated dashboard loaded |
| Authentication | Unauthenticated config API 401; login and authenticated config API 200; cross-site POST 403; HTTP/1.1 and HTTP/2 login passed |
| Native plugins | OMH enabled, 28 tools and 12 hooks registered, no plugin error; existing NemoClaw plugin retained |
| Local routing | All 18 OMH categories use `automation-moe` at medium reasoning; OMH and Hermes delegation concurrency both one |
| Inference | Actual local inference returned the synthetic verification marker |
| Mattermost | Owner DM invoked `omh_status` and browser tools and received a response; a second real reply passed after native restart |
| Browser | Actual public Example Domain navigation passed; private-address navigation was denied |
| Household MCP | 50 tools discovered; all seven existing read-only service probes passed after restart |
| Sealed packages | Native package-manager verification passed with no drift |
| Restart | Source-owned user service restart succeeded; dashboard cookie/session, plugins, local inference, Mattermost authentication and browser checks survived |

The owner-visible final Mattermost reply is retained in the private service:
[post-restart verification](https://mattermost.home.arpa/homecompute/pl/ggm8ezqajtgmdbkzk36jbezxhy).
Private payloads, tokens, screenshots and detailed native traces stay outside Git.
The dashboard account is `mads`; its generated credential is held only in
protected guest/operator files. Native password hashing, cookies, Origin/CSRF
checks and WebSocket tickets remain authoritative.

Existing deferred inference credentials, Mattermost providers, browser policy,
MCP grants and native `MEMORY.md` / `USER.md` were preserved. OMH external
memory and dreaming remain disabled because its current scope classifier does
not recognize personal Mattermost DMs safely. Automatic updates are disabled;
the pinned version is managed through source. Profiles remain deferred.
See the [source audit](research/oh-my-hermes-managed-integration-2026-11-06.md).

## Corrections validated during rollout

Native exec transfers are bounded below the gateway message limit and verified
by complete hashes. OMH configuration rendering indents YAML lists so its
parser accepts existing Hermes custom providers while preserving their values.

Caddy's admin API remains disabled. The guarded ingress installer therefore
uses a normal Caddy container restart and verifies Mattermost reconnects.
A controlled disposable proxy test reproduced an idle reused upstream socket
failure as EOF/502; disabling upstream keepalive on only the Hermes route
restored repeated successful logins. The production browser login and both HTTP
protocol probes then passed. This follows Caddy's documented
[upstream keepalive behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
No authentication bypass was introduced.

## Validation and remaining platform differences

The complete repository validator passed, including containerized Nix flake
evaluation. All six focused add-on tests passed with PyYAML available; four
existing ingress tests and 22 Hermes runtime tests passed. Published CI passed
for the final implementation. Documentation links and `git diff --check` were
checked separately for this report.

Final `status`, `services`, `doctor` and `drift` inspection found both hosts
reachable. Their broad release markers remain older than these dedicated
Hermes changes: core `7df6e792262ac330027b47b188eb9c156eaa68b8`, Spark
`469618169ca43659ffe5e7e3ac7ee3589160811b`. The existing maintenance marker
and unrelated source/live differences were preserved. This rollout does not
establish that either whole host is synchronized with master.

Core's platform health warning remains specific to NAS backup readiness.
Its recovery backup service returned exit 75 with
`main HTTP activity requires recovery preparation to defer`; the NAS check
and other observed platform readiness metrics passed. The last observed
completed encrypted Synology snapshot, `093a7dff9ce129319a459a625536b386a8dadef89f5e298a17a4bcbb6f42b30b`,
completed at 00:20:48 UTC on 6 November and predates this OMH installation.
The existing household restore qualification remains the recovery gate;
no backup was forced through active use, and no fresh final-state restore or
full VM reboot with OMH is claimed. Native service restart was tested.

The guest filesystem retained approximately 17 GiB free after rollout.
The prior native owner remains stopped and preserved for rollback.
