// ReactFlow canvas: packed-tree overview + shift+click ego-focus + hover emphasis.
//
// Interactions (ported from dc-v2):
//   • plain click  → select the node (drives the Node detail panel)
//   • shift+click  → focus its full neighborhood as a radial ego ring; shift-click
//                    another member re-roots onto it
//   • click pane   → exit focus (back to overview) + clear selection
//   • hover a node → brighten edges touching it + its neighbors, dim the rest,
//                    reveal edge-type labels (no layout recompute)
//   • filters      → toggle visibility by node label and by edge type
//
// Performance notes (why this is smooth at a few thousand nodes):
//   • KGNode is React.memo'd and reads the theme from context (no per-render DOM
//     reads), so zoom/pan/hover never re-render the whole node layer.
//   • `onlyRenderVisibleElements` virtualizes off-screen nodes/edges.
//   • nodes are not draggable (the layout is deterministic) — fewer listeners, no
//     accidental drags, lighter interaction loop.
//   • unconnected nodes are hidden by default (toggle to show), keeping the DOM small.

import { memo, type ReactNode, useEffect, useMemo, useRef, useState } from "react"
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type NodeProps,
  type NodeTypes,
  type ReactFlowInstance,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { useStore } from "@/store"
import type { GraphEdge, GraphNode } from "@/lib/api"
import {
  buildLayout,
  emphasize,
  governedMetricIds,
  type EdgeData,
  type FlowEdge,
  type FlowNode,
  type FlowNodeData,
  type FocusOpts,
} from "@/lib/graphLayout"
import {
  CATEGORY_STYLE,
  CAUSAL_RELS,
  categoryStyle,
  causalRoleBadge,
  edgeStyle,
  edgeVisual,
  labelStyle,
  LEAF_RING_COLOR,
  LOOP_RING_COLOR,
  provenanceColor,
} from "@/lib/graphTheme"
import { findLoopNodeIds } from "@/lib/egoLayout"
import { neighborKinds } from "@/lib/egoLayout"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"

// Color a node card/dot: Metric nodes by their `category` prop, others by label.
function nodeColor(node: { label: string; props: Record<string, unknown> } | undefined): string {
  if (!node) return "#8d99ad"
  return labelStyle(node.label, node.props?.category as string | undefined).color
}

// ---------------------------------------------------------------------------
// Edge attribute readers + the store-filter predicate.
//
// Edge metadata lives on the top-level GraphEdge fields when present, falling
// back to `props` for older payloads (mirroring graphTheme.edgeStyle()).
// ---------------------------------------------------------------------------

function edgeProp(edge: GraphEdge, key: string): unknown {
  return (
    (edge as unknown as Record<string, unknown>)[key] ??
    (edge.props as Record<string, unknown> | undefined)?.[key]
  )
}

function edgeIsDeprecated(edge: GraphEdge): boolean {
  return (
    edgeProp(edge, "status") === "deprecated" || Boolean(edgeProp(edge, "deprecated_at"))
  )
}

/**
 * True when an edge should be rendered given the store's edge filters. Only
 * metric→metric edges (DECOMPOSES_INTO / INFLUENCES) carry relation/review/
 * confidence metadata, so the relation/review/confidence filters apply to those;
 * structural spine edges always pass the metric-only filters (they have no such
 * metadata to match against) but still honor edge-type / deprecated visibility.
 */
function passesEdgeFilters(
  edge: GraphEdge,
  f: ReturnType<typeof useStore.getState>["edgeFilters"],
): boolean {
  const deprecated = edgeIsDeprecated(edge)
  if (deprecated && !f.showDeprecated) return false

  if (f.edgeType !== "all" && edge.type !== f.edgeType) return false

  if (f.status === "active" && deprecated) return false
  if (f.status === "deprecated" && !deprecated) return false

  const isMetricEdge = edge.type === "DECOMPOSES_INTO" || edge.type === "INFLUENCES"
  if (isMetricEdge) {
    if (f.relation) {
      const rel = (edgeProp(edge, "relation") as string | undefined)?.toLowerCase()
      if (rel !== f.relation.toLowerCase()) return false
    }
    if (f.reviewState) {
      const rs = (edgeProp(edge, "review_state") as string | undefined)?.toLowerCase()
      if (rs !== f.reviewState.toLowerCase()) return false
    }
    const conf = edgeProp(edge, "confidence")
    if (typeof conf === "number") {
      if (conf < f.confidence[0] || conf > f.confidence[1]) return false
    }
  }
  return true
}

// ---------------------------------------------------------------------------
// Scope / domain faceting (store-driven, mirrors the Toolbar facet sources).
//
// Scope reads off props.scope_key; domain off props.domain_ids (a string or an
// array of ids). Only metric-bearing nodes carry these props — spine/structural
// nodes (Business / Domain / Product / Platform / Dashboard / UIComponent) have
// no scope_key/domain_ids and so are NOT culled by these facets (they remain to
// keep the metrics anchored to their spine).
// ---------------------------------------------------------------------------

function nodeScope(node: GraphNode): string | null {
  const s = node.props?.scope_key
  return typeof s === "string" && s ? s : null
}

function nodeDomains(node: GraphNode): string[] {
  const raw = node.props?.domain_ids
  if (typeof raw === "string" && raw) return [raw]
  if (Array.isArray(raw)) return raw.filter((d): d is string => typeof d === "string" && !!d)
  return []
}

/**
 * True when a node passes the active scope/domain facets. A node is only judged
 * against a facet it actually has a value for: a node with no scope_key passes
 * the scope facet, a node with no domain_ids passes the domain facet. This keeps
 * the structural spine visible while culling metric nodes outside the selection.
 */
function passesScopeDomain(
  node: GraphNode,
  scopeFilter: string[] | null,
  domainFilter: string[] | null,
): boolean {
  if (scopeFilter && scopeFilter.length) {
    const scope = nodeScope(node)
    if (scope !== null && !scopeFilter.includes(scope)) return false
  }
  if (domainFilter && domainFilter.length) {
    const domains = nodeDomains(node)
    if (domains.length && !domains.some((d) => domainFilter.includes(d))) return false
  }
  return true
}

// ---------------------------------------------------------------------------
// Custom card node — clean dc-v2-style chip, colored by label (kind).
//
// Memoized so React Flow only re-renders a node when its own `data` identity
// changes (hover spotlight / selection / focus). Theme comes from context, so a
// theme switch repaints every card but zoom/pan/hover do not.
// ---------------------------------------------------------------------------

// Governance badge color — matches the Policy node / GOVERNS edge (§) hue.
const GOVERNANCE_COLOR = "#ef6f6f"

const KGNode = memo(function KGNode({ data }: NodeProps<FlowNode>) {
  const d = data as FlowNodeData
  const node = d.node
  const st = labelStyle(node.label, node.props?.category as string | undefined)
  const prov = provenanceColor(node.provenance)
  const { resolvedTheme } = useTheme()
  const dark = resolvedTheme === "dark"

  const surface = dark ? "#0f1722" : "#ffffff"
  const border = dark ? "#26303f" : "#e2e8f0"
  const text = dark ? "#dfe6f0" : "#1e293b"
  const sub = dark ? "#7c879a" : "#64748b"
  const ringShadow = dark
    ? "0 8px 26px rgba(0,0,0,0.55)"
    : "0 8px 26px rgba(15,23,42,0.18)"

  const flash = d.flash
  const op = d.dim ? 0.28 : 1
  // locate flash → bright accent ring (highest priority); root (focused) →
  // label-colored ring; selected → provenance ring; otherwise a structural ring
  // for loop members (on a feedback cycle) / leaf nodes (no edges).
  const ring = flash
    ? `0 0 0 3px var(--primary), 0 0 22px 2px var(--primary), ${ringShadow}`
    : d.root
      ? `0 0 0 2px ${st.color}, ${ringShadow}`
      : d.selected
        ? `0 0 0 2px ${prov}`
        : d.loop
          ? `0 0 0 2px ${LOOP_RING_COLOR}, 0 0 12px 0 ${LOOP_RING_COLOR}66`
          : d.leaf
            ? `0 0 0 1.5px ${LEAF_RING_COLOR}99`
            : undefined

  // causal_role badge (metric nodes only) — surfaces a node's role in the causal
  // graph as a small chip, replacing the retired 4-division layout.
  const roleBadge =
    node.label === "Metric" ? causalRoleBadge(node.props?.causal_role) : null

  return (
    <div
      style={{
        width: 190,
        minHeight: 56,
        borderRadius: 10,
        borderStyle: "solid",
        borderWidth: "1px 1px 1px 4px",
        borderColor: `${border} ${border} ${border} ${st.color}`,
        background: surface,
        color: text,
        opacity: op,
        padding: "7px 10px",
        boxShadow: ring,
        transition: "opacity 200ms ease, box-shadow 200ms ease",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0, width: 1, height: 1, border: 0 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: st.color, fontSize: 12, width: 14, textAlign: "center" }}>{st.glyph}</span>
        <span
          style={{
            fontSize: 11.5,
            fontWeight: 550,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {node.title || node.id}
        </span>
        <span
          title={node.provenance}
          style={{ marginLeft: "auto", width: 7, height: 7, borderRadius: 99, background: prov, flexShrink: 0 }}
        />
      </div>
      <div style={{ marginTop: 3, display: "flex", alignItems: "center", gap: 5 }}>
        <span style={{ fontSize: 9.5, color: sub, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {node.label === "Metric" && node.props?.scope_key
            ? `${String(node.props.scope_key)} · ${st.label}`
            : st.label}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
          {d.governed && (
            <span
              title="Has a policy / threshold — shift-click to reveal its governance"
              style={{
                fontSize: 8.5,
                fontWeight: 700,
                lineHeight: 1,
                padding: "2px 5px",
                borderRadius: 99,
                color: GOVERNANCE_COLOR,
                border: `1px solid ${GOVERNANCE_COLOR}66`,
                background: `${GOVERNANCE_COLOR}1a`,
              }}
            >
              §
            </span>
          )}
          {roleBadge && (
            <span
              title={`causal role: ${String(node.props?.causal_role)}`}
              style={{
                fontSize: 8.5,
                fontWeight: 600,
                lineHeight: 1,
                padding: "2px 5px",
                borderRadius: 99,
                color: roleBadge.color,
                border: `1px solid ${roleBadge.color}66`,
                background: `${roleBadge.color}1a`,
              }}
            >
              {roleBadge.label}
            </span>
          )}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, width: 1, height: 1, border: 0 }} />
    </div>
  )
})

// Stable module-level reference — recreating this object re-mounts every node.
const nodeTypes: NodeTypes = { kg: KGNode }
const OVERVIEW_MIN_ZOOM = 0.12
const FOCUS_MIN_ZOOM = 0.22
const VIEW_PADDING = 0.08

// ---------------------------------------------------------------------------
// Filter control: toggle visibility by node label + edge type.
// ---------------------------------------------------------------------------

function FilterPanel({
  labels,
  types,
  hiddenLabels,
  hiddenTypes,
  onToggleLabel,
  onToggleType,
}: {
  labels: string[]
  types: string[]
  hiddenLabels: Set<string>
  hiddenTypes: Set<string>
  onToggleLabel: (l: string) => void
  onToggleType: (t: string) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="absolute right-3 top-3 z-10 flex flex-col items-end gap-1.5">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="bg-background/80"
      >
        Filters{hiddenLabels.size + hiddenTypes.size > 0 ? ` (${hiddenLabels.size + hiddenTypes.size})` : ""}
      </Button>
      {open && (
        <div className="max-h-[70vh] w-[220px] overflow-y-auto rounded-md border border-border bg-background/95 p-2.5 text-xs">
          <div className="mb-1 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
            Node kinds
          </div>
          <div className="mb-3 flex flex-col gap-1">
            {labels.map((l) => {
              const st = labelStyle(l)
              const on = !hiddenLabels.has(l)
              return (
                <label key={l} className="flex cursor-pointer items-center gap-2 select-none">
                  <input type="checkbox" className="size-3.5 accent-primary" checked={on} onChange={() => onToggleLabel(l)} />
                  <i className="inline-block h-2 w-2 rounded-full" style={{ background: st.color }} />
                  <span className={on ? "text-foreground" : "text-muted-foreground line-through"}>{st.label}</span>
                </label>
              )
            })}
          </div>
          <div className="mb-1 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
            Relations
          </div>
          <div className="flex flex-col gap-1">
            {types.map((t) => {
              const v = edgeVisual(t)
              const on = !hiddenTypes.has(t)
              return (
                <label key={t} className="flex cursor-pointer items-center gap-2 select-none">
                  <input type="checkbox" className="size-3.5 accent-primary" checked={on} onChange={() => onToggleType(t)} />
                  <i className="inline-block h-1.5 w-4 rounded-full" style={{ background: v.color }} />
                  <span className={on ? "text-foreground" : "text-muted-foreground line-through"}>{v.label}</span>
                </label>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// Small pill button for the focus toolbar (active = filled).
function ModeBtn({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-md border px-2 py-0.5 text-[11px] transition-colors ${
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border hover:bg-accent"
      }`}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------

export function CanvasView() {
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  const selectedNodeId = useStore((s) => s.selectedNodeId)
  const selectNode = useStore((s) => s.selectNode)
  const focusNodeId = useStore((s) => s.focusNodeId)
  const setFocus = useStore((s) => s.setFocus)
  const revealMetricChart = useStore((s) => s.revealMetricChart)
  const revealDashboardCharts = useStore((s) => s.revealDashboardCharts)
  const revealProductDashboards = useStore((s) => s.revealProductDashboards)
  const revealMetricGovernance = useStore((s) => s.revealMetricGovernance)
  // Metric ids that carry a policy/threshold — drives the shift-click dispatch
  // (governed metrics reveal their governance; others reveal their chart).
  const governedIds = useMemo(() => governedMetricIds(edges), [edges])
  const hoveredId = useStore((s) => s.hoveredId)
  const setHovered = useStore((s) => s.setHovered)
  const focusMode = useStore((s) => s.focusMode)
  const focusDir = useStore((s) => s.focusDir)
  const ringCap = useStore((s) => s.ringCap)
  const growKind = useStore((s) => s.growKind)
  const trail = useStore((s) => s.trail)
  const setFocusMode = useStore((s) => s.setFocusMode)
  const setFocusDir = useStore((s) => s.setFocusDir)
  const setRingCap = useStore((s) => s.setRingCap)
  const setGrowKind = useStore((s) => s.setGrowKind)

  // Edge inspection + relation/status filters + metric traversal (store-driven).
  const selectedEdgeId = useStore((s) => s.selectedEdgeId)
  const selectEdge = useStore((s) => s.selectEdge)
  const edgeFilters = useStore((s) => s.edgeFilters)
  const traversalMode = useStore((s) => s.traversalMode)
  const traversalResult = useStore((s) => s.traversalResult)

  // Scope/domain facets + the transient locate request (search / review panel).
  const scopeFilter = useStore((s) => s.scopeFilter)
  const domainFilter = useStore((s) => s.domainFilter)
  const locateRequest = useStore((s) => s.locateRequest)

  const { resolvedTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  // MiniMap surface/mask/border per theme (React Flow's default is light-only).
  const mini = dark
    ? { bg: "#0b0f17", mask: "rgba(8,11,17,0.55)", border: "#1b2435" }
    : { bg: "#eef1f6", mask: "rgba(180,190,205,0.45)", border: "#cbd3df" }

  const [hiddenLabels, setHiddenLabels] = useState<Set<string>>(new Set())
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set())
  const [showIsolated, setShowIsolated] = useState(false)
  // Overview presentation: "spine" = metrics grouped under their Domain (Business→
  // Domain→metric drawn — the connected default); "tree" = the metric→metric
  // decomposition forest; "map" = compact hub-only skeleton. Switching exits focus.
  const [overview, setOverview] = useState<"map" | "tree" | "spine" | "dash">("spine")
  const [rf, setRf] = useState<ReactFlowInstance<FlowNode> | null>(null)
  // Transient locate highlight: the node ids flashed after a locate() request
  // (an edge flashes both its endpoints). Cleared after the flash window.
  const [locateFlash, setLocateFlash] = useState<Set<string>>(new Set())

  const pickOverview = (v: "map" | "tree" | "spine" | "dash") => {
    setOverview(v)
    setFocus(null) // clicking an overview returns to it (clears focus)
  }

  // Distinct labels / types present in the live graph (for the filter lists).
  const labels = useMemo(
    () => [...new Set(nodes.map((n) => n.label))].sort((a, b) => a.localeCompare(b)),
    [nodes],
  )
  const types = useMemo(
    () => [...new Set(edges.map((e) => e.type))].sort((a, b) => a.localeCompare(b)),
    [edges],
  )

  // Apply filters BEFORE layout so hidden labels/types AND edges failing the
  // store's relation/review/status/confidence/deprecated filters are removed
  // from the canvas entirely (so they never shape the layout either).
  const filtered = useMemo(() => {
    const facetActive =
      (scopeFilter?.length ?? 0) > 0 || (domainFilter?.length ?? 0) > 0
    const fNodes = nodes.filter(
      (n) =>
        !hiddenLabels.has(n.label) &&
        (!facetActive || passesScopeDomain(n, scopeFilter, domainFilter)),
    )
    const present = new Set(fNodes.map((n) => n.id))
    const fEdges = edges.filter(
      (e) =>
        !hiddenTypes.has(e.type) &&
        present.has(e.source) &&
        present.has(e.target) &&
        passesEdgeFilters(e, edgeFilters),
    )
    return { nodes: fNodes, edges: fEdges }
  }, [nodes, edges, hiddenLabels, hiddenTypes, edgeFilters, scopeFilter, domainFilter])

  // Edge id → GraphEdge, so the post-layout styling pass can resolve the full
  // edge metadata (relation, deprecated, status) for graphTheme.edgeStyle().
  const edgeById = useMemo(() => {
    const m = new Map<string, GraphEdge>()
    for (const e of edges) m.set(e.id, e)
    return m
  }, [edges])

  // Count nodes with no (filtered) edge — used for the "Show unconnected" toggle.
  const isoCount = useMemo(() => {
    const connected = new Set<string>()
    for (const e of filtered.edges) {
      connected.add(e.source)
      connected.add(e.target)
    }
    return filtered.nodes.reduce((n, node) => (connected.has(node.id) ? n : n + 1), 0)
  }, [filtered])

  // A focus only makes sense if the focused node still exists in the filtered view.
  const focus = useMemo(
    () => (focusNodeId && filtered.nodes.some((n) => n.id === focusNodeId) ? focusNodeId : null),
    [focusNodeId, filtered.nodes],
  )

  const focusOpts: FocusOpts = useMemo(
    () => ({ mode: focusMode, dir: focusDir, ringCap, growKind, causalRels: CAUSAL_RELS }),
    [focusMode, focusDir, ringCap, growKind],
  )
  const base = useMemo(
    () =>
      buildLayout(
        filtered.nodes,
        filtered.edges,
        focus,
        selectedNodeId,
        showIsolated,
        focusOpts,
        overview,
      ),
    [filtered, focus, selectedNodeId, showIsolated, focusOpts, overview],
  )
  const { nodes: flowNodes, edges: flowEdges } = useMemo(
    () => emphasize(base, hoveredId, focus, dark),
    [base, hoveredId, focus, dark],
  )

  // The set of node/edge ids on the active traversal path (empty when off). Used
  // to highlight the returned chain and dim everything else.
  const traversal = useMemo(() => {
    if (traversalMode === "off" || !traversalResult) {
      return { active: false, nodeIds: new Set<string>(), edgeIds: new Set<string>() }
    }
    return {
      active: true,
      nodeIds: new Set(traversalResult.nodes.map((n) => n.id)),
      edgeIds: new Set(traversalResult.edges.map((e) => e.id)),
    }
  }, [traversalMode, traversalResult])

  // Leaf + loop node sets over the DRAWN edges, so the rings reflect what's
  // actually on the canvas (post-filter / per-overview). Leaf = a node with no
  // drawn edge (an orphan in this view); loop = a node on a metric→metric
  // feedback cycle (findLoopNodeIds over the causal rels).
  const decoration = useMemo(() => {
    const drawn = new Set<string>(base.nodes.map((n) => n.id))
    const touched = new Set<string>()
    for (const e of base.edges) {
      touched.add(e.source)
      touched.add(e.target)
    }
    const leaf = new Set<string>()
    for (const id of drawn) if (!touched.has(id)) leaf.add(id)
    const loop = findLoopNodeIds(
      base.edges.map((e) => ({
        source: e.source,
        target: e.target,
        type: (e.data as EdgeData | undefined)?.type ?? "",
      })),
      CAUSAL_RELS,
    )
    return { leaf, loop }
  }, [base.nodes, base.edges])

  // Final styling pass (no layout recompute): repaint each edge from
  // graphTheme.edgeStyle() so DECOMPOSES_INTO vs INFLUENCES and every relation
  // subtype read distinctly (deprecated → faded + dashed). Then overlay the
  // selected-edge highlight and, in traversal mode, dim everything off the path.
  const styled = useMemo(() => {
    const styledEdges: FlowEdge[] = flowEdges.map((e) => {
      const ge = edgeById.get(e.id)
      const d = e.data as EdgeData | undefined
      const vis = ge ? edgeStyle(ge) : undefined
      const stroke = vis?.stroke ?? d?.color ?? "#46566f"
      const selected = e.id === selectedEdgeId
      const onPath = traversal.active && traversal.edgeIds.has(e.id)
      const offPath = traversal.active && !onPath

      // Base opacity/width come from the emphasis pass; edgeStyle() supplies the
      // relation-driven look. Selection and traversal then layer on top.
      const prevStyle = (e.style ?? {}) as Record<string, unknown>
      let opacity = vis?.opacity ?? (prevStyle.opacity as number | undefined) ?? 0.9
      let width = (prevStyle.strokeWidth as number | undefined) ?? d?.baseWidth ?? 1.4
      // Thickness encodes confidence (metric edges only; widthScale is undefined
      // for structural edges → no change). Layered before the traversal/selection
      // boosts below so those still dominate.
      width *= vis?.widthScale ?? 1
      let z = (e.zIndex as number | undefined) ?? 0

      if (offPath) {
        opacity = 0.06
      } else if (onPath) {
        opacity = 1
        width = width + 0.9
        z = 7
      }
      if (selected) {
        opacity = 1
        width = Math.max(width, (d?.baseWidth ?? 1.4) + 1.4)
        z = 8
      }

      return {
        ...e,
        zIndex: z,
        animated: selected || onPath || Boolean(vis?.animated),
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 13, height: 13 },
        style: {
          stroke: selected ? "var(--primary)" : stroke,
          strokeWidth: width,
          opacity,
          strokeDasharray: vis?.strokeDasharray,
        },
      } satisfies FlowEdge
    })

    // Traversal dims nodes off the path; a locate request flashes its target(s);
    // leaf / loop decorations ring the relevant nodes. We rewrite a node's data
    // only when one of these flags actually changes (keeps identity stable so
    // React Flow / the memoized card skip re-rendering otherwise).
    const styledNodes: FlowNode[] = flowNodes.map((n) => {
      const dimNow = traversal.active && !traversal.nodeIds.has(n.id)
      const flashNow = locateFlash.has(n.id)
      const leafNow = decoration.leaf.has(n.id)
      const loopNow = decoration.loop.has(n.id)
      const d = n.data as FlowNodeData
      if (
        d.dim === dimNow &&
        Boolean(d.flash) === flashNow &&
        Boolean(d.leaf) === leafNow &&
        Boolean(d.loop) === loopNow
      ) {
        return n
      }
      return {
        ...n,
        data: { ...n.data, dim: dimNow, flash: flashNow, leaf: leafNow, loop: loopNow },
      }
    })

    return { nodes: styledNodes, edges: styledEdges }
  }, [flowNodes, flowEdges, edgeById, selectedEdgeId, traversal, locateFlash, decoration])

  const focusName = useMemo(
    () => (focus ? nodes.find((n) => n.id === focus)?.title ?? focus : null),
    [focus, nodes],
  )

  // Per-causal-relation neighbor counts for the "grow along" chips (focus only).
  const causalKinds = useMemo(() => {
    if (!focus) return [] as { type: string; count: number }[]
    return neighborKinds(filtered.edges, focus).filter((k) => CAUSAL_RELS.has(k.type))
  }, [focus, filtered.edges])

  // Categories present in the graph, for the legend (sorted, named first).
  const legendCategories = useMemo(() => {
    const present = new Set<string>()
    for (const n of nodes) {
      if (n.label === "Metric") {
        const c = (n.props?.category as string | undefined)?.toLowerCase()
        if (c) present.add(c)
      }
    }
    return [...present].sort(
      (a, b) =>
        (CATEGORY_STYLE[a] ? 0 : 1) - (CATEGORY_STYLE[b] ? 0 : 1) || a.localeCompare(b),
    )
  }, [nodes])

  // Re-frame the camera whenever the graph STRUCTURE changes (focus enter/exit,
  // SSE-driven node/edge updates, filter changes).
  const sig = `${overview}|${focus ?? ""}|${base.nodes.length}|${base.edges.length}`
  const sigRef = useRef("")
  useEffect(() => {
    if (rf && base.nodes.length && sigRef.current !== sig) {
      sigRef.current = sig
      rf.fitView({
        duration: 400,
        padding: VIEW_PADDING,
        maxZoom: 1,
        minZoom: focus ? FOCUS_MIN_ZOOM : OVERVIEW_MIN_ZOOM,
      })
    }
  }, [rf, base.nodes.length, focus, sig])

  // Locate: when the store raises a locateRequest (from the search palette or
  // the review queue's "Locate on canvas"), frame the camera onto the target
  // and briefly flash it. A node locates onto itself; an edge locates onto its
  // two endpoints (edges have no position of their own). `ts` makes repeated
  // locates of the same target re-fire this effect.
  const locateTs = locateRequest?.ts ?? 0
  useEffect(() => {
    if (!rf || !locateRequest) return

    // A NODE locate (search palette / review queue) OPENS the node in focus (ego)
    // mode — the same as shift-clicking it. Focus makes the node the root, so it's
    // always shown regardless of the current overview (no "not in this view" dead
    // end), reveals its connections, re-frames the camera (via the focus fitView),
    // and we flash it for a moment.
    if (locateRequest.kind === "node") {
      setFocus(locateRequest.id)
      const id = locateRequest.id
      const raf = window.requestAnimationFrame(() => setLocateFlash(new Set([id])))
      const timer = window.setTimeout(() => setLocateFlash(new Set()), 1600)
      return () => {
        window.cancelAnimationFrame(raf)
        window.clearTimeout(timer)
      }
    }

    // An EDGE locate frames + flashes both endpoints (edges have no position).
    const edge = edgeById.get(locateRequest.id)
    const ids = edge ? [edge.source, edge.target] : []
    const present = new Set(base.nodes.map((n) => n.id))
    const visibleIds = ids.filter((vid) => present.has(vid))
    if (visibleIds.length === 0) return
    rf.fitView({
      nodes: visibleIds.map((vid) => ({ id: vid })),
      duration: 450,
      padding: 0.4,
      maxZoom: 1.2,
      minZoom: OVERVIEW_MIN_ZOOM,
    })
    const raf = window.requestAnimationFrame(() => setLocateFlash(new Set(visibleIds)))
    const timer = window.setTimeout(() => setLocateFlash(new Set()), 1600)
    return () => {
      window.cancelAnimationFrame(raf)
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rf, locateTs])

  // Esc exits focus, unless typing in a field.
  useEffect(() => {
    if (!focus) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return
      if (e.key === "Escape") {
        e.preventDefault()
        setFocus(null)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [focus, setFocus])

  const toggleLabel = (l: string) =>
    setHiddenLabels((prev) => {
      const next = new Set(prev)
      if (next.has(l)) next.delete(l)
      else next.add(l)
      return next
    })
  const toggleType = (t: string) =>
    setHiddenTypes((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })

  // One-click "Causal only": hide every non-causal edge type (toggle to restore).
  const nonCausalTypes = useMemo(() => types.filter((t) => !CAUSAL_RELS.has(t)), [types])
  const causalOnlyActive =
    nonCausalTypes.length > 0 && nonCausalTypes.every((t) => hiddenTypes.has(t))
  const toggleCausalOnly = () =>
    setHiddenTypes(causalOnlyActive ? new Set<string>() : new Set(nonCausalTypes))

  // Focus-mode presets for the HUD (causal directional vs full ego).
  const goNeighborhood = () => {
    setFocusMode("causal")
    setFocusDir({ up: true, down: true })
  }
  const goUpstream = () => {
    setFocusMode("causal")
    setFocusDir({ up: true, down: false })
  }
  const goDownstream = () => {
    setFocusMode("causal")
    setFocusDir({ up: false, down: true })
  }
  const isCausal = focusMode === "causal"
  const isNeighborhood = isCausal && focusDir.up && focusDir.down
  const isUpstream = isCausal && focusDir.up && !focusDir.down
  const isDownstream = isCausal && focusDir.down && !focusDir.up

  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* hint / focus toolbar */}
      {focus ? (
        <div className="absolute left-1/2 top-3 z-10 flex max-w-[92%] -translate-x-1/2 flex-col items-center gap-1.5 rounded-lg border border-border bg-background/90 px-3 py-2 text-xs">
          {/* breadcrumb walk */}
          {trail.length > 1 && (
            <div className="flex max-w-full flex-wrap items-center gap-0.5 text-[11px] text-muted-foreground">
              {trail.map((t, i) => (
                <span key={t.id} className="flex items-center gap-0.5">
                  {i > 0 && <span className="opacity-50">›</span>}
                  <button
                    onClick={() => setFocus(t.id)}
                    className={`max-w-[150px] truncate rounded px-1 hover:bg-accent ${
                      i === trail.length - 1 ? "font-medium text-foreground" : ""
                    }`}
                  >
                    {t.title}
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* focus name + count + show-all + exit */}
          <div className="flex items-center gap-2">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{
                background: nodeColor(nodes.find((n) => n.id === focus)),
              }}
            />
            <span className="max-w-[220px] truncate font-medium">{focusName}</span>
            <span className="text-muted-foreground">
              ·{" "}
              {base.focusTotal > base.focusShown
                ? `${base.focusShown} of ${base.focusTotal}`
                : `${base.focusTotal} ${isCausal ? "metric links" : "connections"}`}
            </span>
            {base.focusTotal > base.focusShown && (
              <button
                onClick={() => setRingCap(Infinity)}
                className="rounded border border-border px-1.5 py-0.5 hover:bg-accent"
              >
                Show all ({base.focusTotal})
              </button>
            )}
            {ringCap === Infinity && base.focusTotal > 18 && (
              <button
                onClick={() => setRingCap(18)}
                className="rounded border border-border px-1.5 py-0.5 hover:bg-accent"
              >
                Top 18
              </button>
            )}
            <button
              onClick={() => setFocus(null)}
              title="Exit focus (Esc)"
              className="ml-1 rounded border border-border px-1.5 py-0.5 hover:bg-accent"
            >
              ✕
            </button>
          </div>

          {/* mode buttons */}
          <div className="flex flex-wrap items-center justify-center gap-1">
            <ModeBtn active={isNeighborhood} onClick={goNeighborhood}>
              Neighborhood
            </ModeBtn>
            <ModeBtn active={isUpstream} onClick={goUpstream}>
              ↑ Upstream
            </ModeBtn>
            <ModeBtn active={isDownstream} onClick={goDownstream}>
              ↓ Downstream
            </ModeBtn>
            <ModeBtn active={!isCausal} onClick={() => setFocusMode("all")}>
              All edges
            </ModeBtn>
          </div>

          {/* grow-along-edge chips (causal mode) */}
          {isCausal && causalKinds.length > 0 && (
            <div className="flex flex-wrap items-center justify-center gap-1">
              {causalKinds.map((k) => (
                <button
                  key={k.type}
                  onClick={() => setGrowKind(k.type)}
                  className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] hover:bg-accent ${
                    growKind === k.type ? "border-primary bg-primary/10" : "border-border"
                  }`}
                >
                  <i
                    className="inline-block h-1.5 w-3 rounded-full"
                    style={{ background: edgeVisual(k.type).color }}
                  />
                  {edgeVisual(k.type).label} {k.count}
                </button>
              ))}
            </div>
          )}

          {isCausal && base.focusShown === 0 && (
            <div className="text-[11px] text-muted-foreground">
              No {isUpstream ? "upstream drivers" : isDownstream ? "downstream effects" : "metric links"}
              {" "}— try{" "}
              <button onClick={goNeighborhood} className="underline hover:text-foreground">
                Neighborhood
              </button>{" "}
              or{" "}
              <button onClick={() => setFocusMode("all")} className="underline hover:text-foreground">
                All edges
              </button>
              .
            </div>
          )}
        </div>
      ) : (
        <div className="absolute left-1/2 top-3 z-10 max-w-[70%] -translate-x-1/2 truncate rounded-md border border-border bg-background/80 px-3 py-1 text-xs text-muted-foreground">
          Shift-click a metric to walk its parent + child metrics · hover for links · click empty space to exit
        </div>
      )}

      {/* category legend (bottom-left, offset right so it never covers the
          +/−/fit Controls that React Flow renders in the bottom-left corner) */}
      {legendCategories.length > 0 && (
        <div className="absolute bottom-3 left-16 z-10 flex max-w-[260px] flex-wrap gap-x-2.5 gap-y-1 rounded-md border border-border bg-background/80 px-2.5 py-1.5 text-[10px]">
          {legendCategories.map((c) => {
            const st = categoryStyle(c)
            return (
              <span key={c} className="flex items-center gap-1 text-muted-foreground">
                <i className="inline-block size-2 rounded-full" style={{ background: st.color }} />
                {st.label}
              </span>
            )
          })}
        </div>
      )}

      {/* left: overview toggle (Spine/Tree/Map) + causal-only + show-unconnected */}
      <div className="absolute left-3 top-3 z-10 flex gap-1.5">
        {/* Spine = metrics under their Domain (connected) · Tree = decomposition
            forest · Map = compact hub skeleton */}
        <div className="flex items-center rounded-md border border-border bg-background/80 p-0.5">
          {(["spine", "tree", "map", "dash"] as const).map((v) => (
            <button
              key={v}
              onClick={() => pickOverview(v)}
              aria-pressed={overview === v}
              title={
                v === "spine"
                  ? "Spine — every metric grouped under its Domain (Business → Domain → metric)"
                  : v === "tree"
                    ? "Decomposition tree — the metric→metric forest"
                    : v === "map"
                      ? "Hub map — the metrics everything decomposes into"
                      : "Dashboards — main dashboards grouped by product; shift-click one for its charts + metrics"
              }
              className={`rounded px-2.5 py-0.5 text-xs capitalize transition-colors ${
                overview === v
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              {v === "dash" ? "Dashboards" : v}
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          aria-pressed={causalOnlyActive}
          onClick={toggleCausalOnly}
          title="Show only metric→metric causal edges (hide spine)"
          className="bg-background/80 aria-pressed:bg-muted"
        >
          {causalOnlyActive ? "Causal only ✓" : "Causal only"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!!focus || overview === "map"}
          aria-pressed={showIsolated}
          onClick={() => setShowIsolated((v) => !v)}
          title={overview === "map" ? "Switch to Tree to show unconnected nodes" : undefined}
          className="bg-background/80 aria-pressed:bg-muted"
        >
          {showIsolated ? `Unconnected${isoCount ? ` (${isoCount})` : ""}` : "Show unconnected"}
        </Button>
      </div>

      <FilterPanel
        labels={labels}
        types={types}
        hiddenLabels={hiddenLabels}
        hiddenTypes={hiddenTypes}
        onToggleLabel={toggleLabel}
        onToggleType={toggleType}
      />

      <ReactFlow
        nodes={styled.nodes}
        edges={styled.edges}
        nodeTypes={nodeTypes}
        onInit={setRf}
        fitView
        fitViewOptions={{ maxZoom: 1, minZoom: OVERVIEW_MIN_ZOOM, padding: VIEW_PADDING }}
        minZoom={OVERVIEW_MIN_ZOOM}
        // Theme React Flow's built-in chrome (Controls +/−/fit, MiniMap) with the app.
        colorMode={dark ? "dark" : "light"}
        // Perf + interaction model: virtualize off-screen elements, no dragging /
        // connecting (the layout is deterministic), and reserve Shift entirely for
        // our shift-click-to-focus (React Flow's built-in shift box/multi-select
        // would otherwise swallow it).
        onlyRenderVisibleElements
        nodesDraggable={false}
        nodesConnectable={false}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
        onNodeClick={(e, n) => {
          // Shift-click enters focus; while already focused, a plain click on a
          // neighbor re-centers (walk to the next hop). Always keep selection.
          if (e.shiftKey || focus) setFocus(n.id)
          selectNode(n.id)
          selectEdge(null)
          // Shift-click drill-down, dispatched by node kind (each action no-ops
          // for the wrong label): Dashboard → its charts; Product → its
          // dashboards; a governed Metric → its policy/threshold (full-ego
          // focus); any other Metric → its chart. Runs after focus/select.
          if (e.shiftKey) {
            const sn = nodes.find((x) => x.id === n.id)
            if (sn?.label === "Dashboard") void revealDashboardCharts(n.id)
            else if (sn?.label === "IntelligenceProduct") void revealProductDashboards(n.id)
            else if (sn?.label === "Metric" && governedIds.has(n.id)) {
              // Make sure the governance edges aren't hidden (e.g. "Causal only"),
              // then center on the metric's policy/threshold fan.
              setHiddenTypes((prev) => {
                const next = new Set(prev)
                for (const t of ["HAS_THRESHOLD", "ENFORCES_THRESHOLD", "GOVERNS"]) next.delete(t)
                return next.size === prev.size ? prev : next
              })
              revealMetricGovernance(n.id)
            } else void revealMetricChart(n.id)
          }
        }}
        onEdgeClick={(_, e) => selectEdge(e.id)}
        onNodeMouseEnter={(_, n) => setHovered(n.id)}
        onNodeMouseLeave={() => setHovered(null)}
        onPaneClick={() => {
          if (focus) setFocus(null)
          selectNode(null)
          selectEdge(null)
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background color={dark ? "#1b2435" : "#dbe2ec"} gap={26} />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          zoomStep={4}
          bgColor={mini.bg}
          maskColor={mini.mask}
          nodeColor={(n) => nodeColor((n.data as FlowNodeData | undefined)?.node)}
          nodeStrokeColor={(n) => nodeColor((n.data as FlowNodeData | undefined)?.node)}
          nodeStrokeWidth={16}
          nodeBorderRadius={3}
          style={{ border: `1px solid ${mini.border}`, borderRadius: 8 }}
        />
      </ReactFlow>
    </div>
  )
}
