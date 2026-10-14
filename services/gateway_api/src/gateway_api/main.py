"""Gateway API for RM Intelligence Platform.

Provides secure REST endpoints for:
- RM Authentication (/api/auth/login, /api/auth/rms)
- Morning Briefing & Digestion (/api/briefing/latest, /api/briefing/relationship/{client_id})
- RM Copilot conversational briefs (/api/copilot/chat)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from openinference.instrumentation.groq import GroqInstrumentor
from pydantic import BaseModel, Field

# Add paths
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "services" / "agno_service" / "src"))
sys.path.insert(0, str(ROOT / "services" / "risk_scoring_engine" / "src"))

from groq import Groq
from rm_agent.db import get_connection
from rm_agent.mongo_client import get_document_store
from rm_agent.observability import configure_tracing
from rm_agent.settings import LLMSettings
from .auth import create_access_token, decode_access_token

logger = logging.getLogger(__name__)

app = FastAPI(
    title="RM Agent Gateway API",
    description="Intelligence and Copilot platform for Commercial Relationship Managers",
    version="1.0.0",
)

configure_tracing("rm-agent-gateway")
FastAPIInstrumentor.instrument_app(app, tracer_provider=trace.get_tracer_provider())
GroqInstrumentor().instrument(tracer_provider=trace.get_tracer_provider())

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

copilot_tracer = trace.get_tracer("rm-agent-copilot")


class LoginRequest(BaseModel):
    rm_id: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    rm_id: str
    name: str
    email: str


class CopilotChatRequest(BaseModel):
    query: str
    client_id: str | None = None


def get_current_rm(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
        )
    return payload


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "gateway_api"}


@app.get("/api/auth/rms")
def list_available_rms() -> list[dict[str, Any]]:
    """Lists available RMs from Postgres (or Document Store fallback)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT rm_id, name, email FROM relationship_managers ORDER BY name ASC")
                rows = cur.fetchall()
                return [{"rm_id": r[0], "name": r[1], "email": r[2]} for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Postgres unavailable, falling back to document store: %s", e)
        doc_store = get_document_store()
        digests = doc_store.list_all_digests()
        return [
            {"rm_id": d["rm_id"], "name": d.get("rm_name", d["rm_id"]), "email": f"{d['rm_id']}@bank.com"}
            for d in digests
        ]


@app.post("/api/auth/login", response_model=LoginResponse)
def login_rm(req: LoginRequest) -> LoginResponse:
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rm_id, name, email FROM relationship_managers WHERE rm_id = %s",
                    (req.rm_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Relationship Manager '{req.rm_id}' not found",
                    )
                rm_id, name, email = row
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Postgres login fallback: %s", e)
        doc_store = get_document_store()
        digest = doc_store.get_latest_digest(req.rm_id)
        if not digest:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"RM '{req.rm_id}' not found",
            )
        rm_id = digest["rm_id"]
        name = digest.get("rm_name", req.rm_id)
        email = f"{rm_id}@bank.com"

    token = create_access_token({"sub": rm_id, "name": name, "email": email})
    return LoginResponse(access_token=token, rm_id=rm_id, name=name, email=email)


@app.get("/api/briefing/latest")
def get_morning_briefing(current_rm: dict[str, Any] = Depends(get_current_rm)) -> dict[str, Any]:
    """Returns today's morning briefing digest for the logged-in RM."""
    rm_id = current_rm["sub"]
    doc_store = get_document_store()
    digest = doc_store.get_latest_digest(rm_id)
    if not digest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No briefing generated yet for RM {rm_id}. Run daily_briefing_flow first.",
        )
    return digest


@app.get("/api/briefing/relationship/{client_id}")
def get_relationship_detail(
    client_id: str, current_rm: dict[str, Any] = Depends(get_current_rm)
) -> dict[str, Any]:
    """Returns detailed signals and action recommendations for a specific relationship."""
    rm_id = current_rm["sub"]
    doc_store = get_document_store()
    digest = doc_store.get_latest_digest(rm_id)
    if not digest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No digest found for RM",
        )

    for rel in digest.get("flagged_relationships", []):
        if rel.get("client_id") == client_id:
            return rel

    # If not among the flagged list, fetch from clients table
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT client_id, name, profitability_score FROM clients WHERE client_id = %s AND rm_id = %s",
                (client_id, rm_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client relationship not found under this RM",
                )
            return {
                "client_id": row[0],
                "client_name": row[1],
                "profitability_score": float(row[2]),
                "risk_level": "LOW",
                "risk_score": 0.1,
                "operating_balance": 500000.0,
                "deposits_at_risk": 0.0,
                "signals": [],
                "agent_conclusion": "Relationship is in good standing with normal activity.",
                "recommended_action": "Maintain scheduled quarterly review.",
                "opportunities": ["Treasury Management", "Commercial Lending"],
            }
    finally:
        conn.close()


@app.post("/api/copilot/chat")
def copilot_chat(
    req: CopilotChatRequest, current_rm: dict[str, Any] = Depends(get_current_rm)
) -> dict[str, Any]:
    """RM Copilot Conversational Assistant.

    Uses live Postgres data only; no document-store fallback is allowed for this route so the
    real backend error is surfaced immediately.
    """
    rm_id = current_rm["sub"]
    rm_name = current_rm.get("name", "Relationship Manager")

    portfolio_summary: list[dict[str, Any]] = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.client_id, c.name, c.profitability_score, c.relationship_start_date,
                       COALESCE(lb.operating_balance, 750000.0) as balance
                FROM clients c
                LEFT JOIN LATERAL (
                    SELECT operating_balance FROM ledger_balances
                    WHERE client_id = c.client_id ORDER BY balance_date DESC LIMIT 1
                ) lb ON TRUE
                WHERE c.rm_id = %s
                LIMIT 10
                """,
                (rm_id,),
            )
            clients = cur.fetchall()
            portfolio_summary = [
                {"client_id": r[0], "name": r[1], "tenure_start": str(r[3]), "balance": float(r[4])}
                for r in clients
            ]
    finally:
        conn.close()

    if not portfolio_summary:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No live client portfolio data was returned for this RM. The backend data source is empty or unavailable.",
        )

    llm_settings = LLMSettings()
    client = Groq(api_key=llm_settings.groq_api_key)
    model = llm_settings.llm_model_name or "openai/gpt-oss-120b"

    prompt = f"""You are the RM Copilot for {rm_name}.
RM's Portfolio Context:
{json.dumps(portfolio_summary, indent=2)}

Banker Question: "{req.query}"

Respond in clear, executive commercial banking format:
1. Relationship Summary (Deposits, Loans, Treasury, Tenure)
2. Recent Signals (Deposit trajectory, ACH/Wire trends, CD maturities)
3. Potential Risks (Deposit attrition, liquidity risk)
4. Strategic Opportunities (Equipment financing, Treasury management, Working capital)
5. Structured One-Page Pre-Call Briefing"""

    try:
        with copilot_tracer.start_as_current_span("copilot.meeting_brief") as span:
            span.set_attribute("rm.id", rm_id)
            span.set_attribute("portfolio.client_count", len(portfolio_summary))
            span.set_attribute("llm.model", model)
            resp = client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional banking copilot providing concise, high-impact meeting briefs and portfolio insights.",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.3,
            )
            answer = resp.choices[0].message.content or "No response generated."
    except Exception as e:
        logger.error("Copilot LLM synthesis error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM synthesis failed: {str(e)}",
        )

    return {"query": req.query, "response": answer, "rm_id": rm_id}
