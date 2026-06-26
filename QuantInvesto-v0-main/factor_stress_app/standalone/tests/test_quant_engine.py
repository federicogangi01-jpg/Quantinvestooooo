from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant_engine import FACTORS, normalize_portfolio, factor_analysis, monte_carlo, scenario_analysis  # noqa: E402


BASE_PAYLOAD = {
    "start": "2021-01-01",
    "end": "2024-12-31",
    "use_demo_data": True,
    "portfolio": [
        {"symbol": "SPY", "weight": 50, "asset_class": "etf"},
        {"symbol": "TLT", "weight": 30, "asset_class": "bond"},
        {"symbol": "GLD", "weight": 20, "asset_class": "gold"},
    ],
}


class QuantEngineTest(unittest.TestCase):
    def test_normalize_portfolio(self) -> None:
        assets = normalize_portfolio(BASE_PAYLOAD["portfolio"])
        self.assertEqual([asset.symbol for asset in assets], ["SPY", "TLT", "GLD"])
        self.assertAlmostEqual(sum(asset.weight for asset in assets), 1.0)

    def test_factor_analysis(self) -> None:
        result = factor_analysis(BASE_PAYLOAD)
        self.assertEqual(result["factors"], FACTORS)
        self.assertEqual(set(result["beta_matrix"]), {"SPY", "TLT", "GLD"})
        self.assertEqual(len(result["exposures"]), 3)
        for exposure in result["exposures"]:
            self.assertIn("alpha", exposure)
            self.assertIn("r_squared", exposure)
            self.assertLessEqual(exposure["r_squared"], 1.0)

    def test_predefined_scenario(self) -> None:
        result = scenario_analysis({**BASE_PAYLOAD, "scenario": "Global Recession"})
        self.assertEqual(result["scenario"], "Global Recession")
        self.assertEqual(len(result["contribution_by_asset"]), 3)
        self.assertEqual(len(result["contribution_by_factor"]), 6)
        self.assertIn(result["best_hedge"], {"SPY", "TLT", "GLD"})
        self.assertIn(result["worst_contributor"], {"SPY", "TLT", "GLD"})

    def test_custom_scenario(self) -> None:
        result = scenario_analysis(
            {
                **BASE_PAYLOAD,
                "scenario": "Custom Shock",
                "shocks": {"equity": -0.1, "rates": 0.02, "inflation": -0.01, "gold": 0.05, "commodities": -0.03, "credit": -0.04},
            }
        )
        self.assertEqual(result["scenario"], "Custom Shock")
        self.assertAlmostEqual(result["shocks"]["gold"], 0.05)

    def test_monte_carlo(self) -> None:
        result = monte_carlo({**BASE_PAYLOAD, "simulations": 500, "horizon_days": 126, "seed": 5})
        self.assertLessEqual(result["percentile_5"], result["percentile_50"])
        self.assertLessEqual(result["percentile_50"], result["percentile_95"])
        self.assertGreaterEqual(result["probability_of_loss"], 0)
        self.assertLessEqual(result["probability_of_loss"], 1)


if __name__ == "__main__":
    unittest.main()

