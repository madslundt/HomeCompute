# Books importer

Shelfmark is available at `http://home-core:8084/`. Its default Universal search
uses the configured Hardcover catalogue. Selecting a book then searches the
enabled download sources separately.

On 2026-10-09, Direct searches stalled on Anna's Archive's DDoS-Guard page.
The protection script at `https://check.ddos-guard.net/check.js` timed out from
both home-core and the Shelfmark container. NextDNS also blocked external DoH
endpoints, causing certificate errors against its block page. Shelfmark now uses
system DNS, keeping the host's NextDNS policy. The remaining connection timeout
was traced to UniFi's Russia country restriction. A user-approved rule now permits
only home-core to reach the protection endpoint on port 443; a replacement
Russia rule keeps other clients blocked. See the
[repair and rollback record](../../docs/operations/shelfmark-direct-download-repair.md).

Shelfmark was updated from v1.3.15 to pinned v1.4.0, which includes the
[clearance-cookie reuse fix](https://github.com/calibrain/shelfmark/pull/1305).
The update alone did not fix the router's country restriction.
Catalogue results do not guarantee that a download source is available.

The live settings under `/srv/state/books_importer/shelfmark_config` were updated
to match the Compose environment. The image pin is also recorded in
`config/books_importer.env.example` for future NixOS deployments. Only Shelfmark
was restarted; the other books services were left running.

Run the authenticated search smoke check from this repository on the workstation:

```sh
ssh home-core 'sudo -n python3 -u -' < scripts/shelfmark-search-smoke.py
```

The check follows the configured UI search mode, requires results for two
queries, and never downloads a book or prints credentials. To diagnose the
external download source separately:

```sh
ssh home-core 'sudo -n python3 -u - --direct' < scripts/shelfmark-search-smoke.py
```

To verify the download list shown after selecting a catalogue book:

```sh
ssh home-core 'sudo -n python3 -u - --releases' < scripts/shelfmark-search-smoke.py
```

The pre-change configuration and runtime image pin are backed up in the
root-only directory
`/srv/state/books_importer/maintenance/shelfmark-search-20261009/`.
