"""Money-type misuse that mypy must reject; read only by ``test_money_type_safety.py``.

Never imported or executed. mypy must report exactly the error code named by each
``# expect: <code>`` comment and nothing else anywhere in this file, so ``well_typed``
is the control that the supported operations still type-check.
"""

from decimal import Decimal

from options_backtest.models.market import ContractTerms
from options_backtest.money import Price, Usd


def well_typed(usd: Usd, price: Price, terms: ContractTerms) -> list[object]:
    """Supported operations."""
    return [
        usd + usd,
        usd - usd,
        -usd,
        usd.scaled_by(3),
        usd < -usd,
        usd.is_cents(),
        Price.mid(price, price),
        Price.mid(price, price) < price,
        terms.premium_usd(price, -2),
        terms.deliverable.value_usd({"SPX": price}),
        terms.intrinsic_usd({"SPX": price}),
    ]


def money_misuse(usd: Usd, price: Price, factor: Decimal) -> list[object]:
    """Price and money never mix, and money never multiplies by a fraction in WP1."""
    return [
        usd + price,  # expect: operator
        price + usd,  # expect: operator
        usd - price,  # expect: operator
        price + price,  # expect: operator
        usd + factor,  # expect: operator
        usd * factor,  # expect: operator
        factor * usd,  # expect: operator
        price * factor,  # expect: operator
        factor * price,  # expect: operator
        usd < price,  # expect: operator
        Usd(1.5),  # expect: arg-type
        Usd(price),  # expect: arg-type
        Price(1),  # expect: arg-type
        usd.scaled_by(factor),  # expect: arg-type
    ]


def market_misuse(usd: Usd, price: Price, factor: Decimal, terms: ContractTerms) -> list[object]:
    """Only prices convert to money, and only through contract terms and deliverables."""
    decimal_prices: dict[str, Decimal] = {"SPX": factor}
    usd_prices: dict[str, Usd] = {"SPX": usd}
    return [
        terms.premium_usd(usd, 1),  # expect: arg-type
        terms.premium_usd(price, factor),  # expect: arg-type
        terms.deliverable.value_usd(decimal_prices),  # expect: arg-type
        terms.intrinsic_usd(usd_prices),  # expect: arg-type
    ]
