"""Offline integration gates against the installed candidate, without credentials."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import importlib.metadata
import os
import tempfile


@contextmanager
def environment(**values):
    previous = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    from hermes_cli import __version__
    assert __version__ == '0.23.0', __version__
    import sys
    assert sys.version_info[:2] == (3, 14), sys.version
    assert importlib.metadata.version('browser-harness') == '0.1.13'
    assert importlib.metadata.version('PyYAML') == '6.0.3'

    # The privacy hook must return before inspecting any request or agent data.
    from agent.agent_runtime_helpers import dump_api_request_debug
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError('request dump inspected private data')
    assert dump_api_request_debug(Unreadable(), Unreadable(), reason='synthetic') is None

    from gateway.status import _get_pid_path, _get_runtime_status_path
    with tempfile.TemporaryDirectory() as directory, environment(HERMES_HOME=directory):
        assert _get_pid_path() == Path(directory) / 'runtime/gateway.pid'
        assert _get_runtime_status_path() == Path(directory) / 'runtime/gateway_state.json'

    from gateway.restart import EXTERNAL_GATEWAY_SUPERVISOR_ENV
    from gateway.run_shutdown import _resolve_gateway_exit_verdict
    runner = SimpleNamespace(should_exit_with_failure=False, exit_code=None, _restart_requested=False)
    with environment(**{EXTERNAL_GATEWAY_SUPERVISOR_ENV: '1'}):
        try:
            _resolve_gateway_exit_verdict(runner, True)
        except SystemExit as exc:
            assert exc.code == 79, exc.code
        else:
            raise AssertionError('unplanned supervised stop lost recovery exit code')

    # Exercise upstream's new proxy implementation rather than layering the old
    # private-httpx patch over it. No client is connected to a socket.
    from tools.mcp_tool import sdk_httpx
    from tools.mcp_tool_transport import _mcp_proxy_mounts
    import asyncio
    proxy_environment = dict(HTTP_PROXY='', HTTPS_PROXY='http://10.200.0.1:3128',
                             ALL_PROXY='', NO_PROXY='', http_proxy=None,
                             https_proxy=None, all_proxy=None, no_proxy=None)
    with environment(**proxy_environment):
        routes = _mcp_proxy_mounts(sdk_httpx(), 'https://remote.example/mcp', True, None)
        assert routes and 'https://' in routes
        transport = routes['https://']
        assert type(transport).__name__ == '_BodyCapTransport'
        assert type(transport._inner._pool).__name__ == 'AsyncHTTPProxy'
        for transport in routes.values():
            asyncio.run(transport.aclose())

    from tools.browser_use_cli import browser_exec_available, _code_url_verdict
    from tools.browser_session_cdp import set_live_cdp_override
    assert browser_exec_available(), 'pinned harness unavailable'
    # This is an off-host CDP deployment; upstream deliberately trusts a local
    # on-device browser differently. The raw override does not open a socket.
    set_live_cdp_override('ws://172.18.0.1:18810/devtools/browser/homecompute')
    try:
        verdict = _code_url_verdict('navigate("http://127.0.0.1/")')
        assert verdict.blocked, 'private URL literal admitted'
    finally:
        set_live_cdp_override(None)
    print('PASS: version, Python, privacy, runtime paths, recovery, MCP proxy and browser harness')


if __name__ == '__main__':
    main()
