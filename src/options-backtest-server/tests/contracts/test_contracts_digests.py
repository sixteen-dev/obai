"""The vendored design contracts are byte-exact and cannot drift silently (ADR 0001 §6)."""

import hashlib
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = CONTRACTS_DIR.parents[3]
DESIGN_DIR = REPO_ROOT / "docs" / "design" / "options-backtesting-v3"
PACKAGED_SCHEMA = (
    CONTRACTS_DIR.parents[1] / "src" / "options_backtest" / "contracts" / "strategy.schema.json"
)
"""The copy the wheel ships and the capabilities payload returns (ADR 0003 §8, F1/F2/M2/M1)."""

PINNED_SHA256 = {
    "example-strategy.json": "72983b66c2c91540594505fc784d3baeeba2df36b488f4333f670705fad18ae1",
    "ledger-fixtures.json": "3482c8d6e61b4f2fa0b78dfa13c8cca28beb52c2b837204e122e2f3ad6bb1351",
    "strategy.schema.json": "385ee006868bf6fb9f5c06b5099412f381bc123f48edd71fece1eb58685a7b5e",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_sha256sums(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", maxsplit=1)
        entries[name] = digest
    return entries


def test_sha256sums_lists_exactly_the_pinned_digests() -> None:
    assert _read_sha256sums(CONTRACTS_DIR / "SHA256SUMS") == PINNED_SHA256


@pytest.mark.parametrize("name", sorted(PINNED_SHA256))
def test_vendored_contract_matches_pinned_digest(name: str) -> None:
    assert _sha256(CONTRACTS_DIR / name) == PINNED_SHA256[name]


@pytest.mark.parametrize("name", sorted(PINNED_SHA256))
def test_vendored_contract_matches_local_design_copy(name: str) -> None:
    if not DESIGN_DIR.is_dir():
        pytest.skip("docs/design/ is gitignored; the vendored copies are the versioned contract")
    assert (CONTRACTS_DIR / name).read_bytes() == (DESIGN_DIR / name).read_bytes()


def test_packaged_schema_is_the_vendored_contract() -> None:
    assert _sha256(PACKAGED_SCHEMA) == PINNED_SHA256["strategy.schema.json"]
