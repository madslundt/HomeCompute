# Shelfmark direct-download repair — 2026-10-09

## Cause

Anna's Archive release requests received a DDoS-Guard protection page that loads
`https://check.ddos-guard.net/check.js`. DNS resolved this hostname to
`185.129.100.100`, but TCP connections timed out from home-core and the Mac.
NextDNS's Home profile allowed the hostname and the configured Archive mirrors.
UniFi's global Region Blocking included Russia in both directions, which blocked
the protection endpoint before an ordinary firewall allow rule could help.

The user approved a narrow exception for home-core rather than removing the
Russia restriction for every client. NextDNS was not changed.

## Applied UniFi policies

The existing legacy firewall was retained. Two Simple Traffic Rules were added:

| Name | Action | Source | Destination | Schedule |
| --- | --- | --- | --- | --- |
| Block Russia - scoped exception | Block | All Devices | Russia | Always |
| Shelfmark - home-core DDoS-Guard HTTPS | Allow | home-core (`84:47:09:79:58:b1`, `192.168.30.122`) | `185.129.100.100`, port `443` | Always |

After these policies were saved, Russia alone was removed from the global
CyberSecure Region Blocking list. The remaining countries (China, Belarus,
Iran, Brazil, Vietnam, Pakistan, India, North Korea, Syria) retain the global
Both directions restriction. Existing advanced firewall rules and IDS/IPS
settings were not modified. The replacement Russia policy applies to client
traffic; this is not a migration of the gateway's firewall.

The exception is IP-specific. If the protection endpoint changes addresses,
confirm the new DNS answer and update this rule, rather than expanding it to
all Russian destinations.

## Verification

- home-core → `https://check.ddos-guard.net/check.js`: HTTP 200.
- Mac → the same HTTPS endpoint: connection timeout (still blocked).
- home-core → the same endpoint on HTTP port 80: timeout (still blocked).
- Shelfmark's protection challenge subsequently cleared itself.
- A real metadata-to-releases lookup for Harry Potter returned 21 releases.
- The Hobbit's book-specific lookup returned 50 releases. Both regression queries
  passed after the initial protection solve (21.32s and 39.56s respectively).
- No book was downloaded during verification.

Run the regression check from the workstation:

```sh
ssh home-core 'sudo -n python3 -u - --releases' < scripts/shelfmark-search-smoke.py
```

This uses the same provider/book-id release lookup as selecting a catalogue
result in the UI. `--direct` separately checks free-text Direct searches; without
a flag, the script checks the configured default search mode. A catalogue book
can legitimately have no matching release at the external source.

## Rollback

First re-add Russia to CyberSecure → Threat Management → Region Blocking and
apply it. Then disable the two named Simple Traffic Rules. This restores the
original country restriction without a window where other clients are unblocked.

References:

- [UniFi country restriction](https://help.ui.com/hc/en-us/articles/12567758783383-UniFi-Gateway-Country-Restriction)
- [Traffic and policy management](https://help.ui.com/hc/en-us/articles/5546542486551-Traffic-Policy-Management-in-UniFi)
