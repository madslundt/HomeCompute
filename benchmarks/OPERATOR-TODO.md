# Benchmark operator placeholders

## Code benchmarks

- [ ] Put `OPENROUTER_API_KEY` in the local shell or a non-repository secret
  manager. Do not add it to a file in this repository.
- [ ] Replace `benchmark_commit=WORKING_TREE` with the commit used for each real
  evidence run.
- [ ] Copy `templates/code-case.placeholder.json` for each real task and point
  `workspace.source` at a synthetic/sanitized repository snapshot.
- [ ] Add immutable setup, build, lint, type-check, and test argument arrays.
- [ ] Add at least three representative tasks for .NET, Python, Vue, and
  React/TypeScript before drawing a coding conclusion.
- [ ] Decide whether provider-variable OpenRouter Responses runs are acceptable
  for screening or whether OpenRouter presets must pin endpoints for Codex.
- [ ] Run code-understanding candidates directly and implementation candidates
  through `codex_exec`.
- [ ] Generate `review-packet.json` and have a new Codex task score it before
  revealing `run.json`.

## Automation benchmarks

- [x] Rotate the exposed n8n MCP bearer token and update the local Codex entry.
- [x] Add the OpenRouter credential and keep the benchmark workflows inactive.
- [x] Create an inactive Notion-style research benchmark with real Tavily public
  search and no Notion write or notification action.
- [x] Create a separately gated inactive Aula integration benchmark using the
  live read-only MCP, published prompt logic, and no Telegram action.
- [x] Consolidate the benchmark branches into one inactive n8n benchmark lab and
  archive the four prototype workflows.
- [x] Keep only the real read-only Aula MCP branch; run candidates close
  together so they see substantially similar live data.
- [x] Configure n8n-generated workflow/webhook URLs to use
  `http://home-core:15678` instead of the Tailscale-specific hostname.
- [x] Add synthetic fixtures, deterministic checks, and review rubrics for both
  workflow model stages.
- [ ] Purchase/add OpenRouter credits to the account used by the n8n credential.
  The verified credential currently returns HTTP 402 before generation.
- [ ] If running without Codex, open **HomeCompute TEST ONLY - model benchmark
  lab**, listen on the desired trigger, and export the matching local
  `http://home-core:15678/webhook-test/...` URL from the README.
- [ ] Run each initial candidate separately, restarting test-event listening
  before every request. Do not manually execute either production workflow.
- [ ] Before automating the full matrix, add header authentication and review
  LAN/tailnet exposure, then publish only the isolated benchmark workflow.
- [ ] Record the exact start time and execution order for Aula candidates so
  reviewers can account for live-data drift between runs.
- [ ] Generate the blinded review packet, complete the manual scorecards, and
  have a fresh Codex task write `judgments.jsonl` before revealing `run.json`.
