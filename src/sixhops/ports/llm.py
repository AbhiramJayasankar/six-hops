"""LLMProvider port. Anything that can mutate the graph must come back as a validated
Pydantic model via `structured`; free text from `text` is only ever shown to the user."""

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMOutputError(Exception):
    """The model did not return output matching the requested schema."""


class LLMProvider(Protocol):
    name: str

    def structured(self, *, system: str, messages: list[ChatMessage], schema: type[T]) -> T:
        """Return an instance of `schema`, validated. Raises LLMOutputError otherwise."""
        ...

    def text(self, *, system: str, messages: list[ChatMessage]) -> str: ...
