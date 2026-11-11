# AI automation manager — domain glossary

This glossary describes the parallel HomeCompute replacement for the Notion
database and its `Notion AI automations` workflow. The current Notion setup stays
intact during the transition period.

| Term | Meaning |
| --- | --- |
| Automation | One saved recurring task, topic briefing, news digest, or system check. |
| Task description | The instructions for what to research or check, including the requested detail level. This replaces the old `AI prompt` field. |
| Working context | Persistent Markdown notes from the previous run and user-provided starting context. This replaces the Notion page body. |
| Success criteria | Conditions that must all be met before the automation can resolve itself. |
| Completion rule | Resolve only when success criteria are met, or keep running until the user archives the item. |
| Update style | Fresh briefing, changes since the previous run, or current-status check. |
| Notification rule | Send every run, only when the content changes, or only when action is needed. |
| Cadence | Morning/evening time slot or hourly. Selected weekdays apply to every cadence. |
| Recipient | An active Home Assistant user who has a configured Inbox notification service. The site stores the HA user ID and resolves the display name from Home Assistant. |
| Run | One claimed scheduled execution. A run has an idempotency key, summary, and Home Assistant delivery state. |
| Project / tag | Optional editable text labels replacing the old Notion relations. |

## Status lifecycle

- **Draft** — editable and not scheduled.
- **Ready** — eligible for the selected schedule and weekdays.
- **Resolved** — completed by its success criteria or marked resolved by the user.
- **Archived** — retained for reference and excluded from schedules.

## Formatting contract

Run summaries should begin with the key result, use descriptive headings and
short paragraphs or bullets, identify dates and confidence where relevant, and
link primary sources for researched claims. The task description sets how much
detail to include. Daily news usually uses a fresh briefing; ongoing topics can
track changes; system checks usually use a current-status check. These are
defaults to choose per item, not hardcoded categories.
