"""Bounded read-only workflow failure metadata, including privacy-pruned rows."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time
from typing import Any

DATABASE = Path('/srv/state/automation/n8n/database.sqlite')
CHECK = 'n8n-workflow-failures'
WINDOW = 900


def collect(path: Path = DATABASE, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    # mode=ro preserves the live WAL while refusing to create a missing DB.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5) as db:
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 0.5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        # Do not filter deletedAt: n8n soft-deletes errors when saving is disabled.
        # Never read workflow nodes, credentials, execution_data or message bodies.
        failures = db.execute('''SELECT count(*) FROM (
            SELECT 1 FROM execution_entity e JOIN workflow_entity w ON w.id=e.workflowId
            WHERE w.active=1 AND w.isArchived=0 AND e.mode<>'manual'
              AND e.status IN ('error','crashed')
              AND julianday(e.stoppedAt)>=julianday(?)
              AND julianday(e.stoppedAt)<=julianday(?) LIMIT 10000
        )''', ((now - timedelta(seconds=WINDOW)).isoformat(), now.isoformat())).fetchone()[0]
    return dict(schema_version=1, generated_at=now.isoformat(), window_seconds=WINDOW,
                failure_count=failures)


def project(value: Any, now: datetime) -> tuple[str, dict[str, Any]]:
    """Alert on presence, so a continuing failure loop does not generate count spam."""
    try:
        if (not isinstance(value, dict) or set(value) != {
                'schema_version', 'generated_at', 'window_seconds', 'failure_count'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or type(value['window_seconds']) is not int or value['window_seconds'] != WINDOW
                or type(value['failure_count']) is not int or not 0 <= value['failure_count'] <= 10000):
            raise ValueError('invalid workflow health metadata')
        generated = datetime.fromisoformat(value['generated_at'].replace('Z', '+00:00'))
        if generated.tzinfo is None or not -30 <= (now - generated).total_seconds() <= 120:
            raise ValueError('stale workflow health metadata')
        failed = bool(value['failure_count'])
        return ('degraded' if failed else 'healthy'), {'failed': failed}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return 'degraded', {'reason': 'collection_failed'}
