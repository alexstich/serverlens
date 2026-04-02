from __future__ import annotations

import sys

from serverlens.audit.audit_logger import AuditLogger
from serverlens.auth.rate_limiter import RateLimiter
from serverlens.auth.token_auth import TokenAuth
from serverlens.config import Config
from serverlens.mcp.server import Server
from serverlens.module.config_reader import ConfigReader
from serverlens.module.db_query import DbQuery
from serverlens.module.log_reader import LogReader
from serverlens.module.system_info import SystemInfo
from serverlens.transport.base import TransportInterface
from serverlens.transport.sse import SseTransport
from serverlens.transport.stdio import StdioTransport


class Application:
    def __init__(self, config_path: str) -> None:
        self._config = Config.load(config_path)
        self._mcp_server: Server
        self._transport: TransportInterface
        self._boot()

    def run(self) -> None:
        print(f"[ServerLens] Starting server...", file=sys.stderr)
        print(f"[ServerLens] Transport: {self._config.get_transport()}", file=sys.stderr)

        self._transport.on_message(
            lambda message, client_ip: self._mcp_server.handle_message(message, client_ip)
        )
        self._transport.start()

    def _boot(self) -> None:
        audit = None
        if self._config.is_audit_enabled():
            audit = AuditLogger(
                self._config.get_audit_path(),
                self._config.should_log_params(),
            )

        rate_limiter = RateLimiter(
            self._config.get_requests_per_minute(),
            self._config.get_max_concurrent(),
        )

        self._mcp_server = Server(audit, rate_limiter)
        self._register_modules()
        self._create_transport()

    def _register_modules(self) -> None:
        if self._config.get_log_sources():
            self._mcp_server.register_module(LogReader(self._config))
            print("[ServerLens] Module loaded: LogReader", file=sys.stderr)

        if self._config.get_config_sources():
            self._mcp_server.register_module(ConfigReader(self._config))
            print("[ServerLens] Module loaded: ConfigReader", file=sys.stderr)

        if self._config.get_database_connections():
            self._mcp_server.register_module(DbQuery(self._config))
            print("[ServerLens] Module loaded: DbQuery", file=sys.stderr)

        if self._config.is_system_enabled():
            self._mcp_server.register_module(SystemInfo(self._config))
            print("[ServerLens] Module loaded: SystemInfo", file=sys.stderr)

    def _create_transport(self) -> None:
        if self._config.get_transport() == "sse":
            auth = TokenAuth(self._config)
            self._transport = SseTransport(
                self._config.get_server_host(),
                self._config.get_server_port(),
                auth,
            )
        else:
            self._transport = StdioTransport()
