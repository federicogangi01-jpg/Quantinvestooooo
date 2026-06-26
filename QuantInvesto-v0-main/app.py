#!/usr/bin/env python3
"""Local portfolio backtester.

Run with:
    python3 app.py

You can store local credentials in .env.local:
    EODHD_API_KEY=...
    OPENFIGI_API_KEY=...
    OPENAI_API_KEY=...

The app serves a small web UI and a JSON API. It uses OpenFIGI for instrument
resolution and EODHD for adjusted historical prices through urllib so the
project works without installing packages.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import hashlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from standard_portfolios import STANDARD_PORTFOLIO_DISCLAIMER, STANDARD_PORTFOLIOS
from storage_utils import atomic_write_json, load_json_file, prune_mapping


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


ROOT = Path(__file__).resolve().parent
load_local_env(ROOT / ".env.local")

STATIC_DIR = ROOT / "static"
FAMA_FRENCH_DIR = ROOT / "data" / "fama_french"
SAVED_PORTFOLIOS_FILE = ROOT / "saved_portfolios.json"
MARKET_DATA_CACHE_FILE = ROOT / "market_data_cache.json"
AI_EXPLANATION_CACHE_FILE = ROOT / "ai_explanation_cache.json"
MARKET_DATA_CACHE_MEMORY: dict[str, Any] | None = None
MARKET_DATA_CACHE_MEMORY_PATH: Path | None = None
FACTOR_ENGINE_DIR = ROOT / "factor_stress_app" / "standalone"
if str(FACTOR_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(FACTOR_ENGINE_DIR))

from quant_engine import (  # noqa: E402
    FACTORS as FACTOR_STRESS_FACTORS,
    SCENARIOS as FACTOR_STRESS_SCENARIOS,
    factor_analysis as run_factor_stress_analysis,
    monte_carlo as run_factor_stress_monte_carlo,
    scenario_analysis as run_factor_stress_scenario,
)

OPENFIGI_API_URL = os.getenv("OPENFIGI_API_URL", "https://api.openfigi.com")
EODHD_API_URL = os.getenv("EODHD_API_URL", "https://eodhd.com/api")
DEFAULT_PORT = int(os.getenv("PORT", "8765"))
ASSET_CLASSES = {"equity", "bonds", "gold", "commodities"}
ASSET_CLASS_LABELS = {
    "equity": "Azioni",
    "bonds": "Obbligazioni",
    "gold": "Oro",
    "commodities": "Materie prime",
}
BENCHMARK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "global_equity": {
        "id": "global_equity",
        "name": "Azionario Globale",
        "type": "single",
        "assetClass": "equity",
        "description": "Benchmark azionario globale usato per confrontare portafogli prevalentemente azionari e globalmente diversificati.",
        "holdings": [{"name": "iShares MSCI ACWI", "isin": "IE00B6R52259", "weight": 100.0, "role": "Benchmark azionario globale diversificato"}],
    },
    "us_equity": {
        "id": "us_equity",
        "name": "Azionario USA",
        "type": "single",
        "assetClass": "equity",
        "description": "Benchmark azionario USA usato quando la parte azionaria del portfolio è concentrata prevalentemente sugli Stati Uniti.",
        "holdings": [{"name": "iShares Core S&P 500", "isin": "IE00B5BMR087", "weight": 100.0, "role": "Benchmark azionario USA large cap"}],
    },
    "europe_equity": {
        "id": "europe_equity",
        "name": "Azionario Europa",
        "type": "single",
        "assetClass": "equity",
        "description": "Benchmark azionario europeo usato quando la parte azionaria del portfolio è concentrata prevalentemente in Europa.",
        "holdings": [{"name": "iShares Core MSCI Europe", "isin": "IE00B1YZSC51", "weight": 100.0, "role": "Benchmark azionario europeo"}],
    },
    "emerging_equity": {
        "id": "emerging_equity",
        "name": "Azionario Mercati Emergenti",
        "type": "single",
        "assetClass": "equity",
        "description": "Benchmark azionario usato quando la parte azionaria del portfolio è concentrata sui mercati emergenti.",
        "holdings": [{"name": "iShares Core MSCI Emerging Markets", "isin": "IE00BKM4GZ66", "weight": 100.0, "role": "Benchmark azionario mercati emergenti"}],
    },
    "global_aggregate_bond": {
        "id": "global_aggregate_bond",
        "name": "Obbligazionario Globale Aggregato",
        "type": "single",
        "assetClass": "bond",
        "description": "Benchmark obbligazionario globale usato per portafogli prevalentemente obbligazionari o multi-asset con forte componente bond.",
        "holdings": [{"name": "iShares Core Global Aggregate Bond", "isin": "IE00BZ043R46", "weight": 100.0, "role": "Benchmark obbligazionario globale aggregate"}],
    },
    "eur_corporate_bond": {
        "id": "eur_corporate_bond",
        "name": "Obbligazionario Corporate Euro",
        "type": "single",
        "assetClass": "bond",
        "description": "Benchmark obbligazionario corporate investment grade in euro.",
        "holdings": [{"name": "iShares Core EUR Corporate Bond", "isin": "IE00B3F81R35", "weight": 100.0, "role": "Benchmark obbligazionario corporate investment grade in euro"}],
    },
    "euro_government_bond": {
        "id": "euro_government_bond",
        "name": "Obbligazionario Governativo Euro",
        "type": "single",
        "assetClass": "bond",
        "description": "Benchmark obbligazionario governativo dell'area euro.",
        "holdings": [{"name": "iShares Core Euro Government Bond", "isin": "IE00B4WXJJ64", "weight": 100.0, "role": "Benchmark obbligazionario governativo area euro"}],
    },
    "short_duration_bond": {
        "id": "short_duration_bond",
        "name": "Obbligazionario Breve / Cash-like",
        "type": "single",
        "assetClass": "bond",
        "description": "Benchmark obbligazionario a breve durata usato come proxy cash-like.",
        "holdings": [{"name": "iShares EUR Ultrashort Bond", "isin": "IE00BCRY6557", "weight": 100.0, "role": "Benchmark obbligazionario a breve durata / liquidità"}],
    },
    "gold": {
        "id": "gold",
        "name": "Oro",
        "type": "single",
        "assetClass": "gold",
        "description": "Benchmark usato per portafogli prevalentemente esposti all'oro.",
        "holdings": [{"name": "iShares Physical Gold", "isin": "IE00B4ND3602", "weight": 100.0, "role": "Benchmark oro fisico"}],
    },
    "broad_commodities": {
        "id": "broad_commodities",
        "name": "Commodity Globali",
        "type": "single",
        "assetClass": "commodity",
        "description": "Benchmark commodity diversificato.",
        "holdings": [{"name": "Invesco Bloomberg Commodity", "isin": "IE00BD6FTQ80", "weight": 100.0, "role": "Benchmark commodity diversificato"}],
    },
}
SERVER_USER_PLAN = os.getenv("APP_USER_PLAN", "").strip().upper()
ENFORCE_SERVER_USER_PLAN = os.getenv("ENFORCE_SERVER_USER_PLAN", "0").strip().lower() in {"1", "true", "yes", "on"}
MARKET_DATA_CACHE_MAX_PRICES = int(os.getenv("MARKET_DATA_CACHE_MAX_PRICES", "1200"))
MARKET_DATA_CACHE_MAX_INSTRUMENTS = int(os.getenv("MARKET_DATA_CACHE_MAX_INSTRUMENTS", "600"))
AI_EXPLANATION_CACHE_MAX_ITEMS = int(os.getenv("AI_EXPLANATION_CACHE_MAX_ITEMS", "600"))
FAMA_FRENCH_CACHE: dict[str, dict[str, Any]] = {}
INSTRUMENT_SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
INSTRUMENT_SEARCH_CACHE_TTL_SECONDS = 3600
INSTRUMENT_SEARCH_VARIANT_LIMIT = 4
INSTRUMENT_SEARCH_DERIVED_QUERY_LIMIT = 2
EODHD_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
EODHD_SEARCH_CACHE_TTL_SECONDS = 3600
EODHD_SEARCH_DISABLED_UNTIL = 0.0
EODHD_SEARCH_DISABLED_TTL_SECONDS = 3600
EUROPEAN_OPENFIGI_EXCHANGES = {
    "AS",
    "AT",
    "BB",
    "BR",
    "CH",
    "CO",
    "DC",
    "DU",
    "EU",
    "F",
    "FP",
    "GR",
    "GY",
    "HE",
    "IC",
    "ID",
    "IM",
    "IR",
    "LN",
    "LONDON",
    "LS",
    "MC",
    "MI",
    "NA",
    "NO",
    "OL",
    "PA",
    "PL",
    "PR",
    "S",
    "SM",
    "SS",
    "ST",
    "SW",
    "TI",
    "VI",
    "VX",
    "WA",
    "XE",
    "XO",
}
EUROPEAN_EXCHANGE_PRIORITY = {
    "XE": 0,
    "GY": 0,
    "GR": 0,
    "XETRA": 0,
    "XO": 1,
    "NA": 1,
    "AS": 1,
    "FP": 2,
    "PA": 2,
    "LN": 3,
    "LONDON": 3,
    "IM": 4,
    "MI": 4,
    "SW": 5,
    "VX": 5,
    "SM": 6,
    "MC": 6,
    "S": 7,
    "SS": 7,
    "ST": 7,
}
GLOBAL_EXCHANGE_PRIORITY = {
    "US": 0,
    "UN": 0,
    "UW": 0,
    "UQ": 0,
    "UA": 0,
    "N": 0,
    "NYSE": 0,
    "NASDAQ": 1,
    "Q": 1,
    "ARCA": 2,
    "TO": 3,
    "CN": 3,
    "HK": 4,
    "KS": 5,
    "JP": 6,
    "JT": 6,
    "AU": 7,
}
EUROPEAN_SEARCH_ALIASES = {
    "lvmh": [("MC", "FP"), ("MC", "XE")],
    "lvmh moet": [("MC", "FP"), ("MC", "XE")],
    "lvmh moet hennessy": [("MC", "FP"), ("MC", "XE")],
    "moet hennessy": [("MC", "FP"), ("MC", "XE")],
    "louis vuitton": [("MC", "FP"), ("MC", "XE")],
}
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/responses")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
OPENAI_EXPLANATION_MODEL = os.getenv("OPENAI_EXPLANATION_MODEL", OPENAI_MODEL)
OPENAI_ADVISOR_MODEL = os.getenv("OPENAI_ADVISOR_MODEL", OPENAI_MODEL)
OPENAI_EXPLANATIONS_ENABLED = os.getenv("OPENAI_EXPLANATIONS_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "18"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
OPENAI_RETRY_BASE_SECONDS = float(os.getenv("OPENAI_RETRY_BASE_SECONDS", "0.4"))
OPENAI_EXPLANATION_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_EXPLANATION_MAX_OUTPUT_TOKENS", "900"))
OPENAI_ADVISOR_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_ADVISOR_MAX_OUTPUT_TOKENS", "280"))
AI_EXPLANATION_CACHE_TTL_SECONDS = int(os.getenv("AI_EXPLANATION_CACHE_TTL_SECONDS", str(7 * 24 * 60 * 60)))
AI_EXPLANATION_CACHE_VERSION = os.getenv("AI_EXPLANATION_CACHE_VERSION", "2026-06-21-v4")
AI_EXPLANATION_DISCLAIMER = (
    "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata "
    "né raccomandazione di investimento."
)
AI_EXPLANATION_FORBIDDEN_WORDS = {"compra", "vendi", "garantito"}
AI_EXPLANATION_FIELDS = [
    "summary",
    "meaning",
    "what_to_watch",
    "main_strength",
    "main_weakness",
    "possible_improvement",
    "concrete_example",
    "technical_detail",
    "disclaimer",
]
AI_EXPLANATION_DETAIL_FIELDS = [
    "title",
    "simple_summary",
    "what_the_numbers_mean",
    "main_risk",
    "main_strength",
    "possible_improvement",
    "what_to_look_at",
    "technical_note",
    "disclaimer",
]
AI_EXPLANATION_LEGACY_DETAIL_FIELDS = [
    "technical_detail",
    "main_risk",
    "main_strength",
    "possible_improvement",
    "disclaimer",
]
AI_EXPLANATION_FIELD_ALIASES = {
    "meaning": ["meaning_for_user", "what_the_numbers_mean"],
    "what_to_watch": ["what_to_look_at"],
    "main_weakness": ["main_risk"],
    "technical_detail": ["technical_note"],
}
AI_EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": AI_EXPLANATION_FIELDS,
    "properties": {field: {"type": "string"} for field in AI_EXPLANATION_FIELDS},
}
AI_EXPLANATION_DETAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": AI_EXPLANATION_FIELDS,
    "properties": {field: {"type": "string", "maxLength": 520} for field in AI_EXPLANATION_FIELDS},
}
AI_ADVISOR_FIELDS = [
    "summary",
    "score_comment",
    "main_risk",
    "main_strength",
    "optimization_note",
    "disclaimer",
]
AI_ADVISOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": AI_ADVISOR_FIELDS,
    "properties": {field: {"type": "string"} for field in AI_ADVISOR_FIELDS},
}
AI_EXPLANATION_SYSTEM_PROMPT = """
Sei un assistente educativo per investitori retail europei integrato in una app di analisi quantitativa del portafoglio.

Non calcolare metriche finanziarie. Interpreta solo risultati già calcolati dal backend: backtest, rendimento atteso, oscillazione, peggiore perdita temporanea, rapporto rischio-rendimento, rapporto rendimento-rischio negativo, frontiera efficiente, Monte Carlo, scenari, stress test, diversificazione reale, geografia, PAC, tracking, ribilanciamento, Portfolio Fit Score, Fama-French e advanced analytics.

Principio guida: la matematica è il motore, la diagnosi è il prodotto. Non iniziare dalla metrica tecnica. Inizia sempre dal significato pratico per l'investitore.

Restituisci solo JSON conforme a questo schema:
{
  "summary": "",
  "meaning": "",
  "what_to_watch": "",
  "main_strength": "",
  "main_weakness": "",
  "possible_improvement": "",
  "concrete_example": "",
  "technical_detail": "",
  "disclaimer": ""
}

Regole di lunghezza: summary massimo una frase. meaning massimo tre frasi. what_to_watch massimo tre frasi brevi o tre bullet testuali. main_strength, main_weakness e possible_improvement una frase ciascuno. concrete_example deve usare euro quando portfolioValue o capitale sono disponibili. technical_detail breve e solo se utile. Totale ideale: 120-160 parole.

Regola obbligatoria per what_to_watch / "Cosa guardare": ogni punto deve essere una frase completa con soggetto, verbo e complemento oggetto. Non usare frammenti nominali come "distanza dalla curva efficiente" o "variazione tra portafoglio inserito e ottimizzato". Scrivi invece "L'utente deve osservare quanto il punto del portafoglio è distante dalla curva efficiente" e "L'utente deve confrontare come cambiano rischio e rendimento tra portafoglio inserito e portafoglio ottimizzato". Adatta sempre il soggetto al confronto attivo: se il confronto è con benchmark standard, usa "benchmark standard"; se è standard_only, descrivi solo il portafoglio standard analizzato.

Regola obbligatoria per possible_improvement / "Area di miglioramento": non generare azioni operative precise fuori dal Piano di miglioramento. Non indicare percentuali, pesi o strumenti specifici da aumentare/ridurre. Scrivi sempre una direzione pratica non prescrittiva: problema rilevato, categoria di intervento possibile e rimando al Piano di miglioramento per pesi e strumenti. Esempio corretto: "La perdita potenziale è un'area da monitorare. Una direzione possibile è valutare più componenti difensive e minore dipendenza dagli asset più volatili. Le modifiche precise su pesi e strumenti sono nel Piano di miglioramento."

Stile: parole semplici, frasi brevi, tono chiaro, concreto, rassicurante, educativo, non allarmistico e non promozionale. Scrivi per un investitore retail intelligente ma non esperto.

Evita nomi tecnici nei campi letti dall'utente: summary, meaning, what_to_watch, main_strength, main_weakness, possible_improvement e concrete_example. Usa parole comprensibili: "rapporto rischio-rendimento" al posto di "Sharpe", "rapporto rendimento-rischio negativo" al posto di "Sortino", "crescita media annua" al posto di "CAGR", "peggiore perdita temporanea" al posto di "drawdown", "oscillazione del portafoglio" al posto di "volatilità", "diversificazione reale" al posto di "correlazione". Puoi usare nomi tecnici solo in technical_detail, spiegandoli in modo semplice.

Divieti: non dire "compra", "vendi", "devi fare", "questo investimento salirà", "questo portafoglio è il migliore", "garantito", "sicuro", "senza rischio", "previsione". Usa "potrebbe", "storicamente", "in base ai dati calcolati", "una possibile area da valutare", "il sistema evidenzia", "questo dato suggerisce".

Frasi vietate perché troppo generiche: "questo confronto aiuta un investitore", "l'ottimizzazione rende il portafoglio più coerente", "il dato è interessante", "la sezione mostra informazioni utili". Se stai per usarle, riscrivile citando una metrica o una differenza concreta.

Per ogni sezione spiega cosa sta mostrando, quali dati sono stati calcolati, cosa significano per l'investitore, cosa guardare nel grafico o nella tabella, il punto forte, il punto debole e una possibile area da valutare. Se confronti portafoglio inserito e ottimizzato, spiega la differenza reale: se il rischio scende, se il rendimento sale, se il vantaggio è marginale, oppure se il rendimento aumenta ma aumenta anche l'instabilità.

Quando commenti diagnosi o backtesting, usa le metriche storiche già calcolate dal backend: valore finale nella simulazione, risultato totale nel periodo analizzato, crescita media annua storica, oscillazione del portafoglio, rapporto rischio-rendimento e peggiore perdita temporanea. Individua la metrica con differenza più rilevante tra portafoglio inserito e portafoglio ottimizzato/di confronto e commenta quella. Se il rapporto rischio-rendimento è molto diverso, spiega esplicitamente il confronto: per esempio "il portafoglio inserito ha ottenuto 0,28 euro per unità di rischio, mentre il portafoglio ottimizzato ne ha ottenuti 0,75". Non usare il nome tecnico Sharpe nei campi letti dall'utente.

Quando commenti il punto di forza in diagnosi e sono disponibili benchmark e valori finali, confronta sempre portafoglio inserito vs benchmark e portafoglio ottimizzato vs benchmark. Spiega in poche parole che il benchmark è un riferimento educativo per capire se il risultato è sopra o sotto un modello coerente. Mostra i valori finali e indica se ciascun portafoglio batte o non batte il benchmark.

Regole per sezioni:
- Portfolio Fit Score / Check-up del portafoglio: spiega perché il punteggio è alto o basso, quali pillar lo sostengono o lo penalizzano, quali metriche sono escluse dal piano, se la diagnosi è preliminare o completa, quale trade-off emerge tra portafoglio inserito e ottimizzato e cosa monitorare. Non dire solo il numero: spiega cosa significa in base al piano FREE, PLUS o ADVANCED.
- Efficient Frontier: spiega se il portafoglio è vicino o lontano dalla combinazione più efficiente, se l'ottimizzato riduce il rischio o aumenta il rendimento e cosa guardare nel grafico.
- Monte Carlo: chiarisci che non predice il futuro; spiega scenario negativo, mediano, positivo, probabilità di successo e differenza tra portafoglio inserito e ottimizzato.
- Scenario Analysis e Stress Testing: spiega shock simulato, perdita stimata in percentuale e in euro, componente più vulnerabile e componente più difensiva.
- Backtest: spiega il viaggio storico, non solo il rendimento finale: crescita, oscillazioni, peggior fase, recupero e limiti del passato.
- Max Drawdown e Peak-to-Trough: traduci sempre la perdita in euro se portfolioValue è disponibile e cita capitale minimo o impatto psicologico.
- Correlation Matrix e Diversificazione: spiega se la diversificazione è reale o apparente e se gli strumenti si muovono insieme.
- Geografia: distingue sede dello strumento ed esposizione economica reale; segnala eventuale concentrazione.
- PAC: spiega ruolo del tempo, dei versamenti e dello scenario prudente.
- Tracking e ribilanciamento: parla di pesi target, scostamento e manutenzione del rischio; non usare linguaggio di trading.
- Advanced Analytics e Factor Analysis: spiega quali forze muovono il portafoglio e cosa significa in scenari diversi.

Controllo qualità prima di rispondere: la risposta deve essere specifica per la sezione, citare dati reali se disponibili, spiegare cosa significano per i soldi dell'utente, indicare cosa guardare, evidenziare punto forte e punto debole, evitare raccomandazioni dirette e includere il disclaimer.

Disclaimer obbligatorio: Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.
""".strip()
AI_EXPLANATION_TEMPLATES: dict[str, dict[str, str]] = {
    "portfolio_overview": {"focus": "coerenza generale del portafoglio"},
    "portfolio_health_score": {"focus": "lettura del check-up del portafoglio in base al piano utente"},
    "portfolio_fit_score": {"focus": "coerenza tra portafoglio, profilo investitore, orizzonte, obiettivo e rischio sostenibile"},
    "backtesting": {"focus": "risultato storico, rischio sopportato e peggiore perdita temporanea"},
    "efficient_frontier": {"focus": "rapporto rischio-rendimento e alternativa ottimizzata"},
    "sharpe_ratio": {"focus": "rendimento ottenuto rispetto all'oscillazione totale"},
    "sortino_ratio": {"focus": "rendimento ottenuto rispetto alle oscillazioni negative"},
    "max_drawdown": {"focus": "perdita temporanea massima tradotta in euro"},
    "monte_carlo": {"focus": "traiettorie future simulate e probabilita di successo"},
    "stress_testing": {"focus": "resistenza del portafoglio a shock avversi"},
    "scenario_analysis": {"focus": "impatto degli scenari macroeconomici"},
    "correlation_matrix": {"focus": "diversificazione reale o concentrazione nascosta"},
    "fama_french_factor_analysis": {"focus": "dipendenza dai fattori di mercato"},
    "rolling_returns": {"focus": "stabilita dei rendimenti su finestre storiche"},
    "peak_to_trough": {"focus": "caduta dal massimo al minimo e recupero"},
    "rebalancing": {"focus": "scostamento dai pesi target e disciplina di ribilanciamento"},
    "portfolio_formation": {"focus": "costruzione iniziale del portafoglio"},
    "geography_exposure": {"focus": "concentrazione geografica stimata del portafoglio"},
    "benchmark_comparison": {"focus": "confronto con un riferimento educativo coerente"},
}

LOCAL_TECHNICAL_GLOSSARY: dict[str, str] = {
    "alpha": "Alpha: parte del rendimento non spiegata dai fattori del modello; non è una previsione.",
    "averageAbsoluteCorrelation": "Correlazione media assoluta: misura quanto gli strumenti tendono a muoversi insieme, ignorando il segno.",
    "benchmark": "Benchmark: riferimento educativo usato per confrontare il portafoglio con una struttura alternativa.",
    "bestHedge": "Best hedge: componente che nello scenario simulato protegge meglio o perde meno.",
    "beta": "Beta: sensibilità di un asset a un fattore; se il fattore si muove, il beta indica quanto l'asset tende a reagire.",
    "cagr": "Crescita media annua: traduce il risultato totale in una crescita annua composta equivalente.",
    "cashLike": "Cash-like: strumenti simili alla liquidità o obbligazioni molto brevi, di solito meno sensibili ai movimenti dei mercati.",
    "cma": "CMA: fattore Fama-French che confronta aziende con investimenti prudenti rispetto ad aziende con investimenti aggressivi.",
    "commodity": "Commodity: materie prime come energia, metalli o prodotti agricoli, spesso influenzate da inflazione e ciclo economico.",
    "countryExposure": "Esposizione per paese: stima quanto peso del portafoglio dipende da uno specifico mercato nazionale.",
    "correlation": "Correlazione: misura se due strumenti si muovono insieme; vicino a 1 significa movimenti simili, vicino a 0 movimenti poco collegati.",
    "drawdown": "Peggiore perdita temporanea: caduta dal massimo precedente al minimo successivo prima di un eventuale recupero.",
    "duration": "Durata della perdita: numero di giorni in cui il portafoglio resta sotto il massimo precedente.",
    "efficientFrontier": "Frontiera efficiente: insieme di combinazioni che storicamente hanno offerto il miglior equilibrio tra crescita e oscillazione.",
    "expectedReturn": "Rendimento atteso: stima costruita dai dati disponibili; non è una previsione certa.",
    "factorContribution": "Contributo fattoriale: parte dell'impatto stimato attribuita a ciascun motore di rischio.",
    "finalValue": "Valore finale: capitale simulato alla fine del periodo analizzato.",
    "geographicProxy": "Proxy geografico: classificazione stimata usando i dati disponibili sugli strumenti; può non coincidere con tutte le vendite reali delle aziende sottostanti.",
    "hml": "HML: fattore Fama-French che confronta titoli value con titoli growth.",
    "maxDrawdown": "Max drawdown: nome tecnico della peggiore perdita temporanea dal massimo al minimo.",
    "mkt": "MKT: fattore mercato; misura l'esposizione generale al mercato azionario rispetto al tasso privo di rischio.",
    "mom": "MOM: fattore momentum; misura la tendenza dei titoli che sono saliti di più a continuare a comportarsi meglio nel breve periodo.",
    "monteCarlo": "Monte Carlo: simulazione di molti possibili percorsi futuri usando rendimento, oscillazione e correlazioni storiche.",
    "p5": "Percentile 5: scenario negativo plausibile; solo il 5% delle simulazioni finisce peggio.",
    "p50": "Percentile 50: scenario mediano; metà delle simulazioni finisce sopra e metà sotto.",
    "p95": "Percentile 95: scenario positivo plausibile; solo il 5% delle simulazioni finisce meglio.",
    "pac": "PAC: piano di accumulo; simula investimenti periodici nel tempo.",
    "peakToTrough": "Peak-to-trough: distanza tra massimo storico e minimo successivo nella fase di perdita.",
    "pValue": "P-value: indica quanto è statisticamente solida la relazione stimata; valori più bassi sono in genere più significativi.",
    "probabilityLoss": "Probabilità di perdita: quota di simulazioni che chiude sotto il capitale iniziale.",
    "probabilitySuccess": "Probabilità di successo: quota di simulazioni che raggiunge l'obiettivo impostato.",
    "rSquared": "R²: quota dei movimenti spiegata dal modello; più è alto, più il modello descrive bene i dati storici.",
    "rebalancing": "Ribilanciamento: riportare i pesi verso la strategia scelta quando il portafoglio si è allontanato dai target.",
    "recovery": "Tempo di recupero: tempo necessario per tornare al massimo precedente dopo una perdita.",
    "rmw": "RMW: fattore Fama-French che confronta aziende più redditizie con aziende meno redditizie.",
    "rollingReturn": "Rendimento rolling: rendimento calcolato su finestre mobili, utile per non guardare un solo periodo fisso.",
    "rollingSharpe": "Rapporto rischio-rendimento rolling: efficienza calcolata su finestre mobili nel tempo.",
    "rollingSortino": "Rapporto rendimento-rischio negativo rolling: misura su finestre mobili concentrata sulle sole oscillazioni negative.",
    "sharpe": "Rapporto rischio-rendimento: misura quanto rendimento è stato ottenuto per ogni unità di oscillazione totale.",
    "shock": "Shock: variazione ipotetica applicata a un fattore di mercato per stimare l'impatto sul portafoglio.",
    "smb": "SMB: fattore Fama-French che confronta società piccole con società grandi.",
    "sortino": "Rapporto rendimento-rischio negativo: simile al rapporto rischio-rendimento, ma considera soprattutto le discese.",
    "volatility": "Oscillazione del portafoglio: misura quanto il valore varia nel tempo; più è alta, più il percorso è instabile.",
    "worstContributor": "Worst contributor: componente che nello scenario simulato pesa di più sulla perdita.",
}

TECHNICAL_TERMS_BY_ANALYSIS: dict[str, list[str]] = {
    "portfolio_overview": ["expectedReturn", "volatility", "drawdown", "sharpe"],
    "portfolio_health_score": ["expectedReturn", "volatility", "drawdown", "sharpe", "probabilitySuccess"],
    "portfolio_fit_score": ["expectedReturn", "volatility", "drawdown", "sharpe", "probabilitySuccess"],
    "backtesting": ["finalValue", "cagr", "volatility", "drawdown", "sharpe"],
    "efficient_frontier": ["efficientFrontier", "expectedReturn", "volatility", "sharpe", "benchmark"],
    "sharpe_ratio": ["sharpe", "volatility"],
    "sortino_ratio": ["sortino", "volatility"],
    "max_drawdown": ["drawdown", "maxDrawdown", "recovery"],
    "monte_carlo": ["monteCarlo", "p5", "p50", "p95", "probabilityLoss", "probabilitySuccess"],
    "stress_testing": ["shock", "beta", "factorContribution", "bestHedge", "worstContributor"],
    "scenario_analysis": ["shock", "beta", "factorContribution", "bestHedge", "worstContributor"],
    "correlation_matrix": ["correlation", "averageAbsoluteCorrelation"],
    "fama_french_factor_analysis": ["alpha", "beta", "rSquared", "pValue", "mkt", "smb", "hml", "rmw", "cma", "mom"],
    "rolling_returns": ["rollingReturn", "rollingSharpe", "rollingSortino", "sortino"],
    "peak_to_trough": ["peakToTrough", "drawdown", "duration", "recovery"],
    "rebalancing": ["rebalancing", "cashLike"],
    "portfolio_formation": ["pac", "finalValue", "cagr", "probabilitySuccess"],
    "geography_exposure": ["countryExposure", "geographicProxy"],
    "benchmark_comparison": ["benchmark", "cagr", "volatility", "drawdown", "sharpe"],
}
PLAN_ORDER = {"FREE": 0, "PLUS": 1, "ADVANCED": 2}
PLAN_FEATURES: dict[str, dict[str, Any]] = {
    "FREE": {
        "maxPortfolios": 1,
        "basicBacktest": True,
        "advancedBacktest": False,
        "expectedReturn": True,
        "volatility": True,
        "maxDrawdown": True,
        "sharpeRatio": True,
        "portfolioHealthScore": "basic",
        "benchmarkComparison": "basic",
        "efficientFrontier": False,
        "optimizedPortfolio": False,
        "monteCarlo": False,
        "scenarioAnalysis": False,
        "stressTesting": False,
        "portfolioTracking": False,
        "rebalancingSuggestions": False,
        "rebalancingNotifications": False,
        "famaFrench": False,
        "sortinoRatio": False,
        "correlationMatrix": False,
        "geographyExposure": False,
        "rollingReturns": False,
        "rollingVolatility": False,
        "rollingSharpe": False,
        "rollingSortino": False,
        "pacAnalysis": False,
        "peakToTrough": False,
        "factorShockAnalysis": False,
    },
    "PLUS": {
        "maxPortfolios": "unlimited",
        "basicBacktest": True,
        "advancedBacktest": True,
        "expectedReturn": True,
        "volatility": True,
        "maxDrawdown": True,
        "sharpeRatio": True,
        "portfolioHealthScore": "full",
        "benchmarkComparison": "full",
        "efficientFrontier": True,
        "optimizedPortfolio": True,
        "monteCarlo": True,
        "scenarioAnalysis": True,
        "stressTesting": True,
        "portfolioTracking": True,
        "rebalancingSuggestions": True,
        "rebalancingNotifications": True,
        "famaFrench": False,
        "sortinoRatio": False,
        "correlationMatrix": False,
        "geographyExposure": False,
        "rollingReturns": False,
        "rollingVolatility": False,
        "rollingSharpe": False,
        "rollingSortino": False,
        "pacAnalysis": "basic",
        "peakToTrough": False,
        "factorShockAnalysis": False,
    },
    "ADVANCED": {
        "maxPortfolios": "unlimited",
        "basicBacktest": True,
        "advancedBacktest": True,
        "expectedReturn": True,
        "volatility": True,
        "maxDrawdown": True,
        "sharpeRatio": True,
        "portfolioHealthScore": "full",
        "benchmarkComparison": "full",
        "efficientFrontier": True,
        "optimizedPortfolio": True,
        "monteCarlo": True,
        "scenarioAnalysis": True,
        "stressTesting": True,
        "portfolioTracking": True,
        "rebalancingSuggestions": True,
        "rebalancingNotifications": True,
        "famaFrench": True,
        "sortinoRatio": True,
        "correlationMatrix": "advanced",
        "geographyExposure": True,
        "rollingReturns": True,
        "rollingVolatility": True,
        "rollingSharpe": True,
        "rollingSortino": True,
        "pacAnalysis": "advanced",
        "peakToTrough": True,
        "factorShockAnalysis": True,
    },
}
FEATURE_MESSAGES = {
    "efficientFrontier": {
        "name": "Efficient Frontier",
        "requiredPlan": "PLUS",
        "message": "La Efficient Frontier e disponibile dal piano Plus.",
        "previewMessage": "Ti aiuta a capire se esiste una combinazione degli stessi strumenti con un miglior rapporto rischio-rendimento.",
    },
    "monteCarlo": {
        "name": "Monte Carlo Simulation",
        "requiredPlan": "PLUS",
        "message": "La Monte Carlo Simulation e disponibile dal piano Plus.",
        "previewMessage": "Mostra possibili traiettorie future del portafoglio, inclusi scenario negativo plausibile, mediano e positivo.",
    },
    "scenarioAnalysis": {
        "name": "Scenario Analysis",
        "requiredPlan": "PLUS",
        "message": "La Scenario Analysis e disponibile dal piano Plus.",
        "previewMessage": "Traduce shock macroeconomici in impatto stimato sul portafoglio.",
    },
    "stressTesting": {
        "name": "Stress Testing",
        "requiredPlan": "PLUS",
        "message": "Lo Stress Testing e disponibile dal piano Plus.",
        "previewMessage": "Ti aiuta a vedere come il portafoglio potrebbe reagire a condizioni di mercato difficili.",
    },
    "portfolioTracking": {
        "name": "Portfolio Tracking",
        "requiredPlan": "PLUS",
        "message": "Il Portfolio Tracking e disponibile dal piano Plus.",
        "previewMessage": "Monitora il portafoglio nel tempo e confronta i pesi attuali con quelli target.",
    },
    "rebalancingSuggestions": {
        "name": "Rebalancing Suggestions",
        "requiredPlan": "PLUS",
        "message": "I suggerimenti di ribilanciamento sono disponibili dal piano Plus.",
        "previewMessage": "Indicano cosa comprare o vendere in modo indicativo per tornare ai pesi desiderati.",
    },
    "multiplePortfolios": {
        "name": "Più portafogli",
        "requiredPlan": "PLUS",
        "message": "Il piano Free consente un solo portafoglio salvato.",
        "previewMessage": "Con Plus puoi salvare e confrontare più portafogli.",
    },
    "famaFrench": {
        "name": "Fama-French Factor Analysis",
        "requiredPlan": "ADVANCED",
        "message": "L'analisi Fama-French e disponibile nel piano Advanced.",
        "previewMessage": "Mostra da quali fattori di mercato dipende davvero il portafoglio: mercato, small cap, value, profitability, investment e momentum.",
    },
    "sortinoRatio": {
        "name": "Rapporto rendimento-rischio negativo",
        "requiredPlan": "ADVANCED",
        "message": "Il rapporto rendimento-rischio negativo e disponibile nel piano Advanced.",
        "previewMessage": "Misura il rendimento rispetto al rischio di perdita, concentrandosi sulle oscillazioni negative.",
    },
    "correlationMatrix": {
        "name": "Matrice di correlazione avanzata",
        "requiredPlan": "ADVANCED",
        "message": "La matrice di correlazione avanzata e disponibile nel piano Advanced.",
        "previewMessage": "Aiuta a capire se i tuoi ETF sono davvero diversificati o se si muovono nella stessa direzione.",
    },
    "geographyExposure": {
        "name": "Mappa geografica",
        "requiredPlan": "ADVANCED",
        "message": "La mappa geografica e disponibile nel piano Advanced.",
        "previewMessage": "Mostra la concentrazione geografica stimata per continenti e stati usando i metadati disponibili sugli strumenti.",
    },
    "rollingReturns": {
        "name": "Rendimenti rolling",
        "requiredPlan": "ADVANCED",
        "message": "I rendimenti rolling sono disponibili nel piano Advanced.",
        "previewMessage": "Mostrano come il portafoglio si e comportato in diversi periodi storici, non solo nel rendimento medio complessivo.",
    },
    "pacAnalysis": {
        "name": "PAC Analysis avanzata",
        "requiredPlan": "ADVANCED",
        "message": "La PAC Analysis avanzata e disponibile nel piano Advanced.",
        "previewMessage": "Simula un piano di accumulo periodico sul portafoglio e confronta capitale investito, valore finale e risultato.",
    },
    "peakToTrough": {
        "name": "Analisi della perdita temporanea",
        "requiredPlan": "ADVANCED",
        "message": "Caduta massima, recupero e durata della perdita temporanea sono disponibili nel piano Advanced.",
        "previewMessage": "Mostra quanto e durata la fase negativa peggiore dal massimo al minimo e se il portafoglio ha recuperato.",
    },
    "factorShockAnalysis": {
        "name": "Factor risk decomposition",
        "requiredPlan": "ADVANCED",
        "message": "La decomposizione del rischio fattoriale e disponibile nel piano Advanced.",
        "previewMessage": "Scompone l'impatto dello scenario per capire quali fattori macro pesano di più sul portafoglio.",
    },
    "benchmarkComparison": {
        "name": "Benchmark comparison",
        "requiredPlan": "FREE",
        "message": "Il confronto benchmark e incluso nel piano corrente.",
        "previewMessage": "Confronta il portafoglio con un benchmark intelligente costruito localmente su azioni, obbligazioni, oro e commodity.",
    },
    "standardPortfolios": {
        "name": "Portfolio standard QuantInvest",
        "requiredPlan": "PLUS",
        "message": "I portfolio standard QuantInvest sono disponibili dal piano Plus.",
        "previewMessage": "Usa modelli educativi come punto di partenza o benchmark di confronto.",
    },
}


def local_known_asset_class_for_identifier(symbol: str) -> str:
    wanted = str(symbol or "").strip().upper()
    if not wanted:
        return ""
    for portfolio in STANDARD_PORTFOLIOS:
        for holding in portfolio.get("holdings", []):
            if str(holding.get("isin") or "").strip().upper() == wanted:
                return infer_standard_asset_class(holding)
    for benchmark in BENCHMARK_DEFINITIONS.values():
        for holding in benchmark.get("holdings", []):
            if str(holding.get("isin") or "").strip().upper() == wanted:
                return {
                    "bond": "bonds",
                    "commodity": "commodities",
                }.get(str(benchmark.get("assetClass") or "").lower(), str(benchmark.get("assetClass") or "").lower())
    return ""


def infer_asset_class_from_text(*parts: Any) -> str:
    text = " ".join(str(part or "") for part in parts).lower()
    if not text.strip():
        return ""
    if any(token in text for token in ("physical gold", " gold", "oro", "xetra-gold", "sgold", "gld", "iau", "phys")):
        return "gold"
    if any(token in text for token in ("commodity", "commodit", "materie prime", "bloomberg commodity", "dbc", "pdbc", "gsg", "comt", " oil", " crude", " energy", "uso", "ung", "dba")):
        return "commodities"
    if any(token in text for token in ("bond", "obblig", "treasury", "government", "corporate", "aggregate", "ultrashort", "money market", "cash", "duration", "fixed income", "floating rate", "high yield", "lqd", "hyg", "agg", "bnd", "tlt", "ief", "shy", "tip")):
        return "bonds"
    if any(token in text for token in ("common stock", "equity", "stock", "shares", "azioni", "msci", "s&p", "spdr", "nasdaq", "world", "acwi", "all-world", "stoxx", "emerging markets")):
        return "equity"
    return ""


def infer_asset_class_from_metadata(metadata: dict[str, Any] | None, fallback_symbol: str = "") -> str:
    metadata = metadata or {}
    symbol = str(fallback_symbol or metadata.get("instrumentId") or metadata.get("isin") or metadata.get("ticker") or "").strip().upper()
    local = local_known_asset_class_for_identifier(symbol)
    if local in ASSET_CLASSES:
        return local
    ticker = str(metadata.get("ticker") or "").strip().upper()
    if ticker:
        direct = {
            "GLD": "gold",
            "IAU": "gold",
            "SGOL": "gold",
            "GLDM": "gold",
            "PHYS": "gold",
            "AGG": "bonds",
            "BND": "bonds",
            "TLT": "bonds",
            "IEF": "bonds",
            "SHY": "bonds",
            "LQD": "bonds",
            "HYG": "bonds",
            "TIP": "bonds",
            "MUB": "bonds",
            "BIL": "bonds",
            "SHV": "bonds",
            "DBC": "commodities",
            "PDBC": "commodities",
            "GSG": "commodities",
            "COMT": "commodities",
            "USO": "commodities",
            "UNG": "commodities",
            "DBA": "commodities",
        }.get(ticker)
        if direct:
            return direct
    inferred = infer_asset_class_from_text(
        symbol,
        ticker,
        metadata.get("name"),
        metadata.get("displayName"),
        metadata.get("securityType"),
        metadata.get("marketSector"),
    )
    if inferred in ASSET_CLASSES:
        return inferred
    sector = str(metadata.get("marketSector") or "").lower()
    security_type = str(metadata.get("securityType") or "").lower()
    if "equity" in sector or "common stock" in security_type:
        return "equity"
    if "corp" in security_type or "common" in security_type:
        return "equity"
    return "equity"


def infer_asset_class(symbol: str) -> str:
    return infer_asset_class_from_metadata({"instrumentId": symbol, "ticker": symbol}, symbol)


@dataclass(frozen=True)
class PricePoint:
    day: str
    close: float


class BacktestError(Exception):
    pass


class FeatureLockedError(BacktestError):
    def __init__(self, feature: str, plan: str) -> None:
        self.feature = feature
        self.plan = plan
        locked = locked_feature_payload(feature)
        super().__init__(locked["message"])


def normalize_plan(value: Any) -> str:
    plan = str(value or os.getenv("APP_USER_PLAN", "PLUS")).strip().upper()
    return plan if plan in PLAN_FEATURES else "PLUS"


def effective_user_plan(requested_plan: Any) -> str:
    requested = normalize_plan(requested_plan)
    if ENFORCE_SERVER_USER_PLAN and SERVER_USER_PLAN in PLAN_FEATURES:
        return SERVER_USER_PLAN
    return requested


def public_internal_error(exc: BaseException) -> dict[str, str]:
    sys.stderr.write(f"[internal-error] {type(exc).__name__}: {exc}\n")
    return {
        "error": "Errore interno del server locale. Riprova o controlla il terminale per il dettaglio tecnico.",
        "code": "INTERNAL_ERROR",
    }


def has_feature(plan: str, feature: str) -> bool:
    value = PLAN_FEATURES[normalize_plan(plan)].get(feature, False)
    return value is True or value == "basic" or value == "full" or value == "advanced" or value == "unlimited"


def feature_value(plan: str, feature: str) -> Any:
    return PLAN_FEATURES[normalize_plan(plan)].get(feature)


def locked_feature_payload(feature: str) -> dict[str, Any]:
    meta = FEATURE_MESSAGES.get(feature, {})
    return {
        "locked": True,
        "error": "FEATURE_LOCKED",
        "feature": feature,
        "name": meta.get("name", feature),
        "requiredPlan": meta.get("requiredPlan", "PLUS"),
        "message": meta.get("message", f"Funzionalita disponibile dal piano {meta.get('requiredPlan', 'PLUS')}."),
        "previewMessage": meta.get("previewMessage", "Questa funzionalita e disponibile con un piano superiore."),
    }


def get_available_standard_portfolios(user_plan: str, *, include_locked_preview: bool = False) -> list[dict[str, Any]]:
    plan = normalize_plan(user_plan).lower()
    items: list[dict[str, Any]] = []
    for portfolio in STANDARD_PORTFOLIOS:
        item = dict(portfolio)
        item["holdings"] = [dict(holding) for holding in portfolio.get("holdings", [])]
        item["locked"] = False
        if plan == "free":
            item["locked"] = True
            if include_locked_preview:
                items.append(item)
            continue
        if plan == "plus" and item.get("plan") == "advanced":
            item["locked"] = True
            if include_locked_preview:
                items.append(item)
            continue
        items.append(item)
    return items


def horizon_bucket_from_years(years: Any) -> str:
    try:
        value = int(float(years))
    except (TypeError, ValueError):
        value = 10
    if value <= 1:
        return "very_short"
    if value <= 4:
        return "short"
    if value <= 8:
        return "medium"
    if value <= 15:
        return "long"
    return "very_long"


def risk_tolerance_from_profile(value: Any) -> str:
    risk = str(value or "balanced").strip().lower()
    return {
        "conservative": "low",
        "prudente": "low",
        "low": "low",
        "very_low": "very_low",
        "balanced": "balanced",
        "medium": "balanced",
        "moderate": "balanced",
        "high": "high",
        "aggressive": "high",
        "very_high": "very_high",
    }.get(risk, "balanced")


def loss_bucket_from_value(value: Any) -> str:
    try:
        loss = float(value)
    except (TypeError, ValueError):
        loss = 0.2
    if loss > 1:
        loss = loss / 100
    if loss <= 0.05:
        return "0_5"
    if loss <= 0.10:
        return "5_10"
    if loss <= 0.20:
        return "10_20"
    if loss <= 0.30:
        return "20_30"
    return "30_plus"


def investment_experience_from_profile(value: Any) -> str:
    experience = str(value or "base").strip().lower()
    return {
        "beginner": "base",
        "base": "base",
        "intermediate": "intermediate",
        "advanced": "advanced",
    }.get(experience, "base")


def calculate_risk_capacity_score(input_data: dict[str, Any]) -> int:
    score = 0
    score += {"very_short": 5, "short": 15, "medium": 28, "long": 38, "very_long": 45}.get(input_data.get("investmentHorizon"), 25)
    score += {"high": 5, "medium": 18, "low": 30}.get(input_data.get("liquidityNeed"), 15)
    score += 10 if float(input_data.get("monthlyPAC") or 0) > 0 else 4
    score += {"base": 5, "intermediate": 10, "advanced": 15}.get(input_data.get("investmentExperience"), 5)
    return score_component(score)


def calculate_risk_tolerance_score(input_data: dict[str, Any]) -> int:
    score = 0
    score += {"very_low": 5, "low": 15, "balanced": 28, "high": 38, "very_high": 45}.get(input_data.get("declaredRiskTolerance"), 25)
    score += {"0_5": 5, "5_10": 18, "10_20": 32, "20_30": 45, "30_plus": 55}.get(input_data.get("maxTemporaryLoss"), 25)
    return score_component(score)


def calculate_goal_aggressiveness_score(goal_priority: Any) -> int:
    return {
        "capital_protection": 15,
        "house_future_expense": 25,
        "periodic_income": 40,
        "capital_growth": 65,
        "retirement_long_term": 75,
    }.get(str(goal_priority or ""), 50)


def apply_hard_risk_caps(score: float, input_data: dict[str, Any]) -> float:
    capped = score
    if input_data.get("investmentHorizon") == "very_short":
        capped = min(capped, 20)
    if input_data.get("investmentHorizon") == "short":
        capped = min(capped, 35)
    if input_data.get("maxTemporaryLoss") == "0_5":
        capped = min(capped, 20)
    if input_data.get("maxTemporaryLoss") == "5_10":
        capped = min(capped, 35)
    if input_data.get("maxTemporaryLoss") == "10_20":
        capped = min(capped, 55)
    if input_data.get("liquidityNeed") == "high":
        capped = min(capped, 35)
    if input_data.get("goalPriority") == "house_future_expense":
        capped = min(capped, 45)
    if input_data.get("goalPriority") == "capital_protection":
        capped = min(capped, 35)
    if input_data.get("investmentExperience") == "base" and capped > 75:
        capped = 75
    return capped


def map_risk_score_to_investor_profile(final_risk_score: float) -> str:
    if final_risk_score <= 20:
        return "very_defensive"
    if final_risk_score <= 40:
        return "defensive"
    if final_risk_score <= 60:
        return "moderate"
    if final_risk_score <= 75:
        return "dynamic"
    return "aggressive"


def passes_standard_portfolio_hard_exclusions(portfolio: dict[str, Any], input_data: dict[str, Any]) -> bool:
    if input_data.get("userPlan") == "free":
        return False
    if input_data.get("userPlan") == "plus" and portfolio.get("plan") == "advanced":
        return False
    risk_level = int(portfolio.get("riskLevel") or 0)
    if input_data.get("investmentHorizon") == "very_short" and risk_level > 1:
        return False
    if input_data.get("investmentHorizon") == "short" and risk_level > 2:
        return False
    if input_data.get("maxTemporaryLoss") == "0_5" and risk_level > 1:
        return False
    if input_data.get("maxTemporaryLoss") == "5_10" and risk_level > 2:
        return False
    if input_data.get("maxTemporaryLoss") == "10_20" and risk_level > 3:
        return False
    if input_data.get("goalPriority") in {"capital_protection", "house_future_expense"} and portfolio.get("category") in {"aggressive", "growth", "thematic"}:
        return False
    if input_data.get("investmentExperience") == "base" and portfolio.get("category") == "thematic":
        return False
    return True


def calculate_standard_risk_match(portfolio: dict[str, Any], final_investor_profile: str) -> int:
    target = {"very_defensive": 1, "defensive": 2, "moderate": 3, "dynamic": 4, "aggressive": 5}.get(final_investor_profile, 3)
    distance = abs(target - int(portfolio.get("riskLevel") or 0))
    if distance == 0:
        return 100
    if distance == 1:
        return 75
    if distance == 2:
        return 40
    return 0


def calculate_standard_horizon_match(portfolio: dict[str, Any], investment_horizon: str) -> int:
    risk = int(portfolio.get("riskLevel") or 0)
    if investment_horizon == "very_short":
        return 100 if risk == 1 else 0
    if investment_horizon == "short":
        return 100 if risk == 1 else 85 if risk == 2 else 0
    if investment_horizon == "medium":
        return 100 if risk in {2, 3} else 65 if risk in {1, 4} else 30
    if investment_horizon == "long":
        return 100 if risk in {3, 4} else 75 if risk in {2, 5} else 40
    if investment_horizon == "very_long":
        return 100 if risk in {4, 5} else 80 if risk == 3 else 50 if risk == 2 else 30
    return 50


def calculate_standard_goal_match(portfolio: dict[str, Any], goal_priority: str) -> int:
    name = str(portfolio.get("name") or "")
    category = str(portfolio.get("category") or "")
    goal_map = {
        "capital_protection": {
            "Ultra Difensivo Obbligazionario": 100,
            "Difensivo Globale 25/75": 95,
            "Prudente Globale Distribuzione": 85,
            "Difensivo Stabilità & Oro": 80,
            "Governativo Breve USD/EUR": 90,
        },
        "house_future_expense": {
            "Ultra Difensivo Obbligazionario": 95,
            "Difensivo Globale 25/75": 90,
            "Prudente Globale Distribuzione": 80,
            "Difensivo Stabilità & Oro": 75,
            "Governativo Breve USD/EUR": 90,
        },
        "periodic_income": {
            "Dividend Prudente USA": 90,
            "Income Europa & Dividendi": 100,
            "Income Bond Dinamico": 85,
            "Prudente Globale Distribuzione": 70,
            "Difensivo Stabilità & Oro": 60,
        },
        "capital_growth": {
            "Moderato Globale USA Tilt": 100,
            "Moderato World Cash & Gold": 90,
            "Moderato Barbell Globale": 85,
            "Growth & Value Globale": 90,
            "Azionario Globale Aggressivo": 85,
            "Aggressivo Multi-Asset Growth": 80,
        },
        "retirement_long_term": {
            "Moderato Globale USA Tilt": 85,
            "Moderato World Cash & Gold": 80,
            "Moderato Barbell Globale": 85,
            "Growth & Value Globale": 95,
            "Azionario Globale Aggressivo": 100,
            "Aggressivo Multi-Asset Growth": 90,
            "Growth Small Cap & US": 85,
        },
    }
    if goal_priority in goal_map and name in goal_map[goal_priority]:
        return goal_map[goal_priority][name]
    if goal_priority == "capital_protection":
        return 75 if category in {"defensive", "bond"} else 20
    if goal_priority == "house_future_expense":
        return 75 if category in {"defensive", "bond"} else 50 if category == "moderate" else 20
    if goal_priority == "periodic_income":
        return 90 if category == "income" else 85 if "dividend" in name.lower() else 55 if category == "defensive" else 35
    if goal_priority == "capital_growth":
        return 85 if category in {"moderate", "growth"} else 80 if category == "aggressive" else 50
    if goal_priority == "retirement_long_term":
        return 90 if category in {"aggressive", "growth"} else 75 if category == "moderate" else 45
    return 50


def standard_loss_level_to_number(loss: str) -> int:
    return {"0_5": 1, "5_10": 2, "10_20": 3, "20_30": 4, "30_plus": 5}.get(loss, 3)


def standard_required_loss_level(portfolio: dict[str, Any]) -> str:
    return {
        "Ultra Difensivo Obbligazionario": "0_5",
        "Difensivo Globale 25/75": "5_10",
        "Difensivo Stabilità & Oro": "5_10",
        "Prudente Globale Distribuzione": "10_20",
        "Dividend Prudente USA": "10_20",
        "Moderato Globale USA Tilt": "10_20",
        "Moderato World Cash & Gold": "10_20",
        "Moderato Barbell Globale": "10_20",
        "Azionario Globale Aggressivo": "30_plus",
        "Tech Growth & EM ex-China": "30_plus",
        "Aggressivo Multi-Asset Growth": "20_30",
        "Growth & Value Globale": "20_30",
        "Income Bond Dinamico": "10_20",
        "Income Europa & Dividendi": "10_20",
        "Growth Small Cap & US": "30_plus",
        "Satellite Value Globale": "20_30",
        "Tech & Future Infrastructure": "30_plus",
        "Satellite AI & Big Data": "30_plus",
        "Industrials & Blue Chips": "20_30",
        "Satellite Space Economy": "30_plus",
        "Satellite Defense": "30_plus",
        "Megatrends Industriale & Tech": "30_plus",
        "Governativo Breve USD/EUR": "5_10",
        "ESG USA & Industrials": "20_30",
        "USA Climate Aligned": "20_30",
    }.get(str(portfolio.get("name") or ""), "10_20")


def calculate_standard_loss_match(portfolio: dict[str, Any], max_temporary_loss: str) -> int:
    required = standard_loss_level_to_number(standard_required_loss_level(portfolio))
    user = standard_loss_level_to_number(max_temporary_loss)
    if user >= required:
        return 100
    if user == required - 1:
        return 50
    return 0


def calculate_standard_experience_match(portfolio: dict[str, Any], investment_experience: str) -> int:
    category = str(portfolio.get("category") or "")
    risk = int(portfolio.get("riskLevel") or 0)
    if investment_experience == "advanced":
        return 100
    if investment_experience == "intermediate":
        return 65 if category in {"thematic", "growth"} else 100
    if investment_experience == "base":
        if category == "thematic":
            return 20
        if category == "growth":
            return 40
        if risk >= 5:
            return 65
        return 100
    return 70


def calculate_standard_portfolio_match_score(portfolio: dict[str, Any], input_data: dict[str, Any]) -> int:
    score = (
        calculate_standard_risk_match(portfolio, input_data["finalInvestorProfile"]) * 0.30
        + calculate_standard_horizon_match(portfolio, input_data["investmentHorizon"]) * 0.20
        + calculate_standard_goal_match(portfolio, input_data["goalPriority"]) * 0.30
        + calculate_standard_loss_match(portfolio, input_data["maxTemporaryLoss"]) * 0.15
        + calculate_standard_experience_match(portfolio, input_data["investmentExperience"]) * 0.05
    )
    return score_component(score)


def build_standard_recommendation_explanation(params: dict[str, Any]) -> dict[str, Any]:
    portfolio = params["portfolio"]
    bullets = [
        f"Profilo finale stimato: {params['finalInvestorProfile'].replace('_', ' ')}",
        f"Orizzonte indicato: {params['investmentHorizon'].replace('_', ' ')}",
        f"Obiettivo principale: {params['goalPriority'].replace('_', ' ')}",
        f"Perdita temporanea massima sopportabile: {params['maxTemporaryLoss'].replace('_', '-')}%",
        f"Esperienza dichiarata: {params['investmentExperience']}",
        f"Portfolio suggerito: {portfolio['name']}",
    ]
    return {
        "summary": (
            "Questo portfolio standard appare più coerente con il profilo indicato perché il check-up o la "
            "coerenza con l'obiettivo sono inferiori a 60/100."
        ),
        "reason": (
            "Il modello selezionato ha un livello di rischio più allineato alla perdita temporanea massima "
            "dichiarata, all'orizzonte temporale e alla priorità dell'obiettivo."
        ),
        "bullets": bullets,
        "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
    }


def get_recommended_standard_portfolio(input_data: dict[str, Any]) -> dict[str, Any]:
    health = input_data.get("portfolioHealthScore")
    goal = input_data.get("goalCompatibilityScore")
    should_suggest = True
    if isinstance(health, (int, float)) and isinstance(goal, (int, float)):
        should_suggest = float(health) < 60 or float(goal) < 60
    if not should_suggest:
        return {"shouldSuggest": False, "reason": "Il portfolio risulta sufficientemente coerente con il profilo indicato."}

    plan = str(input_data.get("userPlan") or "plus").lower()
    if plan == "free":
        return {
            "shouldSuggest": True,
            "locked": True,
            "suggestedUpgrade": "plus",
            "message": "Sblocca i portfolio standard con il piano Plus.",
            "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
        }

    risk_capacity = calculate_risk_capacity_score(input_data)
    risk_tolerance = calculate_risk_tolerance_score(input_data)
    goal_aggressiveness = calculate_goal_aggressiveness_score(input_data.get("goalPriority"))
    final_risk_score = apply_hard_risk_caps(
        risk_capacity * 0.30 + risk_tolerance * 0.45 + goal_aggressiveness * 0.25,
        input_data,
    )
    final_profile = map_risk_score_to_investor_profile(final_risk_score)
    scoring_input = {**input_data, "finalInvestorProfile": final_profile}

    scored: list[dict[str, Any]] = []
    for portfolio in get_available_standard_portfolios(plan):
        if not passes_standard_portfolio_hard_exclusions(portfolio, scoring_input):
            continue
        scored.append({"portfolio": portfolio, "score": calculate_standard_portfolio_match_score(portfolio, scoring_input)})
    scored.sort(key=lambda item: item["score"], reverse=True)

    advanced_locked = None
    if plan == "plus":
        advanced_scored: list[dict[str, Any]] = []
        for portfolio in get_available_standard_portfolios("advanced"):
            if portfolio.get("plan") != "advanced":
                continue
            if passes_standard_portfolio_hard_exclusions(portfolio, {**scoring_input, "userPlan": "advanced"}):
                advanced_scored.append({"portfolio": portfolio, "score": calculate_standard_portfolio_match_score(portfolio, scoring_input)})
        advanced_scored.sort(key=lambda item: item["score"], reverse=True)
        if advanced_scored and (not scored or advanced_scored[0]["score"] > scored[0]["score"]):
            advanced_locked = {**advanced_scored[0], "locked": True, "message": "Il portfolio più coerente appartiene al piano Advanced. Ti mostriamo anche la migliore alternativa disponibile nel tuo piano."}

    if not scored:
        fallback = next((p for p in get_available_standard_portfolios(plan) if p.get("name") == "Ultra Difensivo Obbligazionario"), None)
        if not fallback:
            return {"shouldSuggest": True, "fallbackRequired": True, "message": "Non è stato trovato un portfolio standard coerente nel piano corrente.", "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER}
        scored = [{"portfolio": fallback, "score": 50}]

    best = scored[0]
    explanation = build_standard_recommendation_explanation(
        {
            "portfolio": best["portfolio"],
            "finalInvestorProfile": final_profile,
            "finalRiskScore": final_risk_score,
            "riskCapacityScore": risk_capacity,
            "riskToleranceScore": risk_tolerance,
            "goalAggressivenessScore": goal_aggressiveness,
            "investmentHorizon": input_data.get("investmentHorizon"),
            "goalPriority": input_data.get("goalPriority"),
            "maxTemporaryLoss": input_data.get("maxTemporaryLoss"),
            "liquidityNeed": input_data.get("liquidityNeed"),
            "investmentExperience": input_data.get("investmentExperience"),
            "portfolioHealthScore": input_data.get("portfolioHealthScore"),
            "goalCompatibilityScore": input_data.get("goalCompatibilityScore"),
        }
    )
    return {
        "shouldSuggest": True,
        "finalRiskScore": round(final_risk_score),
        "finalInvestorProfile": final_profile,
        "recommendedPortfolio": best["portfolio"],
        "matchScore": best["score"],
        "alternatives": scored[1:4],
        "lockedAdvancedCandidate": advanced_locked,
        "explanation": explanation,
        "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
    }


def standard_recommendation_input(profile: dict[str, Any], health: dict[str, Any], user_plan: str) -> dict[str, Any]:
    components = health.get("components") if isinstance(health.get("components"), dict) else {}
    goal_score = first_number(
        components.get("goalCoherence"),
        components.get("baseCoherence"),
        components.get("advancedGoalCoherence"),
        health.get("overall"),
    )
    return {
        "initialCapital": profile.get("capital"),
        "investmentHorizon": horizon_bucket_from_years(profile.get("horizonYears")),
        "goalPriority": profile.get("goalPriority") or "capital_growth",
        "personalGoalText": profile.get("objective"),
        "declaredRiskTolerance": risk_tolerance_from_profile(profile.get("riskPreference")),
        "maxTemporaryLoss": loss_bucket_from_value(profile.get("maxTemporaryLoss")),
        "investmentExperience": investment_experience_from_profile(profile.get("experienceLevel")),
        "monthlyPAC": profile.get("monthlyPac"),
        "liquidityNeed": profile.get("liquidityNeed") or "low",
        "portfolioHealthScore": health.get("overall"),
        "goalCompatibilityScore": goal_score,
        "userPlan": normalize_plan(user_plan).lower(),
    }


def enforce_feature(plan: str, feature: str) -> None:
    if not has_feature(plan, feature):
        raise FeatureLockedError(feature, plan)


def explanation_level(plan: str) -> str:
    return {"FREE": "basic", "PLUS": "complete", "ADVANCED": "advanced"}[normalize_plan(plan)]


def classify_openai_error(status: int | None = None, error: BaseException | None = None, detail: str = "") -> str:
    if status in {401, 403}:
        return "auth"
    if status == 429:
        return "rate_limit"
    if status and status >= 500:
        return "server"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, urllib.error.URLError):
        reason = str(getattr(error, "reason", error)).lower()
        return "timeout" if "timed out" in reason or "timeout" in reason else "network"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "schema"
    if "timed out" in detail.lower() or "timeout" in detail.lower():
        return "timeout"
    return "unknown"


def should_retry_openai(status: int | None = None, category: str = "") -> bool:
    return status in {429, 500, 502, 503, 504} or category in {"network", "timeout", "server", "rate_limit"}


def openai_status_from_error(error: str) -> str:
    return "openai" if not error else "fallback"


def parse_openai_json_response(payload: dict[str, Any], context: str) -> dict[str, Any]:
    text = openai_text_from_response(payload)
    if not text:
        raise ValueError(f"{context}: OpenAI ha risposto senza testo.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        if "Unterminated string" in str(exc) or exc.pos >= max(0, len(text) - 5):
            raise ValueError(
                f"{context}: risposta OpenAI incompleta o troncata. Riprova: il sistema richiederà un JSON più breve."
            ) from exc
        raise ValueError(f"{context}: risposta OpenAI non è JSON valido.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{context}: OpenAI non ha restituito un oggetto JSON.")
    return parsed


def load_ai_explanation_cache() -> dict[str, Any]:
    payload = load_json_file(AI_EXPLANATION_CACHE_FILE, {})
    return payload if isinstance(payload, dict) else {}


def save_ai_explanation_cache(cache: dict[str, Any]) -> None:
    try:
        pruned = prune_mapping(cache, AI_EXPLANATION_CACHE_MAX_ITEMS, "createdAt")
        atomic_write_json(AI_EXPLANATION_CACHE_FILE, pruned, ensure_ascii=False)
    except OSError:
        return


def ai_explanation_cache_key(request_payload: dict[str, Any]) -> str:
    stable = {
        "version": AI_EXPLANATION_CACHE_VERSION,
        "model": OPENAI_EXPLANATION_MODEL,
        "payload": request_payload,
    }
    body = json.dumps(stable, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def get_cached_ai_explanation(request_payload: dict[str, Any]) -> dict[str, str] | None:
    if AI_EXPLANATION_CACHE_TTL_SECONDS <= 0:
        return None
    cache = load_ai_explanation_cache()
    item = cache.get(ai_explanation_cache_key(request_payload))
    if not isinstance(item, dict):
        return None
    if time.time() - float(item.get("createdAt", 0)) > AI_EXPLANATION_CACHE_TTL_SECONDS:
        return None
    explanation = item.get("explanation")
    if not isinstance(explanation, dict):
        return None
    try:
        return validate_ai_explanation(explanation)
    except ValueError:
        return None


def set_cached_ai_explanation(request_payload: dict[str, Any], explanation: dict[str, str]) -> None:
    if AI_EXPLANATION_CACHE_TTL_SECONDS <= 0:
        return
    cache = load_ai_explanation_cache()
    cache[ai_explanation_cache_key(request_payload)] = {
        "createdAt": time.time(),
        "model": OPENAI_EXPLANATION_MODEL,
        "version": AI_EXPLANATION_CACHE_VERSION,
        "explanation": explanation,
    }
    save_ai_explanation_cache(cache)


def openai_responses_request(body: dict[str, Any], timeout: float | None = None) -> tuple[dict[str, Any] | None, str, str]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None, "OPENAI_API_KEY non configurata nel server.", "disabled"
    request = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    timeout_value = OPENAI_TIMEOUT_SECONDS if timeout is None else timeout
    last_error = ""
    last_category = "unknown"
    for attempt in range(max(0, OPENAI_MAX_RETRIES) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_value) as response:
                return json.loads(response.read().decode("utf-8")), "", ""
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            last_category = classify_openai_error(exc.code, detail=detail)
            last_error = f"Errore OpenAI {exc.code}: {detail[:220] or exc.reason}"
            retry = should_retry_openai(exc.code, last_category)
        except urllib.error.URLError as exc:
            last_category = classify_openai_error(error=exc)
            last_error = f"Connessione OpenAI non riuscita: {exc.reason}"
            retry = should_retry_openai(category=last_category)
        except TimeoutError as exc:
            last_category = classify_openai_error(error=exc)
            last_error = "Timeout OpenAI."
            retry = should_retry_openai(category=last_category)
        except json.JSONDecodeError as exc:
            last_category = "schema"
            last_error = f"Risposta OpenAI non valida: {exc}"
            retry = False
        if not retry or attempt >= OPENAI_MAX_RETRIES:
            break
        time.sleep(OPENAI_RETRY_BASE_SECONDS * (2 ** attempt))
    return None, last_error, last_category


def euro_text(value: float) -> str:
    return f"{value:,.0f} euro"


def sanitize_ai_explanation_text(text: str) -> str:
    sanitized = str(text or "")
    sanitized = re.sub(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]", "", sanitized)
    replacements = {
        "compra": "valuta l'esposizione a",
        "Compra": "Valuta l'esposizione a",
        "vendi": "riduci l'esposizione a",
        "Vendi": "Riduci l'esposizione a",
        "devi comprare": "può valutare l'esposizione a",
        "Devi comprare": "Può valutare l'esposizione a",
        "devi vendere": "può valutare una minore esposizione a",
        "Devi vendere": "Può valutare una minore esposizione a",
        "devi": "può",
        "Devi": "Può",
        "garantito": "non certo",
        "Garantito": "Non certo",
    }
    for source, target in replacements.items():
        sanitized = sanitized.replace(source, target)
    sanitized = sanitized.replace("può valuta", "può valutare").replace("Può valuta", "Può valutare")
    accent_replacements = {
        "perche": "perché",
        "Perche": "Perché",
        "puo": "può",
        "piu": "più",
        "gia": "già",
        "cioe": "cioè",
        "probabilita": "probabilità",
        "volatilita": "volatilità",
        "finalita": "finalità",
        "unita": "unità",
    }
    for source, target in accent_replacements.items():
        sanitized = re.sub(rf"\b{source}\b", target, sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) >= 90 and not re.search(r"[.!?)]$", sanitized):
        last_stop = max(sanitized.rfind("."), sanitized.rfind("!"), sanitized.rfind("?"))
        if last_stop >= 50:
            sanitized = sanitized[: last_stop + 1].strip()
    return sanitized


def plain_language_metric_names(text: str) -> str:
    replacements = [
        (r"\bSharpe\s+Ratio\b", "rapporto rischio-rendimento"),
        (r"\bSharpe\b", "rapporto rischio-rendimento"),
        (r"\bSortino\s+Ratio\b", "rapporto rendimento-rischio negativo"),
        (r"\bSortino\b", "rapporto rendimento-rischio negativo"),
        (r"\bCAGR\b", "crescita media annua"),
        (r"\bMax\s+Drawdown\b", "peggiore perdita temporanea"),
        (r"\bdrawdown\b", "perdita temporanea"),
        (r"\bDrawdown\b", "Perdita temporanea"),
        (r"\bvolatilità\b", "oscillazione del portafoglio"),
        (r"\bVolatilità\b", "Oscillazione del portafoglio"),
        (r"\bcorrelazione\b", "movimento comune tra strumenti"),
        (r"\bCorrelazione\b", "Movimento comune tra strumenti"),
    ]
    result = text
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE if pattern.startswith("\\bS") or pattern.startswith("\\bCAGR") or pattern.startswith("\\bMax") else 0)
    return result


def complete_what_to_watch_text(text: str) -> str:
    cleaned = sanitize_ai_explanation_text(plain_language_metric_names(text))
    if not cleaned:
        return cleaned
    imperative_verbs = {
        "guarda": "guardare",
        "osserva": "osservare",
        "controlla": "controllare",
        "valuta": "valutare",
        "confronta": "confrontare",
        "verifica": "verificare",
        "leggi": "leggere",
    }
    imperative_match = re.match(r"^(guarda|osserva|controlla|valuta|confronta|verifica|leggi)\b\s*(.*)$", cleaned, re.IGNORECASE)
    if imperative_match:
        verb = imperative_verbs.get(imperative_match.group(1).lower(), imperative_match.group(1).lower())
        obj = imperative_match.group(2).strip()
        sentence = f"L'utente deve {verb}{f' {obj}' if obj else ''}".strip()
        if sentence and not re.search(r"[.!?)]$", sentence):
            sentence += "."
        return sentence
    if re.search(r"\b(l'utente|utente|investitore|il portafoglio|la tabella|il grafico|il sistema)\b.+\b(deve|può|mostra|confronta|indica|evidenzia|osserva|valuta)\b", cleaned, re.IGNORECASE):
        return cleaned

    raw_parts = re.split(r"\s+-\s+|[;\n•]+", cleaned)
    parts = [part.strip(" .;-") for part in raw_parts if part.strip(" .;-")]
    if len(parts) <= 1:
        parts = [cleaned.strip(" .;-")]

    completed: list[str] = []
    for part in parts[:3]:
        lower = part.lower()
        if "distanza" in lower and "curva efficiente" in lower:
            sentence = "L'utente deve osservare quanto il punto del portafoglio è distante dalla curva efficiente."
        elif ("variazione" in lower or "differenza" in lower) and ("portafoglio inserito" in lower or "ottimizzato" in lower):
            sentence = "L'utente deve confrontare come cambiano rischio e rendimento tra portafoglio inserito e portafoglio di confronto."
        elif "riduzione del rischio" in lower and "rendimento" in lower:
            sentence = "L'utente deve valutare se la riduzione del rischio compensa il cambiamento di rendimento."
        elif "benchmark" in lower:
            sentence = "L'utente deve confrontare il portafoglio analizzato con il benchmark standard."
        elif "perdita" in lower or "drawdown" in lower:
            sentence = "L'utente deve osservare quanto il portafoglio può scendere nei periodi peggiori."
        elif "probabilità" in lower or "probabilita" in lower:
            sentence = "L'utente deve osservare quante simulazioni raggiungono l'obiettivo o restano sopra il capitale iniziale."
        elif "scenario" in lower or "shock" in lower:
            sentence = "L'utente deve osservare quale scenario genera l'impatto più negativo sul portafoglio."
        elif "correlazione" in lower or "muovono insieme" in lower or "movimento comune" in lower:
            sentence = "L'utente deve osservare se gli strumenti si muovono insieme nei periodi difficili."
        elif re.search(r"^(guarda|osserva|controlla|valuta|confronta|verifica|leggi)\b", part, re.IGNORECASE):
            match = re.match(r"^(guarda|osserva|controlla|valuta|confronta|verifica|leggi)\b\s*(.*)$", part, re.IGNORECASE)
            verb = imperative_verbs.get(match.group(1).lower(), match.group(1).lower())
            obj = match.group(2).strip()
            sentence = f"L'utente deve {verb}{f' {obj}' if obj else ''}".strip()
        elif re.search(r"\b(deve|può|mostra|confronta|indica|evidenzia|osserva|valuta|guarda|controlla|verifica|leggi)\b", lower):
            sentence = part
        else:
            sentence = f"L'utente deve osservare {part}"
        sentence = sentence.strip()
        if sentence and not re.search(r"[.!?)]$", sentence):
            sentence += "."
        completed.append(sentence)
    return " ".join(completed)


def complete_possible_improvement_text(text: str) -> str:
    cleaned = sanitize_ai_explanation_text(plain_language_metric_names(text))
    if not cleaned:
        cleaned = "Il portafoglio presenta un'area da approfondire rispetto a rischio, diversificazione o coerenza con il profilo."
    forbidden_patterns = [
        r"\b[aA]ggiungi\s+\d",
        r"\b[rR]iduci\s+[^.]{0,40}\s+al\s+\d",
        r"\b[pP]orta\s+[^.]{0,40}\s+al\s+\d",
        r"\b[aA]umenta\s+[^.]{0,40}\s+al\s+\d",
    ]
    for pattern in forbidden_patterns:
        cleaned = re.sub(pattern, "Valuta una modifica simulata nel Piano di miglioramento", cleaned)
    if "Piano di miglioramento" not in cleaned:
        cleaned = (
            f"{cleaned} Una direzione possibile è valutare componenti più difensive, maggiore diversificazione o minore "
            "dipendenza dagli asset più volatili, in base alla sezione analizzata. Le modifiche precise su pesi e strumenti "
            "vengono mostrate nel Piano di miglioramento."
        )
    return cleaned


GENERIC_AI_PHRASES = [
    "questo confronto aiuta",
    "questa sezione mostra informazioni utili",
    "la sezione mostra informazioni utili",
    "l'ottimizzazione rende il portafoglio",
    "ottimizzazione rende il portafoglio",
    "coerente con il rischio sopportabile",
    "il dato è interessante",
    "il dato e interessante",
]


def contains_generic_ai_phrase(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    return any(phrase in normalized for phrase in GENERIC_AI_PHRASES)


def explanation_payload_value(payload: dict[str, Any], field: str) -> str:
    if payload.get(field) not in (None, ""):
        return str(payload.get(field, ""))
    for alias in AI_EXPLANATION_FIELD_ALIASES.get(field, []):
        if payload.get(alias) not in (None, ""):
            return str(payload.get(alias, ""))
    return ""


def add_explanation_legacy_aliases(result: dict[str, str]) -> dict[str, str]:
    result["meaning_for_user"] = result.get("meaning", "")
    result["what_the_numbers_mean"] = result.get("meaning", "")
    result["what_to_look_at"] = result.get("what_to_watch", "")
    result["main_risk"] = result.get("main_weakness", "")
    result["simple_summary"] = result.get("summary", "")
    result["technical_note"] = result.get("technical_detail", "")
    return result


def deduplicate_explanation_fields(result: dict[str, str]) -> dict[str, str]:
    fallbacks = {
        "summary": "Il portafoglio va letto guardando insieme crescita, oscillazioni e perdite temporanee.",
        "meaning": "Il dato mostra se il percorso del portafoglio è compatibile con capitale, orizzonte e rischio dichiarato.",
        "what_to_watch": "Osserva la differenza tra portafoglio inserito e portafoglio efficiente, soprattutto nelle fasi negative.",
        "main_weakness": "Il punto debole principale emerge dalla metrica della sezione che mostra più instabilità o perdita.",
        "main_strength": "Il punto di forza principale è la parte del portafoglio che sostiene meglio crescita, stabilità o diversificazione.",
        "possible_improvement": "Una possibile area da valutare è ridurre la dipendenza da pochi strumenti o da un unico scenario di mercato.",
        "concrete_example": "Esempio pratico: una perdita del 10% su 10,000 euro porta temporaneamente il valore a circa 9,000 euro.",
        "technical_detail": "Dettaglio tecnico: questa sezione interpreta solo le metriche già calcolate dal backend.",
    }
    seen: set[str] = set()
    for field in AI_EXPLANATION_FIELDS:
        if field == "disclaimer":
            continue
        normalized = " ".join(result.get(field, "").lower().split())
        if normalized and (normalized in seen or contains_generic_ai_phrase(normalized)):
            result[field] = fallbacks.get(field, result[field])
            normalized = " ".join(result[field].lower().split())
        if normalized:
            seen.add(normalized)
    return result


def validate_ai_explanation(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Spiegazione OpenAI non valida.")
    result: dict[str, str] = {}
    for field in AI_EXPLANATION_FIELDS:
        result[field] = sanitize_ai_explanation_text(explanation_payload_value(payload, field).strip())
        if field != "technical_detail":
            result[field] = plain_language_metric_names(result[field])
        if field == "what_to_watch":
            result[field] = complete_what_to_watch_text(result[field])
        if field == "possible_improvement":
            result[field] = complete_possible_improvement_text(result[field])
    result = deduplicate_explanation_fields(result)
    if result["disclaimer"] != AI_EXPLANATION_DISCLAIMER:
        result["disclaimer"] = AI_EXPLANATION_DISCLAIMER
    return add_explanation_legacy_aliases(result)


def sanitize_standard_benchmark_language(result: dict[str, str], request_payload: dict[str, Any]) -> dict[str, str]:
    context = request_payload.get("finalAnalysisContext") if isinstance(request_payload.get("finalAnalysisContext"), dict) else {}
    mode = str(context.get("mode") or request_payload.get("finalAnalysisMode") or "")
    role = str(context.get("comparisonPortfolioRole") or request_payload.get("comparisonPortfolioRole") or "")
    if "standard" not in mode and "standard" not in role and "benchmark" not in role:
        return result
    replacements = {
        "portafoglio ottimale": "benchmark standard",
        "Portafoglio ottimale": "Benchmark standard",
        "portfolio ottimale": "benchmark standard",
        "Portfolio ottimale": "Benchmark standard",
        "portafoglio efficiente": "modello educativo di confronto",
        "Portafoglio efficiente": "Modello educativo di confronto",
        "portfolio efficiente": "modello educativo di confronto",
        "Portfolio efficiente": "Modello educativo di confronto",
        "migliore per te": "più coerente con alcune metriche disponibili",
        "consigliato da acquistare": "usato come confronto educativo",
    }
    for field, value in list(result.items()):
        text = value
        for source, target in replacements.items():
            text = text.replace(source, target)
        result[field] = text
    return result


def drawdown_example(metrics: dict[str, Any], portfolio_value: float) -> str:
    drawdown = float(metrics.get("maxDrawdown") or 0)
    if portfolio_value <= 0 or drawdown >= 0:
        return "Il rischio principale va letto come possibile perdita temporanea rispetto ai massimi precedenti."
    stressed_value = portfolio_value * (1 + drawdown)
    return (
        f"Con un portafoglio da {euro_text(portfolio_value)}, una perdita temporanea del {drawdown * 100:.1f}% "
        f"significherebbe vedere il valore scendere temporaneamente fino a circa {euro_text(stressed_value)}."
    )


def risk_plain_language(metrics: dict[str, Any], analysis_type: str) -> str:
    drawdown = float(metrics.get("maxDrawdown") or 0)
    probability_loss = metrics.get("probabilityLoss")
    expected_scenario_return = metrics.get("expectedPortfolioReturn")
    if analysis_type == "monte_carlo" and probability_loss is not None:
        return f"Il punto debole principale è che una parte delle simulazioni chiude sotto il capitale iniziale: in questo caso circa {float(probability_loss) * 100:.1f}% dei percorsi."
    if analysis_type in {"stress_testing", "scenario_analysis"} and expected_scenario_return is not None:
        return f"Il punto debole principale è la sensibilità del portafoglio allo scenario scelto, con un impatto stimato di {float(expected_scenario_return) * 100:.1f}%."
    if drawdown < 0:
        return f"Il punto debole principale è la peggiore perdita temporanea dal massimo al minimo: nel periodo analizzato ha raggiunto il {drawdown * 100:.1f}%."
    return "Il punto debole principale è che l'oscillazione del portafoglio può rendere il percorso meno stabile rispetto all'obiettivo dichiarato."


def local_indicator_glossary(analysis_type: str, metrics: dict[str, Any]) -> str:
    terms = list(TECHNICAL_TERMS_BY_ANALYSIS.get(analysis_type, []))
    metric_term_map = {
        "alpha": "alpha",
        "averageAbsoluteCorrelation": "averageAbsoluteCorrelation",
        "bestHedge": "bestHedge",
        "cagr": "cagr",
        "currentExpectedReturn": "expectedReturn",
        "currentSharpe": "sharpe",
        "currentVolatility": "volatility",
        "dominantFactor": "factorContribution",
        "dominantFactorShare": "factorContribution",
        "expectedPortfolioReturn": "expectedReturn",
        "finalValue": "finalValue",
        "gainPercent": "cagr",
        "maxDrawdown": "drawdown",
        "maxDrawdownDurationDays": "duration",
        "observations": "rSquared",
        "optimizedExpectedReturn": "expectedReturn",
        "optimizedSharpe": "sharpe",
        "optimizedVolatility": "volatility",
        "p5": "p5",
        "p50": "p50",
        "p95": "p95",
        "peakToTroughDays": "peakToTrough",
        "probabilityGain": "probabilitySuccess",
        "probabilityLoss": "probabilityLoss",
        "probabilitySuccess": "probabilitySuccess",
        "rSquared": "rSquared",
        "rollingReturn": "rollingReturn",
        "rollingSharpe": "rollingSharpe",
        "rollingSortino": "rollingSortino",
        "sharpe": "sharpe",
        "sortino": "sortino",
        "totalInvested": "pac",
        "volatility": "volatility",
        "worstContributor": "worstContributor",
        "worstScenarioReturn": "expectedReturn",
    }
    for metric_key, term_key in metric_term_map.items():
        if metrics.get(metric_key) is not None:
            terms.append(term_key)
    ordered_unique = list(dict.fromkeys(term for term in terms if term in LOCAL_TECHNICAL_GLOSSARY))
    if not ordered_unique:
        return "Indicatori usati: le metriche sono calcolate localmente dal backend e servono a tradurre rischio, rendimento e coerenza del portafoglio."
    definitions = " ".join(LOCAL_TECHNICAL_GLOSSARY[term] for term in ordered_unique[:12])
    return f"Indicatori usati: {definitions}"


def technical_glossary(metrics: dict[str, Any], analysis_type: str = "portfolio_overview") -> str:
    parts: list[str] = []
    if metrics.get("cagr") is not None or metrics.get("currentExpectedReturn") is not None:
        parts.append("Crescita media annua significa rendimento medio annuo composto: traduce il risultato totale in una crescita annua equivalente.")
    if metrics.get("maxDrawdown") is not None:
        parts.append("Perdita temporanea significa discesa dal massimo precedente al minimo successivo.")
    if metrics.get("sharpe") is not None or metrics.get("currentSharpe") is not None:
        parts.append("Il rapporto rischio-rendimento misura quanto rendimento storico è stato ottenuto per ogni unità di oscillazione.")
    if metrics.get("volatility") is not None or metrics.get("currentVolatility") is not None:
        parts.append("Oscillazione del portafoglio indica quanto il valore cambia nel tempo.")
    parts.append(local_indicator_glossary(analysis_type, metrics))
    parts.append("Le metriche sono calcolate dal backend con regole locali; OpenAI, quando disponibile, le interpreta soltanto.")
    return " ".join(part for part in parts if part)


def portfolio_strength_from_metrics(metrics: dict[str, Any], analysis_type: str) -> str:
    sharpe = metrics.get("sharpe") if metrics.get("sharpe") is not None else metrics.get("currentSharpe")
    cagr = metrics.get("cagr") if metrics.get("cagr") is not None else metrics.get("currentExpectedReturn")
    probability_gain = metrics.get("probabilityGain")
    expected_scenario_return = metrics.get("expectedPortfolioReturn")
    volatility = metrics.get("volatility") if metrics.get("volatility") is not None else metrics.get("currentVolatility")

    if analysis_type == "monte_carlo" and probability_gain is not None:
        return f"Il punto di forza principale del portafoglio è che nelle simulazioni chiude sopra il capitale iniziale nel {float(probability_gain) * 100:.1f}% dei percorsi."
    if analysis_type in {"stress_testing", "scenario_analysis"} and expected_scenario_return is not None:
        if float(expected_scenario_return) >= 0:
            return "Il punto di forza principale del portafoglio è la tenuta positiva nello scenario analizzato."
        return "Il punto di forza principale del portafoglio è la presenza di componenti che assorbono meglio lo shock rispetto alle altre."
    if sharpe is not None and float(sharpe) >= 0.8:
        return "Il punto di forza principale del portafoglio è un rapporto rendimento/rischio storicamente favorevole rispetto all'oscillazione assunta."
    if cagr is not None and float(cagr) > 0:
        return "Il punto di forza principale del portafoglio è una crescita storica positiva nel periodo analizzato."
    if volatility is not None and float(volatility) < 0.12:
        return "Il punto di forza principale del portafoglio è un'oscillazione storica relativamente contenuta."
    return "Il punto di forza principale del portafoglio è una composizione che può essere valutata in termini di equilibrio tra rischio, rendimento e diversificazione."


def rule_based_explanation(request: dict[str, Any]) -> dict[str, str]:
    analysis_type = str(request.get("analysisType", "portfolio_overview"))
    template = AI_EXPLANATION_TEMPLATES.get(analysis_type, AI_EXPLANATION_TEMPLATES["portfolio_overview"])
    metrics = request.get("metrics") if isinstance(request.get("metrics"), dict) else {}
    is_optimized = request.get("portfolioVariant") == "optimized"
    section_title = str(request.get("sectionTitle") or analysis_type.replace("_", " "))
    portfolio_value = float(request.get("portfolioValue") or request.get("portfolio_value") or metrics.get("initialCapital") or 0)
    horizon = int(request.get("investmentHorizonYears") or request.get("investment_horizon_years") or 0)
    risk_profile = str(request.get("riskProfile") or request.get("risk_profile") or "medio")
    drawdown_text = drawdown_example(metrics, portfolio_value)
    risk_text = risk_plain_language(metrics, analysis_type)
    sharpe = metrics.get("sharpe") if metrics.get("sharpe") is not None else metrics.get("currentSharpe")
    cagr = metrics.get("cagr") if metrics.get("cagr") is not None else metrics.get("currentExpectedReturn")
    summary = f"L'analisi {analysis_type.replace('_', ' ')} indica come il portafoglio si comporta rispetto a {template['focus']}."
    if cagr is not None:
        summary = f"In base ai dati analizzati, il portafoglio mostra una crescita annua storica/stimata di circa {float(cagr) * 100:.1f}%."
    if analysis_type == "efficient_frontier" and metrics.get("optimizedVolatility") is not None:
        summary = "Esiste una combinazione degli stessi strumenti che potrebbe migliorare il rapporto tra rischio e rendimento."
    if analysis_type == "monte_carlo" and metrics.get("probabilityGain") is not None:
        summary = f"La simulazione mostra una probabilità di chiudere sopra il capitale iniziale del {float(metrics['probabilityGain']) * 100:.1f}%."
    if analysis_type in {"stress_testing", "scenario_analysis"} and metrics.get("expectedPortfolioReturn") is not None:
        summary = f"Nello scenario analizzato l'impatto stimato sul portafoglio è {float(metrics['expectedPortfolioReturn']) * 100:.1f}%."
    if is_optimized:
        if analysis_type == "efficient_frontier":
            summary = "Il portafoglio efficiente cerca un equilibrio migliore tra crescita attesa e oscillazione usando gli stessi strumenti."
        elif analysis_type == "backtesting":
            summary = "Nel percorso storico, il portafoglio efficiente va valutato guardando crescita, perdita temporanea e rapporto rischio-rendimento."
        elif analysis_type == "monte_carlo":
            summary = "Nelle simulazioni, il portafoglio efficiente è utile se riduce gli esiti negativi plausibili o aumenta le traiettorie favorevoli."
        elif analysis_type in {"stress_testing", "scenario_analysis"}:
            summary = "Negli scenari, il portafoglio efficiente è più interessante se riduce l'impatto dello shock rispetto al portafoglio inserito."
    if is_optimized:
        meaning = (
            f"Per un profilo {risk_profile} con orizzonte {horizon or 'non indicato'} anni, osserva se il portafoglio efficiente "
            "migliora davvero il percorso: meno oscillazioni, minore perdita temporanea o migliore remunerazione del rischio. "
            "Se il vantaggio è piccolo o richiede più instabilità, va letto come miglioramento marginale."
        )
        what_to_watch = (
            "Guarda la differenza tra portafoglio inserito ed efficiente. "
            "Controlla se il rischio scende senza perdere troppa crescita. "
            "Osserva soprattutto la parte negativa del grafico o della tabella."
        )
    else:
        meaning = (
            f"Per un profilo {risk_profile} con orizzonte {horizon or 'non indicato'} anni, il dato mostra se il portafoglio "
            "sta assumendo un livello di rischio compatibile con l'obiettivo. "
            "La crescita conta, ma va letta insieme alle fasi negative."
        )
        what_to_watch = (
            "Guarda la perdita temporanea peggiore. "
            "Controlla se la crescita compensa l'oscillazione. "
            "Osserva se pochi strumenti guidano gran parte del risultato."
        )
    payload = {
        "summary": summary,
        "meaning": meaning,
        "what_to_watch": what_to_watch,
        "main_strength": portfolio_strength_from_metrics(metrics, analysis_type),
        "main_weakness": risk_text,
        "possible_improvement": (
            "Una possibile area da valutare è mantenere vincoli minimi e massimi sui pesi, così il portafoglio efficiente resta coerente con preferenze e tolleranza al rischio."
            if is_optimized
            else "Una possibile area da valutare è la diversificazione dei pesi, soprattutto se pochi strumenti guidano gran parte del risultato."
        ),
        "concrete_example": drawdown_text,
        "technical_detail": f"Dettaglio tecnico: {template['focus']}. {technical_glossary(metrics, analysis_type)}",
        "disclaimer": AI_EXPLANATION_DISCLAIMER,
    }
    if sharpe is not None:
        payload["technical_detail"] += f" Rapporto rischio-rendimento indicativo: {float(sharpe):.2f}."
    return validate_ai_explanation(payload)


def build_ai_explanation_input(
    analysis_type: str,
    profile: dict[str, Any],
    portfolio: list[dict[str, Any]],
    metrics: dict[str, Any],
    user_plan: str,
    language: str = "it",
) -> dict[str, Any]:
    display_portfolio = [
        {
            **item,
            "symbol": public_display_name_for_symbol(str(item.get("symbol", "")), str(item.get("displayName", ""))),
            "instrumentId": item.get("symbol"),
        }
        for item in portfolio
    ]
    return {
        "analysisType": analysis_type,
        "userProfile": profile,
        "portfolioComposition": display_portfolio,
        "portfolioValue": float(metrics.get("initialCapital") or profile.get("capital") or 0),
        "capitalInvested": float(metrics.get("initialCapital") or profile.get("capital") or 0),
        "investmentHorizonYears": int(profile.get("horizonYears", 0) or 0),
        "riskProfile": profile.get("riskPreference", "balanced"),
        "metrics": metrics,
        "language": language,
        "userPlan": normalize_plan(user_plan),
        "templateFocus": AI_EXPLANATION_TEMPLATES.get(analysis_type, AI_EXPLANATION_TEMPLATES["portfolio_overview"])["focus"],
    }


def call_openai_explanation(request_payload: dict[str, Any]) -> tuple[dict[str, str] | None, str]:
    cached = get_cached_ai_explanation(request_payload)
    if cached:
        return cached, ""
    body = {
        "model": OPENAI_EXPLANATION_MODEL,
        "instructions": AI_EXPLANATION_SYSTEM_PROMPT,
        "input": json.dumps(request_payload, ensure_ascii=True),
        "max_output_tokens": OPENAI_EXPLANATION_MAX_OUTPUT_TOKENS,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ai_explanation",
                "schema": AI_EXPLANATION_SCHEMA,
                "strict": True,
            }
        },
    }
    payload, error, _category = openai_responses_request(body)
    if error or payload is None:
        return None, error
    try:
        explanation = sanitize_standard_benchmark_language(
            validate_ai_explanation(parse_openai_json_response(payload, "Spiegazione OpenAI")),
            request_payload,
        )
        set_cached_ai_explanation(request_payload, explanation)
        return explanation, ""
    except ValueError as exc:
        return None, f"Spiegazione OpenAI non valida: {exc}"


def build_ai_explanation(request_payload: dict[str, Any], prefer_openai: bool = False) -> dict[str, str]:
    if prefer_openai and OPENAI_EXPLANATIONS_ENABLED:
        explanation, _error = call_openai_explanation(request_payload)
        if explanation:
            return explanation
    return rule_based_explanation(request_payload)


def validate_ai_explanation_detail(payload: dict[str, Any]) -> dict[str, str]:
    base = validate_ai_explanation(payload)
    result: dict[str, str] = {
        "title": sanitize_ai_explanation_text(str(payload.get("title") or "Come migliorare ancora")),
        "simple_summary": sanitize_ai_explanation_text(str(payload.get("simple_summary") or base["summary"])),
        "what_the_numbers_mean": base["meaning"],
        "main_risk": base["main_weakness"],
        "main_strength": base["main_strength"],
        "possible_improvement": base["possible_improvement"],
        "what_to_look_at": base["what_to_watch"]
        or "Guarda il grafico o la tabella della sezione per confrontare rischio, rendimento e contributi dei due portafogli.",
        "technical_note": base["technical_detail"],
        "disclaimer": AI_EXPLANATION_DISCLAIMER,
    }
    for field in ("title", "simple_summary", "what_the_numbers_mean", "main_risk", "main_strength", "possible_improvement", "what_to_look_at"):
        result[field] = plain_language_metric_names(result[field])
    result.update({field: base[field] for field in AI_EXPLANATION_FIELDS})
    return result


def rule_based_explanation_detail(request_payload: dict[str, Any]) -> dict[str, str]:
    base = rule_based_explanation(request_payload)
    return {
        "title": "Come migliorare ancora",
        "simple_summary": base["summary"],
        "what_the_numbers_mean": base["meaning"],
        "main_risk": base["main_weakness"],
        "main_strength": base["main_strength"],
        "possible_improvement": base["possible_improvement"],
        "what_to_look_at": base["what_to_watch"],
        "technical_note": base["technical_detail"],
        "disclaimer": AI_EXPLANATION_DISCLAIMER,
    }


def call_openai_explanation_detail(request_payload: dict[str, Any]) -> tuple[dict[str, str] | None, str]:
    body = {
        "model": OPENAI_EXPLANATION_MODEL,
        "instructions": (
            AI_EXPLANATION_SYSTEM_PROMPT
            + "\nRispondi solo con JSON conforme allo schema richiesto. Non aggiungere testo fuori dal JSON."
        ),
        "input": json.dumps(request_payload, ensure_ascii=True),
        "max_output_tokens": OPENAI_EXPLANATION_MAX_OUTPUT_TOKENS,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ai_explanation_detail",
                "schema": AI_EXPLANATION_DETAIL_SCHEMA,
                "strict": True,
            }
        },
    }
    payload, error, _category = openai_responses_request(body)
    if error or payload is None:
        return None, error
    try:
        detail = validate_ai_explanation_detail(parse_openai_json_response(payload, "Dettaglio OpenAI"))
        detail = sanitize_standard_benchmark_language(detail, request_payload)
        return detail, ""
    except ValueError as exc:
        return None, f"Dettaglio OpenAI non valido: {exc}"


def build_ai_explanation_detail(request_payload: dict[str, Any]) -> dict[str, Any]:
    technical_fallback = rule_based_explanation_detail(request_payload)
    if not OPENAI_EXPLANATIONS_ENABLED or not os.getenv("OPENAI_API_KEY"):
        return {
            "source": "openai",
            "aiStatus": "disabled",
            "aiErrorCategory": "configuration",
            "detail": {},
            "technicalFallback": {
                "technical_note": technical_fallback["technical_note"],
                "disclaimer": technical_fallback["disclaimer"],
            },
            "openaiError": "OpenAI non disponibile: configura OPENAI_API_KEY per generare questa sezione.",
        }
    detail, error = call_openai_explanation_detail(request_payload)
    if detail:
        return {
            "source": "openai",
            "aiStatus": "openai",
            "detail": detail,
            "technicalFallback": {
                "technical_note": technical_fallback["technical_note"],
                "disclaimer": technical_fallback["disclaimer"],
            },
            "openaiError": "",
        }
    return {
        "source": "openai",
        "aiStatus": "fallback",
        "aiErrorCategory": classify_openai_error(detail=error),
        "detail": {},
        "technicalFallback": {
            "technical_note": technical_fallback["technical_note"],
            "disclaimer": technical_fallback["disclaimer"],
        },
        "openaiError": error or "OpenAI non ha restituito un commento valido.",
    }


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def prune_market_data_cache(payload: dict[str, Any]) -> dict[str, Any]:
    instruments = payload.get("instruments", {}) if isinstance(payload.get("instruments"), dict) else {}
    prices = payload.get("prices", {}) if isinstance(payload.get("prices"), dict) else {}
    return {
        "instruments": prune_mapping(instruments, MARKET_DATA_CACHE_MAX_INSTRUMENTS, "resolvedAt"),
        "prices": prune_mapping(prices, MARKET_DATA_CACHE_MAX_PRICES, "cachedAt"),
    }


def load_saved_portfolios() -> list[dict[str, Any]]:
    payload = load_json_file(SAVED_PORTFOLIOS_FILE, [])
    return payload if isinstance(payload, list) else []


def write_saved_portfolios(items: list[dict[str, Any]]) -> None:
    atomic_write_json(SAVED_PORTFOLIOS_FILE, items, ensure_ascii=True)


def load_market_data_cache() -> dict[str, Any]:
    global MARKET_DATA_CACHE_MEMORY, MARKET_DATA_CACHE_MEMORY_PATH
    if MARKET_DATA_CACHE_MEMORY is not None and MARKET_DATA_CACHE_MEMORY_PATH == MARKET_DATA_CACHE_FILE:
        return MARKET_DATA_CACHE_MEMORY
    payload = load_json_file(MARKET_DATA_CACHE_FILE, {"instruments": {}, "prices": {}})
    MARKET_DATA_CACHE_MEMORY = {
        "instruments": payload.get("instruments", {}) if isinstance(payload.get("instruments"), dict) else {},
        "prices": payload.get("prices", {}) if isinstance(payload.get("prices"), dict) else {},
    }
    MARKET_DATA_CACHE_MEMORY_PATH = MARKET_DATA_CACHE_FILE
    return MARKET_DATA_CACHE_MEMORY


def write_market_data_cache(payload: dict[str, Any]) -> None:
    global MARKET_DATA_CACHE_MEMORY, MARKET_DATA_CACHE_MEMORY_PATH
    payload = prune_market_data_cache(payload)
    MARKET_DATA_CACHE_MEMORY = payload
    MARKET_DATA_CACHE_MEMORY_PATH = MARKET_DATA_CACHE_FILE
    atomic_write_json(MARKET_DATA_CACHE_FILE, payload, ensure_ascii=True)


def cache_key(*parts: str) -> str:
    return "::".join(str(part).strip().upper() for part in parts)


def looks_like_isin(value: str) -> bool:
    text = value.strip().upper()
    return len(text) == 12 and text[:2].isalpha() and text[2:11].isalnum() and text[-1].isdigit()


def looks_like_figi(value: str) -> bool:
    text = value.strip().upper()
    return text.startswith("FIGI:BBG") and len(text) >= 15


def require_isin(value: str, field: str = "ISIN") -> str:
    text = value.strip().upper()
    if not looks_like_isin(text):
        raise BacktestError(
            f"{field} non valido: inserisci solo codici ISIN a 12 caratteri, "
            "per esempio IE00B3WJKG14. I ticker brevi non sono più accettati per evitare strumenti ambigui."
        )
    return text


def require_instrument_identifier(value: str, field: str = "strumento") -> str:
    text = value.strip().upper()
    if looks_like_isin(text) or looks_like_figi(text):
        return text
    raise BacktestError(
        f"{field} non valido: inserisci un ISIN a 12 caratteri oppure seleziona uno strumento dalla ricerca."
    )


def map_openfigi_exchange_to_eodhd(exch_code: str | None) -> str:
    code = str(exch_code or "US").upper()
    return {
        "US": "US",
        "LN": "LSE",
        "LONDON": "LSE",
        "GY": "XETRA",
        "GR": "XETRA",
        "FP": "PA",
        "XE": "PA",
        "XO": "PA",
        "IM": "MI",
        "SW": "SW",
        "SM": "MC",
        "NA": "AS",
        "VX": "SW",
    }.get(code, code)


class OpenFigiClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENFIGI_API_KEY", "")
        self.base_url = OPENFIGI_API_URL.rstrip("/")

    def _post(self, path: str, payload: Any) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise BacktestError(f"data_unavailable: OpenFIGI errore {exc.code}: {detail[:180] or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise BacktestError(f"data_unavailable: OpenFIGI non raggiungibile: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise BacktestError("data_unavailable: risposta OpenFIGI non valida.") from exc

    def map_identifier(self, query: str) -> dict[str, Any]:
        text = require_instrument_identifier(query)
        job = (
            {"idType": "ID_BB_GLOBAL", "idValue": text.split(":", 1)[1]}
            if looks_like_figi(text)
            else {"idType": "ID_ISIN", "idValue": text}
        )
        response = self._post("/v3/mapping", [job])
        first = response[0] if isinstance(response, list) and response else {}
        data = first.get("data") if isinstance(first, dict) else None
        if not data:
            warning = first.get("warning") or first.get("error") if isinstance(first, dict) else "nessun risultato"
            raise BacktestError(f"data_unavailable: OpenFIGI non ha risolto {query}: {warning}.")
        return data[0]

    def map_ticker(self, ticker: str, exchange: str) -> dict[str, Any]:
        job = {"idType": "TICKER", "idValue": ticker.strip().upper(), "exchCode": exchange.strip().upper()}
        response = self._post("/v3/mapping", [job])
        first = response[0] if isinstance(response, list) and response else {}
        data = first.get("data") if isinstance(first, dict) else None
        if not data:
            warning = first.get("warning") or first.get("error") if isinstance(first, dict) else "nessun risultato"
            raise BacktestError(f"data_unavailable: OpenFIGI non ha risolto {ticker}.{exchange}: {warning}.")
        return data[0]

    def search(self, query: str) -> dict[str, Any]:
        response = self._post("/v3/search", {"query": query, "marketSecDes": "Equity"})
        data = response.get("data") if isinstance(response, dict) else None
        if not data:
            message = response.get("warning") or response.get("error") if isinstance(response, dict) else "nessun risultato"
            raise BacktestError(f"data_unavailable: OpenFIGI non ha trovato {query}: {message}.")
        return data[0]

    def search_many(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        text = query.strip()
        if len(text) < 2:
            return []
        response = self._post("/v3/search", {"query": text, "marketSecDes": "Equity"})
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            return []
        return data[:limit]


class EodhdClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("EODHD_API_KEY", "")
        self.base_url = EODHD_API_URL.rstrip("/")

    def historical_prices(self, eodhd_code: str, start: date, end: date) -> list[PricePoint]:
        if not self.api_key:
            raise BacktestError("data_unavailable: EODHD_API_KEY non configurata nel server.")
        query = urllib.parse.urlencode(
            {
                "api_token": self.api_key,
                "fmt": "json",
                "period": "d",
                "from": start.isoformat(),
                "to": end.isoformat(),
                "order": "a",
            }
        )
        request = urllib.request.Request(f"{self.base_url}/eod/{urllib.parse.quote(eodhd_code)}?{query}")
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise BacktestError(f"data_unavailable: EODHD errore {exc.code} per {eodhd_code}: {detail[:180] or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise BacktestError(f"data_unavailable: EODHD non raggiungibile per {eodhd_code}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise BacktestError(f"data_unavailable: risposta EODHD non valida per {eodhd_code}.") from exc
        if isinstance(payload, dict) and payload.get("errors"):
            raise BacktestError(f"data_unavailable: EODHD non ha dati per {eodhd_code}: {payload.get('errors')}.")
        if not isinstance(payload, list) or not payload:
            raise BacktestError(f"data_unavailable: EODHD non ha prezzi storici per {eodhd_code}.")
        points: list[PricePoint] = []
        for row in payload:
            if not isinstance(row, dict) or not row.get("date"):
                continue
            adjusted = row.get("adjusted_close")
            close = adjusted if adjusted not in {None, ""} else row.get("close")
            if close in {None, ""}:
                continue
            points.append(PricePoint(day=str(row["date"])[:10], close=float(close)))
        if not points:
            raise BacktestError(f"data_unavailable: EODHD non ha prezzi adjusted utilizzabili per {eodhd_code}.")
        return points

    def search_symbols(self, query_text: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self.api_key:
            raise BacktestError("data_unavailable: EODHD_API_KEY non configurata nel server.")
        query = urllib.parse.urlencode({"api_token": self.api_key, "fmt": "json"})
        request = urllib.request.Request(f"{self.base_url}/search/{urllib.parse.quote(query_text.strip())}?{query}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise BacktestError(f"data_unavailable: EODHD search errore {exc.code}: {detail[:180] or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise BacktestError(f"data_unavailable: EODHD search non raggiungibile: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise BacktestError("data_unavailable: risposta EODHD search non valida.") from exc
        if not isinstance(payload, list):
            return []
        return [item for item in payload[:limit] if isinstance(item, dict)]


openFigiClient = OpenFigiClient()
eodhdClient = EodhdClient()


def eodhd_symbol_code(row: dict[str, Any]) -> str:
    code = str(row.get("Code") or row.get("code") or "").strip().upper()
    exchange = str(row.get("Exchange") or row.get("exchange") or "").strip().upper()
    return f"{code}.{exchange}" if code and exchange else ""


def eodhd_search_result_priority(row: dict[str, Any], instrument: dict[str, Any]) -> tuple[int, int, int, str]:
    row_isin = str(row.get("ISIN") or row.get("isin") or "").strip().upper()
    instrument_isin = str(instrument.get("isin") or "").strip().upper()
    row_exchange = str(row.get("Exchange") or row.get("exchange") or "").strip().upper()
    row_code = str(row.get("Code") or row.get("code") or "").strip().upper()
    row_type = str(row.get("Type") or row.get("type") or "").upper()
    wanted_exchange = str(instrument.get("eodhdExchange") or "").strip().upper()
    isin_penalty = 0 if instrument_isin and row_isin == instrument_isin else 2 if instrument_isin else 1
    exchange_penalty = 0 if wanted_exchange and row_exchange == wanted_exchange else 1
    type_penalty = 0 if any(kind in row_type for kind in ("COMMON", "ETF", "FUND", "STOCK")) else 1
    return (isin_penalty, exchange_penalty, type_penalty, row_code)


def resolve_eodhd_code_from_search(instrument: dict[str, Any]) -> tuple[str, str]:
    global EODHD_SEARCH_DISABLED_UNTIL
    if not hasattr(eodhdClient, "search_symbols"):
        return "", ""
    if time.time() < EODHD_SEARCH_DISABLED_UNTIL:
        return "", ""
    queries = [
        str(instrument.get("isin") or "").strip(),
        str(instrument.get("name") or "").strip(),
        str(instrument.get("ticker") or "").strip(),
    ]
    seen_queries: set[str] = set()
    matches: list[dict[str, Any]] = []
    for query_text in queries:
        if not query_text or query_text.upper() in seen_queries:
            continue
        seen_queries.add(query_text.upper())
        try:
            cache_key_text = query_text.upper()
            cached = EODHD_SEARCH_CACHE.get(cache_key_text)
            if cached and time.time() - cached[0] < EODHD_SEARCH_CACHE_TTL_SECONDS:
                rows = cached[1]
            else:
                rows = eodhdClient.search_symbols(query_text)
                EODHD_SEARCH_CACHE[cache_key_text] = (time.time(), rows)
            for row in rows:
                code = eodhd_symbol_code(row)
                if code:
                    matches.append(row)
        except BacktestError as exc:
            if "402" in str(exc) or "Payment Required" in str(exc):
                EODHD_SEARCH_DISABLED_UNTIL = time.time() + EODHD_SEARCH_DISABLED_TTL_SECONDS
                break
            continue
        if any(
            str(row.get("ISIN") or row.get("isin") or "").strip().upper()
            == str(instrument.get("isin") or "").strip().upper()
            for row in matches
        ):
            break
    if not matches:
        return "", ""
    best = sorted(matches, key=lambda row: eodhd_search_result_priority(row, instrument))[0]
    return eodhd_symbol_code(best), "eodhd_search_isin" if str(instrument.get("isin") or "").strip() else "eodhd_search"


def apply_eodhd_resolution(instrument: dict[str, Any]) -> dict[str, Any]:
    eodhd_code, source = resolve_eodhd_code_from_search(instrument)
    if eodhd_code:
        code, exchange = eodhd_code.rsplit(".", 1)
        instrument["ticker"] = code
        instrument["eodhdExchange"] = exchange
        instrument["eodhdCode"] = eodhd_code
        instrument["eodhdResolutionSource"] = source
    else:
        instrument["eodhdResolutionSource"] = "openfigi_exchange_mapping"
    return instrument


def instrumentResolverService(instrument_id: str) -> dict[str, Any]:
    normalized_id = require_instrument_identifier(instrument_id)
    cache = load_market_data_cache()
    key = cache_key(normalized_id)
    if key in cache["instruments"]:
        return cache["instruments"][key]
    try:
        raw = openFigiClient.map_identifier(normalized_id)
    except BacktestError:
        raise
    ticker = str(raw.get("ticker") or instrument_id).strip().upper()
    exchange = str(raw.get("exchCode") or "US").strip().upper()
    currency = str(raw.get("currency") or "").strip().upper()
    eodhd_exchange = map_openfigi_exchange_to_eodhd(exchange)
    instrument = {
        "input": normalized_id,
        "figi": raw.get("figi"),
        "isin": normalized_id if looks_like_isin(normalized_id) else str(raw.get("idValue") or raw.get("isin") or "").strip().upper(),
        "instrumentId": normalized_id,
        "ticker": ticker,
        "exchange": exchange,
        "eodhdExchange": eodhd_exchange,
        "currency": currency,
        "name": raw.get("name") or raw.get("securityDescription") or ticker,
        "securityType": raw.get("securityType") or raw.get("securityType2"),
        "marketSector": raw.get("marketSector"),
        "eodhdCode": f"{ticker}.{eodhd_exchange}",
        "resolvedAt": now_utc_iso(),
    }
    instrument["assetClass"] = infer_asset_class_from_metadata(instrument, normalized_id)
    instrument["assetClassLabel"] = ASSET_CLASS_LABELS.get(instrument["assetClass"], "Azioni")
    instrument = apply_eodhd_resolution(instrument)
    cache["instruments"][key] = instrument
    write_market_data_cache(cache)
    return instrument


def getHistoricalPrices(instrumentId: str, startDate: date, endDate: date) -> list[PricePoint]:
    normalized_id = require_instrument_identifier(instrumentId)
    cache = load_market_data_cache()
    instrument = instrumentResolverService(normalized_id)
    price_key = cache_key(instrument["eodhdCode"], startDate.isoformat(), endDate.isoformat())
    if price_key in cache["prices"]:
        cached_prices = cache["prices"][price_key]
        rows = cached_prices.get("items", []) if isinstance(cached_prices, dict) else cached_prices
        return [PricePoint(day=item["day"], close=float(item["close"])) for item in rows]
    try:
        points = eodhdClient.historical_prices(instrument["eodhdCode"], startDate, endDate)
    except BacktestError:
        previous_code = instrument.get("eodhdCode")
        refreshed = apply_eodhd_resolution(dict(instrument))
        if refreshed.get("eodhdCode") == previous_code:
            raise
        points = eodhdClient.historical_prices(refreshed["eodhdCode"], startDate, endDate)
        instrument = refreshed
    cache = load_market_data_cache()
    price_key = cache_key(instrument["eodhdCode"], startDate.isoformat(), endDate.isoformat())
    cache["prices"][price_key] = {
        "cachedAt": now_utc_iso(),
        "items": [{"day": point.day, "close": point.close} for point in points],
    }
    cache["instruments"][cache_key(normalized_id)] = instrument
    write_market_data_cache(cache)
    return points


def instrument_metadata_for(symbols: list[str]) -> dict[str, dict[str, str]]:
    cache = load_market_data_cache()
    metadata: dict[str, dict[str, str]] = {}
    for symbol in symbols:
        instrument = cache.get("instruments", {}).get(cache_key(symbol), {})
        name = str(instrument.get("name") or "").strip()
        ticker = str(instrument.get("ticker") or "").strip()
        exchange = str(instrument.get("eodhdExchange") or instrument.get("exchange") or "").strip()
        display_name = name.title() if name and name.isupper() else name
        metadata[symbol] = {
            "isin": str(instrument.get("isin") or symbol),
            "instrumentId": symbol,
            "name": name or symbol,
            "ticker": ticker,
            "exchange": exchange,
            "displayName": display_name or symbol,
            "assetClass": str(instrument.get("assetClass") or infer_asset_class_from_metadata(instrument, symbol)),
            "assetClassLabel": ASSET_CLASS_LABELS.get(str(instrument.get("assetClass") or infer_asset_class_from_metadata(instrument, symbol)), "Azioni"),
        }
    return metadata


def display_name_for_symbol(symbol: str) -> str:
    if not symbol:
        return "--"
    local_name = local_known_name_for_identifier(symbol)
    if local_name:
        return local_name
    metadata = instrument_metadata_for([symbol]).get(symbol, {})
    name = str(metadata.get("displayName") or metadata.get("name") or "").strip()
    if name and not is_public_identifier(name):
        return name
    return str(symbol)


def is_public_identifier(value: Any) -> bool:
    text = str(value or "").strip().upper()
    return looks_like_isin(text) or text.startswith("FIGI:")


def local_known_name_for_identifier(symbol: str) -> str:
    wanted = str(symbol or "").strip().upper()
    if not wanted:
        return ""
    for portfolio in STANDARD_PORTFOLIOS:
        for holding in portfolio.get("holdings", []):
            if str(holding.get("isin") or "").strip().upper() == wanted and holding.get("name"):
                return str(holding["name"]).strip()
    for benchmark in BENCHMARK_DEFINITIONS.values():
        for holding in benchmark.get("holdings", []):
            if str(holding.get("isin") or "").strip().upper() == wanted and holding.get("name"):
                return str(holding["name"]).strip()
    return ""


def public_display_name_for_symbol(symbol: str, fallback_name: str = "") -> str:
    fallback = str(fallback_name or "").strip()
    if fallback and not is_public_identifier(fallback):
        return fallback
    resolved = display_name_for_symbol(symbol)
    if resolved and not is_public_identifier(resolved):
        return resolved
    return "Strumento finanziario"


def enrich_portfolio_display_names(portfolio: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **item,
            "displayName": public_display_name_for_symbol(str(item.get("symbol", "")), str(item.get("displayName", ""))),
        }
        for item in portfolio
    ]


def public_instrument_metadata_for(portfolio: list[dict[str, Any]], extra_symbols: list[str] | None = None) -> dict[str, dict[str, str]]:
    symbols = [str(item.get("symbol", "")) for item in portfolio if item.get("symbol")]
    if extra_symbols:
        symbols.extend(str(symbol) for symbol in extra_symbols if symbol)
    metadata = instrument_metadata_for(list(dict.fromkeys(symbols)))
    display_by_symbol = {
        str(item.get("symbol", "")): str(item.get("displayName", ""))
        for item in portfolio
        if item.get("symbol")
    }
    for symbol, item in metadata.items():
        display_name = public_display_name_for_symbol(symbol, display_by_symbol.get(symbol, ""))
        item["name"] = display_name
        item["displayName"] = display_name
    return metadata


def normalize_openfigi_search_result(raw: dict[str, Any]) -> dict[str, str] | None:
    isin = str(raw.get("idValue") or raw.get("isin") or "").strip().upper()
    figi = str(raw.get("figi") or "").strip().upper()
    instrument_id = f"FIGI:{figi}" if figi.startswith("BBG") else isin if looks_like_isin(isin) else ""
    security_type = str(raw.get("securityType") or raw.get("securityType2") or "").strip()
    if not instrument_id:
        return None
    if any(word in security_type.upper() for word in ("FUTURE", "OPTION", "WARRANT", "RIGHT")):
        return None
    ticker = str(raw.get("ticker") or "").strip().upper()
    exchange = str(raw.get("exchCode") or "").strip().upper()
    name = str(raw.get("name") or raw.get("securityDescription") or ticker or isin).strip()
    display_name = name.title() if name and name.isupper() else name
    inferred_asset_class = infer_asset_class_from_metadata(
        {
            "instrumentId": instrument_id,
            "isin": isin,
            "ticker": ticker,
            "name": display_name or name,
            "securityType": security_type,
            "marketSector": raw.get("marketSector"),
        },
        instrument_id,
    )
    return {
        "isin": isin,
        "instrumentId": instrument_id,
        "name": display_name or isin,
        "ticker": ticker,
        "exchange": exchange,
        "isEuropeanListing": "true" if exchange in EUROPEAN_OPENFIGI_EXCHANGES else "false",
        "securityType": security_type,
        "currency": str(raw.get("currency") or "").strip().upper(),
        "assetClass": inferred_asset_class,
        "assetClassLabel": ASSET_CLASS_LABELS.get(inferred_asset_class, "Azioni"),
    }


def search_result_priority(item: dict[str, str], include_global: bool) -> tuple[int, int, int, str]:
    exchange = str(item.get("exchange") or "").upper()
    currency = str(item.get("currency") or "").upper()
    ticker = str(item.get("ticker") or "")
    isin = str(item.get("isin") or "")
    is_european = item.get("isEuropeanListing") in {True, "true"}
    priority_map = GLOBAL_EXCHANGE_PRIORITY if include_global else EUROPEAN_EXCHANGE_PRIORITY
    listing_penalty = 0 if include_global or is_european else 1
    exchange_rank = priority_map.get(exchange, 99)
    currency_penalty = 0 if (not include_global and currency in {"EUR", "GBP", "CHF", "SEK", "DKK", "NOK"}) or (include_global and currency == "USD") else 1
    identifier_penalty = 0 if isin else 1
    return (listing_penalty, exchange_rank, currency_penalty + identifier_penalty, ticker)


def select_primary_search_result(items: list[dict[str, str]], include_global: bool) -> list[dict[str, str]]:
    if not items:
        return []
    return [sorted(items, key=lambda item: search_result_priority(item, include_global))[0]]


def is_european_search_result(item: dict[str, str]) -> bool:
    return item.get("isEuropeanListing") in {True, "true"}


def is_depositary_receipt(item: dict[str, str]) -> bool:
    security_type = str(item.get("securityType") or "").upper()
    return "DR" in security_type or "DEPOSITARY" in security_type or "ADR" in security_type


def build_text_search_variants(text: str) -> list[str]:
    variants = [text]
    lower_text = text.lower()
    if all(suffix not in lower_text for suffix in (" corp", " inc", " etf", " ucits")):
        variants.extend([f"{text} Corp", f"{text} Inc", f"{text} ETF", f"{text} UCITS"])
    return variants[:INSTRUMENT_SEARCH_VARIANT_LIMIT]


def derived_queries_from_items(items: list[dict[str, str]], original_query: str) -> list[str]:
    original = original_query.strip().lower()
    queries: list[str] = []
    for item in items:
        name = re.sub(r"[^A-Za-z0-9 ]+", " ", str(item.get("name") or ""))
        words = [word for word in name.split() if len(word) > 2 and word.upper() not in {"CORP", "INC", "PLC", "SA", "SPA", "NV", "SE", "AG", "ADR", "DR"}]
        for size in (3, 2):
            candidate = " ".join(words[:size]).strip()
            if candidate and candidate.lower() != original and candidate not in queries:
                queries.append(candidate)
            if len(queries) >= INSTRUMENT_SEARCH_DERIVED_QUERY_LIMIT:
                return queries
    return queries


def european_alias_matches(query: str) -> list[tuple[str, str]]:
    normalized = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    matches: list[tuple[str, str]] = []
    for alias, mappings in EUROPEAN_SEARCH_ALIASES.items():
        if alias in normalized:
            matches.extend(mappings)
    return matches


def search_european_aliases(query: str, seen: set[str]) -> list[dict[str, str]]:
    alias_items: list[dict[str, str]] = []
    for ticker, exchange in european_alias_matches(query):
        try:
            item = normalize_openfigi_search_result(openFigiClient.map_ticker(ticker, exchange))
        except BacktestError:
            continue
        if not item or item["instrumentId"] in seen:
            continue
        seen.add(item["instrumentId"])
        alias_items.append(item)
    return alias_items


def search_instruments(query: str, include_global: bool = False) -> dict[str, Any]:
    text = query.strip()
    if len(text) < 3 and not looks_like_isin(text):
        return {
            "items": [],
            "hasGlobalResults": False,
            "hasEuropeanResults": False,
            "totalMatchedResults": 0,
            "filteredToEuropeanListings": not include_global,
            "message": "Inserisci almeno 3 caratteri per cercare uno strumento.",
        }
    cache_key_text = f"{text.lower()}::global={int(include_global)}"
    cached = INSTRUMENT_SEARCH_CACHE.get(cache_key_text)
    if cached and time.time() - cached[0] < INSTRUMENT_SEARCH_CACHE_TTL_SECONDS:
        return cached[1]
    if looks_like_isin(text):
        instrument = instrumentResolverService(text)
        exchange = str(instrument["exchange"]).upper()
        result = {
            "items": [
                {
                    "isin": instrument["isin"],
                    "instrumentId": instrument["instrumentId"],
                    "name": instrument["name"],
                    "ticker": instrument["ticker"],
                    "exchange": instrument["exchange"],
                    "isEuropeanListing": exchange in EUROPEAN_OPENFIGI_EXCHANGES,
                    "securityType": str(instrument.get("securityType") or ""),
                    "currency": instrument["currency"],
                    "assetClass": str(instrument.get("assetClass") or infer_asset_class_from_metadata(instrument, text)),
                    "assetClassLabel": ASSET_CLASS_LABELS.get(str(instrument.get("assetClass") or infer_asset_class_from_metadata(instrument, text)), "Azioni"),
                }
            ]
        }
        INSTRUMENT_SEARCH_CACHE[cache_key_text] = (time.time(), result)
        return result
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    variants = build_text_search_variants(text)
    for variant in variants:
        for raw in openFigiClient.search_many(variant):
            item = normalize_openfigi_search_result(raw)
            if not item or item["instrumentId"] in seen:
                continue
            seen.add(item["instrumentId"])
            items.append(item)
        if any(is_european_search_result(item) for item in items):
            break
    european_items = [item for item in items if is_european_search_result(item) and not is_depositary_receipt(item)]
    if not european_items and items:
        for derived_query in derived_queries_from_items(items, text):
            for raw in openFigiClient.search_many(derived_query):
                item = normalize_openfigi_search_result(raw)
                if not item or item["instrumentId"] in seen:
                    continue
                seen.add(item["instrumentId"])
                items.append(item)
                if is_european_search_result(item) and not is_depositary_receipt(item):
                    european_items.append(item)
            if european_items:
                break
    if not european_items:
        alias_items = search_european_aliases(text, seen)
        items.extend(alias_items)
        european_items.extend(item for item in alias_items if is_european_search_result(item) and not is_depositary_receipt(item))
    candidate_items = items if include_global else european_items
    result_items = select_primary_search_result(candidate_items, include_global)
    result = {
        "items": result_items,
        "hasGlobalResults": bool(items),
        "hasEuropeanResults": bool(european_items),
        "totalMatchedResults": len(items),
        "filteredToEuropeanListings": not include_global,
    }
    INSTRUMENT_SEARCH_CACHE[cache_key_text] = (time.time(), result)
    return result


def marketDataService(instrument_ids: list[str], start: date, end: date) -> dict[str, list[PricePoint]]:
    result: dict[str, list[PricePoint]] = {}
    errors: list[str] = []
    for instrument_id in instrument_ids:
        try:
            result[instrument_id] = getHistoricalPrices(instrument_id, start, end)
        except BacktestError as exc:
            errors.append(str(exc))
    if not result:
        raise BacktestError("data_unavailable: nessun prezzo disponibile. " + " | ".join(errors))
    if errors:
        raise BacktestError("data_unavailable: alcuni strumenti non sono disponibili. " + " | ".join(errors))
    return result


def parse_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise BacktestError(f"{field} deve essere nel formato YYYY-MM-DD.") from exc


def normalize_portfolio(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    portfolio: list[dict[str, Any]] = []
    for item in items:
        raw_symbol = str(item.get("symbol", "")).strip().upper()
        symbol = require_instrument_identifier(raw_symbol, "Identificativo strumento") if raw_symbol else ""
        if not symbol:
            continue
        try:
            weight = float(item.get("weight", 0))
        except (TypeError, ValueError):
            raise BacktestError(f"Peso non valido per {symbol}.")
        if weight < 0:
            raise BacktestError(f"Il peso di {symbol} non puo essere negativo.")
        try:
            min_weight = float(item.get("minWeight", 0))
            max_weight = float(item.get("maxWeight", 100))
        except (TypeError, ValueError):
            raise BacktestError(f"Vincoli min/max non validi per {symbol}.")
        if min_weight < 0 or max_weight < 0 or min_weight > 100 or max_weight > 100:
            raise BacktestError(f"I vincoli di {symbol} devono essere tra 0% e 100%.")
        if min_weight > max_weight:
            raise BacktestError(f"Il peso minimo di {symbol} non puo superare il peso massimo.")
        cached_instrument = load_market_data_cache().get("instruments", {}).get(cache_key(symbol), {})
        asset_class = str(
            item.get("assetClass")
            or infer_asset_class_from_metadata(cached_instrument, symbol)
            or infer_asset_class_from_text(symbol, item.get("displayName"), item.get("name"), item.get("query"))
            or "equity"
        ).lower()
        if asset_class not in ASSET_CLASSES:
            raise BacktestError(f"Asset class non valida per {symbol}.")
        portfolio.append(
            {
                "symbol": symbol,
                "displayName": str(item.get("displayName") or item.get("name") or "").strip(),
                "weight": weight,
                "minWeight": min_weight / 100,
                "maxWeight": max_weight / 100,
                "assetClass": asset_class,
                "stressEnabled": bool(item.get("stressEnabled", False)),
            }
        )

    if not portfolio:
        raise BacktestError("Inserisci almeno un ISIN.")

    total_weight = sum(item["weight"] for item in portfolio)
    if total_weight <= 0:
        equal = 1 / len(portfolio)
        return [{**item, "weight": equal} for item in portfolio]

    return [{**item, "weight": item["weight"] / total_weight} for item in portfolio]


def parse_investor_profile(value: Any, initial_capital: float) -> dict[str, Any]:
    profile = value if isinstance(value, dict) else {}
    try:
        age = int(profile.get("age", 40))
    except (TypeError, ValueError):
        age = 40
    try:
        horizon_years = int(profile.get("horizonYears", 10))
    except (TypeError, ValueError):
        horizon_years = 10
    try:
        capital = float(profile.get("capital", initial_capital))
    except (TypeError, ValueError):
        capital = initial_capital
    try:
        max_temporary_loss = float(profile.get("maxTemporaryLoss", 0.2))
    except (TypeError, ValueError):
        max_temporary_loss = 0.2
    try:
        monthly_pac = float(profile.get("monthlyPac", 0))
    except (TypeError, ValueError):
        monthly_pac = 0
    return {
        "capital": max(1.0, capital),
        "age": max(18, min(100, age)),
        "horizonYears": max(1, min(60, horizon_years)),
        "objective": str(profile.get("objective", "Crescita del capitale")).strip()[:240],
        "riskPreference": str(profile.get("riskPreference", "balanced")).strip().lower(),
        "maxTemporaryLoss": max(0.01, min(0.8, max_temporary_loss)),
        "monthlyPac": max(0.0, monthly_pac),
        "goalPriority": str(profile.get("goalPriority", "capital_growth")).strip().lower()[:60],
        "experienceLevel": str(profile.get("experienceLevel", "beginner")).strip().lower()[:40],
        "liquidityNeed": str(profile.get("liquidityNeed", "low")).strip().lower()[:40],
    }


def make_demo_bars(symbols: list[str], start: date, end: date) -> dict[str, list[PricePoint]]:
    """Generate deterministic weekday data when credentials are absent."""
    result: dict[str, list[PricePoint]] = {}
    for index, symbol in enumerate(symbols):
        seed = sum(ord(ch) for ch in symbol) + start.toordinal() + end.toordinal()
        rng = random.Random(seed)
        price = 80 + (seed % 140)
        drift = 0.00025 + index * 0.00004
        vol = 0.012 + (seed % 7) * 0.001
        points: list[PricePoint] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                seasonal = math.sin(cursor.toordinal() / 18 + index) * 0.002
                price *= 1 + drift + seasonal + rng.gauss(0, vol)
                points.append(PricePoint(day=cursor.isoformat(), close=round(max(price, 1), 2)))
            cursor += timedelta(days=1)
        result[symbol] = points
    return result


def price_coverage_summary(price_data: dict[str, list[PricePoint]]) -> str:
    details: list[str] = []
    for symbol, points in price_data.items():
        days = sorted({point.day for point in points})
        if not days:
            details.append(f"{symbol}: 0 prezzi")
            continue
        details.append(f"{symbol}: {len(days)} prezzi, {days[0]} -> {days[-1]}")
    return "; ".join(details)


def years_between(start_day: str, end_day: str) -> float:
    start_date = parse_date(start_day, "Data iniziale")
    end_date = parse_date(end_day, "Data finale")
    return max(0.0, (end_date - start_date).days / 365.25)


def align_returns(price_data: dict[str, list[PricePoint]]) -> tuple[list[str], dict[str, dict[str, float]]]:
    calendars = [set(point.day for point in points) for points in price_data.values() if points]
    if not calendars:
        raise BacktestError("Nessun dato prezzo disponibile per il periodo scelto.")
    common_days = sorted(set.intersection(*calendars))
    if len(common_days) < 2:
        raise BacktestError(
            "Servono almeno due giornate comuni di prezzo per backtestare. "
            "Copertura dati: "
            f"{price_coverage_summary(price_data)}. "
            "Verifica che ogni ISIN sia stato risolto sul mercato corretto."
        )

    returns: dict[str, dict[str, float]] = {}
    for symbol, points in price_data.items():
        close_by_day = {point.day: point.close for point in points}
        symbol_returns: dict[str, float] = {}
        previous = close_by_day[common_days[0]]
        for day in common_days[1:]:
            current = close_by_day[day]
            symbol_returns[day] = current / previous - 1
            previous = current
        returns[symbol] = symbol_returns
    return common_days, returns


def max_drawdown(values: list[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
    return worst


def sharpe_ratio(daily_returns: list[float]) -> float | None:
    if len(daily_returns) < 2:
        return None
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((item - mean) ** 2 for item in daily_returns) / (len(daily_returns) - 1)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return None
    return (mean / stdev) * math.sqrt(252)


def sortino_ratio(daily_returns: list[float], target_daily_return: float = 0.0) -> float | None:
    if len(daily_returns) < 2:
        return None
    excess_returns = [item - target_daily_return for item in daily_returns]
    downside = [min(0.0, item) for item in excess_returns]
    downside_variance = sum(item * item for item in downside) / max(1, len(downside) - 1)
    downside_deviation = math.sqrt(downside_variance)
    if downside_deviation == 0:
        return None
    mean_excess = sum(excess_returns) / len(excess_returns)
    return (mean_excess / downside_deviation) * math.sqrt(252)


def parse_rebalance_frequency(value: Any) -> str:
    frequency = str(value or "monthly").lower()
    valid = {"weekly", "monthly", "quarterly", "semiannual", "annual"}
    if frequency not in valid:
        raise BacktestError("Frequenza di ribilanciamento non valida.")
    return frequency


def rebalance_label(frequency: str) -> str:
    return {
        "weekly": "Ogni settimana",
        "monthly": "Ogni mese",
        "quarterly": "Ogni 3 mesi",
        "semiannual": "Ogni 6 mesi",
        "annual": "Ogni anno",
    }[frequency]


def save_portfolio(payload: dict[str, Any]) -> dict[str, Any]:
    plan = effective_user_plan(payload.get("userPlan"))
    try:
        initial_capital = float(payload.get("initialCapital", 10000))
    except (TypeError, ValueError) as exc:
        raise BacktestError("Capitale iniziale non valido.") from exc
    if initial_capital <= 0:
        raise BacktestError("Il capitale iniziale deve essere positivo.")
    portfolio = normalize_portfolio(payload.get("portfolio", []))
    saved_portfolio = [
        {
            **item,
            "minWeight": round(float(item.get("minWeight", 0)) * 100, 4),
            "maxWeight": round(float(item.get("maxWeight", 1)) * 100, 4),
        }
        for item in portfolio
    ]
    frequency = parse_rebalance_frequency(payload.get("rebalanceFrequency"))
    name = str(payload.get("name") or f"Portfolio {datetime.now().strftime('%Y-%m-%d %H:%M')}").strip()[:80]
    created_at = now_utc_iso()
    saved = {
        "id": f"pf-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
        "name": name,
        "createdAt": created_at,
        "lastRebalanceDate": payload.get("lastRebalanceDate") or created_at[:10],
        "initialCapital": initial_capital,
        "rebalanceFrequency": frequency,
        "feed": "eodhd",
        "portfolio": saved_portfolio,
        "investorProfile": parse_investor_profile(payload.get("investorProfile"), initial_capital),
    }
    items = load_saved_portfolios()
    max_portfolios = feature_value(plan, "maxPortfolios")
    if max_portfolios != "unlimited" and len(items) >= int(max_portfolios):
        raise FeatureLockedError("multiplePortfolios", plan)
    items.append(saved)
    write_saved_portfolios(items)
    return {"portfolio": saved, "items": items}


def delete_saved_portfolio(portfolio_id: str) -> dict[str, Any]:
    portfolio_id = str(portfolio_id or "").strip()
    if not portfolio_id:
        raise BacktestError("Seleziona un portfolio da eliminare.")
    items = load_saved_portfolios()
    remaining = [item for item in items if item.get("id") != portfolio_id]
    if len(remaining) == len(items):
        raise BacktestError("Portfolio salvato non trovato.")
    write_saved_portfolios(remaining)
    return {"deletedId": portfolio_id, "items": remaining}


def find_saved_portfolio(portfolio_id: str) -> dict[str, Any]:
    for item in load_saved_portfolios():
        if item.get("id") == portfolio_id:
            return item
    raise BacktestError("Portfolio salvato non trovato.")


def update_saved_portfolio_fields(portfolio_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    items = load_saved_portfolios()
    updated: dict[str, Any] | None = None
    for item in items:
        if item.get("id") == portfolio_id:
            item.update(updates)
            updated = item
            break
    if updated is None:
        raise BacktestError("Portfolio salvato non trovato.")
    write_saved_portfolios(items)
    return updated


def run_portfolio_monitor(payload: dict[str, Any]) -> dict[str, Any]:
    plan = effective_user_plan(payload.get("userPlan"))
    enforce_feature(plan, "portfolioTracking")
    enforce_feature(plan, "rebalancingSuggestions")
    portfolio_id = str(payload.get("id", ""))
    saved = find_saved_portfolio(portfolio_id)
    portfolio = normalize_portfolio(saved.get("portfolio", []))
    portfolio = enrich_portfolio_display_names(portfolio)
    symbols = [item["symbol"] for item in portfolio]
    use_demo = bool(payload.get("demo", False))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=45)
    try:
        price_data = make_demo_bars(symbols, start, today) if use_demo else marketDataService(symbols, start, today)
        data_source = "demo" if use_demo else "eodhd"
    except BacktestError:
        raise
    missing = [symbol for symbol in symbols if symbol not in price_data]
    if missing:
        raise BacktestError(f"Nessun dato prezzo per monitorare: {', '.join(missing)}.")
    common_days, _ = align_returns(price_data)
    first_day = common_days[0]
    latest_day = common_days[-1]
    initial_capital = float(saved.get("initialCapital", 0))
    rows = []
    current_total = 0.0
    for item in portfolio:
        symbol = item["symbol"]
        closes = {point.day: point.close for point in price_data[symbol]}
        initial_price = closes[first_day]
        latest_price = closes[latest_day]
        shares = (initial_capital * item["weight"]) / initial_price
        current_value = shares * latest_price
        current_total += current_value
        rows.append(
            {
                "symbol": symbol,
                "displayName": item.get("displayName") or public_display_name_for_symbol(symbol),
                "targetWeight": item["weight"],
                "shares": shares,
                "initialPrice": initial_price,
                "latestPrice": latest_price,
                "currentValue": current_value,
            }
        )
    trades = []
    for row in rows:
        target_value = current_total * row["targetWeight"]
        trade_value = target_value - row["currentValue"]
        quantity = trade_value / row["latestPrice"] if row["latestPrice"] else 0.0
        action = "mantieni"
        if trade_value > 1:
            action = "compra"
        elif trade_value < -1:
            action = "vendi"
        trades.append(
            {
                **row,
                "displayName": row.get("displayName") or public_display_name_for_symbol(row.get("symbol", "")),
                "currentWeight": row["currentValue"] / current_total if current_total else 0,
                "targetValue": target_value,
                "tradeValue": trade_value,
                "quantity": quantity,
                "action": action,
            }
        )
    last_rebalance = str(saved.get("lastRebalanceDate") or saved.get("createdAt", "")[:10] or first_day)
    previous_monitor = str(saved.get("lastMonitorDate") or "")
    due = should_rebalance(last_rebalance, latest_day, saved.get("rebalanceFrequency", "monthly"))
    notifications = build_rebalancing_notifications(due, trades)
    saved = update_saved_portfolio_fields(
        portfolio_id,
        {
            "lastMonitorDate": latest_day,
            "lastMonitorAt": now_utc_iso(),
            "lastMonitorValue": current_total,
            "lastMonitorPriceDate": latest_day,
        },
    )
    return {
        "portfolio": saved,
        "instrumentMetadata": public_instrument_metadata_for(portfolio),
        "dataSource": data_source,
        "firstPriceDate": first_day,
        "latestPriceDate": latest_day,
        "currentValue": current_total,
        "rebalance": {
            "frequency": saved.get("rebalanceFrequency", "monthly"),
            "label": rebalance_label(saved.get("rebalanceFrequency", "monthly")),
            "lastDate": last_rebalance,
            "due": due,
        },
        "monitor": {
            "previousDate": previous_monitor,
            "currentDate": latest_day,
        },
        "trades": trades,
        "notifications": notifications,
        "note": "Quantita teoriche ricostruite dal capitale salvato e dai prezzi storici; verifica sempre con le posizioni reali del broker prima di operare.",
    }


def build_rebalancing_notifications(due: bool, trades: list[dict[str, Any]]) -> list[dict[str, str]]:
    notifications = []
    if due:
        notifications.append(
            {
                "level": "info",
                "title": "Ribilanciamento previsto",
                "message": "La periodicita scelta indica che e il momento di controllare i pesi del portafoglio.",
            }
        )
    largest_drift = max(trades, key=lambda item: abs(item.get("currentWeight", 0) - item.get("targetWeight", 0)), default=None)
    if largest_drift:
        drift = abs(largest_drift.get("currentWeight", 0) - largest_drift.get("targetWeight", 0))
        if drift >= 0.03:
            instrument_name = largest_drift.get("displayName") or public_display_name_for_symbol(str(largest_drift.get("symbol", "")))
            notifications.append(
                {
                    "level": "warning",
                    "title": "Scostamento dai pesi target",
                    "message": f"{instrument_name} si discosta dal target di circa {drift * 100:.1f} punti percentuali.",
                }
            )
    if not notifications:
        notifications.append(
            {
                "level": "ok",
                "title": "Nessuna azione urgente",
                "message": "Il portafoglio risulta vicino ai pesi target rispetto alle soglie impostate.",
            }
        )
    return notifications


def period_key(day: str, frequency: str) -> tuple[int, ...]:
    parsed = parse_date(day, "giorno")
    if frequency == "weekly":
        iso_year, iso_week, _ = parsed.isocalendar()
        return (iso_year, iso_week)
    if frequency == "monthly":
        return (parsed.year, parsed.month)
    if frequency == "quarterly":
        return (parsed.year, (parsed.month - 1) // 3)
    if frequency == "semiannual":
        return (parsed.year, (parsed.month - 1) // 6)
    return (parsed.year,)


def should_rebalance(previous_day: str, current_day: str, frequency: str) -> bool:
    return period_key(previous_day, frequency) != period_key(current_day, frequency)


def parse_monte_carlo_settings(value: Any) -> dict[str, int]:
    settings = value if isinstance(value, dict) else {}
    try:
        simulations = int(settings.get("simulations", 1000))
        horizon_years = int(settings.get("horizonYears", 10))
    except (TypeError, ValueError) as exc:
        raise BacktestError("Impostazioni Monte Carlo non valide.") from exc

    if simulations not in {500, 1000, 2500, 5000, 10000}:
        raise BacktestError("Le simulazioni Monte Carlo ammesse sono 500, 1000, 2500, 5000 o 10000.")
    if horizon_years < 1 or horizon_years > 20:
        raise BacktestError("L'orizzonte Monte Carlo deve essere compreso tra 1 e 20 anni.")

    return {
        "simulations": simulations,
        "horizonYears": horizon_years,
        "tradingDays": horizon_years * 252,
    }


def percentile(sorted_values: list[float], percent: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * percent
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[int(index)]
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def sample_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0))


def run_monte_carlo(
    daily_returns: list[float],
    initial_capital: float,
    settings: dict[str, int],
) -> dict[str, Any]:
    if not daily_returns:
        raise BacktestError("Servono rendimenti storici per eseguire la simulazione Monte Carlo.")

    rng = random.Random(42)
    final_values: list[float] = []
    yearly_values: dict[int, list[float]] = {year: [] for year in range(settings["horizonYears"] + 1)}
    for simulation_index in range(settings["simulations"]):
        value = initial_capital
        yearly_values[0].append(round(value, 2))
        next_year_day = 252
        for day_index in range(1, settings["tradingDays"] + 1):
            value *= 1 + rng.choice(daily_returns)
            if day_index == next_year_day:
                yearly_values[day_index // 252].append(round(value, 2))
                next_year_day += 252
        final_values.append(round(value, 2))

    sorted_values = sorted(final_values)
    expected = sum(final_values) / len(final_values)
    probability_gain = sum(1 for value in final_values if value > initial_capital) / len(final_values)
    bucket_count = 24
    low = sorted_values[0]
    high = sorted_values[-1]
    bucket_size = (high - low) / bucket_count if high > low else 1
    histogram = [{"from": low + index * bucket_size, "to": low + (index + 1) * bucket_size, "count": 0} for index in range(bucket_count)]
    for value in final_values:
        index = min(bucket_count - 1, int((value - low) / bucket_size)) if bucket_size else 0
        histogram[index]["count"] += 1
    return {
        "settings": settings,
        "initialCapital": initial_capital,
        "expectedFinalValue": round(expected, 2),
        "p5": round(percentile(sorted_values, 0.05), 2),
        "p25": round(percentile(sorted_values, 0.25), 2),
        "median": round(percentile(sorted_values, 0.50), 2),
        "p75": round(percentile(sorted_values, 0.75), 2),
        "p95": round(percentile(sorted_values, 0.95), 2),
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "probabilityGain": probability_gain,
        "projection": [
            {
                "year": year,
                "mean": round(sum(values) / len(values), 2),
                "p5": round(percentile(sorted(values), 0.05), 2),
                "p25": round(percentile(sorted(values), 0.25), 2),
                "median": round(percentile(sorted(values), 0.50), 2),
                "p75": round(percentile(sorted(values), 0.75), 2),
                "p95": round(percentile(sorted(values), 0.95), 2),
            }
            for year, values in yearly_values.items()
        ],
        "histogram": [
            {
                "from": round(item["from"], 2),
                "to": round(item["to"], 2),
                "mid": round((item["from"] + item["to"]) / 2, 2),
                "count": item["count"],
            }
            for item in histogram
        ],
    }


def asset_return_series(common_days: list[str], close_by_symbol: dict[str, dict[str, float]], symbols: list[str]) -> dict[str, list[float]]:
    result = {symbol: [] for symbol in symbols}
    for previous_day, day in zip(common_days, common_days[1:]):
        for symbol in symbols:
            previous = close_by_symbol[symbol][previous_day]
            current = close_by_symbol[symbol][day]
            result[symbol].append(current / previous - 1)
    return result


def correlation_matrix(series_by_symbol: dict[str, list[float]], symbols: list[str]) -> list[list[float]]:
    means = {symbol: sum(series_by_symbol[symbol]) / len(series_by_symbol[symbol]) for symbol in symbols}
    stdevs = {symbol: sample_stdev(series_by_symbol[symbol]) for symbol in symbols}
    matrix: list[list[float]] = []
    denominator = max(1, len(next(iter(series_by_symbol.values()))) - 1)
    for symbol_i in symbols:
        row = []
        for symbol_j in symbols:
            if symbol_i == symbol_j:
                row.append(1.0)
                continue
            if stdevs[symbol_i] == 0 or stdevs[symbol_j] == 0:
                row.append(0.0)
                continue
            covariance = sum(
                (series_by_symbol[symbol_i][index] - means[symbol_i])
                * (series_by_symbol[symbol_j][index] - means[symbol_j])
                for index in range(len(series_by_symbol[symbol_i]))
            ) / denominator
            row.append(covariance / (stdevs[symbol_i] * stdevs[symbol_j]))
        matrix.append(row)
    return matrix


def normalize_weights(weights: list[float]) -> list[float]:
    total = sum(weights)
    if total <= 0:
        return [1 / len(weights) for _ in weights]
    return [weight / total for weight in weights]


def portfolio_variance(weights: list[float], stdevs: list[float], correlations: list[list[float]]) -> float:
    total = 0.0
    for i, weight_i in enumerate(weights):
        for j, weight_j in enumerate(weights):
            total += weight_i * weight_j * stdevs[i] * stdevs[j] * correlations[i][j]
    return max(total, 0)


def efficient_frontier(portfolios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    for portfolio in sorted(portfolios, key=lambda item: item["risk"]):
        if not frontier or portfolio["return"] > frontier[-1]["return"]:
            frontier.append(portfolio)
    return frontier


def constrained_random_weights(min_weights: list[float], max_weights: list[float], rng: random.Random) -> list[float]:
    min_total = sum(min_weights)
    max_total = sum(max_weights)
    if min_total > 1 + 1e-9 or max_total < 1 - 1e-9:
        raise BacktestError("Vincoli min/max non compatibili: la somma dei minimi deve essere <= 100% e la somma dei massimi >= 100%.")
    weights = min_weights[:]
    remaining = 1 - min_total
    capacities = [max(0.0, max_weight - min_weight) for min_weight, max_weight in zip(min_weights, max_weights)]
    order = list(range(len(weights)))
    rng.shuffle(order)
    for position, index in enumerate(order):
        if remaining <= 1e-12:
            break
        future_capacity = sum(capacities[item] for item in order[position + 1 :])
        low = max(0.0, remaining - future_capacity)
        high = min(capacities[index], remaining)
        addition = high if position == len(order) - 1 else rng.uniform(low, high)
        weights[index] += addition
        remaining -= addition
    if abs(remaining) > 1e-7:
        for index in order:
            room = max_weights[index] - weights[index]
            addition = min(room, remaining)
            weights[index] += addition
            remaining -= addition
            if abs(remaining) <= 1e-7:
                break
    total = sum(weights)
    return [weight / total for weight in weights]


def run_efficient_frontier(
    series_by_symbol: dict[str, list[float]],
    symbols: list[str],
    current_weights: list[float],
    constraints: list[dict[str, float]] | None = None,
    sample_count: int | None = None,
) -> dict[str, Any]:
    if len(symbols) < 2:
        return {"available": False, "reason": "Servono almeno 2 asset per calcolare la frontiera efficiente."}

    means = [sum(series_by_symbol[symbol]) / len(series_by_symbol[symbol]) * 252 for symbol in symbols]
    stdevs = [sample_stdev(series_by_symbol[symbol]) * math.sqrt(252) for symbol in symbols]
    correlations = correlation_matrix(series_by_symbol, symbols)
    min_weights = [float(item.get("minWeight", 0.0)) for item in constraints] if constraints else [0.0 for _symbol in symbols]
    max_weights = [float(item.get("maxWeight", 1.0)) for item in constraints] if constraints else [1.0 for _symbol in symbols]
    if len(min_weights) != len(symbols) or len(max_weights) != len(symbols):
        raise BacktestError("Vincoli efficient frontier non coerenti con il numero di asset.")
    if sum(min_weights) > 1 + 1e-9 or sum(max_weights) < 1 - 1e-9:
        raise BacktestError("Vincoli min/max non compatibili: la somma dei minimi deve essere <= 100% e la somma dei massimi >= 100%.")
    if sample_count is None:
        sample_count = max(2500, min(6000, int(12000 / math.sqrt(max(1, len(symbols))))))
    rng = random.Random(99)
    portfolios: list[dict[str, Any]] = []
    for _ in range(sample_count):
        weights = constrained_random_weights(min_weights, max_weights, rng)
        expected_return = sum(weight * mean for weight, mean in zip(weights, means))
        risk = math.sqrt(portfolio_variance(weights, stdevs, correlations))
        portfolios.append(
            {
                "weights": weights,
                "return": expected_return,
                "risk": risk,
                "sharpe": expected_return / risk if risk else 0,
            }
        )

    for index in range(len(symbols)):
        weights = [0.0] * len(symbols)
        weights[index] = 1.0
        if weights[index] > max_weights[index] + 1e-9 or any(weights[item] < min_weights[item] - 1e-9 for item in range(len(symbols))):
            continue
        expected_return = sum(weight * mean for weight, mean in zip(weights, means))
        risk = math.sqrt(portfolio_variance(weights, stdevs, correlations))
        portfolios.append({"weights": weights, "return": expected_return, "risk": risk, "sharpe": expected_return / risk if risk else 0})

    best = max(portfolios, key=lambda item: item["sharpe"])
    frontier = efficient_frontier(portfolios)
    step = max(1, len(frontier) // 40)
    point_step = max(1, len(portfolios) // 900)
    current_return = sum(weight * mean for weight, mean in zip(current_weights, means))
    current_risk = math.sqrt(portfolio_variance(current_weights, stdevs, correlations))
    return {
        "available": True,
        "symbols": symbols,
        "best": best,
        "frontier": frontier[::step],
        "points": [
            {"risk": item["risk"], "return": item["return"]}
            for index, item in enumerate(portfolios)
            if index % point_step == 0
        ],
        "current": {
            "weights": current_weights,
            "return": current_return,
            "risk": current_risk,
            "sharpe": current_return / current_risk if current_risk else 0,
        },
        "constraints": [
            {"symbol": symbol, "minWeight": min_weight, "maxWeight": max_weight}
            for symbol, min_weight, max_weight in zip(symbols, min_weights, max_weights)
        ],
        "correlations": correlations,
    }


def parse_stress_testing_settings(value: Any, portfolio: list[dict[str, Any]]) -> dict[str, Any]:
    settings = value if isinstance(value, dict) else {}
    enabled = bool(settings.get("enabled", False))
    direction = str(settings.get("direction", "up")).lower()
    try:
        rate_change_bps = int(settings.get("rateChangeBps", 100))
    except (TypeError, ValueError) as exc:
        raise BacktestError("Variazione tassi non valida.") from exc

    if direction not in {"up", "down"}:
        raise BacktestError("Direzione tassi non valida.")
    if rate_change_bps not in {25, 50, 100, 150, 200, 300}:
        raise BacktestError("La variazione tassi deve essere 25, 50, 100, 150, 200 o 300 punti base.")

    selected_bonds = [
        item
        for item in portfolio
        if item["assetClass"] == "bonds" and item.get("stressEnabled", False)
    ]
    if enabled and not selected_bonds:
        raise BacktestError("Seleziona almeno un asset obbligazionario per lo stress test.")
    signed_rate_change = rate_change_bps / 10000
    if direction == "down":
        signed_rate_change *= -1

    return {
        "enabled": enabled,
        "direction": direction,
        "rateChangeBps": rate_change_bps,
        "signedRateChange": signed_rate_change,
        "selectedSymbols": [item["symbol"] for item in selected_bonds],
    }


def parse_factor_stress_settings(value: Any) -> dict[str, Any]:
    settings = value if isinstance(value, dict) else {}
    scenario = str(settings.get("scenario") or "Global Recession")
    if scenario not in FACTOR_STRESS_SCENARIOS:
        scenario = "Global Recession"
    scenario_defaults = FACTOR_STRESS_SCENARIOS[scenario]
    shocks_input = settings.get("shocks") if isinstance(settings.get("shocks"), dict) else {}
    try:
        shocks = {
            factor: max(-0.5, min(0.5, float(shocks_input.get(factor, scenario_defaults[factor]))))
            for factor in FACTOR_STRESS_FACTORS
        }
    except (TypeError, ValueError) as exc:
        raise BacktestError("Shock fattoriale non valido.") from exc
    return {
        "enabled": bool(settings.get("enabled", True)),
        "scenario": scenario,
        "useCustomShock": bool(settings.get("useCustomShock", False)),
        "shocks": shocks,
        "runMonteCarlo": bool(settings.get("runMonteCarlo", False)),
    }


def build_factor_stress_payload(
    portfolio: list[dict[str, Any]],
    start: str,
    end: str,
    data_source: str,
    feed: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "use_demo_data": data_source == "demo",
        "feed": feed,
        "portfolio": [
            {
                "symbol": item["symbol"],
                "weight": item["weight"],
                "asset_class": {
                    "equity": "etf",
                    "bonds": "bond",
                    "gold": "gold",
                    "commodities": "commodity",
                }.get(item["assetClass"], "etf"),
            }
            for item in portfolio
        ],
        "scenario": "Custom Shock" if settings["useCustomShock"] else settings["scenario"],
        "shocks": settings["shocks"] if settings["useCustomShock"] else None,
    }


def estimate_bond_duration(symbol: str, daily_returns: list[float]) -> tuple[float, str]:
    lookup = {
        "TLT": 16.5,
        "IEF": 7.4,
        "SHY": 1.9,
        "AGG": 6.0,
        "BND": 6.1,
        "LQD": 8.4,
        "HYG": 3.6,
        "TIP": 6.6,
        "MUB": 5.8,
        "BIL": 0.2,
        "SHV": 0.3,
    }
    if symbol in lookup:
        return lookup[symbol], "lookup"

    if len(daily_returns) < 2:
        return 5.0, "default"
    annualized_volatility = sample_stdev(daily_returns) * math.sqrt(252)
    # Fallback data-driven proxy: for unknown bond tickers, use annualized volatility
    # as a rough effective-duration estimate against a 100 bps shock, bounded to sane values.
    return min(20.0, max(0.5, annualized_volatility / 0.01)), "historical_proxy"


def build_close_by_symbol(
    price_data: dict[str, list[PricePoint]],
    common_days: list[str],
    asset_classes: dict[str, str],
) -> dict[str, dict[str, float]]:
    close_by_symbol: dict[str, dict[str, float]] = {}
    for symbol, points in price_data.items():
        original = {point.day: point.close for point in points}
        close_by_symbol[symbol] = {day: original[day] for day in common_days}
    return close_by_symbol


def apply_interest_rate_stress(
    close_by_symbol: dict[str, dict[str, float]],
    common_days: list[str],
    portfolio: list[dict[str, Any]],
    stress_settings: dict[str, Any],
    return_by_symbol: dict[str, list[float]],
) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
    stressed = {symbol: closes.copy() for symbol, closes in close_by_symbol.items()}
    selected = set(stress_settings["selectedSymbols"])
    impacts: list[dict[str, Any]] = []
    if not stress_settings["enabled"]:
        return stressed, impacts

    total_steps = max(len(common_days) - 1, 1)
    for item in portfolio:
        symbol = item["symbol"]
        if symbol not in selected:
            continue
        first_price = close_by_symbol[symbol][common_days[0]]
        duration, duration_source = estimate_bond_duration(symbol, return_by_symbol.get(symbol, []))
        price_impact = -duration * stress_settings["signedRateChange"]
        final_multiplier = max(0.01, 1 + price_impact)
        stressed[symbol] = {}
        for index, day in enumerate(common_days):
            progress = index / total_steps
            stressed[symbol][day] = first_price * (1 + (final_multiplier - 1) * progress)
        impacts.append(
            {
                "symbol": symbol,
                "duration": duration,
                "durationSource": duration_source,
                "priceImpact": price_impact,
                "finalMultiplier": final_multiplier,
            }
        )
    return stressed, impacts


def simulate_portfolio(
    common_days: list[str],
    price_data: dict[str, list[PricePoint]],
    symbols: list[str],
    weights: dict[str, float],
    asset_classes: dict[str, str],
    initial_capital: float,
    rebalance_frequency: str,
    close_by_symbol_override: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    close_by_symbol = close_by_symbol_override or build_close_by_symbol(price_data, common_days, asset_classes)
    first_day = common_days[0]
    shares = {
        symbol: (initial_capital * weights[symbol]) / close_by_symbol[symbol][first_day]
        for symbol in symbols
    }
    equity = initial_capital
    equity_curve = [{"date": first_day, "value": round(equity, 2), "return": 0.0, "rebalanced": True}]
    daily_portfolio_returns: list[float] = []
    rebalance_dates: list[str] = []

    for previous_day, day in zip(common_days, common_days[1:]):
        previous_equity = equity
        equity = sum(shares[symbol] * close_by_symbol[symbol][day] for symbol in symbols)
        daily_return = equity / previous_equity - 1
        daily_portfolio_returns.append(daily_return)

        rebalanced = should_rebalance(previous_day, day, rebalance_frequency)
        if rebalanced:
            rebalance_dates.append(day)
            shares = {
                symbol: (equity * weights[symbol]) / close_by_symbol[symbol][day]
                for symbol in symbols
            }

        equity_curve.append(
            {
                "date": day,
                "value": round(equity, 2),
                "return": daily_return,
                "rebalanced": rebalanced,
            }
        )

    final_prices = {symbol: close_by_symbol[symbol][common_days[-1]] for symbol in symbols}
    final_asset_values = {symbol: shares[symbol] * final_prices[symbol] for symbol in symbols}
    return {
        "closeBySymbol": close_by_symbol,
        "equity": equity,
        "equityCurve": equity_curve,
        "dailyReturns": daily_portfolio_returns,
        "rebalanceDates": rebalance_dates,
        "finalAssetValues": final_asset_values,
    }


def build_metrics(equity_curve: list[dict[str, Any]], daily_returns: list[float], initial_capital: float) -> dict[str, Any]:
    final_value = equity_curve[-1]["value"]
    total_return = final_value / initial_capital - 1
    years = max(
        (parse_date(equity_curve[-1]["date"], "fine") - parse_date(equity_curve[0]["date"], "inizio")).days / 365.25,
        1 / 365.25,
    )
    return {
        "initialCapital": initial_capital,
        "finalValue": final_value,
        "totalReturn": total_return,
        "cagr": (final_value / initial_capital) ** (1 / years) - 1,
        "maxDrawdown": max_drawdown([point["value"] for point in equity_curve]),
        "sharpe": sharpe_ratio(daily_returns),
        "sortino": sortino_ratio(daily_returns),
        "bestDay": max(daily_returns) if daily_returns else 0,
        "worstDay": min(daily_returns) if daily_returns else 0,
    }


def rolling_window_metrics(daily_returns: list[float], window: int = 252, minimum_window: int = 30) -> dict[str, Any]:
    if len(daily_returns) < minimum_window:
        return {
            "available": False,
            "reason": f"Servono almeno {minimum_window} rendimenti giornalieri comuni per calcolare rendimenti rolling e rapporto rendimento-rischio negativo.",
            "window": min(window, len(daily_returns)),
            "points": [],
        }
    effective_window = min(window, len(daily_returns))
    points = []
    for end_index in range(effective_window, len(daily_returns) + 1):
        window_returns = daily_returns[end_index - effective_window : end_index]
        cumulative = 1.0
        for item in window_returns:
            cumulative *= 1 + item
        rolling_return = cumulative - 1
        volatility = sample_stdev(window_returns) * math.sqrt(252)
        sharpe = sharpe_ratio(window_returns)
        sortino = sortino_ratio(window_returns)
        points.append(
            {
                "index": end_index,
                "rollingReturn": rolling_return,
                "rollingVolatility": volatility,
                "rollingSharpe": sharpe,
                "rollingSortino": sortino,
            }
        )
    returns = [point["rollingReturn"] for point in points]
    sortinos = [point["rollingSortino"] for point in points if point["rollingSortino"] is not None]
    latest = points[-1]
    return {
        "available": True,
        "window": effective_window,
        "requestedWindow": window,
        "latest": latest,
        "bestRollingReturn": max(returns) if returns else None,
        "worstRollingReturn": min(returns) if returns else None,
        "averageRollingReturn": sum(returns) / len(returns) if returns else None,
        "averageSortino": sum(sortinos) / len(sortinos) if sortinos else None,
        "points": points[:: max(1, len(points) // 160)],
    }


ROLLING_WINDOW_OPTIONS = {
    "1y": {"label": "1 anno", "days": 252},
    "2y": {"label": "2 anni", "days": 504},
    "6m": {"label": "6 mesi", "days": 126},
}


def build_rolling_windows(
    current_daily_returns: list[float],
    optimized_daily_returns: list[float] | None,
    comparison_unavailable_reason: str = "Portafoglio di confronto non disponibile.",
) -> dict[str, Any]:
    windows: dict[str, Any] = {}
    for key, option in ROLLING_WINDOW_OPTIONS.items():
        current = rolling_window_metrics(current_daily_returns, option["days"])
        current["windowKey"] = key
        current["windowLabel"] = option["label"]
        if optimized_daily_returns:
            optimized = rolling_window_metrics(optimized_daily_returns, option["days"])
            optimized["windowKey"] = key
            optimized["windowLabel"] = option["label"]
        else:
            optimized = {
                "available": False,
                "reason": comparison_unavailable_reason,
                "windowKey": key,
                "windowLabel": option["label"],
                "window": option["days"],
                "requestedWindow": option["days"],
                "points": [],
            }
        windows[key] = {
            "label": option["label"],
            "days": option["days"],
            "current": current,
            "optimized": optimized,
        }
    return windows


def solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for pivot_index in range(size):
        pivot_row = max(range(pivot_index, size), key=lambda row_index: abs(augmented[row_index][pivot_index]))
        if abs(augmented[pivot_row][pivot_index]) < 1e-12:
            return None
        augmented[pivot_index], augmented[pivot_row] = augmented[pivot_row], augmented[pivot_index]
        pivot = augmented[pivot_index][pivot_index]
        augmented[pivot_index] = [value / pivot for value in augmented[pivot_index]]
        for row_index in range(size):
            if row_index == pivot_index:
                continue
            factor = augmented[row_index][pivot_index]
            augmented[row_index] = [
                value - factor * augmented[pivot_index][column_index]
                for column_index, value in enumerate(augmented[row_index])
            ]
    return [augmented[index][-1] for index in range(size)]


def ols_regression(y_values: list[float], x_rows: list[list[float]]) -> dict[str, Any]:
    if len(y_values) < 12 or len(y_values) != len(x_rows):
        return {"available": False, "reason": "Dati insufficienti per regressione OLS."}
    design = [[1.0, *row] for row in x_rows]
    columns = len(design[0])
    xtx = [[0.0 for _ in range(columns)] for _ in range(columns)]
    xty = [0.0 for _ in range(columns)]
    for row, y_value in zip(design, y_values):
        for i in range(columns):
            xty[i] += row[i] * y_value
            for j in range(columns):
                xtx[i][j] += row[i] * row[j]
    coefficients = solve_linear_system(xtx, xty)
    if coefficients is None:
        ridge_xtx = [row[:] for row in xtx]
        for index in range(columns):
            ridge_xtx[index][index] += 1e-8
        coefficients = solve_linear_system(ridge_xtx, xty)
        if coefficients is None:
            return {"available": False, "reason": "Matrice fattoriale non invertibile."}
    fitted = [sum(coef * value for coef, value in zip(coefficients, row)) for row in design]
    mean_y = sum(y_values) / len(y_values)
    ss_total = sum((item - mean_y) ** 2 for item in y_values)
    ss_error = sum((actual - predicted) ** 2 for actual, predicted in zip(y_values, fitted))
    r_squared = 1 - ss_error / ss_total if ss_total else 0
    degrees = max(1, len(y_values) - columns)
    mse = ss_error / degrees
    inverse_xtx_diag = inverse_diagonal(xtx)
    if any(value is None for value in inverse_xtx_diag):
        inverse_xtx_diag = inverse_diagonal(ridge_xtx if "ridge_xtx" in locals() else xtx)
    standard_errors = [math.sqrt(max(0.0, mse * value)) if value is not None else None for value in inverse_xtx_diag]
    t_stats = [
        coefficients[index] / standard_errors[index]
        if standard_errors[index] not in {None, 0}
        else None
        for index in range(columns)
    ]
    p_values = [
        min(1.0, math.erfc(abs(t_stat) / math.sqrt(2))) if t_stat is not None else None
        for t_stat in t_stats
    ]
    return {
        "available": True,
        "alpha": coefficients[0],
        "betas": coefficients[1:],
        "rSquared": r_squared,
        "pValues": p_values[1:],
    }


def inverse_diagonal(matrix: list[list[float]]) -> list[float | None]:
    diagonal = []
    for index in range(len(matrix)):
        unit = [0.0 for _ in matrix]
        unit[index] = 1.0
        solution = solve_linear_system([row[:] for row in matrix], unit)
        diagonal.append(solution[index] if solution else None)
    return diagonal


def build_style_factor_matrix(
    series_by_symbol: dict[str, list[float]],
    symbols: list[str],
    asset_classes: dict[str, str],
) -> dict[str, Any]:
    if not symbols or not series_by_symbol:
        return {"available": False, "reason": "Dati insufficienti per fattori stile Fama-French."}
    length = min(len(series_by_symbol[symbol]) for symbol in symbols)
    if length < 12:
        return {"available": False, "reason": "Servono più rendimenti storici per fattori stile Fama-French."}
    trimmed = {symbol: series_by_symbol[symbol][-length:] for symbol in symbols}
    vol_rank = sorted(symbols, key=lambda symbol: sample_stdev(trimmed[symbol]))
    momentum_rank = sorted(symbols, key=lambda symbol: sum(trimmed[symbol][-min(63, length) :]))
    half = max(1, len(symbols) // 2)
    low_vol = set(vol_rank[:half])
    high_vol = set(vol_rank[-half:])
    low_momentum = set(momentum_rank[:half])
    high_momentum = set(momentum_rank[-half:])

    factors: list[dict[str, float]] = []
    for index in range(length):
        market = sum(trimmed[symbol][index] for symbol in symbols) / len(symbols)
        small_proxy = average_group_return(trimmed, low_vol, index) - average_group_return(trimmed, high_vol, index)
        value_proxy = average_group_return(trimmed, high_momentum, index) - average_group_return(trimmed, low_momentum, index)
        momentum = value_proxy
        profitability = average_group_return(trimmed, {symbol for symbol in symbols if asset_classes.get(symbol) == "equity"}, index) - market
        investment = average_group_return(trimmed, {symbol for symbol in symbols if asset_classes.get(symbol) in {"bonds", "gold"}}, index) - market
        factors.append(
            {
                "market": market,
                "size": small_proxy,
                "value": value_proxy,
                "momentum": momentum,
                "profitability": profitability,
                "investment": investment,
            }
        )
    return {"available": True, "factors": factors, "factorNames": ["market", "size", "value", "momentum", "profitability", "investment"]}


def average_group_return(series_by_symbol: dict[str, list[float]], symbols: set[str], index: int) -> float:
    selected = [series_by_symbol[symbol][index] for symbol in symbols if symbol in series_by_symbol]
    if not selected:
        return 0.0
    return sum(selected) / len(selected)


def weighted_return_series(series_by_symbol: dict[str, list[float]], symbols: list[str], weights: dict[str, float]) -> list[float]:
    length = min(len(series_by_symbol[symbol]) for symbol in symbols)
    return [
        sum(weights[symbol] * series_by_symbol[symbol][-length + index] for symbol in symbols)
        for index in range(length)
    ]


FAMA_FRENCH_DATASETS = {
    "developed_daily_5": {
        "file": "Developed_5_Factors_Daily.csv",
        "frequency": "daily",
        "region": "Developed",
    },
    "north_america_5": {
        "file": "North_America_5_Factors.csv",
        "frequency": "monthly",
        "region": "North America",
        "momentumFile": "Developed_MOM_Factor.csv",
    },
    "europe_5": {
        "file": "Europe_5_Factors.csv",
        "frequency": "monthly",
        "region": "Europe",
        "momentumFile": "Developed_MOM_Factor.csv",
    },
    "japan_5": {
        "file": "Japan_5_Factors.csv",
        "frequency": "monthly",
        "region": "Japan",
        "momentumFile": "Developed_MOM_Factor.csv",
    },
    "asia_pacific_ex_japan_5": {
        "file": "Asia_Pacific_ex_Japan_5_Factors.csv",
        "frequency": "monthly",
        "region": "Asia Pacific ex Japan",
        "momentumFile": "Developed_MOM_Factor.csv",
    },
    "emerging_5": {
        "file": "Emerging_5_Factors.csv",
        "frequency": "monthly",
        "region": "Emerging Markets",
        "momentumFile": "Emerging_MOM_Factor.csv",
    },
    "developed_3": {
        "file": "Developed_3_Factors.csv",
        "frequency": "monthly",
        "region": "Developed",
        "momentumFile": "Developed_MOM_Factor.csv",
    },
    "us_3": {
        "file": "F-F_Research_Data_Factors.csv",
        "frequency": "monthly",
        "region": "United States",
        "momentumFile": "Developed_MOM_Factor.csv",
    },
}


def parse_momentum_file(file_name: str) -> dict[str, float]:
    cache_key_name = f"mom::{file_name}"
    if cache_key_name in FAMA_FRENCH_CACHE:
        return FAMA_FRENCH_CACHE[cache_key_name].get("rows", {})
    path = FAMA_FRENCH_DIR / file_name
    rows: dict[str, float] = {}
    if not path.exists():
        FAMA_FRENCH_CACHE[cache_key_name] = {"rows": rows}
        return rows
    has_header = False
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        if parts[0] == "" and any(name.upper() in {"WML", "MOM"} for name in parts[1:]):
            has_header = True
            continue
        key = parts[0].strip()
        if not has_header or not key.isdigit() or len(key) != 6:
            continue
        try:
            parsed = float(parts[1]) / 100
        except ValueError:
            continue
        if parsed > -0.9998:
            rows[key] = parsed
    FAMA_FRENCH_CACHE[cache_key_name] = {"rows": rows}
    return rows


def parse_fama_french_file(dataset_key: str) -> dict[str, Any]:
    if dataset_key in FAMA_FRENCH_CACHE:
        return FAMA_FRENCH_CACHE[dataset_key]
    meta = FAMA_FRENCH_DATASETS[dataset_key]
    path = FAMA_FRENCH_DIR / meta["file"]
    rows: dict[str, dict[str, float]] = {}
    factor_names: list[str] = []
    if not path.exists():
        return {"available": False, "reason": f"Dataset Fama-French mancante: {meta['file']}"}
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        if parts[0] == "" and "Mkt-RF" in parts:
            factor_names = [name for name in parts[1:] if name]
            continue
        key = parts[0].strip()
        if not key.isdigit():
            continue
        if meta["frequency"] == "daily" and len(key) != 8:
            continue
        if meta["frequency"] == "monthly" and len(key) != 6:
            continue
        values: dict[str, float] = {}
        for name, value in zip(factor_names, parts[1:]):
            try:
                parsed = float(value) / 100
            except ValueError:
                parsed = -0.9999
            if parsed <= -0.9998:
                continue
            values[name] = parsed
        if values:
            rows[key] = values
    momentum_file = meta.get("momentumFile")
    if meta["frequency"] == "monthly" and momentum_file:
        momentum_rows = parse_momentum_file(str(momentum_file))
        if momentum_rows:
            for key, value in momentum_rows.items():
                if key in rows:
                    rows[key]["MOM"] = value
            if "MOM" not in factor_names:
                factor_names.append("MOM")
    effective_factor_names = [
        name
        for name in factor_names
        if name != "RF" and any(name in row for row in rows.values())
    ]
    payload = {
        "available": bool(rows and factor_names),
        "dataset": dataset_key,
        "file": meta["file"],
        "momentumFile": momentum_file,
        "frequency": meta["frequency"],
        "region": meta["region"],
        "factorNames": effective_factor_names,
        "rows": rows,
    }
    FAMA_FRENCH_CACHE[dataset_key] = payload
    return payload


def month_key(day: str) -> str:
    parsed = parse_date(day, "giorno")
    return f"{parsed.year}{parsed.month:02d}"


def daily_key(day: str) -> str:
    return parse_date(day, "giorno").strftime("%Y%m%d")


def monthly_portfolio_returns(
    common_days: list[str],
    series_by_symbol: dict[str, list[float]],
    symbols: list[str],
    weights: dict[str, float],
) -> dict[str, float]:
    daily = weighted_return_series(series_by_symbol, symbols, weights)
    result: dict[str, float] = {}
    current_month = ""
    cumulative = 1.0
    for day, daily_return in zip(common_days[1:], daily):
        key = month_key(day)
        if current_month and key != current_month:
            result[current_month] = cumulative - 1
            cumulative = 1.0
        current_month = key
        cumulative *= 1 + daily_return
    if current_month:
        result[current_month] = cumulative - 1
    return result


def daily_portfolio_returns_by_date(
    common_days: list[str],
    series_by_symbol: dict[str, list[float]],
    symbols: list[str],
    weights: dict[str, float],
) -> dict[str, float]:
    daily = weighted_return_series(series_by_symbol, symbols, weights)
    return {daily_key(day): value for day, value in zip(common_days[1:], daily)}


def choose_fama_french_dataset(symbols: list[str]) -> str:
    geos = [resolve_geo_metadata(symbol) for symbol in symbols]
    countries = {geo["country"] for geo in geos if geo["country"] in COUNTRY_TO_CONTINENT}
    countries.update(symbol[:2].upper() for symbol in symbols if looks_like_isin(symbol) and symbol[:2].upper() in COUNTRY_TO_CONTINENT)
    continents = {geo["continent"] for geo in geos if geo["continent"] != "Non classificato"}
    continents.update(COUNTRY_TO_CONTINENT[country] for country in countries if country in COUNTRY_TO_CONTINENT)
    upper_symbols = {symbol.upper() for symbol in symbols}
    emerging_symbol_hints = {"EEM", "IEMG", "VWO", "EMIM", "EIMI", "XMME", "SEMA"}
    if upper_symbols & emerging_symbol_hints or any(country in EMERGING_COUNTRIES for country in countries):
        return "emerging_5"
    if countries and countries <= {"US", "CA"}:
        return "north_america_5"
    if countries and countries <= {"GB", "IE", "DE", "FR", "IT", "CH", "NL", "ES"}:
        return "europe_5"
    if countries == {"JP"}:
        return "japan_5"
    if countries and countries <= {"AU", "HK", "SG", "NZ"}:
        return "asia_pacific_ex_japan_5"
    if "Nord America" in continents and len(continents) == 1:
        return "north_america_5"
    if "Europa" in continents and len(continents) == 1:
        return "europe_5"
    return "developed_daily_5"


def run_official_fama_french_regression(
    dataset_key: str,
    common_days: list[str],
    series_by_symbol: dict[str, list[float]],
    symbols: list[str],
    weights: dict[str, float],
) -> dict[str, Any]:
    dataset = parse_fama_french_file(dataset_key)
    if not dataset.get("available"):
        return dataset
    returns = (
        daily_portfolio_returns_by_date(common_days, series_by_symbol, symbols, weights)
        if dataset["frequency"] == "daily"
        else monthly_portfolio_returns(common_days, series_by_symbol, symbols, weights)
    )
    y_values: list[float] = []
    x_rows: list[list[float]] = []
    factor_names = dataset["factorNames"]
    for key in sorted(set(returns) & set(dataset["rows"])):
        factor_row = dataset["rows"][key]
        if not all(name in factor_row for name in factor_names):
            continue
        risk_free = factor_row.get("RF", 0.0)
        y_values.append(returns[key] - risk_free)
        x_rows.append([factor_row[name] for name in factor_names])
    if len(y_values) < max(12, len(factor_names) + 4):
        return {
            "available": False,
            "reason": f"Dati sovrapposti insufficienti con {dataset['file']}.",
            "dataset": dataset_key,
        }
    regression = ols_regression(y_values, x_rows)
    if not regression.get("available"):
        return regression
    return {
        "available": True,
        "method": "Kenneth French Data Library ufficiale locale",
        "dataset": dataset_key,
        "datasetFile": dataset["file"],
        "momentumFile": dataset.get("momentumFile"),
        "region": dataset["region"],
        "frequency": dataset["frequency"],
        "includesMomentum": "MOM" in factor_names,
        "observations": len(y_values),
        "factorNames": factor_names,
        "alpha": regression["alpha"],
        "betas": [
            {"factor": factor, "beta": beta, "pValue": p_value}
            for factor, beta, p_value in zip(factor_names, regression["betas"], regression["pValues"])
        ],
        "rSquared": regression["rSquared"],
    }


def build_fama_french_analysis(
    series_by_symbol: dict[str, list[float]],
    common_days: list[str],
    symbols: list[str],
    weights: dict[str, float],
    asset_classes: dict[str, str],
) -> dict[str, Any]:
    official = run_official_fama_french_regression(
        choose_fama_french_dataset(symbols),
        common_days,
        series_by_symbol,
        symbols,
        weights,
    )
    if official.get("available"):
        return official
    factor_matrix = build_style_factor_matrix(series_by_symbol, symbols, asset_classes)
    if not factor_matrix.get("available"):
        return factor_matrix
    y_values = weighted_return_series(series_by_symbol, symbols, weights)
    x_rows = [[row[name] for name in factor_matrix["factorNames"]] for row in factor_matrix["factors"][-len(y_values) :]]
    regression = ols_regression(y_values, x_rows)
    if not regression.get("available"):
        return regression
    return {
        "available": True,
        "method": f"Fallback Fama-French style proxy: {official.get('reason', 'dataset ufficiale non allineabile')}",
        "factorNames": factor_matrix["factorNames"],
        "alpha": regression["alpha"],
        "betas": [
            {"factor": factor, "beta": beta, "pValue": p_value}
            for factor, beta, p_value in zip(factor_matrix["factorNames"], regression["betas"], regression["pValues"])
        ],
        "rSquared": regression["rSquared"],
    }


COUNTRY_TO_CONTINENT = {
    "US": "Nord America",
    "CA": "Nord America",
    "GB": "Europa",
    "IE": "Europa",
    "DE": "Europa",
    "FR": "Europa",
    "IT": "Europa",
    "CH": "Europa",
    "NL": "Europa",
    "ES": "Europa",
    "JP": "Asia",
    "CN": "Asia",
    "HK": "Asia",
    "SG": "Asia",
    "IN": "Asia",
    "KR": "Asia",
    "TW": "Asia",
    "BR": "Sud America",
    "MX": "Nord America",
    "ZA": "Africa",
    "NZ": "Oceania",
    "AU": "Oceania",
}

EMERGING_COUNTRIES = {"BR", "CN", "IN", "KR", "MX", "TW", "ZA"}


EXCHANGE_TO_COUNTRY = {
    "US": "US",
    "NASDAQ": "US",
    "NYSE": "US",
    "LSE": "GB",
    "LN": "GB",
    "XETRA": "DE",
    "GY": "DE",
    "GR": "DE",
    "PA": "FR",
    "FP": "FR",
    "MI": "IT",
    "IM": "IT",
    "SW": "CH",
    "AS": "NL",
    "MC": "ES",
}


def resolve_geo_metadata(symbol: str) -> dict[str, str]:
    cache = load_market_data_cache()
    instrument = cache.get("instruments", {}).get(cache_key(symbol), {})
    exchange = str(instrument.get("eodhdExchange") or instrument.get("exchange") or "").upper()
    isin = str(instrument.get("isin") or "").upper()
    country = ""
    method = "unclassified"
    if len(isin) >= 2 and isin[:2].isalpha() and isin[:2] in COUNTRY_TO_CONTINENT:
        country = isin[:2]
        method = "isin_country"
    elif exchange in EXCHANGE_TO_COUNTRY:
        country = EXCHANGE_TO_COUNTRY[exchange]
        method = "listing_exchange"
    if not country:
        return {"country": "Non classificato", "continent": "Non classificato", "method": method}
    return {"country": country, "continent": COUNTRY_TO_CONTINENT.get(country, "Non classificato"), "method": method}


def build_geographic_exposure(symbols: list[str], weights: dict[str, float]) -> dict[str, Any]:
    countries: dict[str, float] = {}
    continents: dict[str, float] = {}
    instruments = []
    for symbol in symbols:
        geo = resolve_geo_metadata(symbol)
        weight = weights.get(symbol, 0.0)
        countries[geo["country"]] = countries.get(geo["country"], 0.0) + weight
        continents[geo["continent"]] = continents.get(geo["continent"], 0.0) + weight
        instruments.append({"symbol": symbol, "weight": weight, **geo})
    return {
        "available": True,
        "method": "Esposizione geografica proxy da ISIN/exchange OpenFIGI; non equivale sempre alle holdings geografiche dell'ETF.",
        "continents": [{"name": key, "weight": value} for key, value in sorted(continents.items(), key=lambda item: item[1], reverse=True)],
        "countries": [{"name": key, "weight": value} for key, value in sorted(countries.items(), key=lambda item: item[1], reverse=True)],
        "instruments": instruments,
    }


def build_drawdown_analysis(equity_curve: list[dict[str, Any]]) -> dict[str, Any]:
    if not equity_curve:
        return {"available": False, "reason": "Curva equity non disponibile."}
    peak_value = float(equity_curve[0]["value"])
    peak_date = equity_curve[0]["date"]
    worst_peak_value = peak_value
    worst_peak_date = peak_date
    trough_value = peak_value
    trough_date = peak_date
    worst = 0.0
    current_start = peak_date
    current_duration = 0
    max_duration = 0
    recovery_days = 0
    recovered = True
    episodes = []
    for index, point in enumerate(equity_curve):
        value = float(point["value"])
        day = point["date"]
        if value >= peak_value:
            if not recovered:
                episodes.append(
                    {
                        "peakDate": current_start,
                        "troughDate": trough_date,
                        "recoveryDate": day,
                        "drawdown": worst,
                        "durationDays": current_duration,
                        "recoveryDays": recovery_days,
                    }
                )
            peak_value = value
            peak_date = day
            current_start = day
            current_duration = 0
            recovery_days = 0
            recovered = True
            continue
        recovered = False
        drawdown = value / peak_value - 1 if peak_value else 0
        current_duration += 1
        recovery_days += 1
        max_duration = max(max_duration, current_duration)
        if drawdown < worst:
            worst = drawdown
            worst_peak_value = peak_value
            worst_peak_date = peak_date
            trough_value = value
            trough_date = day
    peak_to_trough_days = max(0, (parse_date(trough_date, "trough") - parse_date(worst_peak_date, "peak")).days)
    return {
        "available": True,
        "maxDrawdown": worst,
        "peakDate": worst_peak_date,
        "troughDate": trough_date,
        "peakValue": worst_peak_value,
        "troughValue": trough_value,
        "peakToTroughDays": peak_to_trough_days,
        "maxDrawdownDurationDays": max_duration,
        "recovered": recovered,
        "recoveryDays": 0 if recovered else None,
        "episodes": episodes[-8:],
    }


def build_correlation_analysis(series_by_symbol: dict[str, list[float]], symbols: list[str]) -> dict[str, Any]:
    if len(symbols) < 2:
        return {"available": False, "reason": "Servono almeno due asset per la matrice di correlazione."}
    matrix = correlation_matrix(series_by_symbol, symbols)
    display_symbols = {symbol: public_display_name_for_symbol(symbol) for symbol in symbols}
    pairs = []
    for i, symbol_i in enumerate(symbols):
        for j, symbol_j in enumerate(symbols):
            if j <= i:
                continue
            pairs.append(
                {
                    "pair": f"{symbol_i}/{symbol_j}",
                    "displayPair": f"{display_symbols.get(symbol_i, symbol_i)} / {display_symbols.get(symbol_j, symbol_j)}",
                    "correlation": matrix[i][j],
                }
            )
    average_abs = sum(abs(item["correlation"]) for item in pairs) / len(pairs) if pairs else 0
    highest = max(pairs, key=lambda item: item["correlation"], default=None)
    lowest = min(pairs, key=lambda item: item["correlation"], default=None)
    return {
        "available": True,
        "symbols": symbols,
        "displaySymbols": display_symbols,
        "matrix": matrix,
        "pairs": sorted(pairs, key=lambda item: abs(item["correlation"]), reverse=True),
        "averageAbsoluteCorrelation": average_abs,
        "highestPair": highest,
        "lowestPair": lowest,
    }


def simulate_pac(
    common_days: list[str],
    price_data: dict[str, list[PricePoint]],
    symbols: list[str],
    weights: dict[str, float],
    asset_classes: dict[str, str],
    initial_capital: float,
    monthly_contribution: float | None = None,
) -> dict[str, Any]:
    contribution = monthly_contribution if monthly_contribution is not None else max(100.0, initial_capital * 0.01)
    close_by_symbol = build_close_by_symbol(price_data, common_days, asset_classes)
    first_day = common_days[0]
    shares = {symbol: (initial_capital * weights[symbol]) / close_by_symbol[symbol][first_day] for symbol in symbols}
    invested = initial_capital
    previous_month = parse_date(first_day, "inizio").month
    contributions = []
    curve = []
    for day in common_days:
        parsed = parse_date(day, "giorno")
        if day != first_day and parsed.month != previous_month:
            value = sum(shares[symbol] * close_by_symbol[symbol][day] for symbol in symbols)
            for symbol in symbols:
                shares[symbol] += (contribution * weights[symbol]) / close_by_symbol[symbol][day]
            invested += contribution
            contributions.append({"date": day, "amount": contribution, "valueBeforeContribution": value})
            previous_month = parsed.month
        value = sum(shares[symbol] * close_by_symbol[symbol][day] for symbol in symbols)
        curve.append({"date": day, "value": round(value, 2), "invested": round(invested, 2)})
    final_value = curve[-1]["value"] if curve else initial_capital
    return {
        "available": True,
        "monthlyContribution": contribution,
        "totalInvested": invested,
        "finalValue": final_value,
        "gain": final_value - invested,
        "gainPercent": final_value / invested - 1 if invested else 0,
        "contributionCount": len(contributions),
        "curve": curve[:: max(1, len(curve) // 180)],
    }


def equal_weight_benchmark(
    common_days: list[str],
    price_data: dict[str, list[PricePoint]],
    symbols: list[str],
    asset_classes: dict[str, str],
    initial_capital: float,
) -> dict[str, Any]:
    if not symbols:
        return {"available": False, "reason": "Nessun asset per benchmark."}
    weights = {symbol: 1 / len(symbols) for symbol in symbols}
    simulation = simulate_portfolio(common_days, price_data, symbols, weights, asset_classes, initial_capital, "monthly")
    metrics = build_metrics(simulation["equityCurve"], simulation["dailyReturns"], initial_capital)
    return {
        "available": True,
        "name": "Confronto equal-weight sugli stessi strumenti",
        "method": "Non e un indice di mercato esterno: pesa allo stesso modo gli strumenti inseriti.",
        "metrics": metrics,
        "equityCurve": simulation["equityCurve"],
    }


def round_weight(value: float) -> float:
    return round(float(value) * 10) / 10


def normalize_benchmark_weights(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(holding.get("weight") or 0) for holding in holdings)
    if total == 0:
        return holdings
    normalized = [
        {
            **holding,
            "weight": round_weight(float(holding.get("weight") or 0) / total * 100),
        }
        for holding in holdings
    ]
    normalized_total = sum(float(holding.get("weight") or 0) for holding in normalized)
    diff = round_weight(100 - normalized_total)
    if diff and normalized:
        normalized[0]["weight"] = round_weight(float(normalized[0].get("weight") or 0) + diff)
    return normalized


normalizeBenchmarkWeights = normalize_benchmark_weights


def benchmark_definition(benchmark_id: str) -> dict[str, Any]:
    return json.loads(json.dumps(BENCHMARK_DEFINITIONS[benchmark_id]))


def empty_portfolio_exposures() -> dict[str, Any]:
    return {
        "equityExposure": 0.0,
        "bondExposure": 0.0,
        "goldExposure": 0.0,
        "commodityExposure": 0.0,
        "cashLikeExposure": 0.0,
        "unknownExposure": 0.0,
        "equityRegions": {
            "global": 0.0,
            "usa": 0.0,
            "europe": 0.0,
            "emerging_markets": 0.0,
            "developed_ex_usa": 0.0,
            "other": 0.0,
        },
        "bondTypes": {
            "aggregate": 0.0,
            "government": 0.0,
            "corporate": 0.0,
            "high_yield": 0.0,
            "short_duration": 0.0,
            "long_duration": 0.0,
            "floating_rate": 0.0,
            "other": 0.0,
        },
    }


def classify_benchmark_asset(item: dict[str, Any]) -> dict[str, Any]:
    symbol = str(item.get("symbol") or "").upper()
    text = f"{symbol} {item.get('displayName', '')} {item.get('name', '')}".lower()
    asset_class = str(item.get("assetClass") or "").lower()

    if asset_class == "equity":
        macro = "equity"
    elif asset_class == "bonds":
        macro = "bond"
    elif asset_class == "gold":
        macro = "gold"
    elif asset_class == "commodities":
        macro = "commodity"
    else:
        macro = "unknown"

    if any(token in text for token in ("cash", "ultrashort", "0-1", "1-3", "short", "liquid", "shv", "bil")):
        if macro == "bond":
            macro = "cash_like"

    region = "other"
    if macro == "equity":
        if any(token in text for token in ("s&p 500", "sp 500", "s&p500", "usa", "u.s.", "us equity", "nasdaq", "msft", "microsoft")):
            region = "usa"
        elif any(token in text for token in ("europe", "euro stoxx", "stoxx", "europa", "eur equity")):
            region = "europe"
        elif any(token in text for token in ("emerging", "em ", "emerging markets", "em ex-china", "mercati emergenti")):
            region = "emerging_markets"
        elif any(token in text for token in ("ex-usa", "ex usa", "developed ex")):
            region = "developed_ex_usa"
        elif any(token in text for token in ("acwi", "world", "all-world", "all world", "msci global", "global")):
            region = "global"

    bond_type = "other"
    if macro in {"bond", "cash_like"}:
        if macro == "cash_like":
            bond_type = "short_duration"
        elif any(token in text for token in ("aggregate", "global aggregate", "agg")):
            bond_type = "aggregate"
        elif any(token in text for token in ("corporate", "corp", "investment grade")):
            bond_type = "corporate"
        elif any(token in text for token in ("government", "govt", "treasury", "governativ")):
            bond_type = "government"
        elif any(token in text for token in ("high yield", "hyg", "junk")):
            bond_type = "high_yield"
        elif any(token in text for token in ("long", "15-30", "20+", "long duration")):
            bond_type = "long_duration"
        elif any(token in text for token in ("floating", "float", "floating rate")):
            bond_type = "floating_rate"
        elif any(token in text for token in ("short", "0-1", "1-3", "ultrashort")):
            bond_type = "short_duration"
    return {"assetClass": macro, "region": region, "bondType": bond_type, "weight": float(item.get("weight") or 0) * 100}


def calculate_portfolio_exposures(portfolio: list[dict[str, Any]]) -> dict[str, Any]:
    exposures = empty_portfolio_exposures()
    for item in portfolio:
        classification = classify_benchmark_asset(item)
        weight = float(classification["weight"])
        macro = classification["assetClass"]
        if macro == "equity":
            exposures["equityExposure"] += weight
            exposures["equityRegions"][classification["region"]] += weight
        elif macro == "bond":
            exposures["bondExposure"] += weight
            exposures["bondTypes"][classification["bondType"]] += weight
        elif macro == "cash_like":
            exposures["cashLikeExposure"] += weight
        elif macro == "gold":
            exposures["goldExposure"] += weight
        elif macro == "commodity":
            exposures["commodityExposure"] += weight
        else:
            exposures["unknownExposure"] += weight
    return exposures


calculatePortfolioExposures = calculate_portfolio_exposures


def select_equity_benchmark(exposures: dict[str, Any]) -> dict[str, Any]:
    equity = float(exposures.get("equityExposure") or 0)
    if equity <= 0:
        return benchmark_definition("global_equity")
    regions = exposures.get("equityRegions", {})
    usa_share = float(regions.get("usa") or 0) / equity * 100
    europe_share = float(regions.get("europe") or 0) / equity * 100
    emerging_share = float(regions.get("emerging_markets") or 0) / equity * 100
    global_share = float(regions.get("global") or 0) / equity * 100
    if usa_share >= 60:
        return benchmark_definition("us_equity")
    if europe_share >= 60:
        return benchmark_definition("europe_equity")
    if emerging_share >= 40:
        return benchmark_definition("emerging_equity")
    if global_share >= 50:
        return benchmark_definition("global_equity")
    return benchmark_definition("global_equity")


selectEquityBenchmark = select_equity_benchmark


def select_bond_benchmark(exposures: dict[str, Any]) -> dict[str, Any]:
    bond_total = float(exposures.get("bondExposure") or 0) + float(exposures.get("cashLikeExposure") or 0)
    if bond_total <= 0:
        return benchmark_definition("global_aggregate_bond")
    bond_types = exposures.get("bondTypes", {})
    short_share = (float(bond_types.get("short_duration") or 0) + float(exposures.get("cashLikeExposure") or 0)) / bond_total * 100
    corporate_share = float(bond_types.get("corporate") or 0) / bond_total * 100
    government_share = float(bond_types.get("government") or 0) / bond_total * 100
    aggregate_share = float(bond_types.get("aggregate") or 0) / bond_total * 100
    if short_share >= 50:
        return benchmark_definition("short_duration_bond")
    if corporate_share >= 50:
        return benchmark_definition("eur_corporate_bond")
    if government_share >= 50:
        return benchmark_definition("euro_government_bond")
    if aggregate_share >= 40:
        return benchmark_definition("global_aggregate_bond")
    return benchmark_definition("global_aggregate_bond")


selectBondBenchmark = select_bond_benchmark


def build_synthetic_benchmark_from_exposures(exposures: dict[str, Any]) -> dict[str, Any]:
    known_total = (
        float(exposures.get("equityExposure") or 0)
        + float(exposures.get("bondExposure") or 0)
        + float(exposures.get("cashLikeExposure") or 0)
        + float(exposures.get("goldExposure") or 0)
        + float(exposures.get("commodityExposure") or 0)
    )
    if known_total <= 0:
        return benchmark_definition("global_equity")
    holdings: list[dict[str, Any]] = []
    equity_weight = float(exposures.get("equityExposure") or 0) / known_total * 100
    bond_weight = (float(exposures.get("bondExposure") or 0) + float(exposures.get("cashLikeExposure") or 0)) / known_total * 100
    gold_weight = float(exposures.get("goldExposure") or 0) / known_total * 100
    commodity_weight = float(exposures.get("commodityExposure") or 0) / known_total * 100
    if equity_weight > 0:
        base = select_equity_benchmark(exposures)["holdings"][0]
        holdings.append({**base, "weight": round_weight(equity_weight), "role": "Componente azionaria del benchmark multi-asset"})
    if bond_weight > 0:
        base = select_bond_benchmark(exposures)["holdings"][0]
        holdings.append({**base, "weight": round_weight(bond_weight), "role": "Componente obbligazionaria del benchmark multi-asset"})
    if gold_weight > 0:
        base = benchmark_definition("gold")["holdings"][0]
        holdings.append({**base, "weight": round_weight(gold_weight), "role": "Componente oro del benchmark multi-asset"})
    if commodity_weight > 0:
        base = benchmark_definition("broad_commodities")["holdings"][0]
        holdings.append({**base, "weight": round_weight(commodity_weight), "role": "Componente commodity del benchmark multi-asset"})
    return {
        "id": "synthetic_benchmark",
        "name": "Benchmark Multi-Asset Ponderato",
        "type": "synthetic",
        "assetClass": "multi_asset",
        "description": "Benchmark costruito ponderando azioni, obbligazioni, oro e commodity in base alla composizione del portfolio.",
        "holdings": normalize_benchmark_weights(holdings),
    }


buildSyntheticBenchmarkFromExposures = build_synthetic_benchmark_from_exposures


def get_smart_benchmark_for_portfolio(portfolio: list[dict[str, Any]]) -> dict[str, Any]:
    exposures = calculate_portfolio_exposures(portfolio)
    warnings: list[str] = []
    if exposures["unknownExposure"] > 0:
        warnings.append("Una parte del portfolio non è stata classificata. Il benchmark è stato costruito redistribuendo il peso tra le asset class riconosciute.")
    if exposures["unknownExposure"] > 10:
        warnings.append("Una parte rilevante del portfolio non è stata classificata. Il benchmark potrebbe essere meno preciso.")
    confidence = "medium" if exposures["unknownExposure"] > 10 else "high"
    if exposures["unknownExposure"] > 35:
        confidence = "low"
    if exposures["equityExposure"] >= 60:
        return {
            "benchmark": select_equity_benchmark(exposures),
            "benchmarkType": "single",
            "reason": "Il portfolio è prevalentemente azionario, quindi viene usato un benchmark azionario coerente con l’esposizione geografica prevalente.",
            "exposures": exposures,
            "confidence": confidence,
            "warnings": warnings,
        }
    if exposures["bondExposure"] + exposures["cashLikeExposure"] >= 60:
        return {
            "benchmark": select_bond_benchmark(exposures),
            "benchmarkType": "single",
            "reason": "Il portfolio è prevalentemente obbligazionario, quindi viene usato un benchmark obbligazionario coerente con la tipologia prevalente di bond.",
            "exposures": exposures,
            "confidence": confidence,
            "warnings": warnings,
        }
    if exposures["goldExposure"] >= 60:
        return {
            "benchmark": benchmark_definition("gold"),
            "benchmarkType": "single",
            "reason": "Il portfolio è prevalentemente esposto all’oro, quindi viene usato un benchmark oro.",
            "exposures": exposures,
            "confidence": confidence,
            "warnings": warnings,
        }
    if exposures["commodityExposure"] >= 60:
        return {
            "benchmark": benchmark_definition("broad_commodities"),
            "benchmarkType": "single",
            "reason": "Il portfolio è prevalentemente esposto alle commodity, quindi viene usato un benchmark commodity diversificato.",
            "exposures": exposures,
            "confidence": confidence,
            "warnings": warnings,
        }
    return {
        "benchmark": build_synthetic_benchmark_from_exposures(exposures),
        "benchmarkType": "synthetic",
        "reason": "Il portfolio è multi-asset, quindi viene costruito un benchmark ponderato tra azioni, obbligazioni, oro e commodity.",
        "exposures": exposures,
        "confidence": confidence,
        "warnings": warnings,
    }


getSmartBenchmarkForPortfolio = get_smart_benchmark_for_portfolio


def smart_benchmark_to_backtest_items(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for holding in benchmark.get("holdings", []):
        symbol = str(holding.get("isin") or holding.get("ticker") or "").strip().upper()
        if not symbol:
            continue
        asset_class = {
            "equity": "equity",
            "bond": "bonds",
            "gold": "gold",
            "commodity": "commodities",
            "multi_asset": "equity",
        }.get(str(benchmark.get("assetClass") or "").lower(), infer_standard_asset_class(holding))
        if "bond" in str(holding.get("role", "")).lower() or "obblig" in str(holding.get("role", "")).lower():
            asset_class = "bonds"
        if "oro" in str(holding.get("role", "")).lower() or "gold" in str(holding.get("role", "")).lower():
            asset_class = "gold"
        if "commodity" in str(holding.get("role", "")).lower():
            asset_class = "commodities"
        items.append(
            {
                "symbol": symbol,
                "weight": float(holding.get("weight") or 0) / 100,
                "assetClass": asset_class,
                "displayName": holding.get("name") or symbol,
                "minWeight": 0,
                "maxWeight": 1,
            }
        )
    return items


def build_smart_benchmark_comparison(
    portfolio: list[dict[str, Any]],
    start: date,
    end: date,
    demo: bool,
    data_source: str,
    initial_capital: float,
    rebalance_frequency: str,
    equal_weight: dict[str, Any],
) -> dict[str, Any]:
    selection = get_smart_benchmark_for_portfolio(portfolio)
    benchmark = selection["benchmark"]
    items = smart_benchmark_to_backtest_items(benchmark)
    if not items:
        return {
            "available": False,
            "reason": "Benchmark intelligente non disponibile: nessuno strumento valido nella definizione locale.",
            "smartBenchmark": selection,
            "equalWeightBenchmark": equal_weight,
        }
    benchmark_symbols = [item["symbol"] for item in items]
    benchmark_asset_classes = {item["symbol"]: item["assetClass"] for item in items}
    benchmark_weights = {item["symbol"]: item["weight"] for item in items}
    try:
        benchmark_prices = make_demo_bars(benchmark_symbols, start, end) if demo else marketDataService(benchmark_symbols, start, end)
        benchmark_days, _ = align_returns(benchmark_prices)
        simulation = simulate_portfolio(
            benchmark_days,
            benchmark_prices,
            benchmark_symbols,
            benchmark_weights,
            benchmark_asset_classes,
            initial_capital,
            rebalance_frequency,
        )
        metrics = build_metrics(simulation["equityCurve"], simulation["dailyReturns"], initial_capital)
        contribution = build_asset_contribution(
            benchmark_symbols,
            benchmark_prices,
            benchmark_days,
            benchmark_weights,
            simulation["finalAssetValues"],
            simulation["equity"],
            benchmark_asset_classes,
        )
    except BacktestError as exc:
        return {
            "available": False,
            "reason": f"data_unavailable: benchmark intelligente non calcolabile. {exc}",
            "smartBenchmark": selection,
            "equalWeightBenchmark": equal_weight,
        }
    return {
        "available": True,
        "name": benchmark.get("name", "Benchmark intelligente"),
        "method": "Smart benchmark locale basato su azioni, obbligazioni, oro e commodity.",
        "benchmarkType": selection["benchmarkType"],
        "reason": selection["reason"],
        "confidence": selection["confidence"],
        "warnings": selection["warnings"],
        "exposures": selection["exposures"],
        "benchmark": benchmark,
        "metrics": metrics,
        "equityCurve": simulation["equityCurve"],
        "dailyReturns": simulation["dailyReturns"],
        "contribution": contribution,
        "dataSource": data_source,
        "equalWeightBenchmark": equal_weight,
        "disclaimer": "Il benchmark intelligente QuantInvest è un modello educativo costruito per confrontare il portfolio con un riferimento più coerente rispetto alla sua composizione. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
    }


def build_factor_risk_decomposition(stress_payload: dict[str, Any]) -> dict[str, Any]:
    result = stress_payload.get("result") if isinstance(stress_payload, dict) else None
    if not isinstance(result, dict):
        return {"available": False, "reason": stress_payload.get("error") or "Stress testing non disponibile."}
    contributions = result.get("contribution_by_factor") or []
    total_abs = sum(abs(float(item.get("contribution", 0))) for item in contributions)
    return {
        "available": True,
        "scenario": result.get("scenario"),
        "factors": [
            {
                "factor": item.get("factor"),
                "contribution": float(item.get("contribution", 0)),
                "riskShare": abs(float(item.get("contribution", 0))) / total_abs if total_abs else 0,
            }
            for item in contributions
        ],
    }


def build_scenario_comparison(stress_payload: dict[str, Any]) -> dict[str, Any]:
    result = stress_payload.get("result") if isinstance(stress_payload, dict) else None
    if not isinstance(result, dict):
        return {"available": False, "reason": stress_payload.get("error") or "Esegui prima lo stress testing per confrontare gli scenari."}
    analysis = result.get("factor_analysis") if isinstance(result.get("factor_analysis"), dict) else {}
    beta_matrix = analysis.get("beta_matrix") if isinstance(analysis.get("beta_matrix"), dict) else {}
    base_assets = result.get("contribution_by_asset") if isinstance(result.get("contribution_by_asset"), list) else []
    if not beta_matrix or not base_assets:
        return {"available": False, "reason": "Beta matrix non disponibile per il confronto scenari."}
    rows = []
    for scenario, shocks in FACTOR_STRESS_SCENARIOS.items():
        by_asset = []
        factor_totals = {factor: 0.0 for factor in FACTOR_STRESS_FACTORS}
        for asset in base_assets:
            symbol = asset.get("symbol")
            weight = float(asset.get("weight") or 0)
            betas = beta_matrix.get(symbol, {})
            expected = sum(float(betas.get(factor, 0)) * float(shocks.get(factor, 0)) for factor in FACTOR_STRESS_FACTORS)
            weighted = weight * expected
            by_asset.append(
                {
                    "symbol": symbol,
                "displayName": public_display_name_for_symbol(symbol),
                    "weight": weight,
                    "expectedReturn": expected,
                    "weightedContribution": weighted,
                }
            )
            for factor in FACTOR_STRESS_FACTORS:
                factor_totals[factor] += weight * float(betas.get(factor, 0)) * float(shocks.get(factor, 0))
        expected_portfolio = sum(item["weightedContribution"] for item in by_asset)
        best = max(by_asset, key=lambda item: item["weightedContribution"])["symbol"] if by_asset else None
        worst = min(by_asset, key=lambda item: item["weightedContribution"])["symbol"] if by_asset else None
        rows.append(
            {
                "scenario": scenario,
                "expectedPortfolioReturn": expected_portfolio,
                "bestHedge": best,
                "bestHedgeName": public_display_name_for_symbol(best),
                "worstContributor": worst,
                "worstContributorName": public_display_name_for_symbol(worst),
                "contributionByFactor": [{"factor": factor, "contribution": factor_totals[factor]} for factor in FACTOR_STRESS_FACTORS],
            }
        )
    available_rows = [row for row in rows if row.get("expectedPortfolioReturn") is not None]
    worst = min(available_rows, key=lambda item: item["expectedPortfolioReturn"], default=None)
    best = max(available_rows, key=lambda item: item["expectedPortfolioReturn"], default=None)
    return {"available": bool(available_rows), "rows": rows, "worstScenario": worst, "bestScenario": best}


def enrich_stress_result_display_names(stress_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(stress_result, dict):
        return stress_result
    for key in ("best_hedge", "worst_contributor"):
        if stress_result.get(key):
            stress_result[f"{key}_name"] = public_display_name_for_symbol(str(stress_result[key]))
    assets = stress_result.get("contribution_by_asset")
    if isinstance(assets, list):
        for item in assets:
            if isinstance(item, dict) and item.get("symbol"):
                item["displayName"] = public_display_name_for_symbol(str(item["symbol"]))
    analysis = stress_result.get("factor_analysis")
    exposures = analysis.get("exposures") if isinstance(analysis, dict) else None
    if isinstance(exposures, list):
        for item in exposures:
            if isinstance(item, dict) and item.get("symbol"):
                item["displayName"] = public_display_name_for_symbol(str(item["symbol"]))
    return stress_result


def build_advanced_analytics(
    user_plan: str,
    profile: dict[str, Any],
    series_by_symbol: dict[str, list[float]],
    symbols: list[str],
    current_weights: dict[str, float],
    optimized_weights: dict[str, float] | None,
    asset_classes: dict[str, str],
    current_daily_returns: list[float],
    optimized_daily_returns: list[float] | None,
    current_equity_curve: list[dict[str, Any]],
    optimized_equity_curve: list[dict[str, Any]] | None,
    price_data: dict[str, list[PricePoint]],
    common_days: list[str],
    portfolio_items: list[dict[str, Any]],
    stress_settings: dict[str, Any],
    current_stress: dict[str, Any],
    optimized_stress: dict[str, Any] | None,
    data_source: str,
    feed: str,
    comparison_symbols: list[str] | None = None,
    comparison_weights: dict[str, float] | None = None,
    comparison_asset_classes: dict[str, str] | None = None,
    comparison_series_by_symbol: dict[str, list[float]] | None = None,
    comparison_price_data: dict[str, list[PricePoint]] | None = None,
    comparison_common_days: list[str] | None = None,
    comparison_portfolio_items: list[dict[str, Any]] | None = None,
    comparison_label: str = "portfolio efficiente",
    primary_label: str = "portfolio inserito",
    comparison_unavailable_reason: str = "Portafoglio di confronto non disponibile.",
) -> dict[str, Any]:
    if normalize_plan(user_plan) != "ADVANCED":
        return {
            "locked": True,
            "famaFrench": locked_feature_payload("famaFrench"),
            "geography": locked_feature_payload("correlationMatrix"),
            "rollingSortino": locked_feature_payload("rollingReturns"),
            "correlation": locked_feature_payload("correlationMatrix"),
            "pac": locked_feature_payload("pacAnalysis"),
            "drawdown": locked_feature_payload("peakToTrough"),
            "factorRisk": locked_feature_payload("factorShockAnalysis"),
            "scenarioComparison": locked_feature_payload("factorShockAnalysis"),
        }
    optimized_weights = optimized_weights or {}
    comparison_symbols = comparison_symbols or symbols
    comparison_weights = comparison_weights if comparison_weights is not None else optimized_weights
    comparison_asset_classes = comparison_asset_classes or asset_classes
    comparison_series_by_symbol = comparison_series_by_symbol or series_by_symbol
    comparison_price_data = comparison_price_data or price_data
    comparison_common_days = comparison_common_days or common_days
    optimized_items = (
        comparison_portfolio_items
        if comparison_portfolio_items is not None
        else [{**item, "weight": optimized_weights.get(item["symbol"], item["weight"])} for item in portfolio_items] if optimized_weights else []
    )
    rolling_windows = build_rolling_windows(current_daily_returns, optimized_daily_returns, comparison_unavailable_reason)
    default_rolling = rolling_windows["1y"]
    result = {
        "locked": False,
        "famaFrench": {
            "current": build_fama_french_analysis(series_by_symbol, common_days, symbols, current_weights, asset_classes),
            "optimized": build_fama_french_analysis(comparison_series_by_symbol, comparison_common_days, comparison_symbols, comparison_weights, comparison_asset_classes) if comparison_weights else {"available": False, "reason": comparison_unavailable_reason},
        },
        "geography": {
            "current": build_geographic_exposure(symbols, current_weights),
            "optimized": build_geographic_exposure(comparison_symbols, comparison_weights) if comparison_weights else {"available": False, "reason": comparison_unavailable_reason},
        },
        "rollingSortino": {
            "current": default_rolling["current"],
            "optimized": default_rolling["optimized"],
            "windows": rolling_windows,
            "selectedWindow": "1y",
            "sortino": {
                "current": sortino_ratio(current_daily_returns),
                "optimized": sortino_ratio(optimized_daily_returns or []) if optimized_daily_returns else None,
            },
        },
        "correlation": {
            "current": build_correlation_analysis(series_by_symbol, symbols),
            "optimized": build_correlation_analysis(comparison_series_by_symbol, comparison_symbols) if comparison_weights else {"available": False, "reason": comparison_unavailable_reason},
        },
        "pac": {
            "current": simulate_pac(common_days, price_data, symbols, current_weights, asset_classes, profile.get("capital", 10000)),
            "optimized": simulate_pac(comparison_common_days, comparison_price_data, comparison_symbols, comparison_weights, comparison_asset_classes, profile.get("capital", 10000)) if comparison_weights else {"available": False, "reason": comparison_unavailable_reason},
        },
        "drawdown": {
            "current": build_drawdown_analysis(current_equity_curve),
            "optimized": build_drawdown_analysis(optimized_equity_curve or []) if optimized_equity_curve else {"available": False, "reason": comparison_unavailable_reason},
        },
        "factorRisk": {
            "current": build_factor_risk_decomposition(current_stress),
            "optimized": build_factor_risk_decomposition(optimized_stress or {}) if optimized_stress else {"available": False, "reason": comparison_unavailable_reason},
        },
        "scenarioComparison": {
            "current": build_scenario_comparison(current_stress),
            "optimized": build_scenario_comparison(optimized_stress or {}) if optimized_items else {"available": False, "reason": comparison_unavailable_reason},
        },
    }
    comment_specs = {
        "famaFrench": ("fama_french_factor_analysis", "Fama-French Factor Analysis"),
        "geography": ("geography_exposure", "Mappa geografica per continenti e stati"),
        "rollingSortino": ("rolling_returns", "Rendimenti rolling e rischio negativo"),
        "correlation": ("correlation_matrix", "Matrice di correlazione avanzata"),
        "pac": ("portfolio_formation", "PAC Analysis avanzata"),
        "drawdown": ("peak_to_trough", "Caduta massima, recupero e durata della perdita temporanea"),
        "factorRisk": ("scenario_analysis", "Factor risk decomposition"),
        "scenarioComparison": ("scenario_analysis", "Advanced scenario comparison"),
    }
    for section_key, (analysis_type, section_title) in comment_specs.items():
        section = result.get(section_key, {})
        for variant in ("current", "optimized"):
            data = section.get(variant) if isinstance(section, dict) else None
            if not isinstance(data, dict) or not data.get("available"):
                continue
            metrics = summarize_advanced_metrics(section_key, data, section, variant)
            payload = build_ai_explanation_input(
                analysis_type,
                profile,
                [
                    {"symbol": symbol, "weight": (comparison_weights if variant == "optimized" else current_weights).get(symbol, 0)}
                    for symbol in (comparison_symbols if variant == "optimized" else symbols)
                ],
                metrics,
                user_plan,
                "it",
            )
            payload["portfolioVariant"] = variant
            payload["sectionTitle"] = section_title
            comparison_sections = {"pac", "drawdown", "factorRisk", "scenarioComparison"}
            if section_key in comparison_sections:
                payload["commentInstruction"] = (
                    f"Commenta solo la sezione {section_title}. "
                    f"Spiega il {primary_label} e il {comparison_label} come confronto tra i due, "
                    "indicando cosa migliora, cosa resta da monitorare e quale rischio emerge dai dati della sezione. "
                    "Usa esclusivamente i calcoli e i grafici di questa sezione. "
                    "Non parlare dei vantaggi della piattaforma o del sistema: parla solo dei portafogli. "
                    "Non duplicare frasi già usate in altre card o altri campi JSON."
                )
            else:
                payload["commentInstruction"] = (
                    f"Commenta solo la sezione {section_title}. "
                    f"Il commento deve essere una comparazione sul perché il {comparison_label} può essere diverso "
                    f"dal {primary_label}, usando solo i dati della sezione corrente. "
                    "Non parlare dei vantaggi della piattaforma o del sistema: parla solo dei portafogli. "
                    "Non duplicare frasi già usate in altre card o altri campi JSON."
                )
            data["structuredExplanation"] = build_ai_explanation(payload)
            data["explanationRequest"] = payload
            data["commentSource"] = "openai_on_demand"
        if section_key == "rollingSortino":
            for window_key, window_section in section.get("windows", {}).items():
                window_label = window_section.get("label", window_key)
                for variant in ("current", "optimized"):
                    data = window_section.get(variant) if isinstance(window_section, dict) else None
                    if not isinstance(data, dict) or not data.get("available"):
                        continue
                    metrics = summarize_advanced_metrics(
                        section_key,
                        data,
                        {**section, "current": window_section.get("current"), "optimized": window_section.get("optimized")},
                        variant,
                    )
                    payload = build_ai_explanation_input(
                        analysis_type,
                        profile,
                        [
                            {"symbol": symbol, "weight": (comparison_weights if variant == "optimized" else current_weights).get(symbol, 0)}
                            for symbol in (comparison_symbols if variant == "optimized" else symbols)
                        ],
                        metrics,
                        user_plan,
                        "it",
                    )
                    payload["portfolioVariant"] = variant
                    payload["sectionTitle"] = f"{section_title} - {window_label}"
                    payload["commentInstruction"] = (
                        f"Commenta solo la sezione {section_title} con orizzonte rolling {window_label}. "
                        f"Il commento deve confrontare il {comparison_label} con il {primary_label}, "
                        "spiegando se l'orizzonte selezionato mostra maggiore stabilità, rischio negativo o continuità dei risultati. "
                        "Usa solo i dati rolling e di rischio negativo di questa finestra. "
                        "Non parlare dei vantaggi della piattaforma o del sistema: parla solo dei portafogli. "
                        "Non duplicare frasi già usate in altre card o altri campi JSON."
                    )
                    data["structuredExplanation"] = build_ai_explanation(payload)
                    data["explanationRequest"] = payload
                    data["commentSource"] = "openai_on_demand"
    return result


def summarize_advanced_metrics(section_key: str, data: dict[str, Any], section: dict[str, Any], variant: str = "current") -> dict[str, Any]:
    if section_key == "famaFrench":
        return {
            "rSquared": data.get("rSquared"),
            "alpha": data.get("alpha"),
            "dataset": data.get("datasetFile") or data.get("dataset"),
            "frequency": data.get("frequency"),
            "observations": data.get("observations"),
            "maxDrawdown": 0,
        }
    if section_key == "geography":
        top = data.get("continents", [{}])[0] if data.get("continents") else {}
        return {
            "topGeography": top.get("name"),
            "topGeographyWeight": top.get("weight"),
            "maxDrawdown": 0,
        }
    if section_key == "correlation":
        return {
            "averageAbsoluteCorrelation": data.get("averageAbsoluteCorrelation"),
            "highestPair": data.get("highestPair"),
            "lowestPair": data.get("lowestPair"),
            "maxDrawdown": 0,
        }
    if section_key == "pac":
        return {
            "finalValue": data.get("finalValue"),
            "totalInvested": data.get("totalInvested"),
            "gainPercent": data.get("gainPercent"),
            "maxDrawdown": 0,
        }
    if section_key == "drawdown":
        return {
            "maxDrawdown": data.get("maxDrawdown"),
            "peakToTroughDays": data.get("peakToTroughDays"),
            "maxDrawdownDurationDays": data.get("maxDrawdownDurationDays"),
        }
    if section_key == "factorRisk":
        top = sorted(data.get("factors", []), key=lambda item: item.get("riskShare", 0), reverse=True)[:1]
        return {
            "dominantFactor": top[0].get("factor") if top else None,
            "dominantFactorShare": top[0].get("riskShare") if top else None,
            "maxDrawdown": 0,
        }
    if section_key == "scenarioComparison":
        worst = data.get("worstScenario") or {}
        best = data.get("bestScenario") or {}
        return {
            "worstScenario": worst.get("scenario"),
            "worstScenarioReturn": worst.get("expectedPortfolioReturn"),
            "bestScenario": best.get("scenario"),
            "bestScenarioReturn": best.get("expectedPortfolioReturn"),
            "maxDrawdown": 0,
        }
    latest = data.get("latest", {})
    return {
        "rollingReturn": latest.get("rollingReturn"),
        "rollingSortino": latest.get("rollingSortino"),
        "sortino": section.get("sortino", {}).get(variant),
        "maxDrawdown": 0,
    }


def build_asset_contribution(
    symbols: list[str],
    price_data: dict[str, list[PricePoint]],
    common_days: list[str],
    weights: dict[str, float],
    final_asset_values: dict[str, float],
    equity: float,
    asset_classes: dict[str, str],
) -> list[dict[str, Any]]:
    contribution = []
    for symbol in symbols:
        first = next(point.close for point in price_data[symbol] if point.day == common_days[0])
        last = next(point.close for point in reversed(price_data[symbol]) if point.day == common_days[-1])
        asset_return = last / first - 1
        contribution.append(
            {
                "symbol": symbol,
                "displayName": public_display_name_for_symbol(symbol),
                "weight": weights[symbol],
                "finalWeight": final_asset_values[symbol] / equity if equity else 0,
                "assetClass": asset_classes[symbol],
                "assetReturn": asset_return,
                "weightedReturn": asset_return * weights[symbol],
            }
        )
    return contribution


def infer_standard_asset_class(holding: dict[str, Any]) -> str:
    text = f"{holding.get('name', '')} {holding.get('role', '')}".lower()
    if "oro" in text or "gold" in text:
        return "gold"
    if "commodity" in text:
        return "commodities"
    if any(token in text for token in ("bond", "obblig", "treasury", "government", "governativ")):
        return "bonds"
    return "equity"


def standard_portfolio_to_backtest_items(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": str(holding.get("isin") or "").strip().upper(),
            "weight": float(holding.get("weight") or 0) / 100,
            "assetClass": infer_standard_asset_class(holding),
            "displayName": holding.get("name") or holding.get("isin"),
            "minWeight": 0,
            "maxWeight": 1,
        }
        for holding in portfolio.get("holdings", [])
        if holding.get("isin")
    ]


def find_standard_portfolio(portfolio_id: Any) -> dict[str, Any] | None:
    wanted = str(portfolio_id or "")
    return next((dict(portfolio) for portfolio in STANDARD_PORTFOLIOS if portfolio.get("id") == wanted), None)


def resolve_selected_standard_portfolio(payload_value: Any) -> dict[str, Any] | None:
    if not isinstance(payload_value, dict):
        return None
    matched = find_standard_portfolio(payload_value.get("id"))
    if matched:
        return matched
    holdings = payload_value.get("holdings")
    if isinstance(holdings, list) and holdings:
        return {
            **payload_value,
            "id": payload_value.get("id") or "payload_standard_benchmark",
            "name": payload_value.get("name") or "Portfolio benchmark standard QuantInvest",
            "holdings": holdings,
        }
    return None


def public_standard_portfolio_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        return analysis
    return public_json_payload(analysis)


def public_json_payload(value: Any) -> Any:
    if isinstance(value, PricePoint):
        return {"day": value.day, "close": value.close}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"priceData", "closeBySymbol"}:
                continue
            result[key] = public_json_payload(item)
        return result
    if isinstance(value, list):
        return [public_json_payload(item) for item in value]
    return value


def metric_delta(primary: dict[str, Any], benchmark: dict[str, Any], key: str) -> float | None:
    primary_value = first_number(primary.get(key))
    benchmark_value = first_number(benchmark.get(key))
    if primary_value is None or benchmark_value is None:
        return None
    return primary_value - benchmark_value


def compare_portfolios(primary: dict[str, Any], benchmark: dict[str, Any], comparison_type: str) -> dict[str, Any]:
    primary_metrics = primary.get("metrics") if isinstance(primary.get("metrics"), dict) else {}
    benchmark_metrics = benchmark.get("metrics") if isinstance(benchmark.get("metrics"), dict) else {}
    deltas = {
        "cagr": metric_delta(primary_metrics, benchmark_metrics, "cagr"),
        "volatility": None,
        "maxDrawdown": metric_delta(primary_metrics, benchmark_metrics, "maxDrawdown"),
        "sharpe": metric_delta(primary_metrics, benchmark_metrics, "sharpe"),
        "totalReturn": metric_delta(primary_metrics, benchmark_metrics, "totalReturn"),
    }
    primary_daily = primary.get("dailyReturns") if isinstance(primary.get("dailyReturns"), list) else []
    benchmark_daily = benchmark.get("dailyReturns") if isinstance(benchmark.get("dailyReturns"), list) else []
    if len(primary_daily) > 1 and len(benchmark_daily) > 1:
        deltas["volatility"] = sample_stdev(primary_daily) * math.sqrt(252) - sample_stdev(benchmark_daily) * math.sqrt(252)
    def exposure_map(rows: Any) -> dict[str, float]:
        result: dict[str, float] = {}
        if not isinstance(rows, list):
            return result
        for row in rows:
            if not isinstance(row, dict):
                continue
            asset_class = str(row.get("assetClass") or "altro")
            result[asset_class] = result.get(asset_class, 0.0) + float(row.get("weight") or 0)
        return result
    primary_exposure = exposure_map(primary.get("contribution"))
    benchmark_exposure = exposure_map(benchmark.get("contribution"))
    exposure_deltas = {
        key: primary_exposure.get(key, 0.0) - benchmark_exposure.get(key, 0.0)
        for key in sorted(set(primary_exposure) | set(benchmark_exposure))
    }
    def max_weight(rows: Any) -> float | None:
        if not isinstance(rows, list) or not rows:
            return None
        return max(float(row.get("weight") or 0) for row in rows if isinstance(row, dict))
    return {
        "available": bool(primary_metrics and benchmark_metrics),
        "comparisonType": comparison_type,
        "primaryName": primary.get("name", "Portfolio"),
        "benchmarkName": benchmark.get("name", "Portfolio standard"),
        "deltas": deltas,
        "exposure": {
            "primary": primary_exposure,
            "benchmark": benchmark_exposure,
            "delta": exposure_deltas,
            "primaryMaxWeight": max_weight(primary.get("contribution")),
            "benchmarkMaxWeight": max_weight(benchmark.get("contribution")),
        },
        "summary": (
            "Il portfolio standard non è una raccomandazione personalizzata, ma un benchmark educativo "
            "creato da QuantInvest per confrontare rischio, rendimento e coerenza."
        ),
        "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
    }


def benchmark_relative_label(score: int) -> str:
    if score >= 85:
        return "Forte"
    if score >= 70:
        return "Coerente"
    if score >= 55:
        return "Discreto"
    if score >= 40:
        return "Da migliorare"
    return "Debole"


def calculate_benchmark_relative_checkup(
    *,
    user_metrics: dict[str, Any],
    benchmark_metrics: dict[str, Any],
    user_contribution: list[dict[str, Any]],
    benchmark_contribution: list[dict[str, Any]],
    investor_profile: dict[str, Any],
) -> dict[str, Any]:
    user_drawdown = abs(float(user_metrics.get("maxDrawdown") or 0))
    benchmark_drawdown = abs(float(benchmark_metrics.get("maxDrawdown") or 0))
    user_cagr = float(user_metrics.get("cagr") or 0)
    benchmark_cagr = float(benchmark_metrics.get("cagr") or 0)
    user_sharpe = first_number(user_metrics.get("sharpe")) or 0
    benchmark_sharpe = first_number(benchmark_metrics.get("sharpe")) or 0
    max_loss = float(investor_profile.get("maxTemporaryLoss") or risk_budget_for_profile(investor_profile))
    user_max_weight = max((float(item.get("weight") or 0) for item in user_contribution), default=0)
    benchmark_max_weight = max((float(item.get("weight") or 0) for item in benchmark_contribution), default=0)

    drawdown_score = 100 - max(0, user_drawdown - benchmark_drawdown) * 160 - max(0, user_drawdown - max_loss) * 220
    return_score = 62 + (user_cagr - benchmark_cagr) * 260 - max(0, user_drawdown - benchmark_drawdown) * 90
    efficiency_score = 58 + (user_sharpe - benchmark_sharpe) * 28
    diversification_score = 72 - max(0, user_max_weight - benchmark_max_weight) * 90
    score = score_component((drawdown_score * 0.30) + (return_score * 0.20) + (efficiency_score * 0.25) + (diversification_score * 0.25))

    if user_drawdown > benchmark_drawdown and user_cagr <= benchmark_cagr:
        main_risk = "Rispetto al benchmark standard selezionato, il portafoglio mostra una perdita temporanea più alta senza un miglioramento proporzionato della crescita storica."
    elif user_drawdown > max_loss:
        main_risk = "La perdita temporanea del portafoglio supera la soglia indicata dall'investitore."
    else:
        main_risk = "Il rischio storico resta confrontabile con il benchmark standard selezionato."

    if user_cagr > benchmark_cagr:
        strength = "Il portafoglio inserito ha mostrato una crescita media annua storica superiore al benchmark standard."
    else:
        strength = "Il benchmark standard offre una struttura utile per confrontare rischio e diversificazione in modo educativo."

    if user_max_weight > benchmark_max_weight:
        improvement = "Una possibile area da valutare è la concentrazione: il portafoglio inserito dipende di più da pochi strumenti rispetto al benchmark."
    else:
        improvement = "La principale area da valutare è se il rischio assunto è coerente con il rendimento storico ottenuto."

    benchmark_insight = (
        f"Differenza crescita media annua: {(user_cagr - benchmark_cagr) * 100:.2f} punti. "
        f"Differenza perdita temporanea: {(benchmark_drawdown - user_drawdown) * 100:.2f} punti. "
        f"Differenza rapporto rischio-rendimento: {user_sharpe - benchmark_sharpe:.2f}."
    )
    return {
        "score": score,
        "label": benchmark_relative_label(score),
        "mainRisk": main_risk,
        "strength": strength,
        "improvementArea": improvement,
        "benchmarkInsight": benchmark_insight,
    }


def build_standard_benchmark_comment(
    standard_analysis: dict[str, Any],
    benchmark_checkup: dict[str, Any] | None,
) -> dict[str, Any]:
    portfolio = standard_analysis.get("portfolio") if isinstance(standard_analysis.get("portfolio"), dict) else {}
    metrics = standard_analysis.get("metrics") if isinstance(standard_analysis.get("metrics"), dict) else {}
    name = portfolio.get("name") or "Portfolio benchmark standard QuantInvest"
    cagr = first_number(metrics.get("cagr"))
    drawdown = first_number(metrics.get("maxDrawdown"))
    sharpe = first_number(metrics.get("sharpe"))
    summary_parts = [f"{name} è il portfolio benchmark educativo selezionato per il confronto."]
    if cagr is not None:
        summary_parts.append(f"Crescita media annua storica simulata {cagr * 100:.1f}%.")
    if drawdown is not None:
        summary_parts.append(f"Peggiore perdita storica {drawdown * 100:.1f}%.")
    return {
        "executiveSummary": " ".join(summary_parts),
        "riskInsight": (
            benchmark_checkup.get("mainRisk")
            if benchmark_checkup
            else "Il portfolio benchmark standard serve a confrontare rischio, perdita storica e composizione con il portfolio analizzato."
        ),
        "suggestedAction": (
            benchmark_checkup.get("improvementArea")
            if benchmark_checkup
            else "Osserva se il portfolio analizzato assume più rischio o concentrazione rispetto al portfolio benchmark standard."
        ),
        "technicalExplanation": (
            f"Metriche benchmark: crescita media annua {cagr * 100:.2f}%"
            if cagr is not None
            else "Metriche benchmark calcolate sugli stessi parametri temporali dell'analisi."
        )
        + (f", peggiore perdita {drawdown * 100:.2f}%" if drawdown is not None else "")
        + (f", rapporto rischio-rendimento {sharpe:.2f}." if sharpe is not None else "."),
        "mainRisk": benchmark_checkup.get("mainRisk") if benchmark_checkup else "rischio benchmark da confrontare",
        "mainStrength": benchmark_checkup.get("strength") if benchmark_checkup else "modello educativo con pesi standard definiti",
        "improvementArea": benchmark_checkup.get("improvementArea") if benchmark_checkup else "confrontare differenze di rischio e concentrazione",
        "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
    }


def resolve_analysis_comparison_context(
    *,
    user_portfolio: list[dict[str, Any]],
    optimized_portfolio: dict[str, Any],
    selected_standard_portfolio: dict[str, Any] | None,
    standard_portfolio_mode: str,
    standard_benchmark_analysis: dict[str, Any],
    benchmark_checkup: dict[str, Any] | None = None,
    benchmark_health_score: dict[str, Any] | None = None,
    benchmark_comment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if standard_portfolio_mode == "use_as_benchmark" and selected_standard_portfolio:
        return {
            "comparisonMode": "standard_benchmark",
            "primaryPortfolio": user_portfolio,
            "comparisonPortfolio": selected_standard_portfolio,
            "benchmarkPortfolio": selected_standard_portfolio,
            "comparisonDataKey": "standardBenchmarkAnalysis",
            "labels": {
                "primary": "Portfolio inserito",
                "comparison": "Portfolio benchmark standard QuantInvest",
                "backtestComparison": "Backtest portfolio benchmark",
                "monteCarloComparison": "Monte Carlo benchmark standard",
                "scenarioComparison": "Scenario portfolio benchmark",
                "diagnosisComparison": "Confronto con portfolio benchmark standard",
            },
            "shouldShowOptimizedAsPrimary": False,
            "shouldShowStandardAsPrimaryBenchmark": True,
            "benchmarkAvailable": bool(standard_benchmark_analysis.get("available")),
            "benchmarkReason": standard_benchmark_analysis.get("reason", ""),
            "benchmarkCheckup": benchmark_checkup,
            "benchmarkHealthScore": benchmark_health_score,
            "benchmarkComment": benchmark_comment,
            "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
        }
    if standard_portfolio_mode == "use_as_portfolio" and selected_standard_portfolio:
        return {
            "comparisonMode": "standard_only",
            "primaryPortfolio": selected_standard_portfolio,
            "analyzedPortfolio": user_portfolio,
            "comparisonPortfolio": None,
            "benchmarkPortfolio": None,
            "comparisonDataKey": "",
            "labels": {
                "primary": "Portfolio standard QuantInvest",
                "comparison": "Nessun confronto principale",
                "backtestComparison": "Analisi solo portfolio standard",
                "monteCarloComparison": "Monte Carlo portfolio standard",
                "scenarioComparison": "Scenario portfolio standard",
                "diagnosisComparison": "Analisi portfolio standard",
            },
            "shouldShowOptimizedAsPrimary": False,
            "shouldShowStandardAsPrimaryBenchmark": False,
            "benchmarkAvailable": False,
            "benchmarkReason": "",
            "benchmarkCheckup": None,
            "benchmarkHealthScore": None,
            "benchmarkComment": None,
            "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
        }
    return {
        "comparisonMode": "optimization",
        "primaryPortfolio": user_portfolio,
        "comparisonPortfolio": optimized_portfolio,
        "benchmarkPortfolio": optimized_portfolio,
        "comparisonDataKey": "optimizedPortfolio",
        "labels": {
            "primary": "Portfolio inserito",
            "comparison": "Portfolio efficiente",
            "backtestComparison": "Backtest ottimizzato",
            "monteCarloComparison": "Monte Carlo ottimizzato",
            "scenarioComparison": "Scenario ottimizzato",
            "diagnosisComparison": "Confronto con portfolio efficiente",
        },
        "shouldShowOptimizedAsPrimary": True,
        "shouldShowStandardAsPrimaryBenchmark": False,
        "benchmarkAvailable": bool(optimized_portfolio.get("available")),
        "benchmarkReason": optimized_portfolio.get("reason", ""),
        "benchmarkCheckup": None,
        "benchmarkHealthScore": None,
        "benchmarkComment": None,
        "disclaimer": "",
    }


def health_score_pillars_by_key(health: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    health = health or {}
    pillars = health.get("pillars") if isinstance(health.get("pillars"), list) else []
    return {str(pillar.get("key") or pillar.get("name") or ""): pillar for pillar in pillars if isinstance(pillar, dict)}


def first_available_pillar(pillars: dict[str, dict[str, Any]], keys: list[str]) -> dict[str, Any] | None:
    for key in keys:
        if key in pillars:
            return pillars[key]
    return None


def score_comparison_pillar_row(name: str, primary: dict[str, Any] | None, comparison: dict[str, Any] | None) -> dict[str, Any]:
    primary_score = first_number((primary or {}).get("score"))
    comparison_score = first_number((comparison or {}).get("score"))
    if primary_score is None or comparison_score is None:
        direction = "not_available"
        explanation = "Dato non disponibile per entrambi i portafogli."
    else:
        diff = comparison_score - primary_score
        if diff > 3:
            direction = "better"
            explanation = "Il portafoglio di confronto migliora questa area."
        elif diff < -3:
            direction = "worse"
            explanation = "Il portafoglio di confronto peggiora questa area."
        else:
            direction = "same"
            explanation = "Le due letture sono simili."
    return {
        "name": name,
        "primary": round(primary_score, 1) if primary_score is not None else None,
        "comparison": round(comparison_score, 1) if comparison_score is not None else None,
        "direction": direction,
        "explanation": explanation,
    }


def build_score_comparison(primary_health: dict[str, Any] | None, comparison_health: dict[str, Any] | None, comparison_label: str) -> dict[str, Any]:
    primary_score = first_number((primary_health or {}).get("overall"))
    comparison_score = first_number((comparison_health or {}).get("overall"))
    if primary_score is None or comparison_score is None:
        return {
            "available": False,
            "primaryScore": primary_score,
            "comparisonScore": comparison_score,
            "difference": None,
            "summary": "Confronto score non disponibile: manca uno dei due punteggi.",
            "tradeOffs": [],
            "pillars": [],
        }
    primary_pillars = health_score_pillars_by_key(primary_health)
    comparison_pillars = health_score_pillars_by_key(comparison_health)
    pillar_specs = [
        ("Rischio reale", ["realRisk", "baseRisk", "advancedRisk"]),
        ("Efficienza", ["riskReturnEfficiency", "baseEfficiency", "advancedEfficiency"]),
        ("Diversificazione", ["diversification", "baseDiversification", "realDiversification"]),
        ("Coerenza obiettivi", ["goalCoherence", "baseCoherence", "advancedGoalCoherence"]),
        ("Robustezza", ["scenarioRobustness", "factorScenarioRobustness"]),
    ]
    pillars = [
        score_comparison_pillar_row(
            name,
            first_available_pillar(primary_pillars, keys),
            first_available_pillar(comparison_pillars, keys),
        )
        for name, keys in pillar_specs
    ]
    difference = comparison_score - primary_score
    improved = [row["name"] for row in pillars if row["direction"] == "better"]
    worsened = [row["name"] for row in pillars if row["direction"] == "worse"]
    if difference > 0:
        summary = f"{comparison_label} migliora lo score da {round(primary_score)} a {round(comparison_score)}."
    elif difference < 0:
        summary = f"{comparison_label} ha uno score inferiore di {abs(round(difference))} punti: il confronto va letto area per area."
    else:
        summary = f"{comparison_label} ha uno score simile al portafoglio principale."
    tradeoffs: list[str] = []
    if improved:
        tradeoffs.append("Migliora: " + ", ".join(improved[:3]) + ".")
    if worsened:
        tradeoffs.append("Peggiora o richiede attenzione: " + ", ".join(worsened[:3]) + ".")
    if not tradeoffs:
        tradeoffs.append("Non emergono differenze rilevanti tra i pillar disponibili.")
    return {
        "available": True,
        "primaryScore": round(primary_score, 1),
        "comparisonScore": round(comparison_score, 1),
        "difference": round(difference, 1),
        "summary": summary,
        "tradeOffs": tradeoffs,
        "pillars": pillars,
    }


def monitoring_dashboard_from_result(metrics: dict[str, Any], contribution: list[dict[str, Any]], stress: dict[str, Any]) -> dict[str, str]:
    drawdown = first_number(metrics.get("maxDrawdown"))
    risk_to_monitor = f"Perdita temporanea {drawdown * 100:.2f}%" if drawdown is not None and drawdown < 0 else "Rischio non ancora calcolato"
    exposure: dict[str, float] = {}
    for item in contribution or []:
        asset_class = str(item.get("assetClass") or "unknown").lower()
        exposure[asset_class] = exposure.get(asset_class, 0.0) + float(item.get("weight") or 0)
    class_labels = {
        "equity": "Azioni",
        "bonds": "Obbligazioni",
        "bond": "Obbligazioni",
        "gold": "Oro",
        "commodities": "Commodity",
        "commodity": "Commodity",
        "cash_like": "Liquidità",
        "unknown": "Non classificato",
    }
    top_class, top_weight = max(exposure.items(), key=lambda item: item[1], default=("", 0.0))
    dominant_component = f"{class_labels.get(top_class, top_class or '--')} {top_weight * 100:.1f}%" if top_class else "Composizione non disponibile"
    stress_result = stress.get("result") if isinstance(stress, dict) else {}
    critical_scenario = (
        str(stress_result.get("scenario") or stress_result.get("scenario_name") or "")
        if isinstance(stress_result, dict)
        else ""
    ) or "Scenario non attivo"
    biggest = max(contribution or [], key=lambda item: abs(float(item.get("weightedReturn") or 0)), default=None)
    next_check = f"Controlla {public_display_name_for_symbol(str(biggest.get('symbol') or ''), str(biggest.get('displayName') or ''))}" if biggest else "Controllo mensile"
    return {
        "riskToMonitor": risk_to_monitor,
        "dominantComponent": dominant_component,
        "criticalScenario": critical_scenario,
        "nextCheck": next_check,
    }


def build_data_reliability(
    *,
    demo: bool,
    coverage_warning: str,
    primary_health: dict[str, Any] | None,
    comparison_health: dict[str, Any] | None,
    comparison_available: bool,
    user_plan: str,
) -> dict[str, Any]:
    warnings: list[str] = []
    if demo:
        warnings.append("Dati demo: rischio, oscillazione e perdita temporanea possono essere sottostimati.")
    if coverage_warning:
        warnings.append(coverage_warning)
    for health in (primary_health, comparison_health):
        missing = health.get("metricsMissing") if isinstance(health, dict) else None
        if isinstance(missing, list) and missing:
            warnings.append("Alcune metriche incluse nel piano non sono disponibili e sono state escluse dallo score.")
    if comparison_health is not None and not comparison_available:
        warnings.append("Il portafoglio di confronto non ha dati sufficienti per tutte le analisi.")
    if demo or coverage_warning:
        level = "low"
    elif warnings or normalize_plan(user_plan) == "FREE":
        level = "medium"
    else:
        level = "high"
    reason = {
        "high": "Dati completi, storico sufficiente e score costruito con le metriche disponibili.",
        "medium": "Diagnosi utile ma indicativa: alcune metriche possono essere escluse, proxy o limitate dal piano.",
        "low": "Affidabilità ridotta: dati demo, storico breve o copertura incompleta possono rendere il rischio meno rappresentativo.",
    }[level]
    return {"level": level, "reason": reason, "warnings": list(dict.fromkeys(warnings))}


def final_context_roles(mode: str) -> tuple[str, str | None]:
    if mode == "standard_only":
        return "selected_standard", None
    if mode == "standard_benchmark":
        return "user", "selected_standard"
    if mode == "final_user_vs_recommended_standard":
        return "user", "recommended_standard"
    if mode == "final_optimized_vs_recommended_standard":
        return "optimized", "recommended_standard"
    return "user", "optimized"


def role_display_name(role: str | None, *, selected_standard: dict[str, Any] | None, recommended_standard: dict[str, Any] | None) -> str | None:
    if role == "user":
        return "Portfolio inserito"
    if role == "optimized":
        return "Portfolio ottimizzato"
    if role == "selected_standard":
        return "Portfolio benchmark standard QuantInvest" if selected_standard else "Portfolio standard QuantInvest"
    if role == "recommended_standard":
        return "Portfolio standard suggerito"
    return None


def build_final_analysis_context(
    *,
    mode: str,
    user_portfolio: list[dict[str, Any]],
    optimized_portfolio: dict[str, Any],
    selected_standard: dict[str, Any] | None,
    recommended_standard: dict[str, Any] | None,
    metrics: dict[str, Any],
    optimized_metrics: dict[str, Any] | None,
    selected_standard_analysis: dict[str, Any],
    recommended_standard_analysis: dict[str, Any],
    health_score: dict[str, Any],
    optimized_health_score: dict[str, Any] | None,
    benchmark_health_score: dict[str, Any] | None,
    recommended_standard_health_score: dict[str, Any] | None,
    investor_profile: dict[str, Any],
    user_plan: str,
    contribution: list[dict[str, Any]],
    stress: dict[str, Any],
    standard_recommendation: dict[str, Any],
    demo: bool,
    coverage_warning: str,
    ai_status: str,
    improvement_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_mode = mode if mode in {
        "optimization",
        "standard_benchmark",
        "standard_only",
        "final_user_vs_recommended_standard",
        "final_optimized_vs_recommended_standard",
    } else "optimization"
    primary_role, comparison_role = final_context_roles(normalized_mode)
    selected_available = bool(selected_standard_analysis.get("available"))
    recommended_available = bool(recommended_standard_analysis.get("available"))
    optimized_available = bool(optimized_portfolio.get("available"))

    score_by_role = {
        "user": health_score,
        "optimized": optimized_health_score,
        "selected_standard": benchmark_health_score,
        "recommended_standard": recommended_standard_health_score,
    }
    metrics_by_role = {
        "user": metrics,
        "optimized": optimized_metrics if optimized_available else None,
        "selected_standard": selected_standard_analysis.get("metrics") if selected_available else None,
        "recommended_standard": recommended_standard_analysis.get("metrics") if recommended_available else None,
    }
    if normalized_mode == "final_optimized_vs_recommended_standard":
        score_by_role["optimized"] = health_score
        metrics_by_role["optimized"] = metrics
    if normalized_mode == "standard_only":
        score_by_role["selected_standard"] = health_score
        metrics_by_role["selected_standard"] = metrics
    primary_health = score_by_role.get(primary_role)
    comparison_health = score_by_role.get(comparison_role or "")
    primary_metrics = metrics_by_role.get(primary_role)
    comparison_metrics = metrics_by_role.get(comparison_role or "") if comparison_role else None
    primary_name = role_display_name(primary_role, selected_standard=selected_standard, recommended_standard=recommended_standard) or "Portfolio principale"
    comparison_name = role_display_name(comparison_role, selected_standard=selected_standard, recommended_standard=recommended_standard) if comparison_role else None
    score_comparison = build_score_comparison(primary_health, comparison_health, comparison_name or "Confronto")
    monitoring = monitoring_dashboard_from_result(metrics, contribution, stress)
    data_reliability = build_data_reliability(
        demo=demo,
        coverage_warning=coverage_warning,
        primary_health=primary_health,
        comparison_health=comparison_health,
        comparison_available=comparison_health is not None,
        user_plan=user_plan,
    )
    mode_titles = {
        "optimization": "Confronto tra portafoglio inserito e portafoglio efficiente",
        "standard_benchmark": "Confronto con portfolio benchmark standard QuantInvest",
        "standard_only": "Analisi del portfolio standard QuantInvest",
        "final_user_vs_recommended_standard": "Confronto con portfolio standard suggerito",
        "final_optimized_vs_recommended_standard": "Portafoglio ottimizzato vs standard suggerito",
    }
    if normalized_mode == "standard_only":
        summary = "Stai analizzando un portfolio standard QuantInvest con i pesi indicati. Non viene generato un confronto con se stesso."
    elif normalized_mode == "standard_benchmark":
        summary = "Il confronto principale è tra il portafoglio inserito e il portfolio benchmark standard QuantInvest selezionato. Il portfolio benchmark è un modello educativo, separato dal portafoglio ottimizzato."
    elif normalized_mode == "final_user_vs_recommended_standard":
        summary = "Il confronto principale è tra il portafoglio inserito e il portfolio standard suggerito in Analisi Finale."
    elif normalized_mode == "final_optimized_vs_recommended_standard":
        summary = "Il portafoglio ottimizzato viene confrontato con lo standard suggerito per distinguere efficienza quantitativa e coerenza con profilo, obiettivo e perdita sopportabile."
    else:
        summary = "Il confronto principale è tra il portafoglio inserito e il portafoglio efficiente calcolato con la logica quantitativa disponibile."
    strongest = None
    weakest = None
    if isinstance(primary_health, dict):
        pillars = primary_health.get("pillars") if isinstance(primary_health.get("pillars"), list) else []
        numeric = [pillar for pillar in pillars if first_number(pillar.get("score")) is not None]
        if numeric:
            strongest = max(numeric, key=lambda item: first_number(item.get("score")) or 0)
            weakest = min(numeric, key=lambda item: first_number(item.get("score")) or 0)
    improvement = improvement_plan or {}
    return {
        "mode": normalized_mode,
        "primaryPortfolioRole": primary_role,
        "comparisonPortfolioRole": comparison_role,
        "primaryPortfolioName": primary_name,
        "comparisonPortfolioName": comparison_name,
        "primaryHealthScore": primary_health,
        "comparisonHealthScore": comparison_health,
        "primaryMetrics": primary_metrics,
        "comparisonMetrics": comparison_metrics,
        "scoreComparison": score_comparison,
        "monitoringDashboard": monitoring,
        "mainConclusion": {
            "title": mode_titles[normalized_mode],
            "summary": summary,
            "strength": str((strongest or {}).get("name") or "Punto forte da verificare con i dati disponibili."),
            "weakness": str((weakest or {}).get("name") or "Area debole da verificare con i dati disponibili."),
            "watchOut": monitoring["riskToMonitor"],
        },
        "recommendedStandardPortfolio": recommended_standard,
        "selectedStandardBenchmark": selected_standard,
        "standardRecommendation": {
            "shouldShow": bool(standard_recommendation.get("shouldSuggest")),
            "reason": str(standard_recommendation.get("message") or standard_recommendation.get("explanation", {}).get("reason") or ""),
            "portfolio": recommended_standard,
            "alternatives": standard_recommendation.get("alternatives") if isinstance(standard_recommendation.get("alternatives"), list) else [],
        },
        "improvementContext": {
            "sourcePortfolioRole": primary_role if normalized_mode != "optimization" else "user",
            "targetPortfolioRole": (improvement.get("target", {}) or {}).get("type") or comparison_role,
            "sourcePortfolioName": improvement.get("sourceName") or primary_name,
            "targetPortfolioName": (improvement.get("target", {}) or {}).get("name") or comparison_name,
            "reason": (improvement.get("target", {}) or {}).get("reason") or summary,
            "correctionIntensity": (improvement.get("target", {}) or {}).get("correctionIntensity") or "none",
        },
        "dataReliability": data_reliability,
        "aiStatus": ai_status,
    }


IMPROVEMENT_DISCLAIMER = (
    "Le azioni mostrate sono simulazioni educative basate sui dati inseriti e sui modelli QuantInvest. "
    "Non costituiscono consulenza finanziaria personalizzata ne raccomandazione di acquisto o vendita."
)


def portfolio_items_for_improvement(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items or []:
        symbol = str(item.get("symbol") or item.get("isin") or item.get("instrumentId") or "").strip().upper()
        if not symbol:
            continue
        weight = first_number(item.get("weight")) or 0
        if weight > 1.5:
            weight = weight / 100
        display_name = public_display_name_for_symbol(
            symbol,
            str(item.get("displayName") or item.get("name") or "").strip(),
        )
        result.append(
            {
                "symbol": symbol,
                "displayName": display_name,
                "weight": max(0.0, float(weight)),
                "assetClass": str(item.get("assetClass") or infer_asset_class(symbol)).lower(),
                "role": str(item.get("role") or "").strip(),
            }
        )
    return result


def standard_portfolio_items_for_improvement(portfolio: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not portfolio:
        return []
    return portfolio_items_for_improvement(standard_portfolio_to_backtest_items(portfolio))


def optimized_portfolio_items_for_improvement(
    optimized_portfolio: dict[str, Any] | None,
    user_portfolio: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not optimized_portfolio or not optimized_portfolio.get("available"):
        return []
    user_by_symbol = {item["symbol"]: item for item in portfolio_items_for_improvement(user_portfolio)}
    result: list[dict[str, Any]] = []
    for row in optimized_portfolio.get("weights", []):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        original = user_by_symbol.get(symbol, {})
        weight = first_number(row.get("weight")) or 0
        display_name = public_display_name_for_symbol(
            symbol,
            str(row.get("displayName") or original.get("displayName") or "").strip(),
        )
        result.append(
            {
                "symbol": symbol,
                "displayName": display_name,
                "weight": max(0.0, float(weight)),
                "assetClass": str(row.get("assetClass") or original.get("assetClass") or infer_asset_class(symbol)).lower(),
                "role": "Peso calcolato dal motore di ottimizzazione quantitativa",
            }
        )
    return result


def improvement_asset_exposure(items: list[dict[str, Any]]) -> dict[str, float]:
    exposures = {"equity": 0.0, "bonds": 0.0, "gold": 0.0, "commodities": 0.0, "cash_like": 0.0, "unknown": 0.0}
    for item in items:
        asset_class = str(item.get("assetClass") or "unknown").lower()
        weight = float(item.get("weight") or 0)
        if asset_class in {"bond", "bonds"}:
            exposures["bonds"] += weight
        elif asset_class in {"commodity", "commodities"}:
            exposures["commodities"] += weight
        elif asset_class in exposures:
            exposures[asset_class] += weight
        else:
            exposures["unknown"] += weight
    return exposures


def goal_compatibility_from_health(health_score: dict[str, Any] | None) -> float | None:
    health_score = health_score or {}
    for pillar in health_score.get("pillars", []) if isinstance(health_score.get("pillars"), list) else []:
        key = str(pillar.get("key") or "").lower()
        name = str(pillar.get("name") or "").lower()
        if "goal" in key or "coerenza" in name or "obiettivo" in name:
            score = first_number(pillar.get("score"))
            if score is not None:
                return score
    components = health_score.get("components") if isinstance(health_score.get("components"), dict) else {}
    for key in ("goalCoherence", "coherence", "advancedGoalCoherence", "objectiveFit"):
        score = first_number(components.get(key))
        if score is not None:
            return score
    return first_number(health_score.get("goalCompatibilityScore"))


def portfolio_name_for_improvement(portfolio: Any, fallback: str) -> str:
    if isinstance(portfolio, dict):
        return str(portfolio.get("name") or portfolio.get("title") or fallback)
    return fallback


def resolveImprovementContext(input_data: dict[str, Any]) -> dict[str, Any]:
    comparison_mode = str(input_data.get("comparisonMode") or "optimization")
    user_portfolio = input_data.get("userPortfolio") or []
    optimized_portfolio = input_data.get("optimizedPortfolio") or {}
    selected_standard = input_data.get("selectedStandardPortfolio")
    recommended_standard = input_data.get("recommendedStandardPortfolio") or input_data.get("profileAlignedPortfolio")
    health_value = first_number(input_data.get("portfolioHealthScore"))
    goal_value = first_number(input_data.get("goalCompatibilityScore"))
    is_strongly_misaligned = (
        (health_value is not None and health_value < 60)
        or (goal_value is not None and goal_value < 60)
    )

    def context(
        *,
        section_mode: str,
        source_portfolio: Any,
        target_portfolio: Any,
        source_metrics: dict[str, Any] | None,
        target_metrics: dict[str, Any] | None,
        target_type: str,
        correction_intensity: str,
        title: str,
        subtitle: str,
        target_reason: str,
    ) -> dict[str, Any]:
        return {
            "sectionMode": section_mode,
            "sourcePortfolio": source_portfolio,
            "targetPortfolio": target_portfolio,
            "sourceMetrics": source_metrics or {},
            "targetMetrics": target_metrics or {},
            "targetType": target_type,
            "correctionIntensity": correction_intensity,
            "title": title,
            "subtitle": subtitle,
            "targetReason": target_reason,
            "sourceName": portfolio_name_for_improvement(source_portfolio, "Portafoglio di partenza"),
            "targetName": portfolio_name_for_improvement(target_portfolio, "Target educativo"),
        }

    if comparison_mode == "standard_only" and selected_standard:
        return context(
            section_mode="standard_explanation",
            source_portfolio=selected_standard,
            target_portfolio=selected_standard,
            source_metrics=input_data.get("selectedStandardMetrics"),
            target_metrics=input_data.get("selectedStandardMetrics"),
            target_type="standard_self_analysis",
            correction_intensity="none",
            title="Come e costruito questo portafoglio standard",
            subtitle="Capisci la logica del modello QuantInvest, il ruolo degli strumenti e i rischi principali.",
            target_reason="Hai scelto di analizzare un portafoglio standard QuantInvest. La sezione spiega la struttura del modello invece di generare azioni per raggiungerlo.",
        )

    if comparison_mode == "standard_benchmark" and selected_standard:
        return context(
            section_mode="improvement_plan",
            source_portfolio=user_portfolio,
            target_portfolio=selected_standard,
            source_metrics=input_data.get("portfolioMetrics"),
            target_metrics=input_data.get("selectedStandardMetrics"),
            target_type="selected_standard",
            correction_intensity="drastic" if is_strongly_misaligned else "balanced",
            title="Piano di miglioramento verso il benchmark standard",
            subtitle="Azioni simulate per avvicinare il portafoglio inserito al portfolio benchmark standard QuantInvest selezionato.",
            target_reason="Hai scelto un portafoglio standard come benchmark. Le azioni vengono generate confrontando il portafoglio inserito con il benchmark selezionato.",
        )

    if comparison_mode == "final_user_vs_recommended_standard" and recommended_standard:
        return context(
            section_mode="improvement_plan",
            source_portfolio=user_portfolio,
            target_portfolio=recommended_standard,
            source_metrics=input_data.get("portfolioMetrics"),
            target_metrics=input_data.get("recommendedStandardMetrics"),
            target_type="recommended_standard",
            correction_intensity="drastic",
            title="Piano di riallineamento al profilo",
            subtitle="Azioni simulate per avvicinare il tuo portafoglio al portafoglio standard suggerito da QuantInvest.",
            target_reason="Hai scelto di confrontare il tuo portafoglio con lo standard consigliato in Analisi Finale. Le azioni danno priorita alla coerenza con profilo, obiettivi e perdita massima sopportabile.",
        )

    if comparison_mode == "final_optimized_vs_recommended_standard" and recommended_standard:
        source_portfolio = optimized_portfolio if optimized_portfolio_items_for_improvement(optimized_portfolio, user_portfolio) else user_portfolio
        return context(
            section_mode="improvement_plan",
            source_portfolio=source_portfolio,
            target_portfolio=recommended_standard,
            source_metrics=input_data.get("optimizedMetrics") or input_data.get("portfolioMetrics"),
            target_metrics=input_data.get("recommendedStandardMetrics"),
            target_type="recommended_standard",
            correction_intensity="balanced",
            title="Confronto tra portafoglio ottimizzato e standard consigliato",
            subtitle="Azioni simulate per capire come il portafoglio ottimizzato cambierebbe per avvicinarsi allo standard piu coerente con il profilo.",
            target_reason="Il portafoglio ottimizzato puo migliorare l'efficienza quantitativa, ma lo standard consigliato puo essere piu coerente con profilo, obiettivi e tolleranza alla perdita.",
        )

    if comparison_mode == "optimization" and is_strongly_misaligned and recommended_standard:
        return context(
            section_mode="improvement_plan",
            source_portfolio=user_portfolio,
            target_portfolio=recommended_standard,
            source_metrics=input_data.get("portfolioMetrics"),
            target_metrics=input_data.get("recommendedStandardMetrics"),
            target_type="recommended_standard",
            correction_intensity="drastic",
            title="Piano di riallineamento al profilo",
            subtitle="Azioni simulate per rendere il portafoglio piu coerente con obiettivi, orizzonte e rischio indicato.",
            target_reason="Il portafoglio inserito presenta Health Score o Goal Compatibility inferiori a 60/100. Per questo il target prioritario non e il portafoglio ottimizzato, ma un modello piu coerente con il profilo indicato.",
        )

    if comparison_mode == "optimization" and optimized_portfolio_items_for_improvement(optimized_portfolio, user_portfolio):
        intensity = "light" if (health_value or 0) >= 75 and (goal_value or 0) >= 75 else "balanced"
        return context(
            section_mode="improvement_plan",
            source_portfolio=user_portfolio,
            target_portfolio=optimized_portfolio,
            source_metrics=input_data.get("portfolioMetrics"),
            target_metrics=input_data.get("optimizedMetrics"),
            target_type="optimized",
            correction_intensity=intensity,
            title="Piano di miglioramento verso il portafoglio ottimizzato",
            subtitle="Azioni simulate per migliorare efficienza, rischio-rendimento e diversificazione.",
            target_reason="Il portafoglio appare abbastanza coerente con il profilo indicato. Le azioni puntano quindi ad avvicinarlo al portafoglio ottimizzato.",
        )

    return context(
        section_mode="light_review",
        source_portfolio=user_portfolio,
        target_portfolio=user_portfolio,
        source_metrics=input_data.get("portfolioMetrics"),
        target_metrics=input_data.get("portfolioMetrics"),
        target_type="light_adjustment",
        correction_intensity="light",
        title="Revisione leggera del portafoglio",
        subtitle="Il portafoglio appare gia abbastanza coerente. Eventuali interventi sono rifiniture educative.",
        target_reason="Non e disponibile un target alternativo completo. La sezione mostra solo aree di possibile miglioramento.",
    )


def resolve_improvement_target(
    *,
    comparisonMode: str,
    userPortfolio: list[dict[str, Any]],
    optimizedPortfolio: dict[str, Any],
    selectedStandardPortfolio: dict[str, Any] | None,
    recommendedStandardPortfolio: dict[str, Any] | None,
    portfolioHealthScore: dict[str, Any] | None,
    goalCompatibilityScore: float | None,
    investorProfile: dict[str, Any],
    maxTemporaryLoss: float | None,
    portfolioMetrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = resolveImprovementContext(
        {
            "comparisonMode": comparisonMode,
            "userPortfolio": userPortfolio,
            "optimizedPortfolio": optimizedPortfolio,
            "selectedStandardPortfolio": selectedStandardPortfolio,
            "recommendedStandardPortfolio": recommendedStandardPortfolio,
            "portfolioHealthScore": (portfolioHealthScore or {}).get("overall"),
            "goalCompatibilityScore": goalCompatibilityScore,
            "investorProfile": investorProfile,
            "maxTemporaryLoss": maxTemporaryLoss,
            "portfolioMetrics": portfolioMetrics or {},
            "optimizedMetrics": optimizedPortfolio.get("metrics", {}) if isinstance(optimizedPortfolio, dict) else {},
        }
    )
    target_portfolio = context.get("targetPortfolio")
    if context.get("targetType") == "optimized":
        target_items = optimized_portfolio_items_for_improvement(optimizedPortfolio, userPortfolio)
    elif isinstance(target_portfolio, dict):
        target_items = standard_portfolio_items_for_improvement(target_portfolio)
    else:
        target_items = portfolio_items_for_improvement(target_portfolio or [])
    return {
        "targetPortfolio": target_portfolio,
        "targetItems": target_items,
        "targetType": context.get("targetType"),
        "correctionIntensity": context.get("correctionIntensity"),
        "reason": context.get("targetReason"),
        "title": context.get("title"),
        "description": context.get("subtitle"),
    }


def generate_portfolio_actions(
    *,
    currentPortfolio: list[dict[str, Any]],
    targetPortfolio: list[dict[str, Any]],
    portfolioMetrics: dict[str, Any] | None,
    targetMetrics: dict[str, Any] | None,
    investorProfile: dict[str, Any],
    goalPriority: str,
    maxTemporaryLoss: float | None,
    correctionIntensity: str,
) -> list[dict[str, Any]]:
    del portfolioMetrics, targetMetrics, investorProfile, goalPriority, maxTemporaryLoss
    current = {item["symbol"]: item for item in portfolio_items_for_improvement(currentPortfolio)}
    target = {item["symbol"]: item for item in portfolio_items_for_improvement(targetPortfolio)}
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    actions: list[dict[str, Any]] = []
    for symbol in sorted(set(current) | set(target)):
        current_item = current.get(symbol, {"symbol": symbol, "weight": 0.0, "displayName": public_display_name_for_symbol(symbol), "assetClass": "unknown"})
        target_item = target.get(symbol, {"symbol": symbol, "weight": 0.0, "displayName": current_item.get("displayName"), "assetClass": current_item.get("assetClass", "unknown")})
        current_weight = float(current_item.get("weight") or 0)
        target_weight = float(target_item.get("weight") or 0)
        difference = target_weight - current_weight
        if abs(difference) < 0.02:
            if current_weight <= 0 and target_weight <= 0:
                continue
            action_type = "keep"
        elif current_weight <= 0 and target_weight > 0:
            action_type = "add"
        elif target_weight <= 0 and current_weight > 0:
            action_type = "remove"
        elif difference > 0:
            action_type = "increase"
        else:
            action_type = "reduce"
        abs_diff = abs(difference)
        priority = "high" if correctionIntensity == "drastic" or abs_diff >= 0.15 else "medium" if abs_diff >= 0.07 else "low"
        if action_type == "keep":
            reason = "Lo strumento risulta gia allineato al peso target simulato."
            impact_areas = ["goal_alignment"]
            priority = "low"
        elif action_type in {"reduce", "remove"}:
            reason = "Possibile intervento per ridurre concentrazione o peso in eccesso rispetto al target usato."
            impact_areas = ["risk_reduction", "diversification", "goal_alignment"]
        else:
            reason = "Azione simulata per aumentare una componente presente nel target e migliorare la coerenza della struttura."
            impact_areas = ["diversification", "goal_alignment", "return_efficiency"]
        actions.append(
            {
                "id": f"{action_type}-{symbol}",
                "symbol": symbol,
                "actionType": action_type,
                "instrumentName": public_display_name_for_symbol(symbol, str(target_item.get("displayName") or current_item.get("displayName") or "")),
                "isin": symbol if looks_like_isin(symbol) else None,
                "currentWeight": round(current_weight * 100, 2),
                "targetWeight": round(target_weight * 100, 2),
                "difference": round(difference * 100, 2),
                "priority": priority,
                "reason": reason,
                "impactAreas": impact_areas,
            }
        )
    actions.sort(key=lambda item: (priority_rank.get(item["priority"], 9), -abs(float(item.get("difference") or 0))))
    target_building_actions = [
        item
        for item in actions
        if item.get("actionType") in {"add", "increase"} and float(item.get("targetWeight") or 0) > 0
    ]
    remaining_actions = [item for item in actions if item not in target_building_actions]
    selected_actions: list[dict[str, Any]] = []
    for item in target_building_actions + remaining_actions:
        selected_actions.append(item)
    return selected_actions


def build_improvement_action_completeness(
    *,
    source_items: list[dict[str, Any]],
    target_items: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    source = {str(item.get("symbol") or "").strip().upper(): item for item in portfolio_items_for_improvement(source_items)}
    target = {str(item.get("symbol") or "").strip().upper(): item for item in portfolio_items_for_improvement(target_items)}
    action_by_symbol = {
        str(item.get("symbol") or "").strip().upper(): item
        for item in actions
        if str(item.get("symbol") or "").strip()
    }
    missing_source = [
        {
            "symbol": symbol,
            "name": public_display_name_for_symbol(symbol, str(item.get("displayName") or "")),
            "weight": round(float(item.get("weight") or 0) * 100, 2),
        }
        for symbol, item in source.items()
        if symbol not in action_by_symbol and float(item.get("weight") or 0) > 0
    ]
    missing_target = [
        {
            "symbol": symbol,
            "name": public_display_name_for_symbol(symbol, str(item.get("displayName") or "")),
            "weight": round(float(item.get("weight") or 0) * 100, 2),
        }
        for symbol, item in target.items()
        if symbol not in action_by_symbol and float(item.get("weight") or 0) > 0
    ]
    target_total_from_actions = round(sum(float(item.get("targetWeight") or 0) for item in action_by_symbol.values()), 2)
    source_total_from_actions = round(sum(float(item.get("currentWeight") or 0) for item in action_by_symbol.values()), 2)
    unexpected_residuals = [
        {
            "symbol": symbol,
            "name": public_display_name_for_symbol(symbol, str(source[symbol].get("displayName") or "")),
            "finalWeight": round(float(action_by_symbol[symbol].get("targetWeight") or 0), 2),
        }
        for symbol in source
        if symbol not in target and symbol in action_by_symbol and abs(float(action_by_symbol[symbol].get("targetWeight") or 0)) > 0.01
    ]
    shared_retained = [
        {
            "symbol": symbol,
            "name": public_display_name_for_symbol(symbol, str(target[symbol].get("displayName") or source[symbol].get("displayName") or "")),
            "finalWeight": round(float(action_by_symbol[symbol].get("targetWeight") or 0), 2),
        }
        for symbol in sorted(set(source) & set(target))
        if symbol in action_by_symbol and float(action_by_symbol[symbol].get("targetWeight") or 0) > 0
    ]
    target_complete = not missing_target and abs(target_total_from_actions - 100) <= 0.25
    source_complete = not missing_source and not unexpected_residuals
    return {
        "available": bool(actions),
        "targetComplete": target_complete,
        "sourceComplete": source_complete,
        "allComplete": target_complete and source_complete,
        "targetTotalFromActions": target_total_from_actions,
        "sourceTotalFromActions": source_total_from_actions,
        "missingSourceInActions": missing_source,
        "missingTargetInActions": missing_target,
        "unexpectedSourceResiduals": unexpected_residuals,
        "sharedRetained": shared_retained,
        "summary": (
            "Le card coprono il portafoglio iniziale e ricostruiscono il target simulato."
            if target_complete and source_complete
            else "Controllo di completezza: alcune componenti richiedono verifica nei dettagli delle card."
        ),
    }


def generate_portfolio_problems(
    *,
    userPortfolio: list[dict[str, Any]],
    portfolioMetrics: dict[str, Any] | None,
    targetPortfolio: list[dict[str, Any]],
    targetMetrics: dict[str, Any] | None,
    investorProfile: dict[str, Any],
    goalPriority: str,
    maxTemporaryLoss: float | None,
    goalCompatibilityScore: float | None,
    portfolioHealthScore: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    portfolioMetrics = portfolioMetrics or {}
    targetMetrics = targetMetrics or {}
    current_items = portfolio_items_for_improvement(userPortfolio)
    current_exposure = improvement_asset_exposure(current_items)
    max_loss = first_number(maxTemporaryLoss) or risk_budget_for_profile(investorProfile)
    drawdown = abs(first_number(portfolioMetrics.get("maxDrawdown")) or 0)
    volatility = abs(first_number(portfolioMetrics.get("volatility")) or 0)
    sharpe = first_number(portfolioMetrics.get("sharpe")) or 0
    target_sharpe = first_number(targetMetrics.get("sharpe")) or sharpe
    max_weight = max((float(item.get("weight") or 0) for item in current_items), default=0)
    risk_profile = str(investorProfile.get("riskPreference") or "balanced").lower()
    problems: list[dict[str, Any]] = []

    def add_problem(code: str, title: str, message: str, priority: str, why: str, related_metric: str, suggested_action_type: str) -> None:
        problems.append(
            {
                "id": code,
                "code": code,
                "severity": priority,
                "priority": priority,
                "title": title,
                "explanation": message,
                "message": message,
                "whyItMatters": why,
                "relatedMetric": related_metric,
                "suggestedActionType": suggested_action_type,
            }
        )

    if max_loss > 0 and drawdown > max_loss:
        add_problem(
            "risk_above_profile",
            "Rischio superiore alla soglia indicata",
            f"La peggiore perdita storica simulata e circa {drawdown * 100:.1f}%, sopra la soglia indicata di {max_loss * 100:.1f}%.",
            "high",
            "Una perdita temporanea superiore alla soglia psicologica puo rendere piu difficile mantenere il piano nei momenti difficili.",
            "peggiore perdita storica",
            "risk_reduction",
        )
    if target_sharpe > sharpe + 0.15:
        add_problem(
            "weak_risk_return",
            "Rendimento non pienamente proporzionato al rischio",
            "Il target mostra un rapporto rischio-rendimento migliore rispetto al portafoglio inserito.",
            "medium",
            "Il punto non e solo crescere, ma capire quanta instabilita e stata necessaria per ottenere quella crescita.",
            "rapporto rischio-rendimento",
            "return_efficiency",
        )
    target_volatility = abs(first_number(targetMetrics.get("volatility")) or 0)
    if target_volatility > 0 and volatility > target_volatility * 1.2:
        add_problem(
            "volatility_above_target",
            "Oscillazione superiore al target",
            "L'oscillazione storica simulata del portafoglio e superiore a quella del target usato.",
            "medium",
            "Un percorso piu instabile puo rendere piu difficile rispettare il piano nei periodi sfavorevoli.",
            "oscillazione media",
            "risk_reduction",
        )
    if max_weight >= 0.45:
        add_problem(
            "high_concentration",
            "Concentrazione elevata",
            f"Lo strumento principale pesa circa {max_weight * 100:.1f}% del portafoglio.",
            "high" if max_weight >= 0.65 else "medium",
            "Una forte concentrazione puo far dipendere il risultato da pochi strumenti o da una sola area di mercato.",
            "peso massimo singolo strumento",
            "diversification",
        )
    if goalPriority == "capital_protection" and current_exposure.get("equity", 0) > 0.45:
        add_problem(
            "equity_high_for_protection",
            "Esposizione azionaria alta per protezione capitale",
            "Per un obiettivo di protezione del capitale, la quota azionaria appare elevata.",
            "high",
            "La priorita e ridurre oscillazioni e perdita potenziale, non massimizzare il rendimento.",
            "quota azionaria",
            "risk_reduction",
        )
    if goalPriority == "house_future_expense" and current_exposure.get("equity", 0) > 0.55:
        add_problem(
            "equity_high_for_future_expense",
            "Azionario elevato per una spesa futura",
            "Per un capitale che potrebbe servire in futuro, una quota azionaria alta puo aumentare oscillazioni indesiderate.",
            "high",
            "La priorita e evitare oscillazioni eccessive prima della data in cui il capitale potrebbe servire.",
            "quota azionaria",
            "risk_reduction",
        )
    if risk_profile in {"conservative", "balanced"} and current_exposure.get("bonds", 0) + current_exposure.get("cash_like", 0) < 0.20 and current_exposure.get("equity", 0) > 0.65:
        add_problem(
            "low_defensive_component",
            "Componente difensiva limitata",
            "La quota obbligazionaria o cash-like appare contenuta rispetto a un profilo prudente o bilanciato.",
            "medium",
            "Una componente difensiva puo ridurre oscillazioni e perdita temporanea nei periodi sfavorevoli.",
            "quota obbligazionaria o cash-like",
            "drawdown_reduction",
        )
    if goalPriority in {"capital_growth", "retirement_long_term"} and current_exposure.get("equity", 0) < 0.35:
        add_problem(
            "too_prudent_for_growth",
            "Struttura prudente rispetto a un obiettivo di crescita",
            "La quota azionaria appare contenuta per un obiettivo orientato alla crescita di lungo periodo.",
            "medium",
            "Nel lungo periodo una struttura troppo prudente puo ridurre il potenziale di crescita, pur abbassando le oscillazioni.",
            "quota azionaria",
            "goal_alignment",
        )
    if len(current_items) < 3 or max(current_exposure.values(), default=0) > 0.85:
        add_problem(
            "limited_diversification",
            "Diversificazione limitata",
            "Il portafoglio sembra dipendere da pochi strumenti o da una sola macro asset class.",
            "medium",
            "Avere piu fonti di rendimento puo ridurre il rischio che tutto si muova nella stessa direzione.",
            "numero strumenti e concentrazione asset class",
            "diversification",
        )
    if goalCompatibilityScore is not None and goalCompatibilityScore < 60:
        add_problem(
            "low_goal_fit",
            "Coerenza con obiettivo bassa",
            f"La coerenza con l'obiettivo risulta circa {goalCompatibilityScore:.0f}/100.",
            "high",
            "Un portafoglio puo essere efficiente sulla carta ma poco adatto se non rispetta orizzonte, obiettivo e tolleranza alle perdite.",
            "coerenza obiettivo",
            "goal_alignment",
        )
    health_value = first_number((portfolioHealthScore or {}).get("overall"))
    if health_value is not None and health_value < 60:
        add_problem(
            "low_checkup",
            "Check-up sotto la soglia di attenzione",
            f"Il punteggio complessivo e circa {health_value:.0f}/100.",
            "high",
            "Il dato segnala che piu aree del portafoglio meritano una verifica, non un solo numero isolato.",
            "check-up portafoglio",
            "goal_alignment",
        )

    priority_order = {
        "risk_above_profile": 0,
        "low_goal_fit": 1,
        "low_checkup": 2,
        "high_concentration": 3,
        "weak_risk_return": 4,
    }
    rank = {"high": 0, "medium": 1, "low": 2}
    problems.sort(key=lambda item: (priority_order.get(item["code"], 20), rank.get(item["priority"], 9)))
    return problems[:5]


def generate_recommendations(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for problem in problems[:3]:
        code = problem.get("code")
        if code == "risk_above_profile":
            title = "Riduci il rischio non coerente con il profilo"
            message = "Il portafoglio mostra una perdita massima simulata superiore alla soglia dichiarata. Una possibile area di miglioramento e ridurre l'esposizione agli asset piu volatili e aumentare la componente difensiva."
        elif code == "high_concentration":
            title = "Riduci la dipendenza da pochi strumenti"
            message = "La concentrazione rende il risultato piu sensibile a pochi strumenti. Un possibile intervento e distribuire meglio i pesi per avvicinare il portafoglio al target scelto."
        elif code == "low_defensive_component":
            title = "Aumenta la stabilita potenziale"
            message = "La componente difensiva appare limitata rispetto al profilo. Una possibile area da valutare e una struttura piu bilanciata tra crescita e stabilita."
        elif code == "too_prudent_for_growth":
            title = "Verifica il potenziale di crescita"
            message = "Per obiettivi di lungo periodo orientati alla crescita, una struttura molto prudente potrebbe ridurre il potenziale rendimento storico simulato."
        elif code == "weak_risk_return":
            title = "Migliora il rapporto rischio-rendimento"
            message = "Il target mostra una gestione piu efficiente del rischio. L'area da valutare e il peso degli strumenti che aumentano oscillazione senza contribuire in modo proporzionato."
        else:
            title = problem.get("title") or "Area da monitorare"
            message = problem.get("message") or "Questa area merita una verifica nel prossimo controllo del portafoglio."
        recommendations.append({"title": title, "message": message, "priority": problem.get("priority", "medium")})
    return recommendations


def calculate_correction_impact(
    *,
    currentMetrics: dict[str, Any] | None,
    targetMetrics: dict[str, Any] | None,
    currentHealthScore: dict[str, Any] | None,
    targetHealthScore: dict[str, Any] | None,
    currentExposure: dict[str, float] | None,
    targetExposure: dict[str, float] | None,
    goalCompatibilityBefore: float | None,
    goalCompatibilityAfter: float | None,
) -> dict[str, Any]:
    currentMetrics = currentMetrics or {}
    targetMetrics = targetMetrics or {}
    currentExposure = currentExposure or {}
    targetExposure = targetExposure or {}
    before_score = first_number((currentHealthScore or {}).get("overall"))
    after_score = first_number((targetHealthScore or {}).get("overall"))

    def row(label: str, before: Any, after: Any, formatter: str, effect: str) -> dict[str, Any]:
        return {"metric": label, "before": before, "after": after, "format": formatter, "effect": effect}

    rows = [
        row("Check-up", before_score, after_score, "score", "Piu coerente" if after_score and before_score and after_score > before_score else "Da confrontare"),
        row("Coerenza obiettivo", goalCompatibilityBefore, goalCompatibilityAfter, "score", "Piu vicino al profilo" if goalCompatibilityAfter and goalCompatibilityBefore and goalCompatibilityAfter > goalCompatibilityBefore else "Da confrontare"),
        row("Oscillazione media", first_number(currentMetrics.get("volatility")), first_number(targetMetrics.get("volatility")), "percent", "Meno volatile"),
        row("Peggiore perdita storica", first_number(currentMetrics.get("maxDrawdown")), first_number(targetMetrics.get("maxDrawdown")), "percent", "Riduce il rischio"),
        row("Rapporto rischio-rendimento", first_number(currentMetrics.get("sharpe")), first_number(targetMetrics.get("sharpe")), "number", "Migliora efficienza"),
        row("Crescita media annua", first_number(currentMetrics.get("cagr"), currentMetrics.get("expectedReturn")), first_number(targetMetrics.get("cagr"), targetMetrics.get("expectedReturn")), "percent", "Da valutare"),
        row("Quota azionaria", currentExposure.get("equity"), targetExposure.get("equity"), "percent", "Piu vicino al target"),
        row("Quota obbligazionaria", currentExposure.get("bonds"), targetExposure.get("bonds"), "percent", "Piu vicino al target"),
    ]
    return {
        "healthScoreBefore": before_score,
        "healthScoreAfter": after_score,
        "goalCompatibilityBefore": goalCompatibilityBefore,
        "goalCompatibilityAfter": goalCompatibilityAfter,
        "volatilityBefore": first_number(currentMetrics.get("volatility")),
        "volatilityAfter": first_number(targetMetrics.get("volatility")),
        "maxDrawdownBefore": first_number(currentMetrics.get("maxDrawdown")),
        "maxDrawdownAfter": first_number(targetMetrics.get("maxDrawdown")),
        "sharpeBefore": first_number(currentMetrics.get("sharpe")),
        "sharpeAfter": first_number(targetMetrics.get("sharpe")),
        "expectedReturnBefore": first_number(currentMetrics.get("cagr"), currentMetrics.get("expectedReturn")),
        "expectedReturnAfter": first_number(targetMetrics.get("cagr"), targetMetrics.get("expectedReturn")),
        "equityExposureBefore": currentExposure.get("equity"),
        "equityExposureAfter": targetExposure.get("equity"),
        "bondExposureBefore": currentExposure.get("bonds"),
        "bondExposureAfter": targetExposure.get("bonds"),
        "goldExposureBefore": currentExposure.get("gold"),
        "goldExposureAfter": targetExposure.get("gold"),
        "commodityExposureBefore": currentExposure.get("commodities"),
        "commodityExposureAfter": targetExposure.get("commodities"),
        "rows": [item for item in rows if item["before"] is not None or item["after"] is not None],
    }


SIMULATED_ETF_CANDIDATES = {
    "real_risk": [
        "IE00BCRY6557",
        "IE00BZ043R46",
        "IE00B3F81R35",
        "IE00B4WXJJ64",
        "IE00BGSF1X88",
        "IE00B4ND3602",
    ],
    "efficiency_defensive": [
        "IE00BZ043R46",
        "IE00BCRY6557",
        "IE00B3F81R35",
        "IE00B4ND3602",
        "IE00B4WXJJ64",
    ],
    "efficiency_growth": [
        "IE00B6R52259",
        "IE00B4L5Y983",
        "IE00B3RBWM25",
        "IE00B5BMR087",
    ],
    "diversification_usa": [
        "IE000R4ZNTN3",
        "IE00B1YZSC51",
        "IE00BKM4GZ66",
        "IE00BMG6Z448",
    ],
    "diversification_equity": [
        "IE00BZ043R46",
        "IE00BCRY6557",
        "IE00B4ND3602",
        "IE00BD6FTQ80",
    ],
    "diversification_bond": [
        "IE00B6R52259",
        "IE00B4L5Y983",
        "IE00B3RBWM25",
    ],
    "diversification_large_cap": [
        "IE00BCBJG560",
        "IE00BF4RFH31",
    ],
    "goal_protection": [
        "IE00BCRY6557",
        "IE00BZ043R46",
        "IE00B3F81R35",
        "IE00B4WXJJ64",
    ],
    "goal_future_expense": [
        "IE00BCRY6557",
        "IE00B4WXJJ64",
        "IE00BZ043R46",
    ],
    "goal_growth": [
        "IE00B6R52259",
        "IE00B4L5Y983",
        "IE00B3RBWM25",
        "IE00B5BMR087",
    ],
    "goal_retirement": [
        "IE00B6R52259",
        "IE00B3RBWM25",
        "IE00B4L5Y983",
        "IE00BZ043R46",
    ],
    "goal_income": [
        "IE00B3F81R35",
        "IE00B4L60045",
        "IE00B6YX5D40",
        "DE000A0F5UH1",
        "IE00B66F4759",
    ],
}


ETF_CATEGORY_HINTS = {
    "IE00BCRY6557": ("Obbligazionario breve / cash-like", "cash_like", "difensivo"),
    "IE00BZ043R46": ("Obbligazionario globale aggregato", "bonds", "difensivo"),
    "IE00B3F81R35": ("Obbligazionario corporate euro", "bonds", "difensivo"),
    "IE00B4WXJJ64": ("Obbligazionario governativo euro", "bonds", "difensivo"),
    "IE00BGSF1X88": ("Treasury USA brevissimo", "cash_like", "difensivo"),
    "IE00B4ND3602": ("Oro fisico", "gold", "diversificatore"),
    "IE00BD6FTQ80": ("Commodity globali", "commodities", "diversificatore"),
    "IE00B6R52259": ("Azionario globale", "equity", "globale"),
    "IE00B4L5Y983": ("Azionario mercati sviluppati", "equity", "globale"),
    "IE00B3RBWM25": ("Azionario globale all-world", "equity", "globale"),
    "IE00B5BMR087": ("Azionario USA", "equity", "growth"),
    "IE000R4ZNTN3": ("Azionario sviluppato ex-USA", "equity", "diversificatore geografico"),
    "IE00B1YZSC51": ("Azionario Europa", "equity", "diversificatore geografico"),
    "IE00BKM4GZ66": ("Azionario emergenti", "equity", "aggressivo"),
    "IE00BMG6Z448": ("Azionario emergenti ex-China", "equity", "aggressivo"),
    "IE00BCBJG560": ("Azionario small cap globale", "equity", "advanced"),
    "IE00BF4RFH31": ("Azionario small cap globale", "equity", "advanced"),
    "IE00B4L60045": ("Obbligazionario corporate euro breve", "bonds", "income"),
    "IE00B6YX5D40": ("Azionario dividendi USA", "equity", "income"),
    "DE000A0F5UH1": ("Azionario dividendi globale", "equity", "income"),
    "IE00B66F4759": ("Obbligazionario high yield euro", "bonds", "advanced"),
}


def standard_etf_library() -> dict[str, dict[str, Any]]:
    library: dict[str, dict[str, Any]] = {}
    for portfolio in STANDARD_PORTFOLIOS:
        plan = normalize_plan(portfolio.get("plan", "PLUS"))
        category = str(portfolio.get("category") or "").lower()
        risk_level = first_number(portfolio.get("riskLevel")) or 3
        for holding in portfolio.get("holdings", []):
            isin = str(holding.get("isin") or "").strip().upper()
            if not isin:
                continue
            item = library.setdefault(
                isin,
                {
                    "isin": isin,
                    "name": public_display_name_for_symbol(isin, str(holding.get("name") or "")),
                    "role": str(holding.get("role") or ""),
                    "plans": set(),
                    "portfolioCategories": set(),
                    "riskLevels": [],
                    "standardCount": 0,
                },
            )
            item["plans"].add(plan)
            item["portfolioCategories"].add(category)
            item["riskLevels"].append(risk_level)
            item["standardCount"] += 1
            if not item.get("role") and holding.get("role"):
                item["role"] = str(holding.get("role"))
    for isin, item in library.items():
        category, asset_class, style = ETF_CATEGORY_HINTS.get(isin, ("ETF standard QuantInvest", infer_asset_class(isin), "standard"))
        item["category"] = category
        item["assetClass"] = asset_class
        item["style"] = style
        item["plans"] = sorted(item["plans"], key=lambda value: planRank(value) if "planRank" in globals() else 0)
        item["portfolioCategories"] = sorted(item["portfolioCategories"])
        item["averageRiskLevel"] = sum(item["riskLevels"]) / len(item["riskLevels"]) if item["riskLevels"] else 3
    return library


def planRank(plan: str) -> int:
    return {"FREE": 0, "PLUS": 1, "ADVANCED": 2}.get(normalize_plan(plan), 1)


def normalize_goal_priority(value: Any) -> str:
    goal = str(value or "").lower()
    if goal in {"capital_preservation", "capital_protection", "protection"} or any(token in goal for token in ("protezione", "preserv", "conserv", "sicurezza")):
        return "capital_protection"
    if goal in {"home", "house", "house_future_expense", "future_expense"} or any(token in goal for token in ("casa", "spesa", "futura", "acquisto")):
        return "house_future_expense"
    if goal in {"income", "periodic_income"} or any(token in goal for token in ("entrate", "periodic", "reddito", "rendita", "cedol", "dividend")):
        return "periodic_income"
    if goal in {"retirement", "retirement_long_term", "pension"} or any(token in goal for token in ("pensione", "pension", "previd")):
        return "retirement_long_term"
    return "capital_growth"


def normalized_risk_preference(profile: dict[str, Any]) -> str:
    risk = str(profile.get("riskPreference") or profile.get("riskProfile") or profile.get("declaredRiskTolerance") or "balanced").lower()
    if risk in {"very_low", "low", "conservative", "defensive", "prudente"}:
        return "conservative"
    if risk in {"high", "very_high", "aggressive", "growth"}:
        return "aggressive"
    return "balanced"


def investment_experience(profile: dict[str, Any]) -> str:
    return str(profile.get("experience") or profile.get("investmentExperience") or "base").lower()


GOAL_ALIGNMENT_TAGS_BY_ISIN: dict[str, set[str]] = {
    "US0378331005": {"equity_us", "equity_large_cap", "growth_quality", "low_income", "single_stock"},
    "AAPL": {"equity_us", "equity_large_cap", "growth_quality", "low_income", "single_stock"},
    "US5949181045": {"equity_us", "equity_large_cap", "growth_quality", "low_income", "single_stock"},
    "MSFT": {"equity_us", "equity_large_cap", "growth_quality", "low_income", "single_stock"},
    "IE00B5BMR087": {"equity_us", "equity_large_cap", "broad_market", "moderate_income", "growth_core"},
    "IE00B6YX5D40": {"equity_dividend", "income", "equity_us", "dividend_quality"},
    "DE000A0F5UH1": {"equity_dividend", "income", "global_dividend"},
    "IE00B3F81R35": {"bond_corporate", "income", "defensive"},
    "IE00B4L60045": {"bond_corporate", "bond_short_duration", "income", "defensive"},
    "IE00B66F4759": {"bond_high_yield", "income", "higher_risk_income", "advanced"},
    "IE00BCRY6557": {"cash_like", "bond_short_duration", "defensive", "capital_preservation"},
    "IE00B4ND3602": {"gold", "diversifier", "no_income"},
    "IE00B53SZB19": {"equity_us", "equity_tech", "growth", "high_volatility", "low_income"},
    "IE00B3RBWM25": {"equity_global", "broad_market", "growth_core", "moderate_income"},
    "IE00B6R52259": {"equity_global", "broad_market", "growth_core", "moderate_income"},
    "IE00B4L5Y983": {"equity_global", "broad_market", "growth_core", "moderate_income"},
    "IE000R4ZNTN3": {"equity_global", "developed_ex_usa", "broad_market", "growth_core"},
    "IE00B1YZSC51": {"equity_europe", "broad_market", "moderate_income"},
    "IE00BKM4GZ66": {"equity_emerging", "growth", "high_volatility"},
    "IE00BMG6Z448": {"equity_emerging", "growth", "high_volatility", "advanced"},
    "IE00BCBJG560": {"equity_small_cap", "growth", "high_volatility", "advanced"},
    "IE00BF4RFH31": {"equity_small_cap", "growth", "high_volatility", "advanced"},
    "IE00BZ043R46": {"bond_aggregate", "income", "defensive"},
    "IE00B4WXJJ64": {"bond_government", "income", "defensive"},
    "IE00BGSF1X88": {"cash_like", "bond_short_duration", "defensive", "capital_preservation"},
    "IE00BD6FTQ80": {"commodity", "diversifier", "no_income"},
}


def goal_alignment_income_profile(tags: set[str], item: dict[str, Any]) -> str:
    dividend_yield = first_number(item.get("dividendYield"), item.get("yield"), item.get("distributionYield"))
    if "higher_risk_income" in tags or dividend_yield is not None and dividend_yield >= 0.055:
        return "high_income"
    if "income" in tags or "equity_dividend" in tags or dividend_yield is not None and dividend_yield >= 0.03:
        return "income"
    if "moderate_income" in tags or dividend_yield is not None and dividend_yield >= 0.015:
        return "moderate_income"
    if "low_income" in tags or dividend_yield is not None and dividend_yield > 0:
        return "low_income"
    if "no_income" in tags:
        return "no_income"
    return "unknown"


def goal_alignment_risk_profile(tags: set[str], asset_class: str) -> str:
    if "high_volatility" in tags or "higher_volatility" in tags or "thematic" in tags or "equity_tech" in tags or "equity_emerging" in tags:
        return "high"
    if "bond_high_yield" in tags or "equity_small_cap" in tags or asset_class == "commodity":
        return "medium_high"
    if "equity" in tags or any(tag.startswith("equity_") for tag in tags) or asset_class == "equity":
        return "medium"
    if "bond_corporate" in tags or "bond_aggregate" in tags or asset_class == "bond":
        return "low_medium"
    if "cash_like" in tags or "bond_short_duration" in tags or "defensive" in tags:
        return "low"
    return "unknown"


def goal_alignment_asset_class_from_tags(tags: set[str], fallback: str = "") -> str:
    normalized = str(fallback or "").lower()
    if "cash_like" in tags:
        return "cash_like"
    if "gold" in tags:
        return "gold"
    if "commodity" in tags:
        return "commodity"
    if any(tag.startswith("bond_") or tag == "bond" for tag in tags):
        return "bond"
    if any(tag.startswith("equity_") or tag == "equity" for tag in tags):
        return "equity"
    if "commod" in normalized:
        return "commodity"
    if "gold" in normalized:
        return "gold"
    if "cash" in normalized:
        return "cash_like"
    if "bond" in normalized or normalized == "bonds":
        return "bond"
    if "equity" in normalized or normalized in {"stock", "stocks", "azioni"}:
        return "equity"
    return "unknown"


def goal_alignment_region_from_tags(tags: set[str], item: dict[str, Any]) -> str | None:
    country = str(item.get("country") or item.get("region") or "").lower()
    if "equity_us" in tags or country in {"us", "usa", "united states", "stati uniti"}:
        return "usa"
    if "equity_europe" in tags or country in {"de", "fr", "it", "es", "nl", "europe", "europa"}:
        return "europe"
    if "equity_emerging" in tags:
        return "emerging_markets"
    if "developed_ex_usa" in tags:
        return "developed_ex_usa"
    if "equity_global" in tags:
        return "global"
    return None


def classify_instrument_for_goal_alignment(instrument: dict[str, Any]) -> dict[str, Any]:
    item = instrument or {}
    symbol = str(item.get("symbol") or item.get("isin") or item.get("ticker") or "").strip().upper()
    text = " ".join(
        str(item.get(key) or "")
        for key in (
            "displayName",
            "name",
            "role",
            "assetClass",
            "category",
            "assetType",
            "securityType",
            "sector",
            "industry",
            "country",
            "fundCategory",
            "description",
            "benchmark",
            "benchmarkIndex",
            "ticker",
            "symbol",
        )
    ).lower()
    tags = set(GOAL_ALIGNMENT_TAGS_BY_ISIN.get(symbol, set()))
    reasons: list[str] = []
    classification_source = "fallback_unknown"
    confidence = "low"

    if tags:
        classification_source = "standard_library"
        confidence = "high"
        reasons.append("Strumento riconosciuto nella libreria locale QuantInvest.")

    metadata_fields = [
        item.get("assetType"),
        item.get("securityType"),
        item.get("sector"),
        item.get("industry"),
        item.get("country"),
        item.get("fundCategory"),
        item.get("description"),
        item.get("benchmark"),
        item.get("benchmarkIndex"),
    ]
    has_metadata = any(str(value or "").strip() for value in metadata_fields)
    asset_type = str(item.get("assetType") or item.get("securityType") or item.get("assetClass") or "").lower()
    sector = str(item.get("sector") or item.get("industry") or item.get("marketSector") or "").lower()
    country = str(item.get("country") or "").lower()

    if has_metadata and classification_source == "fallback_unknown":
        classification_source = "metadata"
        confidence = "medium"
        reasons.append("Classificazione derivata dai metadata disponibili.")

    if any(token in asset_type for token in ("stock", "equity", "common stock", "share", "azione")):
        tags.add("equity")
        if country in {"us", "usa", "united states", "stati uniti"}:
            tags.add("equity_us")
        if "technology" in sector or "information technology" in sector or "software" in sector:
            tags.update({"equity_tech", "growth", "low_income", "high_volatility"})
        elif any(token in sector for token in ("utilities", "consumer staples", "telecom", "communication")):
            tags.add("moderate_income")
        else:
            tags.add("low_income")
        tags.add("single_stock")
        if has_metadata:
            confidence = "high" if sector or country else "medium"

    keyword_rules: list[tuple[tuple[str, ...], set[str], str]] = [
        (("dividend", "dividends", "income", "high dividend", "dividend aristocrats", "select dividend", "distribution", "yield", "dividendi"), {"equity_dividend", "income"}, "Keyword dividend/income rilevata."),
        (("bond", "aggregate", "government bond", "treasury", "corporate bond", "credit", "fixed income", "euro government", "btp", "bund", "gilts", "obblig"), {"bond", "income", "defensive"}, "Keyword obbligazionaria rilevata."),
        (("ultrashort", "short duration", "0-1yr", "1-3yr", "money market", "cash", "overnight", "floating rate", "breve durata"), {"bond_short_duration", "cash_like", "defensive", "capital_preservation"}, "Keyword breve durata/cash-like rilevata."),
        (("msci world", "acwi", "all-world", "global equity", "ftse all-world", "developed world"), {"equity_global", "broad_market", "growth_core"}, "Keyword azionario globale rilevata."),
        (("s&p 500", "s&p500", "sp 500", "usa", "us equity", "msci usa"), {"equity_us", "broad_market", "equity_large_cap", "moderate_income"}, "Keyword azionario USA rilevata."),
        (("nasdaq", "information technology", "technology", "semiconductor", "tecnologia"), {"equity_tech", "growth", "high_volatility", "low_income"}, "Keyword tecnologia/Nasdaq rilevata."),
        (("emerging markets", "em ex-china", "em ", " china", "india", "latin america", "asia", "mercati emergenti"), {"equity_emerging", "higher_volatility", "high_volatility", "growth"}, "Keyword mercati emergenti rilevata."),
        (("gold", "physical gold", "oro"), {"gold", "diversifier", "no_income"}, "Keyword oro rilevata."),
        (("commodity", "commodities", "bloomberg commodity", " oil", "energy", "uranium"), {"commodity", "diversifier", "no_income"}, "Keyword commodity rilevata."),
        (("ai", "artificial intelligence", "big data", "space", "defense", "cybersecurity", "clean energy", "robotics", "innovation", "uranium", "nuclear"), {"thematic", "growth", "high_volatility", "advanced", "low_income"}, "Keyword tematica rilevata."),
        (("small cap",), {"equity_small_cap", "growth", "high_volatility"}, "Keyword small cap rilevata."),
    ]
    matched_keyword = False
    for tokens, rule_tags, reason in keyword_rules:
        if any(token in text for token in tokens):
            tags.update(rule_tags)
            reasons.append(reason)
            matched_keyword = True

    if matched_keyword and classification_source == "fallback_unknown":
        classification_source = "name_keyword"
        confidence = "medium" if len(text.strip()) > 4 else "low"
    elif matched_keyword and classification_source == "metadata" and confidence == "medium":
        confidence = "high" if asset_type and (sector or item.get("fundCategory") or item.get("benchmarkIndex")) else "medium"

    fallback_asset_class = str(item.get("assetClass") or infer_asset_class_from_text(symbol, text) or "")
    asset_class = goal_alignment_asset_class_from_tags(tags, fallback_asset_class)
    if asset_class != "unknown" and not tags:
        tags.add(asset_class)
    if not tags:
        tags.add("unknown")
        reasons.append("Dati insufficienti per una classificazione affidabile.")

    style_tags = sorted(tag for tag in tags if tag not in {"income", "low_income", "moderate_income", "no_income"})
    income_profile = goal_alignment_income_profile(tags, item)
    risk_profile = goal_alignment_risk_profile(tags, asset_class)
    region = goal_alignment_region_from_tags(tags, item)
    if classification_source == "fallback_unknown":
        confidence = "low"

    return {
        "assetClass": asset_class,
        "region": region,
        "styleTags": style_tags,
        "incomeProfile": income_profile,
        "riskProfile": risk_profile,
        "goalTags": sorted(tags),
        "confidence": confidence,
        "classificationSource": classification_source,
        "reasons": list(dict.fromkeys(reasons)),
    }


def classifyInstrumentForGoalAlignment(instrument: dict[str, Any]) -> dict[str, Any]:
    return classify_instrument_for_goal_alignment(instrument)


def goal_alignment_tags_for_item(item: dict[str, Any]) -> set[str]:
    return set(classify_instrument_for_goal_alignment(item).get("goalTags") or [])


def normalized_item_weight(item: dict[str, Any], total_weight: float) -> float:
    value = abs(first_number(item.get("weight"), item.get("allocation")) or 0)
    if total_weight <= 1.5:
        value *= 100
    return value


def goal_alignment_exposures(contribution: list[dict[str, Any]], portfolio_context: dict[str, Any] | None = None) -> dict[str, Any]:
    items = contribution or []
    total_weight = sum(abs(first_number(item.get("weight"), item.get("allocation")) or 0) for item in items)
    exposures: dict[str, float] = {
        "income": 0.0,
        "dividend": 0.0,
        "bond": 0.0,
        "cashLike": 0.0,
        "defensive": 0.0,
        "equity": 0.0,
        "growth": 0.0,
        "growthCore": 0.0,
        "techThematic": 0.0,
        "highVolatility": 0.0,
        "highYield": 0.0,
        "goldCommodity": 0.0,
        "singleStock": 0.0,
        "globalDiversified": 0.0,
        "lowConfidence": 0.0,
        "unknown": 0.0,
    }
    max_weight = 0.0
    classification_sources: dict[str, float] = {}
    for item in items:
        weight = normalized_item_weight(item, total_weight)
        max_weight = max(max_weight, weight)
        classification = classify_instrument_for_goal_alignment(item)
        tags = set(classification.get("goalTags") or [])
        income_profile = str(classification.get("incomeProfile") or "unknown")
        risk_profile = str(classification.get("riskProfile") or "unknown")
        source = str(classification.get("classificationSource") or "fallback_unknown")
        classification_sources[source] = classification_sources.get(source, 0.0) + weight
        if classification.get("confidence") == "low":
            exposures["lowConfidence"] += weight
        if "unknown" in tags or classification.get("assetClass") == "unknown":
            exposures["unknown"] += weight
        if any(tag.startswith("equity") for tag in tags):
            exposures["equity"] += weight
        if "income" in tags or income_profile in {"income", "high_income"}:
            exposures["income"] += weight
        if "equity_dividend" in tags or "global_dividend" in tags or "dividend_quality" in tags:
            exposures["dividend"] += weight
        if any(tag.startswith("bond_") or tag == "bond" for tag in tags):
            exposures["bond"] += weight
        if "cash_like" in tags:
            exposures["cashLike"] += weight
        if "defensive" in tags or "capital_preservation" in tags:
            exposures["defensive"] += weight
        if "growth" in tags or "growth_quality" in tags:
            exposures["growth"] += weight
        if "growth_core" in tags or "broad_market" in tags:
            exposures["growthCore"] += weight
        if "equity_tech" in tags or "thematic" in tags:
            exposures["techThematic"] += weight
        if "high_volatility" in tags or "higher_volatility" in tags or "equity_emerging" in tags or "equity_small_cap" in tags or risk_profile == "high":
            exposures["highVolatility"] += weight
        if "bond_high_yield" in tags or "higher_risk_income" in tags:
            exposures["highYield"] += weight
        if "gold" in tags or "commodity" in tags:
            exposures["goldCommodity"] += weight
        if "single_stock" in tags:
            exposures["singleStock"] += weight
        if "equity_global" in tags or "broad_market" in tags or "bond_aggregate" in tags:
            exposures["globalDiversified"] += weight
    context = portfolio_context or {}
    return {
        **{key: round(value, 1) for key, value in exposures.items()},
        "maxWeight": round(max_weight, 1),
        "category": str(context.get("category") or "").lower(),
        "riskLevel": first_number(context.get("riskLevel")),
        "portfolioName": str(context.get("name") or ""),
        "classificationSources": {key: round(value, 1) for key, value in classification_sources.items()},
    }


def calculate_goal_alignment_fallback(
    contribution: list[dict[str, Any]],
    investor_profile: dict[str, Any],
    metrics: dict[str, Any] | None = None,
    portfolio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = metrics or {}
    goal = normalize_goal_priority(investor_profile.get("goalPriority") or investor_profile.get("objective"))
    risk = normalized_risk_preference(investor_profile)
    horizon = int(float(investor_profile.get("horizonYears") or 0))
    max_loss = first_number(investor_profile.get("maxTemporaryLoss")) or risk_budget_for_profile(investor_profile)
    max_drawdown = abs(first_number(metrics.get("maxDrawdown")) or 0)
    exposures = goal_alignment_exposures(contribution, portfolio_context)
    adjustment = 0.0
    hard_cap: int | None = None
    reasons: list[str] = []
    warnings: list[str] = []
    matched: list[str] = []
    missing: list[str] = []
    category = exposures["category"]

    def cap(value: int, reason: str) -> None:
        nonlocal hard_cap
        hard_cap = value if hard_cap is None else min(hard_cap, value)
        warnings.append(reason)

    income = exposures["income"]
    equity = exposures["equity"]
    growth = exposures["growth"] + exposures["growthCore"] * 0.45
    defensive = exposures["defensive"] + exposures["cashLike"] * 0.35
    tech_thematic = exposures["techThematic"]
    high_vol = exposures["highVolatility"]
    single_stock = exposures["singleStock"]
    bond_cash = exposures["bond"] + exposures["cashLike"]
    unknown = exposures.get("unknown", 0.0)
    low_confidence = exposures.get("lowConfidence", 0.0)
    if low_confidence >= 20:
        warnings.append("Alcuni strumenti non sono presenti nella libreria QuantInvest e sono stati classificati tramite metadata o parole chiave. La coerenza con obiettivi può essere stimata con minore precisione.")

    if goal == "periodic_income":
        if income < 20:
            if unknown >= 50 and equity + growth + tech_thematic < 50:
                adjustment -= 8
                missing.append("classificazione income più affidabile")
                reasons.append("L'obiettivo è entrate periodiche, ma diversi strumenti non permettono di stimare con precisione la componente income.")
            else:
                adjustment -= 28
                missing.append("componenti income/dividend/obbligazionarie")
                reasons.append("L'obiettivo è entrate periodiche, ma il portafoglio è orientato soprattutto a crescita o azionario generico.")
        elif income < 40:
            adjustment -= 14
            missing.append("quota income più solida")
        elif income > 50:
            adjustment += 16
            matched.append("componenti income/dividend/bond coerenti")
        if category == "income" or "income" in exposures["portfolioName"].lower() or "dividend" in exposures["portfolioName"].lower():
            adjustment += 18
            matched.append("portfolio standard con categoria income/dividend")
        if (single_stock + tech_thematic >= 65 or (equity >= 90 and income < 25)):
            adjustment -= 14
            cap(40 if single_stock + tech_thematic >= 90 else 50, "Portafoglio growth/core equity poco coerente con entrate periodiche.")
        if exposures["highYield"] >= 20:
            warnings.append("La componente high yield può aumentare il reddito potenziale ma anche il rischio di credito.")
    elif goal == "capital_protection":
        if defensive >= 55 or bond_cash >= 60:
            adjustment += 16
            matched.append("componenti difensive e obbligazionarie")
        if equity > 60:
            adjustment -= 24
            cap(45, "Quota azionaria elevata rispetto alla protezione del capitale.")
        if equity > 90:
            adjustment -= 10
            cap(35, "Portafoglio quasi interamente azionario rispetto a un obiettivo di protezione.")
        if tech_thematic + high_vol > 35:
            adjustment -= 16
            missing.append("stabilità e bassa volatilità")
        if max_drawdown and max_drawdown > max_loss:
            adjustment -= 18
            reasons.append("La perdita storica supera la perdita massima dichiarata.")
    elif goal == "house_future_expense":
        if defensive >= 50 or exposures["cashLike"] + exposures["bond"] >= 55:
            adjustment += 14
            matched.append("stabilità e liquidità")
        if horizon <= 5 and equity > 40:
            adjustment -= 24
            missing.append("rischio contenuto su orizzonte breve")
        if horizon <= 5 and equity > 60:
            cap(40, "Quota azionaria troppo alta per una spesa futura su orizzonte breve.")
        if max_loss <= 0.10 and max_drawdown > 0.10:
            adjustment -= 18
            cap(35, "Perdita storica superiore alla soglia dichiarata per una spesa futura.")
        if tech_thematic + high_vol > 30:
            adjustment -= 14
    elif goal == "capital_growth":
        if equity >= 45 and (exposures["globalDiversified"] >= 35 or growth >= 45):
            adjustment += 12
            matched.append("componente di crescita diversificata")
        if horizon >= 8 and equity < 30:
            if unknown >= 50:
                adjustment -= 8
                missing.append("classificazione crescita più affidabile")
            else:
                adjustment -= 24
                cap(55, "Esposizione alla crescita insufficiente per obiettivo di crescita su orizzonte lungo.")
                missing.append("componente azionaria di crescita")
        if risk == "conservative" and (tech_thematic + high_vol > 45 or equity > 80):
            adjustment -= 12
            reasons.append("La ricerca di crescita va letta insieme alla perdita massima dichiarata.")
    elif goal == "retirement_long_term":
        if horizon >= 8 and exposures["globalDiversified"] >= 45 and 35 <= equity <= 85:
            adjustment += 16
            matched.append("struttura globale diversificata per lungo periodo")
        if horizon >= 8 and equity < 25:
            if unknown >= 50:
                adjustment -= 8
            else:
                adjustment -= 18
                cap(60, "Crescita quasi assente rispetto a un obiettivo pensionistico lungo.")
        if single_stock + tech_thematic > 70:
            adjustment -= 22
            cap(55, "Concentrazione elevata in singole azioni o temi rispetto a un obiettivo pensionistico.")
        if equity > 90 and risk != "aggressive":
            adjustment -= 10
            reasons.append("Il lungo periodo non elimina la necessità di sostenibilità del rischio.")

    if low_confidence >= 30 and adjustment > 0:
        adjustment *= 0.55
        warnings.append("Il bonus di coerenza viene mantenuto prudente perché una parte del portafoglio è classificata con affidabilità bassa.")

    return {
        "goalAlignmentAdjustment": round(adjustment, 1),
        "hardCap": hard_cap,
        "reasons": list(dict.fromkeys(reasons)),
        "warnings": list(dict.fromkeys(warnings)),
        "matchedGoalDrivers": list(dict.fromkeys(matched)),
        "missingGoalDrivers": list(dict.fromkeys(missing)),
        "exposures": exposures,
    }


def health_pillar_scores(health_score: dict[str, Any] | None) -> dict[str, float]:
    health_score = health_score or {}
    scores = {"realRisk": None, "efficiency": None, "diversification": None, "goalAlignment": None}
    for pillar in health_score.get("pillars", []) if isinstance(health_score.get("pillars"), list) else []:
        key = str(pillar.get("key") or "").lower()
        name = str(pillar.get("name") or "").lower()
        score = first_number(pillar.get("score"))
        if score is None:
            continue
        if any(token in key for token in ("risk", "baserisk", "advancedrisk")) or "rischio reale" in name:
            scores["realRisk"] = score if scores["realRisk"] is None else min(scores["realRisk"], score)
        if "efficiency" in key or "efficienza" in name:
            scores["efficiency"] = score if scores["efficiency"] is None else min(scores["efficiency"], score)
        if "diversification" in key or "diversificazione" in name:
            scores["diversification"] = score if scores["diversification"] is None else min(scores["diversification"], score)
        if "goal" in key or "coerenza" in name or "obiettivo" in name:
            scores["goalAlignment"] = score if scores["goalAlignment"] is None else min(scores["goalAlignment"], score)
    return {key: float(value) for key, value in scores.items() if value is not None}


def priority_improvement_pillar(pillar_scores: dict[str, float]) -> str | None:
    weak = {key: value for key, value in pillar_scores.items() if value < 60}
    if not weak:
        return None
    weakest_key, weakest_value = min(weak.items(), key=lambda item: item[1])
    if weakest_value < 45:
        return weakest_key
    for key in ("goalAlignment", "realRisk", "diversification", "efficiency"):
        if key in weak:
            return key
    return weakest_key


def current_portfolio_has_theme(items: list[dict[str, Any]], *tokens: str) -> bool:
    text = " ".join(f"{item.get('symbol', '')} {item.get('displayName', '')} {item.get('role', '')}" for item in items).lower()
    return any(token.lower() in text for token in tokens)


def candidate_groups_for_pillar(
    pillar: str,
    *,
    current_items: list[dict[str, Any]],
    current_exposure: dict[str, float],
    metrics: dict[str, Any],
    investor_profile: dict[str, Any],
) -> list[str]:
    goal = normalize_goal_priority(investor_profile.get("objective") or investor_profile.get("goalPriority"))
    risk = normalized_risk_preference(investor_profile)
    horizon = int(float(investor_profile.get("horizonYears") or 0))
    volatility = abs(first_number(metrics.get("volatility")) or 0)
    if pillar == "realRisk":
        return ["real_risk"]
    if pillar == "efficiency":
        too_prudent = goal in {"capital_growth", "retirement_long_term"} and horizon >= 8 and current_exposure.get("equity", 0) < 0.45 and risk != "conservative"
        return ["efficiency_growth" if too_prudent and volatility < 0.16 else "efficiency_defensive"]
    if pillar == "diversification":
        if current_portfolio_has_theme(current_items, "s&p 500", "nasdaq", "usa", "technology", "semiconductor"):
            return ["diversification_usa"]
        if current_exposure.get("equity", 0) >= 0.70:
            return ["diversification_equity"]
        if current_exposure.get("bonds", 0) + current_exposure.get("cash_like", 0) >= 0.70:
            return ["diversification_bond"]
        if current_portfolio_has_theme(current_items, "large cap", "world", "acwi"):
            return ["diversification_large_cap", "diversification_equity"]
        return ["diversification_equity", "diversification_usa"]
    if pillar == "goalAlignment":
        return {
            "capital_protection": ["goal_protection"],
            "house_future_expense": ["goal_future_expense"],
            "periodic_income": ["goal_income"],
            "retirement_long_term": ["goal_retirement"],
        }.get(goal, ["goal_growth"])
    return []


def etf_candidate_allowed(candidate: dict[str, Any], *, user_plan: str, investor_profile: dict[str, Any]) -> tuple[bool, str]:
    plan = normalize_plan(user_plan)
    if plan == "FREE":
        return False, "Nel piano Free l'aggiunta simulata resta una preview: i portfolio standard sono disponibili dal piano Plus."
    candidate_plans = {normalize_plan(value) for value in candidate.get("plans", [])}
    if plan == "PLUS" and "PLUS" not in candidate_plans:
        return False, "ETF disponibile solo nei portfolio standard Advanced."
    goal = normalize_goal_priority(investor_profile.get("objective") or investor_profile.get("goalPriority"))
    risk = normalized_risk_preference(investor_profile)
    max_loss = first_number(investor_profile.get("maxTemporaryLoss")) or risk_budget_for_profile(investor_profile)
    horizon = int(float(investor_profile.get("horizonYears") or 0))
    experience = investment_experience(investor_profile)
    style = str(candidate.get("style") or "").lower()
    asset_class = str(candidate.get("assetClass") or "").lower()
    if asset_class == "equity" and (horizon < 4 or max_loss <= 0.10 or goal in {"capital_protection", "house_future_expense"}):
        return False, "ETF azionario non coerente con orizzonte breve, bassa perdita sopportabile o obiettivo difensivo."
    if style in {"aggressivo", "advanced", "growth"} and (risk == "conservative" or max_loss <= 0.10 or horizon < 5):
        return False, "ETF troppo aggressivo per profilo, orizzonte o perdita massima dichiarata."
    if style in {"advanced", "aggressivo"} and experience in {"base", "beginner"}:
        return False, "ETF avanzato o aggressivo evitato come prima scelta per esperienza base."
    if "high yield" in str(candidate.get("category") or "").lower() and risk == "conservative":
        return False, "High yield non usato come prima scelta per profili prudenti."
    return True, ""


def simulated_pillar_effect(
    *,
    candidate: dict[str, Any],
    pillar: str,
    weight: float,
    current_score: float,
    overall_score: float,
    current_exposure: dict[str, float],
    investor_profile: dict[str, Any],
) -> dict[str, float]:
    asset_class = str(candidate.get("assetClass") or "").lower()
    style = str(candidate.get("style") or "").lower()
    risk = normalized_risk_preference(investor_profile)
    goal = normalize_goal_priority(investor_profile.get("objective") or investor_profile.get("goalPriority"))
    base = weight * 100
    target_gain = 0.0
    side_effect = 0.0
    if pillar == "realRisk":
        target_gain = base * (1.35 if asset_class in {"bonds", "cash_like"} else 0.85 if asset_class == "gold" else 0.25)
        side_effect = base * (0.20 if asset_class in {"bonds", "cash_like"} else 0.35)
    elif pillar == "efficiency":
        if asset_class in {"bonds", "cash_like"} and current_exposure.get("equity", 0) > 0.65:
            target_gain = base * 1.05
        elif asset_class == "equity" and goal in {"capital_growth", "retirement_long_term"}:
            target_gain = base * 0.95
        else:
            target_gain = base * 0.65
        side_effect = base * (0.45 if style in {"aggressivo", "advanced"} and risk != "aggressive" else 0.20)
    elif pillar == "diversification":
        target_gain = base * (1.25 if asset_class not in {"unknown"} else 0.30)
        side_effect = base * (0.30 if asset_class == "commodities" or style in {"aggressivo", "advanced"} else 0.15)
    elif pillar == "goalAlignment":
        if goal in {"capital_protection", "house_future_expense"} and asset_class in {"bonds", "cash_like"}:
            target_gain = base * 1.30
        elif goal in {"capital_growth", "retirement_long_term"} and asset_class == "equity":
            target_gain = base * 1.05
        elif goal == "periodic_income" and style == "income":
            target_gain = base * 1.00
        else:
            target_gain = base * 0.55
        side_effect = base * (0.35 if asset_class == "equity" and risk == "conservative" else 0.15)
    profile_coherence = max(35.0, 90.0 - side_effect * 2)
    simplicity = 90.0 if style in {"difensivo", "globale", "standard"} else 74.0 if style in {"diversificatore", "income"} else 58.0
    estimated_after = min(100.0, current_score + max(0.0, target_gain - side_effect * 0.35))
    overall_after = min(100.0, overall_score + max(0.0, target_gain * 0.22 - side_effect * 0.15))
    candidate_score = (estimated_after - current_score) * 0.50 + (overall_after - overall_score) * 0.20 + profile_coherence * 0.15 + simplicity * 0.10 - side_effect * 0.15
    return {
        "estimatedPillarAfter": round(estimated_after, 1),
        "estimatedOverallAfter": round(overall_after, 1),
        "candidateScore": round(candidate_score, 2),
        "targetGain": round(estimated_after - current_score, 1),
        "sideEffectPenalty": round(side_effect, 1),
        "profileCoherence": round(profile_coherence, 1),
        "simplicityScore": round(simplicity, 1),
    }


def build_simulated_etf_addition(
    *,
    health_score: dict[str, Any],
    current_items: list[dict[str, Any]],
    metrics: dict[str, Any],
    investor_profile: dict[str, Any],
    user_plan: str,
    target_items: list[dict[str, Any]],
    comparison_mode: str,
    source_name: str,
    reference_name: str,
) -> dict[str, Any]:
    if not current_items:
        return {
            "available": False,
            "reason": "La card non viene mostrata perché non esiste un portafoglio sorgente migliorabile.",
        }
    if comparison_mode == "standard_only":
        return {
            "available": False,
            "reason": "In modalità solo portfolio standard non viene suggerito un ETF singolo: il modello standard non viene corretto automaticamente.",
        }
    pillar_scores = health_pillar_scores(health_score)
    target_pillar = priority_improvement_pillar(pillar_scores)
    if not target_pillar:
        return {
            "available": False,
            "reason": "Nessun pilastro principale è sotto 60/100: l'aggiunta di un ETF non viene mostrata come correzione principale.",
        }
    if normalize_plan(user_plan) == "FREE":
        return {
            "available": False,
            "locked": True,
            "comparisonMode": comparison_mode,
            "sourcePortfolioName": source_name,
            "referencePortfolioName": reference_name,
            "targetPillar": target_pillar,
            "currentPillarScore": pillar_scores.get(target_pillar),
            "message": "Preview: nel piano Free il sistema segnala il pilastro debole, ma l'aggiunta simulata da ETF standard viene sbloccata dal piano Plus.",
        }
    library = standard_etf_library()
    current_symbols = {str(item.get("symbol") or "").upper() for item in current_items}
    target_symbols = {str(item.get("symbol") or "").upper() for item in target_items}
    exposure = improvement_asset_exposure(current_items)
    groups = candidate_groups_for_pillar(
        target_pillar,
        current_items=current_items,
        current_exposure=exposure,
        metrics=metrics,
        investor_profile=investor_profile,
    )
    candidate_isins: list[str] = []
    for group in groups:
        candidate_isins.extend(SIMULATED_ETF_CANDIDATES.get(group, []))
    candidate_isins = list(dict.fromkeys(candidate_isins))
    overall = first_number(health_score.get("overall")) or 0
    current_pillar_score = float(pillar_scores.get(target_pillar) or 0)
    scored: list[dict[str, Any]] = []
    rejected: list[str] = []
    for isin in candidate_isins:
        candidate = library.get(isin)
        if not candidate:
            continue
        if isin in current_symbols:
            continue
        allowed, reason = etf_candidate_allowed(candidate, user_plan=user_plan, investor_profile=investor_profile)
        if not allowed:
            rejected.append(reason)
            continue
        for weight in (0.05, 0.10, 0.15, 0.20):
            effect = simulated_pillar_effect(
                candidate=candidate,
                pillar=target_pillar,
                weight=weight,
                current_score=current_pillar_score,
                overall_score=overall,
                current_exposure=exposure,
                investor_profile=investor_profile,
            )
            target_bonus = 1.5 if isin in target_symbols else 0.0
            standard_bonus = min(float(candidate.get("standardCount") or 0), 6.0) * 0.25
            score = effect["candidateScore"] + target_bonus + standard_bonus
            scored.append({**candidate, **effect, "simulatedWeight": weight, "rankScore": round(score, 2)})
    if not scored:
        return {
            "available": False,
            "comparisonMode": comparison_mode,
            "sourcePortfolioName": source_name,
            "referencePortfolioName": reference_name,
            "targetPillar": target_pillar,
            "currentPillarScore": round(current_pillar_score, 1),
            "reason": "Nessun ETF della libreria standard supera i filtri di piano, profilo, obiettivo e perdita massima dichiarata.",
            "warnings": list(dict.fromkeys(rejected))[:2],
        }
    scored.sort(key=lambda item: item["rankScore"], reverse=True)
    best = scored[0]
    if best["targetGain"] < 3:
        return {
            "available": False,
            "comparisonMode": comparison_mode,
            "sourcePortfolioName": source_name,
            "referencePortfolioName": reference_name,
            "targetPillar": target_pillar,
            "currentPillarScore": round(current_pillar_score, 1),
            "reason": "Nessuna singola aggiunta migliora in modo chiaro il pilastro sotto soglia senza peggiorare altre aree. Il Piano propone una revisione più bilanciata del portafoglio.",
        }
    suggestions = [best]
    pillar_labels = {
        "realRisk": "Rischio reale",
        "efficiency": "Efficienza rischio/rendimento",
        "diversification": "Diversificazione",
        "goalAlignment": "Coerenza con obiettivi",
    }
    primary = suggestions[0]
    return {
        "available": True,
        "title": "Aggiunta simulata suggerita",
        "subtitle": "Scelta locale basata sul pilastro più debole del portafoglio.",
        "selectionMethod": "fallback_locale_deterministico",
        "comparisonMode": comparison_mode,
        "sourcePortfolioName": source_name,
        "referencePortfolioName": reference_name,
        "targetPillar": target_pillar,
        "targetPillarLabel": pillar_labels.get(target_pillar, "Pilastro sotto soglia"),
        "currentPillarScore": round(current_pillar_score, 1),
        "estimatedPillarAfter": primary["estimatedPillarAfter"],
        "estimatedOverallAfter": primary["estimatedOverallAfter"],
        "suggestions": [
            {
                "name": item["name"],
                "isin": item["isin"],
                "category": item["category"],
                "assetClass": item["assetClass"],
                "simulatedWeight": round(float(item["simulatedWeight"]) * 100, 1),
                "scoreCurrent": round(current_pillar_score, 1),
                "scoreAfter": item["estimatedPillarAfter"],
                "reason": simulated_etf_reason(item, target_pillar, investor_profile),
                "expectedImprovement": simulated_etf_improvement_text(item, target_pillar),
                "watchOut": simulated_etf_watch_out(item),
                "sideEffects": simulated_etf_side_effects(item, target_pillar),
            }
            for item in suggestions[:1]
        ],
        "disclaimer": "È una simulazione educativa basata su regole locali e sugli ETF presenti nei portfolio standard QuantInvest. Non costituisce consulenza finanziaria personalizzata né indicazione operativa.",
    }


def simulated_etf_reason(candidate: dict[str, Any], pillar: str, investor_profile: dict[str, Any]) -> str:
    del investor_profile
    if pillar == "realRisk":
        return f"Il pilastro più debole è il rischio reale. {candidate['name']} viene selezionato perché può aumentare una componente più difensiva o diversificante rispetto agli asset più volatili."
    if pillar == "efficiency":
        return f"Il pilastro più debole è l'efficienza rischio/rendimento. {candidate['name']} viene selezionato perché mira a migliorare il rapporto tra crescita potenziale e oscillazioni stimate."
    if pillar == "diversification":
        return f"Il pilastro più debole è la diversificazione. {candidate['name']} viene selezionato perché aggiunge una fonte di esposizione diversa rispetto alla struttura attuale."
    return f"Il pilastro più debole è la coerenza con gli obiettivi. {candidate['name']} viene selezionato perché può avvicinare il portafoglio al profilo e all'orizzonte indicati."


def simulated_etf_improvement_text(candidate: dict[str, Any], pillar: str) -> str:
    if pillar == "realRisk":
        return "Può contribuire a ridurre oscillazioni e perdita stimata, senza garantire che il pilastro superi la soglia."
    if pillar == "efficiency":
        return "Può contribuire a migliorare il compromesso tra rischio assunto e rendimento storico/stimato."
    if pillar == "diversification":
        return "Può contribuire a ridurre la dipendenza da pochi strumenti, mercati o asset class."
    return "Può contribuire ad avvicinare la struttura del portafoglio a obiettivo, orizzonte e perdita massima dichiarata."


def simulated_etf_watch_out(candidate: dict[str, Any]) -> str:
    asset_class = str(candidate.get("assetClass") or "")
    if asset_class in {"bonds", "cash_like"}:
        return "L'aggiunta può ridurre le oscillazioni, ma potrebbe abbassare il rendimento nelle fasi positive di mercato."
    if asset_class == "equity":
        return "L'aggiunta può aumentare il potenziale di crescita o diversificazione, ma può anche aumentare le oscillazioni."
    if asset_class in {"gold", "commodities"}:
        return "L'aggiunta può diversificare, ma il suo comportamento può essere irregolare e non sempre protettivo."
    return "L'effetto va verificato insieme alle altre metriche del Piano di miglioramento."


def simulated_etf_side_effects(candidate: dict[str, Any], pillar: str) -> str:
    if candidate.get("sideEffectPenalty", 0) >= 6:
        return "Effetto collaterale da monitorare: il miglioramento del pilastro target può peggiorare stabilità, rendimento atteso o semplicità del portafoglio."
    if pillar == "realRisk":
        return "Possibile effetto collaterale: maggiore stabilità può significare minore crescita nelle fasi favorevoli."
    if pillar == "diversification":
        return "Possibile effetto collaterale: una nuova asset class può rendere il portafoglio più complesso da seguire."
    return "Possibile effetto collaterale: il beneficio stimato va confrontato con costi, complessità e coerenza del profilo."


def build_standard_only_improvement_explanation(standard_portfolio: dict[str, Any] | None, target_items: list[dict[str, Any]]) -> dict[str, Any]:
    if not standard_portfolio:
        return {}
    risk_level = standard_portfolio.get("riskLevel")
    holdings = []
    for item in target_items:
        role = item.get("role") or "Componente del modello QuantInvest"
        holdings.append(
            {
                "instrumentName": item.get("displayName") or "Strumento finanziario",
                "weight": round(float(item.get("weight") or 0) * 100, 2),
                "role": role,
                "explanation": f"{role}. Il peso indica quanto questa componente contribuisce alla struttura del modello.",
            }
        )
    return {
        "modelLogic": standard_portfolio.get("description") or "Portfolio standard costruito come modello educativo QuantInvest.",
        "profileFit": f"Profilo indicativo: rischio {risk_level}/5, orizzonte {standard_portfolio.get('suggestedHorizon', 'non specificato')}.",
        "mainRisks": standard_portfolio.get("warning") or "Il rischio principale dipende dalla combinazione tra quota azionaria, obbligazionaria e orizzonte temporale.",
        "howToModify": "Il modello puo essere usato come punto di partenza educativo. Cambiare i pesi modifica rischio, rendimento storico simulato e coerenza con il profilo.",
        "holdings": holdings,
    }


def build_standard_only_personalization(
    standard_portfolio: dict[str, Any] | None,
    investor_profile: dict[str, Any],
    user_plan: str,
) -> dict[str, Any] | None:
    if not standard_portfolio:
        return None
    risk = normalized_risk_preference(investor_profile)
    goal = normalize_goal_priority(investor_profile.get("objective") or investor_profile.get("goalPriority"))
    horizon = int(float(investor_profile.get("horizonYears") or 0))
    max_loss = first_number(investor_profile.get("maxTemporaryLoss")) or risk_budget_for_profile(investor_profile)
    if risk == "conservative" or max_loss <= 0.10 or goal in {"capital_protection", "house_future_expense"}:
        direction = "Per renderlo più prudente, l'utente può valutare un portfolio standard più difensivo o una struttura con maggiore componente obbligazionaria/cash-like."
    elif risk == "aggressive" and goal in {"capital_growth", "retirement_long_term"} and horizon >= 8:
        direction = "Per renderlo più dinamico, l'utente può confrontarlo con un portfolio standard più orientato alla crescita di lungo periodo."
    else:
        direction = "Per renderlo più diversificato, l'utente può confrontarlo con un portfolio standard multi-asset più bilanciato tra crescita e stabilità."
    available = get_available_standard_portfolios(user_plan)
    current_risk = first_number(standard_portfolio.get("riskLevel")) or 3
    if "prudente" in direction:
        alternatives = [item for item in available if (first_number(item.get("riskLevel")) or 3) < current_risk]
    elif "dinamico" in direction:
        alternatives = [item for item in available if (first_number(item.get("riskLevel")) or 3) > current_risk]
    else:
        alternatives = [item for item in available if item.get("id") != standard_portfolio.get("id")]
    return {
        "title": "Possibile personalizzazione educativa",
        "summary": "Il portfolio standard è un modello educativo: non viene corretto automaticamente e non viene suggerito un singolo ETF.",
        "direction": direction,
        "nextStep": "Se il profilo personale è diverso dal modello, confronta un altro portfolio standard coerente invece di trattare il modello come incompleto.",
        "alternativeStandard": alternatives[0] if alternatives else None,
    }


def build_improvement_plan(
    *,
    comparison_context: dict[str, Any],
    user_portfolio: list[dict[str, Any]],
    metrics: dict[str, Any],
    optimized_portfolio: dict[str, Any],
    selected_standard: dict[str, Any] | None,
    standard_benchmark_analysis: dict[str, Any],
    standard_recommendation: dict[str, Any],
    standard_recommendation_analysis: dict[str, Any],
    investor_profile: dict[str, Any],
    health_score: dict[str, Any],
    user_plan: str,
    improvement_comparison_mode: str | None = None,
) -> dict[str, Any]:
    comparison_mode = improvement_comparison_mode or comparison_context.get("comparisonMode", "optimization")
    recommended_standard = standard_recommendation.get("recommendedPortfolio") if isinstance(standard_recommendation, dict) else None
    if comparison_mode in {"final_user_vs_recommended_standard", "final_optimized_vs_recommended_standard"} and selected_standard:
        recommended_standard = selected_standard
    goal_score = goal_compatibility_from_health(health_score)
    max_loss = first_number(investor_profile.get("maxTemporaryLoss")) or risk_budget_for_profile(investor_profile)
    improvement_context = resolveImprovementContext(
        {
            "comparisonMode": comparison_mode,
            "userPortfolio": user_portfolio,
            "optimizedPortfolio": optimized_portfolio,
            "selectedStandardPortfolio": selected_standard,
            "recommendedStandardPortfolio": recommended_standard if isinstance(recommended_standard, dict) else None,
            "portfolioHealthScore": first_number(health_score.get("overall")),
            "goalCompatibilityScore": goal_score,
            "investorProfile": investor_profile,
            "goalPriority": str(investor_profile.get("objective") or investor_profile.get("goalPriority") or ""),
            "maxTemporaryLoss": max_loss,
            "portfolioMetrics": metrics,
            "optimizedMetrics": optimized_portfolio.get("metrics", {}) if isinstance(optimized_portfolio, dict) else {},
            "selectedStandardMetrics": standard_benchmark_analysis.get("metrics", {}) if isinstance(standard_benchmark_analysis, dict) else {},
            "recommendedStandardMetrics": standard_recommendation_analysis.get("metrics", {}) if isinstance(standard_recommendation_analysis, dict) else {},
        }
    )
    target_type = improvement_context.get("targetType")
    source_portfolio = improvement_context.get("sourcePortfolio")
    target_portfolio = improvement_context.get("targetPortfolio")
    if target_type == "optimized" and isinstance(target_portfolio, dict):
        target_items = optimized_portfolio_items_for_improvement(target_portfolio, user_portfolio)
    else:
        target_items = (
            standard_portfolio_items_for_improvement(target_portfolio)
            if isinstance(target_portfolio, dict) and target_portfolio.get("holdings")
            else portfolio_items_for_improvement(target_portfolio if isinstance(target_portfolio, list) else [])
        )
    source_items = (
        optimized_portfolio_items_for_improvement(source_portfolio, user_portfolio)
        if isinstance(source_portfolio, dict) and source_portfolio.get("weights")
        else (
            standard_portfolio_items_for_improvement(source_portfolio)
            if isinstance(source_portfolio, dict) and source_portfolio.get("holdings")
            else portfolio_items_for_improvement(source_portfolio if isinstance(source_portfolio, list) else user_portfolio)
        )
    )
    source_name = str(improvement_context.get("sourceName") or "")
    if comparison_mode == "final_user_vs_recommended_standard":
        source_name = "Portafoglio inserito"
    elif comparison_mode == "final_optimized_vs_recommended_standard":
        source_name = "Portafoglio ottimizzato"
    elif comparison_mode == "standard_benchmark":
        source_name = "Portafoglio inserito"
    elif comparison_mode == "standard_only":
        source_name = improvement_context.get("targetName") or "Portafoglio standard QuantInvest"
    target_name = str(improvement_context.get("targetName") or improvement_context.get("title") or "Target educativo")
    target_metrics: dict[str, Any] = improvement_context.get("targetMetrics") or {}
    source_metrics: dict[str, Any] = improvement_context.get("sourceMetrics") or metrics
    target_health_score: dict[str, Any] | None = None
    if target_type == "selected_standard":
        target_health_score = comparison_context.get("benchmarkHealthScore")

    target_exposure = improvement_asset_exposure(target_items)
    current_exposure = improvement_asset_exposure(source_items)
    target_goal_score = goal_score
    if target_type in {"selected_standard", "recommended_standard"} and target_items:
        target_goal_score = max(goal_score or 0, 70)
    elif target_type == "optimized" and target_items:
        target_goal_score = max(goal_score or 0, 65)

    problems = generate_portfolio_problems(
        userPortfolio=source_items,
        portfolioMetrics=source_metrics,
        targetPortfolio=target_items,
        targetMetrics=target_metrics,
        investorProfile=investor_profile,
        goalPriority=str(investor_profile.get("objective") or investor_profile.get("goalPriority") or ""),
        maxTemporaryLoss=max_loss,
        goalCompatibilityScore=goal_score,
        portfolioHealthScore=health_score,
    )
    actions: list[dict[str, Any]] = []
    if target_type != "standard_self_analysis" and target_items:
        actions = generate_portfolio_actions(
            currentPortfolio=source_items,
            targetPortfolio=target_items,
            portfolioMetrics=source_metrics,
            targetMetrics=target_metrics,
            investorProfile=investor_profile,
            goalPriority=str(investor_profile.get("objective") or investor_profile.get("goalPriority") or ""),
            maxTemporaryLoss=max_loss,
            correctionIntensity=str(improvement_context.get("correctionIntensity") or "light"),
        )
    action_completeness = build_improvement_action_completeness(
        source_items=source_items,
        target_items=target_items,
        actions=actions,
    ) if actions else {"available": False}
    impact = calculate_correction_impact(
        currentMetrics=source_metrics,
        targetMetrics=target_metrics,
        currentHealthScore=health_score,
        targetHealthScore=target_health_score,
        currentExposure=current_exposure,
        targetExposure=target_exposure,
        goalCompatibilityBefore=goal_score,
        goalCompatibilityAfter=target_goal_score,
    )
    simulated_etf_addition = (
        build_simulated_etf_addition(
            health_score=health_score,
            current_items=source_items,
            metrics=source_metrics,
            investor_profile=investor_profile,
            user_plan=user_plan,
            target_items=target_items,
            comparison_mode=comparison_mode,
            source_name=source_name,
            reference_name=target_name,
        )
        if target_type != "standard_self_analysis"
        else {"available": False, "reason": "In modalità solo portfolio standard la sezione spiega il modello invece di suggerire un ETF aggiuntivo."}
    )
    locked_target = None
    if isinstance(standard_recommendation, dict) and standard_recommendation.get("lockedAdvancedCandidate"):
        locked = standard_recommendation["lockedAdvancedCandidate"]
        locked_target = {
            "name": locked.get("portfolio", {}).get("name"),
            "message": locked.get("message"),
            "requiredPlan": "ADVANCED",
        }
    return {
        "available": True,
        "plan": normalize_plan(user_plan),
        "comparisonMode": comparison_mode,
        "sectionMode": improvement_context.get("sectionMode"),
        "title": improvement_context.get("title") or "Piano di miglioramento del portafoglio",
        "subtitle": improvement_context.get("subtitle") or "Trasforma l'analisi in azioni simulate per migliorare rischio, diversificazione e coerenza con il tuo profilo.",
        "sourceName": source_name,
        "activeContext": {
            "activeComparisonMode": comparison_mode,
            "activeSourcePortfolio": source_name,
            "activeTargetPortfolio": target_name,
            "activeSourceMetrics": source_metrics,
            "activeTargetMetrics": target_metrics,
        },
        "target": {
            "type": target_type,
            "name": target_name,
            "title": improvement_context.get("title"),
            "description": improvement_context.get("subtitle"),
            "reason": improvement_context.get("targetReason"),
            "correctionIntensity": improvement_context.get("correctionIntensity"),
            "lockedTarget": locked_target,
        },
        "problems": problems[:3],
        "recommendations": generate_recommendations(problems),
        "actions": actions,
        "priorityActions": actions[:3],
        "actionCompleteness": action_completeness,
        "impact": impact,
        "simulatedEtfAddition": simulated_etf_addition,
        "standardOnly": build_standard_only_improvement_explanation(selected_standard, target_items) if target_type == "standard_self_analysis" else None,
        "standardPersonalization": build_standard_only_personalization(selected_standard, investor_profile, user_plan) if target_type == "standard_self_analysis" else None,
        "disclaimer": IMPROVEMENT_DISCLAIMER,
    }


def build_standard_portfolio_analysis(
    standard_portfolio: dict[str, Any] | None,
    *,
    start: date,
    end: date,
    initial_capital: float,
    rebalance_frequency: str,
    monte_carlo_settings: dict[str, int],
    demo: bool,
    user_plan: str,
    name_prefix: str,
    stress_settings: dict[str, Any] | None = None,
    data_source: str = "demo",
    feed: str = "",
) -> dict[str, Any]:
    monte_carlo_settings = parse_monte_carlo_settings(monte_carlo_settings)
    if not standard_portfolio:
        return {"available": False, "reason": "Portfolio standard non selezionato."}
    if normalize_plan(user_plan) == "FREE":
        locked = locked_feature_payload("standardPortfolios")
        locked.update({"available": False, "reason": "Portfolio standard disponibili dal piano Plus."})
        return locked
    if normalize_plan(user_plan) == "PLUS" and standard_portfolio.get("plan") == "advanced":
        return {
            "available": False,
            "locked": True,
            "requiredPlan": "ADVANCED",
            "reason": "Questo portfolio standard è disponibile nel piano Advanced.",
            "portfolio": standard_portfolio,
        }
    items = standard_portfolio_to_backtest_items(standard_portfolio)
    if not items:
        return {"available": False, "reason": "Il portfolio standard non contiene strumenti validi.", "portfolio": standard_portfolio}
    symbols = [item["symbol"] for item in items]
    weights = {item["symbol"]: item["weight"] for item in items}
    asset_classes = {item["symbol"]: item["assetClass"] for item in items}
    try:
        price_data = make_demo_bars(symbols, start, end) if demo else marketDataService(symbols, start, end)
        common_days, _returns = align_returns(price_data)
        simulation = simulate_portfolio(common_days, price_data, symbols, weights, asset_classes, initial_capital, rebalance_frequency)
        metrics = build_metrics(simulation["equityCurve"], simulation["dailyReturns"], initial_capital)
        contribution = build_asset_contribution(
            symbols,
            price_data,
            common_days,
            weights,
            simulation["finalAssetValues"],
            simulation["equity"],
            asset_classes,
        )
        name_by_symbol = {item["symbol"]: item.get("displayName") for item in items}
        for row in contribution:
            row["displayName"] = name_by_symbol.get(row["symbol"]) or row.get("displayName") or row["symbol"]
        monte_carlo = (
            run_monte_carlo(simulation["dailyReturns"], initial_capital, monte_carlo_settings)
            if has_feature(user_plan, "monteCarlo")
            else locked_feature_payload("monteCarlo")
        )
        stress_settings = stress_settings or {"enabled": False}
        standard_stress = {
            "enabled": bool(stress_settings.get("enabled")) and has_feature(user_plan, "stressTesting"),
            "result": None,
            "monteCarlo": None,
            "error": "",
        }
        if not has_feature(user_plan, "stressTesting") or not has_feature(user_plan, "scenarioAnalysis"):
            standard_stress.update(locked_feature_payload("stressTesting"))
        elif stress_settings.get("enabled"):
            try:
                factor_payload = build_factor_stress_payload(items, common_days[0], common_days[-1], data_source, feed, stress_settings)
                standard_stress["result"] = enrich_stress_result_display_names(run_factor_stress_scenario(factor_payload))
                if stress_settings.get("runMonteCarlo"):
                    standard_stress["monteCarlo"] = run_factor_stress_monte_carlo(
                        {
                            **factor_payload,
                            "simulations": 10000,
                            "horizon_days": 252,
                            "seed": 42,
                        }
                    )
            except Exception as exc:
                standard_stress["error"] = str(exc)
        return {
            "available": True,
            "name": f"{name_prefix}: {standard_portfolio.get('name')}",
            "portfolio": standard_portfolio,
            "symbols": symbols,
            "weights": weights,
            "assetClasses": asset_classes,
            "priceData": price_data,
            "commonDays": common_days,
            "closeBySymbol": simulation["closeBySymbol"],
            "metrics": metrics,
            "equityCurve": simulation["equityCurve"],
            "dailyReturns": simulation["dailyReturns"],
            "contribution": contribution,
            "monteCarlo": monte_carlo,
            "stressTesting": standard_stress,
            "start": common_days[0],
            "end": common_days[-1],
            "tradingDays": len(common_days),
            "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
        }
    except BacktestError as exc:
        return {
            "available": False,
            "reason": f"data_unavailable: dati non disponibili per il portfolio standard {standard_portfolio.get('name')}. {exc}",
            "portfolio": standard_portfolio,
            "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
        }


def risk_budget_from_profile(profile: dict[str, Any]) -> float:
    horizon = profile["horizonYears"]
    preference = profile["riskPreference"]
    base = 0.10
    if horizon >= 10:
        base += 0.08
    elif horizon <= 3:
        base -= 0.04
    if profile["age"] >= 65:
        base -= 0.03
    if preference == "conservative":
        base -= 0.04
    if preference == "aggressive":
        base += 0.05
    return max(0.04, min(0.24, base))


def build_advisor_score(profile: dict[str, Any], metrics: dict[str, Any], frontier: dict[str, Any], stress: dict[str, Any]) -> dict[str, Any]:
    max_drawdown_abs = abs(float(metrics.get("maxDrawdown") or 0))
    sharpe = float(metrics.get("sharpe") or 0)
    cagr = float(metrics.get("cagr") or 0)
    budget = risk_budget_from_profile(profile)
    score = 55
    score += min(20, max(-10, sharpe * 12))
    score += 12 if cagr > 0 else -8
    score -= max(0, (max_drawdown_abs - budget) * 120)
    stress_result = stress.get("result") if isinstance(stress, dict) else None
    if stress_result:
        scenario_return = float(stress_result.get("expected_portfolio_return") or 0)
        if scenario_return < -budget / 2:
            score -= 10
        elif scenario_return >= 0:
            score += 5
    frontier_best = frontier.get("best") if isinstance(frontier, dict) else None
    if frontier_best and metrics.get("sharpe") is not None:
        score += 8 if float(frontier_best.get("sharpe") or 0) <= sharpe * 1.15 else -3
    return {
        "score": int(max(0, min(100, round(score)))),
        "riskBudget": budget,
        "riskLabel": "prudente" if profile["riskPreference"] == "conservative" else "aggressivo" if profile["riskPreference"] == "aggressive" else "bilanciato",
    }


def clamp_rating(value: float) -> int:
    return int(max(1, min(10, round(value))))


def build_section_comments(
    profile: dict[str, Any],
    metrics: dict[str, Any],
    frontier: dict[str, Any],
    monte_carlo: dict[str, Any],
    stress: dict[str, Any],
    contribution: list[dict[str, Any]],
    score: dict[str, Any],
) -> dict[str, str]:
    best = frontier.get("best") if isinstance(frontier, dict) else None
    current = frontier.get("current") if isinstance(frontier, dict) else None
    frontier_locked = bool(frontier.get("locked")) if isinstance(frontier, dict) else False
    monte_carlo_locked = bool(monte_carlo.get("locked")) if isinstance(monte_carlo, dict) else False
    stress_locked = bool(stress.get("locked")) if isinstance(stress, dict) else False
    frontier_gap = 0.0
    if best and current:
        frontier_gap = float(best.get("sharpe") or 0) - float(current.get("sharpe") or 0)
    worst_asset = min(contribution, key=lambda item: item.get("weightedReturn", 0), default=None)
    stress_return = None
    if stress.get("result"):
        stress_return = float(stress["result"].get("expected_portfolio_return") or 0)
    sharpe_text = "--" if metrics.get("sharpe") is None else f"{float(metrics.get('sharpe')):.2f}"
    worst_asset_name = worst_asset.get("displayName") or public_display_name_for_symbol(worst_asset.get("symbol")) if worst_asset else "--"
    return {
        "profile": (
            f"Profilo {score['riskLabel']}: orizzonte {profile['horizonYears']} anni e obiettivo '{profile['objective']}'. "
            "Usero questi dati per giudicare rischio, tempo e coerenza del portafoglio."
        ),
        "portfolio": (
            f"Portfolio normalizzato su {len(contribution)} asset. "
            f"L'asset che pesa peggio sul rendimento storico è {worst_asset_name}."
        ),
        "frontier": (
            "La frontiera indica margine di miglioramento."
            if frontier_gap > 0.15
            else "Il portafoglio inserito e abbastanza vicino alla frontiera efficiente."
        ),
        "backtest": (
            f"Backtest: crescita media annua {float(metrics.get('cagr') or 0) * 100:.2f}%, "
            f"perdita temporanea massima {abs(float(metrics.get('maxDrawdown') or 0)) * 100:.2f}% e rapporto rischio-rendimento {sharpe_text}."
        ),
        "montecarlo": (
            f"Monte Carlo: valore atteso {float(monte_carlo.get('expectedFinalValue') or 0):.0f}, "
            f"percentile 5% {float(monte_carlo.get('p5') or 0):.0f}. Guarda il percentile 5% come scenario difensivo."
        ),
        "scenarios": (
            "Scenario selezionato non disponibile."
            if stress_return is None
            else (
                f"Scenario: impatto atteso {stress_return * 100:.2f}%. "
                f"Best hedge {public_display_name_for_symbol(stress['result'].get('best_hedge'))}, "
                f"worst contributor {public_display_name_for_symbol(stress['result'].get('worst_contributor'))}."
            )
        ),
    }


def build_final_analysis(profile: dict[str, Any], metrics: dict[str, Any], frontier: dict[str, Any], stress: dict[str, Any]) -> dict[str, Any]:
    budget = risk_budget_from_profile(profile)
    drawdown = abs(float(metrics.get("maxDrawdown") or 0))
    sharpe = float(metrics.get("sharpe") or 0)
    cagr = float(metrics.get("cagr") or 0)
    risk_rating = clamp_rating(10 - max(0, drawdown - budget) * 35 + max(0, sharpe) * 1.5)
    horizon_rating = clamp_rating(5 + profile["horizonYears"] / 4 + (cagr * 20) - drawdown * 6)
    objective_text = profile["objective"].lower()
    objective_bonus = 1.5 if any(word in objective_text for word in ["crescita", "lungo", "capitale"]) and cagr > 0 else 0
    if any(word in objective_text for word in ["casa", "sicurezza", "protezione"]) and drawdown < budget:
        objective_bonus += 1
    objective_rating = clamp_rating(5 + objective_bonus + cagr * 25 - max(0, drawdown - budget) * 25)
    overall = round((risk_rating + horizon_rating + objective_rating) / 3, 1)
    return {
        "riskFit": risk_rating,
        "horizonFit": horizon_rating,
        "objectiveFit": objective_rating,
        "overall": overall,
        "text": "",
    }


def score_component(value: float) -> int:
    return int(max(0, min(100, round(value))))


def score_band_label(value: float) -> str:
    if value >= 95:
        return "Eccellente"
    if value >= 85:
        return "Molto buono"
    if value >= 75:
        return "Buono"
    if value >= 60:
        return "Discreto"
    if value >= 40:
        return "Debole"
    return "Critico"


def score_band_description(value: float) -> str:
    if value >= 95:
        return "Il portafoglio appare molto coerente con le metriche disponibili, pur restando esposto ai normali rischi di mercato."
    if value >= 85:
        return "Il portafoglio mostra una struttura solida, con poche aree evidenti da monitorare."
    if value >= 75:
        return "Il portafoglio è complessivamente buono, ma alcuni fattori meritano controllo periodico."
    if value >= 60:
        return "Il portafoglio è utilizzabile, ma presenta aree di miglioramento evidenti."
    if value >= 40:
        return "Il portafoglio mostra fragilità rilevanti rispetto al profilo o ai dati disponibili."
    return "Il portafoglio richiede attenzione: più indicatori segnalano incoerenze o rischi elevati."


def pillar_status(value: float) -> str:
    if value >= 75:
        return "buono"
    if value >= 55:
        return "da monitorare"
    return "critico"


def first_number(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def score_average(items: list[tuple[str, float | None]], missing: list[str], used: list[str]) -> float | None:
    valid: list[float] = []
    for name, value in items:
        if value is None:
            missing.append(name)
        else:
            valid.append(score_component(value))
            used.append(name)
    if not valid:
        return None
    return sum(valid) / len(valid)


def risk_budget_for_profile(profile: dict[str, Any]) -> float:
    preference = str(profile.get("riskPreference") or profile.get("riskProfile") or "balanced").lower()
    return {"conservative": 0.16, "balanced": 0.28, "aggressive": 0.45}.get(preference, 0.28)


def equity_exposure_from_contribution(contribution: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in contribution:
        asset_class = str(item.get("assetClass") or "").lower()
        if any(token in asset_class for token in ("equity", "azioni", "azionario", "stock")):
            total += abs(float(item.get("weight") or 0))
    return total


def concentration_score(max_weight: float, asset_count: int) -> int:
    return score_component(102 - max_weight * 82 + min(asset_count, 10) * 3)


def drawdown_fit_score(max_drawdown: float | None, profile: dict[str, Any]) -> float | None:
    if max_drawdown is None:
        return None
    budget = risk_budget_for_profile(profile)
    drawdown_abs = abs(max_drawdown)
    return 100 - max(0, drawdown_abs - budget) * 180 - drawdown_abs * 75


def volatility_score(volatility: float | None, profile: dict[str, Any]) -> float | None:
    if volatility is None:
        return None
    preference = str(profile.get("riskPreference") or "balanced").lower()
    target = {"conservative": 0.10, "balanced": 0.17, "aggressive": 0.26}.get(preference, 0.17)
    return 100 - max(0, volatility - target) * 220 - max(0, volatility) * 35


def sharpe_score(sharpe: float | None) -> float | None:
    if sharpe is None:
        return None
    return 42 + sharpe * 32


def return_score(cagr: float | None) -> float | None:
    if cagr is None:
        return None
    return 45 + cagr * 380


def monte_carlo_success_score(monte_carlo: dict[str, Any], initial_capital: float) -> float | None:
    if not isinstance(monte_carlo, dict) or monte_carlo.get("locked"):
        return None
    probability = first_number(monte_carlo.get("probabilityGain"), monte_carlo.get("successProbability"))
    p5 = first_number(monte_carlo.get("p5"))
    if probability is None:
        return None
    score = probability * 80
    if p5 is not None and initial_capital > 0:
        p5_return = p5 / initial_capital - 1
        score = score * 0.65 + score_component(65 + p5_return * 180) * 0.35
    return score


def stress_score(stress: dict[str, Any]) -> float | None:
    if not isinstance(stress, dict) or stress.get("locked"):
        return None
    result = stress.get("result") if isinstance(stress.get("result"), dict) else None
    if not result:
        return None
    scenario_return = first_number(result.get("expected_portfolio_return"))
    if scenario_return is None:
        return None
    return 72 + scenario_return * 150


def stress_return_value(stress: dict[str, Any]) -> float | None:
    if not isinstance(stress, dict) or stress.get("locked"):
        return None
    result = stress.get("result") if isinstance(stress.get("result"), dict) else None
    if not result:
        return None
    return first_number(result.get("expected_portfolio_return"))


def stress_metric_label(stress: dict[str, Any]) -> str:
    scenario_return = stress_return_value(stress)
    result = stress.get("result") if isinstance(stress, dict) and isinstance(stress.get("result"), dict) else {}
    scenario = result.get("scenario") or stress.get("settings", {}).get("scenario") if isinstance(stress, dict) else None
    if scenario_return is None:
        return "Crisi simulate non disponibili"
    scenario_text = f"{scenario}: " if scenario else ""
    return f"Crisi simulate {scenario_text}{scenario_return * 100:.1f}%"


def locked_score_insights(plan: str) -> list[dict[str, str]]:
    normalized = normalize_plan(plan)
    if normalized == "FREE":
        locked = ["efficientFrontier", "monteCarlo", "scenarioAnalysis", "stressTesting", "portfolioTracking", "sortinoRatio", "correlationMatrix"]
    elif normalized == "PLUS":
        locked = ["sortinoRatio", "famaFrench", "correlationMatrix", "geographyExposure", "rollingReturns", "pacAnalysis", "peakToTrough", "factorShockAnalysis"]
    else:
        locked = []
    return [
        {
            "feature": feature,
            "title": FEATURE_MESSAGES.get(feature, {}).get("name", feature),
            "requiredPlan": FEATURE_MESSAGES.get(feature, {}).get("requiredPlan", "PLUS"),
            "message": FEATURE_MESSAGES.get(feature, {}).get("previewMessage", "Analisi disponibile con un piano superiore."),
        }
        for feature in locked
    ]


def score_name_for_plan(plan: str) -> str:
    normalized = normalize_plan(plan)
    if normalized == "FREE":
        return "Check-up Base del portafoglio"
    if normalized == "ADVANCED":
        return "Check-up quantitativo avanzato"
    return "Portfolio Fit Score"


def calculate_portfolio_fit_score(
    *,
    plan: str,
    userProfile: dict[str, Any],
    portfolioMetrics: dict[str, Any],
    optimizedPortfolioMetrics: dict[str, Any] | None = None,
    availableFeatures: dict[str, Any] | None = None,
    dataQuality: dict[str, Any] | None = None,
    frontier: dict[str, Any] | None = None,
    monteCarlo: dict[str, Any] | None = None,
    stressTesting: dict[str, Any] | None = None,
    contribution: list[dict[str, Any]] | None = None,
    advancedAnalytics: dict[str, Any] | None = None,
    tracking: dict[str, Any] | None = None,
    portfolioContext: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_plan = normalize_plan(plan)
    availableFeatures = availableFeatures or PLAN_FEATURES.get(normalized_plan, {})
    dataQuality = dataQuality or {}
    frontier = frontier or {}
    monteCarlo = monteCarlo or {}
    stressTesting = stressTesting or {}
    contribution = contribution or []
    advancedAnalytics = advancedAnalytics or {}
    tracking = tracking or {}
    optimizedPortfolioMetrics = optimizedPortfolioMetrics or {}

    missing: list[str] = []
    used: list[str] = []
    excluded = [card["title"] for card in locked_score_insights(normalized_plan)]
    warnings: list[str] = []
    caps: list[dict[str, Any]] = []
    penalties: list[dict[str, Any]] = []

    initial_capital = float(portfolioMetrics.get("initialCapital") or userProfile.get("capital") or 0)
    max_drawdown = first_number(portfolioMetrics.get("maxDrawdown"))
    volatility = first_number(portfolioMetrics.get("volatility"))
    cagr = first_number(portfolioMetrics.get("cagr"), portfolioMetrics.get("expectedReturn"))
    sharpe = first_number(portfolioMetrics.get("sharpe"))
    weights = [abs(float(item.get("weight") or 0)) for item in contribution]
    max_weight = max(weights) if weights else 0
    equity_exposure = equity_exposure_from_contribution(contribution)
    risk_profile = str(userProfile.get("riskPreference") or userProfile.get("riskProfile") or "balanced").lower()
    horizon_years = int(float(userProfile.get("horizonYears") or 0))
    goal_alignment_fallback = calculate_goal_alignment_fallback(
        contribution,
        userProfile,
        portfolioMetrics,
        portfolioContext,
    )

    def goal_adjusted_score(score: float | None) -> float | None:
        if score is None:
            return None
        adjusted = float(score) + float(goal_alignment_fallback.get("goalAlignmentAdjustment") or 0)
        hard_cap = goal_alignment_fallback.get("hardCap")
        if hard_cap is not None:
            adjusted = min(adjusted, float(hard_cap))
        return score_component(adjusted)

    def goal_interpretation(base_text: str) -> str:
        reasons = goal_alignment_fallback.get("reasons") or []
        matched = goal_alignment_fallback.get("matchedGoalDrivers") or []
        missing_drivers = goal_alignment_fallback.get("missingGoalDrivers") or []
        additions: list[str] = []
        if reasons:
            additions.append(str(reasons[0]))
        elif matched:
            additions.append(f"Coerente con: {', '.join(str(item) for item in matched[:2])}.")
        if missing_drivers:
            additions.append(f"Da verificare: {', '.join(str(item) for item in missing_drivers[:2])}.")
        return " ".join([base_text, *additions]).strip()

    def add_pillar(key: str, name: str, question: str, weight: float, score: float | None, metric: str, interpretation: str) -> None:
        if score is None:
            missing.append(name)
            return
        normalized_score = score_component(score)
        pillars.append(
            {
                "key": key,
                "name": name,
                "question": question,
                "weight": weight,
                "score": normalized_score,
                "maxPoints": round(weight * 100, 1),
                "impactPoints": round(normalized_score * weight, 1),
                "interpretation": interpretation,
                "primaryMetric": metric,
                "status": pillar_status(score),
            }
        )

    pillars: list[dict[str, Any]] = []
    if normalized_plan == "FREE":
        add_pillar(
            "baseRisk",
            "Rischio reale base",
            "Quanto potresti perdere nei momenti difficili?",
            0.35,
            score_average(
                [
                    ("volatilità", volatility_score(volatility, userProfile)),
                    ("peggiore perdita storica", drawdown_fit_score(max_drawdown, userProfile)),
                ],
                missing,
                used,
            ),
            f"Perdita storica {max_drawdown * 100:.1f}%" if max_drawdown is not None else "Perdita storica non disponibile",
            "Valuta oscillazione e perdita storica rispetto al profilo dichiarato.",
        )
        add_pillar(
            "baseEfficiency",
            "Efficienza base",
            "Il rendimento compensa il rischio?",
            0.30,
            score_average(
                [
                    ("rapporto rischio-rendimento", sharpe_score(sharpe)),
                    ("crescita media annua", return_score(cagr)),
                ],
                missing,
                used,
            ),
            f"Rapporto rischio-rendimento {sharpe:.2f}" if sharpe is not None else "Rapporto rischio-rendimento non disponibile",
            "Legge rendimento e oscillazione senza usare ottimizzazione o simulazioni.",
        )
        add_pillar(
            "baseDiversification",
            "Diversificazione base",
            "Il portafoglio dipende troppo da pochi strumenti?",
            0.20,
            concentration_score(max_weight, len(weights)) if weights else None,
            f"Peso massimo {max_weight * 100:.1f}%",
            "Usa numero di strumenti e peso massimo per stimare la concentrazione semplice.",
        )
        add_pillar(
            "baseCoherence",
            "Coerenza base",
            "Il portafoglio è coerente con profilo e orizzonte?",
            0.15,
            goal_adjusted_score(
                score_average(
                    [
                        ("coerenza drawdown-profilo", drawdown_fit_score(max_drawdown, userProfile)),
                        ("orizzonte temporale", 82 if horizon_years >= 10 else 68 if horizon_years >= 5 else 52),
                    ],
                    missing,
                    used,
                )
            ),
            f"Profilo {risk_profile}, orizzonte {horizon_years} anni",
            goal_interpretation("Incrocia rischio dichiarato, orizzonte e obiettivo senza usare analisi Plus."),
        )
    elif normalized_plan == "PLUS":
        current = frontier.get("current") if isinstance(frontier.get("current"), dict) else {}
        best = frontier.get("best") if isinstance(frontier.get("best"), dict) else {}
        optimized_sharpe = first_number(best.get("sharpe"), optimizedPortfolioMetrics.get("sharpe"))
        efficiency_delta = optimized_sharpe - sharpe if optimized_sharpe is not None and sharpe is not None else None
        add_pillar(
            "realRisk",
            "Rischio reale",
            "Quanto potresti perdere nei momenti difficili?",
            0.25,
            score_average(
                [
                    ("volatilità", volatility_score(volatility, userProfile)),
                    ("peggiore perdita storica", drawdown_fit_score(max_drawdown, userProfile)),
                    ("crisi simulate", stress_score(stressTesting)),
                ],
                missing,
                used,
            ),
            stress_metric_label(stressTesting) if stress_return_value(stressTesting) is not None else f"Perdita storica {max_drawdown * 100:.1f}%" if max_drawdown is not None else "Perdita storica non disponibile",
            "Combina oscillazione, perdita storica e impatto delle crisi simulate disponibili.",
        )
        add_pillar(
            "riskReturnEfficiency",
            "Efficienza rischio-rendimento",
            "Il rendimento compensa il rischio?",
            0.25,
            score_average(
                [
                    ("rapporto rischio-rendimento", sharpe_score(sharpe)),
                    ("frontiera efficiente", 65 + (efficiency_delta or 0) * 120 if best and current else None),
                    ("crescita media annua", return_score(cagr)),
                ],
                missing,
                used,
            ),
            f"Delta ottimizzazione {efficiency_delta:.2f}" if efficiency_delta is not None else "Frontiera non disponibile",
            "Valuta se il portafoglio usa bene il rischio rispetto alla combinazione ottimizzata.",
        )
        add_pillar(
            "goalCoherence",
            "Coerenza con obiettivo",
            "Il portafoglio è coerente con il tuo obiettivo?",
            0.20,
            goal_adjusted_score(
                score_average(
                    [
                        ("Monte Carlo", monte_carlo_success_score(monteCarlo, initial_capital)),
                        ("coerenza drawdown-profilo", drawdown_fit_score(max_drawdown, userProfile)),
                    ],
                    missing,
                    used,
                )
            ),
            f"Probabilità profitto {float(monteCarlo.get('probabilityGain') or 0) * 100:.1f}%" if isinstance(monteCarlo, dict) and not monteCarlo.get("locked") else "Monte Carlo non disponibile",
            goal_interpretation("Usa simulazioni e profilo per stimare quanto il percorso sia compatibile con l’obiettivo."),
        )
        add_pillar(
            "scenarioRobustness",
            "Robustezza agli scenari",
            "Cosa succede se il mercato va male?",
            0.15,
            stress_score(stressTesting),
            stress_metric_label(stressTesting),
            "Misura la resistenza alle crisi simulate disponibili nel piano Plus.",
        )
        add_pillar(
            "diversification",
            "Diversificazione",
            "Il portafoglio dipende troppo da pochi strumenti?",
            0.15,
            concentration_score(max_weight, len(weights)) if weights else None,
            f"Peso massimo {max_weight * 100:.1f}%",
            "Misura concentrazione per strumenti e asset class disponibili, senza usare analisi Advanced.",
        )
    else:
        sortino = first_number(portfolioMetrics.get("sortino"), advancedAnalytics.get("sortino"))
        correlation = advancedAnalytics.get("correlation") if isinstance(advancedAnalytics.get("correlation"), dict) else {}
        drawdown_analysis = advancedAnalytics.get("drawdown") if isinstance(advancedAnalytics.get("drawdown"), dict) else {}
        rolling = advancedAnalytics.get("rollingSortino") if isinstance(advancedAnalytics.get("rollingSortino"), dict) else {}
        current_rolling = rolling.get("current") if isinstance(rolling.get("current"), dict) else {}
        average_rolling_sortino = first_number(current_rolling.get("averageSortino"), rolling.get("averageSortino"))
        add_pillar(
            "advancedRisk",
            "Rischio reale avanzato",
            "Quanto è profonda e lunga una fase negativa?",
            0.20,
            score_average(
                [
                    ("peggiore perdita storica", drawdown_fit_score(max_drawdown, userProfile)),
                    ("rapporto rendimento-rischio negativo", sharpe_score(sortino)),
                    ("tempo di recupero", 90 - float(drawdown_analysis.get("recoveryDays") or 0) / 8 if drawdown_analysis else None),
                ],
                missing,
                used,
            ),
            f"Rapporto rendimento-rischio negativo {sortino:.2f}" if sortino is not None else "Rischio negativo non disponibile",
            "Legge perdita, rischio negativo e durata delle fasi difficili.",
        )
        add_pillar(
            "advancedEfficiency",
            "Efficienza avanzata",
            "Il rendimento è stabile rispetto al rischio?",
            0.20,
            score_average(
                [
                    ("rapporto rischio-rendimento", sharpe_score(sharpe)),
                    ("rapporto rendimento-rischio negativo", sharpe_score(sortino)),
                    ("rolling rischio-rendimento negativo", sharpe_score(average_rolling_sortino)),
                    ("crescita media annua", return_score(cagr)),
                ],
                missing,
                used,
            ),
            "Metriche rolling e rischio negativo",
            "Aggiunge stabilità nel tempo e rischio negativo all’efficienza base.",
        )
        avg_corr = first_number(correlation.get("averageCorrelation"))
        add_pillar(
            "realDiversification",
            "Diversificazione reale avanzata",
            "Sei davvero diversificato?",
            0.20,
            score_average(
                [
                    ("matrice di correlazione", 100 - (avg_corr or 0.65) * 75 if avg_corr is not None else None),
                    ("concentrazione pesi", concentration_score(max_weight, len(weights))),
                    ("esposizione geografica", 76 if advancedAnalytics.get("geography") else None),
                ],
                missing,
                used,
            ),
            f"Correlazione media {avg_corr:.2f}" if avg_corr is not None else f"Peso massimo {max_weight * 100:.1f}%",
            "Valuta se strumenti e aree si muovono insieme o offrono diversificazione reale.",
        )
        add_pillar(
            "advancedGoalCoherence",
            "Coerenza con obiettivi/PAC",
            "Il piano resta coerente nel tempo?",
            0.20,
            goal_adjusted_score(
                score_average(
                    [
                        ("Monte Carlo", monte_carlo_success_score(monteCarlo, initial_capital)),
                        ("orizzonte temporale", 86 if horizon_years >= 10 else 70 if horizon_years >= 5 else 55),
                        ("PAC avanzato", 74 if advancedAnalytics.get("pac") else None),
                    ],
                    missing,
                    used,
                )
            ),
            f"Orizzonte {horizon_years} anni",
            goal_interpretation("Integra obiettivo, simulazioni e piano di accumulo quando disponibile."),
        )
        add_pillar(
            "factorScenarioRobustness",
            "Robustezza fattoriale e scenari",
            "Da quali fattori dipende il portafoglio?",
            0.20,
            score_average(
                [
                    ("crisi simulate", stress_score(stressTesting)),
                    ("factor shock analysis", 72 if advancedAnalytics.get("factorRisk") else None),
                    ("Fama-French", 72 if advancedAnalytics.get("famaFrench") else None),
                ],
                missing,
                used,
            ),
            "Fattori e scenari avanzati",
            "Misura la vulnerabilità a shock e fattori di mercato comuni.",
        )

    available_pillars = [pillar for pillar in pillars if pillar.get("score") is not None]
    if goal_alignment_fallback.get("goalAlignmentAdjustment"):
        used.append("coerenza obiettivo locale")
        adjustment_value = float(goal_alignment_fallback.get("goalAlignmentAdjustment") or 0)
        if adjustment_value < 0:
            penalties.append(
                {
                    "reason": "Fallback locale obiettivo: composizione poco coerente con l'obiettivo dichiarato.",
                    "impact": round(adjustment_value, 1),
                }
            )
        elif adjustment_value > 0:
            warnings.append("La coerenza con obiettivi riceve un supporto locale perché la composizione è allineata all'obiettivo dichiarato.")
    if goal_alignment_fallback.get("hardCap") is not None:
        caps.append(
            {
                "reason": "Cap locale sulla coerenza con obiettivi per incoerenza evidente con l'obiettivo dichiarato.",
                "cap": goal_alignment_fallback.get("hardCap"),
            }
        )
    for goal_warning in goal_alignment_fallback.get("warnings", []) or []:
        warnings.append(str(goal_warning))
    total_weight = sum(float(pillar.get("weight") or 0) for pillar in available_pillars)
    if total_weight:
        overall = sum(float(pillar["score"]) * float(pillar["weight"]) for pillar in available_pillars) / total_weight
    else:
        overall = 0.0

    if normalized_plan == "FREE" and overall > 85:
        caps.append({"reason": "Il piano Free produce una diagnosi preliminare: cap massimo 85/100.", "cap": 85})
        overall = min(overall, 85)
    if risk_profile == "balanced" and max_weight > 0.9:
        caps.append({"reason": "Profilo bilanciato con peso massimo oltre il 90%.", "cap": 70})
        overall = min(overall, 70)
    elif risk_profile == "balanced" and max_weight > 0.8:
        caps.append({"reason": "Profilo bilanciato con peso massimo oltre l'80%.", "cap": 75})
        overall = min(overall, 75)
    if risk_profile == "conservative" and max_weight > 0.7:
        caps.append({"reason": "Profilo prudente con concentrazione elevata.", "cap": 70})
        overall = min(overall, 70)

    if risk_profile == "aggressive" and max_weight > 0.9:
        warnings.append("Il portafoglio è molto concentrato: per un profilo aggressivo non applico un cap severo, ma il compromesso va monitorato.")
    if risk_profile == "conservative" and max_drawdown is not None and max_drawdown < -0.20:
        warnings.append("La perdita storica supera la soglia tipica di un profilo prudente.")
    if risk_profile == "balanced" and max_drawdown is not None and max_drawdown < -0.30:
        warnings.append("La perdita storica è elevata per un profilo bilanciato.")
    if risk_profile == "aggressive" and max_drawdown is not None and max_drawdown < -0.45:
        warnings.append("La perdita storica è molto profonda anche per un profilo aggressivo.")
    if horizon_years and horizon_years < 5 and equity_exposure > 0.70:
        warnings.append("Orizzonte sotto 5 anni con esposizione azionaria elevata.")
    scenario_return_for_score = stress_return_value(stressTesting)
    if normalized_plan in {"PLUS", "ADVANCED"} and scenario_return_for_score is not None:
        if risk_profile == "conservative" and scenario_return_for_score < -0.12:
            warnings.append("La crisi simulata genera una perdita elevata per un profilo prudente.")
        elif risk_profile == "balanced" and scenario_return_for_score < -0.20:
            warnings.append("La crisi simulata pesa in modo significativo per un profilo bilanciato.")
        elif risk_profile == "aggressive" and scenario_return_for_score < -0.32:
            warnings.append("La crisi simulata mostra una vulnerabilità rilevante anche per un profilo aggressivo.")

    tradeoff = None
    if optimizedPortfolioMetrics:
        optimized_max_weight = max((abs(float(item.get("weight") or 0)) for item in optimizedPortfolioMetrics.get("weights", []) if isinstance(item, dict)), default=0)
        current_div = next((p["score"] for p in available_pillars if p["key"] in {"baseDiversification", "diversification", "realDiversification"}), concentration_score(max_weight, len(weights)))
        optimized_div = concentration_score(optimized_max_weight, len(weights)) if optimized_max_weight else current_div
        current_sharpe = sharpe or 0
        optimized_sharpe_value = first_number(optimizedPortfolioMetrics.get("sharpe"))
        if optimized_sharpe_value is not None and optimized_sharpe_value > current_sharpe and optimized_div <= current_div - 20:
            tradeoff = "L’ottimizzazione migliora il punteggio complessivo, ma riduce la diversificazione. Il portafoglio efficiente non è automaticamente migliore: è più efficiente secondo il modello, ma più concentrato."
        if risk_profile == "balanced" and optimized_max_weight > 0.80:
            warnings.append("Il portafoglio ottimizzato concentra molto il peso su pochi strumenti: per un profilo bilanciato questo compromesso va valutato con attenzione.")

    if dataQuality.get("demo") or dataQuality.get("shortHistory") or dataQuality.get("dataUnavailable"):
        missing.append("qualità dati completa")
    missing_unique = list(dict.fromkeys(missing))
    used_unique = list(dict.fromkeys(used))
    excluded_unique = list(dict.fromkeys(excluded))
    weakest_pillar = min(available_pillars, key=lambda pillar: float(pillar.get("score") or 0), default=None)
    strongest_pillar = max(available_pillars, key=lambda pillar: float(pillar.get("score") or 0), default=None)
    score_reducing_factors: list[dict[str, Any]] = []
    for pillar in available_pillars:
        pillar_score = float(pillar.get("score") or 0)
        if pillar_score < 65:
            score_reducing_factors.append(
                {
                    "label": f"{pillar.get('name')}: {round(pillar_score)}/100",
                    "impact": f"incide per {pillar.get('impactPoints')}/{pillar.get('maxPoints')} punti",
                }
            )
    for cap in caps:
        score_reducing_factors.append({"label": cap.get("reason", "Cap applicato allo score"), "impact": f"cap {cap.get('cap')}/100"})
    for warning in list(dict.fromkeys(warnings))[:3]:
        score_reducing_factors.append({"label": warning, "impact": "fattore da monitorare"})
    if missing_unique:
        score_reducing_factors.append(
            {
                "label": "Alcune metriche disponibili nel piano non sono ancora presenti.",
                "impact": "non penalizzano lo score, ma riducono l'affidabilità",
            }
        )
    if normalized_plan == "FREE":
        confidence = "Bassa" if missing_unique or dataQuality.get("demo") else "Base"
        confidence_reason = "Lo score usa solo metriche incluse nel piano Free e non include scenari, simulazioni o ottimizzazione."
    elif normalized_plan == "PLUS":
        confidence = "Bassa" if dataQuality.get("demo") else "Media" if missing_unique else "Buona"
        confidence_reason = "Lo score usa metriche Plus; eventuali sezioni mancanti vengono escluse e non penalizzano il punteggio."
    else:
        confidence = "Bassa" if dataQuality.get("demo") else "Media" if missing_unique else "Alta"
        confidence_reason = "Lo score usa anche metriche avanzate quando disponibili; dati proxy o incompleti riducono l’affidabilità."

    score = score_component(overall)
    diagnosis = f"{score_name_for_plan(normalized_plan)}: {score}/100. {score_band_label(score)} in base alle metriche disponibili nel piano {normalized_plan}."
    if warnings:
        diagnosis += f" Punto da monitorare: {warnings[0]}"
    elif normalized_plan == "FREE":
        diagnosis += " La diagnosi è preliminare perché non include simulazioni, scenari o ottimizzazione."

    details = {pillar["key"]: pillar["interpretation"] for pillar in available_pillars}
    components = {pillar["key"]: pillar["score"] for pillar in available_pillars}
    if weakest_pillar:
        why_not_higher = f"Il limite principale è {weakest_pillar.get('name')}, con {round(float(weakest_pillar.get('score') or 0))}/100."
    elif missing_unique:
        why_not_higher = "Non ci sono abbastanza metriche disponibili per spiegare pienamente lo score."
    else:
        why_not_higher = "Non emergono penalizzazioni rilevanti dalle metriche disponibili."
    if strongest_pillar:
        what_works = f"Il punto più solido è {strongest_pillar.get('name')}, con {round(float(strongest_pillar.get('score') or 0))}/100."
    else:
        what_works = "Il sistema non ha ancora abbastanza dati per individuare il punto di forza principale."
    what_penalizes = score_reducing_factors[0]["label"] if score_reducing_factors else "Nessuna penalizzazione esplicita applicata."
    monitor_items = []
    if max_drawdown is not None:
        monitor_items.append("peggiore perdita storica")
    if max_weight > 0:
        monitor_items.append("concentrazione del peso principale")
    if normalized_plan in {"PLUS", "ADVANCED"}:
        monitor_items.append("crisi simulate")
    what_to_monitor = ", ".join(monitor_items[:3]) if monitor_items else "completezza dei dati e coerenza con il profilo."
    formula_labels = {pillar["name"]: round(float(pillar.get("weight") or 0) * 100, 1) for pillar in available_pillars}
    return {
        "scoreType": score_name_for_plan(normalized_plan),
        "name": score_name_for_plan(normalized_plan),
        "plan": normalized_plan,
        "overall": score,
        "label": score_band_label(score),
        "labelDescription": score_band_description(score),
        "confidence": confidence,
        "confidenceReason": confidence_reason,
        "diagnosis": diagnosis,
        "whyNotHigher": why_not_higher,
        "whatWorks": what_works,
        "whatPenalizes": what_penalizes,
        "whatToMonitor": what_to_monitor,
        "scoreReducingFactors": score_reducing_factors,
        "mainTradeoff": tradeoff or "Il punteggio va letto come diagnosi educativa basata sulle metriche disponibili, non come indicazione operativa.",
        "warnings": list(dict.fromkeys(warnings)),
        "pillars": available_pillars,
        "components": components,
        "details": details,
        "metricsUsed": used_unique,
        "metricsExcludedByPlan": excluded_unique,
        "metricsMissing": missing_unique,
        "lockedInsights": locked_score_insights(normalized_plan),
        "capsApplied": caps,
        "penaltiesApplied": penalties,
        "scoreFormula": formula_labels,
        "pillarWeights": {pillar["key"]: pillar["weight"] for pillar in available_pillars},
        "technicalDisclaimer": AI_EXPLANATION_DISCLAIMER,
        "maxAssetWeight": max_weight,
        "equityExposure": equity_exposure,
        "goalAlignmentFallback": goal_alignment_fallback,
    }


def calculatePortfolioFitScore(payload: dict[str, Any]) -> dict[str, Any]:
    return calculate_portfolio_fit_score(
        plan=payload.get("plan", "PLUS"),
        userProfile=payload.get("userProfile", {}),
        portfolioMetrics=payload.get("portfolioMetrics", {}),
        optimizedPortfolioMetrics=payload.get("optimizedPortfolioMetrics"),
        availableFeatures=payload.get("availableFeatures"),
        dataQuality=payload.get("dataQuality"),
        frontier=payload.get("frontier"),
        monteCarlo=payload.get("monteCarlo"),
        stressTesting=payload.get("stressTesting"),
        contribution=payload.get("contribution"),
        advancedAnalytics=payload.get("advancedAnalytics"),
        tracking=payload.get("tracking"),
        portfolioContext=payload.get("portfolioContext"),
    )


def build_portfolio_health_score(
    profile: dict[str, Any],
    metrics: dict[str, Any],
    frontier: dict[str, Any],
    monte_carlo: dict[str, Any],
    stress: dict[str, Any],
    contribution: list[dict[str, Any]],
    user_plan: str = "PLUS",
    optimized_metrics: dict[str, Any] | None = None,
    advanced_analytics: dict[str, Any] | None = None,
    tracking: dict[str, Any] | None = None,
    portfolio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return calculate_portfolio_fit_score(
        plan=user_plan,
        userProfile=profile,
        portfolioMetrics=metrics,
        optimizedPortfolioMetrics=optimized_metrics,
        availableFeatures=PLAN_FEATURES.get(normalize_plan(user_plan), {}),
        dataQuality={"demo": bool(metrics.get("demo"))},
        frontier=frontier,
        monteCarlo=monte_carlo,
        stressTesting=stress,
        contribution=contribution,
        advancedAnalytics=advanced_analytics,
        tracking=tracking,
        portfolioContext=portfolio_context,
    )


def qualitative_sharpe(sharpe: float | None) -> str:
    if sharpe is None:
        return "Il rapporto rendimento/rischio non e calcolabile con stabilita."
    if sharpe < 0.3:
        return "Il rendimento ottenuto e debole rispetto al rischio assunto."
    if sharpe < 0.8:
        return "Il rendimento ottenuto e moderato rispetto al rischio assunto."
    return "Il rendimento storico e stato buono rispetto all'oscillazione sostenuta."


def signed_points(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:+.{digits}f}"


def dominant_backtest_risk_comparison(metrics: dict[str, Any], optimized_metrics: dict[str, Any] | None) -> str:
    if not optimized_metrics:
        drawdown = first_number(metrics.get("maxDrawdown"))
        if drawdown is not None:
            return f"Il rischio principale resta la peggiore perdita temporanea storica: {drawdown * 100:.2f}% nel periodo analizzato."
        sharpe = first_number(metrics.get("sharpe"))
        if sharpe is not None:
            return f"Il rischio principale è capire se il rendimento compensa il rischio: il rapporto rischio-rendimento è {sharpe:.2f}."
        return "Il rischio principale va letto confrontando risultato storico, oscillazione e perdita temporanea."

    current = {
        "finalValue": first_number(metrics.get("finalValue")),
        "totalReturn": first_number(metrics.get("totalReturn")),
        "cagr": first_number(metrics.get("cagr")),
        "volatility": first_number(metrics.get("volatility")),
        "sharpe": first_number(metrics.get("sharpe")),
        "maxDrawdown": first_number(metrics.get("maxDrawdown")),
    }
    optimized = {
        "finalValue": first_number(optimized_metrics.get("finalValue")),
        "totalReturn": first_number(optimized_metrics.get("totalReturn")),
        "cagr": first_number(optimized_metrics.get("cagr")),
        "volatility": first_number(optimized_metrics.get("volatility")),
        "sharpe": first_number(optimized_metrics.get("sharpe")),
        "maxDrawdown": first_number(optimized_metrics.get("maxDrawdown")),
    }

    candidates: list[tuple[float, str]] = []
    if current["sharpe"] is not None and optimized["sharpe"] is not None:
        candidates.append((abs(optimized["sharpe"] - current["sharpe"]) / 0.35, "sharpe"))
    if current["maxDrawdown"] is not None and optimized["maxDrawdown"] is not None:
        candidates.append((abs(abs(optimized["maxDrawdown"]) - abs(current["maxDrawdown"])) / 0.06, "maxDrawdown"))
    if current["volatility"] is not None and optimized["volatility"] is not None:
        candidates.append((abs(optimized["volatility"] - current["volatility"]) / 0.05, "volatility"))
    if current["cagr"] is not None and optimized["cagr"] is not None:
        candidates.append((abs(optimized["cagr"] - current["cagr"]) / 0.04, "cagr"))
    if current["totalReturn"] is not None and optimized["totalReturn"] is not None:
        candidates.append((abs(optimized["totalReturn"] - current["totalReturn"]) / 0.15, "totalReturn"))
    if current["finalValue"] is not None and optimized["finalValue"] is not None and current["finalValue"]:
        candidates.append((abs(optimized["finalValue"] - current["finalValue"]) / max(abs(current["finalValue"]) * 0.10, 1), "finalValue"))
    if not candidates:
        return "Il rischio principale va letto confrontando risultato storico, oscillazione e perdita temporanea."

    _, key = max(candidates, key=lambda item: item[0])
    if key == "sharpe":
        current_value = current["sharpe"] or 0
        optimized_value = optimized["sharpe"] or 0
        delta = optimized_value - current_value
        return (
            "La differenza più importante è il rapporto rischio-rendimento: "
            f"nel portafoglio inserito il rendimento ha compensato il rischio con {current_value:.2f} euro per unità di rischio, "
            f"mentre nel portafoglio ottimale il valore è {optimized_value:.2f}. "
            f"Differenza: {signed_points(delta)} punti."
        )
    if key == "maxDrawdown":
        current_loss = abs(current["maxDrawdown"] or 0)
        optimized_loss = abs(optimized["maxDrawdown"] or 0)
        delta = optimized_loss - current_loss
        return (
            "La differenza più importante è la peggiore perdita temporanea: "
            f"il portafoglio inserito sarebbe sceso fino a {current_loss * 100:.2f}%, "
            f"mentre il portafoglio ottimale fino a {optimized_loss * 100:.2f}%. "
            f"Cambiamento: {signed_points(delta * 100)} punti."
        )
    if key == "volatility":
        delta = (optimized["volatility"] or 0) - (current["volatility"] or 0)
        return (
            "La differenza più importante è l'oscillazione del portafoglio: "
            f"il portafoglio inserito ha avuto {float(current['volatility'] or 0) * 100:.2f}%, "
            f"mentre il portafoglio ottimale {float(optimized['volatility'] or 0) * 100:.2f}%. "
            f"Cambiamento: {signed_points(delta * 100)} punti."
        )
    if key == "cagr":
        delta = (optimized["cagr"] or 0) - (current["cagr"] or 0)
        return (
            "La differenza più importante è la crescita media annua storica: "
            f"il portafoglio inserito ha avuto {float(current['cagr'] or 0) * 100:.2f}%, "
            f"mentre il portafoglio ottimale {float(optimized['cagr'] or 0) * 100:.2f}%. "
            f"Cambiamento: {signed_points(delta * 100)} punti."
        )
    if key == "totalReturn":
        delta = (optimized["totalReturn"] or 0) - (current["totalReturn"] or 0)
        return (
            "La differenza più importante è il risultato totale nel periodo analizzato: "
            f"portafoglio inserito {float(current['totalReturn'] or 0) * 100:.2f}%, "
            f"portafoglio ottimale {float(optimized['totalReturn'] or 0) * 100:.2f}%. "
            f"Cambiamento: {signed_points(delta * 100)} punti."
        )
    delta = (optimized["finalValue"] or 0) - (current["finalValue"] or 0)
    return (
        "La differenza più importante è il valore finale nella simulazione: "
        f"portafoglio inserito {euro_text(float(current['finalValue'] or 0))}, "
        f"portafoglio ottimale {euro_text(float(optimized['finalValue'] or 0))}. "
        f"Differenza: {euro_text(delta)}."
    )


def build_intelligence_layer(
    profile: dict[str, Any],
    metrics: dict[str, Any],
    frontier: dict[str, Any],
    monte_carlo: dict[str, Any],
    stress: dict[str, Any],
    contribution: list[dict[str, Any]],
    optimized: dict[str, Any] | None = None,
    user_plan: str = "PLUS",
) -> dict[str, Any]:
    initial = float(metrics.get("initialCapital") or profile.get("capital") or 0)
    drawdown = float(metrics.get("maxDrawdown") or 0)
    drawdown_value = initial * (1 + drawdown)
    sharpe = metrics.get("sharpe")
    best = frontier.get("best") if isinstance(frontier, dict) else None
    current = frontier.get("current") if isinstance(frontier, dict) else None
    frontier_locked = bool(frontier.get("locked")) if isinstance(frontier, dict) else False
    monte_carlo_locked = bool(monte_carlo.get("locked")) if isinstance(monte_carlo, dict) else False
    stress_locked = bool(stress.get("locked")) if isinstance(stress, dict) else False
    risk_delta = return_delta = 0.0
    if best and current:
        risk_delta = float(best.get("risk") or 0) - float(current.get("risk") or 0)
        return_delta = float(best.get("return") or 0) - float(current.get("return") or 0)
    optimized_weights_text = ""
    if best and isinstance(frontier, dict):
        optimized_pairs = sorted(
            zip(frontier.get("symbols", []), best.get("weights", [])),
            key=lambda item: item[1],
            reverse=True,
        )[:4]
        optimized_weights_text = ", ".join(f"{public_display_name_for_symbol(symbol)} {weight * 100:.1f}%" for symbol, weight in optimized_pairs)
    constraint_text = ""
    if isinstance(frontier, dict) and frontier.get("constraints"):
        constrained = [
            f"{public_display_name_for_symbol(item['symbol'])} {item['minWeight'] * 100:.0f}-{item['maxWeight'] * 100:.0f}%"
            for item in frontier.get("constraints", [])
        ]
        constraint_text = "; vincoli: " + ", ".join(constrained)
    stress_result = stress.get("result") if isinstance(stress, dict) else None
    stress_return = float(stress_result.get("expected_portfolio_return") or 0) if stress_result else None
    strongest = max(contribution, key=lambda item: item.get("weight", 0), default={})
    worst = min(contribution, key=lambda item: item.get("weightedReturn", 0), default={})
    mc_success = float(monte_carlo.get("probabilityGain") or 0)
    mc_years = int(monte_carlo.get("settings", {}).get("horizonYears", 0) or 0)
    mc_projection = monte_carlo.get("projection") if isinstance(monte_carlo, dict) else []
    mc_last = mc_projection[-1] if mc_projection else {}
    optimized = optimized if isinstance(optimized, dict) else {}
    optimized_available = bool(optimized.get("available"))
    optimized_metrics = optimized.get("metrics") if optimized_available else {}
    optimized_mc = optimized.get("monteCarlo") if optimized_available else {}
    optimized_stress = optimized.get("stressTesting") if optimized_available else {}
    optimized_contribution = optimized.get("contribution") if optimized_available else []
    health = build_portfolio_health_score(
        profile,
        metrics,
        frontier,
        monte_carlo,
        stress,
        contribution,
        user_plan,
        optimized_metrics if optimized_available else None,
    )
    optimized_health = (
        build_portfolio_health_score(
            profile,
            optimized_metrics,
            frontier,
            optimized_mc if isinstance(optimized_mc, dict) else {},
            optimized_stress if isinstance(optimized_stress, dict) else {},
            optimized_contribution if isinstance(optimized_contribution, list) else [],
            user_plan,
            None,
        )
        if optimized_available
        else None
    )
    optimized_cagr_delta = float(optimized_metrics.get("cagr") or 0) - float(metrics.get("cagr") or 0) if optimized_available else 0.0
    optimized_drawdown_delta = float(optimized_metrics.get("maxDrawdown") or 0) - float(metrics.get("maxDrawdown") or 0) if optimized_available else 0.0
    optimized_success_delta = (
        float(optimized_mc.get("probabilityGain") or 0) - float(monte_carlo.get("probabilityGain") or 0)
        if optimized_available and isinstance(optimized_mc, dict) and not optimized_mc.get("locked") and not monte_carlo_locked
        else 0.0
    )
    optimized_stress_return = None
    if optimized_available and isinstance(optimized_stress, dict) and optimized_stress.get("result"):
        optimized_stress_return = float(optimized_stress["result"].get("expected_portfolio_return") or 0)
    optimized_summary = (
        f"Portafoglio ottimale: crescita media annua {float(optimized_metrics.get('cagr') or 0) * 100:.2f}%, "
        f"perdita temporanea {float(optimized_metrics.get('maxDrawdown') or 0) * 100:.2f}%, "
        f"rapporto rischio-rendimento {float(optimized_metrics.get('sharpe') or 0):.2f}."
        if optimized_available
        else optimized.get("reason", "Portafoglio ottimale non disponibile per questa analisi.")
    )
    dominant_backtest_risk = dominant_backtest_risk_comparison(metrics, optimized_metrics if optimized_available else None)
    sections = {
        "healthScore": health,
        "optimizedHealthScore": optimized_health,
        "frontier": {
            "executiveSummary": (
                frontier.get("previewMessage", "La Efficient Frontier e bloccata per il piano corrente.")
                if frontier_locked
                else (
                    "Il portafoglio e confrontato con combinazioni alternative degli stessi strumenti. "
                    "L'obiettivo e capire se il rischio assunto e proporzionato al rendimento storico."
                )
            ),
            "riskInsight": (
                "Il rischio di efficienza non viene calcolato nel piano corrente."
                if frontier_locked
                else f"La combinazione efficiente simulata cambia il rischio di {risk_delta * 100:.2f} punti percentuali rispetto al portafoglio inserito. Il portafoglio ottimale principale risulta: {optimized_weights_text or 'non disponibile'}."
            ),
            "suggestedAction": (
                "Passare a Plus abilita il confronto tra portafoglio attuale e combinazione ottimizzata."
                if frontier_locked
                else "Una riallocazione più bilanciata tra gli stessi strumenti potrebbe migliorare il rapporto tra rischio e rendimento, senza introdurre nuovi asset."
            ),
            "technicalExplanation": (
                frontier.get("message", "Frontiera efficiente non disponibile per il piano corrente.")
                if frontier_locked
                else "La frontiera efficiente confronta molte combinazioni di pesi e cerca quelle con rendimento maggiore a parita di oscillazione, o rischio minore a parita di rendimento."
            ),
            "currentSituation": (
                "Il piano corrente mostra il backtest base, ma non calcola il confronto rischio/rendimento ottimizzato."
                if frontier_locked
                else f"Situazione attuale: rendimento annuo stimato {float(current.get('return', 0) if current else 0) * 100:.2f}%, oscillazione {float(current.get('risk', 0) if current else 0) * 100:.2f}%."
            ),
            "optimizedAlternative": (
                "Con Plus viene mostrata una combinazione alternativa degli stessi strumenti, stimata con la frontiera efficiente."
                if frontier_locked
                else f"Alternativa ottimizzata: rendimento {float(best.get('return', 0) if best else 0) * 100:.2f}%, oscillazione {float(best.get('risk', 0) if best else 0) * 100:.2f}%. Differenza rendimento {return_delta * 100:.2f} punti. Pesi principali: {optimized_weights_text or 'non disponibili'}{constraint_text}."
            ),
        },
        "backtest": {
            "executiveSummary": f"{qualitative_sharpe(sharpe)} Il punto forte principale e la crescita storica; il problema principale e la perdita temporanea da sostenere nei periodi negativi.",
            "riskInsight": dominant_backtest_risk,
            "suggestedAction": (
                f"Il portafoglio ottimale avrebbe cambiato la crescita media annua di {optimized_cagr_delta * 100:+.2f} punti e la perdita temporanea di {optimized_drawdown_delta * 100:+.2f} punti: usa questo confronto per capire se il vincolo rischio/rendimento e coerente."
                if optimized_available
                else "Una riduzione della concentrazione o una maggiore diversificazione potrebbe rendere il percorso più regolare."
            ),
            "technicalExplanation": "Il rapporto rischio-rendimento misura il rendimento per unita di rischio. La perdita temporanea misura la discesa dal massimo precedente al minimo successivo. La crescita media annua e la crescita annua composta.",
            "optimizedComparison": optimized_summary,
        },
        "montecarlo": {
            "executiveSummary": (
                monte_carlo.get("previewMessage", "La Monte Carlo Simulation e bloccata per il piano corrente.")
                if monte_carlo_locked
                else f"La simulazione mostra come potrebbe evolvere il portafoglio nei prossimi {mc_years} anni in {monte_carlo.get('settings', {}).get('simulations', 0)} percorsi possibili. Il capitale resta sopra quello iniziale nel {mc_success * 100:.1f}% dei casi."
            ),
            "riskInsight": (
                "Il rischio probabilistico futuro non viene simulato nel piano corrente."
                if monte_carlo_locked
                else f"Nella traiettoria negativa plausibile, dopo {mc_years} anni il valore finale e circa {float(mc_last.get('p5') or monte_carlo.get('p5') or 0):,.0f} euro. La mediana stimata e circa {float(mc_last.get('median') or monte_carlo.get('median') or 0):,.0f} euro. Il portafoglio ottimale cambia la probabilita di chiudere sopra il capitale iniziale di {optimized_success_delta * 100:.1f} punti."
            ),
            "suggestedAction": (
                "Passare a Plus abilita la lettura delle traiettorie P5, mediana, valore atteso e P95."
                if monte_carlo_locked
                else "Guarda soprattutto la distanza tra scenario prudente e mediana: se è troppo ampia rispetto al capitale che vuoi proteggere, una minore oscillazione potrebbe rendere il percorso più sostenibile."
            ),
            "technicalExplanation": (
                monte_carlo.get("message", "Monte Carlo non disponibile per il piano corrente.")
                if monte_carlo_locked
                else "Monte Carlo ricampiona i rendimenti storici giornalieri e costruisce molte traiettorie future. Il grafico lineare mostra anno per anno P5, mediana, valore atteso e P95: non e una previsione, ma una mappa probabilistica basata sul comportamento storico."
            ),
            "optimizedComparison": (
                f"Portafoglio ottimale: probabilita di profitto {float(optimized_mc.get('probabilityGain') or 0) * 100:.1f}%, P5 finale {float(optimized_mc.get('p5') or 0):,.0f} euro, mediana {float(optimized_mc.get('median') or 0):,.0f} euro."
                if optimized_available and isinstance(optimized_mc, dict) and not optimized_mc.get("locked")
                else optimized.get("reason", "Monte Carlo del portafoglio ottimale non disponibile.")
            ),
        },
        "scenarios": {
            "executiveSummary": stress.get("previewMessage", "Gli scenari sono bloccati per il piano corrente.") if stress_locked else "Gli scenari traducono shock macroeconomici in impatto atteso sul portafoglio tramite esposizioni fattoriali.",
            "riskInsight": "La sensibilità agli shock macro non viene calcolata nel piano corrente." if stress_locked else f"Il rischio principale emerge quando più asset reagiscono allo stesso fattore, creando falsa diversificazione. Il portafoglio ottimale nello stesso scenario ha impatto {optimized_stress_return * 100:.2f}%." if optimized_stress_return is not None else "Il rischio principale emerge quando più asset reagiscono allo stesso fattore, creando falsa diversificazione.",
            "suggestedAction": "Passare a Plus abilita scenari, stress testing e impatto per asset/fattore." if stress_locked else "Confronta impatto attuale e ottimale: se l'ottimale riduce la perdita nello scenario, la diversificazione fattoriale è più robusta.",
            "technicalExplanation": stress.get("message", "Stress testing non disponibile per il piano corrente.") if stress_locked else "La factor analysis stima beta verso equity, tassi, inflazione, credito, oro e commodity. Lo scenario applica beta x shock per stimare l'impatto.",
            "factorSummary": f"Il portafoglio è guidato soprattutto da {strongest.get('symbol', '--')} per peso e da {worst.get('symbol', '--')} come contributo storico più debole.",
            "scenarioImpact": None if stress_return is None else f"Impatto scenario selezionato: {stress_return * 100:.2f}%.",
            "optimizedComparison": (
                f"Portafoglio ottimale nello stesso scenario: {optimized_stress_return * 100:.2f}%."
                if optimized_stress_return is not None
                else optimized.get("reason", "Scenario del portafoglio ottimale non disponibile.")
            ),
        },
        "correlation": {
            "executiveSummary": "La correlazione serve a capire se gli asset sono davvero diversificati o se si muovono in modo simile.",
            "riskInsight": "Molti strumenti diversi possono comportarsi come un unico blocco se reagiscono allo stesso mercato.",
            "suggestedAction": "Cerca diversificazione che cambi comportamento nei periodi difficili, non solo numero di strumenti.",
            "technicalExplanation": "La correlazione misura quanto due serie di rendimenti si muovono insieme: vicino a 1 indica movimenti simili, vicino a 0 indipendenza, sotto 0 movimenti opposti.",
        },
        "analysis": {
            "executiveSummary": f"{health.get('scoreType', 'Portfolio Fit Score')} {health['overall']}/100. {health.get('diagnosis', '')}",
            "riskInsight": f"Il rischio principale resta la coerenza tra perdita temporanea tollerabile e perdita storica o simulata. {optimized_summary}",
            "suggestedAction": "Usa il confronto con il portafoglio ottimale per capire se migliorare pesi e vincoli, non solo aggiungere nuovi strumenti.",
            "technicalExplanation": "Lo score usa solo metriche incluse nel piano utente, esclude quelle bloccate e ricalcola i pesi quando una metrica consentita non e disponibile.",
            "optimizedComparison": optimized_summary,
        },
    }
    footers = {
        "frontier": (
            "rischio non compensato dal rendimento",
            "possibilita di ottenere un miglior equilibrio tra rendimento e oscillazione con gli stessi strumenti",
            "migliorare la distribuzione dei pesi",
        ),
        "backtest": (
            "perdita temporanea nei periodi negativi",
            "crescita storica positiva o contributo favorevole degli asset migliori",
            "ridurre concentrazione e oscillazione del percorso",
        ),
        "montecarlo": (
            "valore finale inferiore al capitale iniziale negli scenari sfavorevoli",
            "probabilita di chiudere sopra il capitale iniziale nelle traiettorie simulate",
            "allineare rischio simulato e obiettivo personale",
        ),
        "scenarios": (
            "dipendenza da fattori macro comuni",
            "presenza di componenti che possono attenuare parte degli shock",
            "aumentare la diversificazione fattoriale",
        ),
        "correlation": (
            "falsa diversificazione tra asset simili",
            "diversificazione reale quando gli strumenti non si muovono tutti nella stessa direzione",
            "includere strumenti con comportamento meno sincronizzato",
        ),
        "analysis": (
            "scostamento tra rischio assunto e profilo investitore",
            "componenti del portafoglio coerenti con orizzonte e obiettivi quando il rischio resta sostenibile",
            "intervenire sulla componente con punteggio più basso",
        ),
    }
    for key, footer in footers.items():
        if key in sections:
            sections[key]["mainRisk"] = footer[0]
            sections[key]["mainStrength"] = footer[1]
            sections[key]["improvementArea"] = footer[2]
    explanation_metrics = {
        "frontier": {
            "currentExpectedReturn": float(current.get("return", 0) if current else 0),
            "currentVolatility": float(current.get("risk", 0) if current else 0),
            "optimizedExpectedReturn": float(best.get("return", 0) if best else 0),
            "optimizedVolatility": float(best.get("risk", 0) if best else 0),
            "currentSharpe": float(current.get("sharpe", 0) if current else 0),
            "optimizedSharpe": float(best.get("sharpe", 0) if best else 0),
            "maxDrawdown": metrics.get("maxDrawdown"),
            "initialCapital": initial,
        },
        "backtest": {**metrics, "initialCapital": initial},
        "montecarlo": {
            **(monte_carlo if isinstance(monte_carlo, dict) and not monte_carlo.get("locked") else {}),
            "initialCapital": initial,
            "maxDrawdown": metrics.get("maxDrawdown"),
        },
        "scenarios": {
            "expectedPortfolioReturn": stress_return,
            "optimizedExpectedPortfolioReturn": optimized_stress_return,
            "maxDrawdown": metrics.get("maxDrawdown"),
            "initialCapital": initial,
        },
        "analysis": {**metrics, "portfolioFitScore": health["overall"], "portfolioHealthScore": health["overall"], "initialCapital": initial, "scorePlan": health.get("plan"), "scoreConfidence": health.get("confidence")},
    }
    optimized_explanation_metrics = {
        "frontier": {
            "currentExpectedReturn": float(best.get("return", 0) if best else 0),
            "currentVolatility": float(best.get("risk", 0) if best else 0),
            "currentSharpe": float(best.get("sharpe", 0) if best else 0),
            "maxDrawdown": optimized_metrics.get("maxDrawdown") if optimized_available else metrics.get("maxDrawdown"),
            "initialCapital": initial,
        },
        "backtest": {**(optimized_metrics if optimized_available else {}), "initialCapital": initial},
        "montecarlo": {
            **(optimized_mc if optimized_available and isinstance(optimized_mc, dict) and not optimized_mc.get("locked") else {}),
            "initialCapital": initial,
            "maxDrawdown": optimized_metrics.get("maxDrawdown") if optimized_available else metrics.get("maxDrawdown"),
        },
        "scenarios": {
            "expectedPortfolioReturn": optimized_stress_return,
            "maxDrawdown": optimized_metrics.get("maxDrawdown") if optimized_available else metrics.get("maxDrawdown"),
            "initialCapital": initial,
        },
        "analysis": {
            **(optimized_metrics if optimized_available else {}),
            "portfolioHealthScore": health["overall"],
            "initialCapital": initial,
        },
    }
    explanation_types = {
        "frontier": "efficient_frontier",
        "backtest": "backtesting",
        "montecarlo": "monte_carlo",
        "scenarios": "scenario_analysis",
        "analysis": "portfolio_fit_score",
    }
    section_titles = {
        "frontier": "Efficient Frontier ottimizzata",
        "backtest": "Backtest ottimizzato",
        "montecarlo": "Monte Carlo ottimizzata",
        "scenarios": "Scenario ottimizzato",
        "analysis": health.get("scoreType", "Portfolio Fit Score"),
    }
    optimized_only_sections = {"frontier", "backtest", "montecarlo", "scenarios"}
    portfolio_for_explanation = [
        {
            "symbol": item.get("symbol"),
            "weight": item.get("weight"),
            "assetClass": item.get("assetClass"),
        }
        for item in contribution
    ]
    optimized_portfolio_for_explanation = portfolio_for_explanation
    if optimized_available and optimized.get("weights"):
        optimized_portfolio_for_explanation = [
            {
                "symbol": item.get("symbol"),
                "weight": item.get("weight"),
                "assetClass": next((asset.get("assetClass") for asset in contribution if asset.get("symbol") == item.get("symbol")), None),
            }
            for item in optimized.get("weights", [])
        ]
    for section_key, analysis_type in explanation_types.items():
        if section_key not in sections:
            continue
        if section_key not in optimized_only_sections:
            request_payload = build_ai_explanation_input(
                analysis_type,
                profile,
                portfolio_for_explanation,
                explanation_metrics.get(section_key, {}),
                user_plan,
                "it",
            )
            request_payload["sectionTitle"] = section_titles.get(section_key, analysis_type)
            sections[section_key]["structuredExplanation"] = build_ai_explanation(request_payload)
            sections[section_key]["explanationRequest"] = request_payload
        if optimized_available:
            optimized_payload = build_ai_explanation_input(
                analysis_type,
                profile,
                optimized_portfolio_for_explanation,
                optimized_explanation_metrics.get(section_key, {}),
                user_plan,
                "it",
            )
            optimized_payload["portfolioVariant"] = "optimized"
            optimized_payload["sectionTitle"] = section_titles.get(section_key, analysis_type)
            optimized_payload["commentInstruction"] = (
                "Commenta solo questa sezione e spiega perché il portafoglio ottimale è più adatto "
                "all'investitore rispetto al portafoglio inserito, usando esclusivamente le metriche ricevute. "
                "Non duplicare frasi già usate in altre card o sezioni e resta aderente al titolo della sezione."
            )
            sections[section_key]["optimizedStructuredExplanation"] = build_ai_explanation(optimized_payload)
            sections[section_key]["optimizedExplanationRequest"] = optimized_payload
    return sections


def openai_text_from_response(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(str(content["text"]))
    return "\n".join(texts).strip()


def validate_ai_advisor(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Advisor JSON non valido.")
    result: dict[str, str] = {}
    for field in AI_ADVISOR_FIELDS:
        value = sanitize_ai_explanation_text(str(payload.get(field, "")).strip())
        if not value:
            raise ValueError(f"Campo advisor mancante: {field}")
        result[field] = value
    result["disclaimer"] = AI_EXPLANATION_DISCLAIMER
    return result


def advisor_text_from_structured(payload: dict[str, str]) -> str:
    parts = [
        payload.get("summary", ""),
        payload.get("score_comment", ""),
        payload.get("main_risk", ""),
        payload.get("main_strength", ""),
        payload.get("optimization_note", ""),
    ]
    return " ".join(part.strip() for part in parts if part.strip()).strip()


def call_openai_advisor(prompt: str) -> tuple[str | None, str, dict[str, str] | None]:
    body = {
        "model": OPENAI_ADVISOR_MODEL,
        "instructions": (
            "Sei un assistente finanziario quantitativo. Non dare consulenza personalizzata vincolante. "
            "Commenta solo i numeri forniti. Rispondi in italiano, breve e concreto. "
            "Non usare le parole compra, vendi o garantito. Restituisci solo JSON valido conforme allo schema."
        ),
        "input": prompt,
        "max_output_tokens": OPENAI_ADVISOR_MAX_OUTPUT_TOKENS,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ai_advisor",
                "schema": AI_ADVISOR_SCHEMA,
                "strict": True,
            }
        },
    }
    payload, error, _category = openai_responses_request(body)
    if error or payload is None:
        return None, error, None
    try:
        structured = validate_ai_advisor(parse_openai_json_response(payload, "Advisor OpenAI"))
        text = advisor_text_from_structured(structured)
        return (text, "", structured) if text else (None, "OpenAI ha risposto senza testo.", None)
    except ValueError as exc:
        return None, f"Risposta OpenAI advisor non valida: {exc}", None


def build_local_advisor_comment(
    profile: dict[str, Any],
    metrics: dict[str, Any],
    frontier: dict[str, Any],
    stress: dict[str, Any],
    score: dict[str, Any],
) -> str:
    drawdown = abs(float(metrics.get("maxDrawdown") or 0))
    sharpe = metrics.get("sharpe")
    sharpe_text = "non calcolabile" if sharpe is None else f"{float(sharpe):.2f}"
    stress_return = None
    if stress.get("result"):
        stress_return = float(stress["result"].get("expected_portfolio_return") or 0)
    proposal = "mantieni il portafoglio, ma confrontalo con il punto rosso della frontiera efficiente."
    best = frontier.get("best") if isinstance(frontier, dict) else None
    symbols = frontier.get("symbols") if isinstance(frontier, dict) else []
    if best and symbols:
        weights = best.get("weights") or []
        top = sorted(zip(symbols, weights), key=lambda item: item[1], reverse=True)[:3]
        proposal = "valuta una versione più efficiente vicina a: " + ", ".join(
            f"{public_display_name_for_symbol(sym)} {weight * 100:.0f}%" for sym, weight in top
        )
    stress_line = "" if stress_return is None else f" Nello scenario selezionato l'impatto atteso e {stress_return * 100:.2f}%."
    return (
        f"Score {score['score']}/100. Profilo {score['riskLabel']} con orizzonte {profile['horizonYears']} anni. "
        f"Il portafoglio ha crescita media annua {float(metrics.get('cagr') or 0) * 100:.2f}%, rapporto rischio-rendimento {sharpe_text} e perdita temporanea massima {drawdown * 100:.2f}%.{stress_line} "
        f"Proposta: {proposal}. Nota: valutazione quantitativa, non raccomandazione finanziaria."
    )


def build_ai_advisor(
    profile: dict[str, Any],
    metrics: dict[str, Any],
    frontier: dict[str, Any],
    monte_carlo: dict[str, Any],
    stress: dict[str, Any],
    contribution: list[dict[str, Any]],
    optimized: dict[str, Any],
    user_plan: str,
) -> dict[str, Any]:
    plan = normalize_plan(user_plan)
    score = build_advisor_score(profile, metrics, frontier, stress)
    frontier_symbols_display = [public_display_name_for_symbol(symbol) for symbol in frontier.get("symbols", [])] if isinstance(frontier, dict) else []
    frontier_constraints_display = [
        {**item, "symbol": public_display_name_for_symbol(item.get("symbol"))}
        for item in frontier.get("constraints", [])
    ] if isinstance(frontier, dict) else []
    contribution_display = [
        {**item, "symbol": item.get("displayName") or public_display_name_for_symbol(item.get("symbol"))}
        for item in contribution
    ]
    optimized_weights_display = [
        {**item, "symbol": public_display_name_for_symbol(item.get("symbol"))}
        for item in optimized.get("weights", [])
    ] if isinstance(optimized, dict) else []
    prompt = json.dumps(
        {
            "user_plan": plan,
            "explanation_level": explanation_level(plan),
            "investor_profile": profile,
            "score": score,
            "metrics": {
                "total_return": metrics.get("totalReturn"),
                "cagr": metrics.get("cagr"),
                "max_drawdown": metrics.get("maxDrawdown"),
                "sharpe": metrics.get("sharpe"),
            },
            "efficient_frontier_best": frontier.get("best") if isinstance(frontier, dict) else None,
            "efficient_frontier_symbols": frontier_symbols_display,
            "efficient_frontier_constraints": frontier_constraints_display,
            "optimized_portfolio": {
                "available": optimized.get("available"),
                "weights": optimized_weights_display,
                "metrics": optimized.get("metrics"),
                "monte_carlo": optimized.get("monteCarlo"),
                "stress": optimized.get("stressTesting", {}).get("result") if isinstance(optimized.get("stressTesting"), dict) else None,
            },
            "stress": stress.get("result") if isinstance(stress, dict) else None,
            "contribution": contribution_display,
            "task": (
                "Dai score, giudizio e proposta di ottimizzazione includendo anche il portafoglio ottimale. Massimo 5 frasi. "
                "Rispetta il livello di spiegazione del piano utente e non commentare funzioni bloccate come se fossero disponibili."
            ),
        },
        ensure_ascii=True,
    )
    advisor_response = call_openai_advisor(prompt)
    if len(advisor_response) == 2:  # Backward-compatible with tests or external monkeypatches.
        comment, openai_error = advisor_response  # type: ignore[misc]
        structured_advisor = None
    else:
        comment, openai_error, structured_advisor = advisor_response
    source = "openai" if comment else "locale"
    if not comment:
        comment = build_local_advisor_comment(profile, metrics, frontier, stress, score)
    section_comments = build_section_comments(profile, metrics, frontier, monte_carlo, stress, contribution, score)
    final_analysis = build_final_analysis(profile, metrics, frontier, stress)
    intelligence = build_intelligence_layer(profile, metrics, frontier, monte_carlo, stress, contribution, optimized, plan)
    section_comments.update(
        {
            "frontier": intelligence["frontier"]["executiveSummary"],
            "backtest": intelligence["backtest"]["executiveSummary"],
            "montecarlo": intelligence["montecarlo"]["executiveSummary"],
            "scenarios": intelligence["scenarios"]["executiveSummary"],
            "analysis": intelligence["analysis"]["executiveSummary"],
        }
    )
    return {
        "source": source,
        "score": intelligence["healthScore"]["overall"],
        "advisorScore": score["score"],
        "userPlan": plan,
        "explanationLevel": explanation_level(plan),
        "riskBudget": score["riskBudget"],
        "summary": comment,
        "advisorStructured": structured_advisor,
        "aiStatus": "openai" if source == "openai" else ("disabled" if not os.getenv("OPENAI_API_KEY") else "fallback"),
        "aiErrorCategory": "" if source == "openai" else classify_openai_error(detail=openai_error),
        "openaiError": openai_error,
        "sections": section_comments,
        "finalAnalysis": final_analysis,
        "intelligence": intelligence,
        "disclaimer": "Analisi quantitativa informativa, non consulenza finanziaria personalizzata.",
    }


def build_optimized_portfolio_analysis(
    frontier: dict[str, Any],
    portfolio: list[dict[str, Any]],
    symbols: list[str],
    price_data: dict[str, list[PricePoint]],
    common_days: list[str],
    asset_classes: dict[str, str],
    initial_capital: float,
    rebalance_frequency: str,
    monte_carlo_settings: dict[str, int],
    stress_settings: dict[str, Any],
    data_source: str,
    feed: str,
    user_plan: str,
) -> dict[str, Any]:
    if not isinstance(frontier, dict) or frontier.get("locked"):
        return {
            "available": False,
            "locked": True,
            "reason": "Portafoglio ottimale disponibile dal piano Plus.",
            "source": "efficientFrontier",
        }
    if not frontier.get("available") or not frontier.get("best"):
        return {
            "available": False,
            "locked": False,
            "reason": frontier.get("reason", "Portafoglio ottimale non disponibile."),
            "source": "efficientFrontier",
        }

    best_weights = frontier["best"].get("weights") or []
    if len(best_weights) != len(symbols):
        return {"available": False, "locked": False, "reason": "Pesi ottimali non coerenti con gli asset.", "source": "efficientFrontier"}

    optimized_weights = {symbol: weight for symbol, weight in zip(symbols, best_weights)}
    optimized_simulation = simulate_portfolio(
        common_days,
        price_data,
        symbols,
        optimized_weights,
        asset_classes,
        initial_capital,
        rebalance_frequency,
    )
    optimized_metrics = build_metrics(
        optimized_simulation["equityCurve"],
        optimized_simulation["dailyReturns"],
        initial_capital,
    )
    optimized_contribution = build_asset_contribution(
        symbols,
        price_data,
        common_days,
        optimized_weights,
        optimized_simulation["finalAssetValues"],
        optimized_simulation["equity"],
        asset_classes,
    )
    portfolio_display_names = {item["symbol"]: item.get("displayName") for item in portfolio}
    for row in optimized_contribution:
        row["displayName"] = public_display_name_for_symbol(row.get("symbol", ""), portfolio_display_names.get(row.get("symbol", ""), ""))
    optimized_monte_carlo = (
        run_monte_carlo(optimized_simulation["dailyReturns"], initial_capital, monte_carlo_settings)
        if has_feature(user_plan, "monteCarlo")
        else locked_feature_payload("monteCarlo")
    )
    optimized_stress = {
        "enabled": stress_settings["enabled"] and has_feature(user_plan, "stressTesting"),
        "result": None,
        "monteCarlo": None,
        "error": "",
    }
    if not has_feature(user_plan, "stressTesting") or not has_feature(user_plan, "scenarioAnalysis"):
        optimized_stress.update(locked_feature_payload("stressTesting"))
    elif stress_settings["enabled"]:
        optimized_items = [
            {
                **item,
                "weight": optimized_weights[item["symbol"]],
            }
            for item in portfolio
        ]
        factor_payload = build_factor_stress_payload(
            optimized_items,
            common_days[0],
            common_days[-1],
            data_source,
            feed,
            stress_settings,
        )
        try:
            optimized_stress["result"] = enrich_stress_result_display_names(run_factor_stress_scenario(factor_payload))
            if stress_settings["runMonteCarlo"]:
                optimized_stress["monteCarlo"] = run_factor_stress_monte_carlo(
                    {
                        **factor_payload,
                        "simulations": 10000,
                        "horizon_days": 252,
                        "seed": 42,
                    }
                )
        except Exception as exc:
            optimized_stress["error"] = str(exc)

    return {
        "available": True,
        "locked": False,
        "weights": [{"symbol": symbol, "weight": optimized_weights[symbol]} for symbol in symbols],
        "metrics": optimized_metrics,
        "equityCurve": optimized_simulation["equityCurve"],
        "dailyReturns": optimized_simulation["dailyReturns"],
        "monteCarlo": optimized_monte_carlo,
        "stressTesting": optimized_stress,
        "contribution": optimized_contribution,
        "constraints": frontier.get("constraints", []),
    }


def run_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    user_plan = effective_user_plan(payload.get("userPlan"))
    start = parse_date(str(payload.get("start", "")), "Data iniziale")
    end = parse_date(str(payload.get("end", "")), "Data finale")
    if end <= start:
        raise BacktestError("La data finale deve essere successiva alla data iniziale.")
    if end > datetime.now(timezone.utc).date():
        raise BacktestError("La data finale non puo essere nel futuro.")

    try:
        initial_capital = float(payload.get("initialCapital", 10000))
    except (TypeError, ValueError) as exc:
        raise BacktestError("Capitale iniziale non valido.") from exc
    if initial_capital <= 0:
        raise BacktestError("Il capitale iniziale deve essere positivo.")
    investor_profile = parse_investor_profile(payload.get("investorProfile"), initial_capital)

    feed = "eodhd"

    improvement_comparison_mode = str(payload.get("improvementComparisonMode") or "")
    portfolio_override = payload.get("portfolioOverride")
    use_portfolio_override = (
        improvement_comparison_mode == "final_optimized_vs_recommended_standard"
        and isinstance(portfolio_override, list)
        and len(portfolio_override) > 0
    )
    portfolio = normalize_portfolio(portfolio_override if use_portfolio_override else payload.get("portfolio", []))
    portfolio = enrich_portfolio_display_names(portfolio)
    symbols = [item["symbol"] for item in portfolio]
    demo = bool(payload.get("demo", False))
    rebalance_frequency = parse_rebalance_frequency(payload.get("rebalanceFrequency"))
    standard_portfolio_mode = str(payload.get("standardPortfolioMode") or "none")
    if standard_portfolio_mode == "none" and isinstance(payload.get("standardBenchmark"), dict):
        standard_portfolio_mode = "use_as_benchmark"
    standard_only_mode = standard_portfolio_mode == "use_as_portfolio"
    skip_optimization_mode = standard_portfolio_mode in {"use_as_portfolio", "use_as_benchmark"}

    try:
        price_data = make_demo_bars(symbols, start, end) if demo else marketDataService(symbols, start, end)
        data_source = "demo" if demo else "eodhd"
    except BacktestError:
        raise

    missing = [symbol for symbol in symbols if symbol not in price_data]
    if missing:
        raise BacktestError(
            "data_unavailable: nessun dato EODHD per: "
            f"{', '.join(missing)}. Verifica ISIN, exchange risolto da OpenFIGI o disponibilita dello storico adjusted."
        )

    common_days, _returns = align_returns(price_data)
    requested_years = max(0.0, (end - start).days / 365.25)
    actual_years = years_between(common_days[0], common_days[-1])
    coverage_warning = ""
    if not demo and requested_years >= 1 and actual_years < requested_years * 0.75:
        coverage_warning = (
            f"Attenzione: hai richiesto circa {requested_years:.1f} anni, "
            f"ma i prezzi disponibili/allineati coprono solo circa {actual_years:.1f} anni "
            f"({common_days[0]} - {common_days[-1]}). "
            "Le metriche possono risultare poco rappresentative. "
            "Verifica piano EODHD, ISIN ed exchange risolto da OpenFIGI."
        )
    weights = {item["symbol"]: item["weight"] for item in portfolio}
    asset_classes = {item["symbol"]: item["assetClass"] for item in portfolio}
    stress_settings = parse_factor_stress_settings(payload.get("stressTesting"))
    base_simulation = simulate_portfolio(
        common_days,
        price_data,
        symbols,
        weights,
        asset_classes,
        initial_capital,
        rebalance_frequency,
    )
    close_by_symbol = base_simulation["closeBySymbol"]
    equity = base_simulation["equity"]
    equity_curve = base_simulation["equityCurve"]
    daily_portfolio_returns = base_simulation["dailyReturns"]
    rebalance_dates = base_simulation["rebalanceDates"]
    metrics = build_metrics(equity_curve, daily_portfolio_returns, initial_capital)
    returns_by_symbol = asset_return_series(common_days, base_simulation["closeBySymbol"], symbols)
    if skip_optimization_mode:
        frontier_result = {
            "available": False,
            "standardOnly": standard_only_mode,
            "standardBenchmark": standard_portfolio_mode == "use_as_benchmark",
            "reason": (
                "Hai scelto di analizzare solo un portfolio standard QuantInvest. "
                "La frontiera efficiente e il portfolio ottimale non vengono calcolati in questa modalita."
                if standard_only_mode
                else (
                    "Hai scelto un portfolio standard QuantInvest come benchmark principale. "
                    "La frontiera efficiente e il portfolio ottimale non vengono calcolati in questa analisi."
                )
            ),
        }
    else:
        frontier_result = (
            run_efficient_frontier(
                returns_by_symbol,
                symbols,
                [weights[symbol] for symbol in symbols],
                [
                    {
                        "minWeight": next(item["minWeight"] for item in portfolio if item["symbol"] == symbol),
                        "maxWeight": next(item["maxWeight"] for item in portfolio if item["symbol"] == symbol),
                    }
                    for symbol in symbols
                ],
            )
            if has_feature(user_plan, "efficientFrontier")
            else locked_feature_payload("efficientFrontier")
        )

    contribution = build_asset_contribution(
        symbols,
        price_data,
        common_days,
        weights,
        base_simulation["finalAssetValues"],
        equity,
        asset_classes,
    )
    portfolio_display_names = {item["symbol"]: item.get("displayName") for item in portfolio}
    for row in contribution:
        row["displayName"] = public_display_name_for_symbol(row.get("symbol", ""), portfolio_display_names.get(row.get("symbol", ""), ""))

    monte_carlo_settings = parse_monte_carlo_settings(payload.get("monteCarlo"))
    monte_carlo = (
        run_monte_carlo(
            daily_portfolio_returns,
            initial_capital,
            monte_carlo_settings,
        )
        if has_feature(user_plan, "monteCarlo")
        else locked_feature_payload("monteCarlo")
    )
    factor_stress = {
        "enabled": stress_settings["enabled"] and has_feature(user_plan, "stressTesting"),
        "settings": stress_settings,
        "scenarios": list(FACTOR_STRESS_SCENARIOS.keys()),
        "factors": FACTOR_STRESS_FACTORS,
        "result": None,
        "monteCarlo": None,
        "error": "",
    }
    if not has_feature(user_plan, "stressTesting") or not has_feature(user_plan, "scenarioAnalysis"):
        factor_stress.update(locked_feature_payload("stressTesting"))
    elif stress_settings["enabled"]:
        factor_payload = build_factor_stress_payload(
            portfolio,
            common_days[0],
            common_days[-1],
            data_source,
            feed,
            stress_settings,
        )
        try:
            factor_stress["result"] = enrich_stress_result_display_names(run_factor_stress_scenario(factor_payload))
            if stress_settings["runMonteCarlo"]:
                factor_stress["monteCarlo"] = run_factor_stress_monte_carlo(
                    {
                        **factor_payload,
                        "simulations": 10000,
                        "horizon_days": 252,
                        "seed": 42,
                    }
                )
        except Exception as exc:
            factor_stress["error"] = str(exc)
    optimized_portfolio = (
        {
            "available": False,
            "standardOnly": standard_only_mode,
            "standardBenchmark": standard_portfolio_mode == "use_as_benchmark",
            "reason": (
                "Modalita portfolio standard: il sistema analizza il portfolio con i pesi indicati "
                "senza creare un portfolio standard ottimale."
                if standard_only_mode
                else (
                    "Modalita benchmark standard: il confronto principale e tra portfolio inserito "
                    "e portfolio benchmark standard QuantInvest, senza calcolare un portfolio ottimale."
                )
            ),
            "source": "standardPortfolio" if standard_only_mode else "standardBenchmark",
        }
        if skip_optimization_mode
        else build_optimized_portfolio_analysis(
            frontier_result,
            portfolio,
            symbols,
            price_data,
            common_days,
            asset_classes,
            initial_capital,
            rebalance_frequency,
            monte_carlo_settings,
            stress_settings,
            data_source,
            feed,
            user_plan,
        )
    )
    optimized_weights = {
        item["symbol"]: float(item.get("weight", 0))
        for item in optimized_portfolio.get("weights", [])
    } if optimized_portfolio.get("available") else None
    selected_standard = None
    if isinstance(payload.get("standardBenchmark"), dict):
        selected_standard = resolve_selected_standard_portfolio(payload.get("standardBenchmark"))
    standard_benchmark_analysis = build_standard_portfolio_analysis(
        selected_standard,
        start=start,
        end=end,
        initial_capital=initial_capital,
        rebalance_frequency=rebalance_frequency,
        monte_carlo_settings=monte_carlo_settings,
        demo=demo,
        user_plan=user_plan,
        name_prefix="Benchmark standard",
        stress_settings=stress_settings,
        data_source=data_source,
        feed=feed,
    ) if selected_standard else {"available": False, "reason": "Nessun benchmark standard selezionato."}
    if has_feature(user_plan, "benchmarkComparison"):
        equal_weight_comparison = equal_weight_benchmark(common_days, price_data, symbols, asset_classes, initial_capital)
        benchmark_comparison = build_smart_benchmark_comparison(
            portfolio,
            start,
            end,
            demo,
            data_source,
            initial_capital,
            rebalance_frequency,
            equal_weight_comparison,
        )
    else:
        benchmark_comparison = locked_feature_payload("benchmarkComparison")
    advanced_comparison_kwargs: dict[str, Any] = {}
    advanced_comparison_unavailable_reason = "Portafoglio di confronto non disponibile."
    primary_label_for_advanced = (
        "portfolio ottimizzato"
        if str(payload.get("improvementComparisonMode") or "") == "final_optimized_vs_recommended_standard"
        else "portfolio inserito"
    )
    if standard_portfolio_mode == "use_as_benchmark" and selected_standard and standard_benchmark_analysis.get("available"):
        try:
            benchmark_items = standard_portfolio_to_backtest_items(selected_standard)
            benchmark_symbols = standard_benchmark_analysis.get("symbols") or [item["symbol"] for item in benchmark_items]
            benchmark_weights = standard_benchmark_analysis.get("weights") or {item["symbol"]: item["weight"] for item in benchmark_items}
            benchmark_asset_classes = standard_benchmark_analysis.get("assetClasses") or {item["symbol"]: item["assetClass"] for item in benchmark_items}
            benchmark_price_data = standard_benchmark_analysis.get("priceData")
            benchmark_common_days = standard_benchmark_analysis.get("commonDays")
            benchmark_close_by_symbol = standard_benchmark_analysis.get("closeBySymbol")
            if not benchmark_price_data or not benchmark_common_days or not benchmark_close_by_symbol:
                benchmark_price_data = make_demo_bars(benchmark_symbols, start, end) if demo else marketDataService(benchmark_symbols, start, end)
                benchmark_common_days, _benchmark_returns = align_returns(benchmark_price_data)
                benchmark_simulation = simulate_portfolio(
                    benchmark_common_days,
                    benchmark_price_data,
                    benchmark_symbols,
                    benchmark_weights,
                    benchmark_asset_classes,
                    initial_capital,
                    rebalance_frequency,
                )
                benchmark_close_by_symbol = benchmark_simulation["closeBySymbol"]
            benchmark_returns_by_symbol = asset_return_series(
                benchmark_common_days,
                benchmark_close_by_symbol,
                benchmark_symbols,
            )
            advanced_comparison_kwargs = {
                "comparison_symbols": benchmark_symbols,
                "comparison_weights": benchmark_weights,
                "comparison_asset_classes": benchmark_asset_classes,
                "comparison_series_by_symbol": benchmark_returns_by_symbol,
                "comparison_price_data": benchmark_price_data,
                "comparison_common_days": benchmark_common_days,
                "comparison_portfolio_items": benchmark_items,
                "comparison_label": "portfolio benchmark standard QuantInvest",
                "comparison_unavailable_reason": "Il portfolio benchmark standard non dispone di dati sufficienti per calcolare questa metrica nel periodo selezionato.",
            }
            optimized_weights_for_advanced = benchmark_weights
            optimized_daily_returns_for_advanced = standard_benchmark_analysis.get("dailyReturns")
            optimized_equity_curve_for_advanced = standard_benchmark_analysis.get("equityCurve")
            optimized_stress_for_advanced = standard_benchmark_analysis.get("stressTesting")
        except Exception:
            advanced_comparison_unavailable_reason = "Il portfolio benchmark standard non dispone di dati sufficienti per calcolare questa metrica nel periodo selezionato."
            advanced_comparison_kwargs = {
                "comparison_label": "portfolio benchmark standard QuantInvest",
                "comparison_unavailable_reason": advanced_comparison_unavailable_reason,
            }
            optimized_weights_for_advanced = None
            optimized_daily_returns_for_advanced = None
            optimized_equity_curve_for_advanced = None
            optimized_stress_for_advanced = None
    else:
        optimized_weights_for_advanced = optimized_weights
        optimized_daily_returns_for_advanced = optimized_portfolio.get("dailyReturns") if optimized_portfolio.get("available") else None
        optimized_equity_curve_for_advanced = optimized_portfolio.get("equityCurve") if optimized_portfolio.get("available") else None
        optimized_stress_for_advanced = optimized_portfolio.get("stressTesting") if optimized_portfolio.get("available") else None
    if "comparison_unavailable_reason" not in advanced_comparison_kwargs:
        advanced_comparison_kwargs["comparison_unavailable_reason"] = advanced_comparison_unavailable_reason
    advanced_analytics = build_advanced_analytics(
        user_plan,
        investor_profile,
        returns_by_symbol,
        symbols,
        weights,
        optimized_weights_for_advanced,
        asset_classes,
        daily_portfolio_returns,
        optimized_daily_returns_for_advanced,
        equity_curve,
        optimized_equity_curve_for_advanced,
        price_data,
        common_days,
        portfolio,
        stress_settings,
        factor_stress,
        optimized_stress_for_advanced,
        data_source,
        feed,
        primary_label=primary_label_for_advanced,
        **advanced_comparison_kwargs,
    )
    ai_advisor = build_ai_advisor(
        investor_profile,
        metrics,
        frontier_result,
        monte_carlo,
        factor_stress,
        contribution,
        optimized_portfolio,
        user_plan,
    )
    health_score = ai_advisor.get("intelligence", {}).get("healthScore", {}) if isinstance(ai_advisor, dict) else {}
    standard_recommendation = get_recommended_standard_portfolio(
        standard_recommendation_input(investor_profile, health_score, user_plan)
    )
    recommended_standard = (
        standard_recommendation.get("recommendedPortfolio")
        if isinstance(standard_recommendation.get("recommendedPortfolio"), dict)
        else None
    )
    standard_recommendation_analysis = build_standard_portfolio_analysis(
        recommended_standard,
        start=start,
        end=end,
        initial_capital=initial_capital,
        rebalance_frequency=rebalance_frequency,
        monte_carlo_settings=monte_carlo_settings,
        demo=demo,
        user_plan=user_plan,
        name_prefix="Portfolio standard suggerito",
        stress_settings=stress_settings,
        data_source=data_source,
        feed=feed,
    ) if standard_recommendation.get("shouldSuggest") and recommended_standard else {"available": False, "reason": "Nessun portfolio standard suggerito da confrontare."}
    current_compare_payload = {
        "name": "Portfolio inserito",
        "metrics": metrics,
        "dailyReturns": daily_portfolio_returns,
        "contribution": contribution,
    }
    optimized_compare_payload = {
        "name": "Portafoglio ottimale",
        "metrics": optimized_portfolio.get("metrics", {}),
        "dailyReturns": optimized_portfolio.get("dailyReturns", []),
        "contribution": optimized_portfolio.get("contribution", []),
    }
    if standard_recommendation_analysis.get("available"):
        standard_recommendation["analysis"] = standard_recommendation_analysis
        standard_recommendation["comparisons"] = {
            "userVsStandard": compare_portfolios(current_compare_payload, standard_recommendation_analysis, "user_vs_standard"),
            "optimizedVsStandard": compare_portfolios(optimized_compare_payload, standard_recommendation_analysis, "optimized_vs_standard") if optimized_portfolio.get("available") else {"available": False, "reason": "Portafoglio ottimale non disponibile."},
        }
    if standard_benchmark_analysis.get("available"):
        standard_benchmark_analysis["comparisons"] = {
            "userVsStandard": compare_portfolios(current_compare_payload, standard_benchmark_analysis, "user_vs_standard"),
            "optimizedVsStandard": compare_portfolios(optimized_compare_payload, standard_benchmark_analysis, "optimized_vs_standard") if optimized_portfolio.get("available") else {"available": False, "reason": "Portafoglio ottimale non disponibile."},
        }
    benchmark_checkup = (
        calculate_benchmark_relative_checkup(
            user_metrics=metrics,
            benchmark_metrics=standard_benchmark_analysis.get("metrics", {}),
            user_contribution=contribution,
            benchmark_contribution=standard_benchmark_analysis.get("contribution", []),
            investor_profile=investor_profile,
        )
        if selected_standard and standard_benchmark_analysis.get("available")
        else None
    )
    benchmark_health_score = (
        calculate_portfolio_fit_score(
            plan=user_plan,
            userProfile=investor_profile,
            portfolioMetrics=standard_benchmark_analysis.get("metrics", {}),
            optimizedPortfolioMetrics=None,
            availableFeatures=PLAN_FEATURES.get(user_plan, {}),
            dataQuality={"demo": demo, "shortHistory": bool(coverage_warning), "dataUnavailable": False},
            frontier={},
            monteCarlo=standard_benchmark_analysis.get("monteCarlo", {}),
            stressTesting=standard_benchmark_analysis.get("stressTesting", {}),
            contribution=standard_benchmark_analysis.get("contribution", []),
            advancedAnalytics={},
            tracking={},
            portfolioContext=selected_standard,
        )
        if selected_standard and standard_benchmark_analysis.get("available")
        else None
    )
    recommended_standard_health_score = (
        calculate_portfolio_fit_score(
            plan=user_plan,
            userProfile=investor_profile,
            portfolioMetrics=standard_recommendation_analysis.get("metrics", {}),
            optimizedPortfolioMetrics=None,
            availableFeatures=PLAN_FEATURES.get(user_plan, {}),
            dataQuality={"demo": demo, "shortHistory": bool(coverage_warning), "dataUnavailable": False},
            frontier={},
            monteCarlo=standard_recommendation_analysis.get("monteCarlo", {}),
            stressTesting=standard_recommendation_analysis.get("stressTesting", {}),
            contribution=standard_recommendation_analysis.get("contribution", []),
            advancedAnalytics={},
            tracking={},
            portfolioContext=recommended_standard,
        )
        if recommended_standard and standard_recommendation_analysis.get("available")
        else None
    )
    benchmark_comment = (
        build_standard_benchmark_comment(standard_benchmark_analysis, benchmark_checkup)
        if selected_standard and standard_benchmark_analysis.get("available")
        else None
    )
    analysis_comparison_context = resolve_analysis_comparison_context(
        user_portfolio=portfolio,
        optimized_portfolio=optimized_portfolio,
        selected_standard_portfolio=selected_standard,
        standard_portfolio_mode=standard_portfolio_mode,
        standard_benchmark_analysis=standard_benchmark_analysis,
        benchmark_checkup=benchmark_checkup,
        benchmark_health_score=benchmark_health_score,
        benchmark_comment=benchmark_comment,
    )
    improvement_plan = build_improvement_plan(
        comparison_context=analysis_comparison_context,
        user_portfolio=portfolio,
        metrics=metrics,
        optimized_portfolio=optimized_portfolio,
        selected_standard=selected_standard,
        standard_benchmark_analysis=standard_benchmark_analysis,
        standard_recommendation=standard_recommendation,
        standard_recommendation_analysis=standard_recommendation_analysis,
        investor_profile=investor_profile,
        health_score=health_score,
        user_plan=user_plan,
        improvement_comparison_mode=str(payload.get("improvementComparisonMode") or "") or None,
    )
    final_analysis_mode = str(payload.get("improvementComparisonMode") or "") or analysis_comparison_context.get("comparisonMode", "optimization")
    final_analysis_context = build_final_analysis_context(
        mode=final_analysis_mode,
        user_portfolio=portfolio,
        optimized_portfolio=optimized_portfolio,
        selected_standard=selected_standard,
        recommended_standard=recommended_standard,
        metrics=metrics,
        optimized_metrics=optimized_portfolio.get("metrics", {}) if optimized_portfolio.get("available") else None,
        selected_standard_analysis=standard_benchmark_analysis,
        recommended_standard_analysis=standard_recommendation_analysis,
        health_score=health_score,
        optimized_health_score=ai_advisor.get("intelligence", {}).get("optimizedHealthScore") if isinstance(ai_advisor, dict) else None,
        benchmark_health_score=benchmark_health_score,
        recommended_standard_health_score=recommended_standard_health_score,
        investor_profile=investor_profile,
        user_plan=user_plan,
        contribution=contribution,
        stress=factor_stress,
        standard_recommendation=standard_recommendation,
        demo=demo,
        coverage_warning=coverage_warning,
        ai_status=ai_advisor.get("aiStatus", "fallback") if isinstance(ai_advisor, dict) else "fallback",
        improvement_plan=improvement_plan,
    )
    if isinstance(ai_advisor, dict):
        ai_advisor["finalAnalysisContext"] = final_analysis_context
        ai_advisor.setdefault("finalAnalysis", {})
        ai_advisor["finalAnalysis"]["text"] = final_analysis_context["mainConclusion"]["summary"]
        analysis_section = ai_advisor.get("intelligence", {}).get("analysis")
        if isinstance(analysis_section, dict):
            analysis_section["finalAnalysisContext"] = final_analysis_context
            if isinstance(analysis_section.get("explanationRequest"), dict):
                analysis_section["explanationRequest"]["finalAnalysisContext"] = {
                    "mode": final_analysis_context["mode"],
                    "primaryPortfolioRole": final_analysis_context["primaryPortfolioRole"],
                    "comparisonPortfolioRole": final_analysis_context["comparisonPortfolioRole"],
                    "primaryScore": final_analysis_context["scoreComparison"].get("primaryScore"),
                    "comparisonScore": final_analysis_context["scoreComparison"].get("comparisonScore"),
                    "scoreComparison": final_analysis_context["scoreComparison"],
                    "monitoringDashboard": final_analysis_context["monitoringDashboard"],
                    "dataReliability": final_analysis_context["dataReliability"],
                }

    return {
        "userPlan": user_plan,
        "plans": PLAN_FEATURES,
        "instrumentMetadata": public_instrument_metadata_for(portfolio),
        "dataSource": data_source,
        "dataWarning": (
            "Dati demo generati: non usare questi risultati come performance storica reale."
            if data_source == "demo"
            else coverage_warning
        ),
        "start": common_days[0],
        "end": common_days[-1],
        "tradingDays": len(common_days),
        "portfolio": portfolio,
        "investorProfile": investor_profile,
        "aiAdvisor": ai_advisor,
        "rebalance": {
            "frequency": rebalance_frequency,
            "label": rebalance_label(rebalance_frequency),
            "count": len(rebalance_dates),
            "dates": rebalance_dates,
        },
        "equityCurve": equity_curve,
        "metrics": metrics,
        "contribution": contribution,
        "monteCarlo": monte_carlo,
        "efficientFrontier": frontier_result,
        "stressTesting": factor_stress,
        "optimizedPortfolio": optimized_portfolio,
        "benchmarkComparison": benchmark_comparison,
        "advancedAnalytics": advanced_analytics,
        "standardPortfolios": {
            "items": get_available_standard_portfolios(user_plan, include_locked_preview=True),
            "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
        },
        "standardBenchmark": payload.get("standardBenchmark") if isinstance(payload.get("standardBenchmark"), dict) else None,
        "standardBenchmarkAnalysis": public_standard_portfolio_analysis(standard_benchmark_analysis),
        "standardPortfolioRecommendation": standard_recommendation,
        "analysisComparisonContext": analysis_comparison_context,
        "improvementPlan": improvement_plan,
    }


def run_benchmark_preview(payload: dict[str, Any]) -> dict[str, Any]:
    result = run_backtest({**payload, "monteCarlo": {"horizonYears": 1, "simulations": 1000}})
    benchmark = result.get("benchmarkComparison", {})
    return {
        "userPlan": result.get("userPlan"),
        "dataSource": result.get("dataSource"),
        "start": result.get("start"),
        "end": result.get("end"),
        "benchmarkComparison": benchmark,
        "message": benchmark.get("method", "Confronto benchmark calcolato sugli stessi strumenti."),
    }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PortfolioBacktester/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(public_json_payload(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/" or self.path.startswith("/?"):
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self.serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self.serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if path == "/charts-core.js":
            self.serve_file(STATIC_DIR / "charts-core.js", "application/javascript; charset=utf-8")
            return
        if path == "/api/portfolios":
            self.send_json(200, {"items": load_saved_portfolios()})
            return
        if path == "/api/standard-portfolios":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            plan = effective_user_plan(query.get("plan", ["PLUS"])[0])
            self.send_json(
                200,
                {
                    "items": get_available_standard_portfolios(plan, include_locked_preview=True),
                    "disclaimer": STANDARD_PORTFOLIO_DISCLAIMER,
                },
            )
            return
        if path == "/api/plans":
            self.send_json(
                200,
                {
                    "plans": PLAN_FEATURES,
                    "features": FEATURE_MESSAGES,
                    "serverPlan": SERVER_USER_PLAN if ENFORCE_SERVER_USER_PLAN and SERVER_USER_PLAN in PLAN_FEATURES else "",
                    "serverPlanEnforced": ENFORCE_SERVER_USER_PLAN and SERVER_USER_PLAN in PLAN_FEATURES,
                },
            )
            return
        if path == "/api/instruments/search":
            try:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                include_global = query.get("includeGlobal", ["0"])[0].lower() in {"1", "true", "yes"}
                self.send_json(200, search_instruments(query.get("q", [""])[0], include_global))
            except BacktestError as exc:
                status = 429 if "429" in str(exc) else 400
                self.send_json(status, {"error": str(exc), "items": []})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in {"/api/backtest", "/api/benchmark", "/api/portfolios", "/api/monitor", "/api/ai/explanation-detail"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/backtest":
                self.send_json(200, run_backtest(payload))
            elif path == "/api/benchmark":
                self.send_json(200, run_benchmark_preview(payload))
            elif path == "/api/portfolios":
                self.send_json(200, save_portfolio(payload))
            elif path == "/api/ai/explanation-detail":
                result = build_ai_explanation_detail(payload)
                self.send_json(200, result)
            else:
                self.send_json(200, run_portfolio_monitor(payload))
        except FeatureLockedError as exc:
            self.send_json(403, locked_feature_payload(exc.feature))
        except BacktestError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:  # Defensive boundary for the local HTTP API.
            self.send_json(500, public_internal_error(exc))

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/portfolios":
            self.send_error(404)
            return
        try:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            portfolio_id = query.get("id", [""])[0]
            if not portfolio_id:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                portfolio_id = str(payload.get("id", ""))
            self.send_json(200, delete_saved_portfolio(portfolio_id))
        except BacktestError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, public_internal_error(exc))

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Portfolio Backtester: http://127.0.0.1:{port}")
    print("Imposta EODHD_API_KEY e OPENFIGI_API_KEY per usare dati reali adjusted.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")


if __name__ == "__main__":
    main()
