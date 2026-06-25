---
description: Look up a live metric's narrative notes — joins its Neo4j props with the chart-registry + openapi endpoint slice (read-only).
argument-hint: <metric_uid>
allowed-tools: mcp__graph__lookup_metric_notes, mcp__graph__get_chart_registry_entry
---

Look up the narrative / explanatory notes for a live metric and join them with their source documentation (chart-registry + openapi). **Read-only** — slices the registry/openapi files (never loads them whole) and writes nothing.

The metric is: `$1` (required `metric_uid`, e.g. `metric:blended:revenue`).

Do this in order:

1. **Look up notes.** Call `mcp__graph__lookup_metric_notes` with `metric_uid="$1"`. This returns:
   - the metric's live Neo4j props (`scope_key`, `formula_text`, `formula_explanation`, `how_to_read`, `decisions_answered`, `narration_text`, `card_endpoint`),
   - the single matching `chart_registry` entry (by `canonical_id` / `chart_id`),
   - the matching `openapi_endpoint` description (summary + description for the metric's card endpoint).
   If `found` is `false`, report that no live metric with that uid exists and stop.

2. **(Optional) registry entry.** If the user asks for the raw chart-registry entry by its `canonical_id`, call `mcp__graph__get_chart_registry_entry` with that `canonical_id`.

3. **Report.** Print a readable summary: the formula + its explanation, the `how_to_read` bullets, the `decisions_answered` this metric supports, the narration text, and the endpoint summary. Both tools are read-only.
