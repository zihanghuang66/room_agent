import json
from pathlib import Path
import sys
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
server=FastMCP('room_suite')
@server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def get_probe_data() -> dict:
    """Return the diagnostic nonce and the path to its synthetic reference image."""
    return json.loads(Path(sys.argv[1]).read_text())
if __name__=='__main__':server.run(transport='stdio')
