"""Crypto strategy artifact validation and export."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ..json_utils import canonical_json

LOAD_BEARING_FIELDS = {
    "strategy_spec",
    "symbols",
    "venues",
    "timeframe",
    "instrument_type",
    "backtest",
    "risk",
    "execution_compatibility",
    "execution_profile",
}


def export_artifact(
    *,
    job_id: str,
    strategy_spec: dict[str, Any],
    risk_config: dict[str, Any],
    execution_profile: dict[str, Any],
    backtest_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic Coinbase v1 strategy artifact."""
    data_config = backtest_result["data_config"]
    product_id = data_config["product_id"]
    artifact: dict[str, Any] = {
        "artifact_type": "CryptoStrategyArtifact",
        "version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "strategy_id": _strategy_id(product_id, strategy_spec, data_config),
        "human_name": f"{product_id} Coinbase Spot Strategy",
        "market": "crypto",
        "instrument_type": "spot",
        "symbols": [product_id],
        "venues": ["coinbase"],
        "timeframe": data_config["timeframe"],
        "strategy_spec": strategy_spec,
        "backtest": {
            "job_id": job_id,
            "start": data_config["start"],
            "end": data_config["end"],
            "data_sources": ["coinbase_advanced_trade_public_market"],
            "quality_status": backtest_result["quality_status"],
            "source_quality_fingerprint": backtest_result["source_quality_fingerprint"],
        },
        "risk": risk_config,
        "execution_profile": execution_profile,
        "execution_compatibility": {
            "paper_backends": ["internal_coinbase_paper_ledger"],
            "live_backends": [],
            "unsupported_reasons": [],
        },
    }
    artifact["fingerprint"] = artifact_fingerprint(artifact)
    return artifact


def artifact_fingerprint(artifact: dict[str, Any]) -> str:
    """SHA-256 over load-bearing artifact fields."""
    payload = {key: artifact.get(key) for key in sorted(LOAD_BEARING_FIELDS)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def validate_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Validate artifact shape and fingerprint."""
    errors: list[str] = []
    if artifact.get("artifact_type") != "CryptoStrategyArtifact":
        errors.append("artifact_type must be CryptoStrategyArtifact")
    if artifact.get("venues") != ["coinbase"]:
        errors.append("venues must be ['coinbase'] for v1")
    if artifact.get("instrument_type") != "spot":
        errors.append("instrument_type must be spot for v1")
    backtest = artifact.get("backtest") or {}
    if backtest.get("quality_status") != "execution_grade":
        errors.append("backtest.quality_status must be execution_grade")
    compatibility = artifact.get("execution_compatibility") or {}
    if "internal_coinbase_paper_ledger" not in compatibility.get("paper_backends", []):
        errors.append("internal_coinbase_paper_ledger paper backend is required")
    fingerprint = artifact.get("fingerprint")
    expected = artifact_fingerprint(artifact)
    if fingerprint != expected:
        errors.append("fingerprint does not match load-bearing fields")
    return {"valid": not errors, "errors": errors, "expected_fingerprint": expected}


def _strategy_id(
    product_id: str,
    strategy_spec: dict[str, Any],
    data_config: dict[str, Any],
) -> str:
    template = str(strategy_spec.get("template") or "strategy").lower()
    timeframe = str(data_config.get("timeframe") or "tf").lower()
    return f"{product_id.lower().replace('-', '_')}_coinbase_{timeframe}_{template}_v1"
