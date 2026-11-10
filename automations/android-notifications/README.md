# Android notification ingress

Home Core receives Mobilerun Portal events over the tablet's authenticated
WebSocket and forwards only `com.facebook.orca` notifications to n8n's local
`android-notifications` webhook. The source workflow validates and normalizes
posted/removed events. It has no LLM, storage, reply, or outbound messaging node;
n8n success and error execution data are disabled.

The workflow JSON is an inactive template. From the exact published release on
home-core, run the guarded installer as root:

```sh
sudo python3 scripts/setup-android-notifications.py
```

The installer reads `/run/secrets/android/n8n-webhook-token` without displaying
it, backs up the n8n database privately, imports the owner-scoped encrypted
credential and source workflow, publishes the workflow, then checks the local
authenticated webhook with one synthetic event. Existing workflow or credential
identities stop the installer for reconciliation. The Portal token remains
external at `/run/secrets/android/portal-token` and is used only by the
source-managed listener.

The listener keeps a private 30-day deduplication database and exposes only a
loopback health endpoint on port 18795. A successful health check reports the
WebSocket connection state and counters, never event text. Check the actual
socket without reading event payloads:

```sh
systemctl is-active homecompute-android-notifications.service
curl -fsS http://127.0.0.1:18795/health
ss -tpn state established dst 192.168.30.34:8081
```

To pause ingress, unpublish the `Android notifications (filtered ingress)`
workflow in n8n. This disables new webhook deliveries while preserving its
credential and source record; the listener will retry rejected deliveries with
deduplication rollback so a later republish can accept them.
