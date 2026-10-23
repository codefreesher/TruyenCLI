from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Chapter:
    title: str
    url: str
    content: str = ""
    number: int | None = None


@dataclass(slots=True)
class Book:
    title: str
    url: str
    author: str = ""
    description: str = ""
    cover_url: str = ""
    chapters: list[Chapter] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
