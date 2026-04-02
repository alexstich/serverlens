from __future__ import annotations

import os
from typing import Any

import yaml


class Config:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def load(cls, path: str) -> Config:
        if not os.path.isfile(path):
            raise RuntimeError(f"Config not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise RuntimeError("Invalid config format")

        config = cls(data)
        config._validate()
        return config

    def get_servers(self) -> dict[str, dict[str, Any]]:
        return self._data.get("servers", {})

    def filter_servers(self, names: list[str]) -> None:
        all_servers = self._data.get("servers", {})
        unknown = set(names) - set(all_servers.keys())
        if unknown:
            available = ", ".join(all_servers.keys())
            missing = ", ".join(unknown)
            raise RuntimeError(
                f"Unknown server(s) in --servers: {missing}. Available in config: {available}"
            )
        self._data["servers"] = {k: v for k, v in all_servers.items() if k in names}

    def _validate(self) -> None:
        servers = self.get_servers()
        if not servers:
            raise RuntimeError("No servers configured")

        for name, server in servers.items():
            if not server.get("ssh", {}).get("host"):
                raise RuntimeError(f"Server '{name}': ssh.host is required")
            if not server.get("ssh", {}).get("user"):
                raise RuntimeError(f"Server '{name}': ssh.user is required")

            if "remote" in server:
                import sys
                print(
                    f"[MCP] Warning: server '{name}' has deprecated 'remote' section. "
                    "Use 'command' instead. See config.example.yaml",
                    file=sys.stderr,
                )
                remote = server["remote"]
                if "command" not in server:
                    if "php" in remote:
                        php = remote["php"]
                        sl_path = remote.get("serverlens_path", "/opt/serverlens/bin/serverlens")
                        sl_config = remote.get("config_path", "/etc/serverlens/config.yaml")
                        server["command"] = f"{php} {sl_path} serve --stdio --config {sl_config}"
                    elif "python" in remote:
                        python = remote["python"]
                        sl_path = remote.get("serverlens_path", "/opt/serverlens")
                        sl_config = remote.get("config_path", "/etc/serverlens/config.yaml")
                        server["command"] = f"{python} -m serverlens serve --stdio --config {sl_config}"
