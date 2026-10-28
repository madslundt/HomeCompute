"""Reviewed OpenClaw team capabilities and side-effect-free configuration planning."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from openclaw_access_policy import ACCESS_TOOLS, ACCESS_ROLES, LEGACY_ACCESS_TOOLS, candidate as _access_candidate, present as _access_present
from openclaw_integration_policy import TOOLS as INTEGRATION_TOOLS, candidate as _integration_candidate, present as _integration_present

ROSTER = ('main', 'personal', 'researcher', 'engineer', 'operator', 'shopper', 'keeper', 'travel',
          'cook', 'administrator', 'finance', 'family')
LEAVES = ROSTER[1:]
DELEGATES = tuple(role for role in LEAVES if role != 'family')
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
                     'shopper': SHOPPING_READ, 'keeper': set(), 'travel': WEB_SEARCH,
                     'cook': WEB_SEARCH | SHOPPING_READ, 'administrator': WEB_SEARCH, 'finance': WEB_SEARCH,
                     'family': SHOPPING_READ}
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
                        'keeper': STORAGE_RECORD_TOOLS | REQUEST_STATUS, 'travel': set(),
                        'cook': {MCP_PREFIX + name for name in ('shopping_list', 'discounts_read', 'storage_list')},
                        'administrator': set(), 'finance': set(), 'family': HOUSEHOLD_TOOLS}
CALENDAR_TOOLS = {MCP_PREFIX + 'calendar_' + name for name in (
    'list', 'events', 'availability', 'create', 'update', 'request_status')}
CALENDAR_READ = {MCP_PREFIX + 'calendar_' + name for name in ('list', 'events', 'availability')}
HOUSEHOLD_TASK_TOOLS = {MCP_PREFIX + 'household_task_' + name for name in (
    'list', 'create', 'update', 'complete', 'cancel')}
FAMILY_TASK_TOOLS = {MCP_PREFIX + 'family_task_' + name for name in (
    'list', 'create', 'update', 'complete', 'cancel')}
MAIL_TOOLS = {MCP_PREFIX + 'mail_' + name for name in ('list_accounts', 'search', 'thread')}
LEGACY_SERVICE_TOOLS = (SERVICE_READ - RETRIEVAL_TOOLS) | HOUSEHOLD_TOOLS
SERVICE_TOOLS = LEGACY_SERVICE_TOOLS | CALENDAR_TOOLS | (RETRIEVAL_TOOLS & SERVICE_READ)
COORDINATOR_DOMAIN_TOOLS = ENGINEERING | OPERATIONS | SERVICE_TOOLS | {'browser'}
LEGACY_COORDINATOR_DOMAIN_TOOLS = set(COORDINATOR_DOMAIN_TOOLS)
COORDINATOR_DOMAIN_TOOLS |= HOUSEHOLD_TASK_TOOLS | FAMILY_TASK_TOOLS | MAIL_TOOLS
ROLE_SERVICE_TOOLS = {role: tools | ROLE_HOUSEHOLD_TOOLS[role]
                      | (CALENDAR_TOOLS if role in {'main', 'personal'} else CALENDAR_READ if role == 'travel' else set())
                      | (HOUSEHOLD_TASK_TOOLS if role in {'main', 'personal', 'operator'} else set())
                      | (FAMILY_TASK_TOOLS if role in {'main', 'personal', 'operator', 'family'} else set())
                      | (MAIL_TOOLS if role in {'main', 'personal'} else set())
                      for role, tools in ROLE_SERVICE_READ.items()}
MANAGED_SKILLS = {'homecompute-operations', 'household-summary', 'incident-triage',
                  'household-shopping', 'discount-shopping', 'household-storage', 'google-calendar', 'travel-adventures',
                  'public-web-research', 'meal-planning', 'household-paperwork', 'budget-analysis',
                  'tasks-and-reminders'}
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


def without_mail(before: dict[str, Any]) -> dict[str, Any]:
    """Retire every mail ceiling without changing other grants or runtime state."""
    result = copy.deepcopy(before)
    policies = [result['tools'], result['tools'].get('subagents', {}).get('tools', {})]
    policies += [entry.setdefault('tools', {}) for entry in result['agents']['entries'].values()]
    for policy in policies:
        for key in ('allow', 'alsoAllow'):
            if key in policy:
                policy[key] = sorted(finite(policy[key]) - MAIL_TOOLS)
        policy['deny'] = sorted(finite(policy.get('deny', [])) | MAIL_TOOLS)
    return result


def calendar_upgrade(before: dict[str, Any], child_allow: set[str], child_deny: set[str],
                     *, enabled: bool) -> None:
    """Admit one reviewed additive upgrade; distinguish it from later revocation."""
    tools, entries = before['tools'], before['agents']['entries']
    legacy = WORKSPACE_TOOLS | (LEGACY_COORDINATOR_DOMAIN_TOOLS - CALENDAR_TOOLS - RETRIEVAL_TOOLS)
    if not legacy <= child_allow - child_deny:
        raise TeamRefused('revoked-child-capability-refused')
    registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
    if not LEGACY_SERVICE_TOOLS <= registered:
        raise TeamRefused('revoked-global-capability-refused')
    if CALENDAR_TOOLS & (child_deny | finite(tools.get('deny', []))):
        raise TeamRefused('calendar-tool-policy-denied')
    for role in ('main', 'personal'):
        role_denied = finite(entries.get(role, {}).get('tools', {}).get('deny', []))
        if CALENDAR_TOOLS & role_denied and not (role == 'main' and LEGACY_COORDINATOR_DOMAIN_TOOLS <= role_denied):
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
        if names & denied and not (role == 'main' and LEGACY_COORDINATOR_DOMAIN_TOOLS <= denied):
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


def additive_upgrade(before: dict[str, Any], names: set[str], roles: tuple[str, ...],
                     label: str, *, enabled: bool) -> None:
    """Require review for an initial grant; never restore a partial or denied grant."""
    tools, entries = before['tools'], before['agents']['entries']
    child = tools.get('subagents', {}).get('tools', {})
    registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
    child_allowed = finite(child.get('allow', []))
    if names & (finite(tools.get('deny', [])) | finite(child.get('deny', []))):
        raise TeamRefused(label + '-policy-denied')
    for role in roles:
        denied = finite(entries.get(role, {}).get('tools', {}).get('deny', []))
        if names & denied and not (role == 'main' and COORDINATOR_DOMAIN_TOOLS <= denied):
            raise TeamRefused(label + '-policy-denied')
    present = names & (registered | child_allowed)
    role_present = any(names & finite(entries.get(role, {}).get('tools', {}).get('allow', []))
                       for role in roles)
    if present or role_present:
        if (not names <= registered or not names <= child_allowed
                or any(not names <= finite(entries.get(role, {}).get('tools', {}).get('allow', []))
                       for role in roles if role in entries)):
            raise TeamRefused('partial-or-revoked-' + label + '-grant')
        return
    if not enabled:
        raise TeamRefused('reviewed-' + label + '-upgrade-required')


def retained_capabilities(before: dict[str, Any], *, enable_calendar_upgrade: bool = False,
                          enable_retrieval_upgrade: bool = False,
                          enable_household_task_upgrade: bool = False,
                          enable_family_task_upgrade: bool = False,
                          enable_mail_upgrade: bool = False) -> set[str]:
    if access_present(before):
        access_candidate(before)
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
        if not child_allow <= WORKSPACE_TOOLS | COORDINATOR_DOMAIN_TOOLS | ACCESS_TOOLS | INTEGRATION_TOOLS:
            raise TeamRefused('unreviewed-child-capability-refused')
        calendar_upgrade(before, child_allow, finite(child.get('deny', [])), enabled=enable_calendar_upgrade)
        allowed |= child_allow - finite(child.get('deny', [])) - denied
    retrieval_upgrade(before, enabled=enable_retrieval_upgrade)
    additive_upgrade(before, HOUSEHOLD_TASK_TOOLS, ('main', 'personal', 'operator'),
                     'household-task', enabled=enable_household_task_upgrade)
    additive_upgrade(before, FAMILY_TASK_TOOLS, ('main', 'personal', 'operator', 'family'),
                     'family-task', enabled=enable_family_task_upgrade)
    if enable_mail_upgrade:
        additive_upgrade(before, MAIL_TOOLS, ('main', 'personal'), 'mail', enabled=True)
    if enable_retrieval_upgrade:
        allowed |= RETRIEVAL_TOOLS
    if enable_household_task_upgrade:
        allowed |= HOUSEHOLD_TASK_TOOLS
    if enable_family_task_upgrade:
        allowed |= FAMILY_TASK_TOOLS
    if enable_mail_upgrade:
        allowed |= MAIL_TOOLS
    if global_allow:
        allowed &= (global_allow | (RETRIEVAL_TOOLS if enable_retrieval_upgrade else set())
                    | (HOUSEHOLD_TASK_TOOLS if enable_household_task_upgrade else set())
                    | (FAMILY_TASK_TOOLS if enable_family_task_upgrade else set())
                    | (MAIL_TOOLS if enable_mail_upgrade else set()))
    return allowed


def access_present(before: dict[str, Any]) -> bool:
    return _access_present(before, finite)


def access_candidate(before: dict[str, Any], *, enable_access_upgrade: bool = False) -> dict[str, Any]:
    return _access_candidate(before, finite=finite, roster=ROSTER, refused=TeamRefused,
                             enable_access_upgrade=enable_access_upgrade)


def family_tasks_candidate(before: dict[str, Any], team: dict[str, Any], *, state_root: Path,
                           enable_family_task_upgrade: bool = False) -> dict[str, Any]:
    """Add only the shared task tools to an already managed, multi-provider team."""
    roles = ('main', 'personal', 'operator', 'family')
    try:
        entries, tools = before['agents']['entries'], before['tools']
        inference = before['models']['providers']['inference']
        if (not set(roles) <= set(entries) or not set(entries) <= set(ROSTER)
                or inference['apiKey'] != 'unused' or inference['baseUrl'] != 'https://inference.local/v1'
                or before['agents']['defaults']['model']['primary'] != team['model']
                or before['agents']['defaults']['workspace'] != str(state_root / 'workspace')
                or tools['fs']['workspaceOnly'] is not True or tools['elevated']['enabled'] is not False):
            raise TeamRefused('managed-family-task-contract-refused')
        for role, entry in entries.items():
            workspace = str(state_root / ('workspace' if role == 'main' else 'workspace-' + role))
            agent_dir = str(state_root / 'agents' / role / 'agent')
            if (entry.get('workspace', workspace) != workspace or entry.get('agentDir', agent_dir) != agent_dir
                    or 'alsoAllow' in entry['tools']):
                raise TeamRefused('isolated-family-task-role-required')
            finite(entry['tools']['allow']); finite(entry['tools'].get('deny', []))
        finite(tools.get('allow', [])); finite(tools.get('alsoAllow', []))
        registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
        child = tools['subagents']['tools']
        finite(child['allow']); finite(child.get('deny', []))
        if not registered or not isinstance(child, dict) or set(child) - {'allow', 'deny'}:
            raise TeamRefused('finite-family-task-parent-policy-required')
        additive_upgrade(before, FAMILY_TASK_TOOLS, roles, 'family-task', enabled=enable_family_task_upgrade)
        result = copy.deepcopy(before)
        key = 'allow' if tools.get('allow') else 'alsoAllow'
        result['tools'][key] = sorted(registered | FAMILY_TASK_TOOLS)
        result['tools']['subagents']['tools']['allow'] = sorted(finite(child['allow']) | FAMILY_TASK_TOOLS)
        for role, entry in result['agents']['entries'].items():
            allowed = finite(entry['tools']['allow'])
            denied = finite(entry['tools'].get('deny', []))
            entry['tools']['allow'] = sorted(allowed | FAMILY_TASK_TOOLS if role in roles else allowed - FAMILY_TASK_TOOLS)
            entry['tools']['deny'] = sorted(denied - FAMILY_TASK_TOOLS if role in roles else denied | FAMILY_TASK_TOOLS)
        return result
    except (KeyError, TypeError, AttributeError):
        raise TeamRefused('managed-family-task-contract-refused') from None


def isolated_memory_search(config: dict[str, Any]) -> dict[str, Any]:
    """Inherit the reviewed local provider without widening a specialist corpus."""
    search = copy.deepcopy(config.get('memory', {}).get('search', {}))
    search.update({'enabled': True, 'sources': ['memory'], 'extraPaths': [],
                   'experimental': {'sessionMemory': False}})
    # Each agent's engine owns its own workspace and SQLite index. Never inherit
    # session sources or another specialist's extraPaths from the global policy.
    return search


def candidate(before: dict[str, Any], team: dict[str, Any], *, state_root: Path,
              enable_calendar_upgrade: bool = False, enable_retrieval_upgrade: bool = False,
              enable_household_task_upgrade: bool = False, enable_family_task_upgrade: bool = False,
              enable_mail_upgrade: bool = False) -> dict[str, Any]:
    try:
        preserve_access = access_present(before)
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
                                        enable_retrieval_upgrade=enable_retrieval_upgrade,
                                        enable_household_task_upgrade=enable_household_task_upgrade,
                                        enable_family_task_upgrade=enable_family_task_upgrade,
                                        enable_mail_upgrade=enable_mail_upgrade)
        if not WORKSPACE_TOOLS <= granted or not ENGINEERING | OPERATIONS <= granted:
            raise TeamRefused('existing-capability-qualification-required')
        if ('browser' not in granted or before['browser']['enabled'] is not True
                or before['plugins']['entries']['homecompute-safety']['enabled'] is not True):
            raise TeamRefused('protected-research-browser-required')
        main_denied = set(before['agents']['entries']['main']['tools'].get('deny', []))
        if (SERVICE_TOOLS & set(before['tools'].get('deny', []))
                or SERVICE_TOOLS & main_denied and not LEGACY_COORDINATOR_DOMAIN_TOOLS <= main_denied):
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
            'maxConcurrent': 1, 'maxSpawnDepth': 1, 'maxChildrenPerAgent': len(DELEGATES),
            'runTimeoutSeconds': team['subagent_timeout_seconds'], 'archiveAfterMinutes': 1440,
            'announceTimeoutMs': 600000,
            'model': {'primary': team['model'], 'fallbacks': []}, 'thinking': 'off',
            'requireAgentId': True, 'allowAgents': [],
        }
        global_tools = result['tools']
        visibility = global_tools.get('sessions', {}).get('visibility')
        global_tools['sessions'] = {'visibility': 'self' if visibility == 'self' else 'tree'}
        result.setdefault('session', {}).setdefault('threadBindings', {})['defaultSpawnContext'] = 'isolated'
        global_tools['deny'] = [name for name in global_tools.get('deny', []) if name not in ORCHESTRATION]
        key = 'allow' if global_tools.get('allow') else 'alsoAllow'
        mail_tools = MAIL_TOOLS if enable_mail_upgrade else set()
        global_tools[key] = sorted((set(global_tools.get(key, [])) - MAIL_TOOLS) | ORCHESTRATION | SERVICE_TOOLS
                                   | RETRIEVAL_TOOLS | HOUSEHOLD_TASK_TOOLS | FAMILY_TASK_TOOLS | mail_tools)
        # The global child policy is an upper bound; each leaf narrows it below.
        global_tools['subagents'] = {'tools': {'allow': sorted(WORKSPACE_TOOLS | (COORDINATOR_DOMAIN_TOOLS - MAIL_TOOLS) | mail_tools),
                                             'deny': sorted(FORBIDDEN | ORCHESTRATION)}}
        entries = result['agents']['entries']
        main = entries['main']
        main.update({key: team['agents']['main'][key] for key in ('name', 'description')})
        main['workspace'] = defaults['workspace']
        main['skills'] = sorted(set(main.get('skills', defaults.get('skills', []))) | set(team['agents']['main']['skills']))
        main['subagents'] = {'allowAgents': list(DELEGATES), 'requireAgentId': True, 'delegationMode': 'suggest'}
        main['tools']['allow'] = sorted((set(main['tools']['allow']) | ORCHESTRATION | ROLE_SERVICE_TOOLS['main'] | RETRIEVAL_TOOLS) - (MAIL_TOOLS - mail_tools))
        main['tools']['deny'] = sorted((set(main['tools'].get('deny', [])) - ORCHESTRATION)
                                      - (COORDINATOR_DOMAIN_TOOLS if LEGACY_COORDINATOR_DOMAIN_TOOLS <= main_denied else set())
                                      | {'sessions_list', 'sessions_history', 'sessions_search', 'sessions_send'})
        role_tools = {'personal': WORKSPACE_TOOLS, 'researcher': WORKSPACE_TOOLS | {'browser'},
                      'engineer': WORKSPACE_TOOLS | ENGINEERING, 'operator': WORKSPACE_TOOLS | OPERATIONS,
                      'shopper': WORKSPACE_TOOLS, 'keeper': WORKSPACE_TOOLS, 'travel': WORKSPACE_TOOLS | {'browser'},
                      'cook': WORKSPACE_TOOLS | {'browser'}, 'administrator': WORKSPACE_TOOLS | {'browser'},
                      'finance': WORKSPACE_TOOLS | {'browser'}, 'family': WORKSPACE_TOOLS}
        role_tools = {role: (tools | ROLE_SERVICE_TOOLS[role]) - (MAIL_TOOLS - mail_tools)
                      for role, tools in role_tools.items()}
        for role in LEAVES:
            workspace = str(state_root / ('workspace-' + role))
            agent_dir = str(state_root / 'agents' / role / 'agent')
            prior = entries.get(role, {})
            if prior and (prior.get('workspace') != workspace or prior.get('agentDir') != agent_dir):
                raise TeamRefused('existing-specialist-location-refused')
            if prior:
                # A reviewed additive upgrade cannot erase a later per-role denial
                # or silently restore an operator's removed grant.
                additions = ((CALENDAR_TOOLS if enable_calendar_upgrade else set())
                             | (RETRIEVAL_TOOLS if enable_retrieval_upgrade else set())
                             | (HOUSEHOLD_TASK_TOOLS if enable_household_task_upgrade else set())
                             | (FAMILY_TASK_TOOLS if enable_family_task_upgrade else set())
                             | (MAIL_TOOLS if enable_mail_upgrade else set()))
                prior_tools = prior.get('tools', {})
                if (role_tools[role] & finite(prior_tools.get('deny', []))
                        or not role_tools[role] - additions <= finite(prior_tools.get('allow', []))):
                    raise TeamRefused('existing-specialist-capability-revoked')
            entries[role] = {
                'name': team['agents'][role]['name'], 'description': team['agents'][role]['description'],
                'workspace': workspace, 'agentDir': agent_dir,
                'model': {'primary': team['model'], 'fallbacks': []},
                'skills': sorted(set(prior.get('skills', [])) | set(team['agents'][role]['skills'])),
                'heartbeat': {'every': '0m'},
                'memory': {'search': isolated_memory_search(result)},
                'subagents': {'allowAgents': [], 'requireAgentId': True},
                'tools': {'allow': sorted(role_tools[role]),
                          'deny': sorted(FORBIDDEN | ORCHESTRATION | (COORDINATOR_DOMAIN_TOOLS - role_tools[role])
                                         | finite(prior.get('tools', {}).get('deny', []))),
                          'fs': {'workspaceOnly': True}, 'elevated': {'enabled': False}},
            }
        for section in PROTECTED:
            if result.get(section) != before.get(section):
                raise TeamRefused('managed-ownership-drift-refused')
        if preserve_access:
            # retained_capabilities checked the intact reviewed grant above.
            # Rebuild that same grant after normal role reconstruction; no new
            # capability is inferred from a later partial or revoked policy.
            key = 'allow' if result['tools'].get('allow') else 'alsoAllow'
            result['tools'][key] = sorted(finite(result['tools'][key]) - ACCESS_TOOLS)
            for entry in result['agents']['entries'].values():
                entry['tools']['deny'] = sorted(finite(entry['tools'].get('deny', [])) - ACCESS_TOOLS)
            result = access_candidate(result, enable_access_upgrade=True)
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


