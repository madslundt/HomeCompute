# ADR-017: `ai-services-01` is the consolidated application host

## Context

The physical inventory is three machines, not the four or five the
documentation assumes:

1. a Home Assistant appliance running HAOS, which also hosts the existing n8n
   add-on;
2. `ai-compute-01`, the GB10 inference appliance;
3. `ai-services-01`, the GMKtec K15.

ADR-014 originally placed automations, personal agents, and toolbox workloads
on this same K15 as role-separated Proxmox VMs. ADR-016 superseded ADR-014 for
provisioning, replacing Proxmox and VMs with NixOS and Docker Compose, but its
decision covers only the trusted gateway. It did not restate where the
automation, agent, and toolbox workloads live.

That substitution was incidental, not argued. ADR-014 had explicitly evaluated
and rejected shared-kernel isolation — "Use LXCs: efficient, but
nested-container and kernel-sharing trade-offs are not worthwhile for this
first security boundary" — and equally rejected combining the trusted gateway,
durable automation, and untrusted tool workloads into one failure domain.
ADR-016's context, decision, and alternatives concern provisioning tooling
only and never revisit that reasoning. This ADR therefore reverses a boundary
that was chosen deliberately, and should be read as such.

The remaining documents therefore still describe a separately qualified
application host that was never acquired: `docs/current-state.md` reuse
boundary, `docs/architecture.md` section 2.1, `docs/setup-guide.md` scope,
`deploy/control-plane/README.md` trust-domain table, and URS-PA-019. The
dependency graph tracks that host as an unresolved critical input whose absence
keeps Hermes a demo.

A fourth machine is not going to be purchased for this platform.

## Decision

`ai-services-01` is the single application host. It runs the trusted gateway
Compose project, the migrated n8n automation workload, and later the Hermes
personal agent sandboxes, as separate Compose projects on one NixOS host.

This replaces host separation with container separation. Every consolidated
workload must have:

- its own Compose project and Docker network, with no route to the gateway's
  `internal` state network;
- non-root containers, dropped capabilities, and no Docker socket mount in any
  service, including schedulers;
- a distinct `/srv/state/<workload>` subtree that the other workloads' runtime
  users cannot read;
- a distinct sops secret group, so an agent sandbox cannot read gateway,
  database, or compute credentials;
- a distinct revocable LiteLLM virtual key per consumer identity, as URS-AI-010
  and URS-SEC-003 already require;
- explicit memory and CPU limits, so an agent or workflow cannot starve the
  gateway.

Agents and automations reach inference only through `https://ai.home.arpa`.
Neither reaches the private compute link, which stays restricted to LiteLLM.

n8n moves off the HAOS add-on to this host. Home Assistant keeps ownership of
tools and physical actions and remains on its own appliance; that boundary is
unchanged by this decision.

### Staged kernel boundaries

Container separation is the boundary now, not the boundary forever. The target
is three kernels on the one machine, reached in stages rather than built up
front:

| Kernel | Workloads | Reason |
| --- | --- | --- |
| Host (NixOS) | Docker Engine, sops-nix, Tailscale, firewall, backups, and the gateway project | The asset being protected, and the only attachment to the private GB10 link |
| `automation` microVM | n8n, MCP servers, browser workers, queue and workflow state | Browser workers render arbitrary web content and n8n code nodes execute JS |
| `agents` microVM | Hermes `owner`/`partner`/`family` as three containers, plus scoped memory | Prompt injection makes model behavior attacker-influenceable while it holds tool credentials |

A fourth `toolbox` microVM is added only if that workload is ever built; it
runs unreviewed code by design and must not sit beside agent credentials.

The three Hermes sandboxes share the `agents` kernel. The boundary between
household members is a privacy boundary, not an adversarial one, and URS-PA-018
already records that sandbox separation is not protection from the
administrator. Separate containers, users, state subtrees, and credential
bundles are the correct control there.

Stage 1 is the current state: one kernel, containers only, with the controls
above enforced by `scripts/validate-repository.sh`. Stage 2 adds the `agents`
microVM and is a precondition for Hermes handling any real personal data or
untrusted input. Stage 3 adds the `automation` microVM when browser workers
appear. Hermes is deferred, so stage 1 is sufficient today.

## Alternatives

- Acquire a fourth application host: rejected because it is not available and
  will not be purchased. It is therefore also rejected as the recorded
  remediation; a remediation nobody will execute is not one.
- `microvm.nix` KVM guests on this machine: accepted as the remediation instead,
  because it restores a separate kernel without a separate machine. Deferred
  rather than adopted now: it is a community flake, another pinned input that
  can fail a rebuild, and another disk image to back up. Its own cost is
  unjustified while only the gateway and n8n are co-located.
- Put the gateway in a microVM as well, leaving a minimal hypervisor host:
  closer to ADR-014's original design, and it would mean a VM escape no longer
  lands directly on the gateway. Rejected as a marginal gain — host root reads
  every guest's memory and disks regardless, so this only shrinks the host
  surface by Docker Engine and three images, at the cost of rebuilding the one
  component that already works. The latency argument sometimes made for this is
  not load-bearing: virtio overhead is microseconds against a time-to-first-token
  measured in hundreds of milliseconds.
- Keep n8n on HAOS: viable, but it leaves automation state on an appliance
  whose lifecycle is tuned for home control, and splits scheduler ownership
  across two hosts against URS-PA-012.
- Keep Hermes unbuilt until a host exists: rejected because it defers the
  personal-agent layer indefinitely for a boundary that container isolation
  substantially, if not completely, reproduces.
- Run agents on GB10: already rejected by ADR-001 and ADR-013 for appliance
  rebuildability and inference contention.

## Consequences

The platform fits the hardware that exists, and the unresolved application-host
dependency is closed rather than carried indefinitely.

The security boundary is genuinely weaker than the one URS-PA-019 specified. A
container escape from a Hermes sandbox reaches the same kernel as the gateway,
its PostgreSQL instance, and the sops-materialized control-plane secrets.
Container isolation is not equivalent to host isolation, and this decision
should be read as accepting that risk knowingly, not as claiming equivalence.
The compensating controls above are mandatory rather than advisory because they
are the only remaining boundary.

Those controls are not equally load-bearing, and recording them as one list
obscures that. Removing the Docker socket and running non-root with dropped
capabilities is what actually raises the cost of an escape. Per-project
networks and per-consumer LiteLLM keys limit the far more likely compromise, in
which an agent is manipulated into connecting somewhere rather than escaping
anywhere. The separate `/srv/state` subtree and sops group largely do not
survive a root-level escape, because host root reads both regardless; they
defend against a non-root compromise and against misconfiguration. None of them
addresses the escape itself, which is why the staged kernel plan exists.

Deferring Hermes keeps the accepted risk small in the meantime. Until agent
sandboxes exist, the co-located workloads are a gateway and user-authored n8n
workflows, and the prompt-injectable component holding tool credentials is
simply absent. This is the largest available risk reduction and it costs
nothing; the risk register is scored for that staged state and carries an
explicit re-score trigger.

Capacity is less pressing than first assumed. Model weights stay on
`ai-compute-01`, so the co-located services hold runtime rather than inference
memory: Caddy is negligible, LiteLLM and PostgreSQL are together a few
gigabytes, n8n and its database a couple more, and the sandboxes are agent
runtimes. Against 48 GB that leaves substantial headroom, and ADR-014's fixed
16 GB agent reservation was sized for VMs. The real exposure is CPU contention
and unbounded sandboxes rather than exhausting RAM, so the limits above must
still be set from measurement rather than assumption.

Availability also concentrates. A single host failure now stops the gateway,
automations, and agents together. Deterministic home control stays independent
on the Home Assistant appliance. Off-host encrypted backup and a tested restore
therefore cover more state and matter more than they did under ADR-016.

## Status

Accepted. Amends ADR-016 by defining workload placement it left unstated, and
amends ADR-013's placement of Hermes on a separate application host. Does not
change ADR-001; `ai-compute-01` remains inference-only.

Hermes is deferred by owner decision on 2026-09-04, so only stage 1 is built.

URS-PA-019 is rewritten to state the container-level controls, URS-PA-020 makes
the `agents` microVM a precondition for real data rather than an aspiration, and
URS-PA-021 requires the controls to be machine-enforced. The architecture,
setup guide, execution plan, implementation plan, design specification,
current state, dependency graph, both READMEs, and the control-plane
trust-domain table are reconciled in the same change.

`docs/research/nemoclaw-machine-placement.md` keeps its original conclusion as
dated evidence and carries an amendment note; it is not rewritten.

## Evidence

- `docs/adr/014-ai-services-node.md`
- `docs/adr/016-nixos-control-plane-host.md`
- `docs/adr/013-hermes-personal-agent-layer.md`
- `docs/requirements.md` URS-PA-019, URS-PA-012, URS-AI-010, URS-SEC-003
- `docs/dependency-graph.md` application-host critical input
