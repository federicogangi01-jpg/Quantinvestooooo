#!/usr/bin/env python3
"""Validate the OpenAI explanation path with a small deterministic fixture."""

from __future__ import annotations

import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def main() -> int:
    payload = app.build_ai_explanation_input(
        "backtesting",
        {
            "capital": 50000,
            "age": 40,
            "horizonYears": 10,
            "riskPreference": "balanced",
            "objective": "Crescita del capitale nel lungo periodo",
        },
        [{"symbol": "US0378331005", "weight": 1.0, "assetClass": "equity"}],
        {"initialCapital": 50000, "cagr": 0.06, "maxDrawdown": -0.2, "sharpe": 0.8},
        "FREE",
        "it",
    )
    started = time.perf_counter()
    explanation, error = app.call_openai_explanation_detail(payload)
    elapsed = time.perf_counter() - started
    if error or not explanation:
        print(f"OpenAI validation failed: {error}")
        return 1
    missing = set(app.AI_EXPLANATION_DETAIL_FIELDS) - set(explanation)
    if missing:
        print(f"OpenAI validation failed: missing fields {sorted(missing)}")
        return 1
    if explanation["disclaimer"] != app.AI_EXPLANATION_DISCLAIMER:
        print("OpenAI validation failed: disclaimer mismatch")
        return 1
    print(f"OpenAI validation ok in {elapsed:.2f}s using {app.OPENAI_EXPLANATION_MODEL}")
    print(explanation["simple_summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
