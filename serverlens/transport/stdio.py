from __future__ import annotations

import json
import sys
from typing import Any

from serverlens.transport.base import MessageHandler, TransportInterface


class StdioTransport(TransportInterface):
    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    def start(self) -> None:
        print("[ServerLens] Stdio transport started", file=sys.stderr)

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._write_error(-32700, "Parse error", None)
                continue

            if not isinstance(message, dict):
                self._write_error(-32700, "Parse error", None)
                continue

            assert self._handler is not None
            response = self._handler(message, "stdio")

            if response is not None:
                self._write(response)

    @staticmethod
    def _write(data: dict[str, Any]) -> None:
        line = json.dumps(data, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    @staticmethod
    def _write_error(code: int, message: str, msg_id: int | str | None) -> None:
        data = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()
