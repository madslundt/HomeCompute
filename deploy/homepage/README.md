# Homepage service dashboard

Homepage provides a source-controlled landing page for the web services on
`home-core`. Open the same URL from a Tailscale-connected client or a client on
the home LAN that can resolve the Tailscale hostname:

```text
http://home-core.tail479ad.ts.net/
```

The short local name is `http://home-core/`, and the LAN-IP fallback is
`http://192.168.30.122/`. All three addresses reach the
same container. The dashboard itself uses HTTP because ingress is limited to
the explicit LAN and Tailscale addresses; it is not internet-facing. Links for
services served by the control-plane Caddy edge use HTTPS on their own
`*.home.arpa` hostnames. Those names need local DNS records pointing to
`home-core`, and clients must trust Caddy's internal CA for certificate
validation.

The local operator links derive their hostname from the URL used to open
Homepage. `config/custom.js` supplies this behavior because a normal relative
URL cannot replace the current URL's port. Caddy serves n8n, Hermes, the AI
gateway, Daythread, Open WebUI, and the model manager at their HTTPS `home.arpa` names.
Daythread uses `https://daythread.home.arpa/` and the existing
`omi-local-backend-1:8787` container on the private control-plane clients network.
Its private Tailscale URL is `https://home-core.tail479ad.ts.net:18787/`.
Tailscale Serve terminates HTTPS with a publicly trusted certificate and
proxies to the backend's explicit home-core LAN listener. This follows the
existing private n8n/Calibre/Shelfmark listener pattern; it does not use Funnel.
Serve's background mode stores the listener in persistent tailscaled state.
The `homecompute-daythread-tailscale` systemd unit in
`modules/nixos/tailscale.nix` declaratively restores this listener on subsequent
NixOS activations and boots after that configuration has been deployed.

Calibre Web Automated and Shelfmark bind to loopback plus the same explicit LAN
and Tailscale addresses as Homepage. Their dynamically resolved links therefore
work from either approved network without an SSH tunnel. Neither application is
published on an unspecified host interface.

Docker discovery and container statistics are disabled. The project receives
no container-engine API access and joins no other application's Docker network.
Add user-facing links explicitly to `config/services.yaml`. This keeps the
dashboard useful without giving it control of every container on `home-core`.

The **Token usage** link opens the aggregate-only dashboard at
`https://ai.home.arpa/usage/` on the LAN. On Tailscale, `custom.js` rewrites it
to `https://home-core.tail479ad.ts.net:8443/usage/`, using the gateway's existing
publicly trusted certificate. It supports day/week/month views, date and model
filters, and refreshes once per minute without storing conversation content.

The **ComfyUI** card includes image-mode status and Start/Stop controls. Open
`https://comfyui.home.arpa/control/` on the LAN, or
`https://home-core.tail479ad.ts.net:18188/control/` through Tailscale. The
existing `home.arpa` split DNS also resolves the LAN name over the advertised
subnet route; clients using it need the HomeCompute CA as usual. UniFi has an
A record for `comfyui.home.arpa` pointing to `192.168.30.122`.

Start advertises text unavailability, waits for protected workflows, Mattermost
turns, and text requests to finish, stops the configured text container, and
starts the image-only Qwen Image Edit workflow. Stop restores the text model after
checking the image queue. An hour without an accepted, queued, running, or
completed image job triggers the same return; opening the controls does not
extend the timer. Neither manual Stop nor idle expiry interrupts image jobs.
While starting, the controls show active workflow, agent-turn and LLM-request
counts so a protected-work wait is distinguishable from image-service startup.
Daythread's new background requests wait immediately when Start is requested;
set `DAYTHREAD_CLIENT_IP` to its control-plane clients-network address. Existing
protected workflow turns can finish their remaining model calls first.
Use **Open ComfyUI** to load the prepared Qwen Image Edit workflow, upload a
photo, enter an edit instruction, and click Run. The optional NSFW adapter is
off by default. Images, workflows, and settings persist on the Spark.

The text service is selected in root-owned `/etc/homecompute/image-mode.json`;
workflows use `/llm/status` and the stable model alias rather than a specific
checkpoint name. n8n holds original execution inputs in durable two-minute
Wait loops, then continues once readiness returns. Mattermost leaves incoming
messages in its native durable queue until the same readiness check succeeds.
Already-started work is drained rather than replayed.

The lifecycle controller binds only to Spark loopback. The two fixed image
ports reach Caddy through a dedicated restricted SSH forward; Homepage gets
an embedded controls page rather than Docker access. Prepare the Spark with
`scripts/prepare-qwen-image-comfyui-spark.sh`, and install the home-core
forwards and private Serve listener with `sudo python3 scripts/setup-comfyui-access.py`.
The regular home-core deployment also reinstalls these persistent services.

## Validate and run

From the repository checkout on `home-core`:

```sh
sudo docker compose --env-file /etc/homecompute/homepage.env \
  -f deploy/homepage/compose.yaml config --quiet
sudo docker compose --env-file /etc/homecompute/homepage.env \
  -f deploy/homepage/compose.yaml up -d --wait
```

The normal `scripts/deploy-home-core.sh` release deployment also validates,
pulls, and reconciles this project.
