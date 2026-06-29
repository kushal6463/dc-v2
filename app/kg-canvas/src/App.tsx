import { useEffect, useMemo, useState } from "react"
import { PanelRightClose } from "lucide-react"

import { ActivityFeed } from "@/components/ActivityFeed"
import { CanvasView } from "@/components/CanvasView"
import { CommandSearch } from "@/components/CommandSearch"
import { EdgeDiffReview } from "@/components/EdgeDiffReview"
import { GovernancePanel } from "@/components/governance/GovernancePanel"
import { NodeDetail } from "@/components/NodeDetail"
import { ProgressBar } from "@/components/ProgressBar"
import { ReviewQueue } from "@/components/ReviewQueue"
import { Toolbar } from "@/components/Toolbar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { subscribeEvents, type CoveragePayload, type GraphEdge } from "@/lib/api"
import { CROSS_DOMAIN_COLOR, edgeIsCrossDomain, edgeStyle, edgeVisual } from "@/lib/graphTheme"
import { useStore, type SidebarTab } from "@/store"

const TABS: { key: SidebarTab; label: string }[] = [
  { key: "activity", label: "Activity" },
  { key: "review", label: "Review" },
  { key: "detail", label: "Node" },
  { key: "edge", label: "Edge" },
  { key: "diff", label: "Edge Diff" },
]

// Coverage summary badge — distils the coverage_report.<tenant>.json into a
// single headline metric count, with the fuller breakdown in a tooltip.
function CoverageBadge({ coverage }: { coverage: CoveragePayload | null }) {
  if (!coverage || typeof coverage.error === "string") return null
  const num = (k: string): number | null => {
    const v = (coverage as Record<string, unknown>)[k]
    return typeof v === "number" ? v : null
  }
  const metrics = num("metric_nodes")
  const withFormula = num("metrics_with_formula")
  const composites = num("composites")
  const conflicts = num("conflicts")
  if (metrics === null) return null

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="secondary" className="cursor-default font-mono">
            {metrics} metrics
          </Badge>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <div className="flex flex-col gap-0.5 text-[11px]">
            <span>{coverage.tenant ?? "tenant"}</span>
            {withFormula !== null && <span>{withFormula} with formula</span>}
            {composites !== null && <span>{composites} composites</span>}
            {conflicts !== null && <span>{conflicts} conflicts</span>}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

const EDGE_DETAIL_FIELDS: { key: keyof GraphEdge; label: string }[] = [
  { key: "relation", label: "relation" },
  { key: "confidence", label: "confidence" },
  { key: "evidence_mass", label: "evidence_mass" },
  { key: "temporal_lag", label: "temporal_lag" },
  { key: "lag_plausibility", label: "lag_plausibility" },
  { key: "scoring_policy", label: "scoring_policy" },
  { key: "review_state", label: "review_state" },
  { key: "status", label: "status" },
  { key: "source_kind", label: "source_kind" },
  { key: "deprecated_at", label: "deprecated_at" },
]

// Edge inspector for the currently selected edge: type/relation header plus the
// scored + lifecycle metadata. Mounted in the "Edge" tab when an edge is
// selected (store.selectedEdgeId).
function EdgeDetail() {
  const selectedEdgeId = useStore((s) => s.selectedEdgeId)
  const edge = useStore((s) => s.edges.find((e) => e.id === s.selectedEdgeId))
  const nodes = useStore((s) => s.nodes)

  const titleOf = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n.title || n.id]))
    return (id: string) => byId.get(id) ?? id
  }, [nodes])

  if (!selectedEdgeId || !edge) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        Select an edge on the canvas to inspect its relation, score and lifecycle.
      </div>
    )
  }

  const visual = edgeVisual(edge.type)
  const style = edgeStyle(edge)
  const crossDomain = edgeIsCrossDomain(edge)
  const mechanism =
    edge.mechanism ??
    ((edge.props as Record<string, unknown> | undefined)?.mechanism as
      | string
      | undefined)

  const fieldValue = (key: keyof GraphEdge): unknown => {
    const direct = edge[key]
    if (direct !== undefined && direct !== null && direct !== "") return direct
    return (edge.props as Record<string, unknown> | undefined)?.[key]
  }

  const fields = EDGE_DETAIL_FIELDS.map((f) => [f.label, fieldValue(f.key)] as const).filter(
    ([, v]) => v !== undefined && v !== null && v !== ""
  )

  return (
    <div className="flex h-full w-full flex-col">
      <div className="border-b border-border px-4 py-3">
        <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
          <i
            className="inline-block h-1 w-5 rounded-full"
            style={{ background: style.stroke, opacity: style.opacity }}
          />
          {visual.label}
          {crossDomain && (
            <span
              className="ml-auto rounded px-1.5 py-0.5 text-[9px] font-semibold normal-case"
              style={{
                color: CROSS_DOMAIN_COLOR,
                backgroundColor: `${CROSS_DOMAIN_COLOR}22`,
              }}
              title="Cross-domain edge: this relation spans two metric domains"
            >
              ⬡ cross-domain
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 text-xs">
          <span className="truncate text-foreground">{titleOf(edge.source)}</span>
          <span className="shrink-0 font-mono text-muted-foreground">→</span>
          <span className="truncate text-foreground">{titleOf(edge.target)}</span>
        </div>
        <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
          {edge.source} → {edge.target}
        </div>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        <div>
          <div className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Edge metadata
          </div>
          {fields.length === 0 ? (
            <div className="text-xs text-muted-foreground">No metadata.</div>
          ) : (
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              {fields.map(([k, v]) => (
                <div key={k} className="min-w-0">
                  <div className="font-mono text-[10px] text-muted-foreground">{k}</div>
                  <div className="break-words text-xs text-foreground">
                    {typeof v === "number" ? v : String(v)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {mechanism && (
          <div>
            <div className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              Mechanism
            </div>
            <div className="text-xs leading-relaxed text-foreground">{mechanism}</div>
          </div>
        )}
      </div>
    </div>
  )
}

export function App() {
  const loadGraph = useStore((s) => s.loadGraph)
  const loadDashboards = useStore((s) => s.loadDashboards)
  const loadProposals = useStore((s) => s.loadProposals)
  const loadStatus = useStore((s) => s.loadStatus)
  const loadCoverage = useStore((s) => s.loadCoverage)
  const handleEvent = useStore((s) => s.handleEvent)
  const setConnected = useStore((s) => s.setConnected)
  const selectedNodeId = useStore((s) => s.selectedNodeId)
  const selectedEdgeId = useStore((s) => s.selectedEdgeId)
  const coverage = useStore((s) => s.coverage)
  const pendingCount = useStore(
    (s) => s.proposals.filter((p) => p.review_state === "proposed").length
  )
  const error = useStore((s) => s.error)

  // Inspector open + active tab now live in the (persisted) store so they survive
  // reloads. `sidebarTab` is set by clicking a tab; when null we auto-show "edge"
  // while an edge is selected, "detail" while a node is selected, else "activity".
  const inspectorOpen = useStore((s) => s.inspectorOpen)
  const setInspectorOpen = useStore((s) => s.setInspectorOpen)
  const governanceOpen = useStore((s) => s.governanceOpen)
  const sidebarTab = useStore((s) => s.sidebarTab)
  const setSidebarTab = useStore((s) => s.setSidebarTab)
  const tab: SidebarTab =
    sidebarTab ?? (selectedEdgeId ? "edge" : selectedNodeId ? "detail" : "activity")

  // Wait for the persisted state to hydrate before the auto-open effect runs, so
  // a freshly-hydrated user-pinned tab / closed inspector is never clobbered by a
  // stale selection on first paint. (localStorage hydration is synchronous in
  // zustand v5, so this is `true` on first render today; the subscription keeps
  // it correct if the persist storage ever becomes async.)
  const [hydrated, setHydrated] = useState(() => useStore.persist.hasHydrated())
  useEffect(() => {
    if (hydrated) return
    // Reconcile via a microtask (not a synchronous body setState) in case
    // hydration finished between the initial read above and this subscription.
    const unsub = useStore.persist.onFinishHydration(() => setHydrated(true))
    if (useStore.persist.hasHydrated()) queueMicrotask(() => setHydrated(true))
    return unsub
  }, [hydrated])

  // Auto-open the inspector when something is selected (post-hydration only).
  // This only OPENS the panel; it never pins a tab (the auto `tab` derivation
  // above already follows the selection), so a user-pinned tab is preserved.
  useEffect(() => {
    if (!hydrated) return
    if (selectedNodeId || selectedEdgeId) setInspectorOpen(true)
  }, [hydrated, selectedNodeId, selectedEdgeId, setInspectorOpen])

  // Initial loads + SSE subscription.
  useEffect(() => {
    void loadGraph()
    void loadDashboards()
    void loadProposals()
    void loadStatus()
    void loadCoverage()

    const unsubscribe = subscribeEvents(
      (ev) => {
        // A delivered event means the SSE channel is live.
        setConnected(true)
        handleEvent(ev)
        // Refresh the graph + status when the backend writes nodes or finishes.
        if (ev.type === "node_written" || ev.type === "run_done") {
          void loadGraph()
          void loadStatus()
        }
      },
      (err) => {
        // EventSource error: mark the connection indicator offline (the browser
        // will auto-reconnect; the next delivered event flips it back on).
        console.error("SSE connection error", err)
        setConnected(false)
      }
    )

    return unsubscribe
  }, [
    loadGraph,
    loadDashboards,
    loadProposals,
    loadStatus,
    loadCoverage,
    handleEvent,
    setConnected,
  ])

  return (
    <div className="flex h-svh w-svw flex-col overflow-hidden bg-background">
      <CommandSearch />
      <Toolbar />
      <ProgressBar />

      {error && (
        <div className="border-b border-destructive/40 bg-destructive/10 px-4 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <CanvasView />
        </div>

        {/* Left governance drawer — always-collapsed; opened from the toolbar
            ShieldPlus icon. Overlays the canvas (absolute) so there's no layout
            shift, exactly like the right inspector. */}
        {governanceOpen && (
          <aside className="absolute inset-y-0 left-0 z-20 flex w-[min(440px,94vw)] flex-col border-r border-border bg-background shadow-xl isolate">
            <GovernancePanel />
          </aside>
        )}

        {inspectorOpen && (
          <aside className="absolute inset-y-0 right-0 z-20 flex w-[min(420px,92vw)] flex-col border-l border-border bg-background shadow-xl isolate">
            <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
              <CoverageBadge coverage={coverage} />
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Hide inspector"
                title="Hide inspector"
                onClick={() => setInspectorOpen(false)}
              >
                <PanelRightClose />
              </Button>
            </div>
            <Tabs
              value={tab}
              onValueChange={(v) => setSidebarTab(v as SidebarTab)}
              className="flex min-h-0 flex-1 flex-col gap-0"
            >
              <div className="flex items-center border-b border-border">
                <TabsList
                  variant="line"
                  className="h-auto min-w-0 flex-1 justify-stretch gap-0 rounded-none bg-transparent p-0"
                >
                  {TABS.map((t) => (
                    <TabsTrigger key={t.key} value={t.key} className="flex-1 rounded-none py-2 text-xs">
                      {t.label}
                      {t.key === "review" && pendingCount > 0 ? (
                        <Badge
                          variant="secondary"
                          className="ml-1 bg-amber-500/20 text-amber-600 dark:text-amber-400"
                        >
                          {pendingCount}
                        </Badge>
                      ) : null}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </div>

              <TabsContent value="activity" className="min-h-0 flex-1 overflow-y-auto">
                <ActivityFeed />
              </TabsContent>
              <TabsContent value="review" className="min-h-0 flex-1 overflow-y-auto">
                <ReviewQueue />
              </TabsContent>
              <TabsContent value="detail" className="min-h-0 flex-1 overflow-y-auto">
                <NodeDetail />
              </TabsContent>
              <TabsContent value="edge" className="min-h-0 flex-1 overflow-y-auto">
                <EdgeDetail />
              </TabsContent>
              <TabsContent value="diff" className="min-h-0 flex-1 overflow-hidden">
                <EdgeDiffReview />
              </TabsContent>
            </Tabs>
          </aside>
        )}
      </div>
    </div>
  )
}

export default App
