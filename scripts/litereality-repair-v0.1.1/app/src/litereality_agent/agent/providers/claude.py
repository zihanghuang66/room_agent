"""Claude execution is disabled in this isolated Codex fork."""
def build_capability_server(*args,**kwargs):
    raise RuntimeError('Use the Codex stdio MCP capability bridge')
class ClaudeHarness:
    def __init__(self):raise RuntimeError('Claude is disabled; no fallback is allowed')
