# AI automation manager language

- **Automation item**: One named, scheduled task with its prompt, current
  context, success criteria, schedule, status, and selected Home Assistant
  recipients.
- **Run**: One scheduled or manually requested attempt to process an automation
  item. A run records its outcome and summary; it does not itself mean the item
  is complete.
- **Current context**: The replaceable Markdown result carried forward between
  runs. It is distinct from the prompt, which describes the task to perform.
- **Success criteria**: The explicit evidence required before the item may move
  from Ready to Resolved. Missing criteria never imply success.
- **Recipient**: A Home Assistant person entity selected for delivery of that
  item's run summary. The entity must still exist when delivery is attempted.
- **Schedule**: One or more weekdays plus a Morning or Evening slot in the
  Europe/Copenhagen time zone. Morning is 07:00; Evening is 21:00.
