from __future__ import annotations

import json
import sys
from typing import Any

from serverlens_mcp.config import Config
from serverlens_mcp.ssh_connection import SshConnection


class McpProxy:
    def __init__(self, config: Config) -> None:
        self._servers: dict[str, SshConnection] = {}
        self._remote_tools: dict[str, list[dict[str, Any]]] = {}
        self._server_configs = config.get_servers()

        server_names = ", ".join(self._server_configs.keys())
        print(
            f"[MCP] Ready (lazy connect): {len(self._server_configs)} server(s) configured ({server_names}), 2 MCP tools",
            file=sys.stderr,
        )

    def run(self) -> None:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue

            response = self._handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()

        self._shutdown()

    def _handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            return None

        params = msg.get("params", {})
        handlers = {
            "initialize": lambda: self._handle_initialize(msg_id),
            "tools/list": lambda: self._handle_tools_list(msg_id),
            "tools/call": lambda: self._handle_tools_call(msg_id, params),
            "ping": lambda: _jsonrpc(msg_id, {}),
        }
        handler = handlers.get(method)
        if handler is None:
            return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")
        return handler()

    def _handle_initialize(self, msg_id: int | str) -> dict[str, Any]:
        return _jsonrpc(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "ServerLens MCP Proxy",
                "version": "2.0.0",
                "connected_servers": list(self._servers.keys()),
            },
        })

    def _handle_tools_list(self, msg_id: int | str) -> dict[str, Any]:
        tools = [
            {
                "name": "serverlens_list",
                "description": "List connected servers and their available tools. Call this first to discover what is available.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string", "description": "Optional: show tools only for this server"},
                    },
                },
            },
            {
                "name": "serverlens_call",
                "description": "Execute a tool on a remote server. Use serverlens_list first to see available servers and tools.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string", "description": "Server name (from serverlens_list)"},
                        "tool": {"type": "string", "description": "Tool name (from serverlens_list)"},
                        "arguments": {"type": "object", "description": "Tool arguments (see tool description for details)"},
                    },
                    "required": ["server", "tool"],
                },
            },
        ]
        return _jsonrpc(msg_id, {"tools": tools})

    def _handle_tools_call(self, msg_id: int | str, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        dispatch = {
            "serverlens_list": self._handle_list,
            "serverlens_call": self._handle_call,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return _jsonrpc(msg_id, {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}. Available: serverlens_list, serverlens_call"}],
                "isError": True,
            })
        return handler(msg_id, arguments)

    def _handle_list(self, msg_id: int | str, args: dict[str, Any]) -> dict[str, Any]:
        filter_server = args.get("server")
        if filter_server:
            return self._handle_list_server(msg_id, filter_server)

        # Refresh connection states on demand to avoid stale "disconnected"
        # statuses in list output when reconnect is possible.
        for name in self._server_configs:
            self._ensure_connected(name)

        result = []
        for name in self._server_configs:
            result.append({
                "server": name,
                "status": "connected" if name in self._servers else "disconnected",
                "tools_count": len(self._remote_tools.get(name, [])),
            })

        text = json.dumps({
            "hint": 'Call serverlens_list with {server: "<name>"} to see available tools for a specific server',
            "servers": result,
        }, indent=2, ensure_ascii=False)
        return _jsonrpc(msg_id, {"content": [{"type": "text", "text": text}]})

    def _handle_list_server(self, msg_id: int | str, server_name: str) -> dict[str, Any]:
        if server_name not in self._server_configs:
            available = ", ".join(self._server_configs.keys())
            return _jsonrpc(msg_id, {
                "content": [{"type": "text", "text": f"Unknown server: {server_name}. Available: {available}"}],
                "isError": True,
            })

        connected = self._ensure_connected(server_name)
        tools = []
        for tool in self._remote_tools.get(server_name, []):
            entry: dict[str, Any] = {"name": tool["name"], "description": tool.get("description", "")}
            schema = tool.get("inputSchema", {})
            props = schema.get("properties", {})
            if props:
                required = schema.get("required", [])
                params = []
                for p_name, p_def in props.items():
                    desc = p_def.get("description", p_def.get("type", ""))
                    req = " (required)" if p_name in required else ""
                    params.append(f"{p_name}{req}: {desc}")
                entry["parameters"] = params
            tools.append(entry)

        text = json.dumps({
            "server": server_name,
            "status": "connected" if connected else "disconnected",
            "hint": f'Call serverlens_call with {{server: "{server_name}", tool: "<name>", arguments: {{...}}}}',
            "tools": tools,
        }, indent=2, ensure_ascii=False)
        return _jsonrpc(msg_id, {"content": [{"type": "text", "text": text}]})

    def _handle_call(self, msg_id: int | str, args: dict[str, Any]) -> dict[str, Any]:
        server_name = args.get("server", "")
        tool_name = args.get("tool", "")
        tool_args = args.get("arguments", {})

        if not server_name or not tool_name:
            return _jsonrpc(msg_id, {
                "content": [{"type": "text", "text": 'Required: "server" and "tool" parameters'}],
                "isError": True,
            })

        if server_name not in self._server_configs:
            available = ", ".join(self._server_configs.keys())
            return _jsonrpc(msg_id, {
                "content": [{"type": "text", "text": f"Unknown server: {server_name}. Available: {available}"}],
                "isError": True,
            })

        if not self._ensure_connected(server_name):
            return _jsonrpc(msg_id, {
                "content": [{"type": "text", "text": f"Server '{server_name}' not connected and reconnect failed"}],
                "isError": True,
            })

        known_tools = [t["name"] for t in self._remote_tools.get(server_name, [])]
        if tool_name not in known_tools:
            return _jsonrpc(msg_id, {
                "content": [{"type": "text", "text": f"Unknown tool '{tool_name}' on server '{server_name}'. Available: {', '.join(known_tools)}"}],
                "isError": True,
            })

        server = self._servers[server_name]
        response = server.call_tool(msg_id, tool_name, tool_args)

        if response is None:
            print(f"[MCP] No response from '{server_name}', attempting reconnect...", file=sys.stderr)
            server.close()
            del self._servers[server_name]

            if self._reconnect_server(server_name):
                response = self._servers[server_name].call_tool(msg_id, tool_name, tool_args)

            if response is None:
                return _jsonrpc(msg_id, {
                    "content": [{"type": "text", "text": f"No response from server '{server_name}' (reconnect attempted)"}],
                    "isError": True,
                })

        return response

    def _reconnect_server(self, server_name: str) -> bool:
        if server_name not in self._server_configs:
            return False

        print(f"[MCP] Reconnecting to '{server_name}'...", file=sys.stderr)
        ssh = SshConnection(server_name, self._server_configs[server_name])

        if not ssh.connect():
            print(f"[MCP] Reconnect FAILED: cannot connect to '{server_name}'", file=sys.stderr)
            return False
        if not ssh.initialize():
            print(f"[MCP] Reconnect FAILED: cannot initialize '{server_name}'", file=sys.stderr)
            ssh.close()
            return False

        self._servers[server_name] = ssh
        # Always refresh tool cache after reconnect in case remote tool set changed.
        self._discover_tools(server_name, ssh)

        print(f"[MCP] Reconnected to '{server_name}'", file=sys.stderr)
        return True

    def _ensure_connected(self, server_name: str) -> bool:
        if server_name not in self._server_configs:
            return False

        existing = self._servers.get(server_name)
        if existing is not None:
            if existing.is_alive():
                return True
            print(f"[MCP] Server '{server_name}' connection lost", file=sys.stderr)
            existing.close()
            del self._servers[server_name]

        return self._reconnect_server(server_name)

    def _discover_tools(self, server_name: str, ssh: SshConnection) -> None:
        tools = ssh.get_tools()
        self._remote_tools[server_name] = tools
        print(f"[MCP] Discovered {len(tools)} tools on '{server_name}'", file=sys.stderr)

    def _shutdown(self) -> None:
        for server in self._servers.values():
            server.close()


def _jsonrpc(msg_id: int | str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
