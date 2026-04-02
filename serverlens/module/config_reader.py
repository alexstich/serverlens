from __future__ import annotations

import json
import os
from typing import Any

from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult
from serverlens.security.path_guard import PathGuard
from serverlens.security.redactor import Redactor


class ConfigReader(ModuleInterface):
    def __init__(self, config: Config) -> None:
        self._sources: dict[str, dict[str, Any]] = {}
        self._path_guard = PathGuard()
        self._redactor = Redactor()

        for source in config.get_config_sources():
            self._sources[source["name"]] = {
                "path": source["path"],
                "type": source.get("type", "file"),
                "redact": source.get("redact", []),
            }

        self._path_guard.register_sources(config.get_config_sources())

    def get_tools(self) -> list[Tool]:
        return [
            Tool("config_list", "List available configuration sources", {
                "type": "object", "properties": {},
            }),
            Tool("config_read", "Read configuration file content (secrets redacted)", {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Config source name"},
                },
                "required": ["source"],
            }),
            Tool("config_search", "Search within a configuration file", {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Config source name"},
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["source", "query"],
            }),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        dispatch = {
            "config_list": self._list_sources,
            "config_read": self._read,
            "config_search": self._search,
        }
        handler = dispatch.get(name)
        if handler is None:
            return self.error(f"Unknown tool: {name}")
        return handler(arguments)

    def _list_sources(self, _args: dict[str, Any]) -> ToolResult:
        result = []
        for name, source in self._sources.items():
            result.append({
                "name": name,
                "type": source["type"],
                "available": os.path.exists(source["path"]),
            })
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _read(self, args: dict[str, Any]) -> ToolResult:
        source_name = args.get("source", "")
        if source_name not in self._sources:
            return self.error(f"Unknown config source: {source_name}")

        info = self._sources[source_name]
        path = info["path"]

        if info["type"] == "directory":
            return self._read_directory(source_name, path, info["redact"])

        if not os.path.isfile(path) or not os.access(path, os.R_OK):
            return self.error(f"Config file not available: {source_name}")

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return self.error("Cannot read config file")

        content = self._redactor.redact(content, info["redact"])
        return self.ok(content)

    def _read_directory(
        self, source: str, path: str, redact_rules: list[Any]
    ) -> ToolResult:
        if not os.path.isdir(path):
            return self.error(f"Config directory not available: {source}")

        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return self.error("Cannot read config directory")

        parts: list[str] = []
        for entry in entries:
            full_path = os.path.join(path, entry)
            if not os.path.isfile(full_path) or not os.access(full_path, os.R_OK):
                continue
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            content = self._redactor.redact(content, redact_rules)
            parts.append(f"=== {entry} ===\n{content}")

        if not parts:
            return self.ok(f"Directory is empty: {source}")
        return self.ok("\n\n".join(parts))

    def _search(self, args: dict[str, Any]) -> ToolResult:
        source_name = args.get("source", "")
        query = args.get("query", "")

        if not query:
            return self.error("Query must not be empty")
        if source_name not in self._sources:
            return self.error(f"Unknown config source: {source_name}")

        info = self._sources[source_name]
        path = info["path"]

        if info["type"] == "directory":
            return self._search_directory(path, query, info["redact"])

        if not os.path.isfile(path) or not os.access(path, os.R_OK):
            return self.error(f"Config file not available: {source_name}")

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return self.error("Cannot read config file")

        content = self._redactor.redact(content, info["redact"])
        matches = [
            f"{i + 1}: {line}"
            for i, line in enumerate(content.split("\n"))
            if query.lower() in line.lower()
        ]

        if not matches:
            return self.ok(f"No matches found for: {query}")
        return self.ok("\n".join(matches))

    def _search_directory(
        self, path: str, query: str, redact_rules: list[Any]
    ) -> ToolResult:
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return self.error("Cannot read config directory")

        results: list[str] = []
        for entry in entries:
            full_path = os.path.join(path, entry)
            if not os.path.isfile(full_path) or not os.access(full_path, os.R_OK):
                continue
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            content = self._redactor.redact(content, redact_rules)
            for i, line in enumerate(content.split("\n")):
                if query.lower() in line.lower():
                    results.append(f"{entry}:{i + 1}: {line}")

        if not results:
            return self.ok(f"No matches found for: {query}")
        return self.ok("\n".join(results))
