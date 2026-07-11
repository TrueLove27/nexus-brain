from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...
