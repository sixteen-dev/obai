# Concept Entry Drafter

You are drafting a single concept entry for the OBaI knowledge base from a glossary seed or reference source. Output only the markdown file content. No commentary.

## Source

{{citation_block}}
{{source_excerpt}}
{{structured_inputs}}

## Required frontmatter

Produce markdown matching this schema:

  entry_type: concept
  id: <snake_case>
  canonical_name: "<canonical phrase>"
  aliases: [<list of common alternative names from the source>]
  category: <one of: regimes | instruments | factors | mechanics>
  definition: "<1-2 sentence formal definition>"
  when_it_matters: "<specific contexts where this concept changes trading decisions; cite venues / regimes / instruments>"
  related_strategies: [<list of strategy IDs already in corpus, optional>]
  references:
    - title: "<title>"
      authors: [<list>]
      year: <int>

## Body (optional, only `## Notes` if present)

## Notes
<caveats, edge cases, opposite condition, related but distinct terms>

## Rules

- `definition` must be formal and factual. No editorial language.
- `when_it_matters` must be specific to trading — link to venues (e.g., VIX futures), regimes, or instrument classes.
- `related_strategies` IDs must reference existing entries. If a referenced strategy is in `corpus_private/`, this concept must also be in `corpus_private/` (link-integrity rule).
- Do not invent references not in the source.
- Use snake_case for `id`. Use Title Case for `canonical_name`.
- If a required field cannot be filled from the source, leave it blank — do NOT fabricate.
