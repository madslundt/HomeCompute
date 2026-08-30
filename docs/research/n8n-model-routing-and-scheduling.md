# n8n model routing, scheduling, and GB10 residency

**Verified:** 2026-08-26  
**Status:** practical starting policy; exact model promotion and capacity remain
hardware-benchmark gates  
**Scope:** the owner's Notion AI Automations, web research, Aula, future
email/calendar/message/banking workflows, Home Assistant, Hermes, and Codex on
one 128 GB GB10 appliance

## Direct recommendation

Start with **one resident generative text model**:
`nvidia/Qwen3.6-35B-A3B-NVFP4`, behind the existing semantic aliases. Limit the
shared vLLM server to the repository's current **two active sequences**, and
allow only **one scheduled n8n LLM job at a time**. A user-facing request may use
the other sequence, but Home Assistant latency still has to pass the mixed-load
test. This is a conservative starting point, not a measured final capacity.

Use this routing order for every workflow:

1. deterministic code and source APIs;
2. embeddings/reranking only when searching a private corpus;
3. a small model for bounded extraction, classification, and routing;
4. the shared general model for summarization, synthesis, and ordinary tools;
5. one on-demand specialist for difficult coding or reasoning;
6. a cloud frontier model only for explicitly public/redacted work or visible
   Codex escalation.

Do not deploy all those tiers at once. Initially, tiers 1 and 4 are sufficient.
Add the small and retrieval services only when a measured workload justifies
their memory and operational cost. Load a coder or large reasoner only through
a serialized queue; do not assume either can coexist safely with every other
model and speech service.

The current Compose file launches one `text-primary` process and gives that one
model six served names: `coding`, `automation`, `research`, `home`, `meeting`,
and `assistant` ([Compose](../../deploy/compute-node/compose.yaml),
[alias ADR](../adr/004-model-aliases.md)). Therefore:

- **six aliases are not six models**;
- three Hermes profiles are not three model copies;
- `max-num-seqs=2` means at most two sequences are processed in a scheduler
  iteration, not that only two clients may hold HTTP connections; excess work
  can queue ([vLLM serve arguments](https://docs.vllm.ai/en/latest/cli/serve/));
- resident weights, active KV-cache sequences, queued jobs, and durable
  application sessions are four different capacity concepts.

## 1. Routing ladder

| Tier | Use it for | Do not use it for | Initial implementation |
| --- | --- | --- | --- |
| Deterministic code | Due-date calculation, hashes, deduplication, filtering, arithmetic, rate limits, schema validation, authorization, writes, retries, citations/source IDs, exact success tests | Summarizing free text or resolving genuinely ambiguous language | n8n Code/IF/Switch nodes and narrow application services |
| Embedding + reranker | Search over private Notion pages, work notes, manuals, email history, family documents, or code chunks | Web search itself, final answers, authorization, or choosing actions | None until a concrete RAG index exists; then test Qwen3 0.6B embedding/reranker |
| Small control LLM | Bounded classification, field extraction, priority labels, query rewriting, simple strict JSON | Open-ended research, consequential decisions, long documents, or autonomous multi-tool loops | Benchmark `Qwen/Qwen3.5-4B`; do not make it resident yet |
| Shared general LLM | Danish/English summaries, source synthesis, ordinary read-only tools, drafts, Notion change proposals, daily briefings | Executing writes directly or conclusively approving its own result | `nvidia/Qwen3.6-35B-A3B-NVFP4` behind `automation`, `research`, `assistant`, and initially `home` |
| Specialist local LLM | Repository implementation or a difficult, bounded research/verifier job | Always-on use or concurrent bulk work by assumption | Qwen3-Coder-Next for coding; Nemotron 3.5 Lightning as an English/coding challenger; gpt-oss-120b or Nemotron Super as one-at-a-time reasoner candidates |
| Cloud frontier | Public/redacted research escalation; high-risk Codex planning and review | Private Aula, email, messages, banking, `home`, `assistant`, meetings, or private Notion content | GPT-5.6 Terra for ordinary cloud escalation and GPT-5.6 Sol for highest-impact planning/review, subject to explicit policy |

OpenAI's current guidance positions Terra as its intelligence/cost balance and
Sol as the frontier choice for complex professional work
([official model guidance](https://developers.openai.com/api/docs/guides/latest-model)).
That positioning suggests an escalation order; it does not override the
repository's local-only data policy or prove that cloud escalation improves a
particular automation.

The repository already requires web search to remain an explicit capability of
the calling workflow and frequent workflows to deduplicate/compare state before
calling a model ([requirements URS-GEN-004/005](../requirements.md)). That
principle saves more capacity than routing every event to a smaller LLM.

### Model candidates and their roles

**Qwen3.6-35B-A3B NVFP4 — first shared baseline.** It is a 35B-total/3B-active
MoE model. NVIDIA publishes a Spark-specific vLLM recipe with FP8 KV cache,
40% GPU-memory utilization, reasoning and tool parsers, and a four-sequence
example. The repository deliberately starts more conservatively at 32K context,
40%, and two sequences. NVIDIA also states that its NVFP4 conversion reduces
disk and weight-memory needs by about 3.06× versus BF16
([NVIDIA model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[repository environment](../../config/compute-node.env.example)). It is the right first
artifact because it has an official single-Spark path, not because Danish,
tool accuracy, or the user's workflows have already passed.

**Qwen3.5-4B — small control candidate.** It is a dense 4B multimodal model with
native 262K context and documented agent/MCP use. Its card documents thinking
and non-thinking operation and current serving requirements
([official model card](https://huggingface.co/Qwen/Qwen3.5-4B)). Evaluate it in
non-thinking mode for short classification/extraction and as a reserved `home`
candidate. Promote it only if it meets the JSON/tool/Danish corpus and improves
latency or frees meaningful shared capacity.

**Qwen3-Coder-Next — on-demand coding candidate.** It is 80B total/3B active,
262K native context, non-thinking only, and tool-oriented. Qwen documents
`qwen3_coder` tool parsing and recommends reducing the served context to 32,768
if memory is tight ([official model card](https://huggingface.co/Qwen/Qwen3-Coder-Next)).
Use its FP8 artifact through a separate pinned recipe for Codex implementation;
do not reuse Qwen3.6's ModelOpt/Marlin/parser flags.

**Qwen3.8-27B FP8 and Gemma 4 31B NVFP4 — shared-model challengers, not extra
roles.** They are dense quality controls for general/coding work. Qwen3.8 is a
27B, 262K-context multimodal model with thinking and selectable reasoning effort;
Gemma 4 31B is a dense multimodal/function-calling model with a 256K context
([Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8),
[Gemma model card](https://huggingface.co/google/gemma-4-31B-it)). Benchmark one
at a time against Qwen3.6. If one wins, it replaces an alias mapping; it does not
need to become another permanent process.

**Nemotron 3.5 Lightning 30B-A3B NVFP4 — optional GB10 agent challenger.**
NVIDIA documents this 30B-total/3B-active Mamba-2/MoE/attention hybrid as
validated on one DGX Spark/GB10, with configurable reasoning and MTP. It is a
useful efficient comparison for English agent/tool and coding tasks
([official model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4)).
Its official supported-language list omits Danish, so it is **not** the default
for Aula, household briefings, Danish messages, or Danish Notion content.

**gpt-oss-120b and Nemotron 3 Super — local high-capacity challengers.**
gpt-oss-120b is 117B total/5.1B active, uses native MXFP4, requires the Harmony
format, and supports configurable reasoning, tools, and structured outputs.
OpenAI says it fits a single 80 GB accelerator
([official model card](https://huggingface.co/openai/gpt-oss-120b)). NVIDIA's
Nemotron 3 Super NVFP4 is 120B/12B active and explicitly lists one DGX Spark as
its minimum, but its published post-training language list omits Danish and its
runtime/parser/license tuple differs from Qwen and gpt-oss
([official model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4)).
Both are evaluation-only reasoners until real jobs prove enough quality gain to
justify cold-load time, memory, and lower availability of the shared service.

**Qwen3 Embedding/Reranker 0.6B — first private-RAG candidates.** Qwen publishes
embedding and reranking models at 0.6B, 4B, and 8B, with 32K inputs and
multilingual/code retrieval support. Start at 0.6B and move to 4B only if a
pinned retrieval fixture shows a material gain
([official Qwen3 Embedding release](https://qwenlm.github.io/blog/qwen3-embedding/)).
Embedding runs when a document changes; reranking runs when a user queries.
Neither should be called on every scheduler tick.

## 2. Workflow-specific routing and cadence

Times below are starting defaults in Europe/Copenhagen. They should be aligned
with the family's real routines and data-provider freshness. Add 2–10 minutes of
jitter so all jobs do not start at the top of an hour.

| Workload | Deterministic/retrieval stage | Model route | Recommended model frequency |
| --- | --- | --- | --- |
| Notion `AI Automations` dispatcher | Poll due active pages; calculate `next_run_at`; acquire lock; compare input/source hashes | No model in the dispatcher | Poll every 15 minutes; run each page only when its daily/weekly interval is due or a manual force flag is set |
| Simple Notion extraction/classification | Validate page schema; normalize content; exact criteria in code | Start on shared Qwen3.6; trial Qwen3.5-4B after fixtures | Only for due, materially changed pages; not every poll |
| Notion web research | Search/fetch in n8n; normalize, dedupe, cap, and identify sources; compare prior state | `research` → shared Qwen3.6; difficult public/redacted jobs may escalate to a local reasoner, then an explicitly allowed cloud route | Keep the page's daily/weekly cadence; use daily only for genuinely fast-changing subjects and weekly/monthly for slower topics |
| Notion success criteria | Exact/date/count/value checks in code; require evidence IDs for free-form criteria | Qwen3.6 produces a typed verdict; difficult/high-impact verdicts go to an independent local reasoner or `needs_review` | Once after a successful proposal; do not repeatedly ask models until one says “complete” |
| Aula | MCP fetch; normalize by message/event ID; remove duplicates; compare last summary set | `automation` → Qwen3.6 for Danish summary; Qwen3.5 only after Aula fixtures | One weekday morning summary; optionally one after-school delta summary only when new content exists; skip unchanged/weekend runs |
| Email | Webhook or delta sync, sender/rule filtering, thread grouping, dedupe, malware/attachment boundary | Qwen3.5 candidate for bounded labels; Qwen3.6 for thread summary, digest, and drafts | Sync every 5–15 minutes or event-driven; model digest 1–2 times/day; model individual mail only for allow-listed urgent classes or on demand |
| Calendar | Delta sync; exact conflict/time/travel checks in code | Usually no LLM; Qwen3.6 for briefing or ambiguous agenda synthesis | Sync every 5–15 minutes; morning briefing and next-day preview; rerun only after material changes |
| Messages | Event ingest, owner/thread identity, tombstones/edits, allow-listed urgent rules | Qwen3.6 for batched thread summaries/drafts; Qwen3.5 classifier only if its false-negative rate passes | Ingest event-driven; summarize on demand or 1–2 digests/day, not every message by default |
| Bank transactions | Daily delta import, reconciliation, totals, duplicate/subscription rules and arithmetic in code | Qwen3.5 candidate for merchant/category proposal; Qwen3.6 for explanations; difficult anomaly review can use a serialized local reasoner | Import daily after posting; weekly household review and monthly report; no per-transaction generation unless categorization is unresolved |
| Home Assistant | Deterministic Assist intent first; HA owns entity/tool authorization | `home` → initially Qwen3.6; reserve Qwen3.5-4B only if it passes 98% tool accuracy/latency requirements | Event-driven only; never scheduled; simple safety-critical behavior must work without GB10 |
| Hermes | Retrieval below `owner_scope`; application sessions remain outside GB10 | `assistant` → shared Qwen3.6 at a separately qualified 64K context | Interactive on demand; at most 1–2 useful briefings/profile/day; stagger profile schedules and dedupe family overlap |
| Coding | Codex inspects repo, runs tools/tests, and records outcomes | Qwen3-Coder-Next FP8 on demand if qualified; Qwen3.8/Gemma and Nemotron 3.5 Lightning are challengers; cloud Sol remains planning/review | User-triggered only; serialize the local specialist; do not leave a reasoner running just for occasional code jobs |
| Local private search | Embed on ingest/change; retrieve top candidates and rerank on query | Qwen3 Embedding/Reranker 0.6B, then Qwen3.6 answer with source IDs | Embedding only on changed documents; rerank and answer only per query |

For the Notion dispatcher, a failed attempt should retry through workflow policy
(for example 5 minutes, 30 minutes, then 2 hours) without changing
`last_success_at`. Retries are recovery attempts, not extra daily runs. The exact
Notion state-machine and hostile-input requirements are specified in the
[use-case audit](use-case-gap-audit.md).

## 3. MCP, web search, and structured output

MCP does not change which model is appropriate. The model sees tool schemas and
emits a function call; n8n, Hermes, or Codex executes the call and returns the
result. NVIDIA explicitly documents that the inference server itself does not
connect to MCP, and that automatic tool use requires a model-matching parser
([NVIDIA NIM tool/MCP guide](https://docs.nvidia.com/nim/large-language-models/latest/advanced-use-cases/tool-calling-and-mcp.html)).

Use this n8n pattern:

```text
trigger / due check
  -> deterministic normalize + dedupe + data classification
  -> model may propose a bounded read-only tool call
  -> validate tool name and arguments against an allow-list
  -> n8n executes MCP/search and caps/normalizes the result
  -> model returns a JSON-schema proposal with source IDs
  -> deterministic schema, citation, policy, and conflict checks
  -> n8n writes or creates needs_review
```

Practical defaults:

- Give a workflow only the tools it needs. Research receives search/fetch, not
  Notion write, email send, calendar mutation, Home Assistant writes, or banking
  actions.
- Separate read/research and write steps. The model produces a proposal; a
  deterministic n8n node performs the approved targeted mutation.
- Cap ordinary research at two or three tool rounds and a bounded source count.
  Escalate or mark `needs_review` rather than allowing an unbounded loop.
- Treat web pages, MCP results, Aula, email, messages, Notion content, and bank
  descriptions as untrusted data, never as new instructions.
- Disable parallel tool calls for workflows where ordering matters or where a
  duplicate operation would be harmful. vLLM permits parallel calls, but actual
  support is model-dependent
  ([vLLM online serving](https://docs.vllm.ai/en/latest/serving/online_serving/)).
- Use named/required tools or strict JSON Schema where possible, then validate
  again in n8n. vLLM's constrained output can guarantee schema-valid JSON; it
  cannot guarantee that the selected tool, values, facts, or citations are
  correct ([vLLM tool calling](https://docs.vllm.ai/en/stable/features/tool_calling/),
  [structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/)).
- Allow one format-repair attempt. A second failure becomes `needs_review` or,
  for explicitly public/redacted work only, a visible cloud escalation.

The parser/template/runtime are part of the model release. The current NVIDIA
Qwen3.6 derivative uses `qwen3` reasoning and `qwen3_xml` tool parsing; Coder-Next
documents `qwen3_coder`; gpt-oss requires Harmony. Changing only `MODEL_ID` is
therefore not a valid model swap.

## 4. How many models and requests can be active?

### Recommended production starting point

| Capacity concept | Start with | Meaning |
| --- | ---: | --- |
| Resident generative text models | 1 | Qwen3.6 weights loaded in one vLLM process |
| Semantic text aliases | 6 | Six stable routes to that same process; no additional weight memory |
| Active shared-model sequences | 2 | Current `VLLM_MAX_NUM_SEQS`; excess requests queue |
| Scheduled n8n generations | 1 | One P3 background generation/tool loop at a time |
| Interactive slot | 1 | Available for P0/P1 arrival only if the mixed-load latency test passes |
| Coding/reasoner specialists resident | 0 | Load through a serialized evaluation/on-demand profile |

STT, TTS, and later diarization are separate inference services and still count
against unified memory and thermal/latency capacity even though they are not
included in the “generative text model” row.

### Plausible later steady state — only after measurement

A reasonable target, not a promise, is:

- one shared Qwen3.6-class text model;
- optionally one Qwen3.5-4B reserved small/`home` model if the shared server
  misses latency or background isolation gates;
- optionally one 0.6B embedding and one 0.6B reranker service after a real RAG
  application exists;
- one coder **or** one high-capacity reasoner activated at a time, generally
  by stopping/remapping another large text service rather than stacking every
  large model in memory.

Thus the likely answer is **one large generative model always resident, perhaps
two generative processes when the second is small, plus zero to two small
retrieval models**. It is not “five large models because there are five roles.”
Do not publish a higher number before measuring the exact model revisions,
quantizations, contexts, KV cache, runtime workspaces, speech services, OS/display,
and a required 10% headroom under mixed load
([requirements URS-PERF-007/008](../requirements.md)).

DGX Spark has 128 GB unified memory at 273 GB/s, but that memory is shared by
CPU, GPU, OS, display, caches, and inference
([NVIDIA hardware specification](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)).
NVIDIA's NIM policy on unified-memory systems clamps its default vLLM allocation
to 50% to leave room for the CPU/OS; the repository's direct vLLM baseline is
even more conservative at 40%. The NIM value is evidence for preserving shared
memory, not a setting that automatically applies to this Compose deployment
([NIM advanced configuration](https://docs.nvidia.com/nim/large-language-models/latest/reference/advanced-configuration.html)).

Long advertised context lengths are model capabilities, not good production
defaults. KV memory and prefill cost grow with context and active sequences.
Keep ordinary n8n jobs bounded near the repository's 32K profile, chunk/map-reduce
larger documents, and qualify Hermes separately at 64K. Three persistent Hermes
sessions do not have to be active sequences simultaneously; concurrent 64K
turns must queue or pass the repository's M6 memory/load test
([verification strategy](../verification-strategy.md)).

### Admission and priority policy

Use the repository's priority order:

1. P0 Home Assistant voice;
2. P1 interactive Codex or Hermes;
3. P2 user-triggered n8n/meeting work;
4. P3 scheduled n8n/Hermes analysis;
5. P4 bulk transcription/diarization.

Until vLLM priority behavior is qualified end to end, enforce the important part
in n8n: background queue concurrency one, no new P3 job while a specialist swap
is active, and pause/defer long background work when the interactive service is
under pressure. `max-num-seqs` and `max-num-batched-tokens` are batching controls,
not a substitute for a consumer-aware admission queue.

## 5. Suggested weekly operating rhythm

- Run the due-page dispatcher every 15 minutes, but start no more than one
  scheduled LLM job at once.
- Put ordinary daily research, document indexing, bank import, and long summaries
  in separate windows; do not launch all “daily” jobs at midnight.
- Reserve morning capacity for Aula/family/calendar briefings and interactive
  Home Assistant. Do heavier public research later with jitter.
- Run weekly research across different weekdays. A page's `next_run_at` should
  decide eligibility, not a single weekly cron that creates a burst.
- Run monthly financial/document analyses in a low-use window and leave enough
  time for a queued job to finish before morning interactive demand.
- Start an on-demand coder/reasoner only when its queue is empty of higher-priority
  work and its readiness/alias swap can complete atomically. Record cold-load,
  warm-up, and rollback time before using this operationally.

## 6. Promotion gates

No model above is a production winner from its parameter count, publisher
benchmark, or nominal fit. Promote the exact tuple of weights revision,
quantization, tokenizer/chat template, reasoning/tool parser, runtime image,
context, and concurrency only after it passes:

1. replay of sanitized historical Notion/Aula/general automation runs;
2. Danish and English human evaluation;
3. strict JSON Schema plus malformed-input recovery;
4. real MCP/search loops, including prompt injection and duplicate tool results;
5. citation correctness and unsupported-claim rate;
6. Notion completion-verdict agreement with human/deterministic evidence;
7. p50/p95 queue time, TTFT, total time, tokens, retries, and cost;
8. Home Assistant + Codex + n8n + speech mixed load;
9. peak unified memory, 10% headroom, forced OOM recovery, cold load, and alias
   rollback;
10. for coding, accepted repository patches, build/tests, and frontier review
    without cloud reimplementation.

The most useful evaluation set already exists in practice: previous successful
`AI Automations` runs. Store frozen input, allowed tool schemas, normalized
source fixtures, expected structured result, human-approved content diff, and
completion verdict. That will answer whether Qwen3.5 can safely take a cheap
step, Qwen3.6 can own the full run, or a reasoner/cloud escalation is actually
worthwhile.

## Conclusion

Use Qwen3.6 as one shared local worker first, not as a universal answer to every
workflow step. Keep scheduling, retrieval, state, and writes deterministic;
call the LLM only on due and changed material. Start scheduled n8n concurrency
at one and shared-model sequence capacity at two. Add Qwen3.5 only after it
proves bounded control work, add 0.6B retrieval models only after private RAG
exists, and serialize Coder-Next/gpt-oss/Nemotron activation. Exact promotion
and any increase in resident models or concurrency must come from the owner's
real replay and mixed-load benchmarks on the purchased GB10.
