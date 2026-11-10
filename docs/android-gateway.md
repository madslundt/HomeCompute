# Android AI gateway

## Architecture

Mobilerun Portal remains on the Lenovo tablet. Hermes runs in the isolated
agents VM and reaches Android through the existing authenticated
`homecompute-capabilities` MCP endpoint. Its Android grant includes five
read-only Portal operations and five bounded UI actions: app launch, tap,
swipe, plain-text input, and safe navigation keys. It does not expose the
community server's broad 149-tool surface, ADB, SMS, file, clipboard, APK
installation, or direct message-sending endpoints. UI actions require an
explicit owner request and must not submit messages, forms, or purchases.
Automatic messaging remains disabled. The adapter uses Portal HTTP on
home-core, so the Hermes sandbox receives no new LAN route.

`homecompute-android-notifications.service` listens to the Portal WebSocket,
filters package IDs, deduplicates event identifiers in a private SQLite file,
and posts normalized events to the n8n webhook on localhost. It stores only
hashed event identities, not notification text. The imported n8n workflow
validates the event and applies the same application allowlist; success and
error execution data are disabled. It does not invoke Hermes or an LLM.

The community `mobilerun-mcp` project is not selected: current upstream exposes
149 tools spanning ADB, Portal, cloud, and write operations. A local facade on
the existing MCP adapter keeps the grant limited to five Portal GET calls and
five fixed UI action calls and avoids a second Android control framework.

## Source and configuration

- NixOS service: [modules/nixos/android-gateway.nix](../modules/nixos/android-gateway.nix)
- Owner MCP tool facade: `scripts/openclaw_capabilities.py`
- Notification listener: `scripts/android_notifications.py`
- Importable n8n workflow: [n8n-workflow.json](../automations/android-notifications/n8n-workflow.json)
- Hermes usage policy: `deploy/hermes/skills/android-gateway/SKILL.md`
- Tablet name/HTTP/WebSocket ports/package allowlist/webhook URL are Nix options
  under `homecompute.androidGateway`.

The checked-in defaults use `android-tablet.home.arpa`, HTTP 8080, WebSocket
8081, the Messenger package `com.facebook.orca`, and localhost n8n port 15678.
Replace the hostname with a reserved LAN DNS name or tablet IP after setup. The
Portal HTTP and WebSocket servers and Notification Access must be enabled in
Mobilerun Portal. Enable its notification event toggle. Keep the tablet and
home-core on the trusted local network; do not expose Portal ports publicly.

Add the Portal token under `android.portal-token` in
`secrets/home-core.sops.yaml`; sops-nix installs it at
`/run/secrets/android/portal-token`, readable by the existing
`homecompute-openclaw` adapter identity. Add an independent random webhook token
under `android.n8n-webhook-token`; sops-nix exposes it only to the notification
service through systemd credentials. Configure that same webhook token in n8n's
`Authorization` Header Auth credential as `Bearer <value>`. Neither token
belongs in plaintext files, Git, or command arguments. Edit the encrypted file
with `sops secrets/home-core.sops.yaml`; do not paste credential values into
chat or shell commands.

## Activate MCP and n8n

Publish the reviewed repository revision first. On home-core, use the guarded
Hermes MCP lifecycle from that published release. Inspect its plan. If a
previous Hermes MCP installation is active, run its guarded rollback first,
then apply the new plan; the updated tool whitelist is intentionally not
hot-patched. On the agents guest, connect the existing owner profile using
`setup-hermes-guest.sh connect-mcp` with the owner credential on stdin, then
run `scripts/connect-hermes-mcp.py` from the published guest checkout. It
installs the new Android skill only if absent or already identical.

If the connector reports NemoClaw's specific obsolete host-only credential
boundary warning, inspect the native MCP status first. For that exact verified
state only, `connect-mcp --repair-legacy-registration` stores a private,
secret-free metadata backup, uses NemoClaw's server-scoped forced cleanup for
the malformed legacy mapping, verifies it is gone, then removes the named
orphan provider only after confirming its type, credential key, and attachment
are exclusive to that owner sandbox. It adds the server again with the
dedicated owner credential. Other registration conflicts or shared provider
attachments remain blocked for manual review.

Import `automations/android-notifications/n8n-workflow.json` into the existing
n8n instance. Create the `Android notification ingress` HTTP Header Auth
credential using the external webhook token, bind it to the webhook node, and
keep the workflow inactive until testing. If the allowed package list is
changed, update the Code node's allowlist to match. The workflow output is a
normalized event for future downstream n8n nodes. Do not add automatic LLM
processing or message sending without an explicit separate policy.

## Checks and troubleshooting

On home-core, check `systemctl status homecompute-android-notifications` and
`journalctl -u homecompute-android-notifications`. Logs omit event bodies and
credentials. The local health endpoint is `http://127.0.0.1:18795/health`; it
returns 200 only while the WebSocket is connected and 503 otherwise. Check
Portal reachability with `curl http://<tablet-host>:8080/ping`; verify
authenticated `/state` with a securely supplied Authorization header. Never
put the token in shell history or a URL.

Use Hermes's `homecompute_android_status`, `homecompute_android_screen`,
`homecompute_android_apps`, `homecompute_android_visible_text`, and
`homecompute_android_screenshot` tools for inspection. After the owner asks for
a specific bounded UI task, `homecompute_android_launch_app`,
`homecompute_android_tap`, `homecompute_android_swipe`,
`homecompute_android_type_text`, and `homecompute_android_navigation_key` can
perform UI navigation. Status/read tools return an explicit unavailable result
when the device or credential cannot be reached. Screenshots can contain
private conversation content; prefer accessibility reads. Messenger UI
layouts, notification previews, and message visibility depend on app settings
and are not a reliable substitute for an official personal-messages API.

## Tablet completion checklist

- [ ] Reserve a private tablet hostname/IP and update `portalHost`.
- [ ] Install Mobilerun Portal, enable local HTTP and WebSocket servers, and
  enable Notification Access and notification events.
- [ ] Add the Portal token and independent n8n webhook token to the encrypted
  `secrets/home-core.sops.yaml`, then rebuild/apply the reviewed home-core revision.
- [ ] Import the n8n workflow, bind the Header Auth credential, and keep it
  inactive until a synthetic notification passes validation.
- [x] Run `/ping`, authenticated `/state`, Hermes MCP discovery and a Hermes
  read-only screen call. Confirm WebSocket health and synthetic notification
  ingestion to n8n.
- [x] Enable bounded UI navigation after the read-only path passed. Direct SMS,
  file, clipboard, APK-install and message-send tools remain absent.
- [ ] Complete OpenBot's custom MCP test. Its hosted test currently cannot reach
  the private endpoint, so keep that server entry unsaved until the test lists
  all ten scoped tools.

## Current limits

Tasker and AutoNotification are optional and are not needed for Portal
notification capture. Android UI automation and Messenger/WhatsApp conversation
reads can be fragile. Generic tap and text-entry tools can still change app
state, so use them only for explicitly requested UI work and never to send
messages or submit forms. The notification path does not invoke an LLM or send
messages.
