#!/usr/bin/env python
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from taxonomy.models import ResearchField  # noqa: E402

FIELDS = [
    ("artificial-intelligence", "人工智慧", ["AI", "machine intelligence"]),
    ("finance", "金融", ["financial studies", "財務金融"]),
    ("ai-finance", "AI 金融", ["FinTech", "金融科技", "智能投資"]),
    ("esg", "ESG 與永續金融", ["永續", "sustainable finance", "公司治理"]),
    ("data-science", "資料科學", ["data analytics", "數據分析"]),
    ("macroeconomics", "總體經濟", ["景氣循環", "macroeconomy"]),
    ("supply-chain", "供應鏈", ["supply chain", "供應鏈管理"]),
]


def main() -> None:
    for slug, display_name, aliases in FIELDS:
        ResearchField.objects.update_or_create(
            slug=slug,
            defaults={"display_name": display_name, "aliases": aliases, "status": "active"},
        )
    print(f"Seeded {len(FIELDS)} research fields.")


if __name__ == "__main__":
    main()

