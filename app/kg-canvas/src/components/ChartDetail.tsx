// Chart-node inspector body.
//
// Renders the detail for a synthetic "Chart" node (see store.revealMetricChart):
// a canonical glyph for the chart_type plus the FULL chart property set merged
// from the chart-registry entry + the source metric — chart_id / canonical_id,
// the dashboard mapping (clickable chips → locate the real Dashboard node),
// formula, how-to-read, decisions answered, narration, and lineage backrefs.
//
// Like the (removed) MetricChartPanel it uses self-contained inline SVG — a
// representative canonical shape per chart_type, NOT a live data plot
// (series_endpoint is surfaced as a reference string, never fetched).

import { Badge } from "@/components/ui/badge"

// Normalize the many chart_type spellings the registry/metrics use down to a
// canonical family we have a renderer for. Covers the full ChartType vocab
// (bar/column/histogram, line/area, pie/donut, scatter, funnel, table/grid,
// gauge, heatmap, sankey, grouped/horizontal bar, alert_panel, kpi …); anything
// unrecognised falls back to the KPI card and the raw chart_type is still shown
// beside the glyph, so nothing renders as a silently-wrong shape.
type ChartFamily = "bar" | "line" | "area" | "pie" | "scatter" | "funnel" | "kpi" | "table"

function chartFamily(chartType: string | null | undefined): ChartFamily {
  const t = (chartType ?? "").toLowerCase()
  if (/(^|[^a-z])(bar|column|histogram)/.test(t)) return "bar"
  if (t.includes("area")) return "area"
  if (t.includes("line") || t.includes("spark") || t.includes("trend")) return "line"
  if (t.includes("pie") || t.includes("donut") || t.includes("doughnut")) return "pie"
  if (t.includes("scatter") || t.includes("bubble")) return "scatter"
  if (t.includes("funnel") || t.includes("sankey")) return "funnel"
  if (t.includes("table") || t.includes("grid") || t.includes("list") || t.includes("heatmap") || t.includes("alert"))
    return "table"
  // kpi / metric / number / gauge / stat → the single-value KPI card.
  return "kpi"
}

const FAMILY_LABEL: Record<ChartFamily, string> = {
  bar: "Bar chart",
  line: "Line chart",
  area: "Area chart",
  pie: "Pie chart",
  scatter: "Scatter plot",
  funnel: "Funnel",
  kpi: "KPI",
  table: "Table",
}

// A representative canonical glyph per chart family. Deterministic shapes (no
// random data) — a "what KIND of chart this renders as" preview, not a live plot.
const ACCENT = "var(--primary)"

function ChartGlyph({ family }: { family: ChartFamily }) {
  const w = 260
  const h = 120
  const common = { width: "100%", viewBox: `0 0 ${w} ${h}`, role: "img" as const }
  const grid = "var(--border)"
  switch (family) {
    case "bar": {
      const bars = [40, 72, 56, 96, 64, 84]
      const bw = 28
      const gap = (w - bars.length * bw) / (bars.length + 1)
      return (
        <svg {...common} aria-label="bar chart preview">
          <line x1={8} y1={h - 16} x2={w - 8} y2={h - 16} stroke={grid} />
          {bars.map((v, i) => (
            <rect
              key={i}
              x={gap + i * (bw + gap)}
              y={h - 16 - v}
              width={bw}
              height={v}
              rx={3}
              fill={ACCENT}
              opacity={0.85}
            />
          ))}
        </svg>
      )
    }
    case "line":
    case "area": {
      const pts = [10, 40, 30, 70, 55, 90, 78].map(
        (v, i, a) => [8 + (i * (w - 16)) / (a.length - 1), h - 14 - v] as const,
      )
      const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ")
      return (
        <svg {...common} aria-label={`${family} chart preview`}>
          <line x1={8} y1={h - 14} x2={w - 8} y2={h - 14} stroke={grid} />
          {family === "area" && (
            <path
              d={`${d} L${pts[pts.length - 1][0]},${h - 14} L${pts[0][0]},${h - 14} Z`}
              fill={ACCENT}
              opacity={0.18}
            />
          )}
          <path d={d} fill="none" stroke={ACCENT} strokeWidth={2.5} />
          {pts.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={2.8} fill={ACCENT} />
          ))}
        </svg>
      )
    }
    case "pie": {
      const cx = w / 2
      const cy = h / 2
      const r = 46
      const fracs = [0.5, 0.3, 0.2]
      let a0 = -Math.PI / 2
      const arc = (frac: number, fill: string, op: number, key: number) => {
        const a1 = a0 + frac * 2 * Math.PI
        const large = frac > 0.5 ? 1 : 0
        const x0 = cx + r * Math.cos(a0)
        const y0 = cy + r * Math.sin(a0)
        const x1 = cx + r * Math.cos(a1)
        const y1 = cy + r * Math.sin(a1)
        a0 = a1
        return (
          <path
            key={key}
            d={`M${cx},${cy} L${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1} Z`}
            fill={fill}
            opacity={op}
          />
        )
      }
      return (
        <svg {...common} aria-label="pie chart preview">
          {fracs.map((f, i) => arc(f, ACCENT, 0.9 - i * 0.28, i))}
        </svg>
      )
    }
    case "scatter": {
      const pts = [
        [40, 80],
        [70, 50],
        [110, 70],
        [150, 38],
        [190, 58],
        [220, 30],
        [90, 95],
        [170, 88],
      ]
      return (
        <svg {...common} aria-label="scatter plot preview">
          <line x1={20} y1={h - 16} x2={w - 8} y2={h - 16} stroke={grid} />
          <line x1={20} y1={8} x2={20} y2={h - 16} stroke={grid} />
          {pts.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={4} fill={ACCENT} opacity={0.8} />
          ))}
        </svg>
      )
    }
    case "funnel": {
      const rows = [
        [12, 236],
        [40, 180],
        [68, 124],
        [96, 70],
      ]
      const rh = 22
      return (
        <svg {...common} aria-label="funnel preview">
          {rows.map(([x, fw], i) => (
            <rect
              key={i}
              x={x}
              y={10 + i * (rh + 4)}
              width={fw}
              height={rh}
              rx={3}
              fill={ACCENT}
              opacity={0.85 - i * 0.15}
            />
          ))}
        </svg>
      )
    }
    case "table": {
      const rows = [0, 1, 2, 3]
      const cols = [16, 96, 176]
      return (
        <svg {...common} aria-label="table preview">
          {rows.map((r) => (
            <line
              key={`r${r}`}
              x1={8}
              y1={20 + r * 24}
              x2={w - 8}
              y2={20 + r * 24}
              stroke={grid}
            />
          ))}
          {cols.map((c, i) =>
            rows.map((r) => (
              <rect
                key={`c${i}r${r}`}
                x={c}
                y={8 + r * 24}
                width={i === 0 ? 64 : 60}
                height={8}
                rx={2}
                fill={ACCENT}
                opacity={r === 0 ? 0.7 : 0.3}
              />
            )),
          )}
        </svg>
      )
    }
    case "kpi":
    default:
      return (
        <svg {...common} aria-label="KPI preview">
          <text
            x={w / 2}
            y={h / 2 - 4}
            textAnchor="middle"
            fontSize={42}
            fontWeight={700}
            fill={ACCENT}
          >
            123
          </text>
          <path
            d="M30,96 L70,84 L100,90 L140,72 L180,80 L230,64"
            fill="none"
            stroke={ACCENT}
            strokeWidth={2}
            opacity={0.6}
          />
        </svg>
      )
  }
}

function ListBlock({ title, items }: { title: string; items?: string[] }) {
  if (!items || items.length === 0) return null
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </div>
      <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-foreground">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  )
}

// Read a string[] field defensively off the merged props record.
function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : []
}
function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null
}

/**
 * Render a synthetic Chart node's detail. `onLocateDashboard` (when provided)
 * is invoked with a dashboard_id when a dashboard chip is clicked so the parent
 * can locate/select the real Dashboard node in the graph.
 */
export function ChartDetail({
  props,
  onLocateDashboard,
}: {
  props: Record<string, unknown>
  onLocateDashboard?: (dashboardId: string) => void
}) {
  const chartType = str(props.chart_type)
  const family = chartFamily(chartType)
  const chartId = str(props.chart_id)
  const canonicalId = str(props.canonical_id)
  const homeDashboard = str(props.dashboard_id)
  // Every dashboard the source metric is SHOWN_ON (FR-ING-013); home dashboard
  // first, then the rest, de-duplicated.
  const dashboards = (() => {
    const all = strList(props.dashboard_ids)
    const ordered = homeDashboard ? [homeDashboard, ...all.filter((d) => d !== homeDashboard)] : all
    return [...new Set(ordered)]
  })()
  const formula = str(props.formula)
  const formulaExplanation = str(props.formula_explanation)
  const narration = str(props.narration_text)
  const audioFile = str(props.audio_file)
  const seriesEndpoint = str(props.series_endpoint)
  const concept = str(props.concept)
  const scope = str(props.scope)

  return (
    <div className="space-y-5">
      {/* canonical chart for the chart_type */}
      <div className="rounded-lg border border-border bg-muted/30 p-3">
        <ChartGlyph family={family} />
        <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <Badge variant="secondary" className="font-mono">
            {chartType ? FAMILY_LABEL[family] : "Chart"}
          </Badge>
          <span className="truncate font-mono" title={chartType ?? undefined}>
            {chartType ?? "kpi"}
          </span>
        </div>
      </div>

      {/* identity */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        {chartId && (
          <div className="min-w-0">
            <div className="font-mono text-[10px] text-muted-foreground">chart_id</div>
            <div className="break-words font-mono text-xs text-foreground">{chartId}</div>
          </div>
        )}
        {canonicalId && (
          <div className="min-w-0">
            <div className="font-mono text-[10px] text-muted-foreground">canonical_id</div>
            <div className="break-words font-mono text-xs text-foreground">{canonicalId}</div>
          </div>
        )}
        {concept && (
          <div className="min-w-0">
            <div className="font-mono text-[10px] text-muted-foreground">concept</div>
            <div className="break-words text-xs text-foreground">{concept}</div>
          </div>
        )}
        {scope && (
          <div className="min-w-0">
            <div className="font-mono text-[10px] text-muted-foreground">scope</div>
            <div className="break-words text-xs text-foreground">{scope}</div>
          </div>
        )}
      </div>

      {/* dashboard mapping — each chip locates the real Dashboard node */}
      {dashboards.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Shown on {dashboards.length === 1 ? "dashboard" : `dashboards (${dashboards.length})`}
          </div>
          <div className="flex flex-wrap gap-1">
            {dashboards.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => onLocateDashboard?.(d)}
                disabled={!onLocateDashboard}
                title={onLocateDashboard ? `Locate dashboard ${d}` : d}
                className="rounded-md border border-border px-2 py-0.5 font-mono text-[11px] text-foreground enabled:hover:bg-accent disabled:cursor-default"
                style={d === homeDashboard ? { borderLeftColor: "var(--primary)", borderLeftWidth: 3 } : undefined}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      )}

      {formula && (
        <div>
          <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Formula
          </div>
          <div className="rounded-md bg-muted/40 px-2 py-1.5 font-mono text-xs break-words text-foreground">
            {formula}
          </div>
        </div>
      )}

      {formulaExplanation && (
        <div className="text-xs leading-relaxed text-foreground">{formulaExplanation}</div>
      )}

      <ListBlock title="How to read" items={strList(props.how_to_read)} />
      <ListBlock title="Decisions answered" items={strList(props.decisions_answered)} />

      {narration && (
        <div>
          <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Narration
          </div>
          <div className="text-xs leading-relaxed whitespace-pre-line text-muted-foreground">
            {narration}
          </div>
          {audioFile && (
            <audio controls src={audioFile} className="mt-2 h-8 w-full">
              <track kind="captions" />
            </audio>
          )}
        </div>
      )}

      {seriesEndpoint && (
        <div>
          <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Series endpoint
          </div>
          <div className="rounded-md bg-muted/40 px-2 py-1.5 font-mono text-[11px] break-all text-muted-foreground">
            {seriesEndpoint}
          </div>
        </div>
      )}
    </div>
  )
}
