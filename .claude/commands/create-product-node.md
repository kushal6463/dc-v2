---
description: Create an IntelligenceProduct (IQ app) spine node by product id after a confirm-before-create check.
argument-hint: <product_id> (one of miq, ciq, piq, dc, creative_iq)
allowed-tools: mcp__graph__lookup_node, mcp__graph__create_product_node
---

Create (or report) an `IntelligenceProduct` node — one of the IQ applications that form a spine axis under the `Business` root (schema section 4). The five V1 products are:

| product_id   | display_name    | category    | note                                  |
|--------------|-----------------|-------------|---------------------------------------|
| `miq`        | Marketing IQ    | analytics   | central analytics, 50+ dashboards     |
| `ciq`        | Customer IQ     | analytics   | real product, still on `miq` schema   |
| `piq`        | Product IQ      | analytics   | real product, still on `miq` schema   |
| `dc`         | Decision Canvas | decisioning | writes capsules/thoughts              |
| `creative_iq`| Creative IQ     | creative    | external, separate repo/manifest      |

The target product id is: `$1` (required).

Do this in order:

1. **Lookup first.** Call `mcp__graph__lookup_node` with `label="IntelligenceProduct"` and `key="<product_id>"` (the key field is `product_id`).
   - If the node already exists, DO NOT create it. Report `IntelligenceProduct <id> already exists` and print its current fields as a plaintext aligned table (field | value). Stop here.

2. **Otherwise create it.** Call `mcp__graph__create_product_node` with sensible values. If a `harness/seed/spine_seed.json` file exists, prefer its `products` entry for this id; otherwise derive defaults from the table above:
   - `product_id`: the resolved id (e.g. `miq`) — REQUIRED.
   - `display_name`: from the table (e.g. `Marketing IQ`) — REQUIRED.
   - `status`: one of `active·hidden·deprecated·proposed` (use `active`) — REQUIRED.
   - `category`: one of `analytics·decisioning·creative·external` (from the table; `creative_iq` is `creative` but external).
   - `description`: a short description from the note column.
   - `schema_name`: e.g. `miq` (ciq/piq are still on the `miq` schema).
   - `schema_status`: `owned` or `shared` (use `shared` for ciq/piq, `owned` for miq).
   - `default_data_classification`: one of `public·internal·restricted·executive` (use `internal`).
   - `min_level`: clearance floor (use `40`).

   A confirm-before-create hook will surface a field table and ask for confirmation before the write — let it run.

3. **Report.** After the create returns, print the resulting node as a plaintext aligned table (field | value) so the user can verify it.
