from __future__ import annotations

import sys
import time
from typing import Any

from serverlens.audit.audit_logger import AuditLogger
from serverlens.auth.rate_limiter import RateLimiter
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface


class Server:
    def __init__(
        self,
        audit: AuditLogger | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._audit = audit
        self._rate_limiter = rate_limiter

    def register_module(self, module: ModuleInterface) -> None:
        for tool in module.get_tools():
            self._tools[tool.name] = {"tool": tool, "module": module}

    def handle_message(
        self, message: dict[str, Any], client_ip: str = "127.0.0.1"
    ) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        if msg_id is None:
            return None

        if self._rate_limiter and not self._rate_limiter.allow(client_ip):
            return _jsonrpc_error(msg_id, -32000, "Rate limit exceeded")

        start = time.monotonic()

        handler_map = {
            "initialize": self._handle_initialize,
            "tools/list": lambda _id, _p, _ip: self._handle_tools_list(_id),
            "tools/call": self._handle_tools_call,
            "ping": lambda _id, _p, _ip: _jsonrpc_response(_id, {}),
        }

        handler = handler_map.get(method)
        if handler is None:
            return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")

        response = handler(msg_id, params, client_ip)

        duration_ms = int((time.monotonic() - start) * 1000)

        if self._audit and method == "tools/call":
            tool_name = params.get("name", "unknown")
            is_error = bool(response and response.get("result", {}).get("isError"))
            self._audit.log(
                client_ip, tool_name, params.get("arguments", {}),
                not is_error, duration_ms,
            )

        return response

    def _handle_initialize(
        self, msg_id: int | str, params: dict[str, Any], _ip: str
    ) -> dict[str, Any]:
        return _jsonrpc_response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ServerLens", "version": "1.0.0"},
        })

    def _handle_tools_list(self, msg_id: int | str) -> dict[str, Any]:
        tools = [entry["tool"].to_dict() for entry in self._tools.values()]
        return _jsonrpc_response(msg_id, {"tools": tools})

    def _handle_tools_call(
        self, msg_id: int | str, params: dict[str, Any], _ip: str,
    ) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            return _jsonrpc_error(msg_id, -32602, f"Unknown tool: {tool_name}")

        if self._rate_limiter:
            self._rate_limiter.increment_concurrent()

        try:
            module: ModuleInterface = self._tools[tool_name]["module"]
            result = module.handle_tool_call(tool_name, arguments)
            return _jsonrpc_response(msg_id, result)
        except Exception as e:
            print(f"[ServerLens] Tool '{tool_name}' exception: {e}", file=sys.stderr)
            return _jsonrpc_response(msg_id, {
                "content": [{"type": "text", "text": "Internal error"}],
                "isError": True,
            })
        finally:
            if self._rate_limiter:
                self._rate_limiter.decrement_concurrent()


def _jsonrpc_response(msg_id: int | str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
