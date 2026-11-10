# OpenBot Android MCP

OpenBot reaches the tablet through LiteLLM's private MCP gateway, which forwards
to the Home Core native HTTPS relay under the `openbot-android` principal. That
principal receives an independent bearer from the native secret store and
exposes five read-only Portal calls and five bounded UI interaction calls. The
OpenBot pins `mcp.home.arpa` to the Home Core host-only bridge and allows only
TCP 443 to that address for the gateway. The Android relay remains separately
pinned to TCP 19445.

- `homecompute_android_status`
- `homecompute_android_screen`
- `homecompute_android_apps`
- `homecompute_android_visible_text`
- `homecompute_android_screenshot`
- `homecompute_android_launch_app`
- `homecompute_android_tap`
- `homecompute_android_swipe`
- `homecompute_android_type_text`
- `homecompute_android_navigation_key`

The interaction tools are MCP actions and are available only after verifying
the read-only screen path. They do not include direct SMS, file, clipboard,
APK-install, or message-send endpoints. Agents may use them only for an
explicitly requested, bounded UI task; they must not tap send, submit, or
purchase controls or type message content. Automatic messaging remains disabled.

The bearer is issued by `scripts/setup-hermes-mcp.py` as part of the guarded
native capability admission. Never put it in Git, shell history, issue text, or
logs. The tool inventory for this principal is checked by the install probe;
its exact set and read/action annotations are covered by repository tests.

After deploying the Home Core revision, install its OpenBot guest source with:

```bash
./scripts/setup-openbot-guest.sh install
```

In OpenBot's `home-core` server settings, add one **Streamable HTTP** connection
to the LiteLLM gateway:

- Name: `homecompute`
- URL: `https://mcp.home.arpa/mcp`
- Header: `x-litellm-api-key: Bearer <OpenBot Android virtual key>`
- Header: `x-mcp-homecompute_openbot_android-authorization: Bearer <openbot-android credential>`

Retrieve the credential only through the guarded private export path and enter
it into OpenBot's secure header field. The virtual key must have only the
`openbot-android-bounded-ui` access group; do not include Aula, n8n-owner,
Hermes, or OpenClaw capability groups. Use **Test connection** before saving;
the result must list exactly the ten tools above and no task, Home Assistant, or
maintenance tools. The server setting is shared across OpenBot agents. The
OpenBot MCP principal itself remains Android-only even if its clients request
other tool names. The OpenBot container resolves `mcp.home.arpa` to
`10.77.20.1`; verify TLS and the gateway's root CA before saving the setting.

Verify at least one read-only status call from an OpenBot Hermes agent before
using the interaction tools. Keep automatic messaging disabled through this
integration.
If the CA, token role, tool inventory, or network policy check fails, leave the
MCP entry unsaved and reconcile the guarded source lifecycle before retrying.
