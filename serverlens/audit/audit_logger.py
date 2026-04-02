from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, IO


class AuditLogger:
    def __init__(self, path: str, log_params: bool = False) -> None:
        self._path = path
        self._log_params = log_params
        self._handle: IO[str] | None = None

    def log(
        self,
        client_ip: str,
        tool: str,
        params: dict[str, Any],
        success: bool,
        duration_ms: int,
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "client_ip": client_ip,
            "tool": tool,
            "params_summary": self._summarize_params(params),
            "result": {
                "status": "ok" if success else "error",
                "duration_ms": duration_ms,
            },
        }
        self._write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _summarize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._log_params:
            return params

        summary: dict[str, Any] = {}
        passthrough = {"source", "database", "table", "service", "stack"}

        for key, value in params.items():
            if key in passthrough:
                summary[key] = value
            elif key == "fields" and isinstance(value, list):
                summary["fields_count"] = len(value)
            elif key == "filters" and isinstance(value, dict):
                summary["has_filters"] = bool(value)
            elif key == "limit":
                summary["limit"] = value
            elif key == "lines":
                summary["lines"] = value
            elif key == "query":
                summary["query_length"] = len(str(value))

        return summary

    def _write(self, data: str) -> None:
        if self._handle is None:
            dir_path = os.path.dirname(self._path)
            if dir_path and not os.path.isdir(dir_path):
                try:
                    os.makedirs(dir_path, mode=0o750, exist_ok=True)
                except OSError:
                    pass

            try:
                self._handle = open(self._path, "a", encoding="utf-8")
            except OSError:
                print(
                    f"[ServerLens] Cannot open audit log: {self._path}",
                    file=sys.stderr,
                )
                return

        self._handle.write(data)
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __del__(self) -> None:
        self.close()
