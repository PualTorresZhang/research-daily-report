from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import date

from openai import OpenAI

from daily_research_report.models import NewsItem

LOGGER = logging.getLogger(__name__)

SECTIONS = [
    "要闻",
    "经济",
    "科技与产业",
    "涉台",
    "贸易科技战",
    "涉外",
    "热点地区",
    "人事",
    "反腐",
    "附：新闻联播",
]


SYSTEM_PROMPT = """你是研究院情报简报编辑。你只根据输入材料写日报，不能编造新闻。
风格接近内部研究院情报简报，不要公众号风，不要泛泛趋势分析。
事实占约70%，背景解释占约20%，判断观察占约10%。
只收录报告日期当天真实发生的新动态；没有重大增量的栏目可写“昨日无重大增量”或省略。
每条新闻必须保留来源链接。X/Twitter内容如未交叉验证，必须标记“未必经官方确认”。
输出必须是 JSON，不要输出 Markdown。"""


def generate_report(report_date: date, items: list[NewsItem], model: str | None = None) -> dict:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    selected_model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        return fallback_report(report_date, items)

    client = OpenAI(api_key=api_key, base_url=base_url)
    payload = {
        "report_date": report_date.isoformat(),
        "required_sections": SECTIONS,
        "items": [serialize_item(item) for item in items],
        "format": {
            "title": "研究院版日报｜YYYY年M月D日",
            "sections": [
                {
                    "name": "栏目名",
                    "items": [
                        {
                            "title": "标题",
                            "event": "事件事实",
                            "focus": ["重点1", "重点2"],
                            "observation": "观察",
                            "sources": [{"name": "来源名称", "url": "URL"}],
                        }
                    ],
                }
            ],
        },
    }
    try:
        response = client.chat.completions.create(
            model=selected_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "请基于以下采集材料生成昨日研究院版日报。"
                        "材料不足时不要硬凑；同一事件合并；每条新闻最多3个重点。\n"
                        f"{json.dumps(payload, ensure_ascii=False)}"
                    ),
                },
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        return normalize_report(json.loads(content), report_date, items)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("LLM generation failed, falling back to template report: %s", exc)
        return fallback_report(report_date, items)


def fallback_report(report_date: date, items: list[NewsItem]) -> dict:
    grouped: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        grouped[item.category].append(item)
    sections = []
    for section in SECTIONS:
        section_items = []
        for item in grouped.get(section, [])[:5]:
            section_items.append(
                {
                    "title": item.title,
                    "event": item.summary or item.title,
                    "focus": ["该条由采集材料直接生成，未调用 LLM 深度改写。"],
                    "observation": "需人工复核其重要性与上下文。",
                    "sources": [{"name": item.source_name, "url": item.url}],
                }
            )
        if section_items:
            sections.append({"name": section, "items": section_items})
    return {
        "title": format_title(report_date),
        "sections": sections,
        "index": index_items(items),
        "meta": {"llm_used": False},
    }


def normalize_report(report: dict, report_date: date, items: list[NewsItem]) -> dict:
    report.setdefault("title", format_title(report_date))
    report.setdefault("sections", [])
    report["index"] = report.get("index") or index_items(items)
    report["meta"] = report.get("meta") or {"llm_used": True}
    return report


def serialize_item(item: NewsItem) -> dict:
    return {
        "title": item.title,
        "url": item.url,
        "source_name": item.source_name,
        "category": item.category,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "summary": item.summary,
        "content": item.content,
    }


def index_items(items: list[NewsItem]) -> list[dict]:
    return [
        {
            "title": item.title,
            "source_name": item.source_name,
            "url": item.url,
            "category": item.category,
        }
        for item in items
    ]


def format_title(report_date: date) -> str:
    return f"研究院版日报｜{report_date.year}年{report_date.month}月{report_date.day}日"
