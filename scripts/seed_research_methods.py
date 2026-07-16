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

from taxonomy.models import ResearchMethod  # noqa: E402

METHODS = [
    ("machine-learning", "機器學習", ["ML", "machine learning"]),
    ("lstm", "LSTM", ["Long Short-Term Memory", "長短期記憶網路"]),
    ("var", "VAR", ["Vector Autoregression", "向量自我迴歸"]),
    ("time-series", "時間序列", ["Time Series", "時間序列分析"]),
    ("regime-switching", "Regime Switching", ["狀態轉換模型", "Markov switching"]),
    ("panel-data", "Panel Data", ["追蹤資料", "面板資料"]),
    ("difference-in-differences", "DID", ["Difference-in-Differences", "雙重差分"]),
    ("survey", "問卷調查", ["Survey", "questionnaire"]),
]


def main() -> None:
    for slug, display_name, aliases in METHODS:
        ResearchMethod.objects.update_or_create(
            slug=slug,
            defaults={"display_name": display_name, "aliases": aliases, "status": "active"},
        )
    print(f"Seeded {len(METHODS)} research methods.")


if __name__ == "__main__":
    main()

