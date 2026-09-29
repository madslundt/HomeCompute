# Aula morning delivery patch

The production `Aula - Family Briefings` workflow is managed in n8n. This
directory records the reviewed change to its `Prepare channel deliveries` Code
node without committing a live workflow export, credentials, or Aula content.

`morning-delivery.patch` is a unified diff of that Code node before and after
the 2026-09-29 fix. It makes source-grounded day-of actions eligible for the
06:00 reminder, includes the action in the short text, and sends that text to
the existing Telegram and Home Assistant branches. Routine items without an
action or high-importance flag remain silent in the morning.

The fix was published in n8n on 2026-09-29. A private replay of the missed
outdoor-clothing post verified both delivery flags, the clothing and extra
sweater text, a missed model action flag, and a routine no-alert case. The next
scheduled 06:00 execution is the first live delivery check.

To apply this patch to another instance, export only the existing Code node's
JavaScript to a private file named `prepare-channel-deliveries.js`, apply the
patch there, validate the node in n8n, and publish the resulting workflow
version. Keep exports, executions, and source content out of Git.
