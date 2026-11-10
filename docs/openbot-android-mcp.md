# OpenBot Android MCP

OpenBot reaches the tablet through LiteLLM's private MCP gateway, which forwards
only to the Home Core native HTTPS relay under the `openbot-android` principal.
That principal receives an independent bearer from the native secret store and
exposes only these read-only Portal calls:

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

In OpenBot's `home-core` server settings, add one **Streamable HTTP** connection
to the LiteLLM gateway:

- Name: `homecompute`
- URL: `https://mcp.home.arpa/mcp`
- Header: `x-litellm-api-key: Bearer <OpenBot Android virtual key>`
- Header: `x-mcp-homecompute-openbot-android-authorization: Bearer <openbot-android credential>`

Retrieve the credential only through the guarded private export path and enter
it into OpenBot's secure header field. The virtual key must have only the
`openbot-android-read` access group; do not include Aula, n8n-owner, Hermes, or
OpenClaw capability groups. Use **Test connection** before saving; the result
must show only the five Android read tools under the
`homecompute-openbot-android` server prefix and no task, Home Assistant, or
maintenance tools. The server setting is shared across OpenBot agents. The
OpenBot MCP principal itself remains Android-only even if its clients request
other tool names. Verify the agents VM can reach `mcp.home.arpa` over an approved
network path before saving; the current egress policy may need a source-owned
allow rule through the gateway's private front door.

Verify at least one read-only status call from an OpenBot Hermes agent. Do not
enable Android action tools or automatic messaging through this integration.
If the CA, token role, tool inventory, or network policy check fails, leave the
MCP entry unsaved and reconcile the guarded source lifecycle before retrying.
