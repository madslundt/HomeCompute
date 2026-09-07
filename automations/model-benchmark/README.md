# Isolated n8n model benchmark

This directory defines one test-only n8n workflow with three independent
webhook-triggered branches. It does not edit, activate, or execute the
production Notion or Aula workflows, and it has no schedule or notification
action.

## Current deployment state

**HomeCompute TEST ONLY - model benchmark lab** (`JA0y1aRkp8t1VQ25`) was
created in the owner's personal n8n project on 2026-09-05 and left inactive. It
contains these branches:

- generic model prompt/response;
- Notion-style public research with the real Tavily tool;
- Aula with the published weekday prompt-generation logic and live read-only
  Aula MCP.

The four earlier prototype workflows were archived after the consolidated graph
validated. The OpenRouter credential is attached to each model subnode, and the
existing Tavily credential is attached only to the research branch. The
production workflow graphs and publication state remain unchanged.

The first manual canaries reached the OpenRouter boundary but were rejected
because the account behind the credential had no purchased credits. No Tavily
query, notification, production write, or real Aula call occurred. Add credits
before retrying.

## Safety properties

- Each branch has its own dedicated POST webhook trigger inside the same
  workflow.
- Synthetic branches require `safety_mode=synthetic-inputs-no-side-effects`.
- The live Aula branch requires `real-read-only-data-no-side-effects` plus its
  explicit provider-authorization marker.
- Each execution calls one selected OpenRouter model and returns captured text
  and, where applicable, tool traces.
- Results remain in ordinary n8n test execution data and the benchmark harness.
- The workflow must remain unpublished until webhook authentication and network
  exposure have been reviewed.
- Only synthetic or explicitly sanitized cases may be sent through it.

The live inventory confirmed that the production Aula workflow has enabled
Telegram actions and the production Notion workflow has enabled database-update
nodes. Never use either production workflow itself for a benchmark.

The real Aula branch sends retrieved private household/school data to the
configured model provider. It exists because the owner explicitly authorized
that use. Each request must carry both the real-data safety mode and the
provider-authorization marker; the workflow otherwise refuses to run.

## Import and configure

1. Keep the benchmark lab unpublished while using n8n's test webhook. If a persistent
   production webhook is later needed, require header authentication and limit
   it to the LAN/tailnet before publishing.
2. Open the benchmark lab, select the desired trigger branch, and start
   **Listen for test event**.
3. Set the matching URL only in the shell running the benchmark:

   ```bash
   export N8N_AULA_REAL_MCP_BENCHMARK_WEBHOOK_URL='http://home-core:15678/webhook-test/homecompute-aula-real-mcp-model-benchmark'
   export N8N_TAVILY_BENCHMARK_WEBHOOK_URL='http://home-core:15678/webhook-test/homecompute-research-model-benchmark'
   ```

4. A test webhook normally listens for one request, so run one candidate at a
   time and click **Listen for test event** again before the next request:

   ```bash
   python3 benchmarks/harness.py run \
     --plan benchmarks/plans/n8n-tavily.example.json \
     --release benchmarks/manifests/n8n-openrouter.example.json \
     --candidate qwen3.6-35b-a3b-tavily

   python3 benchmarks/harness.py run \
     --plan benchmarks/plans/n8n-aula-real-mcp.example.json \
     --release benchmarks/manifests/n8n-openrouter.example.json \
     --candidate qwen3.6-35b-a3b-aula-real-mcp
   ```

   Test-webhook listening normally accepts one execution. Restart listening for
   each candidate. Publish only after adding header authentication if a
   persistent webhook is needed for the full automated matrix.

Codex can also invoke the inactive workflow in n8n's manual execution mode.
After OpenRouter is funded, the operator can ask Codex to run the canaries and
matrix without publishing a webhook.

Each branch accepts `model` dynamically. The plans use the repository's
mandatory three-model text wave. OpenRouter-hosted precision is recorded
separately and does not qualify the eventual GB10 artifact.

`benchmark-lab.workflow.ts` is the validated Workflow SDK source for the live
workflow. The other `*.workflow.ts` files remain as readable component sources.
Credential identifiers are intentionally absent from the repository.
