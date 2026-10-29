# Your agent team 🚀

**Atlas answers your ordinary messages in every channel he has joined.** He asks the relevant specialists for help and brings their results back to the same thread. Mads and Kristina share all channels outside Systems; direct-message access follows each bot's approved users.

### Atlas · Coordinator — @atlas
Your channel contact and coordinator. Helps with requests, chooses the right specialist, and brings their findings together. No @mention is needed.
[Message Atlas](https://mattermost.home.arpa/homecompute/messages/@atlas) · [Inbox](https://mattermost.home.arpa/homecompute/channels/inbox) · [OpenClaw setup](https://mattermost.home.arpa/homecompute/channels/openclaw)

### Luna · Personal, tasks and correspondence — @luna
Helps with Aula, household tasks, personal planning, correspondence drafts, and calendars. Reads your connected household Google calendars, checks availability, and creates or changes ordinary events when you ask. If you haven't named a calendar, she asks which one to use. Tracks household tasks with due dates, recurring chores, completion, and reminders through the household task tools. Correspondence supports a read-only owner Outlook bridge after OAuth setup; until connected, works from material you supply. Kristina's Outlook and work Gmail have separate staged scopes and are unavailable to this personal Luna. Sending is not enabled. Can also help with household shopping and inventory.
Try: “Am I free tomorrow afternoon?” or “Create a dentist appointment on the Mads calendar on Tuesday at 14:00 for one hour.”
[Message Luna](https://mattermost.home.arpa/homecompute/messages/@luna) · [Planning](https://mattermost.home.arpa/homecompute/channels/personal) · [Aula](https://mattermost.home.arpa/homecompute/channels/aula)

### Nova · Research and news — @nova
Researches questions, compares options, and checks public sources through web search. Provides links and explains uncertainty.
Try: “Compare these options and show me your sources.”
[Message Nova](https://mattermost.home.arpa/homecompute/messages/@nova) · [Shopping](https://mattermost.home.arpa/homecompute/channels/general-shopping) · [Research](https://mattermost.home.arpa/homecompute/channels/research) · [News](https://mattermost.home.arpa/homecompute/channels/news)

### Apollo · Coding — @apollo
Turns ideas and bugs into clear coding tasks, submits isolated jobs in reviewed repositories, and follows their progress. Publishing, merging, and deployment use the existing approval process.
[Message Apollo](https://mattermost.home.arpa/homecompute/messages/@apollo) · [Engineering](https://mattermost.home.arpa/homecompute/channels/engineering)

### Orion · Home and infrastructure — @orion
Checks HomeCompute service health and approved Home Assistant energy observations. Prepares supported lighting, heating, and cover proposals, and follows protected maintenance plans. Device proposals need separate human approval before execution.
Also helps plan physical house maintenance, appliance servicing, and seasonal chores. Household task tools record requested maintenance tasks and reminders; Ceres tracks spare parts and supplies. Computer maintenance continues through the protected infrastructure controllers.
[Message Orion](https://mattermost.home.arpa/homecompute/messages/@orion) · [Home](https://mattermost.home.arpa/homecompute/channels/home) · [Energy](https://mattermost.home.arpa/homecompute/channels/energy) · [Infrastructure](https://mattermost.home.arpa/homecompute/channels/home-operations)

### Vega · Shopping and discounts — @vega
Manages the Home Assistant shopping list, finds Tilbudstrolden offers, and manages discount watches with optional unit-price limits. Existing Home Assistant and n8n flows handle offer refreshes and notifications.
Try: “Add two cartons of milk” or “Show current coffee offers.”
[Message Vega](https://mattermost.home.arpa/homecompute/messages/@vega) · [Food & household supplies](https://mattermost.home.arpa/homecompute/channels/shopping)

### Ceres · Storage inventory — @ceres
Finds physical household stock and manages quantities, expiry dates, and items in existing storage locations. Ask Orion about computer disk space.
Try: “Where is the rice stored?” or “We used one bag of rice from the pantry.”
[Message Ceres](https://mattermost.home.arpa/homecompute/messages/@ceres) · [Food & household supplies](https://mattermost.home.arpa/homecompute/channels/shopping)

### Lyra · Travel and adventures — @lyra
Finds family holidays, local adventures, restaurants, and date-night ideas. Checks current public sources through web search and compares total group prices, dates, age limits, and booking conditions. Can read a named connected calendar and check availability when asked. Luna handles requested calendar changes; purchases and bookings need your action.
Try: “Find three family activities near Copenhagen this weekend” or “Check the Family calendar for a free weekend, then compare holiday options.”
[Message Lyra](https://mattermost.home.arpa/homecompute/messages/@lyra) · [Travel](https://mattermost.home.arpa/homecompute/channels/travel)

### Hestia · Meals and recipes — @hestia
Plans meals around your preferences, available stock, expiry dates, and current offers. Reads shopping and inventory evidence and checks public recipe sources. Vega handles requested shopping-list changes; Ceres handles stock changes. A meal plan never implies ingredients were purchased or consumed.
Try: “Plan five dinners using what we already have, prioritize food expiring soon, and show what we need to buy.”
[Message Hestia](https://mattermost.home.arpa/homecompute/messages/@hestia) · [Food & household supplies](https://mattermost.home.arpa/homecompute/channels/shopping)

### Themis · Household administration — @themis
Helps organize paperwork, warranties, subscriptions, renewals, and document deadlines. Analyzes the material you supply, distinguishes quoted terms from assumptions, and prepares questions or drafts. Ask Luna to save a resulting task or reminder. Document-store access needs an explicitly configured source; Themis cannot claim to have searched your private files.
Try: “Summarize this warranty and identify the dates and documents we need to keep.”
[Message Themis](https://mattermost.home.arpa/homecompute/messages/@themis) · [Household Admin](https://mattermost.home.arpa/homecompute/channels/household-admin)

### Plutus · Household finances — @plutus
Helps with budgets, spending categories, subscription costs, and comparisons using records you supply. Shows totals, periods, currency, missing data, and uncertain classifications. Bank accounts and financial imports require an explicitly configured source. Plutus cannot pay, transfer money, trade, or contact providers.
Try: “Use these monthly expenses to show our recurring costs and which subscriptions need review.”
[Message Plutus](https://mattermost.home.arpa/homecompute/messages/@plutus) · [Finances](https://mattermost.home.arpa/homecompute/channels/finances)

### Gaia · Shared family assistant — @gaia
Answers ordinary messages in the private Family channel for explicitly admitted adults. Has a separate workspace, shared shopping, offers, physical inventory, and shared family task tools. Creates, assigns, reschedules, completes, and cancels explicitly requested shared tasks, including daily, weekly, or monthly chores. Requested reminders go to Family; a deadline alone does not schedule a reminder. Shared tasks have their own store and never expose private owner tasks. Cannot read adult correspondence, private calendars, school messages, financial records, private owner tasks, or other agents' memories, and cannot delegate into private agents. Gaia's direct messages are disabled; use Family for shared requests. Kristina can also use Atlas in the other shared channels outside Systems.
Try: “Add a shared task to replace the filter on Sunday at 10:00, assign it to Mads, and remind us at 09:00.”
[Family](https://mattermost.home.arpa/homecompute/channels/family)

## Channel defaults

Atlas remains your contact and delegates domain work to these specialists:

| Channels | Default specialist |
| --- | --- |
| Aula, Planning | Luna |
| Research, News | Nova |
| Engineering | Apollo |
| Home (household devices), Energy (electricity and batteries), Infrastructure (servers, services and alerts) | Orion |
| Food & household supplies | Hestia for meals and recipes; Vega for household lists and offers; Ceres for stock, locations and expiry dates |
| Shopping | Nova for product research and comparisons |
| Travel; adventures and travel requests in other channels | Lyra |
| Household Admin | Themis |
| Finances | Plutus |
| Family | Gaia directly; private specialists stay outside this shared room |
| OpenClaw | Orion for operations; Apollo for code changes |

Use [OpenClaw](https://mattermost.home.arpa/homecompute/channels/openclaw) to discuss agents, tools, skills, routing, and new project channels. For example: “Plan a private renovation channel with Orion, Nova, and Apollo as its expert team.” Atlas can prepare the setup and coordinate a reviewed implementation; adding agents or granting tools remains a configuration change.

The sidebar groups **Family & plans** (Family, Planning, Aula, Travel), **Household** (Home, Energy, Food & household supplies, Shopping, Household Admin, Finances), **Research & projects** (Research, News, Engineering, Agent playground), and **Systems** (Infrastructure, OpenClaw). Inbox and Town Square are in Favorites. Home Assistant is consolidated into Home; Alerts into Infrastructure; Off-Topic into Town Square. Archived channels retain their earlier threads. News holds delivered digests; Research holds new investigations. Planning combines Personal, Calendar, Tasks and Correspondence; Food & household supplies combines meals, household lists and inventory. Shopping covers electronics, clothing, furniture, gifts and other purchases; Atlas asks Nova for current product research and comparisons. Their archived rooms retain earlier threads. Travel remains separate for holidays, restaurants and experience deals.

Mads and Kristina have access to every current channel outside **Systems**. **Infrastructure** and **OpenClaw** remain restricted to Mads. The other topic channels remain private household rooms, including their earlier threads. Direct-message histories stay separate, and mailbox connections retain their own account scopes. Task records are household records shared by the explicitly granted household specialists; keep private financial, correspondence, and document details out of task titles. Specialist workspaces separate roles. A channel name or new bot does not grant another person access.

Task reminders are delivered to Planning by Hermes once per occurrence, checked every minute outside quiet hours (22:00–07:00 Copenhagen). Completing a recurring task advances it to its next future due date. Tasks and reminders remain separate from calendar events and coding jobs. An uncertain notification is held for reconciliation instead of blindly sent again.

Shared family reminders use the separate Family task store and are delivered to Family under the same quiet hours. The existing kitchen-triggered morning briefing includes shared tasks due today and overdue tasks. Tomorrow preparation runs daily at 20:30 Copenhagen, with catch-up checks until 21:15, combining calendar events, Aula preparation and shared tasks. It uses the existing adult phone destinations and private Planning channel; private calendar and school content is not copied into Family. Missing or stale sources are labelled, and uncertain delivery claims are held instead of automatically repeated. The 20:00 Aula collection still refreshes school information; its separate evening message is replaced by the combined preparation.

Mail setup uses three separate source scopes: Mads's `outlook_mads`, Kristina's `outlook_kristina`, and work `gmail_work`. Each needs its own OAuth sign-in in [n8n credentials](https://n8n.home.arpa/home/credentials). Partner and work sources additionally need their isolated assistant runtimes before admission. The mail bridge cannot send, mark read, delete, or download attachments. It stores no mail archive, while native conversation transcripts can retain returned content. The existing family Gmail automation remains independent.

## How to work with the team

- **Direct messages:** write normally; an @mention is unnecessary.
- **Channels:** write normally to Atlas. You can ask him to involve a specialist, such as Vega for shopping. Start one thread per task and keep its follow-ups in that thread.
- **Projects:** Atlas automatically joins new HomeCompute channels you create or join, including private channels, normally within 30 seconds. Tell him the project's purpose and the experts you want to involve.
- **Context:** each task thread has its own conversation. Delegated experts receive only that task's necessary context. Only preferences you explicitly approve for use across conversations belong in shared memory.
- **Hermes · Updates — @hermes:** delivers automated updates from n8n workflows. Quote the relevant update text when asking an agent to follow up on it.
- **Changes:** explicitly requested shopping, watch, inventory, and ordinary calendar edits can be handled directly. Calendar deletion, invitations, and recurring-event changes aren't included. Device changes and production actions retain their existing approval rules.
