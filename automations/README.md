# Automations

Repository-owned automation templates and selected live workflow exports live
here. Check each automation's README for whether the exported workflow is
currently active in a production n8n instance.

| Automation | Purpose |
| --- | --- |
| [Battery and HomeCompute systems review](battery-and-system-review/README.md) | Active workflow sends daily and weekly battery reviews plus a Sunday audit of Home Assistant, available host telemetry, automation availability, updates, and current primary-source ideas |
| [Aula local-only qualification](aula-local/README.md) | Replay captured Aula inputs and shadow the read-only Aula workflow against the fixed local `automation` alias before an operator-owned cutover |
| [Weekly setup update check](update-check/README.md) | Watch model, runtime, and DGX Spark recipe sources and produce a review notification when they change |
| [Isolated model benchmark](model-benchmark/README.md) | Replay cases through different OpenRouter models using real read-only Aula MCP and public Tavily search, without schedules, notifications, or production writes |
