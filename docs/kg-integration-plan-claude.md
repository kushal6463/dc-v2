# KG Skeleton + Teammate Discovery — Integration Plan (Claude)

Date: 2026-06-21
Status: interview-approved decision + roadmap. **No code is written yet.**

> **Decision deliverable** for: do we implement our deterministic skeleton or the teammate's
> (`knowledgeGraph`) pipeline, and how do they combine — re-evaluated against the **updated**
> `openapi.json` / `chart-registry.json`. Skeleton internals live in
> `docs/kg-skeleton-implementation-claude.md` (the merged spec); this doc focuses on the
> **integration**. Companions: `INTEGRATION-ANALYSIS-claude.md`, `ARCHITECTURE-claude.md`.

---

## 1. Context — why this work

A teammate built `knowledgeGraph` — a file-based causal-discovery pipeline with a node-centric
"Causal Explorer" (drivers → node → effects). We needed to decide whether to adopt it, build our own
skeleton, or combine — now that the openapi/chart-registry were updated with finer sub-channel scopes.

**Decision (approved): Skeleton-first in `dc-kg` + the teammate's statistical discovery as an evidence
feed.** `dc-kg` stays the governed Neo4j system-of-record; we harvest only the teammate's *additive*
organ (temporal/statistical discovery) plus two ideas (drivers/effects traversal, mart lineage on
nodes). We do **not** adopt the flat-file model or re-implement its deterministic edges.

---

## 2. Findings that drove the decision

### 2.1 Updated data now makes OUR skeleton finer than the teammate's build
- chart-registry: **989 entries / 90 dashboards**; openapi: **953 GET** of 986 paths.
- **Sub-channel scopes are real and clean:** `google_search` (15), `google_youtube` (14),
  `google_shopping` (11), `google_pmax` (12), `meta_prospecting` (14), `meta_retargeting` (13),
  `meta_creative` (11), `klaviyo` email/sms, `magento`, `website`, `customer`, `product`, `prediction`.
- **The teammate's current graph is COARSE** (`google.roas`, `meta.reach`, `web.users` — one scope per
  platform). Our skeleton on the updated data produces `metric:google-search:roas` ≠
  `metric:google-youtube:roas` — exactly the correctness requirement the teammate's build can't meet.
- **Formulas are 3–4 layers deep** (`Projected ROAS → Projected Revenue → Budget × Channel ROAS →
  spend/convValue`); **24 table charts** bundle multiple metrics → confirms the metrics-only split.

### 2.2 Edge types & coverage (theirs vs ours)
| | Teammate `knowledgeGraph` (rare_seeds) | `dc-kg` (now) |
|---|---|---|
| Nodes | 395 (coarse scope) | 1,385 (442 Metric · 870 Endpoint · 48 Dashboard · spine) |
| Edges | 219: structural 88 · **temporal 50** · model 31 · compositional 25 · crossproduct 23 · alias 2 | 1,234 committed (195 `DECOMPOSES_INTO` + spine/surface) **+ 3,348 proposals** pending |
| Statistical/temporal | ✅ measured (PCMCI+, FDR, lag, CMIknn) | ❌ 4 hand-typed seeds |
| Governance | none | Neo4j + arbitration + review + provenance |
| Metric semantics | rich (formula/lineage/state) | metric nodes currently **shells** (null scope/formula) |

### 2.3 Depth — multi-layer, query-time (not a 2-layer cap)
Both are multi-hop DAGs. The explorer's "2-HOP" columns are a display default
(`trace_breach --depth 2`, configurable). Our skeleton is also multi-layer (each derived metric
decomposes; traversal walks chains). **Action:** don't cap decomposition; expose a `depth` param on
upstream/downstream traversal (skeleton-spec §11).

### 2.4 Implementation ideas worth taking from the explorer (not the UI)
1. **Node-centric `drivers → node → effects` traversal** ranked by `Π(confidence) × lag plausibility`
   (`trace_breach.py walk()`) — concretizes skeleton-spec §11 path-score traversal.
2. **One graph unions all edge kinds, each tagged `kind`+confidence+lag** — confirms our 2-type +
   `relation` model.
3. **Per-node lineage** (`grain`, `source`, `mart`) surfaced — carry mart/source lineage on metrics
   (fed by `model_sql.<tenant>.json`).

---

## 3. What integrates — and what does NOT

| Teammate module | Verdict | Why |
|---|---|---|
| `discover_engine.py` + `cmi_gpu.py` (temporal: PCMCI+, Granger, MI, FDR, deseasonalize, stability, CMIknn) | ✅ **Integrate — the whole point** | dc-kg has zero statistical discovery; replaces the 4 hand-typed seeds with measured, FDR-controlled, confound-pruned, lag-estimated candidates |
| `trace_breach.py` traversal model | ✅ Adopt pattern (not code) | drivers/effects path-score traversal (skeleton-spec §11) |
| node lineage (grain/source/mart) | ✅ Adopt as metric props | richer provenance; fed by `model_sql.json` |
| structural / crossproduct / compositional / alias builders | ❌ Redundant | dc-kg does these deterministically with **better sub-channel correctness** |
| flat-file graph / tenant-free ids / no-governance autonomy | ❌ Reject | dc-kg is the governed Neo4j system-of-record |

**Net: exactly one organ crosses the boundary — the statistical discovery engine — as a file feed.**
(Unchanged conclusion from `INTEGRATION-ANALYSIS-claude.md`, reinforced by the explorer being
overwhelmingly temporal.)

---

## 4. The granularity mismatch (new, important)

The updated data introduces a mismatch the original integration doc didn't have: **our skeleton is
sub-channel-scoped (`google-search`), but the teammate's discovery feed is platform-coarse
(`google`).** A coarse edge `google.roas → blended.revenue` has no single fine target.

**Resolution (preferred):** point the teammate's pipeline at the **updated fine-scoped registry** —
update its `build_registry_seed.py scope_of()` so discovery runs at `google-search` / `google-youtube`
granularity. Then the feed matches the skeleton 1:1. Until then, map coarse edges to the
**platform-level** metric (e.g. `metric:google:roas`) and tag them `scope_level: platform`, never
fanning one coarse edge out to all sub-channels.

---

## 5. Roadmap

| Phase | Action | Gating |
|---|---|---|
| **1 — Skeleton (now)** | Build the deterministic skeleton per `docs/kg-skeleton-implementation-claude.md`: metrics-only `metric:<scope>:<base>`, sub-channel scope, `CORRELATES_WITH` (formula auto-apply) + review-only structural, governed via arbitration. | none — uses chart-registry only |
| **2 — Discovery alignment** | Update the teammate's `scope_of()` to fine sub-channel scopes so its registry + discovered edges match dc-kg ids. | needs teammate-repo edit |
| **3 — Discovery feed** | New `harness/ingest/import_discovery.py`: read `discovered_edges.<tenant>.csv` → emit `INFLUENCES {relation: statistical}` **review-only** proposals; resolve ids via `ConceptIndex` (scope-aware) + `_ALIAS_GROUPS`; map fields (§6). | **gated on live BC_ANALYTICS series API** (currently down) for *fresh* edges; cached CSV works coarse now |
| **4 — Measured Beta weight** | Replace flat `evidence_mass = 1.0` with `base × f(sample_size) × g(stability) × (fdr_pass?1:0.3)`; fold via existing `beta_confidence`. | follows Phase 3 |
| **Later** | Optionally port `discover_engine.py`/`cmi_gpu.py` into `dc-kg/harness/discovery/` behind an optional extra (one repo, heavy deps). | deps + live API |

Nothing auto-promotes to `CAUSES`; the statistical feed is always review-only.

---

## 6. Integration mechanics (the seam)

- **Contract artifact (file, not in-process):** teammate writes `data/discovered_edges.<tenant>.csv`
  (`src, dst, lag, corr, granger_p, mi, discovery_score, stability, cond_corr, method, fdr_pass`).
  Keeps `tigramite`/`statsmodels`/`torch` out of dc-kg's runtime.
- **Consumer:** `harness/ingest/import_discovery.py` mirrors the existing `correlation_edges()` shape →
  edge-only proposals (`operation: "upsert_edge"`, `target_label: "Metric"`, `key_field: "metric_uid"`).
- **Field map → `INFLUENCES` props:** `corr → correlation`, `granger_p → p_value`, `lag →
  temporal_lag`, series length → `sample_size`, `method`/`fdr_pass`/`stability`/`cond_corr` → provenance
  + Beta weight (§5 Phase 4).
- **Identity resolver:** reuse `ConceptIndex` (scope-aware `prefer_scope`) + `_ALIAS_GROUPS`; log every
  unresolved pair (no silent drops); handle the coarse↔fine mismatch per §4.
- **Sequencing:** skeleton metrics must exist first (`upsert_edge` returns `missing_endpoint` for
  dangling edges; completes on re-run).

---

## 7. Critical files

| File | Change |
|---|---|
| `docs/kg-skeleton-implementation-claude.md` | the skeleton spec (Phase 1) — source of truth, already written |
| `harness/ingest/import_discovery.py` | **new** — discovery-CSV → `INFLUENCES{statistical}` proposals (§6) |
| `harness/ingest/causal.py` | reuse `ConceptIndex` (scope-aware `prefer_scope`), `_ALIAS_GROUPS`, `beta_confidence`; measured Beta weight (Phase 4) |
| `harness/mcp/graph_server.py` / `harness/api/server.py` | upstream/downstream path-score traversal (drivers/effects), `depth` param |
| `harness/kg/models.py` | metric `source_scope`/`mart`/`grain` lineage props; `INFLUENCES` `temporal_lag`/`evidence_mass` |
| teammate `knowledgeGraph/tools/build_registry_seed.py` | `scope_of()` → fine sub-channel scopes (Phase 2) |

---

## 8. Verification

1. **Skeleton:** per skeleton-spec §19 (scope correctness, additivity, 2 edge types, auto-apply formula only).
2. **Discovery import (dry-run):** `discovered_edges.<t>.csv` → N `INFLUENCES{statistical}` proposals,
   all `review_state: proposed`; unresolved pairs logged; **0 auto-`CAUSES`**.
3. **Granularity:** no coarse edge fans out to multiple sub-channels; coarse edges land at
   `scope_level: platform`.
4. **Beta weight:** an imported edge's `evidence_mass` reflects sample/stability/FDR (not flat 1.0).
5. **Traversal:** drivers/effects return path-score + cumulative lag at a configurable `depth`.

---

## 9. Blockers & out of scope

- **Live series blocker:** fresh temporal discovery needs the BC_ANALYTICS series API (down). Cached
  coarse `discovered_edges.<t>.csv` can be imported meanwhile; fine-grained fresh edges wait on the API.
- **Out of scope:** porting heavy stats deps into dc-kg runtime (Phase "Later" only); model/alias edge
  types; auto-promotion to `CAUSES`; the teammate's flat-file graph as a system-of-record.
