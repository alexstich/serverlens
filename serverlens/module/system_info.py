from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult


class SystemInfo(ModuleInterface):
    def __init__(self, config: Config) -> None:
        self._enabled = config.is_system_enabled()
        self._allowed_services = config.get_allowed_services()
        self._allowed_docker_stacks = config.get_allowed_docker_stacks()

    def get_tools(self) -> list[Tool]:
        if not self._enabled:
            return []

        return [
            Tool("system_overview", "Get CPU, RAM, disk usage, and uptime", {
                "type": "object", "properties": {},
            }),
            Tool("system_services", "Get status of allowed systemd services", {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Specific service name (optional, shows all if omitted)"},
                },
            }),
            Tool("system_docker", "Get status of allowed Docker containers", {
                "type": "object",
                "properties": {
                    "stack": {"type": "string", "description": "Docker stack/compose name (optional)"},
                },
            }),
            Tool("system_connections", "Get active database and service connection counts", {
                "type": "object", "properties": {},
            }),
            Tool("system_processes", "Get top processes by CPU or memory usage (like htop)", {
                "type": "object",
                "properties": {
                    "sort_by": {"type": "string", "description": 'Sort by "cpu" (default) or "memory"', "enum": ["cpu", "memory"]},
                    "limit": {"type": "integer", "description": "Number of processes to return (default 20, max 100)"},
                    "user": {"type": "string", "description": "Filter by OS user (optional)"},
                    "filter": {"type": "string", "description": "Filter by command name substring (optional)"},
                },
            }),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if not self._enabled:
            return self.error("System module is disabled")

        dispatch = {
            "system_overview": self._overview,
            "system_services": self._services,
            "system_docker": self._docker,
            "system_connections": self._connections,
            "system_processes": self._processes,
        }
        handler = dispatch.get(name)
        if handler is None:
            return self.error(f"Unknown tool: {name}")
        return handler(arguments)

    def _overview(self, _args: dict[str, Any]) -> ToolResult:
        info: dict[str, Any] = {}
        info["uptime"] = (self._exec("uptime -p") or self._exec("uptime") or "N/A").strip()
        info["load_average"] = (self._exec("cat /proc/loadavg") or "N/A").strip()

        mem = self._exec("free -h --si")
        if mem:
            info["memory"] = mem

        disk = self._exec('df -h --total 2>/dev/null | grep -E "^(/dev|total)"')
        if disk:
            info["disk"] = disk

        info["cpu_cores"] = (self._exec("nproc") or "N/A").strip()
        return self.ok(json.dumps(info, indent=2, ensure_ascii=False))

    def _services(self, args: dict[str, Any]) -> ToolResult:
        specific = args.get("service")
        if specific is not None:
            if specific not in self._allowed_services:
                return self.error(f"Service not in whitelist: {specific}")
            return self.ok(json.dumps(self._get_service_status(specific), indent=2))

        results = [self._get_service_status(s) for s in self._allowed_services]
        return self.ok(json.dumps(results, indent=2, ensure_ascii=False))

    def _docker(self, args: dict[str, Any]) -> ToolResult:
        stack = args.get("stack")
        if stack is not None and stack not in self._allowed_docker_stacks:
            return self.error(f"Docker stack not in whitelist: {stack}")

        output = self._exec(
            'docker ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}" 2>/dev/null'
        )
        if not output:
            return self.ok("Docker not available or no running containers")

        stacks = [stack] if stack else self._allowed_docker_stacks
        containers: list[dict[str, str]] = []

        for line in output.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name = parts[0]
            if not any(name.startswith(f"{s}-") or name.startswith(f"{s}_") for s in stacks):
                continue
            containers.append({
                "name": name,
                "status": parts[1] if len(parts) > 1 else "",
                "image": parts[2] if len(parts) > 2 else "",
                "ports": parts[3] if len(parts) > 3 else "",
            })

        if not containers:
            return self.ok("No matching containers found")
        return self.ok(json.dumps(containers, indent=2, ensure_ascii=False))

    def _connections(self, _args: dict[str, Any]) -> ToolResult:
        result: dict[str, Any] = {}

        pg_active = self._exec("psql -t -c \"SELECT count(*) FROM pg_stat_activity WHERE state = 'active'\" 2>/dev/null")
        if pg_active is not None:
            result["postgresql_active"] = int(pg_active.strip())

        pg_total = self._exec('psql -t -c "SELECT count(*) FROM pg_stat_activity" 2>/dev/null')
        if pg_total is not None:
            result["postgresql_total"] = int(pg_total.strip())

        rmq_in = self._exec("ss -tn state established 'sport = :5672' 2>/dev/null | tail -n +2 | wc -l")
        if rmq_in is not None:
            result["rabbitmq_incoming"] = int(rmq_in.strip())

        rmq_out = self._exec("ss -tn state established 'dport = :5672' 2>/dev/null | tail -n +2 | wc -l")
        if rmq_out is not None:
            result["rabbitmq_outgoing"] = int(rmq_out.strip())

        result["rabbitmq_connections"] = result.get("rabbitmq_incoming", 0) + result.get("rabbitmq_outgoing", 0)

        established = self._exec("ss -tun state established 2>/dev/null | wc -l")
        if established is not None:
            result["tcp_established"] = max(0, int(established.strip()) - 1)

        return self.ok(json.dumps(result, indent=2))

    def _processes(self, args: dict[str, Any]) -> ToolResult:
        sort_by = args.get("sort_by", "cpu")
        limit = min(max(int(args.get("limit", 20)), 1), 100)
        user_filter = args.get("user")
        cmd_filter = args.get("filter")

        sort_flag = "-%mem" if sort_by == "memory" else "-%cpu"
        output = self._exec(f"ps aux --sort={sort_flag} 2>/dev/null")
        if not output:
            return self.error("Failed to execute ps command")

        lines = output.strip().split("\n")
        if len(lines) < 2:
            return self.ok("No processes found")

        processes: list[dict[str, Any]] = []
        for line in lines[1:]:
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue

            proc_user = parts[0]
            command = parts[10]

            if user_filter and proc_user != user_filter:
                continue
            if cmd_filter and cmd_filter.lower() not in command.lower():
                continue

            processes.append({
                "user": proc_user,
                "pid": int(parts[1]),
                "cpu": float(parts[2]),
                "mem": float(parts[3]),
                "vsz_kb": int(parts[4]),
                "rss_kb": int(parts[5]),
                "stat": parts[7],
                "time": parts[9],
                "command": command,
            })
            if len(processes) >= limit:
                break

        result = {
            "sort_by": sort_by,
            "total_shown": len(processes),
            "filters": {k: v for k, v in {"user": user_filter, "command": cmd_filter}.items() if v},
            "processes": processes,
        }
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _get_service_status(self, service: str) -> dict[str, Any]:
        escaped = shlex.quote(service)
        is_active = (self._exec(f"systemctl is-active {escaped} 2>/dev/null") or "unknown").strip()
        is_enabled = (self._exec(f"systemctl is-enabled {escaped} 2>/dev/null") or "unknown").strip()

        memory = None
        main_pid = None
        show = self._exec(f"systemctl show {escaped} --property=MainPID,MemoryCurrent 2>/dev/null")
        if show:
            for line in show.strip().split("\n"):
                if line.startswith("MainPID="):
                    main_pid = int(line[8:])
                elif line.startswith("MemoryCurrent="):
                    raw = line[14:]
                    if raw.isdigit():
                        memory = _format_bytes(int(raw))

        return {
            "service": service,
            "active": is_active,
            "enabled": is_enabled,
            "pid": main_pid,
            "memory": memory,
        }

    @staticmethod
    def _exec(command: str) -> str | None:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=10,
            )
            return result.stdout if result.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    i = 0
    s = float(size)
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{round(s, 1)} {units[i]}"
