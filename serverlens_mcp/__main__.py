from __future__ import annotations

import os
import sys

import yaml

from serverlens_mcp.config import Config
from serverlens_mcp.mcp_proxy import McpProxy


def main() -> None:
    config_path = None
    server_filter: list[str] | None = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--config" and i + 1 < len(sys.argv):
            config_path = sys.argv[i + 1]
            i += 2
        elif arg == "--servers" and i + 1 < len(sys.argv):
            server_filter = [s.strip() for s in sys.argv[i + 1].split(",") if s.strip()]
            i += 2
        elif arg in ("--help", "-h"):
            _print_usage()
            sys.exit(0)
        else:
            i += 1

    default_paths = [
        os.path.expanduser("~/.serverlens/config.yaml"),
        os.path.join(os.path.dirname(__file__), "..", "mcp-client", "config.yaml"),
        os.path.join(os.path.dirname(__file__), "..", "mcp-client", "config.example.yaml"),
    ]

    if config_path is None:
        for path in default_paths:
            if os.path.isfile(path):
                config_path = path
                break

    if config_path is None:
        print("Error: config not found. Use --config <path>", file=sys.stderr)
        print(f"Searched: {', '.join(default_paths)}", file=sys.stderr)
        sys.exit(1)

    print(f"[MCP] Config: {config_path}", file=sys.stderr)

    try:
        config = Config.load(config_path)

        if server_filter:
            config.filter_servers(server_filter)
            print(f"[MCP] Server filter: {', '.join(server_filter)}", file=sys.stderr)

        proxy = McpProxy(config)
        proxy.run()
    except Exception as e:
        print(f"Fatal: {e}", file=sys.stderr)
        sys.exit(1)


def _print_usage() -> None:
    print("""ServerLens MCP Proxy — local MCP server for Cursor

Connects to remote ServerLens instances via SSH and exposes
their tools through the MCP protocol (stdio transport).

Usage:
  python -m serverlens_mcp [--config <path>] [--servers <name1,name2,...>]

Options:
  --config <path>            Path to config.yaml (default: ~/.serverlens/config.yaml)
  --servers <name1,name2>    Connect only to these servers (comma-separated).
  --help, -h                 Show this help""", file=sys.stderr)


if __name__ == "__main__":
    main()
