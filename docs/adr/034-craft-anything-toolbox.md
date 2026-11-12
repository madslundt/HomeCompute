# ADR-034: Isolate Craft Anything in a Home Core toolbox guest

## Status

Accepted for isolation. The dedicated toolbox guest is already declared in the
current source; installing or integrating Craft Anything in it remains gated on
release-specific qualification. This decision does not authorize live changes.

## Context

Craft Anything exposes its inspection and build engine through an MCP server
that uses stdio. Its tools can inspect targets, read and write project files,
and run build commands. Hermes supports local stdio MCP servers and per-server
tool filtering. OpenBot's installed MCP list is shared server-wide and cannot
grant a connection to one agent, so it is not a suitable integration point.

Hermes Owner already has personal state, household tools and credentials. The
Craft Anything build engine runs untrusted project code by design. Placing it
in that owner sandbox would put its MCP subprocess beside private data and
existing household authority. The existing agents guest also holds OpenBot
state and multiple agent runtimes. ADR-017 reserves a separate toolbox guest
for workloads that run unreviewed code.

The current Craft Anything release is early alpha. Its release notes and
capability descriptions are not target-hardware qualification evidence. The
upstream CLI also needs release-specific validation for Linux x86_64, Hermes
stdio startup, the exact tool-filter format, web inspection dependencies, and
build approval behavior.

## Decision

Use the existing dedicated toolbox KVM guest on `home-core`, separate from both
the agents guest and the NixOS host services. Any Craft Anything installation
must remain separate from Hermes Owner and OpenBot's shared MCP list.

The toolbox guest will:

- use its own virtual disk, state paths, operating-system principal, Hermes
  profile, and revocable LiteLLM key restricted to the local `automation-moe`
  model;
- contain only projects explicitly copied into a dedicated work directory; it
  receives no owner/HomeCompute credentials, household data, private mounts,
  Docker socket, or access to the agents guest;
- use a default-deny host firewall with public TCP 80/443 for selected web and
  dependency access, DNS through its host bridge, and only the exact host-only
  inference bridge needed for local model calls; private, link-local, tailnet,
  and compute networks remain denied;
- expose no application or management listener on the LAN. The operator enters
  through the existing SSH administration path; Hermes and the Craft MCP server
  run locally in the guest;
- keep Craft Anything in its default clean-room mode and expose a release-
  reviewed subset of tools. Deep inspection and arbitrary target files stay
  disabled. Build commands require a supervised, explicit approval path; no
  project-wide "always allow" grants or external publication are enabled;
- keep durable projects and Hermes state under a dedicated guest disk included
  in the encrypted application-state backup, with an isolated restore and
  rollback procedure before real user projects are admitted.

The guest and MCP integration remain synthetic/public-only until negative
network tests, execution-approval checks, restart persistence, backup/restore,
and a representative website/app build all pass. Craft Anything's upstream
early-alpha status remains visible in the workload runbook. Commercial use is
outside this decision; check the pinned upstream license and intended project
use before admitting any project.

## Alternatives

- **OpenBot shared server MCP:** rejected because every OpenBot agent receives
  the server-wide connection and the installed interface has no per-agent MCP
  grants or verified filesystem boundary.
- **Hermes Owner MCP:** rejected because Craft Anything's file and build tools
  would share a sandbox that holds personal state and household tools.
- **Craft Anything in the existing agents guest:** rejected because the guest
  already contains OpenBot data and other agent runtimes. Its guest-level
  network, disk, and kernel boundaries would be shared.
- **A new Compose project on the NixOS host:** rejected because command/build
  tools belong behind the dedicated KVM boundary described by ADR-017, rather
  than sharing the host's kernel and Docker control plane.
- **Remote HTTP MCP bridge:** deferred. Craft Anything documents stdio; adding
  an HTTP adapter would create an extra network service and authentication
  boundary without being needed when Hermes runs in the same guest.

## Consequences

This adds a guest lifecycle, network policy, runtime image/artifact pins,
credential identity, durable storage, and backup/restore work. It consumes host
memory, CPU, disk, and operational attention. The separate kernel contains
ordinary guest-level compromise better than adding a container alongside the
existing agents, but host root still controls guest memory and disks; it is not
a defense against a Home Core administrator or KVM escape.

The setup must not be considered complete until the current Craft Anything and
Hermes versions pass the defined integration and containment checks. A staged
guest declaration or successful install command alone does not establish
readiness.

## Evidence

- [ADR-017: consolidated application host](017-consolidated-application-host.md)
- [OpenBot agent setup](../openbot-agent-setup.md)
- [Craft Anything upstream repository](https://github.com/storytold/craft-anything)
- [Hermes MCP documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
