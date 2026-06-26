// Edge inspector for the currently selected metric→metric edge. Shown when the
// store has a selectedEdgeId. Surfaces the full edge metadata pinned by the KG
// backend: from/to metric, rel_type + relation, confidence, evidence_mass,
// scoring_policy, review_state, status, mechanism, temporal_lag, source_kind,
// plus deprecation metadata when status === "deprecated".

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"
import { useStore } from "@/store"
import { edgeStyle, edgeVisual } from "@/lib/graphTheme"
import type { GraphEdge } from "@/lib/api"

// Read a field off the edge, falling back to props for older payloads where the
// metadata was only carried inside `props`.
function field<T = unknown>(edge: GraphEdge, key: string): T | undefined {
  const top = (edge as unknown as Record<string, unknown>)[key]
  if (top !== undefined && top !== null && top !== "") return top as T
  const fromProps = (edge.props as Record<string, unknown> | undefined)?.[key]
  if (fromProps !== undefined && fromProps !== null && fromProps !== "")
    return fromProps as T
  return undefined
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—"
  if (typeof value === "boolean") return value ? "yes" : "no"
  if (typeof value === "number") return String(value)
  if (Array.isArray(value)) return value.map((x) => String(x)).join(", ")
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

function fmtNum(value: unknown, digits = 3): string {
  const n = typeof value === "number" ? value : Number(value)
  if (value === null || value === undefined || Number.isNaN(n)) return "—"
  return n.toFixed(digits)
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[10px] text-muted-foreground">{label}</div>
      <div className="break-words text-xs text-foreground">{children}</div>
    </div>
  )
}

export function EdgeDetail() {
  const selectedEdgeId = useStore((s) => s.selectedEdgeId)
  const edge = useStore((s) => s.edges.find((e) => e.id === s.selectedEdgeId))
  const nodes = useStore((s) => s.nodes)
  const selectEdge = useStore((s) => s.selectEdge)
  const selectNode = useStore((s) => s.selectNode)
  const setFocus = useStore((s) => s.setFocus)

  if (!selectedEdgeId || !edge) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        Select an edge on the canvas to inspect its relationship metadata.
      </div>
    )
  }

  const byId = new Map(nodes.map((n) => [n.id, n.title || n.id]))
  const fromTitle = byId.get(edge.source) ?? edge.source
  const toTitle = byId.get(edge.target) ?? edge.target

  const relation = field<string>(edge, "relation")
  const status = field<string>(edge, "status") ?? "active"
  const reviewState = field<string>(edge, "review_state")
  const confidence = field(edge, "confidence")
  const evidenceMass = field(edge, "evidence_mass")
  const scoringPolicy = field<string>(edge, "scoring_policy")
  const mechanism = field<string>(edge, "mechanism")
  const temporalLag = field<string>(edge, "temporal_lag")
  const sourceKind = field<string>(edge, "source_kind")
  const sourceRef = field<string>(edge, "source_ref")

  const isDeprecated = status === "deprecated"
  const deprecatedAt = field<string>(edge, "deprecated_at")
  const deprecatedBy = field<string>(edge, "deprecated_by_run")
  const deprecationReason = field<string>(edge, "deprecation_reason")

  // Machine-discovery edges carry measured statistical stats (Granger / MI /
  // correlation, FDR, stability). They arrive with source_kind == 'kg_discovery'
  // and/or a statistical relation; the stats live top-level or inside edge.props.
  const isDiscovery =
    sourceKind === "kg_discovery" ||
    relation === "statistical" ||
    relation === "statistical_candidate"
  const method = field(edge, "method")
  const correlation = field(edge, "correlation")
  const grangerP = field(edge, "granger_p")
  const discoveryScore = field(edge, "discovery_score")
  const stability = field(edge, "stability")
  const mi = field(edge, "mi")
  const condCorr = field(edge, "cond_corr")
  const sign = field(edge, "sign")
  const fdrPass = field(edge, "fdr_pass")

  const visual = edgeVisual(edge.type)
  const style = edgeStyle(edge)

  const navigate = (id: string) => {
    selectNode(id)
    setFocus(id)
  }

  return (
    <Card size="sm" className="m-3">
      <CardHeader className="gap-2">
        <div className="flex items-center gap-2">
          <i
            className="inline-block h-1.5 w-4 rounded-full"
            style={{
              background: style.stroke,
              opacity: style.opacity,
            }}
          />
          <CardTitle className="text-sm">{visual.label}</CardTitle>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 px-2 text-xs"
            onClick={() => selectEdge(null)}
          >
            Close
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline">{edge.type}</Badge>
          {relation ? <Badge variant="secondary">{relation}</Badge> : null}
          <Badge variant={isDeprecated ? "destructive" : "outline"}>{status}</Badge>
          {reviewState ? <Badge variant="ghost">{reviewState}</Badge> : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* endpoints */}
        <div className="grid grid-cols-1 gap-2 text-sm">
          <Row label="from">
            <button
              onClick={() => navigate(edge.source)}
              className="flex w-full items-center gap-2 rounded-md border border-border px-2 py-1 text-left hover:bg-accent"
            >
              <span className="w-3 shrink-0 text-muted-foreground">●</span>
              <span className="truncate text-foreground">{fromTitle}</span>
            </button>
          </Row>
          <Row label="to">
            <button
              onClick={() => navigate(edge.target)}
              className="flex w-full items-center gap-2 rounded-md border border-border px-2 py-1 text-left hover:bg-accent"
            >
              <span className="w-3 shrink-0 text-muted-foreground">→</span>
              <span className="truncate text-foreground">{toTitle}</span>
            </button>
          </Row>
        </div>

        <Separator />

        {/* scoring + metadata */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
          <Row label="rel_type">{edge.type}</Row>
          <Row label="relation">{fmt(relation)}</Row>
          <Row label="confidence">{fmtNum(confidence)}</Row>
          <Row label="evidence_mass">{fmtNum(evidenceMass)}</Row>
          <Row label="scoring_policy">{fmt(scoringPolicy)}</Row>
          <Row label="review_state">{fmt(reviewState)}</Row>
          <Row label="status">{fmt(status)}</Row>
          <Row label="source_kind">{fmt(sourceKind)}</Row>
          <Row label="temporal_lag">{fmt(temporalLag)}</Row>
          <Row label="source_ref">{fmt(sourceRef)}</Row>
          <div className="col-span-2">
            <Row label="mechanism">{fmt(mechanism)}</Row>
          </div>
        </div>

        {/* discovery evidence — measured statistical stats, review-only */}
        {isDiscovery ? (
          <>
            <Separator />
            <div>
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Discovery evidence
                </span>
                <Badge variant="secondary">review-only</Badge>
              </div>
              <div className="mb-2 text-[10px] text-muted-foreground">
                Measured statistical evidence from the machine-discovery feed —
                not a curated causal claim.
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <Row label="method">{fmt(method)}</Row>
                <Row label="sign">{fmt(sign)}</Row>
                <Row label="correlation">{fmtNum(correlation)}</Row>
                <Row label="cond_corr">{fmtNum(condCorr)}</Row>
                <Row label="granger_p (p-value)">{fmtNum(grangerP)}</Row>
                <Row label="mi">{fmtNum(mi)}</Row>
                <Row label="discovery_score">{fmtNum(discoveryScore)}</Row>
                <Row label="stability">{fmtNum(stability)}</Row>
                <Row label="lag (temporal_lag)">{fmt(temporalLag)}</Row>
                <Row label="fdr_pass">{fmt(fdrPass)}</Row>
              </div>
            </div>
          </>
        ) : null}

        {/* deprecation metadata — only when deprecated */}
        {isDeprecated ? (
          <>
            <Separator />
            <div>
              <div className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                Deprecation
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <Row label="deprecated_at">{fmt(deprecatedAt)}</Row>
                <Row label="deprecated_by_run">{fmt(deprecatedBy)}</Row>
                <div className="col-span-2">
                  <Row label="deprecation_reason">{fmt(deprecationReason)}</Row>
                </div>
              </div>
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}
