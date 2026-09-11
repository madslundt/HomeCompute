# TTLock webhook gateway

This project is the only public ingress for TTLock. Tailscale Funnel terminates
HTTPS and proxies to `127.0.0.1:8085`; the container forwards an allow-listed,
normalized event to one private Home Assistant webhook. It does not use a Home
Assistant token and does not expose Home Assistant.

## Security and delivery behavior

- The public route is `POST /webhooks/ttlock/<TTLOCK_WEBHOOK_SECRET>`. Use a
  64-hex-character secret. A wrong secret returns 404 and Uvicorn access logs are
  disabled so the path is not logged.
- JSON and TTLock's form-encoded callbacks are limited to 16 KiB by default,
  validated, and rate-limited to 30 requests per minute per forwarded client
  address. The port is published on loopback only.
- Only normalized fields reach Home Assistant. TTLock's `keyboardPwd` and unknown
  payload fields are discarded. Raw payload logging is not implemented.
- A record ID is the preferred dedupe key. Without one, a SHA-256 key is derived
  from lock ID, record type, result, timestamp, and user ID. Successful keys remain
  in a ten-minute in-memory cache. Failed deliveries are released so vendor retries
  can try again. This cache intentionally does not survive process restarts.
- Home Assistant 5xx, 429, timeout, and connection failures receive three attempts:
  immediately, after one second, and after three seconds. Final failure returns 503
  to request a vendor retry. Other HA 4xx responses fail immediately.

## TTLock contract status

Checked 2026-09-11 against the official TTLock Open Platform documentation. The
public Cloud API v3 documentation describes lock records with `lockId`,
`recordType`, `success`, `username`, `keyboardPwd`, `lockDate`, and `serverDate`.
It documents record types including app/passcode/card/fingerprint unlock, door
sensor open/close, auto-lock, tamper, and failed-passcode events. It does **not**
publish a webhook subscription endpoint, callback envelope, signature/HMAC,
required callback response, timeout, retry policy, or webhook event ID.

The TTLock application manager's callback test and existing TTLock integrations
show that cloud callbacks use `application/x-www-form-urlencoded`: the `records`
form field contains a JSON array of record objects. The adapter supports that
format, direct JSON records, and JSON labeled as `text/plain`. An authenticated
empty/form probe is acknowledged with HTTP 200 without creating an event. Real
records still undergo full validation and deduplication. It does not guess a
signature header; the secret URL remains the only incoming credential until TTLock
publishes or supplies a cryptographic callback contract. Add a signature verifier
first if tenant-specific documentation provides one.

Official references:

- <https://euopen.ttlock.com/doc/api/v3/lockRecord/list>
- <https://euopen.ttlock.com/doc/api/>
- <https://github.com/jbergler/hass-ttlock/blob/develop/custom_components/ttlock/webhook.py>
- <https://tailscale.com/docs/reference/tailscale-cli/funnel>

## Configure Home Assistant

Generate two independent values:

```sh
openssl rand -hex 32  # TTLOCK_WEBHOOK_SECRET
openssl rand -hex 32  # HA webhook ID
```

Install `home-assistant-automation.yaml`, replacing its placeholder with the second
value, reload automations, and set `HA_WEBHOOK_URL` to the private LAN or tailnet
URL. Keep `local_only: true`; that check concerns the gateway-to-HA request and does
not make Home Assistant public. An automation can then consume `ttlock_event`:

```yaml
triggers:
  - trigger: event
    event_type: ttlock_event
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.event_type == 'keypad_unlock' }}"
```

## Test locally

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
docker compose --env-file ../../config/ttlock-webhook.env.example config --quiet
```

Production and test transitive dependencies are fully pinned with package hashes
in `requirements.lock` and `requirements-test.lock`. Regenerate them with
`uv pip compile --generate-hashes` when intentionally updating dependencies.

The checked-in example deliberately contains invalid placeholders and is only for
Compose rendering. Create a real encrypted dotenv document containing at least:

```dotenv
TTLOCK_WEBHOOK_SECRET=<first-secret>
HA_WEBHOOK_URL=http://homeassistant.local/api/webhook/<second-secret>
HOME_ASSISTANT_ADDRESS=192.168.30.30
TTLOCK_GATEWAY_PORT=8085
```

The explicit `HOME_ASSISTANT_ADDRESS` mapping lets the container use the local
`.local` hostname even though Docker DNS does not provide multicast DNS.

## NixOS deployment and reboot recovery

1. Add the complete dotenv document as the `ttlock-webhook/environment` value in
   `secrets/home-core.sops.yaml` using `sops`; never commit plaintext.
2. Set `homecompute.ttlockWebhook.enable = true` in `hosts/home-core/default.nix`.
3. Run `sudo nixos-rebuild test --flake .#home-core`, then inspect
   `systemctl status ttlock-webhook ttlock-funnel` and `tailscale funnel status`.
4. Send a test event through the public URL and verify one `ttlock_event` in Home
   Assistant. Promote with `sudo nixos-rebuild switch --flake .#home-core`.

The NixOS units build/start the Compose project after Docker and establish the
exclusive port-443 Funnel after the healthy gateway. `--bg` persists Tailscale's
configuration across daemon and machine restarts; the unit reapplies it on boot.
The container also has `restart: unless-stopped` and a health check.

Recovery checks:

```sh
sudo systemctl restart docker
sudo systemctl restart ttlock-webhook ttlock-funnel
sudo docker compose --env-file /run/secrets/ttlock-webhook/environment \
  -f /home/mads/HomeCompute/deploy/ttlock-webhook/compose.yaml ps
tailscale funnel status
curl -fsS http://127.0.0.1:8085/health
```

No persistent application data exists, so there is nothing to back up or restore.
Update by pulling reviewed source and restarting `ttlock-webhook`; rollback by
checking out the previous revision and restarting it. To remove public ingress,
stop/disable `ttlock-funnel` and verify `tailscale funnel status` before stopping
the container.
