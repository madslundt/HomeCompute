# Local and Tailscale access policy

**Version:** 1.0

**Date:** 2026-08-31

**Status:** Normative deployment contract

## Scope and outcome

HomeCompute is private infrastructure. Interactive access is allowed only from
approved local networks or the household tailnet. No AI, automation, agent,
speech, database, SSH, or management listener is published to the
internet. Tailscale is an encrypted transport and device/user admission layer;
it does not replace application authentication or authorization.

The stable application URL is `https://ai.home.arpa`. Local DNS resolves it to
the gateway's LAN address. Tailscale uses a restricted (split-DNS) resolver for
`home.arpa` that returns a reachable private address. Keep the same TLS name and
local CA trust on LAN and tailnet clients.

## Required traffic policy

| Source | Destination | Allowed | Denied by default |
| --- | --- | --- | --- |
| Approved household LAN/tailnet clients | `home-core` | TCP 443 only; per-consumer application credential still required | Direct compute, database, container, and management access |
| Approved administrator devices/users | `home-core` management | SSH through the declared Tailscale policy with device posture and reauthentication where available | All non-admin identities and shared household devices |
| `home-core` | `home-spark` private link | Only qualified inference and audio ports | General internet forwarding and compute management |
| Automation hosts | Gateway and approved integrations | TCP 443 plus explicitly documented service endpoints | Direct compute, agent state, admin LAN, and broad RFC1918 access |
| Personal-agent hosts | Gateway and approved provider/tool endpoints | TCP 443 and required DNS/NTP only; separate credentials per principal | Direct compute, automation databases, other trust domains, and undeclared LAN services |
| Toolbox hosts | Gateway and approved artifact sources | Deliberately enabled outbound flows while in use | Household credentials, durable production state, direct compute, inbound services |
| Home Assistant | Gateway and private speech endpoints | Authenticated AI API on 443; fixed Wyoming/STT/TTS ports only where the integration requires them | Agent/admin/database access; model-driven unrestricted home control |
| Artifact fetch job | Publisher endpoints | HTTPS while the explicit prepare job runs | Runtime inference egress |

Wyoming does not provide a general application authorization boundary. Restrict
its listeners by source network/firewall and expose only the specific Home
Assistant paths that have passed voice verification.

## Tailscale contract

1. Use current Tailscale **grants** with deny-by-default rules. Give server
   devices non-human tags such as `tag:ai-gateway`, `tag:ai-admin`, and
   `tag:home-assistant`; restrict who may assign each tag.
2. Grant ordinary household identities only TCP 443 to the gateway. Create a
   separate administrator group for SSH access. Do not grant
   `autogroup:member` broad subnet or wildcard-port access. Each adult uses an
   individual identity; do not share an owner login.
3. Use a restricted nameserver for `home.arpa`. The resolver itself must be
   reachable through an explicit grant or approved subnet route. MagicDNS
   cannot carry the arbitrary `ai.home.arpa` record by itself.
4. Either run Tailscale directly on the gateway or advertise only the minimum
   gateway/DNS subnet through a dedicated subnet router. Do not advertise the
   whole household LAN merely to make the AI hostname reachable; approve the
   route and constrain it with grants.
5. Disable key expiry only for tagged unattended servers, not user devices.
   Remove stale devices promptly and require MFA at the identity provider.
6. Enable Tailnet Lock after testing recovery. Keep at least two signing nodes
   and store disablement secrets offline; it cannot be enabled together with
   Tailscale device approval.
7. Do not enable Tailscale Funnel. If Tailscale Serve is used, it must remain
   tailnet-only and proxy only to the authenticated TLS gateway. Prefer direct
   tailnet routing to the gateway so LAN and remote behavior stay identical.
8. Tailscale SSH is optional. If enabled, constrain both network grants and SSH
   rules to the administrator group, use short-lived/check-mode access for
   privileged logins, and retain ordinary host SSH hardening.
9. Review the tailnet policy, tag owners, devices, routes, DNS, and grants
   quarterly and after any household member, device, or administrator changes.

The checked-in repository deliberately does not contain a ready-to-apply
tailnet policy because user identities, device tags, subnet routes, and the
private DNS address must be discovered from the live tailnet. Copying guessed
selectors into a production policy can either lock out recovery or grant too
much access.

## TLS, identity, and logging

- Use a private CA whose root is installed only on managed household devices.
  Issue a gateway certificate for `ai.home.arpa`, automate renewal, protect the
  CA signing key offline, and test expiry alerts and rotation.
- Require a distinct revocable gateway credential for each person, application,
  and agent profile. Never infer authorization from a source IP, Tailscale
  hostname, forwarded consumer header, or model prompt.
- Preserve only metadata needed for abuse and reliability: request ID,
  authenticated principal, route alias, outcome, latency, and token/byte counts.
  Do not log prompts, responses, audio, transcripts, tool arguments/results, or
  memory content by default.
- Administrative access does not silently grant application data access. Audit
  privileged access and follow the administrator threat model in the personal
  data contract.

## Acceptance checks

Run these from one LAN client, one approved tailnet client outside the home,
one unapproved tailnet identity/device, and each restricted VM network:

1. `ai.home.arpa` resolves to the intended private endpoint and presents the
   trusted certificate on LAN and Tailscale.
2. TCP 443 succeeds only for approved clients; an absent/revoked application
   credential receives an authorization failure.
3. Direct compute and management ports fail from every non-admin client and
   from automation, agents, and toolbox.
4. Cross-principal agent state, credentials, and tools are inaccessible.
5. Funnel is disabled; no router port-forward or public DNS record exposes the
   private endpoints.
6. Disconnecting Tailscale does not weaken LAN firewall behavior. Disconnecting
   the private compute link fails private aliases closed.
7. DNS, certificate renewal, grant changes, device removal, backup-key recovery,
   and Tailnet Lock recovery each have an operator-owned rollback test.

## Primary references

- [Tailscale grants](https://tailscale.com/docs/features/access-control/grants)
- [Tailscale DNS and restricted nameservers](https://tailscale.com/docs/reference/dns-in-tailscale)
- [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)
- [Tailscale Tailnet Lock](https://tailscale.com/docs/features/tailnet-lock)
- [RFC 8375: Special-Use Domain `home.arpa.`](https://www.rfc-editor.org/rfc/rfc8375)
