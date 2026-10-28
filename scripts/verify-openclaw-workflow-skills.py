#!/usr/bin/env python3
"""Run one fresh synthetic native turn; inspect only its own skill-read evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

from openclaw_workflow_skills import WORKFLOWS

FIXTURES = {
    'cook': 'Four diners, one dinner. Supplied pantry: 500 g dry lentils and 400 g rice. The shopping list already has carrots. Offer prices are unknown. Propose a feasible meal, quantities, missing purchases and who would own list/stock changes. Do not claim purchases or verified prices.',
    'administrator': 'Supplied synthetic renewal letter: page 1 states renewal on 2027-02-01; page 2 says cancel at least 30 calendar days before renewal. No other pages are supplied. Explain the notice deadline with page references and assumptions. The owner asked for explanation only, not a reminder or message.',
    'finance': 'Supplied synthetic DKK records for January: card grocery purchase 150; streaming expense 50; bank card repayment 150, possibly an internal transfer covering that same grocery purchase. Show confirmed expenses and the unresolved transfer separately; do not double count or invent balances.',
    'personal': 'Private owner asks for an explanation only: replace the synthetic filter by 2027-01-15 at 18:00 Europe/Copenhagen. Propose the task scope, due_at and reminder settings without creating anything. Explain how a monthly recurring task completes and why a zone reminder cannot combine a timed reminder or calendar recurrence.',
    'operator': 'Private owner asks for an explanation only: replace the synthetic filter by 2027-01-15 at 18:00 Europe/Copenhagen. Propose the maintenance task scope, due_at and reminder settings without creating anything. Explain how a monthly recurring task completes and why a zone reminder cannot combine a timed reminder or calendar recurrence.',
    'family': 'In this shared Family fixture an approved member asks for an explanation only: replace the synthetic filter by 2027-01-15 at 18:00 Europe/Copenhagen. Propose the shared task scope, due_at and reminder settings without creating anything. Explain how a monthly recurring task completes and why a zone reminder cannot combine a timed reminder or calendar recurrence. Keep private adult sources and delegation outside this shared workflow.',
}


def write(path, value):
    with path.open('x') as stream:
        stream.write(value)
    path.chmod(0o600)


def verify(role, output, *, state_root=Path('/sandbox/.openclaw'), runner=subprocess.run):
    os.umask(0o077)
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('Private output symlink refused')
    output.mkdir(mode=0o700)
    if output.stat().st_uid != os.geteuid():
        raise ValueError('Owned output directory required')
    root = state_root
    skill = WORKFLOWS[role][0]
    path = root / ('workspace-' + role) / 'skills' / skill / 'SKILL.md'
    content = path.read_bytes()
    session = str(uuid.uuid4())
    db_path = root / 'agents' / role / 'agent/openclaw-agent.sqlite'
    # Native OpenClaw creates a role's database on its first real turn. Absence
    # is valid here; do not initialize or modify its store from this verifier.
    if db_path.exists():
        db = sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True)
        prior = db.execute('SELECT count(*) FROM transcript_events WHERE session_id=?', (session,)).fetchone()[0]
        db.close()
        if prior:
            raise ValueError('Fresh verification session required')
    message = ('This is a synthetic read-only qualification, not an action request. '
               'First use read to load your own installed workflow skill at ' + str(path) + '. '
               'Then apply that skill to the supplied fixture. Use only read for the skill file; '
               'make no service, network, memory, delegation or write calls. Do not send or deliver any message. '
               'Use supplied fixture evidence only and explain the proposed result in English.\n\n' + FIXTURES[role])
    prompt = output / 'prompt.txt'
    write(prompt, message)
    result = runner(['openclaw', 'agent', '--agent', role, '--session-id', session,
                             '--message-file', str(prompt), '--timeout', '180', '--json'],
                            capture_output=True, text=True, timeout=210)
    write(output / 'native-result.json', result.stdout)
    write(output / 'native-stderr.txt', result.stderr)
    events = []
    if db_path.exists():
        db = sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True)
        events = [json.loads(row[0]) for row in db.execute(
            'SELECT event_json FROM transcript_events WHERE session_id=? ORDER BY seq LIMIT 1000', (session,))]
        db.close()
    calls, reads, failures, answers = [], [], [], []
    heading = next(line for line in content.decode().splitlines() if line.startswith('# '))
    for event in events:
        message = event.get('message', {})
        body = message.get('content', [])
        if not isinstance(body, list):
            body = []
        if message.get('role') == 'assistant':
            for item in body:
                if item.get('type') == 'toolCall':
                    calls.append({'name': item.get('name'), 'arguments': item.get('arguments', {})})
                elif item.get('type') == 'text':
                    answers.append(item.get('text', ''))
        if message.get('role') == 'toolResult':
            if message.get('isError'):
                failures.append(message.get('toolName'))
            elif message.get('toolName') == 'read' and any(heading in item.get('text', '') for item in body):
                reads.append(message.get('toolCallId'))
    skill_calls = [call for call in calls if call['name'] == 'read'
                   and str(path) in call['arguments'].values()]
    receipt = {'role': role, 'skill': skill, 'session_id': session, 'native_exit': result.returncode,
               'skill_sha256': hashlib.sha256(content).hexdigest(), 'transcript_event_count': len(events),
               'skill_read_requested': bool(skill_calls), 'skill_body_read': bool(reads),
               'tool_names': [call['name'] for call in calls], 'tool_failures': failures,
               'answer_present': bool(answers), 'synthetic_only': True, 'delivery_requested': False}
    receipt['read_only_probe_passed'] = bool(result.returncode == 0 and skill_calls and reads and answers
                                           and not failures and all(call['name'] == 'read' for call in calls))
    write(output / 'receipt.json', json.dumps(receipt, indent=2) + '\n')
    write(output / 'answer.txt', '\n'.join(answers))
    print(json.dumps(receipt))
    return receipt['read_only_probe_passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=sorted(WORKFLOWS))
    parser.add_argument('--output', required=True, type=Path, help='new private directory; existing paths are refused')
    args = parser.parse_args()
    if not verify(args.role, args.output):
        raise SystemExit(1)
