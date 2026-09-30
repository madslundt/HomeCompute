# Flash-Next production cutover and rollback

**State: NOT READY; do not execute.** Qwen3.6 remains production. This
runbook becomes actionable only after the benchmark report is complete, every
hard/relative gate passes, and the owner records a separate explicit approval
for the exact model/runtime image and route change.

## Cutover gates

Before scheduling a maintenance window, require all of these artifacts and
checks:

1. A sanitized benchmark report marked `APPROVED FOR CUTOVER`, with the exact
   fixture corpus hash and passing Qwen3.6, Blazux/NVIDIA quality-baseline, and
   dime UltraFast challenger rows. The approved Flash profile must meet every
   hard and repeated reliability gate; throughput cannot offset regression.
2. `home-spark`'s protected profile runtime manifest, including the built image
   ID, exact model/source/base-image revisions, patch/build hashes, and prepared
   layout digest; the local image ID must match the approval record.
3. A verified Qwen3.6 rollback cache/artifact at
   `unsloth/Qwen3.6-35B-A3B-NVFP4@739af1e7aac320af1682ed1e0cce369af4c5265d`,
   with its runtime and current route snapshot available locally. Rollback must
   not need a model download.
4. Healthy Hviske v5.3 and Plapre Nano v2 services; available unified memory,
   swap, and storage within the measured accepted envelope.
5. A tested snapshot of `config/model-catalog.json`,
   `config/capability-routes.json`, the applied LiteLLM config, current
   virtual-key model scopes, and exact n8n workflow/model-node versions.
6. An approved maintenance window and an operator able to restore routes and
   n8n workflow versions.

If any gate fails, stop before draining workflows.

## Cutover sequence

1. Record the approval artifact ID, approved tuple, benchmark report hash, and
   current applied route/config hashes in the protected release record.
2. Pause the scheduled Aula, Find Offers, and Shopping model workflows. Wait
   for in-flight executions to finish and verify no write-capable execution is
   queued. Leave the production model and route unchanged while draining.
3. Verify Qwen3.6 cache integrity, model health, current `automation-moe`
   route, the rollback n8n credential scope, and both speech health endpoints.
4. Save the applied LiteLLM configuration and n8n workflow versions to the
   operator's protected rollback location. Confirm the rollback commands can
   be run without fetching images or model files.
5. Use the reviewed registry change to mark only the approved Flash-Next tuple
   qualified, mark its deployment active/resident, and route `automation` to
   that deployment. Keep Qwen3.6 intact as rollback. Do not mark UltraFast
   qualified from upstream results or its MTP verification alone. Render and
   validate LiteLLM configuration; do not alter client scopes as a side effect
   of rendering.
6. Ensure each intended n8n consumer uses the semantic `automation` alias and
   a separately scoped local-only key. Keep `automation-moe` available only
   for the documented compatibility/rollback path. Verify candidate aliases
   are not exposed to unrelated consumer keys.
7. Run `sudo ./scripts/setup-compute-flash-next.sh activate-canary --profile
   <approved-profile>` only after the reviewed route tuple is ready. The
   lifecycle records and stops current
   text containers, leaves Hviske and Plapre running, starts the exact local
   image, and restores the previous text containers if startup or smoke fails.
8. Verify health, exact served model, tool protocol, alias authorization,
   backend identity metadata, and one controlled **read-only** Aula n8n
   execution. Confirm zero notification or write nodes executed.
9. Resume the scheduled workflows only after all checks pass. Observe the next
   normal execution and record sanitized metrics and the accepted deployment
   tuple.

Keep the Flash profile exclusive with Qwen3.8-27B and every other large text
model. Qwen3.8-27B may remain a separately qualified cold fallback/workhorse;
this runbook does not assign it a route. Do not add Whisper or Qwen3-TTS to the
Spark target.

## Rollback sequence

On any gate, health, authorization, tool, output, or read-only n8n failure:

1. Keep scheduled automation workflows paused.
2. Restore the saved LiteLLM route/config and virtual-key scopes. Regenerate the
   registry-owned route section from the saved canonical registry snapshot;
   restore the prior applied config atomically.
3. Restore the saved n8n workflow versions and `automation-moe` model alias/key
   from the protected snapshot.
4. Run `sudo ./scripts/setup-compute-flash-next.sh deactivate-canary`. It stops
   the candidate and starts the text containers that were running before
   activation. Confirm Qwen3.6 health and `automation-moe` serving before
   continuing.
5. Run a local protocol/tool smoke against Qwen3.6, then one controlled
   read-only n8n validation. Resume scheduled workflows only after those pass.
6. Record the failing gate without prompt, household, tool-result, or credential
   content. Keep the candidate files and image for diagnosis; do not delete the
   rollback artifact.

The exact applied gateway/n8n snapshot path depends on the operator's protected
deployment and credential store, which is intentionally not committed here.
Do not substitute a guessed host command for that snapshot/restore procedure.
