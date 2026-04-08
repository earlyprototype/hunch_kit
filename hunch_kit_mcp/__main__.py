"""Entry point for running the MCP server directly."""

try:
    from .server import mcp
except ImportError:
    import sys
    print(
        "Error: MCP dependencies not installed.\n"
        "Install with: pip install -e \".[mcp]\"",
        file=sys.stderr,
    )
    sys.exit(1)

if __name__ == "__main__":
    mcp.run()
