from __future__ import annotations

import json
import os
import select
import shlex
import subprocess
import sys
from typing import Any

SSH_CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
INIT_READ_TIMEOUT = 15


class SshConnection:
    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self._name = name
        self._config = config
        self._process: subprocess.Popen | None = None
        self._initialized = False
        self._request_id = 100

    @property
    def name(self) -> str:
        return self._name

    def connect(self) -> bool:
        cmd = self._build_command()
        print(f"[MCP:{self._name}] SSH command: {cmd}", file=sys.stderr)

        try:
            self._process = subprocess.Popen(
                cmd,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            print(f"[MCP:{self._name}] Failed to start SSH process", file=sys.stderr)
            return False

        return True

    def initialize(self) -> bool:
        response = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "serverlens-mcp-proxy", "version": "1.0.0"},
        }, timeout=INIT_READ_TIMEOUT)

        if response is None:
            print(f"[MCP:{self._name}] Initialize failed: no response", file=sys.stderr)
            return False

        if "error" in response:
            err = response["error"].get("message", "unknown error")
            print(f"[MCP:{self._name}] Initialize failed: {err}", file=sys.stderr)
            return False

        self._send_notification("notifications/initialized")
        self._initialized = True

        info = response.get("result", {}).get("serverInfo", {})
        server_name = info.get("name", "unknown")
        version = info.get("version", "?")
        print(f"[MCP:{self._name}] Initialized: {server_name} v{version}", file=sys.stderr)
        return True

    def get_tools(self) -> list[dict[str, Any]]:
        response = self._send_request("tools/list")
        if response is None or "result" not in response:
            print(f"[MCP:{self._name}] Failed to get tools list", file=sys.stderr)
            return []
        return response["result"].get("tools", [])

    def call_tool(
        self, original_id: int | str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        response = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if response is None:
            return None
        response["id"] = original_id
        return response

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def close(self) -> None:
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                if self._process.stdout:
                    self._process.stdout.close()
                if self._process.stderr:
                    self._process.stderr.close()
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    def _send_request(
        self, method: str, params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        msg_id = self._request_id
        self._request_id += 1

        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params if params else {},
        }

        line = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            assert self._process and self._process.stdin
            self._process.stdin.write(line.encode())
            self._process.stdin.flush()
        except (OSError, AssertionError):
            self._drain_stderr()
            return None

        return self._read_response(timeout=timeout or READ_TIMEOUT)

    def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params

        line = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            assert self._process and self._process.stdin
            self._process.stdin.write(line.encode())
            self._process.stdin.flush()
        except (OSError, AssertionError):
            pass

    def _read_response(self, timeout: float = READ_TIMEOUT) -> dict[str, Any] | None:
        try:
            assert self._process and self._process.stdout
            fd = self._process.stdout
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                print(
                    f"[MCP:{self._name}] Read timeout ({timeout}s) — remote serverlens did not respond",
                    file=sys.stderr,
                )
                self._drain_stderr()
                return None
            raw = fd.readline()
            if not raw:
                self._drain_stderr()
                return None
            data = json.loads(raw.decode().strip())
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError, AssertionError):
            self._drain_stderr()
            return None

    def _drain_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        try:
            while select.select([self._process.stderr], [], [], 0)[0]:
                line = self._process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text:
                    print(f"[MCP:{self._name}:remote] {text}", file=sys.stderr)
        except Exception:
            pass

    def _build_command(self) -> str:
        ssh = self._config.get("ssh", {})

        host = ssh["host"]
        user = ssh.get("user", "root")
        port = int(ssh.get("port", 22))
        key = ssh.get("key")
        options = ssh.get("options", {})

        remote_cmd = self._config.get("command", "serverlens serve --stdio")

        parts = ["ssh"]
        parts.append("-o BatchMode=yes")
        parts.append("-o StrictHostKeyChecking=accept-new")
        parts.append(f"-o ConnectTimeout={SSH_CONNECT_TIMEOUT}")
        parts.append("-o ConnectionAttempts=1")
        parts.append("-o ServerAliveInterval=15")
        parts.append("-o ServerAliveCountMax=3")

        for opt_key, opt_val in options.items():
            if isinstance(opt_key, str):
                parts.append(f"-o {shlex.quote(f'{opt_key}={opt_val}')}")

        parts.append(f"-p {port}")

        if key:
            expanded = key.replace("~", os.environ.get("HOME", ""))
            parts.append(f"-i {shlex.quote(expanded)}")

        parts.append(shlex.quote(f"{user}@{host}"))
        parts.append(shlex.quote(remote_cmd))

        return " ".join(parts)
