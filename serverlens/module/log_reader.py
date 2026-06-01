from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult
from serverlens.security.path_guard import PathGuard

_TS_NGINX = re.compile(
    r"\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}\s[+\-]\d{4})\]"
)
_TS_ISO = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")
_TS_SYSLOG = re.compile(r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")


class LogReader(ModuleInterface):
    def __init__(self, config: Config) -> None:
        self._sources: dict[str, dict[str, Any]] = {}
        self._dir_sources: dict[str, dict[str, Any]] = {}
        self._docker_sources: dict[str, dict[str, Any]] = {}
        self._path_guard = PathGuard()

        for source in config.get_log_sources():
            src_type = source.get("type", "file")
            if src_type == "directory":
                self._dir_sources[source["name"]] = {
                    "path": source["path"].rstrip("/"),
                    "pattern": source.get("pattern", "*.log"),
                    "format": source.get("format", "plain"),
                    "max_lines": int(source.get("max_lines", 5000)),
                }
            elif src_type == "docker":
                container = source.get("container", "")
                if not container:
                    print(
                        f"[ServerLens] Warning: docker source '{source['name']}' missing 'container' field",
                        file=sys.stderr,
                    )
                    continue
                self._docker_sources[source["name"]] = {
                    "container": container,
                    "format": source.get("format", "plain"),
                    "max_lines": int(source.get("max_lines", 5000)),
                }
                print(
                    f"[ServerLens] Docker log source: {source['name']} -> container '{container}'",
                    file=sys.stderr,
                )
            else:
                self._sources[source["name"]] = {
                    "path": source["path"],
                    "format": source.get("format", "plain"),
                    "max_lines": int(source.get("max_lines", 5000)),
                }

        non_docker = [s for s in config.get_log_sources() if s.get("type", "file") != "docker"]
        self._path_guard.register_sources(non_docker)

    def get_tools(self) -> list[Tool]:
        return [
            Tool("logs_list", "List available log sources", {
                "type": "object", "properties": {},
            }),
            Tool("logs_tail", "Get the last N lines from a log file", {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Log source name"},
                    "lines": {"type": "integer", "description": "Number of lines (max 500)", "default": 100},
                },
                "required": ["source"],
            }),
            Tool("logs_search", "Search log by substring or regex", {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Log source name"},
                    "query": {"type": "string", "description": "Search query"},
                    "regex": {"type": "boolean", "description": "Use regex", "default": False},
                    "lines": {"type": "integer", "description": "Max matching lines (max 1000)", "default": 100},
                },
                "required": ["source", "query"],
            }),
            Tool("logs_count", "Get line count and file size", {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Log source name"},
                },
                "required": ["source"],
            }),
            Tool("logs_time_range", "Get log entries within a time range", {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Log source name"},
                    "from": {"type": "string", "description": "Start time (ISO 8601 or common format)"},
                    "to": {"type": "string", "description": "End time (ISO 8601 or common format)"},
                    "lines": {"type": "integer", "description": "Max lines", "default": 200},
                },
                "required": ["source", "from", "to"],
            }),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        dispatch = {
            "logs_list": self._list_sources,
            "logs_tail": self._tail,
            "logs_search": self._search,
            "logs_count": self._count,
            "logs_time_range": self._time_range,
        }
        handler = dispatch.get(name)
        if handler is None:
            return self.error(f"Unknown tool: {name}")
        return handler(arguments)

    # ------------------------------------------------------------------

    def _list_sources(self, _args: dict[str, Any]) -> ToolResult:
        result: list[dict[str, Any]] = []

        for name, source in self._sources.items():
            path = source["path"]
            available = os.path.isfile(path) and os.access(path, os.R_OK)
            result.append({
                "name": name,
                "format": source["format"],
                "max_lines": source["max_lines"],
                "available": available,
            })

        for name, ds in self._docker_sources.items():
            result.append({
                "name": name,
                "type": "docker",
                "container": ds["container"],
                "format": ds["format"],
                "max_lines": ds["max_lines"],
                "available": self._docker_available(ds["container"]),
            })

        for name, ds in self._dir_sources.items():
            dir_path = ds["path"]
            available = os.path.isdir(dir_path) and os.access(dir_path, os.R_OK)
            files: list[dict[str, Any]] = []

            if available:
                pattern = os.path.join(dir_path, ds["pattern"])
                found = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
                for fp in found[:50]:
                    if os.path.isfile(fp) and os.access(fp, os.R_OK):
                        st = os.stat(fp)
                        files.append({
                            "name": f"{name}/{os.path.basename(fp)}",
                            "size": _format_bytes(st.st_size),
                            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        })

            result.append({
                "name": name,
                "type": "directory",
                "path_pattern": ds["pattern"],
                "format": ds["format"],
                "max_lines": ds["max_lines"],
                "available": available,
                "files_count": len(files),
                "files": files,
                "hint": f'Use "{name}/<filename>" as source name in logs_tail/logs_search',
            })

        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _tail(self, args: dict[str, Any]) -> ToolResult:
        source = args.get("source", "")
        lines = min(int(args.get("lines", 100)), 500)
        max_lines = self._get_max_lines(source)
        lines = min(lines, max_lines)

        if self._is_docker_source(source):
            ds = self._docker_sources[source]
            output = self._docker_logs(ds["container"], tail=lines)
            if output is None:
                return self.error(f"Cannot read Docker logs for container: {ds['container']}")
            result_lines = output.rstrip("\n").split("\n") if output.strip() else []
            return self.ok("\n".join(result_lines[-lines:]))

        path = self._resolve_source(source)
        if path is None:
            return self.error(f"Unknown or inaccessible log source: {source}")
        result = _read_last_lines(path, lines)
        return self.ok("\n".join(result))

    def _search(self, args: dict[str, Any]) -> ToolResult:
        source = args.get("source", "")
        query = args.get("query", "")
        use_regex = bool(args.get("regex", False))
        max_lines = min(int(args.get("lines", 100)), 1000)

        if not query:
            return self.error("Query must not be empty")

        if use_regex:
            try:
                pattern = re.compile(query)
            except re.error:
                return self.error("Invalid regex pattern")
        else:
            pattern = None

        if self._is_docker_source(source):
            ds = self._docker_sources[source]
            output = self._docker_logs(ds["container"])
            if output is None:
                return self.error(f"Cannot read Docker logs for container: {ds['container']}")
            matches: list[str] = []
            for raw_line in output.split("\n"):
                if len(matches) >= max_lines:
                    break
                line = raw_line.rstrip("\r")
                if pattern is not None:
                    if pattern.search(line):
                        matches.append(line)
                elif query in line:
                    matches.append(line)
            if not matches:
                return self.ok(f"No matches found for query: {query}")
            return self.ok("\n".join(matches))

        path = self._resolve_source(source)
        if path is None:
            return self.error(f"Unknown or inaccessible log source: {source}")

        matches = []
        start = time.monotonic()
        timeout = 5.0

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    if len(matches) >= max_lines:
                        break
                    if time.monotonic() - start > timeout:
                        matches.append(f"[TIMEOUT: search exceeded {timeout}s limit]")
                        break
                    line = raw_line.rstrip("\n\r")
                    if pattern is not None:
                        if pattern.search(line):
                            matches.append(line)
                    elif query in line:
                        matches.append(line)
        except OSError:
            return self.error("Cannot read log file")

        if not matches:
            return self.ok(f"No matches found for query: {query}")
        return self.ok("\n".join(matches))

    def _count(self, args: dict[str, Any]) -> ToolResult:
        source = args.get("source", "")

        if self._is_docker_source(source):
            ds = self._docker_sources[source]
            output = self._docker_logs(ds["container"])
            if output is None:
                return self.error(f"Cannot read Docker logs for container: {ds['container']}")
            line_count = output.count("\n")
            info = {
                "source": source,
                "type": "docker",
                "container": ds["container"],
                "lines": line_count,
            }
            return self.ok(json.dumps(info, indent=2))

        path = self._resolve_source(source)
        if path is None:
            return self.error(f"Unknown or inaccessible log source: {source}")

        line_count = 0
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    line_count += chunk.count(b"\n")
        except OSError:
            pass

        size = os.path.getsize(path) if os.path.isfile(path) else 0
        info = {
            "source": source,
            "lines": line_count,
            "size_bytes": size,
            "size_human": _format_bytes(size),
        }
        return self.ok(json.dumps(info, indent=2))

    def _time_range(self, args: dict[str, Any]) -> ToolResult:
        source = args.get("source", "")
        from_str = args.get("from", "")
        to_str = args.get("to", "")
        max_lines = min(int(args.get("lines", 200)), 1000)

        from_ts = _parse_timestamp(from_str)
        to_ts = _parse_timestamp(to_str)
        if from_ts is None or to_ts is None:
            return self.error("Invalid date format. Use ISO 8601 or common format.")

        if self._is_docker_source(source):
            ds = self._docker_sources[source]
            from_dt = datetime.fromtimestamp(from_ts).strftime("%Y-%m-%dT%H:%M:%S")
            to_dt = datetime.fromtimestamp(to_ts).strftime("%Y-%m-%dT%H:%M:%S")
            output = self._docker_logs(ds["container"], since=from_dt, until=to_dt)
            if output is None:
                return self.error(f"Cannot read Docker logs for container: {ds['container']}")
            result_lines = output.rstrip("\n").split("\n") if output.strip() else []
            if not result_lines:
                return self.ok("No entries found in the specified time range")
            return self.ok("\n".join(result_lines[:max_lines]))

        path = self._resolve_source(source)
        if path is None:
            return self.error(f"Unknown or inaccessible log source: {source}")

        matches: list[str] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    if len(matches) >= max_lines:
                        break
                    ts = _extract_timestamp(raw_line)
                    if ts is not None and from_ts <= ts <= to_ts:
                        matches.append(raw_line.rstrip("\n\r"))
        except OSError:
            return self.error("Cannot read log file")

        if not matches:
            return self.ok("No entries found in the specified time range")
        return self.ok("\n".join(matches))

    # ------------------------------------------------------------------

    def _resolve_source(self, name: str) -> str | None:
        if name in self._sources:
            path = self._sources[name]["path"]
            if not os.path.isfile(path) or not os.access(path, os.R_OK):
                return None
            rp = os.path.realpath(path)
            return rp if os.path.exists(rp) else None

        if "/" in name:
            dir_name, file_name = name.split("/", 1)
            if dir_name not in self._dir_sources:
                return None
            if "/" in file_name or ".." in file_name:
                return None

            file_path = os.path.join(self._dir_sources[dir_name]["path"], file_name)
            if not os.path.isfile(file_path) or not os.access(file_path, os.R_OK):
                return None

            rp = os.path.realpath(file_path)
            if not os.path.exists(rp):
                return None
            return rp if self._path_guard.is_allowed(rp) else None

        return None

    def _is_docker_source(self, name: str) -> bool:
        return name in self._docker_sources

    def _docker_logs(self, container: str, tail: int | None = None,
                     since: str | None = None, until: str | None = None) -> str | None:
        cmd = ["docker", "logs"]
        if tail is not None:
            cmd += ["--tail", str(tail)]
        if since:
            cmd += ["--since", since]
        if until:
            cmd += ["--until", until]
        cmd.append(container)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return None
            return (result.stdout or "") + (result.stderr or "")
        except (subprocess.TimeoutExpired, OSError):
            return None

    def _docker_available(self, container: str) -> bool:
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", container],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _get_max_lines(self, source: str) -> int:
        if source in self._sources:
            return self._sources[source]["max_lines"]
        if source in self._docker_sources:
            return self._docker_sources[source]["max_lines"]
        if "/" in source:
            dir_name = source.split("/", 1)[0]
            ds = self._dir_sources.get(dir_name)
            if ds:
                return ds["max_lines"]
        return 5000


def _read_last_lines(path: str, count: int) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            pos = fh.tell()
            lines: list[str] = []
            buf = b""

            while pos > 0 and len(lines) < count:
                pos -= 1
                fh.seek(pos)
                char = fh.read(1)
                if char == b"\n":
                    if buf:
                        lines.insert(0, buf.decode("utf-8", errors="replace"))
                        buf = b""
                else:
                    buf = char + buf

            if buf and len(lines) < count:
                lines.insert(0, buf.decode("utf-8", errors="replace"))

            return lines[:count]
    except OSError:
        return []


def _extract_timestamp(line: str) -> float | None:
    m = _TS_NGINX.search(line)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S %z")
            return dt.timestamp()
        except ValueError:
            pass

    m = _TS_ISO.search(line)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1))
            return dt.timestamp()
        except ValueError:
            pass

    m = _TS_SYSLOG.search(line)
    if m:
        try:
            ts_str = m.group(1)
            dt = datetime.strptime(
                f"{datetime.now().year} {ts_str}", "%Y %b %d %H:%M:%S"
            )
            return dt.timestamp()
        except ValueError:
            pass

    return None


def _parse_timestamp(s: str) -> float | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    i = 0
    s = float(size)
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{round(s, 2)} {units[i]}"
