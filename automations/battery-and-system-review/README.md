# Battery and HomeCompute systems review

The importable n8n export combines the existing battery review with a weekly
Home Assistant and HomeCompute systems audit.

The active n8n workflow is **Battery Plan and HomeCompute Weekly Review**
(workflow ID `RjId1K2BPpv5lAVN`). It sends the battery review each Sunday at
08:00, then runs the systems review each Sunday at 09:00.
Schedules use `Europe/Copenhagen`. The systems review uses Home Assistant
entity states, including automation availability, battery/energy state,
unavailable entities, update entities, and any exposed performance/host
telemetry. It uses restricted primary-source web search for up to two current
model/runtime/Home Assistant ideas and sends a concise result to the existing
Telegram destination.

The Home Assistant, OpenAI, and Telegram credentials are references to
credentials already configured in the live n8n instance. Re-select them after
importing into another instance. No credential secrets are included.

Home-core's node exporter currently listens on loopback and no home-spark host
metrics endpoint is exposed to n8n. The workflow reports missing core/Spark
coverage instead of inferring host health. It reads current Home Assistant
states, not historical automation success logs; unavailable/unknown states
are review signals, and an automation being off is not treated as an error.

The workflow has no daily schedule. Manual runs also produce a weekly battery
review. It is review-only and does not change Home Assistant controls,
deploy software, install models, or promote model candidates. Model ideas need
local benchmark and compatibility evidence before adoption.
