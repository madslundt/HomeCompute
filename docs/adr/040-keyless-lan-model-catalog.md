# ADR-040: Keyless LiteLLM catalog on the Default LAN

Status: Accepted for the reviewed Home Core release.

Date: 2026-11-20

Use `https://llm.home.arpa/v1` as the model API for LAN clients. On the
trusted Default LAN (`192.168.10.0/24`), Caddy supplies a dedicated virtual
key for model listing and inference when the client omits credentials or
sends the SDK placeholder `Bearer local`. Use the socket peer address together
with the existing ingress policy. Admit service traffic by the isolated agents
bridge address, the fixed n8n address, and the fixed Paperclip, PodWave, and
Open WebUI addresses on unpublished TLS listeners. Restrict keyless browser
access to the gateway's own origin. Keep PodWave and n8n on the existing
local-only network inference scope. MCP, operators, and clients that supply their own credentials
continue to use LiteLLM authentication and authorization.

On deployment, reconcile the virtual key to the exact aliases listed by the
loaded LiteLLM release. This amends the keyless subscription access rule in
[ADR-035](035-gateway-owned-chatgpt-subscription.md). The LAN catalog includes
the shared ChatGPT subscription models. Clients select these cloud aliases
explicitly, and local aliases retain their existing routing.

The guest-only HTTP bridge remains necessary for the pinned OpenShell
router's private-CA limitation. Its existing transport and the compatibility
HTTPS listeners retain the smaller local-model key. The embedded OpenBot Hermes runtime uses private HTTPS. The separate OpenShell
runtimes continue on their compatibility transport until their TLS clients
can use the HomeCompute CA.
