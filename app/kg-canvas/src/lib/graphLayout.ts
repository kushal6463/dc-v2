// graphLayout.ts — deterministic layout + graph -> @xyflow/react mapping.
//
// Two layout modes:
//   • OVERVIEW: packed tree — connected nodes split into weakly-connected
//     components, each laid out by dagre INDEPENDENTLY (overlap-free within a
//     tree), then shelf-packed side by side; isolated nodes go in a tray below.
//     (Ported from dc-v2's graph-layout.)
//   • FOCUS: a radial ego ring around the focused node showing all its
//     directly-connected neighbors (ported from dc-v2's ego-layout).
//
// `buildLayout` produces the React-Flow nodes/edges for the current view, and
// `emphasize` is a cheap hover/focus pass that brightens edges/nodes WITHOUT
// recomputing the layout.
//
// Pure + deterministic: every id/edge list is sorted, no wall-clock, no randomness.

import dagre from "dagre"
import type { Edge, Node } from "@xyflow/react"
import { MarkerType, Position } from "@xyflow/react"

import type { GraphEdge, GraphNode } from "@/lib/api"
import { edgeVisual, type EdgeTier } from "@/lib/graphTheme"
import {
  causalRing,
  directionalRadial,
  radialPositions,
  rankedNeighbors,
} from "@/lib/egoLayout"

/** Options controlling the FOCUS layout (causal directional ring vs full ego). */
export type FocusOpts = {
  mode: "causal" | "all"
  dir: { up: boolean; down: boolean }
  ringCap: number
  growKind: string | null
  causalRels: Set<string>
}

const DEFAULT_FOCUS_OPTS: FocusOpts = {
  mode: "all",
  dir: { up: true, down: true },
  ringCap: Infinity,
  growKind: null,
  causalRels: new Set(),
}

export const NODE_WIDTH = 190
export const NODE_HEIGHT = 60

export type XY = { x: number; y: number }
export type Size = { w: number; h: number }
export type Sizer = (id: string) => Size
export type LayoutEdge = { source: string; target: string }

export interface FlowNodeData extends Record<string, unknown> {
  node: GraphNode
  selected: boolean
  dim: boolean
  root: boolean
  /** Transient: flashed by a locate() request. */
  flash?: boolean
  /** No edges in the current view (a leaf / orphan) — gets a dashed ring. */
  leaf?: boolean
  /** On a feedback loop in the current view — gets a solid ring. */
  loop?: boolean
  /** Metric carries a Policy/Threshold (governance) — gets a § badge. */
  governed?: boolean
}

export type FlowNode = Node<FlowNodeData>

export type EdgeData = {
  type: string
  color: string
  tier: EdgeTier
  label: string
  baseWidth: number
  baseOpacity: number
  dash?: string
}
export type FlowEdge = Edge<EdgeData>

const layoutSize: Sizer = () => ({ w: NODE_WIDTH, h: NODE_HEIGHT + 24 })

// ---------------------------------------------------------------------------
// Packed-tree overview layout (ported from dc-v2 graph-layout.ts)
// ---------------------------------------------------------------------------

const GAP = 80 // breathing room between packed component boxes

/* Union-find → weakly-connected components over the (undirected) edge set. */
function components(ids: string[], edges: LayoutEdge[]): string[][] {
  const parent = new Map<string, string>(ids.map((id) => [id, id]))
  const find = (x: string): string => {
    let r = x
    while (parent.get(r) !== r) r = parent.get(r)!
    while (parent.get(x) !== r) {
      const next = parent.get(x)!
      parent.set(x, r)
      x = next
    }
    return r
  }
  const union = (a: string, b: string) => {
    const ra = find(a)
    const rb = find(b)
    if (ra !== rb) parent.set(ra, rb)
  }
  for (const e of edges) if (parent.has(e.source) && parent.has(e.target)) union(e.source, e.target)

  const groups = new Map<string, string[]>()
  for (const id of ids) {
    const r = find(id)
    const g = groups.get(r)
    if (g) g.push(id)
    else groups.set(r, [id])
  }
  const out = [...groups.values()].map((g) => g.sort((a, b) => a.localeCompare(b)))
  out.sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]))
  return out
}

/* dagre one component → normalized top-left positions (origin at 0,0) + box size. */
function layoutComponent(
  ids: string[],
  edges: LayoutEdge[],
  size: Sizer,
  rankdir: "TB" | "LR",
  nodesep: number,
  ranksep: number,
): { local: Record<string, XY>; w: number; h: number } {
  // Fast path for singleton components (the overview packs hundreds of them) —
  // a lone node needs no dagre pass.
  if (ids.length === 1) {
    const s = size(ids[0])
    return { local: { [ids[0]]: { x: 0, y: 0 } }, w: s.w, h: s.h }
  }
  const idset = new Set(ids)
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir, nodesep, ranksep, edgesep: 24, marginx: 0, marginy: 0, ranker: "tight-tree" })
  g.setDefaultEdgeLabel(() => ({}))
  for (const id of ids) {
    const s = size(id)
    g.setNode(id, { width: s.w, height: s.h })
  }
  const seen = new Set<string>()
  const local: Record<string, XY> = {}
  const sorted = edges
    .filter((e) => idset.has(e.source) && idset.has(e.target) && e.source !== e.target)
    .sort((a, b) => a.source.localeCompare(b.source) || a.target.localeCompare(b.target))
  for (const e of sorted) {
    const k = `${e.source}->${e.target}`
    if (seen.has(k)) continue
    seen.add(k)
    g.setEdge(e.source, e.target, { weight: 2, minlen: 1 })
  }
  dagre.layout(g)

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const id of ids) {
    const dn = g.node(id) as { x: number; y: number } | undefined
    const s = size(id)
    const x = (dn?.x ?? 0) - s.w / 2
    const y = (dn?.y ?? 0) - s.h / 2
    local[id] = { x, y }
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + s.w)
    maxY = Math.max(maxY, y + s.h)
  }
  if (!Number.isFinite(minX)) {
    minX = 0
    minY = 0
    maxX = 0
    maxY = 0
  }
  for (const id of ids) local[id] = { x: local[id].x - minX, y: local[id].y - minY }
  return { local, w: maxX - minX, h: maxY - minY }
}

/** Overlap-free tree layout. Connected nodes split into components, each laid out
 *  by dagre, then shelf-packed into a roughly square grid of trees. */
export function packedTree(
  ids: string[],
  edges: LayoutEdge[],
  size: Sizer,
  opts: { rankdir?: "TB" | "LR"; nodesep?: number; ranksep?: number } = {},
): { pos: Record<string, XY>; width: number; height: number } {
  const rankdir = opts.rankdir ?? "TB"
  const nodesep = opts.nodesep ?? 56
  const ranksep = opts.ranksep ?? 130
  if (ids.length === 0) return { pos: {}, width: 0, height: 0 }

  const comps = components(ids, edges).map((c) => layoutComponent(c, edges, size, rankdir, nodesep, ranksep))

  const sumArea = comps.reduce((a, c) => a + c.w * c.h, 0)
  const maxCompW = comps.reduce((a, c) => Math.max(a, c.w), 0)
  // Shelf width ≈ K·√area sets the packed field's aspect ratio (≈ K² wide:tall
  // before packing slack). K≈1.5 targets a screen-ish ratio so the field fills the
  // viewport instead of letterboxing into a thin band. The widest single component
  // is still a hard floor (a tree never gets clipped).
  const maxRow = Math.max(maxCompW, Math.sqrt(sumArea) * 1.5)

  const pos: Record<string, XY> = {}
  let cursorX = 0
  let shelfY = 0
  let shelfH = 0
  let width = 0
  for (const comp of comps) {
    if (cursorX > 0 && cursorX + comp.w > maxRow) {
      shelfY += shelfH + GAP
      cursorX = 0
      shelfH = 0
    }
    for (const id of Object.keys(comp.local)) {
      pos[id] = { x: comp.local[id].x + cursorX, y: comp.local[id].y + shelfY }
    }
    cursorX += comp.w + GAP
    shelfH = Math.max(shelfH, comp.h)
    width = Math.max(width, cursorX - GAP)
  }
  return { pos, width, height: shelfY + shelfH }
}

/* Grid tray for isolated nodes (no visible edge). Deterministic row-major fill. */
export function trayLayout(
  ids: string[],
  size: Sizer,
  originX: number,
  originY: number,
  maxWidth: number,
): Record<string, XY> {
  const pos: Record<string, XY> = {}
  if (ids.length === 0) return pos
  const cell = size(ids[0])
  const colW = cell.w + 26
  const rowH = cell.h + 22
  const cols = Math.max(6, Math.floor(maxWidth / colW) || 6)
  ids.forEach((id, i) => {
    pos[id] = { x: originX + (i % cols) * colW, y: originY + Math.floor(i / cols) * rowH }
  })
  return pos
}

// ---------------------------------------------------------------------------
// Build the React-Flow view (overview OR focus)
// ---------------------------------------------------------------------------

function baseEdgeStyle(tier: EdgeTier): { width: number; opacity: number; dash?: string } {
  if (tier === "spine") return { width: 1.6, opacity: 0.85 }
  if (tier === "lateral") return { width: 1.1, opacity: 0.4, dash: "5 4" }
  return { width: 1.2, opacity: 0.55 }
}
const tierZ = (t: EdgeTier) => (t === "spine" ? 2 : t === "structural" ? 1 : 0)

export type BuildResult = {
  nodes: FlowNode[]
  edges: FlowEdge[]
  focusTotal: number // total neighbors of the focused node (0 in overview)
  focusShown: number // neighbors actually rendered after the ring cap
}

/** Does `rootId` touch at least one causal edge? Gates causal-vs-full focus so a
 *  metric with no metric→metric links never lands on a blank causal canvas. */
function hasCausalLinks(edges: GraphEdge[], rootId: string, rels: Set<string>): boolean {
  for (const e of edges) {
    if (rels.has(e.type) && (e.source === rootId || e.target === rootId)) return true
  }
  return false
}

/** The decomposition hub relation — "map" overview is built from its in-degree. */
const HUB_REL = "DECOMPOSES_INTO"

/**
 * Relations that SHAPE the "tree" overview. The metric→metric decomposition forest
 * plus the spine's own internal hierarchy (Business→Domain/Product/Platform).
 *
 * Deliberately EXCLUDES the metric→spine ATTACHMENT relations
 * (BELONGS_TO_DOMAIN / PART_OF_PRODUCT / SOURCES): every one of the ~885 metrics
 * attaches to one of only ~19 shared spine hubs, so letting those edges shape the
 * layout collapses the whole graph into a single dagre component — one flat,
 * unreadable horizontal band. With them out, the graph is a forest of small
 * decomposition trees (+ the compact spine skeleton) that shelf-packs into a
 * roughly-square 2D field. The attachment edges are still in the data and are
 * revealed when you focus a metric.
 */
const SHAPING_RELS = new Set([
  "DECOMPOSES_INTO",
  "HAS_DOMAIN",
  "HAS_PRODUCT",
  "USES_PLATFORM",
])

// Relations stored one way but read more naturally the other way. GOVERNS is
// persisted Policy→Metric; we render it Metric→Policy so it reads "Metric
// governed by Policy" (matching the EDGE_MAP label). Render-only — the stored
// edge direction and layout are untouched; we just swap the drawn endpoints so
// the arrowhead and label agree.
const INVERT_RENDER_RELS = new Set(["GOVERNS"])

/**
 * Metric ids that carry governance — i.e. have a Threshold (``HAS_THRESHOLD``
 * out of the metric) or a Policy (``GOVERNS`` into the metric). Drives the node
 * badge and the shift-click governance reveal. Mirrors the adjacency style used
 * elsewhere; cheap to recompute from the edge list.
 */
export function governedMetricIds(edges: GraphEdge[]): Set<string> {
  const out = new Set<string>()
  for (const e of edges) {
    if (e.type === "HAS_THRESHOLD") out.add(e.source)
    else if (e.type === "GOVERNS") out.add(e.target)
  }
  return out
}

/**
 * SPINE overview — every metric clustered UNDER its primary Domain, with
 * Business → Domain → metric actually drawn.
 *
 * This is the answer to "why isn't anything connected?": most metrics have NO
 * metric→metric decomposition — they hang off the tri-axis spine — so a
 * decomposition-only view shows them as floating islands. Here each Domain is a
 * labelled cluster (its metrics packed beneath the Domain header by their
 * intra-domain decomposition), the clusters shelf-pack into a 2D field, and the
 * Business root sits above them. Each metric draws ONE short local edge up to its
 * own Domain header (not a canvas-spanning hairball), so the connectivity is
 * visible and legible.
 */
function spineGroupedLayout(
  sorted: GraphNode[],
  edges: GraphEdge[],
  present: Set<string>,
): { pos: Record<string, XY>; members: Set<string>; drawEdges: GraphEdge[] } {
  // Group metrics by their PRIMARY domain — derived from the actual
  // BELONGS_TO_DOMAIN edges (target ids are Domain node ids), so the grouping key
  // always matches a real Domain node.
  const domainsOf = new Map<string, string[]>()
  for (const e of edges) {
    if (e.type === "BELONGS_TO_DOMAIN" && present.has(e.source) && present.has(e.target)) {
      const arr = domainsOf.get(e.source)
      if (arr) arr.push(e.target)
      else domainsOf.set(e.source, [e.target])
    }
  }
  const primaryDomain = (metricId: string): string => {
    const ds = domainsOf.get(metricId)
    return ds && ds.length ? [...ds].sort((a, b) => a.localeCompare(b))[0] : "__none__"
  }

  const metrics = sorted.filter((n) => n.label === "Metric")
  const domainById = new Map(
    sorted.filter((n) => n.label === "Domain").map((n) => [n.id, n] as const),
  )
  const business = sorted.find((n) => n.label === "Business")

  const groups = new Map<string, string[]>()
  for (const m of metrics) {
    const key = primaryDomain(m.id)
    const arr = groups.get(key)
    if (arr) arr.push(m.id)
    else groups.set(key, [m.id])
  }

  const decomp = edges.filter(
    (e) => e.type === "DECOMPOSES_INTO" && present.has(e.source) && present.has(e.target),
  )

  // Pack each domain group (its intra-domain decomposition keeps sub-trees tidy).
  type Block = { key: string; bpos: Record<string, XY>; w: number; h: number }
  const blocks: Block[] = []
  for (const key of [...groups.keys()].sort((a, b) => a.localeCompare(b))) {
    const ids = groups.get(key)!
    const idset = new Set(ids)
    const intra = decomp
      .filter((e) => idset.has(e.source) && idset.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }))
    const t = packedTree(ids, intra, layoutSize, { rankdir: "TB" })
    blocks.push({ key, bpos: t.pos, w: Math.max(t.width, NODE_WIDTH), h: t.height })
  }

  const HEADER = NODE_HEIGHT + 70 // room for the Domain header above each block
  const GAPX = 150
  const GAPY = 180
  const sumArea = blocks.reduce((a, b) => a + b.w * (b.h + HEADER), 0)
  const maxRow = Math.max(
    blocks.reduce((m, b) => Math.max(m, b.w), 0),
    Math.sqrt(sumArea) * 1.4,
  )

  const pos: Record<string, XY> = {}
  const members = new Set<string>()
  let cursorX = 0
  let shelfY = HEADER + GAPY // top band reserved for the Business root
  let shelfH = 0
  let fieldW = 0
  for (const b of blocks) {
    if (cursorX > 0 && cursorX + b.w > maxRow) {
      shelfY += shelfH + HEADER + GAPY
      cursorX = 0
      shelfH = 0
    }
    const dom = domainById.get(b.key)
    if (dom) {
      pos[dom.id] = { x: cursorX + b.w / 2 - NODE_WIDTH / 2, y: shelfY - HEADER }
      members.add(dom.id)
    }
    for (const id of Object.keys(b.bpos)) {
      pos[id] = { x: cursorX + b.bpos[id].x, y: shelfY + b.bpos[id].y }
      members.add(id)
    }
    cursorX += b.w + GAPX
    shelfH = Math.max(shelfH, b.h)
    fieldW = Math.max(fieldW, cursorX - GAPX)
  }
  if (business) {
    pos[business.id] = { x: Math.max(0, fieldW / 2 - NODE_WIDTH / 2), y: 0 }
    members.add(business.id)
  }

  // Draw: Business→Domain, each metric→its PRIMARY domain (one short local edge),
  // plus decomposition + influences. (Secondary multi-domain edges are omitted so
  // the view stays a clean tree, not a cross-cluster hairball.)
  const drawEdges = edges.filter((e) => {
    if (!members.has(e.source) || !members.has(e.target)) return false
    if (e.type === "HAS_DOMAIN" || e.type === "DECOMPOSES_INTO" || e.type === "INFLUENCES") {
      return true
    }
    if (e.type === "BELONGS_TO_DOMAIN") return primaryDomain(e.source) === e.target
    return false
  })
  return { pos, members, drawEdges }
}

/** A dashboard is a curated "main" surface (shown at the top level of the
 *  Dashboards view) when it is an executive/review summary OR a channel overview.
 *  Everything else (operational sub-views, ml-* dashboards) is reachable by
 *  drilling into its Product. */
export function isMainDashboard(n: GraphNode): boolean {
  const t = String((n.props as Record<string, unknown> | undefined)?.dashboard_type ?? "")
  return t === "executive" || t === "review" || n.id.endsWith("-overview")
}

/**
 * DASHBOARDS overview — the curated-main Dashboard nodes clustered under their
 * Product (the existing IntelligenceProduct node is the cluster header; grouping
 * is by the Dashboard's `product_id` prop, since no Dashboard→Product edge
 * exists). Dashboards have no edges among themselves, so each product block is a
 * simple packed grid of its mains. Non-main dashboards are omitted here — they
 * appear when a Product is shift-clicked (revealProductDashboards). A layout-only
 * Dashboard→Product membership edge is drawn per cluster for legibility.
 */
function dashboardGroupedLayout(sorted: GraphNode[]): {
  pos: Record<string, XY>
  members: Set<string>
  drawEdges: GraphEdge[]
} {
  const mains = sorted.filter((n) => n.label === "Dashboard" && isMainDashboard(n))
  const productById = new Map(
    sorted.filter((n) => n.label === "IntelligenceProduct").map((n) => [n.id, n] as const),
  )
  const productOf = (d: GraphNode): string =>
    String((d.props as Record<string, unknown> | undefined)?.product_id ?? "__none__")

  const groups = new Map<string, string[]>()
  for (const d of mains) {
    const key = productOf(d)
    const arr = groups.get(key)
    if (arr) arr.push(d.id)
    else groups.set(key, [d.id])
  }

  type Block = { key: string; bpos: Record<string, XY>; w: number; h: number }
  const blocks: Block[] = []
  for (const key of [...groups.keys()].sort((a, b) => a.localeCompare(b))) {
    const ids = groups.get(key)!.sort((a, b) => a.localeCompare(b))
    const t = packedTree(ids, [], layoutSize, { rankdir: "TB" }) // no intra edges → grid
    blocks.push({ key, bpos: t.pos, w: Math.max(t.width, NODE_WIDTH), h: t.height })
  }

  const HEADER = NODE_HEIGHT + 70 // room for the Product header above each block
  const GAPX = 150
  const GAPY = 180
  const sumArea = blocks.reduce((a, b) => a + b.w * (b.h + HEADER), 0)
  const maxRow = Math.max(
    blocks.reduce((m, b) => Math.max(m, b.w), 0),
    Math.sqrt(sumArea) * 1.4,
  )

  const pos: Record<string, XY> = {}
  const members = new Set<string>()
  let cursorX = 0
  let shelfY = 0
  let shelfH = 0
  for (const b of blocks) {
    if (cursorX > 0 && cursorX + b.w > maxRow) {
      shelfY += shelfH + HEADER + GAPY
      cursorX = 0
      shelfH = 0
    }
    const prod = productById.get(b.key)
    if (prod) {
      pos[prod.id] = { x: cursorX + b.w / 2 - NODE_WIDTH / 2, y: shelfY }
      members.add(prod.id)
    }
    for (const id of Object.keys(b.bpos)) {
      pos[id] = { x: cursorX + b.bpos[id].x, y: shelfY + HEADER + b.bpos[id].y }
      members.add(id)
    }
    cursorX += b.w + GAPX
    shelfH = Math.max(shelfH, b.h)
  }

  // Layout-only membership edges (dashboard → its product) for the cluster visual.
  const drawEdges: GraphEdge[] = []
  for (const [key, ids] of groups) {
    if (!productById.has(key)) continue
    for (const did of ids) {
      drawEdges.push({
        id: `dashmem::${did}->${key}`,
        source: did,
        target: key,
        type: "PART_OF_PRODUCT",
        props: {},
      } as GraphEdge)
    }
  }
  return { pos, members, drawEdges }
}

/**
 * Build React-Flow nodes+edges for the current view. With `focus` set, lays out a
 * radial ego ring of the focused node's full neighborhood (others hidden). Without
 * focus, the overview is either:
 *   • "tree" — the overlap-free packed decomposition spine (every connected node);
 *   • "map"  — only the decomposition HUB metrics (the bases everything decomposes
 *              INTO) + the hub→hub decompose edges. A compact, high-level map.
 * Unconnected nodes are dropped unless `showIsolated` is set (keeps the rendered
 * DOM small for smooth zoom/pan); `showIsolated` has no effect in "map".
 */
export function buildLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  focus: string | null,
  selectedNodeId: string | null,
  showIsolated: boolean = false,
  focusOpts: FocusOpts = DEFAULT_FOCUS_OPTS,
  overview: "map" | "tree" | "spine" | "dash" = "spine",
): BuildResult {
  const sorted = [...nodes].sort((a, b) => a.id.localeCompare(b.id))

  let pos: Record<string, XY> = {}
  let members: Set<string> | null = null // when set, only these ids render
  let drawEdges: GraphEdge[] = edges
  let curvedAll = false
  let focusTotal = 0
  let focusShown = 0

  const present = new Set(sorted.map((n) => n.id))
  const hasFocus = focus && present.has(focus)

  if (hasFocus && focusOpts.mode === "causal" && hasCausalLinks(edges, focus, focusOpts.causalRels)) {
    // Directional metric→metric ring: parents (incoming) on the upper arc,
    // children (outgoing) below — only causal edges, only Metric neighbors.
    // (Only taken when the focus actually HAS causal links — otherwise we fall
    // through to the full ego ring below, so shift-click never lands on a blank
    // canvas for a metric with no metric→metric edges.)
    const ring = causalRing(sorted, edges, focus, {
      rels: focusOpts.causalRels,
      metricsOnly: true,
      cap: focusOpts.ringCap,
      growKind: focusOpts.growKind,
    })
    const parents = focusOpts.dir.up ? ring.parents : []
    const children = focusOpts.dir.down ? ring.children : []
    pos = directionalRadial(focus, parents, children, { x: 0, y: 0 })
    members = new Set([focus, ...parents, ...children])
    drawEdges = edges.filter(
      (e) =>
        focusOpts.causalRels.has(e.type) &&
        members!.has(e.source) &&
        members!.has(e.target),
    )
    curvedAll = true
    focusTotal = ring.total
    focusShown = parents.length + children.length
  } else if (hasFocus) {
    const rk = rankedNeighbors(sorted, edges, focus, focusOpts.ringCap, focusOpts.growKind)
    const kindOf = new Map(sorted.map((n) => [n.id, n.label]))
    pos = radialPositions(focus, rk.kept, { x: 0, y: 0 }, { kindOf })
    members = new Set([focus, ...rk.kept])
    drawEdges = edges.filter((e) => members!.has(e.source) && members!.has(e.target))
    curvedAll = true
    focusTotal = rk.total
    focusShown = rk.kept.length
  } else if (overview === "dash") {
    // DASHBOARDS overview — curated-main dashboards clustered under their Product.
    const g = dashboardGroupedLayout(sorted)
    pos = g.pos
    members = g.members
    drawEdges = g.drawEdges
  } else if (overview === "spine") {
    // SPINE overview — metrics clustered under their Domain, Business→Domain→metric
    // drawn (see spineGroupedLayout). The default: shows the graph as CONNECTED.
    const g = spineGroupedLayout(sorted, edges, present)
    if (g.members.size > 0) {
      pos = g.pos
      members = g.members
      drawEdges = g.drawEdges
    } else {
      // The filtered set has NO spine (Metric/Domain/Business) — e.g. the user
      // filtered NODE KINDS to Dashboard / Policy / Threshold / Chart only. Pack
      // every filtered node (using whatever edges exist among them) so the
      // selection actually renders — isolated kinds become a grid — instead of a
      // blank canvas.
      const allIds = sorted.map((n) => n.id)
      const shaping = edges
        .filter((e) => present.has(e.source) && present.has(e.target) && e.source !== e.target)
        .map((e) => ({ source: e.source, target: e.target }))
      pos = packedTree(allIds, shaping, layoutSize, { rankdir: "TB" }).pos
      members = new Set(allIds)
      drawEdges = edges
    }
  } else {
    // OVERVIEW (no focus). "map" = hub-only skeleton; "tree" = full spine.
    let built = false
    if (overview === "map") {
      // Hubs = metrics that things decompose INTO. in-degree over HUB_REL ranks them;
      // we render those hubs + the hub→hub decompose edges (dc-v2 "hubs" view).
      const indeg = new Map<string, number>()
      for (const e of edges) {
        if (e.type === HUB_REL && present.has(e.source) && present.has(e.target)) {
          indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1)
        }
      }
      const hubIds = sorted.filter((n) => indeg.has(n.id)).map((n) => n.id)
      if (hubIds.length > 0) {
        members = new Set(hubIds)
        const hubEdges = edges.filter(
          (e) => e.type === HUB_REL && indeg.has(e.source) && indeg.has(e.target),
        )
        drawEdges = hubEdges
        pos = packedTree(
          hubIds,
          hubEdges.map((e) => ({ source: e.source, target: e.target })),
          layoutSize,
          { rankdir: "TB" },
        ).pos
        built = true
      }
      // else: no decomposition hubs → fall through to the full tree (never blank).
    }
    if (!built) {
      // Shape ONLY on the decomposition forest + spine skeleton (SHAPING_RELS) — the
      // metric→spine attachment edges are excluded so the graph stays a many-tree
      // forest that packs into 2D instead of one flat band (see SHAPING_RELS).
      const seen = new Set<string>()
      const shaping = edges
        .filter((e) => SHAPING_RELS.has(e.type) && e.source !== e.target)
        .filter((e) => present.has(e.source) && present.has(e.target))
        .filter((e) => {
          const k = `${e.source}->${e.target}`
          if (seen.has(k)) return false
          seen.add(k)
          return true
        })
      const connected = new Set<string>()
      for (const e of shaping) {
        connected.add(e.source)
        connected.add(e.target)
      }
      // Default view = the STRUCTURED nodes (decomposition forest + spine skeleton);
      // "Show unconnected" also packs the attachment-only / isolated metrics in as
      // singleton components — both into the SAME shelf-packed field (no flat tray).
      const fieldIds = (
        showIsolated ? sorted : sorted.filter((n) => connected.has(n.id))
      ).map((n) => n.id)
      pos = packedTree(fieldIds, shaping, layoutSize, { rankdir: "TB" }).pos
      members = new Set(fieldIds)
      // Draw the structural forest + lateral influences only — NOT the metric→spine
      // attachment edges (they'd be a hairball at overview scale; shown on focus).
      drawEdges = edges.filter(
        (e) => SHAPING_RELS.has(e.type) || e.type === "INFLUENCES",
      )
    }
  }

  // Synthetic VIEW nodes (e.g. the shift-click Chart node, props.synthetic) are
  // not part of the causal/structural layout, so the ring/overview passes above
  // never include them. Splice in any synthetic node whose anchor (the other end
  // of its synthetic edge) is already visible — positioned just beside that
  // anchor — plus its connecting edge, so the revealed chart always renders next
  // to its metric (in causal focus, full ego, AND overview).
  if (members) {
    const synthIds = new Set(
      sorted
        .filter((n) => (n.props as Record<string, unknown> | undefined)?.synthetic)
        .map((n) => n.id),
    )
    if (synthIds.size) {
      const extraEdges: GraphEdge[] = []
      for (const e of edges) {
        const sSyn = synthIds.has(e.source)
        const tSyn = synthIds.has(e.target)
        if (sSyn === tSyn) continue // skip non-synthetic and synth↔synth edges
        const anchor = sSyn ? e.target : e.source
        const synthId = sSyn ? e.source : e.target
        if (!members.has(anchor) || members.has(synthId)) continue
        members.add(synthId)
        const a = pos[anchor] ?? { x: 0, y: 0 }
        pos[synthId] = { x: a.x + NODE_WIDTH + 80, y: a.y + 120 }
        extraEdges.push(e)
      }
      if (extraEdges.length) drawEdges = [...drawEdges, ...extraEdges]
    }
  }

  // `members && members.size > 0`: an EMPTY Set is truthy, so a layout that
  // produced no members (e.g. a filtered set with nothing it could lay out) must
  // fall back to showing all `sorted` rather than silently rendering nothing.
  const visible =
    members && members.size > 0 ? sorted.filter((n) => members!.has(n.id)) : sorted
  const governed = governedMetricIds(edges)
  const rfNodes: FlowNode[] = visible.map((n) => ({
    id: n.id,
    type: "kg",
    position: pos[n.id] ?? { x: 0, y: 0 },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    data: {
      node: n,
      selected: n.id === selectedNodeId,
      dim: false,
      root: focus === n.id,
      governed: governed.has(n.id),
    },
  }))

  const visibleIds = new Set(visible.map((n) => n.id))
  const rfEdges: FlowEdge[] = drawEdges
    .filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
    .map((e) => {
      const v = edgeVisual(e.type)
      const b = baseEdgeStyle(v.tier)
      const type = curvedAll || v.tier === "lateral" ? "default" : "smoothstep"
      // Render-only direction flip (see INVERT_RENDER_RELS): swap the drawn
      // endpoints so the arrowhead matches the label's reading.
      const invert = INVERT_RENDER_RELS.has(e.type)
      const source = invert ? e.target : e.source
      const target = invert ? e.source : e.target
      return {
        id: e.id,
        source,
        target,
        type,
        zIndex: tierZ(v.tier),
        markerEnd: { type: MarkerType.ArrowClosed, color: v.color, width: 13, height: 13 },
        style: { stroke: v.color, strokeWidth: b.width, opacity: b.opacity, strokeDasharray: b.dash },
        data: {
          type: e.type,
          color: v.color,
          tier: v.tier,
          label: v.label,
          baseWidth: b.width,
          baseOpacity: b.opacity,
          dash: b.dash,
        },
      } satisfies FlowEdge
    })

  return { nodes: rfNodes, edges: rfEdges, focusTotal, focusShown }
}

/**
 * Cheap hover/focus emphasis — runs WITHOUT recomputing the layout. Edges touching
 * the hovered (or focused) node brighten + reveal their type label; the rest fade.
 * Hovering a node spotlights it + its neighbors (others dim).
 */
export function emphasize(
  base: { nodes: FlowNode[]; edges: FlowEdge[] },
  hoveredId: string | null,
  focus: string | null,
  dark: boolean,
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  // Idle (nothing hovered, no focus): the base styling is already correct, so return
  // it untouched. This keeps every node/edge object identity stable, so React Flow
  // and the memoized node component skip re-rendering entirely.
  if (!hoveredId && !focus) return base

  const litNodes = new Set<string>()
  if (hoveredId) {
    litNodes.add(hoveredId)
    for (const e of base.edges) {
      if (e.source === hoveredId) litNodes.add(e.target)
      if (e.target === hoveredId) litNodes.add(e.source)
    }
  }
  const spotlight = !!hoveredId

  const labelFill = dark ? "#dbe3ef" : "#1e293b"
  const labelBg = dark ? "#0c0f16" : "#ffffff"

  const edges = base.edges.map((e) => {
    const d = e.data as EdgeData
    const touchesHover = hoveredId ? e.source === hoveredId || e.target === hoveredId : false
    const touchesFocus = focus ? e.source === focus || e.target === focus : false
    const lit = touchesHover || (!spotlight && touchesFocus)
    let opacity = d.baseOpacity
    let width = d.baseWidth
    let showLabel = false
    let z = (e.zIndex as number | undefined) ?? 0
    if (spotlight) {
      if (lit) {
        opacity = 1
        width = d.baseWidth + 0.9
        showLabel = true
        z = 6
      } else {
        opacity = d.tier === "lateral" ? 0.06 : 0.1
      }
    } else if (touchesFocus) {
      showLabel = true
      opacity = Math.max(opacity, 0.95)
      width = d.baseWidth + 0.4
      z = 5
    }
    return {
      ...e,
      zIndex: z,
      animated: lit && d.tier === "lateral",
      style: { stroke: d.color, strokeWidth: width, opacity, strokeDasharray: d.dash },
      label: showLabel ? d.label : undefined,
      labelStyle: showLabel ? { fill: labelFill, fontSize: 9.5, fontWeight: 600 } : undefined,
      labelBgStyle: showLabel ? { fill: labelBg, fillOpacity: 0.92 } : undefined,
      labelBgPadding: showLabel ? ([4, 2] as [number, number]) : undefined,
      labelBgBorderRadius: showLabel ? 3 : undefined,
    }
  })

  const nodes =
    spotlight && hoveredId
      ? base.nodes.map((n) => {
          const dimNow = !litNodes.has(n.id)
          if ((n.data as FlowNodeData).dim === dimNow) return n
          return { ...n, data: { ...n.data, dim: dimNow } }
        })
      : base.nodes

  return { nodes, edges }
}

