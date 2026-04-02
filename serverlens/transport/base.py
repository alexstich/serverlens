from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

MessageHandler = Callable[[dict[str, Any], str], dict[str, Any] | None]


class TransportInterface(ABC):
    @abstractmethod
    def on_message(self, handler: MessageHandler) -> None: ...

    @abstractmethod
    def start(self) -> None: ...
