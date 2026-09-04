# Deployment artifacts

Deployment files are split by stable node role:

- [`compute-node/`](compute-node/README.md) contains the first inference
  service definition.
- [`control-plane/`](control-plane/README.md) contains the NixOS-hosted
  control-plane Compose workload.
- [`homepage/`](homepage/README.md) contains the source-controlled service
  dashboard for `home-core`.

These artifacts are invoked by scripts in [`../scripts/`](../scripts/README.md)
and depend on validated external configuration. They are scaffolding for the
documented phase gates, not complete production stacks.
