# Portfolio Intelligence Backtester

App locale per analizzare portfolio con OpenFIGI per risolvere strumenti ed EODHD per scaricare prezzi storici adjusted.

## Avvio

Metodo consigliato: crea un file `.env.local` nella cartella del progetto con:

```bash
EODHD_API_KEY="la_tua_key"
OPENFIGI_API_KEY="la_tua_key"
OPENAI_API_KEY="la_tua_key"
OPENAI_MODEL="gpt-5.5"
PORT="8765"
```

Puoi usare `OPENAI_EXPLANATION_MODEL` e `OPENAI_ADVISOR_MODEL` per separare il modello delle spiegazioni strutturate dal commento sintetico generale. Per ridurre costo e latenza puoi impostare `OPENAI_MODEL="gpt-5.4-mini"`.

Opzioni utili per affidabilità, costo e latenza:

```bash
OPENAI_TIMEOUT_SECONDS="18"
OPENAI_MAX_RETRIES="2"
OPENAI_EXPLANATION_MAX_OUTPUT_TOKENS="520"
OPENAI_ADVISOR_MAX_OUTPUT_TOKENS="280"
AI_EXPLANATION_CACHE_TTL_SECONDS="604800"
```

Poi avvia:

```bash
python3 app.py
```

Su macOS puoi anche aprire `start_portfolio.command`: avvia il server e apre automaticamente il browser su `http://127.0.0.1:8765`.

Metodo alternativo senza `.env.local`:

```bash
EODHD_API_KEY="la_tua_key" OPENFIGI_API_KEY="la_tua_key" python3 app.py
```

Poi apri:

```text
http://127.0.0.1:8765
```

Se non imposti le credenziali, puoi usare la spunta `Usa dati demo`. Se la modalita demo e disattivata e un provider non restituisce dati, il backend risponde con `data_unavailable`.

## Funzioni

- Selettore piano `FREE`, `PLUS`, `ADVANCED` con feature gating leggero.
- Portfolio multi-strumento con pesi percentuali. Puoi inserire ticker, ISIN o nome.
- Classificazione per strumento: azioni, obbligazioni, oro o materie prime.
- Sezione `Arco temporale` con date manuali e scorciatoie `5 anni`, `10 anni`, `15 anni`, `YTD`.
- Ribilanciamento mensile di default: il tracking usa una verifica mensile per confrontare pesi attuali e pesi target.
- Sezione `Monte Carlo` con orizzonte e numero di simulazioni configurabili, basata sui rendimenti giornalieri storici del portfolio.
- Sezione `Stress testing` fattoriale quantitativa; con dati reali non usa simulazioni sintetiche se i fattori non sono disponibili.
- Sezione `Efficient frontier` integrata, calcolata dai rendimenti storici degli asset del portfolio, con vincoli opzionali di peso minimo e massimo per ciascun asset.
- Analisi del portfolio ottimale anche nelle sezioni Backtest, Monte Carlo, Scenari e Analisi finale.
- Salvataggio portfolio in locale su `saved_portfolios.json`.
- Monitor portfolio salvati con prezzi EODHD/demo e calcolo indicativo di cosa comprare o vendere per tornare ai pesi target.
- Integrazione OpenFIGI + EODHD per risolvere strumenti e scaricare prezzi giornalieri adjusted.
- Cache locale strumenti/prezzi su `market_data_cache.json`.
- Metriche: valore finale, rendimento totale, CAGR, max drawdown, Sharpe e giorni di mercato.
- Curva equity e tabella contributi per asset.

## Piani e feature gating

L'app non include pagamenti reali o Stripe. Il piano utente e selezionabile localmente nella schermata `Dati investitore` e viene passato alle API come `userPlan`.

- `FREE`: backtest base, performance storica, CAGR, volatilita implicita nelle metriche, max drawdown, Sharpe base e Portfolio Health Score base. Le sezioni avanzate mostrano una card bloccata con CTA.
- `PLUS`: include Efficient Frontier, portafoglio ottimizzato, Monte Carlo, Scenario Analysis, Stress Testing, tracking portfolio e suggerimenti di ribilanciamento.
- `ADVANCED`: abilita la configurazione per funzionalita evolute come Fama-French, Sortino, correlazione avanzata, rolling metrics, peak-to-trough e factor shock analysis.

La configurazione centrale vive in `app.py` (`PLAN_FEATURES` e `FEATURE_MESSAGES`). Il backend evita i calcoli costosi per le funzioni bloccate e restituisce payload `FEATURE_LOCKED`; il frontend mostra una preview invece di errori tecnici.

## Stress testing fattoriale integrato

Il programma principale include ora il nuovo stress testing quantitativo direttamente nella stessa interfaccia di backtest, insieme a Monte Carlo ed Efficient Frontier.

La sezione `Stress testing` usa:

- fattori proxy: equity, rates, inflation, credit, gold, commodities;
- regressione OLS implementata senza dipendenze esterne;
- scenari predefiniti;
- slider per shock personalizzati;
- beta matrix;
- contributo per asset e contributo per fattore;
- Monte Carlo fattoriale opzionale.

## Agente AI e percorso guidato

La UI ora e' step-by-step:

1. dati investitore;
2. portfolio;
3. Efficient Frontier;
4. backtest;
5. Monte Carlo;
6. scenari.

Il backend genera uno score e un commento breve con proposta di ottimizzazione. Se imposti `OPENAI_API_KEY`, il commento viene generato tramite OpenAI Responses API; altrimenti usa un fallback locale deterministico basato sui risultati quantitativi.

Il piano utente e il livello di spiegazione (`basic`, `complete`, `advanced`) vengono inclusi nel prompt OpenAI, cosi i commenti restano coerenti con le funzionalita disponibili. Le sezioni mostrano subito interpretazione, significato ed esempio concreto calcolati localmente; i dettagli avanzati vengono richiesti a OpenAI solo quando l'utente preme il pulsante dedicato. Anche il piano `FREE` usa OpenAI per questi dettagli quando `OPENAI_API_KEY` e configurata; il fallback locale resta come rete di sicurezza se la chiave manca o l'API non risponde.

## AI Explanation Service

Il backend include un layer `AI Explanation` che interpreta soltanto metriche gia calcolate dal sistema. Non calcola rendimenti, drawdown, frontiere o scenari.

Componenti principali in `app.py`:

- `AI_EXPLANATION_SCHEMA`
- `AI_EXPLANATION_TEMPLATES`
- `build_ai_explanation_input`
- `call_openai_explanation`
- `rule_based_explanation`
- `build_ai_explanation`

Ogni spiegazione automatica mostra subito sintesi, significato per l'utente, esempio concreto e disclaimer. I campi piu costosi da elaborare vengono generati on demand tramite OpenAI Structured Outputs quando l'utente li apre, con schema `title`, `simple_summary`, `what_the_numbers_mean`, `main_risk`, `main_strength`, `possible_improvement`, `what_to_look_at`, `technical_note`, `disclaimer`. Se OpenAI non e disponibile, il fallback rule-based produce lo stesso formato.

Le chiamate OpenAI passano da un helper unico con retry/backoff per errori temporanei, timeout configurabile e cache locale versionata su `ai_explanation_cache.json`. L'advisor generale usa Structured Outputs e continua a esporre il campo `summary` per compatibilita con il frontend.

## Test

```bash
PYTHONPYCACHEPREFIX=/private/tmp/pycache python3 -m py_compile app.py
node --check static/app.js
PYTHONPYCACHEPREFIX=/private/tmp/pycache python3 -m unittest tests.test_feature_access
python3 tools/validate_openai_ai.py
```

## Monitoraggio e ribilanciamento

I portfolio salvati contengono capitale, pesi target, frequenza di ribilanciamento e profilo investitore. Il monitor ricostruisce quantità teoriche dal capitale salvato e dai prezzi storici/attuali; le quantità di acquisto o vendita sono quindi indicative e vanno confrontate con le posizioni reali del broker prima di operare.

La versione standalone senza dipendenze esterne resta disponibile in:

```text
factor_stress_app/
```

Per avviare solo la standalone:

```bash
python3 factor_stress_app/standalone/server.py 8877
```

Poi apri `http://127.0.0.1:8877`.

## Note market data

Il backend legge le credenziali da `EODHD_API_KEY` e `OPENFIGI_API_KEY`.

Il flusso dati e:

1. input utente: ticker, ISIN o nome;
2. risoluzione strumento con OpenFIGI;
3. normalizzazione di ISIN, ticker, exchange e valuta;
4. download prezzi adjusted da EODHD;
5. salvataggio in cache locale;
6. errore leggibile `data_unavailable` se un dato non e disponibile.

La funzione unica per il resto dell'app e `getHistoricalPrices(instrumentId, startDate, endDate)`.

## Deploy Vercel

Il progetto include un wrapper leggero per Vercel:

- `api/index.py`: espone `app.py` come Python Serverless Function;
- `vercel.json`: instrada tutte le richieste verso la funzione.

Prima di usare dati reali in produzione, configura in Vercel Project Settings le variabili:

```text
EODHD_API_KEY
OPENFIGI_API_KEY
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_EXPLANATION_MODEL
OPENAI_ADVISOR_MODEL
OPENAI_TIMEOUT_SECONDS
OPENAI_MAX_RETRIES
OPENAI_EXPLANATION_MAX_OUTPUT_TOKENS
OPENAI_ADVISOR_MAX_OUTPUT_TOKENS
AI_EXPLANATION_CACHE_TTL_SECONDS
```

Non caricare `.env.local`: resta solo per l'ambiente locale.
