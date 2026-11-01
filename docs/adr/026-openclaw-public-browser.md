# ADR-026: Public browsing and owner-requested page interaction

**Status:** Accepted policy; source implementation pending live qualification.
**Date:** 2026-11-01
**Tracking:** [HomeCompute issue 47](https://github.com/madslundt/HomeCompute/issues/47)

## Context

The owner could not open a survey because its domain was absent from the finite
browser policy. The owner requests arbitrary public URLs and ordinary website
interaction, including filling and submitting a survey when expressly requested.
Adding each survey provider and dependency to several copies of an allowlist
does not meet that requirement.

## Decision

The isolated public browser admits HTTP(S) hostname URLs on standard ports
without per-domain approval. DNS answers must contain only public addresses.
The browser continues to have a single internal network attachment and forced
Squid egress, whose destination checks apply to connections from pages,
subresources, redirects and form submissions. Localhost, LAN, tailnet, metadata
and reserved destinations remain blocked at that boundary.

The protected tool permits bounded native `act` operations: click, type, press,
hover, scrollIntoView, drag, select and fill. Interactions require an explicit
tab ID and current snapshot refs where applicable. Script evaluation, nested
batches/requests, browser shortcut chords, file tools, profile imports and
alternate browser targets remain unavailable. Existing specialist grants and
Atlas's delegation boundary remain unchanged.

Owner instructions authorize the task and destination. Reading alone does not
authorize submission. Agents use supplied or confirmed survey answers, inspect
the confirmation, and report an uncertain outcome without automatic resubmission.
Pages cannot authorize actions or disclosure. This intent rule is agent guidance;
the tool hook does not independently prove owner intent from model arguments.
The independent network, tool-shape, role and credential boundaries remain
enforced below the model. Purchases, payments, bookings, private accounts,
passwords and MFA retain their separate owner-controlled paths.

For the inspected pinned OpenClaw runtime, explicitly setting
`dangerouslyAllowPrivateNetwork: false` adds an IP-literal-only navigation rule
for hosts without exact exceptions. Leave that option unset; never set it true.
Retain only `127.0.0.1` as the authenticated CDP transport exception. Native
public DNS/address checks remain active. That CDP exception is not browser
network access to localhost: the browser's forced proxy still rejects it.
Requalify this behavior on an OpenClaw upgrade.
See the [upstream configuration reference](https://docs.openclaw.ai/tools/browser/configuration)
and the [local runbook](../openclaw-tools.md#public-browser-policy-candidate-2026-11-01).

## Consequences

The agent can send owner-authorized survey answers to a public website. A
malicious public site also has more opportunities to influence an agent with
page instructions; the removed domain list no longer limits public exfiltration
destinations. Local inference and private-address filtering do not prevent that
disclosure. Keep private source data out of public requests unless the owner
authorizes its disclosure for the particular task.

The hostname policy is shared conceptually by the protected JS modules and Python
worker, with cross-language tests. The formerly finite DNS cache now has an
explicit 256-entry bound. Rollout must update both modules and the browser/DNS
image, together with native configuration and agent instructions. A successful
configuration write alone does not establish browser readiness.

The credential-backed registered-site workers keep their finite reviewed site
profiles, grant ledger, transaction approvals and private sign-in surfaces.
This decision changes only the public browser. ADR-015's data-domain separation
and ADR-017's workload boundaries continue to apply.

## Qualification and rollback

Follow the [browser runbook](../openclaw-tools.md). Retain the previous image,
containers, protected modules, private native configuration and agent instruction
backups. Prove public navigation, requested synthetic form submission, and
private-destination denial through the authenticated native client before
claiming the capability live. Revert the entire browser/DNS/configuration tuple
if qualification fails. Reverting code cannot withdraw an already submitted form.
