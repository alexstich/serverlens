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
            raise RuntimeError(f"Config file not found: {path}")

        env_candidates = [
            os.path.join(os.path.dirname(path), "env"),
            "/etc/serverlens/env",
        ]
        for env_path in env_candidates:
            if os.path.isfile(env_path) and os.access(env_path, os.R_OK):
                cls._load_env_file(env_path)
                break

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise RuntimeError("Invalid config format")

        config = cls(data)
        config._validate()
        return config

    @staticmethod
    def _load_env_file(env_path: str) -> None:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        os.environ[key.strip()] = value.strip()
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value: Any = self._data
        for k in keys:
            if not isinstance(value, dict) or k not in value:
                return default
            value = value[k]
        return value

    # --- Server ---

    def get_server_host(self) -> str:
        return str(self.get("server.host", "127.0.0.1"))

    def get_server_port(self) -> int:
        return int(self.get("server.port", 9600))

    def get_transport(self) -> str:
        return str(self.get("server.transport", "sse"))

    # --- Auth ---

    def get_tokens(self) -> list[dict[str, Any]]:
        return self.get("auth.tokens", []) or []

    def get_max_failed_attempts(self) -> int:
        return int(self.get("auth.max_failed_attempts", 5))

    def get_lockout_minutes(self) -> int:
        return int(self.get("auth.lockout_minutes", 15))

    # --- Rate limiting ---

    def get_requests_per_minute(self) -> int:
        return int(self.get("rate_limiting.requests_per_minute", 60))

    def get_max_concurrent(self) -> int:
        return int(self.get("rate_limiting.max_concurrent", 5))

    # --- Audit ---

    def is_audit_enabled(self) -> bool:
        return bool(self.get("audit.enabled", True))

    def get_audit_path(self) -> str:
        return str(self.get("audit.path", "/var/log/serverlens/audit.log"))

    def should_log_params(self) -> bool:
        return bool(self.get("audit.log_params", False))

    # --- Logs ---

    def get_log_sources(self) -> list[dict[str, Any]]:
        return self.get("logs.sources", []) or []

    # --- Configs ---

    def get_config_sources(self) -> list[dict[str, Any]]:
        return self.get("configs.sources", []) or []

    # --- Databases ---

    def get_database_connections(self) -> list[dict[str, Any]]:
        return self.get("databases.connections", []) or []

    # --- System ---

    def is_system_enabled(self) -> bool:
        return bool(self.get("system.enabled", False))

    def get_allowed_services(self) -> list[str]:
        return self.get("system.allowed_services", []) or []

    def get_allowed_docker_stacks(self) -> list[str]:
        return self.get("system.allowed_docker_stacks", []) or []

    def _validate(self) -> None:
        host = self.get_server_host()
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(
                f"Security: server.host must be localhost (127.0.0.1). Got: {host}"
            )

        transport = self.get_transport()
        if transport not in ("sse", "stdio"):
            raise RuntimeError("server.transport must be 'sse' or 'stdio'")

        if transport == "sse" and not self.get_tokens():
            raise RuntimeError("auth.tokens must not be empty for SSE transport")
