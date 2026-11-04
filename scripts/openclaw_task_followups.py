"""One expiring unfinished-task watch in the existing reminder delivery ledger."""
from datetime import datetime, timedelta, timezone
import hashlib
import re

FIELDS = {name: {'type': 'string', 'minLength': 0, 'maxLength': 35}
          for name in ('follow_up_at', 'follow_up_expires_at')}


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Explicit follow-up instant required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    if not 2000 <= result.year <= 2100:
        raise ValueError('Follow-up date outside supported range')
    return result


def configure(task, args, now):
    for field in FIELDS:
        task.setdefault(field, '')
    if not set(args) & FIELDS.keys():
        return
    if args.get('follow_up_at') == '':
        task.update({field: '' for field in FIELDS})
        return
    if not task['follow_up_at'] or not task['follow_up_expires_at']:
        raise ValueError('Follow-up needs a check time and explicit expiry')
    at, expiry = (instant(task[field]) for field in FIELDS)
    if not now < at < expiry <= now + timedelta(days=30):
        raise ValueError('Follow-up must start in future and expire within thirty days')
    task.update(follow_up_at=at.isoformat(), follow_up_expires_at=expiry.isoformat())


def key(task):
    value = f"{task['task_id']}:follow-up:{task.get('follow_up_at', '')}:{task.get('follow_up_expires_at', '')}"
    return hashlib.sha256(value.encode()).hexdigest()


def eligible(row, task, now):
    return (task['status'] == 'open' and bool(task.get('follow_up_at'))
            and bool(task.get('follow_up_expires_at')) and row['key'] == key(task)
            and instant(task['follow_up_at']) <= now < instant(task['follow_up_expires_at']))


def enqueue(db, task, now):
    if not eligible({'key': key(task)}, task, now):
        return
    db.execute('INSERT INTO household_task_reminders '
               '(key,task_id,revision,state,created,updated,post_id,purpose) '
               "VALUES(?,?,?,'pending',?,?,NULL,'follow_up') ON CONFLICT(key) DO UPDATE SET revision=excluded.revision",
               (key(task), task['task_id'], task['revision'], now.timestamp(), now.timestamp()))


def status(task, delivery, now):
    if task['status'] != 'open' or not task.get('follow_up_at'):
        return 'disabled'
    if delivery in {'delivered', 'accepted', 'sending', 'uncertain'}:
        return delivery
    if instant(task['follow_up_expires_at']) <= now:
        return 'expired'
    return delivery or 'scheduled'
