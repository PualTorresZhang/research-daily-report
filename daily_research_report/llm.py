from __future__ import annotations

import json
import logging
import os
import re
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
你要像研究院情报值班编辑一样筛选，不要做普通新闻聚合；优先 P0 官方源，控制单一来源堆叠。
固定栏目为：要闻、经济、科技与产业、涉台、贸易科技战、涉外、热点地区、人事、反腐、附：新闻联播。
分类要求：AI诉讼、AI产品、AI监管、芯片/出口管制均归入“科技与产业”或“贸易科技战”，不要因为来源是BBC/Reuters就放入热点地区。
AI栏目优先级：中国AI政策/监管、中国大厂AI产品、OpenAI/Anthropic/Google/NVIDIA重大产品或政策、AI与芯片/出口管制、AI法律诉讼；普通企业案例降权。
如果诊断信息显示“新闻联播”或“反腐”未成功抓取，不得写“昨日无重大增量”，必须写“未成功抓取，需人工复核。”。
输出必须是 JSON，不要输出 Markdown。"""


def generate_report(
    report_date: date,
    items: list[NewsItem],
    model: str | None = None,
    diagnostics: dict | None = None,
) -> dict:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    selected_model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "12000"))
    if not api_key:
        return fallback_report(report_date, items, diagnostics=diagnostics)

    client = OpenAI(api_key=api_key, base_url=base_url)
    payload = {
        "report_date": report_date.isoformat(),
        "required_sections": SECTIONS,
        "diagnostics": diagnostics or {},
        "editorial_rules": {
            "source_caps": {
                "商务部": 3,
                "OpenAI News": 2,
                "BBC China": 2,
                "央行": 2,
            },
            "hard_checks": [
                "新闻联播当天摘要",
                "国务院当天发布",
                "外交部例行记者会",
                "中纪委当天通报",
                "国台办/涉台关键词",
                "Reuters/FT中国、AI、中东、台海关键词",
            ],
            "quality_gate": "新闻联播或反腐抓取失败时必须写“未成功抓取，需人工复核。”",
        },
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
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or "{}"
        return normalize_report(load_json_content(content), report_date, items, diagnostics=diagnostics)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("LLM generation failed, falling back to template report: %s", exc)
        return fallback_report(report_date, items, diagnostics=diagnostics)


def fallback_report(report_date: date, items: list[NewsItem], diagnostics: dict | None = None) -> dict:
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
        elif missing_required_section(section, diagnostics):
            sections.append({"name": section, "items": [], "status": "未成功抓取，需人工复核。"})
    return {
        "title": format_title(report_date),
        "sections": sections,
        "index": index_items(items),
        "meta": {"llm_used": False},
    }


def missing_required_section(section: str, diagnostics: dict | None) -> bool:
    if not diagnostics:
        return False
    if section not in {"反腐", "附：新闻联播"}:
        return False
    return any(
        check.get("section") == section and check.get("required") and not check.get("ok")
        for check in diagnostics.get("mandatory_checks", [])
    )


def load_json_content(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        extracted = extract_json_object(content)
        if extracted and extracted != content:
            return json.loads(extracted)
        raise


def extract_json_object(content: str) -> str | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return content[start : end + 1]
    return None


def normalize_report(
    report: dict,
    report_date: date,
    items: list[NewsItem],
    diagnostics: dict | None = None,
) -> dict:
    report.setdefault("title", format_title(report_date))
    report.setdefault("sections", [])
    report["index"] = report.get("index") or index_items(items)
    report["meta"] = report.get("meta") or {"llm_used": True}
    apply_diagnostic_status(report, diagnostics)
    return report


def apply_diagnostic_status(report: dict, diagnostics: dict | None) -> None:
    if not diagnostics:
        return
    sections = report.setdefault("sections", [])
    by_name = {section.get("name"): section for section in sections}
    for section_name in ("反腐", "附：新闻联播"):
        if not missing_required_section(section_name, diagnostics):
            continue
        section = by_name.get(section_name)
        if section is None:
            section = {"name": section_name, "items": []}
            sections.append(section)
        if not section.get("items"):
            section["status"] = "未成功抓取，需人工复核。"


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
