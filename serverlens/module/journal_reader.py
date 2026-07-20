from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any, Callable

from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult

_TAIL_MAX_LINES = 500
_SEARCH_MAX_MATCHES = 1000
_SEARCH_SCAN_LINES = 50000
_TIME_SPEC_MAX_LENGTH = 64
_SEARCH_TIMEOUT_SECONDS = 5.0

# journalctl time specs: "2026-07-18", "2026-07-18 10:00:00", "3 days ago",
# "yesterday", "now", "-2h", "+5min". Defense in depth on top of list-args exec.
_TIME_SPEC_RE = re.compile(r"^[a-zA-Z0-9 :.+\-]+$")

Executor = Callable[[list[str]], "str | None"]


class JournalReader(ModuleInterface):
    def __init__(self, config: Config, executor: Executor | None = None) -> None:
        self._enabled = config.is_journal_enabled()
        self._allowed_units = config.get_allowed_journal_units()
        self._executor: Executor = executor if executor is not None else _default_executor

    def get_tools(self) -> list[Tool]:
        if not self._enabled:
            return []

        return [
            Tool("journal_units", "List systemd units available for journal reading", {
                "type": "object", "properties": {},
            }),
            Tool("journal_tail", "Get the last N lines from a systemd unit journal", {
                "type": "object",
                "properties": {
                    "unit": {"type": "string", "description": "Systemd unit name (from journal_units whitelist)"},
                    "lines": {"type": "integer", "description": "Number of lines (max 500)", "default": 100},
                },
                "required": ["unit"],
            }),
            Tool("journal_search", "Search a systemd unit journal by substring or regex", {
                "type": "object",
                "properties": {
                    "unit": {"type": "string", "description": "Systemd unit name (from journal_units whitelist)"},
                    "query": {"type": "string", "description": "Search query"},
                    "regex": {"type": "boolean", "description": "Use regex", "default": False},
                    "since": {"type": "string", "description": 'journalctl time spec, e.g. "2026-07-18" or "3 days ago" (optional)'},
                    "until": {"type": "string", "description": "journalctl time spec (optional)"},
                    "lines": {"type": "integer", "description": "Max matching lines (max 1000)", "default": 100},
                },
                "required": ["unit", "query"],
            }),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if not self._enabled:
            return self.error("Journal module is disabled")

        dispatch = {
            "journal_units": self._units,
            "journal_tail": self._tail,
            "journal_search": self._search,
        }
        handler = dispatch.get(name)
        if handler is None:
            return self.error(f"Unknown tool: {name}")
        return handler(arguments)

    def _units(self, _args: dict[str, Any]) -> ToolResult:
        result = {
            "allowed_units": list(self._allowed_units),
            "hint": "Use these unit names in journal_tail/journal_search",
        }
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _tail(self, args: dict[str, Any]) -> ToolResult:
        unit = str(args.get("unit", ""))
        lines = min(max(int(args.get("lines", 100)), 1), _TAIL_MAX_LINES)

        if not self._is_unit_allowed(unit):
            return self.error(f"Unit not in whitelist: {unit}")

        output = self._executor(_build_tail_command(unit, lines))
        if output is None:
            return self.error(f"Failed to read journal for unit: {unit}")

        output = output.strip()
        if not output:
            return self.ok(f"Journal is empty for unit: {unit}")

        return self.ok(output)

    def _search(self, args: dict[str, Any]) -> ToolResult:
        unit = str(args.get("unit", ""))
        query = str(args.get("query", ""))
        use_regex = bool(args.get("regex", False))
        since = str(args["since"]) if args.get("since") is not None else None
        until = str(args["until"]) if args.get("until") is not None else None
        max_matches = min(max(int(args.get("lines", 100)), 1), _SEARCH_MAX_MATCHES)

        if not self._is_unit_allowed(unit):
            return self.error(f"Unit not in whitelist: {unit}")

        if not query:
            return self.error("Query must not be empty")

        pattern = None
        if use_regex:
            try:
                pattern = re.compile(query)
            except re.error:
                return self.error("Invalid regex pattern")

        for param_name, time_spec in (("since", since), ("until", until)):
            if time_spec is not None and not _is_valid_time_spec(time_spec):
                return self.error(
                    f'Invalid {param_name} format. Use journalctl time spec, e.g. "2026-07-18" or "3 days ago"'
                )

        output = self._executor(_build_search_command(unit, since, until))
        if output is None:
            return self.error(f"Failed to read journal for unit: {unit}")

        # Filtering is done here in Python: user query never reaches the shell.
        matches = _filter_lines(output.split("\n"), query, pattern, max_matches)

        if not matches:
            return self.ok(f"No matches found for query: {query}")

        return self.ok("\n".join(matches))

    def _is_unit_allowed(self, unit: str) -> bool:
        return bool(unit) and unit in self._allowed_units


def _build_tail_command(unit: str, lines: int) -> list[str]:
    return ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"]


def _build_search_command(unit: str, since: str | None, until: str | None) -> list[str]:
    command = ["journalctl", "-u", unit, "-n", str(_SEARCH_SCAN_LINES), "--no-pager", "-o", "short-iso"]
    if since is not None:
        command += ["--since", since]
    if until is not None:
        command += ["--until", until]
    return command


def _is_valid_time_spec(time_spec: str) -> bool:
    if not time_spec or len(time_spec) > _TIME_SPEC_MAX_LENGTH:
        return False
    return bool(_TIME_SPEC_RE.match(time_spec))


def _filter_lines(
    lines: list[str],
    query: str,
    pattern: re.Pattern[str] | None,
    max_matches: int,
) -> list[str]:
    matches: list[str] = []
    start = time.monotonic()

    for line in lines:
        if len(matches) >= max_matches:
            break

        if time.monotonic() - start > _SEARCH_TIMEOUT_SECONDS:
            matches.append(f"[TIMEOUT: search exceeded {_SEARCH_TIMEOUT_SECONDS}s limit]")
            break

        line = line.rstrip("\n\r")
        if not line:
            continue

        if pattern is not None:
            if pattern.search(line):
                matches.append(line)
        elif query in line:
            matches.append(line)

    return matches


def _default_executor(command: list[str]) -> str | None:
    # Argument list, never shell=True — the unit name is whitelist-checked and
    # time specs are format-validated, but nothing here is shell-interpreted anyway.
    try:
        result = subprocess.run(
            command, shell=False, capture_output=True, text=True, timeout=10,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None
