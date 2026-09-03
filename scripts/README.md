# Setup scripts

The repository retains a privileged setup helper only for the vendor-managed
compute appliance. `ai-services-01` is configured with `nixos-rebuild`.

| Script | Target | Mutating commands |
| --- | --- | --- |
| `setup-compute-node.sh` | NVIDIA GB10 or DGX Spark-class appliance | `init`, `install`, `rollback`, `down` |

The script defaults to safe, staged operation. Run `help`, `validate`, and
`preflight` first, and read the matching node plan before a mutating command.
It refuses unresolved placeholders and avoids deleting existing models,
caches, secrets, or previous release records.

```bash
./scripts/setup-compute-node.sh help
nix flake check
nixos-rebuild build --flake .#ai-services-01
```

The script must run from an intact repository checkout because it resolves
templates relative to their own location. Production configuration lives under
`/etc`, and runtime/model data lives outside the repository.

Repository validation:

```bash
./scripts/validate-repository.sh
```

This checks Bash syntax and ShellCheck, the non-executing configuration loader,
automation JSON, YAML when Ruby is installed, Compose rendering (including the
artifact-fetch profile), control-plane isolation policy, and D2 rendering when
D2 is installed.
