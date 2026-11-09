# OpenBot on the agents VM

OpenBot is installed as a single pinned container in the Ubuntu agents VM on
`home-core`. It uses OpenBot's hosted account, Signal, and TURN services. The
OpenBot image publishes no ports and has no bundled web client; after sign-in,
use the desktop/iPhone app or `https://openbot.run/app`.

The upstream `v0.65.0` Linux/amd64 image is pinned by manifest digest in
`compose.yaml`. Its upstream seccomp profile is tracked here and was checked
against SHA-256 `665289e6a1c41045f55166d0b01993c929f71df78e3a3b264f3252bb8e0b3d9e`.
The guest installer verifies the Ubuntu 24.04 `docker-compose-v2` package
version and `.deb` SHA-256 from `config/openbot-install.json` before installing
it, then checks the reported Compose CLI version.
The container has no Docker socket, host-home mount, host networking, or
published ports. Its only persistent mount is its private `/data` directory;
its default capabilities are dropped. The agents-VM Docker bridge egress policy
allows public TCP 80/443, the LiteLLM compatibility endpoint at
`ai.home.arpa:18080`, and the documented TURN ports for the two currently
resolved OpenBot relays. All other private and public UDP destinations are
blocked. The Home Core firewall repeats the TURN restriction by relay IP.

TURN IPv4 addresses in `config/openbot-network.json` were resolved on
2026-11-09. Refresh them with `getent ahostsv4 turn.openbot.run turn2.openbot.run`
before a future OpenBot network update. Update the JSON, review the resulting
Home Core firewall diff, and deploy through the normal host release before
reconciling the guest policy. Stale entries fail closed and prevent reliable
remote WebRTC sessions.

## Install and sign in

After publishing the source revision and deploying it to Home Core:

```bash
./scripts/setup-openbot-guest.sh install
./scripts/setup-openbot-guest.sh status
./scripts/setup-openbot-guest.sh login
```

The login command asks for the account email and one-time code in the local
terminal. It stores the server session only in OpenBot's `/data` keyring. Then
sign in to the desktop/iPhone app or `openbot.run/app` with the same account and
select the `home-core` server.

## Models and teams

Provision the dedicated LiteLLM consumer after installation:

```bash
python3 scripts/provision-openbot-litellm-key.py
```

It creates a non-expiring key restricted to local-only `automation-moe` and
stores it in the guest at
`~/.config/homecompute-openbot/secrets/litellm-api-key` with mode `0600`.
In OpenBot's server Provider settings, add the custom OpenAI-compatible endpoint
`http://ai.home.arpa:18080/v1`, model `automation-moe`, and that key. To copy
the key into the app, read it in the operator's own terminal (do not paste it
into logs or chat):

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'cat ~/.config/homecompute-openbot/secrets/litellm-api-key'
```

Connect a cloud provider from Provider settings using the account or API key
you choose. Assign it only to agents intended to send data to that provider;
cloud providers receive the prompts and files sent to their agents. The OpenBot
owner can then create named agents and teams in the app. HomeCompute does not
create personal agents, sign into a cloud account, or choose cloud providers on
the owner's behalf.

## Operations and recovery

`homecompute-openbot.service` owns startup, restart, and graceful stop. It starts
only after `homecompute-openbot-firewall.service` installs the container egress
rules. Docker itself does not auto-start the container outside that ordering.
Use `./scripts/setup-openbot-guest.sh status` for a read-only status check and
the following command for a guarded restart:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'sudo -n systemctl restart homecompute-openbot.service'
```

OpenBot keeps its database, credentials, keyring, workspaces, and logs together
in the guest data directory. Do not copy only part of it. The agents-VM disk is
included in the encrypted Home Core application-state backup and has a prior
restore drill. Confirm a fresh completed NAS backup before adding household
workspaces or provider sign-ins; the NAS backup timer was retrying when this
source change began. To recover, restore the whole agents VM using its runbook,
then run the service status command above. Code rollback does not restore
OpenBot data. Keep the prior Home Core release and guest image pinned until the
next off-host backup completes.

OpenBot is licensed under PolyForm Noncommercial 1.0.0. This deployment is for
personal, noncommercial use. Commercial use requires a separate license.
