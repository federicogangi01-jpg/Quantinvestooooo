# Quantitative Factor Stress Testing Platform

## 1. Architettura completa

La piattaforma e' separata in due applicazioni:

- Backend FastAPI: espone API quantitative per factor analysis, scenario analysis e Monte Carlo.
- Frontend React/TypeScript: dashboard interattiva con slider, grafici e tabelle.

Flusso dati:

1. L'utente inserisce asset, pesi, date e scenario.
2. Il backend scarica prezzi giornalieri degli asset e degli ETF proxy fattoriali.
3. I prezzi vengono trasformati in rendimenti giornalieri.
4. Per ogni asset viene stimata una regressione OLS contro i fattori.
5. Lo scenario applica uno shock vettoriale ai beta stimati.
6. Il frontend visualizza impatti, esposizioni, matrice beta e distribuzione Monte Carlo.

## 2. Schema database

La versione iniziale e' stateless, ma lo schema consigliato per produzione e':

```sql
CREATE TABLE portfolios (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE portfolio_assets (
  id UUID PRIMARY KEY,
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  symbol TEXT NOT NULL,
  asset_class TEXT NOT NULL,
  weight NUMERIC NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE factor_prices (
  factor TEXT NOT NULL,
  proxy_symbol TEXT NOT NULL,
  price_date DATE NOT NULL,
  close NUMERIC NOT NULL,
  PRIMARY KEY (factor, price_date)
);

CREATE TABLE asset_prices (
  symbol TEXT NOT NULL,
  price_date DATE NOT NULL,
  close NUMERIC NOT NULL,
  PRIMARY KEY (symbol, price_date)
);

CREATE TABLE factor_exposures (
  id UUID PRIMARY KEY,
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  symbol TEXT NOT NULL,
  alpha NUMERIC NOT NULL,
  beta_equity NUMERIC NOT NULL,
  beta_rates NUMERIC NOT NULL,
  beta_inflation NUMERIC NOT NULL,
  beta_gold NUMERIC NOT NULL,
  beta_commodities NUMERIC NOT NULL,
  beta_credit NUMERIC NOT NULL,
  r_squared NUMERIC NOT NULL,
  model_p_value NUMERIC,
  estimated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE scenario_runs (
  id UUID PRIMARY KEY,
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  scenario_name TEXT NOT NULL,
  shock_vector JSONB NOT NULL,
  expected_portfolio_return NUMERIC NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

## 3. Struttura cartelle

```text
factor_stress_app/
  backend/
    app/
      analytics.py
      data.py
      factors.py
      main.py
      monte_carlo.py
      scenario_engine.py
      scenarios.py
      schemas.py
    tests/
      test_api.py
      test_factor_engine.py
    requirements.txt
  frontend/
    src/
      components/
      api.ts
      App.tsx
      main.tsx
      styles.css
      types.ts
    package.json
    tsconfig.json
    vite.config.ts
  docs/
    TECHNICAL_DESIGN.md
```

## 4. Backend FastAPI

Endpoint:

- `POST /portfolio/factor-analysis`
- `POST /portfolio/scenario`
- `POST /portfolio/monte-carlo`

Provider dati:

- `EodhdDataProvider`: usa prezzi giornalieri adjusted da EODHD.
- `DemoDataProvider`: genera dati sintetici deterministici per test e sviluppo offline.

Fattori iniziali:

| Fattore | ETF proxy |
|---|---|
| Global Equity | VT |
| Government Bonds / Rates | IEF |
| Inflation Proxy | TIP |
| Gold | GLD |
| Commodities | DBC |
| Credit | LQD |

## 5. Modello matematico

Per ogni asset viene stimato:

```text
R_asset,t = alpha + beta_equity * R_equity,t
                  + beta_rates * R_rates,t
                  + beta_inflation * R_inflation,t
                  + beta_gold * R_gold,t
                  + beta_commodities * R_commodities,t
                  + beta_credit * R_credit,t
                  + epsilon_t
```

La regressione e' OLS. Gli output sono:

- alpha;
- beta factor exposures;
- R²;
- p-value del modello;
- p-value dei singoli fattori.

Dato uno shock vettoriale:

```text
s = [shock_equity, shock_rates, shock_inflation, shock_gold, shock_commodities, shock_credit]
```

il rendimento atteso dello scenario per asset e':

```text
asset_return_i = beta_i · s
```

Il rendimento atteso del portafoglio e':

```text
portfolio_return = Σ weight_i * asset_return_i
```

Il contributo per fattore e':

```text
factor_contribution_j = Σ weight_i * beta_i,j * shock_j
```

## 6. Monte Carlo

La simulazione opzionale usa:

- rendimento atteso storico giornaliero;
- volatilita' storiche;
- matrice di covarianza storica, quindi correlazioni implicite;
- distribuzione normale multivariata;
- 10.000 simulazioni di default.

Output:

- percentile 5%;
- percentile 50%;
- percentile 95%;
- probabilita' di perdita.

## 7. Frontend React

Dashboard incluse:

- Factor Exposure Dashboard;
- Scenario Dashboard;
- Interactive Shock Sliders;
- Heatmap beta matrix;
- Portfolio impact chart;
- Monte Carlo distribution chart.

Gli slider aggiornano gli shock custom:

- Equity Shock;
- Rates Shock;
- Inflation Shock;
- Credit Shock;
- Gold Shock;
- Commodity Shock.

## 8. Avvio

Backend:

```bash
cd factor_stress_app/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd factor_stress_app/frontend
npm install
npm run dev
```

Test:

```bash
cd factor_stress_app/backend
pytest
```
