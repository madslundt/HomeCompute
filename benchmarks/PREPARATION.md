# Benchmark preparation checklist

The harness can be developed with synthetic fixtures while the real evaluation
material is collected. Do not commit private prompts, proprietary repositories,
n8n execution exports, credentials, or personal data.

## Decisions to prepare

- Exact candidate model slugs and the OpenRouter provider endpoint to pin.
- The likely GB10 artifact for each finalist: weights, immutable revision,
  quantization, runtime, tokenizer/chat template, parser, context, and MTP mode.
- One or two independent frontier judges. Judges are evaluators, not the score
  aggregator.
- Production-like sampling, context, retry, tool-call, time, and token budgets.
- Acceptance thresholds for each role before results are inspected.

## Code-understanding cases

Prepare 10–20 questions from repositories you know well. For each question,
record the expected facts and supporting file/line references. Include
architecture, change-impact, defect-location, and misleading-premise cases.
Sanitize or keep these fixtures outside Git when repository content is private.

## Coding implementation cases

Prepare at least three small tasks for each important stack, initially .NET,
Python, Vue, and React/TypeScript as required by V-CODE-001. Each case needs:

- an immutable repository revision and a disposable checkout recipe;
- a bounded specification with no hidden dependency on prior conversation;
- setup, build, lint, type-check, and test commands;
- hidden tests or behavioral assertions;
- allowed tools, network policy, timeout, retries, and context budget;
- a known-good implementation or maintainer-authored grading notes.

Do not use active working directories. The coding-agent adapter will operate on
fresh worktrees or copies and retain patches rather than mutating the source.

## n8n cases

Two production-shaped cases are already prepared: an authorized live read-only
Aula MCP integration and a Notion-style research task backed by public Tavily
search. Use these as canaries before adding historical cases.

Export the workflow definition and select 15–30 representative historical
inputs after sanitization. For every case, capture:

- normalized input and deterministic upstream responses;
- expected output fields and business-rule assertions;
- expected and forbidden tool/action calls;
- acceptable semantic variation;
- timeout and retry behavior;
- whether the input is safe for cloud processing.

Create a staging copy of the workflow. Replace email, Notion, calendar, banking,
Home Assistant, and other write-capable nodes with mocks or dry-run adapters.
Use separate test credentials and a test webhook. A successful benchmark must
not depend on undoing real side effects.

## What can proceed before the fixtures are ready

- Add and validate exact OpenRouter candidate configurations.
- Run the synthetic smoke suite and one inexpensive judge canary.
- Define score weights and acceptance thresholds.
- Implement the disposable coding-agent adapter against a synthetic repository.
- Implement the staging n8n webhook adapter against a synthetic workflow.
- Prepare result comparison and review templates.
- Later, run the same retained corpus against direct GB10 and proxied LiteLLM
  endpoints using separate release manifests.
