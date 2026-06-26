// egoLayout.ts — focus / ego-ring math ported from dc-v2's ego-layout, adapted to
// THIS app's edge shape ({ source, target, type } — no `rel`/`confidence`).
//
//   • radialPositions  → the Shift+Click "neighborhood" view: root at the centre,
//                        its directly-connected nodes equally spaced on a ring.
//   • rankedNeighbors  → a node's 1-hop neighbors, deterministically ordered so the
//                        ring is stable.
//
// Pure + deterministic: read-only, sorted ordering, no Date/Math.random.

import type { GraphEdge, GraphNode } from "@/lib/api"
import { edgeVisual } from "@/lib/graphTheme"

export type XY = { x: number; y: number }

/* Undirected adjacency (id → neighbor ids), self-loops ignored. */
export function adjacency(edges: GraphEdge[]): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>()
  const link = (a: string, b: string) => {
    let s = adj.get(a)
    if (!s) adj.set(a, (s = new Set()))
    s.add(b)
  }
  for (const e of edges) {
    if (!e.source || !e.target || e.source === e.target) continue
    link(e.source, e.target)
    link(e.target, e.source)
  }
  return adj
}

/* Degree-of-Interest weight per relation tier — spine ties beat structural beats
   lateral, so the most meaningful neighbors are ordered first. */
function relWeight(type: string): number {
  const tier = edgeVisual(type).tier
  if (tier === "spine") return 3
  if (tier === "structural") return 2
  return 1
}

export type RankedRing = { kept: string[]; total: number; overflow: number }

/**
 * Rank a node's 1-hop neighbors by a lightweight Degree-of-Interest (strongest
 * incident edge tier), then optionally cap to `cap`. Deterministic: DOI desc, id
 * asc tie-break. When `edgeType` is set, only neighbors linked to root via that
 * relation are kept ("grow along one connection").
 */
export function rankedNeighbors(
  nodes: GraphNode[],
  edges: GraphEdge[],
  rootId: string,
  cap = Infinity,
  edgeType?: string | null,
): RankedRing {
  let ring = [...(adjacency(edges).get(rootId) ?? [])]
  if (edgeType) {
    const viaKind = new Set<string>()
    for (const e of edges) {
      if (e.type !== edgeType) continue
      if (e.source === rootId) viaKind.add(e.target)
      else if (e.target === rootId) viaKind.add(e.source)
    }
    ring = ring.filter((id) => viaKind.has(id))
  }
  const score = (id: string): number => {
    let best = 0
    for (const e of edges) {
      const inc =
        (e.source === rootId && e.target === id) ||
        (e.source === id && e.target === rootId)
      if (inc) best = Math.max(best, relWeight(e.type))
    }
    return best
  }
  void nodes // kept for signature parity / future per-node bonuses
  const ranked = ring.sort((a, b) => score(b) - score(a) || a.localeCompare(b))
  const kept = cap === Infinity ? ranked : ranked.slice(0, cap)
  return { kept, total: ranked.length, overflow: ranked.length - kept.length }
}

/**
 * Node ids that participate in a directed cycle (feedback loop) over the given
 * edges, restricted to relation `types` (e.g. the metric→metric causal rels).
 *
 * Uses iterative Tarjan SCC: any strongly-connected component with >1 node is a
 * loop, as is a node with a self-loop. Pure + deterministic (sorted adjacency,
 * no recursion so it's safe on large graphs). Returns the union of all such ids.
 */
export function findLoopNodeIds(
  edges: { source: string; target: string; type: string }[],
  types?: Set<string>,
): Set<string> {
  // Build a directed adjacency over the in-scope edges; track self-loops.
  const adj = new Map<string, string[]>()
  const nodes = new Set<string>()
  const selfLoops = new Set<string>()
  for (const e of edges) {
    if (types && !types.has(e.type)) continue
    if (!e.source || !e.target) continue
    nodes.add(e.source)
    nodes.add(e.target)
    if (e.source === e.target) {
      selfLoops.add(e.source)
      continue
    }
    const arr = adj.get(e.source)
    if (arr) arr.push(e.target)
    else adj.set(e.source, [e.target])
  }
  for (const arr of adj.values()) arr.sort((a, b) => a.localeCompare(b))

  const order = [...nodes].sort((a, b) => a.localeCompare(b))
  const index = new Map<string, number>()
  const low = new Map<string, number>()
  const onStack = new Set<string>()
  const stack: string[] = []
  const loops = new Set<string>(selfLoops)
  let counter = 0

  // Iterative Tarjan: each frame tracks its node + the next neighbor to visit.
  for (const start of order) {
    if (index.has(start)) continue
    const work: { node: string; i: number }[] = [{ node: start, i: 0 }]
    index.set(start, counter)
    low.set(start, counter)
    counter++
    stack.push(start)
    onStack.add(start)

    while (work.length) {
      const frame = work[work.length - 1]
      const neighbors = adj.get(frame.node) ?? []
      if (frame.i < neighbors.length) {
        const next = neighbors[frame.i]
        frame.i++
        if (!index.has(next)) {
          index.set(next, counter)
          low.set(next, counter)
          counter++
          stack.push(next)
          onStack.add(next)
          work.push({ node: next, i: 0 })
        } else if (onStack.has(next)) {
          low.set(frame.node, Math.min(low.get(frame.node)!, index.get(next)!))
        }
      } else {
        // Done with this node: if it's an SCC root, pop the component.
        if (low.get(frame.node) === index.get(frame.node)) {
          const comp: string[] = []
          for (;;) {
            const w = stack.pop()!
            onStack.delete(w)
            comp.push(w)
            if (w === frame.node) break
          }
          if (comp.length > 1) for (const id of comp) loops.add(id)
        }
        work.pop()
        const parent = work[work.length - 1]
        if (parent) {
          low.set(parent.node, Math.min(low.get(parent.node)!, low.get(frame.node)!))
        }
      }
    }
  }
  return loops
}

/** Relations touching `rootId` with the count of distinct neighbours via each —
 *  powers per-relation chips. Sorted by count desc, then type asc. */
export function neighborKinds(
  edges: GraphEdge[],
  rootId: string,
): { type: string; count: number }[] {
  const byType = new Map<string, Set<string>>()
  for (const e of edges) {
    const other = e.source === rootId ? e.target : e.target === rootId ? e.source : null
    if (!other || other === rootId) continue
    const s = byType.get(e.type) ?? new Set<string>()
    s.add(other)
    byType.set(e.type, s)
  }
  return [...byType.entries()]
    .map(([type, s]) => ({ type, count: s.size }))
    .sort((a, b) => b.count - a.count || a.type.localeCompare(b.type))
}

/** Directed causal neighborhood of `rootId` over `rels`, split into parents
 *  (incoming `X -> root`) and children (outgoing `root -> X`). `metricsOnly`
 *  keeps only metric↔metric links so spine nodes never clutter the ring. Each
 *  side is DOI-ranked and capped to `cap`; `total` is the pre-cap count (for the
 *  "M of N" HUD). `growKind` narrows to a single relation type. */
export type CausalRing = {
  parents: string[]
  children: string[]
  total: number
  shown: number
}

export function causalRing(
  nodes: GraphNode[],
  edges: GraphEdge[],
  rootId: string,
  opts: {
    rels: Set<string>
    metricsOnly?: boolean
    cap?: number
    growKind?: string | null
  },
): CausalRing {
  const labelOf = new Map(nodes.map((n) => [n.id, n.label]))
  const isMetric = (id: string) => labelOf.get(id) === "Metric"
  const cap = opts.cap ?? Infinity
  const grow = opts.growKind ?? null

  const parentsSet = new Set<string>()
  const childrenSet = new Set<string>()
  for (const e of edges) {
    if (!opts.rels.has(e.type) || e.source === e.target) continue
    if (grow && e.type !== grow) continue
    if (opts.metricsOnly && (!isMetric(e.source) || !isMetric(e.target))) continue
    if (e.target === rootId) parentsSet.add(e.source) // X -> root : X is a parent (incoming)
    else if (e.source === rootId) childrenSet.add(e.target) // root -> X : child (outgoing)
  }

  // DOI score = strongest incident causal edge weight (deterministic id tie-break).
  const score = (id: string): number => {
    let best = 0
    for (const e of edges) {
      if (!opts.rels.has(e.type)) continue
      const inc =
        (e.source === rootId && e.target === id) || (e.source === id && e.target === rootId)
      if (inc) best = Math.max(best, relWeight(e.type))
    }
    return best
  }
  const rank = (set: Set<string>) =>
    [...set].sort((a, b) => score(b) - score(a) || a.localeCompare(b))

  const allParents = rank(parentsSet)
  const allChildren = rank(childrenSet)
  const total = allParents.length + allChildren.length
  const parents = cap === Infinity ? allParents : allParents.slice(0, cap)
  const children = cap === Infinity ? allChildren : allChildren.slice(0, cap)
  return { parents, children, total, shown: parents.length + children.length }
}

/** Radial directional layout: focus at `center`, parents fanned across the UPPER
 *  arc and children across the LOWER arc (screen y grows downward). Organic, not
 *  a tree — reads "drivers above / effects below" while staying a ring. */
export function directionalRadial(
  rootId: string,
  parents: string[],
  children: string[],
  center: XY,
  opts: { cardW?: number; gap?: number; minR?: number } = {},
): Record<string, XY> {
  const cardW = opts.cardW ?? 230
  const gap = opts.gap ?? 70
  const minR = opts.minR ?? 340
  const pos: Record<string, XY> = { [rootId]: { x: center.x, y: center.y } }
  const maxSide = Math.max(parents.length, children.length, 1)
  const R = Math.max(minR, (maxSide * (cardW + gap)) / Math.PI)
  const pad = 0.32 // keep cards off the exact horizontal so up/down read clearly

  const place = (ids: string[], arcStart: number, arcEnd: number) => {
    const n = ids.length
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1)
      const theta = arcStart + (arcEnd - arcStart) * t
      pos[ids[i]] = { x: center.x + R * Math.cos(theta), y: center.y + R * Math.sin(theta) }
    }
  }
  // Upper semicircle (y<0) = parents; lower (y>0) = children.
  place(parents, -Math.PI + pad, -pad)
  place(children, pad, Math.PI - pad)
  return pos
}

/**
 * Root at `center`, `kept` neighbors equally spaced on a ring whose radius is wide
 * enough to seat them without overlap. First neighbor at 12 o'clock, then clockwise.
 * When `kindOf` is given, neighbors are ordered by kind so same-color cards sit
 * adjacent and the ring reads as wedges (keeps within-kind DOI order).
 */
export function radialPositions(
  rootId: string,
  kept: string[],
  center: XY,
  opts: { cardW?: number; gap?: number; minR?: number; kindOf?: Map<string, string | undefined> } = {},
): Record<string, XY> {
  const cardW = opts.cardW ?? 200
  const gap = opts.gap ?? 80
  const minR = opts.minR ?? 320
  const pos: Record<string, XY> = { [rootId]: { x: center.x, y: center.y } }
  const n = kept.length
  if (n === 0) return pos

  let order = kept
  if (opts.kindOf) {
    const rank = new Map(kept.map((id, i) => [id, i]))
    order = [...kept].sort((a, b) => {
      const ca = opts.kindOf!.get(a) ?? ""
      const cb = opts.kindOf!.get(b) ?? ""
      if (ca !== cb) return ca < cb ? -1 : 1
      return (rank.get(a) ?? 0) - (rank.get(b) ?? 0)
    })
  }

  const R = Math.max(minR, (n * (cardW + gap)) / (2 * Math.PI))
  const start = -Math.PI / 2
  for (let i = 0; i < n; i++) {
    const theta = start + (2 * Math.PI * i) / n
    pos[order[i]] = { x: center.x + R * Math.cos(theta), y: center.y + R * Math.sin(theta) }
  }
  return pos
}
