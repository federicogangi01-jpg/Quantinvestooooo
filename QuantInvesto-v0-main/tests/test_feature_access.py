import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


class FeatureAccessTests(unittest.TestCase):
    def setUp(self):
        self._openai_explanations_enabled = app.OPENAI_EXPLANATIONS_ENABLED
        self._ai_cache_ttl = app.AI_EXPLANATION_CACHE_TTL_SECONDS
        app.OPENAI_EXPLANATIONS_ENABLED = False
        app.AI_EXPLANATION_CACHE_TTL_SECONDS = 0

    def tearDown(self):
        app.OPENAI_EXPLANATIONS_ENABLED = self._openai_explanations_enabled
        app.AI_EXPLANATION_CACHE_TTL_SECONDS = self._ai_cache_ttl

    def goal_pillar_score(self, health):
        for pillar in health.get("pillars", []):
            key = str(pillar.get("key") or "")
            name = str(pillar.get("name") or "")
            if "Goal" in key or "goal" in key or "Coerenza" in name or "obiettivo" in name:
                return pillar["score"]
        self.fail("Goal alignment pillar not found")

    def find_standard_portfolio(self, portfolio_id):
        for portfolio in app.STANDARD_PORTFOLIOS:
            if portfolio.get("id") == portfolio_id:
                return portfolio
        self.fail(f"Standard portfolio {portfolio_id} not found")

    def test_free_can_use_basic_backtest(self):
        self.assertTrue(app.has_feature("FREE", "basicBacktest"))
        self.assertTrue(app.has_feature("FREE", "expectedReturn"))

    def test_free_cannot_use_plus_features(self):
        self.assertFalse(app.has_feature("FREE", "efficientFrontier"))
        self.assertFalse(app.has_feature("FREE", "monteCarlo"))
        self.assertFalse(app.has_feature("FREE", "portfolioTracking"))

    def test_free_cannot_use_advanced_features(self):
        self.assertFalse(app.has_feature("FREE", "famaFrench"))
        self.assertFalse(app.has_feature("FREE", "sortinoRatio"))
        self.assertFalse(app.has_feature("FREE", "rollingReturns"))

    def test_plus_can_use_plus_features(self):
        self.assertTrue(app.has_feature("PLUS", "efficientFrontier"))
        self.assertTrue(app.has_feature("PLUS", "monteCarlo"))
        self.assertTrue(app.has_feature("PLUS", "rebalancingSuggestions"))

    def test_plus_cannot_use_advanced_features(self):
        self.assertFalse(app.has_feature("PLUS", "famaFrench"))
        self.assertFalse(app.has_feature("PLUS", "sortinoRatio"))
        self.assertFalse(app.has_feature("PLUS", "factorShockAnalysis"))

    def test_advanced_can_use_everything_configured(self):
        for feature, value in app.PLAN_FEATURES["ADVANCED"].items():
            if feature == "maxPortfolios":
                self.assertEqual(value, "unlimited")
            else:
                self.assertTrue(app.has_feature("ADVANCED", feature), feature)

    def test_standard_portfolios_are_complete_and_weights_sum_to_100(self):
        self.assertEqual(len(app.STANDARD_PORTFOLIOS), 25)
        seen_ids = set()
        seen_names = set()
        required = {"id", "name", "originalName", "plan", "category", "riskLevel", "investorProfile", "suggestedHorizon", "description", "warning", "holdings"}
        required_holding = {"name", "isin", "weight", "role"}
        for portfolio in app.STANDARD_PORTFOLIOS:
            self.assertTrue(required.issubset(portfolio.keys()), portfolio.get("name"))
            self.assertNotIn(portfolio["id"], seen_ids)
            self.assertNotIn(portfolio["name"], seen_names)
            seen_ids.add(portfolio["id"])
            seen_names.add(portfolio["name"])
            self.assertIn(portfolio["plan"], {"plus", "advanced"})
            self.assertIn(portfolio["category"], {"defensive", "moderate", "aggressive", "income", "growth", "thematic", "bond", "esg"})
            self.assertTrue(portfolio["name"])
            self.assertTrue(portfolio["description"])
            self.assertTrue(portfolio["warning"])
            self.assertTrue(portfolio["holdings"])
            total = sum(float(item["weight"]) for item in portfolio["holdings"])
            self.assertAlmostEqual(total, 100.0)
            for holding in portfolio["holdings"]:
                self.assertTrue(required_holding.issubset(holding.keys()), portfolio["name"])
                self.assertTrue(holding["isin"], portfolio["name"])

    def test_standard_portfolio_gating_by_plan(self):
        free_items = app.get_available_standard_portfolios("FREE", include_locked_preview=True)
        plus_items = app.get_available_standard_portfolios("PLUS", include_locked_preview=False)
        plus_preview = app.get_available_standard_portfolios("PLUS", include_locked_preview=True)
        advanced_items = app.get_available_standard_portfolios("ADVANCED", include_locked_preview=False)
        self.assertTrue(free_items)
        self.assertTrue(all(item["locked"] for item in free_items))
        self.assertTrue(all(item["category"] in {"defensive", "moderate", "aggressive"} for item in plus_items))
        self.assertFalse(any(item["plan"] == "advanced" for item in plus_items))
        self.assertTrue(any(item.get("locked") and item["plan"] == "advanced" for item in plus_preview))
        self.assertEqual(len(advanced_items), len(app.STANDARD_PORTFOLIOS))

    def test_standard_hard_exclusions_and_risk_formula(self):
        aggressive = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Azionario Globale Aggressivo")
        thematic = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Satellite AI & Big Data")
        defensive_input = {
            "investmentHorizon": "short",
            "goalPriority": "capital_protection",
            "declaredRiskTolerance": "low",
            "maxTemporaryLoss": "5_10",
            "investmentExperience": "base",
            "monthlyPAC": 0,
            "liquidityNeed": "high",
            "userPlan": "plus",
            "finalInvestorProfile": "defensive",
        }
        self.assertFalse(app.passes_standard_portfolio_hard_exclusions(aggressive, defensive_input))
        self.assertFalse(app.passes_standard_portfolio_hard_exclusions(thematic, {**defensive_input, "userPlan": "advanced"}))
        capacity = app.calculate_risk_capacity_score(defensive_input)
        tolerance = app.calculate_risk_tolerance_score(defensive_input)
        goal = app.calculate_goal_aggressiveness_score(defensive_input["goalPriority"])
        raw = capacity * 0.30 + tolerance * 0.45 + goal * 0.25
        capped = app.apply_hard_risk_caps(raw, defensive_input)
        self.assertLessEqual(capped, 35)

    def test_recommendation_activates_when_goal_score_is_low(self):
        result = app.get_recommended_standard_portfolio(
            {
                "investmentHorizon": "long",
                "goalPriority": "capital_growth",
                "declaredRiskTolerance": "balanced",
                "maxTemporaryLoss": "10_20",
                "investmentExperience": "base",
                "monthlyPAC": 0,
                "liquidityNeed": "low",
                "portfolioHealthScore": 70,
                "goalCompatibilityScore": 55,
                "userPlan": "plus",
            }
        )
        self.assertTrue(result["shouldSuggest"])
        self.assertEqual(result["recommendedPortfolio"]["plan"], "plus")

    def test_standard_recommendation_free_is_locked(self):
        result = app.get_recommended_standard_portfolio(
            {
                "investmentHorizon": "long",
                "goalPriority": "capital_growth",
                "declaredRiskTolerance": "balanced",
                "maxTemporaryLoss": "10_20",
                "investmentExperience": "base",
                "monthlyPAC": 0,
                "liquidityNeed": "low",
                "portfolioHealthScore": 55,
                "goalCompatibilityScore": 58,
                "userPlan": "free",
            }
        )
        self.assertTrue(result["shouldSuggest"])
        self.assertTrue(result["locked"])
        self.assertEqual(result["suggestedUpgrade"], "plus")

    def test_standard_recommendation_plus_uses_only_plus_categories(self):
        result = app.get_recommended_standard_portfolio(
            {
                "investmentHorizon": "long",
                "goalPriority": "capital_growth",
                "declaredRiskTolerance": "balanced",
                "maxTemporaryLoss": "10_20",
                "investmentExperience": "base",
                "monthlyPAC": 0,
                "liquidityNeed": "low",
                "portfolioHealthScore": 55,
                "goalCompatibilityScore": 58,
                "userPlan": "plus",
            }
        )
        self.assertTrue(result["shouldSuggest"])
        self.assertIn(result["recommendedPortfolio"]["category"], {"defensive", "moderate", "aggressive"})
        self.assertEqual(result["recommendedPortfolio"]["plan"], "plus")
        self.assertNotIn(result["recommendedPortfolio"]["name"], {"Tech Growth & EM ex-China", "Azionario Globale Aggressivo"})

    def test_standard_recommendation_not_shown_when_scores_are_sufficient(self):
        result = app.get_recommended_standard_portfolio(
            {
                "investmentHorizon": "long",
                "goalPriority": "capital_growth",
                "declaredRiskTolerance": "balanced",
                "maxTemporaryLoss": "10_20",
                "investmentExperience": "base",
                "monthlyPAC": 0,
                "liquidityNeed": "low",
                "portfolioHealthScore": 70,
                "goalCompatibilityScore": 65,
                "userPlan": "plus",
            }
        )
        self.assertFalse(result["shouldSuggest"])

    def test_standard_portfolio_analysis_builds_quantitative_benchmark(self):
        portfolio = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        analysis = app.build_standard_portfolio_analysis(
            portfolio,
            start=date(2020, 1, 1),
            end=date(2021, 1, 1),
            initial_capital=10000,
            rebalance_frequency="monthly",
            monte_carlo_settings={"horizonYears": 1, "simulations": 500},
            demo=True,
            user_plan="PLUS",
            name_prefix="Benchmark standard",
        )
        self.assertTrue(analysis["available"])
        self.assertIn("metrics", analysis)
        self.assertIn("equityCurve", analysis)
        comparison = app.compare_portfolios(
            {"name": "Portfolio inserito", "metrics": analysis["metrics"], "dailyReturns": analysis["dailyReturns"]},
            analysis,
            "user_vs_standard",
        )
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["comparisonType"], "user_vs_standard")

    def test_standard_benchmark_context_uses_selected_standard_as_primary_comparison(self):
        portfolio = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        context = app.resolve_analysis_comparison_context(
            user_portfolio=[{"symbol": "US0378331005", "weight": 1.0, "assetClass": "equity"}],
            optimized_portfolio={"available": True, "name": "Portfolio efficiente"},
            selected_standard_portfolio=portfolio,
            standard_portfolio_mode="use_as_benchmark",
            standard_benchmark_analysis={"available": True},
            benchmark_checkup={"score": 72, "label": "Coerente"},
        )
        self.assertEqual(context["comparisonMode"], "standard_benchmark")
        self.assertEqual(context["comparisonDataKey"], "standardBenchmarkAnalysis")
        self.assertEqual(context["benchmarkPortfolio"]["id"], portfolio["id"])
        self.assertFalse(context["shouldShowOptimizedAsPrimary"])
        self.assertEqual(context["labels"]["comparison"], "Portfolio benchmark standard QuantInvest")

    def test_plus_backtest_standard_benchmark_does_not_use_optimized_as_main_comparison(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        result = app.run_backtest(
            {
                "userPlan": "PLUS",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {
                    "capital": 10000,
                    "age": 40,
                    "horizonYears": 10,
                    "riskPreference": "balanced",
                    "maxTemporaryLoss": 0.25,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "standardPortfolioMode": "use_as_benchmark",
                "standardBenchmark": {"id": standard["id"], "name": standard["name"], "holdings": standard["holdings"]},
                "demo": True,
            }
        )
        context = result["analysisComparisonContext"]
        self.assertEqual(context["comparisonMode"], "standard_benchmark")
        self.assertEqual(context["comparisonDataKey"], "standardBenchmarkAnalysis")
        self.assertTrue(result["standardBenchmarkAnalysis"]["available"])
        self.assertIn("benchmarkCheckup", context)
        self.assertIn("benchmarkHealthScore", context)
        self.assertIn("benchmarkComment", context)
        self.assertIsInstance(context["benchmarkHealthScore"], dict)
        self.assertGreaterEqual(context["benchmarkHealthScore"]["overall"], 0)
        self.assertEqual(context["labels"]["monteCarloComparison"], "Monte Carlo benchmark standard")
        self.assertFalse(result["optimizedPortfolio"]["available"])
        self.assertTrue(result["optimizedPortfolio"]["standardBenchmark"])
        self.assertFalse(result["efficientFrontier"]["available"])
        self.assertTrue(result["efficientFrontier"]["standardBenchmark"])
        self.assertEqual(result["improvementPlan"]["activeContext"]["activeComparisonMode"], "standard_benchmark")
        self.assertEqual(result["improvementPlan"]["activeContext"]["activeSourcePortfolio"], "Portafoglio inserito")

    def test_advanced_standard_benchmark_populates_advanced_comparison_sections(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        result = app.run_backtest(
            {
                "userPlan": "ADVANCED",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {
                    "capital": 10000,
                    "age": 40,
                    "horizonYears": 10,
                    "riskPreference": "balanced",
                    "maxTemporaryLoss": 0.25,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "standardPortfolioMode": "use_as_benchmark",
                "standardBenchmark": {"id": standard["id"], "name": standard["name"], "holdings": standard["holdings"]},
                "simpleBenchmarks": ["AAPL", "MSFT", "SPY"],
                "benchmarkSymbols": ["AAPL", "MSFT", "SPY"],
                "demo": True,
            }
        )
        self.assertEqual(result["analysisComparisonContext"]["comparisonMode"], "standard_benchmark")
        self.assertEqual(result["analysisComparisonContext"]["labels"]["comparison"], "Portfolio benchmark standard QuantInvest")
        self.assertFalse(result["optimizedPortfolio"]["available"])
        self.assertTrue(result["standardBenchmarkAnalysis"]["available"])
        self.assertEqual(
            set(result["standardBenchmarkAnalysis"]["symbols"]),
            {holding["isin"] for holding in standard["holdings"]},
        )
        self.assertEqual(
            set(result["standardBenchmarkAnalysis"]["weights"].keys()),
            {holding["isin"] for holding in standard["holdings"]},
        )
        advanced = result["advancedAnalytics"]
        for section in ("famaFrench", "geography", "rollingSortino", "correlation", "pac", "drawdown", "factorRisk", "scenarioComparison"):
            self.assertTrue(advanced[section]["optimized"]["available"], section)
            self.assertIn("structuredExplanation", advanced[section]["optimized"], section)
        optimized_correlation_symbols = set(advanced["correlation"]["optimized"]["symbols"])
        self.assertFalse({"AAPL", "MSFT", "SPY"} & optimized_correlation_symbols)
        self.assertEqual(
            optimized_correlation_symbols,
            {holding["isin"] for holding in standard["holdings"]},
        )

    def test_advanced_standard_benchmark_works_when_payload_id_is_missing(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        payload_standard = {"name": standard["name"], "holdings": standard["holdings"]}
        result = app.run_backtest(
            {
                "userPlan": "ADVANCED",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {
                    "capital": 10000,
                    "age": 40,
                    "horizonYears": 10,
                    "riskPreference": "balanced",
                    "maxTemporaryLoss": 0.25,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "standardPortfolioMode": "use_as_benchmark",
                "standardBenchmark": payload_standard,
                "demo": True,
            }
        )
        self.assertTrue(result["standardBenchmarkAnalysis"]["available"])
        advanced = result["advancedAnalytics"]
        for section in ("famaFrench", "geography", "rollingSortino", "correlation", "pac", "drawdown", "factorRisk", "scenarioComparison"):
            self.assertTrue(advanced[section]["optimized"]["available"], section)
        self.assertEqual(
            set(advanced["correlation"]["optimized"]["symbols"]),
            {holding["isin"] for holding in standard["holdings"]},
        )

    def test_advanced_standard_benchmark_is_inferred_when_mode_is_missing(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        result = app.run_backtest(
            {
                "userPlan": "ADVANCED",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {
                    "capital": 10000,
                    "age": 40,
                    "horizonYears": 10,
                    "riskPreference": "balanced",
                    "maxTemporaryLoss": 0.25,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "standardBenchmark": {"name": standard["name"], "holdings": standard["holdings"]},
                "demo": True,
            }
        )
        self.assertEqual(result["analysisComparisonContext"]["comparisonMode"], "standard_benchmark")
        advanced = result["advancedAnalytics"]
        for section in ("famaFrench", "geography", "rollingSortino", "correlation", "pac", "drawdown", "factorRisk", "scenarioComparison"):
            self.assertTrue(advanced[section]["optimized"]["available"], section)

    def test_advanced_final_optimized_vs_standard_comments_use_optimized_source_label(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        optimized_override = [
            {"symbol": "IE00B6R52259", "weight": 60, "assetClass": "equity", "displayName": "iShares MSCI ACWI"},
            {"symbol": "IE00BZ043R46", "weight": 40, "assetClass": "bonds", "displayName": "iShares Core Global Aggregate Bond"},
        ]
        result = app.run_backtest(
            {
                "userPlan": "ADVANCED",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 55, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 45, "assetClass": "equity"},
                ],
                "investorProfile": {
                    "capital": 10000,
                    "age": 40,
                    "horizonYears": 10,
                    "riskPreference": "balanced",
                    "maxTemporaryLoss": 0.25,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "standardPortfolioMode": "use_as_benchmark",
                "standardBenchmark": {"id": standard["id"], "name": standard["name"], "holdings": standard["holdings"]},
                "improvementComparisonMode": "final_optimized_vs_recommended_standard",
                "portfolioOverride": optimized_override,
                "demo": True,
            }
        )
        request = result["advancedAnalytics"]["drawdown"]["optimized"]["explanationRequest"]
        self.assertIn("portfolio ottimizzato", request["commentInstruction"])
        self.assertIn("portfolio benchmark standard QuantInvest", request["commentInstruction"])
        current_correlation = result["advancedAnalytics"]["correlation"]["current"]
        benchmark_correlation = result["advancedAnalytics"]["correlation"]["optimized"]
        self.assertTrue(current_correlation["available"])
        self.assertEqual(set(current_correlation["symbols"]), {item["symbol"] for item in optimized_override})
        self.assertTrue(benchmark_correlation["available"])
        self.assertEqual(
            set(benchmark_correlation["symbols"]),
            {holding["isin"] for holding in standard["holdings"]},
        )

    def test_standard_only_mode_does_not_create_optimized_standard_portfolio(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        result = app.run_backtest(
            {
                "userPlan": "PLUS",
                "portfolio": [
                    {"symbol": holding["isin"], "weight": holding["weight"], "assetClass": "bonds" if "Bond" in holding["name"] else "equity"}
                    for holding in standard["holdings"]
                ],
                "investorProfile": {
                    "capital": 10000,
                    "age": 40,
                    "horizonYears": 5,
                    "riskPreference": "conservative",
                    "maxTemporaryLoss": 0.1,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": False, "scenario": "Global Recession"},
                "standardPortfolioMode": "use_as_portfolio",
                "standardBenchmark": {"id": standard["id"], "name": standard["name"], "holdings": standard["holdings"]},
                "demo": True,
            }
        )
        self.assertEqual(result["analysisComparisonContext"]["comparisonMode"], "standard_only")
        self.assertEqual(result["analysisComparisonContext"]["primaryPortfolio"]["id"], standard["id"])
        self.assertEqual(len(result["analysisComparisonContext"]["analyzedPortfolio"]), len(standard["holdings"]))
        self.assertFalse(result["optimizedPortfolio"]["available"])
        self.assertTrue(result["optimizedPortfolio"]["standardOnly"])
        self.assertFalse(result["efficientFrontier"]["available"])
        self.assertTrue(result["efficientFrontier"]["standardOnly"])
        self.assertEqual(result["improvementPlan"]["target"]["type"], "standard_self_analysis")
        self.assertIsNotNone(result["improvementPlan"]["standardOnly"])
        self.assertEqual(result["improvementPlan"]["actions"], [])

    def test_improvement_plan_uses_optimized_target_when_profile_is_coherent(self):
        target = app.resolve_improvement_target(
            comparisonMode="optimization",
            userPortfolio=[
                {"symbol": "US0378331005", "weight": 0.5, "assetClass": "equity", "displayName": "Apple"},
                {"symbol": "US5949181045", "weight": 0.5, "assetClass": "equity", "displayName": "Microsoft"},
            ],
            optimizedPortfolio={
                "available": True,
                "weights": [
                    {"symbol": "US0378331005", "weight": 0.45},
                    {"symbol": "US5949181045", "weight": 0.55},
                ],
            },
            selectedStandardPortfolio=None,
            recommendedStandardPortfolio=None,
            portfolioHealthScore={"overall": 75},
            goalCompatibilityScore=72,
            investorProfile={"riskPreference": "balanced", "maxTemporaryLoss": 0.25},
            maxTemporaryLoss=0.25,
            portfolioMetrics={"maxDrawdown": -0.18},
        )
        self.assertEqual(target["targetType"], "optimized")
        self.assertIn(target["correctionIntensity"], {"light", "balanced"})

    def test_improvement_plan_uses_recommended_standard_when_scores_are_low(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        target = app.resolve_improvement_target(
            comparisonMode="optimization",
            userPortfolio=[{"symbol": "US0378331005", "weight": 1.0, "assetClass": "equity", "displayName": "Apple"}],
            optimizedPortfolio={"available": True, "weights": [{"symbol": "US0378331005", "weight": 1.0}]},
            selectedStandardPortfolio=None,
            recommendedStandardPortfolio=standard,
            portfolioHealthScore={"overall": 45},
            goalCompatibilityScore=50,
            investorProfile={"riskPreference": "conservative", "maxTemporaryLoss": 0.1},
            maxTemporaryLoss=0.1,
            portfolioMetrics={"maxDrawdown": -0.32},
        )
        self.assertEqual(target["targetType"], "recommended_standard")
        self.assertEqual(target["correctionIntensity"], "drastic")

    def test_improvement_plan_uses_selected_standard_as_benchmark_target(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Moderato Globale USA Tilt")
        target = app.resolve_improvement_target(
            comparisonMode="standard_benchmark",
            userPortfolio=[{"symbol": "US0378331005", "weight": 1.0, "assetClass": "equity", "displayName": "Apple"}],
            optimizedPortfolio={"available": True, "weights": [{"symbol": "US0378331005", "weight": 1.0}]},
            selectedStandardPortfolio=standard,
            recommendedStandardPortfolio=None,
            portfolioHealthScore={"overall": 80},
            goalCompatibilityScore=80,
            investorProfile={"riskPreference": "balanced", "maxTemporaryLoss": 0.25},
            maxTemporaryLoss=0.25,
            portfolioMetrics={"maxDrawdown": -0.18},
        )
        self.assertEqual(target["targetType"], "selected_standard")
        self.assertIn("benchmark standard", target["title"])

    def test_resolve_improvement_context_handles_final_user_vs_recommended_standard(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        context = app.resolveImprovementContext(
            {
                "comparisonMode": "final_user_vs_recommended_standard",
                "userPortfolio": [{"symbol": "US0378331005", "weight": 1.0, "assetClass": "equity", "displayName": "Apple"}],
                "optimizedPortfolio": {"available": True, "weights": [{"symbol": "US0378331005", "weight": 1.0}]},
                "recommendedStandardPortfolio": standard,
                "portfolioHealthScore": 45,
                "goalCompatibilityScore": 51,
                "portfolioMetrics": {"maxDrawdown": -0.3},
                "recommendedStandardMetrics": {"maxDrawdown": -0.1},
            }
        )
        self.assertEqual(context["sectionMode"], "improvement_plan")
        self.assertEqual(context["targetType"], "recommended_standard")
        self.assertEqual(context["correctionIntensity"], "drastic")
        self.assertEqual(context["sourcePortfolio"][0]["symbol"], "US0378331005")
        self.assertEqual(context["targetPortfolio"]["id"], standard["id"])

    def test_resolve_improvement_context_handles_final_optimized_vs_recommended_standard(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        context = app.resolveImprovementContext(
            {
                "comparisonMode": "final_optimized_vs_recommended_standard",
                "userPortfolio": [{"symbol": "US0378331005", "weight": 1.0, "assetClass": "equity", "displayName": "Apple"}],
                "optimizedPortfolio": {
                    "available": True,
                    "weights": [{"symbol": "US0378331005", "weight": 0.7}, {"symbol": "US5949181045", "weight": 0.3}],
                    "metrics": {"maxDrawdown": -0.2},
                },
                "recommendedStandardPortfolio": standard,
                "portfolioHealthScore": 65,
                "goalCompatibilityScore": 62,
                "optimizedMetrics": {"maxDrawdown": -0.2},
                "recommendedStandardMetrics": {"maxDrawdown": -0.1},
            }
        )
        self.assertEqual(context["sectionMode"], "improvement_plan")
        self.assertEqual(context["targetType"], "recommended_standard")
        self.assertEqual(context["correctionIntensity"], "balanced")
        self.assertIsInstance(context["sourcePortfolio"], dict)
        self.assertEqual(context["targetPortfolio"]["id"], standard["id"])

    def test_backtest_exposes_active_improvement_context_for_final_choice(self):
        standard = next(item for item in app.STANDARD_PORTFOLIOS if item["name"] == "Difensivo Globale 25/75")
        result = app.run_backtest(
            {
                "userPlan": "PLUS",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 100, "assetClass": "equity", "displayName": "Apple"},
                ],
                "investorProfile": {
                    "capital": 10000,
                    "horizonYears": 10,
                    "riskPreference": "balanced",
                    "objective": "capital_protection",
                    "maxTemporaryLoss": 0.1,
                },
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": False, "scenario": "Global Recession"},
                "standardPortfolioMode": "use_as_benchmark",
                "standardBenchmark": {"id": standard["id"], "name": standard["name"], "holdings": standard["holdings"]},
                "improvementComparisonMode": "final_user_vs_recommended_standard",
                "demo": True,
            }
        )
        plan = result["improvementPlan"]
        self.assertEqual(plan["comparisonMode"], "final_user_vs_recommended_standard")
        self.assertEqual(plan["target"]["type"], "recommended_standard")
        self.assertEqual(plan["activeContext"]["activeSourcePortfolio"], "Portafoglio inserito")
        self.assertEqual(plan["activeContext"]["activeTargetPortfolio"], standard["name"])
        self.assertGreater(len(plan["actions"]), 0)

    def test_improvement_actions_are_limited_and_non_prescriptive(self):
        actions = app.generate_portfolio_actions(
            currentPortfolio=[
                {"symbol": "US0378331005", "weight": 0.80, "assetClass": "equity", "displayName": "Apple"},
                {"symbol": "US5949181045", "weight": 0.20, "assetClass": "equity", "displayName": "Microsoft"},
            ],
            targetPortfolio=[
                {"symbol": "US0378331005", "weight": 0.40, "assetClass": "equity", "displayName": "Apple"},
                {"symbol": "IE00B4WXJJ64", "weight": 0.60, "assetClass": "bonds", "displayName": "Euro Government Bond"},
            ],
            portfolioMetrics={},
            targetMetrics={},
            investorProfile={"riskPreference": "balanced"},
            goalPriority="capital_growth",
            maxTemporaryLoss=0.25,
            correctionIntensity="balanced",
        )
        self.assertGreaterEqual(len(actions), 1)
        text = json.dumps(actions).lower()
        self.assertNotIn("compra", text)

    def test_improvement_actions_keep_target_components_visible_when_portfolios_are_different(self):
        actions = app.generate_portfolio_actions(
            currentPortfolio=[
                {"symbol": "AAA", "weight": 0.34, "assetClass": "equity", "displayName": "Strumento A"},
                {"symbol": "BBB", "weight": 0.33, "assetClass": "equity", "displayName": "Strumento B"},
                {"symbol": "CCC", "weight": 0.33, "assetClass": "equity", "displayName": "Strumento C"},
            ],
            targetPortfolio=[
                {"symbol": "DDD", "weight": 0.30, "assetClass": "equity", "displayName": "Target D"},
                {"symbol": "EEE", "weight": 0.30, "assetClass": "bonds", "displayName": "Target E"},
                {"symbol": "FFF", "weight": 0.40, "assetClass": "gold", "displayName": "Target F"},
            ],
            portfolioMetrics={},
            targetMetrics={},
            investorProfile={"riskPreference": "balanced"},
            goalPriority="capital_growth",
            maxTemporaryLoss=0.25,
            correctionIntensity="balanced",
        )
        target_actions = [item for item in actions if item["actionType"] in {"add", "increase"}]
        source_actions = [item for item in actions if item["actionType"] in {"remove", "reduce"}]
        self.assertEqual({item["instrumentName"] for item in target_actions}, {"Target D", "Target E", "Target F"})
        self.assertEqual({item["instrumentName"] for item in source_actions}, {"Strumento A", "Strumento B", "Strumento C"})
        self.assertGreaterEqual(sum(float(item["targetWeight"]) for item in target_actions), 99.0)
        self.assertEqual(len(actions), 6)
        text = json.dumps(actions).lower()
        self.assertNotIn("compra", text)
        self.assertNotIn("vendi", text)

    def test_improvement_actions_include_spdr_sp500_when_absent_from_target(self):
        actions = app.generate_portfolio_actions(
            currentPortfolio=[
                {
                    "symbol": "US78462F1030",
                    "weight": 0.40,
                    "assetClass": "equity",
                    "displayName": "SPDR S&P 500 ETF Trust-US",
                },
                {"symbol": "US0378331005", "weight": 0.30, "assetClass": "equity", "displayName": "Apple"},
                {"symbol": "US5949181045", "weight": 0.30, "assetClass": "equity", "displayName": "Microsoft"},
            ],
            targetPortfolio=[
                {"symbol": "IE00B6R52259", "weight": 0.40, "assetClass": "equity", "displayName": "iShares MSCI ACWI"},
                {"symbol": "IE00BZ043R46", "weight": 0.40, "assetClass": "bonds", "displayName": "iShares Core Global Aggregate Bond"},
                {"symbol": "IE00B4ND3602", "weight": 0.20, "assetClass": "gold", "displayName": "iShares Physical Gold"},
            ],
            portfolioMetrics={},
            targetMetrics={},
            investorProfile={"riskPreference": "balanced"},
            goalPriority="capital_growth",
            maxTemporaryLoss=0.25,
            correctionIntensity="balanced",
        )
        by_name = {item["instrumentName"]: item for item in actions}
        self.assertIn("SPDR S&P 500 ETF Trust-US", by_name)
        self.assertEqual(by_name["SPDR S&P 500 ETF Trust-US"]["actionType"], "remove")
        self.assertEqual(float(by_name["SPDR S&P 500 ETF Trust-US"]["targetWeight"]), 0.0)
        self.assertEqual(len([item for item in actions if item["actionType"] in {"add", "increase"}]), 3)
        self.assertEqual(len([item for item in actions if item["actionType"] in {"remove", "reduce"}]), 3)

    def test_improvement_action_completeness_reconciles_source_and_target(self):
        source = [
            {"symbol": "US78462F1030", "weight": 0.40, "assetClass": "equity", "displayName": "SPDR S&P 500 ETF Trust-US"},
            {"symbol": "IE00B6R52259", "weight": 0.30, "assetClass": "equity", "displayName": "iShares MSCI ACWI"},
            {"symbol": "US5949181045", "weight": 0.30, "assetClass": "equity", "displayName": "Microsoft"},
        ]
        target = [
            {"symbol": "IE00B6R52259", "weight": 0.50, "assetClass": "equity", "displayName": "iShares MSCI ACWI"},
            {"symbol": "IE00BZ043R46", "weight": 0.30, "assetClass": "bonds", "displayName": "iShares Core Global Aggregate Bond"},
            {"symbol": "IE00B4ND3602", "weight": 0.20, "assetClass": "gold", "displayName": "iShares Physical Gold"},
        ]
        actions = app.generate_portfolio_actions(
            currentPortfolio=source,
            targetPortfolio=target,
            portfolioMetrics={},
            targetMetrics={},
            investorProfile={"riskPreference": "balanced"},
            goalPriority="capital_growth",
            maxTemporaryLoss=0.25,
            correctionIntensity="balanced",
        )
        completeness = app.build_improvement_action_completeness(
            source_items=source,
            target_items=target,
            actions=actions,
        )
        self.assertTrue(completeness["targetComplete"])
        self.assertTrue(completeness["sourceComplete"])
        self.assertEqual(completeness["targetTotalFromActions"], 100.0)
        self.assertEqual(completeness["missingSourceInActions"], [])
        self.assertEqual(completeness["missingTargetInActions"], [])
        self.assertEqual(completeness["unexpectedSourceResiduals"], [])
        self.assertEqual(completeness["sharedRetained"][0]["name"], "iShares MSCI ACWI")

    def test_simulated_etf_addition_uses_standard_library_when_pillar_is_weak(self):
        addition = app.build_simulated_etf_addition(
            health_score={
                "overall": 54,
                "pillars": [
                    {"key": "realRisk", "name": "Rischio reale", "score": 42},
                    {"key": "riskReturnEfficiency", "name": "Efficienza rischio-rendimento", "score": 63},
                    {"key": "diversification", "name": "Diversificazione", "score": 65},
                    {"key": "goalCoherence", "name": "Coerenza con obiettivo", "score": 61},
                ],
            },
            current_items=[
                {"symbol": "IE00B5BMR087", "weight": 0.80, "assetClass": "equity", "displayName": "iShares Core S&P 500"},
                {"symbol": "IE00B4L5Y983", "weight": 0.20, "assetClass": "equity", "displayName": "iShares Core MSCI World"},
            ],
            metrics={"volatility": 0.22, "maxDrawdown": -0.34, "sharpe": 0.35},
            investor_profile={"riskPreference": "balanced", "objective": "capital_growth", "horizonYears": 10, "maxTemporaryLoss": 0.20, "experience": "base"},
            user_plan="PLUS",
            target_items=[],
            comparison_mode="optimization",
            source_name="Portafoglio inserito",
            reference_name="Portafoglio ottimizzato",
        )
        self.assertTrue(addition["available"])
        self.assertEqual(addition["targetPillar"], "realRisk")
        self.assertLessEqual(len(addition["suggestions"]), 1)
        self.assertEqual(addition["sourcePortfolioName"], "Portafoglio inserito")
        self.assertIn(addition["suggestions"][0]["isin"], {holding["isin"] for portfolio in app.STANDARD_PORTFOLIOS for holding in portfolio["holdings"]})
        self.assertNotIn("compra", json.dumps(addition).lower())
        self.assertNotIn("vendi", json.dumps(addition).lower())

    def test_simulated_etf_addition_not_forces_when_pillars_are_above_threshold(self):
        addition = app.build_simulated_etf_addition(
            health_score={
                "overall": 76,
                "pillars": [
                    {"key": "realRisk", "name": "Rischio reale", "score": 70},
                    {"key": "riskReturnEfficiency", "name": "Efficienza rischio-rendimento", "score": 74},
                    {"key": "diversification", "name": "Diversificazione", "score": 68},
                    {"key": "goalCoherence", "name": "Coerenza con obiettivo", "score": 72},
                ],
            },
            current_items=[
                {"symbol": "IE00B6R52259", "weight": 0.60, "assetClass": "equity", "displayName": "iShares MSCI ACWI"},
                {"symbol": "IE00BZ043R46", "weight": 0.40, "assetClass": "bonds", "displayName": "iShares Core Global Aggregate Bond"},
            ],
            metrics={"volatility": 0.12, "maxDrawdown": -0.18, "sharpe": 0.85},
            investor_profile={"riskPreference": "balanced", "objective": "capital_growth", "horizonYears": 10, "maxTemporaryLoss": 0.20, "experience": "base"},
            user_plan="PLUS",
            target_items=[],
            comparison_mode="optimization",
            source_name="Portafoglio inserito",
            reference_name="Portafoglio ottimizzato",
        )
        self.assertFalse(addition["available"])
        self.assertIn("Nessun pilastro", addition["reason"])

    def test_simulated_etf_addition_is_disabled_for_standard_only(self):
        addition = app.build_simulated_etf_addition(
            health_score={
                "overall": 48,
                "pillars": [{"key": "realRisk", "name": "Rischio reale", "score": 40}],
            },
            current_items=[{"symbol": "IE00B6R52259", "weight": 1.0, "assetClass": "equity", "displayName": "iShares MSCI ACWI"}],
            metrics={"volatility": 0.22},
            investor_profile={"riskPreference": "balanced", "objective": "capital_growth", "horizonYears": 10, "maxTemporaryLoss": 0.20},
            user_plan="PLUS",
            target_items=[],
            comparison_mode="standard_only",
            source_name="Portfolio standard QuantInvest",
            reference_name="Portfolio standard QuantInvest",
        )
        self.assertFalse(addition["available"])
        self.assertIn("non viene suggerito un ETF singolo", addition["reason"])

    def test_smart_benchmark_uses_global_equity_for_equity_heavy_portfolio(self):
        portfolio = [
            {"symbol": "IE00B4L5Y983", "weight": 0.70, "assetClass": "equity", "displayName": "iShares Core MSCI World"},
            {"symbol": "IE00B3F81R35", "weight": 0.30, "assetClass": "bonds", "displayName": "iShares Core EUR Corporate Bond"},
        ]
        result = app.get_smart_benchmark_for_portfolio(portfolio)
        self.assertEqual(result["benchmarkType"], "single")
        self.assertEqual(result["benchmark"]["id"], "global_equity")
        self.assertIn("prevalentemente azionario", result["reason"])

    def test_smart_benchmark_builds_weighted_multi_asset_benchmark(self):
        portfolio = [
            {"symbol": "IE00B4L5Y983", "weight": 0.40, "assetClass": "equity", "displayName": "iShares Core MSCI World"},
            {"symbol": "IE00B3F81R35", "weight": 0.40, "assetClass": "bonds", "displayName": "iShares Core EUR Corporate Bond"},
            {"symbol": "IE00B4ND3602", "weight": 0.20, "assetClass": "gold", "displayName": "iShares Physical Gold"},
        ]
        result = app.get_smart_benchmark_for_portfolio(portfolio)
        holdings = result["benchmark"]["holdings"]
        self.assertEqual(result["benchmarkType"], "synthetic")
        self.assertEqual(result["benchmark"]["id"], "synthetic_benchmark")
        self.assertAlmostEqual(sum(item["weight"] for item in holdings), 100.0)
        self.assertEqual([item["weight"] for item in holdings], [40.0, 40.0, 20.0])
        self.assertEqual(holdings[1]["isin"], "IE00B3F81R35")

    def test_smart_benchmark_uses_corporate_bond_for_bond_heavy_portfolio(self):
        portfolio = [
            {"symbol": "IE00B3F81R35", "weight": 0.70, "assetClass": "bonds", "displayName": "iShares Core EUR Corporate Bond"},
            {"symbol": "IE00BCRY6557", "weight": 0.30, "assetClass": "bonds", "displayName": "iShares EUR Ultrashort Bond"},
        ]
        result = app.get_smart_benchmark_for_portfolio(portfolio)
        self.assertEqual(result["benchmarkType"], "single")
        self.assertEqual(result["benchmark"]["id"], "eur_corporate_bond")

    def test_smart_benchmark_uses_specific_regions_inside_synthetic_benchmark(self):
        portfolio = [
            {"symbol": "IE00B5BMR087", "weight": 0.50, "assetClass": "equity", "displayName": "iShares Core S&P 500"},
            {"symbol": "IE00B4WXJJ64", "weight": 0.30, "assetClass": "bonds", "displayName": "iShares Core Euro Government Bond"},
            {"symbol": "IE00B4ND3602", "weight": 0.10, "assetClass": "gold", "displayName": "iShares Physical Gold"},
            {"symbol": "IE00BD6FTQ80", "weight": 0.10, "assetClass": "commodities", "displayName": "Invesco Bloomberg Commodity"},
        ]
        result = app.get_smart_benchmark_for_portfolio(portfolio)
        holdings = result["benchmark"]["holdings"]
        self.assertEqual(result["benchmarkType"], "synthetic")
        self.assertEqual(holdings[0]["isin"], "IE00B5BMR087")
        self.assertEqual(holdings[1]["isin"], "IE00B4WXJJ64")
        self.assertEqual([item["weight"] for item in holdings], [50.0, 30.0, 10.0, 10.0])

    def test_smart_benchmark_uses_gold_for_gold_heavy_portfolio(self):
        portfolio = [
            {"symbol": "IE00B4ND3602", "weight": 0.80, "assetClass": "gold", "displayName": "iShares Physical Gold"},
            {"symbol": "IE00BCRY6557", "weight": 0.20, "assetClass": "bonds", "displayName": "iShares EUR Ultrashort Bond"},
        ]
        result = app.get_smart_benchmark_for_portfolio(portfolio)
        self.assertEqual(result["benchmarkType"], "single")
        self.assertEqual(result["benchmark"]["id"], "gold")

    def test_smart_benchmark_redistributes_unknown_weight_with_warning(self):
        portfolio = [
            {"symbol": "IE00B4L5Y983", "weight": 0.50, "assetClass": "equity", "displayName": "iShares Core MSCI World"},
            {"symbol": "IE00BZ043R46", "weight": 0.30, "assetClass": "bonds", "displayName": "iShares Core Global Aggregate Bond"},
            {"symbol": "UNKNOWN000000", "weight": 0.20, "assetClass": "unknown", "displayName": "Strumento non classificato"},
        ]
        result = app.get_smart_benchmark_for_portfolio(portfolio)
        holdings = result["benchmark"]["holdings"]
        self.assertEqual(result["benchmarkType"], "synthetic")
        self.assertAlmostEqual(sum(item["weight"] for item in holdings), 100.0)
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["warnings"])

    def test_local_technical_detail_explains_backtest_indicators(self):
        detail = app.rule_based_explanation_detail(
            {
                "analysisType": "backtesting",
                "metrics": {"cagr": 0.08, "maxDrawdown": -0.22, "sharpe": 0.9, "volatility": 0.14, "initialCapital": 10000},
                "portfolioValue": 10000,
            }
        )
        note = detail["technical_note"]
        self.assertIn("Crescita media annua", note)
        self.assertIn("Rapporto rischio-rendimento", note)
        self.assertIn("Peggiore perdita temporanea", note)
        self.assertIn("backend", note)

    def test_local_technical_detail_explains_fama_french_factors(self):
        detail = app.rule_based_explanation_detail(
            {
                "analysisType": "fama_french_factor_analysis",
                "metrics": {"alpha": 0.001, "rSquared": 0.72, "observations": 36, "maxDrawdown": 0},
            }
        )
        note = detail["technical_note"]
        self.assertIn("HML", note)
        self.assertIn("CMA", note)
        self.assertIn("MOM", note)
        self.assertIn("R²", note)

    def test_analysis_public_display_name_hides_raw_isin_when_name_is_missing(self):
        self.assertEqual(app.public_display_name_for_symbol("US0000000000"), "Strumento finanziario")
        enriched = app.enrich_portfolio_display_names(
            [{"symbol": "US0000000000", "displayName": "US0000000000", "weight": 1.0, "assetClass": "equity"}]
        )
        self.assertEqual(enriched[0]["displayName"], "Strumento finanziario")

    def test_analysis_public_display_name_prefers_known_standard_names(self):
        known = app.public_display_name_for_symbol("IE00B6R52259")
        self.assertEqual(known, "iShares MSCI ACWI")

    def test_saved_portfolio_preserves_user_isin_entry(self):
        portfolio = app.normalize_portfolio(
            [{"symbol": "US0378331005", "displayName": "US0378331005", "weight": 100, "assetClass": "equity"}]
        )
        self.assertEqual(portfolio[0]["displayName"], "US0378331005")

    def test_locked_payload_contract(self):
        locked = app.locked_feature_payload("efficientFrontier")
        self.assertEqual(locked["error"], "FEATURE_LOCKED")
        self.assertEqual(locked["requiredPlan"], "PLUS")
        self.assertIn("previewMessage", locked)

    def test_portfolio_rejects_short_tickers(self):
        with self.assertRaises(app.BacktestError) as context:
            app.normalize_portfolio([{"symbol": "AAPL", "weight": 100, "assetClass": "equity"}])
        self.assertIn("ISIN", str(context.exception))

    def test_free_backtest_returns_locked_plus_sections(self):
        result = app.run_backtest(
            {
                "userPlan": "FREE",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "demo": True,
            }
        )
        self.assertEqual(result["userPlan"], "FREE")
        self.assertEqual(result["efficientFrontier"]["error"], "FEATURE_LOCKED")
        self.assertEqual(result["monteCarlo"]["error"], "FEATURE_LOCKED")
        self.assertEqual(result["stressTesting"]["error"], "FEATURE_LOCKED")
        self.assertIn("metrics", result)

    def test_plus_backtest_keeps_existing_calculations(self):
        result = app.run_backtest(
            {
                "userPlan": "PLUS",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": False, "scenario": "Global Recession"},
                "demo": True,
            }
        )
        self.assertTrue(result["efficientFrontier"]["available"])
        self.assertIn("projection", result["monteCarlo"])
        self.assertNotEqual(result["monteCarlo"].get("error"), "FEATURE_LOCKED")
        self.assertTrue(result["optimizedPortfolio"]["available"])
        self.assertIn("metrics", result["optimizedPortfolio"])
        self.assertIn("monteCarlo", result["optimizedPortfolio"])
        self.assertIn("optimizedComparison", result["aiAdvisor"]["intelligence"]["backtest"])
        self.assertIn("optimizedComparison", result["aiAdvisor"]["intelligence"]["montecarlo"])
        for section in ("frontier", "backtest", "montecarlo", "scenarios"):
            intelligence = result["aiAdvisor"]["intelligence"][section]
            self.assertNotIn("structuredExplanation", intelligence)
            self.assertIn("optimizedStructuredExplanation", intelligence)

    def test_plus_backtest_runs_scenario_when_stress_enabled(self):
        result = app.run_backtest(
            {
                "userPlan": "PLUS",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "demo": True,
            }
        )
        self.assertTrue(result["stressTesting"]["enabled"])
        self.assertNotEqual(result["stressTesting"].get("error"), "FEATURE_LOCKED")
        self.assertIn("result", result["stressTesting"])
        self.assertIn("expected_portfolio_return", result["stressTesting"]["result"])
        health = result["aiAdvisor"]["intelligence"]["healthScore"]
        self.assertIn("crisi simulate", health["metricsUsed"])
        scenario_pillar = next(pillar for pillar in health["pillars"] if pillar["key"] == "scenarioRobustness")
        self.assertIn("Crisi simulate", scenario_pillar["primaryMetric"])

    def test_efficient_frontier_respects_min_max_constraints(self):
        result = app.run_backtest(
            {
                "userPlan": "PLUS",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity", "minWeight": 10, "maxWeight": 60},
                    {"symbol": "US5949181045", "weight": 30, "assetClass": "equity", "minWeight": 20, "maxWeight": 50},
                    {"symbol": "US78462F1030", "weight": 20, "assetClass": "equity", "minWeight": 10, "maxWeight": 40},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "start": "2023-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 1, "simulations": 500},
                "stressTesting": {"enabled": False, "scenario": "Global Recession"},
                "demo": True,
            }
        )
        constraints = result["efficientFrontier"]["constraints"]
        best_weights = result["efficientFrontier"]["best"]["weights"]
        for weight, constraint in zip(best_weights, constraints):
            self.assertGreaterEqual(weight + 1e-9, constraint["minWeight"])
            self.assertLessEqual(weight - 1e-9, constraint["maxWeight"])

    def test_advanced_plan_returns_advanced_analytics_for_current_and_optimized(self):
        result = app.run_backtest(
            {
                "userPlan": "ADVANCED",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 40, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 35, "assetClass": "equity"},
                    {"symbol": "US78462F1030", "weight": 25, "assetClass": "equity"},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "start": "2021-01-01",
                "end": "2023-12-31",
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "monteCarlo": {"horizonYears": 3, "simulations": 500},
                "stressTesting": {"enabled": True, "scenario": "Global Recession"},
                "demo": True,
            }
        )
        advanced = result["advancedAnalytics"]
        self.assertFalse(advanced["locked"])
        for section in (
            "famaFrench",
            "geography",
            "rollingSortino",
            "correlation",
            "pac",
            "drawdown",
            "factorRisk",
            "scenarioComparison",
        ):
            self.assertTrue(advanced[section]["current"]["available"])
            self.assertTrue(advanced[section]["optimized"]["available"])
            self.assertIn("structuredExplanation", advanced[section]["current"])
            self.assertIn("structuredExplanation", advanced[section]["optimized"])
            self.assertIn("explanationRequest", advanced[section]["current"])
            self.assertIn("explanationRequest", advanced[section]["optimized"])
            self.assertEqual(advanced[section]["current"]["commentSource"], "openai_on_demand")
            self.assertEqual(advanced[section]["optimized"]["commentSource"], "openai_on_demand")
        self.assertEqual(advanced["famaFrench"]["current"]["method"], "Kenneth French Data Library ufficiale locale")
        self.assertIn("datasetFile", advanced["famaFrench"]["current"])
        self.assertTrue(advanced["famaFrench"]["current"]["includesMomentum"])
        self.assertIn("MOM", advanced["famaFrench"]["current"]["factorNames"])
        correlation = advanced["correlation"]["current"]
        self.assertIn("displaySymbols", correlation)
        self.assertEqual(correlation["displaySymbols"]["US0378331005"], "Apple Inc")
        self.assertIn("displayPair", correlation["highestPair"])
        self.assertNotIn("US0378331005", correlation["highestPair"]["displayPair"])
        rolling_windows = advanced["rollingSortino"]["windows"]
        self.assertEqual(set(rolling_windows.keys()), {"1y", "2y", "6m"})
        self.assertEqual(rolling_windows["1y"]["label"], "1 anno")
        self.assertEqual(rolling_windows["2y"]["days"], 504)
        self.assertEqual(rolling_windows["6m"]["days"], 126)
        self.assertTrue(rolling_windows["1y"]["current"]["available"])
        self.assertTrue(rolling_windows["6m"]["optimized"]["available"])
        self.assertIn("explanationRequest", rolling_windows["2y"]["optimized"])
        self.assertTrue(result["benchmarkComparison"]["available"])

    def test_rebalancing_notifications_are_structured(self):
        notifications = app.build_rebalancing_notifications(
            True,
            [{"symbol": "US0378331005", "currentWeight": 0.55, "targetWeight": 0.5}],
        )
        self.assertGreaterEqual(len(notifications), 2)
        self.assertIn("title", notifications[0])
        self.assertIn("message", notifications[0])

    def test_emerging_fama_french_dataset_uses_emerging_momentum(self):
        dataset = app.parse_fama_french_file("emerging_5")
        self.assertTrue(dataset["available"])
        self.assertIn("MOM", dataset["factorNames"])
        self.assertEqual(dataset["momentumFile"], "Emerging_MOM_Factor.csv")
        self.assertEqual(app.choose_fama_french_dataset(["EEM"]), "emerging_5")

    def test_rolling_metrics_use_adaptive_window_for_short_periods(self):
        returns = [0.001, -0.002, 0.003, 0.001, -0.001] * 12
        result = app.rolling_window_metrics(returns)
        self.assertTrue(result["available"])
        self.assertEqual(result["window"], len(returns))
        self.assertEqual(result["requestedWindow"], 252)
        self.assertIsNotNone(result["latest"]["rollingSortino"])

    def test_investor_profile_keeps_personalization_fields(self):
        profile = app.parse_investor_profile(
            {
                "capital": 25000,
                "age": 35,
                "horizonYears": 12,
                "objective": "Pensione integrativa",
                "riskPreference": "balanced",
                "maxTemporaryLoss": 0.25,
                "monthlyPac": 300,
                "goalPriority": "retirement",
                "experienceLevel": "intermediate",
                "liquidityNeed": "medium",
            },
            10000,
        )
        self.assertEqual(profile["capital"], 25000)
        self.assertEqual(profile["maxTemporaryLoss"], 0.25)
        self.assertEqual(profile["monthlyPac"], 300)
        self.assertEqual(profile["goalPriority"], "retirement")
        self.assertEqual(profile["experienceLevel"], "intermediate")
        self.assertEqual(profile["liquidityNeed"], "medium")

    def test_ai_explanation_user_fields_use_plain_metric_names(self):
        result = app.validate_ai_explanation(
            {
                "summary": "Sharpe Ratio e CAGR sono migliorati.",
                "meaning": "Il drawdown e la volatilità restano da monitorare.",
                "main_weakness": "Sortino basso.",
                "what_to_watch": "Guarda Sharpe e correlazione.",
                "main_strength": "Sharpe alto.",
                "possible_improvement": "Ridurre la correlazione apparente.",
                "concrete_example": "Max Drawdown del -20%.",
                "technical_detail": "Sharpe Ratio tecnico: 0.80.",
                "disclaimer": app.AI_EXPLANATION_DISCLAIMER,
            }
        )
        self.assertIn("rapporto rischio-rendimento", result["summary"])
        self.assertIn("crescita media annua", result["summary"])
        self.assertIn("perdita temporanea", result["meaning"])
        self.assertIn("oscillazione del portafoglio", result["meaning"])
        self.assertEqual(result["meaning_for_user"], result["meaning"])
        self.assertIn("Sharpe Ratio tecnico", result["technical_detail"])
        self.assertIn("L'utente deve guardare", result["what_to_watch"])
        self.assertNotIn("osservare Guarda", result["what_to_watch"])

    def test_peak_to_trough_uses_peak_of_worst_drawdown(self):
        curve = [
            {"date": "2024-01-01", "value": 100},
            {"date": "2024-01-02", "value": 120},
            {"date": "2024-01-05", "value": 90},
            {"date": "2024-01-08", "value": 130},
        ]
        result = app.build_drawdown_analysis(curve)
        self.assertEqual(result["peakDate"], "2024-01-02")
        self.assertEqual(result["troughDate"], "2024-01-05")
        self.assertEqual(result["peakToTroughDays"], 3)

    def test_openai_prompt_receives_user_plan(self):
        captured = {}
        original = app.call_openai_advisor

        def fake_call(prompt):
            captured.update(json.loads(prompt))
            return "ok", ""

        app.call_openai_advisor = fake_call
        try:
            app.build_ai_advisor(
                {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced", "objective": "test"},
                {"initialCapital": 10000, "cagr": 0.05, "maxDrawdown": -0.1, "sharpe": 0.8},
                app.locked_feature_payload("efficientFrontier"),
                app.locked_feature_payload("monteCarlo"),
                app.locked_feature_payload("stressTesting"),
                [{"symbol": "US0378331005", "weight": 1.0, "weightedReturn": 0.05}],
                {"available": False, "reason": "test"},
                "FREE",
            )
        finally:
            app.call_openai_advisor = original
        self.assertEqual(captured["user_plan"], "FREE")
        self.assertEqual(captured["explanation_level"], "basic")

    def test_free_plan_uses_openai_detail_when_requested(self):
        original = app.call_openai_explanation_detail
        app.OPENAI_EXPLANATIONS_ENABLED = True
        expected = {field: f"openai {field}" for field in app.AI_EXPLANATION_DETAIL_FIELDS}
        expected["disclaimer"] = app.AI_EXPLANATION_DISCLAIMER
        captured = {}

        def fake_call(payload):
            captured.update(payload)
            return expected, ""

        app.call_openai_explanation_detail = fake_call
        try:
            result = app.build_ai_explanation_detail(
                {
                    "analysisType": "backtesting",
                    "userPlan": "FREE",
                    "portfolioValue": 10000,
                    "metrics": {"cagr": 0.05, "maxDrawdown": -0.1},
                }
            )
        finally:
            app.call_openai_explanation_detail = original
        self.assertEqual(result["detail"], expected)
        self.assertEqual(result["aiStatus"], "openai")
        self.assertEqual(captured["userPlan"], "FREE")

    def test_openai_detail_truncated_json_returns_clean_error(self):
        payload = {"output": [{"content": [{"type": "output_text", "text": "{\"title\":\"test"}]}]}
        with self.assertRaises(ValueError) as context:
            app.parse_openai_json_response(payload, "Dettaglio OpenAI")
        self.assertIn("incompleta o troncata", str(context.exception))

    def test_openai_detail_includes_local_technical_fallback_when_disabled(self):
        previous_key = os.environ.pop("OPENAI_API_KEY", None)
        previous_enabled = app.OPENAI_EXPLANATIONS_ENABLED
        app.OPENAI_EXPLANATIONS_ENABLED = True
        try:
            result = app.build_ai_explanation_detail(
                {
                    "analysisType": "backtesting",
                    "userPlan": "FREE",
                    "portfolioValue": 10000,
                    "metrics": {"cagr": 0.05, "maxDrawdown": -0.1},
                }
            )
        finally:
            if previous_key is not None:
                os.environ["OPENAI_API_KEY"] = previous_key
            app.OPENAI_EXPLANATIONS_ENABLED = previous_enabled
        self.assertEqual(result["aiStatus"], "disabled")
        self.assertIn("technical_note", result["technicalFallback"])
        self.assertIn("Dettaglio tecnico", result["technicalFallback"]["technical_note"])

    def test_automatic_explanation_is_rule_based_by_default(self):
        original = app.call_openai_explanation
        app.OPENAI_EXPLANATIONS_ENABLED = True

        def fake_call(_payload):
            raise AssertionError("automatic report should not call OpenAI")

        app.call_openai_explanation = fake_call
        try:
            result = app.build_ai_explanation(
                {
                    "analysisType": "backtesting",
                    "userPlan": "FREE",
                    "portfolioValue": 10000,
                    "metrics": {"cagr": 0.05, "maxDrawdown": -0.1},
                }
            )
        finally:
            app.call_openai_explanation = original
        self.assertIn("summary", result)

    def test_openai_explanation_uses_responses_json_schema_body(self):
        captured = {}
        original_urlopen = app.urllib.request.urlopen
        original_model = app.OPENAI_EXPLANATION_MODEL
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        app.OPENAI_EXPLANATION_MODEL = "gpt-5.5"
        response_payload = {
            field: f"test {field}"
            for field in app.AI_EXPLANATION_FIELDS
        }
        response_payload["disclaimer"] = app.AI_EXPLANATION_DISCLAIMER

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "output": [
                            {
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": json.dumps(response_payload),
                                    }
                                ]
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        app.urllib.request.urlopen = fake_urlopen
        try:
            result, error = app.call_openai_explanation(
                {
                    "analysisType": "backtesting",
                    "userPlan": "FREE",
                    "metrics": {"cagr": 0.05},
                }
            )
        finally:
            app.urllib.request.urlopen = original_urlopen
            app.OPENAI_EXPLANATION_MODEL = original_model
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key
        self.assertEqual(error, "")
        self.assertEqual(result["disclaimer"], app.AI_EXPLANATION_DISCLAIMER)
        self.assertEqual(captured["url"], app.OPENAI_API_URL)
        self.assertEqual(captured["body"]["model"], "gpt-5.5")
        self.assertFalse(captured["body"]["store"])
        self.assertEqual(captured["body"]["text"]["format"]["type"], "json_schema")
        self.assertTrue(captured["body"]["text"]["format"]["strict"])
        self.assertEqual(captured["body"]["text"]["format"]["schema"], app.AI_EXPLANATION_SCHEMA)

    def test_openai_request_retries_rate_limit_then_succeeds(self):
        original_urlopen = app.urllib.request.urlopen
        original_retries = app.OPENAI_MAX_RETRIES
        original_sleep = app.time.sleep
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        app.OPENAI_MAX_RETRIES = 1
        calls = {"count": 0}

        class ErrorBody:
            def read(self):
                return b'{"error":"rate limit"}'

            def close(self):
                return None

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"output":[]}'

        def fake_urlopen(_request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise app.urllib.error.HTTPError("url", 429, "Too Many Requests", {}, ErrorBody())
            return FakeResponse()

        app.urllib.request.urlopen = fake_urlopen
        app.time.sleep = lambda _seconds: None
        try:
            payload, error, category = app.openai_responses_request({"model": "gpt-5.4-mini", "input": "x"})
        finally:
            app.urllib.request.urlopen = original_urlopen
            app.OPENAI_MAX_RETRIES = original_retries
            app.time.sleep = original_sleep
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key
        self.assertEqual(calls["count"], 2)
        self.assertEqual(error, "")
        self.assertEqual(category, "")
        self.assertEqual(payload, {"output": []})

    def test_ai_explanation_cache_reuses_validated_response(self):
        original_cache_file = app.AI_EXPLANATION_CACHE_FILE
        original_ttl = app.AI_EXPLANATION_CACHE_TTL_SECONDS
        original_model = app.OPENAI_EXPLANATION_MODEL
        with tempfile.TemporaryDirectory() as tmp:
            app.AI_EXPLANATION_CACHE_FILE = Path(tmp) / "ai_cache.json"
            app.AI_EXPLANATION_CACHE_TTL_SECONDS = 3600
            app.OPENAI_EXPLANATION_MODEL = "gpt-5.4-mini"
            payload = {"analysisType": "backtesting", "userPlan": "FREE", "metrics": {"cagr": 0.05}}
            explanation = {field: f"value {field}" for field in app.AI_EXPLANATION_FIELDS}
            explanation["disclaimer"] = app.AI_EXPLANATION_DISCLAIMER
            try:
                app.set_cached_ai_explanation(payload, explanation)
                cached = app.get_cached_ai_explanation(payload)
                expected = app.validate_ai_explanation(explanation)
            finally:
                app.AI_EXPLANATION_CACHE_FILE = original_cache_file
                app.AI_EXPLANATION_CACHE_TTL_SECONDS = original_ttl
                app.OPENAI_EXPLANATION_MODEL = original_model
        self.assertIsNotNone(cached)
        for field in app.AI_EXPLANATION_FIELDS:
            self.assertEqual(cached[field], expected[field])

    def test_openai_advisor_returns_structured_summary(self):
        original_urlopen = app.urllib.request.urlopen
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        response_payload = {
            "summary": "Sintesi del portafoglio.",
            "score_comment": "Score coerente con il profilo.",
            "main_risk": "Il rischio principale è il drawdown.",
            "main_strength": "Il punto di forza è la diversificazione.",
            "optimization_note": "Il portafoglio efficiente riduce il rischio.",
            "disclaimer": app.AI_EXPLANATION_DISCLAIMER,
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {"output": [{"content": [{"type": "output_text", "text": json.dumps(response_payload)}]}]}
                ).encode("utf-8")

        app.urllib.request.urlopen = lambda _request, timeout: FakeResponse()
        try:
            text, error, structured = app.call_openai_advisor("{}")
        finally:
            app.urllib.request.urlopen = original_urlopen
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key
        self.assertEqual(error, "")
        self.assertIsNotNone(structured)
        self.assertIn("Sintesi del portafoglio", text)
        self.assertEqual(structured["disclaimer"], app.AI_EXPLANATION_DISCLAIMER)

    def test_ai_explanation_schema_and_disclaimer(self):
        explanation = app.rule_based_explanation(
            {
                "analysisType": "efficient_frontier",
                "portfolioValue": 50000,
                "riskProfile": "medium",
                "investmentHorizonYears": 20,
                "metrics": {
                    "currentExpectedReturn": 0.072,
                    "currentVolatility": 0.156,
                    "optimizedExpectedReturn": 0.071,
                    "optimizedVolatility": 0.118,
                    "currentSharpe": 0.62,
                    "optimizedSharpe": 0.81,
                    "maxDrawdown": -0.35,
                },
            }
        )
        self.assertTrue(set(app.AI_EXPLANATION_FIELDS).issubset(set(explanation.keys())))
        self.assertIn("meaning_for_user", explanation)
        self.assertNotIn("next_step_question", explanation)
        self.assertEqual(explanation["disclaimer"], app.AI_EXPLANATION_DISCLAIMER)

    def test_ai_explanation_schema_uses_diagnostic_fields(self):
        self.assertEqual(app.AI_EXPLANATION_SCHEMA["required"], app.AI_EXPLANATION_FIELDS)
        self.assertIn("meaning", app.AI_EXPLANATION_SCHEMA["required"])
        self.assertIn("what_to_watch", app.AI_EXPLANATION_SCHEMA["required"])
        self.assertIn("main_weakness", app.AI_EXPLANATION_SCHEMA["required"])
        self.assertNotIn("meaning_for_user", app.AI_EXPLANATION_SCHEMA["required"])

    def test_ai_explanation_avoids_forbidden_words(self):
        explanation = app.validate_ai_explanation(
            {
                field: "compra vendi garantito"
                for field in app.AI_EXPLANATION_FIELDS
            }
        )
        combined = " ".join(explanation.values()).lower()
        for word in app.AI_EXPLANATION_FORBIDDEN_WORDS:
            self.assertNotIn(word, combined)

    def test_ai_explanation_replaces_generic_phrases(self):
        explanation = app.validate_ai_explanation(
            {
                field: "Questo confronto aiuta un investitore a capire se l'ottimizzazione rende il portafoglio più coerente."
                for field in app.AI_EXPLANATION_FIELDS
            }
        )
        combined = " ".join(explanation[field].lower() for field in app.AI_EXPLANATION_FIELDS)
        self.assertNotIn("questo confronto aiuta", combined)
        self.assertNotIn("ottimizzazione rende il portafoglio", combined)

    def test_ai_explanation_converts_drawdown_to_euro(self):
        explanation = app.rule_based_explanation(
            {
                "analysisType": "max_drawdown",
                "portfolioValue": 50000,
                "riskProfile": "medium",
                "investmentHorizonYears": 20,
                "metrics": {"maxDrawdown": -0.35},
            }
        )
        self.assertIn("32,500 euro", explanation["concrete_example"])

    def test_ai_explanation_strength_is_about_portfolio(self):
        explanation = app.rule_based_explanation(
            {
                "analysisType": "backtesting",
                "portfolioValue": 50000,
                "riskProfile": "medium",
                "investmentHorizonYears": 20,
                "metrics": {"cagr": 0.06, "sharpe": 0.9, "maxDrawdown": -0.2},
            }
        )
        strength = explanation["main_strength"].lower()
        self.assertIn("portafoglio", strength)
        self.assertNotIn("piattaforma", strength)
        self.assertNotIn("software", strength)
        self.assertNotIn("sistema", strength)
        self.assertNotIn("lettura quantitativa", strength)

    def test_ai_explanation_does_not_repeat_risk_as_example(self):
        explanation = app.rule_based_explanation(
            {
                "analysisType": "backtesting",
                "portfolioValue": 10000,
                "riskProfile": "medium",
                "investmentHorizonYears": 10,
                "metrics": {"cagr": 0.05, "maxDrawdown": -0.176, "sharpe": 0.7},
            }
        )
        self.assertNotEqual(explanation["main_weakness"], explanation["concrete_example"])
        self.assertIn("perdita temporanea", explanation["main_weakness"].lower())
        self.assertEqual(explanation["main_risk"], explanation["main_weakness"])
        self.assertIn("10,000 euro", explanation["concrete_example"])

    def test_ai_explanation_deduplicates_identical_fields(self):
        explanation = app.validate_ai_explanation(
            {field: "Stesso commento ripetuto." for field in app.AI_EXPLANATION_FIELDS}
        )
        unique_non_disclaimer = {
            explanation[field]
            for field in app.AI_EXPLANATION_FIELDS
            if field != "disclaimer"
        }
        self.assertGreater(len(unique_non_disclaimer), 1)

    def test_ai_explanation_sanitizes_common_accents(self):
        text = app.sanitize_ai_explanation_text("perche puo piu gia cioe probabilita volatilita finalita unita")
        self.assertIn("perché", text)
        self.assertIn("può", text)
        self.assertIn("più", text)
        self.assertIn("già", text)
        self.assertIn("cioè", text)
        self.assertIn("probabilità", text)
        self.assertIn("volatilità", text)

    def test_ai_explanation_explains_growth_and_temporary_loss_terms(self):
        explanation = app.rule_based_explanation(
            {
                "analysisType": "backtesting",
                "portfolioValue": 50000,
                "riskProfile": "medium",
                "investmentHorizonYears": 20,
                "metrics": {"cagr": 0.06, "maxDrawdown": -0.2, "sharpe": 0.9},
            }
        )
        detail = explanation["technical_detail"].lower()
        self.assertIn("crescita media annua significa", detail)
        self.assertIn("rendimento medio annuo composto", detail)
        self.assertIn("perdita temporanea significa", detail)
        self.assertIn("perdita temporanea", detail)

    def test_ai_explanation_differs_by_analysis_type(self):
        base = {
            "portfolioValue": 50000,
            "riskProfile": "medium",
            "investmentHorizonYears": 20,
            "metrics": {"maxDrawdown": -0.2, "probabilityGain": 0.73},
        }
        monte = app.rule_based_explanation({**base, "analysisType": "monte_carlo"})
        stress = app.rule_based_explanation({**base, "analysisType": "stress_testing", "metrics": {"expectedPortfolioReturn": -0.12, "maxDrawdown": -0.2}})
        self.assertNotEqual(monte["summary"], stress["summary"])

    def test_portfolio_fit_score_plus_uses_downside_not_only_probability(self):
        health = app.build_portfolio_health_score(
            {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced", "objective": "crescita"},
            {"initialCapital": 10000, "maxDrawdown": -0.2, "sharpe": 0.8, "cagr": 0.06},
            {"best": {"sharpe": 0.9}},
            {"probabilityGain": 1.0, "p5": 8200},
            {"result": {"expected_portfolio_return": -0.25}},
            [{"symbol": "US0378331005", "weight": 0.5}, {"symbol": "US5949181045", "weight": 0.5}],
            "PLUS",
        )
        self.assertEqual(health["scoreType"], "Portfolio Fit Score")
        self.assertEqual(health["plan"], "PLUS")
        self.assertNotIn("Fama-French Factor Analysis", health["metricsUsed"])
        goal = next(pillar for pillar in health["pillars"] if pillar["key"] == "goalCoherence")
        robust = next(pillar for pillar in health["pillars"] if pillar["key"] == "scenarioRobustness")
        self.assertLess(goal["score"], 100)
        self.assertLess(robust["score"], 50)

    def test_free_portfolio_fit_score_uses_only_free_metrics_and_caps_at_85(self):
        health = app.build_portfolio_health_score(
            {"capital": 10000, "age": 40, "horizonYears": 15, "riskPreference": "balanced", "objective": "crescita"},
            {"initialCapital": 10000, "maxDrawdown": -0.05, "sharpe": 2.4, "cagr": 0.25, "volatility": 0.08},
            {"best": {"sharpe": 3.0}},
            {"probabilityGain": 1.0, "p5": 12000},
            {"result": {"expected_portfolio_return": 0.02}},
            [{"symbol": "US0378331005", "weight": 0.5}, {"symbol": "US5949181045", "weight": 0.5}],
            "FREE",
        )
        self.assertEqual(health["scoreType"], "Check-up Base del portafoglio")
        self.assertLessEqual(health["overall"], 85)
        self.assertIn("rapporto rischio-rendimento", health["metricsUsed"])
        self.assertIn("Efficient Frontier", health["metricsExcludedByPlan"])
        self.assertIn("Monte Carlo Simulation", health["metricsExcludedByPlan"])
        self.assertFalse(any(pillar["key"] == "scenarioRobustness" for pillar in health["pillars"]))

    def test_plus_portfolio_fit_score_excludes_advanced_metrics(self):
        health = app.build_portfolio_health_score(
            {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced", "objective": "crescita"},
            {"initialCapital": 10000, "maxDrawdown": -0.18, "sharpe": 0.9, "cagr": 0.07, "volatility": 0.14},
            {"current": {"sharpe": 0.9}, "best": {"sharpe": 1.1}},
            {"probabilityGain": 0.72, "p5": 8300},
            {"result": {"expected_portfolio_return": -0.08}},
            [{"symbol": "US0378331005", "weight": 0.55}, {"symbol": "US5949181045", "weight": 0.45}],
            "PLUS",
        )
        self.assertEqual(health["plan"], "PLUS")
        self.assertTrue(any(pillar["key"] == "goalCoherence" for pillar in health["pillars"]))
        self.assertIn("Fama-French Factor Analysis", health["metricsExcludedByPlan"])
        self.assertIn("Matrice di correlazione avanzata", health["metricsExcludedByPlan"])
        self.assertNotIn("Fama-French", " ".join(health["metricsUsed"]))

    def test_goal_alignment_periodic_income_favors_income_standard_over_growth_stocks(self):
        profile = {
            "capital": 10000,
            "horizonYears": 7,
            "riskPreference": "balanced",
            "objective": "periodic_income",
            "goalPriority": "periodic_income",
            "maxTemporaryLoss": 0.20,
        }
        metrics = {"initialCapital": 10000, "maxDrawdown": -0.14, "sharpe": 0.7, "cagr": 0.045, "volatility": 0.12}
        monte_carlo = {"probabilityGain": 0.70, "p5": 9000}
        stress = {"result": {"expected_portfolio_return": -0.08}}
        user_health = app.calculate_portfolio_fit_score(
            plan="ADVANCED",
            userProfile=profile,
            portfolioMetrics=metrics,
            monteCarlo=monte_carlo,
            stressTesting=stress,
            contribution=[
                {"symbol": "US0378331005", "displayName": "Apple", "weight": 0.34},
                {"symbol": "US5949181045", "displayName": "Microsoft", "weight": 0.33},
                {"symbol": "IE00B5BMR087", "displayName": "iShares Core S&P 500", "weight": 0.33},
            ],
            advancedAnalytics={"pac": {"available": True}},
        )
        income_standard = self.find_standard_portfolio("income-europa-dividendi")
        standard_health = app.calculate_portfolio_fit_score(
            plan="ADVANCED",
            userProfile=profile,
            portfolioMetrics=metrics,
            monteCarlo=monte_carlo,
            stressTesting=stress,
            contribution=income_standard["holdings"],
            advancedAnalytics={"pac": {"available": True}},
            portfolioContext=income_standard,
        )
        self.assertLessEqual(self.goal_pillar_score(user_health), 50)
        self.assertGreater(self.goal_pillar_score(standard_health), self.goal_pillar_score(user_health))
        self.assertIn("income", standard_health["goalAlignmentFallback"]["matchedGoalDrivers"][0])

    def test_goal_alignment_classifier_recognizes_growth_stocks_and_sp500(self):
        apple = app.classifyInstrumentForGoalAlignment(
            {"symbol": "AAPL", "name": "Apple Inc", "assetType": "stock", "sector": "Technology", "country": "US"}
        )
        sp500 = app.classifyInstrumentForGoalAlignment({"name": "iShares Core S&P 500 UCITS ETF"})
        self.assertIn("equity_us", apple["goalTags"])
        self.assertIn("growth", apple["goalTags"])
        self.assertEqual(apple["incomeProfile"], "low_income")
        self.assertIn("equity_us", sp500["goalTags"])
        self.assertIn("broad_market", sp500["goalTags"])
        self.assertEqual(sp500["incomeProfile"], "moderate_income")

    def test_goal_alignment_classifier_recognizes_income_dividend_and_bonds(self):
        dividend = app.classifyInstrumentForGoalAlignment({"name": "Global Select Dividend Income ETF", "description": "High Dividend Yield strategy"})
        corporate = app.classifyInstrumentForGoalAlignment({"name": "EUR Corporate Bond 1-5yr", "fundCategory": "Fixed Income"})
        self.assertIn("equity_dividend", dividend["goalTags"])
        self.assertIn("income", dividend["goalTags"])
        self.assertIn(corporate["assetClass"], {"bond", "cash_like"})
        self.assertIn("income", corporate["goalTags"])
        self.assertIn("defensive", corporate["goalTags"])

    def test_goal_alignment_classifier_recognizes_thematic_and_cash_like_outside_library(self):
        ai_etf = app.classifyInstrumentForGoalAlignment({"name": "Artificial Intelligence Big Data Innovation ETF"})
        money_market = app.classifyInstrumentForGoalAlignment({"name": "EUR Money Market Overnight Fund"})
        self.assertIn("thematic", ai_etf["goalTags"])
        self.assertIn("high_volatility", ai_etf["goalTags"])
        self.assertEqual(ai_etf["incomeProfile"], "low_income")
        self.assertIn("cash_like", money_market["goalTags"])
        self.assertIn("capital_preservation", money_market["goalTags"])
        self.assertEqual(money_market["riskProfile"], "low")

    def test_goal_alignment_low_confidence_unknown_does_not_force_strong_income_penalty(self):
        fallback = app.calculate_goal_alignment_fallback(
            [{"symbol": "XYZ", "name": "XYZ", "weight": 1.0}],
            {"objective": "periodic_income", "goalPriority": "periodic_income", "horizonYears": 6, "riskPreference": "balanced"},
            {"maxDrawdown": -0.08},
        )
        self.assertGreater(fallback["goalAlignmentAdjustment"], -15)
        self.assertTrue(fallback["warnings"])

    def test_goal_alignment_capital_protection_caps_very_equity_portfolio(self):
        health = app.calculate_portfolio_fit_score(
            plan="PLUS",
            userProfile={"capital": 10000, "horizonYears": 8, "riskPreference": "conservative", "objective": "capital_protection", "goalPriority": "capital_protection", "maxTemporaryLoss": 0.10},
            portfolioMetrics={"initialCapital": 10000, "maxDrawdown": -0.28, "sharpe": 0.8, "cagr": 0.08, "volatility": 0.22},
            frontier={"current": {"sharpe": 0.8}, "best": {"sharpe": 1.0}},
            monteCarlo={"probabilityGain": 0.75, "p5": 7800},
            stressTesting={"result": {"expected_portfolio_return": -0.18}},
            contribution=[{"symbol": "IE00B53SZB19", "displayName": "iShares Nasdaq 100", "weight": 1.0}],
        )
        self.assertLessEqual(self.goal_pillar_score(health), 35)
        self.assertTrue(any("protezione" in warning.lower() or "azionaria" in warning.lower() for warning in health["warnings"]))

    def test_goal_alignment_capital_growth_penalizes_ultrashort_on_long_horizon(self):
        health = app.calculate_portfolio_fit_score(
            plan="PLUS",
            userProfile={"capital": 10000, "horizonYears": 12, "riskPreference": "balanced", "objective": "capital_growth", "goalPriority": "capital_growth", "maxTemporaryLoss": 0.20},
            portfolioMetrics={"initialCapital": 10000, "maxDrawdown": -0.03, "sharpe": 0.4, "cagr": 0.01, "volatility": 0.03},
            frontier={"current": {"sharpe": 0.4}, "best": {"sharpe": 0.7}},
            monteCarlo={"probabilityGain": 0.58, "p5": 9700},
            stressTesting={"result": {"expected_portfolio_return": -0.02}},
            contribution=[{"symbol": "IE00BCRY6557", "displayName": "iShares EUR Ultrashort Bond", "weight": 1.0}],
        )
        self.assertLessEqual(self.goal_pillar_score(health), 55)

    def test_goal_alignment_house_future_expense_penalizes_sp500_on_short_horizon(self):
        health = app.calculate_portfolio_fit_score(
            plan="PLUS",
            userProfile={"capital": 10000, "horizonYears": 3, "riskPreference": "conservative", "objective": "house_future_expense", "goalPriority": "house_future_expense", "maxTemporaryLoss": 0.05},
            portfolioMetrics={"initialCapital": 10000, "maxDrawdown": -0.18, "sharpe": 0.7, "cagr": 0.06, "volatility": 0.18},
            frontier={"current": {"sharpe": 0.7}, "best": {"sharpe": 0.9}},
            monteCarlo={"probabilityGain": 0.68, "p5": 8200},
            stressTesting={"result": {"expected_portfolio_return": -0.12}},
            contribution=[{"symbol": "IE00B5BMR087", "displayName": "iShares Core S&P 500", "weight": 1.0}],
        )
        self.assertLessEqual(self.goal_pillar_score(health), 35)

    def test_goal_alignment_retirement_rewards_global_balanced_portfolio(self):
        health = app.calculate_portfolio_fit_score(
            plan="ADVANCED",
            userProfile={"capital": 10000, "horizonYears": 20, "riskPreference": "balanced", "objective": "retirement_long_term", "goalPriority": "retirement_long_term", "maxTemporaryLoss": 0.25},
            portfolioMetrics={"initialCapital": 10000, "maxDrawdown": -0.16, "sharpe": 0.85, "cagr": 0.06, "volatility": 0.13},
            monteCarlo={"probabilityGain": 0.75, "p5": 9000},
            stressTesting={"result": {"expected_portfolio_return": -0.08}},
            contribution=[
                {"symbol": "IE00B6R52259", "displayName": "iShares MSCI ACWI", "weight": 0.60},
                {"symbol": "IE00BZ043R46", "displayName": "iShares Core Global Aggregate Bond", "weight": 0.40},
            ],
            advancedAnalytics={"pac": {"available": True}},
        )
        self.assertGreaterEqual(self.goal_pillar_score(health), 70)

    def test_advanced_portfolio_fit_score_includes_advanced_pillars_when_available(self):
        health = app.calculatePortfolioFitScore(
            {
                "plan": "ADVANCED",
                "userProfile": {"capital": 10000, "horizonYears": 12, "riskPreference": "balanced", "objective": "crescita"},
                "portfolioMetrics": {"initialCapital": 10000, "maxDrawdown": -0.18, "sharpe": 0.9, "sortino": 1.2, "cagr": 0.07, "volatility": 0.14},
                "monteCarlo": {"probabilityGain": 0.72, "p5": 8300},
                "stressTesting": {"result": {"expected_portfolio_return": -0.08}},
                "contribution": [{"symbol": "US0378331005", "weight": 0.55}, {"symbol": "US5949181045", "weight": 0.45}],
                "advancedAnalytics": {
                    "correlation": {"averageCorrelation": 0.45},
                    "geography": {"available": True},
                    "pac": {"available": True},
                    "factorRisk": {"available": True},
                    "famaFrench": {"available": True},
                    "rollingSortino": {"current": {"averageSortino": 1.1}},
                    "drawdown": {"recoveryDays": 80},
                },
            }
        )
        self.assertEqual(health["scoreType"], "Check-up quantitativo avanzato")
        self.assertTrue(any(pillar["key"] == "realDiversification" for pillar in health["pillars"]))
        self.assertTrue(any(pillar["key"] == "factorScenarioRobustness" for pillar in health["pillars"]))
        self.assertEqual(health["metricsExcludedByPlan"], [])

    def test_portfolio_fit_score_missing_metrics_reduce_confidence_not_score_to_zero(self):
        health = app.build_portfolio_health_score(
            {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
            {"initialCapital": 10000, "maxDrawdown": -0.1, "sharpe": 0.7},
            {},
            {},
            {},
            [{"symbol": "US0378331005", "weight": 1.0}],
            "PLUS",
        )
        self.assertGreater(health["overall"], 0)
        self.assertIn(health["confidence"], {"Media", "Bassa"})
        self.assertTrue(health["metricsMissing"])

    def test_portfolio_fit_score_concentration_cap_and_warning(self):
        health = app.build_portfolio_health_score(
            {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
            {"initialCapital": 10000, "maxDrawdown": -0.1, "sharpe": 2.0, "cagr": 0.20, "volatility": 0.08},
            {"current": {"sharpe": 2.0}, "best": {"sharpe": 2.2}},
            {"probabilityGain": 0.9, "p5": 10500},
            {"result": {"expected_portfolio_return": 0.02}},
            [{"symbol": "US0378331005", "weight": 0.92}, {"symbol": "US5949181045", "weight": 0.08}],
            "PLUS",
        )
        self.assertLessEqual(health["overall"], 70)
        self.assertTrue(health["capsApplied"])

    def test_portfolio_fit_score_compliance_words_absent(self):
        health = app.build_portfolio_health_score(
            {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
            {"initialCapital": 10000, "maxDrawdown": -0.18, "sharpe": 0.9, "cagr": 0.07, "volatility": 0.14},
            {"current": {"sharpe": 0.9}, "best": {"sharpe": 1.1}},
            {"probabilityGain": 0.72, "p5": 8300},
            {"result": {"expected_portfolio_return": -0.08}},
            [{"symbol": "US0378331005", "weight": 0.55}, {"symbol": "US5949181045", "weight": 0.45}],
            "PLUS",
        )
        combined = json.dumps(health, ensure_ascii=False).lower()
        for word in ("compra", "vendi", "garantito", "sicuro", "migliore in assoluto"):
            self.assertNotIn(word, combined)


class MarketDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_cache = app.MARKET_DATA_CACHE_FILE
        self.original_openfigi = app.openFigiClient
        self.original_eodhd = app.eodhdClient
        app.MARKET_DATA_CACHE_FILE = Path(self.tmp.name) / "market_data_cache.json"
        app.MARKET_DATA_CACHE_MEMORY = None
        app.MARKET_DATA_CACHE_MEMORY_PATH = None
        app.EODHD_SEARCH_CACHE.clear()
        app.EODHD_SEARCH_DISABLED_UNTIL = 0.0

    def tearDown(self):
        app.MARKET_DATA_CACHE_FILE = self.original_cache
        app.openFigiClient = self.original_openfigi
        app.eodhdClient = self.original_eodhd
        app.MARKET_DATA_CACHE_MEMORY = None
        app.MARKET_DATA_CACHE_MEMORY_PATH = None
        app.EODHD_SEARCH_CACHE.clear()
        app.EODHD_SEARCH_DISABLED_UNTIL = 0.0
        self.tmp.cleanup()

    def test_instrument_resolver_normalizes_openfigi_result(self):
        class FakeOpenFigi:
            def map_identifier(self, _query):
                return {
                    "figi": "BBG000B9XRY4",
                    "ticker": "AAPL",
                    "exchCode": "US",
                    "currency": "USD",
                    "name": "Apple Inc",
                    "securityType": "Common Stock",
                    "marketSector": "Equity",
                }

            def search(self, _query):
                raise AssertionError("search should not be used when mapping succeeds")

        app.openFigiClient = FakeOpenFigi()
        app.eodhdClient = object()
        instrument = app.instrumentResolverService("US0378331005")
        self.assertEqual(instrument["ticker"], "AAPL")
        self.assertEqual(instrument["exchange"], "US")
        self.assertEqual(instrument["currency"], "USD")
        self.assertEqual(instrument["eodhdCode"], "AAPL.US")
        self.assertEqual(instrument["assetClass"], "equity")

    def test_instrument_resolver_infers_bond_asset_class_from_provider_metadata(self):
        class FakeOpenFigi:
            def map_identifier(self, _query):
                return {
                    "figi": "BBG000BOND1",
                    "idValue": "IE00B4WXJJ64",
                    "ticker": "IEGA",
                    "exchCode": "LN",
                    "currency": "EUR",
                    "name": "ISHARES CORE EURO GOVERNMENT BOND",
                    "securityType": "ETF",
                    "marketSector": "Equity",
                }

        class FakeEodhd:
            def search_symbols(self, _query):
                return []

        app.openFigiClient = FakeOpenFigi()
        app.eodhdClient = FakeEodhd()
        instrument = app.instrumentResolverService("IE00B4WXJJ64")
        self.assertEqual(instrument["assetClass"], "bonds")
        normalized = app.normalize_portfolio(
            [{"symbol": "IE00B4WXJJ64", "weight": 100, "displayName": "iShares Core Euro Government Bond"}]
        )
        self.assertEqual(normalized[0]["assetClass"], "bonds")

    def test_search_instruments_returns_inferred_asset_class(self):
        class FakeOpenFigi:
            def search_many(self, _query):
                return [
                    {
                        "figi": "BBG000BOND1",
                        "idValue": "IE00B4WXJJ64",
                        "ticker": "IEGA",
                        "exchCode": "LN",
                        "currency": "EUR",
                        "name": "ISHARES CORE EURO GOVERNMENT BOND",
                        "securityType": "ETF",
                    }
                ]

        app.openFigiClient = FakeOpenFigi()
        result = app.search_instruments("Euro Government Bond")
        self.assertEqual(result["items"][0]["assetClass"], "bonds")
        self.assertEqual(result["items"][0]["assetClassLabel"], "Obbligazioni")

    def test_instrument_resolver_prefers_eodhd_search_by_isin(self):
        class FakeOpenFigi:
            def map_identifier(self, _query):
                return {
                    "figi": "BBG000BC7Q05",
                    "idValue": "FR0000121014",
                    "ticker": "MC",
                    "exchCode": "XE",
                    "currency": "EUR",
                    "name": "LVMH MOET HENNESSY LOUIS VUI",
                    "securityType": "Common Stock",
                }

        class FakeEodhd:
            def __init__(self):
                self.queries = []

            def search_symbols(self, query):
                self.queries.append(query)
                if query == "FR0000121014":
                    return [
                        {"Code": "LVMH", "Exchange": "US", "Name": "LVMH ADR", "Type": "Common Stock", "ISIN": "US0000000000"},
                        {"Code": "MC", "Exchange": "PA", "Name": "LVMH", "Type": "Common Stock", "ISIN": "FR0000121014"},
                    ]
                return []

        fake_eodhd = FakeEodhd()
        app.openFigiClient = FakeOpenFigi()
        app.eodhdClient = fake_eodhd

        instrument = app.instrumentResolverService("FR0000121014")
        self.assertEqual(instrument["eodhdCode"], "MC.PA")
        self.assertEqual(instrument["eodhdResolutionSource"], "eodhd_search_isin")
        self.assertEqual(fake_eodhd.queries[0], "FR0000121014")

    def test_get_historical_prices_downloads_adjusted_and_caches(self):
        class FakeOpenFigi:
            def map_identifier(self, _query):
                return {"figi": "F", "ticker": "AAPL", "exchCode": "US", "currency": "USD", "name": "Apple"}

            def search(self, _query):
                raise AssertionError("search should not be used")

        class FakeEodhd:
            def __init__(self):
                self.calls = 0
                self.last_code = ""

            def historical_prices(self, eodhd_code, _start, _end):
                self.calls += 1
                self.last_code = eodhd_code
                return [app.PricePoint("2024-01-02", 100.0), app.PricePoint("2024-01-03", 102.0)]

        fake_eodhd = FakeEodhd()
        app.openFigiClient = FakeOpenFigi()
        app.eodhdClient = fake_eodhd

        first = app.getHistoricalPrices("US0378331005", date(2024, 1, 1), date(2024, 1, 31))
        second = app.getHistoricalPrices("US0378331005", date(2024, 1, 1), date(2024, 1, 31))

        self.assertEqual([point.close for point in first], [100.0, 102.0])
        self.assertEqual([point.close for point in second], [100.0, 102.0])
        self.assertEqual(fake_eodhd.calls, 1)
        self.assertEqual(fake_eodhd.last_code, "AAPL.US")
        cache = json.loads(app.MARKET_DATA_CACHE_FILE.read_text(encoding="utf-8"))
        self.assertIn("AAPL.US", next(iter(cache["prices"])))

    def test_get_historical_prices_retries_with_eodhd_search_when_cached_code_fails(self):
        class FakeOpenFigi:
            def map_identifier(self, _query):
                return {"figi": "F", "idValue": "FR0000121014", "ticker": "MC", "exchCode": "XE", "currency": "EUR", "name": "LVMH"}

        class FakeEodhd:
            def __init__(self):
                self.price_calls = []

            def search_symbols(self, _query):
                return [{"Code": "MC", "Exchange": "PA", "Name": "LVMH", "Type": "Common Stock", "ISIN": "FR0000121014"}]

            def historical_prices(self, eodhd_code, _start, _end):
                self.price_calls.append(eodhd_code)
                if eodhd_code == "MC.PA":
                    return [app.PricePoint("2024-01-02", 100.0), app.PricePoint("2024-01-03", 101.0)]
                raise app.BacktestError(f"data_unavailable: EODHD errore 404 per {eodhd_code}")

        app.openFigiClient = FakeOpenFigi()
        fake_eodhd = FakeEodhd()
        app.eodhdClient = fake_eodhd
        cache = {
            "instruments": {
                app.cache_key("FR0000121014"): {
                    "instrumentId": "FR0000121014",
                    "isin": "FR0000121014",
                    "ticker": "MC",
                    "exchange": "XE",
                    "eodhdExchange": "XE",
                    "name": "LVMH",
                    "eodhdCode": "MC.XE",
                }
            },
            "prices": {},
        }
        app.MARKET_DATA_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

        prices = app.getHistoricalPrices("FR0000121014", date(2024, 1, 1), date(2024, 1, 31))
        self.assertEqual([point.close for point in prices], [100.0, 101.0])
        self.assertEqual(fake_eodhd.price_calls, ["MC.XE", "MC.PA"])

    def test_search_instruments_filters_results_without_isin(self):
        class FakeOpenFigi:
            def search_many(self, _query):
                return [
                    {"figi": "BBG000BPH459", "idValue": "US5949181045", "ticker": "MSFT", "exchCode": "US", "currency": "USD", "name": "MICROSOFT CORP"},
                    {"figi": "BBG000BPH4G2", "idValue": "US5949181045", "ticker": "MSF", "exchCode": "GY", "currency": "EUR", "name": "MICROSOFT CORP"},
                    {"ticker": "MSFT", "exchCode": "US", "name": "Microsoft no isin"},
                ]

        app.openFigiClient = FakeOpenFigi()
        result = app.search_instruments("Microsoft")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["isin"], "US5949181045")
        self.assertEqual(result["items"][0]["exchange"], "GY")
        self.assertEqual(result["items"][0]["name"], "Microsoft Corp")
        self.assertTrue(result["hasGlobalResults"])
        self.assertTrue(result["hasEuropeanResults"])

        global_result = app.search_instruments("Microsoft", include_global=True)
        self.assertEqual(len(global_result["items"]), 1)
        self.assertEqual(global_result["items"][0]["exchange"], "US")

    def test_search_instruments_uses_european_alias_when_text_search_returns_dr(self):
        class FakeOpenFigi:
            def search_many(self, _query):
                return [
                    {
                        "figi": "BBG01YJL5MK1",
                        "ticker": "LVMH",
                        "exchCode": "TX",
                        "name": "LVMH MOET HENNESSY LOUIS VUI",
                        "securityType": "Canadian DR",
                    }
                ]

            def map_ticker(self, ticker, exchange):
                self.last_mapping = (ticker, exchange)
                return {
                    "figi": "BBG000BC7Q05",
                    "ticker": "MC",
                    "exchCode": "FP",
                    "name": "LVMH MOET HENNESSY LOUIS VUI",
                    "securityType": "Common Stock",
                    "securityType2": "Common Stock",
                }

        fake = FakeOpenFigi()
        app.openFigiClient = fake
        result = app.search_instruments("LVMH")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["ticker"], "MC")
        self.assertEqual(result["items"][0]["exchange"], "FP")
        self.assertTrue(result["hasEuropeanResults"])

    def test_search_instruments_keeps_trying_variants_until_european_listing(self):
        class FakeOpenFigi:
            def __init__(self):
                self.queries = []

            def search_many(self, query):
                self.queries.append(query)
                if query == "Example":
                    return [
                        {
                            "figi": "BBG000GLOBAL1",
                            "ticker": "EXM",
                            "exchCode": "US",
                            "name": "EXAMPLE PLC",
                            "securityType": "Common Stock",
                        }
                    ]
                return [
                    {
                        "figi": "BBG000EUROPE1",
                        "ticker": "EXM",
                        "exchCode": "LN",
                        "name": "EXAMPLE PLC",
                        "securityType": "Common Stock",
                    }
                ]

        fake = FakeOpenFigi()
        app.openFigiClient = fake
        result = app.search_instruments("Example")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["exchange"], "LN")
        self.assertGreater(len(fake.queries), 1)

    def test_market_data_service_uses_data_unavailable_for_provider_failures(self):
        class FailingOpenFigi:
            def map_identifier(self, _query):
                raise app.BacktestError("data_unavailable: OpenFIGI non ha risolto TEST.")

            def search(self, _query):
                raise app.BacktestError("data_unavailable: OpenFIGI non ha trovato TEST.")

        app.openFigiClient = FailingOpenFigi()
        with self.assertRaises(app.BacktestError) as context:
            app.marketDataService(["TEST"], date(2024, 1, 1), date(2024, 1, 31))
        self.assertIn("data_unavailable", str(context.exception))


class SavedPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_saved = app.SAVED_PORTFOLIOS_FILE
        app.SAVED_PORTFOLIOS_FILE = Path(self.tmp.name) / "saved_portfolios.json"

    def tearDown(self):
        app.SAVED_PORTFOLIOS_FILE = self.original_saved
        self.tmp.cleanup()

    def test_delete_saved_portfolio_removes_item(self):
        saved = app.save_portfolio(
            {
                "userPlan": "PLUS",
                "name": "Test portfolio",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
            }
        )
        portfolio_id = saved["portfolio"]["id"]
        result = app.delete_saved_portfolio(portfolio_id)
        self.assertEqual(result["deletedId"], portfolio_id)
        self.assertEqual(result["items"], [])
        self.assertEqual(app.load_saved_portfolios(), [])

    def test_monitor_persists_last_monitor_date_without_changing_rebalance_date(self):
        saved = app.save_portfolio(
            {
                "userPlan": "PLUS",
                "name": "Monitor test",
                "portfolio": [
                    {"symbol": "US0378331005", "weight": 50, "assetClass": "equity"},
                    {"symbol": "US5949181045", "weight": 50, "assetClass": "equity"},
                ],
                "investorProfile": {"capital": 10000, "age": 40, "horizonYears": 10, "riskPreference": "balanced"},
                "initialCapital": 10000,
                "rebalanceFrequency": "monthly",
                "lastRebalanceDate": "2026-06-13",
            }
        )
        portfolio_id = saved["portfolio"]["id"]
        result = app.run_portfolio_monitor({"id": portfolio_id, "userPlan": "PLUS", "demo": True})
        stored = app.find_saved_portfolio(portfolio_id)
        self.assertEqual(stored["lastRebalanceDate"], "2026-06-13")
        self.assertEqual(stored["lastMonitorDate"], result["latestPriceDate"])
        self.assertEqual(result["monitor"]["currentDate"], result["latestPriceDate"])


if __name__ == "__main__":
    unittest.main()
