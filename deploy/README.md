# Deployment artifacts

Deployment files are split by stable node role:

- [`compute-node/`](compute-node/README.md) contains the first inference
  service definition.
- [`services-node/`](services-node/README.md) contains the common cloud-init
  baseline for provisioned service VMs.

These artifacts are invoked by scripts in [`../scripts/`](../scripts/README.md)
and depend on validated external configuration. They are scaffolding for the
documented phase gates, not complete production stacks.
