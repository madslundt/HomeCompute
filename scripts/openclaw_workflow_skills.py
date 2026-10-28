"""Instruction-only workflow assignments for the already installed native team."""

WORKFLOWS = {
    'cook': ('meal-planning', 'meal plans or recipes'),
    'administrator': ('household-paperwork', 'household documents or deadlines'),
    'finance': ('budget-analysis', 'budgets or spending records'),
    'personal': ('tasks-and-reminders', 'household tasks or reminders'),
    'operator': ('tasks-and-reminders', 'household maintenance tasks or reminders'),
    'family': ('tasks-and-reminders', 'shared Family tasks or reminders'),
}
SKILLS = {name for name, _ in WORKFLOWS.values()}
# Retained native roles absent from the older full-team bootstrap manifest.
RETAINED_ROLES = ('cook', 'administrator', 'finance', 'family')
