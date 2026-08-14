"""Unit tests for the research specialist prompt contract."""

from __future__ import annotations

from pathlib import Path


def _read_prompt_file(name: str) -> str:
    prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
    return (prompts_dir / f"{name}.md").read_text()


def test_prompt_covers_future_freshness_and_url_citations() -> None:
    """Prompt handles the 'future' freshness class and requires URL citations.

    Guards accuracy.md §32: the research server emits a ``freshness: "future"``
    class for post-today dates (a fabrication/misdate red flag), and every
    result already carries the full ``url``. The prompt must therefore (a)
    enumerate ``future`` and give it a suspicious-source rule, and (b) cite the
    full source URL so material claims are verifiable, not domain-only.
    """
    prompt = _read_prompt_file("research")
    lowered = prompt.lower()

    # (a) 'future' is enumerated as a freshness class and has a handling rule.
    assert '"future"' in prompt
    assert "suspicious" in lowered

    # (b) Citations require the full source URL, not domain-only.
    assert "url" in lowered
    assert "source domain" not in lowered


class TestCitationVerification:
    """Cited links are checked against what the research tools returned."""

    # Recorded in the CORE-WALKFORWARD-era CORE-RESEARCH gate run: the
    # research tool returned the first URL, and the specialist emitted the
    # second — a re-slugified title that exists in no retrieved result.
    RETRIEVED = (
        "https://www.morningstar.com/stocks/"
        "tesla-earnings-shares-fall-capital-expenditure-growth-delayed-optimus"
    )
    FABRICATED = (
        "https://www.morningstar.com/stocks/"
        "tesla-earnings-shares-fall-capital-expenditures-and-delayed-optimus"
    )

    def test_collects_urls_from_string_and_structured_output(self) -> None:
        """Tool payloads arrive as JSON strings and as objects; read both."""
        from core_agents.central_hub_agent import _collect_retrieved_urls

        urls = _collect_retrieved_urls(
            [
                {"specialist": "Research Agent", "output": f'{{"url": "{self.RETRIEVED}"}}'},
                {
                    "specialist": "Research Agent",
                    "output": {"results": [{"url": "https://a.test/x"}]},
                },
            ]
        )

        assert self.RETRIEVED.casefold() in urls
        assert "https://a.test/x" in urls

    def test_replaces_a_url_no_retrieved_result_contained(self) -> None:
        """The real fabrication must not reach the user as a live link."""
        from core_agents.central_hub_agent import _redact_unretrieved_urls

        answer = (
            f"Morningstar kept its fair-value estimate. Source: [Morningstar]({self.FABRICATED})"
        )

        verified, dropped = _redact_unretrieved_urls(answer, {self.RETRIEVED.casefold()})

        assert self.FABRICATED not in verified
        assert dropped == [self.FABRICATED]
        assert "SOURCE-UNVERIFIED" in verified
        assert "unverified" in verified.casefold()

    def test_leaves_a_retrieved_url_untouched(self) -> None:
        """A genuine citation must survive byte-for-byte."""
        from core_agents.central_hub_agent import _redact_unretrieved_urls

        answer = f"Source: [Morningstar]({self.RETRIEVED})"

        verified, dropped = _redact_unretrieved_urls(answer, {self.RETRIEVED.casefold()})

        assert verified == answer
        assert dropped == []

    def test_trailing_prose_punctuation_does_not_fake_a_mismatch(self) -> None:
        """A URL ending a sentence is the same URL."""
        from core_agents.central_hub_agent import _redact_unretrieved_urls

        answer = f"See {self.RETRIEVED}."

        verified, dropped = _redact_unretrieved_urls(answer, {self.RETRIEVED.casefold()})

        assert dropped == []
        assert verified == answer

    def test_redacts_every_url_when_nothing_was_retrieved(self) -> None:
        """A citation with no retrieval behind it is model memory, not a source.

        Marking a real link unverified is visible and recoverable; shipping a
        fabricated one is silent and is what this guard exists to stop.
        """
        from core_agents.central_hub_agent import _redact_unretrieved_urls

        verified, dropped = _redact_unretrieved_urls(f"Source: {self.FABRICATED}", set())

        assert dropped == [self.FABRICATED]
        assert self.FABRICATED not in verified
