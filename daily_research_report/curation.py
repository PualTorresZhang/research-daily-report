from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import re
from typing import Iterable

from daily_research_report.models import NewsItem

SOURCE_DAILY_CAPS = {
    "商务部": 3,
    "OpenAI News": 2,
    "BBC China": 2,
    "央行": 2,
    "CCTV新闻联播": 5,
    "FT China": 3,
}

SOURCE_PRIORITY = {
    "新闻联播": "P0",
    "CCTV新闻联播": "P0",
    "新华社": "P0",
    "人民日报": "P0",
    "国务院": "P0",
    "外交部": "P0",
    "国台办": "P0",
    "中纪委国家监委": "P0",
    "央行": "P0",
    "商务部": "P0",
    "国家发改委": "P0",
    "财联社": "P1",
    "证券时报": "P1",
    "第一财经": "P1",
    "界面新闻": "P1",
    "Reuters China": "P1",
    "FT": "P1",
    "FT China": "P1",
    "Bloomberg": "P1",
    "AP": "P1",
    "BBC China": "P1",
    "Nikkei": "P1",
    "Kyodo": "P1",
    "OpenAI News": "P2",
    "Anthropic News": "P2",
    "Google DeepMind Blog": "P2",
    "NVIDIA Newsroom": "P2",
    "Microsoft AI Blog": "P2",
    "Meta AI Blog": "P2",
}

MANDATORY_CHECKS = [
    {
        "key": "xinwenlianbo",
        "label": "新闻联播当天摘要",
        "section": "附：新闻联播",
        "sources": {"新闻联播", "CCTV新闻联播"},
        "required": True,
    },
    {
        "key": "gov",
        "label": "国务院当天发布",
        "section": "要闻",
        "sources": {"国务院"},
        "required": True,
    },
    {
        "key": "mfa_press",
        "label": "外交部例行记者会",
        "section": "涉外",
        "sources": {"外交部"},
        "keywords": ("例行记者会", "记者会", "发言人"),
        "required": True,
    },
    {
        "key": "ccdi",
        "label": "中纪委当天通报",
        "section": "反腐",
        "sources": {"中纪委国家监委"},
        "required": True,
    },
    {
        "key": "taiwan",
        "label": "国台办/涉台关键词",
        "section": "涉台",
        "sources": {"国台办"},
        "keywords": ("台湾", "台海", "台独", "国台办", "两岸", "民进党"),
        "required": True,
    },
    {
        "key": "reuters_ft_watch",
        "label": "Reuters/FT中国、AI、中东、台海关键词",
        "section": "涉外",
        "sources": {"Reuters China", "FT", "FT China"},
        "keywords": ("China", "Chinese", "AI", "Taiwan", "Middle East", "中国", "人工智能", "台海", "中东"),
        "required": True,
    },
]

AI_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "openai",
    "anthropic",
    "deepmind",
    "nvidia",
    "chatgpt",
    "codex",
    "大模型",
    "人工智能",
    "生成式",
    "算力",
    "芯片",
    "gpu",
    "出口管制",
    "诉讼",
)

CHINA_AI_PRIORITY_KEYWORDS = (
    "中国",
    "监管",
    "政策",
    "备案",
    "算法",
    "大厂",
    "阿里",
    "腾讯",
    "百度",
    "字节",
    "华为",
    "芯片",
    "出口管制",
)


@dataclass
class CurationResult:
    items: list[NewsItem]
    diagnostics: dict


def curate_items(items: list[NewsItem], report_date: date) -> CurationResult:
    normalized = [normalize_item(item) for item in items]
    diagnostics = build_diagnostics(normalized, report_date)
    ranked = sorted(normalized, key=item_rank, reverse=True)
    capped = apply_source_caps(ranked)
    capped = ensure_mandatory_items(capped, ranked)
    diagnostics["source_counts_after_caps"] = dict(Counter(item.source_name for item in capped))
    diagnostics["total_candidates_after_caps"] = len(capped)
    return CurationResult(items=capped, diagnostics=diagnostics)


def normalize_item(item: NewsItem) -> NewsItem:
    text = searchable_text(item)
    item.category = classify_item(item, text)
    return item


def classify_item(item: NewsItem, text: str) -> str:
    source = item.source_name
    if source in {"新闻联播", "CCTV新闻联播"}:
        return "附：新闻联播"
    if source == "中纪委国家监委" or has_any(text, ("审查调查", "接受纪律审查", "监察调查", "处分", "违纪违法", "反腐")):
        return "反腐"
    if source == "国台办" or has_any(text, ("台湾", "台海", "台独", "两岸", "民进党", "赖清德")):
        return "涉台"
    if has_any(text, AI_KEYWORDS):
        return "科技与产业"
    if source == "央行" or source == "国家发改委":
        return "经济"
    if source == "商务部":
        if has_any(text, ("外资", "消费", "服务消费", "投资", "外贸", "进出口", "商务运行")):
            return "经济"
        if has_any(text, ("关税", "出口管制", "制裁", "贸易摩擦", "美方", "欧盟", "芯片")):
            return "贸易科技战"
        return "涉外"
    if source in {"外交部", "Reuters China", "FT", "FT China", "AP", "Nikkei", "Kyodo"}:
        if has_any(text, ("中东", "伊朗", "以色列", "俄乌", "乌克兰", "加沙", "朝鲜", "半岛")):
            return "热点地区"
        return "涉外"
    if source in {"国务院", "新华社", "人民日报"}:
        if has_any(text, ("任免", "任命", "免去", "人事")):
            return "人事"
        return "要闻"
    return item.category


def build_diagnostics(items: list[NewsItem], report_date: date) -> dict:
    checks = []
    for check in MANDATORY_CHECKS:
        matched = matching_items(items, check["sources"], check.get("keywords", ()))
        checks.append(
            {
                "key": check["key"],
                "label": check["label"],
                "section": check["section"],
                "ok": bool(matched),
                "required": check["required"],
                "matched_titles": [item.title for item in matched[:5]],
                "message": "抓取成功" if matched else "未成功抓取，需人工复核。",
            }
        )
    return {
        "report_date": report_date.isoformat(),
        "mandatory_checks": checks,
        "source_counts_before_caps": dict(Counter(item.source_name for item in items)),
        "total_candidates_before_caps": len(items),
        "source_caps": SOURCE_DAILY_CAPS,
    }


def matching_items(items: Iterable[NewsItem], sources: set[str], keywords: tuple[str, ...]) -> list[NewsItem]:
    matched = []
    for item in items:
        if item.source_name not in sources:
            continue
        if keywords and not has_any(searchable_text(item), keywords):
            continue
        matched.append(item)
    return matched


def apply_source_caps(items: list[NewsItem]) -> list[NewsItem]:
    counts: Counter[str] = Counter()
    result = []
    for item in items:
        cap = SOURCE_DAILY_CAPS.get(item.source_name)
        if cap is not None and counts[item.source_name] >= cap:
            continue
        counts[item.source_name] += 1
        result.append(item)
    return result


def ensure_mandatory_items(capped: list[NewsItem], ranked: list[NewsItem]) -> list[NewsItem]:
    result = list(capped)
    existing_keys = {item.source_key for item in result}
    for check in MANDATORY_CHECKS:
        matched = matching_items(ranked, check["sources"], check.get("keywords", ()))
        if not matched:
            continue
        if any(item.source_key in existing_keys for item in matched):
            continue
        item = matched[0]
        result.append(item)
        existing_keys.add(item.source_key)
    return sorted(result, key=item_rank, reverse=True)


def item_rank(item: NewsItem) -> tuple[int, int, int, str]:
    text = searchable_text(item)
    priority = {"P0": 3, "P1": 2, "P2": 1}.get(SOURCE_PRIORITY.get(item.source_name, "P1"), 1)
    ai_priority = 1 if item.category == "科技与产业" and has_any(text, CHINA_AI_PRIORITY_KEYWORDS) else 0
    published = int(item.published_at.timestamp()) if item.published_at else 0
    return priority, ai_priority, published, item.title


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    for keyword in keywords:
        normalized = keyword.lower()
        if normalized.isascii() and normalized.replace(" ", "").isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered):
                return True
        elif normalized in lowered:
            return True
    return False


def searchable_text(item: NewsItem) -> str:
    return " ".join([item.title or "", item.summary or "", item.content or "", item.url or ""])
