# OpenClaw agent office

A local, read-only pixel office for the team declared in
[`config/openclaw-team.json`](../../config/openclaw-team.json). Agent names come
from that source; instructions, messages, channel IDs, tool arguments/results,
and session identifiers never reach the browser. The preview needs no npm
install, cloud service, database, credentials, or inference calls. Published access requires a separate operator password.
Requires Node.js 22+.

## Preview

From the repository root:

```bash
node deploy/agent-office/serve.mjs --demo
```

Open <http://127.0.0.1:18802>. `--port 18803` selects a different loopback port.
Demo mode is explicitly synthetic and does not query or change live agents.
Omitting `--demo` shows the canonical roster with unknown activity; a standalone
preview cannot observe a gateway in another process. Stop with Ctrl-C.

Select an agent in the roster or office to inspect its activity category, run
count, observation age, specialty, and recent transitions. Clicking a character
or its name in the side menu selects both and highlights the character with a
glow and floor ring. Characters have no overhead name tags. Filter by state or
pause motion. Reduced-motion preferences disable animation. Facing follows
their direction of travel.

The four directional atlases each contain three characters, two walking poses
per direction, and a planted standing pose for each profile. Their shared frame
size is 128×160 with a foot baseline at 154. The browser renders original pixel
frames rather than bobbing a static portrait. Asset provenance and layout
are recorded in [`walking-art.json`](public/assets/walking-art.json).

[`scene-layout.mjs`](scene-layout.mjs) owns room labels, floor destinations,
facing directions, corridors, and art-derived furniture bounds. Coordinates
anchor a character's **feet**. Atlas stands below the table and chairs; desks,
shelves, and counters are blocked. Mid-walk destination changes rejoin the
existing corridor graph. Invalid routes fail closed rather than moving through
furniture. Keep geometry and the background asset in step when editing the map.

## Roles and visual language

| Agent | Work area and objects | Responsibility |
| --- | --- | --- |
| Atlas | Coordination table, route papers | Delegate work and review specialist results |
| Luna | Calendar, planner, sticky notes | Personal planning, school, tasks, reminders |
| Nova | Library, reference books, research desk | Public research and cited evidence |
| Apollo | Code monitors, purple keyboard, hardware | Prepare coding requests and follow Codex jobs |
| Orion | Server racks, diagnostic monitor | Infrastructure, Home Assistant, maintenance |
| Vega | Shopping counter, bags, offers, scanner | Shopping lists and discount watches |
| Ceres | Supply shelves, jars, boxes | Household stock, storage, expiry records |
| Lyra | World map, globe, suitcase | Travel, restaurants, adventures |
| Hestia | Kitchen, recipe book, vegetables | Meals and recipes |
| Themis | Binders, document trays, paperwork | Household administration and deadlines |
| Plutus | Ledger, calculator, chart display | Supplied expenses, budgets, reconciliation |
| Gaia | Separate family garden, picnic and toys | Expressly shared family plans and tasks |

Locations are activity metaphors, not physical machines, real-world locations,
authority, or access grants. A character entering another work area does not
delegate a task or grant access. Gaia's visual area does not expose owner-private
task content to Family. This whole dashboard is an **operator-only** projection;
do not embed it in a shared-family channel or service.

The warm oak/navy pixel environment and role-specific characters are original
generated assets. The spatial activity idea is inspired by
[Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents). No upstream code,
sprites, or third-party furniture assets were copied. See
[`docs/adr/009-logging-policy.md`](../../docs/adr/009-logging-policy.md) and
[`docs/adr/015-personal-data-domains-and-memory.md`](../../docs/adr/015-personal-data-domains-and-memory.md)
for the privacy boundaries.

## Activity contract

The optional plugin registers `model_call_started`, `model_call_ended`, `before_tool_call`,
`after_tool_call`, and `agent_end` observations. These are based on the
[pinned OpenClaw hook contract](https://github.com/openclaw/openclaw/blob/v2026.9.9/src/plugins/hook-types.ts),
with [hook permissions documented upstream](https://docs.openclaw.ai/plugins/hooks).
The pinned runtime's model hook supplies native session/run identity without
an `agentId`; the observer derives only a known role from that trusted session
prefix and rejects a conflicting explicit agent. Request-scoped plugin
registrations share the gateway service's in-memory activity, including when
the native loader evaluates another module instance. Unstarted request services
cannot stop that gateway-owned listener.
The plugin reads only native agent/session/run/request identity for in-memory
correlation, fixed tool identifiers for categorization, and the end success
boolean. It never inspects conversation content or a tool's inputs/outputs.
Public snapshots omit native identity and raw tool identifiers.

Native auxiliary model requests may not emit `agent_end`. A matching
`model_call_ended` retires their provisional model phase without creating an
Idle/Error outcome. Outstanding model requests and tool continuations remain
active; only a native agent end establishes a completed agent outcome.

Unobserved characters stand separately near their role's floor area. Their
status stays Unknown; their placement does not invent activity or imply a
running task. This avoids overlapping the whole team at the entrance after a
gateway restart.

Completed runs move characters to distinct rest positions on the existing
floor graph, near their own work areas (Atlas uses the lounge). Those positions
also clear other roles' home spots, so an idle agent cannot overlap an unobserved
specialist. The detail panel names the corresponding area.

- **Working:** a fresh model/tool observation exists for an active native run.
- **Waiting:** the observed tool is `sessions_yield`; another working run still
  takes precedence when the same agent has concurrent work.
- **Idle:** a successful end was observed and no fresh run remains active.
- **Needs attention:** an unsuccessful end was observed and no run remains active.
- **Unknown:** no verified observation, missing identity, or an observation more
  than five minutes old. A heartbeat cannot renew a run's evidence.

Categories describe observed operations, not private task titles or outcomes.
For example, `task_status` means repository-work activity; it does not prove
that a Codex job is running or has succeeded. Unknown tool names become generic
thinking. Model-only work stays generic rather than inventing a task. Events
are bounded to 40 entries and five minutes, held only in memory, and disappear
on gateway restart. The browser polls every two seconds and marks agents unknown
when the feed fails. A gateway that does not emit these hooks remains unverified.

## Prepare and deploy

The private service URL is **https://agents.home.arpa/**. Homepage links to it
under **AI assistants**. The rollout has three independently verified parts:

1. The normal home-core release installs the NixOS Caddy-only bridge proxy and
   HTTPS route, and updates Homepage. It does not modify the running sandbox.
2. The guarded sandbox installer activates the observer from an immutable,
   digest-verified bundle. Guest units forward its loopback port to the bridge.
3. The network operator applies the tracked DNS record to the existing private
   resolver. DNS is not changed by a NixOS release or a Git push.

Follow [HomeCompute operations](../../docs/homecompute-operations.md),
[Git deployment](../../docs/git-deployment.md), and
[OpenClaw operations](../../docs/openclaw-operations.md). Use a reviewed commit
on `origin/master`, or an explicitly selected historical full SHA. Keep the
prior release, native configuration, and plugin bundle for rollback. No data
migrations are involved. A feature-branch push still needs review and merge.

### Private DNS

[`config/agent-office-dns.json`](../../config/agent-office-dns.json) declares:

```text
agents.home.arpa.  300  IN  A  192.168.30.122
```

Apply this **Host (A)** record in the existing UniFi private DNS settings. Compare
an existing exact-name record first: leave a matching record alone; preserve
sanitized evidence and resolve an unexpected target before replacing it. Do not
create a public record, port forward, or Funnel. The tracked declaration is the
source of truth; the router is its external application. UniFi's documented
[DNS-record procedure](https://help.ui.com/hc/en-us/articles/15179064940439-UniFi-DNS-Records-and-Local-Hostnames)
is Settings → Policy Table → Create New Policy → DNS in Network 9.4 (Policy
Engine → DNS in 9.3). Set the exact name, IPv4 address and TTL above. This source
contains no controller credentials and does not assume an undocumented API.

LAN clients must use the existing private resolver. Tailscale clients use the
existing `home.arpa` restricted nameserver and approved subnet route. Both use
the existing HomeCompute CA; no additional public listener is introduced.
Verify from each intended client network after applying DNS:

```bash
python3 scripts/verify-agent-office-dns.py --check
```

Without `--check`, that command only prints the desired record. A declaration or
successful lookup from one network does not prove the other network works.

### Operator credential

Keep a distinct password of at least 16 characters in the external secret store.
A private plaintext file can supply it to the verifier builder; the password is
never a command-line argument or log entry:

```bash
node scripts/provision-agent-office-auth.mjs --username owner \
  --password-file /absolute/private-password-file \
  --output /absolute/new-private-verifier.json
```

The input and output must be private regular files. Output refuses overwrite and
must stay outside the checkout. Use the existing authenticated operator transfer
to provision the verifier as `/sandbox/.openclaw/agent-office-auth.json`, mode
`0600`, owned by the native gateway runtime UID. Keep it outside all agent
workspaces and Git; preserve it across rebuilds or provision a replacement from
the external password. It contains a salted scrypt verifier. An absent, public,
or malformed verifier prevents the published observer from starting.

### Sandbox installation

Build from the selected release; the builder projects only public roster fields
from that release's canonical team declaration. It never copies agent prompts,
channel IDs, native configuration, or credentials:

```bash
node scripts/build-agent-office.mjs --output /absolute/new-private-office-bundle
```

Use the existing authenticated sandbox transfer to stage that exact bundle plus
the selected release's setup sources, preserving their repository layout
(`scripts/setup-agent-office.mjs`, `deploy/agent-office/config-patch.mjs`, and
`deploy/agent-office/auth.mjs`). Inspect the sandbox's actual native version,
configuration, selected OpenShell gateway/workspace and free port first. From
the staged selected-release sources **inside the existing sandbox**, plan:

```bash
node scripts/setup-agent-office.mjs --bundle /absolute/staged-office-bundle
```

The plan prints only the bundle/config digests, protected installed path, and
additive plugin changes. Review those exact values, then use the guarded apply:

```bash
node scripts/setup-agent-office.mjs --bundle /absolute/staged-office-bundle \
  --expected-bundle-sha256 REVIEWED_BUNDLE_SHA256 \
  --expected-config-sha256 REVIEWED_CONFIG_SHA256 --apply
```

Apply requires root, validates bundle file hashes and credentials, installs an
immutable version, uses native config validation, retains a private config
backup, and checks the applied settings. It does not restart the gateway.
Native config validation and writes run as the existing gateway UID/GID 998;
only immutable bundle installation and its backup require root. This retains
runtime ownership when the native CLI atomically replaces its config file.
Run the existing gateway restart lifecycle after review. Hook compatibility remains
a live qualification gate: a gateway that does not emit the native hooks must
show unknown activity rather than synthetic work. Do not edit the safety plugin,
grant an agent tool, or change sandbox egress for this feature.

The builder's optional `--config /absolute/private-native-config.json` also emits
`config-patch.json` for independent review. Existing plugin paths/allowlists are
preserved, explicit denies remain effective, and existing office configuration
that differs from policy requires reconciliation. The upstream conversation-hook
grant is needed for `agent_end`; the observer ignores its message/error fields.

### Guest and host ingress

The checked-in guest units assume the existing `agent-openclaw` sandbox,
`nemoclaw-9123` OpenShell gateway, `default` workspace and `hermes-operator` user.
Verify these identities against the live registry before enabling them. The
foreground forward uses the supported [OpenShell forwarding CLI](https://docs.nvidia.com/openshell/v0.1.4/how-it-works/sandboxes/overview).
It owns only port 18802 and never adopts or stops the native dashboard forward.
Install these exact selected-release units **in the agents VM**:

```bash
sudo install -D -m 0644 deploy/agent-office/guest-forward.service \
  /etc/systemd/user/homecompute-agent-office-forward.service
sudo install -D -m 0644 deploy/agent-office/guest-proxy.service \
  /etc/systemd/system/homecompute-agent-office-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now homecompute-agent-office-proxy.service
```

Then, as the existing `hermes-operator` account with its native user-service
session, run `systemctl --user daemon-reload` and
`systemctl --user enable --now homecompute-agent-office-forward.service`.
Do not run these commands on home-core or introduce a second gateway owner.

The selected release's normal host deployment is:

```bash
./scripts/homecompute deploy home-core --revision FULL_SHA
```

The path is Caddy → `172.28.200.1:18802` → `10.77.20.2:18803` → the guest's
OpenShell loopback forward → the observer's `127.0.0.1:18802`. The NixOS firewall
admits only Caddy's fixed `172.28.200.2` source on the control bridge. Caddy accepts
approved LAN/tailnet clients and rewrites Host for the fixed loopback upstream;
Origin remains validated and every page/asset/API request requires the separate
operator password. A local preview refuses the publication marker, so connecting
Caddy to an unconfigured preview fails closed. No password enters Homepage.

The guest bridge proxy uses port 18803 because native OpenShell forwarding
checks port ownership across all interfaces before binding loopback port 18802.
Using the same number on the bridge would prevent native forwarding from
starting or recovering. No additional LAN listener is introduced.

### Verify and roll back

After an authorized rollout run `./scripts/homecompute status`, `services`,
`doctor`, and `drift`, then inspect the actual plugin bundle hashes, native
loaded-plugin configuration, guest units and both relay listeners. Check DNS,
trusted HTTPS and operator sign-in through `https://agents.home.arpa/` on both
approved networks. Unauthenticated UI, assets and `/api/office` must return 401;
foreign origins must fail, and the plaintext bridge must not be reachable from
LAN clients. Run synthetic tasks for two agents and confirm actual working,
waiting, concurrent/end/error transitions, without private task content in the
API. A matching revision marker is insufficient. Until these pass, record the
DNS, credential, guest installation and live observation checks as unverified.

For rollback, retain and select the prior host release. Restore the prior native
plugin paths/entry/allowlist through native config tools using the saved backup,
restart through the existing gateway lifecycle, and stop only the two office
guest units. Remove the exact DNS record only after checking that it is still
the managed value. Keep external credentials and prior bundles for recovery.

## Checks

```bash
node --test tests/agent-office.test.mjs
node --check deploy/agent-office/public/app.mjs
./scripts/validate-repository.sh
git diff --check
```

The focused tests use synthetic lifecycle events and a real loopback HTTP server.
They cover concurrent runs, expiration, content exclusion, identity rejection,
bounded event retention, request isolation, additive configuration, and
reproducible bundle generation. Geometry checks cover every role/activity/state
destination and corridor segment, including the table-placement regression.
Animation checks cover all four profiles and both leg phases for all 12 agents.
Browser verification must also cover agent
selection from characters and the side menu, selection highlighting, filters,
directional walking, motion controls, and narrow
viewports. Synthetic tests do not establish live gateway readiness.

## Scoped rollout with existing host maintenance overrides

When the normal host release is guarded by unrelated live image or Homepage
maintenance, preserve those markers and use the office's dedicated lifecycle.
`scripts/setup-agent-office-ingress.py` requires a root-owned clean checkout at
`/srv/homecompute/releases/FULL_SHA`, with that revision published on
`origin/master`. Its default is a read-only plan of the actual Caddy/Homepage
mounts and exact before/after hashes:

```bash
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-agent-office-ingress.py \
  --revision FULL_SHA
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-agent-office-ingress.py \
  --revision FULL_SHA --expected-caddy-sha256 REVIEWED_CADDY_SHA256 \
  --expected-homepage-sha256 REVIEWED_HOMEPAGE_SHA256 --apply
```

The installer builds only the canonical Nix proxy/firewall unit derivations,
retains their Nix GC roots and installs source-backed attached unit links. It
validates the candidate against the existing Caddy container's environment and
imports before adding only the office HTTPS/redirect blocks and Homepage card.
It preserves file-bind-mount inodes, retains exact private preimages and locks
deployment/automatic Docker maintenance. Caddy and Homepage are restarted with
their existing IDs, images, mounts, networks and other settings. Existing links,
native OpenClaw routes, retired-service choices, maintenance overrides and full
host revision markers are preserved. No full NixOS switch or VM restart occurs.
The guest units, operator credential and observer installation still use the
sandbox procedure above; this path does not substitute demo activity.

Private ingress receipts and rollback files are stored under
`/var/lib/homecompute-agent-office/ingress-*`. A failed write/restart restores
both configuration preimages. If a later health check fails, compare current
hashes with the receipt before restoring those exact preimages in place and
restarting the same containers. Stop only the office proxy/firewall and guest
units when retiring this workload. Retain its credential, native configuration
backup and immutable bundles until recovery is accepted.

A future normal NixOS release owns the same canonical units. Once their loaded
unit contents match, remove only the office's attached links and their
`multi-user.target.wants` link through this documented reconciliation, reload
systemd and verify the Nix-owned units remain active before retiring old GC
roots. Reconcile existing general maintenance overrides separately; a scoped
office rollout does not claim global host synchronization.

The office HTTPS admission includes both existing trusted LANs
(`192.168.30.0/24` and `192.168.10.0/24`) and the household tailnet. The scoped
installer can upgrade the exact previous office block to this policy; a custom
or broader existing office block still requires explicit reconciliation.

On the native plugin path, the roster uses `api.config.agents.entries` from the
verified OpenClaw plugin API. It projects only supported role IDs and bounded
display names. This includes the currently registered 12-agent team without
changing the team policy or copying instructions, tool grants, channel IDs or
credentials into the browser. Standalone preview/bundle generation continues
to use the canonical source roster.
