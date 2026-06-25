---
description: Validate + explain a metric→metric edge candidate — endpoint existence, scope rule, and deterministic scoring (read-only).
argument-hint: <from_uid> <to_uid> <rel_type> <relation>
allowed-tools: mcp__graph__validate_edge_candidate, mcp__graph__explain_edge_candidate
---

Validate and explain a single metric→metric edge candidate against the deterministic edge-scoring policy (plan §9/§14). **Read-only** — no edge is written; this only tells you whether the candidate is well-formed and whether it would be auto-safe or held for review.

Arguments (all required): `from_uid` `$1`, `to_uid` `$2`, `rel_type` `$3` (`DECOMPOSES_INTO` or `INFLUENCES`), `relation` `$4` (the subtype — e.g. `formula`, `identity`, `component`, `rollup`, `crossproduct`, `funnel` for DECOMPOSES_INTO; `curated_rule`, `llm_verified`, `statistical`, `statistical_candidate`, `promoted` for INFLUENCES).

Do this in order:

1. **Validate.** Call `mcp__graph__validate_edge_candidate` with `from_uid`, `to_uid`, `rel_type`, `relation`. Report `valid`, `endpoint_exists` (both `from` and `to`), `scope_ok` (the same-scope rule applies to `formula`/`identity` relations), the `scoring` block (`confidence`, `evidence_mass`, `scoring_policy`, `review`), and any `reasons` if it is not valid.

2. **Explain.** Call `mcp__graph__explain_edge_candidate` with the same four arguments. Surface the one-line `why` rationale, the `scoring_policy`, and `auto_safe_or_review`.

3. **Read.** Give a one-line verdict: is this a clean, deterministic auto-safe edge, or a review-only candidate (and why)? Both tools are read-only and write nothing.
