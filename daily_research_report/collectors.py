from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from daily_research_report.models import NewsItem, Source
from daily_research_report.time_utils import in_day

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = 20
USER_AGENT = "research-daily-report/0.1 (+https://github.com)"
NAV_TITLES = {
    "首页",
    "网站地图",
    "联系我们",
    "繁体",
    "简体",
    "english",
    "english version",
    "rss",
    "登录",
    "注册",
    "新闻发布",
    "时政要闻",
    "术语表",
    "日常新闻发布",
    "新闻发言人谈话",
    "司局负责人发布",
    "例行新闻发布会",
    "专题新闻发布会",
}
TITLE_REJECT_PATTERNS = [
    r"备案",
    r"公网安备",
    r"ICP备",
]


def collect_sources(
    sources: list[Source],
    start: datetime,
    end: datetime,
    max_items_per_source: int = 20,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for source in sources:
        try:
            if source.type == "rss":
                collected = collect_rss(source, start, end, max_items_per_source)
            elif source.type == "web":
                collected = collect_web(source, start, end, max_items_per_source)
            elif source.type == "manual":
                collected = collect_manual(source, start, end)
            elif source.type == "api":
                collected = collect_api(source, start, end, max_items_per_source)
            else:
                LOGGER.warning("Unsupported source type %s for %s", source.type, source.name)
                collected = []
            items.extend(collected)
            LOGGER.info("Collected %s items from %s", len(collected), source.name)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to collect %s: %s", source.name, exc)
    return items


def collect_rss(
    source: Source,
    start: datetime,
    end: datetime,
    max_items: int,
) -> list[NewsItem]:
    parsed = feedparser.parse(source.url)
    items = []
    for entry in parsed.entries[: max_items * 2]:
        published_at = parse_entry_datetime(entry)
        if published_at is None or not in_day(published_at, start, end):
            continue
        title = clean_text(entry.get("title", ""))
        url = entry.get("link", source.url)
        summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
        items.append(
            NewsItem(
                title=title,
                url=url,
                source_name=source.name,
                category=source.category,
                published_at=published_at,
                summary=summary,
            )
        )
        if len(items) >= max_items:
            break
    return items


def collect_web(
    source: Source,
    start: datetime,
    end: datetime,
    max_items: int,
) -> list[NewsItem]:
    response = requests.get(
        source.url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    selector = source.selector or "a"
    links = soup.select(selector)
    items = []
    for link in links:
        title = clean_text(link.get_text(" "))
        href = link.get("href")
        if not is_candidate_link(title, href):
            continue
        url = requests.compat.urljoin(source.url, href)
        if not is_http_url(url):
            continue
        if is_homepage_like(url, title, source):
            continue
        if not source_allows_url(source, url):
            continue
        inferred_at = infer_datetime_from_url(url, start) or infer_datetime_from_text(
            link.parent.get_text(" ") if link.parent else "",
            start,
        )
        if source.meta.get("require_date_from_url") and inferred_at is None:
            continue
        if inferred_at is not None and not in_day(inferred_at, start, end):
            continue
        items.append(
            NewsItem(
                title=title,
                url=url,
                source_name=source.name,
                category=source.category,
                published_at=inferred_at,
            )
        )
        if len(items) >= max_items:
            break
    return items


def collect_api(
    source: Source,
    start: datetime,
    end: datetime,
    max_items: int,
) -> list[NewsItem]:
    headers = {"User-Agent": USER_AGENT}
    if os.getenv("NEWS_API_KEY"):
        headers["X-Api-Key"] = os.getenv("NEWS_API_KEY", "")
    response = requests.get(source.url, timeout=REQUEST_TIMEOUT, headers=headers)
    response.raise_for_status()
    data = response.json()
    records = data.get("articles") or data.get("items") or data.get("data") or []
    items = []
    for record in records:
        published_at = parse_datetime(
            record.get("publishedAt") or record.get("published_at") or record.get("date")
        )
        if published_at is None or not in_day(published_at, start, end):
            continue
        title = clean_text(record.get("title", ""))
        url = record.get("url") or record.get("link")
        if not title or not url:
            continue
        items.append(
            NewsItem(
                title=title,
                url=url,
                source_name=source.name,
                category=source.category,
                published_at=published_at,
                summary=clean_text(record.get("description") or record.get("summary") or ""),
                content=clean_text(record.get("content") or ""),
            )
        )
        if len(items) >= max_items:
            break
    return items


def collect_manual(source: Source, start: datetime, end: datetime) -> list[NewsItem]:
    raw_items = source.meta.get("items", [])
    items = []
    for raw in raw_items:
        published_at = parse_datetime(raw.get("published_at"))
        if not in_day(published_at, start, end):
            continue
        items.append(
            NewsItem(
                title=raw["title"],
                url=raw["url"],
                source_name=source.name,
                category=raw.get("category", source.category),
                published_at=published_at,
                summary=raw.get("summary"),
                content=raw.get("content"),
            )
        )
    return items


def parse_entry_datetime(entry: dict) -> datetime | None:
    for key in ("published", "updated", "created"):
        if entry.get(key):
            return parse_datetime(entry[key])
    return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            return date_parser.parse(value)
        except (TypeError, ValueError):
            return None


def clean_text(value: str) -> str:
    return " ".join(BeautifulSoup(value or "", "html.parser").get_text(" ").split())


def is_candidate_link(title: str, href: str) -> bool:
    normalized_title = title.strip().lower()
    if len(normalized_title) < 6:
        return False
    if normalized_title in NAV_TITLES:
        return False
    if any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in TITLE_REJECT_PATTERNS):
        return False
    lowered_href = href.strip().lower()
    if lowered_href.startswith(("javascript:", "#", "mailto:", "tel:")):
        return False
    return True


def source_allows_url(source: Source, url: str) -> bool:
    include_patterns = source.meta.get("include_url_patterns") or []
    exclude_patterns = source.meta.get("exclude_url_patterns") or []
    if include_patterns and not any(re.search(pattern, url) for pattern in include_patterns):
        return False
    if exclude_patterns and any(re.search(pattern, url) for pattern in exclude_patterns):
        return False
    return True


def is_http_url(url: str) -> bool:
    return urlsplit(url).scheme in {"http", "https"}


def is_homepage_like(url: str, title: str, source: Source) -> bool:
    parts = urlsplit(url)
    source_parts = urlsplit(source.url)
    path = parts.path.strip("/")
    source_path = source_parts.path.strip("/")
    if title.strip() == source.name:
        return True
    if parts.netloc == source_parts.netloc and path in {"", source_path}:
        return True
    return False


def infer_datetime_from_url(url: str, start: datetime) -> datetime | None:
    path = urlsplit(url).path
    patterns = [
        r"(20\d{2})(\d{2})(\d{2})",
        r"(20\d{2})[-_/](\d{2})[-_/](\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, path)
        if not match:
            continue
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3)) if len(match.groups()) >= 3 else 1
        try:
            return datetime(year, month, day, tzinfo=start.tzinfo)
        except ValueError:
            return None
    return None


def infer_datetime_from_text(text: str, start: datetime) -> datetime | None:
    patterns = [
        r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})",
        r"(\d{1,2})[-/月.](\d{1,2})日?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if len(match.groups()) == 3:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
        else:
            year = start.year
            month = int(match.group(1))
            day = int(match.group(2))
        try:
            return datetime(year, month, day, tzinfo=start.tzinfo)
        except ValueError:
            return None
    return None
