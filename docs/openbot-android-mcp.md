# OpenBot Android MCP

OpenBot reaches the tablet through Home Core's native HTTPS MCP relay. The
OpenBot guest's container egress policy allows only `10.77.20.1:19445` for this
private service, and its Compose configuration trusts the local Home Core CA.
The relay's `openbot-android` principal receives an independent bearer from the
native secret store and exposes only these read-only Portal calls:

- `homecompute_android_status`
- `homecompute_android_screen`
- `homecompute_android_apps`
- `homecompute_android_visible_text`
- `homecompute_android_screenshot`

The bearer is issued by `scripts/setup-hermes-mcp.py` as part of the guarded
native capability admission. Never put it in Git, shell history, issue text, or
logs. The tool inventory for this principal is checked by the install probe;
its exact set and read-only annotations are covered by repository tests.

After deploying the Home Core revision, install its OpenBot guest source with:

```bash
./scripts/setup-openbot-guest.sh install
```

In OpenBot's `home-core` server settings, add a **Streamable HTTP** MCP server:

- Name: `homecompute-android`
- URL: `https://homecompute-capabilities.home.arpa:19445/mcp`
- Header: `Authorization: Bearer <openbot-android credential>`

Retrieve the credential only through the guarded private export path and enter
it into OpenBot's secure header field. Use **Test connection** before saving;
the result must list the five tools above and no task, Home Assistant, or
maintenance tools. The server setting is shared across OpenBot agents. The
OpenBot MCP principal itself remains Android-only even if its clients request
other tool names.

Verify at least one read-only status call from an OpenBot Hermes agent. Do not
enable Android action tools or automatic messaging through this integration.
If the CA, token role, tool inventory, or network policy check fails, leave the
MCP entry unsaved and reconcile the guarded source lifecycle before retrying.
