from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from daily_research_report.collectors import collect_sources
from daily_research_report.compiler import build_compiler_context
from daily_research_report.config import load_sources
from daily_research_report.curation import curate_items
from daily_research_report.dedup import deduplicate
from daily_research_report.feishu import push_report
from daily_research_report.llm import generate_report
from daily_research_report.render import write_report
from daily_research_report.time_utils import day_window, resolve_report_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate research daily report.")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--sources", default="config/sources.yaml")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--template-dir", default="templates")
    parser.add_argument("--timezone", default=os.getenv("REPORT_TIMEZONE", "Asia/Shanghai"))
    parser.add_argument("--max-items-per-source", type=int, default=20)
    parser.add_argument("--push", action="store_true", help="Push report summary to Feishu.")
    parser.add_argument(
        "--public-url",
        default=os.getenv("REPORT_PUBLIC_URL"),
        help="Public URL for Feishu card link. Defaults to REPORT_PUBLIC_URL.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    report_date = resolve_report_date(args.date, args.timezone)
    start, end = day_window(report_date, args.timezone)
    sources = load_sources(args.sources)
    raw_items = collect_sources(sources, start, end, args.max_items_per_source)
    deduped_items = deduplicate(raw_items)
    curated = curate_items(deduped_items, report_date)
    items = curated.items
    logging.info(
        "Collected %s raw items, %s after deduplication, %s after curation",
        len(raw_items),
        len(deduped_items),
        len(items),
    )
    for check in curated.diagnostics.get("mandatory_checks", []):
        if not check.get("ok"):
            logging.warning("Mandatory check failed: %s", check["label"])

    compiler_context = build_compiler_context(
        report_date,
        items,
        diagnostics=curated.diagnostics,
        raw_count=len(raw_items),
        dedup_count=len(deduped_items),
    )
    logging.info(
        "Built %s candidates and %s issue clusters",
        len(compiler_context.get("candidates", [])),
        len(compiler_context.get("clusters", [])),
    )

    report = generate_report(
        report_date,
        items,
        diagnostics=curated.diagnostics,
        compiler_context=compiler_context,
    )
    md_path, html_path, json_path = write_report(
        report,
        output_dir=Path(args.output_dir),
        template_dir=Path(args.template_dir),
    )
    logging.info("Wrote %s, %s and %s", md_path, html_path, json_path)

    if args.push:
        ok = push_report(report["title"], md_path, html_path, public_url=args.public_url)
        logging.info("Feishu push %s", "succeeded" if ok else "skipped or failed")


if __name__ == "__main__":
    main()
