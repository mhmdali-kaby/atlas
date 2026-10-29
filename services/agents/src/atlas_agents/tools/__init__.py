"""The agents' tool layer: the framework, the §6.2 catalogue and untrusted-text quoting (ATL-84).

Importing this package registers every catalogue tool (as a ``not_implemented`` stub until a real
tool with the same server and name is registered). Modules with real tools are imported here too.
"""

from atlas_agents.tools import catalogue as catalogue

# Real tools (each replaces its catalogue stub on import).
from atlas_agents.tools import memory as memory
from atlas_agents.tools import news as news
from atlas_agents.tools.framework import (
    SERVERS,
    NoInput,
    ToolContext,
    ToolDef,
    ToolError,
    ToolInput,
    ToolResult,
    atlas_tool,
    build_mcp_servers,
    fq_name,
    get_tool,
    registered_tools,
)
from atlas_agents.tools.untrusted import UntrustedText, detect_instructions, untrusted

__all__ = [
    "SERVERS",
    "NoInput",
    "ToolContext",
    "ToolDef",
    "ToolError",
    "ToolInput",
    "ToolResult",
    "UntrustedText",
    "atlas_tool",
    "build_mcp_servers",
    "catalogue",
    "detect_instructions",
    "fq_name",
    "get_tool",
    "registered_tools",
    "untrusted",
]
