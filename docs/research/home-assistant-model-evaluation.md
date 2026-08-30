# Home Assistant model evaluation

Verified: 2026-08-25

Status: benchmark plan; no production control authorization

## Architecture finding

Home Assistant remains the authority for device access. Its built-in Assist LLM
API exposes intents and only the capabilities/entities available to the built-in
conversation agent; it does not expose administrative tasks. The model chooses
tools, while Home Assistant validates and executes them.

Deterministic Assist intents should run before the LLM. `home` is for
requests that need conversational interpretation or tool reasoning.

Sources: [Home Assistant LLM API](https://developers.home-assistant.io/docs/core/llm/),
[Assist best practices](https://www.home-assistant.io/voice_control/best_practices/),
[Home Assistant local voice pipeline](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/).

## Initial model candidates

| Candidate | Purpose |
| --- | --- |
| NVIDIA Qwen3.6-35B-A3B NVFP4 | Primary Spark-qualified multilingual/tool-use candidate; 35B total/3B active; qualify MTP on/off |
| GLM-4.7-Flash | 30B/3B-active low-latency tool-use comparison |
| gpt-oss-20b | Compact Harmony/function-calling comparison |
| Devstral Small 2 24B | Control candidate for tool accuracy, despite coding specialization |

Publisher facts only establish that these models support tool-oriented serving;
none establishes safe Danish smart-home behavior. The repository benchmark is
the decision authority.

Sources: [NVIDIA Qwen3.6 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[GLM-4.7-Flash model card](https://huggingface.co/zai-org/GLM-4.7-Flash),
[gpt-oss-20b model card](https://huggingface.co/openai/gpt-oss-20b),
[Devstral Small 2 model card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512).

## Evaluation matrix

Run Danish and English cases at 10, 25, 50, and 100+ exposed entities. Include
areas, floors, aliases, duplicate friendly names, unavailable entities, and
read-only sensors.

Score:

| Measure | Weight |
| --- | ---: |
| Correct tool | 30% |
| Correct entity/entities | 20% |
| End-to-end latency | 20% |
| Hallucinated entity/action rate | 10% |
| Danish instruction quality | 10% |
| Resource usage | 5% |
| Short spoken response quality | 5% |

Dangerous, ambiguous, and exception requests must be separate test classes.
They include doors/locks, alarms, heating limits, "turn everything off except",
negation, rooms with children, unavailable devices, and requests that require
clarification. False execution is a hard failure even if the final prose sounds
correct.

## Concurrency gate

Measure voice TTFT and total response time while `coding` is generating and
while n8n uses `automation`. If the P0 voice target is missed, test—in order—a
shared smaller model, reserved memory/runtime capacity, and a separate serving
process. Do not introduce priority infrastructure before measurements require
it. The first GB10 run should test this early: anecdotal multi-second TTFT from
high-precision llama.cpp configurations would miss URS-PERF-001, but it does
not establish the performance of the selected vLLM/NVFP4 tuple.
