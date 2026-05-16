"""Generate a first-pass OBaI knowledge-base corpus from acquired sources.

This is a deterministic bootstrapper, not a substitute for human review. It
turns the OpenAP `SignalDoc.csv` catalog into conservative strategy entries and
adds a small manually curated concept glossary so the indexer and lookup path
have real corpus material to validate.
"""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
OPENAP_SIGNAL_DOC = ROOT / "sources" / "openap" / "SignalDoc.csv"
CORPUS_ROOT = ROOT / "corpus"
DRAFTS_ROOT = CORPUS_ROOT / "_drafts"


def slugify(value: str) -> str:
    value = value.strip()
    value = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_").lower()
    return value or "unnamed"


def clean(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.replace("\n", " ").split())


def parse_float(value: str) -> float | None:
    try:
        if value.strip() == "":
            return None
        return float(value)
    except ValueError:
        return None


def yaml_block(data: dict[str, Any]) -> str:
    dumped = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n"


def openap_category(row: dict[str, str]) -> str:
    economic = clean(row.get("Cat.Economic")).casefold()
    data = clean(row.get("Cat.Data")).casefold()
    long_description = clean(row.get("LongDescription")).casefold()
    text = f"{economic} {data} {long_description}"

    if "momentum" in text or "lead lag" in text or "reversal" in text:
        return "momentum"
    if "valuation" in text or "book-to-market" in text or "value" in text:
        return "quality"
    if "profit" in text or "accrual" in text or "earnings" in text or "r&d" in text:
        return "quality"
    if "liquidity" in text or "volume" in text or "trading" in text:
        return "microstructure"
    if "risk" in text or "volatility" in text or data == "options":
        return "low_volatility"
    if "size" in text or "market equity" in text:
        return "size"
    if "event" in data:
        return "event_driven"
    return "other"


def signal_direction(row: dict[str, str]) -> str:
    sign = parse_float(row.get("Sign", ""))
    if sign is None:
        return "rank stocks by the signal and form the source-defined long-short spread"
    if sign >= 0:
        return "long high-signal stocks and short low-signal stocks"
    return "long low-signal stocks and short high-signal stocks"


def holding_period(row: dict[str, str]) -> str:
    period = parse_float(row.get("Portfolio Period", ""))
    if period is not None and period <= 1:
        return "monthly"
    if period is not None and period <= 3:
        return "quarterly"
    return "monthly"


def source_paper(row: dict[str, str]) -> dict[str, Any]:
    authors = [part.strip() for part in clean(row.get("Authors")).split(" and ") if part.strip()]
    if not authors and clean(row.get("Authors")):
        authors = [clean(row.get("Authors"))]
    year = parse_float(row.get("Year", ""))
    return {
        "title": clean(row.get("LongDescription")) or clean(row.get("Acronym")),
        "authors": authors,
        "year": int(year) if year is not None else None,
        "venue": clean(row.get("Journal")),
        "url": "https://www.openassetpricing.com/data/",
    }


def openap_entry(row: dict[str, str]) -> tuple[str, str]:
    acronym = clean(row.get("Acronym"))
    acronym2 = clean(row.get("Acronym2"))
    description = clean(row.get("LongDescription")) or acronym
    entry_id = f"openap_{slugify(acronym)}"
    category = openap_category(row)
    direction = signal_direction(row)
    detailed_definition = clean(row.get("Detailed Definition"))
    evidence = clean(row.get("Evidence Summary"))
    notes = clean(row.get("Notes"))
    t_stat = clean(row.get("T-Stat"))
    return_value = clean(row.get("Return"))
    data_type = clean(row.get("Cat.Data"))
    economic_type = clean(row.get("Cat.Economic"))

    aliases = [value for value in [acronym, acronym2, description] if value]
    frontmatter: dict[str, Any] = {
        "entry_type": "strategy",
        "id": entry_id,
        "canonical_name": description,
        "aliases": sorted(set(aliases)),
        "one_line": f"Cross-sectional equity anomaly that uses {description} to {direction}.",
        "category": category,
        "asset_classes": ["equities"],
        "typical_holding_period": holding_period(row),
        "engine_fit": "approximate",
        "approximation_notes": (
            "OpenAP signals require dynamic cross-sectional ranking and portfolio formation. "
            "Current OBaI backtests can only approximate this with a fixed universe, "
            "screening, or per-symbol proxy rules; do not treat the result as a verbatim "
            "OpenAP replication."
        ),
        "signal_inputs": [
            f"OpenAP {data_type or 'source'} data",
            "broad equity universe",
            "monthly portfolio construction data",
        ],
        "known_failure_modes": [
            notes or "OpenAP notes should be reviewed before production use.",
            (
                f"Original-paper replication evidence: {evidence or 'not provided'}; "
                f"reported long-short return={return_value or 'n/a'}, t-stat={t_stat or 'n/a'}."
            ),
            "Performance can decay after publication and is sensitive to reporting lags, liquidity filters, and transaction costs.",
        ],
        "when_to_consider": (
            f"Broad equity universes where {data_type or 'the required'} data is available, "
            f"with a monthly rebalance workflow and a desire to test {economic_type or 'cross-sectional'} effects."
        ),
        "when_to_avoid": (
            "Avoid narrow universes, stale or restated data, unavailable source fields, "
            "and implementations that cannot respect source-specific lags or filters."
        ),
        "seminal_papers": [source_paper(row)],
    }

    body = [
        yaml_block(frontmatter),
        "## Thesis\n",
        (
            f"{description} is represented in the OpenAP signal catalog as a "
            f"{economic_type or 'cross-sectional'} predictor. The corpus entry is a "
            "source-derived reference for strategy discovery, not a claim that the "
            "current backtest engine can reproduce the OpenAP portfolio exactly.\n"
        ),
        "\n## Signal intuition\n",
        (
            f"The signal definition is: {detailed_definition or 'see the OpenAP source row for details.'} "
            f"The source direction is to {direction}.\n"
        ),
        "\n## Construction sketch\n",
        "```text\n",
        "for each rebalance date:\n",
        f"    compute {acronym} for every eligible stock using OpenAP data rules\n",
        "    rank the eligible universe cross-sectionally\n",
        f"    {direction}\n",
        "    rebalance on the source-defined monthly schedule\n",
        "```\n",
        "\n## Notes\n",
        (
            f"Source row: acronym={acronym}; category={economic_type or 'n/a'}; "
            f"data={data_type or 'n/a'}; evidence={evidence or 'n/a'}. "
            "Review the generated entry before using it as a final public corpus item.\n"
        ),
    ]
    return category, "".join(body)


CONCEPTS: list[dict[str, Any]] = [
    {
        "id": "contango",
        "canonical_name": "Contango",
        "aliases": ["upward-sloping futures curve", "positive carry term structure"],
        "category": "regimes",
        "definition": "A futures curve state where later-dated contracts trade above near-dated contracts.",
        "when_it_matters": "Long futures holders can lose roll yield; short front-month or calendar-spread strategies may harvest the carry if risk controls survive stress reversals.",
        "notes": "Common in storable commodities and often in VIX futures outside stress periods.",
    },
    {
        "id": "backwardation",
        "canonical_name": "Backwardation",
        "aliases": ["inverted futures curve", "negative carry term structure"],
        "category": "regimes",
        "definition": "A futures curve state where near-dated contracts trade above later-dated contracts.",
        "when_it_matters": "Can indicate scarcity or market stress; long futures may benefit from positive roll yield while short-carry trades can be exposed to squeezes.",
        "notes": "Backwardation is the opposite of contango and is often regime-dependent.",
    },
    {
        "id": "variance_risk_premium",
        "canonical_name": "Variance Risk Premium",
        "aliases": ["VRP", "volatility risk premium"],
        "category": "factors",
        "definition": "The tendency for implied variance to exceed subsequently realized variance over many samples.",
        "when_it_matters": "It underpins option-selling, buywrite, putwrite, and volatility carry strategies; losses concentrate when realized volatility jumps.",
        "notes": "Cboe buywrite and putwrite methodology documents are relevant sources for systematic implementations.",
    },
    {
        "id": "funding_rate",
        "canonical_name": "Funding Rate",
        "aliases": ["perp funding", "perpetual swap funding"],
        "category": "mechanics",
        "definition": "A recurring payment between long and short perpetual-swap holders intended to anchor the perp near spot.",
        "when_it_matters": "Persistent positive or negative funding can create basis, carry, and hedged spot-perp trades, but liquidation and venue risk dominate naive yield calculations.",
        "notes": "Funding rate mechanics appear in the acquired perpetual-futures papers and crypto-native research sources.",
    },
    {
        "id": "perpetual_swap",
        "canonical_name": "Perpetual Swap",
        "aliases": ["perp", "perpetual future"],
        "category": "instruments",
        "definition": "A derivative contract with futures-like exposure and no fixed maturity, typically anchored by funding payments.",
        "when_it_matters": "Perps are central to crypto basis, funding carry, liquidation, and market-making strategies.",
        "notes": "The acquired SSRN/arXiv perpetual-futures sources cover pricing and arbitrage bounds.",
    },
    {
        "id": "spot_perp_basis",
        "canonical_name": "Spot-Perp Basis",
        "aliases": ["spot perpetual basis", "perp basis"],
        "category": "mechanics",
        "definition": "The price difference between a spot asset and its perpetual-swap contract.",
        "when_it_matters": "A persistent basis can motivate market-neutral spot-perp trades, but borrow, margin, liquidation, funding, and exchange risk can overwhelm the spread.",
        "notes": "Use with funding-rate and perpetual-swap entries.",
    },
    {
        "id": "factor_crowding",
        "canonical_name": "Factor Crowding",
        "aliases": ["crowded factor", "crowded trade"],
        "category": "regimes",
        "definition": "A condition where many portfolios hold similar factor exposures, increasing unwind and crash risk.",
        "when_it_matters": "Momentum, value, low-volatility, and quality strategies can all suffer when crowded positions unwind simultaneously.",
        "notes": "Crowding is a risk lens rather than a standalone trade rule.",
    },
    {
        "id": "low_dispersion",
        "canonical_name": "Low Dispersion",
        "aliases": ["low cross-sectional dispersion"],
        "category": "regimes",
        "definition": "A market state where return differences across securities are compressed.",
        "when_it_matters": "Cross-sectional strategies can struggle when spreads between winners and losers are too small to overcome costs and noise.",
        "notes": "Relevant to OpenAP-style anomaly portfolios.",
    },
    {
        "id": "skew",
        "canonical_name": "Option Skew",
        "aliases": ["volatility skew", "implied volatility skew"],
        "category": "mechanics",
        "definition": "The pattern of implied volatility differing by strike, often with downside puts richer than upside calls.",
        "when_it_matters": "Skew affects collars, put spreads, covered calls, and downside-hedging cost; it can make visually similar option structures have very different risk-reward.",
        "notes": "Cboe collar and put-protection sources are relevant references.",
    },
    {
        "id": "term_structure",
        "canonical_name": "Term Structure",
        "aliases": ["curve shape", "maturity curve"],
        "category": "mechanics",
        "definition": "The relationship between contract prices, yields, or implied volatilities across maturities.",
        "when_it_matters": "Term structure drives futures roll yield, calendar spreads, VIX products, and option maturity selection.",
        "notes": "Use with contango and backwardation entries.",
    },
    {
        "id": "basis",
        "canonical_name": "Basis",
        "aliases": ["cash-futures basis", "spread to spot"],
        "category": "mechanics",
        "definition": "The price difference between a derivative or forward contract and the related spot instrument.",
        "when_it_matters": "Basis drives futures arbitrage, hedging slippage, spot-perp trades, and calendar-spread profitability.",
        "notes": "Basis should be evaluated net of financing, borrow, carry, execution, and settlement risk.",
    },
    {
        "id": "roll_yield",
        "canonical_name": "Roll Yield",
        "aliases": ["futures roll return", "roll return"],
        "category": "mechanics",
        "definition": "The return component from moving exposure from one contract maturity to another as futures contracts age.",
        "when_it_matters": "Roll yield is central to commodity, VIX, and futures-based carry strategies.",
        "notes": "Positive roll yield is not free return; it compensates for inventory, scarcity, volatility, and crash risks.",
    },
    {
        "id": "carry",
        "canonical_name": "Carry",
        "aliases": ["income carry", "risk premium carry"],
        "category": "factors",
        "definition": "Expected return from holding an asset or spread when prices do not move, after financing and income flows.",
        "when_it_matters": "Carry strategies can look stable for long periods and then suffer large losses during funding or volatility shocks.",
        "notes": "Carry must be measured net of financing, margin, transaction costs, and tail exposure.",
    },
    {
        "id": "buywrite",
        "canonical_name": "BuyWrite",
        "aliases": ["covered call overwrite", "covered call index"],
        "category": "instruments",
        "definition": "A strategy that holds an underlying asset while selling call options against the position.",
        "when_it_matters": "Buywrite strategies monetize option premium but cap upside and remain exposed to downside moves in the underlying.",
        "notes": "Cboe BXM and daily buywrite methodology documents are relevant acquired sources.",
    },
    {
        "id": "putwrite",
        "canonical_name": "PutWrite",
        "aliases": ["cash-secured put selling", "systematic put writing"],
        "category": "instruments",
        "definition": "A strategy that systematically sells put options, often collateralized by cash or Treasury bills.",
        "when_it_matters": "Putwrite strategies harvest option premium but can suffer large drawdowns when equity markets fall sharply.",
        "notes": "Cboe PUT methodology documents are relevant acquired sources.",
    },
    {
        "id": "collar",
        "canonical_name": "Collar",
        "aliases": ["protective collar", "zero-cost collar"],
        "category": "instruments",
        "definition": "An options structure that typically owns the underlying, buys a protective put, and sells a call to finance protection.",
        "when_it_matters": "Collars trade upside participation for downside protection and are sensitive to skew and option maturity selection.",
        "notes": "Cboe collar methodology documents are relevant acquired sources.",
    },
    {
        "id": "quality_factor",
        "canonical_name": "Quality Factor",
        "aliases": ["profitability factor", "quality investing"],
        "category": "factors",
        "definition": "An equity factor family that favors firms with stronger profitability, balance sheets, earnings quality, or operational stability.",
        "when_it_matters": "Quality can complement value and momentum but may become expensive or crowded in defensive regimes.",
        "notes": "Many OpenAP accounting and profitability signals map into this family.",
    },
    {
        "id": "value_factor",
        "canonical_name": "Value Factor",
        "aliases": ["cheapness factor", "valuation factor"],
        "category": "factors",
        "definition": "An equity factor family that favors securities trading cheaply relative to fundamentals or assets.",
        "when_it_matters": "Value strategies can underperform during growth-led markets, accounting regime changes, or when cheapness reflects distress.",
        "notes": "OpenAP valuation signals and Fama-French factors are relevant acquired sources.",
    },
    {
        "id": "momentum_crash",
        "canonical_name": "Momentum Crash",
        "aliases": ["momentum unwind", "winner-loser reversal"],
        "category": "regimes",
        "definition": "A sharp reversal regime where recent winners underperform and recent losers rebound, hurting momentum portfolios.",
        "when_it_matters": "Momentum crashes often appear around market rebounds and stress recoveries, when high-beta losers rally abruptly.",
        "notes": "Momentum entries should treat this as a core failure mode.",
    },
    {
        "id": "liquidity",
        "canonical_name": "Liquidity",
        "aliases": ["market liquidity", "trading liquidity"],
        "category": "mechanics",
        "definition": "The ability to trade an instrument in size without materially moving price or incurring excessive costs.",
        "when_it_matters": "Liquidity controls whether a signal can survive implementation; illiquid anomalies can disappear after realistic costs.",
        "notes": "OpenAP liquidity and microstructure signals should be reviewed with cost assumptions.",
    },
    {
        "id": "bid_ask_spread",
        "canonical_name": "Bid-Ask Spread",
        "aliases": ["quoted spread", "transaction spread"],
        "category": "mechanics",
        "definition": "The difference between the best available bid and ask prices for an instrument.",
        "when_it_matters": "Spreads are a direct trading cost and can dominate high-turnover or microstructure strategies.",
        "notes": "Spread-sensitive strategies need execution assumptions, not just signal strength.",
    },
    {
        "id": "transaction_costs",
        "canonical_name": "Transaction Costs",
        "aliases": ["trading costs", "implementation costs"],
        "category": "mechanics",
        "definition": "The all-in costs of trading, including spread, slippage, commissions, market impact, borrow, and financing.",
        "when_it_matters": "Costs can turn high-turnover anomalies from attractive paper returns into untradeable strategies.",
        "notes": "Any corpus seed with short holding periods should carry cost sensitivity.",
    },
    {
        "id": "slippage",
        "canonical_name": "Slippage",
        "aliases": ["execution slippage", "price impact"],
        "category": "mechanics",
        "definition": "The difference between expected execution price and actual execution price.",
        "when_it_matters": "Slippage is especially important for intraday, low-liquidity, high-volatility, and crowded strategies.",
        "notes": "Backtests that ignore slippage are optimistic by construction.",
    },
    {
        "id": "borrow_cost",
        "canonical_name": "Borrow Cost",
        "aliases": ["short borrow fee", "stock loan fee"],
        "category": "mechanics",
        "definition": "The financing cost paid to borrow securities for short selling.",
        "when_it_matters": "Short legs of anomaly, pairs, and market-neutral strategies can lose expected edge when borrow is expensive or unavailable.",
        "notes": "Borrow constraints are a common gap between academic long-short returns and live tradability.",
    },
    {
        "id": "market_neutrality",
        "canonical_name": "Market Neutrality",
        "aliases": ["beta neutral", "dollar neutral"],
        "category": "mechanics",
        "definition": "A portfolio construction goal that reduces broad market exposure through offsetting long and short positions.",
        "when_it_matters": "Market neutrality changes risk attribution; a signal can look good long-short while being difficult to approximate in long-only form.",
        "notes": "OpenAP long-short spreads should not be conflated with long-only implementations.",
    },
    {
        "id": "realized_volatility",
        "canonical_name": "Realized Volatility",
        "aliases": ["historical volatility", "realized vol"],
        "category": "factors",
        "definition": "Volatility measured from actual historical price movements over a lookback window.",
        "when_it_matters": "Realized volatility is used in vol targeting, risk filters, variance-risk-premium measurement, and low-volatility equity signals.",
        "notes": "Measurement frequency and window length materially affect signal behavior.",
    },
    {
        "id": "implied_volatility",
        "canonical_name": "Implied Volatility",
        "aliases": ["IV", "option implied vol"],
        "category": "factors",
        "definition": "The volatility level implied by option prices under an option-pricing model.",
        "when_it_matters": "Implied volatility drives option premium, skew, term structure, and variance-risk-premium strategies.",
        "notes": "Implied volatility is not a forecast by itself; it includes risk premia and supply-demand effects.",
    },
    {
        "id": "open_interest",
        "canonical_name": "Open Interest",
        "aliases": ["OI", "contracts outstanding"],
        "category": "mechanics",
        "definition": "The number of outstanding derivative contracts that have not been closed or settled.",
        "when_it_matters": "Open interest helps diagnose liquidity, positioning, crowded strikes, and potential unwind pressure.",
        "notes": "Rising open interest can reflect either new risk transfer or increased crowding.",
    },
    {
        "id": "liquidation_cascade",
        "canonical_name": "Liquidation Cascade",
        "aliases": ["forced liquidation cascade", "deleverage cascade"],
        "category": "regimes",
        "definition": "A feedback loop where forced liquidations move price, triggering more liquidations.",
        "when_it_matters": "Crypto perp and leveraged strategies can fail abruptly when margin liquidations amplify price moves.",
        "notes": "Funding and basis trades need liquidation and venue-risk controls.",
    },
    {
        "id": "automated_market_maker",
        "canonical_name": "Automated Market Maker",
        "aliases": ["AMM", "constant function market maker"],
        "category": "instruments",
        "definition": "A decentralized exchange mechanism that quotes prices from pool inventory and a pricing function rather than a central limit order book.",
        "when_it_matters": "AMMs create unique execution, arbitrage, liquidity-provision, and impermanent-loss mechanics.",
        "notes": "The acquired Oxford AMM statistical-arbitrage paper is relevant here.",
    },
    {
        "id": "impermanent_loss",
        "canonical_name": "Impermanent Loss",
        "aliases": ["divergence loss", "LP loss versus hold"],
        "category": "mechanics",
        "definition": "The relative loss a liquidity provider experiences versus simply holding the pool assets when relative prices move.",
        "when_it_matters": "Liquidity-provision strategies must compare fees and incentives against inventory rebalancing losses.",
        "notes": "This is a crypto-native implementation risk rather than a conventional equity factor.",
    },
    {
        "id": "order_flow",
        "canonical_name": "Order Flow",
        "aliases": ["signed order flow", "trade imbalance"],
        "category": "mechanics",
        "definition": "Information about buying and selling pressure inferred from submitted orders or executed trades.",
        "when_it_matters": "Order-flow imbalance can predict short-horizon returns but is sensitive to venue, latency, and market-impact assumptions.",
        "notes": "The acquired EFMA order-flow paper is relevant source material.",
    },
]


def concept_entry(item: dict[str, Any]) -> tuple[str, str]:
    frontmatter = {
        "entry_type": "concept",
        "id": item["id"],
        "canonical_name": item["canonical_name"],
        "aliases": item["aliases"],
        "category": item["category"],
        "definition": item["definition"],
        "when_it_matters": item["when_it_matters"],
        "references": [
            {
                "title": "OBaI concept seed glossary",
                "authors": ["OBaI maintainers"],
                "year": 2026,
            }
        ],
    }
    body = [
        yaml_block(frontmatter),
        "## Notes\n",
        item["notes"],
        "\n",
    ]
    return item["category"], "".join(body)


def reset_generated_dirs() -> None:
    for path in [CORPUS_ROOT / "strategies", CORPUS_ROOT / "concepts"]:
        if path.exists():
            shutil.rmtree(path)


def generate_openap() -> int:
    count = 0
    with OPENAP_SIGNAL_DOC.open(newline="") as handle:
        for row in csv.DictReader(handle):
            acronym = clean(row.get("Acronym"))
            if not acronym:
                continue
            category, content = openap_entry(row)
            out_dir = DRAFTS_ROOT / "strategies" / category
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"openap_{slugify(acronym)}.md"
            out_path.write_text(content, encoding="utf-8")
            count += 1
    return count


def generate_concepts() -> int:
    for item in CONCEPTS:
        category, content = concept_entry(item)
        out_dir = DRAFTS_ROOT / "concepts" / category
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{item['id']}.md"
        out_path.write_text(content, encoding="utf-8")
    return len(CONCEPTS)


def main() -> None:
    if not OPENAP_SIGNAL_DOC.is_file():
        raise SystemExit(f"missing source: {OPENAP_SIGNAL_DOC}")
    reset_generated_dirs()
    strategy_count = generate_openap()
    concept_count = generate_concepts()
    print(f"generated {strategy_count} strategy entries and {concept_count} concept entries")


if __name__ == "__main__":
    main()
