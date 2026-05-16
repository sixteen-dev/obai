# Strategy Entry Drafter

You are drafting a single strategy entry for the OBaI knowledge base from a source artifact. Output only the markdown file content. No commentary.

## Source

{{citation_block}}
{{source_excerpt}}
{{structured_inputs}}

## Engine reference

{{engine_reference}}

## Required frontmatter

Produce markdown matching this schema:

  entry_type: strategy
  id: <snake_case>
  canonical_name: "<canonical phrase>"
  aliases: [<list of common alternative names from the source>]
  one_line: "<one-sentence summary of what the strategy does>"
  category: <one of: momentum | mean_reversion | vol | options_structures | carry | quality | size | low_volatility | crypto_native | microstructure | event_driven | other>
  asset_classes: [<list>]
  typical_holding_period: <daily | weekly | monthly | quarterly | intraday>
  engine_fit: <native | approximate | reference_only>
  approximation_notes: "<how the current backtest engine can approximate this, or why it cannot>"
  signal_inputs: [<list of data the signal needs>]
  known_failure_modes:
    - "<failure mode 1, specific to this strategy, evidenced by source>"
    - "<failure mode 2>"
  when_to_consider: "<specific regime/universe/horizon conditions where this fits>"
  when_to_avoid: "<specific conditions where it underperforms or breaks>"
  seminal_papers:
    - title: "<title>"
      authors: [<list>]
      year: <int>
      venue: "<journal/conference>"
      url: "<url>"

## Body (in this order, exactly)

## Thesis
<2-3 sentences on the economic/behavioral rationale>

## Signal intuition
<2-4 sentences on what the signal computes and why>

## Construction sketch
```
<pseudocode of signal construction and trade rules>
```

## Notes
<any caveats, regime sensitivity, replication evidence, decay patterns>

## Rules

- `known_failure_modes` and `when_to_avoid` must reflect actual evidence in the source, not generic warnings.
- `when_to_consider` must be specific (regime, universe size, holding period).
- `engine_fit` is an OBaI execution-fit label, not a source claim. Use the `## Engine reference` block above as the source of truth — do NOT rely on training data about what backtest engines generally support. Use `native` only for mechanics representable in the current backtest schema, `approximate` when a proxy backtest is plausible (and put the proxy in `approximation_notes`), and `reference_only` when the current strategy engine should not execute it.
- `approximation_notes` is required when `engine_fit` is `approximate` or `reference_only`. Leave blank for `native`.
- Do not invent papers, aliases, or failure modes not in the source.
- If a required field cannot be filled from the source, leave it blank — do NOT fabricate.
- Use snake_case for `id`. Use Title Case for `canonical_name`.
