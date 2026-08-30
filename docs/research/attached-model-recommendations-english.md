# GX10 — model selection, performance, and workflows

> Translator's note (2026-08-26): This is a faithful, lightly condensed English
> translation of the attached Danish document. Its model names, performance
> figures, ratings, and recommendations are translated rather than endorsed.
> See the [primary-source audit](attached-model-recommendations-audit.md) for
> corrections, current alternatives, and implications for this repository.

## 1. The most important models

I would start with **four LLM roles**, not ten different models.

| Role | Recommended model | Approx. GX10 speed | Quality | Primary use |
| --- | --- | ---: | --- | --- |
| ⚡ Fast | **Qwen3 1.7B–4B** | ~100–160+ tok/s | ⭐⭐⭐ | Routing, extraction, classification |
| 🧠 General | **Qwen3.6 35B-A3B** | ~60–110 tok/s | ⭐⭐⭐⭐½ | Assistant, email, summaries, agents |
| 💻 Coding | **Qwen3-Coder-Next 80B-A3B** | ~55–70 tok/s | ⭐⭐⭐⭐⭐ coding | Implementation, repository agent |
| 🧠 Deep reasoning | **gpt-oss-120B** | ~38–55 tok/s | ⭐⭐⭐⭐⭐ reasoning | Difficult local tasks |
| ☁️ Frontier | **GPT-5.6 Sol** | Cloud | ⭐⭐⭐⭐⭐+ | Architecture, planning, final review |

GX10 benchmarks reportedly put Qwen3.6 35B-A3B at about **86 tok/s** at
4K context with a modern NVFP4/MTP configuration, and additional speculative
decoding optimization can reportedly exceed 100 tok/s.

gpt-oss-120B reportedly delivers about **38–40 tok/s** in practical GB10 tests,
while llama.cpp can reach about 55 tok/s at short context.

## 2. How the models differ

### Qwen small — 1.7B/4B

Strengths:

- extremely low latency;
- small memory footprint and high throughput;
- good structured output and simple instruction following;
- inexpensive compute.

Weaknesses:

- weaker reasoning and easier misunderstanding of complex instructions;
- should not make important decisions;
- weaker on long, complicated documents.

A specific GX10 llama.cpp benchmark reportedly measured Qwen3 1.7B at about
**146 tok/s at ~2K context**, versus about 42 tok/s for Qwen3 8B.

Use it when the task is deterministic or simple:

```text
Is the task deterministic or simple?
            │
           YES
            ↓
        Qwen small
```

Examples include deciding whether an email is important, extracting a date,
classifying a transaction, choosing a Home Assistant tool, returning JSON,
filtering a news item, or detecting intent.

Do not use it for complex research, architecture, financial analysis, difficult
coding, or long conversations with extensive context.

## 3. Qwen3.6 35B-A3B — the default model

This would probably be the model the GX10 uses most. It has roughly **35B total
parameters** but only about **3B active parameters per token**. The intention is
to obtain greater model capacity without the same bandwidth cost as a 35B dense
model.

Reported performance is **~60–110 tok/s**, depending on runtime, quantization,
context, and speculative decoding. SparkBench reportedly measured about 86 tok/s
at 4K context, while a tuned llama.cpp/DFlash setup reportedly reached about
112 tok/s under optimal conditions.

Quality rating: ⭐⭐⭐⭐½. It is described as strong at general reasoning, agents,
tool calling, summaries, information extraction, research synthesis, ordinary
coding, and long contexts. Treat it as the local **default GPT model**.

```text
Small model
    │
    └── the task requires real understanding
                 ↓
          Qwen3.6 35B-A3B
```

Examples: summarize today's emails or Aula messages, identify priorities,
research Tesla FSD news, analyze spending, talk with Hermes, explain an error,
or make a simple implementation.

## 4. Qwen3-Coder-Next 80B-A3B

Reserve this model for **software engineering**. It has about **80B total
parameters** but roughly **3B active parameters per token**, which makes it
interesting for GX10.

Reported real-world single-user performance is **~55–70 tok/s**, depending on
runtime. A tuned vLLM setup reportedly reached about 70 tok/s alone and about
22 tok/s per user at 16 concurrent requests. A llama.cpp setup reportedly
reached about 52 tok/s without speculation and about 58 tok/s with DFlash.

At 60 tok/s, generating 4,000 tokens takes roughly:

```text
4000 / 60
≈ 67 seconds
```

Generation is therefore often faster than the agent's file reads, builds,
tests, Git operations, and tool calls.

Quality rating: ⭐⭐⭐⭐⭐ for local coding. The document describes the model as
80B total/3B active, with 262K native context and an agentic-coding focus.

```text
Approved task
     ↓
Qwen Coder
     ↓
read repository
     ↓
implement
     ↓
build
     ↓
test
     ↓
fix
     ↓
PR
```

Use Qwen General instead for explaining one function, writing a small SQL query,
updating one TypeScript type, writing a simple test, or fixing lint. Using the
large coding agent for trivial work is unnecessary.

## 5. gpt-oss-120B

This is the local **heavy reasoner**. It has **117B total parameters** and only
about **5.1B active per token**. A typical GX10 MXFP4 configuration is said to
occupy about 63 GB.

Reported single-user performance is **~38–55 tok/s**. One GB10 benchmark
reportedly measured about **40 tok/s** with reasoning enabled, while OpenAI's
evaluations show stronger reasoning than smaller open-weight models on GPQA,
mathematics, and related benchmarks.

Use it when Qwen General reaches its limit:

```text
Qwen General
     ↓
low confidence /
complex task
     ↓
gpt-oss-120B
```

Typical tasks include difficult analysis, consequential decisions, complex
agent planning, cross-comparison of many documents, difficult debugging, and
longer research synthesis.

Do not use it for Home Assistant commands, classification, email filtering,
simple summaries, or trivial coding; that would waste compute.

## 6. GPT-5.6 Sol

Sol is not replaced by GX10. It gets a different role:

```text
Local AI
   ↓
handles most work
   ↓
is frontier quality necessary?
   │
   YES
   ↓
GPT-5.6 Sol
```

Use Sol for software architecture, major design decisions, user requirements
specifications, design specifications, risk analysis, verification strategy,
complex migrations, difficult security work, final code review, and very
important research.

The hierarchy becomes:

```text
Qwen small
   ↓
Qwen General
   ↓
gpt-oss / Qwen Coder
   ↓
GPT-5.6 Sol
```

It should not be `everything → GPT-5.6 Sol`.

## 7. Personal assistant / Hermes

Normal conversation:

```text
Discord
   ↓
Hermes
   ↓
Qwen3.6 35B-A3B
   ↓
tools
   ↓
response
```

Qwen3.6 is chosen for its claimed 60–100 tok/s, conversational quality, tool
calling, reasoning, and large context.

For a simple request such as “Turn off the kitchen light,” use a small model
directly with the Home Assistant tool. There is no reason to use 35B.

For a complex request such as “Look at my calendar, emails, and Plaud notes and
tell me what I should prioritize tomorrow”:

```text
Hermes
   ↓
retrieve context
   ↓
Qwen3.6
   ↓
complex enough?
   ├─ no → response
   └─ yes
        ↓
    gpt-oss-120B
```

## 8. Home Assistant voice

Here, latency matters more than maximum intelligence.

For STT, use **NVIDIA Parakeet** or **Whisper large-v3-turbo**. Parakeet is very
fast, NVIDIA-optimized, and low-latency. Whisper is robust, multilingual, good
for Danish and English, and strong with mixed-language audio. Start with Whisper
large-v3-turbo and benchmark Parakeet against your own recordings.

```text
Microphone
   ↓
Whisper
   ↓
small Qwen
   ↓
intent
   │
   ├─ Home Assistant command
   │      ↓
   │     HA
   │
   └─ actual question
          ↓
      Qwen3.6
          ↓
         TTS
```

“Turn on the kitchen light” uses the small model. “Why is electricity usage so
high today?” uses Qwen3.6.

## 9. Emails

Use multiple model stages:

```text
New email
   ↓
Qwen small
   ↓
classification
   ↓
important?
   │
   NO → done
   │
   YES
   ↓
Qwen3.6
   ↓
summary, actions, deadline
```

If the message is genuinely complex, escalate uncertainty from Qwen3.6 to
gpt-oss.

| Task | Model |
| --- | --- |
| Spam/relevance | Small |
| Extract deadline | Small |
| Classify | Small |
| Summary | General |
| Understand implications | General |
| Difficult reasoning | gpt-oss |
| Critical professional decision | Sol |

## 10. Aula

Use the same pattern:

```text
Aula data
   ↓
small model
   ↓
extract dates, events, tasks, items
   ↓
Qwen3.6
   ↓
family summary
```

Qwen3.6 is more appropriate than gpt-oss-120B because the workflow generally
does not contain enough complex reasoning to justify the larger model.

## 11. Bank transactions

Almost always use **a small model plus ordinary code**:

```text
Transaction
   ↓
rules
   ↓
Qwen small
   ↓
category, merchant, description
```

Reduce the day's transactions to structured data. Use Qwen3.6 for questions
such as “Why did we spend 25% more this month?” Use gpt-oss-120B for a two-year
review looking for structural changes.

## 12. News monitoring

This is an ideal multi-model workflow:

```text
100 search results
        ↓
Qwen small
        ↓
relevance filter
        ↓
15 results
        ↓
deduplicate
        ↓
8 sources
        ↓
Qwen3.6
        ↓
summary
```

Ordinary monitoring stops there. If the topic requires deeper analysis,
scenarios, conflict resolution, or substantial reasoning:

```text
Qwen3.6 summary
      ↓
gpt-oss-120B
      ↓
deep analysis
```

This prevents the 120B model from reading 100 irrelevant results.

## 13. Plaud / meetings

```text
audio
 ↓
Whisper
 ↓
transcript
 ↓
small model
 ↓
people, dates, tasks, decisions
 ↓
Qwen3.6
 ↓
meeting summary
```

For a question such as “Compare this meeting with the previous five meetings
and find changes in project direction,” use retrieval → Qwen3.6 → gpt-oss.

## 14. Coding — recommended workflow

Stage 1, planning, uses **GPT-5.6 Sol** to produce requirements, URS, design
specification, architecture, risks, verification, and task breakdown.

Stage 2 classifies implementation tasks:

- Small task, such as adding validation to a DTO → Qwen General.
- Normal implementation, such as repository + API endpoint + tests → Qwen Coder.
- Difficult engineering problem, such as an intermittent multi-service
  concurrency issue → Qwen Coder, optionally assisted by gpt-oss.

## 15. Direct comparison of the coding models

| | Qwen3.6 35B | Qwen Coder 80B | gpt-oss 120B | Sol |
| --- | ---: | ---: | ---: | ---: |
| GX10 speed | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐½ | ⭐⭐⭐ | Cloud |
| Simple code | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Repository edits | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Agentic coding | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Architecture | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐½ | ⭐⭐⭐⭐⭐ |
| Deep debugging | ⭐⭐⭐½ | ⭐⭐⭐⭐½ | ⭐⭐⭐⭐½ | ⭐⭐⭐⭐⭐ |
| Tool calling | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐½ | ⭐⭐⭐⭐⭐ |
| Resource use | Low | Medium | High | Cloud |

Rule of thumb:

- **Qwen3.6:** “I know exactly what needs to change.”
- **Qwen Coder:** “Here is a task—implement it correctly in the repository.”
- **gpt-oss:** “We do not know why the system fails. Analyze the problem.”
- **Sol:** “What solution should we build at all?”

## 16. Coding agent in practice

```text
                   Requirement
                        │
                        ▼
                  GPT-5.6 Sol
                        │
               approved design
                        │
                        ▼
                 Task queue
                        │
              ┌─────────┴─────────┐
              │                   │
           simple              normal
              │                   │
              ▼                   ▼
          Qwen3.6          Qwen Coder 80B
                                  │
                            implement
                                  │
                              run tests
                                  │
                              fix errors
                                  │
                              run tests
                                  │
                                  ▼
                             local review
                                  │
                                  ▼
                            GPT-5.6 Sol
                             final review
```

## 17. Embeddings

Do not use a large LLM. Use a dedicated embedding model:

```text
document
 ↓
embedding model
 ↓
vector database
```

This should have very low latency and can support email, Plaud, Hermes memory,
documents, repositories, and news.

## 18. Reranking

Also use a dedicated small model:

```text
query
 ↓
vector search
 ↓
50 candidates
 ↓
reranker
 ↓
5 best
 ↓
Qwen3.6
```

This is better than sending all 50 documents to the LLM.

## 19. Vision

Use a dedicated smaller vision-language model, not gpt-oss:

```text
Reolink image
 ↓
object detector
 ↓
interesting?
 ↓
vision model
 ↓
description
```

Continue to use classical computer vision for person, vehicle, and package
detection. Use an LLM/VLM only when actual understanding is required.

## 20. The central routing rule

```text
                         REQUEST
                            │
                            ▼
                     deterministic?
                        │        │
                       yes       no
                        │        │
                     CODE       ▼
                           trivial/simple?
                              │       │
                             yes      no
                              │       │
                         SMALL MODEL  ▼
                                  general task?
                                   │       │
                                  yes      no
                                   │       │
                                QWEN      ▼
                                         specialized?
                                       │           │
                                     coding      reasoning
                                       │           │
                                  QWEN CODER   GPT-OSS
                                       │           │
                                       └─────┬─────┘
                                             │
                                      confidence low?
                                        │         │
                                       no        yes
                                        │         │
                                      done      SOL
```

Cloud is the final escalation level, not the default.

## 21. How different speeds will feel

- **150 tok/s:** almost instant; good for routing.
- **80 tok/s:** extremely fast chat; faster than reading speed.
- **60 tok/s:** still very fast; ideal for coding.
- **40 tok/s:** comfortably interactive; good for reasoning.
- **10 tok/s:** begins to feel slow.
- **3–5 tok/s:** mainly suitable for background workloads.

This is why MoE models are described as a good fit for GX10. A classic dense 32B
Qwen model is said to deliver about 10–11 tok/s on GB10, while newer 35B-A3B MoE
models are said to deliver about 60–100 tok/s—nearly an order of magnitude.

## 22. Expected model use

| Model | Share of requests |
| --- | ---: |
| Small | **40–50%** |
| Qwen3.6 General | **30–40%** |
| Qwen Coder | **5–15%** |
| gpt-oss | **3–8%** |
| GPT-5.6 Sol | **1–5%** |

Compute share will differ because coder/reasoner calls are much larger.

## 23. What should be loaded?

Always resident:

```text
Small LLM
embedding
STT
TTS
```

Nearly always resident:

```text
Qwen3.6 35B-A3B
```

On demand:

```text
Qwen Coder
gpt-oss-120B
large vision model
```

The coder model can load when the coding agent starts; gpt-oss can load for
background reasoning.

## 24. Prioritized model stack

If the GX10 were available tomorrow, start with:

1. **Tier 1 — always: Qwen small.** Routing, extraction, and Home Assistant.
2. **Tier 2 — default: Qwen3.6 35B-A3B.** Hermes, summaries, email, research,
   Aula, and ordinary questions.
3. **Tier 3 — coding: Qwen3-Coder-Next 80B-A3B.** Implementation agent.
4. **Tier 4 — local reasoning: gpt-oss-120B.** Difficult problems and
   background analysis.
5. **Tier 5 — frontier: GPT-5.6 Sol.** Planning, architecture, high-risk
   decisions, and final review.

The result is not “GX10 replaces GPT-5.6 Sol.” It is:

> **GX10 handles volume, agents, and implementation. GPT-5.6 Sol is used where
> the difference in model quality actually has value.**

That is presented as the most effective way to combine a local 128 GB AI server
with frontier cloud models.
