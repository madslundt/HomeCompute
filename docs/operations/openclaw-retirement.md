# Retire OpenClaw

The production target removes OpenClaw execution, its dashboards, and the
OpenClaw tool principal. Keep the agents VM, OpenShell, Hermes owner
sandbox, OpenBot teams and their embedded Hermes runtime. The latter has
its own profile and state.

## Before the change

- Record the active release and effective dashboard units on Home Core and the
  exact state of the OpenClaw sandbox in the guest. The native agent previously
  stopped under its own gateway on port 9123. Confirm its supervisor, shutdown
  unit, dedicated browser, and dashboard proxy remain stopped or disabled.
  Recheck the two guest Agent Office forwarders as well.
- Reconcile pending task records, active retries, standing access and browser
  grants, and delivered reminders. Preserve needed history as a scoped data
  export. Transfer the remaining work and test its delivery from the new
  owner. Stopping the household and location reminder services ends those
  delivery paths for new Hermes-created reminders too. Migrate or disable the
  corresponding scheduled reminder entry points before relying on them again.
- Obtain a fresh, independently restorable export of any irreplaceable data
  being retired. Keep the existing OpenClaw workspace, sessions, and local
  images until this check passes. A VM disk archive is not required by this
  retirement procedure. Treat state deletion as a later operation.
- Record authenticated read baselines for owner Hermes, OpenBot's retained
  principals, n8n workflow retrieval, and Home Assistant. When OpenBot's
  existing connection supplied an OpenClaw bearer, review that agent's needed
  tools and test the scoped replacement before removing the saved header.

## Core workers and credentials

Use an exact published review revision in
`/srv/homecompute/releases/FULL_SHA`. This working directory may differ from
the running host generation.

Plan the dedicated service stop:

```bash
sudo python3 scripts/retire-openclaw-core.py --revision FULL_SHA
sudo python3 scripts/retire-openclaw-core.py --revision FULL_SHA \
  --apply --expected-plan-sha256 REVIEWED_PLAN_SHA256
```

The script stops the OpenClaw access and site services, task feed and reminder
workers and removes only their known attached boot links. It holds the normal
deploy lock and retains effective service hashes, initial activity and exact
link targets in a root-private receipt. The script records the known
`50-restore-recovery.conf` hash for the access proxy. The override remains
dormant with the service stopped. A changed attachment or drop-in stops the plan.

Keep the shared adapter service and its existing account. It supplies role
authorization and finite tools to the native relay for Hermes and OpenBot.
Assistant context sync and workflow retrieval still rely on data and access
owned by that account. The finite machine-status and update-feed services
also supply shared monitoring evidence. Keep the n8n notification relay
and existing Hermes dashboard and speech services.

Use the existing guarded adapter transaction to withdraw the legacy native
bearer after the retained clients pass their read checks:

```bash
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --retire-openclaw
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --retire-openclaw \
  --apply --expected-plan-sha256 REVIEWED_MCP_PLAN_SHA256
```

The transaction restarts only the adapter, tests the retained credential
inventories, and requires the old bearer to receive HTTP 401. The associated
legacy native embeddings endpoint rejects the old bearer, and native
conversation entry points return 404. n8n workflow
retrieval uses its own service endpoint and separate LiteLLM embedding
credential. The shared native MCP relay uses TLS on the existing private
network. A fresh relay installation requires explicit Hermes owner admission
before issuing any initial credential.

## Front doors and verification

The reviewed source removes the IP dashboard from Caddy, disables the Core
dashboard proxy and Tailscale Serve units, withdraws the OpenClaw-hosted Agent
Office bridge, and removes both cards from Homepage. It removes
`homecompute_openclaw` from LiteLLM while preserving the other aliases and
their scopes. The Hermes dashboard remains at `agents.home.arpa/hermes/`.

Apply those declarations through the [Home Core release lifecycle](../homecompute-operations.md).
For a scoped change, follow the [dashboard retirement procedure](../../deploy/agent-office/README.md#retirement)
and apply the LiteLLM catalog change against its running mounted configuration:

```bash
sudo python3 scripts/retire-openclaw-gateway.py --revision FULL_SHA
sudo python3 scripts/retire-openclaw-gateway.py --revision FULL_SHA \
  --apply-plan-sha256 REVIEWED_PLAN_SHA256
```

The installer preserves the mounted model configuration and recreates LiteLLM
with the same image and reviewed config except for the retired MCP alias. It
keeps the original mount file for rollback and blocks the dedicated
`homecompute-agent-openclaw-synthetic-20261009` key. It verifies the key block
without printing its identifier. Use the original config path from the plan
to restore the previous gateway configuration and unblock only that key in
the LiteLLM admin UI if reactivating OpenClaw is approved.
Verify Tailscale port 18791 is absent, port 18796 still reaches Hermes, and the
standalone approval proxy no longer listens on port 18814.

Confirm the old IP dashboard, Agent Office root, OpenClaw catalog entry and
native bearer are unavailable. Recheck the guest Hermes and OpenBot
services, authenticated Hermes owner tools and a harmless scoped OpenBot
read, regular workflow retrieval and Home Assistant. Keep named shared
services in the inventory even if they still carry the OpenClaw name.
Reconcile the installed monitoring bundle with the source inventory after
the access proxy and site egress services have stopped.

If dedicated worker retirement needs to be reversed, use the receipt from
the selected release:

```bash
sudo python3 scripts/retire-openclaw-core.py --revision FULL_SHA \
  --rollback /var/lib/homecompute-openclaw-retirement/retirement-TIMESTAMP.json
```

Use `scripts/setup-hermes-mcp.py --revision FULL_SHA --rollback` separately
for the credential transaction. Roll back the front doors through their
owning release. Recovery does not resume an OpenClaw model run or replay a
notification automatically.
