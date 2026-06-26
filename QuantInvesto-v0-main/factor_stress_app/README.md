# Factor Stress App

Modulo quantitativo per analizzare portafogli con stress testing fattoriale, scenari predefiniti e Monte Carlo.

Questa cartella contiene due versioni:

- `standalone/`: versione consigliata locale, senza pandas, pytest, npm o altre dipendenze esterne.
- `backend/` + `frontend/`: bozza architetturale FastAPI + React per una futura versione con stack completo.

## Avvio rapido senza dipendenze

```bash
python3 factor_stress_app/standalone/server.py 8877
```

Poi apri:

```bash
http://127.0.0.1:8877
```

## Test senza pytest

```bash
python3 -m unittest discover -s factor_stress_app/standalone/tests -v
```

## Endpoint standalone

Gli endpoint sono gli stessi della specifica:

- `POST /portfolio/factor-analysis`
- `POST /portfolio/scenario`
- `POST /portfolio/monte-carlo`

## Documentazione

Vedi `docs/TECHNICAL_DESIGN.md`.
