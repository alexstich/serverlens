from __future__ import annotations

import os
import sys
from typing import Any


class PathGuard:
    def __init__(self) -> None:
        self._allowed_dirs: list[str] = []
        self._allowed_paths: dict[str, str] = {}

    def register_sources(self, sources: list[dict[str, Any]]) -> None:
        for source in sources:
            name = source["name"]
            path = source["path"]
            src_type = source.get("type", "file")

            resolved = self._realpath(path)
            if resolved is None:
                print(
                    f"[ServerLens] Warning: path not found for source '{name}': {path}",
                    file=sys.stderr,
                )
                self._allowed_paths[name] = path
                continue

            self._allowed_paths[name] = resolved

            if src_type == "directory" or os.path.isdir(resolved):
                self._allowed_dirs.append(resolved.rstrip("/") + "/")

    def get_resolved_path(self, name: str) -> str | None:
        return self._allowed_paths.get(name)

    def is_allowed(self, path: str) -> bool:
        resolved = self._realpath(path)
        if resolved is None:
            return False

        if resolved in self._allowed_paths.values():
            return True

        return any(resolved.startswith(d) for d in self._allowed_dirs)

    @staticmethod
    def _realpath(path: str) -> str | None:
        try:
            rp = os.path.realpath(path)
            return rp if os.path.exists(rp) else None
        except OSError:
            return None
