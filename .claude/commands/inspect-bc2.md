---
description: Inspect the BC_2 offline snapshot — file hashes + validated metric-relationship candidates.
argument-hint: [bc_path] (default /Users/kushal/Desktop/kal/BC_2)
allowed-tools: mcp__graph__inspect_bc2_sources
---

Inspect the BC_2 source snapshot that feeds the KG skeleton (plan §6). This is **read-only** — it loads, hashes, and validates BC_2 seed rows; it never writes the graph.

The BC_2 path is: `$1` (optional; defaults to `/Users/kushal/Desktop/kal/BC_2`).

Do this in order:

1. **Inspect.** Call `mcp__graph__inspect_bc2_sources` with `bc_path` (pass `$1` when given, else omit to use the default). This is pre-allowed and read-only — no confirmation needed.

2. **Report.** Summarize the result for the user:
   - Print a plaintext aligned table of the hashed source files (`name` | `rows` | `sha256` short prefix) — this proves the "identical fixtures" content fingerprint.
   - State the `valid_rel_candidates` and `rejected_rel_rows` counts (these are BC_2 metric-relationship / ontology-causal-edge rows that did / did not survive structural validation).
   - Print the `reject_reasons` breakdown (e.g. `sql_token_source`, `self_loop`, `unknown_relationship_type`, `inactive`) — these are intentional rejections, not errors. Nothing is silently dropped.

3. **Next step.** Note that the valid candidates become edge proposals downstream (through arbitration, after review) — this tool only inspects them.
