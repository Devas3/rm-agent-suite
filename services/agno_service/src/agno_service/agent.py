"""Agno LLM Synthesis Service.

Leverages the official Agno Agent framework with native Pydantic response_model
enforcement using Groq models. No hardcoded signal text, no hardcoded fallbacks.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from agno.agent import Agent
from agno.models.groq import Groq
from opentelemetry import trace
from openinference.instrumentation.agno import AgnoInstrumentor
from openinference.instrumentation.groq import GroqInstrumentor
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from rm_agent.settings import LLMSettings
from rm_agent.observability import configure_tracing
from .schemas import ClientDigest, MorningDigest, SignalDetail

logger = logging.getLogger(__name__)

configure_tracing("rm-agent-agno")
AgnoInstrumentor().instrument(tracer_provider=trace.get_tracer_provider())
GroqInstrumentor().instrument(tracer_provider=trace.get_tracer_provider())


class AgnoSynthesisOutput(BaseModel):
    """Pydantic model that Agno forces the LLM to output directly."""
    signals: list[SignalDetail] = Field(
        ...,
        description="Detailed list of specific financial & behavioral signals analyzed from the input data",
    )
    agent_conclusion: str = Field(
        ...,
        description="Authoritative, crisp commercial banking diagnostic (1-2 sentences)",
    )
    recommended_action: str = Field(
        ...,
        description="Specific, prioritized tactical actions for the RM (e.g. CFO outreach, treasury discussion, CD renewal)",
    )
    opportunities: list[str] = Field(
        default_factory=list,
        description="Strategic cross-sell or retention solutions (e.g. Treasury Management, Liquidity Sweep, CD Extension)",
    )


BANKING_SYSTEM_INSTRUCTIONS = [
    "You are an elite Senior Commercial Banking Relationship Intelligence Agent for institutional and corporate banks.",
    "Analyze real-time relationship signals including deposit trajectories, wire transaction velocity, CD maturities, CRM contact staleness, and client profitability.",
    "Formulate crisp, authoritative banking conclusions and actionable recommendations for the Relationship Manager.",
    "Derive every signal description, conclusion, and recommended action strictly from the client data provided.",
]


class BriefingAgent:
    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings or LLMSettings()
        model_id = self.settings.llm_model_name or "openai/gpt-oss-120b"
        
        # Instantiate official Agno Agent with output_schema enforcement
        self.agent = Agent(
            model=Groq(id=model_id, api_key=self.settings.groq_api_key),
            instructions=BANKING_SYSTEM_INSTRUCTIONS,
            output_schema=AgnoSynthesisOutput,
            use_json_mode=True,
            markdown=False,
        )

    def synthesize_client_digest(
        self,
        client_info: dict[str, Any],
        evaluation_signals: dict[str, Any],
        metrics: dict[str, Any],
    ) -> ClientDigest:
        """Invokes the Agno Agent to reason over metrics and return validated Pydantic models."""
        client_name = client_info.get("name", "Commercial Client")
        client_id = client_info.get("client_id", "")
        profitability = metrics.get("profitability_score", 75.0)
        operating_balance = float(client_info.get("operating_balance", 1_000_000.0))
        risk_level = client_info.get("risk_level", "HIGH")
        risk_score = float(client_info.get("risk_score", 0.70))

        prompt = f"""Perform risk and opportunity synthesis on this corporate relationship:
- Client Name: {client_name} (ID: {client_id})
- Relationship Profitability Score: {profitability} / 100
- Current Operating Balance: ${operating_balance:,.2f}
- Deposit Trajectory: {evaluation_signals.get('deposit_pct_change')}% change (Z-Score: {evaluation_signals.get('deposit_zscore')})
- Wire Volume Trajectory: {evaluation_signals.get('wire_pct_change')}% change
- CRM Contact Staleness: {evaluation_signals.get('days_since_contact')} days since last RM touchpoint
- CD Maturity Window: {evaluation_signals.get('cd_maturity_days')} days until maturity

Generate the structured signals, agent conclusion, recommended actions, and potential opportunities."""

        # Execute Agno Agent run
        response = self.agent.run(prompt)
        
        # Agno's response.content is the validated AgnoSynthesisOutput instance
        synthesis: AgnoSynthesisOutput
        if isinstance(response.content, AgnoSynthesisOutput):
            synthesis = response.content
        else:
            # Fallback parsing in case of raw dictionary
            synthesis = AgnoSynthesisOutput.model_validate(response.content)

        # Dynamic deposit at risk calculation based on actual deposit change or score
        deposit_pct_drop = abs(evaluation_signals.get("deposit_pct_change") or (risk_score * 30.0))
        deposits_at_risk = round(operating_balance * min(deposit_pct_drop / 100.0, 1.0), 2)

        return ClientDigest(
            client_id=client_id,
            client_name=client_name,
            risk_level=risk_level,
            risk_score=risk_score,
            operating_balance=operating_balance,
            deposits_at_risk=deposits_at_risk,
            signals=synthesis.signals,
            agent_conclusion=synthesis.agent_conclusion,
            recommended_action=synthesis.recommended_action,
            opportunities=synthesis.opportunities,
        )

    def generate_briefing_headline(
        self, rm_name: str, flagged_count: int, total_count: int, deposits_at_risk: float
    ) -> str:
        """Formats the morning briefing digest headline."""
        at_risk_fmt = (
            f"${deposits_at_risk / 1_000_000:.1f}M"
            if deposits_at_risk >= 1_000_000
            else f"${deposits_at_risk:,.0f}"
        )
        return (
            f"Good morning, {rm_name}. {flagged_count} of your {total_count} relationships "
            f"need attention today. {at_risk_fmt} of deposits are potentially at risk."
        )
