
from __future__ import annotations

import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from rm_agent import upstash_client
from rm_agent.settings import RiskScoringSettings

logger = logging.getLogger(__name__)


@dataclass
class ClientRiskEvaluation:
    client_id: str
    rm_id: str
    risk_score: float
    is_flagged: bool
    risk_level: str  # HIGH, MEDIUM, LOW
    signals: dict[str, Any]
    metrics_snapshot: dict[str, Any]


def _sigmoid(x: float, center: float = 0.0, scale: float = 1.0) -> float:
    """Standard statistical logistic sigmoid mapping any real value to (0, 1)."""
    try:
        z = (x - center) / max(scale, 1e-6)
        # Clamp z to avoid math overflow
        z = max(min(z, 20.0), -20.0)
        return 1.0 / (1.0 + math.exp(-z))
    except (OverflowError, ZeroDivisionError):
        return 1.0 if x > center else 0.0


def _compute_entropy_weights(feature_matrix: np.ndarray) -> np.ndarray:
    """Dynamically calculates objective weights via the Information Entropy Method.

    Features with higher variance/dispersion across the portfolio provide more
    information about relative risk and receive proportionately higher weight.
    """
    n_samples, n_features = feature_matrix.shape
    if n_samples <= 1:
        # Uniform weight distribution if insufficient sample variance
        return np.ones(n_features) / n_features

    # Normalize matrix to column probabilities (sum to 1 per feature)
    col_sums = np.sum(feature_matrix, axis=0)
    col_sums[col_sums == 0] = 1.0
    p = feature_matrix / col_sums

    # Compute Shannon Entropy per feature: e = -1/ln(n) * sum(p * ln(p))
    k = 1.0 / math.log(max(n_samples, 2))
    p_safe = np.where(p > 0, p, 1.0)
    entropy = -k * np.sum(p * np.log(p_safe), axis=0)

    # Information utility d = 1 - entropy
    utility = 1.0 - entropy
    utility_sum = np.sum(utility)
    if utility_sum == 0:
        return np.ones(n_features) / n_features
    return utility / utility_sum


class RiskScoringEngine:
    def __init__(self, settings: RiskScoringSettings | None = None) -> None:
        self.settings = settings or RiskScoringSettings()
        self.redis = upstash_client

    def extract_feature_vector(self, metrics: dict[str, Any]) -> tuple[float, float, float, float]:
        """Maps raw client metrics to continuous statistical sub-scores in [0, 1] using standard sigmoid distributions."""
        # 1. Deposit decline: based on percentage change or z-score
        dep_60 = metrics.get("deposit_balance_60d_pct_change")
        dep_90 = metrics.get("deposit_balance_90d_pct_change")
        dep_z = metrics.get("deposit_zscore")

        if dep_60 is not None:
            # -30% drop sits at midpoint 0.5; steeper drop approaches 1.0
            dep_score = _sigmoid(-dep_60, center=20.0, scale=12.0)
        elif dep_90 is not None:
            dep_score = _sigmoid(-dep_90, center=25.0, scale=15.0)
        elif dep_z is not None:
            dep_score = _sigmoid(-dep_z, center=1.5, scale=0.8)
        else:
            dep_score = 0.0

        # 2. Wire decline
        wire_60 = metrics.get("wire_volume_60d_pct_change")
        wire_score = _sigmoid(-wire_60, center=25.0, scale=15.0) if wire_60 is not None else 0.0

        # 3. CRM contact staleness (days)
        contact_days = metrics.get("days_since_last_interaction")
        contact_score = _sigmoid(float(contact_days), center=45.0, scale=15.0) if contact_days is not None else 0.0

        # 4. CD maturity proximity (inversed: fewer days until maturity = higher risk)
        cd_days = metrics.get("cd_maturity_days")
        if cd_days is not None and cd_days > 0:
            cd_score = _sigmoid(-float(cd_days), center=-30.0, scale=15.0)
        else:
            cd_score = 0.0

        return dep_score, wire_score, contact_score, cd_score

    def evaluate_rm_portfolio(
        self, rm_id: str, client_ids: list[str]
    ) -> list[ClientRiskEvaluation]:
        """Evaluates all relationships in an RM's portfolio dynamically using statistical information entropy."""
        client_metrics: list[dict[str, Any]] = []
        raw_vectors: list[tuple[float, float, float, float]] = []

        for cid in client_ids:
            key = f"metrics:{rm_id}:{cid}"
            data = self.redis.get_json(key)
            if not data:
                continue
            client_metrics.append(data)
            raw_vectors.append(self.extract_feature_vector(data))

        if not client_metrics:
            return []

        # Convert to matrix of shape (n_clients, 4)
        X = np.array(raw_vectors, dtype=float)

        # Calculate dynamic information-theoretic weights across the portfolio
        weights = _compute_entropy_weights(X)

        # Composite score = dot product with dynamic weights
        composite_scores = np.dot(X, weights)
        composite_scores = np.round(np.clip(composite_scores, 0.0, 1.0), 3)

        # Dynamic portfolio percentile threshold (top 20% or outlier score)
        score_threshold = float(np.percentile(composite_scores, 75)) if len(composite_scores) >= 4 else 0.40

        evaluations: list[ClientRiskEvaluation] = []
        for i, metrics in enumerate(client_metrics):
            score = float(composite_scores[i])
            is_flagged = score >= score_threshold and score > 0.25

            if score >= 0.60:
                level = "HIGH"
            elif score >= 0.35:
                level = "MEDIUM"
            else:
                level = "LOW"

            signals = {
                "deposit_decline_score": round(float(X[i, 0]), 3),
                "wire_decline_score": round(float(X[i, 1]), 3),
                "contact_staleness_score": round(float(X[i, 2]), 3),
                "cd_maturity_score": round(float(X[i, 3]), 3),
                "deposit_pct_change": metrics.get("deposit_balance_60d_pct_change") or metrics.get("deposit_balance_90d_pct_change"),
                "deposit_zscore": metrics.get("deposit_zscore"),
                "wire_pct_change": metrics.get("wire_volume_60d_pct_change"),
                "days_since_contact": metrics.get("days_since_last_interaction"),
                "cd_maturity_days": metrics.get("cd_maturity_days"),
                "entropy_weight_vector": [round(float(w), 3) for w in weights],
            }

            evaluations.append(
                ClientRiskEvaluation(
                    client_id=metrics.get("client_id", ""),
                    rm_id=rm_id,
                    risk_score=score,
                    is_flagged=is_flagged,
                    risk_level=level,
                    signals=signals,
                    metrics_snapshot=metrics,
                )
            )

        evaluations.sort(key=lambda e: e.risk_score, reverse=True)
        return evaluations
