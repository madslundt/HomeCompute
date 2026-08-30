# HomeCompute

**A local-first AI platform for private inference, automation, and agents.**

HomeCompute provides infrastructure, deployment scaffolding, and verification
plans built around two node roles:

- `ai-compute-01`: a rebuildable NVIDIA GB10 inference appliance;
- `ai-services-01`: an always-on Proxmox host for the API gateway,
  automations, tools, and durable application state.

This repository is a design and guarded deployment scaffold. It is not a
turnkey product and it does not claim that the target platform is installed or
production-qualified.

> **Project status:** Phase B review / Phase C0 preparation. Design approval,
> immutable artifact pins, target-hardware access, and the documented
> acceptance tests are still required.

![Target platform overview](diagrams/gb10-platform.svg)

## Goals

The project aims to provide one authenticated local AI boundary for coding,
Home Assistant, n8n automations, meeting processing, and optional personal
agents while preserving three rules:

1. consumers use logical model aliases rather than concrete model names;
2. durable data and application state stay off the rebuildable compute node;
3. private workloads do not silently fall back to cloud providers.

The first deployment candidate is
`nvidia/Qwen3.6-35B-A3B-NVFP4` served by a pinned vLLM container. It is a
benchmark candidate, not a selected production model. Model revision,
tokenizer, remote code, chat template, quantization, runtime image, parsers,
context, and speculative-decoding settings are qualified as one immutable
tuple.

## Architecture

| Layer | Responsibility |
| --- | --- |
| Consumers | Codex, Home Assistant, n8n, Meeting Assistant, and optional personal agents |
| API boundary | Authenticated TLS, request identity, logical aliases, routing, and explicit fallback policy |
| Compute node | Text, speech, and diarization inference only; no durable application state |
| Services node | Gateway, automations, tool execution, databases, backups, and later agent sandboxes |

Existing services remain available for rollback until replacements pass their
phase gates. See the [architecture](docs/architecture.md) for boundaries and
flows, and the [platform execution plan](docs/platform-execution-plan.md) for
the controlling build order.

## What is included

- guarded Bash setup tools for the compute and services nodes;
- a hardened Compose definition for the first compute-node candidate;
- a cloud-init baseline and Proxmox provisioning inputs for the services node;
- architecture decisions, requirements, risks, implementation plans, and
  verification criteria;
- editable D2 diagrams with rendered SVG and PNG outputs;
- dated research that explains model and runtime recommendations.

The repository does **not** contain an operating system installer, model
weights, container images, credentials, live configuration, production data,
or completed benchmark evidence.

## Prerequisites

Read the node plan before running either script.

### Compute node

- ASUS GX10 or another supported NVIDIA GB10/DGX Spark-class appliance;
- supported DGX OS with a working NVIDIA driver and container runtime;
- Docker with the Compose plugin;
- Bash, `curl`, `jq`, `openssl`, and standard Linux administration tools;
- exact image/model revisions, artifact provenance, and external secret files.

### Services node

- a reviewed Proxmox VE 9 installation on the intended x86 host;
- configured VM and snippet storage;
- LAN and private-compute bridges created during host installation;
- a public SSH key and operator-supplied network values;
- a verified SHA-512 digest for the Debian 13 cloud image.

## Getting started

1. Read the [documentation guide](docs/README.md) and approve the unresolved
   decisions in the [platform execution plan](docs/platform-execution-plan.md).
2. Follow the [services-node plan](docs/ai-services-node-plan.md) to establish
   the empty, recoverable application host.
3. Follow the [compute-node plan](docs/ai-compute-node-plan.md) and
   [setup guide](docs/setup-guide.md) to qualify the direct inference baseline.
4. Do not migrate consumers until the corresponding tests in the
   [verification strategy](docs/verification-strategy.md) pass.

The scripts deliberately separate initialization, validation, preflight, and
mutation. Their example configuration contains placeholders and will be
rejected until every required value is resolved.

### Compute-node scaffold

```bash
sudo ./scripts/setup-compute-node.sh init --env /etc/gb10-ai/gb10.env
sudoedit /etc/gb10-ai/gb10.env
sudoedit /etc/gb10-ai/secrets/hf_token

sudo ./scripts/setup-compute-node.sh preflight --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh validate --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh install --env /etc/gb10-ai/gb10.env --wait 1200
```

### Services-node scaffold

```bash
sudo ./scripts/setup-services-node.sh init
sudoedit /etc/ai-platform/services-node.env

sudo ./scripts/setup-services-node.sh validate
sudo ./scripts/setup-services-node.sh preflight
```

Review the generated configuration and the full node plan before running
`host-packages`, `create-template`, `provision`, or `start`.

## Repository layout

| Directory | Contents |
| --- | --- |
| [`config/`](config/README.md) | Operator configuration templates with safe placeholders |
| [`deploy/`](deploy/README.md) | Compose and cloud-init deployment artifacts |
| [`diagrams/`](diagrams/README.md) | D2 sources and rendered architecture diagrams |
| [`docs/`](docs/README.md) | Canonical design, execution, verification, and research documentation |
| [`scripts/`](scripts/README.md) | Guarded setup and operations entry points |

## Security model

- Never commit `.env` files, private keys, API keys, tokens, prompts,
  recordings, transcripts, personal data, or live infrastructure exports.
- Keep the first inference listener on loopback. A non-loopback bind requires a
  verified firewall or VPN allow-list.
- Use separate, revocable consumer credentials. Do not distribute an
  administrative gateway key.
- Keep production logs metadata-only. Request and response bodies, tool data,
  audio, and authorization values are excluded.
- Home Assistant remains the authority that validates and executes home
  actions; the model only proposes calls to an allow-listed tool surface.

See the [risk analysis](docs/risk-analysis.md) and
[security requirements](docs/requirements.md#security-and-privacy-requirements)
before exposing any endpoint beyond a test host.

## Validation

The static checks available on a development machine are:

```bash
bash -n scripts/setup-compute-node.sh scripts/setup-services-node.sh
shellcheck scripts/setup-compute-node.sh scripts/setup-services-node.sh
d2 diagrams/gb10-platform.d2 /tmp/gb10-platform.svg
d2 diagrams/gb10-installation.d2 /tmp/gb10-installation.svg
```

Host preflight, rendered Compose validation, smoke tests, benchmarks, recovery,
and acceptance tests require the target systems and separately supplied
configuration. A successful smoke test proves startup and a minimal Responses
request only; it does not qualify the full platform.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for project boundaries, pull-request
expectations, validation, and contribution licensing. Security issues should
follow the private reporting process in [SECURITY.md](SECURITY.md).

## License

HomeCompute is licensed under the
[Apache License 2.0](LICENSE). Model artifacts, container images, and third-party
software used with HomeCompute retain their own licenses and are not
redistributed by this repository.
