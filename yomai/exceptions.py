from __future__ import annotations


class YomaiError(Exception):
    """Base class for all Yomai errors."""

    def __init__(self, message: str, *, hint: str | None = None, docs: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.docs = docs

    def __str__(self) -> str:
        lines = [f"{self.__class__.__name__}: {self.message}"]
        if self.hint:
            lines.extend(["", f"  {self.hint}"])
        if self.docs:
            lines.extend(["", f"  Docs: {self.docs}"])
        return "\n".join(lines)


class YomaiConfigError(YomaiError):
    pass


class YomaiRouteError(YomaiError):
    pass


class YomaiLLMError(YomaiError):
    pass


class YomaiToolError(YomaiError):
    pass


class YomaiMemoryError(YomaiError):
    pass
