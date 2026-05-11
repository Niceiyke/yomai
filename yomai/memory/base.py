from __future__ import annotations

from abc import ABC, abstractmethod

from yomai.llm.base import Message


class MemoryBackend(ABC):
    """Public ABC for custom Yomai memory backends."""

    @abstractmethod
    async def load(self, session_id: str) -> list[Message]:
        """Return conversation history as a list of message dicts."""
        ...

    @abstractmethod
    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        """Append the user message and assistant reply to history."""
        ...

    @abstractmethod
    async def clear(self, session_id: str) -> None:
        """Delete all history for this session."""
        ...
