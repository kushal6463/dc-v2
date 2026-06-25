---
description: Create the single Business root node (defaults to rare-seeds) after a confirm-before-create check.
argument-hint: "[business_id] (optional; defaults to rare-seeds)"
allowed-tools: mcp__graph__lookup_node, mcp__graph__create_business_node
---

Create (or report) the single `Business` root node for this tenant database. There is exactly one `Business` per tenant DB — it is the anchor every spine axis hangs from (schema section 4).

Resolve the business id: use `$1` if provided, otherwise default to `rare-seeds`.

Do this in order:

1. **Lookup first.** Call `mcp__graph__lookup_node` with `label="Business"` and `key="<business_id>"` (the key field is `business_id`).
   - If the node already exists, DO NOT create it. Report `Business <id> already exists` and print its current fields as a plaintext aligned table (field | value). Stop here.

2. **Otherwise create it.** Call `mcp__graph__create_business_node` with sensible values derived from the id. If a `harness/seed/spine_seed.json` file exists, prefer its `business` defaults; otherwise use these defaults for `rare-seeds`:
   - `business_id`: the resolved id (e.g. `rare-seeds`) — REQUIRED.
   - `display_name`: a human-readable name (e.g. `Rare Seeds`) — REQUIRED.
   - `tier`: one of `startup·smb·mid_market·mnc` (use `smb` for rare-seeds) — REQUIRED.
   - `status`: one of `active·paused·archived` (use `active`) — REQUIRED.
   - `business_type`: one of `ecommerce·saas·marketplace·retail·services·other` (use `ecommerce` for rare-seeds).
   - `primary_currency`: e.g. `USD`.
   - `timezone`: e.g. `America/New_York`.
   - `default_granularity`: one of `daily·weekly·monthly·quarterly` (use `daily`).
   - `decision_risk_posture`: one of `conservative·balanced·aggressive` (use `balanced`).
   - `default_data_classification`: one of `public·internal·restricted·executive` (use `internal`).
   - `root_seniority_rank`: the top role's rank (use `100` for the CEO).
   - `strategic_intent_summary`: a short statement of what the company optimizes for.

   A confirm-before-create hook will surface a field table and ask for confirmation before the write — let it run.

3. **Report.** After the create returns, print the resulting node as a plaintext aligned table (field | value) so the user can verify it.
