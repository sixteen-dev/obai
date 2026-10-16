"""Massive symbol forms for index options and single-contract snapshots.

Verified live against Massive on 2026-10-15: the chain and contract
snapshots return Greeks, IV and the underlying value for index options only
under the ``I:`` underlying (``I:XSP``), while the contracts reference list
returns nothing for ``I:XSP`` and needs the bare root. The single-contract
snapshot answers "Options contract not found" unless the contract keeps its
``O:`` prefix, for every underlying.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.massive_client import MassiveClient, massive_underlying
from src.config import Settings


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


class TestMassiveUnderlying:
    """Index roots map to Massive's ``I:`` form; everything else is untouched."""

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("XSP", "I:XSP"),
            ("spx", "I:SPX"),
            ("NDX", "I:NDX"),
            ("RUT", "I:RUT"),
            ("VIX", "I:VIX"),
            ("I:XSP", "I:XSP"),
            ("AAPL", "AAPL"),
            ("SPY", "SPY"),
        ],
    )
    def test_maps_index_roots_only(self, symbol: str, expected: str) -> None:
        assert massive_underlying(symbol) == expected

    def test_rejects_an_empty_symbol(self) -> None:
        with pytest.raises(ValueError, match="underlying"):
            massive_underlying("  ")


class TestIndexEndpoints:
    """Each endpoint gets the symbol form Massive actually answers."""

    @pytest.mark.asyncio
    async def test_chain_snapshot_uses_the_index_form(self, mock_settings: Settings) -> None:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _response({"results": []})
            async with MassiveClient(mock_settings) as client:
                await client.get_option_chain_snapshot("XSP", contract_type="put")

        assert mock_get.call_args.args[0].endswith("/v3/snapshot/options/I:XSP")

    @pytest.mark.asyncio
    async def test_chain_snapshot_sends_the_strike_range(self, mock_settings: Settings) -> None:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _response({"results": []})
            async with MassiveClient(mock_settings) as client:
                await client.get_option_chain_snapshot("XSP", strike_min=740.0, strike_max=780.0)

        params = mock_get.call_args.kwargs["params"]
        assert params["strike_price.gte"] == 740.0
        assert params["strike_price.lte"] == 780.0

    @pytest.mark.asyncio
    async def test_chain_snapshot_rejects_an_inverted_strike_range(
        self, mock_settings: Settings
    ) -> None:
        async with MassiveClient(mock_settings) as client:
            with pytest.raises(ValueError, match="strike_min"):
                await client.get_option_chain_snapshot("XSP", strike_min=780.0, strike_max=740.0)

    @pytest.mark.asyncio
    async def test_contract_list_uses_the_bare_root(self, mock_settings: Settings) -> None:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _response({"results": []})
            async with MassiveClient(mock_settings) as client:
                await client.list_option_contracts(underlying_ticker="I:XSP")

        assert mock_get.call_args.kwargs["params"]["underlying_ticker"] == "XSP"


class TestContractSnapshotPath:
    """The single-contract snapshot keeps the ``O:`` prefix in its path."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("underlying", "contract", "path"),
        [
            ("AAPL", "O:AAPL261120P00200000", "/v3/snapshot/options/AAPL/O:AAPL261120P00200000"),
            ("AAPL", "AAPL261120P00200000", "/v3/snapshot/options/AAPL/O:AAPL261120P00200000"),
            ("XSP", "O:XSP261026P00754000", "/v3/snapshot/options/I:XSP/O:XSP261026P00754000"),
        ],
    )
    async def test_path_keeps_the_contract_prefix(
        self, mock_settings: Settings, underlying: str, contract: str, path: str
    ) -> None:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _response({"results": {}})
            async with MassiveClient(mock_settings) as client:
                await client.get_option_contract_snapshot(underlying, contract)

        assert mock_get.call_args.args[0].endswith(path)
