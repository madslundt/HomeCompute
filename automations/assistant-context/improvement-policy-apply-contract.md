# Managed assistant policy lifecycle

The source policy remains sealed until the operator enables the managed
briefings selector. The controller's reviewed source must be a full commit SHA
reachable from `origin/master`, and the candidate must already be reconciled to
that exact merged source.

Before enabling the selector, initialize the fixed managed target with the
published source policy:

```sh
sudo python3 scripts/setup-assistant-context-policy.py initialize --revision FULL_SHA
```

The command first returns a hash-bound plan. Apply it with the reported
transaction ID and plan hash. The result is `initialized_not_active`: it creates
only `/var/lib/homecompute-assistant-policy/assistant-briefings.json` and does
not restart the context service or claim that the managed policy is selected.
The operator may then enable the existing `managedBriefings` Nix option through
its normal reviewed lifecycle.

For each already reviewed candidate, read the installed target SHA and request
a plan:

```sh
sudo python3 scripts/setup-assistant-context-policy.py plan \
  --revision FULL_SHA --candidate-id CANDIDATE_ID \
  --expected-installed-sha256 INSTALLED_SHA
sudo python3 scripts/setup-assistant-context-policy.py apply \
  --transaction-id TRANSACTION_ID --plan-sha256 PLAN_SHA256
```

Application requires the active service command to explicitly select the fixed
managed path, an exact installed preimage, a trusted backup plus isolated
restore receipt, and a healthy service. The installer stops the active service
before replacing the one JSON policy file, starts it, and probes authenticated
`/v1/health` with a separate operator credential provisioned at `/etc/homecompute/assistant-context-policy-health-token` and projected into the controller systemd unit as a credential. Failure restores the
captured preimage and verifies health; an incomplete recovery is reported as
`recovery_required`.

Rollback uses the transaction's saved preimage and is refused if the installed
file no longer has the exact planned after hash:

```sh
sudo python3 scripts/setup-assistant-context-policy.py rollback \
  --transaction-id TRANSACTION_ID --expected-installed-sha256 INSTALLED_SHA
```

The setup helper accepts only the fixed `/var/lib/homecompute-assistant-policy`
target, root on Linux home-core, and source revisions on `origin/master`. It
does not write Nix-store files, scripts, workflow definitions, or other service
configuration.
