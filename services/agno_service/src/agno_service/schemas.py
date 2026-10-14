"""Structured schemas for Morning Briefings, Relationship Digests, and Action Plans."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class SignalDetail(BaseModel):
    name: str = Field(..., description="Signal name, e.g. Operating Balance, Wire Activity")
    description: str = Field(..., description="E.g. Operating account balance has declined 37% over 60 days")
    metric_value: str = Field(..., description="Current value or percentage change, e.g. -37%")
    severity: Literal["HIGH", "MEDIUM", "LOW", "NEUTRAL"] = Field(
        ..., description="Signal risk severity"
    )


class ClientDigest(BaseModel):
    client_id: str
    client_name: str
    risk_level: Literal["HIGH", "MEDIUM", "LOW"]
    risk_score: float
    operating_balance: float
    deposits_at_risk: float
    signals: list[SignalDetail]
    agent_conclusion: str = Field(
        ...,
        description="E.g. High-value relationship with elevated deposit attrition risk.",
    )
    recommended_action: str = Field(
        ...,
        description="E.g. Contact the CFO before CD maturity. Discuss renewal/treasury alternatives...",
    )
    opportunities: list[str] = Field(
        default_factory=list,
        description="Growth/retention opportunities, e.g. Treasury Management, CD renewal",
    )


class MorningDigest(BaseModel):
    rm_id: str
    rm_name: str
    total_relationships: int
    flagged_count: int
    total_deposits_at_risk: float
    summary_headline: str = Field(
        ...,
        description="Executive summary line, e.g. Good morning, Sarah. 7 of your 184 relationships need attention today. $4.2M of deposits are potentially at risk.",
    )
    flagged_relationships: list[ClientDigest]
    generated_at: str
