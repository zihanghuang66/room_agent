"""One harmless tool used to verify the Codex stdio MCP bridge."""
from pathlib import Path
import sys
from mcp.server.fastmcp import FastMCP

server=FastMCP('room_probe')
@server.tool()
def get_probe_value() -> str:
    """Return the test value needed by the integration check."""
    return Path(sys.argv[1]).read_text().strip()

if __name__=='__main__':server.run(transport='stdio')
