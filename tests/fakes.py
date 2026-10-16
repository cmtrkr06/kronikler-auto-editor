"""Scripted AI backend for tests: no network, no SDK."""
from __future__ import annotations

from typing import Optional

from ai_client import GenerateResult


class HttpError(Exception):
    """Mimics SDK errors: interactions errors carry .status_code, models.* errors carry .code."""
    def __init__(self, status: int, message: str = "", retry_after: Optional[str] = None,
                 attr: str = "status_code") -> None:
        super().__init__(message or f"HTTP {status}")
        setattr(self, attr, status)
        self.response = type("R", (), {"headers": {"retry-after": retry_after} if retry_after else {}})()


class FakeBackend:
    """Each queue holds responses in order: a str/GenerateResult/vector list to return,
    or an Exception to raise. An empty queue raises AssertionError (unexpected call)."""

    def __init__(self, generate=None, embed=None, models=None) -> None:
        self.generate_queue = list(generate or [])
        self.embed_queue = list(embed or [])
        self.models = models if models is not None else {}
        self.prompts: list[tuple[str, str, Optional[str]]] = []  # (model, prompt, system)
        self.images: list = []                                   # images sent with each prompt
        self.embed_calls: list[list[str]] = []

    @staticmethod
    def _next(queue, what):
        if not queue:
            raise AssertionError(f"unexpected {what} call")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def generate_json(self, model, prompt, schema, system, images=None):
        self.prompts.append((model, prompt, system))
        self.images.append(images)
        item = self._next(self.generate_queue, "generate")
        return item if isinstance(item, GenerateResult) else GenerateResult(item, tokens=10)

    def embed(self, model, texts, dimensions):
        self.embed_calls.append(list(texts))
        item = self._next(self.embed_queue, "embed")
        return item(texts) if callable(item) else item

    def list_models(self):
        if isinstance(self.models, BaseException):
            raise self.models
        return self.models
