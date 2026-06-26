// Edge-diff review: render the latest (or a chosen run's) reconcile artifact —
// `edge_diff.<tenant>.<run_id>.json` — bucketed into added / unchanged /
// deprecated / skipped, each with its count, the reason it lives in that bucket,
// and the per-edge identity tuple (from -> REL·relation -> to).
//
// The on-disk artifact shape (see harness/kg/reconcile._write_reconcile_artifacts):
//   { run_id, tenant, counts: {added, unchanged, deprecated, skipped},
//     edges: { added: [[from, rel_type, relation, to], ...], ... } }
// Older / generic payloads may instead carry top-level `added: EdgeDiffEntry[]`
// arrays — we normalise both into a single `{ key, entries }[]` view.

import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { useStore } from "@/store"
import {
  DECOMPOSES_COLOR,
  DEPRECATED_COLOR,
  INFLUENCES_COLOR,
} from "@/lib/graphTheme"
import type { EdgeDiffEntry, EdgeDiffPayload } from "@/lib/api"

// A normalised edge in a bucket: its identity tuple + the raw entry (if any).
interface DiffEdge {
  fromId: string
  relType: string
  relation: string
  toId: string
}

type BucketKey = "added" | "unchanged" | "deprecated" | "skipped"

// Each bucket's display chrome + the WHY (reason code) for membership. These
// mirror the deterministic reconcile semantics exactly:
//   added       — recomputed edge absent from the live graph (will be written)
//   unchanged   — present in both live + recompute (no-op)
//   deprecated  — live, absent from recompute, eligible source_kind -> retired
//   skipped     — live, absent from recompute, review-protected -> kept as-is
const BUCKETS: {
  key: BucketKey
  label: string
  reason: string
  color: string
  variant: "default" | "secondary" | "outline" | "destructive"
}[] = [
  {
    key: "added",
    label: "Added",
    reason: "Recomputed edge absent from the live graph — will be written.",
    color: DECOMPOSES_COLOR,
    variant: "default",
  },
  {
    key: "unchanged",
    label: "Unchanged",
    reason: "Present in both the live graph and the recompute — no-op.",
    color: "#8d99ad",
    variant: "secondary",
  },
  {
    key: "deprecated",
    label: "Deprecated",
    reason: "absent_from_recompute · eligible source_kind — retired (not deleted).",
    color: DEPRECATED_COLOR,
    variant: "destructive",
  },
  {
    key: "skipped",
    label: "Skipped",
    reason:
      "absent_from_recompute · review-protected source_kind — kept as-is.",
    color: INFLUENCES_COLOR,
    variant: "outline",
  },
]

// The edge `rel_type` -> hue used in the tuple summary.
function relTypeColor(relType: string): string {
  if (relType === "DECOMPOSES_INTO") return DECOMPOSES_COLOR
  if (relType === "INFLUENCES") return INFLUENCES_COLOR
  return "#8d99ad"
}

// Coerce a single bucket entry — either a `[from, rel_type, relation, to]` tuple
// (the on-disk artifact shape) or an `EdgeDiffEntry` dict (the generic API type)
// — into a `DiffEdge`.
function toDiffEdge(entry: unknown): DiffEdge {
  if (Array.isArray(entry)) {
    const [fromId, relType, relation, toId] = entry as unknown[]
    return {
      fromId: String(fromId ?? ""),
      relType: String(relType ?? ""),
      relation: String(relation ?? ""),
      toId: String(toId ?? ""),
    }
  }
  const e = (entry ?? {}) as EdgeDiffEntry
  return {
    fromId: String(e.source ?? e.from_id ?? ""),
    relType: String(e.type ?? e.rel_type ?? ""),
    relation: String(e.relation ?? ""),
    toId: String(e.target ?? e.to_id ?? ""),
  }
}

// Pull a bucket's edge list out of either `payload.edges[bucket]` (artifact
// shape) or `payload[bucket]` (generic shape).
function bucketEntries(
  payload: EdgeDiffPayload,
  key: BucketKey
): DiffEdge[] {
  const edges = payload.edges as Record<string, unknown> | undefined
  const fromEdges = edges && Array.isArray(edges[key]) ? (edges[key] as unknown[]) : null
  const fromTop = Array.isArray((payload as Record<string, unknown>)[key])
    ? ((payload as Record<string, unknown>)[key] as unknown[])
    : null
  const raw = fromEdges ?? fromTop ?? []
  return raw.map(toDiffEdge)
}

// Read a bucket's count from the artifact's `counts` map, falling back to the
// materialised list length.
function bucketCount(
  payload: EdgeDiffPayload,
  key: BucketKey,
  entries: DiffEdge[]
): number {
  const counts = payload.counts as Record<string, unknown> | undefined
  const c = counts?.[key]
  return typeof c === "number" ? c : entries.length
}

function EdgeTuple({ edge }: { edge: DiffEdge }) {
  return (
    <li className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px]">
      <span className="truncate text-muted-foreground" title={edge.fromId}>
        {edge.fromId}
      </span>
      <span
        className="shrink-0 font-mono"
        style={{ color: relTypeColor(edge.relType) }}
        title={`${edge.relType}${edge.relation ? ` · ${edge.relation}` : ""}`}
      >
        →{edge.relType}
        {edge.relation ? `·${edge.relation}` : ""}→
      </span>
      <span className="truncate text-muted-foreground" title={edge.toId}>
        {edge.toId}
      </span>
    </li>
  )
}

// One collapsible bucket: header (count badge + reason) + the edge list.
function DiffBucket({
  spec,
  edges,
  count,
}: {
  spec: (typeof BUCKETS)[number]
  edges: DiffEdge[]
  count: number
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={edges.length === 0}
        className="flex w-full items-center gap-2 px-3 py-2 text-left disabled:cursor-default"
      >
        <i
          className="inline-block h-2 w-2 shrink-0 rounded-full"
          style={{ background: spec.color }}
        />
        <span className="text-xs font-semibold text-foreground">{spec.label}</span>
        <Badge variant={spec.variant} className="ml-0.5">
          {count}
        </Badge>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
          {edges.length > 0 ? (open ? "▾" : "▸") : ""}
        </span>
      </button>
      <div className="px-3 pb-2 text-[10px] italic text-muted-foreground">
        {spec.reason}
      </div>
      {open && edges.length > 0 && (
        <ul className="flex flex-col gap-1 px-3 pb-3">
          {edges.map((edge, i) => (
            <EdgeTuple
              key={`${edge.fromId}-${edge.relType}-${edge.relation}-${edge.toId}-${i}`}
              edge={edge}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

const DEFAULT_TENANT = "rare_seeds"

export function EdgeDiffReview() {
  const edgeDiff = useStore((s) => s.edgeDiff)
  const loadEdgeDiff = useStore((s) => s.loadEdgeDiff)

  // The user can pin a specific run_id; empty -> the backend serves the newest
  // edge_diff.<tenant>.*.json for the tenant.
  const [tenant] = useState(DEFAULT_TENANT)
  const [runDraft, setRunDraft] = useState("")

  useEffect(() => {
    if (!edgeDiff) void loadEdgeDiff(tenant)
  }, [edgeDiff, loadEdgeDiff, tenant])

  const load = (runId?: string) =>
    void loadEdgeDiff(tenant, runId && runId.trim() ? runId.trim() : undefined)

  const error =
    edgeDiff && typeof (edgeDiff as Record<string, unknown>).error === "string"
      ? String((edgeDiff as Record<string, unknown>).error)
      : null

  const resolvedRunId = edgeDiff?.run_id ?? null

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">
            {tenant}
            {resolvedRunId ? ` · ${resolvedRunId}` : ""}
          </span>
          <Button size="xs" variant="ghost" onClick={() => load(runDraft)}>
            Refresh
          </Button>
        </div>
        <div className="flex items-center gap-1.5">
          <Input
            value={runDraft}
            onChange={(e) => setRunDraft(e.target.value)}
            placeholder="run_id (blank = latest)"
            className="h-7 flex-1 text-xs"
            spellCheck={false}
            onKeyDown={(e) => {
              if (e.key === "Enter") load(runDraft)
            }}
          />
          <Button size="xs" variant="outline" onClick={() => load(runDraft)}>
            Load
          </Button>
          {runDraft && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                setRunDraft("")
                load(undefined)
              }}
            >
              Latest
            </Button>
          )}
        </div>
      </div>

      {error ? (
        <div className="p-4 text-sm text-muted-foreground">{error}</div>
      ) : !edgeDiff ? (
        <div className="p-4 text-sm text-muted-foreground">
          Loading edge diff…
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-2 p-3">
            {BUCKETS.map((spec, i) => {
              const edges = bucketEntries(edgeDiff, spec.key)
              const count = bucketCount(edgeDiff, spec.key, edges)
              return (
                <div key={spec.key}>
                  {i > 0 && <Separator className="my-2" />}
                  <DiffBucket spec={spec} edges={edges} count={count} />
                </div>
              )
            })}
          </div>
        </ScrollArea>
      )}
    </div>
  )
}
