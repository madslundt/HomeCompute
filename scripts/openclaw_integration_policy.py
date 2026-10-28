"""Finite specialist integrations; native identity, never tool arguments, selects a role."""
from __future__ import annotations

import copy
from typing import Any, Callable

PREFIX = 'homecompute__homecompute_'
DOCS = {PREFIX + name for name in ('docs_resolve', 'docs_query')}
PLACES = {PREFIX + name for name in ('places_search', 'places_route', 'places_weather')}
STATUS = {PREFIX + 'integration_status'}
AUTOMATION = {PREFIX + 'automation_status'}
ROLE_TOOLS = {
    'engineer': DOCS | STATUS,
    'operator': DOCS | AUTOMATION | STATUS,
    'researcher': AUTOMATION | STATUS,
    'travel': PLACES | {PREFIX + 'hotel_search'} | AUTOMATION | STATUS,
    'personal': {PREFIX + 'photo_search'} | STATUS,
    'shopper': {PREFIX + 'places_search'} | STATUS,
}
TOOLS = set().union(*ROLE_TOOLS.values())


def present(config: dict, finite: Callable[[Any], set[str]]) -> bool:
    tools = config['tools']
    policies = [tools, tools.get('subagents', {}).get('tools', {})]
    policies += [entry.get('tools', {}) for entry in config['agents']['entries'].values()]
    return any(TOOLS & (finite(p.get('allow', [])) | finite(p.get('alsoAllow', []))) for p in policies)


def candidate(before: dict, *, finite: Callable[[Any], set[str]],
              refused: type[Exception], enable: bool = False) -> dict:
    """Change only this integration's grants; reject revoked or partial prior grants."""
    try:
        tools, entries = before['tools'], before['agents']['entries']
        child = tools['subagents']['tools']
        if (not {'main', *ROLE_TOOLS} <= set(entries)
                or tools['fs']['workspaceOnly'] is not True or tools['elevated']['enabled'] is not False
                or not isinstance(child, dict) or set(child) - {'allow', 'deny'}):
            raise refused('managed-integration-contract-required')
        registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
        if (not registered or tools.get('allow') and TOOLS & finite(tools.get('alsoAllow', []))
                or TOOLS & (finite(tools.get('deny', [])) | finite(child.get('deny', [])))):
            raise refused('integration-parent-policy-denied')
        allowed, denied = {}, {}
        for role, entry in entries.items():
            if 'alsoAllow' in entry['tools']:
                raise refused('finite-integration-role-required')
            allowed[role] = finite(entry['tools']['allow']) & TOOLS
            denied[role] = finite(entry['tools'].get('deny', [])) & TOOLS
        full = (registered & TOOLS == TOOLS and finite(child['allow']) & TOOLS == TOOLS
                and all(allowed[r] == ROLE_TOOLS.get(r, set())
                        and denied[r] == TOOLS - ROLE_TOOLS.get(r, set()) for r in entries))
        if present(before, finite):
            if not full:
                raise refused('partial-or-revoked-integration-grant')
        elif not enable or any(denied.values()):
            raise refused('reviewed-integration-upgrade-required')
        result = copy.deepcopy(before)
        key = 'allow' if tools.get('allow') else 'alsoAllow'
        result['tools'][key] = sorted(registered | TOOLS)
        result['tools']['subagents']['tools']['allow'] = sorted(finite(child['allow']) | TOOLS)
        for role, entry in result['agents']['entries'].items():
            selected = ROLE_TOOLS.get(role, set())
            entry['tools']['allow'] = sorted((finite(entry['tools']['allow']) - TOOLS) | selected)
            entry['tools']['deny'] = sorted((finite(entry['tools'].get('deny', [])) - TOOLS) | (TOOLS - selected))
        return result
    except (KeyError, TypeError, AttributeError):
        raise refused('managed-integration-contract-required') from None
