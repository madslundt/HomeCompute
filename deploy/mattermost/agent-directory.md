# Your agent team 🚀

**Atlas answers your ordinary messages in every channel he has joined.** He asks the relevant specialists for help and brings their results back to the same thread. You can also message any specialist directly.

### Atlas · Coordinator — @atlas
Your channel contact and coordinator. Helps with requests, chooses the right specialist, and brings their findings together. No @mention is needed.
[Message Atlas](https://mattermost.home.arpa/homecompute/messages/@atlas) · [Inbox](https://mattermost.home.arpa/homecompute/channels/inbox) · [OpenClaw setup](https://mattermost.home.arpa/homecompute/channels/openclaw)

### Luna · Personal, Aula and Calendar — @luna
Helps with Aula messages, school calendars, personal planning, and drafts. Reads your connected household Google calendars, checks availability, and creates or changes ordinary events when you ask. If you haven't named a calendar, she asks which one to use. Can also help with household shopping and inventory.
Try: “Am I free tomorrow afternoon?” or “Create a dentist appointment on the Mads calendar on Tuesday at 14:00 for one hour.”
[Message Luna](https://mattermost.home.arpa/homecompute/messages/@luna) · [Aula](https://mattermost.home.arpa/homecompute/channels/aula) · [Calendar](https://mattermost.home.arpa/homecompute/channels/calendar) · [Personal](https://mattermost.home.arpa/homecompute/channels/personal)

### Nova · Research and news — @nova
Researches questions, compares options, and checks public sources through web search. Provides links and explains uncertainty.
Try: “Compare these options and show me your sources.”
[Message Nova](https://mattermost.home.arpa/homecompute/messages/@nova) · [Research](https://mattermost.home.arpa/homecompute/channels/research) · [News](https://mattermost.home.arpa/homecompute/channels/news)

### Apollo · Coding — @apollo
Turns ideas and bugs into clear coding tasks, submits isolated jobs in reviewed repositories, and follows their progress. Publishing, merging, and deployment use the existing approval process.
[Message Apollo](https://mattermost.home.arpa/homecompute/messages/@apollo) · [Engineering](https://mattermost.home.arpa/homecompute/channels/engineering)

### Orion · Home and infrastructure — @orion
Checks HomeCompute service health and approved Home Assistant energy observations. Prepares supported lighting, heating, and cover proposals, and follows protected maintenance plans. Device proposals need separate human approval before execution.
[Message Orion](https://mattermost.home.arpa/homecompute/messages/@orion) · [Home](https://mattermost.home.arpa/homecompute/channels/home) · [Energy](https://mattermost.home.arpa/homecompute/channels/energy) · [Infrastructure](https://mattermost.home.arpa/homecompute/channels/home-operations)

### Vega · Shopping and discounts — @vega
Manages the Home Assistant shopping list, finds Tilbudstrolden offers, and manages discount watches with optional unit-price limits. Existing Home Assistant and n8n flows handle offer refreshes and notifications.
Try: “Add two cartons of milk” or “Show current coffee offers.”
[Message Vega](https://mattermost.home.arpa/homecompute/messages/@vega) · [Shopping](https://mattermost.home.arpa/homecompute/channels/shopping)

### Ceres · Storage inventory — @ceres
Finds physical household stock and manages quantities, expiry dates, and items in existing storage locations. Ask Orion about computer disk space.
Try: “Where is the rice stored?” or “We used one bag of rice from the pantry.”
[Message Ceres](https://mattermost.home.arpa/homecompute/messages/@ceres) · [Inventory](https://mattermost.home.arpa/homecompute/channels/storage)

### Lyra · Travel and adventures — @lyra
Finds family holidays, local adventures, restaurants, and date-night ideas. Checks current public sources through web search and compares total group prices, dates, age limits, and booking conditions. Can read a named connected calendar and check availability when asked. Luna handles requested calendar changes; purchases and bookings need your action.
Try: “Find three family activities near Copenhagen this weekend” or “Check the Family calendar for a free weekend, then compare holiday options.”
[Message Lyra](https://mattermost.home.arpa/homecompute/messages/@lyra) · [Travel](https://mattermost.home.arpa/homecompute/channels/travel)

## Channel defaults

Atlas remains your contact and delegates domain work to these specialists:

| Channels | Default specialist |
| --- | --- |
| Aula, Calendar, Personal | Luna |
| Research, News | Nova |
| Engineering | Apollo |
| Home (household devices), Energy (electricity and batteries), Infrastructure (servers, services and alerts) | Orion |
| Shopping | Vega |
| Inventory (physical household stock) | Ceres |
| Travel; adventures and travel requests in other channels | Lyra |
| OpenClaw | Orion for operations; Apollo for code changes |

Use [OpenClaw](https://mattermost.home.arpa/homecompute/channels/openclaw) to discuss agents, tools, skills, routing, and new project channels. For example: “Plan a private renovation channel with Orion, Nova, and Apollo as its expert team.” Atlas can prepare the setup and coordinate a reviewed implementation; adding agents or granting tools remains a configuration change.

The sidebar groups **Family & plans** (Personal, Aula, Calendar, Travel), **Household** (Home, Energy, Shopping, Inventory), **Research & projects** (Research, News, Engineering, Agent playground), and **Systems** (Infrastructure, OpenClaw). Inbox and Town Square are in Favorites. Home Assistant is consolidated into Home; Alerts into Infrastructure; Off-Topic into Town Square. Archived channels retain their earlier threads. News holds delivered digests; Research holds new investigations. Shopping covers purchases; Inventory covers items already owned.

## How to work with the team

- **Direct messages:** write normally; an @mention is unnecessary.
- **Channels:** write normally to Atlas. You can ask him to involve a specialist, such as Vega for shopping. Start one thread per task and keep its follow-ups in that thread.
- **Projects:** Atlas automatically joins new HomeCompute channels you create or join, including private channels, normally within 30 seconds. Tell him the project's purpose and the experts you want to involve.
- **Context:** each task thread has its own conversation. Delegated experts receive only that task's necessary context. Only preferences you explicitly approve for use across conversations belong in shared memory.
- **Hermes · Updates — @hermes:** delivers automated updates from n8n workflows. Quote the relevant update text when asking an agent to follow up on it.
- **Changes:** explicitly requested shopping, watch, inventory, and ordinary calendar edits can be handled directly. Calendar deletion, invitations, and recurring-event changes aren't included. Device changes and production actions retain their existing approval rules.
