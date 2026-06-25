# Deterministic Edge Formation Plan

## Summary

- Do not use LLMs to create causal edges.
- Keep edge formation deterministic, evidence-gated, and explainable.
- For `Top Performing Campaigns`, current two metric links are not a complete business explanation, but new edges should only appear when a rule, formula, seed, or explicit registry text supports them.
- Do not hand-author thousands of individual edges. Author a small catalog of reusable deterministic mechanism rules, then let the graph apply those rules across resolved metrics when evidence matches.

## Findings

- The current `Top Performing Campaigns` view is incomplete. Its two visible metric links are an artifact of current candidate generation and UI focus, not proof that only two factors affect campaign performance.
- Cross-domain edges are schema-allowed but not systematically generated today. Dashboard co-occurrence and existing priors mostly keep candidates inside one dashboard/domain, so relationships like marketing/sales demand affecting inventory or stockout risk are missed.
- Important drivers can be stored as `causal_role: "constraint"` metrics, such as deliverability or stockout signals. The current candidate gate does not treat `constraint` as a cause role, so these drivers can be skipped even when the business mechanism is clear.
- Clean causal concepts do not always resolve to live metric nodes because many stored `concept_key` / `metric_base` values are raw chart IDs. Alias coverage must bridge clean rule vocabulary such as `out_of_stock`, `deliverability`, `campaign_engagement`, and `demand` to real stored metric IDs.
- Dense coverage must come from rule families, not manual edge enumeration. A rule like `deliverability -> email_engagement` can create many valid `INFLUENCES` proposals, but only where the source and target metrics resolve and required evidence appears in formula/title/narration/how-to-read text.
- Existing `llm_link` and `llm_proposal` causal edges are useful as historical suggestions, but they are not authoritative unless the same edge can be re-derived by deterministic rules or explicitly approved by a human.

## Deterministic Edge Policy

- `DECOMPOSES_INTO`: created only from formula/ranking parsing or explicit component mapping.
- `CORRELATES_WITH`: created only from measured statistical seed data or explicit approved association rules; never promoted to causal.
- `INFLUENCES`: created only when a curated rule resolves both endpoints and required evidence text matches.
- `CAUSES`: not auto-created; only possible later through explicit human approval or empirical promotion.
- Existing `llm_link` and `llm_proposal` edges should be treated as non-authoritative unless re-derived by deterministic rules.

## Key Changes

- Disable or avoid LLM causal edge generation for production graph writes:
  - Do not use `kg run-causal --llm`.
  - Do not rely on `llm_link` edges for causal truth.
  - Treat existing `llm_link` and `llm_proposal` causal edges as review-only or removable unless independently supported.

- Add deterministic causal templates:
  - Formula rules: `CTOR = Clicks / Opens` creates `DECOMPOSES_INTO`.
  - Ranking rules: "Top campaigns sorted by CTOR" creates `DECOMPOSES_INTO -> Campaign Performance`.
  - Threshold/filter rules: "minimum 100K sends" creates a deterministic dependency on send volume if a matching send-volume metric exists.
  - Inventory rules only fire when source text explicitly mentions stockout, inventory, out-of-stock, availability, lost revenue, cart-to-purchase gap, or demand.

- Add a curated deterministic mechanism-rule catalog:
  - Rules are concept-family templates, not one-off edges.
  - Example: `inventory_stockout -> campaign_revenue` only if both endpoints exist and the inventory metric text explicitly describes lost sales or blocked purchases.
  - Example: `email_volume -> campaign_performance` only if the campaign metric formula/ranking references sends or volume.
  - Example: `deliverability -> email_engagement` only if the source metric contains delivery/bounce/spam/inbox evidence and the target metric contains open/click/CTOR/campaign engagement evidence.
  - Each rule must include `source_concepts`, `target_concepts`, `relation_type`, `direction`, `required_evidence_keywords`, `mechanism_template`, and `confidence_policy`.
  - The rule engine resolves concepts through aliases, checks required evidence, emits an edge proposal only on match, and records a rejection reason otherwise.

- Extend deterministic resolution and coverage:
  - Add alias groups for clean causal concepts to real stored chart IDs, including stockout, deliverability, campaign engagement, demand, send volume, inventory availability, conversion gap, and revenue at risk.
  - Consider `constraint` a valid deterministic cause role for candidate discovery, but never let role compatibility alone create an edge.
  - Split any overloaded limits into separate subject and candidate limits so coverage gaps are visible instead of hidden by a single cap.

- Add an audit mode before writing:
  - For any selected metric, show all candidate edges with reason codes:
    - `formula_match`
    - `ranking_metric_match`
    - `explicit_text_keyword`
    - `curated_bridge_rule`
    - `alias_resolved`
    - `missing_endpoint`
    - `rejected_no_evidence`
  - This answers why only certain edges exist.

## Test Plan

- Add tests proving no random edges are created:
  - A campaign metric with no formula or text evidence gets no inventory edge.
  - A campaign metric mentioning sends gets a deterministic send-volume dependency.
  - A deliverability metric can influence email engagement only when delivery/open/click evidence is present.
  - Inventory edges only appear when inventory or stockout evidence exists in source text.
  - Clean concepts like `out_of_stock` and `deliverability` resolve to real metric IDs through aliases.
  - Cross-domain rules produce proposals only after endpoint resolution and evidence matching.
  - Existing formula and rollup behavior remains unchanged.
- Run `uv run pytest harness/tests/test_causal.py`.

## Assumptions

- Correctness is more important than dense graph coverage.
- `INFLUENCES` should only be produced by deterministic mechanism rules with explicit evidence.
- LLMs may be used later for suggestions, but not as automatic edge creators.
- Human review can promote strong deterministic `INFLUENCES` to `CAUSES` later, but the engine should not auto-create `CAUSES`.
