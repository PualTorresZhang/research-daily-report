from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Source:
    name: str
    category: str
    type: str
    url: str
    enabled: bool = True
    selector: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsItem:
    title: str
    url: str
    source_name: str
    category: str
    published_at: datetime | None = None
    summary: str | None = None
    content: str | None = None

    @property
    def source_key(self) -> str:
        return f"{self.source_name}:{self.url}"

