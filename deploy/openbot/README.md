# OpenBot on the agents VM

OpenBot is installed as a single pinned container in the Ubuntu agents VM on
`home-core`. It uses OpenBot's hosted account, Signal, and TURN services. The
OpenBot image publishes no ports and has no bundled web client; after sign-in,
use the desktop/iPhone app or `https://openbot.run/app`.

The upstream `v0.66.0` Linux/amd64 image is pinned by manifest digest in
`compose.yaml`. Its upstream seccomp profile is tracked here and was checked
against SHA-256 `665289e6a1c41045f55166d0b01993c929f71df78e3a3b264f3252bb8e0b3d9e`.
The guest installer verifies the Ubuntu 24.04 `docker-compose-v2` package
version and `.deb` SHA-256 from `config/openbot-install.json` before installing
it, then checks the reported Compose CLI version.
The container has no Docker socket, host-home mount, host networking, or
published ports. Its only persistent mount is its private `/data` directory;
all capabilities are dropped except `SYS_CHROOT`, which Chromium's sandbox
needs inside the container. `no-new-privileges` remains enabled. The agents-VM Docker bridge egress policy
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
cloud providers receive the prompts and files sent to their agents. The
recommended OpenBot roles and data boundaries are documented in
[`docs/openbot-agent-setup.md`](../../docs/openbot-agent-setup.md); apply them in
the UI because OpenBot has no supported declarative agent-roster import.
HomeCompute does not sign into a cloud account or choose cloud providers on the
owner's behalf.

## Operations and recovery

`homecompute-openbot.service` owns startup, restart, and graceful stop. It starts
only after `homecompute-openbot-firewall.service` installs the container egress
rules. Its post-start check waits up to one minute for Compose to create and
start the container before checking the image pin and absence of published
ports. Docker itself does not auto-start the container outside that ordering.
Re-running the source installer upgrades only an exact managed release and
preserves `/data`; unknown paths or changed installed files stop the operation.
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
restore drill. Confirm a fresh completed NAS backup before an image update or
before adding household workspaces or provider sign-ins. On 2026-11-09, the
backup lifecycle reported retired and its last recorded snapshot was
2026-10-28; resolve and verify that lifecycle before updating the image. To
recover, restore the whole agents VM using its runbook,
then run the service status command above. Code rollback does not restore
OpenBot data. Keep the prior Home Core release and guest image pinned until the
next off-host backup completes.

The source-managed Hermes CLI inside OpenBot uses its own `/data/.hermes`
configuration. To reconcile its local Home AI route after deployment, run:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'python3 ~/.local/share/homecompute-openbot/current/configure-openbot-hermes.py'
```

This configures inference only. It does not import the separate
`hermes-personal-v23` owner's memories, skills or HomeCompute tools into
OpenBot.

OpenBot is licensed under PolyForm Noncommercial 1.0.0. This deployment is for
personal, noncommercial use. Commercial use requires a separate license.
