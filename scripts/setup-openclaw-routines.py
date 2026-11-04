#!/usr/bin/env python3
"""Render the recorded routine index into Atlas's skill; never changes a schedule."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = '<!-- homecompute-routines:start -->', '<!-- homecompute-routines:end -->'


def render(root=ROOT):
    cfg = json.loads((root / 'config/openclaw-routines.json').read_text())
    if (cfg['schema_version'] != 1 or cfg['timezone'] != 'Europe/Copenhagen'
            or cfg['native_cron_enabled'] is not False or cfg['ambient_heartbeats_enabled'] is not False
            or len({x['workflow_id'] for x in cfg['existing_routines']}) != len(cfg['existing_routines'])
            or any(x['backend'] != 'n8n' for x in cfg['existing_routines'])):
        raise ValueError('Retained scheduler ownership required')
    watch = cfg['task_follow_up']
    if (watch['backend'] != 'existing-household-task-timers' or watch['scope'] != 'selected-task-store'
            or watch['activation'] != 'explicit-check-time-and-expiry' or watch['max_lifetime_days'] != 30
            or watch['notifications_per_occurrence'] != 1
            or watch['quiet_hours'] != {'start': '22:00', 'end': '07:00'}
            or watch['timed_reminders_observe_quiet_hours'] is not False):
        raise ValueError('Routine policy must match the finite deployed task-watch contract')
    path = root / 'deploy/openclaw/skills/task-ownership/SKILL.md'
    text = path.read_text()
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError('Routine index markers refused')
    index = START + '\n## Recorded routine index\n\n'
    index += ('Checked ' + cfg['inventory_checked_at'] + '; all times Europe/Copenhagen. '
              'This is configuration evidence; live readiness and next-run require the owning backend.\n\n')
    index += '\n'.join('- ' + row['name'] + ': ' + row['schedule'] + '. Backend n8n; role `' + row['specialist'] + '`.'
                       for row in cfg['existing_routines']) + '\n' + END
    if START in text:
        before, rest = text.split(START); _, after = rest.split(END)
        return path, before + index + after
    return path, text.rstrip() + '\n\n' + index + '\n'


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true')
    args = p.parse_args(); path, content = render()
    if args.check:
        if path.read_text() != content: raise SystemExit('Regenerate the recorded routine index')
    else: path.write_text(content)
