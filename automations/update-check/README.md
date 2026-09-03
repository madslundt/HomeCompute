# Weekly setup update check

This directory contains an importable n8n workflow that checks the upstream
projects relevant to the HomeCompute setup once a week. It produces a review
notification when a watched artifact changes, an installed pin differs from
upstream, a new NVIDIA NVFP4 model appears, or a source cannot be checked.

The workflow is deliberately **review-only**. It never downloads, installs,
promotes, or switches a model. An upstream revision is not proof that a model is
better, and it does not make a runtime/model/parser/configuration tuple safe on
GB10 hardware.

## Files

- `n8n-workflow.json` is the workflow export to import into n8n.
- `watchlist.json` is the repository-owned list of models and projects to
  monitor. Update it in code review when the setup's candidate set changes.

## Install in n8n

1. Import `n8n-workflow.json` using **Import from File**.
2. The **Load repository watchlist** node uses the repository's `master` branch
   and a fixed HTTPS URL. If the repository is private, moved, or forked, edit
   that node and use an authenticated HTTP credential rather than putting a
   token in the URL. The workflow rejects source requests outside
   `huggingface.co` and `api.github.com`; update that allow-list only in review.
3. Set `pinned_overrides_json` to the installed revisions or release tags that
   should be compared with upstream. The keys are IDs from `watchlist.json`:

   ```json
   {
     "primary-text-model": "0123456789abcdef0123456789abcdef01234567",
     "vllm-runtime": "v0.17.0",
     "n8n-runtime": "n8n@2.20.0"
   }
   ```

   Leave it as `{}` if change detection from the first scheduled run is enough.
   The workflow does not read `/etc/gb10-ai/gb10.env`; this avoids exposing host
   configuration or secrets to n8n.
4. Replace **NOTIFICATION PLACEHOLDER - configure and enable** with the user's
   Email, Slack, Discord, Gotify, ntfy, Home Assistant, or other notification
   node. Alternatively, set `notification_webhook_url`, enable the existing
   generic HTTP node, and adapt its payload to the receiver. Keep secret webhook
   URLs in n8n credentials whenever the chosen node supports them; do not commit
   an export containing a secret URL.
5. Run the workflow manually to verify source access and inspect the output from
   **Compare with previous run and pins**.
6. Publish/activate the workflow (the wording depends on the n8n version). It
   runs each Monday at 09:07 in `Europe/Copenhagen`; edit the Schedule Trigger
   to choose another cadence.

The first published scheduled run records a baseline and normally sends no
notification unless a configured pin is already behind or a check fails. n8n
does not persist workflow static data during editor/test executions, so use a
published trigger execution when validating baseline persistence.

## What a notification means

A notification means **review the setup**, not “upgrade now.” Before changing a
deployed tuple:

1. verify publisher provenance, license, architecture, and exact model files;
2. verify a DGX Spark/GB10 recipe and the exact container/runtime version;
3. treat model, tokenizer, remote code, chat template, quantization, parsers,
   context, and speculative decoding as one candidate tuple;
4. run the repository's per-alias quality, Danish, tool, latency, mixed-load,
   memory, recovery, and rollback gates;
5. promote by stable alias only after the candidate passes.

The NVIDIA NVFP4 discovery query is intentionally broad. It can find a new
artifact worth evaluating, but it cannot determine that the artifact is better
for HomeCompute's workloads. Add promising discoveries to the explicit
watchlist only after a human review.

## Operational notes

- The workflow uses public Hugging Face and GitHub APIs. Thirteen weekly checks
  are well below ordinary anonymous GitHub rate limits, but self-hosted networks
  must allow outbound HTTPS and DNS.
- Enable n8n's SSRF protection and deny private/link-local destinations at the
  VM firewall even though this workflow also validates its request hostnames.
- A failed source check is reportable by default and does not overwrite that
  source's last successful marker.
- The comparison state is n8n workflow static data, which n8n currently labels
  experimental. Weekly execution keeps the write frequency low. Use an n8n Data
  Table instead if this becomes a higher-frequency or compliance-sensitive
  monitor. Exporting/importing into another instance starts a new baseline.
- If the repository is private, prefer a small authenticated proxy or an n8n
  HTTP credential rather than making the repository or a token public.

After editing either JSON file, run `jq empty n8n-workflow.json watchlist.json`
before importing the workflow.
