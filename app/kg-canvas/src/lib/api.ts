// Typed REST + SSE wrappers for the ThoughtWire live-canvas backend.
//
// All endpoints live under /api (the Vite dev server proxies /api to the
// FastAPI backend at http://127.0.0.1:8000). The shapes here mirror the pinned
// API contract exactly.

export type Provenance = "deterministic" | "agent" | "human"

export interface GraphNode {
  id: string
  label: string
  title: string
  provenance: Provenance
  props: Record<string, unknown>
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
  mechanism?: string
  source_kind?: string
  deprecated_at?: string
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
  traverseUpstream: (metricUid: string, maxDepth = 3) =>
    getJSON<TraversePayload>(
      `/traverse/upstream?metric_uid=${encodeURIComponent(
        metricUid
      )}&max_depth=${maxDepth}`
    ),
  traverseDownstream: (metricUid: string, maxDepth = 3) =>
    getJSON<TraversePayload>(
      `/traverse/downstream?metric_uid=${encodeURIComponent(
        metricUid
      )}&max_depth=${maxDepth}`
    ),
  metricChart: (metricUid: string) =>
    getJSON<MetricChartPayload>(
      `/metric-chart?metric_uid=${encodeURIComponent(metricUid)}`
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
  maxDepth = 3
): Promise<TraversePayload> {
  return api.traverseUpstream(metricUid, maxDepth)
}

export function traverseDownstream(
  metricUid: string,
  maxDepth = 3
): Promise<TraversePayload> {
  return api.traverseDownstream(metricUid, maxDepth)
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
