// Shift-click metric chart panel.
//
// When a Metric is shift-clicked the store fetches /api/metric-chart (the live
// metric's chart_type + its single chart-registry entry + a series_endpoint
// passthrough) into `metricChart`. This panel renders ONE canonical chart per
// chart_type (bar / line / pie / area / scatter / kpi / table / funnel …) from
// that payload; charts are hidden otherwise (the panel only mounts when a chart
// is open). It deliberately uses self-contained inline SVG (the same zero-dep
// approach as the edge-legend) — a representative canonical shape per chart_type
// plus the registry narrative — rather than pulling in a charting dependency.

import { X } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { ChartRegistryEntry } from "@/lib/api"
import { useStore } from "@/store"

// Normalize the many chart_type spellings the registry/metrics use down to a
// canonical family we have a renderer for.
type ChartFamily =
  | "bar"
  | "line"
  | "area"
  | "pie"
  | "scatter"
  | "funnel"
  | "kpi"
  | "table"

function chartFamily(chartType: string | null | undefined): ChartFamily {
  const t = (chartType ?? "").toLowerCase()
  if (/(^|[^a-z])(bar|column|histogram)/.test(t)) return "bar"
  if (t.includes("area")) return "area"
  if (t.includes("line") || t.includes("spark") || t.includes("trend")) return "line"
  if (t.includes("pie") || t.includes("donut") || t.includes("doughnut")) return "pie"
  if (t.includes("scatter") || t.includes("bubble")) return "scatter"
  if (t.includes("funnel")) return "funnel"
  if (t.includes("table") || t.includes("grid") || t.includes("list")) return "table"
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
// random data) — this is a "what KIND of chart this metric renders as" preview,
// not a live data plot (the series_endpoint is surfaced separately below).
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
      // Three deterministic wedges.
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
          {/* sparkline beneath the big number */}
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

export function MetricChartPanel({ inspectorOpen }: { inspectorOpen: boolean }) {
  const metricChart = useStore((s) => s.metricChart)
  const close = useStore((s) => s.closeMetricChart)

  if (!metricChart) return null

  const { title, loading, payload } = metricChart
  const family = chartFamily(payload?.chart_type)
  const entry: ChartRegistryEntry | null = payload?.registry_entry ?? null

  return (
    <aside
      // Floats over the canvas; sits left of the inspector when it's open so the
      // two panels don't overlap.
      className={cn(
        "absolute inset-y-0 z-30 flex w-[min(420px,92vw)] flex-col border-l border-border bg-background shadow-xl isolate",
        inspectorOpen ? "right-[min(420px,92vw)]" : "right-0",
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Badge variant="secondary" className="shrink-0 font-mono">
            {payload?.chart_type ? FAMILY_LABEL[family] : "Chart"}
          </Badge>
          <span className="truncate text-sm font-semibold text-foreground" title={title}>
            {entry?.title || title}
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close chart"
          title="Close chart"
          onClick={close}
        >
          <X />
        </Button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {loading ? (
          <div className="text-xs text-muted-foreground">Loading chart…</div>
        ) : !payload || !payload.found ? (
          <div className="text-xs text-muted-foreground">
            No chart for this metric.
          </div>
        ) : (
          <>
            {/* canonical chart for the metric's chart_type */}
            <div className="rounded-lg border border-border bg-muted/30 p-3">
              <ChartGlyph family={family} />
              <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                <span className="font-mono">{payload.chart_type ?? "kpi"}</span>
                {payload.chart_id && (
                  <span className="font-mono">{payload.chart_id}</span>
                )}
              </div>
            </div>

            {entry?.formula && (
              <div>
                <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Formula
                </div>
                <div className="rounded-md bg-muted/40 px-2 py-1.5 font-mono text-xs break-words text-foreground">
                  {entry.formula}
                </div>
              </div>
            )}

            {entry?.formula_explanation && (
              <div className="text-xs leading-relaxed text-foreground">
                {entry.formula_explanation}
              </div>
            )}

            <ListBlock title="How to read" items={entry?.how_to_read} />
            <ListBlock title="Decisions answered" items={entry?.decisions_answered} />

            {payload.series_endpoint && (
              <div>
                <div className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Series endpoint
                </div>
                <div className="rounded-md bg-muted/40 px-2 py-1.5 font-mono text-[11px] break-all text-muted-foreground">
                  {payload.series_endpoint}
                </div>
              </div>
            )}

            {!entry && (
              <div className="text-xs text-muted-foreground">
                No chart-registry entry for this metric.
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  )
}
