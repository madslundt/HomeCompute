# ADR-037: Isolate OpenBot infrastructure maintenance

## Status

Accepted for source implementation. Runtime admission remains gated on a clean
published Home Core release, a fresh agents-VM backup, and explicit setup of the
separate OpenBot operator server and its credentials.

The OpenBot maintenance scope is superseded by
[ADR-038](038-openbot-git-proposals-and-restarts.md). The isolation decision remains in force.

## Context

OpenBot's MCP connections are shared server-wide. Its regular team server has
multiple agents, shared files, and an unqualified Linux execution boundary, so
adding host maintenance tools and their bearer credentials there would grant
the capability to every agent on that server. OpenBot must not receive host SSH
keys, Docker socket access, package-manager access, or arbitrary commands.

HomeCompute already has a root-owned maintenance controller. It prepares exact
plans from trusted update metadata, validates target-specific commands, records
one-attempt receipts, and requires separate root approval for major, uncertain,
and repair plans. A private Unix socket gives the existing trusted adapter
access to list, prepare, inspect, and request execution of plans.

## Decision

Run a second OpenBot server named `home-core-operator` in the agents VM. Give it
a separate `/data` directory, Docker bridge, egress chain, and bounded resource
budget. Keep it in the same encrypted VM backup and lifecycle as the existing
OpenBot service. The existing team server receives no maintenance credential
or MCP server connection.

Expose a separate `openbot-maintenance` principal through the HomeCompute
capability relay. The principal sees only bounded host metadata and the
maintenance plan tools. It is restricted in code to `home-core` and `home-spark`
and to the controller's finite package and image selectors. It cannot see or
act on Home Assistant plans, submit arbitrary commands, approve plans, deploy
HomeCompute source releases, or manage models.

Create a separate LiteLLM key limited to `automation-moe` and the
`homecompute_openbot_maintenance` MCP server. Do not include this server in
`all-harness-mcps`. The owner configures one operator agent on the isolated
server and adds the distinct HomeCompute and LiteLLM credentials there. Server
separation is the access control because per-agent tool isolation is not
qualified in the existing OpenBot server.

The controller's standing authorization remains the only authority for
verified routine updates. Major, unknown, downgrade, fix, and uncertain plans
remain stopped for the existing exact-plan root approval and reconciliation
path. DGX OS, kernel, firmware, CUDA, NVIDIA drivers and Container Toolkit
remain vendor-managed. Model changes and HomeCompute code/configuration
deployments continue through their own reviewed lifecycles.

## Consequences

- OpenBot has no host SSH or Docker credentials.
- A compromised regular OpenBot team server does not receive the new principal
  or key.
- The isolated operator server shares its maintenance tools among its own
  agents. Keep one operator agent there until per-agent grants are qualified.
- Routine updates can have effects under the existing controller policy, so
  the operator server and both maintenance credentials require careful
  handling and prompt revocation when the boundary is in doubt.
- Adding a second server increases guest memory, CPU and backup requirements;
  its limits are bounded independently and must be observed after rollout.

## Rollout gates

1. Publish the reviewed source and deploy Home Core before guest reconciliation.
2. Verify a fresh completed off-host agents-VM backup and available guest
   resources.
3. Install the separate server and complete its owner sign-in; leave its MCP
   connection absent until the HomeCompute principal and LiteLLM key are
   independently verified.
4. Verify the seven-tool HomeCompute inventory, exact target/component
   restrictions, LiteLLM key server/group scope, and denial from the regular
   OpenBot server and unrelated model-only keys.
5. Exercise report and plan preparation first. Verify routine execution only
   against a harmless accepted candidate; verify major, unknown, Home
   Assistant, arbitrary command, expired plan, and replay cases are denied or
   require root approval.
6. Confirm actual host service state, receipts, and rollback path before
   enabling the operator agent for routine use.

## Evidence

- [ADR-030: OpenBot team server](030-openbot-team-server.md)
- [ADR-036: shared LiteLLM MCP catalog](036-share-lite-llm-mcp-catalog-across-harnesses.md)
- [OpenBot operations](../../deploy/openbot/README.md)
- [Infrastructure maintenance controller](../../scripts/infrastructure_maintenance.py)
