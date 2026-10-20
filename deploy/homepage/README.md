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
