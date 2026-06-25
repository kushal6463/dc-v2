# knowledgeGraph ↔ dc-kg — Integration Analysis

> **Scope of this document:** analysis & recommendation only. It explains whether the
> `knowledgeGraph` pipeline should be integrated into `dc-kg`, what exactly should flow, what is
> redundant, what is missing, how it makes the graph more accurate, and whether `knowledgeGraph` has
> deviated from the intended design. **No code is changed by this document.** Saved identically in
> both repos.
>
> Repos analyzed: `/Users/kushal/Desktop/kal/knowledgeGraph` and `/Users/kushal/Desktop/kal/dc-kg`.

---

## 1. Executive summary & verdict

`dc-kg` is a **production Neo4j + Claude-Agent-SDK** knowledge-graph engine with the governance the
product actually needs: a single arbitration writer, a human review canvas, provenance on every edge,
and a Beta-evidence confidence model. `knowledgeGraph` is a **file-based causal-discovery pipeline**
with deep statistical rigor (PCMCI+, Granger, FDR, deseasonalization, stability selection, nonlinear
GPU CMIknn) but no DB, no governance, and (by its own admission) a drift toward being a
time-series tool rather than the intended *breach → Thought* KG.

**Verdict — integrate narrowly. Make `dc-kg` the main repo and pull in `knowledgeGraph`'s one
additive capability — statistical discovery — as an evidence FEED into dc-kg's existing
proposal → review → arbitration path.** Do **not** merge it wholesale, and do **not** keep them
permanently separate.

The decisive reason: **dc-kg today fakes its statistical layer with 4 hand-typed correlation rows**
(`harness/seed/rare_seeds_correlations.json`), and has **zero** statistical time-series discovery
code. `knowledgeGraph` is exactly the engine that should produce those edges — measured, not typed by
hand. The seam already exists (dc-kg already *consumes* a correlations file and routes statistical
edges as review-only proposals); `knowledgeGraph` just needs to *become the producer*.

---

## 2. The two repos at a glance

| | **knowledgeGraph** | **dc-kg** |
|---|---|---|
| **Role** | Causal-discovery **pipeline** (evidence engine) | **System-of-record** KG (the product) |
| **Storage** | Flat files (`graph.<t>.json`, CSVs) | **Neo4j** property graph |
| **Stack** | stdlib + numpy/scipy/statsmodels (+tigramite/torch) | Neo4j, FastAPI+React canvas, **Claude Agent SDK**, MCP, FastMCP |
| **Node id** | `scope.metric[.agg]` (e.g. `blended.revenue`) | `metric_uid = metric:scope:base`, often `metric:<dashboard>:<chart>` |
| **Tenancy** | per-tenant **file** | per-tenant **Neo4j database** |
| **Writes** | scripts overwrite CSVs | **single arbitration writer** (idempotent MERGE), review-gated |
| **Causal edges** | structural, temporal, model, compositional, crossproduct, alias | DECOMPOSES_INTO, ROLLS_UP_TO, CORRELATES_WITH, INFLUENCES, CAUSES |
| **Statistical discovery** | **Yes** — FDR, deseasonalize, stability, PCMCI+, nonlinear CMIknn | **None** — relies on a static seed + LLM judgment |
| **Governance / review / LLM** | none | review canvas, provenance, Beta evidence, LLM judge+refuter |
| **Authoritative for** | *measuring* statistical/temporal associations | *everything definitional & structural*, identity, governance |

**Both use the same pilot tenant `rare_seeds`** — they are two halves of one system that have never
been wired together.

---

## 3. Is integration required? (the four options)

| Option | Verdict | Why |
|---|---|---|
| **(a) Merge wholesale** | ✗ Rejected | Forces one repo to carry both the scientific stack *and* Neo4j/SDK/FastAPI/React. Imports a second node-identity scheme + a second registry into dc-kg, breaking its single-source-of-truth & single-writer invariants. Most of knowledgeGraph (registry, catalog, UI, state) duplicates dc-kg. |
| **(b) Evidence PROVIDER → dc-kg** | ✅ **Recommended** | dc-kg stays the system-of-record; knowledgeGraph's only additive part (statistical discovery) feeds dc-kg's existing proposal/arbitration path. Preserves both repos' invariants; the seam already exists. |
| **(c) Keep fully separate** | ◑ Only as the *current* phase | dc-kg keeps shipping statistically-unfounded hand-typed correlations; knowledgeGraph keeps producing rigorous edges nothing consumes. The two best assets never meet. |
| **(d) Shared stats library / in-process call** | ◐ Premature | Couples release cycles and pulls heavy scientific deps into dc-kg's runtime. The file-handoff in (b) gives the same accuracy benefit with zero runtime coupling. Revisit only if real-time discovery is needed. |

**Recommendation (b):** `knowledgeGraph` becomes a *headless statistical discovery service*. The only
thing that crosses the boundary is **edge-only `CORRELATES_WITH` proposals** (plus a small gated set
of `INFLUENCES` candidates), emitted in dc-kg's proposal format. Everything else stays on its own
side.

---

## 4. What should flow — and what must NOT

dc-kg is **authoritative for everything definitional and structural** (it computes those
deterministically at confidence 1.0, tied to its own identity). `knowledgeGraph` should send **only
what dc-kg cannot measure**: real, FDR-controlled, deseasonalized statistical associations + lag.

| knowledgeGraph provider | dc-kg edge type | Flow? | Rationale |
|---|---|---|---|
| **temporal** (PCMCI+/Granger/CMIknn, FDR-gated, deseasonalized, conditioned) | **`CORRELATES_WITH`** (default) → narrow **`INFLUENCES`** (gated, review-only) | **YES — the whole point** | dc-kg has no way to measure this. This is the irreplaceable, additive output. **Never auto-promote to `CAUSES`.** |
| structural (formula / identity, conf 1.0) | DECOMPOSES_INTO | **NO (redundant)** | dc-kg's `formula_edges()` already parses `formula_text` → DECOMPOSES_INTO at conf 1.0. Two engines emitting the same deterministic edge invites conflict; dc-kg wins. |
| crossproduct (channel → blended) | ROLLS_UP_TO | **NO (redundant)** | dc-kg's `rollup_edges()` already emits ROLLS_UP_TO within `concept_key` groups at conf 1.0. |
| compositional (funnel stage→stage) | DECOMPOSES_INTO | **NO (risky)** | Definitional progressions dc-kg derives structurally; importing as statistical would mislabel them. |
| model (feature→model→target) | (no V1 equivalent) | **NO (out of scope)** | dc-kg has no model-structure edge type in V1. Defer; later → `INFLUENCES` with a `model_structure` tag, review-gated. |
| alias (same_as, conf 1.0) | (no `SAME_AS` edge type) | **NO (blocked)** | dc-kg models identity via shared `concept_key` + ROLLS_UP_TO, not an alias edge. Useful later as Metric `synonyms`/`aliases` **metadata**, not an edge. |

**Net: exactly one provider crosses the boundary as edges — `temporal`.** The other five are either
already done deterministically by dc-kg or have no home in its V1 vocabulary. *This is the precise
statement of "only the statistical discovery core is additive."*

---

## 5. The contract artifact (the boundary)

The handoff is a **file**, not an in-process call — which keeps `tigramite`/`statsmodels`/GPU CMIknn
out of dc-kg's runtime.

- **Producer side (knowledgeGraph):** `data/discovered_edges.<tenant>.csv` — columns
  `src, dst, lag, corr, granger_p, mi, discovery_score, stability, cond_corr, method, fdr_pass`.
  **Prefer the CSV over `graph.<t>.json`**, because `export_graph.py` flattens the rich statistics
  into a single human-readable `evidence` string (lossy for the importer).
- **Consumer side (dc-kg):** an importer would emit **edge-only proposals**
  (`operation: "upsert_edge"`, `target_label: "Metric"`, `key_field: "metric_uid"`) into the existing
  proposal queue. From there the **existing** review → arbitration (`upsert_edge`) → apply path takes
  over unchanged.

**dc-kg has no generic JSON-graph importer today** — confirmed; every ingress is node proposals
(prepass/proposer) or edge proposals (causal). The one piece that would need *describing* (not built
here) is a small `harness/ingest/import_kg.py` mirroring the existing `correlation_edges()` shape.

---

## 6. Bridging work (described, not implemented)

### 6.1 Identity resolver — `scope.metric[.agg]` ↔ `metric_uid`
Not a pure string transform; three mismatches:
1. **Format:** `blended.revenue` → `metric:blended:revenue`. But knowledgeGraph ids can carry a third
   `.agg` segment (`blended.reach.sum`) with no slot in `metric:scope:base` — fold into the base or
   drop and rely on dc-kg's `aggregation` field.
2. **Scope vs dashboard:** dc-kg's *live* `metric_uid` is often `metric:<dashboard>:<chart>`, so a
   clean `metric:blended:revenue` may not exist. The robust resolver should resolve through
   **`concept_key` + scope**, reusing dc-kg's own `ConceptIndex` (in `harness/ingest/causal.py`) —
   **scope-aware** so `meta.impressions` maps to the meta-scoped metric, not the broadest one.
3. **Vocabulary:** knowledgeGraph bases (`aov`, `ltv`, `cpc`) vs dc-kg (`average_order_value`). dc-kg
   already maintains `_ALIAS_GROUPS` for exactly this — reuse it so resolution agrees with the
   existing correlation import.

The resolver belongs **inside dc-kg** (where the concept index + metric list already are);
knowledgeGraph stays identity-agnostic and just ships concept endpoints — exactly like the existing
seed's `{"a":"spend","b":"revenue"}` shape.

### 6.2 Edge-kind → edge-type mapping
| kg `kind` | condition | dc-kg `rel_type` | dc-kg `source_kind` |
|---|---|---|---|
| temporal | FDR-passing | `CORRELATES_WITH` | `kg_discovery` (a new statistical-proposal tag) |
| temporal | conditioned PCMCI+ survivor, oriented | `INFLUENCES` (review-only) | `kg_discovery` |
| structural / compositional / crossproduct / model / alias | — | *dropped* (see §4) | — |

### 6.3 Statistical fields → dc-kg props + Beta weights
Map into `CORRELATES_WITH` props (schema §6: `correlation, p_value, lag, sample_size`):

| kg field | dc-kg prop | transform |
|---|---|---|
| `corr` / PCMCI+ MCI | `correlation` | direct (partial corr after conditioning — strictly better than the seed's raw r) |
| `granger_p` | `p_value` | direct |
| `lag` | `lag` | direct (a *measured* lag, vs the seed's hand-typed one) |
| series length | `sample_size` | add to the export |
| `method` | `method` / provenance | `pcmci+` vs `granger` |
| `mi`, `stability`, `cond_corr`, `fdr_pass` | extra props | carry for the reviewer + the Beta weight |

Then fold into dc-kg's existing **`beta_confidence(support, oppose)`**: replace the flat
`evidence_mass = 1.0` placeholder (used for the hand-typed seeds) with a **measured mass**
`support_weight = base × f(sample_size) × g(stability) × (fdr_pass ? 1 : 0.3)`. This is the concrete
realization of dc-kg's deferred "observational" evidence tier (its schema literally says "scaled by
effect size, sample, FDR, lag") — knowledgeGraph supplies the per-record weights; dc-kg's fold stays
unchanged.

---

## 7. Blockers & sequencing

1. **Nodes before edges.** dc-kg's `upsert_edge` returns `missing_endpoint` (it won't create dangling
   edges), so the import can only land edges for metrics dc-kg has already ingested (M2). Sequencing:
   **dc-kg metric ingestion → kg import**. Missing-endpoint edges are skipped gracefully and complete
   on re-run once the node lands — a sequencing constraint, not a blocker.
2. **Concept-resolution coverage.** Expect a non-trivial skip rate on first import until
   `_ALIAS_GROUPS` + `concept_key`s are reconciled. Log every unresolved pair (no silent drops),
   mirroring dc-kg's existing `corr_skipped` discipline. Tuning task.
3. **Export lossiness.** `export_graph.py` flattens temporal stats into an `evidence` string and never
   exports `sample_size` — read `discovered_edges.<t>.csv` directly, or extend the export.
4. **No `SAME_AS` edge type** in dc-kg → the alias provider has no edge home. Skip aliases for V1.
5. **Live data dependency.** knowledgeGraph's temporal discovery needs the BC_ANALYTICS series API,
   which is currently **down (503/404)** and warehouse reads are sandboxed off. Until provisioned,
   knowledgeGraph can only emit cached/previously-discovered edges. **This is the real-world blocker**
   — the *value* of the integration is gated on knowledgeGraph having live series.
6. **Keep heavy deps out of dc-kg.** `tigramite`/`statsmodels`/GPU CMIknn must not leak into dc-kg's
   `pyproject.toml`. The file-handoff is what keeps them isolated.
7. **Tenant guard.** Both default to `rare_seeds`, but dc-kg's tenant = the Neo4j database while
   knowledgeGraph's = the file. An importer must assert it writes to the matching database.

---

## 8. How knowledgeGraph makes dc-kg's graph MORE accurate

Today dc-kg's statistical layer is **4 hand-typed rows** (`spend→revenue r=0.82 lag 7`, etc.) with no
measurement behind them. knowledgeGraph replaces them with measured evidence:

1. **Hand-typed → FDR-controlled.** Benjamini–Hochberg across the candidate batch gives a controlled
   false-discovery rate instead of an asserted number; `fdr_pass` becomes a real gate + Beta weight.
2. **Raw r → deseasonalized partial correlation.** STL removes shared weekly/seasonal trend before
   testing on residuals, so the imported `correlation` reflects genuine co-movement, not seasonality.
3. **Stability selection → fewer flukes.** Only edges surviving a majority of sub-windows reach the
   review queue — less reviewer load, fewer false edges.
4. **Real lag estimates.** Lag is estimated per pair from cross-correlation, not hand-coded.
5. **PCMCI+ conditioning removes common-cause confounds → fewer false `INFLUENCES`.** The most
   important gain: feeding *conditioned* survivors as the INFLUENCES candidate set means dc-kg's
   expensive LLM judge+refuter+Beta pipeline only fires on pairs that already passed a confound
   test — higher precision, lower token cost.
6. **Nonlinear CMIknn catches links ParCorr misses.** Diminishing-returns relationships (spend→roas)
   are exactly where linear partial correlation under-detects; CMIknn surfaces associations dc-kg's
   linear/LLM path would never propose.
7. **Measured evidence mass.** Replacing the flat `evidence_mass = 1.0` with a sample/stability/FDR-
   derived mass makes dc-kg's Beta confidence meaningful — finally honoring its own note that "0.80
   from one weak prior ≠ 0.80 from many outcomes."

Net effect: dc-kg's correlation layer goes from *illustrative* to *evidence-backed*, and its
INFLUENCES candidate generation gets a confound-filtered, nonlinear-aware front end.

---

## 9. Deviation assessment

**Has knowledgeGraph deviated? Yes — and it says so.** `docs/07-KG-ARCHITECTURE.md`: *"the KG we
intended, not the time-series-discovery tool we drifted into."* The intended product (breach → node →
drivers/effects → Thought) needs **state + typed edges + metadata** and "barely uses time series at
all." knowledgeGraph over-invested in temporal edges + a flat-file model and under-built state and
governance.

**Is dc-kg the truer realization? Yes.** dc-kg delivers exactly the intended architecture — a Metric
hub with a tri-axis spine, deterministic structural/rollup edges, an evidence-backed causal layer,
provenance on every edge, human review before write, single-writer arbitration, and the Beta evidence
model — all the governance knowledgeGraph lacks.

**Is the deviation nonetheless valuable? Strongly yes — and that is the whole case for integration
(b).** knowledgeGraph's drift produced the one thing dc-kg cannot build for itself: a rigorous,
FDR-controlled, deseasonalized, PCMCI+-conditioned, nonlinear-aware discovery engine. dc-kg's causal
layer is, by its own design, leaning on 4 hand-typed correlations + LLM judgment with no real
statistical discovery. The "wrong turn" is precisely the missing organ in dc-kg.

> **dc-kg is the body; knowledgeGraph is the statistical sense organ it never grew. Wire the organ to
> the body through the proposal queue — don't graft on a second body.**

---

## 10. Why CMIknn? (asked explicitly)

CMIknn = a *k*-nearest-neighbour estimator of **Conditional Mutual Information**, used as PCMCI+'s
conditional-independence test. Two reasons it matters for accuracy:
- **Nonlinearity:** the default `ParCorr` only detects *linear* dependence. Conditional MI captures
  *arbitrary nonlinear* dependence — essential for relationships like ad-spend → ROAS (diminishing
  returns).
- **Confound removal:** by testing independence *conditioned* on other variables, it distinguishes a
  **direct** causal link from one explained away by a **common cause** — pruning spurious edges that a
  pairwise correlation (and dc-kg's static seed) cannot.

The cost is a permutation null test; hence `knowledgeGraph`'s **GPU-batched** `cmi_gpu.py`, which
makes it tractable at scale. `docs/diff.html` visualizes the payoff: edges found by both ParCorr and
CMIknn, linear-only, and nonlinear-only.

---

## 11. Recommended phased rollout (for when code work is later approved)

| Phase | Action | Risk | Value |
|---|---|---|---|
| **0 (now)** | Keep repos separate; this analysis documents the seam. | none | clarity |
| **1** | dc-kg `import_kg.py`: import **temporal → `CORRELATES_WITH`** only, behind review. Replaces the 4 hand-typed seeds. | low (review-gated, no auto-`CAUSES`) | evidence-backed correlations |
| **2** | Feed conditioned PCMCI+ survivors as the **`INFLUENCES` candidate set** into dc-kg's existing LLM judge pass. | low | higher-precision, cheaper INFLUENCES |
| **3** | Replace flat `evidence_mass` with **measured Beta weights** (sample/stability/FDR). | low | meaningful confidence |
| **Later** | Optionally port `discover_engine.py` + `cmi_gpu.py` into `dc-kg/harness/discovery/` behind an optional extra, so dc-kg runs discovery natively. | higher (deps + live API) | one repo |

Each phase ships independently and is reversible; **nothing auto-promotes to `CAUSES`.**

## 12. Explicitly out of scope for V1
Model edges (no dc-kg type), alias → `SAME_AS` (no type), real-time/in-process discovery, a shared
statistics library, and any change to dc-kg's writer/review invariants.

---

*Companion document: `knowledgeGraph/ARCHITECTURE-claude.md` (file-by-file architecture of the
discovery pipeline).*
