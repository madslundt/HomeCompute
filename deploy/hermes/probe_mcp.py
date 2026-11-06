"""Read-only probes through Hermes's real MCP registry; emit no household payloads."""
import json

from tools.mcp_tool_discovery import discover_mcp_tools
from tools.mcp_tool_lifecycle import shutdown_mcp_servers
from tools.registry import registry


def run():
    names = discover_mcp_tools(['homecompute'])
    checks = {}
    for suffix, arguments, source in (
        ('school_discover', {}, 'aula'),
        ('shopping_stores', {'query': 'Netto'}, 'tilbudstrolden'),
        ('system_read', {'system_id': 'home-assistant'}, None),
        ('shopping_list', {}, None),
        ('storage_list', {}, None),
        ('calendar_list', {}, None),
        ('household_task_list', {}, None),
    ):
        found = [name for name in names if name.endswith('_homecompute_' + suffix)]
        if len(found) != 1:
            checks[suffix] = False
            continue
        envelope = json.loads(registry.get_entry(found[0]).handler(arguments))
        if 'error' in envelope or not isinstance(envelope.get('result'), str):
            checks[suffix] = False
            continue
        result = json.loads(envelope['result'])
        ok = isinstance(result, dict) and not result.get('error')
        if source:
            ok = ok and result.get('source') == source and 'data' in result
        if suffix == 'system_read':
            machine = result.get('report', {}).get('machines', {}).get('home-assistant', {})
            ok = ok and any(row[:3] == ['collection', 'health', 'healthy'] for row in machine.get('checks', []))
        checks[suffix] = bool(ok)
    print(json.dumps({'tool_count': len(names), 'read_only_checks': checks}, sort_keys=True))
    return all(checks.values())


if __name__ == '__main__':
    try:
        passed = run()
    finally:
        shutdown_mcp_servers()
    raise SystemExit(0 if passed else 2)
