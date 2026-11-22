# T3 Code on Home Core

T3 Code is a private coding workspace for personal projects. The application
runs in its own Compose project on `home-core`. The upstream Linux x64 T3 Code
and Codex CLI artifacts and the Hermes source and Python runtime are verified
against the SHA-256 pins in the Dockerfile. Hermes uses the upstream locked
dependencies for its ACP adapter.
The container runs as UID/GID 12073 with a read-only root filesystem, resource
limits, and no capabilities. Application state persists under `/data`. It
receives no host checkout, SSH keys, Docker socket, or production credentials.

The managed policy disables T3 Connect, webhooks, browser-cookie import, cloud
preview tunnels, product telemetry, and in-app updates. Providers are defined
by the managed policy. Threads default to approval-required mode. The container can reach
the network for provider login, model requests, and Git. Use only projects
approved for the provider you sign in to.

## Connect from your workstation

Open **T3 Code** in Homepage or visit [t3code.home.arpa](https://t3code.home.arpa/)
from an approved home network. Use the home DNS resolver and trust the
[HomeCompute Caddy CA](../homepage/README.md). Caddy reaches the application
over a dedicated, internal Docker network. T3 Code requires an application
session.

For a new browser or device, create a five-minute, single-use link:

```sh
ssh home-core 'sudo docker exec homecompute-t3code-t3code-1 t3 auth pairing create --ttl 5m --base-url https://t3code.home.arpa'
```

Open the printed link, then use the Homepage card for future visits. Generate
a separate link for each client. Manage established sessions under **Settings
→ Sharing & access**.

For ChatGPT-backed Codex threads, sign in with a ChatGPT account authorized
for the projects you plan to use. The login command runs inside the container and
stores its credentials in the service's private state:

```sh
ssh -t home-core 'sudo docker exec -it homecompute-t3code-t3code-1 codex login --device-auth'
```

Complete the browser prompt shown by Codex. In T3 Code, open or clone a Git
repository under `/data/projects` and select Codex when creating a thread.
Access to private Git repositories requires separately scoped Git credentials.

## GitHub projects

The [project list](projects.json) declares the public GitHub repositories
provisioned on Home Core. Open **Projects** in a paired T3 Code client and
choose the repository you want to work on. The checkouts live under
`/data/projects`. Choose **HomeCompute Local → Automation MoE** to use the
local coding team in any of these projects.

From a clean, reviewed release published on `origin/master` and checked out
on Home Core, seed the Git checkouts and register them with the running app:

```sh
revision=FULL_RELEASE_SHA
sudo python3 "/srv/homecompute/releases/$revision/scripts/setup-t3code-projects.py"
```

The installer clones over public HTTPS and leaves matching checkouts and their
changes in place on retries. It restores a matching archived T3 Code project
to the project list. Branches and worktrees are ordinary Git state. Use T3
Code's project settings to archive a checkout you no longer want in the list.
Remove its declaration before rerunning the installer if the archive is meant
to persist. T3 Code uses each checkout's GitHub remote for its repository
issues and pull requests.

### GitHub Projects backlog

In a thread using **HomeCompute Local → Automation MoE**, ask
`$homecompute-team Show this project's GitHub backlog`. The lead can also run
`t3-github-backlog` from the checkout or its worktree. Approve the read command
when Supervised mode prompts for it.

The reader scans every open personal board owned by the configured GitHub
account every 15 minutes. For each declared T3 repository it publishes up to
40 open issue and PR references per board with Status and Priority. Draft
cards and private or out-of-scope repository items stay outside the snapshot.
The snapshot reports partial queries and additional pages. T3 accepts data up
to 30 minutes old. The local coding lead can hand the relevant issue and
priority to the Hermes reviewer.

After deployment, create a dedicated classic GitHub token with an expiry and
only `read:project`. In a private terminal run the following with the published
T3 Code provisioner release SHA, then enter the token at the hidden prompt.
The provisioner verifies token ownership and scope against GitHub before
storing it. If GitHub rejects the credential, the terminal prints the reason
without the token or GitHub response body. The T3 Code container mounts only
the generated snapshots, read-only.
[GitHub Projects authentication](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects#authentication)

```sh
ssh -t home-core 'sudo python3 /srv/homecompute/releases/FULL_RELEASE_SHA/deploy/t3code/github-projects.py provision && sudo docker restart homecompute-t3code-github-projects-1'
```

From Home Core, verify the status from a declared checkout. `available` with
an empty board list means GitHub returned no matching open items.

```sh
sudo docker exec -w /data/projects/Daythread homecompute-t3code-t3code-1 t3-github-backlog | jq '{repository, status, boards: (.boards | length)}'
```

Rotate the token with the same provision command and restart. Revoke retired
tokens in GitHub. A failed scope check or revoked credential yields an
unavailable snapshot at the next refresh. When rolling back to a release with
no reader, stop the `github-projects` sidecar under the active Compose release
and redeploy the earlier T3 Code image through the normal procedure.

## Local models

In a new thread, select **HomeCompute Local → Automation MoE** in the model
picker. This provider uses the local `automation-moe` alias through the
gateway's local-only Responses API. It has a separate Codex home and uses the
private HomeCompute CA. Pairing with T3 Code is sufficient for this provider.
The separate Codex provider continues to use its configured ChatGPT account.

The model catalog lists the local text alias exposed for coding. Update the model
catalog and its Codex configuration together if the local routing changes.
The `ai.home.arpa` Docker alias belongs to Caddy on the dedicated T3 Code
bridge. It leaves the workstation DNS record and the LAN model catalog in
place.

## Coding team

On iPhone, web, or desktop, select **HomeCompute Local → Automation MoE** and
start a thread in your project. Enter `$homecompute-team` with the task or choose
the skill from the `$` menu. Codex leads implementation and testing. T3 Code
delegates the review to a child thread using **Hermes Code Review**, which you
can also select directly for a review. Open the child thread from the parent to
inspect its findings. The team uses local Automation MoE for both roles.

The Hermes ACP instance has its own `/data/.hermes-code` state, separate from
the household Hermes and OpenBot instances. It starts with file read and search
tools, with no shell or household credentials. Its model requests use the same
private Caddy bridge as local Codex and require only a paired T3 Code client.
The team runs on demand from a thread in the selected project.

## Deploy and inspect

Use this dedicated lifecycle on `home-core` from a reviewed commit published
on `origin/master`:

```sh
revision=FULL_RELEASE_SHA
release=/srv/homecompute/releases/$revision
sudo git clone https://github.com/madslundt/HomeCompute.git "$release"
sudo git -C "$release" checkout --detach "$revision"
sudo python3 "$release/scripts/setup-t3code-access.py"
sudo bash "$release/scripts/deploy-t3code.sh" "$revision"
```

For a release directory already present, verify its checkout and run just the
installer. It uses the shared deployment lock, validates state ownership,
builds the pinned image, checks the Hermes ACP review toolset and tests the
Codex sandbox in disposable containers,
and waits for container health. Inspect the running image, Compose source, and
management policy:

```sh
sudo docker inspect --format '{{.Config.Image}} {{.State.Health.Status}} {{index .Config.Labels "com.docker.compose.project.config_files"}}' homecompute-t3code-t3code-1
sudo docker exec homecompute-t3code-t3code-1 t3 policy show
sudo docker exec homecompute-t3code-t3code-1 codex login status
```

The access installer adds the virtual host and the internal Docker network to
the healthy gateway, including its private model API alias. The normal Home
Core release declares the same configuration. Add the
record in [t3code-dns.json](../../config/t3code-dns.json) in UniFi Network under
**Settings → Policy Engine → Policy Table → DNS Records**. Verify DNS, trusted
TLS, browser pairing, and the projects view before applying the [Homepage
card](../homepage/README.md).
Docker stdout collection is disabled because the upstream foreground server
prints a pairing credential at startup.

## Backup and rollback

Session state, checkouts, and provider sign-ins live in `/srv/state/t3code`.
Before upgrading, stop the container and make a protected backup of that
directory. Restart the prior image if the upgrade has to be deferred. Keep
backup archives private because they contain credentials and session data.

To roll back an upgrade, stop the container, preserve its current state, restore
the pre-upgrade snapshot, and apply the previous compatible release. To remove
the scoped gateway change, reapply Caddy using the Compose files in
`/var/lib/homecompute/t3code-gateway/FULL_SHA/receipt.json` in their recorded
order with `/etc/homecompute/control-plane.env`. Restore the Homepage
configuration recorded in its [rollback procedure](../homepage/README.md) and
remove the T3 Code DNS record only if it still matches the declared value.
