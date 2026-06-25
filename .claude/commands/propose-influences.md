---
description: Propose curated INFLUENCES candidates from the causal-rule seed against live metrics (candidates only, no LLM, no write).
argument-hint: [scope_key] [domain_id]
allowed-tools: mcp__graph__propose_influence_candidates
---

Propose cross-domain `INFLUENCES {relation: curated_rule}` **candidates** from the curated causal-mechanism rules (`harness/seed/concept_causal_rules.json`) resolved onto the live metrics (plan §5e). This is **candidates only** — the LLM judge / refuter is NOT run here, and nothing is written. Every candidate is later verified and human-reviewed before any edge is applied.

Arguments: scope filter `$1` (optional `scope_key` to bias endpoint resolution), domain filter `$2` (optional `domain_id` to restrict the indexed metrics).

Do this in order:

1. **Propose.** Call `mcp__graph__propose_influence_candidates` with `scope` (`$1` when given) and `domain` (`$2` when given). This reads the curated rules, resolves each rule's source/target concepts to live metrics via the ConceptIndex (+ aliases), and returns the resolvable pairs.

2. **Report.** For each candidate print `rule_id`, `from` -> `to` (the resolved `metric_uid`s), `mechanism`, and `prior`. Then summarize the `rejected` rules with their `reason` (e.g. `source did not resolve`, `target did not resolve`, `self_loop`) — these are intentional (a rule whose endpoints have no live metric never becomes a candidate), not errors.

3. **Next step.** Remind the user these are review-only `curated_rule` candidates: they feed the LLM verification pass (`/run-causal --llm`) and are NEVER auto-promoted to a confirmed causal edge. This tool is read-only and writes nothing.
