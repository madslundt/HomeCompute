export type Cadence = 'time_slot' | 'hourly'
export type Slot = 'Morning' | 'Evening'
export type ItemStatus = 'Draft' | 'Ready' | 'Resolved' | 'Archived'
export type UpdateStyle = 'briefing' | 'changes' | 'status_check'
export type NotifyWhen = 'every_run' | 'on_change' | 'action_needed'
export type CompletionRule = 'success_criteria' | 'manual_archive'

export type Recipient = { id: string; name: string }

export type Automation = {
  id: string
  name: string
  task_description: string
  current_context: string
  success_criteria: string
  status: ItemStatus
  cadence: Cadence
  days_to_run: string[]
  time_to_run: Slot | null
  update_style: UpdateStyle
  notify_when: NotifyWhen
  completion_rule: CompletionRule
  project: string
  tag: string
  recipients: string[]
  recipient_names: string[]
  last_run_at: string | null
  created_at: string
  created_by: string
  updated_at: string
  last_summary?: string | null
}

export type AutomationInput = Omit<Automation, 'id' | 'recipient_names' | 'last_run_at' | 'created_at' | 'created_by' | 'updated_at' | 'last_summary'>

export type Run = {
  id: string
  automation_id: string
  schedule_key: string
  scheduled_at: string
  started_at: string
  finished_at: string | null
  state: 'running' | 'delivery_pending' | 'delivered' | 'skipped' | 'failed'
  change_summary: string | null
  recipients: string[]
  delivery_receipt: string | null
  error_code: string | null
}

export type Session = { authenticated: boolean; username?: string; csrf_token?: string }

export const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export const updateStyleLabels: Record<UpdateStyle, string> = {
  briefing: 'Fresh briefing',
  changes: 'Track changes since last run',
  status_check: 'Current status check',
}

export const notifyWhenLabels: Record<NotifyWhen, string> = {
  every_run: 'Every run',
  on_change: 'Only when there are changes',
  action_needed: 'Only when action is needed',
}

export const completionRuleLabels: Record<CompletionRule, string> = {
  success_criteria: 'Resolve when success criteria are met',
  manual_archive: 'Keep running until I archive it',
}
