---
description: Create a Domain (FRD functional column) spine node by id/name after a confirm-before-create check.
argument-hint: <domain_id> (e.g. marketing, finance, operations)
allowed-tools: mcp__graph__lookup_node, mcp__graph__create_domain_node
---

Create (or report) a `Domain` node — one of the FRD functional columns that form a spine axis under the `Business` root (schema section 4). The FRD column set is: `finance·marketing·operations·service·customer·product·supply_chain·hr·data_it`.

The target domain id/name is: `$1` (required). Normalize a human name to a slug id if needed (e.g. `Marketing` -> `marketing`, `Supply Chain` -> `supply_chain`).

Do this in order:

1. **Lookup first.** Call `mcp__graph__lookup_node` with `label="Domain"` and `key="<domain_id>"` (the key field is `domain_id`).
   - If the node already exists, DO NOT create it. Report `Domain <id> already exists` and print its current fields as a plaintext aligned table (field | value). Stop here.

2. **Otherwise create it.** Call `mcp__graph__create_domain_node` with sensible values. If a `harness/seed/spine_seed.json` file exists, prefer its `domains` entry for this id; otherwise derive defaults:
   - `domain_id`: the resolved slug (e.g. `marketing`) — REQUIRED.
   - `name`: a human-readable name (e.g. `Marketing`) — REQUIRED.
   - `decision_scope_summary`: one short sentence describing the decisions this domain owns or contextualizes (e.g. for marketing: "Owns acquisition, campaign spend, and channel ROAS decisions.") — REQUIRED.
   - `min_level`: clearance floor for the domain branch (use `40` for a standard business column) — REQUIRED.
   - `data_classification`: one of `public·internal·restricted·executive` (use `internal`) — REQUIRED.
   - `status`: one of `active·hidden·deprecated·proposed` (use `active`) — REQUIRED.
   - `domain_type`: one of `business·technical·risk·data_quality·ml` (use `business` for the FRD columns; use `data_quality`/`ml`/`technical` for `data_it`).
   - `approval_policy_summary`: a short human summary of approval expectations for this domain.

   A confirm-before-create hook will surface a field table and ask for confirmation before the write — let it run.

3. **Report.** After the create returns, print the resulting node as a plaintext aligned table (field | value) so the user can verify it.
