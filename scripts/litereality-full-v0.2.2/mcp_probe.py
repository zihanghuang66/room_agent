"""One harmless MCP capability used to verify the Codex -> MCP transport."""
import hashlib
import json
from pathlib import Path
import sys
import time
from mcp.server.fastmcp import FastMCP

root = Path(sys.argv[1]).resolve()
server = FastMCP('room-runtime-probe')


@server.tool()
def verify_runtime_file() -> dict:
    """Read this smoke test's challenge and record an MCP execution receipt."""
    result = dict(sha256=hashlib.sha256((root / 'challenge.txt').read_bytes()).hexdigest(),
                  transport='mcp', time_ns=time.time_ns())
    (root / 'mcp_receipt.json').write_text(json.dumps(result))
    return result


if __name__ == '__main__':
    server.run(transport='stdio')
