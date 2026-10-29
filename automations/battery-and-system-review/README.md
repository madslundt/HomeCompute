# Battery and HomeCompute systems review

The historical bootstrap n8n export combines the battery review with a weekly
Home Assistant and HomeCompute systems audit. It predates the current local-model,
retrieval, queue and Mattermost nodes and must not replace the live workflow.
The selected current sanitized managed definition is maintained in the
HA-automations repository. The patch builders below own additive overlays from
freshly inspected workflow definitions, preserving those runtime integrations.

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

## Mattermost presentation

The existing weekly Mattermost mirror has a separate presentation renderer in
`mattermost-report.js`. It opens with a short sourced result for the mobile push
preview, keeps missing/unknown-coverage qualifications visible, and retains the
report underneath. Long unstructured prose is split at sentence boundaries;
existing bullets and source links remain intact. The Telegram formatter, report
generation, schedules, audiences and delivery behavior are unchanged.

`scripts/workflow_notification_presentation.py` builds a narrow atomic update
from freshly read workflow details. It preserves the validated formatter's
original outputs, adds a separate `mattermostMessage` field in that same Code
node, and points the existing mirror at it. Presentation failures leave the
original Telegram output intact and return no Mattermost text. The builder
replaces its own bounded wrapper when reapplied rather than nesting wrappers. It
also owns the News mirror's canonical renderer in
`automations/semantic-research/news-mattermost.js`: a leading headline preview,
then five ordered story blocks with separate story/discussion bullets, source
links and the original sampled-comment qualification. No full private live
workflow export is stored in this repository.

Run `node --test tests/notification-digests.test.mjs` and
`python3 tests/notification-presentation-test.py` for synthetic checks. The
selected sanitized n8n managed snapshots are maintained in the HA-automations
repository. Commit/push the canonical sources and matching sanitized snapshots
before an authorized n8n MCP update/publication, then verify the saved and active
definitions. No live message is sent by these tests or patch builders.
