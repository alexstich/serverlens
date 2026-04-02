from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from serverlens.mcp.tool import Tool

ToolResult = dict[str, Any]


class ModuleInterface(ABC):
    @abstractmethod
    def get_tools(self) -> list[Tool]: ...

    @abstractmethod
    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...

    @staticmethod
    def ok(text: str) -> ToolResult:
        return {"content": [{"type": "text", "text": text}]}

    @staticmethod
    def error(text: str) -> ToolResult:
        return {"content": [{"type": "text", "text": text}], "isError": True}
