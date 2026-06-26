// Zustand store: graph state, live ingestion events, proposals + review.

import { create } from "zustand"
import { persist } from "zustand/middleware"

import {
  api,
  type CanvasEvent,
  type CoveragePayload,
  type DashboardInfo,
  type EdgeDiffPayload,
  type GraphEdge,
  type GraphNode,
  type IngestOptions,
  type MetricChartPayload,
  type Proposal,
  type ReviewAction,
  type StatusPayload,
} from "@/lib/api"

export interface Progress {
  dashboard: string
  done: number
  total: number
}

// ---------------------------------------------------------------------------
// Edge filter + traversal UI state
// ---------------------------------------------------------------------------

export type EdgeTypeFilter = "DECOMPOSES_INTO" | "INFLUENCES" | "all"
export type StatusFilter = "active" | "deprecated" | "all"
export type TraversalMode = "off" | "upstream" | "downstream"

/**
 * Which inspector tab is shown. Mirrors App's tab set. Lives in the store (and
 * is persisted) so it survives reloads and is decoupled from the filter panel.
 * `null` = "auto": follow the current selection (edge → "edge", node → "detail",
 * else "activity"). A non-null value is a user-pinned tab.
 */
export type SidebarTab = "activity" | "review" | "detail" | "edge" | "diff"

export interface EdgeFilters {
  edgeType: EdgeTypeFilter
  /** Relation subtype (e.g. formula, component, curated_rule); null = any. */
  relation: string | null
  /** review_state subtype (e.g. approved, pending); null = any. */
  reviewState: string | null
  status: StatusFilter
  showDeprecated: boolean
  /** Inclusive [min, max] confidence range, 0..1. */
  confidence: [number, number]
}

export const DEFAULT_EDGE_FILTERS: EdgeFilters = {
  edgeType: "all",
  relation: null,
  reviewState: null,
  status: "active",
  showDeprecated: false,
  confidence: [0, 1],
}

export interface TraversalResult {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/**
 * Transient "locate" request the canvas watches: when set, the canvas should
 * fitView onto the target and flash a highlight. `ts` makes repeated locates of
 * the same target distinct so the effect re-fires.
 */
export interface LocateRequest {
  kind: "node" | "edge"
  id: string
  ts: number
}

/** Lightweight node row for the command/search palette. */
export interface NodeSearchEntry {
  id: string
  label: string
  scope: string | null
  kind: string | null
}

/**
 * The metric-chart panel state: the payload returned by `shiftClickMetric` plus
 * a loading flag. `null` payload = no chart open (charts are hidden otherwise).
 */
export interface MetricChartState {
  metricUid: string
  title: string
  loading: boolean
  payload: MetricChartPayload | null
}

const MAX_ACTIVITY = 200

interface CanvasState {
  nodes: GraphNode[]
  edges: GraphEdge[]
  dashboards: DashboardInfo[]
  proposals: Proposal[]
  status: StatusPayload | null
  runId: string | null
  progress: Progress | null
  running: boolean
  applying: boolean
  connected: boolean
  activity: string[]
  selectedNodeId: string | null
  focusNodeId: string | null
  hoveredId: string | null
  error: string | null

  // Causal-focus exploration state.
  focusMode: "causal" | "all"
  focusDir: { up: boolean; down: boolean }
  ringCap: number
  growKind: string | null
  trail: { id: string; title: string }[]

  // Edge inspection + filtering + traversal state.
  selectedEdgeId: string | null
  edgeFilters: EdgeFilters
  traversalMode: TraversalMode
  traversalRootId: string | null
  traversalResult: TraversalResult | null
  /** Read-time confidence floor (0..1) passed to the traverse API; 0 = off. */
  traverseMinConfidence: number
  coverage: CoveragePayload | null
  edgeDiff: EdgeDiffPayload | null

  // Command/search palette + locate + scope/domain faceting.
  searchOpen: boolean
  locateRequest: LocateRequest | null
  scopeFilter: string[] | null
  domainFilter: string[] | null

  // Persisted inspector + filter-panel UI state (see persist() partialize). The
  // filter panel (filtersOpen) is INDEPENDENT of the inspector tab (sidebarTab):
  // switching tabs never hides the filter, and navigation never resets filters.
  inspectorOpen: boolean
  /** Pinned inspector tab, or null = follow selection (auto). */
  sidebarTab: SidebarTab | null
  filtersOpen: boolean

  // Shift-click metric chart panel (one canonical chart per chart_type).
  metricChart: MetricChartState | null

  loadGraph: () => Promise<void>
  loadDashboards: () => Promise<void>
  loadProposals: (runId?: string) => Promise<void>
  loadStatus: () => Promise<void>
  startIngest: (opts: IngestOptions) => Promise<void>
  approveAll: () => Promise<void>
  applyRun: (runId?: string) => Promise<void>
  reviewProposal: (
    proposalId: string,
    action: ReviewAction,
    reason?: string,
    payload?: Record<string, unknown>
  ) => Promise<void>
  handleEvent: (ev: CanvasEvent) => void
  setConnected: (connected: boolean) => void
  selectNode: (id: string | null) => void
  setFocus: (id: string | null) => void
  setHovered: (id: string | null) => void
  setFocusMode: (mode: "causal" | "all") => void
  setFocusDir: (dir: { up: boolean; down: boolean }) => void
  setRingCap: (cap: number) => void
  setGrowKind: (kind: string | null) => void

  // Edge inspection + filtering + traversal actions.
  selectEdge: (id: string | null) => void
  setEdgeFilters: (patch: Partial<EdgeFilters>) => void
  resetEdgeFilters: () => void
  setShowDeprecated: (show: boolean) => void
  setTraversalMode: (mode: TraversalMode) => void
  runTraversal: (
    metricUid: string,
    mode: "upstream" | "downstream",
    maxDepth?: number
  ) => Promise<void>
  clearTraversal: () => void
  setTraverseMinConfidence: (value: number) => void
  loadCoverage: (tenant?: string) => Promise<void>
  loadEdgeDiff: (tenant?: string, runId?: string) => Promise<void>

  // Command/search palette + locate + scope/domain faceting actions.
  setSearchOpen: (open: boolean) => void
  locate: (target: { kind: "node" | "edge"; id: string }) => void
  setScopeFilter: (scopes: string[] | null) => void
  setDomainFilter: (domains: string[] | null) => void
  listNodesForSearch: () => NodeSearchEntry[]

  // Persisted inspector + filter-panel UI actions.
  setInspectorOpen: (open: boolean) => void
  toggleInspector: () => void
  setSidebarTab: (tab: SidebarTab | null) => void
  setFiltersOpen: (open: boolean) => void
  toggleFiltersOpen: () => void

  // Shift-click charts: fetch + open the canonical chart for a Metric, or close.
  shiftClickMetric: (nodeId: string) => Promise<void>
  closeMetricChart: () => void
}

function ts(): string {
  return new Date().toLocaleTimeString()
}

export const useStore = create<CanvasState>()(
  persist(
    (set, get) => ({
  nodes: [],
  edges: [],
  dashboards: [],
  proposals: [],
  status: null,
  runId: null,
  progress: null,
  running: false,
  applying: false,
  connected: false,
  activity: [],
  selectedNodeId: null,
  focusNodeId: null,
  hoveredId: null,
  error: null,

  focusMode: "causal",
  focusDir: { up: true, down: true },
  ringCap: 18,
  growKind: null,
  trail: [],

  selectedEdgeId: null,
  edgeFilters: DEFAULT_EDGE_FILTERS,
  traversalMode: "off",
  traversalRootId: null,
  traversalResult: null,
  traverseMinConfidence: 0,
  coverage: null,
  edgeDiff: null,

  searchOpen: false,
  locateRequest: null,
  scopeFilter: null,
  domainFilter: null,

  // Persisted UI state (hydrated from localStorage; see persist() below).
  inspectorOpen: false,
  sidebarTab: null,
  filtersOpen: false,

  metricChart: null,

  loadGraph: async () => {
    try {
      const payload = await api.graph(2000, get().edgeFilters.showDeprecated)
      set({ nodes: payload.nodes, edges: payload.edges })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  loadStatus: async () => {
    try {
      const status = await api.status()
      set({ status })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  loadDashboards: async () => {
    try {
      const payload = await api.dashboards()
      set({ dashboards: payload.dashboards })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  loadProposals: async (runId?: string) => {
    try {
      const payload = await api.proposals(runId)
      set({ proposals: payload.proposals, runId: payload.run_id })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  startIngest: async (opts: IngestOptions) => {
    try {
      const { run_id } = await api.ingest(opts)
      set((state) => ({
        runId: run_id,
        running: true,
        progress: null,
        proposals: [],
        activity: [`[${ts()}] Ingest run started: ${run_id}`, ...state.activity].slice(
          0,
          MAX_ACTIVITY
        ),
      }))
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  // NOTE: the old `runCausal` action is retired — the deterministic causal pass
  // is gone (POST /api/run-causal now returns 501) and the metric→metric graph is
  // built by the agentic builder (`kg build` / harness.agentic), a CLI/harness
  // operation rather than a canvas button.
  // TODO(build-report): if a read-only "last build report" surface is wanted,
  // wire a `loadBuildReport()` action here against data/build-report.<ts>.json.

  approveAll: async () => {
    const runId = get().runId
    if (!runId) {
      return
    }
    try {
      const { approved } = await api.approveAll(runId)
      set((state) => ({
        activity: [
          `[${ts()}] Approved all ${approved} pending proposal(s) in ${runId}`,
          ...state.activity,
        ].slice(0, MAX_ACTIVITY),
      }))
      // Refresh review states so "Apply approved (N)" reflects the new count.
      await get().loadProposals(runId)
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  applyRun: async (runId?: string) => {
    const target = runId ?? get().runId
    if (!target) {
      return
    }
    set({ applying: true })
    try {
      const summary = await api.apply(target)
      set((state) => ({
        activity: [
          `[${ts()}] Applied run ${target}: ${JSON.stringify(summary)}`,
          ...state.activity,
        ].slice(0, MAX_ACTIVITY),
      }))
      // Reflect the newly-written nodes/edges + counts.
      await get().loadGraph()
      await get().loadStatus()
      await get().loadProposals(target)
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    } finally {
      set({ applying: false })
    }
  },

  reviewProposal: async (proposalId, action, reason, payload) => {
    const runId = get().runId
    if (!runId) {
      return
    }
    try {
      const result = await api.reviewProposal(proposalId, {
        action,
        run_id: runId,
        reason,
        payload,
      })
      set((state) => ({
        proposals: state.proposals.map((p) =>
          p.proposal_id === proposalId
            ? { ...p, review_state: result.state, ...(payload ? { payload } : {}) }
            : p
        ),
        activity: [
          `[${ts()}] ${action} proposal ${proposalId} -> ${result.state}`,
          ...state.activity,
        ].slice(0, MAX_ACTIVITY),
      }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      // A 404 just means the proposal isn't pending under this run (e.g. an
      // auto-approve run already applied it). That's benign — don't raise a
      // scary banner; drop it from the local queue and note it softly.
      if (msg.includes("404")) {
        set((state) => ({
          proposals: state.proposals.filter((p) => p.proposal_id !== proposalId),
          activity: [
            `[${ts()}] proposal ${proposalId} already applied (skipped)`,
            ...state.activity,
          ].slice(0, MAX_ACTIVITY),
        }))
        return
      }
      set({ error: msg })
    }
  },

  handleEvent: (ev: CanvasEvent) => {
    const data = ev.data ?? {}
    switch (ev.type) {
      case "run_started": {
        const dashboards = Number(data.dashboards ?? 0)
        set((state) => ({
          running: true,
          runId: ev.run_id ?? state.runId,
          activity: [
            `[${ts()}] Run started — ${dashboards} dashboard(s)`,
            ...state.activity,
          ].slice(0, MAX_ACTIVITY),
        }))
        break
      }
      case "ingest_progress": {
        set({
          progress: {
            dashboard: String(data.dashboard ?? ""),
            done: Number(data.done ?? 0),
            total: Number(data.total ?? 0),
          },
        })
        break
      }
      case "agent_action": {
        const dashboard = String(data.dashboard ?? "")
        const message = String(data.message ?? "")
        set((state) => ({
          activity: [
            `[${ts()}] ${dashboard ? `${dashboard}: ` : ""}${message}`,
            ...state.activity,
          ].slice(0, MAX_ACTIVITY),
        }))
        break
      }
      case "proposal_new": {
        const proposal = data.proposal as Proposal | undefined
        if (proposal) {
          set((state) => {
            const exists = state.proposals.some(
              (p) => p.proposal_id === proposal.proposal_id
            )
            const proposals = exists
              ? state.proposals.map((p) =>
                  p.proposal_id === proposal.proposal_id ? proposal : p
                )
              : [...state.proposals, proposal]
            return {
              proposals,
              activity: [
                `[${ts()}] Proposal: ${proposal.target_label} ${proposal.target_id}`,
                ...state.activity,
              ].slice(0, MAX_ACTIVITY),
            }
          })
        }
        break
      }
      case "node_written": {
        const label = String(data.label ?? "")
        const key = String(data.key ?? "")
        const status = String(data.status ?? "")
        set((state) => ({
          activity: [
            `[${ts()}] Wrote ${label} ${key} (${status})`,
            ...state.activity,
          ].slice(0, MAX_ACTIVITY),
        }))
        break
      }
      case "run_done": {
        set((state) => ({
          running: false,
          progress: null,
          activity: [`[${ts()}] Run complete`, ...state.activity].slice(
            0,
            MAX_ACTIVITY
          ),
        }))
        break
      }
      case "error": {
        const message = String(data.message ?? "unknown error")
        set((state) => ({
          running: false,
          error: message,
          activity: [`[${ts()}] ERROR: ${message}`, ...state.activity].slice(
            0,
            MAX_ACTIVITY
          ),
        }))
        break
      }
      default:
        break
    }
  },

  setConnected: (connected) => set({ connected }),

  selectNode: (id) => set({ selectedNodeId: id }),

  // Focus a node and maintain the breadcrumb "walk". Re-focusing a node already
  // in the trail truncates back to it (stepping back); a new node is appended.
  // Clearing focus empties the trail. growKind resets on every re-center.
  setFocus: (id) =>
    set((state) => {
      if (!id) return { focusNodeId: null, trail: [], growKind: null }
      const title = state.nodes.find((n) => n.id === id)?.title ?? id
      const last = state.trail[state.trail.length - 1]
      if (last && last.id === id) return { focusNodeId: id }
      const existing = state.trail.findIndex((t) => t.id === id)
      const trail =
        existing >= 0
          ? state.trail.slice(0, existing + 1)
          : [...state.trail, { id, title }]
      return { focusNodeId: id, trail, growKind: null }
    }),

  setHovered: (id) => set({ hoveredId: id }),

  setFocusMode: (mode) => set({ focusMode: mode, growKind: null }),

  // Set the focus direction directly (never both off — falls back to both on).
  setFocusDir: (dir) =>
    set({ focusDir: dir.up || dir.down ? dir : { up: true, down: true } }),

  setRingCap: (cap) => set({ ringCap: cap }),

  setGrowKind: (kind) =>
    set((state) => ({ growKind: state.growKind === kind ? null : kind })),

  selectEdge: (id) => set({ selectedEdgeId: id }),

  setEdgeFilters: (patch) =>
    set((state) => ({ edgeFilters: { ...state.edgeFilters, ...patch } })),

  resetEdgeFilters: () => set({ edgeFilters: DEFAULT_EDGE_FILTERS }),

  // Toggling deprecated visibility re-fetches the graph so the deprecated edges
  // are actually included/excluded server-side.
  setShowDeprecated: (show) => {
    set((state) => ({ edgeFilters: { ...state.edgeFilters, showDeprecated: show } }))
    void get().loadGraph()
  },

  setTraversalMode: (mode) =>
    set((state) =>
      mode === "off"
        ? { traversalMode: "off", traversalRootId: null, traversalResult: null }
        : { ...state, traversalMode: mode }
    ),

  runTraversal: async (metricUid, mode, maxDepth = 3) => {
    try {
      // Read-time confidence floor (server-side). 0 ⇒ omit the param so the
      // request is byte-identical to the pre-filter behaviour.
      const minConf = get().traverseMinConfidence
      const minConfArg = minConf > 0 ? minConf : undefined
      const payload =
        mode === "upstream"
          ? await api.traverseUpstream(metricUid, maxDepth, minConfArg)
          : await api.traverseDownstream(metricUid, maxDepth, minConfArg)

      // The traverse response is now signed paths ({paths, cyclic_paths,...}),
      // not a {nodes, edges} subgraph. The canvas path-highlight only needs the
      // SET of node/edge ids on the (acyclic) chain, so flatten the paths: every
      // metric_uid becomes a node, and each hop (from→to of a given rel_type) is
      // matched back to a live store edge to recover its canvas edge id.
      const liveEdges = get().nodes.length ? get().edges : []
      const edgeKey = (s: string, t: string, ty: string) => `${s}|${t}|${ty}`
      const liveByKey = new Map<string, string>()
      for (const e of liveEdges) liveByKey.set(edgeKey(e.source, e.target, e.type), e.id)

      const nodeIds = new Set<string>()
      const edgeIds = new Set<string>()
      for (const p of payload.paths) {
        for (const uid of p.nodes) nodeIds.add(uid)
        for (const hop of p.edges) {
          if (hop.from && hop.to && hop.rel_type) {
            const id = liveByKey.get(edgeKey(hop.from, hop.to, hop.rel_type))
            if (id) edgeIds.add(id)
          }
        }
      }

      const nodes = get().nodes.filter((n) => nodeIds.has(n.id))
      const edges = get().edges.filter((e) => edgeIds.has(e.id))
      set({
        traversalMode: mode,
        traversalRootId: metricUid,
        traversalResult: { nodes, edges },
      })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  clearTraversal: () =>
    set({ traversalMode: "off", traversalRootId: null, traversalResult: null }),

  setTraverseMinConfidence: (value) =>
    set({ traverseMinConfidence: Math.max(0, Math.min(1, value)) }),

  loadCoverage: async (tenant = "rare_seeds") => {
    try {
      const coverage = await api.coverage(tenant)
      set({ coverage })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  loadEdgeDiff: async (tenant = "rare_seeds", runId?: string) => {
    try {
      const edgeDiff = await api.edgeDiff(tenant, runId)
      set({ edgeDiff })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  setSearchOpen: (open) => set({ searchOpen: open }),

  // Select the target AND raise a transient locate request the canvas watches
  // to fitView + flash a highlight. `ts` keeps repeated locates distinct so the
  // canvas effect re-fires even on the same target. Closes the palette.
  locate: (target) =>
    set(
      target.kind === "node"
        ? {
            selectedNodeId: target.id,
            locateRequest: { ...target, ts: Date.now() },
            searchOpen: false,
          }
        : {
            selectedEdgeId: target.id,
            locateRequest: { ...target, ts: Date.now() },
            searchOpen: false,
          }
    ),

  setScopeFilter: (scopes) =>
    set({ scopeFilter: scopes && scopes.length ? scopes : null }),

  setDomainFilter: (domains) =>
    set({ domainFilter: domains && domains.length ? domains : null }),

  // Derived selector: flatten loaded nodes into palette rows. Scope is read off
  // props.scope_key; kind off props.category (falling back to the node label).
  listNodesForSearch: () =>
    get().nodes.map((n) => ({
      id: n.id,
      label: n.title || n.id,
      scope: (n.props?.scope_key as string | undefined) ?? null,
      kind: (n.props?.category as string | undefined) ?? n.label ?? null,
    })),

  // --- Persisted inspector + filter-panel UI actions -----------------------
  // The filter panel is independent of the inspector tab: opening/closing it
  // never touches sidebarTab, and switching sidebarTab never touches filtersOpen.
  setInspectorOpen: (open) => set({ inspectorOpen: open }),
  toggleInspector: () => set((state) => ({ inspectorOpen: !state.inspectorOpen })),
  setSidebarTab: (tab) => set({ sidebarTab: tab }),
  setFiltersOpen: (open) => set({ filtersOpen: open }),
  toggleFiltersOpen: () => set((state) => ({ filtersOpen: !state.filtersOpen })),

  // --- Shift-click charts ---------------------------------------------------
  // Fetch the canonical chart for a Metric node and open it in the chart panel.
  // No-ops for non-metric nodes. Runs ALONGSIDE the existing shift-click focus
  // (the canvas still calls setFocus); this only manages the chart payload.
  shiftClickMetric: async (nodeId) => {
    const node = get().nodes.find((n) => n.id === nodeId)
    if (!node || node.label !== "Metric") return
    const metricUid = (node.props?.metric_uid as string | undefined) ?? node.id
    const title = node.title || node.id
    set({ metricChart: { metricUid, title, loading: true, payload: null } })
    try {
      const payload = await api.metricChart(metricUid)
      // Guard against a newer shift-click having superseded this fetch.
      if (get().metricChart?.metricUid !== metricUid) return
      set({ metricChart: { metricUid, title, loading: false, payload } })
    } catch (err) {
      if (get().metricChart?.metricUid === metricUid) {
        set({ metricChart: { metricUid, title, loading: false, payload: null } })
      }
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  closeMetricChart: () => set({ metricChart: null }),
    }),
    {
      name: "kg-canvas-state",
      version: 1,
      // Persist ONLY the user's filter/inspector preferences — NEVER the canvas-
      // local graph/overview/proposal/run data (which is reloaded from the API on
      // mount). This is what keeps filters + inspector state across reloads and
      // navigation while never staling the live graph.
      partialize: (state) => ({
        edgeFilters: state.edgeFilters,
        scopeFilter: state.scopeFilter,
        domainFilter: state.domainFilter,
        inspectorOpen: state.inspectorOpen,
        sidebarTab: state.sidebarTab,
        filtersOpen: state.filtersOpen,
        traverseMinConfidence: state.traverseMinConfidence,
      }),
    }
  )
)
