from __future__ import annotations

import json
import math
import os
import random
import statistics
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


FACTORS = ["equity", "rates", "inflation", "gold", "commodities", "credit"]
FACTOR_PROXIES = {
    "equity": "VT",
    "rates": "IEF",
    "inflation": "TIP",
    "gold": "GLD",
    "commodities": "DBC",
    "credit": "LQD",
}
SCENARIOS = {
    "Global Recession": {"equity": -0.20, "rates": 0.05, "inflation": -0.02, "credit": -0.08, "gold": 0.04, "commodities": -0.10},
    "High Inflation": {"equity": -0.08, "rates": -0.06, "inflation": 0.12, "credit": -0.03, "gold": 0.10, "commodities": 0.16},
    "Stagflation": {"equity": -0.16, "rates": -0.08, "inflation": 0.10, "credit": -0.07, "gold": 0.12, "commodities": 0.14},
    "Tech Crash": {"equity": -0.28, "rates": 0.04, "inflation": -0.01, "credit": -0.06, "gold": 0.05, "commodities": -0.06},
    "Credit Crisis": {"equity": -0.18, "rates": 0.07, "inflation": -0.02, "credit": -0.16, "gold": 0.06, "commodities": -0.08},
    "Interest Rate Shock": {"equity": -0.07, "rates": -0.10, "inflation": 0.03, "credit": -0.05, "gold": -0.02, "commodities": 0.02},
    "Commodity Shock": {"equity": -0.06, "rates": -0.02, "inflation": 0.08, "credit": -0.02, "gold": 0.06, "commodities": 0.22},
    "Deflation": {"equity": -0.14, "rates": 0.09, "inflation": -0.08, "credit": -0.05, "gold": 0.03, "commodities": -0.16},
    "Dot-com Crash": {"equity": -0.30, "rates": 0.06, "inflation": -0.01, "credit": -0.05, "gold": 0.02, "commodities": -0.05},
    "2008 Financial Crisis": {"equity": -0.38, "rates": 0.12, "inflation": -0.04, "credit": -0.22, "gold": 0.05, "commodities": -0.28},
}


class QuantError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    symbol: str
    weight: float
    asset_class: str = "etf"


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def normalize_portfolio(items: list[dict[str, Any]]) -> list[Asset]:
    assets: list[Asset] = []
    for item in items:
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        weight = float(item.get("weight", 0))
        if weight < 0:
            raise QuantError(f"Peso negativo per {symbol}.")
        assets.append(Asset(symbol=symbol, weight=weight, asset_class=str(item.get("asset_class", "etf"))))
    if not assets:
        raise QuantError("Inserisci almeno un asset.")
    total = sum(item.weight for item in assets)
    if total <= 0:
        equal = 1 / len(assets)
        return [Asset(item.symbol, equal, item.asset_class) for item in assets]
    return [Asset(item.symbol, item.weight / total, item.asset_class) for item in assets]


def make_demo_prices(symbols: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    start_day = parse_date(start)
    end_day = parse_date(end)
    prices: dict[str, dict[str, float]] = {}
    for index, symbol in enumerate(symbols):
        seed = sum(ord(ch) for ch in symbol) + start_day.toordinal()
        rng = random.Random(seed)
        price = 80 + seed % 110
        drift = 0.00018 + index * 0.000025
        vol = 0.009 + (seed % 8) * 0.001
        symbol_prices: dict[str, float] = {}
        cursor = start_day
        while cursor <= end_day:
            if cursor.weekday() < 5:
                cycle = math.sin(cursor.toordinal() / 27 + index) * 0.0015
                price *= max(0.75, 1 + drift + cycle + rng.gauss(0, vol))
                symbol_prices[cursor.isoformat()] = round(price, 6)
            cursor += timedelta(days=1)
        prices[symbol] = symbol_prices
    return prices


def eodhd_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    return value if "." in value else f"{value}.US"


def fetch_eodhd_prices(symbols: list[str], start: str, end: str, _feed: str = "eodhd") -> dict[str, dict[str, float]]:
    key = os.getenv("EODHD_API_KEY")
    if not key:
        raise QuantError("data_unavailable: EODHD_API_KEY mancante. Attiva dati demo o imposta la chiave.")
    prices: dict[str, dict[str, float]] = {}
    base_url = os.getenv("EODHD_API_URL", "https://eodhd.com/api").rstrip("/")
    for symbol in symbols:
        query = urllib.parse.urlencode(
            {
                "api_token": key,
                "fmt": "json",
                "period": "d",
                "from": start,
                "to": end,
                "order": "a",
            }
        )
        request = urllib.request.Request(f"{base_url}/eod/{urllib.parse.quote(eodhd_symbol(symbol))}?{query}")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        symbol_prices: dict[str, float] = {}
        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict) or not row.get("date"):
                    continue
                close = row.get("adjusted_close") if row.get("adjusted_close") not in {None, ""} else row.get("close")
                if close not in {None, ""}:
                    symbol_prices[str(row["date"])[:10]] = float(close)
        if symbol_prices:
            prices[symbol] = symbol_prices
    return prices


def get_prices(symbols: list[str], start: str, end: str, use_demo_data: bool, feed: str = "iex") -> dict[str, dict[str, float]]:
    return make_demo_prices(symbols, start, end) if use_demo_data else fetch_eodhd_prices(symbols, start, end, feed)


def calculate_returns(prices: dict[str, dict[str, float]]) -> tuple[list[str], dict[str, list[float]]]:
    calendars = [set(days) for days in prices.values() if days]
    if not calendars:
        raise QuantError("Nessun prezzo disponibile.")
    days = sorted(set.intersection(*calendars))
    if len(days) < 30:
        raise QuantError("Servono almeno 30 date comuni.")
    returns: dict[str, list[float]] = {}
    for symbol, series in prices.items():
        values: list[float] = []
        for prev_day, day in zip(days, days[1:]):
            previous = series[prev_day]
            current = series[day]
            values.append(current / previous - 1)
        returns[symbol] = values
    return days[1:], returns


def transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(row) for row in zip(*matrix)]


def matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    b_t = transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) for col in b_t] for row in a]


def matvec(a: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(x * y for x, y in zip(row, vector)) for row in a]


def invert(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    augmented = [[float(matrix[i][j]) for j in range(n)] + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            raise QuantError("Matrice singolare: regressione non stimabile.")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        pivot_value = augmented[col][col]
        augmented[col] = [value / pivot_value for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [value - factor * pivot_value for value, pivot_value in zip(augmented[row], augmented[col])]
    return [row[n:] for row in augmented]


def normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def ols(y: list[float], factors: dict[str, list[float]]) -> dict[str, Any]:
    rows = len(y)
    means = {factor: statistics.mean(values) for factor, values in factors.items()}
    x = [[1.0] + [factors[factor][i] - means[factor] for factor in FACTORS] for i in range(rows)]
    x_t = transpose(x)
    xtx_inv = invert(matmul(x_t, x))
    beta = matvec(matmul(xtx_inv, x_t), y)
    predictions = [sum(coef * value for coef, value in zip(beta, row)) for row in x]
    residuals = [actual - predicted for actual, predicted in zip(y, predictions)]
    y_mean = statistics.mean(y)
    ss_res = sum(item * item for item in residuals)
    ss_tot = sum((item - y_mean) ** 2 for item in y)
    r_squared = 1 - ss_res / ss_tot if ss_tot else 0.0
    dof = max(rows - len(beta), 1)
    sigma2 = ss_res / dof
    std_errors = [math.sqrt(max(sigma2 * xtx_inv[i][i], 0)) for i in range(len(beta))]
    p_values = []
    for coef, stderr in zip(beta, std_errors):
        if stderr == 0:
            p_values.append(None)
        else:
            z_score = abs(coef / stderr)
            p_values.append(2 * (1 - normal_cdf(z_score)))
    return {
        "alpha": beta[0],
        "betas": {factor: beta[index + 1] for index, factor in enumerate(FACTORS)},
        "r_squared": r_squared,
        "model_p_value": min((value for value in p_values[1:] if value is not None), default=None),
        "factor_p_values": {factor: p_values[index + 1] for index, factor in enumerate(FACTORS)},
    }


def correlation_matrix(factor_returns: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {}
    for left in FACTORS:
        matrix[left] = {}
        for right in FACTORS:
            l_values = factor_returns[left]
            r_values = factor_returns[right]
            l_mean = statistics.mean(l_values)
            r_mean = statistics.mean(r_values)
            covariance = sum((x - l_mean) * (y - r_mean) for x, y in zip(l_values, r_values)) / max(len(l_values) - 1, 1)
            l_std = statistics.stdev(l_values) if len(l_values) > 1 else 0
            r_std = statistics.stdev(r_values) if len(r_values) > 1 else 0
            matrix[left][right] = covariance / (l_std * r_std) if l_std and r_std else 0.0
    return matrix


def factor_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    portfolio = normalize_portfolio(payload.get("portfolio", []))
    symbols = sorted({asset.symbol for asset in portfolio} | set(FACTOR_PROXIES.values()))
    prices = get_prices(symbols, payload["start"], payload["end"], bool(payload.get("use_demo_data", False)), payload.get("feed", "iex"))
    _, returns = calculate_returns(prices)
    factor_returns = {factor: returns[proxy] for factor, proxy in FACTOR_PROXIES.items()}
    exposures = []
    beta_matrix = {}
    for asset in portfolio:
        model = ols(returns[asset.symbol], factor_returns)
        exposures.append({"symbol": asset.symbol, **model})
        beta_matrix[asset.symbol] = model["betas"]
    return {
        "factors": FACTORS,
        "factor_proxies": FACTOR_PROXIES,
        "exposures": exposures,
        "beta_matrix": beta_matrix,
        "correlation_matrix": correlation_matrix(factor_returns),
    }


def scenario_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    portfolio = normalize_portfolio(payload.get("portfolio", []))
    analysis = factor_analysis({**payload, "portfolio": [asset.__dict__ for asset in portfolio]})
    scenario_name = str(payload.get("scenario") or "Global Recession")
    shocks = payload.get("shocks") or SCENARIOS.get(scenario_name) or SCENARIOS["Global Recession"]
    shocks = {factor: float(shocks.get(factor, 0.0)) for factor in FACTORS}
    by_asset = []
    factor_totals = {factor: 0.0 for factor in FACTORS}
    for asset in portfolio:
        betas = analysis["beta_matrix"][asset.symbol]
        expected = sum(betas[factor] * shocks[factor] for factor in FACTORS)
        weighted = asset.weight * expected
        by_asset.append({"symbol": asset.symbol, "weight": asset.weight, "expected_return": expected, "weighted_contribution": weighted})
        for factor in FACTORS:
            factor_totals[factor] += asset.weight * betas[factor] * shocks[factor]
    expected_portfolio = sum(item["weighted_contribution"] for item in by_asset)
    best = max(by_asset, key=lambda item: item["weighted_contribution"])["symbol"] if by_asset else None
    worst = min(by_asset, key=lambda item: item["weighted_contribution"])["symbol"] if by_asset else None
    return {
        "scenario": scenario_name,
        "shocks": shocks,
        "expected_portfolio_return": expected_portfolio,
        "contribution_by_asset": by_asset,
        "contribution_by_factor": [{"factor": factor, "contribution": factor_totals[factor]} for factor in FACTORS],
        "best_hedge": best,
        "worst_contributor": worst,
        "factor_analysis": analysis,
    }


def covariance_matrix(series: list[list[float]]) -> list[list[float]]:
    n = len(series[0])
    means = [statistics.mean(row) for row in series]
    matrix: list[list[float]] = []
    for i in range(len(series)):
        row = []
        for j in range(len(series)):
            row.append(sum((series[i][k] - means[i]) * (series[j][k] - means[j]) for k in range(n)) / max(n - 1, 1))
        matrix.append(row)
    return matrix


def cholesky(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            total = sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                lower[i][j] = math.sqrt(max(matrix[i][i] - total, 1e-12))
            else:
                lower[i][j] = (matrix[i][j] - total) / lower[j][j]
    return lower


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def monte_carlo(payload: dict[str, Any]) -> dict[str, Any]:
    portfolio = normalize_portfolio(payload.get("portfolio", []))
    symbols = [asset.symbol for asset in portfolio]
    prices = get_prices(symbols, payload["start"], payload["end"], bool(payload.get("use_demo_data", False)), payload.get("feed", "iex"))
    _, returns = calculate_returns(prices)
    series = [returns[symbol] for symbol in symbols]
    means = [statistics.mean(row) for row in series]
    covariance = covariance_matrix(series)
    lower = cholesky(covariance)
    weights = [asset.weight for asset in portfolio]
    simulations = int(payload.get("simulations", 10000))
    horizon_days = int(payload.get("horizon_days", 252))
    rng = random.Random(int(payload.get("seed", 42)))
    terminal: list[float] = []
    for _ in range(simulations):
        value = 1.0
        for _day in range(horizon_days):
            z = [rng.gauss(0, 1) for _ in symbols]
            asset_draws = [means[i] + sum(lower[i][j] * z[j] for j in range(len(symbols))) for i in range(len(symbols))]
            value *= 1 + sum(weight * draw for weight, draw in zip(weights, asset_draws))
        terminal.append(value - 1)
    return {
        "percentile_5": percentile(terminal, 0.05),
        "percentile_50": percentile(terminal, 0.50),
        "percentile_95": percentile(terminal, 0.95),
        "probability_of_loss": sum(1 for value in terminal if value < 0) / len(terminal),
        "simulations": simulations,
        "horizon_days": horizon_days,
    }
