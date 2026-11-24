# Private consumer routing migration

Hermes owner, OpenBot's owner Hermes and Home Planner, and the n8n Aula summary
reviewer use the managed `local-only` text alias. The same private Flash-Next
relay serves the requests. Existing workflow delivery rules, credentials,
account scopes and storage stay with their owning applications.

The Home Core compatibility listener and fixed private n8n bridge use the
network-inference virtual key. Add just the local alias from this published
release without replacing the key or restarting the gateway.

```sh
sudo python3 scripts/setup-network-inference.py --enable-local-only
```

Verify the model catalog over each consumer path before applying its binding.
The new scope contains only local aliases. Keep NAS backup jobs on their
existing lifecycle. This migration does not require a Home Core rebuild.

## Hermes

Use the existing owner sandbox through its native CLI. Verify the exact
compatible endpoint, absence of fallback, and the hash returned by the plan.
Stream `scripts/migrate-private-models.py` to native Python with
`nemohermes hermes-personal-v2 exec --stdin -- python3 - hermes plan`.
Repeat with `hermes apply --expected HASH`, then restart only the native
gateway using `nemohermes hermes-personal-v2 gateway restart`. Check the
selected route through an authenticated synthetic owner turn and verify it
after restart. The startup image's provisioning default remains the legacy
local alias. A replacement sandbox must run this binding installer again.
A mismatch at ordinary restart fails qualification and requires rollback.

The installer keeps a private copy of the config and changes only the exact
model line. Run `hermes rollback --expected POSTIMAGE` through the same
transport and restart the gateway to restore that copy.

## OpenBot

Stream the same script into Python in the existing `homecompute-openbot`
container with `openbot plan --agent ID`, then apply to each verified agent
using `openbot apply --agent ID`. The IDs and accepted preimages are in the
script. The supported Local scripts settings endpoint changes the model while
the agent is idle. No permissions, sessions or instructions are rewritten.
Use a synthetic no-tools prompt on the local binding to verify the client.
The rollback mode returns the agent to the direct **local** compatibility
alias. Explicit subscription-only specialist agents have their own data
approval and are outside this scoped migration.

## n8n

Qualify a synthetic, unpublished reviewer graph with the existing local
credential, the managed alias, and the same parser before publishing the
production patch. The existing reviewer took 7.5 to 12.8 seconds in four
observed successful runs. The main Aula collector and AI automation manager
keep their direct local aliases and longer time budgets.

Pass the fresh workflow object from n8n MCP to
`python3 scripts/migrate-private-models.py aula plan`. Use the emitted atomic
node operations in n8n MCP after verifying the active version remains the
reviewed version. Publish that exact draft and verify the active node.
The patch removes only the provider-specific thinking flag because the
managed alias controls effective reasoning. It retains the parser, retry
policy, timeout, credential and wider graph. The rollback mode generates the
inverse operations for the same node. Keep live exports and execution payloads
out of Git and avoid production trigger execution for smoke tests.
