# Aula local-only qualification and cutover

This directory starts the Aula migration without changing, importing,
publishing, or executing a live n8n workflow. The qualification graph is
deliberately separate from production and has no schedule, Telegram node,
Notion node, mail node, or other write/notification action.

It preserves the currently published Aula prompt by importing the exact
`productionContext` node from the existing model-benchmark source. Do not copy
or independently edit that prompt during the provider cutover.

## Safety contract

- Inference is pinned to the semantic model alias `automation`; requests cannot
  select a model or provider.
- The only credential reference is `HomeCompute local automation only`. Create
  that as an n8n OpenAI credential whose base URL is the qualified local
  `https://ai.home.arpa/v1` endpoint and whose key is restricted to local
  automation aliases. Never attach an OpenAI, OpenRouter, or other cloud
  credential to this workflow.
- Local model calls make at most two library retries and time out after six
  minutes per attempt. There is no alternate model or cloud fallback node.
- Live qualification can use only the existing read-only Aula MCP service.
- Captured replay accepts a bounded context string and provides no tools.
- Both branches return their candidate output to the test caller. They cannot
  send it, write it to an application, or invoke an n8n schedule.
- Both test webhooks require the separate `HomeCompute qualification webhook`
  header-auth credential. Keep the imported workflow unpublished and invoke
  its one-shot test webhook only from a trusted operator path.

The gateway's local-only route and egress controls remain independent required
controls. A credential name and model alias alone do not prove that the gateway
served the request on GB10. Qualification evidence must record the gateway's
actual-backend metadata without recording prompts, Aula data, or generated text
in shared logs.

## Import preparation

`qualification.workflow.ts` is Workflow SDK source, like the existing model
benchmark sources. Compile/import it using the same pinned n8n Workflow SDK
process used for that benchmark. Credential identifiers are intentionally not
stored in Git. After import:

1. Attach only `HomeCompute local automation only` to both model subnodes.
   Attach a generated, qualification-only header secret through
   `HomeCompute qualification webhook` to both webhook triggers.
2. Verify the credential URL is exactly the local gateway and select
   `automation`. Keep Responses API disabled for this initial tool-loop parity
   test.
3. Confirm the workflow is unpublished and inspect the graph for zero schedule,
   notification, and write nodes.
4. Configure execution-data pruning before using private captured or live Aula
   data. Never commit requests, execution exports, outputs, or MCP traces.

## Qualification sequence

Start with captured replay. Send a previous, locally stored Aula context to the
one-shot replay test webhook:

```json
{
  "safety_mode": "captured-aula-data-local-only-no-side-effects",
  "case_id": "operator-private-case-id",
  "captured_system_prompt": "the exact system prompt retained with the source run",
  "captured_user_prompt": "the exact dated user prompt retained with the source run",
  "captured_context": "private captured Aula source material"
}
```

Compare the result with the corresponding known production output. The replay
branch requires the exact retained prompts so dates and run type do not drift
when an older capture is replayed. It has no MCP tools, making prompt/output
comparison side-effect-free apart from local inference. Keep this complete
payload private and delete it under the same retention policy as execution data.

After replay passes, test fresh read-only data through the live shadow branch:

```json
{
  "safety_mode": "real-aula-data-local-only-no-side-effects",
  "case_id": "operator-private-case-id"
}
```

Run it beside, not inside, the production schedule. Compare completeness,
dates, Danish formatting, Telegram HTML validity, attachment handling,
`NO_NEW_AULA_CHANGES`, latency, retries, and the actual local backend identity.
No shadow result is delivered.

## Production cutover gate

Do not turn this qualification workflow into production. Once representative
replays and live shadows pass:

1. Duplicate the currently published Aula workflow in n8n and leave the copy
   unpublished.
2. Preserve its triggers, prompt node, MCP node, Telegram formatting, and
   delivery conditions. Change only the model subnode to the fixed
   `automation` alias with the local-only credential and the bounded request
   retry values in `retry-policy.json`.
3. Temporarily disconnect Telegram and any other action nodes. Manually run the
   unpublished copy and compare its captured output with production.
4. Add the scheduled-run retry envelope from `retry-policy.json`: retain the
   run input, retry locally after 15, 30, and 60 minutes, deduplicate delivery,
   and alert the operator after two hours. That alert must not contain Aula
   content. There is no cloud path.
5. Force the local gateway unavailable and prove that the workflow queues or
   fails explicitly, sends no duplicate, and never reaches a cloud provider.
6. Stop the source schedule, reconcile in-flight executions, reconnect the
   existing Telegram delivery action, publish the qualified copy, and observe
   one run before retiring the old workflow.

The repository cannot safely automate steps 1-6 because the live workflow and
credential identifiers are intentionally absent. Record the exact live workflow
version and rollback version during the operator-owned cutover.

## Validation

Run the repository checks for these assets:

```sh
python3 tests/aula-local-workflow-test.py
jq empty automations/aula-local/retry-policy.json
```
