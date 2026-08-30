# Contributing to HomeCompute

HomeCompute is an early-stage infrastructure project. Contributions should
preserve its local-first security model, stable node boundaries, and gated
deployment approach.

## Before making a change

- Read the [documentation guide](docs/README.md), relevant architecture
  decisions, and the current [execution plan](docs/platform-execution-plan.md).
- Open an issue before a large architectural change, new runtime, new model
  family, or change to a trust boundary.
- Never include credentials, live environment files, private infrastructure
  exports, prompts, recordings, transcripts, personal data, or proprietary
  model artifacts.
- Use synthetic or explicitly sanitized fixtures for examples and evidence.

## Pull requests

Keep each pull request focused on one decision, gate, or operational outcome.
Include:

1. the problem and intended result;
2. the affected requirement, ADR, or phase gate;
3. security and rollback implications;
4. commands run and their results;
5. any target-hardware evidence that is still missing.

Update documentation with implementation changes. When a decision changes, add
or supersede an ADR instead of silently rewriting its history. Update each D2
source and its SVG/PNG renderings in the same pull request.

## Validation

Run the checks relevant to your change. The baseline static checks are:

```bash
bash -n scripts/setup-compute-node.sh scripts/setup-services-node.sh
shellcheck scripts/setup-compute-node.sh scripts/setup-services-node.sh
d2 diagrams/gb10-platform.d2 /tmp/gb10-platform.svg
d2 diagrams/gb10-installation.d2 /tmp/gb10-installation.svg
docker compose -f deploy/compute-node/compose.yaml config
```

Compose rendering requires safe test values for all required variables. Host
preflight, deployment, smoke, recovery, and performance tests must run only on
the intended systems with separately supplied configuration.

## Licensing

By intentionally submitting a contribution for inclusion in HomeCompute, you
agree that it is licensed under the
[Apache License 2.0](LICENSE), in accordance with Section 5 of that license.
Do not submit material you do not have the right to contribute.
