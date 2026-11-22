#!/opt/hermes/.venv/bin/python
"""Run the pinned Hermes ACP adapter with the write tools removed."""

import sys

sys.path.insert(0, "/opt/hermes")

from acp_adapter.entry import main
from hermes_cli.config import load_config_readonly
from hermes_cli.tools_config import _get_platform_tools
from model_tools import _select_tool_names
from toolsets import create_custom_toolset

create_custom_toolset("t3code-write-tools", "Hermes file modifications", ["write_file", "patch"])

config = load_config_readonly()
tools = _select_tool_names(
    sorted(_get_platform_tools(config, "acp")), config["agent"]["disabled_toolsets"], quiet_mode=True
)
if tools != {"read_file", "search_files"}:
    sys.exit("Hermes ACP review requires only the read_file and search_files tools")

main()
