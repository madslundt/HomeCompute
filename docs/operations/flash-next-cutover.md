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

## Private retained-27B comparison

`setup-compute-flash-next.sh qualify-qwen27b` is an opt-in, temporary
same-request comparison. It does not change the model catalog, aliases, or
gateway routes. It briefly stops the active UltraFast container, starts the
already-cached pinned `general-spark-qwen38` profile, captures its response,
and restores the exact preflighted UltraFast container and restart policy.
Text service requests will be unavailable during the cold swap; this is not a
zero-interruption operation.

Before running it, pause the scheduled text workflows, wait for their current
requests to finish, and use a private prompt-free evaluation manifest with the
exact request to compare. The root-owned helper refuses to proceed unless the
current runtime's vLLM running/waiting metrics are present and idle, the exact
fallback image and accepted model-cache tuple are already local, the current
Flash owner records and container match, Whisper is healthy, and the memory
gate passes. It never pulls or downloads a model. Both raw responses and the
receipt are stored under `/var/lib/homecompute-qwen27b-qualification/`; the
command prints only the receipt identifier, status, and protected receipt
path. Do not put recording, household, credential, or other sensitive content
in a committed manifest or fixture.

Cache preflight failures use fixed role-specific classifications for the
pinned candidate cache manifest and the active accepted-cache manifest.
`owner-state` failures identify the Flash owner-record category separately.
These checks remain fail-closed: do not recreate a missing manifest or bypass
cache verification; reconcile the missing or untrusted state through its
documented provisioning owner, then retry the private qualification.

If the invoking user needs to inspect results outside the protected root state,
pass `--export-dir` pointing to a pre-existing mode-0700 directory owned by
that same user. Only `receipt.json` and the raw `flash.response` and
`qwen27b.response` files are copied there, as mode-0600 files owned by the
invoker. The request, environment, API key, and recovery journal remain in
root-only state. Export is optional; an invalid or unwritable destination is
reported separately from model qualification and rollback status.

Run the command only through `/srv/homecompute/current`; the helper verifies
that its source belongs to that active immutable release, so the dashboard and
other text-model owners share the same lifecycle guard. A retained
`pending.json` fences every model-mutating owner after interruption. Only the
Flash-Next `recover-qwen27b` action may cross that fence, and it must verify
the saved environment, source, configuration, owner records, and runtime
inventory before changing containers. Unknown or changed state remains held
for operator diagnosis.

The operator-provided manifest must be a regular mode-0600 JSON file inside a
mode-0700 directory owned by the invoking user or root. Its schema is
`{"schema_version":1,"request":{...}}`; `request` is the frozen OpenAI chat
completion request body, with `stream:true`, and without a `model` field. The
request is sent to each profile without changing its content; only the served
model alias differs. For example:

```sh
sudo /srv/homecompute/current/scripts/setup-compute-flash-next.sh \
  qualify-qwen27b --profile ultrafast --env /etc/gb10-ai/gb10.env \
  --manifest /tmp/homecompute-qwen27b.XXXXXX/request.json --maintenance-window \
  --export-dir /tmp/homecompute-qwen27b-results.XXXXXX
```

The `--maintenance-window` flag is an explicit operator acknowledgement; it
does not pause workflows by itself. If the command exits with a pending
rollback, preserve its receipt and run the owner-native recovery action:

```sh
sudo /srv/homecompute/current/scripts/setup-compute-flash-next.sh \
  recover-qwen27b --env /etc/gb10-ai/gb10.env
```

All Compose, Flash-Next, automation-model and catalog-backed model start/stop
owners share the Spark text-model lifecycle lock. Do not bypass these owners
with direct Docker commands during qualification or recovery.

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
