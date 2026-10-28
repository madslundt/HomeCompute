"""Reviewed OpenClaw team capabilities and side-effect-free configuration planning."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from openclaw_integration_policy import TOOLS as INTEGRATION_TOOLS, candidate as _integration_candidate, present as _integration_present

ROSTER = ('main', 'personal', 'researcher', 'engineer', 'operator', 'shopper', 'keeper', 'travel')
LEAVES = ROSTER[1:]
ORCHESTRATION = {'agents_list', 'sessions_spawn', 'sessions_yield', 'subagents'}
WORKSPACE_TOOLS = {'read', 'write', 'edit', 'apply_patch', 'memory_search', 'memory_get', 'session_status'}
MCP_PREFIX = 'homecompute__homecompute_'
RETRIEVAL_TOOLS = {MCP_PREFIX + name for name in ('research_search', 'school_search', 'operations_search')}
ENGINEERING = {MCP_PREFIX + name for name in ('task_submit', 'task_status', 'task_cancel')}
OPERATIONS = {MCP_PREFIX + name for name in (
    'systems', 'system_read', 'ha_propose', 'ha_status', 'ha_cancel',
    'maintenance_prepare', 'maintenance_preparation', 'maintenance_list', 'maintenance_status', 'maintenance_run', 'operations_search')}
WEB_SEARCH = {MCP_PREFIX + name for name in ('web_search', 'research_search')}
SCHOOL_READ = {MCP_PREFIX + 'school_' + name for name in ('discover', 'calendar', 'posts', 'threads', 'thread', 'weekly_letter', 'search')}
SHOPPING_READ = {MCP_PREFIX + 'shopping_' + name for name in ('search', 'stores', 'offers')}
SERVICE_READ = WEB_SEARCH | SCHOOL_READ | SHOPPING_READ
ROLE_SERVICE_READ = {'main': SERVICE_READ, 'personal': SCHOOL_READ | SHOPPING_READ,
                     'researcher': WEB_SEARCH, 'engineer': set(), 'operator': set(),
                     'shopper': SHOPPING_READ, 'keeper': set(), 'travel': WEB_SEARCH}
SHOPPING_RECORD_TOOLS = {MCP_PREFIX + name for name in (
    'shopping_list', 'shopping_add', 'shopping_update', 'shopping_complete', 'shopping_remove',
    'discounts_read', 'discount_watch', 'discount_unwatch')}
STORAGE_RECORD_TOOLS = {MCP_PREFIX + name for name in (
    'storage_list', 'storage_add', 'storage_update', 'storage_remove', 'storage_adjust')}
REQUEST_STATUS = {MCP_PREFIX + 'household_request_status'}
HOUSEHOLD_TOOLS = SHOPPING_RECORD_TOOLS | STORAGE_RECORD_TOOLS | REQUEST_STATUS
ROLE_HOUSEHOLD_TOOLS = {'main': HOUSEHOLD_TOOLS, 'personal': HOUSEHOLD_TOOLS,
                        'researcher': set(), 'engineer': set(), 'operator': set(),
                        'shopper': SHOPPING_RECORD_TOOLS | REQUEST_STATUS,
                        'keeper': STORAGE_RECORD_TOOLS | REQUEST_STATUS, 'travel': set()}
CALENDAR_TOOLS = {MCP_PREFIX + 'calendar_' + name for name in (
    'list', 'events', 'availability', 'create', 'update', 'request_status')}
CALENDAR_READ = {MCP_PREFIX + 'calendar_' + name for name in ('list', 'events', 'availability')}
LEGACY_SERVICE_TOOLS = (SERVICE_READ - RETRIEVAL_TOOLS) | HOUSEHOLD_TOOLS
SERVICE_TOOLS = LEGACY_SERVICE_TOOLS | CALENDAR_TOOLS | (RETRIEVAL_TOOLS & SERVICE_READ)
COORDINATOR_DOMAIN_TOOLS = ENGINEERING | OPERATIONS | SERVICE_TOOLS | {'browser'}
ROLE_SERVICE_TOOLS = {role: tools | ROLE_HOUSEHOLD_TOOLS[role]
                      | (CALENDAR_TOOLS if role in {'main', 'personal'} else CALENDAR_READ if role == 'travel' else set())
                      for role, tools in ROLE_SERVICE_READ.items()}
MANAGED_SKILLS = {'homecompute-operations', 'household-summary', 'incident-triage',
                  'household-shopping', 'discount-shopping', 'household-storage', 'google-calendar', 'travel-adventures',
                  'public-web-research'}
REVIEWED_SKILLS = MANAGED_SKILLS | {'browser-automation'}
FORBIDDEN = {'exec', 'process', 'sessions_send', 'sessions_list', 'sessions_history', 'sessions_search',
             'github_publish', 'canvas', 'screen', 'terminal', 'group:nodes', 'group:automation', 'group:messaging'}
PROTECTED = ('models', 'gateway', 'proxy', 'plugins', 'mcp', 'browser', 'acp', 'hooks', 'cron', 'memory',
             'channels', 'bindings')
class TeamRefused(Exception):
    """Fixed diagnostic only; never disclose native configuration or credentials."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if (not isinstance(value, dict)
            or set(value) != {'schema_version', 'model', 'subagent_timeout_seconds', 'agents', 'channel_routes'}
            or value['schema_version'] != 1 or value['model'] != 'inference/automation-moe'
            or type(value['subagent_timeout_seconds']) is not int
            or not 30 <= value['subagent_timeout_seconds'] <= 600
            or not isinstance(value['agents'], dict) or set(value['agents']) != set(ROSTER)):
        raise TeamRefused('team-manifest-refused')
    routes = value['channel_routes']
    if (not isinstance(routes, dict) or not 1 <= len(routes) <= 64
            or any(not isinstance(key, str) or not re.fullmatch('[a-z0-9]{26}', key)
                   or not isinstance(row, dict) or set(row) != {'name', 'agent'}
                   or row['agent'] not in LEAVES or not isinstance(row['name'], str)
                   or not re.fullmatch('[a-z0-9-]{1,64}', row['name'])
                   for key, row in routes.items())):
        raise TeamRefused('trusted-channel-routes-required')
    for role in value['agents'].values():
        if (not isinstance(role, dict) or set(role) != {'name', 'description', 'skills', 'instructions'}
                or any(not isinstance(role[key], str) or not 1 <= len(role[key]) <= 4000
                       for key in ('name', 'description', 'instructions'))
                or not isinstance(role['skills'], list)
                or any(not isinstance(name, str) for name in role['skills'])
                or not set(role['skills']) <= REVIEWED_SKILLS):
            raise TeamRefused('team-role-refused')
    return value


def finite(value: Any) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(name, str) or '*' in name for name in value):
        raise TeamRefused('finite-tool-policy-required')
    return set(value)


def calendar_upgrade(before: dict[str, Any], child_allow: set[str], child_deny: set[str],
                     *, enabled: bool) -> None:
    """Admit one reviewed additive upgrade; distinguish it from later revocation."""
    tools, entries = before['tools'], before['agents']['entries']
    legacy = WORKSPACE_TOOLS | (COORDINATOR_DOMAIN_TOOLS - CALENDAR_TOOLS - RETRIEVAL_TOOLS)
    if not legacy <= child_allow - child_deny:
        raise TeamRefused('revoked-child-capability-refused')
    registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
    if not LEGACY_SERVICE_TOOLS <= registered:
        raise TeamRefused('revoked-global-capability-refused')
    if CALENDAR_TOOLS & (child_deny | finite(tools.get('deny', []))):
        raise TeamRefused('calendar-tool-policy-denied')
    for role in ('main', 'personal'):
        role_denied = finite(entries.get(role, {}).get('tools', {}).get('deny', []))
        if CALENDAR_TOOLS & role_denied and not (role == 'main' and COORDINATOR_DOMAIN_TOOLS <= role_denied):
            raise TeamRefused('calendar-tool-policy-denied')
    present = CALENDAR_TOOLS & child_allow
    if present:
        if present != CALENDAR_TOOLS or not CALENDAR_TOOLS <= registered:
            raise TeamRefused('partial-or-revoked-calendar-grant')
        for role in ('main', 'personal'):
            if not CALENDAR_TOOLS <= finite(entries.get(role, {}).get('tools', {}).get('allow', [])):
                raise TeamRefused('partial-or-revoked-calendar-grant')
        return
    previous = CALENDAR_TOOLS & registered or any(
        CALENDAR_TOOLS & finite(entry.get('tools', {}).get('allow', []))
        or 'google-calendar' in entry.get('skills', []) for entry in entries.values())
    if previous:
        raise TeamRefused('partial-or-revoked-calendar-grant')
    if not enabled:
        raise TeamRefused('reviewed-calendar-upgrade-required')


def retrieval_upgrade(before: dict[str, Any], *, enabled: bool) -> None:
    """Admit a reviewed initial grant; partial grants and revocations stay refused."""
    tools, entries = before['tools'], before['agents']['entries']
    child = tools.get('subagents', {}).get('tools', {})
    registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
    child_allowed = finite(child.get('allow', []))
    if RETRIEVAL_TOOLS & (finite(tools.get('deny', [])) | finite(child.get('deny', []))):
        raise TeamRefused('retrieval-tool-policy-denied')
    expected = {'main': RETRIEVAL_TOOLS, 'personal': {MCP_PREFIX + 'school_search'},
                'researcher': {MCP_PREFIX + 'research_search'},
                'operator': {MCP_PREFIX + 'operations_search'},
                'travel': {MCP_PREFIX + 'research_search'}}
    for role, names in expected.items():
        denied = finite(entries.get(role, {}).get('tools', {}).get('deny', []))
        if names & denied and not (role == 'main' and COORDINATOR_DOMAIN_TOOLS <= denied):
            raise TeamRefused('retrieval-tool-policy-denied')
    present = RETRIEVAL_TOOLS & (registered | child_allowed)
    role_present = any(names & finite(entries.get(role, {}).get('tools', {}).get('allow', []))
                       for role, names in expected.items())
    if present or role_present:
        if (not RETRIEVAL_TOOLS <= registered or not RETRIEVAL_TOOLS <= child_allowed
                or any(not names <= finite(entries.get(role, {}).get('tools', {}).get('allow', []))
                       for role, names in expected.items() if role in entries)):
            raise TeamRefused('partial-or-revoked-retrieval-grant')
        return
    if not enabled:
        raise TeamRefused('reviewed-retrieval-upgrade-required')


def retained_capabilities(before: dict[str, Any], *, enable_calendar_upgrade: bool = False,
                          enable_retrieval_upgrade: bool = False) -> set[str]:
    tools = before['tools']
    main = before['agents']['entries']['main']['tools']
    allowed = finite(main.get('allow', []))
    if not allowed or '*' in allowed or 'alsoAllow' in main:
        raise TeamRefused('finite-main-tool-policy-required')
    global_allow = finite(tools.get('allow', []))
    finite(tools.get('alsoAllow', []))
    denied = finite(tools.get('deny', []))
    allowed -= denied | finite(main.get('deny', []))
    child = tools.get('subagents', {}).get('tools')
    if child is None:
        if set(before['agents']['entries']) != {'main'}:
            raise TeamRefused('finite-child-tool-policy-required')
        if not enable_calendar_upgrade:
            raise TeamRefused('reviewed-calendar-upgrade-required')
    else:
        # Preserve the reviewed child ceiling; a revoked child grant requires
        # operator reconciliation rather than an implicit grant on reapply.
        if not isinstance(child, dict) or set(child) - {'allow', 'deny'}:
            raise TeamRefused('finite-child-tool-policy-required')
        child_allow = finite(child.get('allow', []))
        if not child_allow <= WORKSPACE_TOOLS | COORDINATOR_DOMAIN_TOOLS | INTEGRATION_TOOLS:
            raise TeamRefused('unreviewed-child-capability-refused')
        calendar_upgrade(before, child_allow, finite(child.get('deny', [])), enabled=enable_calendar_upgrade)
        allowed |= child_allow - finite(child.get('deny', [])) - denied
    retrieval_upgrade(before, enabled=enable_retrieval_upgrade)
    if enable_retrieval_upgrade:
        allowed |= RETRIEVAL_TOOLS
    if global_allow:
        allowed &= global_allow | (RETRIEVAL_TOOLS if enable_retrieval_upgrade else set())
    return allowed


def isolated_memory_search(config: dict[str, Any]) -> dict[str, Any]:
    """Inherit the reviewed local provider without widening a specialist corpus."""
    search = copy.deepcopy(config.get('memory', {}).get('search', {}))
    search.update({'enabled': True, 'sources': ['memory'], 'extraPaths': [],
                   'experimental': {'sessionMemory': False}})
    # Each agent's engine owns its own workspace and SQLite index. Never inherit
    # session sources or another specialist's extraPaths from the global policy.
    return search


def candidate(before: dict[str, Any], team: dict[str, Any], *, state_root: Path,
              enable_calendar_upgrade: bool = False, enable_retrieval_upgrade: bool = False) -> dict[str, Any]:
    try:
        preserve_integrations = _integration_present(before, finite)
        if preserve_integrations:
            _integration_candidate(before, finite=finite, refused=TeamRefused)
        providers = before['models']['providers']
        if (set(providers) != {'inference'} or providers['inference']['apiKey'] != 'unused'
                or providers['inference']['baseUrl'] != 'https://inference.local/v1'
                or before['agents']['defaults']['model']['primary'] != team['model']
                or before['agents']['defaults']['workspace'] != str(state_root / 'workspace')
                or not set(before['agents']['entries']) <= set(ROSTER)
                or 'main' not in before['agents']['entries']
                or before['tools']['fs']['workspaceOnly'] is not True
                or before['tools']['elevated']['enabled'] is not False):
            raise TeamRefused('managed-runtime-contract-refused')
        granted = retained_capabilities(before, enable_calendar_upgrade=enable_calendar_upgrade,
                                        enable_retrieval_upgrade=enable_retrieval_upgrade)
        if not WORKSPACE_TOOLS <= granted or not ENGINEERING | OPERATIONS <= granted:
            raise TeamRefused('existing-capability-qualification-required')
        if ('browser' not in granted or before['browser']['enabled'] is not True
                or before['plugins']['entries']['homecompute-safety']['enabled'] is not True):
            raise TeamRefused('protected-research-browser-required')
        main_denied = set(before['agents']['entries']['main']['tools'].get('deny', []))
        if (SERVICE_TOOLS & set(before['tools'].get('deny', []))
                or SERVICE_TOOLS & main_denied and not COORDINATOR_DOMAIN_TOOLS <= main_denied):
            raise TeamRefused('service-tool-policy-denied')
        result = copy.deepcopy(before)
        # Native 2026.9.1 canonicalizes these fields when a roster is written.
        # Include its explicit ownership and main-system-agent selection so a
        # verified successful native write remains idempotent on the next run.
        result['agents']['ownership'] = 'explicit'
        defaults = result['agents']['defaults']
        defaults.setdefault('compaction', {}).setdefault('memoryFlush', {})['enabled'] = False
        defaults['maxConcurrent'] = 1
        defaults['systemAgent'] = {'agentId': 'main'}
        defaults['subagents'] = {
            'maxConcurrent': 1, 'maxSpawnDepth': 1, 'maxChildrenPerAgent': len(LEAVES),
            'runTimeoutSeconds': team['subagent_timeout_seconds'], 'archiveAfterMinutes': 1440,
            'model': {'primary': team['model'], 'fallbacks': []}, 'thinking': 'off',
            'requireAgentId': True, 'allowAgents': [],
        }
        global_tools = result['tools']
        visibility = global_tools.get('sessions', {}).get('visibility')
        global_tools['sessions'] = {'visibility': 'self' if visibility == 'self' else 'tree'}
        result.setdefault('session', {}).setdefault('threadBindings', {})['defaultSpawnContext'] = 'isolated'
        global_tools['deny'] = [name for name in global_tools.get('deny', []) if name not in ORCHESTRATION]
        key = 'allow' if global_tools.get('allow') else 'alsoAllow'
        global_tools[key] = sorted(set(global_tools.get(key, [])) | ORCHESTRATION | SERVICE_TOOLS | RETRIEVAL_TOOLS)
        # The global child policy is an upper bound; each leaf narrows it below.
        global_tools['subagents'] = {'tools': {'allow': sorted(WORKSPACE_TOOLS | ENGINEERING | OPERATIONS | SERVICE_TOOLS | {'browser'}),
                                             'deny': sorted(FORBIDDEN | ORCHESTRATION)}}
        entries = result['agents']['entries']
        main = entries['main']
        main.update({key: team['agents']['main'][key] for key in ('name', 'description')})
        main['workspace'] = defaults['workspace']
        main['skills'] = sorted(set(main.get('skills', defaults.get('skills', []))) | set(team['agents']['main']['skills']))
        main['subagents'] = {'allowAgents': list(LEAVES), 'requireAgentId': True, 'delegationMode': 'suggest'}
        main['tools']['allow'] = sorted(set(main['tools']['allow']) | ORCHESTRATION | ROLE_SERVICE_TOOLS['main'] | RETRIEVAL_TOOLS)
        main['tools']['deny'] = sorted((set(main['tools'].get('deny', [])) - ORCHESTRATION)
                                      - (COORDINATOR_DOMAIN_TOOLS if COORDINATOR_DOMAIN_TOOLS <= main_denied else set())
                                      | {'sessions_list', 'sessions_history', 'sessions_search', 'sessions_send'})
        role_tools = {'personal': WORKSPACE_TOOLS, 'researcher': WORKSPACE_TOOLS | {'browser'},
                      'engineer': WORKSPACE_TOOLS | ENGINEERING, 'operator': WORKSPACE_TOOLS | OPERATIONS,
                      'shopper': WORKSPACE_TOOLS, 'keeper': WORKSPACE_TOOLS, 'travel': WORKSPACE_TOOLS | {'browser'}}
        role_tools = {role: tools | ROLE_SERVICE_TOOLS[role] for role, tools in role_tools.items()}
        for role in LEAVES:
            workspace = str(state_root / ('workspace-' + role))
            agent_dir = str(state_root / 'agents' / role / 'agent')
            prior = entries.get(role, {})
            if prior and (prior.get('workspace') != workspace or prior.get('agentDir') != agent_dir):
                raise TeamRefused('existing-specialist-location-refused')
            entries[role] = {
                'name': team['agents'][role]['name'], 'description': team['agents'][role]['description'],
                'workspace': workspace, 'agentDir': agent_dir,
                'model': {'primary': team['model'], 'fallbacks': []},
                'skills': sorted(set(prior.get('skills', [])) | set(team['agents'][role]['skills'])),
                'heartbeat': {'every': '0m'},
                'memory': {'search': isolated_memory_search(result)},
                'subagents': {'allowAgents': [], 'requireAgentId': True},
                'tools': {'allow': sorted(role_tools[role]),
                          'deny': sorted(FORBIDDEN | ORCHESTRATION | ((ENGINEERING | OPERATIONS | SERVICE_TOOLS | {'browser'}) - role_tools[role])),
                          'fs': {'workspaceOnly': True}, 'elevated': {'enabled': False}},
            }
        for section in PROTECTED:
            if result.get(section) != before.get(section):
                raise TeamRefused('managed-ownership-drift-refused')
        if preserve_integrations:
            # The intact prior grant was checked before role reconstruction.
            # Remove only this feature's old ceilings, then reapply its exact
            # reviewed roles without treating a revoked grant as new consent.
            key = 'allow' if result['tools'].get('allow') else 'alsoAllow'
            result['tools'][key] = sorted(finite(result['tools'][key]) - INTEGRATION_TOOLS)
            result['tools']['subagents']['tools']['allow'] = sorted(
                finite(result['tools']['subagents']['tools']['allow']) - INTEGRATION_TOOLS)
            for entry in result['agents']['entries'].values():
                for field in ('allow', 'deny'):
                    entry['tools'][field] = sorted(finite(entry['tools'].get(field, [])) - INTEGRATION_TOOLS)
            result = _integration_candidate(result, finite=finite, refused=TeamRefused, enable=True)
        return result
    except (KeyError, TypeError, AttributeError):
        raise TeamRefused('managed-runtime-contract-refused') from None


