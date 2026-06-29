// graphTheme.ts — single source of truth for the canvas visual language.
//
// Ported from dc-v2's graph-theme, adapted to THIS app's data shape:
//   • nodes are colored by their `label` (kind): Business / Domain /
//     IntelligenceProduct / Platform / Metric / Dashboard / UIComponent.
//   • edges are colored by their relation `type`: BELONGS_TO_DOMAIN,
//     PART_OF_PRODUCT, VISUALIZES, SHOWN_ON, DECOMPOSES_INTO, INFLUENCES,
//     HAS_DOMAIN, HAS_PRODUCT, ...  Metric→metric edges (DECOMPOSES_INTO,
//     INFLUENCES) are further styled by their `relation` subtype via
//     edgeStyle() below.
//   • provenance (deterministic / agent / human) stays distinguishable via a
//     subtle accent ring on the card.

import type { GraphEdge, Provenance } from "@/lib/api"

export type LabelStyle = { color: string; glyph: string; label: string }

// Per-LABEL (node kind) palette. The label IS the node type in our data.
export const LABEL_STYLE: Record<string, LabelStyle> = {
  Business: { color: "#e8b04b", glyph: "★", label: "Business" },
  Domain: { color: "#54d6c4", glyph: "◇", label: "Domain" },
  IntelligenceProduct: { color: "#c98bff", glyph: "▤", label: "Product" },
  Platform: { color: "#6ea8ff", glyph: "▦", label: "Platform" },
  Metric: { color: "#7ee081", glyph: "◔", label: "Metric" },
  Dashboard: { color: "#f0a868", glyph: "▥", label: "Dashboard" },
  UIComponent: { color: "#9aa7ff", glyph: "⬚", label: "Component" },
  Policy: { color: "#ef6f6f", glyph: "§", label: "Policy" },
  Threshold: { color: "#d9a83b", glyph: "⌁", label: "Threshold" },
  // Client-only VIEW node: the specific chart revealed by shift-clicking a Metric
  // (a chart INSTANCE, distinct from the generalised UIComponent chart-TYPE).
  Chart: { color: "#5fb0d9", glyph: "▦", label: "Chart" },
}

export const DEFAULT_LABEL_STYLE: LabelStyle = {
  color: "#8d99ad",
  glyph: "•",
  label: "Node",
}

// Metric CATEGORY palette (the 14-value MetricCategory vocab is 100% populated).
// Named colors for the common ones; everything else gets a STABLE hashed hue so
// the legend reads cleanly instead of a wall of grey. Ported from dc-v2's
// CATEGORY_STYLE / hashedCategoryStyle.
export const CATEGORY_STYLE: Record<string, LabelStyle> = {
  marketing: { color: "#e8b04b", glyph: "◈", label: "Marketing" },
  customer: { color: "#54d6c4", glyph: "❂", label: "Customer" },
  operational: { color: "#6ea8ff", glyph: "⚙", label: "Operational" },
  financial: { color: "#7ee081", glyph: "$", label: "Financial" },
  product: { color: "#c98bff", glyph: "▤", label: "Product" },
  revenue: { color: "#5ad19a", glyph: "$", label: "Revenue" },
  advertising: { color: "#f0a868", glyph: "◭", label: "Advertising" },
  traffic: { color: "#9ad0ff", glyph: "↗", label: "Traffic" },
  email: { color: "#d98be0", glyph: "✉", label: "Email" },
}

const DEFAULT_CATEGORY: LabelStyle = { color: "#8d99ad", glyph: "•", label: "Other" }

function hashedCategoryStyle(category: string): LabelStyle {
  let h = 0
  for (let i = 0; i < category.length; i++) h = (h * 31 + category.charCodeAt(i)) % 360
  const label = category.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  return { color: `hsl(${h} 52% 62%)`, glyph: "•", label }
}

export function categoryStyle(category?: string | null): LabelStyle {
  const key = (category ?? "").toLowerCase()
  if (CATEGORY_STYLE[key]) return CATEGORY_STYLE[key]
  return key ? hashedCategoryStyle(key) : DEFAULT_CATEGORY
}

// Color a node: Metric nodes by their `category`; everything else by label.
export function labelStyle(label?: string | null, category?: string | null): LabelStyle {
  if (label === "Metric") {
    return category ? categoryStyle(category) : (LABEL_STYLE.Metric ?? DEFAULT_LABEL_STYLE)
  }
  if (!label) return DEFAULT_LABEL_STYLE
  return LABEL_STYLE[label] ?? DEFAULT_LABEL_STYLE
}

// The metric→metric causal relations (uppercase, matching the API edge `type`).
// The directional focus ring traverses ONLY these. The backend models metric
// edges as exactly DECOMPOSES_INTO and INFLUENCES.
export const CAUSAL_RELS = new Set(["DECOMPOSES_INTO", "INFLUENCES"])

// Provenance accent — kept distinguishable since the legend already exists.
export const PROVENANCE_COLORS: Record<Provenance, string> = {
  deterministic: "#3b82f6",
  agent: "#a855f7",
  human: "#22c55e",
  synthetic: "#8d99ad",
}

export function provenanceColor(p?: string | null): string {
  return PROVENANCE_COLORS[(p as Provenance) ?? "deterministic"] ?? PROVENANCE_COLORS.deterministic
}

// ---------------------------------------------------------------------------
// Metric→metric edge styling, keyed by (rel_type, relation).
//
// The backend models metric edges as exactly two rel_types, each with a fixed
// relation vocabulary:
//   DECOMPOSES_INTO — relation ∈ formula | component | identity | rollup |
//                     crossproduct | funnel
//   INFLUENCES      — relation ∈ curated_rule | llm_verified | statistical |
//                     statistical_candidate | promoted
//
// DECOMPOSES_INTO is the structural backbone (one hue); formula/component are
// the "strong" identities (solid, full opacity), while identity/rollup/
// crossproduct/funnel are dashed variants. INFLUENCES is a distinct hue with
// dash/opacity variants per relation strength (curated_rule strongest →
// statistical_candidate weakest). Deprecated edges override everything: faded +
// dashed.
// ---------------------------------------------------------------------------

export const DECOMPOSES_COLOR = "#6f86ad"
export const INFLUENCES_COLOR = "#e8794b"
export const DEPRECATED_COLOR = "#5a6072"
/** Cross-domain edges (`cross_domain: true`) get a distinct violet hue + dashing. */
export const CROSS_DOMAIN_COLOR = "#b07be8"
/** A structurally-inverse decomposition hop (denominator / subtrahend role). */
export const INVERSE_COLOR = "#e8556f"

/** Resolved visual properties for a single rendered edge. */
export interface EdgeStyle {
  stroke: string
  strokeDasharray?: string
  opacity: number
  animated?: boolean
  /**
   * Optional stroke-width multiplier (≈0.9–1.7), confidence-driven for metric
   * edges so higher-confidence causal/decomposition links render thicker. The
   * canvas multiplies the edge's base width by this; undefined ⇒ no change.
   */
  widthScale?: number
}

// ---------------------------------------------------------------------------
// Structural sign + cross-domain edge reads.
//
// A DECOMPOSES_INTO hop carries a structural `role`: denominator / subtrahend
// enter their parent's formula INVERSELY (a "-1" hop), so they're drawn in a
// distinct red "inverse" style. Every other role is additive. Cross-domain edges
// (cross_domain:true) are drawn dashed in a distinct hue. Mirrors the backend's
// _hop_sign() (harness/api/server.py).
// ---------------------------------------------------------------------------

/** Structural roles whose component enters its parent inversely (sign −1). */
export const INVERSE_ROLES = new Set(["denominator", "subtrahend"])

function edgeField(edge: GraphEdge, key: string): unknown {
  return (
    (edge as unknown as Record<string, unknown>)[key] ??
    (edge.props as Record<string, unknown> | undefined)?.[key]
  )
}

/** Read an edge's structural `role` (lower-cased), if any. */
export function edgeRole(edge: GraphEdge): string | undefined {
  const r = edgeField(edge, "role")
  return typeof r === "string" && r ? r.toLowerCase() : undefined
}

/** True when the edge is a structurally-inverse decomposition hop. */
export function edgeIsInverse(edge: GraphEdge): boolean {
  const role = edgeRole(edge)
  return role ? INVERSE_ROLES.has(role) : false
}

/** True when the edge is flagged `cross_domain` (spans two domains). */
export function edgeIsCrossDomain(edge: GraphEdge): boolean {
  const v = edgeField(edge, "cross_domain")
  return v === true || v === "true" || v === 1
}

/** Read an edge's `confidence` (0..1) if numeric, else null. */
export function edgeConfidence(edge: GraphEdge): number | null {
  const v = edgeField(edge, "confidence")
  return typeof v === "number" && Number.isFinite(v) ? v : null
}

/**
 * Map a confidence (0..1) to a gentle stroke-width multiplier so thickness
 * encodes evidence strength without fighting the relation-opacity ladder:
 * c=0 → 0.9×, c=1 → 1.7×.
 */
export function confidenceWidthScale(confidence: number): number {
  const c = confidence < 0 ? 0 : confidence > 1 ? 1 : confidence
  return 0.9 + 0.8 * c
}

// Per-relation styling for DECOMPOSES_INTO. formula/component = strong (solid),
// the rest = dashed variants.
const DECOMPOSES_RELATION_STYLE: Record<string, EdgeStyle> = {
  formula: { stroke: DECOMPOSES_COLOR, opacity: 1 },
  component: { stroke: DECOMPOSES_COLOR, opacity: 1 },
  identity: { stroke: DECOMPOSES_COLOR, strokeDasharray: "2 3", opacity: 0.9 },
  rollup: { stroke: DECOMPOSES_COLOR, strokeDasharray: "6 3", opacity: 0.85 },
  crossproduct: { stroke: DECOMPOSES_COLOR, strokeDasharray: "8 4", opacity: 0.8 },
  funnel: { stroke: DECOMPOSES_COLOR, strokeDasharray: "1 4", opacity: 0.8 },
}

const DECOMPOSES_DEFAULT: EdgeStyle = { stroke: DECOMPOSES_COLOR, opacity: 0.9 }

// Per-relation styling for INFLUENCES. Distinct hue; dash/opacity encode the
// strength of the evidence backing the relation.
const INFLUENCES_RELATION_STYLE: Record<string, EdgeStyle> = {
  curated_rule: { stroke: INFLUENCES_COLOR, opacity: 1 },
  promoted: { stroke: INFLUENCES_COLOR, opacity: 0.95 },
  llm_verified: { stroke: INFLUENCES_COLOR, strokeDasharray: "6 4", opacity: 0.85 },
  statistical: { stroke: INFLUENCES_COLOR, strokeDasharray: "3 3", opacity: 0.7 },
  statistical_candidate: {
    stroke: INFLUENCES_COLOR,
    strokeDasharray: "2 5",
    opacity: 0.5,
    animated: true,
  },
}

const INFLUENCES_DEFAULT: EdgeStyle = {
  stroke: INFLUENCES_COLOR,
  strokeDasharray: "4 4",
  opacity: 0.7,
}

// Deprecated edges are faded + heavily dashed regardless of rel_type/relation.
export const DEPRECATED_EDGE_STYLE: EdgeStyle = {
  stroke: DEPRECATED_COLOR,
  strokeDasharray: "2 6",
  opacity: 0.3,
}

function isDeprecated(edge: GraphEdge): boolean {
  return (
    edge.status === "deprecated" ||
    Boolean(edge.deprecated_at) ||
    Boolean((edge.props as Record<string, unknown> | undefined)?.deprecated_at) ||
    (edge.props as Record<string, unknown> | undefined)?.status === "deprecated"
  )
}

/** Read `relation` off the edge, falling back to props for older payloads. */
function edgeRelation(edge: GraphEdge): string | undefined {
  const rel =
    edge.relation ??
    ((edge.props as Record<string, unknown> | undefined)?.relation as
      | string
      | undefined)
  return rel ? rel.toLowerCase() : undefined
}

/**
 * Resolve the rendered style for an edge from its (rel_type, relation). Returns
 * deprecated styling when the edge is deprecated, then metric-edge styling for
 * DECOMPOSES_INTO / INFLUENCES, then a neutral fallback derived from edgeVisual.
 */
export function edgeStyle(edge: GraphEdge): EdgeStyle {
  if (isDeprecated(edge)) return DEPRECATED_EDGE_STYLE

  const relation = edgeRelation(edge)
  let style: EdgeStyle
  if (edge.type === "DECOMPOSES_INTO") {
    style = (relation ? DECOMPOSES_RELATION_STYLE[relation] : undefined) ?? DECOMPOSES_DEFAULT
  } else if (edge.type === "INFLUENCES") {
    style = (relation ? INFLUENCES_RELATION_STYLE[relation] : undefined) ?? INFLUENCES_DEFAULT
  } else {
    // Non-metric edges: solid, colored by the existing per-type palette.
    style = { stroke: edgeVisual(edge.type).color, opacity: 0.9 }
  }

  // Confidence → stroke-width multiplier (thickness encodes evidence strength).
  // Only edges carrying a numeric confidence (metric→metric edges) get scaled;
  // structural spine edges have none, so they render at their base width.
  const conf = edgeConfidence(edge)
  if (conf != null) style = { ...style, widthScale: confidenceWidthScale(conf) }

  // Structural sign: a denominator/subtrahend hop enters its parent inversely —
  // recolor it red so "this pushes the parent DOWN" reads at a glance.
  if (edgeIsInverse(edge)) {
    style = { ...style, stroke: INVERSE_COLOR }
  }

  // Cross-domain edges get a distinct violet hue + dashing on top (a metric
  // influencing/decomposing across a domain boundary).
  if (edgeIsCrossDomain(edge)) {
    style = {
      ...style,
      stroke: CROSS_DOMAIN_COLOR,
      strokeDasharray: style.strokeDasharray ?? "7 4",
    }
  }

  return style
}

// Legend descriptor for the edge styling — drives the UI legend so it stays in
// lockstep with edgeStyle().
export interface EdgeLegendItem {
  key: string
  relType: "DECOMPOSES_INTO" | "INFLUENCES" | "deprecated"
  relation?: string
  label: string
  style: EdgeStyle
}

export const EDGE_LEGEND: EdgeLegendItem[] = [
  {
    key: "DECOMPOSES_INTO:formula",
    relType: "DECOMPOSES_INTO",
    relation: "formula",
    label: "Decomposes · formula",
    style: DECOMPOSES_RELATION_STYLE.formula,
  },
  {
    key: "DECOMPOSES_INTO:component",
    relType: "DECOMPOSES_INTO",
    relation: "component",
    label: "Decomposes · component",
    style: DECOMPOSES_RELATION_STYLE.component,
  },
  {
    key: "DECOMPOSES_INTO:identity",
    relType: "DECOMPOSES_INTO",
    relation: "identity",
    label: "Decomposes · identity",
    style: DECOMPOSES_RELATION_STYLE.identity,
  },
  {
    key: "DECOMPOSES_INTO:rollup",
    relType: "DECOMPOSES_INTO",
    relation: "rollup",
    label: "Decomposes · rollup",
    style: DECOMPOSES_RELATION_STYLE.rollup,
  },
  {
    key: "DECOMPOSES_INTO:crossproduct",
    relType: "DECOMPOSES_INTO",
    relation: "crossproduct",
    label: "Decomposes · cross-product",
    style: DECOMPOSES_RELATION_STYLE.crossproduct,
  },
  {
    key: "DECOMPOSES_INTO:funnel",
    relType: "DECOMPOSES_INTO",
    relation: "funnel",
    label: "Decomposes · funnel",
    style: DECOMPOSES_RELATION_STYLE.funnel,
  },
  {
    key: "INFLUENCES:curated_rule",
    relType: "INFLUENCES",
    relation: "curated_rule",
    label: "Influences · curated rule",
    style: INFLUENCES_RELATION_STYLE.curated_rule,
  },
  {
    key: "INFLUENCES:llm_verified",
    relType: "INFLUENCES",
    relation: "llm_verified",
    label: "Influences · LLM verified",
    style: INFLUENCES_RELATION_STYLE.llm_verified,
  },
  {
    key: "INFLUENCES:statistical",
    relType: "INFLUENCES",
    relation: "statistical",
    label: "Influences · statistical",
    style: INFLUENCES_RELATION_STYLE.statistical,
  },
  {
    key: "INFLUENCES:statistical_candidate",
    relType: "INFLUENCES",
    relation: "statistical_candidate",
    label: "Influences · statistical candidate",
    style: INFLUENCES_RELATION_STYLE.statistical_candidate,
  },
  {
    key: "INFLUENCES:promoted",
    relType: "INFLUENCES",
    relation: "promoted",
    label: "Influences · promoted",
    style: INFLUENCES_RELATION_STYLE.promoted,
  },
  {
    key: "inverse",
    relType: "DECOMPOSES_INTO",
    label: "Inverse (denominator / subtrahend)",
    style: { stroke: INVERSE_COLOR, opacity: 1 },
  },
  {
    key: "cross_domain",
    relType: "INFLUENCES",
    label: "Cross-domain",
    style: { stroke: CROSS_DOMAIN_COLOR, strokeDasharray: "7 4", opacity: 0.95 },
  },
  {
    key: "deprecated",
    relType: "deprecated",
    label: "Deprecated",
    style: DEPRECATED_EDGE_STYLE,
  },
]

// ---------------------------------------------------------------------------
// Node decorations: causal_role badge + leaf / loop ring.
//
// causal_role is rendered as a small badge on the card. Leaf nodes (no edges in
// the current view) get a dashed amber ring; nodes on a feedback loop get a
// solid rose ring (loop membership is computed by the canvas from the live edge
// set and passed in).
// ---------------------------------------------------------------------------

/** A short, glyph-prefixed badge for a metric's `causal_role`, or null. */
export function causalRoleBadge(
  role?: unknown,
): { label: string; color: string } | null {
  if (typeof role !== "string" || !role) return null
  const key = role.toLowerCase()
  const MAP: Record<string, { label: string; color: string }> = {
    driver: { label: "▲ driver", color: "#e8794b" },
    outcome: { label: "◎ outcome", color: "#54d6c4" },
    lever: { label: "⇅ lever", color: "#e8b04b" },
    mediator: { label: "↔ mediator", color: "#c98bff" },
    moderator: { label: "⋈ moderator", color: "#9aa7ff" },
    confounder: { label: "⚠ confounder", color: "#e8556f" },
  }
  return (
    MAP[key] ?? {
      label: role.replace(/[-_]/g, " "),
      color: "#8d99ad",
    }
  )
}

/** Ring colors for the leaf (no edges) / loop (on a feedback cycle) decorations. */
export const LEAF_RING_COLOR = "#e8b04b"
export const LOOP_RING_COLOR = "#e8556f"

// Edge relation styling. `tier` drives weight/opacity/paint-order:
//   spine      = the structural backbone (solid, arrowed)
//   structural = everything that hangs products/dashboards/components together
//   lateral    = lightweight / cross-cutting links (faint, dashed)
export type EdgeTier = "spine" | "structural" | "lateral"
export type EdgeVisual = { color: string; tier: EdgeTier; label: string }

const EDGE_MAP: Record<string, EdgeVisual> = {
  HAS_DOMAIN: { color: "#54d6c4", tier: "spine", label: "has domain" },
  HAS_PRODUCT: { color: "#c98bff", tier: "spine", label: "has product" },
  BELONGS_TO_DOMAIN: { color: "#54d6c4", tier: "spine", label: "belongs to domain" },
  PART_OF_PRODUCT: { color: "#c98bff", tier: "spine", label: "part of product" },
  DECOMPOSES_INTO: { color: DECOMPOSES_COLOR, tier: "spine", label: "decomposes into" },
  VISUALIZES: { color: "#f0a868", tier: "structural", label: "visualizes" },
  SHOWN_ON: { color: "#f0a868", tier: "structural", label: "shown on" },
  RENDERED_BY: { color: "#9aa7ff", tier: "structural", label: "rendered by" },
  SERVES: { color: "#9ad0ff", tier: "structural", label: "serves" },
  HAS_METRIC: { color: "#7ee081", tier: "structural", label: "has metric" },
  USES_PLATFORM: { color: "#6ea8ff", tier: "structural", label: "uses platform" },
  INFLUENCES: { color: INFLUENCES_COLOR, tier: "lateral", label: "influences" },
  // Governance edges (Policy/Threshold authoring).
  GOVERNS: { color: "#ef6f6f", tier: "structural", label: "governed by" },
  HAS_THRESHOLD: { color: "#d9a83b", tier: "structural", label: "measured against" },
  ENFORCES_THRESHOLD: { color: "#e0863b", tier: "structural", label: "enforces" },
  GOVERNED_BY: { color: "#ef6f6f", tier: "lateral", label: "governed by" },
}

export function edgeVisual(type: string): EdgeVisual {
  return (
    EDGE_MAP[type] ?? {
      color: "#46566f",
      tier: "structural",
      label: type.replace(/_/g, " ").toLowerCase(),
    }
  )
}

// Relations that should NEVER shape the overview tree layout (lightweight overlays).
export const LATERAL_RELS = new Set(
  Object.entries(EDGE_MAP)
    .filter(([, v]) => v.tier === "lateral")
    .map(([rel]) => rel),
)
