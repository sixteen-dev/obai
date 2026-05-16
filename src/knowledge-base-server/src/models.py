"""Pydantic response models for the knowledge-base MCP server.

Two top-level shapes are used by the MCP tools:
    * `CorpusEntrySummary` — compact entry returned by `search_corpus`.
    * `CorpusEntry`         — full entry returned by `get_corpus_entry`.

Each shape is a discriminated union on `entry_type` (strategy | concept).
The maintainer-facing `corpus_destination` field is **never** exposed.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SeminalPaper(BaseModel):
    """One paper / reference citation."""

    model_config = ConfigDict(frozen=True)

    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    url: str | None = None


class _SummaryBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    canonical_name: str
    category: str


class StrategySummary(_SummaryBase):
    """Compact strategy entry."""

    entry_type: Literal["strategy"] = "strategy"
    one_line: str | None = None
    when_to_consider: str | None = None
    engine_fit: Literal["native", "approximate", "reference_only"] | None = None
    approximation_notes: str | None = None
    top_failure_modes: list[str] = Field(default_factory=list)


class ConceptSummary(_SummaryBase):
    """Compact concept entry."""

    entry_type: Literal["concept"] = "concept"
    definition: str | None = None
    when_it_matters: str | None = None
    related_strategies: list[str] = Field(default_factory=list)


CorpusEntrySummary = Annotated[
    StrategySummary | ConceptSummary,
    Field(discriminator="entry_type"),
]


class _FullBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    canonical_name: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    body: str = ""  # the markdown body (## Thesis, ## Signal intuition, ...)
    references: list[SeminalPaper] = Field(default_factory=list)


class StrategyEntry(_FullBase):
    """Full strategy record."""

    entry_type: Literal["strategy"] = "strategy"
    one_line: str
    asset_classes: list[str] = Field(default_factory=list)
    typical_holding_period: str | None = None
    engine_fit: Literal["native", "approximate", "reference_only"]
    approximation_notes: str | None = None
    signal_inputs: list[str] = Field(default_factory=list)
    known_failure_modes: list[str] = Field(default_factory=list)
    when_to_consider: str
    when_to_avoid: str


class ConceptEntry(_FullBase):
    """Full concept record."""

    entry_type: Literal["concept"] = "concept"
    definition: str
    when_it_matters: str
    related_strategies: list[str] = Field(default_factory=list)


CorpusEntry = Annotated[
    StrategyEntry | ConceptEntry,
    Field(discriminator="entry_type"),
]


class CategoryCount(BaseModel):
    """Category name + entry count."""

    model_config = ConfigDict(frozen=True)

    category: str
    count: int


class CorpusCategoryIndex(BaseModel):
    """Categories split by entry_type."""

    model_config = ConfigDict(frozen=True)

    strategies: list[CategoryCount] = Field(default_factory=list)
    concepts: list[CategoryCount] = Field(default_factory=list)
