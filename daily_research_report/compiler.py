from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from urllib.parse import urlsplit

from daily_research_report.curation import SOURCE_PRIORITY, has_any, searchable_text
from daily_research_report.models import NewsItem

RESEARCH_SECTIONS = [
    "今日总判断",
    "高层与政策",
    "组织人事",
    "反腐政法",
    "涉台涉外",
    "AI与科技产业",
    "宏观与市场",
    "地方与社会治理",
    "风险提示/后续跟踪",
]

REQUIRED_STABLE_SECTIONS = {"组织人事", "反腐政法", "涉台涉外", "AI与科技产业"}

OFFICIAL_P0_SOURCES = {
    "新闻联播",
    "CCTV新闻联播",
    "新华社",
    "人民日报",
    "国务院",
    "外交部",
    "国台办",
    "国防部",
    "中纪委国家监委",
    "最高法",
    "最高检",
    "央行",
    "商务部",
    "国家发改委",
}

GRADE_A_SOURCES = {
    "国务院",
    "外交部",
    "国台办",
    "国防部",
    "中纪委国家监委",
    "最高法",
    "最高检",
    "央行",
    "商务部",
    "国家发改委",
}

GRADE_B_SOURCES = {
    "新闻联播",
    "CCTV新闻联播",
    "新华社",
    "人民日报",
    "经济日报",
}

GRADE_C_SOURCES = {
    "财联社",
    "证券时报",
    "第一财经",
    "界面新闻",
    "财新",
    "澎湃",
    "中国新闻网",
    "Reuters China",
    "Reuters",
    "FT",
    "FT China",
    "Bloomberg",
    "AP",
    "BBC China",
    "Nikkei",
    "Kyodo",
    "OpenAI News",
    "Anthropic News",
    "Google DeepMind Blog",
    "NVIDIA Newsroom",
    "Microsoft AI Blog",
    "Meta AI Blog",
}

LOW_VALUE_KEYWORDS = (
    "优惠",
    "促销",
    "旅游",
    "娱乐",
    "体育",
    "天气",
    "生活方式",
    "customer story",
    "case study",
)

PERSONNEL_KEYWORDS = (
    "任命",
    "免去",
    "履新",
    "已任",
    "出任",
    "调任",
    "任职",
    "职务调整",
    "党委常委",
    "宣传部部长",
    "组织部部长",
    "副省长",
)
ANTI_CORRUPTION_STAGES = {
    "审查调查": ("审查调查", "接受纪律审查", "监察调查", "调查审查"),
    "双开": ("双开", "开除党籍", "开除公职"),
    "逮捕": ("逮捕", "决定逮捕"),
    "公诉": ("公诉", "提起公诉", "起诉"),
    "一审": ("一审", "一审宣判", "一审开庭"),
    "二审": ("二审", "终审", "维持原判"),
}

AI_TAGS = {
    "政策牵引": ("政策", "监管", "备案", "治理", "安全评估", "行政令", "法案", "标准"),
    "产业落地": ("产品", "发布", "上线", "应用", "agent", "机器人", "算力", "模型", "芯片"),
    "治理风险": ("诉讼", "版权", "安全", "风险", "虚假", "隐私", "合规"),
    "出海竞争": ("出口管制", "制裁", "海外", "竞争", "关税", "供应链", "nvidia", "gpu"),
}

TAIWAN_KEYWORDS = ("台湾", "台海", "国台办", "两岸", "赖清德", "民进党", "国民党", "对台")
FOREIGN_KEYWORDS = (
    "美国",
    "欧盟",
    "日本",
    "韩国",
    "菲律宾",
    "俄罗斯",
    "乌克兰",
    "伊朗",
    "以色列",
    "中东",
    "外交部",
    "reuters",
    "ft",
)

BENCHMARK_TOPICS = [
    {"topic": "高考保障与基础教育", "keywords": ("丁薛祥", "高考", "基础教育")},
    {"topic": "正确政绩观学习教育指导组", "keywords": ("正确政绩观", "中央指导组")},
    {"topic": "十四五经济社会发展成就", "keywords": ("十四五", "成就", "GDP")},
    {"topic": "发改委国企座谈与资源安全", "keywords": ("发改委", "国有企业", "能源安全")},
    {"topic": "二手房成交修复", "keywords": ("二手房", "成交", "住宅")},
    {"topic": "央行逆回购归零", "keywords": ("逆回购", "央行", "流动性")},
    {"topic": "太空算力产业", "keywords": ("太空算力", "卫星", "算力")},
    {"topic": "中国大模型与企业Agent", "keywords": ("DeepSeek", "千问", "Agent")},
    {"topic": "美国AI安全监管", "keywords": ("AI安全", "行政令", "美国")},
    {"topic": "郑丽文访美", "keywords": ("郑丽文", "访美")},
    {"topic": "国台办发布会", "keywords": ("国台办", "发布会")},
    {"topic": "对台军售", "keywords": ("对台军售", "台湾", "美国")},
    {"topic": "强迫劳动关税", "keywords": ("强迫劳动", "关税", "301")},
    {"topic": "欧盟技术主权", "keywords": ("欧盟", "技术主权")},
    {"topic": "所罗门安全协议", "keywords": ("所罗门", "安全协议")},
    {"topic": "中日航班与日本防务", "keywords": ("中日航线", "日本", "防卫省")},
    {"topic": "中菲摩擦", "keywords": ("菲律宾", "中菲")},
    {"topic": "伊朗局势", "keywords": ("伊朗", "美军")},
    {"topic": "宁夏宣传部长调整", "keywords": ("韩冬", "宁夏", "宣传部")},
    {"topic": "王莉霞公诉", "keywords": ("王莉霞", "公诉")},
]


def build_compiler_context(
    report_date: date,
    items: list[NewsItem],
    diagnostics: dict | None,
    raw_count: int,
    dedup_count: int,
) -> dict:
    candidates = [build_candidate(idx + 1, item) for idx, item in enumerate(items)]
    clusters = build_clusters(candidates, diagnostics or {})
    quality = score_quality(candidates, clusters, diagnostics or {}, raw_count, dedup_count)
    benchmark = evaluate_benchmark(clusters)
    return {
        "pipeline_version": "research-daily-compiler-v1",
        "report_date": report_date.isoformat(),
        "required_sections": RESEARCH_SECTIONS,
        "candidates": candidates,
        "clusters": clusters,
        "quality_scores": quality,
        "benchmark_eval": benchmark,
        "diagnostics": diagnostics or {},
        "rules": {
            "cluster_first": "不要逐条改写，先按政策链条、人事链条、事件链条、涉外链条、产业链条聚合。",
            "required_sections": sorted(REQUIRED_STABLE_SECTIONS),
            "link_grade": "A=官方原文，B=新华社/人民日报/央视/地方党报，C=商业媒体/国际通讯社，D=转载或聚合。",
            "minimum_merge_rate": 0.6,
        },
    }


def build_candidate(candidate_id: int, item: NewsItem) -> dict:
    text = searchable_text(item)
    entities = extract_entities(text)
    link_grade = grade_link(item)
    domain_tags = sorted(domain_tags_for(item, text))
    return {
        "candidate_id": f"N{candidate_id:04d}",
        "title": item.title,
        "time": item.published_at.isoformat() if item.published_at else None,
        "source": item.source_name,
        "link": item.url,
        "link_grade": link_grade,
        "body_summary": item.summary or item.content or item.title,
        "category": map_section(item, text),
        "entities": entities["entities"],
        "people": entities["people"],
        "organizations": entities["organizations"],
        "locations": entities["locations"],
        "domain_tags": domain_tags,
        "importance_score": importance_score(item, text, link_grade, domain_tags),
    }


def build_clusters(candidates: list[dict], diagnostics: dict) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for candidate in candidates:
        grouped[cluster_key(candidate)].append(candidate)

    clusters = []
    for idx, ((category, key), members) in enumerate(sorted(grouped.items(), key=cluster_sort_key), 1):
        members = sorted(members, key=lambda item: item["importance_score"], reverse=True)
        clusters.append(make_cluster(idx, category, key, members))

    clusters = merge_thin_related_clusters(clusters)
    clusters = ensure_required_section_clusters(clusters, diagnostics)
    return clusters


def cluster_sort_key(item: tuple[tuple[str, str], list[dict]]) -> tuple[int, float, str]:
    (category, key), members = item
    section_rank = {
        "高层与政策": 8,
        "组织人事": 7,
        "反腐政法": 7,
        "涉台涉外": 6,
        "AI与科技产业": 6,
        "宏观与市场": 5,
        "地方与社会治理": 4,
        "风险提示/后续跟踪": 3,
    }.get(category, 1)
    return (-section_rank, -max(member["importance_score"] for member in members), key)


def cluster_key(candidate: dict) -> tuple[str, str]:
    text = normalized_text(candidate)
    category = candidate["category"]
    if category == "反腐政法":
        stage = anti_corruption_stage(text)
        if stage != "待判定阶段":
            person = first_or(candidate["people"], first_or(candidate["entities"], "反腐政法"))
            return category, f"{stage}:{person}"
        entity = first_or(candidate["organizations"], first_or(candidate["entities"], candidate["title"][:14]))
        return category, f"政法:{normalize_key(entity)}"
    if category == "组织人事":
        person = first_or(candidate["people"], first_or(candidate["organizations"], "组织人事"))
        return category, normalize_key(person)
    if category == "涉台涉外":
        target = foreign_target(candidate, text)
        issue = issue_type(text)
        return category, f"{target}:{issue}"
    if category == "AI与科技产业":
        ai_kind = ai_subtype(text)
        entity = first_or(candidate["organizations"], first_or(candidate["entities"], ai_kind))
        return category, f"{ai_kind}:{normalize_key(entity)}"
    if category == "宏观与市场":
        return category, macro_key(text)
    if category == "高层与政策":
        entity = first_or(candidate["people"], first_or(candidate["organizations"], "政策"))
        return category, normalize_key(entity)
    return category, first_or(candidate["domain_tags"], normalize_key(candidate["title"][:16]))


def make_cluster(cluster_number: int, category: str, key: str, members: list[dict]) -> dict:
    primary = select_primary_source(members)
    included_ids = [member["candidate_id"] for member in members]
    entities = sorted({entity for member in members for entity in member["entities"]})[:12]
    time_values = [member["time"] for member in members if member.get("time")]
    source_list = source_entries(members)
    return {
        "cluster_id": f"C{cluster_number:03d}",
        "cluster_title": cluster_title(category, key, members),
        "primary_source": primary,
        "supporting_sources": [source for source in source_list if source["url"] != primary.get("url")],
        "included_items": included_ids,
        "included_item_titles": [member["title"] for member in members],
        "entities": entities,
        "time_span": {"start": min(time_values) if time_values else None, "end": max(time_values) if time_values else None},
        "category": category,
        "importance_score": round(sum(member["importance_score"] for member in members) / len(members), 2),
        "confidence_score": confidence_score(members),
        "why_grouped": why_grouped(category, key, members),
        "link_grades": sorted({source["grade"] for source in source_list}),
        "domain_tags": sorted({tag for member in members for tag in member["domain_tags"]}),
    }


def merge_thin_related_clusters(clusters: list[dict]) -> list[dict]:
    # Keep the MVP deterministic: only merge single-item clusters that share exact category and entity.
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for cluster in clusters:
        shared_entity = first_or(cluster.get("entities", []), cluster["cluster_title"][:12])
        buckets[(cluster["category"], normalize_key(shared_entity))].append(cluster)

    merged = []
    number = 1
    for bucket in buckets.values():
        if len(bucket) == 1:
            cluster = bucket[0]
            cluster["cluster_id"] = f"C{number:03d}"
            merged.append(cluster)
            number += 1
            continue
        members = []
        category = bucket[0]["category"]
        key = first_or(bucket[0].get("entities", []), bucket[0]["cluster_title"])
        for cluster in bucket:
            for title, item_id in zip(cluster["included_item_titles"], cluster["included_items"]):
                members.append(
                    {
                        "candidate_id": item_id,
                        "title": title,
                        "source": cluster["primary_source"].get("name", ""),
                        "link": cluster["primary_source"].get("url", ""),
                        "link_grade": cluster["primary_source"].get("grade", "D"),
                        "importance_score": cluster["importance_score"],
                        "entities": cluster.get("entities", []),
                        "domain_tags": cluster.get("domain_tags", []),
                        "time": cluster.get("time_span", {}).get("start"),
                    }
                )
        merged.append(make_cluster(number, category, key, members))
        number += 1
    return sorted(merged, key=lambda cluster: (-cluster["importance_score"], cluster["cluster_id"]))


def ensure_required_section_clusters(clusters: list[dict], diagnostics: dict) -> list[dict]:
    present = {cluster["category"] for cluster in clusters if cluster.get("included_items")}
    result = list(clusters)
    next_id = len(result) + 1
    for section in sorted(REQUIRED_STABLE_SECTIONS, key=RESEARCH_SECTIONS.index):
        if section in present:
            continue
        status = "未见重大新增。"
        if section == "反腐政法" and mandatory_failed(diagnostics, "ccdi"):
            status = "未成功抓取，需人工复核。"
        result.append(
            {
                "cluster_id": f"C{next_id:03d}",
                "cluster_title": section,
                "primary_source": {},
                "supporting_sources": [],
                "included_items": [],
                "included_item_titles": [],
                "entities": [],
                "time_span": {"start": None, "end": None},
                "category": section,
                "importance_score": 0.0,
                "confidence_score": 0.0,
                "why_grouped": status,
                "status": status,
                "link_grades": [],
                "domain_tags": [],
            }
        )
        next_id += 1
    return result


def score_quality(
    candidates: list[dict],
    clusters: list[dict],
    diagnostics: dict,
    raw_count: int,
    dedup_count: int,
) -> dict:
    source_names = {candidate["source"] for candidate in candidates}
    content_clusters = [cluster for cluster in clusters if cluster.get("included_items")]
    merged_clusters = [cluster for cluster in content_clusters if len(cluster.get("included_items", [])) >= 2]
    verifiable_links = [
        candidate
        for candidate in candidates
        if candidate.get("link") and candidate.get("link_grade") in {"A", "B", "C"}
    ]
    low_value = [candidate for candidate in candidates if has_any(normalized_text(candidate), LOW_VALUE_KEYWORDS)]
    return {
        "官方源覆盖率": percent(len(source_names & OFFICIAL_P0_SOURCES), len(OFFICIAL_P0_SOURCES)),
        "组织人事覆盖": section_coverage(clusters, "组织人事"),
        "反腐政法覆盖": section_coverage(clusters, "反腐政法"),
        "涉台涉外覆盖": section_coverage(clusters, "涉台涉外"),
        "AI栏目质量": ai_quality_score(clusters),
        "链接可验证率": percent(len(verifiable_links), len(candidates)),
        "议题簇合并率": percent(len(merged_clusters), len(content_clusters)),
        "重复新闻率": percent(max(raw_count - dedup_count, 0), raw_count),
        "低价值资讯占比": percent(len(low_value), len(candidates)),
        "硬性检查通过数": f"{sum(1 for item in diagnostics.get('mandatory_checks', []) if item.get('ok'))}/{len(diagnostics.get('mandatory_checks', []))}",
        "说明": "议题簇合并率低于60%时，应优先补充外部校验源或手动源，避免单条新闻改写。",
    }


def evaluate_benchmark(clusters: list[dict]) -> dict:
    corpus = "\n".join(
        " ".join(
            [
                cluster.get("cluster_title", ""),
                " ".join(cluster.get("included_item_titles", [])),
                " ".join(cluster.get("entities", [])),
                " ".join(cluster.get("domain_tags", [])),
            ]
        )
        for cluster in clusters
    )
    matched = []
    missing = []
    for topic in BENCHMARK_TOPICS:
        if any(keyword.lower() in corpus.lower() for keyword in topic["keywords"]):
            matched.append(topic["topic"])
        else:
            missing.append(topic["topic"])
    required_sections = {"组织人事", "反腐政法", "涉台涉外", "AI与科技产业"}
    covered_sections = {cluster["category"] for cluster in clusters if cluster.get("included_items")}
    return {
        "benchmark_name": "人工版议题敏感度样例",
        "coverage_rate": percent(len(matched), len(BENCHMARK_TOPICS)),
        "matched_topics": matched,
        "missing_topics": missing,
        "required_section_gaps": sorted(required_sections - covered_sections),
        "gap_summary": "自动版应重点补齐人工版中的跨源合并、背景链条和组织人事/反腐/涉台/AI稳定覆盖。",
    }


def map_section(item: NewsItem, text: str) -> str:
    source = item.source_name
    if item.category == "反腐" or source == "中纪委国家监委":
        return "反腐政法"
    if item.category == "科技与产业" or has_any(text, ("人工智能", "ai", "大模型", "芯片", "算力")):
        return "AI与科技产业"
    if item.category in {"涉台", "涉外", "热点地区", "贸易科技战"} or has_any(text, TAIWAN_KEYWORDS + FOREIGN_KEYWORDS):
        return "涉台涉外"
    if source in {"新闻联播", "CCTV新闻联播", "新华社", "人民日报", "国务院"}:
        if has_any(text, PERSONNEL_KEYWORDS):
            return "组织人事"
        return "高层与政策"
    if item.category in {"人事"} or is_personnel_news(text):
        return "组织人事"
    if source in {"最高法", "最高检"}:
        return "反腐政法" if is_anti_corruption_news(text) else "地方与社会治理"
    if item.category == "经济" or source in {"央行", "国家发改委", "商务部"}:
        return "宏观与市场"
    return "地方与社会治理"


def extract_entities(text: str) -> dict:
    organizations = sorted(set(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,30}(?:部|委|局|办|院|会|署|集团|公司|银行|大学|基金|政府|法院|检察院)", text)))[:12]
    people = sorted(set(re.findall(r"(?<![\u4e00-\u9fa5])[\u4e00-\u9fa5]{2,3}(?=(?:表示|指出|强调|称|任|已任|被|涉嫌|接受|访问|调研|主持))", text)))[:10]
    locations = sorted(
        set(
            re.findall(
                r"(北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|香港|澳门|新疆|西藏|内蒙古|宁夏|广西|美国|欧盟|日本|韩国|菲律宾|伊朗|以色列|俄罗斯|乌克兰|所罗门)",
                text,
            )
        )
    )[:12]
    entities = sorted(set(organizations + people + locations))[:20]
    return {"entities": entities, "people": people, "organizations": organizations, "locations": locations}


def domain_tags_for(item: NewsItem, text: str) -> set[str]:
    tags = {SOURCE_PRIORITY.get(item.source_name, "P1"), map_section(item, text)}
    if has_any(text, TAIWAN_KEYWORDS):
        tags.add("涉台")
    if has_any(text, FOREIGN_KEYWORDS):
        tags.add("涉外")
    if has_any(text, ("人工智能", "ai", "大模型", "openai", "deepseek", "算力", "芯片")):
        tags.add("AI")
        tags.add(ai_subtype(text))
    if has_any(text, ("关税", "出口管制", "制裁", "强迫劳动", "供应链")):
        tags.add("贸易科技战")
    if is_personnel_news(text):
        tags.add("组织人事")
    if is_anti_corruption_news(text):
        tags.add("反腐阶段:" + anti_corruption_stage(text))
    return tags


def importance_score(item: NewsItem, text: str, link_grade: str, domain_tags: list[str]) -> float:
    score = {"A": 0.88, "B": 0.78, "C": 0.64, "D": 0.45}.get(link_grade, 0.45)
    if SOURCE_PRIORITY.get(item.source_name) == "P0":
        score += 0.08
    if has_any(text, ("习近平", "李强", "丁薛祥", "国务院", "党中央", "外交部", "中纪委", "国台办")):
        score += 0.08
    if any(tag in {"涉台", "AI", "贸易科技战", "组织人事"} for tag in domain_tags):
        score += 0.05
    if has_any(text, LOW_VALUE_KEYWORDS):
        score -= 0.12
    return round(max(min(score, 1.0), 0.1), 2)


def grade_link(item: NewsItem) -> str:
    if item.source_name in GRADE_A_SOURCES:
        return "A"
    if item.source_name in GRADE_B_SOURCES:
        return "B"
    if item.source_name in GRADE_C_SOURCES:
        return "C"
    host = urlsplit(item.url).netloc
    if host.endswith((".gov.cn", ".gov")):
        return "A"
    if any(domain in host for domain in ("news.cn", "people.com.cn", "cctv.com")):
        return "B"
    return "D"


def select_primary_source(members: list[dict]) -> dict:
    ranked = sorted(
        source_entries(members),
        key=lambda source: ({"A": 4, "B": 3, "C": 2, "D": 1}.get(source["grade"], 0), source["importance_score"]),
        reverse=True,
    )
    return ranked[0] if ranked else {}


def source_entries(members: list[dict]) -> list[dict]:
    seen = set()
    entries = []
    for member in members:
        key = (member.get("source"), member.get("link"))
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "name": member.get("source", "来源"),
                "url": member.get("link", ""),
                "grade": member.get("link_grade", "D"),
                "importance_score": member.get("importance_score", 0),
            }
        )
    return entries


def cluster_title(category: str, key: str, members: list[dict]) -> str:
    if len(members) == 1:
        return members[0]["title"]
    clean_key = key.split(":", 1)[-1]
    return f"{clean_key}相关议题：{members[0]['title']}"


def why_grouped(category: str, key: str, members: list[dict]) -> str:
    if len(members) == 1:
        return "单条高优先级候选，暂无可交叉合并来源。"
    chain = {
        "高层与政策": "同一政策链条或高层活动。",
        "组织人事": "同一人事/机构链条，需补全前任后任与缺位情况。",
        "反腐政法": "同一案件阶段或政法链条。",
        "涉台涉外": "同一对象国家/地区与议题类型。",
        "AI与科技产业": "同一AI/产业链条，按政策牵引、产业落地、治理风险、出海竞争归纳。",
        "宏观与市场": "同一宏观政策、市场数据或监管链条。",
    }.get(category, "同一社会治理议题。")
    return f"{chain} 聚合键：{key}；包含{len(members)}条候选。"


def confidence_score(members: list[dict]) -> float:
    grades = {member.get("link_grade") for member in members}
    base = 0.55 + min(len(members), 3) * 0.1
    if "A" in grades:
        base += 0.15
    elif "B" in grades:
        base += 0.1
    if len(grades) >= 2:
        base += 0.05
    return round(min(base, 0.98), 2)


def ai_quality_score(clusters: list[dict]) -> str:
    ai_clusters = [cluster for cluster in clusters if cluster["category"] == "AI与科技产业" and cluster.get("included_items")]
    if not ai_clusters:
        return "0%"
    valued = [
        cluster
        for cluster in ai_clusters
        if set(cluster.get("domain_tags", [])) & {"政策牵引", "产业落地", "治理风险", "出海竞争", "贸易科技战"}
    ]
    return percent(len(valued), len(ai_clusters))


def section_coverage(clusters: list[dict], section: str) -> str:
    return "100%" if any(cluster["category"] == section and cluster.get("included_items") for cluster in clusters) else "0%"


def mandatory_failed(diagnostics: dict, key: str) -> bool:
    return any(check.get("key") == key and not check.get("ok") for check in diagnostics.get("mandatory_checks", []))


def foreign_target(candidate: dict, text: str) -> str:
    for location in candidate.get("locations", []):
        if location in TAIWAN_KEYWORDS or location in FOREIGN_KEYWORDS:
            return location
    for keyword in TAIWAN_KEYWORDS + FOREIGN_KEYWORDS:
        if keyword.lower() in text:
            return keyword
    return first_or(candidate.get("locations", []), "涉外")


def issue_type(text: str) -> str:
    if has_any(text, ("关税", "贸易", "出口管制", "制裁", "供应链")):
        return "经贸/科技战"
    if has_any(text, ("军售", "军演", "防务", "安全", "国防")):
        return "安全防务"
    if has_any(text, ("记者会", "回应", "声明", "访问", "会见")):
        return "外交互动"
    if has_any(text, TAIWAN_KEYWORDS):
        return "涉台政治"
    return "区域风险"


def ai_subtype(text: str) -> str:
    for tag, keywords in AI_TAGS.items():
        if has_any(text, keywords):
            return tag
    return "产业落地"


def anti_corruption_stage(text: str) -> str:
    for stage, keywords in ANTI_CORRUPTION_STAGES.items():
        if has_any(text, keywords):
            return stage
    return "待判定阶段"


def is_personnel_news(text: str) -> bool:
    if has_any(text, ("总书记", "书记处")) and not has_any(text, ("任命", "免去", "履新", "已任", "出任", "调任")):
        return False
    return has_any(text, PERSONNEL_KEYWORDS)


def is_anti_corruption_news(text: str) -> bool:
    stage_keywords = tuple(sum((list(values) for values in ANTI_CORRUPTION_STAGES.values()), []))
    return has_any(text, stage_keywords + ("受贿", "违纪违法", "监察调查", "纪律审查"))


def macro_key(text: str) -> str:
    for keyword in ("GDP", "十四五", "逆回购", "房地产", "二手房", "外贸", "消费", "投资", "债券", "汇率"):
        if keyword.lower() in text:
            return keyword
    return "宏观与市场"


def normalized_text(candidate: dict) -> str:
    return " ".join(
        [
            candidate.get("title", ""),
            candidate.get("body_summary", ""),
            " ".join(candidate.get("entities", [])),
            " ".join(candidate.get("domain_tags", [])),
        ]
    ).lower()


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", value or "议题")[:24]


def first_or(values: list[str], default: str) -> str:
    return values[0] if values else default


def percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0%"
    return f"{round(numerator / denominator * 100)}%"
