# Control-plane runtime split review

Reviewed: 2026-09-02

> **Implementation outcome:** the review findings were applied to the active
> scaffold. Redis and response caching were removed; PostgreSQL bootstrap and
> LiteLLM roles were separated; only TCP 443 is published; the installer now
> verifies the restricted Compose topology. ADR-016 subsequently moved host
> ownership to NixOS; the workload-boundary findings remain applicable.

## Recommendation

Do **not** treat Caddy + LiteLLM + PostgreSQL + Redis as an indivisible baseline.
The smallest defensible first deployment depends on the access model:

1. If several clients need independently revocable credentials, deploy **Caddy +
   one LiteLLM worker + a LiteLLM-dedicated PostgreSQL database**. This is the
   recommended baseline for the stated control-plane role.
2. If the first deployment really has one trusted caller and can accept one
   master credential, LiteLLM can run without a database, but it loses virtual
   keys, the Admin UI's database-backed management, spend tracking, and
   enforceable per-key budgets. That is simpler but is a materially weaker
   control plane, so it should be an explicit temporary mode rather than an
   accidental outcome.
3. **Remove Redis from the first deployment** unless either response caching is
   explicitly wanted or LiteLLM is configured with more than one worker or
   replica. Start with exactly one worker. Add a dedicated Redis/Valkey service
   before scaling workers.

LiteLLM documents PostgreSQL as a requirement for virtual keys, and its
single-machine Docker quickstart contains LiteLLM and PostgreSQL but no Redis.
Its no-database mode retains only the master key and omits virtual keys, spend
tracking, and UI model management. Redis becomes a coordination requirement
when `--num_workers` is greater than one or there is more than one replica; LLM
response caching is a separate, opt-in use of Redis. Sources: [LiteLLM virtual
keys](https://docs.litellm.ai/docs/proxy/virtual_keys), [Docker quick
start](https://docs.litellm.ai/docs/proxy/docker_quick_start), [running without
a database](https://docs.litellm.ai/docs/proxy/docker_quick_start#running-without-a-database),
[Redis requirements](https://docs.litellm.ai/docs/proxy/redis_requirements),
and [proxy caching](https://docs.litellm.ai/docs/proxy/caching).

## Runtime placement

| Placement | First deployment | Reason |
| --- | --- | --- |
| NixOS host | SSH, networking, Docker Engine, firewall policy, time synchronization, updates, host monitoring, storage, sops-nix, and backups | These are machine-wide control and recovery functions. |
| Docker Compose | Caddy, one LiteLLM process, PostgreSQL if virtual keys are required | These are replaceable application runtimes with an explicit lifecycle and narrow persistent state. |
| Deferred/separate trust domain | n8n, browser workers, agent/code sandboxes, Home Assistant | They add unrelated state, credentials, ingress, hardware/network access, or untrusted execution to a host holding gateway credentials. |

Keeping Caddy in Compose is reasonable while it serves only this gateway. It
needs no Docker socket or host networking: publish only its ingress ports, join
it to the edge network, mount the Caddyfile read-only, and persist `/data` and
`/config`. Caddy's data directory contains certificates and private keys and
must be writable and persistent. If Caddy later becomes a shared ingress for
several independently deployed applications, move it into its own edge Compose
project rather than coupling all applications to the LiteLLM lifecycle. Sources:
[Caddy Docker Compose guidance](https://caddyserver.com/docs/running#docker-compose)
and [Caddy data/config conventions](https://caddyserver.com/docs/conventions#file-locations).

There is no reason for Caddy, LiteLLM, PostgreSQL, or disposable Redis to run
directly as NixOS services. Conversely, host management daemons should not be pulled
into this application Compose project merely for uniformity.

## Network and published-port shape

The proposed two-network topology is correct and should remain small:

- `edge`: Caddy and LiteLLM only.
- `state`/`backend`: LiteLLM and PostgreSQL, plus Redis only when justified;
  mark it `internal: true`.
- Caddy must not join the state network. PostgreSQL and Redis must not join the
  edge network. LiteLLM is the sole bridge between the two.
- Publish no LiteLLM, PostgreSQL, or Redis ports. Compose `expose` does not
  create a host publication and is not an access-control mechanism; network
  membership is what controls container reachability.
- Publish only Caddy on an explicit host IP. Omitting the host IP binds all host
  interfaces. Docker also documents that published container traffic is
  diverted before ordinary UFW input filtering, so a rendered Compose check
  and Docker-aware firewall policy are both required.

`0.0.0.0` is unacceptable for **host-published** mappings by default. The
LiteLLM command may listen on `0.0.0.0:4000` *inside its private container
network* because no host port is published and Caddy must be able to reach the
container's network interface; this is a documented, narrow exception and is
not equivalent to `ports: ["4000:4000"]`.

Caddy normally needs TCP 443. TCP 80 is justified if HTTP-to-HTTPS redirect or
public ACME HTTP challenge is required. UDP 443 exists only for HTTP/3/QUIC and
can be omitted in the minimal deployment if HTTP/3 is not a requirement. For
the current `tls internal` configuration, public ACME reachability is not
needed, but clients must trust the private Caddy CA. Sources: [Compose network
isolation](https://docs.docker.com/reference/compose-file/networks/#internal),
[Compose port warning](https://docs.docker.com/reference/compose-file/services/#ports),
[Docker and UFW](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw),
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https), and
[Caddy HTTP protocols](https://caddyserver.com/docs/modules/http#servers/protocols).

These networks provide useful coarse isolation, not per-port policy or a
security boundary equivalent to a VM. A service attached to the non-internal
edge network still has outbound connectivity; a dual-homed LiteLLM container
also retains that egress. Do not infer egress filtering from the network names.

## PostgreSQL and Redis ownership

PostgreSQL should initially be **dedicated to LiteLLM at least at the logical
database and login-role level**, and preferably remain a dedicated container
and volume while this is a small single-host system. LiteLLM owns migrations
and stores security-relevant key/control-plane state. Sharing the same
application login, schema, database, volume, or Compose lifecycle with n8n or a
future service is not acceptable.

A later shared PostgreSQL server can be operated safely only as deliberate
database infrastructure: one database and non-superuser login role per
application, explicit `CONNECT`/schema privileges, separate credentials,
resource and connection limits, compatible maintenance windows, and tested
per-application backup/restore. PostgreSQL's ownership and privilege model can
support that separation, but sharing a server still shares failure, upgrade,
capacity, and administrator blast radius. Sources: [PostgreSQL roles](https://www.postgresql.org/docs/current/sql-createrole.html),
[database ownership](https://www.postgresql.org/docs/current/sql-createdatabase.html),
and [privileges](https://www.postgresql.org/docs/current/ddl-priv.html).

Redis should not be pre-provisioned merely because a future service might use
it. If LiteLLM later needs Redis, give it a dedicated instance or a dedicated
ACL user plus a verified key namespace. Password-only `requirepass` authenticates
as Redis's `default` user; it does not create least-privilege command or key
separation. Redis also warns that key-pattern ACLs do not constrain commands
such as `FLUSHALL`, so shared Redis requires command restrictions as well as a
namespace. Sources: [Redis ACLs](https://redis.io/docs/latest/operate/oss_and_stack/management/security/acl/)
and [Redis AUTH](https://redis.io/docs/latest/commands/auth/).

## Deferred services

- **n8n:** keep the existing instance where it is until its workflows,
  credentials, webhooks, database, encryption key, and backup/restore path are
  inventoried. It is a separate stateful application and may execute user code
  or credentialed workflows. If later migrated, give it its own Compose project,
  database/login, secrets, and ingress route. Redis is an n8n queue-mode scaling
  dependency, not a reason to share LiteLLM's cache. n8n recommends external
  task-runner mode in production so code execution is isolated from the main
  process. Sources: [n8n Docker Compose installation](https://docs.n8n.io/hosting/installation/server-setups/docker-compose/),
  [queue mode](https://docs.n8n.io/hosting/scaling/queue-mode/), and [task
  runners](https://docs.n8n.io/hosting/configuration/task-runners/).
- **Browser workers:** keep them off this credential-bearing host. Browser
  content is untrusted active code. Playwright's official container guidance
  requires a separate user and seccomp profile for untrusted sites; that is a
  workload-specific sandbox design, not something to append to the gateway
  project. Source: [Playwright Docker guidance](https://playwright.dev/docs/docker#crawling-and-scraping).
- **Agent/code sandboxes:** keep them in a VM or separate sandbox worker host.
  A normal Docker container is not an adequate boundary for arbitrary hostile
  code when the same daemon controls containers holding gateway and database
  credentials. This decision can be revisited only with a named sandbox runtime,
  threat model, resource limits, network-egress policy, and escape response.
- **Home Assistant:** retain Home Assistant OS on its existing appliance/VM.
  Home Assistant calls OS the recommended installation for almost everyone;
  moving it into this Compose project would add device discovery, IoT network,
  hardware access, and an unrelated availability domain. Source: [Home
  Assistant installation comparison](https://www.home-assistant.io/faq/ha-vs-hassio/).

## Security constraints

The stated prohibitions should be release gates: no Docker socket, no
`privileged: true`, no host networking, and no broad host bind mounts. None of
the baseline components requires them. Mount only exact configuration files,
Compose secrets, and named application volumes. Docker documents daemon control
as root-equivalent in practice because it can mount and alter the host
filesystem, recommends removing all capabilities except those explicitly
needed, and notes that a host-networked container can access host ports and
observe host traffic. Sources: [Docker Engine security](https://docs.docker.com/engine/security/)
and [Compose networking modes](https://docs.docker.com/compose/how-tos/networking/#change-the-network-mode).

The follow-up security/network review added these release gates:

- Use Docker Engine 28+ and Compose 2.33.1+. Docker 28 includes the fix for
  hosts on the same L2 segment reaching ports published to loopback, and its
  isolated bridge gateway mode removes an address from the internal state
  bridge. The daemon-wide default publication address is also set to
  `127.0.0.1` for newly created user-defined bridges. Sources: [Docker Engine
  28 release notes](https://docs.docker.com/engine/release-notes/28/) and
  [port publishing](https://docs.docker.com/engine/network/port-publishing/).
- A boolean "firewall confirmed" value is not evidence. The installer writes
  and then checks `DOCKER-USER` rules using conntrack's original destination
  match, as Docker requires after DNAT. The rules source-restrict LAN ingress,
  prevent Caddy from initiating arbitrary egress, and limit LiteLLM egress to
  the configured private compute IP/port. Source: [Docker with
  iptables](https://docs.docker.com/engine/network/firewall-iptables/).
- Bearer credentials and prompts require TLS to the compute endpoint. RFC 6750
  requires TLS for bearer-token use. Plain HTTP is a recorded exception only
  for a private address on a dedicated non-routed point-to-point link/VLAN;
  vLLM supports certificate, key, CA, and client-certificate requirements when
  TLS is terminated in the runtime. Sources: [RFC
  6750](https://www.rfc-editor.org/rfc/rfc6750.html) and [vLLM API-server TLS
  options](https://docs.vllm.ai/en/stable/api/vllm/entrypoints/openai/api_server/).
- LiteLLM must come from its signed GHCR non-root image family using a fixed
  semantic version and digest. Its current security guidance says GHCR images
  from v1.83.0 are cosign-signed and deprecates `main-stable`. Because a 2026
  report shows Prisma migrations failing on one recent non-root release, the
  stack redirects migration caches to writable tmpfs and sets
  `ENFORCE_PRISMA_MIGRATION_CHECK=true`; every selected release must still pass
  an empty-database and upgrade test before promotion. Sources: [LiteLLM image
  security](https://docs.litellm.ai/docs/proxy/docker_image_security) and
  [upstream migration issue](https://github.com/BerriAI/litellm/issues/34236).
- Caddy's internal CA is an online signing authority. Its data volume must be
  encrypted in backup, its root distributed only to approved clients, and
  smoke tests must use that root rather than disabling TLS verification.

## Concrete findings in the proposed files

1. **Remove the current Redis service and cache configuration for the first
   deployment.** `litellm_settings.cache: true` explicitly enables response
   caching; Redis is not passive coordination in the current design. Caching
   prompts/responses is also a data-retention decision that conflicts with the
   otherwise conservative logging posture unless it has a documented purpose,
   TTL, data classification, and deletion behavior.
2. **Fix the PostgreSQL application role before deployment.** In the current
   Compose file, `POSTGRES_USER=litellm` creates `litellm` as the cluster
   superuser, and LiteLLM then connects with that same credential. The official
   image explicitly documents that `POSTGRES_USER` has superuser power. Keep a
   separate bootstrap/admin role and create a non-superuser LiteLLM login that
   owns only its database/schema. Source: [Docker Official Postgres image
   environment variables](https://github.com/docker-library/docs/blob/master/postgres/README.md#postgres_user).
3. **Make the virtual-key operating path explicit.** Caddy currently permits
   only `/v1/*`, while key management uses endpoints such as `/key/generate`.
   Keeping management APIs off the client-facing route is good, but the runbook
   needs a loopback/admin-only provisioning and revocation workflow. Otherwise
   operators will distribute the master key, defeating the reason to carry
   PostgreSQL. Do not expose the entire Admin UI/API to the client LAN by
   default.
4. **Keep the two-network membership.** The present Caddy-on-edge,
   LiteLLM-on-both, database/cache-on-state layout is the correct coarse
   least-privilege shape. Rename `state` to `backend` only if that improves the
   project vocabulary; the security property is `internal: true` and the
   membership list, not the name.
5. **Reconsider UDP 443.** It is valid for Caddy HTTP/3, but it is not necessary
   for the first gateway. Either omit it or document HTTP/3 as the reason for
   the additional published protocol.
6. **Keep the explicit host-IP validation.** Rejecting `0.0.0.0`, starting on
   `127.0.0.1`, requiring confirmation before a LAN bind, and publishing only
   Caddy are sound. A public/wildcard bind should remain an exceptional design
   decision with an identified interface, source policy, and Docker-aware
   firewall verification.
7. **The current Compose file passes the requested gross-isolation review:** it
   has no Docker socket mount, privileged service, host network, or broad host
   filesystem mount. Preserve those invariants when adding backup and
   monitoring; do not solve backup by mounting `/var/lib/docker` into a general
   application container.

## Implementation gate

Before implementation, decide and record just three things: (1) whether
independently revocable virtual keys are a day-one requirement, (2) whether
clients connect only through loopback/VPN or directly over the LAN, and (3)
whether HTTP/3 is required. For the expected multi-client control plane, the
default answers should be **yes virtual keys, explicit LAN/VPN ingress, no
HTTP/3 initially**, yielding Caddy + one LiteLLM worker + dedicated PostgreSQL,
with Redis and all unrelated services deferred.
