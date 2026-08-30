# Setup scripts

The repository has one privileged entry point per node role:

| Script | Target | Mutating commands |
| --- | --- | --- |
| `setup-compute-node.sh` | Supported GX10/GB10 compute host | `init`, `install`, `rollback`, `down` |
| `setup-services-node.sh` | Proxmox VE 9 services host | `init`, `host-packages`, `create-template`, `provision`, `start` |

Both scripts default to safe, staged operation. Run `help`, `validate`, and
`preflight` first, and read the matching node plan before a mutating command.
They refuse unresolved placeholders and avoid deleting existing VMs, models,
caches, secrets, or previous release records.

```bash
./scripts/setup-compute-node.sh help
./scripts/setup-services-node.sh help
```

The scripts must run from an intact repository checkout because they resolve
templates relative to their own location. Production configuration lives under
`/etc`, and runtime/model data lives outside the repository.

Static validation:

```bash
bash -n scripts/setup-compute-node.sh scripts/setup-services-node.sh
shellcheck scripts/setup-compute-node.sh scripts/setup-services-node.sh
```
