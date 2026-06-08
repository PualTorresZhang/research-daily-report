from __future__ import annotations

import json
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_markdown(report: dict) -> str:
    lines: list[str] = [f"# {report['title']}", ""]
    if report.get("daily_judgment"):
        lines.extend(["## 今日总判断", "", report["daily_judgment"], ""])

    top_cards = report.get("top_issue_cards") or []
    if top_cards:
        lines.extend(["## 重点议题卡片", ""])
        for card in top_cards[:6]:
            lines.append(
                f"- **{card.get('title', '重点议题')}**"
                f"（{card.get('cluster_id', '')}）：{card.get('why_it_matters', '')}"
            )
        lines.append("")

    section_names = [section.get("name", "") for section in report.get("sections", []) if section.get("name")]
    if section_names:
        lines.extend(["## 栏目目录", ""])
        for name in section_names:
            lines.append(f"- {name}")
        lines.append("")

    section_number = 1
    for section in report.get("sections", []):
        lines.extend([f"## {chinese_number(section_number)}、{section['name']}", ""])
        items = section.get("items", [])
        if not items:
            lines.extend([section.get("status") or "未见重大新增。", ""])
        for idx, item in enumerate(items, 1):
            lines.extend([f"{idx}. {item['title']}", ""])
            if item.get("facts") or item.get("background") or item.get("follow_up"):
                lines.extend(
                    [
                        f"【事实】{item.get('facts', '')}",
                        "",
                        f"【背景/补充】{item.get('background', '')}",
                        "",
                        f"【观察】{item.get('observation', '')}",
                        "",
                        f"【后续跟踪】{item.get('follow_up', '')}",
                        "",
                        "来源：",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"事件：{item.get('event', '')}",
                        "",
                        "重点：",
                    ]
                )
                for focus in item.get("focus", []):
                    lines.append(f"- {focus}")
                lines.extend(["", f"观察：{item.get('observation', '')}", "", "来源："])
            for source in item.get("sources", []):
                grade = source.get("grade")
                label = f"{grade}｜{source['name']}" if grade else source["name"]
                lines.append(f"- [{label}]({source['url']})")
            lines.append("")
        section_number += 1

    lines.extend([f"## {chinese_number(section_number)}、原文索引", ""])
    for idx, item in enumerate(report.get("index", []), 1):
        lines.append(
            f"{idx}. [{item['title']}]({item['url']})"
            f"（{item.get('source_name', '来源')}｜{item.get('category', '未分类')}）"
        )
    lines.append("")
    return "\n".join(lines)


def render_html(markdown_text: str, report: dict, template_dir: str | Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    body = markdown.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return template.render(title=report["title"], body=body, report=report)


def write_report(report: dict, output_dir: str | Path, template_dir: str | Path) -> tuple[Path, Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    date_part = report["title"].split("｜", 1)[1]
    filename = date_part.replace("年", "-").replace("月", "-").replace("日", "")
    parts = filename.split("-")
    filename = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    md = render_markdown(report)
    html = render_html(md, report, template_dir)
    md_path = out / f"{filename}.md"
    html_path = out / f"{filename}.html"
    json_path = out / f"{filename}.json"
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, html_path, json_path


def chinese_number(value: int) -> str:
    numbers = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
        11: "十一",
        12: "十二",
    }
    return numbers.get(value, str(value))
