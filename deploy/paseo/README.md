# Paseo on Home Core

Paseo hosts the private development workspace. Open **Paseo** in Homepage or
visit [paseo.home.arpa](https://paseo.home.arpa/) on the approved LAN or over
Tailscale split DNS. Trust the [HomeCompute Caddy CA](../homepage/README.md).
Native clients can add the same HTTPS address. The private
[Tailscale Serve address](https://home-core.tail479ad.ts.net:18797/) also works
for clients without the HomeCompute CA.
The daemon also requires a password. Retrieve it through an authenticated
private SSH session from the root-owned
`/var/lib/homecompute/paseo/runtime.env` file.

The daemon uses an unprivileged container with its own state and checkouts,
a read-only root filesystem, no Docker socket or host checkout, and no
production credentials. Its published port is on loopback. Caddy reaches the
daemon over a dedicated internal bridge and admits only the approved LANs and
tailnet sources. Tailscale Serve uses the loopback port and the Paseo relay is
disabled. The two sidecars hold separate
LiteLLM and GitHub credentials. The routing and privacy policy for each model
is described in [managed agent routing](../control-plane/agent-routing.md).

## Private DNS

Apply [paseo-dns.json](../../config/paseo-dns.json) in UniFi Network's DNS
policy table. If the retired T3 record still points to the Home Core LAN
address, replace it with the declared Paseo record. Tailscale forwards
`home.arpa` queries to the same UniFi resolver over the existing subnet
route. Verify the new A record and the old hostname's absence through both
the UniFi resolver and Tailscale DNS. Then check HTTPS and sign-in through
`https://paseo.home.arpa/`.

## Models and voice

Codex lists the live LiteLLM text aliases over Responses. OpenCode uses
Chat Completions for the compatible aliases. The embeddings and video routes
remain available to tools through the private inference relay. Paseo's model
picker lists text generation models. Use the cloud-backed aliases only for
projects and task data approved for the corresponding provider. Select
`local-only` to keep inference on HomeCompute.

Dictation and voice mode use LiteLLM `stt-danish` for multilingual
transcription. Voice playback uses `omnivoice` with `danish-default`.
`english-default` is available for an English voice. Paseo receives mono 24 kHz
PCM from the inference relay, which requests WAV from LiteLLM and converts it
for playback. Change voice selections in [settings.json](settings.json) and
apply the workload release to persist those choices.

## Team and projects

The project list provisions HomeCompute, House-battery, ChessMate, and
Daythread from their public GitHub remotes. Each project's Product Owner,
Developer, Hermes Code Review, Designer, and QA are saved under
**Settings → Host → Agents → Agent profiles**. The defaults use local models.
Use the [HomeCompute team skill](skills/homecompute-team/SKILL.md) to coordinate
an implementation and independent review. Agents are created on demand.

Run `paseo-github-backlog` from a registered repository to see its open GitHub
Projects items, statuses, and priorities. Existing board membership stays
on GitHub. The sidecar refreshes snapshots every 15 minutes. If explicitly
requested, an agent can use `paseo-github-backlog add BOARD NUMBER` and
`paseo-github-backlog set BOARD NUMBER Status|Priority "Option name"`.
The scope is the owner's open boards, the declared public repositories, and
existing field options.

The first deployment can migrate the existing dedicated Projects token. To
rotate it, use this command on Home Core from a private terminal and enter a
classic token with only the `project` scope. Use `read:project` and omit
`--write` for read-only access.

```sh
sudo python3 /srv/homecompute/releases/FULL_SHA/deploy/paseo/github-projects.py provision --write
```

## Deploy or upgrade

Use the clean release checkout at `/srv/homecompute/releases/FULL_SHA` from a
reviewed commit published on `origin/master`. Back up `/srv/state/paseo` before
upgrading. It contains repositories, sessions, and provider state. Store its
backup in a private encrypted location. The private runtime files at
`/var/lib/homecompute/paseo` contain the password, gateway key, and Projects
credential. Restore them from a protected backup or reprovision credentials.

```sh
sudo bash /srv/homecompute/releases/FULL_SHA/scripts/deploy-paseo.sh FULL_SHA
```

The installer verifies the LiteLLM scope, builds the pinned application image,
reconciles the Caddy route and Homepage card, starts the daemon, registers
projects, and applies private Tailscale Serve. If unrelated gateway or Homepage
source has changed since the runtime's active release, use the
[normal Home Core release](../../docs/homecompute-operations.md#deploy-one-host)
before applying Paseo. Rerun the installer when the LiteLLM catalog changes.

Before retiring T3 Code, verify the password-gated browser session, the models
and agent profiles, and one synthetic text and speech round trip. Confirm a
fresh Projects snapshot from each checkout. Then run:

```sh
sudo bash /srv/homecompute/releases/FULL_SHA/scripts/deploy-paseo.sh FULL_SHA retire
```

Retirement removes the T3 Compose containers, networks, and tagged images.
The previous workspaces and session data move to the root-only
`/srv/state/.retired/t3code` archive. Remove the old `t3code.home.arpa` A record
in UniFi after confirming it still points to Home Core. The archive remains
available for recovery and contains sensitive history.

## Rollback

Disable only Paseo's Serve port with
`sudo tailscale serve --https=18797 off`. Stop the Paseo Compose project using
the selected release's `deploy/paseo/compose.yaml`, with `PASEO_RELEASE` set to
its full SHA. Preserve the current state before restoring a prior backup.
Use the normal Home Core lifecycle for the previous Caddy and Homepage release.
To restore the retired service, restore its private state and Projects token
from the migration archive and Paseo credential store, then follow the previous
release's workload runbook. A GitHub board change persists independently of
either application.
