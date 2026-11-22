#!/opt/hermes/.venv/bin/python
"""Verify the Hermes review toolset before starting the ACP adapter."""

import sys

sys.path.insert(0, "/opt/hermes")

from acp_adapter.entry import main
from hermes_cli.config import load_config_readonly
from hermes_cli.tools_config import _get_platform_tools
from model_tools import _select_tool_names

config = load_config_readonly()
for platform in ("acp", "local"):
    tools = _select_tool_names(sorted(_get_platform_tools(config, platform)), None, quiet_mode=True)
    if tools != {"read_file", "search_files"}:
        sys.exit(f"Hermes {platform} review requires only the read_file and search_files tools")

main()
