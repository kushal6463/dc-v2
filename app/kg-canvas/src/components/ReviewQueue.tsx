// Review queue: proposals awaiting review, BUCKETED by edge class.
//
// Metric→metric edge proposals are scored at creation by the deterministic
// edge-scoring policy (harness/ingest/edge_scoring.py): each carries a
// `relation` subtype, a `scoring_policy`, and the `review` gate flag onto its
// payload.properties. We bucket pending proposals into five classes that mirror
// that policy exactly:
//   (a) auto-safe          — DECOMPOSES_INTO formula | component (review=false)
//   (b) review structural  — DECOMPOSES_INTO identity | rollup | crossproduct |
//                            funnel (review=true)
//   (c) discovery          — machine-discovery INFLUENCES (source_kind
//                            'kg_discovery' / relation statistical |
//                            statistical_candidate). The "why" is the measured
//                            stats: method, correlation, discovery_score, fdr_pass.
//   (d) statistical        — other INFLUENCES statistical | statistical_candidate
//   (e) influence          — INFLUENCES curated_rule | llm_verified | promoted
// Each card surfaces WHY it exists: mechanism + scoring_policy + source_kind /
// source_ref (discovery cards add the measured stats). Approve / Reject(+reason)
// / Edit actions are preserved, plus a "Locate on canvas" action.

import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useStore } from "@/store"
import { edgeVisual } from "@/lib/graphTheme"
import type { Proposal } from "@/lib/api"

const PENDING_STATES = new Set(["proposed", "pending"])

// An M3 causal proposal is edge-only: payload IS the edge ({type, from_id, ...}).
type EdgePayload = {
  type?: string
  from_id?: string
  to_id?: string
  properties?: Record<string, unknown>
}

function isEdgeProposal(p: Proposal): boolean {
  return p.operation === "upsert_edge" && typeof p.payload?.type === "string"
}

// ---------------------------------------------------------------------------
// Bucketing — classify a proposal into one of the four review classes.
// ---------------------------------------------------------------------------

type BucketKey =
  | "auto_safe"
  | "review_structural"
  | "discovery"
  | "statistical"
  | "influence"

const DECOMPOSES_AUTO_SAFE = new Set(["formula", "component"])
const DECOMPOSES_REVIEW = new Set([
  "identity",
  "rollup",
  "crossproduct",
  "funnel",
])
const INFLUENCES_STATISTICAL = new Set(["statistical", "statistical_candidate"])
const DISCOVERY_SOURCE_KIND = "kg_discovery"

const BUCKETS: { key: BucketKey; label: string; hint: string }[] = [
  {
    key: "auto_safe",
    label: "Auto-safe (formula / component)",
    hint: "Deterministic exact decompositions — review=false, safe to apply.",
  },
  {
    key: "review_structural",
    label: "Review-required structural",
    hint: "identity / rollup / crossproduct / funnel — held for human review.",
  },
  {
    key: "discovery",
    label: "Discovery candidates",
    hint: "Machine-discovered INFLUENCES from the statistical feed — review the measured stats.",
  },
  {
    key: "statistical",
    label: "Statistical candidates",
    hint: "INFLUENCES from correlation evidence — never structural.",
  },
  {
    key: "influence",
    label: "Influence candidates",
    hint: "Curated-rule / LLM-verified causal links — review-only.",
  },
]

/** Read `relation` (and the `review` gate) off an edge proposal's properties. */
function edgeRelation(p: Proposal): string | undefined {
  const props = (p.payload as EdgePayload | undefined)?.properties
  const rel = props?.relation
  return typeof rel === "string" ? rel.toLowerCase() : undefined
}

/** Read `source_kind` off the proposal (payload.properties first, then top-level). */
function edgeSourceKind(p: Proposal): string | undefined {
  const props = (p.payload as EdgePayload | undefined)?.properties
  const sk = (props?.source_kind as string | undefined) ?? p.source_kind
  return typeof sk === "string" ? sk.toLowerCase() : undefined
}

/**
 * True when a proposal originates from the machine-discovery feed: source_kind
 * 'kg_discovery', or a statistical INFLUENCES relation that carries discovery
 * stats in its properties (method / discovery_score / granger_p / fdr_pass).
 */
function isDiscoveryProposal(p: Proposal): boolean {
  if (edgeSourceKind(p) === DISCOVERY_SOURCE_KIND) return true
  const relation = edgeRelation(p)
  if (!relation || !INFLUENCES_STATISTICAL.has(relation)) return false
  const props = (p.payload as EdgePayload | undefined)?.properties ?? {}
  return (
    typeof props.method === "string" ||
    typeof props.discovery_score === "number" ||
    typeof props.granger_p === "number" ||
    typeof props.fdr_pass === "boolean"
  )
}

/**
 * The canvas edge id for an edge proposal, matching the /graph endpoint's
 * `{source}-{type}-{target}` scheme. Lets "Locate on canvas" focus the edge
 * (when applied) or its endpoints (when the proposal is still pending).
 */
function edgeProposalLocateId(p: Proposal): string | null {
  const payload = p.payload as EdgePayload | undefined
  if (!payload?.type || !payload.from_id || !payload.to_id) return null
  return `${payload.from_id}-${payload.type}-${payload.to_id}`
}

/**
 * Classify a pending proposal into a review bucket using its edge relation +
 * the edge_scoring `review` flag. DECOMPOSES_INTO formula/component is the only
 * auto-safe class; everything structural-but-reviewable falls in (b);
 * INFLUENCES splits into discovery (c, machine-discovered) vs other statistical
 * (d) vs curated/LLM influence (e). Anything that isn't a recognised
 * metric→metric edge defaults to (b) so it is never silently treated as
 * auto-safe.
 */
function bucketOf(p: Proposal): BucketKey {
  const type = (p.payload as EdgePayload | undefined)?.type
  const relation = edgeRelation(p)
  if (type === "DECOMPOSES_INTO") {
    if (relation && DECOMPOSES_AUTO_SAFE.has(relation)) return "auto_safe"
    if (relation && DECOMPOSES_REVIEW.has(relation)) return "review_structural"
    return "review_structural"
  }
  if (type === "INFLUENCES") {
    if (isDiscoveryProposal(p)) return "discovery"
    if (relation && INFLUENCES_STATISTICAL.has(relation)) return "statistical"
    return "influence"
  }
  return "review_structural"
}

// A compact, readable summary of a causal edge proposal (from -> TYPE -> to).
function EdgeSummary({ payload }: { payload: EdgePayload }) {
  const props = payload.properties ?? {}
  const mechanism =
    typeof props.mechanism === "string" ? props.mechanism : undefined
  const conf =
    typeof props.confidence === "number"
      ? `conf ${Math.round((props.confidence as number) * 100)}%`
      : undefined
  const mass =
    typeof props.evidence_mass === "number"
      ? `mass ${(props.evidence_mass as number).toFixed(1)}`
      : undefined
  return (
    <div className="mt-1 space-y-0.5">
      <div className="flex items-center gap-1 text-[11px]">
        <span className="truncate text-muted-foreground">{payload.from_id}</span>
        <span
          className="shrink-0 font-mono"
          style={{ color: edgeVisual(payload.type ?? "").color }}
        >
          →{payload.type}→
        </span>
        <span className="truncate text-muted-foreground">{payload.to_id}</span>
      </div>
      {(conf || mass) && (
        <div className="text-[10px] text-muted-foreground">
          {[conf, mass].filter(Boolean).join(" · ")}
        </div>
      )}
      {mechanism && (
        <div className="text-[10px] italic text-muted-foreground">{mechanism}</div>
      )}
    </div>
  )
}

// WHY this candidate exists: the scoring policy + source provenance. Reads the
// fields the deterministic scorer / proposer stamp onto every edge proposal.
function WhyExists({ proposal }: { proposal: Proposal }) {
  const props = (proposal.payload as EdgePayload | undefined)?.properties ?? {}
  const relation =
    typeof props.relation === "string" ? props.relation : undefined
  const scoringPolicy =
    typeof props.scoring_policy === "string" ? props.scoring_policy : undefined
  const sourceKind =
    (typeof props.source_kind === "string" ? props.source_kind : undefined) ??
    proposal.source_kind
  const sourceRef =
    (typeof props.source_ref === "string" ? props.source_ref : undefined) ??
    proposal.source_ref

  if (!relation && !scoringPolicy && !sourceKind && !sourceRef) return null

  return (
    <div className="mt-2 space-y-1 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5">
      <div className="text-[9px] font-medium tracking-wide text-muted-foreground uppercase">
        Why
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {relation && (
          <Badge variant="outline" className="font-mono text-[9px]">
            {relation}
          </Badge>
        )}
        {scoringPolicy && (
          <Badge variant="secondary" className="font-mono text-[9px]">
            {scoringPolicy}
          </Badge>
        )}
        {sourceKind && (
          <Badge variant="ghost" className="font-mono text-[9px]">
            {sourceKind}
          </Badge>
        )}
      </div>
      {sourceRef && (
        <div
          className="line-clamp-2 font-mono text-[9px] break-words text-muted-foreground"
          title={sourceRef}
        >
          {sourceRef}
        </div>
      )}
    </div>
  )
}

// Discovery "why": the measured statistical evidence the machine-discovery feed
// stamps onto a candidate edge's properties. Surfaces method + correlation +
// discovery_score + fdr_pass prominently, with granger_p / stability / mi / sign
// / temporal_lag as supporting context.
function DiscoveryWhy({ proposal }: { proposal: Proposal }) {
  const props = (proposal.payload as EdgePayload | undefined)?.properties ?? {}
  const str = (k: string) =>
    typeof props[k] === "string" ? (props[k] as string) : undefined
  const num = (k: string) =>
    typeof props[k] === "number" ? (props[k] as number) : undefined

  const method = str("method")
  const correlation = num("correlation")
  const discoveryScore = num("discovery_score")
  const fdrPass = typeof props.fdr_pass === "boolean" ? props.fdr_pass : undefined
  const grangerP = num("granger_p")
  const stability = num("stability")
  const mi = num("mi")
  const sign = str("sign")
  const temporalLag = str("temporal_lag")

  const context = [
    grangerP !== undefined ? `granger p ${grangerP.toFixed(3)}` : undefined,
    stability !== undefined ? `stability ${stability.toFixed(2)}` : undefined,
    mi !== undefined ? `mi ${mi.toFixed(2)}` : undefined,
    sign ? `sign ${sign}` : undefined,
    temporalLag ? `lag ${temporalLag}` : undefined,
  ].filter(Boolean)

  const hasPrimary =
    method !== undefined ||
    correlation !== undefined ||
    discoveryScore !== undefined ||
    fdrPass !== undefined

  if (!hasPrimary && context.length === 0) return null

  return (
    <div className="mt-2 space-y-1 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5">
      <div className="text-[9px] font-medium tracking-wide text-muted-foreground uppercase">
        Discovery evidence
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {method && (
          <Badge variant="outline" className="font-mono text-[9px]">
            {method}
          </Badge>
        )}
        {correlation !== undefined && (
          <Badge variant="secondary" className="font-mono text-[9px]">
            corr {correlation.toFixed(2)}
          </Badge>
        )}
        {discoveryScore !== undefined && (
          <Badge variant="secondary" className="font-mono text-[9px]">
            score {discoveryScore.toFixed(2)}
          </Badge>
        )}
        {fdrPass !== undefined && (
          <Badge
            variant={fdrPass ? "secondary" : "ghost"}
            className="font-mono text-[9px]"
          >
            {fdrPass ? "fdr pass" : "fdr fail"}
          </Badge>
        )}
      </div>
      {context.length > 0 && (
        <div className="font-mono text-[9px] text-muted-foreground">
          {context.join(" · ")}
        </div>
      )}
    </div>
  )
}

function ProposalCard({ proposal }: { proposal: Proposal }) {
  const reviewProposal = useStore((s) => s.reviewProposal)
  const locate = useStore((s) => s.locate)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState("")
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(() =>
    JSON.stringify(proposal.payload ?? {}, null, 2)
  )
  const [editError, setEditError] = useState<string | null>(null)

  const confidence =
    typeof proposal.source_confidence === "number"
      ? `${Math.round(proposal.source_confidence * 100)}%`
      : null

  const submitEdit = () => {
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(draft) as Record<string, unknown>
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "invalid JSON")
      return
    }
    setEditError(null)
    setEditing(false)
    void reviewProposal(proposal.proposal_id, "edit", undefined, parsed)
  }

  const isEdge = isEdgeProposal(proposal)
  const edgePayload = proposal.payload as EdgePayload
  const isDiscovery = isEdge && isDiscoveryProposal(proposal)
  const locateId = isEdge ? edgeProposalLocateId(proposal) : null

  return (
    <li className="rounded-lg border border-amber-500/50 bg-amber-500/5 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-foreground">
            {isEdge
              ? `${edgeVisual(edgePayload.type ?? "").label} edge`
              : `${proposal.operation} ${proposal.target_label}`}
          </div>
          {!isEdge && (
            <div className="truncate text-xs text-muted-foreground">
              {proposal.target_id}
            </div>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-0.5 text-[10px] text-muted-foreground">
          {confidence && <span>conf {confidence}</span>}
          {proposal.dashboard_id && <span>{proposal.dashboard_id}</span>}
        </div>
      </div>

      {isEdge && <EdgeSummary payload={edgePayload} />}

      {isDiscovery && <DiscoveryWhy proposal={proposal} />}

      {isEdge && <WhyExists proposal={proposal} />}

      {!isEdge && (proposal.relationship_payloads?.length ?? 0) > 0 && (
        <div className="mt-1 text-[10px] text-muted-foreground">
          {proposal.relationship_payloads?.length} relationship(s)
        </div>
      )}

      {editing ? (
        <div className="mt-2 flex flex-col gap-2">
          <textarea
            className="h-40 w-full resize-y rounded border border-border bg-background p-2 font-mono text-[11px]"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
          />
          {editError && (
            <div className="text-[11px] text-destructive">{editError}</div>
          )}
          <div className="flex gap-2">
            <Button size="xs" onClick={submitEdit}>
              Save edit
            </Button>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                setEditing(false)
                setEditError(null)
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : rejecting ? (
        <div className="mt-2 flex flex-col gap-2">
          <input
            className="w-full rounded border border-border bg-background px-2 py-1 text-xs"
            placeholder="Reason (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex gap-2">
            <Button
              size="xs"
              variant="destructive"
              onClick={() => {
                void reviewProposal(
                  proposal.proposal_id,
                  "reject",
                  reason || undefined
                )
                setRejecting(false)
              }}
            >
              Confirm reject
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex gap-2">
          <Button
            size="xs"
            onClick={() => void reviewProposal(proposal.proposal_id, "approve")}
          >
            Approve
          </Button>
          <Button
            size="xs"
            variant="destructive"
            onClick={() => setRejecting(true)}
          >
            Reject
          </Button>
          <Button size="xs" variant="outline" onClick={() => setEditing(true)}>
            Edit
          </Button>
          {locateId && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => locate({ kind: "edge", id: locateId })}
              title="Focus this edge (or its endpoints) on the canvas"
            >
              Locate on canvas
            </Button>
          )}
        </div>
      )}
    </li>
  )
}

// One bucket section: header (label + count + hint) and its proposal cards.
function BucketSection({
  spec,
  proposals,
}: {
  spec: (typeof BUCKETS)[number]
  proposals: Proposal[]
}) {
  if (proposals.length === 0) return null
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 border-b border-border/60 pb-1">
        <span className="text-[11px] font-semibold tracking-wide text-foreground uppercase">
          {spec.label}
        </span>
        <Badge variant="secondary">{proposals.length}</Badge>
      </div>
      <div className="text-[10px] text-muted-foreground">{spec.hint}</div>
      <ul className="flex flex-col gap-2">
        {proposals.map((p) => (
          <ProposalCard key={p.proposal_id} proposal={p} />
        ))}
      </ul>
    </div>
  )
}

export function ReviewQueue() {
  const proposals = useStore((s) => s.proposals)
  const loadProposals = useStore((s) => s.loadProposals)
  const approveAll = useStore((s) => s.approveAll)
  const applyRun = useStore((s) => s.applyRun)
  const applying = useStore((s) => s.applying)
  const runId = useStore((s) => s.runId)

  const pending = proposals.filter((p) => PENDING_STATES.has(p.review_state))
  const approvedCount = proposals.filter(
    (p) => p.review_state === "approved"
  ).length

  // Bucket the pending proposals by edge class (preserving order within each).
  const byBucket = new Map<BucketKey, Proposal[]>()
  for (const spec of BUCKETS) byBucket.set(spec.key, [])
  for (const p of pending) byBucket.get(bucketOf(p))!.push(p)

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-xs text-muted-foreground">
          {pending.length} pending · {approvedCount} approved
          {runId ? ` · ${runId}` : ""}
        </span>
        <div className="flex items-center gap-1">
          <Button
            size="xs"
            variant="outline"
            disabled={!runId || pending.length === 0}
            onClick={() => void approveAll()}
            title="Approve every pending proposal in this run"
          >
            Approve all ({pending.length})
          </Button>
          <Button
            size="xs"
            disabled={!runId || applying || approvedCount === 0}
            onClick={() => void applyRun()}
          >
            {applying ? "Applying…" : "Apply approved"}
          </Button>
          <Button
            size="xs"
            variant="ghost"
            onClick={() => void loadProposals(runId ?? undefined)}
          >
            Refresh
          </Button>
        </div>
      </div>
      {pending.length === 0 ? (
        <div className="p-4 text-sm text-muted-foreground">
          No proposals awaiting review.
        </div>
      ) : (
        <div className="flex flex-col gap-4 p-3">
          {BUCKETS.map((spec) => (
            <BucketSection
              key={spec.key}
              spec={spec}
              proposals={byBucket.get(spec.key)!}
            />
          ))}
        </div>
      )}
    </div>
  )
}
