# Homepage service dashboard

Homepage provides a source-controlled landing page for the web services on
`home-core`. Open the same URL from a Tailscale-connected client or a client on
the home LAN that can resolve the Tailscale hostname:

```text
http://home-core.tail479ad.ts.net/
```

The short local name is `http://home-core/`, and the LAN-IP fallback is
`http://192.168.30.122/`. All three addresses reach the
same container. The deployment deliberately uses HTTP because ingress is
limited to the explicit LAN and Tailscale addresses; it is not internet-facing.

Service links derive their hostname from the URL used to open Homepage. For
example, n8n resolves to `http://home-core:15678` when Homepage was opened on
`home-core`, and to `http://home-core.tail479ad.ts.net:15678` when opened on the
Tailscale name. `config/custom.js` supplies this behavior because a normal
relative URL cannot replace the current URL's port.

Calibre Web Automated and Shelfmark remain bound to host loopback by design.
Their dynamically resolved links therefore work directly only when the chosen
hostname can reach those ports; with the present deployment, use the documented
SSH tunnel and open their localhost URLs instead. They have not been exposed
merely to make the dashboard links work.

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
