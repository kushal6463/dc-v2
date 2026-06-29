// Top toolbar: dashboard picker + ingest controls + auto-approve + theme toggle,
// plus edge filters (type / relation / review / status / confidence /
// deprecated), a metric-traversal toggle, and the edge-style legend.

import { useMemo, useState, type ReactNode } from "react"
import {
  Check,
  Hammer,
  PanelRightClose,
  PanelRightOpen,
  Palette,
  Search,
  ShieldPlus,
  SlidersHorizontal,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { ThemeToggle } from "@/components/ThemeToggle"
import { EDGE_LEGEND } from "@/lib/graphTheme"
import { cn } from "@/lib/utils"
import {
  DEFAULT_EDGE_FILTERS,
  useStore,
  type EdgeTypeFilter,
  type StatusFilter,
  type TraversalMode,
} from "@/store"

// Canonical relation vocabulary per metric→metric edge type (used when the live
// graph hasn't surfaced an example of a given relation yet).
const DECOMPOSES_RELATIONS = [
  "formula",
  "component",
  "identity",
  "rollup",
  "crossproduct",
  "funnel",
]
const INFLUENCES_RELATIONS = [
  "curated_rule",
  "llm_verified",
  "statistical",
  "statistical_candidate",
  "promoted",
]
// Sentinel for the "any" option in single-select dropdowns (Radix Select can't
// take an empty-string value).
const ANY = "__any__"

export function Toolbar() {
  const dashboards = useStore((s) => s.dashboards)
  const startIngest = useStore((s) => s.startIngest)
  const applyRun = useStore((s) => s.applyRun)
  const running = useStore((s) => s.running)
  const applying = useStore((s) => s.applying)
  const connected = useStore((s) => s.connected)
  const status = useStore((s) => s.status)
  const runId = useStore((s) => s.runId)
  const approvedCount = useStore(
    (s) => s.proposals.filter((p) => p.review_state === "approved").length
  )

  // Edge filtering + metric traversal (store-driven).
  const edges = useStore((s) => s.edges)
  const edgeFilters = useStore((s) => s.edgeFilters)
  const setEdgeFilters = useStore((s) => s.setEdgeFilters)
  const resetEdgeFilters = useStore((s) => s.resetEdgeFilters)
  const setShowDeprecated = useStore((s) => s.setShowDeprecated)
  const selectedNodeId = useStore((s) => s.selectedNodeId)
  const selectedIsMetric = useStore(
    (s) => s.nodes.find((n) => n.id === s.selectedNodeId)?.label === "Metric"
  )
  const traversalMode = useStore((s) => s.traversalMode)
  const setTraversalMode = useStore((s) => s.setTraversalMode)
  const runTraversal = useStore((s) => s.runTraversal)

  // Command search + scope/domain faceting (store-driven).
  const nodes = useStore((s) => s.nodes)
  const setSearchOpen = useStore((s) => s.setSearchOpen)
  const scopeFilter = useStore((s) => s.scopeFilter)
  const domainFilter = useStore((s) => s.domainFilter)
  const setScopeFilter = useStore((s) => s.setScopeFilter)
  const setDomainFilter = useStore((s) => s.setDomainFilter)

  // Inspector + edge-filter panel toggles (persisted, store-driven). The filter
  // panel is intentionally INDEPENDENT of the inspector tabs — toggling tabs
  // never hides it.
  const inspectorOpen = useStore((s) => s.inspectorOpen)
  const toggleInspector = useStore((s) => s.toggleInspector)
  const governanceOpen = useStore((s) => s.governanceOpen)
  const toggleGovernance = useStore((s) => s.toggleGovernance)
  const filtersOpen = useStore((s) => s.filtersOpen)
  const toggleFiltersOpen = useStore((s) => s.toggleFiltersOpen)

  const [picked, setPicked] = useState("")
  const [autoApprove, setAutoApprove] = useState(false)
  const [buildOpen, setBuildOpen] = useState(false)
  const [legendOpen, setLegendOpen] = useState(false)
  const [scopeOpen, setScopeOpen] = useState(false)
  const [domainOpen, setDomainOpen] = useState(false)

  // Relation options for the active edge-type filter. When a metric edge type is
  // selected we offer its canonical relations; otherwise the union of relations
  // actually present in the live graph (so the picker never goes stale).
  const relationOptions = useMemo(() => {
    if (edgeFilters.edgeType === "DECOMPOSES_INTO") return DECOMPOSES_RELATIONS
    if (edgeFilters.edgeType === "INFLUENCES") return INFLUENCES_RELATIONS
    const live = new Set<string>()
    for (const e of edges) {
      const rel = (e.relation ?? (e.props?.relation as string | undefined))?.toLowerCase()
      if (rel) live.add(rel)
    }
    const merged = new Set([...DECOMPOSES_RELATIONS, ...INFLUENCES_RELATIONS, ...live])
    return [...merged].sort((a, b) => a.localeCompare(b))
  }, [edgeFilters.edgeType, edges])

  // Review-state options: the canonical set plus anything live.
  const reviewOptions = useMemo(() => {
    const live = new Set(["proposed", "pending", "approved", "rejected"])
    for (const e of edges) {
      const rs = (e.review_state ?? (e.props?.review_state as string | undefined))?.toLowerCase()
      if (rs) live.add(rs)
    }
    return [...live].sort((a, b) => a.localeCompare(b))
  }, [edges])

  // Scope options derived from the loaded nodes' props.scope_key.
  const scopeOptions = useMemo(() => {
    const live = new Set<string>()
    for (const n of nodes) {
      const scope = n.props?.scope_key
      if (typeof scope === "string" && scope) live.add(scope)
    }
    return [...live].sort((a, b) => a.localeCompare(b))
  }, [nodes])

  // Domain options derived from the loaded nodes' props.domain_ids (which may be
  // a string or an array of domain ids).
  const domainOptions = useMemo(() => {
    const live = new Set<string>()
    for (const n of nodes) {
      const raw = n.props?.domain_ids
      if (typeof raw === "string" && raw) live.add(raw)
      else if (Array.isArray(raw)) {
        for (const d of raw) if (typeof d === "string" && d) live.add(d)
      }
    }
    return [...live].sort((a, b) => a.localeCompare(b))
  }, [nodes])

  const filterCount =
    (edgeFilters.edgeType !== DEFAULT_EDGE_FILTERS.edgeType ? 1 : 0) +
    (edgeFilters.relation ? 1 : 0) +
    (edgeFilters.reviewState ? 1 : 0) +
    (edgeFilters.status !== DEFAULT_EDGE_FILTERS.status ? 1 : 0) +
    (edgeFilters.confidence[0] !== 0 || edgeFilters.confidence[1] !== 1 ? 1 : 0) +
    (edgeFilters.showDeprecated ? 1 : 0)

  // Traversal: switching to upstream/downstream runs it against the selected
  // metric; "off" clears it. Re-running keeps it pinned to the current metric.
  const onTraversal = (mode: TraversalMode) => {
    if (mode === "off" || !selectedNodeId || !selectedIsMetric) {
      setTraversalMode(mode)
      return
    }
    void runTraversal(selectedNodeId, mode)
  }

  // Toggle a single value in a multi-select facet (scope / domain).
  const toggleFacet = (
    current: string[] | null,
    value: string,
    set: (next: string[] | null) => void
  ) => {
    const list = current ?? []
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value])
  }

  // Effective selection: the user's pick, or the first dashboard as default.
  const selected =
    picked || (dashboards.length > 0 ? dashboards[0].dashboard_id : "")

  const nodeTotal = status
    ? Object.values(status.nodes).reduce((a, b) => a + b, 0)
    : null
  const edgeTotal = status
    ? Object.values(status.edges).reduce((a, b) => a + b, 0)
    : null

  return (
    <div className="relative border-b border-border">
    <div className="flex min-h-10 items-center gap-2 px-3 py-1">
      <div className="shrink-0 text-xs font-semibold text-foreground sm:text-sm">
        ThoughtWire Causal KG
      </div>

      {/* Governance: open the left "Add policy & threshold" drawer. */}
      <Button
        variant={governanceOpen ? "default" : "ghost"}
        size="icon-sm"
        className="shrink-0"
        aria-label="Add policy and threshold"
        aria-pressed={governanceOpen}
        title="Governance — add policy & threshold"
        onClick={toggleGovernance}
      >
        <ShieldPlus />
      </Button>

      {/* Build controls (ingest / causal / apply) collapsed into one popover —
          the graph is normally built from the `kg` CLI runbook, so these stay out
          of the main bar until needed. */}
      <div className="relative shrink-0">
        <Button
          variant="outline"
          size="xs"
          aria-expanded={buildOpen}
          aria-pressed={running || applying}
          onClick={() => setBuildOpen((v) => !v)}
          title="Ingest dashboards + build the causal layer"
          className="gap-1.5 aria-pressed:bg-muted"
        >
          <Hammer />
          Build
          {running
            ? " · running…"
            : approvedCount > 0
              ? ` · ${approvedCount} ✓`
              : ""}
        </Button>
        {buildOpen && (
          <div className="absolute left-0 top-full z-30 mt-1 w-[300px] rounded-md border border-border bg-background p-3 text-xs shadow-lg">
            <div className="mb-2 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
              Build the graph
            </div>
            <div className="flex flex-col gap-2">
              <Select
                value={selected}
                onValueChange={setPicked}
                disabled={dashboards.length === 0}
              >
                <SelectTrigger size="sm" className="w-full">
                  <SelectValue placeholder="No dashboards" />
                </SelectTrigger>
                <SelectContent>
                  {dashboards.map((d) => (
                    <SelectItem key={d.dashboard_id} value={d.dashboard_id}>
                      {d.dashboard_id} ({d.components}c/{d.metrics}m)
                      {d.ingested ? " ✓" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <div className="flex gap-1.5">
                <Button
                  size="xs"
                  className="flex-1"
                  disabled={!selected || running}
                  onClick={() =>
                    void startIngest({
                      dashboard_id: selected,
                      auto_approve: autoApprove,
                    })
                  }
                >
                  Ingest dashboard
                </Button>
                <Button
                  size="xs"
                  variant="outline"
                  disabled={running}
                  onClick={() =>
                    void startIngest({ all: true, auto_approve: autoApprove })
                  }
                >
                  All
                </Button>
              </div>

              {/* "Run causal" retired: the deterministic causal pass is gone
                  (POST /api/run-causal → 501). The metric→metric graph is now
                  built by the agentic builder (`kg build` / harness.agentic), a
                  CLI/harness operation, not a canvas button.
                  TODO(build-report): a read-only "last build report" surface
                  (data/build-report.<ts>.json) could live here if wanted. */}

              <label className="flex items-center gap-1.5 text-xs text-muted-foreground select-none">
                <input
                  type="checkbox"
                  className="size-3.5 accent-primary"
                  checked={autoApprove}
                  onChange={(e) => setAutoApprove(e.target.checked)}
                />
                auto-approve
              </label>

              <Button
                size="xs"
                variant="secondary"
                className="w-full"
                disabled={!runId || applying || approvedCount === 0}
                onClick={() => void applyRun()}
                title="Apply approved proposals for the current run through the writer"
              >
                {applying ? "Applying…" : `Apply approved (${approvedCount})`}
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-2 text-xs">
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <span
            className={cn(
              "inline-block size-2 rounded-full",
              connected ? "bg-green-500" : "bg-muted-foreground/40"
            )}
            title={connected ? "Live event stream connected" : "Event stream offline"}
          />
          {connected ? "live" : "offline"}
        </span>
        {nodeTotal !== null && (
          <Badge variant="secondary" className="font-mono tabular-nums">
            {nodeTotal} nodes · {edgeTotal} edges
          </Badge>
        )}

        <Button
          variant="outline"
          size="xs"
          onClick={() => setSearchOpen(true)}
          title="Search nodes (⌘K / Ctrl+K)"
          className="gap-1.5"
        >
          <Search />
          <span className="hidden sm:inline">Search</span>
          <kbd className="hidden rounded border border-border bg-muted px-1 font-mono text-[10px] text-muted-foreground sm:inline">
            ⌘K
          </kbd>
        </Button>

        <Separator orientation="vertical" className="!h-4" />

        {/* Metric traversal: upstream / downstream from the selected metric. */}
        <div className="flex items-center rounded-md border border-border p-0.5">
          {(
            [
              ["off", "Off"],
              ["upstream", "↑ Up"],
              ["downstream", "↓ Down"],
            ] as [TraversalMode, string][]
          ).map(([mode, label]) => {
            const active = traversalMode === mode
            const needsMetric = mode !== "off" && !selectedIsMetric
            return (
              <button
                key={mode}
                onClick={() => onTraversal(mode)}
                disabled={needsMetric}
                aria-pressed={active}
                title={
                  needsMetric
                    ? "Select a metric node first"
                    : mode === "off"
                      ? "Clear traversal"
                      : `Trace ${mode} metric chain from the selected metric`
                }
                className={cn(
                  "rounded px-2 py-0.5 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground"
                )}
              >
                {label}
              </button>
            )
          })}
        </div>

        {/* Scope / Domain multi-select facet chips. */}
        <FacetChip
          label="Scope"
          options={scopeOptions}
          selected={scopeFilter}
          open={scopeOpen}
          onOpenChange={setScopeOpen}
          onToggle={(v) => toggleFacet(scopeFilter, v, setScopeFilter)}
          onClear={() => setScopeFilter(null)}
        />
        <FacetChip
          label="Domain"
          options={domainOptions}
          selected={domainFilter}
          open={domainOpen}
          onOpenChange={setDomainOpen}
          onToggle={(v) => toggleFacet(domainFilter, v, setDomainFilter)}
          onClear={() => setDomainFilter(null)}
        />

        <Button
          variant="outline"
          size="xs"
          aria-expanded={filtersOpen}
          aria-pressed={filterCount > 0}
          onClick={toggleFiltersOpen}
          title="Edge filters"
          className="aria-pressed:bg-muted"
        >
          <SlidersHorizontal />
          Edges{filterCount > 0 ? ` (${filterCount})` : ""}
        </Button>

        <Button
          variant="outline"
          size="xs"
          aria-expanded={legendOpen}
          aria-pressed={legendOpen}
          onClick={() => setLegendOpen((v) => !v)}
          title="Edge style legend"
          className="aria-pressed:bg-muted"
        >
          <Palette />
          Legend
        </Button>

        <Separator orientation="vertical" className="!h-4" />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={inspectorOpen ? "Hide inspector" : "Show inspector"}
          title={inspectorOpen ? "Hide inspector" : "Show inspector"}
          onClick={toggleInspector}
        >
          {inspectorOpen ? <PanelRightClose /> : <PanelRightOpen />}
        </Button>
        <ThemeToggle />
      </div>
    </div>

      {filtersOpen && (
        <div className="absolute right-3 top-full z-30 mt-1 w-[320px] rounded-md border border-border bg-background/95 p-3 text-xs shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
              Edge filters
            </span>
            <button
              onClick={resetEdgeFilters}
              className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              Reset
            </button>
          </div>

          <div className="flex flex-col gap-2.5">
            <FilterRow label="Edge type">
              <Select
                value={edgeFilters.edgeType}
                onValueChange={(v) =>
                  setEdgeFilters({ edgeType: v as EdgeTypeFilter, relation: null })
                }
              >
                <SelectTrigger size="sm" className="h-7 w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All edges</SelectItem>
                  <SelectItem value="DECOMPOSES_INTO">DECOMPOSES_INTO</SelectItem>
                  <SelectItem value="INFLUENCES">INFLUENCES</SelectItem>
                </SelectContent>
              </Select>
            </FilterRow>

            <FilterRow label="Relation">
              <Select
                value={edgeFilters.relation ?? ANY}
                onValueChange={(v) =>
                  setEdgeFilters({ relation: v === ANY ? null : v })
                }
              >
                <SelectTrigger size="sm" className="h-7 w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>Any relation</SelectItem>
                  {relationOptions.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FilterRow>

            <FilterRow label="Review state">
              <Select
                value={edgeFilters.reviewState ?? ANY}
                onValueChange={(v) =>
                  setEdgeFilters({ reviewState: v === ANY ? null : v })
                }
              >
                <SelectTrigger size="sm" className="h-7 w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>Any review state</SelectItem>
                  {reviewOptions.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FilterRow>

            <FilterRow label="Status">
              <Select
                value={edgeFilters.status}
                onValueChange={(v) => setEdgeFilters({ status: v as StatusFilter })}
              >
                <SelectTrigger size="sm" className="h-7 w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="deprecated">Deprecated</SelectItem>
                  <SelectItem value="all">All</SelectItem>
                </SelectContent>
              </Select>
            </FilterRow>

            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between text-muted-foreground">
                <span>Confidence</span>
                <span className="font-mono tabular-nums">
                  {edgeFilters.confidence[0].toFixed(2)} –{" "}
                  {edgeFilters.confidence[1].toFixed(2)}
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={edgeFilters.confidence[0]}
                onChange={(e) =>
                  setEdgeFilters({
                    confidence: [
                      Math.min(Number(e.target.value), edgeFilters.confidence[1]),
                      edgeFilters.confidence[1],
                    ],
                  })
                }
                className="w-full accent-primary"
                aria-label="Minimum confidence"
              />
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={edgeFilters.confidence[1]}
                onChange={(e) =>
                  setEdgeFilters({
                    confidence: [
                      edgeFilters.confidence[0],
                      Math.max(Number(e.target.value), edgeFilters.confidence[0]),
                    ],
                  })
                }
                className="w-full accent-primary"
                aria-label="Maximum confidence"
              />
            </div>

            <label className="flex cursor-pointer items-center gap-2 select-none">
              <input
                type="checkbox"
                className="size-3.5 accent-primary"
                checked={edgeFilters.showDeprecated}
                onChange={(e) => setShowDeprecated(e.target.checked)}
              />
              <span className="text-foreground">Show deprecated edges</span>
            </label>
          </div>
        </div>
      )}

      {legendOpen && (
        <div className="absolute right-3 top-full z-30 mt-1 w-[280px] rounded-md border border-border bg-background p-3 text-xs shadow-lg">
          <div className="mb-1.5 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
            Node provenance
          </div>
          <div className="mb-3 flex flex-col gap-1">
            <Legend color="#3b82f6" label="deterministic" />
            <Legend color="#a855f7" label="agent" />
            <Legend color="#22c55e" label="human" />
          </div>
          <div className="mb-1.5 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
            Edge legend
          </div>
          <div className="flex flex-col gap-1">
            {EDGE_LEGEND.map((item) => (
              <div key={item.key} className="flex items-center gap-2 text-muted-foreground">
                <svg width="34" height="8" className="shrink-0">
                  <line
                    x1="1"
                    y1="4"
                    x2="33"
                    y2="4"
                    stroke={item.style.stroke}
                    strokeWidth={2}
                    strokeOpacity={item.style.opacity}
                    strokeDasharray={item.style.strokeDasharray}
                  />
                </svg>
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Multi-select facet chip: a toggle button (showing the active count) that opens
// a small checklist panel. Wired to a store setter via onToggle / onClear.
function FacetChip({
  label,
  options,
  selected,
  open,
  onOpenChange,
  onToggle,
  onClear,
}: {
  label: string
  options: string[]
  selected: string[] | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onToggle: (value: string) => void
  onClear: () => void
}) {
  const count = selected?.length ?? 0
  return (
    <div className="relative">
      <Button
        variant="outline"
        size="xs"
        disabled={options.length === 0}
        aria-expanded={open}
        aria-pressed={count > 0}
        onClick={() => onOpenChange(!open)}
        title={`Filter by ${label.toLowerCase()}`}
        className="aria-pressed:bg-muted"
      >
        {label}
        {count > 0 ? ` (${count})` : ""}
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 max-h-[60vh] w-[220px] overflow-y-auto rounded-md border border-border bg-background/95 p-1.5 text-xs shadow-lg">
          <div className="mb-1 flex items-center justify-between px-1.5 py-0.5">
            <span className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
              {label}
            </span>
            {count > 0 && (
              <button
                onClick={onClear}
                className="rounded px-1 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                Clear
              </button>
            )}
          </div>
          <div className="flex flex-col">
            {options.map((opt) => {
              const active = selected?.includes(opt) ?? false
              return (
                <button
                  key={opt}
                  onClick={() => onToggle(opt)}
                  className="flex items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-accent"
                >
                  <span
                    className={cn(
                      "flex size-3.5 shrink-0 items-center justify-center rounded-sm border",
                      active
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border"
                    )}
                  >
                    {active && <Check className="size-3" />}
                  </span>
                  <span className="min-w-0 truncate text-foreground">{opt}</span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function FilterRow({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1 text-muted-foreground">
      <span
        className="inline-block size-3 rounded-sm"
        style={{ background: color }}
      />
      {label}
    </span>
  )
}
