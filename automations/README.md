# Automations

Repository-owned automation templates live here. They are safe starting points,
not active production workflows; import and configure them in the target
automation service before activation.

| Automation | Purpose |
| --- | --- |
| [Weekly setup update check](update-check/README.md) | Watch model, runtime, and DGX Spark recipe sources and produce a review notification when they change |
| [Isolated model benchmark](model-benchmark/README.md) | Replay cases through different OpenRouter models using real read-only Aula MCP and public Tavily search, without schedules, notifications, or production writes |
