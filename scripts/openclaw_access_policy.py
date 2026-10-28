"""Finite native role grants; this policy cannot grant operator approval authority."""
from __future__ import annotations

import copy
from typing import Any, Callable

PREFIX = 'homecompute__homecompute_'
LEGACY_ACCESS_TOOLS = {PREFIX + 'access_' + name for name in ('request', 'status')}
ACCESS_TOOLS = LEGACY_ACCESS_TOOLS | {PREFIX + 'site_' + name for name in ('run', 'status')}
ACCESS_ROLES = {'travel', 'shopper'}


def present(before: dict[str, Any], finite: Callable[[Any], set[str]]) -> bool:
    tools = before['tools']
    granted = finite(tools.get('allow', [])) | finite(tools.get('alsoAllow', []))
    granted |= finite(tools.get('subagents', {}).get('tools', {}).get('allow', []))
    for entry in before['agents']['entries'].values():
        granted |= finite(entry.get('tools', {}).get('allow', []))
        granted |= finite(entry.get('tools', {}).get('alsoAllow', []))
    return bool(ACCESS_TOOLS & granted)


def candidate(before: dict[str, Any], *, finite: Callable[[Any], set[str]], roster: tuple[str, ...],
              refused: type[Exception], enable_access_upgrade: bool = False) -> dict[str, Any]:
    """Only an exact legacy grant can expand after explicit operator review.

    The bridge authenticates a shared OpenClaw principal. Native role policy
    narrows delegation, rather than cryptographically separating specialists.
    """
    try:
        tools, entries = before['tools'], before['agents']['entries']
        child = tools['subagents']['tools']
        if (not {'main', *ACCESS_ROLES} <= set(entries) or not set(entries) <= set(roster)
                or tools['fs']['workspaceOnly'] is not True or tools['elevated']['enabled'] is not False
                or not isinstance(child, dict) or set(child) - {'allow', 'deny'}):
            raise refused('managed-access-contract-refused')
        registered = finite(tools.get('allow') or tools.get('alsoAllow', []))
        if tools.get('allow') and ACCESS_TOOLS & finite(tools.get('alsoAllow', [])):
            raise refused('ambiguous-access-ceiling-refused')
        child_allowed = finite(child.get('allow', []))
        if not registered or ACCESS_TOOLS & (finite(tools.get('deny', [])) | finite(child.get('deny', []))):
            raise refused('access-policy-denied')
        allowed, denied = {}, {}
        for role, entry in entries.items():
            role_tools = entry['tools']
            if 'alsoAllow' in role_tools:
                raise refused('finite-access-role-required')
            allowed[role] = finite(role_tools['allow']) & ACCESS_TOOLS
            denied[role] = finite(role_tools.get('deny', [])) & ACCESS_TOOLS
        full = (registered & ACCESS_TOOLS == ACCESS_TOOLS and child_allowed & ACCESS_TOOLS == ACCESS_TOOLS
                and all(allowed[role] == (ACCESS_TOOLS if role in ACCESS_ROLES else set())
                        and denied[role] == (set() if role in ACCESS_ROLES else ACCESS_TOOLS) for role in entries))
        legacy = (registered & ACCESS_TOOLS == LEGACY_ACCESS_TOOLS
                  and child_allowed & ACCESS_TOOLS == LEGACY_ACCESS_TOOLS
                  and all(allowed[role] == (LEGACY_ACCESS_TOOLS if role == 'travel' else set())
                          and denied[role] == (set() if role == 'travel' else LEGACY_ACCESS_TOOLS)
                          for role in entries))
        if present(before, finite):
            if not full and not (legacy and enable_access_upgrade):
                raise refused('partial-or-revoked-access-grant')
        elif not enable_access_upgrade:
            raise refused('reviewed-access-upgrade-required')
        elif any(denied.values()):
            # Denial-only remnants may be a fully revoked prior grant. The
            # upgrade command cannot infer authorization from an empty allow.
            raise refused('access-policy-denied')
        result = copy.deepcopy(before)
        key = 'allow' if tools.get('allow') else 'alsoAllow'
        result['tools'][key] = sorted(registered | ACCESS_TOOLS)
        result['tools']['subagents']['tools']['allow'] = sorted(child_allowed | ACCESS_TOOLS)
        for role, entry in result['agents']['entries'].items():
            allow = finite(entry['tools']['allow'])
            deny = finite(entry['tools'].get('deny', []))
            entry['tools']['allow'] = sorted(allow | ACCESS_TOOLS if role in ACCESS_ROLES else allow - ACCESS_TOOLS)
            entry['tools']['deny'] = sorted(deny - ACCESS_TOOLS if role in ACCESS_ROLES else deny | ACCESS_TOOLS)
        return result
    except (KeyError, TypeError, AttributeError):
        raise refused('managed-access-contract-refused') from None
