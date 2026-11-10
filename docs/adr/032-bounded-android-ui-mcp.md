# ADR-032: Bounded Android UI interaction through MCP

## Status

Accepted; the Android MCP exposes read-only inspection and a narrow set of UI
actions through the existing Home Core relay after the source revision is
deployed.

## Context

The Lenovo tablet is reachable through the authenticated Mobilerun Portal on
the private LAN. Read-only inspection is verified. The owner now needs MCP
clients to perform explicitly requested UI navigation while keeping automatic
message sending disabled. Upstream Portal supports a much broader API,
including SMS, files, clipboard, app installation, and cloud control.

## Decision

Expose only five read operations and five fixed UI actions through the local
HomeCompute facade: launch an installed app, tap, swipe, type one line of plain
text into the focused field, and send a small set of safe navigation keys. Mark
UI actions as non-read-only and potentially destructive in MCP metadata. Bound
coordinates, text length, app package names, key codes, and swipe duration.

Do not expose SMS sending, file access, clipboard access, app installation,
Portal configuration, or cloud control. Agents may use UI actions only for an
explicitly requested bounded task; they must not send messages or submit forms
or purchases. The notification workflow remains a filtered ingress with no
LLM step or message-send action.

The Hermes owner and Android-only OpenBot principal may discover these tools.
The latter remains unable to call unrelated HomeCompute services. The OpenBot
UI connector must pass its test before its shared server configuration is
saved.

## Consequences

- The existing local HTTP relay and independently authenticated MCP bearers
  remain the only route to Portal.
- UI taps and text entry can still change app state; MCP annotations and agent
  instructions label the authority, while explicit user intent constrains use.
- Direct message sending and notification automation remain unavailable.
- OpenBot deployment remains pending if its hosted connection test cannot reach
  the private endpoint; the connector must remain unsaved in that case.

## Evidence

- [Android gateway](../android-gateway.md)
- [OpenBot Android MCP](../openbot-android-mcp.md)
- [Mobilerun Portal local API](https://github.com/droidrun/mobilerun-portal/blob/main/docs/local-api.md)
