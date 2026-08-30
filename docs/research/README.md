# Research notes

These dated notes contain model, runtime, integration, and use-case evidence.
They support decisions but do not override accepted ADRs, requirements, or
measured results from the target hardware.

Start with these current synthesis documents:

| Topic | Entry point |
| --- | --- |
| Model installation order | [`llm-installation-recommendation.md`](llm-installation-recommendation.md) |
| Runtime | [`inference-runtime-evaluation.md`](inference-runtime-evaluation.md) |
| Gateway | [`gateway-evaluation.md`](gateway-evaluation.md) |
| Codex | [`codex-compatibility.md`](codex-compatibility.md) |
| Home Assistant | [`home-assistant-model-evaluation.md`](home-assistant-model-evaluation.md) |
| Speech | [`stt-model-evaluation.md`](stt-model-evaluation.md) and [`danish-tts-recommendation.md`](danish-tts-recommendation.md) |
| Capacity and workload routing | [`n8n-model-routing-and-scheduling.md`](n8n-model-routing-and-scheduling.md) |
| Remaining gaps | [`use-case-gap-audit.md`](use-case-gap-audit.md) |

Files prefixed `attached-model-recommendations-` preserve and audit supplied
background material. Omission, MTP, and setup-caveat notes document corrections
made after the first shortlist; the installation recommendation is the
canonical shortlist.

Recommendations expire as model releases, runtimes, and client behavior
change. Add a verification date and primary sources to new research, label
inference clearly, and require reproducible GB10 measurements before promoting
an artifact.
