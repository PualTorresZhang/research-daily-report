from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from daily_research_report.models import NewsItem

TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"spm", "from", "ref", "source", "fbclid", "gclid"}


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = item_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def item_key(item: NewsItem) -> str:
    normalized = normalize_url(item.url)
    title = normalize_title(item.title)
    raw = normalized or title
    if not raw:
        raw = item.source_key
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in TRACKING_PARAMS and not key.startswith(TRACKING_PREFIXES)
    ]
    path = re.sub(r"/+$", "", parts.path)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query),
            "",
        )
    )


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).lower()

