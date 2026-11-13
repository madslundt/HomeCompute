# OpenBot on the agents VM

OpenBot is installed as a single pinned container in the Ubuntu agents VM on
`home-core`. It uses OpenBot's hosted account, Signal, and TURN services. The
OpenBot image publishes no ports and has no bundled web client; after sign-in,
use the desktop/iPhone app or `https://openbot.run/app`.

The container tracks OpenBot's mutable `latest` image tag. The systemd unit
pulls it before each service start, and the health check verifies that the
running container uses the pulled image. Its upstream seccomp profile is tracked here and was checked
against SHA-256 `665289e6a1c41045f55166d0b01993c929f71df78e3a3b264f3252bb8e0b3d9e`.
The guest installer verifies the Ubuntu 24.04 `docker-compose-v2` package
version and `.deb` SHA-256 from `config/openbot-install.json` before installing
it, then checks the reported Compose CLI version.
The container has no Docker socket, host-home mount, host networking, or
published ports. Its only persistent mount is its private `/data` directory;
all capabilities are dropped except `SYS_CHROOT`, which Chromium's sandbox
needs inside the container. `no-new-privileges` remains enabled. The agents-VM Docker bridge egress policy
allows public TCP 80/443, the LiteLLM compatibility endpoint at
`ai.home.arpa:18080`, the private LiteLLM gateway at `mcp.home.arpa:443`, and
the documented TURN ports for the two currently resolved OpenBot relays. The
gateway hostname resolves to the host-only bridge; egress allows only TCP 443
to that address for MCP. The Android relay remains separately pinned to TCP
19445. Other private destinations and public UDP destinations are blocked. The
Home Core firewall repeats the TURN restriction by relay IP.

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
the `all-harness-mcps` gateway group, and stores it in the guest at
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

### ChatGPT subscription aliases

The owner-selected OpenBot Researcher model uses LiteLLM's ChatGPT subscription
alias. Terra and Luna remain available in the same provider for deliberate
assignment to another agent. This provider has a separate model-only LiteLLM key;
it does not grant MCP access or replace the local `automation-moe` provider.

After publishing this source, create and install the key from a clean checkout
of the exact `origin/master` revision:

```bash
python3 scripts/provision-openbot-chatgpt-key.py
```

The script creates a non-expiring key restricted to
`chatgpt-sol-latest`, `chatgpt-terra-latest`, and `chatgpt-luna-latest`, with
MCP explicitly denied. It stores the secret at
`~/.config/homecompute-openbot/secrets/chatgpt-models-key` in the agents VM.

In OpenBot → home-core → Settings → Providers → Custom provider, add:

- Provider ID: `homecompute-chatgpt`
- Display name: `HomeCompute ChatGPT subscription`
- Base URL: `https://mcp.home.arpa/v1`
- API key: the value in `chatgpt-models-key`
- Models: the three aliases above, using their matching ChatGPT Sol/Terra/Luna labels

Assign Researcher to `chatgpt-sol-latest` / High. Keep its local
`automation-moe` backup and ask-first backup policy. Terra and Luna are
selectable; this change does not rebind other agents. Do not put the key in chat,
logs, source, or a command argument.

Connect a cloud provider from Provider settings using the account or API key
you choose. Assign it only to agents intended to send data to that provider;
cloud providers receive the prompts and files sent to their agents. The
recommended OpenBot roles and data boundaries are documented in
[`docs/openbot-agent-setup.md`](../../docs/openbot-agent-setup.md); apply them in
the UI because OpenBot has no supported declarative agent-roster import.
HomeCompute does not sign into a cloud account or choose cloud providers on the
owner's behalf.

## Shared LiteLLM MCP connections

OpenBot's MCP list is shared with every agent on its server. The owner has
chosen to expose the full LiteLLM catalog to all OpenBot agents. The
`openbot-mcp` key is restricted to `automation-moe` and the
`all-harness-mcps` group. This includes n8n workflow administration and the
bounded Android UI tools as well as Aula, offers, and public documentation.
Each HomeCompute relay still requires its own role-scoped bearer header.

After deployment, `setup-litellm-harness-mcp-access.py` assigns the existing
OpenBot key the full catalog group. It remains restricted to `automation-moe`
for model requests. Copy it directly from the operator terminal into OpenBot's
secure connection header field:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'cat ~/.config/homecompute-openbot/secrets/litellm-api-key'
```

In OpenBot's `home-core` server settings, add a **Streamable HTTP** connection:

- Name: `homecompute`
- URL: `https://mcp.home.arpa/mcp`
- Header: `x-litellm-api-key: Bearer <OpenBot MCP key>`

Add these role-specific headers using the guarded export commands in
`scripts/setup-hermes-mcp.py` (run on Home Core from the deployed full SHA):

```sh
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --export-credential
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --export-openclaw-credential
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA --export-openbot-android-credential
```

Copy each command's output directly into its matching OpenBot secure header
field. Do not paste these values into Codex, chat, issue text, shell history, or
logs.

- `x-mcp-homecompute_hermes-authorization: Bearer <Hermes owner credential>`
- `x-mcp-homecompute_openclaw-authorization: Bearer <OpenClaw credential>`
- `x-mcp-homecompute_openbot_android-authorization: Bearer <OpenBot Android credential>`

Do not reuse one role's bearer as another role. Test before saving; the list
should include all seven configured aliases. This connection is available to every
OpenBot agent, so protect the key and delegated bearers as full household and
workflow access. Revoke the `openbot-agent` key to withdraw LiteLLM catalog access.

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
before adding household workspaces or provider sign-ins. The 2026-11-10
read-only check found the scheduled backup timer active, a completed archive
receipt from 2026-11-09 22:26 UTC, and a later backup attempt deferred with
exit 75 at 2026-11-10 09:43 CET. The completed receipt remains inside Home
Compute's 36-hour readiness window; inspect the current receipt and timer again
before a future data or image change. To recover, restore the whole agents VM
using its runbook, then run the service status command above. Code rollback
does not restore OpenBot data. Keep the prior Home Core release and guest image
pinned until the next off-host backup completes.

The source-managed Hermes CLI inside OpenBot uses its own `/data/.hermes`
configuration. To reconcile its local Home AI route after deployment, run:

```bash
ssh -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  hermes-operator@10.77.20.2 \
  'python3 ~/.local/share/homecompute-openbot/current/configure-openbot-hermes.py'
```

The helper also disables Hermes' passive startup update check. Git 2.43 in the
OpenBot image can otherwise lazy-fetch the partial Hermes checkout during its
ACP startup probe, which exceeds OpenBot's 10-second provider check. Explicit
`hermes update --check` and `hermes update` commands remain available. This does
not import the separate `hermes-personal-v23` owner's memories, skills or
HomeCompute tools into OpenBot.

OpenBot is licensed under PolyForm Noncommercial 1.0.0. This deployment is for
personal, noncommercial use. Commercial use requires a separate license.
