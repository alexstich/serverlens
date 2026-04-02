from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

if TYPE_CHECKING:
    from serverlens.config import Config

_ph = PasswordHasher(time_cost=4, memory_cost=65536, parallelism=1)


class TokenAuth:
    def __init__(self, config: Config) -> None:
        self._tokens = config.get_tokens()
        self._max_failed = config.get_max_failed_attempts()
        self._lockout_minutes = config.get_lockout_minutes()
        self._failed: dict[str, dict] = {}

    def verify(self, auth_header: str, client_ip: str = "127.0.0.1") -> bool:
        if self._is_locked_out(client_ip):
            return False

        if not auth_header.startswith("Bearer "):
            self._record_failure(client_ip)
            return False

        token = auth_header[7:]
        if not token:
            self._record_failure(client_ip)
            return False

        now = time.time()
        for entry in self._tokens:
            token_hash = entry.get("hash", "")
            expires = entry.get("expires", "")

            if expires:
                from datetime import datetime

                try:
                    exp_ts = datetime.fromisoformat(expires).timestamp()
                    if exp_ts < now:
                        continue
                except ValueError:
                    continue

            try:
                _ph.verify(token_hash, token)
                self._clear_failures(client_ip)
                return True
            except VerifyMismatchError:
                continue
            except Exception:
                continue

        self._record_failure(client_ip)
        return False

    @staticmethod
    def generate_token() -> str:
        return "sl_" + secrets.token_hex(32)

    @staticmethod
    def hash_token(token: str) -> str:
        return _ph.hash(token)

    def _is_locked_out(self, client_ip: str) -> bool:
        entry = self._failed.get(client_ip)
        if not entry:
            return False

        locked_until = entry.get("locked_until", 0)
        if locked_until > 0:
            if locked_until > time.time():
                return True
            del self._failed[client_ip]

        return False

    def _record_failure(self, client_ip: str) -> None:
        if client_ip not in self._failed:
            self._failed[client_ip] = {"count": 0, "locked_until": 0}

        self._failed[client_ip]["count"] += 1

        if self._failed[client_ip]["count"] >= self._max_failed:
            self._failed[client_ip]["locked_until"] = (
                time.time() + self._lockout_minutes * 60
            )

    def _clear_failures(self, client_ip: str) -> None:
        self._failed.pop(client_ip, None)
