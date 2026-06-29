// Typed REST + SSE wrappers for the ThoughtWire live-canvas backend.
//
// All endpoints live under /api (the Vite dev server proxies /api to the
// FastAPI backend at http://127.0.0.1:8000). The shapes here mirror the pinned
// API contract exactly.

// "synthetic" tags client-only VIEW nodes/edges (e.g. the shift-click Chart node)
// that are never persisted to the graph — see store.revealMetricChart (FR-CG-008).
export type Provenance = "deterministic" | "agent" | "human" | "synthetic"

export interface GraphNode {
  id: string
  label: string
  title: string
  provenance: Provenance
  props: Record<string, unknown>
}

/**
 * Metric-node `props` fields. Metric nodes deliver their attributes inside the
 * generic `GraphNode.props` record; this interface documents the metric-specific
 * shape so callers can narrow (`node.props as MetricProps`). Only the recently
 * added mart-lineage / SQL-provenance / data-quality fields are enumerated here
 * — every other prop remains reachable via the generic record.
 */
export interface MetricProps {
  /** dbt mart source identifiers this metric is built from. */
  mart_sources?: string[]
  /** Warehouse column names this metric reads (drives /api/column-impact). */
  source_columns?: string[]
  /** Verbatim backend SQL (from `get_bc2_sql`). */
  sql_query_real?: string
  /** LLM-generated clean, runnable canonical `SELECT`. */
  sql_query_canonical?: string
  /** ISO date — data-coverage start. */
  history_start?: string
  /** ISO date — data-coverage end. */
  history_end?: string
  /** Latest data is older than the freshness SLA. */
  data_stale?: boolean
  /** QA flag — `formula_text` disagrees with `sql_query_real`. */
  formula_sql_mismatch?: boolean
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: string
  props: Record<string, unknown>
  // Edge metadata surfaced for the EdgeDetail panel, filters and styling. All
  // optional — older payloads (and non-metric edges) may not carry them.
  relation?: string
  status?: string
  confidence?: number
  evidence_mass?: number
  scoring_policy?: string
  review_state?: string
  temporal_lag?: string
  /** Plausibility (0..1) that `temporal_lag` is mechanistically credible; a path-score factor (confidence × lag_plausibility). */
  lag_plausibility?: number
  mechanism?: string
  source_kind?: string
  deprecated_at?: string
  /** True when the edge spans two disjoint metric domains (cross-domain link; drawn violet + dashed). */
  cross_domain?: boolean
  // Machine-discovery stats. Arrive inside edge.props for source_kind ==
  // 'kg_discovery'; typed here for direct access in the EdgeDetail panel.
  method?: string
  granger_p?: number
  discovery_score?: number
  stability?: number
  fdr_pass?: boolean
  mi?: number
  cond_corr?: number
  correlation?: number
  sign?: string
}

export interface GraphPayload {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface StatusPayload {
  nodes: Record<string, number>
  edges: Record<string, number>
}

export interface DashboardInfo {
  dashboard_id: string
  components: number
  metrics: number
  ingested: boolean
}

export interface RelationshipPayload {
  type: string
  from_label: string
  from_id: string
  to_label: string
  to_id: string
  properties?: Record<string, unknown>
}

export interface Proposal {
  proposal_id: string
  operation: string
  target_label: string
  target_id: string
  key_field?: string | null
  source_kind?: string
  source_ref?: string
  source_confidence?: number | null
  review_state: string
  run_id?: string
  dashboard_id?: string
  payload: Record<string, unknown>
  relationship_payloads?: RelationshipPayload[]
  reviewed_at?: string
  review_reason?: string
}

export interface ProposalsPayload {
  run_id: string | null
  proposals: Proposal[]
}

export type ReviewAction = "approve" | "reject" | "edit"

export interface ReviewResult {
  ok: boolean
  state: string
}

export interface IngestOptions {
  dashboard_id?: string
  all?: boolean
  concurrency?: number
  auto_approve?: boolean
}

export interface IngestResult {
  run_id: string
}

// ---------------------------------------------------------------------------
// Coverage / edge-diff / traversal payloads
// ---------------------------------------------------------------------------

export interface CoveragePayload {
  tenant: string
  generated_at?: string
  run_id?: string | null
  metrics?: Record<string, unknown>
  edges?: Record<string, unknown>
  [key: string]: unknown
}

export interface EdgeDiffEntry {
  edge_id?: string
  source?: string
  target?: string
  type?: string
  relation?: string
  change?: string
  [key: string]: unknown
}

export interface EdgeDiffPayload {
  tenant: string
  run_id: string | null
  added?: EdgeDiffEntry[]
  removed?: EdgeDiffEntry[]
  changed?: EdgeDiffEntry[]
  deprecated?: EdgeDiffEntry[]
  [key: string]: unknown
}

// ---------------------------------------------------------------------------
// Traversal payloads — signed lineage paths.
//
// /api/traverse/{upstream,downstream} now return ranked paths split into acyclic
// (`paths`) and loop-bearing (`cyclic_paths`) sets, each path carrying a
// `path_sign` (product of per-hop signs: +1 reinforcing, -1 dampening, 0 when a
// hop's direction is unknown — e.g. a causal INFLUENCES hop or a contains-causal
// chain). Every hop carries its structural `role` + derived `sign`.
// ---------------------------------------------------------------------------

/** One hop on a lineage path (a single DECOMPOSES_INTO / INFLUENCES edge). */
export interface TraverseHop {
  from: string | null
  to: string | null
  rel_type: string | null
  relation: string | null
  /** 'structural' = made-of decomposition; 'causal' = driven-by influence. */
  kind: "structural" | "causal" | null
  /** Structural decomposition role (numerator/denominator/…); null on causal. */
  role: string | null
  /** Per-hop sign: +1 additive, -1 inverse (denominator/subtrahend), 0 unknown. */
  sign: number
  confidence: number | null
  temporal_lag: string | null
}

/** One ranked lineage path: a metric chain + its scored, signed hops. */
export interface TraversePath {
  nodes: string[]
  edges: TraverseHop[]
  /** Product of edge confidences along the path (path strength). */
  score: number
  cumulative_lag: number
  /** Product of per-hop signs: +1 reinforcing, -1 dampening, 0 contains a causal/unknown hop. */
  path_sign: number
}

/** Wire shape of a single traverse direction's response. */
export interface TraversePayload {
  paths: TraversePath[]
  cyclic_paths: TraversePath[]
  summary: { acyclic_count: number; cyclic_count: number }
}

// ---------------------------------------------------------------------------
// Column-impact payload — warehouse-column blast radius.
//
// GET /api/column-impact?column= scans every Metric's `source_columns` and
// returns the metrics that read the given warehouse column ("which metrics break
// if this column changes?"). It walks no edges — a flat property scan — so each
// row carries only the pinned identity/lineage fields (values may be null).
// ---------------------------------------------------------------------------

/** One metric whose `source_columns` contains the queried column. */
export interface ColumnImpactMetric {
  metric_uid: string | null
  display_name: string | null
  mart_sources: string[] | null
  domain_ids: string[] | null
}

/** Wire shape of GET /api/column-impact. */
export interface ColumnImpactPayload {
  column: string
  count: number
  metrics: ColumnImpactMetric[]
}

// ---------------------------------------------------------------------------
// Metric-chart payload (shift-click chart panel).
//
// /api/metric-chart returns the live metric's chart_type + the single matching
// chart-registry entry (registry-slice, never the whole file) + a verbatim
// series_endpoint passthrough. The canvas renders ONE canonical chart per
// chart_type from this (charts are hidden otherwise).
// ---------------------------------------------------------------------------

export interface ChartRegistryEntry {
  id?: string
  title?: string
  formula?: string
  formula_explanation?: string
  how_to_read?: string[]
  decisions_answered?: string[]
  narration_text?: string
  canonical_id?: string
  concept?: string
  scope?: string
  [key: string]: unknown
}

export interface MetricChartPayload {
  found: boolean
  metric_uid: string
  chart_type: string | null
  chart_id: string | null
  registry_entry: ChartRegistryEntry | null
  series_endpoint: string | null
}

// All chart-registry entries for one dashboard (the shift-click-a-Dashboard
// reveal). Each entry carries chart_id / canonical_id / chart_type / formula /
// narrative; metric-less composite charts appear here too.
export interface DashboardChartsPayload {
  dashboard_id: string
  count: number
  charts: ChartRegistryEntry[]
}

// ---------------------------------------------------------------------------
// Governance — Policy & Threshold authoring (left-drawer wizard).
//
// POST /api/governance writes a Policy node + a Threshold node + the 3 governance
// edges against a metric. POST /api/governance/extract LLM-parses pasted/uploaded
// text into a draft {policy, threshold} to PREFILL the wizard (it does not write).
// Drafts are loose field maps mirroring the Pydantic Policy/Threshold fields;
// unknown keys are dropped server-side, so the client stays forward-compatible.
// ---------------------------------------------------------------------------

/** Draft Policy fields the wizard collects / the extractor returns. */
export interface PolicyDraft {
  policy_name?: string
  description?: string
  policy_type?: string
  condition_type?: string
  condition_operator?: string
  condition_value?: number
  condition_value_high?: number
  evaluation_window?: string
  severity?: string
  approval_required?: boolean
}

/** Draft Threshold fields: static bands + percentile distribution + industry. */
export interface ThresholdDraft {
  threshold_type?: string
  operator?: string
  direction?: string
  unit?: string
  severity?: string
  warning_value_num?: number
  critical_value_num?: number
  target_value_num?: number
  p95_val?: number
  p85_val?: number
  p75_val?: number
  p50_val?: number
  percentile_basis?: string
  industry_standard_val?: number
  industry_min_val?: number
  industry_max_val?: number
  industry_source?: string
  industry_as_of?: string
  current_val?: number
  current_as_of?: string
  explanation?: string
}

/** Request body for POST /api/governance.
 *
 * A metric may carry several policies (alerting / budget / SLA …) via `policies`,
 * all enforcing the one shared `threshold`. The singular `policy`/`policy_id` are
 * still accepted for back-compat. */
export interface GovernanceBody {
  metric_uid: string
  policy_id?: string
  threshold_id?: string
  policy?: PolicyDraft
  policies?: PolicyDraft[]
  threshold: ThresholdDraft
}

/** Result of POST /api/governance. */
export interface GovernanceResult {
  status: string
  metric_uid: string
  /** First policy, kept for back-compat; `policies` is the full list. */
  policy: { status: string; key: string }
  policies: { status: string; key: string }[]
  threshold: { status: string; key: string }
  edges: { rel_type: string; status: string }[]
  warning: string | null
}

/** Draft returned by POST /api/governance/extract (prefills the wizard). */
export interface GovernanceDraft {
  policy: PolicyDraft
  threshold: ThresholdDraft
  /** Set when extraction failed; the wizard shows it and prefills nothing. */
  error?: string
}

// ---------------------------------------------------------------------------
// SSE event shape
// ---------------------------------------------------------------------------

export type CanvasEventType =
  | "run_started"
  | "ingest_progress"
  | "agent_action"
  | "proposal_new"
  | "node_written"
  | "run_done"
  | "error"

export interface CanvasEvent {
  type: CanvasEventType | string
  run_id: string | null
  ts: string
  data: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// REST helpers
// ---------------------------------------------------------------------------

const API_BASE = "/api"

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
  })
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw new Error(`POST ${path} failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

export const api = {
  health: () => getJSON<{ ok: boolean }>("/health"),
  status: () => getJSON<StatusPayload>("/status"),
  graph: (limit = 2000, includeDeprecated = false) =>
    getJSON<GraphPayload>(
      `/graph?limit=${limit}${includeDeprecated ? "&include_deprecated=true" : ""}`
    ),
  coverage: (tenant = "rare_seeds") =>
    getJSON<CoveragePayload>(`/coverage?tenant=${encodeURIComponent(tenant)}`),
  edgeDiff: (tenant = "rare_seeds", runId?: string) =>
    getJSON<EdgeDiffPayload>(
      `/edge-diff?tenant=${encodeURIComponent(tenant)}${
        runId ? `&run_id=${encodeURIComponent(runId)}` : ""
      }`
    ),
  traverseUpstream: (metricUid: string, maxDepth = 3, minConfidence?: number) =>
    getJSON<TraversePayload>(
      `/traverse/upstream?metric_uid=${encodeURIComponent(
        metricUid
      )}&max_depth=${maxDepth}${
        minConfidence != null ? `&min_confidence=${minConfidence}` : ""
      }`
    ),
  traverseDownstream: (metricUid: string, maxDepth = 3, minConfidence?: number) =>
    getJSON<TraversePayload>(
      `/traverse/downstream?metric_uid=${encodeURIComponent(
        metricUid
      )}&max_depth=${maxDepth}${
        minConfidence != null ? `&min_confidence=${minConfidence}` : ""
      }`
    ),
  columnImpact: (column: string) =>
    getJSON<ColumnImpactPayload>(
      `/column-impact?column=${encodeURIComponent(column)}`
    ),
  metricChart: (metricUid: string) =>
    getJSON<MetricChartPayload>(
      `/metric-chart?metric_uid=${encodeURIComponent(metricUid)}`
    ),
  dashboardCharts: (dashboardId: string) =>
    getJSON<DashboardChartsPayload>(
      `/dashboard-charts?dashboard_id=${encodeURIComponent(dashboardId)}`
    ),
  dashboards: () =>
    getJSON<{ dashboards: DashboardInfo[] }>("/dashboards"),
  proposals: (runId?: string) =>
    getJSON<ProposalsPayload>(
      runId ? `/proposals?run_id=${encodeURIComponent(runId)}` : "/proposals"
    ),
  reviewProposal: (
    proposalId: string,
    body: {
      action: ReviewAction
      run_id: string
      reason?: string
      payload?: Record<string, unknown>
    }
  ) =>
    postJSON<ReviewResult>(
      `/proposals/${encodeURIComponent(proposalId)}/review`,
      body
    ),
  ingest: (opts: IngestOptions) => postJSON<IngestResult>("/ingest", opts),
  approveAll: (runId: string) =>
    postJSON<{ run_id: string; approved: number }>("/proposals/approve-all", {
      run_id: runId,
    }),
  // NOTE: POST /api/run-causal is retired (returns 501) — graph construction
  // moved to the agentic builder (`kg build` / harness.agentic), not a canvas
  // button, so there is intentionally no api.runCausal client.
  apply: (runId: string) =>
    postJSON<Record<string, unknown>>("/apply", { run_id: runId }),
  // Governance authoring (left-drawer wizard).
  createGovernance: (body: GovernanceBody) =>
    postJSON<GovernanceResult>("/governance", body),
  extractGovernance: (body: {
    text: string
    metric_uid?: string
    metric_name?: string
  }) => postJSON<GovernanceDraft>("/governance/extract", body),
}

// ---------------------------------------------------------------------------
// Named fetch helpers (thin wrappers over `api` for direct import).
// ---------------------------------------------------------------------------

export function fetchCoverage(tenant = "rare_seeds"): Promise<CoveragePayload> {
  return api.coverage(tenant)
}

export function fetchEdgeDiff(
  tenant = "rare_seeds",
  runId?: string
): Promise<EdgeDiffPayload> {
  return api.edgeDiff(tenant, runId)
}

export function traverseUpstream(
  metricUid: string,
  maxDepth = 3,
  minConfidence?: number
): Promise<TraversePayload> {
  return api.traverseUpstream(metricUid, maxDepth, minConfidence)
}

export function traverseDownstream(
  metricUid: string,
  maxDepth = 3,
  minConfidence?: number
): Promise<TraversePayload> {
  return api.traverseDownstream(metricUid, maxDepth, minConfidence)
}

export function columnImpact(column: string): Promise<ColumnImpactPayload> {
  return api.columnImpact(column)
}

// ---------------------------------------------------------------------------
// SSE subscription
// ---------------------------------------------------------------------------

/** Every event type the backend may emit as a NAMED SSE frame. */
export const CANVAS_EVENT_TYPES: CanvasEventType[] = [
  "run_started",
  "ingest_progress",
  "agent_action",
  "proposal_new",
  "node_written",
  "run_done",
  "error",
]

/**
 * Subscribe to the live event stream. Returns an unsubscribe function that
 * closes the underlying EventSource.
 *
 * The backend emits NAMED SSE frames (`event: <type>\ndata: {...}` via
 * sse-starlette), so a bare `source.onmessage` never fires for them. We
 * therefore register an explicit listener for every known event type AND keep
 * `onmessage` as a fallback so the canvas works whether frames are named or
 * unnamed.
 */
export function subscribeEvents(
  onEvent: (ev: CanvasEvent) => void,
  onError?: (err: Event) => void
): () => void {
  const source = new EventSource(`${API_BASE}/events`)

  const handle = (msg: MessageEvent<string>) => {
    if (!msg.data) {
      return
    }
    try {
      const parsed = JSON.parse(msg.data) as CanvasEvent
      onEvent(parsed)
    } catch {
      // Ignore keep-alive / non-JSON frames.
    }
  }

  // Named frames: one listener per event type.
  for (const type of CANVAS_EVENT_TYPES) {
    source.addEventListener(type, handle as EventListener)
  }
  // Unnamed frames (default "message" event): fallback path.
  source.onmessage = handle

  source.onerror = (err) => {
    onError?.(err)
  }

  return () => {
    for (const type of CANVAS_EVENT_TYPES) {
      source.removeEventListener(type, handle as EventListener)
    }
    source.close()
  }
}
