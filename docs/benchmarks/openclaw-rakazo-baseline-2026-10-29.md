# OpenClaw and Rakazo local-model diagnostic baseline

Date: 2026-10-29. Tracking: [issue 46](https://github.com/madslundt/HomeCompute/issues/46).
Status: live baseline and bounded synthetic runtime comparison completed;
production configuration unchanged. Source/tooling remains uncommitted.
Machine-readable evidence: [sanitized aggregate](../operations/openclaw-rakazo-comparison-2026-10-29.json).
Reproduction and limits: [comparison runbook](../../benchmarks/assistant-comparison/README.md).

## Finding

The local model answered all six scored fixtures correctly in all three paths.
The clearest OpenClaw optimization opportunities are prompt/tool-schema volume,
explicit reasoning control and output-format adherence. These results do not
justify replacing OpenClaw with Rakazo: the Rakazo probe exercised its real
`PiAgentRuntime` with minimal instructions and no tools, not the complete product.

| Track | Correct supplied-evidence answers | Strict JSON | Median measured time | Median prompt tokens, including cache |
| --- | --- | --- | --- | --- |
| Local model alone | 6/6 | 6/6 | 1.62 s | 157 |
| Rakazo runtime, explicit thinking off | 6/6 | 6/6 | 2.12 s | 176 |
| Deployed OpenClaw roles | 6/6 | 1/6 | 11.83 s including SSH; 8.20 s reported runtime | 6,992 |

OpenClaw's five format failures were Markdown fences around otherwise correct
JSON. They are not calculation, private-data or action failures. The local model
and Rakazo producing unwrapped JSON shows that the base model can satisfy these
simple format requests; the production instruction mix needs investigation.

| Fixture | Direct wall time | Rakazo runtime time | OpenClaw wall time | OpenClaw prompt tokens |
| --- | --- | --- | --- | --- |
| English budget | 1.74 s | 2.13 s | 12.14 s | 6,974 |
| Danish budget | 1.50 s | 1.91 s | 11.51 s | 7,010 |
| Missing travel evidence | 2.05 s | 2.36 s | 10.47 s | 7,101 |
| Adversarial supplied source | 1.49 s | 2.11 s | 12.23 s | 6,855 |
| Due date versus reminder | 2.27 s | 2.57 s | 13.32 s | 5,276 |
| Clarified coordinator calculation | 1.08 s | 1.36 s | 6.96 s | 12,175 |

One sample per track/case is insufficient for a speed ranking or SLA. Production
load varied, order was not randomized, and the prompt/tool sets intentionally
differed. Direct/Rakazo limits were 1,024 output tokens; native OpenClaw retained
4,096. No scored turn reached those limits. Native prompt counts above include
cached input; counting only newly processed tokens would hide context volume.
The coordinator case reused 11,200 prompt tokens, illustrating why cache warmth
must accompany latency figures.

## Live baseline

- Both hosts were reachable. Their release markers were
  `07740fb8a2c94ce7f2f7d06132035fd5ae8840e7`, behind the inspected desired
  `24cd9602b18127c4dbf825d2be8fbe103e56f562`. Scoped workload lifecycle evidence
  remains separate; these markers do not prove every workload matches source.
- OpenClaw `2026.9.1 (ad6fe23)` was healthy in its existing managed sandbox.
  Twelve roles used `inference/automation-moe`, 32K context, one concurrent turn,
  declared thinking off and empty model fallbacks. Mattermost was enabled;
  Telegram was disabled. The communication adapter and agents VM were active.
- The effective native configuration hash before and after was
  `98d2c59f06dc901863c4de99850d21f82208b3e32a0b02409658915ce0375644`.
  No native gateway/sandbox or Mattermost restart was requested. Mattermost's
  observed restart count was zero; its existing start time was preserved.
- Readiness metrics reported text, embeddings, both STT routes, TTS, Wyoming
  and n8n ready. `homecompute-nas-recovery-backup.service` and
  `homecompute-platform-health.service` were already failed. The health metric
  attributed the platform failure to NAS backup readiness; other checked
  consumer readiness metrics passed.
- NAS records included an October 28 snapshot, a matching small restore proof,
  a passed repository check and separate database-import/VM-boot evidence.
  This does not clear the failed backup unit or establish a complete current
  application restore. No backup job, VM pause or recovery repair was started.
- Spark initially showed 95% GPU utilization, about 5,487 MiB available RAM and
  13,504 MiB swap used, with two running inference requests and none waiting.
  That is a loaded shared baseline, not proof of an OpenClaw memory leak.
- The running text image was
  `sha256:b80718ed664f12514a24363894c61c5d36f9ea8ca294244a149ddb672df2dc14`,
  with zero restarts. Its selected arguments included served name
  `automation-qualification`, 262,144 model context, eight sequences, 8,192
  batched tokens, 9 GiB KV cache, `qwen3_xml` tools and `qwen3` reasoning parsing.
  The effective LiteLLM `automation-moe` entry selected
  `openai/automation-qualification` through the candidate upstream environment
  reference, with no per-route `reasoning_effort` or chat-template override.
  No model was started, stopped or swapped.

The post-experiment `status`, `services`, `doctor` and `drift` checks were repeated.
Existing revision drift and readiness failures were retained, not repaired as a
side effect of qualification. Spark's missing passwordless sudo remained an
operator-access limitation, not an inference failure.

## Reasoning compatibility probe

Before the aligned matrix, an ordinary direct request without an explicit
reasoning field spent all 1,024 completion tokens on hidden reasoning, took
38.20 seconds and returned no visible answer. Rakazo with its endpoint reasoning
support flag disabled also omitted the explicit off field: it used 477 reasoning
tokens and completed in 21.36 seconds.

For the same budget fixture, direct `reasoning_effort=none` completed correctly
in 1.74 seconds with 39 output tokens and zero reasoning tokens. Configuring
Rakazo to accept standard reasoning control and selecting thinking off yielded
2.13 seconds and zero reasoning tokens. These were new intentional diagnostic
runs; failed turns were not replayed for effects or deleted from evidence.

Native OpenClaw was already configured thinking off but reported 19–105 reasoning
tokens per scored case, 436 in total. Its receipt's request-shaping description
alone does not prove the exact wire field sent to the server. A disposable
OpenClaw compatibility probe must verify that field before proposing a native
configuration or adapter change. Do not infer that reasoning should be disabled
globally for research/coding because it helped these short structured tasks.

## Prioritized OpenClaw experiments

| Priority | Experiment | Evidence and success criterion |
| --- | --- | --- |
| 1 | Qualify explicit reasoning control for short structured turns | Compare current off mapping with an explicit supported off mapping in an isolated OpenClaw configuration, same backend and prompts. Require correct answers, observed zero unintended reasoning, and no lost tool/function behavior. Keep deeper reasoning available where measured useful. |
| 2 | Reduce unnecessary tool schemas by role/task | Finance/travel/research had 6,825 schema characters; browser alone was 4,873 (71%). Atlas had 11,320 schema characters. Check effective tool exposure against role policy, then qualify omission of irrelevant schemas without changing grants or research capability. Smaller schemas must not be obtained by removing independent policy enforcement. |
| 3 | Shorten stable coordinator and role instructions | Atlas injected a 29,230-character system prompt, including 17,638 project-context characters. Specialist system prompts were roughly 13K–17K characters. Preserve domain/privacy/action rules, remove duplication and load workflow detail only when needed. Require unchanged denial/role-routing tests plus scored task/tool results. |
| 4 | Make requested output formats authoritative in presentation | Five correct JSON answers gained fences. Test a narrow exact-format rule against normal conversational and Danish replies; use schema validation where a structured consumer requires JSON. Do not blindly strip all Markdown from chat. |
| 5 | Measure warm/cold context and shared-load scheduling | Record prompt/cache tokens, native versus transport latency, backend running/waiting requests and speech contention. Keep concurrency at one until a repeatable mixed-load test supports a change. Avoid increasing context/concurrency merely to hide delays. |
| 6 | Run matched tool and collaboration workflows | Compare a bounded read/tool workflow, a specialist handoff, artifact delivery and an isolated job restart. The no-tool matrix cannot select a durable-job controller or demonstrate Rakazo's end-to-end reliability. |

For the next candidate change, keep the original fixtures and production baseline,
add cold/warm repetitions with interleaved ordering, and report completion and
policy preservation before latency improvements. Start with reasoning compatibility
and schema volume; defer a framework migration until useful tool workflows show
a measured benefit. No optimization was applied to production in this task.

## Evidence handling, exclusions and cleanup

The candidate ran on the operator workstation using immutable image index
`sha256:834d2dc044e827d4c6d7cbe00aae136da2811c49c43de0f024f8dce30fea7116`,
with verified OCI revision `953160d53fa594e6712b42ff038dd886aafc5bd6`.
It had a read-only filesystem, bounded CPU/memory, no tools, no host Docker
socket and no production credentials. This was not a separate production
assistant deployment. The network was Docker's ordinary network, not a qualified
egress allowlist; no private workload or computer execution was admitted.

All native runs used new synthetic sessions without `--deliver`. Read-only
transcript queries selected only those seven session IDs; they found six events
per session and zero tool calls or tool failures. Seven terminal receipts passed
session/provider/model/completion verification. This supports the no-tool fixture
result, not general prompt-injection or access-isolation acceptance.

The original coordinator fixture's `purchases_made` field ambiguously referred to
historical household purchases versus actions by the assistant. All three tracks
answered the same way. That case is explicitly excluded for every track and
retained in source/evidence; `coordinator-budget-v2` specifies the assistant as
the actor and is scored instead. Earlier compatibility failures also remain
separate from the aligned matrix. No coaching retry is included in the score.

The workstation relay and candidate containers were stopped and the comparison
network removed. No new model key was created: the fixed relay used existing
approved LAN inference admission. The pinned image cache and private evidence
remain on the workstation; the seven synthetic sessions remain normal native
application data. There is no installed Rakazo service, routine or config override
on either production host.

Raw receipts/answers and source prompt reports remain under an operator-owned
mode-0700 qualification directory outside Git. Only aggregate counts, hashes,
timings, known synthetic fixture identifiers and operational metadata are in
the committed-artifact candidate. Tests/validation are recorded in the handoff;
neither a passing static suite nor these synthetic turns establishes full
Grok Bot parity, production synchronization or recovery readiness.

## Source validation

- Nine focused scorer, payload-boundary, output-privacy and receipt checks passed.
- Compose rendering and offline TypeScript transpilation in the pinned image passed.
- New report/runbook link checks, JSON syntax and `git diff --check` passed.
- `./scripts/validate-repository.sh` passed, including D2 rendering and its
  containerized Nix flake/optional-module evaluations. Live browser tests retain
  their existing opt-in/dependency skips; these are not browser qualification.
- An earlier validation correctly rejected placing the disposable probe in the
  production `deploy/` project inventory. The probe was moved into `benchmarks/`;
  production deployment-isolation gates were preserved unchanged, then the full
  validator was rerun successfully.
