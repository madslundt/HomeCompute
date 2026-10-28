"""Owner-only Mattermost notifications for the trusted access request service.

This module never reads Bitwarden credentials. Its runtime credential belongs
only to the existing delivery bot. The trusted installer supplies an owner DM;
the notifier cannot create channels, choose recipients, or send model text.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Callable
import urllib.error
import urllib.request
from uuid import UUID

from openclaw_bitwarden import open_directory, read_private
from openclaw_site_registry import SiteRegistry

ORIGIN = 'http://127.0.0.1:18065/api/v4'
REVIEW_ORIGIN = 'https://home-core.tail479ad.ts.net:18814'
CANONICAL_REVIEW_ORIGIN = 'https://mattermost.home.arpa:18814'
ACTORS = {'openclaw': 'OpenClaw', 'main': 'Atlas', 'personal': 'Luna', 'researcher': 'Nova', 'engineer': 'Apollo',
          'operator': 'Orion', 'shopper': 'Vega', 'keeper': 'Ceres', 'travel': 'Lyra',
          'cook': 'Hestia', 'administrator': 'Themis', 'finance': 'Plutus', 'family': 'Gaia'}
SCOPES = {'search': 'Search the registered site', 'prepare_booking': 'Prepare booking options; no reservation',
          'prepare_purchase': 'Prepare purchase options; no order', 'read': 'Read approved account information'}
Transport = Callable[[str, str, dict | None], Any]


class NotificationRefused(RuntimeError):
    """Fixed diagnostics only; no raw HTTP responses or upstream exceptions."""


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9]{26}', value):
        raise NotificationRefused('Mattermost identity refused')
    return value


def review_origin(value: object) -> str:
    if value != REVIEW_ORIGIN:
        raise NotificationRefused('Private review origin refused')
    return value


def request_projection(item: object, registry: SiteRegistry | None = None) -> dict:
    fields = {'request_id', 'actor', 'account', 'scope', 'requested_minutes', 'requested_at',
              'request_digest', 'status', 'approved_at', 'expires_at', 'account_execution_available',
              'transaction_execution_available'}
    if not isinstance(item, dict) or set(item) != fields:
        raise NotificationRefused('Access notification metadata refused')
    try:
        valid_id = isinstance(item['request_id'], str) and str(UUID(item['request_id'])) == item['request_id']
    except (ValueError, AttributeError, TypeError):
        valid_id = False
    registry = SiteRegistry() if registry is None else registry
    try:
        profile = registry.profile(item['account'])
        registry.validate_scope(item['account'], item['scope'])
    except Exception:
        raise NotificationRefused('Website notification profile refused') from None
    if (not valid_id or not isinstance(item['actor'], str) or item['actor'] not in ACTORS
            or not isinstance(item['scope'], str) or item['scope'] not in SCOPES or type(item['requested_minutes']) is not int
            or not 1 <= item['requested_minutes'] <= 1440 or type(item['requested_at']) is not int
            or item['requested_at'] < 0 or item['status'] != 'pending' or item['approved_at'] is not None
            or item['expires_at'] is not None or type(item['account_execution_available']) is not bool
            or item['account_execution_available'] and not profile['qualified']
            or item['transaction_execution_available'] is not False
            or not isinstance(item['request_digest'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['request_digest'])):
        raise NotificationRefused('Access notification metadata refused')
    immutable = {key: item[key] for key in ('request_id', 'actor', 'account', 'scope', 'requested_minutes', 'requested_at')}
    expected = hashlib.sha256(json.dumps(immutable, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if item['request_digest'] != expected:
        raise NotificationRefused('Access notification digest refused')
    return dict(item)


def render(item: dict, origin: str, registry: SiteRegistry | None = None) -> str:
    item = request_projection(item, registry)
    review_origin(origin)  # Retain the strict existing private-config contract.
    availability = ('A reviewed site adapter is available for this account and task. '
                    'This does not mean the account is signed in. A private owner sign-in step may be required. '
                    if item['account_execution_available'] else
                    'Current setup records permissions; the site adapter is not connected or qualified yet. ')
    return ('**Account access requested**\n\n'
            f'Agent: {ACTORS[item["actor"]]} ({item["actor"]})\n'
            f'Account: {item["account"]}\n'
            f'Task: {SCOPES[item["scope"]]}\n'
            f'Requested duration: {item["requested_minutes"]} minutes\n\n'
            f'[Review request]({CANONICAL_REVIEW_ORIGIN}/review/{item["request_id"]})\n\n'
            'Approve, deny or change the duration on the private page. Replies here do not approve access.\n'
            + availability +
            'Access approval submits no order; bookings and payments require separate approval.')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class MattermostNotifier:
    def __init__(self, config_file: Path, state_directory: Path, *, transport: Transport | None = None,
                 registry: SiteRegistry | None = None):
        try:
            config = json.loads(read_private(config_file.parent, config_file.name, 4096))
            if (not isinstance(config, dict)
                    or set(config) != {'schema_version', 'bot_id', 'owner_id', 'channel_id', 'bot_token', 'review_origin'}
                    or type(config['schema_version']) is not int or config['schema_version'] != 1):
                raise NotificationRefused('Notification configuration refused')
            for key in ('bot_id', 'owner_id', 'channel_id'):
                identifier(config[key])
            if (config['bot_id'] == config['owner_id'] or not isinstance(config['bot_token'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{20,200}', config['bot_token'])):
                raise NotificationRefused('Notification configuration refused')
            review_origin(config['review_origin'])
            self.config, self.directory = config, state_directory
            self.registry = SiteRegistry() if registry is None else registry
            parent = open_directory(state_directory, create=True)
            try:
                try:
                    descriptor = os.open('notifications.sqlite', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=parent)
                except FileExistsError:
                    descriptor = os.open('notifications.sqlite', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                         dir_fd=parent)
                try:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                            or stat.S_IMODE(info.st_mode) != 0o600):
                        raise NotificationRefused('Private notification state refused')
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
            self.path = state_directory / 'notifications.sqlite'
            self.transport = transport or self._http
            with self._connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS deliveries '
                           '(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,state TEXT NOT NULL,post_id TEXT,updated INTEGER NOT NULL)')
        except Exception:
            raise NotificationRefused('Private notification configuration refused') from None

    @contextlib.contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            if db.in_transaction:
                db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def _http(self, method: str, path: str, body: dict | None = None) -> Any:
        request = urllib.request.Request(ORIGIN + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.config['bot_token'], 'Content-Type': 'application/json'},
            method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=8) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise NotificationRefused('Mattermost response budget refused')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            error.close()
            raise NotificationRefused('Mattermost notification unavailable') from None
        except Exception:
            raise NotificationRefused('Mattermost notification unavailable') from None

    def _target(self) -> None:
        bot = self.transport('GET', '/users/me', None)
        channel = self.transport('GET', '/channels/' + self.config['channel_id'], None)
        members = self.transport('GET', '/channels/' + self.config['channel_id'] + '/members?page=0&per_page=3', None)
        if (not isinstance(bot, dict) or bot.get('id') != self.config['bot_id'] or bot.get('is_bot') is not True
                or bot.get('delete_at', 0) != 0 or set(bot.get('roles', '').split()) != {'system_user'}
                or not isinstance(channel, dict) or channel.get('id') != self.config['channel_id']
                or channel.get('type') != 'D' or channel.get('delete_at', 0) != 0
                or not isinstance(members, list) or len(members) != 2
                or any(not isinstance(member, dict) or member.get('channel_id') != self.config['channel_id'] for member in members)
                or {member.get('user_id') for member in members} != {self.config['bot_id'], self.config['owner_id']}):
            raise NotificationRefused('Owner-only delivery target refused')

    @staticmethod
    def _receipt(row, duplicate: bool) -> dict:
        if row['state'] != 'delivered':
            raise NotificationRefused('Notification delivery uncertain; no retry performed')
        return {'request_id': row['request_id'], 'delivery': 'delivered', 'post_id': row['post_id'], 'duplicate': duplicate}

    def __call__(self, item: dict) -> dict:
        try:
            item = request_projection(item, self.registry)
            with self._connect() as db:
                prior = db.execute('SELECT * FROM deliveries WHERE request_id=?', (item['request_id'],)).fetchone()
                if prior is not None:
                    if prior['digest'] != item['request_digest']:
                        raise NotificationRefused('Notification reference already bound')
                    return self._receipt(prior, True)
            # Reads do not mutate Mattermost. Verify the private recipient before
            # reserving a send, so wrong audience/auth can never cause a post.
            self._target()
            with self._connect() as db:
                prior = db.execute('SELECT * FROM deliveries WHERE request_id=?', (item['request_id'],)).fetchone()
                if prior is not None:
                    if prior['digest'] != item['request_digest']:
                        raise NotificationRefused('Notification reference already bound')
                    return self._receipt(prior, True)
                # A process crash or ambiguous POST permanently remains uncertain.
                # Never automatically replay a credential-bearing delivery attempt.
                db.execute('INSERT INTO deliveries VALUES(?,?,\'uncertain\',NULL,?)',
                           (item['request_id'], item['request_digest'], int(time.time())))
            message = render(item, self.config['review_origin'], self.registry)
            post = self.transport('POST', '/posts', {'channel_id': self.config['channel_id'], 'message': message})
            if (not isinstance(post, dict) or identifier(post.get('id')) != post.get('id')
                    or post.get('channel_id') != self.config['channel_id'] or post.get('user_id') != self.config['bot_id']):
                raise NotificationRefused('Notification delivery receipt refused')
            with self._connect() as db:
                db.execute("UPDATE deliveries SET state='delivered',post_id=?,updated=? WHERE request_id=?",
                           (post['id'], int(time.time()), item['request_id']))
                row = db.execute('SELECT * FROM deliveries WHERE request_id=?', (item['request_id'],)).fetchone()
                return self._receipt(row, False)
        except NotificationRefused:
            raise
        except Exception:
            raise NotificationRefused('Mattermost notification unavailable') from None

    def reconcile(self, item: dict) -> dict:
        """Recover a committed pending request without repeating an uncertain send.

        The durable reservation is written before POST. An absent reservation
        therefore permits a first attempt; delivered records return their known
        receipt, and uncertain records fail closed without any network request.
        """
        return self(item)
