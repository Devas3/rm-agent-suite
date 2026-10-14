# RM Agent

A prototype relationship intelligence platform for commercial banking. It monitors client financial and relationship signals, ranks relationships by risk, and gives Relationship Managers (RMs) daily briefings and an AI copilot for meeting preparation.

## What it does

- Tracks deposit balances, wire/ACH activity, RM interactions, and CD maturities.
- Computes client metrics and portfolio-relative risk scores.
- Uses an LLM to produce structured relationship summaries and recommended actions.
- Provides a web portal for daily briefings and RM Copilot conversations.

## Architecture

### Data and briefing pipeline

1. **Neon Postgres** stores RM, client, balance, payment, interaction, and account data.
2. **CDC poller** checks for updated rows and publishes change events to **Apache Kafka**.
3. **Stream processor** consumes events, recomputes client metrics, and writes them to **Upstash Redis**.
4. **Daily briefing flow** reads portfolio data and metrics, scores relationship risk, and uses **Agno + Groq** to create summaries and recommended actions.
5. Briefings are saved to **MongoDB Atlas** or the local JSON document-store fallback.
6. The **FastAPI gateway** serves the briefing and Copilot APIs to the web frontend.

**Pipeline:** Neon Postgres -> CDC poller -> Kafka -> Stream processor -> Upstash Redis -> Daily briefing flow -> MongoDB / local JSON -> FastAPI + web UI

The CDC poller checks `updated_at` timestamps; it is polling-based change detection, not Debezium/log-based CDC. The processor recomputes metrics for changed clients. The briefing flow scores each RM's portfolio, synthesizes flagged relationships, and saves the resulting digest.

## Technology

- Python 3.11+, `uv`
- Neon/Postgres for source data
- Apache Kafka for change events
- Upstash Redis for computed metrics
- Agno + Groq for LLM synthesis
- MongoDB Atlas, with a local JSON document-store fallback
- FastAPI and a single-page React frontend
- OpenTelemetry/Phoenix tracing (optional)

## Setup

From PowerShell at the repository root:

```powershell
uv sync
Copy-Item .env.example .env
```

Fill in `.env` with the credentials for Neon, Kafka, Upstash, and Groq. `ATLAS_URI` may be left empty to use local JSON storage. Set `GATEWAY_JWT_SECRET` to a private random secret before running the API.

Check the Phase 1/2 configuration, create the database schema, and load the sample data:

```powershell
uv run python scripts\verify_env.py
uv run python scripts\apply_schema.py
uv run python scripts\seed_postgres.py
```

The seed script currently creates **2 RMs, 5 synthetic clients, and 7 days of sample history**. It is demo data, not production banking data.

## Run the data pipeline

Run one poll/processing cycle in order:

```powershell
uv run python services\stream_processor\src\stream_processor\cdc_poller.py --once
uv run python services\stream_processor\src\stream_processor\processor.py --once
uv run python scripts\check_redis_metrics.py
```

To run continuously, start the poller and processor in separate terminals and omit `--once`.

## Generate briefings and start the app

Generate and store daily RM briefings:

```powershell
uv run python orchestration\flows\daily_briefing_flow.py
```

Start the API and serve the frontend at `http://localhost:8000`:

```powershell
uv run uvicorn gateway_api.serve:app --app-dir services\gateway_api\src --reload
```

The API includes RM login, latest briefing, relationship detail, and Copilot chat routes. The demo login accepts an RM ID without a password; this authentication is not suitable for production.

## Configuration and data safety

- `.env` contains credentials; do not commit it or paste secrets into logs.
- The sample database records are synthetic.
- Review authentication, authorization, sensitive workflow payloads, and data-retention requirements before any production use.
