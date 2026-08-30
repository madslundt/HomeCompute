# Configuration templates

This directory contains source-controlled examples for operator-owned
configuration. The setup scripts copy these templates outside the repository;
they do not use them as production configuration in place.

| File | Used by | Purpose |
| --- | --- | --- |
| `compute-node.env.example` | `setup-compute-node.sh` | Immutable model/runtime tuple, bind policy, and compute limits |
| `services-node.env.example` | `setup-services-node.sh` | Proxmox storage, network, image, template, and VM inputs |

Every `REPLACE_WITH` value is intentional. Validation fails while placeholders
remain. Files are sourced by privileged Bash scripts, so use only trusted
operator input and never add shell commands.

Do not store tokens, API keys, private SSH keys, real environment files, or
site-specific secrets here. Compute-node secrets belong under
`/etc/gb10-ai/secrets`; the services-node template accepts only a path to a
public SSH key.
