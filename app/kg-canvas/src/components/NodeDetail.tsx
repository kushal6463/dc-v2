// Node inspector for the currently selected node — dc-v2 node-inspector style:
// header (kind dot + title + id), grouped key/value properties, and connections
// grouped by relation. Reads from the selected node's `props` + store edges.

import { useEffect, useMemo, useState } from "react"

import { useStore } from "@/store"
import { edgeVisual, labelStyle, provenanceColor } from "@/lib/graphTheme"
import {
  traverseDownstream,
  traverseUpstream,
  type GraphNode,
  type MetricProps,
  type TraversePath,
  type TraversePayload,
} from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { ChartDetail } from "@/components/ChartDetail"

// Internal / header-duplicated keys we never surface as properties.
const HIDDEN_FIELDS = new Set([
  "id",
  "title",
  "label",
  "name",
  "node_key",
  "tenant_id",
  "label_name",
  "created_at",
  "updated_at",
  "content_hash",
])

// Long-text fields get a full-width cell so they don't get cramped in the grid.
const WIDE_FIELDS = new Set(["description", "summary", "formula_expression", "condition", "definition"])

// Mart-lineage / SQL-provenance / data-quality keys surfaced by the dedicated
// "Data lineage" panel (DataLineagePanel). Excluded from the generic Properties
// grid for metric nodes so they aren't ALSO dumped raw (SQL especially).
const LINEAGE_FIELDS = new Set([
  "mart_sources",
  "source_columns",
  "sql_query_real",
  "sql_query_canonical",
  "history_start",
  "history_end",
  "n_periods",
  "data_stale",
  "formula_sql_mismatch",
  "formula_sql_note",
])

// Threshold band/industry/current fields rendered by ThresholdBandsPanel as a
// direction-aware comparison ladder — excluded from the generic Properties grid
// for Threshold nodes so they aren't ALSO dumped raw.
const THRESHOLD_BAND_FIELDS = new Set([
  "threshold_type",
  "direction",
  "unit",
  "p95_val",
  "p85_val",
  "p75_val",
  "p50_val",
  "percentile_basis",
  "industry_standard_val",
  "industry_min_val",
  "industry_max_val",
  "industry_source",
  "industry_as_of",
  "current_val",
  "current_as_of",
  "target_value_num",
])

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—"
  if (typeof value === "boolean") return value ? "yes" : "no"
  if (Array.isArray(value)) return value.map((x) => String(x)).join(", ")
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

type Conn = { dir: "out" | "in"; otherId: string; otherTitle: string; type: string }

// Curated metric summary fields, in display order. Keys are read off node.props
// (defensively — older payloads may omit some). The Properties grid below still
// surfaces everything else generically.
const METRIC_SUMMARY_FIELDS: { key: string; label: string; wide?: boolean }[] = [
  { key: "metric_uid", label: "metric_uid" },
  { key: "display_name", label: "display_name" },
  { key: "scope_key", label: "scope_key" },
  { key: "metric_base", label: "metric_base" },
  { key: "is_derived", label: "is_derived" },
  { key: "formula_text", label: "formula_text", wide: true },
  { key: "aliases", label: "aliases", wide: true },
  { key: "synonyms", label: "synonyms", wide: true },
  { key: "card_endpoint", label: "card_endpoint", wide: true },
  { key: "endpoint_paths", label: "endpoint_paths", wide: true },
  { key: "domain_ids", label: "domain", wide: true },
  { key: "product_ids", label: "product", wide: true },
  { key: "platform", label: "platform" },
  { key: "platform_ids", label: "platform" },
  { key: "source_refs", label: "source_refs", wide: true },
]

// Lineage fetched on demand for the selected metric — the full signed-path
// payloads ({paths, cyclic_paths, summary}) for both directions.
interface Lineage {
  upstream: TraversePayload
  downstream: TraversePayload
}

const EMPTY_PAYLOAD: TraversePayload = {
  paths: [],
  cyclic_paths: [],
  summary: { acyclic_count: 0, cyclic_count: 0 },
}

export function NodeDetail() {
  const selectedNodeId = useStore((s) => s.selectedNodeId)
  const node = useStore((s) => s.nodes.find((n) => n.id === s.selectedNodeId))
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  const selectNode = useStore((s) => s.selectNode)
  const locate = useStore((s) => s.locate)
  const setFocus = useStore((s) => s.setFocus)
  const setFocusMode = useStore((s) => s.setFocusMode)
  const setFocusDir = useStore((s) => s.setFocusDir)
  // Drives which traversal view leads (upstream / downstream); also gates the
  // canvas path highlight. The panel always shows all views, emphasizing this one.
  const traversalMode = useStore((s) => s.traversalMode)
  // Read-time confidence floor for traverse calls. Lives in the store (persisted)
  // so the canvas highlight + this inspector lineage share one value; surfaced as
  // a slider in TraversalPanel below.
  const traverseMinConfidence = useStore((s) => s.traverseMinConfidence)
  const setTraverseMinConfidence = useStore((s) => s.setTraverseMinConfidence)

  // Lineage: signed upstream/downstream path payloads for the selected metric.
  // Only fetched for Metric nodes; keyed by metric_uid so it refreshes on change.
  const isMetric = node?.label === "Metric"
  const metricUid = isMetric
    ? ((node?.props?.metric_uid as string | undefined) ?? node?.id)
    : undefined
  const [lineage, setLineage] = useState<Lineage | null>(null)
  const [lineageLoading, setLineageLoading] = useState(false)

  useEffect(() => {
    if (!metricUid) {
      setLineage(null)
      return
    }
    let cancelled = false
    setLineage(null)
    setLineageLoading(true)
    // Read-time confidence floor: 0 ⇒ omit the param (unchanged default). Debounced
    // so dragging the slider doesn't fire a request per tick; the cleanup cancels
    // any superseded fetch.
    const minConf = traverseMinConfidence > 0 ? traverseMinConfidence : undefined
    const handle = setTimeout(() => {
      Promise.all([
        traverseUpstream(metricUid, 3, minConf),
        traverseDownstream(metricUid, 3, minConf),
      ])
        .then(([up, down]) => {
          if (cancelled) return
          setLineage({ upstream: up, downstream: down })
        })
        .catch(() => {
          if (!cancelled)
            setLineage({ upstream: EMPTY_PAYLOAD, downstream: EMPTY_PAYLOAD })
        })
        .finally(() => {
          if (!cancelled) setLineageLoading(false)
        })
    }, 200)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [metricUid, traverseMinConfidence])

  // Connections grouped by relation type (computed before any early return).
  const groups = useMemo(() => {
    if (!selectedNodeId) return [] as { type: string; items: Conn[] }[]
    const byId = new Map(nodes.map((n) => [n.id, n.title || n.id]))
    const conns: Conn[] = []
    for (const e of edges) {
      if (e.source === selectedNodeId && e.target !== selectedNodeId)
        conns.push({ dir: "out", otherId: e.target, otherTitle: byId.get(e.target) ?? e.target, type: e.type })
      else if (e.target === selectedNodeId && e.source !== selectedNodeId)
        conns.push({ dir: "in", otherId: e.source, otherTitle: byId.get(e.source) ?? e.source, type: e.type })
    }
    const byType = new Map<string, Conn[]>()
    for (const c of conns) {
      const arr = byType.get(c.type)
      if (arr) arr.push(c)
      else byType.set(c.type, [c])
    }
    return [...byType.entries()]
      .map(([type, items]) => ({ type, items: items.sort((a, b) => a.otherTitle.localeCompare(b.otherTitle)) }))
      .sort((a, b) => b.items.length - a.items.length || a.type.localeCompare(b.type))
  }, [selectedNodeId, nodes, edges])

  // Curated metric summary (only the populated keys, in display order). Empty
  // for non-metric nodes.
  const summary = useMemo(() => {
    if (node?.label !== "Metric") return [] as { label: string; value: unknown; wide?: boolean }[]
    const props = (node?.props ?? {}) as Record<string, unknown>
    const out: { label: string; value: unknown; wide?: boolean }[] = []
    for (const f of METRIC_SUMMARY_FIELDS) {
      const v = props[f.key]
      if (v === null || v === undefined || v === "") continue
      if (Array.isArray(v) && v.length === 0) continue
      out.push({ label: f.label, value: v, wide: f.wide })
    }
    return out
  }, [node])

  // Keys already shown in the curated summary are dropped from the generic grid.
  const summaryKeys = useMemo(() => new Set(METRIC_SUMMARY_FIELDS.map((f) => f.key)), [])

  const fields = useMemo(() => {
    const props = (node?.props ?? {}) as Record<string, unknown>
    const isMetricNode = node?.label === "Metric"
    const isThresholdNode = node?.label === "Threshold"
    const out: [string, unknown][] = []
    for (const [k, v] of Object.entries(props)) {
      if (HIDDEN_FIELDS.has(k)) continue
      if (isMetricNode && summaryKeys.has(k)) continue
      if (isMetricNode && LINEAGE_FIELDS.has(k)) continue
      if (isThresholdNode && THRESHOLD_BAND_FIELDS.has(k)) continue
      if (v === null || v === undefined || v === "") continue
      out.push([k, v])
    }
    return out
  }, [node, summaryKeys])

  if (!selectedNodeId || !node) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        Select a node on the canvas to inspect its properties. Shift-click to focus its connections.
      </div>
    )
  }

  const st = labelStyle(node.label, node.props?.category as string | undefined)
  const prov = provenanceColor(node.provenance)
  const connTotal = groups.reduce((n, g) => n + g.items.length, 0)

  const navigate = (n: GraphNode | undefined, id: string) => {
    selectNode(id)
    if (n) setFocus(id)
  }

  // Quick causal-focus presets from the inspector header.
  const focusAs = (up: boolean, down: boolean) => {
    setFocusMode("causal")
    setFocusDir({ up, down })
    setFocus(node.id)
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* header */}
      <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
            <i className="inline-block h-2 w-2 rounded-full" style={{ background: st.color }} />
            {st.label}
          </div>
          <div className="truncate text-sm font-semibold text-foreground">{node.title || node.id}</div>
          <div className="truncate font-mono text-[11px] text-muted-foreground">{node.id}</div>
          <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="inline-block size-2.5 rounded-sm" style={{ background: prov }} />
            {node.provenance}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <button
            onClick={() => focusAs(true, true)}
            title="Focus this metric's parents + children"
            className="rounded-md border border-border px-2 py-1 text-xs hover:bg-accent"
          >
            Neighborhood
          </button>
          <div className="flex gap-1">
            <button
              onClick={() => focusAs(true, false)}
              title="Show only what drives this metric (parents)"
              className="rounded-md border border-border px-1.5 py-0.5 text-[11px] hover:bg-accent"
            >
              ↑ Up
            </button>
            <button
              onClick={() => focusAs(false, true)}
              title="Show only what this metric affects (children)"
              className="rounded-md border border-border px-1.5 py-0.5 text-[11px] hover:bg-accent"
            >
              ↓ Down
            </button>
          </div>
        </div>
      </div>

      {/* body */}
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {/* synthetic Chart VIEW node: render the dedicated chart detail (glyph +
            chart_id/canonical_id + dashboard mapping + formula/how-to-read/…) and
            skip the generic metric/threshold/properties sections. */}
        {node.label === "Chart" ? (
          <ChartDetail
            props={node.props}
            onLocateDashboard={(id) => locate({ kind: "node", id })}
          />
        ) : (
          <>
        {/* metric summary (curated, metric nodes only) */}
        {summary.length > 0 && (
          <div>
            <div className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              Metric
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              {summary.map((f) => (
                <div key={f.label} className={`min-w-0 ${f.wide ? "col-span-2" : ""}`}>
                  <div className="font-mono text-[10px] text-muted-foreground">{f.label}</div>
                  <div className="break-words text-xs text-foreground">{formatValue(f.value)}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* data lineage: mart sources, warehouse columns, SQL provenance +
            freshness (metric nodes only; the panel self-hides when absent) */}
        {isMetric && <DataLineagePanel props={node.props} />}

        {/* threshold bands: company percentile ladder vs industry benchmark
            (Threshold nodes only; direction-aware; self-hides when absent) */}
        {node.label === "Threshold" && <ThresholdBandsPanel props={node.props} />}

        {/* properties */}
        <div>
          <div className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Properties
          </div>
          {fields.length === 0 ? (
            <div className="text-xs text-muted-foreground">No properties.</div>
          ) : (
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              {fields.map(([k, v]) => (
                <div key={k} className={`min-w-0 ${WIDE_FIELDS.has(k) ? "col-span-2" : ""}`}>
                  <div className="font-mono text-[10px] text-muted-foreground">{k}</div>
                  <div className="break-words text-xs text-foreground">{formatValue(v)}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* connections grouped by relation */}
        <div>
          <div className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Connections ({connTotal})
          </div>
          {groups.length === 0 ? (
            <div className="text-xs text-muted-foreground">No connections.</div>
          ) : (
            <div className="space-y-3">
              {groups.map((g) => {
                const v = edgeVisual(g.type)
                return (
                  <div key={g.type}>
                    <div className="mb-1 flex items-center gap-1.5">
                      <i className="inline-block h-1.5 w-4 rounded-full" style={{ background: v.color }} />
                      <span className="text-[11px] font-medium text-foreground">{v.label}</span>
                      <span className="text-[11px] text-muted-foreground">{g.items.length}</span>
                    </div>
                    <ul className="space-y-1">
                      {g.items.map((c, i) => (
                        <li key={`${c.dir}-${c.otherId}-${i}`}>
                          <button
                            onClick={() => navigate(nodes.find((n) => n.id === c.otherId), c.otherId)}
                            className="flex w-full items-center gap-2 rounded-md border border-border px-2 py-1 text-left text-xs hover:bg-accent"
                            style={{ borderLeftColor: v.color, borderLeftWidth: 3 }}
                          >
                            <span className="w-3 shrink-0 text-muted-foreground">{c.dir === "out" ? "→" : "←"}</span>
                            <span className="truncate text-foreground">{c.otherTitle}</span>
                            <span className="ml-auto shrink-0 text-muted-foreground">›</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* traversal: signed upstream / downstream / blast-radius / causal-path
            lineage + feedback loops (metric nodes only) */}
        {isMetric && (
          <TraversalPanel
            loading={lineageLoading}
            upstream={lineage?.upstream ?? EMPTY_PAYLOAD}
            downstream={lineage?.downstream ?? EMPTY_PAYLOAD}
            mode={traversalMode}
            nodes={nodes}
            onNavigate={(id) => navigate(nodes.find((n) => n.id === id), id)}
            minConfidence={traverseMinConfidence}
            onMinConfidenceChange={setTraverseMinConfidence}
          />
        )}
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Threshold bands panel (Threshold nodes).
//
// A direction-aware comparison ladder: the company's own percentile distribution
// (p50<p75<p85<p95) against the industry benchmark band [min,max] (+ standard
// marker), plus the current value and target. Bars encode value magnitude
// (normalised to the largest anchor); the "standing" line reads the metric's
// direction so reaching p95 means "top tail" for higher-is-better and the low
// tail for lower-is-better. Self-hides when the node carries no band numbers.
// ---------------------------------------------------------------------------

function thNum(props: Record<string, unknown>, key: string): number | undefined {
  const v = props[key]
  return typeof v === "number" ? v : undefined
}

// One labeled magnitude bar in the threshold ladder (module-scoped so it is a
// stable component, not redefined per render).
function BandBar({
  label,
  v,
  color,
  maxV,
}: {
  label: string
  v: number | undefined
  color: string
  maxV: number
}) {
  if (v == null) return null
  const width = Math.max(2, Math.min(100, (v / maxV) * 100))
  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 font-mono text-[10px] text-muted-foreground">
        {label}
      </span>
      <div className="h-2 flex-1 rounded-full bg-muted/40">
        <div
          className="h-2 rounded-full"
          style={{ width: `${width}%`, background: color }}
        />
      </div>
      <span className="w-12 shrink-0 text-right text-[11px] tabular-nums">{v}</span>
    </div>
  )
}

function ThresholdBandsPanel({ props }: { props: Record<string, unknown> }) {
  const dir = props.direction as string | undefined
  const unit = props.unit as string | undefined
  const ttype = props.threshold_type as string | undefined
  const lower = dir === "lower_is_better"

  const bands = [
    { k: "p50", v: thNum(props, "p50_val") },
    { k: "p75", v: thNum(props, "p75_val") },
    { k: "p85", v: thNum(props, "p85_val") },
    { k: "p95", v: thNum(props, "p95_val") },
  ]
  const iMin = thNum(props, "industry_min_val")
  const iMax = thNum(props, "industry_max_val")
  const iStd = thNum(props, "industry_standard_val")
  const current = thNum(props, "current_val")
  const target = thNum(props, "target_value_num")

  const anchors = [
    ...bands.map((b) => b.v),
    iMin,
    iMax,
    iStd,
    current,
    target,
  ].filter((x): x is number => typeof x === "number")
  if (anchors.length === 0) return null

  const maxV = Math.max(...anchors, 0.000001)

  // The strongest percentile band the current value reaches (direction-aware).
  const presentBands = bands.filter(
    (b): b is { k: string; v: number } => typeof b.v === "number"
  )
  let standing: string | null = null
  if (current != null && presentBands.length) {
    const ordered = [...presentBands].reverse() // p95 → p50
    const hit = ordered.find((b) => (lower ? current <= b.v : current >= b.v))
    standing = hit
      ? `Current ${current} ${lower ? "sits within" : "reaches"} ${hit.k}`
      : `Current ${current} ${lower ? "is above" : "is below"} ${presentBands[0].k}`
  }

  const COMPANY = "#d9a83b"
  const INDUSTRY = "#6ea8ff"
  const CURRENT = "#7ee081"
  const TARGET = "#c98bff"

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        Threshold bands
        {dir ? (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] normal-case">
            {dir.replace(/_/g, " ")}
          </span>
        ) : null}
        {unit ? (
          <span className="text-[9px] normal-case text-muted-foreground">{unit}</span>
        ) : null}
      </div>

      {standing ? (
        <div className="mb-2 rounded-md bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-700 dark:text-emerald-400">
          {standing}
        </div>
      ) : null}

      <div className="space-y-1.5">
        <div className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
          Company {ttype === "percentile" ? "percentiles" : "distribution"}
        </div>
        {bands.map((b) => (
          <BandBar key={b.k} label={b.k} v={b.v} color={COMPANY} maxV={maxV} />
        ))}

        {iMin != null || iMax != null || iStd != null ? (
          <>
            <div className="pt-1 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              Industry
            </div>
            <BandBar label="ind. min" v={iMin} color={INDUSTRY} maxV={maxV} />
            <BandBar label="ind. std" v={iStd} color={INDUSTRY} maxV={maxV} />
            <BandBar label="ind. max" v={iMax} color={INDUSTRY} maxV={maxV} />
          </>
        ) : null}

        {current != null || target != null ? (
          <>
            <div className="pt-1 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              Company value
            </div>
            <BandBar label="current" v={current} color={CURRENT} maxV={maxV} />
            <BandBar label="target" v={target} color={TARGET} maxV={maxV} />
          </>
        ) : null}
      </div>

      {props.industry_source || props.industry_as_of ? (
        <div className="mt-2 text-[10px] text-muted-foreground">
          benchmark: {String(props.industry_source ?? "—")}
          {props.industry_as_of ? ` · as of ${String(props.industry_as_of)}` : ""}
        </div>
      ) : null}
    </div>
  )
}

// Data-lineage panel (Metric nodes).
//
// Surfaces a metric's mart/SQL provenance + data-quality signals — kept separate
// from the generic Properties grid, which excludes these keys via LINEAGE_FIELDS
// so SQL etc. isn't also dumped raw. Contents:
//   • mart_sources / source_columns  — chip lists
//   • sql_query_real / sql_query_canonical — collapsible monospace code blocks
//   • freshness — history_start–history_end · n_periods, with a 'stale' badge
//   • formula_sql_mismatch — a warning chip carrying formula_sql_note
// The whole section is collapsible and self-hides when a metric carries none of
// these fields, so metrics without lineage data are left undisturbed.
// ---------------------------------------------------------------------------

function DataLineagePanel({ props }: { props: Record<string, unknown> }) {
  const [open, setOpen] = useState(true)

  // MetricProps documents the typed lineage keys; n_periods / formula_sql_note
  // aren't on it, so those are read defensively off the generic record.
  const mp = props as MetricProps
  const martSources = Array.isArray(mp.mart_sources) ? mp.mart_sources : []
  const sourceColumns = Array.isArray(mp.source_columns) ? mp.source_columns : []
  const sqlReal = typeof mp.sql_query_real === "string" ? mp.sql_query_real : ""
  const sqlCanonical =
    typeof mp.sql_query_canonical === "string" ? mp.sql_query_canonical : ""
  const historyStart = typeof mp.history_start === "string" ? mp.history_start : ""
  const historyEnd = typeof mp.history_end === "string" ? mp.history_end : ""
  const nPeriods = props.n_periods
  const dataStale = mp.data_stale === true
  const mismatch = mp.formula_sql_mismatch === true
  const mismatchNote =
    typeof props.formula_sql_note === "string" ? props.formula_sql_note : ""

  const hasFreshness = !!(historyStart || historyEnd || nPeriods != null || dataStale)
  const hasAnything =
    martSources.length > 0 ||
    sourceColumns.length > 0 ||
    !!sqlReal ||
    !!sqlCanonical ||
    hasFreshness ||
    mismatch
  if (!hasAnything) return null

  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mb-2 flex w-full items-center gap-1.5 text-left text-[11px] font-medium tracking-wide text-muted-foreground uppercase hover:text-foreground"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>Data lineage</span>
        {mismatch && (
          <span
            className="ml-auto rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium normal-case text-red-600 dark:text-red-400"
            title={mismatchNote || "formula_text disagrees with sql_query_real"}
          >
            ⚠ formula ≠ SQL
          </span>
        )}
      </button>

      {open && (
        <div className="space-y-3">
          {/* QA: formula vs SQL mismatch note */}
          {mismatch && mismatchNote && (
            <div className="rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-xs text-red-600 dark:text-red-400">
              {mismatchNote}
            </div>
          )}

          {/* freshness */}
          {hasFreshness && (
            <div>
              <div className="mb-1 font-mono text-[10px] text-muted-foreground">
                freshness
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-foreground">
                {(historyStart || historyEnd) && (
                  <span className="tabular-nums">
                    {historyStart || "?"} – {historyEnd || "?"}
                  </span>
                )}
                {nPeriods != null && (
                  <span className="tabular-nums text-muted-foreground">
                    {String(nPeriods)} periods
                  </span>
                )}
                {dataStale && (
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                    stale
                  </span>
                )}
              </div>
            </div>
          )}

          {/* mart sources */}
          {martSources.length > 0 && (
            <ChipList label="mart_sources" items={martSources} />
          )}

          {/* warehouse source columns (drive /api/column-impact) */}
          {sourceColumns.length > 0 && (
            <ChipList label="source_columns" items={sourceColumns} mono />
          )}

          {/* SQL provenance — collapsed by default (can be large) */}
          {sqlReal && <SqlBlock label="sql_query_real" sql={sqlReal} />}
          {sqlCanonical && (
            <SqlBlock label="sql_query_canonical" sql={sqlCanonical} />
          )}
        </div>
      )}
    </div>
  )
}

// A wrapped chip list for a string[] lineage field (mart sources / columns).
function ChipList({
  label,
  items,
  mono,
}: {
  label: string
  items: string[]
  mono?: boolean
}) {
  return (
    <div>
      <div className="mb-1 font-mono text-[10px] text-muted-foreground">{label}</div>
      <div className="flex flex-wrap gap-1">
        {items.map((it, i) => (
          <Badge
            key={`${it}-${i}`}
            variant="secondary"
            className={mono ? "font-mono text-[10px]" : ""}
            title={it}
          >
            {it}
          </Badge>
        ))}
      </div>
    </div>
  )
}

// Collapsible, scrollable monospace SQL block. Closed by default — the verbatim
// (sql_query_real) and canonical (sql_query_canonical) queries can be long.
function SqlBlock({ label, sql }: { label: string; sql: string }) {
  const [open, setOpen] = useState(false)
  const lines = sql.split("\n").length
  return (
    <div className="rounded-md border border-border">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-accent/50"
      >
        <span className="text-muted-foreground">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-[10px] tracking-wide text-foreground">{label}</span>
        <span className="ml-auto text-[10px] tabular-nums text-muted-foreground">
          {lines} line{lines === 1 ? "" : "s"}
        </span>
      </button>
      {open && (
        <pre className="max-h-64 overflow-auto border-t border-border bg-muted/40 px-2 py-1.5 font-mono text-[11px] whitespace-pre text-foreground">
          {sql}
        </pre>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Signed-path traversal panel.
//
// Renders the four lineage views the spec calls for, derived from the two
// fetched directions:
//   • Upstream      — what this metric depends on / its causes
//   • Downstream    — what it affects (its blast radius)
//   • Causal path   — downstream paths that include a causal (INFLUENCES) hop
//   • Feedback loops — the cyclic_paths (collapsible)
// The active traversalMode view leads (expanded); each path shows its path_sign
// (+1 reinforcing → green, −1 dampening → red, 0 contains-causal/unknown → grey),
// its confidence score, and clickable nodes.
// ---------------------------------------------------------------------------

/** Sign chip: +1 reinforcing (green) / −1 dampening (red) / 0 unknown (grey). */
function SignBadge({ sign }: { sign: number }) {
  const { label, cls } =
    sign > 0
      ? { label: "+1 reinforcing", cls: "bg-green-500/15 text-green-600 dark:text-green-400" }
      : sign < 0
        ? { label: "−1 dampening", cls: "bg-red-500/15 text-red-600 dark:text-red-400" }
        : { label: "0 contains causal", cls: "bg-muted text-muted-foreground" }
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums ${cls}`}
      title={
        sign === 0
          ? "Path sign is 0: it contains a causal (unsigned) hop, so net direction is unknown in V1"
          : sign > 0
            ? "Net reinforcing: the source pushes the target in the same direction"
            : "Net dampening: the source pushes the target in the opposite direction (denominator / subtrahend on the path)"
      }
    >
      {label}
    </span>
  )
}

function TraversalPanel({
  loading,
  upstream,
  downstream,
  mode,
  nodes,
  onNavigate,
  minConfidence,
  onMinConfidenceChange,
}: {
  loading: boolean
  upstream: TraversePayload
  downstream: TraversePayload
  mode: "off" | "upstream" | "downstream"
  nodes: GraphNode[]
  onNavigate: (uid: string) => void
  minConfidence: number
  onMinConfidenceChange: (value: number) => void
}) {
  // Causal-path = downstream acyclic paths that traverse at least one causal hop.
  const causalPaths = useMemo(
    () => downstream.paths.filter((p) => p.edges.some((e) => e.kind === "causal")),
    [downstream.paths],
  )
  // Feedback loops = both directions' cyclic paths (deduped by node signature).
  const loops = useMemo(() => {
    const seen = new Set<string>()
    const out: TraversePath[] = []
    for (const p of [...upstream.cyclic_paths, ...downstream.cyclic_paths]) {
      const key = p.nodes.join(">")
      if (seen.has(key)) continue
      seen.add(key)
      out.push(p)
    }
    return out
  }, [upstream.cyclic_paths, downstream.cyclic_paths])

  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        Lineage traversal
      </div>
      {/* Read-time confidence floor for the traverse calls (also honored by the
          canvas highlight via the store). 0 = unfiltered. */}
      <div className="flex items-center gap-2 pb-1">
        <span className="shrink-0 text-[10px] text-muted-foreground">min conf</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={minConfidence}
          onChange={(e) => onMinConfidenceChange(Number(e.target.value))}
          className="h-1 flex-1 accent-primary"
          aria-label="Minimum edge confidence for lineage traversal"
          title="Only traverse edges with confidence ≥ this (read-time filter)"
        />
        <span className="w-7 shrink-0 text-right font-mono text-[10px] tabular-nums text-muted-foreground">
          {minConfidence.toFixed(2)}
        </span>
      </div>
      <PathSection
        title="Upstream"
        hint="What this metric depends on / its causes"
        defaultOpen={mode === "upstream"}
        loading={loading}
        paths={upstream.paths}
        nodes={nodes}
        onNavigate={onNavigate}
      />
      <PathSection
        title="Downstream · blast radius"
        hint="Everything this metric affects downstream"
        defaultOpen={mode === "downstream" || mode === "off"}
        loading={loading}
        paths={downstream.paths}
        nodes={nodes}
        onNavigate={onNavigate}
      />
      <PathSection
        title="Causal path"
        hint="Downstream paths that pass through a causal influence"
        defaultOpen={false}
        loading={loading}
        paths={causalPaths}
        nodes={nodes}
        onNavigate={onNavigate}
      />
      <PathSection
        title="Feedback loops"
        hint="Cyclic lineage (loops are reported, not broken)"
        defaultOpen={false}
        loading={loading}
        paths={loops}
        nodes={nodes}
        onNavigate={onNavigate}
        emptyLabel="No feedback loops."
      />
    </div>
  )
}

// One collapsible ranked-path section. Each path shows its signed direction
// (path_sign), confidence score + cumulative lag, and the clickable metric chain
// it traverses. Path nodes re-anchor the inspector on click.
function PathSection({
  title,
  hint,
  defaultOpen,
  loading,
  paths,
  nodes,
  onNavigate,
  emptyLabel,
}: {
  title: string
  hint: string
  defaultOpen: boolean
  loading: boolean
  paths: TraversePath[]
  nodes: GraphNode[]
  onNavigate: (uid: string) => void
  emptyLabel?: string
}) {
  // `open` follows `defaultOpen` (driven by the leading traversal mode) but can
  // be toggled by the user. We re-sync DURING RENDER when defaultOpen flips —
  // React's documented "adjust state when a prop changes" pattern (no effect, so
  // no cascading-render lint hit).
  const [open, setOpen] = useState(defaultOpen)
  const [prevDefault, setPrevDefault] = useState(defaultOpen)
  if (prevDefault !== defaultOpen) {
    setPrevDefault(defaultOpen)
    setOpen(defaultOpen)
  }

  // Resolve a metric_uid to a readable title. The graph node id IS the
  // metric_uid for metric nodes; fall back to props.metric_uid lookup.
  const titleFor = (uid: string): string => {
    const direct = nodes.find((n) => n.id === uid)
    if (direct) return direct.title || direct.id
    const byProp = nodes.find((n) => (n.props?.metric_uid as string | undefined) === uid)
    return byProp ? byProp.title || byProp.id : uid
  }

  // Human-readable label for a single hop's kind. 'structural' = a made-of
  // decomposition; 'causal' = a driven-by influence. Mixed paths interleave both.
  const hopKindLabel = (kind: string | null): string =>
    kind === "structural" ? "made-of" : kind === "causal" ? "driven-by" : "links"

  return (
    <div className="rounded-md border border-border">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-baseline gap-2 px-2.5 py-2 text-left hover:bg-accent/50"
      >
        <span className="text-muted-foreground">{open ? "▾" : "▸"}</span>
        <span className="text-[11px] font-medium tracking-wide text-foreground uppercase">
          {title}
        </span>
        <Badge variant="secondary" className="tabular-nums">
          {paths.length}
        </Badge>
        <span className="ml-auto truncate text-[10px] text-muted-foreground">{hint}</span>
      </button>
      {open && (
        <div className="border-t border-border px-2.5 py-2">
          {loading ? (
            <div className="text-xs text-muted-foreground">Loading lineage…</div>
          ) : paths.length === 0 ? (
            <div className="text-xs text-muted-foreground">
              {emptyLabel ?? `No ${title.toLowerCase()} paths.`}
            </div>
          ) : (
            <ul className="space-y-2">
              {paths.map((p, i) => (
                <li key={`${title}-${i}`} className="rounded-md border border-border px-2 py-1.5">
                  <div className="mb-1 flex flex-wrap items-center gap-1.5">
                    <SignBadge sign={p.path_sign} />
                    <Badge variant="secondary" className="tabular-nums">
                      conf {p.score.toFixed(3)}
                    </Badge>
                    <Badge variant="outline" className="tabular-nums">
                      lag {p.cumulative_lag}d
                    </Badge>
                    <span className="ml-auto text-[10px] text-muted-foreground">
                      {p.edges.length} hop{p.edges.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1 text-xs">
                    {p.nodes.map((uid, j) => {
                      // The hop arriving at node j is edges[j-1]. Show its kind
                      // (made-of / driven-by), relation, role and lag so mixed
                      // structural+causal chains stay legible.
                      const hop = j > 0 ? p.edges[j - 1] : undefined
                      const inverse = hop?.sign != null && hop.sign < 0
                      return (
                        <span key={`${uid}-${j}`} className="flex items-center gap-1">
                          {hop && (
                            <span className="flex items-center gap-1 text-muted-foreground">
                              <span>→</span>
                              <span
                                className={`rounded px-1 text-[10px] ${
                                  inverse
                                    ? "bg-red-500/15 text-red-600 dark:text-red-400"
                                    : "bg-muted"
                                }`}
                                title={hop.relation ?? undefined}
                              >
                                {hopKindLabel(hop.kind)}
                                {hop.relation ? ` · ${hop.relation}` : ""}
                                {hop.role ? ` · ${hop.role}` : ""}
                                {hop.temporal_lag ? ` · ${hop.temporal_lag}` : ""}
                              </span>
                              <span>→</span>
                            </span>
                          )}
                          <button
                            onClick={() => onNavigate(uid)}
                            className="truncate rounded px-1 text-foreground hover:bg-accent"
                            title={uid}
                          >
                            {titleFor(uid)}
                          </button>
                        </span>
                      )
                    })}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
