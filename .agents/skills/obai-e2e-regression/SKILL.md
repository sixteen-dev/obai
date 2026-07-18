---
name: obai-e2e-regression
description: Run the cost-aware OBaI black-box regression gate after substantive prompt, routing, specialist, or subagent changes. Use only when the user explicitly requests OBaI E2E regression, merge validation, or a full regression run. Default to the deduplicated core tier; run the live canary or separate broader evaluation corpus only when explicitly requested because they consume additional billable model requests.
---

# OBaI E2E Regression Compatibility Entry

Use the tracked canonical implementation at
`.claude/skills/obai-e2e-regression/`.

Before planning or executing a regression run, read
`.claude/skills/obai-e2e-regression/SKILL.md` completely and follow it.
Use only the canonical cases and scripts referenced there. Do not use the
legacy sibling `cases/` or `scripts/` copies under `.agents/`.
