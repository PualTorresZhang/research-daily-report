from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import date

from openai import OpenAI

from daily_research_report.compiler import RESEARCH_SECTIONS
from daily_research_report.models import NewsItem

LOGGER = logging.getLogger(__name__)

SECTIONS = RESEARCH_SECTIONS


SYSTEM_PROMPT = """你是研究院情报简报编辑。你只根据输入材料写日报，不能编造新闻。
风格接近内部研究院情报简报，不要公众号风，不要泛泛趋势分析。
事实占约70%，背景解释占约20%，判断观察占约10%。
只收录报告日期当天真实发生的新动态；没有重大增量的栏目可写“昨日无重大增量”或省略。
每条新闻必须保留来源链接。X/Twitter内容如未交叉验证，必须标记“未必经官方确认”。
你要像研究院情报值班编辑一样筛选，不要做普通新闻聚合；优先 A/B 级链接，控制单一来源堆叠。
固定栏目为：今日总判断、高层与政策、组织人事、反腐政法、涉台涉外、AI与科技产业、宏观与市场、地方与社会治理、风险提示/后续跟踪。
不要逐条改写候选新闻，必须优先围绕输入的 clusters 做“主题合并”。
每个正式议题统一输出四层：【事实】、【背景/补充】、【观察】、【后续跟踪】。
组织人事要尽量补全前任、后任、历史职务、同类调整、缺位情况；材料不足时明确“材料未显示”。
反腐政法必须标注阶段：审查调查、双开、逮捕、公诉、一审、二审或待判定阶段。
涉台涉外必须标注对象国家/地区、议题类型、是否外溢。
AI栏目不能只写模型/产品，必须按政策牵引、产业落地、治理风险、出海竞争四类归纳。
每个议题簇必须有一句“小判断”，但不要夸张预测。
组织人事、反腐政法、涉台涉外、AI与科技产业四栏不能缺；没有重大新闻时写“未见重大新增”。
如果诊断信息显示“新闻联播”或“反腐”未成功抓取，不得写“未见重大新增”，必须写“未成功抓取，需人工复核。”。
输出必须是 JSON，不要输出 Markdown。"""


def generate_report(
    report_date: date,
    items: list[NewsItem],
    model: str | None = None,
    diagnostics: dict | None = None,
    compiler_context: dict | None = None,
) -> dict:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    selected_model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "12000"))
    timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    if not api_key:
        return fallback_report(report_date, items, diagnostics=diagnostics, compiler_context=compiler_context)

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=1)
    payload = {
        "report_date": report_date.isoformat(),
        "required_sections": SECTIONS,
        "diagnostics": diagnostics or {},
        "compiler_context": compiler_context or {},
        "items": [serialize_item(item) for item in items],
        "format": {
            "title": "研究院版日报｜YYYY年M月D日",
            "daily_judgment": "今日总判断，2-4句，必须基于议题簇，不做宏大预测。",
            "top_issue_cards": [{"cluster_id": "C001", "title": "重点议题", "why_it_matters": "为什么重要"}],
            "sections": [
                {
                    "name": "栏目名",
                    "items": [
                        {
                            "cluster_id": "C001",
                            "title": "标题",
                            "facts": "【事实】发生了什么，谁说的，数据是什么。",
                            "background": "【背景/补充】与既有政策、人事、案件、国际议题的关系。",
                            "observation": "【观察】为什么值得研究院读者关注。",
                            "follow_up": "【后续跟踪】下一步看什么。",
                            "sources": [{"name": "来源名称", "url": "URL", "grade": "A/B/C/D"}],
                        }
                    ],
                    "status": "无条目时写未见重大新增或未成功抓取，需人工复核。",
                }
            ],
            "quality_scores": "沿用 compiler_context.quality_scores，并可补充一句短说明。",
            "benchmark_eval": "沿用 compiler_context.benchmark_eval，并生成差距说明。",
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
                        "先使用 compiler_context.clusters，而不是逐条新闻改写。"
                        "材料不足时不要硬凑；同一议题尽量合并；D级链接不能作为唯一依据。\n"
                        f"{json.dumps(payload, ensure_ascii=False)}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        LOGGER.info("LLM response received from %s", selected_model)
        content = response.choices[0].message.content or "{}"
        return normalize_report(
            load_json_content(content),
            report_date,
            items,
            diagnostics=diagnostics,
            compiler_context=compiler_context,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("LLM generation failed, falling back to template report: %s", exc)
        return fallback_report(report_date, items, diagnostics=diagnostics, compiler_context=compiler_context)


def fallback_report(
    report_date: date,
    items: list[NewsItem],
    diagnostics: dict | None = None,
    compiler_context: dict | None = None,
) -> dict:
    if compiler_context:
        return fallback_compiler_report(report_date, items, diagnostics, compiler_context)

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
        "quality_scores": {},
        "benchmark_eval": {},
        "meta": {"llm_used": False},
    }


def fallback_compiler_report(
    report_date: date,
    items: list[NewsItem],
    diagnostics: dict | None,
    compiler_context: dict,
) -> dict:
    clusters = compiler_context.get("clusters", [])
    sections = []
    by_section: dict[str, list[dict]] = defaultdict(list)
    for cluster in clusters:
        if not cluster.get("included_items"):
            by_section[cluster["category"]].append({"status": cluster.get("status") or "未见重大新增。"})
            continue
        by_section[cluster["category"]].append(fallback_cluster_item(cluster))

    for section in SECTIONS:
        if section == "今日总判断":
            continue
        values = by_section.get(section, [])
        section_items = [value for value in values if "title" in value][:6]
        status_values = [value["status"] for value in values if "status" in value]
        if section_items:
            sections.append({"name": section, "items": section_items})
        elif status_values:
            sections.append({"name": section, "items": [], "status": status_values[0]})
        elif section in {"组织人事", "反腐政法", "涉台涉外", "AI与科技产业"}:
            sections.append({"name": section, "items": [], "status": "未见重大新增。"})

    return normalize_report(
        {
            "title": format_title(report_date),
            "daily_judgment": fallback_daily_judgment(clusters),
            "top_issue_cards": top_issue_cards(clusters),
            "sections": sections,
            "index": index_items(items),
            "clusters": clusters,
            "candidates": compiler_context.get("candidates", []),
            "quality_scores": compiler_context.get("quality_scores", {}),
            "benchmark_eval": compiler_context.get("benchmark_eval", {}),
            "meta": {"llm_used": False, "pipeline_version": compiler_context.get("pipeline_version")},
        },
        report_date,
        items,
        diagnostics=diagnostics,
        compiler_context=compiler_context,
    )


def fallback_cluster_item(cluster: dict) -> dict:
    primary = cluster.get("primary_source") or {}
    sources = [primary] + cluster.get("supporting_sources", [])
    sources = [source for source in sources if source.get("url")]
    stage_tags = [tag for tag in cluster.get("domain_tags", []) if tag.startswith("反腐阶段:")]
    issue_note = ""
    if cluster["category"] == "反腐政法":
        issue_note = f"阶段：{stage_tags[0].split(':', 1)[1] if stage_tags else '待判定阶段'}。"
    elif cluster["category"] == "涉台涉外":
        issue_note = "对象与议题类型见议题簇实体和聚合理由，需结合外交部回应或外媒交叉校验。"
    elif cluster["category"] == "AI与科技产业":
        issue_note = "按政策牵引、产业落地、治理风险、出海竞争归纳，普通企业案例降权。"
    return {
        "cluster_id": cluster["cluster_id"],
        "title": cluster["cluster_title"],
        "facts": "；".join(cluster.get("included_item_titles", [])[:3]) or cluster["cluster_title"],
        "background": f"{issue_note} 聚合依据：{cluster.get('why_grouped', '')}",
        "observation": "该条由结构化议题簇生成，未调用 LLM 深度改写；需人工复核判断层。",
        "follow_up": "关注是否出现A/B级来源补充、后续表态、执行细则或案件阶段推进。",
        "sources": sources,
    }


def fallback_daily_judgment(clusters: list[dict]) -> str:
    content_clusters = [cluster for cluster in clusters if cluster.get("included_items")]
    if not content_clusters:
        return "候选新闻池未形成高置信议题簇，需人工复核采集链路。"
    top = "、".join(cluster["cluster_title"] for cluster in content_clusters[:3])
    return f"今日重点集中在{top}。该判断来自结构化议题簇，未调用 LLM 深度改写。"


def top_issue_cards(clusters: list[dict]) -> list[dict]:
    return [
        {
            "cluster_id": cluster["cluster_id"],
            "title": cluster["cluster_title"],
            "why_it_matters": cluster.get("why_grouped", ""),
        }
        for cluster in clusters
        if cluster.get("included_items")
    ][:5]


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
    compiler_context: dict | None = None,
) -> dict:
    report.setdefault("title", format_title(report_date))
    report.setdefault("sections", [])
    report["index"] = report.get("index") or index_items(items)
    report["meta"] = report.get("meta") or {"llm_used": True}
    if compiler_context:
        report.setdefault("clusters", compiler_context.get("clusters", []))
        report.setdefault("candidates", compiler_context.get("candidates", []))
        if not isinstance(report.get("quality_scores"), dict):
            report["quality_scores"] = compiler_context.get("quality_scores", {})
        if not isinstance(report.get("benchmark_eval"), dict):
            report["benchmark_eval"] = compiler_context.get("benchmark_eval", {})
        if not isinstance(report.get("top_issue_cards"), list):
            report["top_issue_cards"] = top_issue_cards(compiler_context.get("clusters", []))
        report["meta"].setdefault("pipeline_version", compiler_context.get("pipeline_version"))
    ensure_research_sections(report)
    apply_diagnostic_status(report, diagnostics)
    return report


def ensure_research_sections(report: dict) -> None:
    sections = report.setdefault("sections", [])
    by_name = {section.get("name"): section for section in sections}
    for section_name in ("组织人事", "反腐政法", "涉台涉外", "AI与科技产业"):
        if section_name not in by_name:
            sections.append({"name": section_name, "items": [], "status": "未见重大新增。"})


def apply_diagnostic_status(report: dict, diagnostics: dict | None) -> None:
    if not diagnostics:
        return
    sections = report.setdefault("sections", [])
    by_name = {section.get("name"): section for section in sections}
    section_aliases = {"反腐": "反腐政法", "附：新闻联播": "高层与政策"}
    for old_section, section_name in section_aliases.items():
        if not missing_required_section(old_section, diagnostics):
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
