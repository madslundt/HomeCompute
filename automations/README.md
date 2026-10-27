# Automations

Repository-owned automation templates and selected live workflow exports live
here. Check each automation's README for whether the exported workflow is
currently active in a production n8n instance.

| Automation | Purpose |
| --- | --- |
| [Battery and HomeCompute systems review](battery-and-system-review/README.md) | Active workflow sends a weekly battery review and Sunday audit of Home Assistant, available host telemetry, automation availability, updates, and current primary-source ideas |
| [Aula local-only qualification](aula-local/README.md) | Replay captured Aula inputs and shadow the read-only Aula workflow against the fixed local `automation` alias before an operator-owned cutover |
| [Weekly HomeCompute software update review](update-check/README.md) | Watch model/runtime/NixOS sources and pinned production MCP commits; pair with GitHub Dependabot pull requests for repository-managed images and packages |
| [Weekly family holiday bargains](travel/n8n-workflow.ts) | Monday 09:00 Copenhagen search for family packages with checked totals, optional all-inclusive prices and sunny 25°C weather; forecasts or labelled five-year seasonal averages qualify offers before delivery to Mattermost Travel |
| [Isolated model benchmark](model-benchmark/README.md) | Replay cases through different OpenRouter models using real read-only Aula MCP and public Tavily search, without schedules, notifications, or production writes |
| [Synthetic investigation scaffold](agent-investigation/n8n-workflow.json) | Inactive manual-only incident adapter; HTTP event node is disabled and uses a deliberately invalid endpoint until private ingress and dedicated hook authentication are approved |

Scheduled embedding integrations are maintained as sanitized patch builders in
`scripts/workflow_{calendar_semantic,school_embeddings,research_embeddings,battery_embeddings}.py`
and `automations/semantic-shopping/nodes.mjs`, with runtime snippets under
`automations/semantic-*`. They cover shopping categories and offer relevance,
confirmed inventory categories, calendar duplicate review, Aula source archiving,
same-page Notion research, HN comment selection and coverage, and battery/system
review history. Existing exact action checks and delivery paths remain in charge;
retrieval failures restore the original context. Category examples remain advisory
until thresholds have been calibrated on real household labels.

On home-core, run `sudo python3 scripts/setup-workflow-retrieval.py --source "$PWD"`
from the repository to install the service, narrow firewall rules and gateway
route. The installer preserves seven independent profile tokens, imports and
checks encrypted n8n credentials, and retains an owner-only credential receipt
under `/var/lib/homecompute-openclaw/config`. Use the actual credential references
when generating atomic patches from fresh workflow details; validate the entire
SDK graph before saving and publishing. No live exports or credentials belong in
the repository. `sudo python3 scripts/verify-workflow-retrieval.py` checks verified
HTTPS, profile isolation and ephemeral synthetic embeddings without executing any
workflow. Existing archives use a bounded 30-day working retention; school feeds
the existing owner school archive. Run the installed `semantic-index.py` as
`homecompute-openclaw` to inspect source IDs or delete a source and block replay;
workflow archive names start with `workflow-`, while school uses `school`.
