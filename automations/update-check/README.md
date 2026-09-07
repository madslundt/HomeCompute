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
  monitor. Each benchmark-linked model also names the exact release-manifest
  artifact source that is eligible for comparison. Update these bindings in
  code review when the setup's candidate set changes.
- `../../scripts/check-model-updates.py` is the dependency-free headless
  checker; `../../scripts/update_check_http.py` is its restricted HTTP adapter.

## Headless scheduled checker

The dependency-free checker is the preferred non-interactive entry point. Run
this exact command from a deployed repository checkout; the state directory
must already exist:

```bash
python3 scripts/check-model-updates.py \
  --watchlist automations/update-check/watchlist.json \
  --state /var/lib/homecompute/model-update-check/state.json \
  --report /var/lib/homecompute/model-update-check/report.json \
  --pins /etc/homecompute/model-update-pins.json \
  --selection /var/lib/homecompute/benchmarks/selection.json
```

On `home-core`, `homecompute-model-update-monitor.timer` invokes the same
interface weekly from immutable Nix store inputs. The corresponding oneshot is
`homecompute-model-update-monitor.service`; it creates the state directory and
writes the paths shown above. Its `OnSuccess` consumer deduplicates attention
reports by digest. Without additional configuration it records a warning in the
system journal. To deliver the report to an HTTPS webhook, create root-owned,
mode-0600 `/etc/homecompute/model-update-notification.env` containing:

```text
HOMECOMPUTE_UPDATE_NOTIFICATION_URL=https://notifications.example.invalid/homecompute
HOMECOMPUTE_UPDATE_NOTIFICATIONS=true
```

Set `HOMECOMPUTE_UPDATE_NOTIFICATIONS=false` to suppress delivery deliberately.

Omit `--pins` or `--selection` when that optional file is not managed on the
host. Without both a pinned active model and selection evidence, the checker
cannot report `outperforms_active`. The pins document is metadata only:

```json
{
  "schema_version": 1,
  "active_source_id": "primary-text-model",
  "pins": {
    "primary-text-model": "0123456789abcdef0123456789abcdef01234567",
    "vllm-runtime": "v0.17.0"
  }
}
```

Legacy string pin values remain supported because the string is the exact
installed artifact revision used for the active-artifact comparison. A pin that
does not provide that revision cannot support an outperforming classification.

`--selection` consumes the schema-versioned output from
`python3 benchmarks/harness.py select --run RUN --output PATH`. For every
candidate, that output carries the retained release artifact reference, source,
revision, runtime, quantization, and canonical artifact digest. The checker
recomputes eligibility and deterministic ranking from the evidence summaries
instead of trusting the producer's booleans, ranks, outcome, or winner.

Before it can classify a challenger as `outperforms_active`, the checker also
requires the active benchmark artifact revision to equal the installed pin, the
challenger artifact revision to equal the upstream revision observed during
this check, and both artifact sources to equal their source's explicit
`benchmark_artifact_source` in `watchlist.json`. Missing, stale, inconsistent,
or provider-substituted evidence remains non-actionable with a precise reason.
Every classification has `promotion_allowed: false`.

The checker validates all input schemas before writing, allows only the
documented Hugging Face and GitHub metadata endpoints, ignores ambient HTTP
proxy settings, rejects redirects or DNS answers outside public allow-listed
hosts, and caps responses at 2 MiB. It
writes the report and state atomically with mode `0600`. A first run creates a
baseline. An unchanged run reports `quiet`; upstream changes, installed-pin
drift, source failures, or an evidence-backed challenger winner report
`attention`. A failed fetch does not replace that source's last-success marker.
The checker never downloads, activates, or promotes an artifact.

Successful completed checks exit zero, including checks with reportable source
failures. Invalid inputs or local filesystem failures exit two. Schedule this
command with a headless timer and notify from the report; do not treat process
success as model-selection success.

`home-core` installs this checker through
`modules/nixos/model-update-monitor.nix`. The persistent
`homecompute-model-update-monitor.timer` runs it every Monday at 09:07
`Europe/Copenhagen`; inspect the last run and retained report with:

```bash
systemctl status homecompute-model-update-monitor.service --no-pager
sudo cat /var/lib/homecompute/model-update-check/report.json
```

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

- The workflow and headless checker use public Hugging Face and GitHub APIs.
  The weekly request volume is well below ordinary anonymous GitHub rate
  limits, but self-hosted networks must allow outbound HTTPS and DNS.
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
