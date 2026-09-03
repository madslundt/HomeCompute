# Configuration templates

This directory contains source-controlled examples for operator-owned
configuration. The setup scripts copy these templates outside the repository;
they do not use them as production configuration in place.

| File | Used by | Purpose |
| --- | --- | --- |
| `compute-node.env.example` | `setup-compute-node.sh` | Immutable model/runtime tuple, bind policy, and compute limits |
| `control-plane.env.example` | `deploy/control-plane/compose.yaml` | Immutable gateway images, explicit bindings, `/srv/state`, and sops-nix runtime secret paths |

Every `REPLACE_WITH` value is intentional. Validation fails while placeholders
remain. Files are parsed by a strict allow-list loader and are never executed
as shell code. They still control privileged operations, so use only trusted
operator input and never add shell commands.

Do not store tokens, API keys, private SSH keys, real environment files, or
site-specific secrets here. Compute-node secrets belong under
`/etc/gb10-ai/secrets`; control-plane secrets are materialized under
`/run/secrets/control-plane` by sops-nix and never belong in an environment
file.
