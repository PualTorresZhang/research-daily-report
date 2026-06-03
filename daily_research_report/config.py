from __future__ import annotations

from pathlib import Path

import yaml

from daily_research_report.models import Source


def load_sources(path: str | Path) -> list[Source]:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = []
    for raw in data.get("sources", []):
        sources.append(
            Source(
                name=raw["name"],
                category=raw.get("category", "要闻"),
                type=raw["type"],
                url=raw["url"],
                enabled=raw.get("enabled", True),
                selector=raw.get("selector"),
                meta={k: v for k, v in raw.items() if k not in {"name", "category", "type", "url", "enabled", "selector"}},
            )
        )
    return [source for source in sources if source.enabled]

