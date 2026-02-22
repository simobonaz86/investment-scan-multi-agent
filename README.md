# Investment Scan Multi-Agent (MVP)

Deployable MVP that scans one or more tickers, pulls basic market data + recent news, and produces a simple risk/signal report using a small “multi-agent” pipeline.

## What you get (MVP scope)

- FastAPI server with JSON API
- Background scan jobs (non-blocking request/response)
- SQLite persistence (stores scans + results)
- No paid API keys required (uses public endpoints)
- Docker + docker-compose deployment for a remote machine
- Minimal test suite (pytest)

## Quickstart (local)

Prereqs: Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"

uvicorn invest_scan.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs`.

## Run a scan

```bash
curl -sS -X POST "http://localhost:8000/scan" \
  -H "content-type: application/json" \
  -d '{"tickers":["AAPL","MSFT"],"as_of":"auto"}' | jq
```

Then poll:

```bash
curl -sS "http://localhost:8000/scan/<scan_id>" | jq
```

## Deploy (remote machine) with Docker

Prereqs: Docker + Docker Compose plugin

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f api
```

Service listens on port `8000` by default.

## Tests

```bash
pytest -q
```

## Notes

- This is intentionally MVP-only. The “agents” are lightweight modules:
  - Market data agent (public CSV source)
  - News agent (RSS)
  - Signals + risk scoring agent
  - Summary agent (template-based; can be extended to use an LLM)

